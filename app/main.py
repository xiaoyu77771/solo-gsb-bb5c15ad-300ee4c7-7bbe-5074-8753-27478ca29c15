"""FastAPI 入口：审查、历史记录、$ref 展开树、报告下载。"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .checker import RULES, SEVERITY_LABEL, run_comparison
from .reports import build_report
from .spec_parser import (
    Spec,
    SpecError,
    build_expansion_tree,
    find_schema_pointer,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="OpenAPI 版本兼容性审查",
    description="比对两份 OpenAPI 3.x JSON/YAML，定位破坏性变更并导出报告。",
    version="1.0.0",
    lifespan=lifespan,
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")


# --------------------------------------------------------------------------
# 请求模型
# --------------------------------------------------------------------------
class ReviewRequest(BaseModel):
    old_spec: str
    new_spec: str
    old_format: str | None = None
    new_format: str | None = None


# --------------------------------------------------------------------------
# 核心服务
# --------------------------------------------------------------------------
def _create_review(old_text: str, new_text: str,
                   old_fmt: str | None, new_fmt: str | None) -> dict[str, Any]:
    try:
        old_spec = Spec.parse(old_text, old_fmt)
        new_spec = Spec.parse(new_text, new_fmt)
    except SpecError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = run_comparison(old_spec, new_spec)
    review_id = uuid.uuid4().hex[:12]
    db.save_review(
        review_id,
        old_title=old_spec.title,
        new_title=new_spec.title,
        old_version=old_spec.version,
        new_version=new_spec.version,
        old_format=old_spec.fmt,
        new_format=new_spec.fmt,
        summary=result["summary"],
        findings=result["findings"],
        old_spec=old_spec.raw,
        new_spec=new_spec.raw,
    )
    saved = db.get_review(review_id, include_specs=False)
    return saved


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/rules")
def list_rules() -> dict[str, Any]:
    rules = [
        {"code": code, "title": meta["title"], "category": meta["category"]}
        for code, meta in RULES.items()
    ]
    return {"rules": rules, "severities": SEVERITY_LABEL}


@app.post("/api/reviews", status_code=201)
def create_review(payload: ReviewRequest) -> dict[str, Any]:
    return _create_review(
        payload.old_spec, payload.new_spec,
        payload.old_format, payload.new_format,
    )


@app.post("/api/reviews/upload", status_code=201)
async def upload_review(
    old_file: UploadFile = File(...),
    new_file: UploadFile = File(...),
) -> dict[str, Any]:
    old_text = (await old_file.read()).decode("utf-8-sig")
    new_text = (await new_file.read()).decode("utf-8-sig")
    old_fmt = "json" if (old_file.filename or "").lower().endswith(".json") else None
    new_fmt = "json" if (new_file.filename or "").lower().endswith(".json") else None
    return _create_review(old_text, new_text, old_fmt, new_fmt)


@app.get("/api/reviews")
def get_reviews(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"reviews": db.list_reviews(limit)}


@app.get("/api/reviews/{review_id}")
def get_review(review_id: str) -> dict[str, Any]:
    review = db.get_review(review_id, include_specs=False)
    if not review:
        raise HTTPException(status_code=404, detail="审查记录不存在")
    return review


@app.delete("/api/reviews/{review_id}")
def remove_review(review_id: str) -> dict[str, str]:
    if not db.delete_review(review_id):
        raise HTTPException(status_code=404, detail="审查记录不存在")
    return {"status": "deleted"}


@app.get("/api/reviews/{review_id}/report")
def download_report(review_id: str, format: str = Query("md", pattern="^(md|json|html)$")):
    review = db.get_review(review_id, include_specs=False)
    if not review:
        raise HTTPException(status_code=404, detail="审查记录不存在")
    content, media_type, ext = build_report(review, format)
    filename = f"openapi-review-{review_id}.{ext}"
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/reviews/{review_id}/expansion")
def get_expansion(
    review_id: str,
    side: str = Query(..., pattern="^(old|new)$"),
    pointer: str = Query(..., description="JSON Pointer，如 #/components/schemas/Pet"),
):
    review = db.get_review(review_id, include_specs=True)
    if not review:
        raise HTTPException(status_code=404, detail="审查记录不存在")
    raw = review["old_spec"] if side == "old" else review["new_spec"]
    fmt = review["old_format"] if side == "old" else review["new_format"]
    try:
        spec = Spec.parse(raw, fmt)
        if not pointer.startswith("#/"):
            resolved = find_schema_pointer(spec.doc, pointer)
            if resolved is None:
                raise SpecError(f"无法定位 {pointer}")
            pointer = resolved
        tree = build_expansion_tree(spec.resolver, pointer)
    except SpecError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"side": side, "pointer": pointer, "tree": tree}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
