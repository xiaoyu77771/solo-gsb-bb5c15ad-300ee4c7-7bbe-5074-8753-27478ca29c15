/* OpenAPI 兼容性审查 —— 原生 JavaScript 前端 */
"use strict";

const state = {
  review: null,
  rules: [],
  severities: {},
  tree: { side: "old", pointer: null, reviewId: null },
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const SEV_CLASS = { breaking: "breaking", warning: "warning", "non-breaking": "non-breaking" };

/* ======================================================================
 * 标签页
 * ================================================================== */
$$(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".tab-panel").forEach((p) =>
      p.classList.toggle("active", p.id === `tab-${btn.dataset.tab}`)
    );
    if (btn.dataset.tab === "history") loadHistory();
    if (btn.dataset.tab === "rules") loadRulesPage();
  });
});

/* ======================================================================
 * 输入区：文件上传 / 示例 / 清空
 * ================================================================== */
function bindFile(inputId, textareaId) {
  $(`#${inputId}`).addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => { $(`#${textareaId}`).value = reader.result; };
    reader.readAsText(file);
  });
}
bindFile("old-file", "old-spec");
bindFile("new-file", "new-spec");

$$("[data-clear]").forEach((btn) =>
  btn.addEventListener("click", () => { $(`#${btn.dataset.clear}`).value = ""; })
);

$("#old-sample").addEventListener("click", async () => {
  $("#old-spec").value = await (await fetch("/samples/old-openapi.yaml")).text();
});
$("#new-sample").addEventListener("click", async () => {
  $("#new-spec").value = await (await fetch("/samples/new-openapi.yaml")).text();
});

/* ======================================================================
 * 发起审查
 * ================================================================== */
