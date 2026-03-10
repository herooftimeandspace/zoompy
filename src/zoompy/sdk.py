"""Dynamic SDK layer built on top of the generic `ZoomClient.request()` API.

`zoompy` started life as a deliberately small transport client with one public
request method. That core is still valuable because it keeps auth, retries,
logging, and schema validation concentrated in one obvious place.

For day-to-day automation work, though, people usually want a more ergonomic
shape such as:

    client.users.list(page_size=10)
    client.users.get(user_id="me")
    client.phone.users.get(user_id="abc123")

This module provides that surface without replacing the underlying transport
logic. Every generated SDK method still delegates to `ZoomClient.request()`.
That means the existing request validation, retries, and logging remain the
single source of truth.
"""

from __future__ import annotations

import json
import keyword
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import GenericAlias
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, RootModel, create_model

from .schema import OpenApiSchemaTools, SchemaOperation, SchemaRegistry

if TYPE_CHECKING:
    from .client import ZoomClient


_MISSING = object()


@dataclass(frozen=True)
class SdkParameter:
    """One Python-facing parameter exposed by a generated SDK method.

    The OpenAPI files use Zoom's original parameter names such as `userId` and
    `next_page_token`. Python callers generally expect a friendlier
    snake-cased interface, so each parameter tracks both names.
    """

    original_name: str
    python_name: str
    location: str
    required: bool


@dataclass(frozen=True)
class SdkOperation:
    """Normalized metadata for one generated SDK method.

    The SDK layer intentionally keeps its own small operation shape instead of
    passing raw OpenAPI dictionaries around everywhere. That keeps the runtime
    invocation code readable and makes the tests much easier to follow.
    """

    namespace: tuple[str, ...]
    operation_name: str
    alias_name: str | None
    http_method: str
    path: str
    path_parameters: tuple[SdkParameter, ...]
    query_parameters: tuple[SdkParameter, ...]
    has_json_body: bool
    summary: str | None
    description: str | None
    operation_id: str
    request_schema: Mapping[str, Any] | None
    response_schema: Mapping[str, Any] | None


@dataclass(frozen=True)
class SdkModels:
    """Typed request and response models for one SDK operation."""

    request_model: type[BaseModel] | None
    response_model: type[BaseModel] | None


