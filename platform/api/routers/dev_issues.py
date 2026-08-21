from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from build_identity import current_build_identity

router = APIRouter(prefix="/api/dev/issues", tags=["development-issues"])

DEV_RUNTIME_MODES = {"dev", "development", "test"}


class DevIssueCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    scope_kind: str = Field(min_length=1, max_length=40)
    scope_key: str = Field(min_length=1, max_length=256)
    page_label: str = Field(min_length=1, max_length=160)
    route: str = Field(min_length=1, max_length=2048)
    component_hint: str | None = Field(default=None, max_length=240)
    author_kind: Literal["operator", "ai"] = "operator"
    frontend_revision: str | None = Field(default=None, max_length=64)


class DevIssueUpdate(BaseModel):
    status: Literal["open", "resolved"]
    resolution_note: str | None = Field(default=None, max_length=4000)


class DevIssue(BaseModel):
    id: int
    issue_key: str
    body: str
    status: Literal["open", "resolved"]
    scope_kind: str
    scope_key: str
    page_label: str
    route: str
    component_hint: str | None
    author_kind: Literal["operator", "ai"]
    frontend_revision: str | None
    api_revision: str
    created_at: str
    resolved_at: str | None
    resolution_note: str | None


class DevIssueList(BaseModel):
    items: list[DevIssue]
    open_count: int


def dev_issue_ledger_enabled() -> bool:
    return os.getenv("BMS_RUNTIME_MODE", "").strip().lower() in DEV_RUNTIME_MODES


def _database_path() -> Path:
    if not dev_issue_ledger_enabled():
        raise RuntimeError("development issue ledger is disabled")
    state_dir = os.getenv("BMS_STATE_DIR", "").strip()
    if not state_dir:
        raise RuntimeError("BMS_STATE_DIR is required for the development issue ledger")
    return Path(state_dir).expanduser().resolve() / "dev-issues.sqlite3"


def _connect() -> sqlite3.Connection:
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS dev_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 4000),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'resolved')),
            scope_kind TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            page_label TEXT NOT NULL,
            route TEXT NOT NULL,
            component_hint TEXT,
            author_kind TEXT NOT NULL CHECK(author_kind IN ('operator', 'ai')),
            frontend_revision TEXT,
            api_revision TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            resolution_note TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_dev_issues_scope_status ON dev_issues(scope_key, status, id DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_dev_issues_status_id ON dev_issues(status, id DESC)"
    )
    connection.commit()
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return connection


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_required(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="required text cannot be blank")
    return cleaned


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _to_issue(row: sqlite3.Row) -> DevIssue:
    payload = dict(row)
    payload["issue_key"] = f"BMS-DEV-{payload['id']}"
    return DevIssue.model_validate(payload)


@router.get("", response_model=DevIssueList)
def list_dev_issues(
    status: Literal["open", "resolved", "all"] = "open",
    scope_key: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=100, ge=1, le=200),
) -> DevIssueList:
    filters: list[str] = []
    values: list[object] = []
    if status != "all":
        filters.append("status = ?")
        values.append(status)
    if scope_key:
        filters.append("scope_key = ?")
        values.append(scope_key)
    where = f" WHERE {' AND '.join(filters)}" if filters else ""

    with closing(_connect()) as connection:
        rows = connection.execute(
            f"SELECT * FROM dev_issues{where} ORDER BY id DESC LIMIT ?",
            (*values, limit),
        ).fetchall()
        open_count = connection.execute(
            "SELECT COUNT(*) FROM dev_issues WHERE status = 'open'"
            + (" AND scope_key = ?" if scope_key else ""),
            (scope_key,) if scope_key else (),
        ).fetchone()[0]
    return DevIssueList(items=[_to_issue(row) for row in rows], open_count=int(open_count))


@router.get("/{issue_id}", response_model=DevIssue)
def get_dev_issue(issue_id: int) -> DevIssue:
    with closing(_connect()) as connection:
        row = connection.execute("SELECT * FROM dev_issues WHERE id = ?", (issue_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="development issue not found")
    return _to_issue(row)


@router.post("", response_model=DevIssue, status_code=201)
def create_dev_issue(payload: DevIssueCreate) -> DevIssue:
    now = _utc_now()
    api_revision = str(current_build_identity().get("revision") or "unknown")
    values = (
        _clean_required(payload.body),
        _clean_required(payload.scope_kind),
        _clean_required(payload.scope_key),
        _clean_required(payload.page_label),
        _clean_required(payload.route),
        _clean_optional(payload.component_hint),
        payload.author_kind,
        _clean_optional(payload.frontend_revision),
        api_revision,
        now,
    )
    with closing(_connect()) as connection:
        cursor = connection.execute(
            """
            INSERT INTO dev_issues (
                body, status, scope_kind, scope_key, page_label, route,
                component_hint, author_kind, frontend_revision, api_revision, created_at
            ) VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        row = connection.execute("SELECT * FROM dev_issues WHERE id = ?", (cursor.lastrowid,)).fetchone()
        connection.commit()
    if row is None:
        raise HTTPException(status_code=500, detail="development issue was not persisted")
    return _to_issue(row)


@router.patch("/{issue_id}", response_model=DevIssue)
def update_dev_issue(issue_id: int, payload: DevIssueUpdate) -> DevIssue:
    resolved_at = _utc_now() if payload.status == "resolved" else None
    resolution_note = _clean_optional(payload.resolution_note) if payload.status == "resolved" else None
    with closing(_connect()) as connection:
        cursor = connection.execute(
            """
            UPDATE dev_issues
               SET status = ?, resolved_at = ?, resolution_note = ?
             WHERE id = ?
            """,
            (payload.status, resolved_at, resolution_note, issue_id),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=404, detail="development issue not found")
        row = connection.execute("SELECT * FROM dev_issues WHERE id = ?", (issue_id,)).fetchone()
        connection.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="development issue not found")
    return _to_issue(row)
