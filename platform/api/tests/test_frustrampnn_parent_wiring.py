from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / "workflows" / "structure_prediction.nf"
CANONICAL_MODULE = REPO_ROOT / "modules" / "frustrampnn.nf"
PREPARE_SCRIPT = REPO_ROOT / "scripts" / "prepare_frustrampnn_candidate.py"


def _pdb() -> bytes:
    return (
        b"ATOM      1  N   GLY A   1       1.000   2.000   3.000  1.00 20.00           N  \n"
        b"ATOM      2  CA  GLY A   1       2.000   3.000   4.000  1.00 20.00           C  \n"
        b"ATOM      3  C   GLY A   1       3.000   4.000   5.000  1.00 20.00           C  \n"
        b"ATOM      4  O   GLY A   1       4.000   5.000   6.000  1.00 20.00           O  \n"
        b"END\n"
    )


def test_parent_candidate_identity_is_deterministic_and_domain_separated() -> None:
    from services.frustrampnn.identity import deterministic_candidate_id

    values = {
        "parent_job_id": "job-structure-1",
        "parent_workflow_id": "structure_prediction",
        "producer_stage": "structure_prediction:boltz",
        "producer_candidate_key": "frustrampnn/sources/boltz/rank_0.pdb",
    }
    first = deterministic_candidate_id(**values)
    assert first == deterministic_candidate_id(**values)
    assert len(first) == 36
    assert first != deterministic_candidate_id(**{**values, "parent_job_id": "job-structure-2"})
    assert first != deterministic_candidate_id(**{**values, "producer_stage": "structure_prediction:rf3"})
    assert first != deterministic_candidate_id(
        **{**values, "producer_candidate_key": "frustrampnn/sources/boltz/rank_1.pdb"}
    )


def test_structure_prediction_has_one_canonical_v2_manifest_cutover() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    prediction_module = (REPO_ROOT / "modules" / "structure_prediction.nf").read_text(encoding="utf-8")

    assert "include { CanonicalFrustraMPNNV2 } from '../modules/frustrampnn.nf'" in workflow
    assert "CanonicalFrustraMPNNV2(" in workflow
    assert "FrustrampnnQC" not in workflow
    assert "placeholder.pdb" not in workflow
    assert "structure_prediction_wf.out.canonical_structures" in workflow
    assert "producer_artifact_key" in workflow

    assert "PrepareStructurePredictionFrustraMPNNCandidate" in workflow
    assert "frustrampnn_requiredness ?: 'required'" in workflow
    assert "frustrampnn_requiredness must be required" in workflow
    assert "CanonicalFrustraMPNNV2.out.result" in workflow
    assert "workflow_component_request_v3.json" in workflow
    assert "frustrampnn_structure_map_v1.json" in workflow
    assert "workflow_component_request_v1.json" not in workflow
    assert "checkpoint_id" not in workflow
    assert ".subscribe" not in workflow
    assert "frustrampnn" in workflow
    assert "frustrampnn not_requested" in workflow
    assert "structure_prediction_frustrampnn_terminal_manifest" in workflow
    assert "status: 'not_requested'" in workflow
    assert "requiredness: 'not_requested'" in workflow
    assert "candidate_count: 0" in workflow
    assert "errorStrategy 'terminate'" in CANONICAL_MODULE.read_text(encoding="utf-8")
    assert "predicted.getName()" not in prediction_module
    assert "MessageDigest.getInstance('SHA-256')" in prediction_module
    assert "canonicalProducerOutputs(BoltzFromSequence.out.cifs, 'boltz')" not in prediction_module
    assert "canonicalProducerOutputs(RF3FromSequence.out.cifs, 'rf3')" not in prediction_module