class ModelFactory:
    """Build dynamic Pydantic models from prepared OpenAPI schemas.

    The goal here is not to perfectly reproduce every nuance of the OpenAPI
    corpus as static Python code. Instead, we generate practical runtime models
    that are good enough for interactive scripting and strongly typed helper
    methods, while still leaning on the existing JSON Schema validator as the
    final contract authority.
    """

    def __init__(self, tools: OpenApiSchemaTools | None = None) -> None:
        """Create one reusable model factory with a small in-memory cache."""

        self._tools = tools or OpenApiSchemaTools()
        self._cache: dict[tuple[str, str], type[BaseModel]] = {}

    def models_for_operation(self, operation: SdkOperation) -> SdkModels:
        """Build typed request and response models for one SDK operation."""

        request_model = None
        response_model = None

        if isinstance(operation.request_schema, Mapping):
            request_model = self.model_from_schema(
                name=f"{_pascal_case(operation.operation_name)}Request",
                schema=operation.request_schema,
            )

        if isinstance(operation.response_schema, Mapping):
            response_model = self.model_from_schema(
                name=f"{_pascal_case(operation.operation_name)}Response",
                schema=operation.response_schema,
            )

        return SdkModels(
            request_model=request_model,
            response_model=response_model,
        )

    def model_from_schema(
        self,
        *,
        name: str,
        schema: Mapping[str, Any],
    ) -> type[BaseModel]:
        """Create one Pydantic model class from a prepared schema fragment."""

        normalized = self._tools.normalize_schema(schema)
        cache_key = (name, json.dumps(normalized, sort_keys=True, default=str))
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        annotation = self._annotation_for_schema(
            name=name,
            schema=normalized,
        )
        model = self._wrap_annotation_as_model(name=name, annotation=annotation)
        self._cache[cache_key] = model
        return model

    def _annotation_for_schema(
        self,
        *,
        name: str,
        schema: Mapping[str, Any],
    ) -> Any:
        """Translate a prepared schema fragment into a Python type annotation."""

        if "enum" in schema and isinstance(schema["enum"], list):
            enum_values = tuple(value for value in schema["enum"])
            if enum_values:
                return Literal.__getitem__(enum_values)

        if "allOf" in schema and isinstance(schema["allOf"], list):
            merged = self._merge_all_of(schema)
            return self._annotation_for_schema(name=name, schema=merged)

        for keyword_name in ("oneOf", "anyOf"):
            if keyword_name in schema and isinstance(schema[keyword_name], list):
                variants = [
                    self._annotation_for_schema(
                        name=f"{name}{index + 1}",
                        schema=variant,
                    )
                    for index, variant in enumerate(schema[keyword_name])
                    if isinstance(variant, Mapping)
                ]
                if variants:
                    if len(variants) == 1:
                        return variants[0]
                    return self._union_type(variants)

        schema_type = schema.get("type")
        if schema_type == "object" or "properties" in schema:
            return self._model_for_object_schema(name=name, schema=schema)
        if schema_type == "array":
            item_schema = schema.get("items")
            if isinstance(item_schema, Mapping):
                item_annotation = self._annotation_for_schema(
                    name=f"{name}Item",
                    schema=item_schema,
                )
                return GenericAlias(list, item_annotation)
            return list[Any]
        if schema_type == "string":
            return str
        if schema_type == "integer":
            return int
        if schema_type == "number":
            return float
        if schema_type == "boolean":
            return bool

        additional = schema.get("additionalProperties")
        if isinstance(additional, Mapping):
            value_annotation = self._annotation_for_schema(
                name=f"{name}Value",
                schema=additional,
            )
            return GenericAlias(dict, (str, value_annotation))

        return Any

    def _model_for_object_schema(
        self,
        *,
        name: str,
        schema: Mapping[str, Any],
    ) -> type[BaseModel]:
        """Create one `BaseModel` subclass for an object schema."""

        normalized = self._tools.normalize_schema(schema)
        cache_key = (name, json.dumps(normalized, sort_keys=True, default=str))
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        properties = normalized.get("properties")
        required = normalized.get("required", [])
        required_names = {item for item in required if isinstance(item, str)}
        fields: dict[str, tuple[Any, Any]] = {}

        if isinstance(properties, Mapping):
            for original_name, property_schema in properties.items():
                if not isinstance(original_name, str):
                    continue

                prepared_property_schema = (
                    property_schema
                    if isinstance(property_schema, Mapping)
                    else {}
                )
                field_name = _identifier(original_name)
                field_annotation = self._annotation_for_schema(
                    name=f"{name}{_pascal_case(field_name)}",
                    schema=prepared_property_schema,
                )

                if original_name in required_names:
                    default = Field(..., alias=original_name)
                else:
                    field_annotation = field_annotation | None
                    default = Field(None, alias=original_name)

                fields[field_name] = (field_annotation, default)

        additional = normalized.get("additionalProperties")
        config = ConfigDict(populate_by_name=True, extra="allow")
        if additional is False:
            config = ConfigDict(populate_by_name=True, extra="forbid")

        model = cast(
            type[BaseModel],
            cast(Any, create_model)(
                name,
                __config__=config,
                **fields,
            ),
        )
        self._cache[cache_key] = model
        return model

    def _merge_all_of(self, schema: Mapping[str, Any]) -> dict[str, Any]:
        """Merge object-style `allOf` branches into one composite schema."""

        merged: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "allOf":
                continue
            merged[key] = value

        for branch in schema.get("allOf", []):
            if not isinstance(branch, Mapping):
                continue
            for key, value in branch.items():
                if key == "properties":
                    existing = merged.get("properties")
                    if isinstance(existing, Mapping) and isinstance(value, Mapping):
                        merged[key] = {**existing, **value}
                    else:
                        merged[key] = value
                elif key == "required":
                    existing_required = merged.get("required", [])
                    if isinstance(existing_required, list) and isinstance(value, list):
                        merged[key] = list(
                            dict.fromkeys([*existing_required, *value])
                        )
                    else:
                        merged[key] = value
                else:
                    merged[key] = value
        return merged

    def _union_type(self, variants: list[Any]) -> Any:
        """Build a PEP 604 union from an arbitrary list of annotations."""

        combined = variants[0]
        for variant in variants[1:]:
            combined = combined | variant
        return combined

    def _wrap_annotation_as_model(
        self,
        *,
        name: str,
        annotation: Any,
    ) -> type[BaseModel]:
        """Wrap a root annotation in a Pydantic model when needed.

        Object schemas already become `BaseModel` subclasses directly. Other
        shapes such as arrays or scalar roots are wrapped in `RootModel`
        subclasses so callers still get a consistent model object back from the
        typed SDK path.
        """

        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation

        model = cast(
            type[BaseModel],
            type(
                name,
                (RootModel[annotation],),
                {"model_config": ConfigDict(populate_by_name=True)},
            ),
        )
        return model


