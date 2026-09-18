"""API 集成测试（使用临时 SQLite 数据库）。"""

import importlib
import os
import tempfile

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test-reviews.db"
    monkeypatch.setenv("REVIEW_DB_PATH", str(db_file))

    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)

    from fastapi.testclient import TestClient
    with TestClient(main_module.app) as c:
        yield c


OLD = """
openapi: "3.0.3"
info: {title: API, version: "1"}
paths:
  /items:
    get:
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [id]
                properties:
                  id: {type: integer}
                  name: {type: string}
"""

NEW = """
openapi: "3.0.3"
info: {title: API, version: "2"}
paths:
  /items:
    get:
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema:
                type: object
                required: [id]
                properties:
                  id: {type: string}
"""


def test_create_review_endpoint(client):
    resp = client.post("/api/reviews", json={"old_spec": OLD, "new_spec": NEW})
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert data["summary"]["breaking"] >= 1
    rules = {f["rule"] for f in data["findings"]}
    assert "PROPERTY_REMOVED" in rules
    assert "TYPE_CHANGED" in rules
    # JSON 路径定位
    removed = next(f for f in data["findings"] if f["rule"] == "PROPERTY_REMOVED")
    assert removed["path_old"].endswith("['name']")


def test_invalid_spec_returns_400(client):
    resp = client.post("/api/reviews",
                       json={"old_spec": "{not json", "new_spec": NEW})
    assert resp.status_code == 400


def test_history_get_delete(client):
    created = client.post("/api/reviews", json={"old_spec": OLD, "new_spec": NEW}).json()
    listing = client.get("/api/reviews").json()["reviews"]
    assert len(listing) == 1

    detail = client.get(f"/api/reviews/{created['id']}").json()
    assert detail["id"] == created["id"]

    # 三种报告都能下载
    for fmt, needle in (("md", "# OpenAPI"), ("json", '"report"'),
                        ("html", "<html")):
        r = client.get(f"/api/reviews/{created['id']}/report?format={fmt}")
        assert r.status_code == 200
        assert needle in r.text
        assert "attachment" in r.headers["content-disposition"]

    assert client.delete(f"/api/reviews/{created['id']}").status_code == 200
    assert client.get(f"/api/reviews/{created['id']}").status_code == 404


def test_expansion_tree_for_circular_ref(client, tmp_path):
    old = """
openapi: "3.0.3"
info: {title: C, version: "1"}
paths: {}
components:
  schemas:
    Node:
      type: object
      properties:
        next:
          $ref: "#/components/schemas/Node"
"""
    created = client.post("/api/reviews",
                          json={"old_spec": old, "new_spec": old}).json()
    resp = client.get(
        f"/api/reviews/{created['id']}/expansion",
        params={"side": "old", "pointer": "#/components/schemas/Node"},
    )
    assert resp.status_code == 200
    tree = resp.json()["tree"]

    def find_cycle(node):
        if node.get("cyclic"):
            return True
        return any(find_cycle(c) for c in node.get("children", []))

    assert find_cycle(tree)


def test_expansion_accepts_component_name(client):
    old = """
openapi: "3.0.3"
info: {title: C, version: "1"}
paths: {}
components:
  schemas:
    Foo: {type: object, properties: {a: {type: string}}}
"""
    created = client.post("/api/reviews",
                          json={"old_spec": old, "new_spec": old}).json()
    resp = client.get(
        f"/api/reviews/{created['id']}/expansion",
        params={"side": "old", "pointer": "Foo"},
    )
    assert resp.status_code == 200
    assert resp.json()["pointer"] == "#/components/schemas/Foo"


def test_upload_multipart(client):
    files = {
        "old_file": ("old.yaml", OLD.encode(), "application/x-yaml"),
        "new_file": ("new.yaml", NEW.encode(), "application/x-yaml"),
    }
    resp = client.post("/api/reviews/upload", files=files)
    assert resp.status_code == 201
    assert resp.json()["summary"]["total"] > 0


def test_rules_and_health(client):
    assert client.get("/api/health").json()["status"] == "ok"
    rules = client.get("/api/rules").json()["rules"]
    assert any(r["code"] == "TYPE_CHANGED" for r in rules)
