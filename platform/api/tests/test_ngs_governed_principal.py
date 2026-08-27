from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "scheme": "https", "path": "/api/jobs/job-a/ngs-result", "headers": []})


def test_operator_principal_does_not_require_project_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import ngs_alignment_sessions as routes

    monkeypatch.setattr(routes, "_authenticated_principal", lambda _request: ("operator-a", {"operator"}))

    async def owner_lookup(*_args, **_kwargs):
        raise AssertionError("operator should not enter owner lookup")

    monkeypatch.setattr(routes, "_require_mutation_owner", owner_lookup)
    authorize = cast(Any, getattr(routes, "_require_governed_project_principal", None))
    assert callable(authorize), "governed Project principal helper is required"

    actor = asyncio.run(authorize(_request(), object(), "project-a", "job-a"))

    assert actor == "operator-a"


def test_project_owner_principal_uses_existing_owner_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import ngs_alignment_sessions as routes

    monkeypatch.setattr(routes, "_authenticated_principal", lambda _request: ("scientist-a", {"scientist"}))
    calls: list[str] = []

    async def owner_lookup(_request, _session, *, resource_id: str):
        calls.append(resource_id)
        return "scientist-a"

    monkeypatch.setattr(routes, "_require_mutation_owner", owner_lookup)
    authorize = cast(Any, getattr(routes, "_require_governed_project_principal", None))
    assert callable(authorize), "governed Project principal helper is required"

    actor = asyncio.run(authorize(_request(), object(), "project-a", "job-a"))

    assert actor == "scientist-a"
    assert calls == ["project-a"]


def test_unauthenticated_principal_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import ngs_alignment_sessions as routes

    def reject(_request):
        raise HTTPException(status_code=401, detail="trusted application principal required")

    monkeypatch.setattr(routes, "_authenticated_principal", reject)
    authorize = cast(Any, getattr(routes, "_require_governed_project_principal", None))
    assert callable(authorize), "governed Project principal helper is required"

    with pytest.raises(routes.OntNgsRouteError) as error:
        asyncio.run(authorize(_request(), object(), "project-a", "job-a"))
    assert error.value.status_code == 403
    assert error.value.code == "NGS_PRINCIPAL_DENIED"


def test_governed_hierarchy_gate_is_scoped_to_canonical_fastq_qc() -> None:
    from routers import ngs_alignment_sessions as routes

    gate = cast(Any, getattr(routes, "_requires_governed_ont_hierarchy", None))
    assert callable(gate)
    canonical = type("Job", (), {
        "model_id": "nanopore",
        "params": {"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
    })()
    other = type("Job", (), {
        "model_id": "nanopore",
        "params": {"ont_workflow_id": "ont_raw_signal", "ont_input_mode": "pod5"},
    })()
    assert gate(canonical) is True
    assert gate(other) is False