class SdkMethod:
    """Callable wrapper for one generated SDK operation.

    Instances of this class are what users interact with when they call
    `client.users.get(...)` or `client.phone.users.list(...)`. The wrapper is
    responsible only for Python argument mapping. Actual HTTP behavior still
    lives in `ZoomClient.request()`.
    """

    def __init__(self, client: ZoomClient, operation: SdkOperation) -> None:
        """Store the target client and normalized operation metadata."""

        self._client = client
        self._operation = operation
        self._model_factory = ModelFactory()
        self._models: SdkModels | None = None
        self.__doc__ = self._build_docstring()

    def __call__(self, **kwargs: Any) -> BaseModel | dict[str, Any] | list[Any] | None:
        """Execute the SDK method using scripting-friendly defaults.

        Normal SDK calls return typed model objects automatically when a
        representative response model exists. Callers that explicitly prefer raw
        validated JSON can use `.raw(...)`.
        """

        result = self.raw(**kwargs)
        response_model = self.response_model
        if result is None or response_model is None:
            return result
        return response_model.model_validate(result)

    def raw(self, **kwargs: Any) -> dict[str, Any] | list[Any] | None:
        """Execute the SDK method and return raw validated JSON.

        Supported conventions:

        * required path parameters may be passed in snake_case or original form
        * a generic `id=` alias works when exactly one path parameter exists
        * known query parameters stay query parameters
        * leftover keyword arguments become JSON body fields for body-capable
          operations
        * advanced callers can still pass explicit `path_params=`, `params=`,
          `headers=`, and `timeout=`
        """

        remaining = dict(kwargs)
        explicit_path_params = remaining.pop("path_params", None)
        explicit_params = remaining.pop("params", None)
        explicit_json = remaining.pop("json", _MISSING)
        explicit_body = remaining.pop("body", _MISSING)
        headers = remaining.pop("headers", None)
        timeout = remaining.pop("timeout", None)

        if explicit_json is not _MISSING and explicit_body is not _MISSING:
            raise TypeError(
                "Pass either 'json' or 'body' to an SDK method, not both."
            )

        if explicit_path_params is None:
            path_params = self._consume_path_parameters(remaining)
        else:
            path_params = dict(explicit_path_params)

        params: dict[str, Any] | None = None

        json_payload: Any | None = None
        if explicit_json is not _MISSING:
            json_payload = explicit_json
        elif explicit_body is not _MISSING:
            json_payload = explicit_body
        else:
            if explicit_params is None and self._operation.has_json_body:
                params, json_payload = self._split_query_and_body_kwargs(remaining)
            elif explicit_params is None:
                params = dict(remaining) if remaining else None
                remaining.clear()
            else:
                params = dict(explicit_params)

        if explicit_params is not None:
            params = dict(explicit_params)

        if remaining:
            unknown_names = ", ".join(sorted(remaining))
            raise TypeError(
                f"Unexpected keyword arguments for SDK method "
                f"{self._operation.operation_name}: {unknown_names}"
            )

        if isinstance(json_payload, BaseModel):
            json_payload = json_payload.model_dump(
                by_alias=True,
                exclude_none=True,
            )
        elif json_payload is not None and self.request_model is not None:
            json_payload = self._normalize_typed_body(
                request_model=self.request_model,
                value=json_payload,
            )

        return self._client.request(
            self._operation.http_method,
            self._operation.path,
            path_params=path_params,
            params=params,
            json=json_payload,
            headers=headers,
            timeout=timeout,
        )

    @property
    def request_model(self) -> type[BaseModel] | None:
        """Return the generated request-body model for this SDK method.

        Many Zoom operations do not accept JSON bodies, so this property is
        optional by design.
        """

        return self._get_models().request_model

    @property
    def response_model(self) -> type[BaseModel] | None:
        """Return the generated typed response model for this SDK method."""

        return self._get_models().response_model

    def typed(self, **kwargs: Any) -> BaseModel | dict[str, Any] | list[Any] | None:
        """Backward-compatible alias for the default typed SDK behavior."""

        return self(**kwargs)

    def _normalize_typed_body(
        self,
        *,
        request_model: type[BaseModel],
        value: Any,
    ) -> dict[str, Any] | list[Any] | Any:
        """Validate and serialize one typed request body.

        Accepting either a raw `dict` or a model instance keeps the typed path
        ergonomic in scripts while still ensuring that the body shape matches
        the generated request model before it leaves the process.
        """

        if isinstance(value, BaseModel):
            validated = value
        else:
            validated = request_model.model_validate(value)

        dumped = validated.model_dump(
            by_alias=True,
            exclude_none=True,
        )
        if isinstance(validated, RootModel):
            return dumped["root"]
        return dumped

    def _get_models(self) -> SdkModels:
        """Build and cache typed request/response models on first use."""

        if self._models is None:
            self._models = self._model_factory.models_for_operation(
                self._operation
            )
        return self._models

    def _consume_path_parameters(
        self,
        remaining: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Extract required path parameters from the pending kwargs.

        Path placeholders are positional in the URL template, so callers should
        get a clear failure immediately if they forget one. This helper enforces
        that contract before the request method ever runs.
        """

        collected: dict[str, Any] = {}
        if (
            len(self._operation.path_parameters) == 1 and
            "id" in remaining
        ):
            only_parameter = self._operation.path_parameters[0]
            if only_parameter.python_name not in remaining and only_parameter.original_name not in remaining:
                collected[only_parameter.original_name] = remaining.pop("id")

        for parameter in self._operation.path_parameters:
            if parameter.original_name in collected:
                continue
            if parameter.python_name in remaining:
                collected[parameter.original_name] = remaining.pop(
                    parameter.python_name
                )
                continue

            if parameter.original_name in remaining:
                collected[parameter.original_name] = remaining.pop(
                    parameter.original_name
                )
                continue

            if parameter.required:
                raise TypeError(
                    f"Missing required path parameter "
                    f"'{parameter.python_name}' for SDK method "
                    f"{self._operation.operation_name}."
                )

        return collected or None

    def _split_query_and_body_kwargs(
        self,
        remaining: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | list[Any] | Any | None]:
        """Split leftover kwargs into query params and JSON body data."""

        query_name_map = {
            parameter.python_name: parameter.original_name
            for parameter in self._operation.query_parameters
        }
        params: dict[str, Any] = {}
        body: dict[str, Any] = {}

        for key in list(remaining):
            value = remaining.pop(key)
            if key in query_name_map:
                params[query_name_map[key]] = value
            else:
                body[key] = value

        if body and self.request_model is not None:
            normalized_body = self._normalize_typed_body(
                request_model=self.request_model,
                value=body,
            )
        else:
            normalized_body = body or None

        return params or None, normalized_body

    def _build_docstring(self) -> str:
        """Assemble a human-readable docstring for interactive users.

        The generated SDK layer is dynamic, which means introspection matters
        more than usual. A concise docstring helps future maintainers and
        interactive shell users understand what a method maps to.
        """

        lines = [
            self._operation.summary
            or f"{self._operation.http_method} {self._operation.path}",
            "",
            f"Operation ID: {self._operation.operation_id}",
            f"HTTP: {self._operation.http_method} {self._operation.path}",
        ]

        if self._operation.description:
            lines.extend(["", self._operation.description.strip()])

        if self._operation.path_parameters:
            lines.extend(["", "Path parameters:"])
            for parameter in self._operation.path_parameters:
                lines.append(
                    f"- {parameter.python_name}"
                    f" (Zoom name: {parameter.original_name})"
                )

        if self._operation.query_parameters:
            lines.extend(["", "Query parameters:"])
            for parameter in self._operation.query_parameters:
                lines.append(
                    f"- {parameter.python_name}"
                    f" (Zoom name: {parameter.original_name})"
                )

        if self._operation.has_json_body:
            lines.extend(
                [
                    "",
                    "Request body:",
                    "- leftover kwargs become JSON body fields by default",
                    "- or pass the payload explicitly as `body=` or `json=`",
                ]
            )

        if self._operation.response_schema is not None:
            lines.extend(
                [
                    "",
                    "Return shape:",
                    "- normal calls return a typed Pydantic model when possible",
                    "- use `.raw(...)` for plain JSON",
                ]
            )

        return "\n".join(lines)


class ServiceNode:
    """One namespace node in the dynamic SDK tree.

    A node can contain child namespaces, callable SDK methods, or both. For
    example `client.phone` is a namespace node, `client.phone.users` is a child
    namespace node, and `client.phone.users.get` is an `SdkMethod`.
    """

    def __init__(self, name: str, client: ZoomClient) -> None:
        """Create an initially empty service namespace."""

        self._name = name
        self._client = client
        self._children: dict[str, ServiceNode] = {}
        self._child_aliases: dict[str, ServiceNode] = {}
        self._methods: dict[str, SdkMethod] = {}

    def add_child(self, name: str) -> ServiceNode:
        """Return an existing child namespace or create it lazily."""

        child = self._children.get(name)
        if child is None:
            child = ServiceNode(name=name, client=self._client)
            self._children[name] = child
        return child

    def add_method(self, name: str, method: SdkMethod) -> None:
        """Register one callable SDK method on this namespace."""

        self._methods[name] = method

    def has_member(self, name: str) -> bool:
        """Return whether this namespace exposes a child or a method."""

        return (
            name in self._children or
            name in self._child_aliases or
            name in self._methods
        )

    def get_member(self, name: str) -> Any:
        """Return one child namespace or method by attribute name."""

        if name in self._children:
            return self._children[name]
        if name in self._child_aliases:
            return self._child_aliases[name]
        if name in self._methods:
            return self._methods[name]
        raise AttributeError(f"{self!r} has no member {name!r}")

    def __getattr__(self, name: str) -> Any:
        """Expose child namespaces and operation methods as attributes."""

        return self.get_member(name)

    def __dir__(self) -> list[str]:
        """Make interactive discovery pleasant in shells and editors."""

        return sorted(
            set(
                [
                    *super().__dir__(),
                    *self._children.keys(),
                    *self._child_aliases.keys(),
                    *self._methods.keys(),
                ]
            )
        )

    def __repr__(self) -> str:
        """Return a short debugging representation for service nodes."""

        return f"ServiceNode(name={self._name!r})"


class ZoomSdk:
    """Build and expose the dynamic service tree for one `ZoomClient`.

    The SDK is created lazily because not every application needs it. The first
    attribute access such as `client.users` triggers one schema walk, after
    which the namespace tree is cached on the client.
    """

    def __init__(self, client: ZoomClient, schema_registry: SchemaRegistry) -> None:
        """Build the SDK tree from the same registry used by request routing."""

        self._client = client
        self._registry = schema_registry
        self._root = ServiceNode(name="root", client=client)
        self._build_tree()

    def has_member(self, name: str) -> bool:
        """Return whether the SDK root exposes one top-level namespace."""

        return self._root.has_member(name)

    def get_member(self, name: str) -> Any:
        """Return one top-level namespace from the SDK root."""

        return self._root.get_member(name)

    def __getattr__(self, name: str) -> Any:
        """Delegate root attribute access to the root service node."""

        return self.get_member(name)

    def __dir__(self) -> list[str]:
        """Expose generated top-level namespaces to interactive tooling."""

        return self._root.__dir__()

    def _build_tree(self) -> None:
        """Walk schema operations once and populate namespace nodes.

        The build process intentionally does two passes:

        1. register operation-id-based methods, which are always available
        2. register simple CRUD aliases like `list` and `get` only when they
           are unique within a namespace
        """

        operations = [
            self._build_sdk_operation(operation)
            for operation in self._registry.iter_operations()
        ]

        alias_counts: dict[tuple[tuple[str, ...], str], int] = {}
        for operation in operations:
            if operation.alias_name is None:
                continue
            key = (operation.namespace, operation.alias_name)
            alias_counts[key] = alias_counts.get(key, 0) + 1

        for operation in operations:
            node = self._ensure_namespace(operation.namespace)
            node.add_method(
                operation.operation_name,
                SdkMethod(client=self._client, operation=operation),
            )

        for operation in operations:
            if operation.alias_name is None:
                continue

            key = (operation.namespace, operation.alias_name)
            if alias_counts.get(key) != 1:
                continue

            node = self._ensure_namespace(operation.namespace)
            if node.has_member(operation.alias_name):
                continue

            node.add_method(
                operation.alias_name,
                SdkMethod(client=self._client, operation=operation),
            )

        self._build_singular_aliases(self._root)

    def _ensure_namespace(self, namespace: tuple[str, ...]) -> ServiceNode:
        """Create or reuse the nested namespace path for one operation."""

        node = self._root
        for segment in namespace:
            node = node.add_child(segment)
        return node

    def _build_sdk_operation(self, operation: SchemaOperation) -> SdkOperation:
        """Convert one raw indexed operation into SDK metadata."""

        path_parameters, query_parameters = _extract_parameters(operation)
        namespace = _namespace_from_path(operation.template_path)
        return SdkOperation(
            namespace=namespace,
            operation_name=_identifier(operation.operation_id),
            alias_name=_heuristic_alias(
                method=operation.method,
                path=operation.template_path,
            ),
            http_method=operation.method,
            path=operation.template_path,
            path_parameters=path_parameters,
            query_parameters=query_parameters,
            has_json_body=operation.has_request_body,
            summary=operation.summary,
            description=operation.description,
            operation_id=operation.operation_id,
            request_schema=self._registry.request_body_schema(operation),
            response_schema=self._registry.response_schema(operation),
        )

    def _build_singular_aliases(self, node: ServiceNode) -> None:
        """Add simple singular aliases like `user` for `users` namespaces."""

        for name, child in list(node._children.items()):
            if name.endswith("s") and len(name) > 1:
                singular = name[:-1]
                if singular and not node.has_member(singular):
                    node._child_aliases[singular] = child
            self._build_singular_aliases(child)


def _extract_parameters(
    operation: SchemaOperation,
) -> tuple[tuple[SdkParameter, ...], tuple[SdkParameter, ...]]:
    """Normalize path and query parameter metadata from one OpenAPI operation."""

    tools = OpenApiSchemaTools()
    path_parameters: list[SdkParameter] = []
    query_parameters: list[SdkParameter] = []
    seen: set[tuple[str, str]] = set()

    for raw_parameter in operation.parameters:
        if not isinstance(raw_parameter, Mapping):
            continue

        resolved = tools.resolve_schema(operation.spec, raw_parameter)
        if not isinstance(resolved, Mapping):
            continue

        original_name = resolved.get("name")
        location = resolved.get("in")
        if not isinstance(original_name, str) or not isinstance(location, str):
            continue

        key = (location, original_name)
        if key in seen:
            continue
        seen.add(key)

        parameter = SdkParameter(
            original_name=original_name,
            python_name=_identifier(original_name),
            location=location,
            required=bool(resolved.get("required")),
        )

        if location == "path":
            path_parameters.append(parameter)
        elif location == "query":
            query_parameters.append(parameter)

    return tuple(path_parameters), tuple(query_parameters)


def _namespace_from_path(path: str) -> tuple[str, ...]:
    """Convert one OpenAPI path into a namespace tuple.

    The namespace is intentionally based only on static path segments so the
    same resource group naturally shares one service object:

    * `/users` -> `("users",)`
    * `/users/{userId}` -> `("users",)`
    * `/phone/users/{userId}` -> `("phone", "users")`
    """

    parts = [part for part in path.split("/") if part]
    if parts and parts[-1].startswith("{") and parts[-1].endswith("}"):
        parts = parts[:-1]
    return tuple(_identifier(part) for part in parts)


def _heuristic_alias(*, method: str, path: str) -> str | None:
    """Return a simple CRUD-style alias when the path shape is obvious."""

    parts = [part for part in path.split("/") if part]
    ends_with_placeholder = bool(parts and parts[-1].startswith("{"))
    method = method.upper()

    if method == "GET":
        return "get" if ends_with_placeholder else "list"
    if method == "POST":
        return "create"
    if method == "PUT":
        return "update"
    if method == "PATCH":
        return "patch"
    if method == "DELETE":
        return "delete"
    return None


def _identifier(value: str) -> str:
    """Convert a schema-derived string into a valid Python identifier."""

    value = value.replace("{", "").replace("}", "")
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^0-9a-zA-Z_]+", "_", value)
    value = value.strip("_").lower()
    if not value:
        value = "operation"
    if value[0].isdigit():
        value = f"field_{value}"
    if keyword.iskeyword(value):
        value = f"{value}_"
    return value


def _pascal_case(value: str) -> str:
    """Convert a snake-cased identifier into PascalCase for model names."""

    cleaned = _identifier(value)
    return "".join(part.capitalize() for part in cleaned.split("_")) or "Model"