@pytest.mark.asyncio
async def test_not_requested_stage_continues_ordinary_parent_result_ingestion(
    tmp_path: Path,
) -> None:
    from services.result_ingester import _ingest_explicit_frustrampnn_results

    job = SimpleNamespace(
        id="job-frustrampnn-disabled",
        stage_outputs={"structure_prediction": ["final/design.pdb"], "frustrampnn": []},
        provenance={
            "stage_terminal_states": {
                "structure_prediction": {
                    "status": "complete",
                    "outputs": ["final/design.pdb"],
                },
                "frustrampnn": {"status": "not_requested", "outputs": []}
            }
        },
    )

    created = await _ingest_explicit_frustrampnn_results(
        cast(Any, job),
        tmp_path,
        cast(Any, SimpleNamespace()),
        commit=True,
    )

    assert created is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage_outputs", "terminal_states"),
    [
        (None, {"frustrampnn": {"status": "not_requested", "outputs": []}}),
        ([], {"frustrampnn": {"status": "not_requested", "outputs": []}}),
        ({}, {"frustrampnn": {"status": "not_requested", "outputs": []}}),
        (
            {"structure_prediction": ["final/design.pdb"]},
            {"frustrampnn": {"status": "not_requested", "outputs": []}},
        ),
        (
            {},
            {"FrustraMPNN": {"status": "not_requested", "outputs": []}},
        ),
        ({"frustrampnn": []}, {"frustrampnn": {"status": "not_requested"}}),
        (
            {"frustrampnn": []},
            {"frustrampnn": {"status": "not_requested", "outputs": None}},
        ),
        (
            {"frustrampnn": None},
            {"frustrampnn": {"status": "not_requested", "outputs": []}},
        ),
        (
            {"frustrampnn": {}},
            {"frustrampnn": {"status": "not_requested", "outputs": []}},
        ),
        (
            {"canonical_frustrampnn": []},
            {"frustrampnn": {"status": "not_requested", "outputs": []}},
        ),
        (
            {"frustrampnn": [], "FrustraMPNN": []},
            {"frustrampnn": {"status": "not_requested", "outputs": []}},
        ),
        (
            {"frustrampnn": [], " frustrampnn": []},
            {"frustrampnn": {"status": "not_requested", "outputs": []}},
        ),
        (
            {"frustrampnn": []},
            {
                "frustrampnn": {"status": "not_requested", "outputs": []},
                "FrustraMPNN": {"status": "not_requested", "outputs": []},
            },
        ),
        (
            {"frustrampnn": []},
            {
                "frustrampnn": {"status": "not_requested", "outputs": []},
                "canonical_frustrampnn": {
                    "status": "not_requested",
                    "outputs": [],
                },
            },
        ),
    ],
)
async def test_not_requested_stage_rejects_non_exact_persisted_state(
    tmp_path: Path,
    stage_outputs: object,
    terminal_states: object,
) -> None:
    from services.frustrampnn.persistence import FrustraMPNNPersistenceError
    from services.result_ingester import _ingest_explicit_frustrampnn_results

    job = SimpleNamespace(
        id="job-frustrampnn-disabled-malformed",
        stage_outputs=stage_outputs,
        provenance={"stage_terminal_states": terminal_states},
    )

    with pytest.raises(FrustraMPNNPersistenceError, match="not-requested"):
        await _ingest_explicit_frustrampnn_results(
            cast(Any, job),
            tmp_path,
            cast(Any, SimpleNamespace()),
            commit=True,
        )


def test_canonical_scheduler_publishes_closed_candidate_bundles_without_legacy_gpu_flags() -> None:
    module = CANONICAL_MODULE.read_text(encoding="utf-8")

    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "publish_frustrampnn_bundle.py" in workflow
    assert "${params.out_dir}/frustrampnn/results/${candidateId}" in workflow
    assert "assigned_gpu = params.frustrampnn_physical_gpu_id" in module
    assert "CUDA_VISIBLE_DEVICES='${assigned_gpu}'" in module
    assert "--physical-gpu-id '${assigned_gpu}'" in module
    assert "--gpu_id" not in module
    assert "--device cuda" not in module  # the neutral adapter owns the contained command
    assert "errorStrategy 'terminate'" in module
    assert "ignore" not in module


def test_managed_launcher_propagates_scheduler_gpu_to_canonical_component() -> None:
    from services.nextflow import build_nextflow_command

    command = build_nextflow_command(
        "esmfold2",
        "predict",
        {
            "pred_method": "esmfold2",
            "gpu_id": 3,
            "run_frustrampnn": True,
            "sequence_input": "ACDE",
            "sequence_name": "probe",
        },
        "/tmp/frustrampnn-command-probe",
        job_id="phase5a-probe",
    )
    assert command[command.index("--frustrampnn_physical_gpu_id") + 1] == "3"

    overridden = build_nextflow_command(
        "esmfold2",
        "predict",
        {
            "pred_method": "esmfold2",
            "gpu_id": 3,
            "frustrampnn_physical_gpu_id": 7,
            "run_frustrampnn": True,
            "sequence_input": "ACDE",
            "sequence_name": "probe",
        },
        "/tmp/frustrampnn-command-probe-override",
        job_id="phase5a-probe-override",
    )
    assert overridden[overridden.index("--frustrampnn_physical_gpu_id") + 1] == "3"
    assert "7" not in overridden

    with pytest.raises(ValueError, match="scheduler-assigned physical GPU"):
        build_nextflow_command(
            "esmfold2",
            "predict",
            {
                "pred_method": "esmfold2",
                "frustrampnn_physical_gpu_id": 7,
                "run_frustrampnn": True,
                "sequence_input": "ACDE",
                "sequence_name": "probe",
            },
            "/tmp/frustrampnn-command-probe-no-scheduler-gpu",
            job_id="phase5a-probe-no-scheduler-gpu",
        )