$("#run-btn").addEventListener("click", async () => {
  const oldSpec = $("#old-spec").value.trim();
  const newSpec = $("#new-spec").value.trim();
  const errorEl = $("#input-error");
  errorEl.textContent = "";
  if (!oldSpec || !newSpec) {
    errorEl.textContent = "请先提供新旧两份 OpenAPI 文档。";
    return;
  }
  const btn = $("#run-btn");
  btn.disabled = true;
  btn.textContent = "审查中…";
  try {
    const resp = await fetch("/api/reviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_spec: oldSpec, new_spec: newSpec }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      errorEl.textContent = data.detail || "审查失败";
      return;
    }
    state.review = data;
    renderResult(data);
  } catch (err) {
    errorEl.textContent = `请求失败：${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ 开始兼容性审查";
  }
});

/* ======================================================================
 * 结果渲染
 * ================================================================== */
function renderResult(review) {
  $("#result-area").classList.remove("hidden");
  const s = review.summary;

  $("#report-links").innerHTML = ["md", "html", "json"]
    .map((f) => {
      const label = { md: "Markdown", html: "HTML", json: "JSON" }[f];
      return `<a href="/api/reviews/${review.id}/report?format=${f}" download>⬇ ${label} 报告</a>`;
    })
    .join("");

  const compat = s.compatible
    ? `<div class="card compatible"><div class="num">✅ 兼容</div><div class="label">未发现破坏性变更</div></div>`
    : `<div class="card breaking"><div class="num">❌ ${s.breaking}</div><div class="label">破坏性变更</div></div>`;

  $("#summary-cards").innerHTML = `
    ${compat}
    <div class="card warning"><div class="num">${s.warning}</div><div class="label">潜在风险</div></div>
    <div class="card"><div class="num">${s.non_breaking}</div><div class="label">无破坏 / 新增</div></div>
    <div class="card"><div class="num">${s.affected_endpoints}</div><div class="label">受影响接口</div></div>
    <div class="card"><div class="num">${s.old_operation_count} → ${s.new_operation_count}</div><div class="label">接口数量变化</div></div>`;

  const categories = [...new Set(review.findings.map((f) => f.category))].sort();
  $("#filter-category").innerHTML =
    `<option value="">全部分类</option>` +
    categories.map((c) => `<option value="${c}">${categoryLabel(c)}</option>`).join("");

  renderFindings();
  $("#result-area").scrollIntoView({ behavior: "smooth", block: "start" });
}

function categoryLabel(c) {
  return {
    operation: "接口", parameter: "参数", security: "安全",
    request: "请求体/媒体类型", response: "响应", content: "媒体类型",
    schema: "数据结构",
  }[c] || c;
}

["#filter-text", "#filter-severity", "#filter-location", "#filter-category"].forEach((sel) => {
  $(sel).addEventListener("input", renderFindings);
});

function filteredFindings() {
  const review = state.review;
  if (!review) return [];
  const q = $("#filter-text").value.trim().toLowerCase();
  const sev = $("#filter-severity").value;
  const loc = $("#filter-location").value;
  const cat = $("#filter-category").value;
  return review.findings.filter((f) => {
    if (sev && f.severity !== sev) return false;
    if (loc && f.location !== loc) return false;
    if (cat && f.category !== cat) return false;
    if (q) {
      const hay = [f.rule, f.title, f.message, f.path, f.method, f.param_name,
        f.status_code, f.media_type, f.path_old, f.path_new]
        .filter(Boolean).join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function renderFindings() {
  const container = $("#findings");
  const list = filteredFindings();
  const total = state.review ? state.review.findings.length : 0;
  $("#filter-count").textContent =
    list.length === total ? `共 ${total} 项` : `显示 ${list.length} / ${total} 项`;

  if (!list.length) {
    container.innerHTML = `<div class="empty-state">${
      total === 0 ? "🎉 两份规范之间没有任何差异。" : "没有符合筛选条件的结果。"
    }</div>`;
    return;
  }

  container.innerHTML = list.map((f) => findingCard(f)).join("");

  container.querySelectorAll(".finding-head").forEach((head) => {
    head.addEventListener("click", () =>
      head.closest(".finding").classList.toggle("open")
    );
  });
  container.querySelectorAll(".ref-link").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.stopPropagation();
      openTree(link.dataset.side, link.dataset.ref);
    });
  });
}

function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function locText(f) {
  const parts = [];
  if (f.method && f.path) parts.push(`${f.method.toUpperCase()} ${f.path}`);
  if (f.location) parts.push({ request: "请求", response: "响应" }[f.location] || f.location);
  if (f.status_code) parts.push(`HTTP ${f.status_code}`);
  if (f.param_name) parts.push(`参数 ${f.param_in}.${f.param_name}`);
  return parts.join(" · ");
}

function findingCard(f) {
  const refsOld = (f.ref_old || []).map((r) =>
    `<span class="ref-link" data-side="old" data-ref="${esc(r)}">${esc(r)}</span>`
  ).join(" → ");
  const refsNew = (f.ref_new || []).map((r) =>
    `<span class="ref-link" data-side="new" data-ref="${esc(r)}">${esc(r)}</span>`
  ).join(" → ");
  const values = (f.old_value || f.new_value)
    ? `<p>取值：旧 <span class="val-chip">${esc(f.old_value ?? "—")}</span>
         → 新 <span class="val-chip">${esc(f.new_value ?? "—")}</span></p>` : "";
  return `
  <div class="finding ${SEV_CLASS[f.severity]}">
    <div class="finding-head">
      <span class="badge ${SEV_CLASS[f.severity]}">${esc(state.severities[f.severity] || f.severity)}</span>
      <span class="finding-title">${esc(f.title)}</span>
      <span class="finding-rule">${esc(f.rule)}</span>
      <span class="finding-loc">${esc(locText(f))}</span>
    </div>
    <div class="finding-body">
      <p>${esc(f.message)}</p>
      ${values}
      <div class="path-grid">
        <div class="path-box">
          <div class="side">旧版本 JSON 路径</div>
          ${f.path_old ? `<code>${esc(f.path_old)}</code>` : '<code>—</code>'}
          ${refsOld ? `<div class="ref-chain">$ref 链：${refsOld}</div>` : ""}
        </div>
        <div class="path-box">
          <div class="side">新版本 JSON 路径</div>
          ${f.path_new ? `<code>${esc(f.path_new)}</code>` : '<code>—</code>'}
          ${refsNew ? `<div class="ref-chain">$ref 链：${refsNew}</div>` : ""}
        </div>
      </div>
    </div>
  </div>`;
}

/* ======================================================================
 * $ref 展开树弹窗
 * ================================================================== */
const treeModal = $("#tree-modal");

function openTree(side, pointer) {
  state.tree = { side, pointer, reviewId: state.review.id };
  $$(".seg-toggle button").forEach((b) =>
    b.classList.toggle("active", b.dataset.side === side)
  );
  treeModal.classList.remove("hidden");
  loadTree();
}

$("#tree-close").addEventListener("click", () => treeModal.classList.add("hidden"));
treeModal.addEventListener("click", (e) => {
  if (e.target === treeModal) treeModal.classList.add("hidden");
});
$$(".seg-toggle button").forEach((b) =>
  b.addEventListener("click", () => {
    state.tree.side = b.dataset.side;
    $$(".seg-toggle button").forEach((x) =>
      x.classList.toggle("active", x === b)
    );
    loadTree();
  })
);

async function loadTree() {
  const { side, pointer, reviewId } = state.tree;
  $("#tree-pointer").textContent = `${side === "old" ? "旧" : "新"}版本 · ${pointer}`;
  $("#tree-body").innerHTML = '<div class="empty-state">加载中…</div>';
  try {
    const resp = await fetch(
      `/api/reviews/${reviewId}/expansion?side=${side}&pointer=${encodeURIComponent(pointer)}`
    );
    const data = await resp.json();
    if (!resp.ok) {
      $("#tree-body").innerHTML = `<div class="empty-state">${esc(data.detail)}</div>`;
      return;
    }
    $("#tree-body").innerHTML = renderTreeNode(data.tree, true);
    bindTreeToggles();
  } catch (err) {
    $("#tree-body").innerHTML = `<div class="empty-state">加载失败：${esc(err.message)}</div>`;
  }
}

function renderTreeNode(node, isRoot) {
  const kindBadge = `<span class="tree-kind">${esc(node.kind)}</span>`;
  let extra = "";
  if (node.ref) extra += ` <span class="tree-ref">${esc(node.ref)}</span>`;
  if (node.cyclic) extra += ` <span class="tree-cycle">↩ 循环引用（已截断）</span>`;
  if (node.truncated) extra += ` <span class="tree-truncated">深度超限</span>`;
  const value = node.kind === "scalar" && "value" in node
    ? `<span class="tree-value">= ${esc(node.value)}</span>` : "";

  const hasChildren = node.children && node.children.length > 0;
  const toggle = hasChildren
    ? `<button class="tree-toggle">▾</button>`
    : `<button class="tree-toggle leaf">·</button>`;

  const children = hasChildren
    ? `<div class="tree-children">${
        node.children.map((c) => renderTreeNode(c, false)).join("")
      }</div>` : "";

  return `
  <div class="tree-node ${isRoot ? "tree-node-root" : ""}">
    <div class="tree-row">
      ${toggle}
      <span class="tree-label">${esc(node.label)}</span>
      ${kindBadge}${extra}${value}
    </div>
    ${children}
  </div>`;
}

function bindTreeToggles() {
  $$("#tree-body .tree-toggle:not(.leaf)").forEach((btn) => {
    btn.addEventListener("click", () => {
      const childrenEl = btn.closest(".tree-node").querySelector(".tree-children");
      const collapsed = childrenEl.style.display === "none";
      childrenEl.style.display = collapsed ? "" : "none";
      btn.textContent = collapsed ? "▾" : "▸";
    });
  });
}

/* ======================================================================
 * 历史记录
 * ================================================================== */
async function loadHistory() {
  const container = $("#history-list");
  container.innerHTML = '<div class="empty-state">加载中…</div>';
  try {
    const resp = await fetch("/api/reviews");
    const data = await resp.json();
    if (!data.reviews.length) {
      container.innerHTML = '<div class="empty-state">暂无审查记录，先做一次审查吧。</div>';
      return;
    }
    container.innerHTML = data.reviews.map(historyItem).join("");
    container.querySelectorAll(".js-open").forEach((btn) =>
      btn.addEventListener("click", () => {
        state.review = data.reviews.find((r) => r.id === btn.dataset.id);
        $$(".tab").forEach((b) =>
          b.classList.toggle("active", b.dataset.tab === "review"));
        $$(".tab-panel").forEach((p) =>
          p.classList.toggle("active", p.id === "tab-review"));
        renderResult(state.review);
      })
    );
    container.querySelectorAll(".js-delete").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("确定删除该审查记录？")) return;
        await fetch(`/api/reviews/${btn.dataset.id}`, { method: "DELETE" });
        loadHistory();
      })
    );
  } catch (err) {
    container.innerHTML = `<div class="empty-state">加载失败：${esc(err.message)}</div>`;
  }
}

function historyItem(r) {
  const date = new Date(r.created_at * 1000).toLocaleString("zh-CN");
  const s = r.summary;
  return `
  <div class="history-item">
    <div>
      <h3>${esc(r.old_title || "未命名")} ${esc(r.old_version || "")}
        → ${esc(r.new_title || "未命名")} ${esc(r.new_version || "")}</h3>
      <div class="meta">${date} · 接口 ${s.old_operation_count} → ${s.new_operation_count} · 受影响 ${s.affected_endpoints}</div>
    </div>
    <div class="stats">
      ${s.breaking ? `<span class="mini-stat breaking">破坏 ${s.breaking}</span>` : ""}
      ${s.warning ? `<span class="mini-stat warning">风险 ${s.warning}</span>` : ""}
      <span class="mini-stat non-breaking">其他 ${s.non_breaking}</span>
    </div>
    <div class="history-actions">
      <button class="icon-btn js-open" data-id="${r.id}">查看</button>
      <a class="icon-btn" href="/api/reviews/${r.id}/report?format=md" download>MD</a>
      <a class="icon-btn" href="/api/reviews/${r.id}/report?format=html" download>HTML</a>
      <button class="icon-btn js-delete" data-id="${r.id}">删除</button>
    </div>
  </div>`;
}

/* ======================================================================
 * 规则页
 * ================================================================== */
async function loadRulesPage() {
  if (state.rules.length) return renderRulesPage();
  const resp = await fetch("/api/rules");
  const data = await resp.json();
  state.rules = data.rules;
  state.severities = data.severities;
  renderRulesPage();
}

function renderRulesPage() {
  const groups = {};
  state.rules.forEach((r) => (groups[r.category] ||= []).push(r));
  $("#rules-list").innerHTML = Object.entries(groups).map(([cat, rules]) => `
    <div class="rule-group">
      <h3>${categoryLabel(cat)}</h3>
      ${rules.map((r) => `
        <div class="rule-row"><code>${esc(r.code)}</code><span>${esc(r.title)}</span></div>
      `).join("")}
    </div>`).join("");
}

/* 启动时加载规则（供结果徽标使用严重级别中文标签） */
(async function init() {
  try {
    const resp = await fetch("/api/rules");
    const data = await resp.json();
    state.rules = data.rules;
    state.severities = data.severities;
  } catch (_) { /* 忽略，结果仍可渲染 */ }
})();
