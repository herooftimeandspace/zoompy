"""OpenAPI document loading, indexing, and validation for `zoompy`.

The production client validates every successful JSON response against bundled
OpenAPI documents. This module owns that work so the HTTP client can stay
focused on transport concerns like retries, authentication, and logging.

The repository now stores three schema families:

* ordinary outbound API documents under `zoompy/endpoints`
* master-account API documents under `zoompy/master_accounts`
* inbound webhook documents under `zoompy/webhooks`

Those families are related, but not identical. Endpoint and master-account
documents both describe path-based request/response APIs, while webhook
documents describe incoming event payloads. Keeping the indexing and validation
pieces separate makes the code easier to reason about than one monolithic
"schema registry" that tries to do every job at once.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator


@dataclass(frozen=True)
class SchemaOperation:
    """One path-based HTTP operation extracted from an OpenAPI document.

    The runtime validator only needs method, path, and response schemas, but
    the new SDK layer also needs a little more metadata so it can expose
    ergonomic Python methods on top of the generic request client. We keep that
    metadata here so both layers read from the same canonical OpenAPI source.
    """

    schema_name: str
    method: str
    template_path: str
    path_regex: re.Pattern[str]
    operation_id: str
    summary: str | None
    description: str | None
    parameters: tuple[Any, ...]
    has_request_body: bool
    responses: Mapping[str, Any]
    spec: Mapping[str, Any]
    server_url: str | None


@dataclass(frozen=True)
class WebhookOperation:
    """One webhook event operation extracted from an OpenAPI document."""

    schema_name: str
    event_name: str
    operation_id: str
    method: str
    request_schema: Mapping[str, Any]
    spec: Mapping[str, Any]


class OpenApiSchemaTools:
    """Small schema helpers shared by response and webhook validation.

    Zoom's published OpenAPI files are generally good enough to validate real
    traffic, but they are not perfectly uniform. This helper centralizes the
    narrow compatibility rules we already rely on:

    * prefer JSON-like content blocks automatically
    * resolve local `$ref` values recursively
    * tolerate malformed webhook refs that still point at `#/paths/...`
    * normalize odd type spellings like `Integer`
    * tolerate the empty-string-vs-optional-enum mismatch seen in some live
      responses

    Keeping those rules here prevents the endpoint and webhook code paths from
    drifting apart over time.
    """

    def pick_json_media(
        self,
        content: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        """Select a JSON-like media block from an OpenAPI content map."""

        preferred = (
            "application/json",
            "application/json; charset=utf-8",
            "application/scim+json",
        )
        for key in preferred:
            candidate = content.get(key)
            if isinstance(candidate, Mapping):
                return candidate

        for media_type, candidate in content.items():
            if "json" in str(media_type) and isinstance(candidate, Mapping):
                return candidate
        return None

    def resolve_ref(self, spec: Mapping[str, Any], ref: str) -> Any:
        """Resolve one local JSON Pointer reference inside an OpenAPI document."""

        if not ref.startswith("#/"):
            raise ValueError(f"Only local refs are supported, got: {ref}")

        parts = [
            part.replace("~1", "/").replace("~0", "~")
            for part in ref.lstrip("#/").split("/")
        ]

        if parts and parts[0] == "paths" and "paths" not in spec:
            if "webhooks" in spec:
                # Some webhook docs contain malformed local refs that still use
                # `#/paths/...` even though the document only has `webhooks`.
                parts[0] = "webhooks"

        current: Any = spec
        for part in parts:
            if not isinstance(current, Mapping) or part not in current:
                raise ValueError(f"Unresolvable $ref: {ref}")
            current = current[part]
        return current

    def resolve_schema(self, spec: Mapping[str, Any], schema: Any) -> Any:
        """Recursively inline local `$ref` values within a schema fragment."""

        if not isinstance(schema, Mapping):
            return schema

        if "$ref" in schema:
            target = self.resolve_ref(spec, str(schema["$ref"]))
            merged = dict(target) if isinstance(target, Mapping) else {"value": target}
            for key, value in schema.items():
                if key != "$ref":
                    merged[key] = value
            return self.resolve_schema(spec, merged)

        resolved: dict[str, Any] = {}
        for key, value in schema.items():
            if isinstance(value, Mapping):
                resolved[key] = self.resolve_schema(spec, value)
            elif isinstance(value, list):
                resolved[key] = [self.resolve_schema(spec, item) for item in value]
            else:
                resolved[key] = value
        return resolved

    def normalize_schema(self, schema: Any) -> Any:
        """Normalize narrowly scoped schema irregularities before validation."""

        if isinstance(schema, Mapping):
            normalized: dict[str, Any] = {}
            for key, value in schema.items():
                if key == "type" and isinstance(value, str):
                    normalized[key] = self._normalize_type_name(value)
                elif isinstance(value, Mapping):
                    normalized[key] = self.normalize_schema(value)
                elif isinstance(value, list):
                    normalized[key] = [self.normalize_schema(item) for item in value]
                else:
                    normalized[key] = value

            properties = normalized.get("properties")
            required = normalized.get("required")
            if isinstance(properties, Mapping) and isinstance(required, list):
                synthesized = dict(properties)
                changed = False
                for name in required:
                    if isinstance(name, str) and name not in synthesized:
                        synthesized[name] = {}
                        changed = True
                if changed:
                    normalized["properties"] = synthesized
            return normalized

        if isinstance(schema, list):
            return [self.normalize_schema(item) for item in schema]
        return schema

    def normalize_payload_for_schema(self, payload: Any, schema: Any) -> Any:
        """Adjust payloads just enough to handle known live API quirks."""

        if not isinstance(schema, Mapping):
            return payload

        composed_payload = self._normalize_composed_payload(payload, schema)
        if composed_payload is not payload:
            return composed_payload

        schema_type = schema.get("type")

        if schema_type == "object" and isinstance(payload, Mapping):
            return self._normalize_object_payload(payload, schema)

        if schema_type == "array" and isinstance(payload, list):
            item_schema = schema.get("items")
            return [
                self.normalize_payload_for_schema(item, item_schema)
                for item in payload
            ]

        return payload

    def prepare_schema(self, spec: Mapping[str, Any], schema: Mapping[str, Any]) -> Any:
        """Resolve refs and normalize a schema into validator-ready form."""

        return self.normalize_schema(self.resolve_schema(spec, schema))

    def _normalize_type_name(self, value: str) -> str:
        """Return a canonical JSON Schema type name when recognizable."""

        lowered = value.lower()
        type_map = {
            "array": "array",
            "boolean": "boolean",
            "integer": "integer",
            "int64": "integer",
            "long": "integer",
            "number": "number",
            "object": "object",
            "string": "string",
        }
        if lowered in type_map:
            return type_map[lowered]
        if any(
            token in lowered
            for token in (
                "enum",
                "country",
                "states",
                "city",
                "campus",
                "building",
                "floor",
            )
        ):
            return "string"
        return value

    def _normalize_composed_payload(
        self,
        payload: Any,
        schema: Mapping[str, Any],
    ) -> Any:
        """Normalize payloads described through composition keywords."""

        if "allOf" in schema:
            return self._normalize_all_of_payload(payload, schema)

        for keyword in ("oneOf", "anyOf"):
            if keyword in schema:
                return self._normalize_variant_payload(payload, schema, keyword)

        return payload

    def _normalize_all_of_payload(
        self,
        payload: Any,
        schema: Mapping[str, Any],
    ) -> Any:
        """Apply payload normalization across every `allOf` branch."""

        normalized = payload
        for branch in schema.get("allOf", []):
            if not isinstance(branch, Mapping):
                continue
            merged_branch = self._merge_schema_branch(schema, branch, "allOf")
            normalized = self.normalize_payload_for_schema(
                normalized,
                merged_branch,
            )
        return normalized

    def _normalize_variant_payload(
        self,
        payload: Any,
        schema: Mapping[str, Any],
        keyword: str,
    ) -> Any:
        """Pick the best `oneOf` or `anyOf` branch for normalization."""

        candidates = schema.get(keyword, [])
        if not isinstance(candidates, list):
            return payload

        best_payload = payload
        best_error_count: int | None = None

        for branch in candidates:
            if not isinstance(branch, Mapping):
                continue

            candidate_schema = self._merge_schema_branch(schema, branch, keyword)
            candidate_payload = self.normalize_payload_for_schema(
                payload,
                candidate_schema,
            )
            validator = Draft202012Validator(self.normalize_schema(candidate_schema))
            error_count = sum(1 for _ in validator.iter_errors(candidate_payload))

            if best_error_count is None or error_count < best_error_count:
                best_error_count = error_count
                best_payload = candidate_payload

            if error_count == 0:
                break

        return best_payload

    def _merge_schema_branch(
        self,
        schema: Mapping[str, Any],
        branch: Mapping[str, Any],
        keyword: str,
    ) -> dict[str, Any]:
        """Merge one composition branch with its parent schema siblings."""

        merged: dict[str, Any] = {}
        for key, value in schema.items():
            if key == keyword:
                continue
            merged[key] = value

        for key, value in branch.items():
            if key == "properties":
                existing = merged.get("properties")
                if isinstance(existing, Mapping):
                    merged[key] = {**existing, **value}
                else:
                    merged[key] = value
            elif key == "required":
                existing_required = merged.get("required", [])
                if isinstance(existing_required, list) and isinstance(value, list):
                    merged[key] = list(dict.fromkeys([*existing_required, *value]))
                else:
                    merged[key] = value
            else:
                merged[key] = value

        return merged

    def _normalize_object_payload(
        self,
        payload: Mapping[str, Any],
        schema: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Normalize one object payload against its object schema."""

        normalized = dict(payload)
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return normalized

        required = schema.get("required", [])
        required_names = {name for name in required if isinstance(name, str)}

        for key, value in payload.items():
            property_schema = properties.get(key)
            if not isinstance(property_schema, Mapping):
                continue

            if self._should_drop_empty_optional_enum_value(
                key=key,
                value=value,
                property_schema=property_schema,
                required_names=required_names,
            ):
                normalized.pop(key, None)
                continue

            normalized[key] = self.normalize_payload_for_schema(
                value,
                property_schema,
            )

        return normalized

    def _should_drop_empty_optional_enum_value(
        self,
        *,
        key: str,
        value: Any,
        property_schema: Mapping[str, Any],
        required_names: set[str],
    ) -> bool:
        """Return whether one property should be treated as effectively unset."""

        if key in required_names or value != "":
            return False

        if property_schema.get("type") != "string":
            return False

        enum_values = property_schema.get("enum")
        if not isinstance(enum_values, list):
            return False

        return "" not in enum_values