def _load_stage_reporter():
    reporter_path = REPO_ROOT / "scripts" / "stage_reporter.py"
    spec = importlib.util.spec_from_file_location("stage_reporter_job_root_relative", reporter_path)
    assert spec is not None and spec.loader is not None
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)
    return reporter


def test_stage_reporter_preserves_explicit_job_root_relative_outputs() -> None:
    reporter = _load_stage_reporter()
    value = "frustrampnn/results/candidate-1/workflow_component_result_v1.json"
    assert reporter.normalize_job_root_relative_output(value) == value


def test_stage_reporter_main_sends_job_root_relative_output_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = _load_stage_reporter()
    reporter_path = REPO_ROOT / "scripts" / "stage_reporter.py"
    value = "frustrampnn/results/candidate-1/workflow_component_result_v1.json"
    calls: list[dict[str, object]] = []

    class Response:
        status_code = 200
        text = "ok"

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response()

    setattr(reporter, "API_BASE_URL", "http://localhost:8000")
    setattr(reporter, "STAGE_REPORT_TOKEN", "launch-scoped-test-token")
    monkeypatch.setattr(reporter.requests, "post", fake_post)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(reporter_path),
            "--job-root-relative",
            "job-1",
            "frustrampnn",
            "complete",
            value,
        ],
    )
    reporter.main()
    assert calls[0]["url"] == "http://localhost:8000/api/jobs/job-1/stage-complete"
    assert calls[0]["json"] == [value]


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/absolute/result.json",
        "frustrampnn/./result.json",
        "frustrampnn/../result.json",
        "frustrampnn//result.json",
        "frustrampnn\\result.json",
    ],
)
def test_stage_reporter_rejects_unsafe_job_root_relative_outputs(value: str) -> None:
    reporter = _load_stage_reporter()
    with pytest.raises(ValueError, match="unsafe job-root-relative output"):
        reporter.normalize_job_root_relative_output(value)


def test_enabled_frustrampnn_reporters_request_job_root_relative_outputs() -> None:
    paths = [
        REPO_ROOT / "workflows" / "structure_prediction.nf",
        REPO_ROOT / "workflows" / "frustrampnn_analysis.nf",
        REPO_ROOT / "workflows" / "protein_design.nf",
        REPO_ROOT / "workflows" / "complex_prediction.nf",
        REPO_ROOT / "modules" / "antibody_frustrampnn_parent.nf",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "stage_reporter.py' --job-root-relative" in source, path


def test_every_active_workflow_consumer_is_v3_only_for_new_writes() -> None:
    consumers = {
        "structure_prediction": REPO_ROOT / "workflows" / "structure_prediction.nf",
        "protein_design": REPO_ROOT / "workflows" / "protein_design.nf",
        "complex_prediction": REPO_ROOT / "workflows" / "complex_prediction.nf",
        "antibody_denovo": REPO_ROOT / "workflows" / "antibody_denovo.nf",
        "frustrampnn_analysis": REPO_ROOT / "workflows" / "frustrampnn_analysis.nf",
    }
    for consumer, path in consumers.items():
        source = path.read_text(encoding="utf-8")
        assert "CanonicalFrustraMPNNV2" in source, consumer
        assert "CanonicalFrustraMPNN(" not in source, consumer
        assert "workflow_component_request_v1.json" not in source, consumer

    antibody_parent = (
        REPO_ROOT / "modules" / "antibody_frustrampnn_parent.nf"
    ).read_text(encoding="utf-8")
    assert "workflow_component_request_v3.json" in antibody_parent
    assert "workflow_component_request_v1.json" not in antibody_parent


@pytest.mark.parametrize("status", ["failed", "not_requested"])
def test_stage_reporter_routes_non_success_terminal_states(
    status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporter_path = REPO_ROOT / "scripts" / "stage_reporter.py"
    spec = importlib.util.spec_from_file_location(f"stage_reporter_{status}", reporter_path)
    assert spec is not None and spec.loader is not None
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)
    calls: list[tuple[str, dict[str, str]]] = []

    class Response:
        status_code = 200
        text = "ok"

    def fake_post(url, *, params, **_kwargs):
        calls.append((url, params))
        return Response()

    setattr(reporter, "API_BASE_URL", "http://localhost:8000")
    setattr(reporter, "STAGE_REPORT_TOKEN", "launch-scoped-test-token")
    monkeypatch.setattr(reporter.requests, "post", fake_post)
    monkeypatch.setattr(sys, "argv", [str(reporter_path), "job-1", "frustrampnn", status])
    reporter.main()
    assert calls == [
        (
            "http://localhost:8000/api/jobs/job-1/stage-terminal",
            {"stage": "frustrampnn", "status": status},
        )
    ]


@pytest.mark.asyncio
async def test_stage_terminal_endpoint_persists_immutable_non_success_state() -> None:
    from database import Job
    from routers import jobs
    from services import stage_reporting
    from starlette.requests import Request

    token, digest = stage_reporting.issue_stage_report_token()
    job = Job(
        id="job-stage-terminal",
        status="running",
        queue_status="running",
        awaiting_input=False,
        provenance={stage_reporting.PROVENANCE_DIGEST_KEY: digest},
        completed_stages=[],
        stage_outputs={},
        current_stage="frustrampnn",
        stage_progress={"stage": "frustrampnn"},
    )

    class Result:
        rowcount = 1

        def scalar_one_or_none(self):
            return job

    class Session:
        committed = False

        async def execute(self, statement):
            if getattr(statement, "is_update", False):
                values = statement.compile().params
                job.completed_stages = values["completed_stages"]
                job.stage_outputs = values["stage_outputs"]
                job.provenance = values["provenance"]
                job.current_stage = values["current_stage"]
            return Result()

        def expunge(self, _job):
            return None

        async def rollback(self):
            return None

        async def commit(self):
            self.committed = True

    session = Session()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/jobs/job-stage-terminal/stage-terminal",
            "headers": [(b"authorization", f"Bearer {token}".encode("ascii"))],
        }
    )
    response = await jobs.report_stage_terminal(
        job.id, request, "frustrampnn", "not_requested", [], session
    )
    assert response["status"] == "not_requested"
    assert job.provenance["stage_terminal_states"]["frustrampnn"] == {
        "status": "not_requested",
        "outputs": [],
    }
    assert job.stage_outputs["frustrampnn"] == []
    assert job.current_stage is None
    assert session.committed is True


