"""Focused tests for SDK helper paths that golden surface tests do not hit."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from zoom_sdk.schema import SchemaOperation
from zoom_sdk.sdk import (
    ModelFactory,
    SdkMethod,
    SdkModels,
    SdkOperation,
    SdkParameter,
    _annotation_label,
    _extract_parameters,
    _schema_annotation,
)


def _schema_operation_with_parameters(parameters: list[Any]) -> SchemaOperation:
    """Build a tiny synthetic schema operation for helper tests."""

    return SchemaOperation(
        schema_name="Users",
        method="GET",
        template_path="/users/{userId}",
        path_regex=re.compile(r"^/users/[^/]+$"),
        operation_id="getUser",
        summary=None,
        description=None,
        parameters=tuple(parameters),
        has_request_body=False,
        request_body=None,
        responses={},
        spec={"components": {"schemas": {"Shared": {"type": "string"}}}},
        server_url="https://api.zoom.us/v2",
    )


class _FakeClient:
    """Minimal client stub for exercising internal SDK helper branches."""

    def request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        _ = (args, kwargs)
        return {"ok": True}


def _sdk_operation(
    *,
    namespace: tuple[str, ...] = ("users",),
    path_parameters: tuple[SdkParameter, ...] = (),
    query_parameters: tuple[SdkParameter, ...] = (),
    has_json_body: bool = False,
    request_schema: Mapping[str, Any] | None = None,
    response_schema: Mapping[str, Any] | None = None,
) -> SdkOperation:
    """Build one small synthetic operation for internal helper tests."""

    return SdkOperation(
        namespace=namespace,
        operation_name="list",
        alias_name="list",
        http_method="GET",
        path="/users",
        path_parameters=path_parameters,
        query_parameters=query_parameters,
        has_json_body=has_json_body,
        summary="List resources",
        description=None,
        operation_id="listUsers",
        request_schema=request_schema,
        response_schema=response_schema,
        semantic_aliases=(),
    )


def test_model_factory_caches_models_and_supports_additional_properties() -> None:
    """Cover cache reuse, `additionalProperties`, and `extra=forbid` branches."""

    factory = ModelFactory()
    schema = {
        "type": "object",
        "properties": {
            "metadata": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
            }
        },
        "additionalProperties": False,
    }

    model_one = factory.model_from_schema(name="ConfigModel", schema=schema)
    model_two = factory.model_from_schema(name="ConfigModel", schema=schema)
    instance = model_one(metadata={"count": 3})

    assert model_one is model_two
    assert isinstance(instance, BaseModel)
    assert instance.model_dump(by_alias=True)["metadata"] == {"count": 3}
    assert model_one.model_config.get("extra") == "forbid"


def test_schema_annotation_handles_unions_arrays_and_additional_properties() -> None:
    """Return practical tooling annotations for composed helper schemas."""

    union_label = _annotation_label(
        _schema_annotation({"oneOf": [{"type": "string"}, {"type": "integer"}]})
    )
    assert "str" in union_label
    assert "int" in union_label
    assert _annotation_label(
        _schema_annotation(
            {
                "allOf": [
                    {"type": "object", "properties": {"name": {"type": "string"}}}
                ]
            }
        )
    ) == "dict[str, Any]"
    assert "list[int]" in _annotation_label(
        _schema_annotation({"type": "array", "items": {"type": "integer"}})
    )
    assert "dict[str, bool]" in _annotation_label(
        _schema_annotation(
            {
                "additionalProperties": {"type": "boolean"},
            }
        )
    )
    assert _annotation_label(_schema_annotation({"type": "array"})) == "list[Any]"
    assert _annotation_label(
        _schema_annotation({"oneOf": [{"type": "string"}]})
    ) == "str"
    assert _annotation_label(type(None)) == "None"


def test_extract_parameters_skips_invalid_entries_and_deduplicates() -> None:
    """Ignore malformed parameters while keeping one copy of each real one."""

    operation = _schema_operation_with_parameters(
        [
            {"$ref": "#/components/schemas/Shared"},
            {"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": "page_size", "in": "query", "schema": {"type": "integer"}},
            {"name": 123, "in": "query"},
            "bad",
        ]
    )

    path_parameters, query_parameters = _extract_parameters(operation)

    assert path_parameters == (
        SdkParameter(
            original_name="userId",
            python_name="user_id",
            location="path",
            required=True,
            schema={"type": "string"},
            description=None,
        ),
    )
    assert query_parameters == (
        SdkParameter(
            original_name="page_size",
            python_name="page_size",
            location="query",
            required=False,
            schema={"type": "integer"},
            description=None,
        ),
    )


def test_model_factory_skips_invalid_property_names_and_reuses_cached_models() -> None:
    """Ignore non-string property names while still caching by normalized schema."""

    factory = ModelFactory()
    from zoom_sdk import sdk as sdk_module

    original_dumps = sdk_module.json.dumps
    sdk_module.json.dumps = lambda *args, **kwargs: "weird-model-cache-key"
    try:
        model_one = factory._model_for_object_schema(
            name="WeirdModel",
            schema={
                "type": "object",
                "properties": {
                    "validName": {"type": "string"},
                    1: {"type": "integer"},
                },
            },
        )
    finally:
        sdk_module.json.dumps = original_dumps

    model_two = factory.model_from_schema(
        name="WeirdModelCache",
        schema={"type": "object", "properties": {"validName": {"type": "string"}}},
    )
    model_three = factory.model_from_schema(
        name="WeirdModelCache",
        schema={"type": "object", "properties": {"validName": {"type": "string"}}},
    )

    assert model_two is model_three
    assert "valid_name" in model_one.model_fields
    assert len(model_one.model_fields) == 1


def test_model_factory_merge_all_of_ignores_invalid_branch_shapes() -> None:
    """Keep object merging tolerant of malformed `allOf` branches."""

    factory = ModelFactory()
    model = factory.model_from_schema(
        name="MergedModel",
        schema={
            "allOf": [
                "bad-branch",
                {"properties": {"name": {"type": "string"}}},
                {"required": "not-a-list"},
            ]
        },
    )

    assert "name" in model.model_fields


def test_sdk_method_pagination_helpers_cover_mapping_and_none_fallbacks() -> None:
    """Exercise the remaining best-effort pagination helper branches."""

    method = SdkMethod(
        client=_FakeClient(),  # type: ignore[arg-type]
        operation=_sdk_operation(),
        model_factory=ModelFactory(),
    )

    assert method._next_page_token(["not-a-mapping"]) is None
    assert list(method._collection_items([1, 2])) == [1, 2]
    assert list(method._collection_items(None)) == []
    assert method._preferred_collection(
        {"total_records": 4, "entries": [1, 2], "page_size": 2}
    ) == [1, 2]
    assert method._collection_field_candidates(None) == ()
    assert method._collection_field_candidates("member") == ("member", "members")
    assert method._coerce_page_mapping([1, 2]) is None
    assert method._int_field(None, "page_size") is None


def test_sdk_method_path_and_query_helpers_cover_original_names_and_body_fallbacks() -> None:
    """Use schema-native names and split leftover kwargs into query/body buckets."""

    method = SdkMethod(
        client=_FakeClient(),  # type: ignore[arg-type]
        operation=_sdk_operation(
            path_parameters=(
                SdkParameter(
                    original_name="userId",
                    python_name="user_id",
                    location="path",
                    required=False,
                    schema={"type": "string"},
                    description=None,
                ),
            ),
            query_parameters=(
                SdkParameter(
                    original_name="page_size",
                    python_name="page_size",
                    location="query",
                    required=False,
                    schema={"type": "integer"},
                    description=None,
                ),
            ),
            has_json_body=True,
            request_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        ),
        model_factory=ModelFactory(),
    )

    remaining = {"userId": "abc123"}
    assert method._consume_path_parameters(remaining) == {"userId": "abc123"}
    assert remaining == {}

    params, body = method._split_query_and_body_kwargs(
        {"page_size": 10, "name": "Ada"}
    )
    assert params == {"page_size": 10}
    assert body == {"name": "Ada"}

    params, body = method._split_query_and_body_kwargs({})
    assert params is None
    assert body is None


def test_sdk_method_uses_root_request_model_and_tooling_name_fallbacks() -> None:
    """Cover root-body serialization and duplicate tooling-parameter names."""

    request_model = ModelFactory().model_from_schema(
        name="TagsRequest",
        schema={"type": "array", "items": {"type": "string"}},
    )
    operation = _sdk_operation(
        path_parameters=(
            SdkParameter(
                original_name="userId",
                python_name="identifier",
                location="path",
                required=True,
                schema={"type": "string"},
                description=None,
            ),
        ),
        query_parameters=(
            SdkParameter(
                original_name="identifier",
                python_name="identifier",
                location="query",
                required=False,
                schema={"type": "string"},
                description=None,
            ),
        ),
        response_schema={"type": "integer"},
    )
    method = SdkMethod(
        client=_FakeClient(),  # type: ignore[arg-type]
        operation=operation,
        model_factory=ModelFactory(),
    )
    method._models = SdkModels(
        request_model=request_model,
        response_model=None,
    )

    assert method._normalize_typed_body(
        request_model=request_model,
        value=["a", "b"],
    ) == ["a", "b"]
    assert method._return_annotation() == int | None
    assert method._tooling_parameter_name(operation.path_parameters[0]) == "identifier"
    assert method._tooling_parameter_name(operation.query_parameters[0]) == "query_identifier"
