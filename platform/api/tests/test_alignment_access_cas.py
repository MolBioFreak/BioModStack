from __future__ import annotations

import importlib
import hashlib
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import Request, Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Job


def test_alignment_capability_transport_is_cookie_only() -> None:
    service = importlib.import_module("services.alignment_access")
    token = "opaque-capability"
    bearer = Request({
        "type": "http", "method": "GET", "scheme": "https", "path": "/api/jobs/job-a/alignment-sessions",
        "headers": [(b"authorization", f"Bearer {token}".encode("ascii"))],
    })
    assert service.request_alignment_token(bearer, "job-a") is None

    name = service.cookie_name("job-a", secure=True)
    cookie = Request({
        "type": "http", "method": "GET", "scheme": "https", "path": "/api/jobs/job-a/alignment-sessions",
        "headers": [(b"cookie", f"{name}={token}".encode("ascii"))],
    })
    assert service.request_alignment_token(cookie, "job-a") == token


def test_capability_cookie_uses_exact_host_only_transport_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    service = importlib.import_module("services.alignment_access")
    monkeypatch.setenv("BMS_RUNTIME_MODE", "dev")
    job_id = "31f02bd5-830f-4558-aa78-3873c515de68"
    expected_suffix = hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:16]

    for scheme, forwarded, expected_name, secure in (
        ("http", b"", f"bms-ngs-{expected_suffix}", False),
        ("https", b"https", f"__Host-bms-ngs-{expected_suffix}", True),
    ):
        headers = [(b"host", b"127.0.0.1")]
        if forwarded:
            headers.append((b"x-forwarded-proto", forwarded))
        request = Request({
            "type": "http", "method": "POST", "scheme": scheme, "path": "/", "headers": headers,
            "client": ("127.0.0.1", 40000),
        })
        response = Response()
        service.set_alignment_access_cookie(job_id, "opaque", response, request)
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{expected_name}=opaque;")
        assert "Path=/" in cookie and "Max-Age=1800" in cookie
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert ("Secure" in cookie) is secure
        assert "Domain=" not in cookie

    remote_http = Request({
        "type": "http", "method": "POST", "scheme": "http", "path": "/", "headers": [],
        "client": ("192.0.2.10", 40000),
    })
    with pytest.raises(RuntimeError, match="loopback Development"):
        service.set_alignment_access_cookie(job_id, "opaque", Response(), remote_http)


@pytest.mark.asyncio
async def test_rotation_cas_preserves_concurrent_hierarchy_rewrite(tmp_path: Path) -> None:
    service = importlib.import_module("services.alignment_access")
    rotate = cast(Any, getattr(service, "rotate_alignment_authority_cas", None))
    assert callable(rotate), "rotation must expose a complete-provenance CAS"

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Job.__table__.create)

    job_id = "31f02bd5-830f-4558-aa78-3873c515de68"
    previous = {
        service.PROVENANCE_DIGEST_KEY: "a" * 64,
        service.PROVENANCE_SCHEME_KEY: service.SCHEME,
        "alignment_hierarchy_authority_v1": {"digest": "1" * 64},
    }
    updated = {
        **previous,
        service.PROVENANCE_DIGEST_KEY: "b" * 64,
        "alignment_hierarchy_authority_v1": {"digest": "2" * 64},
    }
    async with sessions() as setup:
        setup.add(Job(
            id=job_id,
            name="rotation-cas",
            model_id="nanopore",
            mode="fastq",
            status="completed",
            queue_status="completed",
            params={"workflow_id": "ont_fastq_qc", "input_mode": "fastq"},
            provenance=previous,
        ))
        await setup.commit()

    first = sessions()
    second = sessions()
    try:
        loaded = await first.get(Job, job_id)
        assert loaded is not None
        concurrent = await second.get(Job, job_id)
        assert concurrent is not None
        concurrent.provenance = {
            **previous,
            "alignment_hierarchy_authority_v1": {"digest": "f" * 64},
        }
        await second.commit()

        changed = await rotate(first, job_id=job_id, previous=previous, updated=updated)

        assert changed is False
        await first.rollback()
        async with sessions() as verify:
            persisted = await verify.get(Job, job_id)
            assert persisted is not None
            assert persisted.provenance["alignment_hierarchy_authority_v1"]["digest"] == "f" * 64
            assert persisted.provenance[service.PROVENANCE_DIGEST_KEY] == "a" * 64
    finally:
        await first.close()
        await second.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_revocation_cas_persists_closed_revoked_authority(tmp_path: Path) -> None:
    service = importlib.import_module("services.alignment_access")
    rotate = cast(Any, service.rotate_alignment_authority_cas)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'revoke.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Job.__table__.create)
    job_id = "revoke-job"
    previous = {
        service.PROVENANCE_DIGEST_KEY: "a" * 64,
        service.PROVENANCE_SCHEME_KEY: service.SCHEME,
        "unrelated": {"retained": True},
    }
    updated = dict(previous)
    updated.pop(service.PROVENANCE_DIGEST_KEY)
    updated["alignment_access_revoked"] = True
    async with sessions() as session:
        session.add(Job(
            id=job_id, name="revoke", model_id="nanopore", mode="fastq",
            status="completed", queue_status="completed", params={}, provenance=previous,
        ))
        await session.commit()
        assert await rotate(session, job_id=job_id, previous=previous, updated=updated) is True
        await session.commit()
    async with sessions() as verify:
        persisted = await verify.get(Job, job_id)
        assert persisted is not None
        assert service.PROVENANCE_DIGEST_KEY not in persisted.provenance
        assert persisted.provenance[service.PROVENANCE_SCHEME_KEY] == service.SCHEME
        assert persisted.provenance["alignment_access_revoked"] is True
        assert persisted.provenance["unrelated"] == {"retained": True}
    await engine.dispose()
