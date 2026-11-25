"""Browser mutation-origin regressions for AtomWorks Studio; isolated transport, no live services."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import atomworks_studio as R

PROXY_SECRET = "test-cm-proxy-secret"


@pytest.fixture
def studio_app(monkeypatch):
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", PROXY_SECRET)
    monkeypatch.setenv("BMS_ATOMWORKS_STUDIO_URL", "http://127.0.0.1:19999")
    monkeypatch.setenv("BMS_ATOMWORKS_STUDIO_SECRET", "test-studio-secret")
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "https://frontend.example.local/health")

    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(200, json={"ok": True})

    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["https://localhost", "http://127.0.0.1:18082"])
    app.include_router(R.router)
    app.state.atomworks_studio_transport = httpx.MockTransport(handler)
    return app, seen


def _auth_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"X-BMS-CM-Proxy-Secret": PROXY_SECRET}
    if extra:
        headers.update(extra)
    return headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "https://attacker.invalid",
        "http://bms.attacker.invalid",
        "null",
        "https://node.example.ts.net:8443",
        "https://node.example.ts.net",
    ],
)
async def test_foreign_browser_origin_denied(studio_app, origin):
    app, seen = studio_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bms") as client:
        result = await client.post(
            "/api/atomworks-studio/api/v1/projects",
            content="",
            headers=_auth_headers({
                "Origin": origin,
                "Content-Type": "application/x-www-form-urlencoded",
                "Sec-Fetch-Site": "cross-site",
            }),
        )
    assert result.status_code == 403
    assert result.json()["detail"]["code"] == "origin_denied"
    assert result.headers["cache-control"] == "private, no-store"
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "http://bms", "Sec-Fetch-Site": "same-origin"},
        {"Origin": "https://frontend.example.local", "Sec-Fetch-Site": "same-origin"},
        {"Origin": "https://localhost", "Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://127.0.0.1:18082", "Sec-Fetch-Site": "same-site"},
    ],
)
async def test_normal_and_configured_browser_origins_remain_supported(studio_app, headers):
    app, seen = studio_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bms") as client:
        result = await client.post(
            "/api/atomworks-studio/api/v1/projects",
            headers=_auth_headers(headers),
        )
    assert result.status_code == 200, result.text
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fetch_site", ["cross-site", "same-site"])
async def test_cross_or_same_site_without_origin_denied(studio_app, fetch_site):
    app, seen = studio_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bms") as client:
        result = await client.post(
            "/api/atomworks-studio/api/v1/projects",
            headers=_auth_headers({"Sec-Fetch-Site": fetch_site}),
        )
    assert result.status_code == 403
    assert result.json()["detail"]["code"] == "origin_denied"
    assert result.headers["cache-control"] == "private, no-store"
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
async def test_other_mutating_methods_have_same_origin_defense(studio_app, method):
    app, seen = studio_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bms") as client:
        result = await client.request(
            method,
            "/api/atomworks-studio/api/v1/projects/1",
            headers=_auth_headers({"Origin": "https://attacker.invalid"}),
        )
    assert result.status_code == 403, result.text
    assert result.json()["detail"]["code"] == "origin_denied"
    assert seen == []


@pytest.mark.asyncio
async def test_safe_get_is_not_blocked_by_origin_check(studio_app):
    app, seen = studio_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bms") as client:
        result = await client.get(
            "/api/atomworks-studio/api/v1/projects",
            headers=_auth_headers({"Origin": "https://attacker.invalid"}),
        )
    assert result.status_code == 200, result.text
    assert len(seen) == 1
