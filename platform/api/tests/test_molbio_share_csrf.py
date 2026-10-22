"""Browser mutation-origin regressions; isolated publication, no live services."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import molbio_share as R
from share_server import grants as G


@pytest.fixture
def publication(tmp_path, monkeypatch):
    root = tmp_path / "publish"
    root.mkdir(mode=0o750)
    G.prepare_publish_dir(root)
    monkeypatch.setenv("BMS_SHARE_PUBLISH_DIR", str(root))
    monkeypatch.setenv("BMS_SHARE_PUBLIC_HOST", "node.example.ts.net")
    monkeypatch.delenv("BMS_FRONTEND_HEALTH_URL", raising=False)
    grant_id = G.new_grant_id()
    now = G.utc_now()
    G.save_index(root, {"grants": [{"grant_id": grant_id, "created_at": now,
        "expires_at": now + 3600, "revoked_at": None, "creator": "operator",
        "label": "test", "scope": {}, "bundle_bytes": 0}]})
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["https://localhost", "http://127.0.0.1:18082"])
    app.include_router(R.router)
    return app, root, grant_id


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["https://attacker.invalid", "http://bms.attacker.invalid", "null", "https://node.example.ts.net:8443"])
async def test_foreign_browser_cannot_revoke(publication, origin):
    app, root, grant_id = publication
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bms") as client:
        result = await client.post(f"/api/molbio/shares/{grant_id}/revoke", content="",
            headers={"Origin": origin, "Content-Type": "application/x-www-form-urlencoded", "Sec-Fetch-Site": "cross-site"})
    assert result.status_code == 403
    assert result.json()["detail"]["code"] == "share_origin_denied"
    assert G.load_index(root)["grants"][0]["revoked_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Origin": "http://bms", "Sec-Fetch-Site": "same-origin"},
    {"Origin": "https://node.example.ts.net", "Sec-Fetch-Site": "same-origin"},
    {"Origin": "https://localhost", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://127.0.0.1:18082", "Sec-Fetch-Site": "same-site"}])
async def test_normal_and_configured_browser_origins_remain_supported(publication, headers):
    app, root, grant_id = publication
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bms") as client:
        result = await client.post(f"/api/molbio/shares/{grant_id}/revoke", headers=headers)
    assert result.status_code == 200, result.text
    assert G.load_index(root)["grants"][0]["revoked_at"] is not None


@pytest.mark.asyncio
async def test_cross_site_without_origin_cannot_revoke(publication):
    app, root, grant_id = publication
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bms") as client:
        result = await client.post(f"/api/molbio/shares/{grant_id}/revoke", headers={"Sec-Fetch-Site": "cross-site"})
    assert result.status_code == 403
    assert G.load_index(root)["grants"][0]["revoked_at"] is None


@pytest.mark.asyncio
async def test_create_has_the_same_browser_defense(publication):
    app, root, _ = publication
    before = G.load_index(root)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bms") as client:
        result = await client.post("/api/molbio/shares", headers={"Origin": "https://attacker.invalid"},
            json={"scope": {"kind": "items", "sequence_ids": ["not-a-real-sequence"]}, "expires_in_seconds": 3600})
    assert result.status_code == 403, result.text
    assert G.load_index(root) == before