def test_terminal_stage_guard_rejects_intermediate_symlink_escape(tmp_path: Path) -> None:
    from services.frustrampnn.persistence import FrustraMPNNPersistenceError
    from services.result_ingester import _read_explicit_terminal_envelope, _stage_path

    job_root = tmp_path / "job"
    outside_bundle = tmp_path / "outside" / "bundle"
    outside_bundle.mkdir(parents=True)
    (outside_bundle / "workflow_component_result_v1.json").write_text("{}", encoding="utf-8")
    (outside_bundle / "frustrampnn_result_manifest_v1.json").write_text("{}", encoding="utf-8")
    job_root.mkdir()
    (job_root / "link").symlink_to(outside_bundle.parent, target_is_directory=True)
    claimed = job_root / "link" / "bundle" / "frustrampnn_result_manifest_v1.json"

    with pytest.raises(FrustraMPNNPersistenceError):
        guarded = _stage_path(os.fspath(claimed), job_root)
        _read_explicit_terminal_envelope(guarded.parent)


def test_candidate_preparation_materializes_one_exact_pdb_and_bound_request(tmp_path: Path) -> None:
    source = tmp_path / "prediction.pdb"
    source.write_bytes(_pdb())
    from services.frustrampnn.identity import deterministic_candidate_id

    output_pdb = tmp_path / "source.pdb"
    request_path = tmp_path / "request.json"
    identity = {
        "parent_job_id": "job-structure-1",
        "parent_workflow_id": "structure_prediction",
        "producer_stage": "structure_prediction:boltz",
        "producer_candidate_key": "frustrampnn/sources/boltz/prediction.pdb",
    }
    candidate_id = deterministic_candidate_id(**identity)
    metadata = {
        **identity,
        "candidate_id": candidate_id,
        "requiredness": "required",
        "checkpoint_id": "megascale.ckpt",
    }
    encoded = base64.b64encode(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")

    completed = subprocess.run(
        [
            sys.executable,
            str(PREPARE_SCRIPT),
            "--source",
            str(source),
            "--output-pdb",
            str(output_pdb),
            "--request",
            str(request_path),
            "--metadata-base64",
            encoded,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert request["candidate_id"] == candidate_id
    assert request["source_artifact"]["artifact_id"] == candidate_id
    assert request["source_artifact"]["relative_path"] == metadata["producer_candidate_key"]
    assert request["source_artifact"]["sha256"] == hashlib.sha256(output_pdb.read_bytes()).hexdigest()
    assert request["identity_authority"] == "pdb_coordinates"