class SchemaValidator:
    """Validate payloads against prepared OpenAPI schemas.

    This class intentionally knows nothing about where schemas came from. It is
    given a document plus the schema fragment to apply, then handles the
    resolve-normalize-validate sequence and turns validation errors into compact
    human-readable exceptions.
    """

    def __init__(self, tools: OpenApiSchemaTools | None = None) -> None:
        """Create one validator shared by endpoint and webhook registries."""

        self._tools = tools or OpenApiSchemaTools()

    def validate_payload(
        self,
        *,
        spec: Mapping[str, Any],
        schema: Mapping[str, Any],
        payload: Any,
        context: str,
    ) -> None:
        """Validate one JSON payload against one schema fragment."""

        prepared_schema = self._tools.prepare_schema(spec, schema)
        normalized_payload = self._tools.normalize_payload_for_schema(
            payload,
            prepared_schema,
        )
        validator = Draft202012Validator(prepared_schema)
        errors = sorted(
            validator.iter_errors(normalized_payload),
            key=lambda item: list(item.path),
        )
        if errors:
            formatted = "; ".join(
                f"path={list(error.path)} message={error.message}"
                for error in errors[:5]
            )
            raise ValueError(f"{context}: {formatted}")


class PathOperationIndex:
    """Index path-based OpenAPI operations for fast request lookup.

    This index is shared by ordinary endpoint APIs and master-account APIs
    because both are described through the OpenAPI `paths` section.
    """

    def __init__(
        self,
        *,
        resource_root: Any | None = None,
        path_root_names: tuple[str, ...] = ("endpoints", "master_accounts"),
    ) -> None:
        """Load and index bundled path-based operations."""

        self._resource_root = resource_root or resources.files("zoompy")
        self._path_root_names = path_root_names
        self._operations_by_prefix: dict[str, list[SchemaOperation]] = {}
        self._all_operations: list[SchemaOperation] = []
        self._load_operations()

    def find_operation(
        self,
        *,
        method: str,
        raw_path: str,
        actual_path: str,
    ) -> SchemaOperation:
        """Find the schema operation that matches one request."""

        candidates = self._candidate_operations(raw_path, actual_path)
        upper_method = method.upper()

        for candidate in candidates:
            if (
                candidate.method == upper_method and
                candidate.template_path == raw_path
            ):
                return candidate

        for candidate in candidates:
            if (
                candidate.method == upper_method and
                candidate.template_path == actual_path
            ):
                return candidate

        for candidate in candidates:
            if (
                candidate.method == upper_method and
                candidate.path_regex.fullmatch(actual_path)
            ):
                return candidate

        raise ValueError(
            f"Could not find OpenAPI operation for {upper_method} {actual_path} "
            f"(raw path: {raw_path})."
        )

    def base_url_for_request(
        self,
        *,
        method: str,
        raw_path: str,
        actual_path: str,
        fallback: str,
    ) -> str:
        """Return the best server URL declared for one matched request."""

        try:
            operation = self.find_operation(
                method=method,
                raw_path=raw_path,
                actual_path=actual_path,
            )
        except ValueError:
            return fallback.rstrip("/")

        if operation.server_url:
            return operation.server_url.rstrip("/")
        return fallback.rstrip("/")

    def iter_operations(self) -> tuple[SchemaOperation, ...]:
        """Return all indexed operations in a stable order.

        The SDK layer walks the full operation list to build namespace objects
        like `client.phone.users.get(...)`. Returning a sorted tuple keeps that
        build process deterministic and easy to reason about in tests.
        """

        return tuple(
            sorted(
                self._all_operations,
                key=lambda item: (
                    item.schema_name,
                    item.template_path,
                    item.method,
                    item.operation_id,
                ),
            )
        )

    def _candidate_operations(
        self,
        raw_path: str,
        actual_path: str,
    ) -> list[SchemaOperation]:
        """Return operations from the relevant leading-path buckets."""

        prefixes = {self._path_prefix(raw_path), self._path_prefix(actual_path)}
        candidates: list[SchemaOperation] = []
        seen: set[tuple[str, str, str]] = set()

        for prefix in prefixes:
            for operation in self._operations_by_prefix.get(prefix, []):
                key = (
                    operation.schema_name,
                    operation.method,
                    operation.template_path,
                )
                if key not in seen:
                    seen.add(key)
                    candidates.append(operation)
        return candidates

    def _load_operations(self) -> None:
        """Load every bundled path-based schema into the operation index."""

        for root_name in self._path_root_names:
            schema_root = self._resource_root / root_name
            for schema_path in self._iter_schema_files(schema_root):
                spec = json.loads(schema_path.read_text(encoding="utf-8"))
                schema_name = str(spec.get("info", {}).get("title", schema_path.stem))
                server_url = self._pick_server_url(spec)

                for path, path_item in spec.get("paths", {}).items():
                    if not isinstance(path_item, Mapping):
                        continue

                    for method in ("get", "post", "put", "patch", "delete"):
                        operation = path_item.get(method)
                        if not isinstance(operation, Mapping):
                            continue

                        entry = SchemaOperation(
                            schema_name=schema_name,
                            method=method.upper(),
                            template_path=path,
                            path_regex=self._compile_path_regex(path),
                            operation_id=str(
                                operation.get("operationId")
                                or f"{method}_{path.strip('/').replace('/', '_')}"
                            ),
                            summary=(
                                str(operation["summary"])
                                if isinstance(operation.get("summary"), str)
                                else None
                            ),
                            description=(
                                str(operation["description"])
                                if isinstance(operation.get("description"), str)
                                else None
                            ),
                            parameters=tuple(
                                item
                                for item in [
                                    *(path_item.get("parameters", []) or []),
                                    *(operation.get("parameters", []) or []),
                                ]
                            ),
                            has_request_body=isinstance(
                                operation.get("requestBody"),
                                Mapping,
                            ),
                            responses=operation.get("responses", {}),
                            spec=spec,
                            server_url=server_url,
                        )
                        prefix = self._path_prefix(path)
                        self._operations_by_prefix.setdefault(prefix, []).append(entry)
                        self._all_operations.append(entry)

    def _iter_schema_files(self, root: Any) -> Iterable[Path]:
        """Recursively yield packaged JSON schema files from a traversable root."""

        try:
            children = list(root.iterdir())
        except FileNotFoundError:
            return

        for child in children:
            if child.is_dir():
                yield from self._iter_schema_files(child)
            elif child.name.endswith(".json"):
                yield Path(str(child))

    def _compile_path_regex(self, path: str) -> re.Pattern[str]:
        """Compile one templated OpenAPI path into a concrete regex."""

        return re.compile("^" + re.sub(r"\{[^/]+\}", r"[^/]+", path) + "$")

    def _pick_server_url(self, spec: Mapping[str, Any]) -> str | None:
        """Return the primary server URL declared by one OpenAPI document."""

        servers = spec.get("servers")
        if not isinstance(servers, list):
            return None

        fallback: str | None = None
        for entry in servers:
            if not isinstance(entry, Mapping):
                continue

            url = entry.get("url")
            if not isinstance(url, str) or not url:
                continue

            cleaned = url.rstrip("/")
            if fallback is None:
                fallback = cleaned
            if "api.zoom.us" in cleaned:
                return cleaned

        return fallback

    def _path_prefix(self, path: str) -> str:
        """Return the leading path segment used for operation bucketing."""

        parts = [part for part in path.split("/") if part]
        return f"/{parts[0]}" if parts else "/"


