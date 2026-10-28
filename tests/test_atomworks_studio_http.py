"""Real isolated CPU editor behind the actual BMS identity/CSRF proxy.

STUDIO_TEST_PYTHON selects the prepared standalone editor, never the API venv.
No database, managed listener, live ingress or inference is used.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import re
import socket
import subprocess
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "platform/api"))
from routers.atomworks_studio import router

BASE = "/api/atomworks-studio"


def document_content(cif: str) -> str:
    # Upstream regenerates these two serialization timestamps on every export.
    # Only Undo semantic comparison ignores them; custody always keeps raw bytes.
    return re.sub(r"^_entry\.(?:date|time)\s+[^\n]*\n", "", cif, flags=re.MULTILINE)



@pytest.fixture(scope="module")
def sidecar(tmp_path_factory):
    python = os.environ.get("STUDIO_TEST_PYTHON")
    if not python:
        pytest.skip("STUDIO_TEST_PYTHON must select isolated prepared Python 3.12 editor")
    secret = secrets.token_hex(32)
    directory = tmp_path_factory.mktemp("studio-isolated")
    log = (directory / "service.log").open("w+")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    env = {**os.environ, "BMS_ATOMWORKS_STUDIO_SECRET": secret,
           "ANNOTATION_STUDIO_SESSION_DIR": str(directory / "sessions")}
    process = subprocess.Popen([python, "-m", "uvicorn", "atomworks_studio_service:create_app",
        "--factory", "--app-dir", str(ROOT / "scripts"), "--fd", str(listener.fileno()),
        "--no-access-log"], pass_fds=(listener.fileno(),), env=env, stdout=log, stderr=log)
    listener.close()
    url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=url, timeout=1, trust_env=False) as client:
            for _ in range(180):
                if process.poll() is not None:
                    log.seek(0)
                    pytest.fail(log.read())
                try:
                    result = client.get("/api/health", headers={"X-BMS-Studio-Secret": secret,
                                                               "X-BMS-Studio-Owner": "probe"})
                    if result.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.25)
            else:
                log.seek(0)
                pytest.fail("isolated editor readiness timed out: " + log.read())
        yield url, secret
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()


@pytest.fixture
def client(sidecar, monkeypatch):
    url, secret = sidecar
    monkeypatch.setenv("BMS_ATOMWORKS_STUDIO_URL", url)
    monkeypatch.setenv("BMS_ATOMWORKS_STUDIO_SECRET", secret)
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", "test-application-secret")
    app = FastAPI()
    # Exercise the existing authenticated-state branch without a new identity scheme.
    @app.middleware("http")
    async def fixture_principal(request, call_next):
        if request.headers.get("x-test-principal"):
            request.state.authenticated_principal = {"id": request.headers["x-test-principal"], "roles": []}
        return await call_next(request)
    app.include_router(router)
    with TestClient(app, base_url="http://bms") as client:
        yield client


def test_no_direct_service_access_or_identity_spoof(sidecar, client):
    url, _ = sidecar
    for path in ("/", "/assets/index-CUTfffbv.js", "/api/health", "/api/v1/schema"):
        assert httpx.get(url + path).status_code == 401
        assert client.get(BASE + path, headers={"X-BMS-Studio-Owner": "spoof",
            "Tailscale-User-Login": "spoof", "X-BMS-Studio-Secret": "spoof"}).status_code == 401
    assert client.get(BASE + "/api/schema", headers={"X-BMS-CM-Proxy-Secret": "wrong"}).status_code == 401
    # The actual application's shared principal remains supported, not a per-user gate.
    assert client.get(BASE + "/api/schema", headers={"X-BMS-CM-Proxy-Secret": "test-application-secret"}).status_code == 200


def test_private_assets_worker_off_and_both_api_forms(client):
    client.headers["x-test-principal"] = "alice"
    index = client.get(BASE + "/")
    assert index.status_code == 200
    assert 'name="atomworks-studio-service-worker" content="off"' in index.text
    bundle = client.get(BASE + "/assets/index-CUTfffbv.js")
    assert 'return `/api/atomworks-studio`+' in bundle.text
    for result in (index, bundle, client.get(BASE + "/api/schema"), client.get(BASE + "/api/v1/schema")):
        assert result.status_code == 200
        assert result.headers["cache-control"] == "private, no-store"
        assert "worker-src 'none'" in result.headers["content-security-policy"]
        assert "etag" not in result.headers
        assert "set-cookie" not in result.headers
    assert client.get(BASE + "/sw.js").status_code == 404


def test_browser_csrf_denied_before_mutation(client):
    client.headers["x-test-principal"] = "alice"
    for headers in ({"Origin": "https://attacker.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
        assert client.post(BASE + "/api/v1/sessions", headers=headers).status_code == 403


def test_real_private_import_edit_undo_frozen_export_and_owner_isolation(client):
    client.headers["x-test-principal"] = "alice"
    cif = client.get(BASE + "/api/examples/unconditional/cif")
    assert cif.status_code == 200
    imported = client.post(BASE + "/api/v1/sessions", files={"file": ("original.cif", cif.content)},
                           data={"parse_preset": "minimal"}, headers={"Origin": "http://bms"})
    assert imported.status_code == 200, imported.text
    sid = imported.json()["session_id"]
    path = BASE + "/api/v1/sessions/" + sid
    initial = client.post(path + "/export")
    assert initial.status_code == 200, initial.text
    frozen = initial.json()
    edited = client.post(path + "/conditions", json={"condition": "hotspot", "atom_indices": [0],
                                                       "scope": "atom", "enabled": True})
    assert edited.status_code == 200, edited.text
    changed = client.post(path + "/export")
    assert changed.status_code == 200, changed.text
    assert changed.json()["cif"] != frozen["cif"]
    assert frozen["revision"] == initial.json()["revision"]
    assert frozen["cif"] == initial.json()["cif"]  # the received snapshot never refetched
    undone = client.post(path + "/undo")
    assert undone.status_code == 200, undone.text
    restored = client.post(path + "/export")
    assert restored.status_code == 200, restored.text
    assert document_content(restored.json()["cif"]) == document_content(frozen["cif"])
    # Every route form checks the trusted principal, including freeze/export.
    client.headers["x-test-principal"] = "bob"
    for suffix in ("", "/cif"):
        assert client.get(path + suffix).status_code == 404
        assert client.get(path.replace("/api/v1/", "/api/") + suffix).status_code == 404
    assert client.post(path + "/export").status_code == 404
    client.headers["x-test-principal"] = "alice"
    assert client.get(path).status_code == 200
    assert client.delete(path).status_code == 204
    assert client.get(path).status_code == 404


def browser_fixture_app():
    """Task-owned receiving harness, not a replacement production ingress."""
    from fastapi.responses import HTMLResponse
    app = FastAPI()
    app.include_router(router)
    @app.get("/")
    def harness():
        return HTMLResponse('<!doctype html><iframe id="studio" style="width:1200px;height:900px"></iframe>')
    return app


@pytest.mark.skipif(os.environ.get("BMS_STUDIO_BROWSER") != "1", reason="opt-in isolated real Chrome iframe probe")
def test_real_browser_iframe_edit_undo_export_and_cache(sidecar, tmp_path):
    from playwright.sync_api import sync_playwright
    url, secret = sidecar
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0)); listener.listen()
    port = listener.getsockname()[1]
    env = {**os.environ, "BMS_ATOMWORKS_STUDIO_URL": url, "BMS_ATOMWORKS_STUDIO_SECRET": secret,
           "BMS_CM_TRUSTED_PROXY_SECRET": secrets.token_hex(32)}
    log = (tmp_path / "browser-api.log").open("w+")
    process = subprocess.Popen([str(Path(sys.executable)), "-m", "uvicorn",
        "test_atomworks_studio_http:browser_fixture_app", "--factory", "--app-dir", str(ROOT / "tests"),
        "--fd", str(listener.fileno()), "--no-access-log"], pass_fds=(listener.fileno(),),
        env=env, stdout=log, stderr=log)
    listener.close()
    origin = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                log.seek(0); pytest.fail(log.read())
            try:
                if httpx.get(origin, timeout=.5).status_code == 200: break
            except httpx.HTTPError: pass
            time.sleep(.1)
        else: pytest.fail("browser receiving harness did not become ready")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get("BMS_STUDIO_CHROME", "/usr/bin/google-chrome"),
                headless=True, args=["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            context = browser.new_context(extra_http_headers={"X-BMS-CM-Proxy-Secret": env["BMS_CM_TRUSTED_PROXY_SECRET"]})
            page = context.new_page()
            page.goto(origin)
            page.evaluate("window.notifications=[]; addEventListener('message', e => window.notifications.push({origin:e.origin,...e.data})); caches.open('bms-sentinel')")
            example = context.request.get(origin + BASE + "/api/examples/unconditional/cif")
            imported = context.request.post(origin + BASE + "/api/v1/sessions", multipart={
                "file": {"name": "browser-original.cif", "mimeType": "chemical/x-mmcif", "buffer": example.body()},
                "parse_preset": "minimal"})
            assert imported.status == 200, imported.text()
            sid = imported.json()["session_id"]
            session_path = origin + BASE + "/api/v1/sessions/" + sid
            original = context.request.post(session_path + "/export").json()
            page.eval_on_selector("#studio", "(frame, src) => frame.src=src",
                BASE + f"/?session={sid}&embed=1&parent_origin=" + origin)
            page.wait_for_function("sid => notifications.some(n => n.type==='ready' && n.session_id===sid)", arg=sid, timeout=90000)
            frame = page.frames[1]
            frame.evaluate("() => window.atomworksStudio.applyEdit('/conditions', {condition:'hotspot',atom_indices:[0],scope:'atom',enabled:true})")
            page.wait_for_function("sid => notifications.some(n => n.type==='changed' && n.session_id===sid)", arg=sid)
            changed = context.request.post(session_path + "/export").json()
            assert changed["cif"] != original["cif"]
            assert frame.locator("#undo").is_enabled()
            with page.expect_response(lambda r: r.url == session_path + "/undo") as undo:
                frame.locator("#undo").evaluate("button => button.click()")
            assert undo.value.status == 200
            restored = context.request.post(session_path + "/export").json()
            assert document_content(restored["cif"]) == document_content(original["cif"])
            assert page.evaluate("() => caches.keys()") == ["bms-sentinel"]
            assert page.evaluate("async () => (await navigator.serviceWorker.getRegistrations()).length") == 0
            assert page.evaluate("origin => notifications.filter(n => n.session_id).every(n => n.origin === origin)", origin)
            context.close(); browser.close()
    finally:
        process.terminate()
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        log.close()


def test_errors_do_not_cache_and_unconfigured_service_does_not_fallback(client, monkeypatch):
    client.headers["x-test-principal"] = "alice"
    for value in ("", "https://remote.invalid", "http://127.0.0.1/path", "http://user@localhost", "http://127.0.0.1:invalid"):
        monkeypatch.setenv("BMS_ATOMWORKS_STUDIO_URL", value)
        result = client.get(BASE + "/api/schema")
        assert result.status_code == 503
        assert result.headers["cache-control"] == "private, no-store"
    client.headers.pop("x-test-principal")
    result = client.get(BASE + "/")
    assert result.status_code == 401
    assert result.headers["cache-control"] == "private, no-store"


def test_transport_strips_client_headers_and_bounds_body(client, monkeypatch):
    from services import atomworks_studio as service
    client.headers["x-test-principal"] = "alice"
    seen = []
    def downstream(request):
        seen.append(request)
        return httpx.Response(200, text="ok", headers={"set-cookie": "private=bad", "etag": "old",
            "cache-control": "public", "x-bms-studio-secret": "must-not-leak"})
    client.app.state.atomworks_studio_transport = httpx.MockTransport(downstream)
    result = client.get(BASE + "/api/schema", headers={"cookie": "browser=private",
        "authorization": "Bearer browser", "X-BMS-Studio-Owner": "bob", "If-None-Match": "old"})
    assert result.status_code == 200
    assert all(h not in seen[0].headers for h in ("cookie", "authorization", "if-none-match"))
    assert seen[0].headers["X-BMS-Studio-Owner"] != "bob"
    assert all(h not in result.headers for h in ("set-cookie", "etag", "x-bms-studio-secret"))
    monkeypatch.setattr(service, "MAX_BODY", 12)
    assert client.post(BASE + "/api/sessions", content=b"x" * 13).status_code == 413
    assert len(seen) == 1
