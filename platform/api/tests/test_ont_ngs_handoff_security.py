from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException, Request, Response

ROOT = Path(__file__).resolve().parents[3]
API_DIR = ROOT / "platform" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import routers.ont_runs as ont_runs
from services import ont_submission_trust


@pytest.fixture(autouse=True)
def _unit_contract_paths_are_prevalidated(monkeypatch):
    monkeypatch.setattr(ont_runs, "_confine_submitted_path", lambda value, _label, **_kwargs: str(value))

    async def valid_receipt(_session, *, receipt_id):
        assert receipt_id == "receipt-safe"
        return SimpleNamespace(
            id="receipt-safe",
            sequence_id="sequence-safe",
            revision_id="revision-safe",
            revision_sha256="c" * 64,
            reference_snapshot_path="/trusted/reference.fasta",
            reference_snapshot_sha256="d" * 64,
            consumed_at=None,
            consumed_job_id=None,
        )

    monkeypatch.setattr(ont_runs, "validate_molbio_ngs_receipt", valid_receipt)
    monkeypatch.setattr(ont_runs, "consume_molbio_ngs_receipt", valid_receipt)

    async def attach_run(_domain_session, _core_session, **kwargs):
        assert kwargs["global_domain_experiment_id"] == "domain-safe"
        assert kwargs["state_revision_id"] == "state-safe"
        assert kwargs["run_id"] == "run-trusted"
        assert kwargs["observed_generation"] == 11
        return SimpleNamespace(
            receipt_id="ont-receipt-safe",
            content_digest="e" * 64,
        )

    monkeypatch.setattr(ont_runs, "attach_instrument_run_evidence", attach_run)


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/", "headers": []})


class _Session:
    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _handoff_payload(params: dict | None = None) -> dict:
    return {
        "molbio_ngs_receipt_id": "receipt-safe",
        "global_domain_experiment_id": "domain-safe",
        "molbio_ngs_state_revision_id": "state-safe",
        "params": params or {},
    }


def test_handoff_accepts_only_explicit_safe_tuning_fields(monkeypatch):
    trusted = {
        "pod5_dir": "/trusted/pod5",
        "output_dir": "/trusted/out",
        "reference_fasta": "/trusted/ref.fa",
        "source_instrument_run_id": "run-trusted",
        "source_instrument_observed_generation": 11,
        "source_instrument_artifact_manifest_sha256": "a" * 64,
        "source_instrument_artifact_sha256": "b" * 64,
        "source_instrument_artifact_bytes": 1234,
    }
    async def build_handoff(run_id, payload):
        return {"params": trusted}
    monkeypatch.setattr(ont_runs.ont_run_control, "build_plasmid_qc_handoff", build_handoff)
    captured = {}
    created = SimpleNamespace(id="job-safe")
    async def fake_create(
        job,
        background_tasks,
        session,
        experiment_session,
        response,
        request,
        **_kwargs,
    ):
        captured["job"] = job
        return created
    monkeypatch.setattr(ont_runs, "_create_pipeline_job", fake_create)
    result = asyncio.run(ont_runs.ont_submit_plasmid_qc_from_run("run-trusted", _handoff_payload({"igv_report_max_sites": 7}), BackgroundTasks(), _request(), Response(), session=_Session(), molbio_ngs_session=_Session()))
    params = captured["job"].params
    assert result is created
    assert params["pod5_dir"] == "/trusted/pod5"
    assert params["output_dir"] == "/trusted/out"
    assert params["reference_fasta"] == "/trusted/ref.fa"
    assert params["source_instrument_run_id"] == "run-trusted"
    assert params["source_instrument_observed_generation"] == 11
    assert params["source_instrument_artifact_manifest_sha256"] == "a" * 64
    assert params["source_instrument_artifact_sha256"] == "b" * 64
    assert params["source_instrument_artifact_bytes"] == 1234
    assert params["igv_report_max_sites"] == 7
    assert params["global_domain_experiment_id"] == "domain-safe"
    assert params["molbio_ngs_state_revision_id"] == "state-safe"
    assert params["ont_instrument_run_receipt_id"] == "ont-receipt-safe"
    assert params["ont_instrument_run_binding"] == {
        "run_id": "run-trusted",
        "observed_generation": 11,
        "observation_sha256": "e" * 64,
        "receipt_id": "ont-receipt-safe",
    }


@pytest.mark.parametrize(
    "params",
    [
        {"threads": 7},
        {"caller_selected_path": "/caller/chosen/output"},
        {"bam_path": "/caller/chosen/reads.bam"},
        {"igv_report_max_sites": "/caller/chosen/path"},
    ],
)
def test_handoff_rejects_unknown_or_path_valued_params_before_handoff_or_job_creation(monkeypatch, params):
    monkeypatch.setattr(
        ont_runs.ont_run_control,
        "build_plasmid_qc_handoff",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unsafe params must not reach handoff")),
    )
    monkeypatch.setattr(
        ont_runs,
        "_create_pipeline_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unsafe params must not create a job")),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            ont_runs.ont_submit_plasmid_qc_from_run(
                "run-trusted", _handoff_payload(params), BackgroundTasks(), _request(), Response(), session=_Session()
            )
        )

    assert exc_info.value.status_code == 422
    assert "/caller/chosen" not in str(exc_info.value.detail)


