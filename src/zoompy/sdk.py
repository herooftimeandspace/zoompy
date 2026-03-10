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

import keyword
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

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
        self.__doc__ = self._build_docstring()

    def __call__(self, **kwargs: Any) -> dict[str, Any] | list[Any] | None:
        """Translate Python kwargs into one `ZoomClient.request()` call.

        Supported conventions:

        * required path parameters may be passed in snake_case or original form
        * leftover keyword arguments become query parameters by default
        * request bodies may be passed as `body=` or `json=`
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

        if explicit_params is None:
            params = dict(remaining) if remaining else None
            remaining.clear()
        else:
            params = dict(explicit_params)

        if remaining:
            unknown_names = ", ".join(sorted(remaining))
            raise TypeError(
                f"Unexpected keyword arguments for SDK method "
                f"{self._operation.operation_name}: {unknown_names}"
            )

        json_payload: Any | None = None
        if explicit_json is not _MISSING:
            json_payload = explicit_json
        elif explicit_body is not _MISSING:
            json_payload = explicit_body

        return self._client.request(
            self._operation.http_method,
            self._operation.path,
            path_params=path_params,
            params=params,
            json=json_payload,
            headers=headers,
            timeout=timeout,
        )

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
        for parameter in self._operation.path_parameters:
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
                    "- pass the JSON payload as `body=` or `json=`",
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

        return name in self._children or name in self._methods

    def get_member(self, name: str) -> Any:
        """Return one child namespace or method by attribute name."""

        if name in self._children:
            return self._children[name]
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
        )


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
