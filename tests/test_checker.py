"""checker 语义判定单元测试。"""

import pytest

from app.checker import run_comparison
from app.spec_parser import Spec


def review(old: dict, new: dict):
    old_spec = Spec(old, "json", "")
    new_spec = Spec(new, "json", "")
    return run_comparison(old_spec, new_spec)


def rules_of(result):
    return [(f["rule"], f["severity"]) for f in result["findings"]]


BASE = {
    "openapi": "3.0.3",
    "info": {"title": "T", "version": "1"},
    "paths": {},
}


def op(path, method="get", **overrides):
    operation = {"responses": {"200": {"description": "ok"}}}
    operation.update(overrides)
    doc = dict(BASE)
    doc["paths"] = {path: {method: operation}}
    return doc


# ---------------------------------------------------------------- 无差异
def test_identical_specs_are_compatible():
    doc = op("/x")
    result = review(doc, doc)
    assert result["summary"]["total"] == 0
    assert result["summary"]["compatible"] is True


# ---------------------------------------------------------------- 接口增删
def test_operation_removed_is_breaking():
    result = review(op("/x"), dict(BASE, paths={}))
    assert ("OPERATION_REMOVED", "breaking") in rules_of(result)
    assert result["summary"]["compatible"] is False


def test_operation_added_is_non_breaking():
    result = review(dict(BASE, paths={}), op("/x"))
    assert ("OPERATION_ADDED", "non-breaking") in rules_of(result)
    assert result["summary"]["compatible"] is True


# ---------------------------------------------------------------- 参数
def test_required_query_param_added_is_breaking():
    old = op("/x", parameters=[{"name": "a", "in": "query",
                                "schema": {"type": "string"}}])
    new = op("/x", parameters=[
        {"name": "a", "in": "query", "schema": {"type": "string"}},
        {"name": "b", "in": "query", "required": True,
         "schema": {"type": "string"}},
    ])
    result = review(old, new)
    assert ("PARAM_ADDED", "breaking") in rules_of(result)


def test_optional_param_added_is_non_breaking():
    old = op("/x")
    new = op("/x", parameters=[{"name": "b", "in": "query",
                                "schema": {"type": "string"}}])
    result = review(old, new)
    assert ("PARAM_ADDED", "non-breaking") in rules_of(result)


def test_param_becomes_required_is_breaking():
    old = op("/x", parameters=[{"name": "a", "in": "query",
                                "schema": {"type": "string"}}])
    new = op("/x", parameters=[{"name": "a", "in": "query", "required": True,
                                "schema": {"type": "string"}}])
    result = review(old, new)
    assert ("PARAM_REQUIRED", "breaking") in rules_of(result)


# ---------------------------------------------------------------- 请求体
def body(schema, required=True):
    return {
        "requestBody": {
            "required": required,
            "content": {"application/json": {"schema": schema}},
        },
        "responses": {"201": {"description": "ok"}},
    }


def test_request_required_property_added_is_breaking():
    old = op("/p", "post", **body({
        "type": "object", "required": ["a"],
        "properties": {"a": {"type": "string"}},
    }))
    new = op("/p", "post", **body({
        "type": "object", "required": ["a", "b"],
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
    }))
    result = review(old, new)
    assert ("PROPERTY_ADDED", "breaking") in rules_of(result)
    added = [f for f in result["findings"] if f["rule"] == "PROPERTY_ADDED"][0]
    assert added["path_new"].endswith("['b']")


def test_request_optional_property_added_is_non_breaking():
    old = op("/p", "post", **body({"type": "object",
                                   "properties": {"a": {"type": "string"}}}))
    new = op("/p", "post", **body({"type": "object", "properties": {
        "a": {"type": "string"}, "b": {"type": "integer"}}}))
    result = review(old, new)
    assert ("PROPERTY_ADDED", "non-breaking") in rules_of(result)


def test_request_property_removed_non_breaking_when_optional():
    old = op("/p", "post", **body({"type": "object", "properties": {
        "a": {"type": "string"}, "b": {"type": "integer"}}}))
    new = op("/p", "post", **body({"type": "object",
                                   "properties": {"a": {"type": "string"}}}))
    result = review(old, new)
    assert ("PROPERTY_REMOVED", "non-breaking") in rules_of(result)


# ---------------------------------------------------------------- 响应
def response_body(schema):
    return {"responses": {"200": {
        "description": "ok",
        "content": {"application/json": {"schema": schema}},
    }}}


def test_response_property_removed_always_breaking():
    old = op("/p", **response_body({"type": "object", "properties": {
        "a": {"type": "string"}}}))
    new = op("/p", **response_body({"type": "object",
                                    "properties": {}}))
    result = review(old, new)
    assert ("PROPERTY_REMOVED", "breaking") in rules_of(result)


def test_response_required_property_becomes_optional_breaking():
    old = op("/p", **response_body({"type": "object", "required": ["a"],
                                    "properties": {"a": {"type": "string"}}}))
    new = op("/p", **response_body({"type": "object",
                                    "properties": {"a": {"type": "string"}}}))
    result = review(old, new)
    assert ("REQUIRED_REMOVED", "breaking") in rules_of(result)


