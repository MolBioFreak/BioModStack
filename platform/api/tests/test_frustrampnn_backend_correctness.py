from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import ValidationError

from routers import frustrampnn as frustrampnn_router
from routers import jobs as jobs_router
from services.frustrampnn import jobs as frustrampnn_jobs
from services.frustrampnn.settings import (
    FrustraMPNNRequestedSettings,
    default_settings,
    validate_complete_requested_settings,
)


def _fanout_child(structure_count: int):
    return frustrampnn_router.FrustraMPNNFanoutChildResponse.model_construct(
        structure_count=structure_count
    )


@pytest.mark.parametrize(
    "counts",
    ([1, 2], [1, 1, 1], [2, 1, 1]),
)
def test_fanout_response_rejects_noncanonical_grouping(counts: list[int]) -> None:
    with pytest.raises(ValidationError, match="canonical partition"):
        frustrampnn_router.FrustraMPNNStructureDatasetFanoutResponse.model_validate(
            {
                "schema_name": "bms.structure-dataset-fanout.v1",
                "fanout_id": "f" * 64,
                "parent_job_id": "parent-1",
                "selected_structure_count": sum(counts),
                "structures_per_job": 2,
                "effective_structures_per_job": 2,
                "replayed": False,
                "child_jobs": [_fanout_child(count) for count in counts],
            }
        )


@pytest.mark.parametrize(
    ("model_id", "mode", "expected_workflow"),
    [
        ("boltz2", "structure_prediction", "structure_prediction"),
        ("boltz2", "complex_prediction", "complex_prediction"),
        ("proteinmpnn", "protein_design", "protein_design"),
        ("antibody_denovo", "antibody_denovo_pipeline", "antibody_denovo"),
        ("conformational_mapping", "map", "conformational_mapping"),
    ],
)
def test_all_frustrampnn_consumers_resolve_to_one_generic_api_fanout_topology(
    model_id: str,
    mode: str,
    expected_workflow: str,
) -> None:
    parent = SimpleNamespace(model_id=model_id, mode=mode)
    assert frustrampnn_router._frustrampnn_consumer_workflow(parent) == expected_workflow