class WebhookRegistry:
    """Index and validate incoming webhook payloads from bundled documents.

    Webhook schemas are shaped differently from path-based API schemas. The
    interesting contract lives under the OpenAPI `webhooks` section and the
    schema we care about is the event request body that Zoom sends to a
    subscriber. This registry turns those documents into a runtime validation
    API that application code can call directly.
    """

    def __init__(
        self,
        *,
        resource_root: Any | None = None,
        webhook_root_name: str = "webhooks",
        tools: OpenApiSchemaTools | None = None,
        validator: SchemaValidator | None = None,
    ) -> None:
        """Load bundled webhook operations into an event lookup table."""

        self._resource_root = resource_root or resources.files("zoompy")
        self._webhook_root_name = webhook_root_name
        self._tools = tools or OpenApiSchemaTools()
        self._validator = validator or SchemaValidator(self._tools)
        self._operations_by_event: dict[str, list[WebhookOperation]] = {}
        self._load_operations()

    def validate_webhook(
        self,
        *,
        event_name: str,
        payload: Any,
        schema_name: str | None = None,
        operation_id: str | None = None,
    ) -> None:
        """Validate one incoming webhook payload against the bundled schema."""

        operation = self.find_operation(
            event_name=event_name,
            schema_name=schema_name,
            operation_id=operation_id,
        )
        self._validator.validate_payload(
            spec=operation.spec,
            schema=operation.request_schema,
            payload=payload,
            context=(
                f"Webhook schema validation failed for {operation.event_name} "
                f"in {operation.schema_name}"
            ),
        )

    def find_operation(
        self,
        *,
        event_name: str,
        schema_name: str | None = None,
        operation_id: str | None = None,
    ) -> WebhookOperation:
        """Return the best matching webhook operation for one event name."""

        candidates = list(self._operations_by_event.get(event_name, []))
        if schema_name is not None:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.schema_name == schema_name
            ]
        if operation_id is not None:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.operation_id == operation_id
            ]

        if not candidates:
            raise ValueError(
                f"Could not find webhook schema for event {event_name!r}."
            )
        if len(candidates) > 1:
            choices = ", ".join(
                f"{candidate.schema_name}:{candidate.operation_id}"
                for candidate in candidates[:5]
            )
            raise ValueError(
                f"Webhook event {event_name!r} is ambiguous. Narrow it with "
                f"schema_name or operation_id. Candidates: {choices}"
            )
        return candidates[0]

    def _load_operations(self) -> None:
        """Load every bundled webhook document into the event lookup table."""

        webhook_root = self._resource_root / self._webhook_root_name
        for schema_path in self._iter_schema_files(webhook_root):
            spec = json.loads(schema_path.read_text(encoding="utf-8"))
            schema_name = str(spec.get("info", {}).get("title", schema_path.stem))
            webhooks = spec.get("webhooks", {})
            if not isinstance(webhooks, Mapping):
                continue

            for event_name, path_item in webhooks.items():
                if not isinstance(path_item, Mapping):
                    continue

                for method in ("get", "post", "put", "patch", "delete"):
                    operation = path_item.get(method)
                    if not isinstance(operation, Mapping):
                        continue

                    request_schema = self._extract_request_schema(operation)
                    if request_schema is None:
                        continue

                    operation_id = str(
                        operation.get("operationId") or
                        f"{schema_name}:{event_name}:{method}"
                    )
                    entry = WebhookOperation(
                        schema_name=schema_name,
                        event_name=event_name,
                        operation_id=operation_id,
                        method=method.upper(),
                        request_schema=request_schema,
                        spec=spec,
                    )
                    self._operations_by_event.setdefault(event_name, []).append(entry)

    def _extract_request_schema(
        self,
        operation: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        """Return the JSON request-body schema for one webhook operation."""

        request_body = operation.get("requestBody")
        if not isinstance(request_body, Mapping):
            return None

        content = request_body.get("content")
        if not isinstance(content, Mapping):
            return None

        media = self._tools.pick_json_media(content)
        if media is None:
            return None

        schema = media.get("schema")
        if isinstance(schema, Mapping):
            return schema
        return None

    def _iter_schema_files(self, root: Any) -> Iterable[Path]:
        """Recursively yield packaged JSON webhook files from a root."""

        try:
            children = list(root.iterdir())
        except FileNotFoundError:
            return

        for child in children:
            if child.is_dir():
                yield from self._iter_schema_files(child)
            elif child.name.endswith(".json"):
                yield Path(str(child))


class SchemaRegistry:
    """Validate path-based API responses against bundled OpenAPI documents.

    `SchemaRegistry` is now intentionally small at the top level:

    * `PathOperationIndex` handles document loading and operation lookup
    * `SchemaValidator` handles schema preparation and JSON validation
    * `SchemaRegistry` ties those together for the public request/response API
    """

    def __init__(
        self,
        *,
        resource_root: Any | None = None,
        path_root_names: tuple[str, ...] = ("endpoints", "master_accounts"),
    ) -> None:
        """Load bundled path-based schemas and prepare validation helpers."""

        tools = OpenApiSchemaTools()
        self._tools = tools
        self._validator = SchemaValidator(tools)
        self._index = PathOperationIndex(
            resource_root=resource_root,
            path_root_names=path_root_names,
        )

    def validate_response(
        self,
        *,
        method: str,
        raw_path: str,
        actual_path: str,
        status_code: int,
        payload: Any,
    ) -> None:
        """Validate one response payload against the matching OpenAPI schema."""

        operation = self.find_operation(
            method=method,
            raw_path=raw_path,
            actual_path=actual_path,
        )
        schema = self._pick_response_schema(operation, status_code)

        if payload is None:
            return

        if schema is None:
            raise ValueError(
                f"No response schema found for {method.upper()} {actual_path} "
                f"with status {status_code}."
            )

        self._validator.validate_payload(
            spec=operation.spec,
            schema=schema,
            payload=payload,
            context=(
                f"Schema validation failed for {method.upper()} {actual_path} "
                f"status {status_code}"
            ),
        )

    def base_url_for_request(
        self,
        *,
        method: str,
        raw_path: str,
        actual_path: str,
        fallback: str,
    ) -> str:
        """Return the most appropriate server URL for one request."""

        return self._index.base_url_for_request(
            method=method,
            raw_path=raw_path,
            actual_path=actual_path,
            fallback=fallback,
        )

    def find_operation(
        self,
        *,
        method: str,
        raw_path: str,
        actual_path: str,
    ) -> SchemaOperation:
        """Find the schema operation that matches a path-based request."""

        return self._index.find_operation(
            method=method,
            raw_path=raw_path,
            actual_path=actual_path,
        )

    def iter_operations(self) -> tuple[SchemaOperation, ...]:
        """Expose all path-based operations for the SDK layer.

        `ZoomClient.request()` still uses targeted lookups for execution, but
        the SDK builder needs to inspect the whole OpenAPI inventory once so it
        can construct namespace proxies and operation callables.
        """

        return self._index.iter_operations()

    def _pick_response_schema(
        self,
        operation: SchemaOperation,
        status_code: int,
    ) -> Mapping[str, Any] | None:
        """Select the best matching response schema for one status code."""

        responses = operation.responses
        preferred_keys = [str(status_code)]
        preferred_keys.extend(
            key for key in responses if key.isdigit() and 200 <= int(key) < 300
        )
        preferred_keys.append("default")

        seen: set[str] = set()
        for key in preferred_keys:
            if key in seen:
                continue
            seen.add(key)

            response = responses.get(key)
            if not isinstance(response, Mapping):
                continue

            content = response.get("content")
            if not isinstance(content, Mapping):
                return None

            media = self._tools.pick_json_media(content)
            if media is None:
                return None

            schema = media.get("schema")
            if isinstance(schema, Mapping):
                return schema
            return None

        return None
