"""SQLite 存储层：保存审查记录与原始规范文本。"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

DB_PATH = os.environ.get("REVIEW_DB_PATH", "/data/reviews.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reviews (
                id          TEXT PRIMARY KEY,
                created_at  REAL NOT NULL,
                old_title   TEXT,
                new_title   TEXT,
                old_version TEXT,
                new_version TEXT,
                old_format  TEXT,
                new_format  TEXT,
                summary     TEXT NOT NULL,
                findings    TEXT NOT NULL,
                old_spec    TEXT NOT NULL,
                new_spec    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews(created_at DESC);
            """
        )


def save_review(
    review_id: str,
    *,
    old_title: str | None,
    new_title: str | None,
    old_version: str | None,
    new_version: str | None,
    old_format: str,
    new_format: str,
    summary: dict[str, Any],
    findings: list[dict[str, Any]],
    old_spec: str,
    new_spec: str,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO reviews (id, created_at, old_title, new_title, old_version,
                                 new_version, old_format, new_format, summary,
                                 findings, old_spec, new_spec)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                time.time(),
                old_title,
                new_title,
                old_version,
                new_version,
                old_format,
                new_format,
                json.dumps(summary, ensure_ascii=False),
                json.dumps(findings, ensure_ascii=False),
                old_spec,
                new_spec,
            ),
        )


def _row_to_dict(row: sqlite3.Row, *, include_specs: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": row["id"],
        "created_at": row["created_at"],
        "old_title": row["old_title"],
        "new_title": row["new_title"],
        "old_version": row["old_version"],
        "new_version": row["new_version"],
        "old_format": row["old_format"],
        "new_format": row["new_format"],
        "summary": json.loads(row["summary"]),
        "findings": json.loads(row["findings"]),
    }
    if include_specs:
        item["old_spec"] = row["old_spec"]
        item["new_spec"] = row["new_spec"]
    return item


def list_reviews(limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reviews ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_review(review_id: str, *, include_specs: bool = True) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM reviews WHERE id = ?", (review_id,)
        ).fetchone()
    return _row_to_dict(row, include_specs=include_specs) if row else None


def delete_review(review_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
    return cur.rowcount > 0
