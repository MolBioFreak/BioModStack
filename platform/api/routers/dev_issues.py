from __future__ import annotations

import hashlib
import io
import os
import secrets
import sqlite3
import stat
import warnings
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from build_identity import current_build_identity

router = APIRouter(prefix="/api/dev/issues", tags=["development-issues"])

DEV_RUNTIME_MODES = {"dev", "development", "test"}
MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024
MAX_SCREENSHOT_PIXELS = 40_000_000
ATTACHMENT_DIRECTORY_NAME = "dev-issue-attachments"
SUPPORTED_SCREENSHOT_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
_SCREENSHOT_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_IMAGE_FORMAT_MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


class DevIssueCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    lane: Literal["general", "mobile"] = "general"
    scope_kind: str = Field(min_length=1, max_length=40)
    scope_key: str = Field(min_length=1, max_length=256)
    page_label: str = Field(min_length=1, max_length=160)
    route: str = Field(min_length=1, max_length=2048)
    component_hint: str | None = Field(default=None, max_length=240)
    author_kind: Literal["operator", "ai"] = "operator"
    frontend_revision: str | None = Field(default=None, max_length=64)


class DevIssueUpdate(BaseModel):
    status: Literal["open", "in_progress", "cleared"]
    resolution_note: str | None = Field(default=None, max_length=4000)


class DevIssueScreenshot(BaseModel):
    sha256: str
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    byte_size: int
    content_url: str


class DevIssue(BaseModel):
    id: int
    issue_key: str
    body: str
    status: Literal["open", "in_progress", "cleared"]
    lane: Literal["general", "mobile"]
    scope_kind: str
    scope_key: str
    page_label: str
    route: str
    component_hint: str | None
    author_kind: Literal["operator", "ai"]
    frontend_revision: str | None
    api_revision: str
    created_at: str
    cleared_at: str | None
    resolution_note: str | None
    screenshot: DevIssueScreenshot | None = None


class DevIssueList(BaseModel):
    items: list[DevIssue]
    active_count: int


def dev_issue_ledger_enabled() -> bool:
    return os.getenv("BMS_RUNTIME_MODE", "").strip().lower() in DEV_RUNTIME_MODES


def _state_directory() -> Path:
    if not dev_issue_ledger_enabled():
        raise RuntimeError("development issue ledger is disabled")
    state_dir = os.getenv("BMS_STATE_DIR", "").strip()
    if not state_dir:
        raise RuntimeError("BMS_STATE_DIR is required for the development issue ledger")
    return Path(state_dir).expanduser().resolve()


def _database_path() -> Path:
    return _state_directory() / "dev-issues.sqlite3"


def _table_sql() -> str:
    return """
        CREATE TABLE dev_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 4000),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'in_progress', 'cleared')),
            lane TEXT NOT NULL DEFAULT 'general' CHECK(lane IN ('general', 'mobile')),
            scope_kind TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            page_label TEXT NOT NULL,
            route TEXT NOT NULL,
            component_hint TEXT,
            author_kind TEXT NOT NULL CHECK(author_kind IN ('operator', 'ai')),
            frontend_revision TEXT,
            api_revision TEXT NOT NULL,
            created_at TEXT NOT NULL,
            cleared_at TEXT,
            resolution_note TEXT,
            screenshot_sha256 TEXT,
            screenshot_media_type TEXT,
            screenshot_byte_size INTEGER,
            screenshot_relative_path TEXT
        )
    """


def _connect() -> sqlite3.Connection:
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(_table_sql().replace("CREATE TABLE dev_issues", "CREATE TABLE IF NOT EXISTS dev_issues", 1))
    existing_table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'dev_issues'"
    ).fetchone()[0]
    if "in_progress" not in existing_table_sql:
        connection.execute("ALTER TABLE dev_issues RENAME TO dev_issues_legacy")
        connection.execute(_table_sql())
        connection.execute(
            """
            INSERT INTO dev_issues (
                id, body, status, lane, scope_kind, scope_key, page_label, route,
                component_hint, author_kind, frontend_revision, api_revision,
                created_at, cleared_at, resolution_note
            )
            SELECT id, body,
                   CASE status WHEN 'resolved' THEN 'cleared' ELSE status END,
                   'general', scope_kind, scope_key, page_label, route, component_hint,
                   author_kind, frontend_revision, api_revision, created_at,
                   resolved_at, resolution_note
              FROM dev_issues_legacy
            """
        )
        connection.execute("DROP TABLE dev_issues_legacy")
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(dev_issues)").fetchall()
    }
    if "lane" not in columns:
        connection.execute(
            "ALTER TABLE dev_issues ADD COLUMN lane TEXT NOT NULL DEFAULT 'general' "
            "CHECK(lane IN ('general', 'mobile'))"
        )
    additive_columns = {
        "screenshot_sha256": "TEXT",
        "screenshot_media_type": "TEXT",
        "screenshot_byte_size": "INTEGER",
        "screenshot_relative_path": "TEXT",
    }
    for name, sql_type in additive_columns.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE dev_issues ADD COLUMN {name} {sql_type}")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_dev_issues_scope_status ON dev_issues(scope_key, status, id DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_dev_issues_status_id ON dev_issues(status, id DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_dev_issues_lane_status_id ON dev_issues(lane, status, id DESC)"
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
    if payload.pop("screenshot_relative_path", None) is not None:
        payload["screenshot"] = {
            "sha256": payload.pop("screenshot_sha256"),
            "media_type": payload.pop("screenshot_media_type"),
            "byte_size": payload.pop("screenshot_byte_size"),
            "content_url": f"/api/dev/issues/{payload['id']}/screenshot-content",
        }
    else:
        payload.pop("screenshot_sha256", None)
        payload.pop("screenshot_media_type", None)
        payload.pop("screenshot_byte_size", None)
        payload["screenshot"] = None
    return DevIssue.model_validate(payload)


