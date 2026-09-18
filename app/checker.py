"""OpenAPI 新旧版本语义兼容性比对引擎。

输出的每条发现包含：规则编号、严重级别（breaking/warning/non-breaking）、
请求/响应方向、新旧两侧的 JSON 路径与 $ref 链，以及可读原因。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .spec_parser import HTTP_METHODS, Resolver, Spec, flatten_allof

# --------------------------------------------------------------------------
# 规则目录
# --------------------------------------------------------------------------
RULES: dict[str, dict[str, str]] = {
    "OPERATION_REMOVED": {"title": "接口被删除", "category": "operation"},
    "OPERATION_ADDED": {"title": "新增接口", "category": "operation"},
    "OPERATION_DEPRECATED": {"title": "接口被标记弃用", "category": "operation"},
    "OPERATION_ID_CHANGED": {"title": "operationId 变化", "category": "operation"},
    "PATH_PARAMETER_MISSING": {"title": "路径参数在操作中缺失", "category": "parameter"},
    "PARAM_REMOVED": {"title": "参数被删除", "category": "parameter"},
    "PARAM_ADDED": {"title": "新增参数", "category": "parameter"},
    "PARAM_REQUIRED": {"title": "参数变为必填", "category": "parameter"},
    "PARAM_OPTIONAL": {"title": "参数变为可选", "category": "parameter"},
    "PARAM_SERIALIZATION": {"title": "参数序列化方式变化", "category": "parameter"},
    "PARAM_DEPRECATED": {"title": "参数被标记弃用", "category": "parameter"},
    "PARAM_TYPE_CHANGED": {"title": "参数类型变化", "category": "parameter"},
    "SECURITY_ADDED": {"title": "新增安全要求", "category": "security"},
    "SECURITY_REMOVED": {"title": "移除安全要求", "category": "security"},
    "SECURITY_CHANGED": {"title": "安全要求变化", "category": "security"},
    "REQUEST_BODY_REMOVED": {"title": "请求体被删除", "category": "request"},
    "REQUEST_BODY_ADDED": {"title": "新增请求体", "category": "request"},
    "REQUEST_BODY_REQUIRED": {"title": "请求体变为必填", "category": "request"},
    "REQUEST_BODY_OPTIONAL": {"title": "请求体变为可选", "category": "request"},
    "RESPONSE_REMOVED": {"title": "响应状态码被删除", "category": "response"},
    "RESPONSE_ADDED": {"title": "新增响应状态码", "category": "response"},
    "MEDIA_TYPE_REMOVED": {"title": "媒体类型被删除", "category": "content"},
    "MEDIA_TYPE_ADDED": {"title": "新增媒体类型", "category": "content"},
    "TYPE_CHANGED": {"title": "字段类型变化", "category": "schema"},
    "TYPE_WIDENED": {"title": "数值类型放宽(int→number)", "category": "schema"},
    "FORMAT_CHANGED": {"title": "字段 format 变化", "category": "schema"},
    "ENUM_VALUE_REMOVED": {"title": "枚举值被删除", "category": "schema"},
    "ENUM_VALUE_ADDED": {"title": "新增枚举值", "category": "schema"},
    "ENUM_CONSTRAINT": {"title": "枚举约束变化", "category": "schema"},
    "PROPERTY_REMOVED": {"title": "字段被删除", "category": "schema"},
    "PROPERTY_ADDED": {"title": "新增字段", "category": "schema"},
    "PROPERTY_VISIBILITY": {"title": "readOnly/writeOnly 变化", "category": "schema"},
    "PROPERTY_DEPRECATED": {"title": "字段被标记弃用", "category": "schema"},
    "REQUIRED_ADDED": {"title": "字段变为必填", "category": "schema"},
    "REQUIRED_REMOVED": {"title": "字段变为可选", "category": "schema"},
    "ADDITIONAL_PROPERTIES": {"title": "additionalProperties 变化", "category": "schema"},
    "CONSTRAINT_CHANGED": {"title": "取值约束变化", "category": "schema"},
    "PATTERN_CHANGED": {"title": "正则 pattern 变化", "category": "schema"},
    "NULLABLE_CHANGED": {"title": "可空性变化", "category": "schema"},
    "ARRAY_ITEMS_CHANGED": {"title": "数组元素结构变化", "category": "schema"},
    "COMPOSITION_CHANGED": {"title": "oneOf/anyOf 组合结构变化", "category": "schema"},
}

SEVERITY_LABEL = {
    "breaking": "破坏性变更",
    "warning": "潜在风险",
    "non-breaking": "无破坏/新增",
}
_SEV_ORDER = {"breaking": 0, "warning": 1, "non-breaking": 2}


def _seg(path: str, segment: Any) -> str:
    if isinstance(segment, int) or (isinstance(segment, str) and segment.isdigit()):
        return f"{path}[{segment}]"
    esc = str(segment).replace("\\", "\\\\").replace("'", "\\'")
    return f"{path}['{esc}']"


def _preview(value: Any, limit: int = 60) -> str:
    if value is None:
        return "未定义"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class Ctx:
    """一次 schema 递归比较的定位上下文。"""

    path_old: str
    path_new: str
    location: str  # request | response
    ref_old: tuple[str, ...] = ()
    ref_new: tuple[str, ...] = ()
    status_code: str | None = None
    media_type: str | None = None
    stack: Any = field(default=None, repr=False)

    def at(
        self,
        seg_old: Any | None = None,
        seg_new: Any | None = None,
        *,
        ref_old: tuple[str, ...] | None = None,
        ref_new: tuple[str, ...] | None = None,
    ) -> "Ctx":
        return replace(
            self,
            path_old=_seg(self.path_old, seg_old) if seg_old is not None else self.path_old,
            path_new=_seg(self.path_new, seg_new) if seg_new is not None else self.path_new,
            ref_old=ref_old if ref_old is not None else self.ref_old,
            ref_new=ref_new if ref_new is not None else self.ref_new,
        )


class Comparator:
    def __init__(self, old_spec: Spec, new_spec: Spec):
        self.old = old_spec
        self.new = new_spec
        self.r_old = old_spec.resolver
        self.r_new = new_spec.resolver
        self.findings: list[dict[str, Any]] = []
        self._seq = 0

    def emit(
        self,
        rule: str,
        severity: str,
        message: str,
        ctx: Ctx | None = None,
        *,
        path_old: str | None = None,
        path_new: str | None = None,
        location: str | None = None,
        ref_old: tuple[str, ...] = (),
        ref_new: tuple[str, ...] = (),
        method: str | None = None,
        api_path: str | None = None,
        param_name: str | None = None,
        param_in: str | None = None,
        status_code: str | None = None,
        media_type: str | None = None,
        old_value: Any = None,
        new_value: Any = None,
        category: str | None = None,
    ) -> None:
        self._seq += 1
        self.findings.append(
            {
                "id": self._seq,
                "rule": rule,
                "title": RULES[rule]["title"],
                "category": category or RULES[rule]["category"],
                "severity": severity,
                "message": message,
                "location": location or (ctx.location if ctx else ""),
                "method": method,
                "path": api_path,
                "param_name": param_name,
                "param_in": param_in,
                "status_code": status_code,
                "media_type": media_type,
                "path_old": path_old or (ctx.path_old if ctx else None),
                "path_new": path_new or (ctx.path_new if ctx else None),
                "ref_old": list(ref_old or (ctx.ref_old if ctx else ())),
                "ref_new": list(ref_new or (ctx.ref_new if ctx else ())),
                "old_value": _preview(old_value) if old_value is not None else None,
                "new_value": _preview(new_value) if new_value is not None else None,
            }
        )

    # ======================================================================
    # 顶层入口
    # ======================================================================
    def compare(self) -> list[dict[str, Any]]:
        self._compare_operations()
        self.findings.sort(
            key=lambda f: (
                _SEV_ORDER[f["severity"]],
                f["path"] or "",
                f["method"] or "",
                f["id"],
            )
        )
        for index, finding in enumerate(self.findings, 1):
            finding["id"] = index
        return self.findings

    def _compare_operations(self) -> None:
        old_ops: dict[tuple[str, str], tuple[dict, dict]] = {}
        for path, method, op, item in self.old.iter_operations():
            old_ops[(path, method)] = (op, item)
        new_ops: dict[tuple[str, str], tuple[dict, dict]] = {}
        for path, method, op, item in self.new.iter_operations():
            new_ops[(path, method)] = (op, item)

        for key in old_ops.keys() - new_ops.keys():
            path, method = key
            self.emit(
                "OPERATION_REMOVED", "breaking",
                f"{method.upper()} {path} 在新版本中被删除，调用方将收到 404。",
                path_old=f"$.paths['{path}'].{method}",
                method=method, api_path=path, location="",
            )
        for key in new_ops.keys() - old_ops.keys():
            path, method = key
            self.emit(
                "OPERATION_ADDED", "non-breaking",
                f"新增接口 {method.upper()} {path}。",
                path_new=f"$.paths['{path}'].{method}",
                method=method, api_path=path, location="",
            )

        for key in sorted(old_ops.keys() & new_ops.keys()):
            path, method = key
            op_old, item_old = old_ops[key]
            op_new, item_new = new_ops[key]
            self._compare_operation(path, method, op_old, op_new, item_old, item_new)

    def _compare_operation(
        self, path, method, op_old, op_new, item_old, item_new
    ) -> None:
        base_old = f"$.paths['{path}'].{method}"
        base_new = f"$.paths['{path}'].{method}"

        if not op_old.get("deprecated") and op_new.get("deprecated"):
            self.emit("OPERATION_DEPRECATED", "warning",
                      f"{method.upper()} {path} 被标记为 deprecated。",
                      path_old=base_old, path_new=base_new,
                      method=method, api_path=path)
        if op_old.get("operationId") != op_new.get("operationId") and (
            op_old.get("operationId") or op_new.get("operationId")
        ):
            self.emit("OPERATION_ID_CHANGED", "warning",
                      f"operationId 由 {op_old.get('operationId')!r} 变为 "
                      f"{op_new.get('operationId')!r}，可能影响代码生成的客户端。",
                      path_old=_seg(base_old, "operationId"),
                      path_new=_seg(base_new, "operationId"),
                      old_value=op_old.get("operationId"),
                      new_value=op_new.get("operationId"),
                      method=method, api_path=path)

        self._compare_security(path, method, op_old, op_new, base_old, base_new)
        self._compare_parameters(path, method, op_old, op_new, item_old, item_new,
                                 base_old, base_new)
        self._compare_request_body(path, method, op_old, op_new, base_old, base_new)
        self._compare_responses(path, method, op_old, op_new, base_old, base_new)

    # ======================================================================
    # security
    # ======================================================================
    def _compare_security(self, path, method, op_old, op_new, base_old, base_new):
        def names(sec_list):
            return {frozenset(s.keys()) for s in sec_list if isinstance(s, dict)}

        old_sets = names(self.old.effective_security(op_old))
        new_sets = names(self.new.effective_security(op_new))
        if old_sets == new_sets:
            return
        added = new_sets - old_sets
        removed = old_sets - new_sets
        if added and not removed and not old_sets:
            rule, sev = "SECURITY_ADDED", "breaking"
            msg = f"{method.upper()} {path} 新增了安全认证要求，未携带凭据的旧客户端将被拒绝。"
        elif removed and not added:
            rule, sev = "SECURITY_REMOVED", "warning"
            msg = f"{method.upper()} {path} 移除了安全认证要求，请确认接口暴露面。"
        else:
            rule, sev = "SECURITY_CHANGED", "breaking"
            msg = f"{method.upper()} {path} 的安全要求发生变化，旧客户端使用的认证方式可能失效。"
        self.emit(rule, sev, msg, path_old=_seg(base_old, "security"),
                  path_new=_seg(base_new, "security"), method=method, api_path=path)

    # ======================================================================
    # parameters
    # ======================================================================
    @staticmethod
    def _parameters(op, item):
        """合并参数（操作级覆盖路径级），并记录每个参数在文档中的真实位置。"""
        merged = {}

        def add(params, base):
            if not isinstance(params, list):
                return
            for index, param in enumerate(params):
                if isinstance(param, dict) and "name" in param and "in" in param:
                    merged[(param["in"], param["name"])] = (param, f"{base}[{index}]")

        add(item.get("parameters"), "paths.parameters")
        add(op.get("parameters"), "parameters")
        return merged

    def _compare_parameters(self, path, method, op_old, op_new, item_old, item_new,
                            base_old, base_new):
        path_base_old = f"$.paths['{path}']"
        path_base_new = f"$.paths['{path}']"
        old_params = self._parameters(op_old, item_old)
        new_params = self._parameters(op_new, item_new)

        def param_path(base, item_base, rel):
            return f"{item_base}.{rel}" if rel.startswith("paths.") else f"{base}.{rel}"

        # 路径模板变量必须在参数列表中声明
        for var in _path_variables(path):
            if ("path", var) not in new_params and ("path", var) in old_params:
                self.emit("PATH_PARAMETER_MISSING", "breaking",
                          f"路径变量 {var!r} 在新版本中缺少 path 参数声明。",
                          path_new=base_new, method=method, api_path=path,
                          param_name=var, param_in="path")

        for key in old_params.keys() - new_params.keys():
            where, name = key
            p_path = param_path(base_old, path_base_old, old_params[key][1])
            self.emit("PARAM_REMOVED", "breaking",
                      f"{where} 参数 {name!r} 被删除，依赖该参数的请求行为可能改变。",
                      path_old=p_path,
                      method=method, api_path=path,
                      param_name=name, param_in=where, location="request")
        for key in new_params.keys() - old_params.keys():
            where, name = key
            p, rel = new_params[key]
            p_path = param_path(base_new, path_base_new, rel)
            required = bool(p.get("required")) and where != "path"
            self.emit(
                "PARAM_ADDED", "breaking" if required else "non-breaking",
                f"新增 {where} 参数 {name!r}"
                + ("（必填，旧请求将被拒绝）。" if required else "（可选）。"),
                path_new=p_path,
                method=method, api_path=path,
                param_name=name, param_in=where, location="request",
            )

        for key in sorted(old_params.keys() & new_params.keys()):
            where, name = key
            po = old_params[key][0]
            pn = new_params[key][0]
            p_old_path = param_path(base_old, path_base_old, old_params[key][1])
            p_new_path = param_path(base_new, path_base_new, new_params[key][1])
            common = dict(method=method, api_path=path,
                          param_name=name, param_in=where)

            if not po.get("required") and pn.get("required") and where != "path":
                self.emit("PARAM_REQUIRED", "breaking",
                          f"{where} 参数 {name!r} 变为必填，旧请求将被拒绝。",
                          path_old=p_old_path, path_new=p_new_path,
                          location="request", **common)
            elif po.get("required") and not pn.get("required") and where != "path":
                self.emit("PARAM_OPTIONAL", "non-breaking",
                          f"{where} 参数 {name!r} 变为可选。",
                          path_old=p_old_path, path_new=p_new_path,
                          location="request", **common)

            for field_name, rule in (("style", "PARAM_SERIALIZATION"),
                                     ("explode", "PARAM_SERIALIZATION")):
                vo, vn = po.get(field_name), pn.get(field_name)
                if vo is not None and vn is not None and vo != vn:
                    self.emit(rule, "breaking",
                              f"{where} 参数 {name!r} 的 {field_name} 由 {vo!r} 变为 {vn!r}，"
                              "线传输格式改变。",
                              path_old=p_old_path, path_new=p_new_path,
                              location="request", old_value=vo, new_value=vn, **common)

            if po.get("allowEmptyValue") and not pn.get("allowEmptyValue"):
                self.emit("PARAM_SERIALIZATION", "warning",
                          f"{where} 参数 {name!r} 不再允许空值。",
                          path_old=p_old_path, path_new=p_new_path,
                          location="request", **common)

            if not po.get("deprecated") and pn.get("deprecated"):
                self.emit("PARAM_DEPRECATED", "warning",
                          f"{where} 参数 {name!r} 被标记为 deprecated。",
                          path_old=p_old_path, path_new=p_new_path,
                          location="request", **common)

            so, sn = po.get("schema"), pn.get("schema")
            if isinstance(so, dict) and isinstance(sn, dict):
                ctx = Ctx(_seg(p_old_path, "schema"),
                          _seg(p_new_path, "schema"), "request")
                self.compare_schema(so, sn, ctx, meta=common)

    # ======================================================================
    # requestBody
    # ======================================================================
    def _compare_request_body(self, path, method, op_old, op_new, base_old, base_new):
        bo, bn = op_old.get("requestBody"), op_new.get("requestBody")
        bo = self._resolve_if_ref(bo, self.r_old)
        bn = self._resolve_if_ref(bn, self.r_new)
        rb_old = _seg(base_old, "requestBody")
        rb_new = _seg(base_new, "requestBody")

        if bo is None and bn is not None:
            required = bool(bn.get("required"))
            self.emit("REQUEST_BODY_ADDED", "breaking" if required else "non-breaking",
                      f"{method.upper()} {path} 新增请求体"
                      + ("且为必填，旧客户端的请求将被拒绝。" if required else "（可选）。"),
                      path_new=rb_new, method=method, api_path=path, location="request")
            return
        if bo is not None and bn is None:
            self.emit("REQUEST_BODY_REMOVED", "breaking",
                      f"{method.upper()} {path} 的请求体被删除，发送 body 的旧客户端可能失败。",
                      path_old=rb_old, method=method, api_path=path, location="request")
            return
        if bo is None or bn is None:
            return

        if not bo.get("required") and bn.get("required"):
            self.emit("REQUEST_BODY_REQUIRED", "breaking",
                      f"{method.upper()} {path} 请求体变为必填。",
                      path_old=rb_old, path_new=rb_new,
                      method=method, api_path=path, location="request")
        elif bo.get("required") and not bn.get("required"):
            self.emit("REQUEST_BODY_OPTIONAL", "non-breaking",
                      f"{method.upper()} {path} 请求体变为可选。",
                      path_old=rb_old, path_new=rb_new,
                      method=method, api_path=path, location="request")

        self._compare_content(bo.get("content"), bn.get("content"),
                              rb_old, rb_new, "request",
                              method=method, api_path=path)

    # ======================================================================
    # responses
    # ======================================================================
    def _compare_responses(self, path, method, op_old, op_new, base_old, base_new):
        ro = self._resolve_if_ref(op_old.get("responses"), self.r_old) or {}
        rn = self._resolve_if_ref(op_new.get("responses"), self.r_new) or {}
        codes_old = {k for k in ro.keys() if k != "headers"}
        codes_new = {k for k in rn.keys() if k != "headers"}

        for code in sorted(codes_old - codes_new):
            self.emit("RESPONSE_REMOVED", "breaking",
                      f"响应 {code} 被删除，处理该状态码的客户端逻辑将失效。",
                      path_old=f"{base_old}.responses['{code}']",
                      method=method, api_path=path, status_code=str(code),
                      location="response")
        for code in sorted(codes_new - codes_old):
            self.emit("RESPONSE_ADDED", "non-breaking",
                      f"新增响应 {code}。",
                      path_new=f"{base_new}.responses['{code}']",
                      method=method, api_path=path, status_code=str(code),
                      location="response")

        for code in sorted(codes_old & codes_new):
            resp_old = self._resolve_if_ref(ro[code], self.r_old)
            resp_new = self._resolve_if_ref(rn[code], self.r_new)
            if not isinstance(resp_old, dict) or not isinstance(resp_new, dict):
                continue
            resp_path_old = f"{base_old}.responses['{code}']"
            resp_path_new = f"{base_new}.responses['{code}']"
            self._compare_content(
                resp_old.get("content"), resp_new.get("content"),
                resp_path_old, resp_path_new, "response",
                method=method, api_path=path, status_code=str(code),
            )

    # ======================================================================
    # content (media types)
    # ======================================================================
    def _compare_content(self, co, cn, path_old, path_new, location, **meta):
        co = co if isinstance(co, dict) else {}
        cn = cn if isinstance(cn, dict) else {}
        for mt in sorted(co.keys() - cn.keys()):
            self.emit("MEDIA_TYPE_REMOVED", "breaking",
                      f"媒体类型 {mt} 被删除，使用该内容类型的"
                      f"{'请求' if location == 'request' else '响应'}将无法协商。",
                      path_old=path_old,
                      media_type=mt, location=location,
                      category="request" if location == "request" else "response",
                      **meta)
        for mt in sorted(cn.keys() - co.keys()):
            self.emit("MEDIA_TYPE_ADDED", "non-breaking",
                      f"新增媒体类型 {mt}。",
                      path_new=path_new,
                      media_type=mt, location=location, **meta)
        for mt in sorted(co.keys() & cn.keys()):
            so = co[mt].get("schema") if isinstance(co[mt], dict) else None
            sn = cn[mt].get("schema") if isinstance(cn[mt], dict) else None
            if isinstance(so, dict) and isinstance(sn, dict):
                ctx = Ctx(f"{path_old}.content['{mt}'].schema",
                          f"{path_new}.content['{mt}'].schema",
                          location, media_type=mt)
                self.compare_schema(so, sn, ctx, meta=meta)

    @staticmethod
    def _resolve_if_ref(node, resolver):
        if isinstance(node, dict) and isinstance(node.get("$ref"), str):
            ref = node["$ref"]
            if ref.startswith("#"):
                try:
                    return resolver.resolve_pointer(ref)
                except Exception:
                    return node
        return node

    # ======================================================================
    # schema 递归比对（支持循环 $ref）
    # ======================================================================
    def compare_schema(
        self,
        a: dict[str, Any],
        b: dict[str, Any],
        ctx: Ctx,
        meta: dict[str, Any] | None = None,
    ) -> None:
        meta = meta or {}
        ta, ca, cyc_a = self.r_old.resolve(a, seen=frozenset(ctx.ref_old))
        tb, cb, cyc_b = self.r_new.resolve(b, seen=frozenset(ctx.ref_new))
        if cyc_a or cyc_b:
            # 回到已经比较过的循环节点，终止递归
            return

        fa = flatten_allof(ta, self.r_old, seen=frozenset(ctx.ref_old + ca))
        fb = flatten_allof(tb, self.r_new, seen=frozenset(ctx.ref_new + cb))
        if not isinstance(fa, dict) or not isinstance(fb, dict):
            return

        refs_old = ctx.ref_old + ca
        refs_new = ctx.ref_new + cb
        # 以 (当前 $ref 或内联锚点, 逻辑 JSON 路径) 作为同一性：
        # 同一 schema 出现在不同属性位置仍需比较，只有沿同一路径绕回自身才算循环。
        anchor_old = refs_old[-1] if refs_old else f"inline:{ctx.path_old}"
        anchor_new = refs_new[-1] if refs_new else f"inline:{ctx.path_new}"
        stack = ctx.stack if isinstance(ctx.stack, set) else set()
        key = (anchor_old, ctx.path_old, anchor_new, ctx.path_new)
        if key in stack:
            return
        stack.add(key)
        try:
            self._compare_resolved(
                fa, fb,
                replace(ctx, ref_old=refs_old, ref_new=refs_new, stack=stack),
                meta,
            )
        finally:
            stack.discard(key)

    def _compare_resolved(self, a, b, ctx: Ctx, meta: dict[str, Any]) -> None:
        loc = ctx.location

        # ---- 组合关键字 -------------------------------------------------
        for kw in ("oneOf", "anyOf"):
            lo, ln = a.get(kw), b.get(kw)
            if isinstance(lo, list) and isinstance(ln, list):
                if (a.get("oneOf") is not None and b.get("anyOf") is not None and
                        not b.get("oneOf") and not a.get("anyOf")) or \
                   (a.get("anyOf") is not None and b.get("oneOf") is not None and
                        not b.get("anyOf") and not a.get("oneOf")):
                    self.emit("COMPOSITION_CHANGED", "breaking",
                              f"组合方式在 oneOf 与 anyOf 之间变化，校验语义不同。",
                              ctx, location=loc, **meta)
                if len(lo) != len(ln):
                    self.emit("COMPOSITION_CHANGED", "breaking",
                              f"{kw} 分支数量由 {len(lo)} 变为 {len(ln)}。",
                              ctx, location=loc, old_value=len(lo),
                              new_value=len(ln), **meta)
                for i in range(min(len(lo), len(ln))):
                    if isinstance(lo[i], dict) and isinstance(ln[i], dict):
                        self.compare_schema(lo[i], ln[i], ctx.at(i, i), meta)

        # ---- type / format ---------------------------------------------
        to, tn = a.get("type"), b.get("type")
        if isinstance(to, str) and isinstance(tn, str) and to != tn:
            if to == "integer" and tn == "number":
                if loc == "request":
                    self.emit("TYPE_WIDENED", "non-breaking",
                              "类型由 integer 放宽为 number，旧的整数请求仍然合法。",
                              ctx, location=loc, old_value=to, new_value=tn, **meta)
                else:
                    self.emit("TYPE_CHANGED", "breaking",
                              "响应类型由 integer 变为 number，客户端可能收到非整数值。",
                              ctx, location=loc, old_value=to, new_value=tn, **meta)
            else:
                self.emit("TYPE_CHANGED", "breaking",
                          f"类型由 {to!r} 变为 {tn!r}，原有数据可能无法通过校验或被正确解析。",
                          ctx, location=loc, old_value=to, new_value=tn, **meta)
            return  # 类型已不同，结构比较无意义

        fo, fn = a.get("format"), b.get("format")
        if fo != fn:
            self._compare_format(fo, fn, to or tn, ctx, meta)

        # ---- enum -------------------------------------------------------
        self._compare_enum(a.get("enum"), b.get("enum"), ctx, meta)

        # ---- nullable ---------------------------------------------------
        no_, nn_ = bool(a.get("nullable")), bool(b.get("nullable"))
        if no_ != nn_:
            if loc == "request":
                sev = "breaking" if no_ and not nn_ else "non-breaking"
                msg = "请求字段不再允许 null。" if sev == "breaking" else "请求字段现在允许 null。"
            else:
                sev = "breaking" if not no_ and nn_ else "non-breaking"
                msg = "响应字段现在可能返回 null，客户端需要处理空值。" if sev == "breaking" \
                    else "响应字段不再为 null。"
            self.emit("NULLABLE_CHANGED", sev, msg, ctx, location=loc,
                      old_value=no_, new_value=nn_, **meta)

        # ---- deprecated -------------------------------------------------
        if not a.get("deprecated") and b.get("deprecated"):
            self.emit("PROPERTY_DEPRECATED", "warning",
                      "字段被标记为 deprecated。", ctx, location=loc, **meta)

        # ---- object -----------------------------------------------------
        if (not to or to == "object") and self._has_properties(a, b):
            self._compare_object(a, b, ctx, meta)
        if isinstance(a.get("additionalProperties"), (bool, dict)) or \
           isinstance(b.get("additionalProperties"), (bool, dict)):
            self._compare_additional_props(a, b, ctx, meta)

        # ---- array ------------------------------------------------------
        if to == "array" or tn == "array":
            io_, in_ = a.get("items"), b.get("items")
            if isinstance(io_, dict) and isinstance(in_, dict):
                self.compare_schema(io_, in_, ctx.at("items", "items"), meta)

        # ---- 数值 / 长度约束 --------------------------------------------
        for field_name, sense in (
            ("minimum", "min"), ("maximum", "max"),
            ("minLength", "min"), ("maxLength", "max"),
            ("minItems", "min"), ("maxItems", "max"),
        ):
            self._compare_bound(a.get(field_name), b.get(field_name),
                                field_name, sense, ctx, meta)
        for field_name in ("pattern",):
            vo, vn = a.get(field_name), b.get(field_name)
            if vo != vn and (vo is not None or vn is not None):
                if loc == "request":
                    sev = "non-breaking" if vn is None else "breaking"
                else:
                    sev = "breaking" if vo is not None else "non-breaking"
                if vo is not None and vn is not None:
                    sev = "breaking"
                self.emit("PATTERN_CHANGED", sev,
                          f"正则约束 pattern 变化（{_preview(vo)} → {_preview(vn)}）。",
                          ctx, location=loc, old_value=vo, new_value=vn, **meta)

    # ------------------------------------------------------------------
    @staticmethod
    def _has_properties(a, b):
        return isinstance(a.get("properties"), dict) or isinstance(b.get("properties"), dict)

    def _compare_format(self, fo, fn, type_, ctx, meta):
        loc = ctx.location
        if fo is not None and fn is not None:
            widen = {"int32": "int64", "float": "double"}
            if fo in widen and widen[fo] == fn:
                sev = "non-breaking" if loc == "request" else "breaking"
            else:
                sev = "breaking"
            self.emit("FORMAT_CHANGED", sev,
                      f"format 由 {fo!r} 变为 {fn!r}，取值范围或语义改变。",
                      ctx, location=loc, old_value=fo, new_value=fn, **meta)
        elif fn is not None:  # 新增加约束
            self.emit("FORMAT_CHANGED",
                      "breaking" if loc == "request" else "non-breaking",
                      f"新增 format 约束 {fn!r}。",
                      ctx, location=loc, new_value=fn, **meta)
        else:  # 约束被移除
            self.emit("FORMAT_CHANGED",
                      "non-breaking" if loc == "request" else "breaking",
                      f"format 约束 {fo!r} 被移除。",
                      ctx, location=loc, old_value=fo, **meta)

    def _compare_enum(self, eo, en, ctx, meta):
        loc = ctx.location
        if not isinstance(eo, list) and not isinstance(en, list):
            return
        old_set = set(eo or [])
        new_set = set(en or [])
        removed = [repr(v) for v in (eo or []) if v not in new_set]
        added = [repr(v) for v in (en or []) if v not in old_set]

        if isinstance(eo, list) and not isinstance(en, list):
            # 枚举约束被移除
            self.emit("ENUM_CONSTRAINT",
                      "non-breaking" if loc == "request" else "breaking",
                      "请求枚举约束被移除，服务端接受范围扩大。" if loc == "request"
                      else "响应枚举约束被移除，可能出现客户端未知的新值。",
                      ctx, location=loc, old_value=eo, **meta)
            return
        if not isinstance(eo, list) and isinstance(en, list):
            self.emit("ENUM_CONSTRAINT",
                      "breaking" if loc == "request" else "non-breaking",
                      "请求新增枚举约束，不在列表内的旧请求值将被拒绝。" if loc == "request"
                      else "响应新增枚举约束。",
                      ctx, location=loc, new_value=en, **meta)
            return

        if removed:
            sev = "breaking" if loc == "request" else "non-breaking"
            msg = ("删除枚举值 " + "、".join(removed) + "，使用这些值的旧请求将被拒绝。"
                   if loc == "request"
                   else "删除枚举值 " + "、".join(removed) + "。")
            self.emit("ENUM_VALUE_REMOVED", sev, msg, ctx, location=loc,
                      old_value=removed, new_value=[], **meta)
        if added:
            sev = "non-breaking" if loc == "request" else "breaking"
            msg = ("新增枚举值 " + "、".join(added) + "。"
                   if loc == "request"
                   else "新增枚举值 " + "、".join(added) + "，客户端需要能处理未知值。")
            self.emit("ENUM_VALUE_ADDED", sev, msg, ctx, location=loc,
                      old_value=[], new_value=added, **meta)

    def _compare_object(self, a, b, ctx, meta):
        loc = ctx.location
        po = a.get("properties") if isinstance(a.get("properties"), dict) else {}
        pn = b.get("properties") if isinstance(b.get("properties"), dict) else {}
        req_old = set(a.get("required") or [])
        req_new = set(b.get("required") or [])

        for name in sorted(po.keys() - pn.keys()):
            if self._skip_property(po[name], loc, self.r_old):
                continue
            was_required = name in req_old
            sev = "breaking" if (loc == "response" or was_required) else "non-breaking"
            msg = (f"响应字段 {name!r} 被删除，读取该字段的客户端将失效。"
                   if loc == "response"
                   else f"请求字段 {name!r} 被删除"
                        + ("（原为必填，旧请求将被拒绝）。" if was_required else "。"))
            self.emit("PROPERTY_REMOVED", sev, msg,
                      ctx.at(name, None), location=loc, **meta)

        for name in sorted(pn.keys() - po.keys()):
            if self._skip_property(pn[name], loc, self.r_new):
                continue
            is_required = name in req_new
            if loc == "request":
                sev = "breaking" if is_required else "non-breaking"
                msg = (f"新增必填请求字段 {name!r}，缺少该字段的旧请求将被拒绝。"
                       if is_required else f"新增可选请求字段 {name!r}。")
            else:
                sev = "non-breaking"
                msg = f"新增响应字段 {name!r}。"
            self.emit("PROPERTY_ADDED", sev, msg,
                      ctx.at(None, name), location=loc, **meta)

        for name in sorted(po.keys() & pn.keys()):
            so, sn = po[name], pn[name]
            child = ctx.at(name, name)
            vo = self._visibility(so, self.r_old)
            vn = self._visibility(sn, self.r_new)
            if vo != vn and "skip" in (vo, vn):
                self.emit("PROPERTY_VISIBILITY", "breaking",
                          f"字段 {name!r} 的 readOnly/writeOnly 由 {vo} 变为 {vn}，"
                          "其在请求/响应中的可见性改变。",
                          child, location=loc, **meta)
                continue
            if self._skip_property(so, loc, self.r_old) and \
               self._skip_property(sn, loc, self.r_new):
                continue
            if isinstance(so, dict) and isinstance(sn, dict):
                # 必填性变化（字段仍然存在）
                old_req, new_req = name in req_old, name in req_new
                if old_req != new_req:
                    if loc == "request":
                        if new_req:
                            self.emit("REQUIRED_ADDED", "breaking",
                                      f"请求字段 {name!r} 变为必填。",
                                      child, location=loc, **meta)
                        else:
                            self.emit("REQUIRED_REMOVED", "non-breaking",
                                      f"请求字段 {name!r} 变为可选。",
                                      child, location=loc, **meta)
                    else:
                        if old_req and not new_req:
                            self.emit("REQUIRED_REMOVED", "breaking",
                                      f"响应字段 {name!r} 变为可选，客户端可能读到缺失值。",
                                      child, location=loc, **meta)
                        elif not old_req and new_req:
                            self.emit("REQUIRED_ADDED", "non-breaking",
                                      f"响应字段 {name!r} 变为必有。",
                                      child, location=loc, **meta)
                self.compare_schema(so, sn, child, meta)

    @staticmethod
    def _visibility(schema, resolver: Resolver) -> str:
        target, _, _ = resolver.resolve(schema) if isinstance(schema, dict) else (schema, (), False)
        if isinstance(target, dict):
            if target.get("writeOnly"):
                return "writeOnly"
            if target.get("readOnly"):
                return "readOnly"
        return "normal"

    @staticmethod
    def _skip_property(schema, location: str, resolver: Resolver) -> bool:
        target, _, _ = resolver.resolve(schema) if isinstance(schema, dict) else (schema, (), False)
        if not isinstance(target, dict):
            return False
        if location == "request" and target.get("readOnly"):
            return True
        if location == "response" and target.get("writeOnly"):
            return True
        return False

    def _compare_additional_props(self, a, b, ctx, meta):
        ao = a.get("additionalProperties", True)
        an = b.get("additionalProperties", True)
        loc = ctx.location
        if ao == an:
            if isinstance(ao, dict) and isinstance(an, dict):
                self.compare_schema(ao, an, ctx.at("additionalProperties",
                                                   "additionalProperties"), meta)
            return
        # 由允许（true/schema）变为 false：请求侧破坏
        if an is False and ao is not False and loc == "request":
            self.emit("ADDITIONAL_PROPERTIES", "breaking",
                      "additionalProperties 变为 false，携带额外字段的旧请求将被拒绝。",
                      ctx, location=loc, old_value=ao, new_value=an, **meta)
        elif ao is False and an is not False:
            self.emit("ADDITIONAL_PROPERTIES", "non-breaking",
                      "additionalProperties 由 false 放开，允许额外字段。",
                      ctx, location=loc, old_value=ao, new_value=an, **meta)
        elif isinstance(ao, dict) and isinstance(an, dict):
            self.compare_schema(ao, an, ctx.at("additionalProperties",
                                               "additionalProperties"), meta)
        else:
            self.emit("ADDITIONAL_PROPERTIES", "warning",
                      f"additionalProperties 由 {_preview(ao)} 变为 {_preview(an)}。",
                      ctx, location=loc, old_value=ao, new_value=an, **meta)

    def _compare_bound(self, vo, vn, field_name, sense, ctx, meta):
        if vo is None and vn is None:
            return
        loc = ctx.location
        if vo == vn:
            return
        if vo is None or vn is None:
            # 约束从无到有 / 从有到无
            if loc == "request":
                breaking = vn is not None
            elif sense == "min":
                breaking = vo is not None and vn is None
            else:
                breaking = vo is not None and vn is None
            self.emit("CONSTRAINT_CHANGED", "breaking" if breaking else "non-breaking",
                      f"{field_name} 约束由 {_preview(vo)} 变为 {_preview(vn)}。",
                      ctx, location=loc, old_value=vo, new_value=vn, **meta)
            return
        try:
            vo_n, vn_n = float(vo), float(vn)
        except (TypeError, ValueError):
            return
        if sense == "min":
            breaks = vn_n > vo_n if loc == "request" else vn_n < vo_n
        else:
            breaks = vn_n < vo_n if loc == "request" else vn_n > vo_n
        if breaks:
            self.emit("CONSTRAINT_CHANGED", "breaking",
                      f"{field_name} 由 {vo} 收紧/改变为 {vn}，部分原有"
                      f"{'请求值' if loc == 'request' else '响应值'}不再合法或可能出现。",
                      ctx, location=loc, old_value=vo, new_value=vn, **meta)
        else:
            self.emit("CONSTRAINT_CHANGED", "non-breaking",
                      f"{field_name} 由 {vo} 放宽为 {vn}。",
                      ctx, location=loc, old_value=vo, new_value=vn, **meta)


def _path_variables(path: str) -> list[str]:
    variables, token, in_brace = [], [], False
    for ch in path:
        if ch == "{":
            in_brace, token = True, []
        elif ch == "}":
            in_brace = False
            variables.append("".join(token))
        elif in_brace:
            token.append(ch)
    return variables


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------
def run_comparison(old_spec: Spec, new_spec: Spec) -> dict[str, Any]:
    comparator = Comparator(old_spec, new_spec)
    findings = comparator.compare()

    endpoint_count = len({(f["path"], f["method"]) for f in findings
                          if f["path"] and f["method"]})
    summary = {
        "total": len(findings),
        "breaking": sum(1 for f in findings if f["severity"] == "breaking"),
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "non_breaking": sum(1 for f in findings if f["severity"] == "non-breaking"),
        "affected_endpoints": endpoint_count,
        "old_operation_count": sum(1 for _ in old_spec.iter_operations()),
        "new_operation_count": sum(1 for _ in new_spec.iter_operations()),
        "compatible": not any(f["severity"] == "breaking" for f in findings),
    }
    return {
        "summary": summary,
        "findings": findings,
    }