def test_handoff_validation_error_is_http_422(monkeypatch):
    async def reject(run_id, payload):
        raise ValueError("invalid instrument handoff")
    monkeypatch.setattr(ont_runs.ont_run_control, "build_plasmid_qc_handoff", reject)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(ont_runs.ont_submit_plasmid_qc_from_run("run-invalid", _handoff_payload(), BackgroundTasks(), _request(), Response(), session=_Session()))
    assert exc_info.value.status_code == 422
    assert "invalid instrument handoff" in str(exc_info.value.detail)


@pytest.mark.parametrize(
    "reserved_key",
    sorted(ont_runs.ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS | ont_runs.ONT_SERVER_CONTROLLED_RUNTIME_PARAMS),
)
def test_public_handoff_rejects_every_reserved_runtime_and_provenance_key(monkeypatch, reserved_key):
    trusted = {
        "pod5_dir": "/trusted/pod5",
        "output_dir": "/trusted/out",
        "reference_fasta": "/trusted/ref.fa",
        "source_instrument_run_id": "run-trusted",
    }
    async def build_handoff(_run_id, _payload):
        return {"params": trusted}
    monkeypatch.setattr(ont_runs.ont_run_control, "build_plasmid_qc_handoff", build_handoff)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            ont_runs.ont_submit_plasmid_qc_from_run(
                "run-trusted",
                _handoff_payload({reserved_key: "/caller/value"}),
                BackgroundTasks(),
                _request(),
                Response(),
                session=_Session(),
            )
        )

    assert exc_info.value.status_code == 422
    assert "unsupported fields" in str(exc_info.value.detail)
    assert "/caller/value" not in str(exc_info.value.detail)


def test_runtime_rejects_digest_mismatch_immediately_before_snapshot_consumption(tmp_path: Path) -> None:
    snapshot = tmp_path / "launch.fastq"
    snapshot.write_bytes(b"@r1\nACGT\n+\n!!!!\n")
    verifier = getattr(ont_submission_trust, "verify_instrument_artifact_snapshot", None)
    assert verifier is not None, "ONT runtime must provide a digest-bound snapshot verifier"
    params = {
        "fastq_path": str(snapshot),
        "source_instrument_run_id": "ont-run-1",
        "source_instrument_observed_generation": 7,
        "source_instrument_artifact_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "source_instrument_artifact_bytes": snapshot.stat().st_size,
        "source_instrument_artifact_manifest_sha256": "a" * 64,
    }
    snapshot.write_bytes(b"@r1\nTGCA\n+\n!!!!\n")

    with pytest.raises(ValueError, match="digest mismatch"):
        verifier(params, snapshot_root=tmp_path)


def test_generic_caller_cannot_claim_instrument_snapshot_authority() -> None:
    request = ont_runs.OntNgsSubmitRequest(
        params={
            "fastq_path": "/trusted/reads.fastq",
            "reference_fasta": "/trusted/reference.fasta",
        },
        source_instrument_run_id="caller-selected-run",
    )

    with pytest.raises(ValueError, match="server-controlled"):
        ont_runs._job_create_for_ont_submit("ont_plasmid_qc", request)


def test_trusted_external_alignment_authority_survives_canonical_submit_normalization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    bam_path = tmp_path / "filtered.bam"
    reference_path = tmp_path / "egfp.fasta"
    bam_path.write_bytes(b"bam")
    reference_path.write_bytes(b">eGFP\nACGT\n")
    monkeypatch.setattr(
        ont_runs, "_confine_submitted_path",
        lambda value, _key, **_kwargs: str(value),
    )
    monkeypatch.setattr(
        ont_runs, "normalized_fasta_sequence_sha256", lambda _path: "e" * 64,
    )
    server_params = {
        "dataset_id": "receipt-1",
        "source_instrument_run_id": "run-1",
        "source_instrument_observed_generation": 7,
        "source_raw_representation_id": "raw-1",
        "source_move_source_id": "moves-1",
        "source_external_move_registration_receipt_id": "receipt-1",
        "source_move_bam_sha256": "a" * 64,
        "source_filtered_move_bam_sha256": "c" * 64,
        "source_read_inventory_sha256": "b" * 64,
        "ngs_reference_revision_id": "reference-1",
        "ngs_reference_artifact_id": "reference-artifact-1",
        "expected_reference_fasta_sha256": "d" * 64,
    }
    request = ont_runs.OntNgsSubmitRequest(
        name="external alignment",
        params={
            "bam_path": str(bam_path),
            "reference_fasta": str(reference_path),
            **server_params,
        },
        source_instrument_run_id="run-1",
    )

    job = ont_runs._job_create_for_ont_submit(
        "ont_plasmid_qc",
        request,
        trusted_server_params=frozenset(server_params),
        trusted_result_paths=frozenset({"bam_path"}),
        trusted_reference_fasta=reference_path,
    )

    assert job.params["ont_input_mode"] == "bam"
    assert job.params["reference_sequence_sha256"] == "e" * 64
    assert {key: job.params[key] for key in server_params} == server_params