def _create_values(payload: DevIssueCreate) -> tuple[object, ...]:
    return (
        _clean_required(payload.body),
        payload.lane,
        _clean_required(payload.scope_kind),
        _clean_required(payload.scope_key),
        _clean_required(payload.page_label),
        _clean_required(payload.route),
        _clean_optional(payload.component_hint),
        payload.author_kind,
        _clean_optional(payload.frontend_revision),
        str(current_build_identity().get("revision") or "unknown"),
        _utc_now(),
    )


def _insert_issue(connection: sqlite3.Connection, payload: DevIssueCreate) -> int:
    cursor = connection.execute(
        """
        INSERT INTO dev_issues (
            body, status, lane, scope_kind, scope_key, page_label, route,
            component_hint, author_kind, frontend_revision, api_revision, created_at
        ) VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _create_values(payload),
    )
    if cursor.lastrowid is None:
        raise HTTPException(status_code=500, detail="development issue was not persisted")
    return int(cursor.lastrowid)


def _detected_media_type(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def _verify_decodable_image(content: bytes, declared_media_type: str) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if _IMAGE_FORMAT_MEDIA_TYPES.get(image.format or "") != declared_media_type:
                    raise ValueError("decoded image format does not match declared media type")
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_SCREENSHOT_PIXELS:
                    raise ValueError("decoded image dimensions exceed the screenshot limit")
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                image.load()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=415, detail="screenshot is not a valid PNG, JPEG, or WebP image") from exc


async def _validated_screenshot(screenshot: UploadFile) -> tuple[bytes, str, str]:
    declared_media_type = (screenshot.content_type or "").lower()
    if declared_media_type not in SUPPORTED_SCREENSHOT_MEDIA_TYPES:
        raise HTTPException(status_code=415, detail="screenshot must be PNG, JPEG, or WebP")
    content = await screenshot.read(MAX_SCREENSHOT_BYTES + 1)
    await screenshot.close()
    if len(content) > MAX_SCREENSHOT_BYTES:
        raise HTTPException(status_code=413, detail="screenshot exceeds the 10 MiB limit")
    detected_media_type = _detected_media_type(content)
    if detected_media_type != declared_media_type:
        raise HTTPException(status_code=415, detail="screenshot MIME type does not match its file signature")
    _verify_decodable_image(content, declared_media_type)
    return content, declared_media_type, hashlib.sha256(content).hexdigest()


def _open_attachment_root(*, create: bool) -> int:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        state_descriptor = os.open(_state_directory(), directory_flags)
    except OSError as exc:
        raise HTTPException(status_code=409, detail="development issue state directory is unavailable") from exc
    try:
        if create:
            try:
                os.mkdir(ATTACHMENT_DIRECTORY_NAME, mode=0o700, dir_fd=state_descriptor)
            except FileExistsError:
                pass
        try:
            attachment_descriptor = os.open(
                ATTACHMENT_DIRECTORY_NAME,
                directory_flags,
                dir_fd=state_descriptor,
            )
        except OSError as exc:
            raise HTTPException(status_code=409, detail="development issue attachment directory is invalid") from exc
        if create:
            try:
                os.fsync(state_descriptor)
            except OSError:
                os.close(attachment_descriptor)
                raise
    finally:
        os.close(state_descriptor)
    try:
        os.fchmod(attachment_descriptor, 0o700)
    except OSError:
        os.close(attachment_descriptor)
        raise
    return attachment_descriptor


def _verify_existing_screenshot(
    attachment_descriptor: int,
    filename: str,
    content: bytes,
    digest: str,
) -> None:
    try:
        screenshot_descriptor = os.open(
            filename,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=attachment_descriptor,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail="development issue screenshot path is invalid",
        ) from exc
    with os.fdopen(screenshot_descriptor, "rb") as screenshot_file:
        existing_stat = os.fstat(screenshot_file.fileno())
        if not stat.S_ISREG(existing_stat.st_mode) or existing_stat.st_size > MAX_SCREENSHOT_BYTES:
            raise HTTPException(
                status_code=409,
                detail="development issue screenshot path conflicts with invalid content",
            )
        existing = screenshot_file.read(MAX_SCREENSHOT_BYTES + 1)
        if len(existing) != len(content) or hashlib.sha256(existing).hexdigest() != digest:
            raise HTTPException(
                status_code=409,
                detail="development issue screenshot path conflicts with existing content",
            )
        os.fchmod(screenshot_file.fileno(), 0o600)
        os.fsync(screenshot_file.fileno())
    os.fsync(attachment_descriptor)


def _write_screenshot(content: bytes, media_type: str, digest: str) -> str:
    filename = f"{digest}.{_SCREENSHOT_EXTENSIONS[media_type]}"
    relative_path = Path(ATTACHMENT_DIRECTORY_NAME) / filename
    attachment_descriptor = _open_attachment_root(create=True)
    temporary_name = f".{digest}.{secrets.token_hex(8)}.tmp"
    temporary_created = False
    try:
        temporary_descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=attachment_descriptor,
        )
        temporary_created = True
        with os.fdopen(temporary_descriptor, "wb") as screenshot_file:
            screenshot_file.write(content)
            screenshot_file.flush()
            os.fsync(screenshot_file.fileno())
        try:
            os.link(
                temporary_name,
                filename,
                src_dir_fd=attachment_descriptor,
                dst_dir_fd=attachment_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            _verify_existing_screenshot(attachment_descriptor, filename, content, digest)
        else:
            os.unlink(temporary_name, dir_fd=attachment_descriptor)
            temporary_created = False
            os.fsync(attachment_descriptor)
    finally:
        if temporary_created:
            try:
                os.unlink(temporary_name, dir_fd=attachment_descriptor)
            except OSError:
                pass
        os.close(attachment_descriptor)
    return relative_path.as_posix()


@router.get("", response_model=DevIssueList)
def list_dev_issues(
    status: Literal["open", "in_progress", "cleared", "active", "all"] = "active",
    lane: Literal["general", "mobile"] | None = Query(default=None),
    scope_key: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=100, ge=1, le=200),
) -> DevIssueList:
    filters: list[str] = []
    values: list[object] = []
    if status == "active":
        filters.append("status IN ('open', 'in_progress')")
    elif status != "all":
        filters.append("status = ?")
        values.append(status)
    if scope_key:
        filters.append("scope_key = ?")
        values.append(scope_key)
    if lane:
        filters.append("lane = ?")
        values.append(lane)
    where = f" WHERE {' AND '.join(filters)}" if filters else ""

    active_filters = ["status IN ('open', 'in_progress')"]
    active_values: list[object] = []
    if scope_key:
        active_filters.append("scope_key = ?")
        active_values.append(scope_key)
    if lane:
        active_filters.append("lane = ?")
        active_values.append(lane)
    active_where = f" WHERE {' AND '.join(active_filters)}"

    with closing(_connect()) as connection:
        rows = connection.execute(
            f"SELECT * FROM dev_issues{where} ORDER BY id DESC LIMIT ?",
            (*values, limit),
        ).fetchall()
        active_count = connection.execute(
            f"SELECT COUNT(*) FROM dev_issues{active_where}",
            active_values,
        ).fetchone()[0]
    return DevIssueList(items=[_to_issue(row) for row in rows], active_count=int(active_count))


@router.get("/{issue_id}", response_model=DevIssue)
def get_dev_issue(issue_id: int) -> DevIssue:
    with closing(_connect()) as connection:
        row = connection.execute("SELECT * FROM dev_issues WHERE id = ?", (issue_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="development issue not found")
    return _to_issue(row)


@router.get("/{issue_id}/screenshot-content")
def get_dev_issue_screenshot_content(issue_id: int) -> Response:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT screenshot_sha256, screenshot_media_type, screenshot_byte_size, screenshot_relative_path "
            "FROM dev_issues WHERE id = ?",
            (issue_id,),
        ).fetchone()
    if row is None or row[3] is None:
        raise HTTPException(status_code=404, detail="development issue screenshot not found")

    digest = row[0]
    media_type = row[1]
    byte_size = row[2]
    relative_path = row[3]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or media_type not in SUPPORTED_SCREENSHOT_MEDIA_TYPES
        or not isinstance(byte_size, int)
        or not 0 < byte_size <= MAX_SCREENSHOT_BYTES
    ):
        raise HTTPException(status_code=409, detail="development issue screenshot metadata is invalid")

    expected_filename = f"{digest}.{_SCREENSHOT_EXTENSIONS[media_type]}"
    if Path(relative_path).parts != (ATTACHMENT_DIRECTORY_NAME, expected_filename):
        raise HTTPException(status_code=409, detail="development issue screenshot path is invalid")
    attachment_descriptor = _open_attachment_root(create=False)
    try:
        try:
            descriptor = os.open(
                expected_filename,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=attachment_descriptor,
            )
            with os.fdopen(descriptor, "rb") as screenshot_file:
                file_stat = os.fstat(screenshot_file.fileno())
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size != byte_size:
                    raise HTTPException(
                        status_code=409,
                        detail="development issue screenshot failed integrity verification",
                    )
                content = screenshot_file.read(MAX_SCREENSHOT_BYTES + 1)
        except HTTPException:
            raise
        except OSError as exc:
            raise HTTPException(status_code=409, detail="development issue screenshot is unavailable") from exc
    finally:
        os.close(attachment_descriptor)
    if (
        len(content) != byte_size
        or hashlib.sha256(content).hexdigest() != digest
        or _detected_media_type(content) != media_type
    ):
        raise HTTPException(status_code=409, detail="development issue screenshot failed integrity verification")
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("", response_model=DevIssue, status_code=201)
def create_dev_issue(payload: DevIssueCreate) -> DevIssue:
    with closing(_connect()) as connection:
        issue_id = _insert_issue(connection, payload)
        row = connection.execute("SELECT * FROM dev_issues WHERE id = ?", (issue_id,)).fetchone()
        connection.commit()
    if row is None:
        raise HTTPException(status_code=500, detail="development issue was not persisted")
    return _to_issue(row)


@router.post("/with-screenshot", response_model=DevIssue, status_code=201)
async def create_dev_issue_with_screenshot(
    body: Annotated[str, Form(min_length=1, max_length=4000)],
    scope_kind: Annotated[str, Form(min_length=1, max_length=40)],
    scope_key: Annotated[str, Form(min_length=1, max_length=256)],
    page_label: Annotated[str, Form(min_length=1, max_length=160)],
    route: Annotated[str, Form(min_length=1, max_length=2048)],
    screenshot: Annotated[UploadFile, File()],
    lane: Annotated[Literal["general", "mobile"], Form()] = "general",
    component_hint: Annotated[str | None, Form(max_length=240)] = None,
    author_kind: Annotated[Literal["operator", "ai"], Form()] = "operator",
    frontend_revision: Annotated[str | None, Form(max_length=64)] = None,
) -> DevIssue:
    content, media_type, digest = await _validated_screenshot(screenshot)
    payload = DevIssueCreate(
        body=body,
        lane=lane,
        scope_kind=scope_kind,
        scope_key=scope_key,
        page_label=page_label,
        route=route,
        component_hint=component_hint,
        author_kind=author_kind,
        frontend_revision=frontend_revision,
    )
    with closing(_connect()) as connection:
        try:
            issue_id = _insert_issue(connection, payload)
            relative_path = _write_screenshot(content, media_type, digest)
            connection.execute(
                """
                UPDATE dev_issues
                   SET screenshot_sha256 = ?, screenshot_media_type = ?,
                       screenshot_byte_size = ?, screenshot_relative_path = ?
                 WHERE id = ?
                """,
                (digest, media_type, len(content), relative_path, issue_id),
            )
            row = connection.execute("SELECT * FROM dev_issues WHERE id = ?", (issue_id,)).fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    if row is None:
        raise HTTPException(status_code=500, detail="development issue was not persisted")
    return _to_issue(row)


@router.patch("/{issue_id}", response_model=DevIssue)
def update_dev_issue(issue_id: int, payload: DevIssueUpdate) -> DevIssue:
    cleared_at = _utc_now() if payload.status == "cleared" else None
    resolution_note = _clean_optional(payload.resolution_note) if payload.status == "cleared" else None
    with closing(_connect()) as connection:
        cursor = connection.execute(
            """
            UPDATE dev_issues
               SET status = ?, cleared_at = ?, resolution_note = ?
             WHERE id = ?
            """,
            (payload.status, cleared_at, resolution_note, issue_id),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=404, detail="development issue not found")
        row = connection.execute("SELECT * FROM dev_issues WHERE id = ?", (issue_id,)).fetchone()
        connection.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="development issue not found")
    return _to_issue(row)
