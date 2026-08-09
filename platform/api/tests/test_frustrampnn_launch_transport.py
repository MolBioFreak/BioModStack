from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from database import get_session
from routers import frustrampnn as router_module
from services.frustrampnn.jobs import upload_selection
from services.frustrampnn.settings import FrustraMPNNRequestedSettings


def _pdb() -> bytes:
    lines: list[str] = []
    for serial, (atom, element) in enumerate((("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")), 1):
        atom_field = f" {atom:<3}"
        lines.append(
            f"ATOM  {serial:5d} {atom_field} GLY A   1    {serial:8.3f}{serial + 1:8.3f}"
            f"{serial + 2:8.3f}{1.0:6.2f}{20.0:6.2f}          {element:>2}  \n"
        )
    return "".join(lines).encode("ascii") + b"END\n"


def _settings_payload() -> dict[str, object]:
    return {
        "schema_name": "frustrampnn_settings",
        "schema_version": 1,
        "protein_selection": {
            "mode": "all_protein_entities",
            "entities": [],
            "residues": [],
        },
        "source_structure": {
            "selected_model_number": 1,
            "preferred_altloc": "",
        },
        "classification_policy": {
            "mode": "custom",
            "high_max": -0.7,
            "minimal_min": 0.2,
        },
    }


class _FakeSession:
    def __init__(self) -> None:
        self.parent = SimpleNamespace(
            id="parent-1",
            model_id="boltz2",
            mode="predict",
            params={},
            queue_status="completed",
        )

    async def get(self, _model, _identity):
        return self.parent

    async def rollback(self) -> None:
        return None


