# OpenAPI 版本兼容性审查 Web 应用

对比新旧两份 **OpenAPI 3.x（JSON / YAML）** 规范，递归解析 `$ref`（**含循环引用**），
按「请求 / 响应」两个语义方向判断向后兼容性，精确定位每一处破坏性变更的 **JSON 路径**
并给出原因；支持结果筛选、`$ref` 引用展开树浏览以及 Markdown / HTML / JSON 审查报告下载。

技术栈：**FastAPI + SQLite + 原生 JavaScript（无前端构建、无框架）**。

---

## 一条命令启动

```bash
docker compose up --build
```

启动后访问：

> **http://localhost:8000**

- Web 界面：`http://localhost:8000/`
- API 文档（Swagger UI）：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/api/health`

审查记录持久化在 Docker 卷 `review-data`（容器内 `/data/reviews.db`），容器重启不丢失。

> 端口映射在 `docker-compose.yml` 中，如需修改调整 `ports` 左侧即可，例如 `"9000:8000"`。

### 不用 Docker 直接运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 使用方法

1. 打开 `http://localhost:8000`，在左右两个文本框分别粘贴/上传**旧版本**（线上）与**新版本**（待发布）OpenAPI 文档；
   也可以点击「加载示例」快速体验（示例刻意包含删除接口、必填参数、枚举收窄、响应字段删除等多种破坏场景及循环 `$ref`）。
2. 点击 **▶ 开始兼容性审查**。
3. 顶部为汇总卡片（破坏性 / 潜在风险 / 无破坏、受影响接口数、接口数量变化与总体兼容性结论）。
4. 用筛选条按 **级别 / 请求方向 / 分类 / 关键字**（路径、规则、字段名、原因）过滤。
5. 点击任意条目展开，查看：
   - 可读的变更原因与新旧取值；
   - 新旧两侧精确的 **JSON 路径**；
   - 命中的 **`$ref` 链**；点击链中任意引用可弹出 **引用展开树**（循环节点标注「↩ 循环引用（已截断）」，可切换旧/新版本）。
6. 右上角一键下载 **Markdown / HTML / JSON** 报告；「历史记录」标签可查看、重新打开或删除历次审查。

---

## 兼容性判定规则（摘要）

判定基于「调用方 ↔ 服务端」的数据流方向：**请求**方向收紧是破坏，**响应**方向放宽（出现调用方未知的内容）是破坏。

| 方向 | 破坏性（breaking）示例 | 非破坏（non-breaking）示例 |
| --- | --- | --- |
| 接口 | 删除接口、接口被标记弃用（warning） | 新增接口 |
| 请求参数 | 删除参数、新增必填参数、可选变必填、style/explode 改变 | 新增可选参数、必填变可选 |
| 请求体 | 新增必填请求体/必填字段、枚举值删除、maximum/minLength 等约束收紧、additionalProperties 关闭 | 新增可选字段、约束放宽、int→number |
| 响应 | 删除状态码/媒体类型/字段、必填字段变可选、新增枚举值、字段类型变化、可空、字段删除 | 新增响应码、新增响应字段 |
| Schema | 类型变化、format 收窄、pattern 改变、readOnly/writeOnly 反转、oneOf/anyOf 结构改变 | int32→int64（请求侧）等放宽 |
| 安全 | 操作新增/收紧安全要求 | （移除安全要求记为 warning） |

完整规则清单见应用内「规则说明」标签或 `GET /api/rules`。所有规则实现于 `app/checker.py`。

### 循环引用如何处理

- 解析层（`app/spec_parser.py`）沿 `$ref` 链解析并记录 `seen` 集合，再次回到链上节点即标记为循环并终止，**不会无限递归**。
- 比对层以 `(schema 锚点, 逻辑 JSON 路径)` 为同一标识：同一个被复用的 schema 出现在不同属性位置仍会正常比较，只有沿同一路径绕回自身才截断。
- 每条发现都携带完整的 `ref_old` / `ref_new` 链；展开树接口（`/api/reviews/{id}/expansion`）逐层展开引用并标记循环节点。

---

## 目录结构

```
.
├── app/
│   ├── main.py          # FastAPI 路由：审查、历史、报告下载、$ref 展开树
│   ├── checker.py       # 语义兼容性比对引擎（请求/响应方向规则、循环安全递归）
│   ├── spec_parser.py   # JSON/YAML 加载、JSON Pointer、$ref 解析、allOf 展平、展开树
│   ├── reports.py       # Markdown / HTML / JSON 报告生成
│   └── db.py            # SQLite 存储（审查记录 + 原始规范文本）
├── static/
│   ├── index.html       # 单页界面
│   ├── app.js           # 原生 JS：输入、筛选、详情、引用树、历史、报告下载
│   ├── styles.css
│   └── samples/         # 新旧示例 OpenAPI（含循环 $ref）
├── tests/               # pytest 测试（30 个用例）
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/reviews` | 提交 JSON 体 `{old_spec, new_spec}` 执行审查 |
| `POST` | `/api/reviews/upload` | multipart 上传 `old_file` / `new_file` |
| `GET` | `/api/reviews` | 历史审查列表 |
| `GET` | `/api/reviews/{id}` | 审查详情（全部发现） |
| `DELETE` | `/api/reviews/{id}` | 删除审查记录 |
| `GET` | `/api/reviews/{id}/report?format=md\|html\|json` | 下载审查报告 |
| `GET` | `/api/reviews/{id}/expansion?side=old\|new&pointer=%23%2Fcomponents%2Fschemas%2FPet` | `$ref` 展开树 |
| `GET` | `/api/rules` | 规则目录与级别说明 |
| `GET` | `/api/health` | 健康检查 |

发现对象关键字段：`rule`、`severity`（breaking / warning / non-breaking）、`location`（request / response）、
`method`、`path`、`path_old`、`path_new`、`ref_old[]`、`ref_new[]`、`message`。

---

## 运行测试

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `REVIEW_DB_PATH` | `/data/reviews.db`（Docker） | SQLite 数据库文件路径 |
