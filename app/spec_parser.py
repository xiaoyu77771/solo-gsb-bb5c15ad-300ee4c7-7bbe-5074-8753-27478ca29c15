"""OpenAPI 文档加载、JSON Pointer / $ref 解析（可安全处理循环引用）。"""

from __future__ import annotations

import json
from typing import Any

import yaml

HTTP_METHODS = (
    "get", "put", "post", "delete", "options", "head", "patch", "trace",
)


class SpecError(ValueError):
    """规范无法解析或不是受支持的 OpenAPI 文档。"""


class Spec:
    def __init__(self, doc: dict[str, Any], fmt: str, raw: str):
        self.doc = doc
        self.fmt = fmt
        self.raw = raw
        self.resolver = Resolver(doc)

    # ---- 加载 -----------------------------------------------------------
    @classmethod
    def parse(cls, text: str, fmt: str | None = None) -> "Spec":
        text = text.lstrip("﻿").strip()
        if not text:
            raise SpecError("内容为空")
        if fmt is None:
            fmt = "yaml" if not text.startswith(("{", "[")) else "json"
        try:
            if fmt == "json":
                doc = json.loads(text)
            elif fmt in ("yaml", "yml"):
                doc = yaml.safe_load(text)
            else:
                raise SpecError(f"不支持的格式: {fmt}")
        except json.JSONDecodeError as exc:
            raise SpecError(f"JSON 解析失败: {exc}") from exc
        except yaml.YAMLError as exc:
            raise SpecError(f"YAML 解析失败: {exc}") from exc

        if not isinstance(doc, dict):
            raise SpecError("文档根节点必须是对象")
        version = str(doc.get("openapi", ""))
        if not version:
            raise SpecError(
                "仅支持 OpenAPI 3.x（未找到顶层 openapi 字段；Swagger 2.0 暂不支持）"
            )
        if not version.startswith("3"):
            raise SpecError(f"仅支持 OpenAPI 3.x，当前版本: {version}")
        return cls(doc, fmt, text)

    # ---- 基本信息 -------------------------------------------------------
    @property
    def title(self) -> str | None:
        info = self.doc.get("info")
        return info.get("title") if isinstance(info, dict) else None

    @property
    def version(self) -> str | None:
        info = self.doc.get("info")
        return info.get("version") if isinstance(info, dict) else None

    # ---- 遍历辅助 -------------------------------------------------------
    def iter_operations(self):
        """产出 (path, method, operation_dict, path_item_dict)。"""
        paths = self.doc.get("paths") or {}
        for path, item in paths.items():
            if not isinstance(item, dict):
                continue
            for method in HTTP_METHODS:
                op = item.get(method)
                if isinstance(op, dict):
                    yield path, method, op, item

    def effective_security(self, operation: dict[str, Any]) -> list:
        """返回操作实际生效的 security 数组（操作级覆盖全局）。"""
        if "security" in operation:
            return operation.get("security") or []
        return self.doc.get("security") or []


class Resolver:
    """JSON Pointer 寻址 + 内部 $ref 解析（带循环检测）。"""

    def __init__(self, doc: dict[str, Any]):
        self.doc = doc

    def resolve_pointer(self, pointer: str) -> Any:
        if pointer in ("", "#", "#/"):
            return self.doc
        p = pointer[1:] if pointer.startswith("#") else pointer
        node: Any = self.doc
        for part in p.split("/"):
            if part == "":
                continue
            token = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, list):
                try:
                    node = node[int(token)]
                except (ValueError, IndexError):
                    raise SpecError(f"JSON Pointer 越界: {pointer}") from None
            elif isinstance(node, dict):
                if token not in node:
                    raise SpecError(f"JSON Pointer 未找到节点: {pointer}")
                node = node[token]
            else:
                raise SpecError(f"JSON Pointer 无法继续寻址: {pointer}")
        return node

    @staticmethod
    def ref_name(ref: str) -> str:
        p = ref[1:] if ref.startswith("#") else ref
        return p.split("/")[-1].replace("~1", "/").replace("~0", "~")

    def resolve(
        self,
        node: Any,
        seen: frozenset[str] = frozenset(),
        ref_chain: tuple[str, ...] = (),
    ):
        """沿 $ref 解析直到非引用节点；返回 (target, ref_chain, cyclic)。"""
        chain = list(ref_chain)
        visited = set(seen)
        cyclic = False
        while isinstance(node, dict) and isinstance(node.get("$ref"), str):
            ref = node["$ref"]
            if not ref.startswith("#"):
                # 外部引用不解析，保留原样
                return node, tuple(chain), False
            if ref in visited:
                cyclic = True
                break
            visited.add(ref)
            chain.append(ref)
            node = self.resolve_pointer(ref)
        return node, tuple(chain), cyclic


