"""Focused tests for temporary public MolBio shares (grants, share server, router, export)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from share_server import app as share_app_module
from share_server import grants as G
from share_server.app import ROUTES, PURE_COMPUTE_POST_ROUTES, ShareApp, ShareSettings, client_ip

API_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def asgi(app, method, path, *, headers=(), body=b"", client=("127.0.0.1", 50000), query=b""):
    scope = {
        "type": "http", "method": method, "path": path, "query_string": query,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers], "client": client,
    }
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    start = sent[0]
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, b"".join(m.get("body", b"") for m in sent[1:])


def write_grant(root: Path, *, lifetime=3600, created_offset=0, revoked=False, bundle=b'{"schema":"x"}'):
    G.prepare_publish_dir(root)
    key, passcode = G.new_link_key(), G.new_passcode()
    grant_id = G.new_grant_id()
    now = G.utc_now() + created_offset
    G.atomic_write(G.bundle_path(root, grant_id), bundle)
    grant = {
        "grant_id": grant_id, "key_sha256": G.key_sha256(key), "passcode": G.hash_passcode(passcode),
        "label": "t", "creator": "operator", "created_at": now, "expires_at": now + lifetime,
        "revoked_at": now if revoked else None, "scope": {}, "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
    }
    document = G.load_index(root)
    document["grants"].append(grant)
    G.save_index(root, {"grants": document["grants"]})
    return grant_id, key, passcode


def creds(key, passcode):
    return (("X-BMS-Share-Key", key), ("X-BMS-Share-Passcode", passcode))


@pytest.fixture
def publish(tmp_path):
    root = tmp_path / "publish"
    root.mkdir(mode=0o750)
    return root


@pytest.fixture
def share(publish, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    static = tmp_path / "static"
    static.mkdir()
    (static / "share.html").write_text("<!doctype html><title>share</title>")
    return ShareApp(ShareSettings(publish_dir=publish, access_log=logs / G.ACCESS_LOG_FILENAME, static_dir=static))


def tree_digest(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and p.name != G.LOCK_FILENAME}


# ---------------------------------------------------------------------------
# grant primitives
# ---------------------------------------------------------------------------

def test_expiry_is_hard_capped_at_24h():
    assert G.MAX_EXPIRY_SECONDS == 24 * 3600
    assert G.clamp_check_expiry(24 * 3600) == 24 * 3600
    for bad in (24 * 3600 + 1, 0, 59, True, 3600.0, "3600"):
        with pytest.raises(ValueError):
            G.clamp_check_expiry(bad)  # type: ignore[arg-type]
    # An index entry claiming a longer lifetime is invalid even if a writer forged it.
    assert G.grant_state({"created_at": 0, "expires_at": 24 * 3600 + 1}, now=1).reason == "invalid"


def test_secret_shapes_and_storage():
    key = G.new_link_key()
    assert G.well_formed_key(key) and len(G.new_grant_id()) == 32
    passcode = G.new_passcode()
    assert len(passcode) == 8 and set(passcode) <= set(G.PASSCODE_ALPHABET)
    assert not set("IL0O1") & set(G.PASSCODE_ALPHABET)
    record = G.hash_passcode(passcode)
    assert passcode not in json.dumps(record)
    assert G.verify_passcode(G.format_passcode(passcode).lower(), record)
    assert not G.verify_passcode("AAAA-AAAA" if passcode != "AAAAAAAA" else "BBBB-BBBB", record)
    other = G.hash_passcode(passcode)
    assert other["salt"] != record["salt"] and other["hash"] != record["hash"]


def test_secret_comparisons_use_hmac_compare_digest(monkeypatch):
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(G.hmac, "compare_digest", spy)
    key = G.new_link_key()
    assert G.verify_key(key, G.key_sha256(key))
    assert G.verify_passcode("ABCD-EFGH", G.hash_passcode("ABCDEFGH"))
    assert len(calls) == 2
    source = (API_ROOT / "share_server" / "app.py").read_text()
    assert "hmac.compare_digest(presented, stored)" in source


# ---------------------------------------------------------------------------
# share server
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_grant_returns_frozen_bundle_with_security_headers(share, publish):
    grant_id, key, passcode = write_grant(publish, bundle=b'{"schema":"bms.molbio.share-bundle.v1"}')
    status, headers, body = await asgi(share, "GET", "/api/share/bundle", headers=(*creds(key, passcode), ("Cookie", "a=b")))
    assert status == 200
    document = json.loads(body)
    assert document["grant"]["grant_id"] == grant_id
    assert document["bundle"] == {"schema": "bms.molbio.share-bundle.v1"}
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "'unsafe-inline'" not in headers["content-security-policy"].split("script-src")[1].split(";")[0]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["strict-transport-security"].startswith("max-age=")
    assert headers["cache-control"] == "no-store"
    assert "set-cookie" not in headers


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["expired", "revoked"])
async def test_expired_and_revoked_grants_are_refused(share, publish, kind):
    if kind == "expired":
        _, key, passcode = write_grant(publish, lifetime=120, created_offset=-121)
    else:
        _, key, passcode = write_grant(publish, revoked=True)
    status, _, body = await asgi(share, "GET", "/api/share/bundle", headers=creds(key, passcode))
    assert status == 410
    assert json.loads(body)["detail"]["code"] == f"share_{kind}"


@pytest.mark.asyncio
async def test_wrong_key_and_wrong_passcode_and_lockout(share, publish):
    _, key, passcode = write_grant(publish)
    wrong_key = G.new_link_key()
    status, _, _ = await asgi(share, "GET", "/api/share/bundle", headers=creds(wrong_key, passcode))
    assert status == 404
    status, _, _ = await asgi(share, "GET", "/api/share/bundle", headers=creds("not-a-key", passcode))
    assert status == 404
    wrong = next(c * 8 for c in G.PASSCODE_ALPHABET if c * 8 != passcode)
    for attempt in range(share_app_module.FAILED_PASSCODE_LIMIT):
        status, _, _ = await asgi(share, "GET", "/api/share/bundle", headers=creds(key, wrong))
        assert status == 401, attempt
    # Locked: even the correct passcode is refused now.
    status, _, body = await asgi(share, "GET", "/api/share/bundle", headers=creds(key, passcode))
    assert status == 423 and json.loads(body)["detail"]["code"] == "share_locked"
    log = (share.settings.access_log).read_text().splitlines()
    outcomes = [json.loads(line)["outcome"] for line in log]
    assert "wrong_passcode_locked" in outcomes and "unknown_key" in outcomes
    assert all(passcode not in line and key not in line for line in log)


@pytest.mark.asyncio
async def test_unauthenticated_post_is_refused_before_body_is_read(share, publish):
    write_grant(publish)
    status, _, _ = await asgi(share, "POST", "/api/molbio/restriction/analyze",
                              headers=(("Content-Type", "application/json"),), body=b"{" * 10)
    assert status == 404


def test_route_table_has_no_write_routes():
    methods = {method for method, _ in ROUTES}
    assert methods <= {"GET", "POST"}
    post_paths = {path for method, path in ROUTES if method == "POST"}
    assert post_paths == PURE_COMPUTE_POST_ROUTES
    assert post_paths == {"/api/molbio/restriction/analyze", "/api/molbio/restriction/digests/simulate"}
    assert all(path.startswith("/api/share/") or path.startswith("/api/molbio/restriction/") for _, path in ROUTES)


@pytest.mark.asyncio
async def test_write_methods_are_rejected_and_publish_dir_is_untouched(share, publish):
    _, key, passcode = write_grant(publish)
    before = tree_digest(publish)
    paths = sorted({path for _, path in ROUTES} | {"/api/molbio/shares", "/api/share/grants", "/"})
    for method in ("PUT", "PATCH", "DELETE", "POST"):
        for path in paths:
            if (method, path) in ROUTES:
                continue
            status, _, _ = await asgi(share, method, path, headers=(*creds(key, passcode), ("Content-Type", "application/json")), body=b"{}")
            assert status in {404, 405}, (method, path, status)
    await asgi(share, "GET", "/api/share/bundle", headers=creds(key, passcode))
    assert tree_digest(publish) == before


@pytest.mark.asyncio
async def test_static_guest_entry_and_traversal(share):
    status, headers, body = await asgi(share, "GET", "/s")
    assert status == 200 and b"<title>share</title>" in body and headers["content-type"].startswith("text/html")
    for path in ("/../etc/passwd", "/s/../../etc/passwd", "/missing.js"):
        status, _, _ = await asgi(share, "GET", path)
        assert status == 404


def test_forwarded_for_trusted_only_from_loopback():
    headers = {b"x-forwarded-for": b"203.0.113.9"}
    assert client_ip({"client": ("127.0.0.1", 1)}, headers) == "203.0.113.9"
    assert client_ip({"client": ("198.51.100.7", 1)}, headers) == "198.51.100.7"
    assert client_ip({"client": ("127.0.0.1", 1)}, {b"x-forwarded-for": b"junk"}) == "127.0.0.1"


@pytest.mark.asyncio
async def test_guest_digest_is_pure_computation(share, publish):
    from share_server import compute

    _, key, passcode = write_grant(publish)
    receipt = compute.catalog_receipt()
    ecori = next(r.enzyme_id for r in compute._view().ordered_records if r.canonical_name == "EcoRI")
    request = {
        "schema": "bms.molbio.restriction-digest-simulation-request.v1",
        "source": {"kind": "inline_dna", "name": "guest", "dna": "AAAAGAATTCAAAA", "topology": "linear"},
        "catalog": {"catalog_id": receipt["catalog_id"], "expected_catalog_sha256": receipt["catalog_sha256"]},
        "enzyme_ids": [ecori],
    }
    before = tree_digest(publish)
    status, _, body = await asgi(share, "POST", "/api/molbio/restriction/digests/simulate",
                                 headers=(*creds(key, passcode), ("Content-Type", "application/json")),
                                 body=json.dumps(request).encode())
    assert status == 200, body
    assert len(json.loads(body)["fragments"]) == 2
    assert tree_digest(publish) == before
    oversized = dict(request, source=dict(request["source"], dna="A" * (compute.SHARE_MAX_SEQUENCE_LENGTH + 1)))
    status, _, _ = await asgi(share, "POST", "/api/molbio/restriction/digests/simulate",
                              headers=(*creds(key, passcode), ("Content-Type", "application/json")),
                              body=json.dumps(oversized).encode())
    assert status == 413


def test_share_server_import_does_not_load_database_or_workflow_modules():
    probe = (
        "import sys\n"
        "import share_server.app, share_server.compute, share_server.__main__\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in {'sqlalchemy','aiosqlite','fastapi','starlette',"
        "'database','molbio_database','experiment_database','molbio_ngs_database','routers','main'}"
        " or m in {'services.nextflow','services.workflow_adapter'}]\n"
        "assert getattr(sys.modules['services'], '__file__', None) is None, 'services/__init__ executed'\n"
        "print(','.join(sorted(bad)))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("BMS_")}
    result = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(API_ROOT)!r})\n" + probe],
                            capture_output=True, text=True, cwd=API_ROOT, env=env, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# BMS router + bundle export
# ---------------------------------------------------------------------------

async def _molbio_fixture(tmp_path):
    from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory
    from molbio_models import (MolecularDocument, MolecularOperation, MolecularOperationInput,
                               MolecularOperationOutput, MolecularRevision)

    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    await init_molbio_db(engine=engine)
    factory = make_molbio_session_factory(engine)

    def snapshot(name, seq, parent=None):
        return {"name": name, "description": "<img src=x onerror=alert(1)>", "sequence": seq, "sequence_type": "dna",
                "is_circular": False, "length": len(seq), "features": [{"name": "<b>f</b>", "start": 0, "end": 2}],
                "primers": [], "parent_id": parent, "source_file": "/home/secret/path/file.gb"}

    async with factory() as session:
        for doc in ("doc-a", "doc-b", "doc-c", "doc-e"):
            session.add(MolecularDocument(id=doc, document_kind="dna", name=doc))
        await session.flush()
        session.add(MolecularOperation(id="op-1", operation_kind="restriction_digest", implementation="t", parameters={}))
        await session.flush()
        session.add_all([
            MolecularRevision(id="rev-a", document_id="doc-a", revision_number=1, change_kind="create",
                              content_sha256="a" * 64, content_length=4, snapshot=snapshot("Ancestor A", "ACGT")),
            MolecularRevision(id="rev-c", document_id="doc-c", revision_number=1, change_kind="create",
                              content_sha256="c" * 64, content_length=4, snapshot=snapshot("Unrelated C", "GGGG")),
        ])
        await session.flush()
        session.add(MolecularRevision(id="rev-b", document_id="doc-b", revision_number=1, change_kind="operation",
                                      content_sha256="b" * 64, content_length=2, operation_id="op-1",
                                      snapshot=snapshot("Digest B", "AC")))
        await session.flush()
        session.add(MolecularRevision(id="rev-e", document_id="doc-e", revision_number=1, change_kind="create",
                                      content_sha256="e" * 64, content_length=2,
                                      snapshot=snapshot("<script>alert('E')</script>", "CC", parent="doc-b")))
        session.add_all([
            MolecularOperationInput(id="in-1", operation_id="op-1", revision_id="rev-a", role="source", position=0),
            MolecularOperationOutput(id="out-1", operation_id="op-1", revision_id="rev-b", role="fragment", position=0),
        ])
        await session.flush()
        for doc, rev in (("doc-a", "rev-a"), ("doc-b", "rev-b"), ("doc-c", "rev-c"), ("doc-e", "rev-e")):
            (await session.get(MolecularDocument, doc)).current_revision_id = rev
        await session.commit()
    return engine, factory


@pytest.mark.asyncio
async def test_bundle_contains_exactly_scope_plus_lineage_and_escapes_html(tmp_path):
    from services.molbio_share_export import build_share_bundle, encode_bundle

    engine, factory = await _molbio_fixture(tmp_path)
    try:
        async with factory() as session:
            bundle, summary = await build_share_bundle(
                {"kind": "items", "sequence_ids": ["doc-e"], "operation_ids": []},
                molbio=session, experiment=None, native=None,  # type: ignore[arg-type]
            )
    finally:
        await engine.dispose()
    roles = {row["revision_id"]: row["role"] for row in bundle["sequences"]}
    assert roles == {"rev-e": "scoped", "rev-b": "ancestor", "rev-a": "ancestor"}  # never rev-c
    assert {op["operation_id"]: op["role"] for op in bundle["operations"]} == {"op-1": "ancestor"}
    assert summary["counts"] == {"scoped_sequences": 1, "ancestor_sequences": 2, "scoped_operations": 0, "ancestor_operations": 1}
    ancestor = next(row for row in bundle["sequences"] if row["revision_id"] == "rev-a")
    assert ancestor["record"]["sequence"] == "ACGT"  # full record, not a stub
    assert all(row["record"]["source_file"] == "file.gb" for row in bundle["sequences"])
    data = encode_bundle(bundle)
    assert b"<" not in data and b">" not in data and b"&" not in data
    assert b"/home/secret" not in data
    assert json.loads(data) == json.loads(json.dumps(bundle))  # lossless escaping
    assert any(row["record"]["name"] == "<script>alert('E')</script>" for row in json.loads(data)["sequences"])


@pytest.mark.asyncio
async def test_router_create_list_revoke_end_to_end(tmp_path, monkeypatch, publish):
    from molbio_database import get_molbio_session
    from experiment_database import get_experiment_session
    from molbio_ngs_database import get_molbio_ngs_session
    from routers import molbio_share

    logs = tmp_path / "logs"
    logs.mkdir()
    token_file = tmp_path / "agent-tokens"
    token_file.write_text("helper:" + "t" * 40 + "\n")
    token_file.chmod(0o600)
    monkeypatch.setenv("BMS_SHARE_PUBLISH_DIR", str(publish))
    monkeypatch.setenv("BMS_SHARE_ACCESS_LOG_DIR", str(logs))
    monkeypatch.setenv("BMS_SHARE_PUBLIC_HOST", "node.example.ts.net")
    monkeypatch.setenv("BMS_SHARE_AGENT_TOKEN_FILE", str(token_file))

    engine, factory = await _molbio_fixture(tmp_path)

    async def molbio_override():
        async with factory() as session:
            yield session

    async def none_override():
        yield None

    app = FastAPI()
    app.include_router(molbio_share.router)
    app.dependency_overrides[get_molbio_session] = molbio_override
    app.dependency_overrides[get_experiment_session] = none_override
    app.dependency_overrides[get_molbio_ngs_session] = none_override
    scope = {"kind": "items", "sequence_ids": ["doc-b"]}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bms") as client:
            assert (await client.get("/api/molbio/shares/config")).json()["max_expiry_seconds"] == 86400
            over = await client.post("/api/molbio/shares", json={"scope": scope, "expires_in_seconds": 86401})
            assert over.status_code == 422
            assert not (publish / G.INDEX_FILENAME).exists()

            created = await client.post("/api/molbio/shares", json={"scope": scope, "expires_in_seconds": 3600, "label": "for <Bob>"})
            assert created.status_code == 200, created.text
            payload = created.json()
            assert payload["link"].startswith("https://node.example.ts.net:8443/s#")
            key = payload["link"].split("#", 1)[1]
            passcode = payload["passcode"]
            index_text = (publish / G.INDEX_FILENAME).read_text()
            assert key not in index_text and passcode.replace("-", "") not in index_text
            assert payload["grant"]["creator"] == "operator"

            agent = await client.post("/api/molbio/shares", json={"scope": scope, "expires_in_seconds": 600},
                                      headers={"Authorization": "Bearer " + "t" * 40})
            assert agent.status_code == 200 and agent.json()["grant"]["creator"] == "agent:helper"
            denied = await client.get("/api/molbio/shares", headers={"Authorization": "Bearer wrong"})
            assert denied.status_code == 401

            guest = ShareApp()  # reads the same env configuration as production
            status, _, body = await asgi(guest, "GET", "/api/share/bundle", headers=creds(key, passcode))
            assert status == 200
            assert {row["revision_id"] for row in json.loads(body)["bundle"]["sequences"]} == {"rev-b", "rev-a"}

            listing = (await client.get("/api/molbio/shares")).json()
            assert listing["active_count"] == 2
            mine = next(item for item in listing["items"] if item["grant_id"] == payload["grant"]["grant_id"])
            assert [entry["outcome"] for entry in mine["access_log"]] == ["bundle_ok"]
            assert "passcode" not in json.dumps(listing["items"]) and "key_sha256" not in json.dumps(listing)

            revoked = await client.post(f"/api/molbio/shares/{payload['grant']['grant_id']}/revoke")
            assert revoked.status_code == 200 and revoked.json()["grant"]["state"] == "revoked"
            assert not G.bundle_path(publish, payload["grant"]["grant_id"]).exists()
            status, _, body = await asgi(guest, "GET", "/api/share/bundle", headers=creds(key, passcode))
            assert status == 410 and json.loads(body)["detail"]["code"] == "share_revoked"

            token_file.chmod(0o644)  # unsafe token file disables agent access
            unsafe = await client.get("/api/molbio/shares", headers={"Authorization": "Bearer " + "t" * 40})
            assert unsafe.status_code == 401
    finally:
        await engine.dispose()


def test_funnel_controller_only_targets_8443():
    from share_server import funnel_controller as F

    on = F.funnel_on_command("tailscale", 18090)
    off = F.funnel_off_command("tailscale")
    assert on == ["tailscale", "funnel", "--bg", "--https=8443", "http://127.0.0.1:18090"]
    assert off == ["tailscale", "funnel", "--https=8443", "off"]
    assert "443" not in " ".join(on + off).replace("8443", "")
    now = G.utc_now()
    assert F.any_active([{"created_at": now, "expires_at": now + 60, "revoked_at": None}], now)
    assert not F.any_active([{"created_at": now - 120, "expires_at": now - 60, "revoked_at": None}], now)
    on_status = {"AllowFunnel": {"h:8443": True}, "Web": {"h:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:18090"}}}}}
    assert F.funnel_state(on_status, 18090) == "on"
    assert F.funnel_state({}, 18090) == "off"
    assert F.funnel_state({"Web": {"h:443": {}}}, 18090) == "off"  # 443 is never ours
    assert F.decide(True, "off") == "on" and F.decide(False, "on") == "off" and F.decide(True, "on") == "noop"