def _complete_settings_payload() -> dict[str, object]:
    return {
        "schema_name": "frustrampnn_settings",
        "schema_version": 2,
        "batching_enabled": False,
        "structures_per_job": 1,
        "protein_selection": {
            "mode": "all_protein_entities",
            "entities": [],
            "regions": [],
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


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _IterationSession:
    async def execute(self, _statement):
        return _ScalarResult([SimpleNamespace(id="design-1")])

    async def rollback(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("include_settings", [False, True])
async def test_antibody_iteration_frustrampnn_passes_validated_requested_settings(
    monkeypatch: pytest.MonkeyPatch,
    include_settings: bool,
) -> None:
    source = SimpleNamespace(id="source-job")
    root = SimpleNamespace(id="root-job")
    captured: dict[str, object] = {}

    async def fake_root(_session, _source_job_id):
        return source, root

    async def fake_selections(*_args, **_kwargs):
        return [SimpleNamespace(candidate_id="candidate-1")]

    async def fake_fanout(*_args, requested_settings, **_kwargs):
        captured["settings"] = requested_settings
        return SimpleNamespace(
            fanout_id="f" * 64,
            child_jobs=(
                SimpleNamespace(id="child-1", output_dir="/tmp/child-1"),
                SimpleNamespace(id="child-2", output_dir="/tmp/child-2"),
            ),
        )

    async def fake_get_job(job_id, _session):
        return {"id": job_id}

    monkeypatch.setattr(jobs_router, "_resolve_antibody_root_job", fake_root)
    monkeypatch.setattr(jobs_router, "_resolve_saved_review_filter_set", lambda *_args: None)
    monkeypatch.setattr(jobs_router, "_resolve_launch_design_ids", lambda *_args: ["design-1"])
    monkeypatch.setattr(jobs_router, "get_job", fake_get_job)
    monkeypatch.setattr(jobs_router, "AntibodyIterationLaunchResponse", lambda **payload: payload)
    monkeypatch.setattr(frustrampnn_jobs, "design_selections", fake_selections)
    monkeypatch.setattr(frustrampnn_router, "_fanout_design_selections", fake_fanout)

    payload: dict[str, object] = {
        "source_job_id": "source-job",
        "design_ids": ["design-1"],
        "action": "frustrampnn",
    }
    if include_settings:
        payload["frustrampnn_settings"] = _complete_settings_payload()
    request = jobs_router.AntibodyIterationLaunchRequest.model_validate(payload)

    response = await jobs_router.launch_antibody_iteration_from_designs(
        request,
        BackgroundTasks(),
        _IterationSession(),
    )

    assert isinstance(captured["settings"], FrustraMPNNRequestedSettings)
    expected = (
        validate_complete_requested_settings(_complete_settings_payload())
        if include_settings
        else default_settings()
    )
    assert captured["settings"] == expected
    assert response["fanout_id"] == "f" * 64
    assert response["launched_jobs"] == [{"id": "child-1"}, {"id": "child-2"}]


def test_antibody_iteration_frustrampnn_rejects_partial_settings_before_launch() -> None:
    with pytest.raises(ValidationError):
        jobs_router.AntibodyIterationLaunchRequest.model_validate(
            {
                "source_job_id": "source-job",
                "design_ids": ["design-1"],
                "action": "frustrampnn",
                "frustrampnn_settings": {
                    "schema_name": "frustrampnn_settings",
                    "schema_version": 1,
                },
            }
        )


class _SizedUpload:
    def __init__(self, size: int) -> None:
        self.remaining = size
        self.requested_sizes: list[int] = []

    async def read(self, size: int) -> bytes:
        self.requested_sizes.append(size)
        returned = min(size, self.remaining)
        self.remaining -= returned
        return b"x" * returned


@pytest.mark.asyncio
async def test_bounded_multipart_reader_accepts_limit_and_rejects_at_limit_plus_one() -> None:
    exact = _SizedUpload(8)
    assert await frustrampnn_router._read_bounded_upload(
        exact,
        max_bytes=8,
        chunk_size=3,
    ) == b"x" * 8
    assert exact.requested_sizes == [3, 3, 3, 1]

    oversized = _SizedUpload(100)
    with pytest.raises(HTTPException) as rejected:
        await frustrampnn_router._read_bounded_upload(
            oversized,
            max_bytes=8,
            chunk_size=3,
        )
    assert rejected.value.status_code == 413
    assert sum(oversized.requested_sizes) == 9
    assert max(oversized.requested_sizes) <= 3
    assert oversized.remaining == 91


def _multipart_properties(app: FastAPI, path: str) -> tuple[dict[str, Any], set[str]]:
    document = app.openapi()
    operation = document["paths"][path]["post"]
    schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    if "$ref" in schema:
        schema = document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema["properties"], set(schema.get("required", []))


def _non_null_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return next(
        (item for item in schema.get("anyOf", []) if item.get("type") != "null"),
        schema,
    )


def test_governed_multipart_openapi_declares_every_accepted_form_field() -> None:
    app = FastAPI()
    app.include_router(frustrampnn_router.router)

    upload, upload_required = _multipart_properties(
        app, "/api/frustrampnn/jobs/uploads/analyze"
    )
    assert set(upload) == {"pdb_file", "frustrampnn_settings"}
    assert upload_required == {"pdb_file"}
    assert upload["pdb_file"]["contentMediaType"] == "application/octet-stream"
    assert "64 MiB" in upload["pdb_file"]["description"]
    assert _non_null_schema(upload["frustrampnn_settings"])["maxLength"] == 65536
    assert "complete" in upload["frustrampnn_settings"]["description"].lower()

    handoff, handoff_required = _multipart_properties(
        app, "/api/frustrampnn/candidates/handoff"
    )
    assert set(handoff) == {
        "structure_file",
        "candidate_id",
        "producer_id",
        "parent_job_id",
        "parent_invocation_id",
        "parent_landscape_sha256",
        "guidance_id",
        "nucleotide_edit_set",
        "protein_sequence_sha256",
        "expected_structure_sha256",
        "frustrampnn_settings",
    }
    assert handoff_required == {
        "structure_file",
        "candidate_id",
        "producer_id",
        "parent_job_id",
        "parent_invocation_id",
        "parent_landscape_sha256",
    }
    assert handoff["structure_file"]["contentMediaType"] == "application/octet-stream"
    assert handoff["parent_landscape_sha256"]["pattern"] == "^[0-9a-f]{64}$"
    assert _non_null_schema(handoff["protein_sequence_sha256"])["pattern"] == "^[0-9a-f]{64}$"
    assert _non_null_schema(handoff["expected_structure_sha256"])["pattern"] == "^[0-9a-f]{64}$"
    assert _non_null_schema(handoff["nucleotide_edit_set"])["maxLength"] == 65536
    assert _non_null_schema(handoff["frustrampnn_settings"])["maxLength"] == 65536
    assert all(field.get("description") for field in handoff.values())