def test_response_new_enum_value_is_breaking():
    old = op("/p", **response_body({"type": "string",
                                    "enum": ["a", "b"]}))
    new = op("/p", **response_body({"type": "string",
                                    "enum": ["a", "b", "c"]}))
    result = review(old, new)
    assert ("ENUM_VALUE_ADDED", "breaking") in rules_of(result)


def test_request_enum_value_removed_is_breaking():
    old = op("/p", "post", **body({"type": "string", "enum": ["a", "b"]}))
    new = op("/p", "post", **body({"type": "string", "enum": ["a"]}))
    result = review(old, new)
    assert ("ENUM_VALUE_REMOVED", "breaking") in rules_of(result)


def test_response_status_removed_is_breaking():
    old = {"openapi": "3.0.3", "info": {"title": "T"}, "paths": {"/p": {
        "get": {"responses": {"200": {"description": "ok"},
                              "404": {"description": "missing"}}}}}}
    new = {"openapi": "3.0.3", "info": {"title": "T"}, "paths": {"/p": {
        "get": {"responses": {"200": {"description": "ok"}}}}}}
    result = review(old, new)
    assert ("RESPONSE_REMOVED", "breaking") in rules_of(result)


# ---------------------------------------------------------------- 类型 / 约束
def test_integer_to_number_request_non_breaking_response_breaking():
    old_req = op("/p", "post", **body({"type": "integer"}))
    new_req = op("/p", "post", **body({"type": "number"}))
    assert ("TYPE_WIDENED", "non-breaking") in rules_of(review(old_req, new_req))

    old_resp = op("/p", **response_body({"type": "integer"}))
    new_resp = op("/p", **response_body({"type": "number"}))
    assert ("TYPE_CHANGED", "breaking") in rules_of(review(old_resp, new_resp))


def test_request_maximum_tightened_is_breaking():
    old = op("/p", "post", **body({"type": "integer", "maximum": 100}))
    new = op("/p", "post", **body({"type": "integer", "maximum": 50}))
    result = review(old, new)
    assert ("CONSTRAINT_CHANGED", "breaking") in rules_of(result)


def test_response_maximum_removed_is_breaking():
    old = op("/p", **response_body({"type": "integer", "maximum": 100}))
    new = op("/p", **response_body({"type": "integer"}))
    result = review(old, new)
    bounds = [f for f in result["findings"] if f["rule"] == "CONSTRAINT_CHANGED"]
    assert all(f["severity"] == "breaking" for f in bounds)


def test_string_to_integer_type_is_breaking():
    old = op("/p", "post", **body({"type": "string"}))
    new = op("/p", "post", **body({"type": "integer"}))
    assert ("TYPE_CHANGED", "breaking") in rules_of(review(old, new))


# ---------------------------------------------------------------- readOnly/writeOnly
def test_readonly_writeonly_properties_are_direction_aware():
    old = op("/p", "post", **body({"type": "object", "properties": {
        "id": {"type": "integer", "readOnly": True},
        "name": {"type": "string"}}}))
    new = op("/p", "post", **body({"type": "object", "properties": {
        "name": {"type": "string"}}}))
    # readOnly 字段本就不属于请求载荷，删除它不应对请求报破坏
    result = review(old, new)
    assert not any(f["rule"] == "PROPERTY_REMOVED" for f in result["findings"])


# ---------------------------------------------------------------- 循环引用
def test_circular_ref_does_not_recurse_forever():
    old = {
        "openapi": "3.0.3", "info": {"title": "T"},
        "paths": {"/p": {"get": {"responses": {"200": {
            "description": "ok",
            "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/Node"}}}}}}}},
        "components": {"schemas": {"Node": {
            "type": "object",
            "properties": {"child": {"$ref": "#/components/schemas/Node"}},
        }}},
    }
    new = {
        "openapi": "3.0.3", "info": {"title": "T"},
        "paths": {"/p": {"get": {"responses": {"200": {
            "description": "ok",
            "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/Node"}}}}}}}},
        "components": {"schemas": {"Node": {
            "type": "object",
            "properties": {"child": {"$ref": "#/components/schemas/Node"},
                           "label": {"type": "string"}},
        }}},
    }
    result = review(old, new)  # 不死循环即通过
    assert ("PROPERTY_ADDED", "non-breaking") in rules_of(result)


def test_circular_ref_type_change_detected_once():
    def build(prop_type):
        return {
            "openapi": "3.0.3", "info": {"title": "T"},
            "paths": {"/p": {"get": {"responses": {"200": {
                "description": "ok",
                "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Node"}}}}}}}},
            "components": {"schemas": {"Node": {
                "type": "object",
                "properties": {"value": {"type": prop_type},
                               "child": {"$ref": "#/components/schemas/Node"}},
            }}},
        }
    result = review(build("string"), build("integer"))
    type_changes = [f for f in result["findings"] if f["rule"] == "TYPE_CHANGED"]
    assert len(type_changes) == 1
    assert type_changes[0]["ref_new"] == ["#/components/schemas/Node"]


# ---------------------------------------------------------------- YAML
def test_yaml_loading():
    text = """
openapi: "3.0.3"
info:
  title: Yaml API
  version: 9.9.9
paths: {}
"""
    spec = Spec.parse(text)
    assert spec.fmt == "yaml"
    assert spec.title == "Yaml API"
    assert spec.version == "9.9.9"


def test_swagger2_rejected():
    with pytest.raises(Exception):
        Spec.parse('{"swagger": "2.0", "paths": {}}')
