from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from routers import dev_issues


def _image_bytes(image_format: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), (15, 23, 42)).save(output, format=image_format)
    return output.getvalue()


PNG_BYTES = _image_bytes("PNG")
JPEG_BYTES = _image_bytes("JPEG")
WEBP_BYTES = _image_bytes("WEBP")
CORRUPT_JPEG_BYTES = JPEG_BYTES[:-1]
CORRUPT_WEBP_BYTES = WEBP_BYTES[:21] + b"\x00" + WEBP_BYTES[22:]
CREATE_FIELDS = {
    "body": "Screenshot regression",
    "scope_kind": "page",
    "scope_key": "page:dashboard",
    "page_label": "Dashboard",
    "route": "/",
    "component_hint": "Issue drawer",
    "author_kind": "operator",
    "frontend_revision": "frontend-test",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("BMS_RUNTIME_MODE", "development")
    monkeypatch.setenv("BMS_STATE_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(dev_issues.router)
    return TestClient(app)


def _create_with_screenshot(
    client: TestClient,
    *,
    content: bytes = PNG_BYTES,
    media_type: str = "image/png",
):
    return client.post(
        "/api/dev/issues/with-screenshot",
        data=CREATE_FIELDS,
        files={"screenshot": ("clipboard", content, media_type)},
    )


def test_multipart_create_persists_lists_and_retrieves_verified_screenshot(
    client: TestClient,
    tmp_path: Path,
) -> None:
    created = _create_with_screenshot(client)

    assert created.status_code == 201, created.text
    issue = created.json()
    digest = hashlib.sha256(PNG_BYTES).hexdigest()
    assert issue["screenshot"] == {
        "sha256": digest,
        "media_type": "image/png",
        "byte_size": len(PNG_BYTES),
        "content_url": f"/api/dev/issues/{issue['id']}/screenshot-content",
    }

    listed = client.get("/api/dev/issues", params={"status": "all"})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["screenshot"] == issue["screenshot"]

    content = client.get(issue["screenshot"]["content_url"])
    assert content.status_code == 200
    assert content.content == PNG_BYTES
    assert content.headers["content-type"] == "image/png"
    assert content.headers["content-disposition"] == "inline"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["cache-control"] == "private, no-store"

    with sqlite3.connect(tmp_path / "dev-issues.sqlite3") as connection:
        row = connection.execute(
            "SELECT screenshot_sha256, screenshot_media_type, screenshot_byte_size, screenshot_relative_path "
            "FROM dev_issues WHERE id = ?",
            (issue["id"],),
        ).fetchone()
    assert row is not None
    assert row[:3] == (digest, "image/png", len(PNG_BYTES))
    relative_path = Path(row[3])
    assert not relative_path.is_absolute()
    assert relative_path.parts == ("dev-issue-attachments", f"{digest}.png")
    stored_path = tmp_path / relative_path
    assert stored_path.read_bytes() == PNG_BYTES
    assert stat.S_IMODE(stored_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(stored_path.parent.stat().st_mode) == 0o700


def test_existing_json_create_remains_unchanged_and_has_no_screenshot(client: TestClient) -> None:
    response = client.post("/api/dev/issues", json=CREATE_FIELDS)

    assert response.status_code == 201, response.text
    assert response.json()["screenshot"] is None


def test_existing_issue_database_is_additively_migrated_without_losing_rows(
    client: TestClient,
    tmp_path: Path,
) -> None:
    with sqlite3.connect(tmp_path / "dev-issues.sqlite3") as connection:
        connection.execute(
            """
            CREATE TABLE dev_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 4000),
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'in_progress', 'cleared')),
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
                resolution_note TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO dev_issues (
                body, status, scope_kind, scope_key, page_label, route,
                component_hint, author_kind, frontend_revision, api_revision, created_at
            ) VALUES (?, 'open', ?, ?, ?, ?, NULL, 'operator', NULL, ?, ?)
            """,
            ("Existing issue", "page", "page:dashboard", "Dashboard", "/", "api-old", "2026-08-27T00:00:00Z"),
        )
        connection.commit()

    response = client.get("/api/dev/issues", params={"status": "all"})

    assert response.status_code == 200
    assert response.json()["items"][0]["body"] == "Existing issue"
    assert response.json()["items"][0]["screenshot"] is None
    with sqlite3.connect(tmp_path / "dev-issues.sqlite3") as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(dev_issues)")}
    assert {
        "screenshot_sha256",
        "screenshot_media_type",
        "screenshot_byte_size",
        "screenshot_relative_path",
    } <= columns


@pytest.mark.parametrize(
    ("media_type", "content"),
    [("image/jpeg", JPEG_BYTES), ("image/webp", WEBP_BYTES)],
)
def test_supported_screenshot_signatures_are_accepted(
    client: TestClient,
    media_type: str,
    content: bytes,
) -> None:
    response = _create_with_screenshot(client, media_type=media_type, content=content)

    assert response.status_code == 201, response.text
    assert response.json()["screenshot"]["media_type"] == media_type


def test_oversized_screenshot_is_rejected_without_issue_or_file(client: TestClient, tmp_path: Path) -> None:
    response = _create_with_screenshot(
        client,
        content=b"\x89PNG\r\n\x1a\n" + b"x" * (10 * 1024 * 1024),
    )

    assert response.status_code == 413
    assert client.get("/api/dev/issues", params={"status": "all"}).json()["items"] == []
    assert not (tmp_path / "dev-issue-attachments").exists()


def test_excessive_pixel_count_is_rejected_without_publication(client: TestClient, tmp_path: Path) -> None:
    output = io.BytesIO()
    Image.new("1", (8_000, 5_001), 0).save(output, format="PNG")
    content = output.getvalue()
    assert len(content) <= dev_issues.MAX_SCREENSHOT_BYTES

    response = _create_with_screenshot(client, content=content)

    assert response.status_code == 415
    assert client.get("/api/dev/issues", params={"status": "all"}).json()["items"] == []
    assert not (tmp_path / "dev-issue-attachments").exists()


def test_unsupported_screenshot_mime_is_rejected(client: TestClient) -> None:
    response = _create_with_screenshot(client, media_type="image/gif", content=b"GIF89a")

    assert response.status_code == 415


def test_screenshot_mime_magic_mismatch_is_rejected(client: TestClient) -> None:
    response = _create_with_screenshot(client, media_type="image/png", content=JPEG_BYTES)

    assert response.status_code == 415


def test_header_only_image_is_rejected(client: TestClient) -> None:
    response = _create_with_screenshot(client, media_type="image/png", content=b"\x89PNG\r\n\x1a\n")

    assert response.status_code == 415


@pytest.mark.parametrize(
    ("media_type", "content"),
    [
        ("image/jpeg", CORRUPT_JPEG_BYTES),
        ("image/webp", CORRUPT_WEBP_BYTES),
    ],
)
def test_corrupt_encoded_image_data_is_rejected(
    client: TestClient,
    media_type: str,
    content: bytes,
) -> None:
    response = _create_with_screenshot(client, media_type=media_type, content=content)

    assert response.status_code == 415
    assert response.json()["detail"] == "screenshot is not a valid PNG, JPEG, or WebP image"


def test_attachment_root_symlink_is_rejected_without_external_write(
    client: TestClient,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "dev-issue-attachments").symlink_to(outside, target_is_directory=True)
    safe_client = TestClient(client.app, raise_server_exceptions=False)

    response = _create_with_screenshot(safe_client)

    assert response.status_code == 409
    assert list(outside.iterdir()) == []
    assert safe_client.get("/api/dev/issues", params={"status": "all"}).json()["items"] == []


def test_existing_attachment_root_entry_is_fsynced_before_use(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachment_root = tmp_path / "dev-issue-attachments"
    attachment_root.mkdir(mode=0o700)
    state_inode = tmp_path.stat().st_ino
    original_fsync = dev_issues.os.fsync
    fsynced_inodes: list[int] = []

    def record_fsync(descriptor: int) -> None:
        fsynced_inodes.append(os.fstat(descriptor).st_ino)
        original_fsync(descriptor)

    monkeypatch.setattr(dev_issues.os, "fsync", record_fsync)

    attachment_descriptor = dev_issues._open_attachment_root(create=True)
    os.close(attachment_descriptor)

    assert state_inode in fsynced_inodes


def test_attachment_file_symlink_is_rejected_without_target_overwrite(
    client: TestClient,
    tmp_path: Path,
) -> None:
    attachment_root = tmp_path / "dev-issue-attachments"
    attachment_root.mkdir(mode=0o700)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"preserve me")
    digest = hashlib.sha256(PNG_BYTES).hexdigest()
    (attachment_root / f"{digest}.png").symlink_to(outside)
    safe_client = TestClient(client.app, raise_server_exceptions=False)

    response = _create_with_screenshot(safe_client)

    assert response.status_code == 409
    assert outside.read_bytes() == b"preserve me"
    assert safe_client.get("/api/dev/issues", params={"status": "all"}).json()["items"] == []


def test_interrupted_file_fsync_never_publishes_final_content(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachment_root = tmp_path / "dev-issue-attachments"
    attachment_root.mkdir(mode=0o700)
    digest = hashlib.sha256(PNG_BYTES).hexdigest()
    final_path = attachment_root / f"{digest}.png"

    original_fsync = dev_issues.os.fsync

    def interrupt_file_fsync(descriptor: int) -> None:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise KeyboardInterrupt("simulated abrupt interruption")
        original_fsync(descriptor)

    monkeypatch.setattr(dev_issues.os, "fsync", interrupt_file_fsync)

    with pytest.raises(KeyboardInterrupt, match="simulated abrupt interruption"):
        dev_issues._write_screenshot(PNG_BYTES, "image/png", digest)

    assert not final_path.exists()
    assert list(attachment_root.iterdir()) == []


def test_existing_content_addressed_file_is_resynced_before_reuse(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachment_root = tmp_path / "dev-issue-attachments"
    attachment_root.mkdir(mode=0o700)
    digest = hashlib.sha256(PNG_BYTES).hexdigest()
    final_path = attachment_root / f"{digest}.png"
    final_path.write_bytes(PNG_BYTES)
    final_path.chmod(0o600)
    original_fsync = dev_issues.os.fsync
    fsynced_inodes: list[int] = []

    def record_fsync(descriptor: int) -> None:
        fsynced_inodes.append(os.fstat(descriptor).st_ino)
        original_fsync(descriptor)

    monkeypatch.setattr(dev_issues.os, "fsync", record_fsync)

    relative_path = dev_issues._write_screenshot(PNG_BYTES, "image/png", digest)

    assert relative_path == f"dev-issue-attachments/{digest}.png"
    assert final_path.stat().st_ino in fsynced_inodes
    assert attachment_root.stat().st_ino in fsynced_inodes
    assert final_path.read_bytes() == PNG_BYTES


def test_content_rejects_file_that_no_longer_matches_ledger(client: TestClient, tmp_path: Path) -> None:
    created = _create_with_screenshot(client).json()
    with sqlite3.connect(tmp_path / "dev-issues.sqlite3") as connection:
        relative_path = connection.execute(
            "SELECT screenshot_relative_path FROM dev_issues WHERE id = ?",
            (created["id"],),
        ).fetchone()[0]
    (tmp_path / relative_path).write_bytes(PNG_BYTES + b"tampered")

    response = client.get(created["screenshot"]["content_url"])

    assert response.status_code == 409


def test_content_rejects_oversized_ledger_metadata_before_reading_file(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _create_with_screenshot(client).json()
    with sqlite3.connect(tmp_path / "dev-issues.sqlite3") as connection:
        connection.execute(
            "UPDATE dev_issues SET screenshot_byte_size = ? WHERE id = ?",
            (dev_issues.MAX_SCREENSHOT_BYTES + 1, created["id"]),
        )
        connection.commit()

    original_open = dev_issues.os.open

    def guarded_open(path, *args, **kwargs):
        if path == dev_issues.ATTACHMENT_DIRECTORY_NAME or str(path).endswith((".png", ".jpg", ".webp")):
            raise AssertionError("oversized screenshot metadata must be rejected before file bytes are opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(dev_issues.os, "open", guarded_open)

    response = client.get(created["screenshot"]["content_url"])

    assert response.status_code == 409


async def _drive_asgi(
    app,
    *,
    chunks: Iterable[bytes],
    content_length: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    body_chunks = list(chunks)
    messages = iter(
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(body_chunks) - 1,
        }
        for index, chunk in enumerate(body_chunks)
    )
    receive_calls = 0
    sent: list[dict[str, Any]] = []
    headers = [(b"content-type", b"multipart/form-data; boundary=bounded")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))

    async def receive() -> dict[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        return next(messages, {"type": "http.disconnect"})

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/dev/issues/with-screenshot",
            "raw_path": b"/api/dev/issues/with-screenshot",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    return sent, receive_calls


def _registered_screenshot_middleware_class() -> Any:
    import main

    matches = [
        item.cls
        for item in main.app.user_middleware
        if getattr(item.cls, "__name__", "") == "DevIssueScreenshotUploadLimitMiddleware"
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.asyncio
async def test_screenshot_route_declared_length_is_rejected_before_receive() -> None:
    middleware_class = _registered_screenshot_middleware_class()
    state: dict[str, Any] = {}

    async def downstream(*_args) -> None:
        state["invoked"] = True

    app = middleware_class(downstream, max_upload_bytes=8, multipart_allowance_bytes=4)
    sent, receive_calls = await _drive_asgi(
        app,
        chunks=[b"not-read"],
        content_length=13,
    )

    assert receive_calls == 0
    assert state == {}
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_screenshot_route_streamed_overflow_is_rejected_before_handler() -> None:
    middleware_class = _registered_screenshot_middleware_class()
    state: dict[str, Any] = {}

    async def downstream(_scope, receive, _send) -> None:
        state["invoked"] = True
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        state["handler_reached"] = True

    app = middleware_class(downstream, max_upload_bytes=8, multipart_allowance_bytes=4)
    sent, receive_calls = await _drive_asgi(
        app,
        chunks=[b"123456", b"789012", b"3"],
    )

    assert receive_calls == 3
    assert state == {"invoked": True}
    assert sent[0]["status"] == 413