def flatten_allof(
    schema: dict[str, Any],
    resolver: Resolver,
    seen: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """展开 allOf（用于结构比较），合并 type/properties/required 等字段。

    遇到循环引用时保留循环节点，比较阶段依靠栈检测终止。
    """
    target, chain, cyclic = resolver.resolve(schema, seen=seen)
    if not isinstance(target, dict):
        return target if isinstance(target, dict) else {}
    if cyclic:
        return target
    new_seen = seen | set(chain)

    parts = target.get("allOf")
    if not isinstance(parts, list) or not parts:
        return target

    merged: dict[str, Any] = {}
    required: set[str] = set()
    properties: dict[str, Any] = {}
    enum_values: list[Any] = []
    for part in parts:
        sub = flatten_allof(part, resolver, new_seen)
        for key, value in sub.items():
            if key in ("allOf",):
                continue
            if key == "required" and isinstance(value, list):
                required.update(value)
            elif key == "properties" and isinstance(value, dict):
                properties.update(value)
            elif key == "enum" and isinstance(value, list):
                enum_values.extend(value)
            elif key in merged:
                continue
            else:
                merged[key] = value
    if required:
        merged["required"] = list(required)
    if properties:
        merged["properties"] = properties
    if enum_values:
        merged["enum"] = enum_values
    # 顶层 target 自身字段优先
    for key, value in target.items():
        if key not in ("allOf", "required", "properties", "enum"):
            merged[key] = value
    return merged


# --------------------------------------------------------------------------
# $ref 展开树
# --------------------------------------------------------------------------
MAX_TREE_DEPTH = 8


def _ref_target(node: dict[str, Any]) -> str | None:
    ref = node.get("$ref")
    return ref if isinstance(ref, str) and ref.startswith("#") else None


def _node_kind(node: Any) -> str:
    if isinstance(node, dict):
        return "object"
    if isinstance(node, list):
        return "array"
    if node is None:
        return "null"
    return "scalar"


def build_expansion_tree(
    resolver: Resolver,
    pointer: str,
    max_depth: int = MAX_TREE_DEPTH,
) -> dict[str, Any]:
    """从给定 JSON Pointer 构造带 $ref 展开信息的树。"""
    root = resolver.resolve_pointer(pointer)

    def node_label(ref: str | None) -> str:
        if ref:
            return resolver.ref_name(ref)
        if pointer in ("", "#", "#/"):
            return "#"
        return pointer.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")

    def walk(
        node: Any,
        label: str,
        ref: str | None,
        path: str,
        seen: frozenset[str],
        depth: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "label": label,
            "path": path,
            "ref": ref,
            "kind": _node_kind(node),
            "cyclic": False,
            "truncated": False,
            "children": [],
        }
        if isinstance(node, dict):
            ref_target = _ref_target(node)
            if ref_target:
                payload["ref"] = ref_target
                payload["label"] = resolver.ref_name(ref_target)
                if ref_target in seen:
                    payload["cyclic"] = True
                    payload["kind"] = "cycle"
                    return payload
                if depth >= max_depth:
                    payload["truncated"] = True
                    payload["children"].append(
                        {"label": "… 深度超限", "path": "", "ref": None,
                         "kind": "truncated", "cyclic": False,
                         "truncated": True, "children": []}
                    )
                    return payload
                target = resolver.resolve_pointer(ref_target)
                child = walk(
                    target,
                    resolver.ref_name(ref_target),
                    ref_target,
                    ref_target,
                    seen | {ref_target},
                    depth + 1,
                )
                payload["children"] = [child]
                return payload

            children: list[dict[str, Any]] = []
            for key, value in node.items():
                child_path = f"{path}/{key.replace('~', '~0').replace('/', '~1')}" if path else f"/{key}"
                if isinstance(value, (dict, list)):
                    children.append(walk(value, key, None, child_path, seen, depth))
                else:
                    children.append(
                        {"label": key, "path": child_path, "ref": None,
                         "kind": "scalar", "value": _scalar_preview(value),
                         "cyclic": False, "truncated": False, "children": []}
                    )
            payload["children"] = children

        elif isinstance(node, list):
            children = []
            for index, value in enumerate(node):
                child_path = f"{path}/{index}" if path else f"/{index}"
                if isinstance(value, (dict, list)):
                    children.append(walk(value, str(index), None, child_path, seen, depth))
                else:
                    children.append(
                        {"label": str(index), "path": child_path, "ref": None,
                         "kind": "scalar", "value": _scalar_preview(value),
                         "cyclic": False, "truncated": False, "children": []}
                    )
            payload["children"] = children
        else:
            payload["value"] = _scalar_preview(node)
            payload["kind"] = "scalar"
        return payload

    initial_seen = frozenset({pointer}) if pointer.startswith("#/components/") else frozenset()
    return walk(root, node_label(None), None, pointer, initial_seen, 0)


def find_schema_pointer(doc: dict[str, Any], hint: str) -> str | None:
    """在文档中定位一个 schema 的 JSON Pointer；hint 可为完整 Pointer 或组件名。"""
    resolver = Resolver(doc)
    candidates = [hint]
    if not hint.startswith("#"):
        candidates = [
            f"#/components/schemas/{hint}",
            f"#/components/parameters/{hint}",
            f"#/components/requestBodies/{hint}",
            f"#/components/responses/{hint}",
        ]
    for pointer in candidates:
        try:
            resolver.resolve_pointer(pointer)
            return pointer
        except SpecError:
            continue
    return None


def _scalar_preview(value: Any, limit: int = 80) -> str:
    if value is None:
        return "null"
    text = str(value)
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text
