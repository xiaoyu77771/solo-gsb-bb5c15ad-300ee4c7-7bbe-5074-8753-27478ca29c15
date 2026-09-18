"""审查报告导出：JSON / Markdown / HTML。"""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any

from .checker import SEVERITY_LABEL

SEVERITY_ORDER = {"breaking": 0, "warning": 1, "non-breaking": 2}


def _header(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "old_title": review.get("old_title"),
        "new_title": review.get("new_title"),
        "old_version": review.get("old_version"),
        "new_version": review.get("new_version"),
        "created_at": datetime.fromtimestamp(review["created_at"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }


def build_json(review: dict[str, Any]) -> str:
    payload = {
        "report": "OpenAPI 兼容性审查报告",
        **_header(review),
        "summary": review["summary"],
        "findings": review["findings"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _loc_text(f: dict[str, Any]) -> str:
    loc_map = {"request": "请求", "response": "响应"}
    parts = []
    if f.get("method") and f.get("path"):
        parts.append(f"{f['method'].upper()} {f['path']}")
    if f.get("location"):
        parts.append(loc_map.get(f["location"], f["location"]))
    if f.get("status_code"):
        parts.append(f"HTTP {f['status_code']}")
    if f.get("param_name"):
        parts.append(f"参数 {f['param_in']}.{f['param_name']}")
    if f.get("media_type"):
        parts.append(f["media_type"])
    return " · ".join(parts) or "全局"


def build_markdown(review: dict[str, Any]) -> str:
    h = _header(review)
    s = review["summary"]
    lines = [
        "# OpenAPI 兼容性审查报告",
        "",
        f"- 生成时间：{h['created_at']}",
        f"- 旧版本：{h['old_title'] or '未命名'}（{h['old_version'] or '-'}）",
        f"- 新版本：{h['new_title'] or '未命名'}（{h['new_version'] or '-'}）",
        "",
        "## 结论",
        "",
        f"- **破坏性变更：{s['breaking']} 项**",
        f"- 潜在风险：{s['warning']} 项",
        f"- 无破坏/新增：{s['non_breaking']} 项",
        f"- 受影响接口：{s['affected_endpoints']} 个",
        f"- 接口数量：{s['old_operation_count']} → {s['new_operation_count']}",
        f"- 总体兼容性：{'✅ 兼容' if s['compatible'] else '❌ 存在破坏性变更'}",
        "",
    ]
    if not review["findings"]:
        lines += ["未发现任何差异，两版规范完全兼容。", ""]
        return "\n".join(lines)

    for severity in ("breaking", "warning", "non-breaking"):
        group = [f for f in review["findings"] if f["severity"] == severity]
        if not group:
            continue
        lines += [f"## {SEVERITY_LABEL[severity]}（{len(group)} 项）", ""]
        for i, f in enumerate(group, 1):
            lines.append(
                f"{i}. **[{f['rule']}] {f['title']}** — {f['message']}"
            )
            lines.append(f"   - 位置：{_loc_text(f)}")
            if f.get("path_old"):
                lines.append(f"   - 旧路径：`{f['path_old']}`")
            if f.get("path_new"):
                lines.append(f"   - 新路径：`{f['path_new']}`")
            if f.get("ref_old"):
                lines.append("   - 旧 $ref 链：" +
                             " → ".join(f"`{r}`" for r in f["ref_old"]))
            if f.get("ref_new"):
                lines.append("   - 新 $ref 链：" +
                             " → ".join(f"`{r}`" for r in f["ref_new"]))
        lines.append("")
    return "\n".join(lines)


def build_html(review: dict[str, Any]) -> str:
    h = _header(review)
    s = review["summary"]

    def esc(value) -> str:
        return html.escape(str(value)) if value is not None else ""

    rows = []
    for f in review["findings"]:
        refs_old = " → ".join(f"<code>{esc(r)}</code>" for r in f.get("ref_old", []))
        refs_new = " → ".join(f"<code>{esc(r)}</code>" for r in f.get("ref_new", []))
        rows.append(
            f"""<tr class="sev-{esc(f['severity'])}">
  <td><span class="badge {esc(f['severity'])}">{esc(SEVERITY_LABEL[f['severity']])}</span></td>
  <td><code>{esc(f['rule'])}</code><br>{esc(f['title'])}</td>
  <td>{esc(_loc_text(f))}</td>
  <td>{esc(f['message'])}</td>
  <td>{('<code>' + esc(f.get('path_old') or '') + '</code>') if f.get('path_old') else ''}<br>{refs_old}</td>
  <td>{('<code>' + esc(f.get('path_new') or '') + '</code>') if f.get('path_new') else ''}<br>{refs_new}</td>
</tr>"""
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>OpenAPI 兼容性审查报告</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "PingFang SC", sans-serif;
         margin: 32px; color: #1f2937; }}
  h1 {{ margin-bottom: 4px; }}
  .meta {{ color: #6b7280; margin-bottom: 24px; }}
  .cards {{ display: flex; gap: 12px; margin: 16px 0 24px; flex-wrap: wrap; }}
  .card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 20px; min-width: 130px; }}
  .card .num {{ font-size: 26px; font-weight: 700; }}
  .breaking .num {{ color: #dc2626; }}
  .warning .num {{ color: #d97706; }}
  .ok {{ color: #16a34a; font-weight: 600; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ border: 1px solid #e5e7eb; padding: 8px 10px; text-align: left;
            vertical-align: top; }}
  th {{ background: #f9fafb; }}
  code {{ background: #f3f4f6; padding: 1px 4px; border-radius: 4px;
          font-size: 12px; word-break: break-all; }}
  .badge {{ padding: 2px 8px; border-radius: 999px; font-size: 12px; color: #fff;
            white-space: nowrap; }}
  .badge.breaking {{ background: #dc2626; }}
  .badge.warning {{ background: #d97706; }}
  .badge.non-breaking {{ background: #16a34a; }}
  tr.sev-breaking {{ background: #fef2f2; }}
</style>
</head>
<body>
<h1>OpenAPI 兼容性审查报告</h1>
<div class="meta">
  生成时间 {esc(h['created_at'])} ·
  旧版本 {esc(h['old_title'] or '未命名')}（{esc(h['old_version']) or '-'}）→
  新版本 {esc(h['new_title'] or '未命名')}（{esc(h['new_version']) or '-'}）
</div>
<p class="{'ok' if s['compatible'] else ''}">
  {'✅ 未发现破坏性变更，两版规范兼容。' if s['compatible'] else '❌ 存在破坏性变更，请逐项确认。'}
</p>
<div class="cards">
  <div class="card breaking"><div class="num">{s['breaking']}</div>破坏性变更</div>
  <div class="card warning"><div class="num">{s['warning']}</div>潜在风险</div>
  <div class="card"><div class="num">{s['non_breaking']}</div>无破坏/新增</div>
  <div class="card"><div class="num">{s['affected_endpoints']}</div>受影响接口</div>
</div>
<table>
<thead><tr>
  <th>级别</th><th>规则</th><th>位置</th><th>说明</th><th>旧 JSON 路径 / $ref 链</th><th>新 JSON 路径 / $ref 链</th>
</tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
</body>
</html>"""


def build_report(review: dict[str, Any], fmt: str) -> tuple[str, str, str]:
    """返回 (content, media_type, filename_ext)。"""
    if fmt == "json":
        return build_json(review), "application/json; charset=utf-8", "json"
    if fmt == "html":
        return build_html(review), "text/html; charset=utf-8", "html"
    return build_markdown(review), "text/markdown; charset=utf-8", "md"