@pytest.mark.asyncio
async def test_every_direct_launch_body_and_multipart_form_transports_the_same_typed_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, FrustraMPNNRequestedSettings]] = []
    bounded_reads: list[str] = []
    selection = upload_selection(filename="candidate.pdb", payload=_pdb(), expected_sha256=None)

    async def fake_design_selections(*_args, **_kwargs):
        return [selection]

    async def fake_create_child_job(*_args, requested_settings, trigger, **_kwargs):
        assert isinstance(requested_settings, FrustraMPNNRequestedSettings)
        captured.append((trigger, requested_settings))
        return SimpleNamespace(id=f"child-{trigger}")

    async def fake_create_reanalysis_child(*_args, replacement_settings, **_kwargs):
        assert isinstance(replacement_settings, FrustraMPNNRequestedSettings)
        captured.append(("reanalyze", replacement_settings))
        return SimpleNamespace(id="child-reanalyze")

    async def fake_receipt(_session, child):
        requested = FrustraMPNNRequestedSettings.model_validate(
            {**settings, "settings_value_origin": "operator_request"}
        )
        return {
            "job_id": child.id,
            "child_job_id": child.id,
            "result_job_id": child.id,
            "name": child.id,
            "parent_job_id": None,
            "source_parent_job_id": None,
            "trigger": "test",
            "status": "queued",
            "created_at": None,
            "started_at": None,
            "completed_at": None,
            "settings_value_origin": "operator_request",
            "requested_settings": requested.model_dump(mode="json"),
            "requested_settings_sha256": "0" * 64,
            "candidates": [],
            "results": [],
        }

    async def fake_scoped_result(*_args, **_kwargs):
        return SimpleNamespace(candidate_id="parent-candidate")

    async def fake_persisted_landscape(*_args, **_kwargs):
        return {"landscape_sha256": "a" * 64}

    async def fake_bounded_read(upload, **_kwargs):
        bounded_reads.append(upload.filename)
        return await upload.read()

    monkeypatch.setattr(router_module, "design_selections", fake_design_selections)
    monkeypatch.setattr(router_module, "create_child_job", fake_create_child_job)
    monkeypatch.setattr(router_module, "create_reanalysis_child", fake_create_reanalysis_child)
    monkeypatch.setattr(router_module, "child_receipt", fake_receipt)
    monkeypatch.setattr(router_module, "_scoped_result", fake_scoped_result)
    monkeypatch.setattr(router_module, "load_persisted_landscape", fake_persisted_landscape)
    monkeypatch.setattr(router_module, "_read_bounded_upload", fake_bounded_read)

    app = FastAPI()
    app.include_router(router_module.router)
    fake_session = _FakeSession()

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    settings = _settings_payload()
    settings_json = json.dumps(settings)
    pdb_sha256 = hashlib.sha256(_pdb()).hexdigest()

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        analyze = await client.post(
            "/api/frustrampnn/jobs/parent-1/analyze",
            json={
                "selections": [{"design_id": "design-1", "source_sha256": pdb_sha256}],
                "frustrampnn_settings": settings,
            },
        )
        upload = await client.post(
            "/api/frustrampnn/jobs/uploads/analyze",
            files={
                "pdb_file": ("candidate.pdb", _pdb(), "chemical/x-pdb"),
                "frustrampnn_settings": (None, settings_json),
            },
        )
        handoff = await client.post(
            "/api/frustrampnn/candidates/handoff",
            files={
                "structure_file": ("candidate.pdb", _pdb(), "chemical/x-pdb"),
                "candidate_id": (None, "candidate-1"),
                "producer_id": (None, "producer-1"),
                "parent_job_id": (None, "parent-1"),
                "parent_invocation_id": (None, "invoke-1"),
                "parent_landscape_sha256": (None, "a" * 64),
                "nucleotide_edit_set": (None, "[]"),
                "frustrampnn_settings": (None, settings_json),
            },
        )
        reanalyze = await client.post(
            "/api/frustrampnn/jobs/prior-1/reanalyze",
            json={"frustrampnn_settings": settings},
        )

    for response in (analyze, upload, handoff, reanalyze):
        assert response.status_code == 202, response.text
    assert [trigger for trigger, _settings in captured] == [
        "design_analyze",
        "upload_analyze",
        "external_candidate_handoff",
        "reanalyze",
    ]
    normalized = FrustraMPNNRequestedSettings.model_validate(
        {**settings, "settings_value_origin": "operator_request"}
    ).model_dump(mode="json")
    assert [item.model_dump(mode="json") for _trigger, item in captured] == [normalized] * 4
    assert bounded_reads == ["candidate.pdb", "candidate.pdb"]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_name": "frustrampnn_settings",
            "schema_version": 1,
        },
        {
            **_settings_payload(),
            "gpu_id": 0,
        },
        {
            **_settings_payload(),
            "runtime": {"container": "/caller/image.sif"},
        },
    ],
)
def test_direct_body_rejects_partial_unknown_runtime_or_storage_settings(payload) -> None:
    with pytest.raises(ValidationError):
        router_module.AnalyzeDesignsRequest.model_validate(
            {
                "selections": [{"design_id": "d1", "source_sha256": "a" * 64}],
                "frustrampnn_settings": payload,
            }
        )


def test_reanalysis_replacement_rejects_partial_or_unknown_settings() -> None:
    with pytest.raises(ValidationError):
        router_module.ReanalyzeRequest.model_validate(
            {
                "frustrampnn_settings": {
                    "schema_name": "frustrampnn_settings",
                    "schema_version": 1,
                    "classification_policy": {"mode": "canonical"},
                }
            }
        )
    with pytest.raises(ValidationError):
        router_module.ReanalyzeRequest.model_validate(
            {"frustrampnn_settings": {**_settings_payload(), "command": ["--unsafe"]}}
        )


@pytest.mark.asyncio
async def test_upload_partial_settings_reject_before_child_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def forbidden_create(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("child creation must not run")

    monkeypatch.setattr(router_module, "create_child_job", forbidden_create)
    app = FastAPI()
    app.include_router(router_module.router)
    fake_session = _FakeSession()

    async def override_session():
        yield fake_session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/frustrampnn/jobs/uploads/analyze",
            files={
                "pdb_file": ("candidate.pdb", _pdb(), "chemical/x-pdb"),
                "frustrampnn_settings": (
                    None,
                    json.dumps({"schema_name": "frustrampnn_settings", "schema_version": 1}),
                ),
            },
        )

    assert response.status_code == 422
    assert called is False
