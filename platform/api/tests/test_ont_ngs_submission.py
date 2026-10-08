from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
ROUTERS_ROOT = API_ROOT / "routers"
for path in (API_ROOT, ROUTERS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from model_registry import ModelRegistry  # noqa: E402
import ont_runs  # noqa: E402
import routers.jobs as jobs_router  # noqa: E402
from schemas import JobResponse, JobStatus  # noqa: E402


def _post_with_preview(client, url, **kwargs):
    """Exercise the public two-step protocol for existing launch regressions."""
    if "/ngs/" in url and url.endswith("/submit") and "pooled-reference-assignment" not in url:
        payload = kwargs.get("json", {})
        preview = client.post(url.removesuffix("/submit") + "/preview", json=payload)
        if preview.status_code != 200:
            return preview
        kwargs["json"] = {**payload, "preview_digest": preview.json()["preview_digest"]}
    return client.post(url, **kwargs)


def _client_with_fake_create(monkeypatch, captured: dict[str, Any]) -> TestClient:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")

    class FakeSession:
        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    app.dependency_overrides[ont_runs.get_session] = FakeSession
    app.dependency_overrides[ont_runs.get_experiment_session] = FakeSession
    app.dependency_overrides[ont_runs.get_molbio_ngs_session] = FakeSession

    async def fake_create_pipeline_job(
        job_data,
        background_tasks,
        session,
        experiment_session,
        response,
        request,
        *,
        commit=True,
        **_kwargs,
    ):
        captured["job_data"] = job_data
        captured["session"] = session
        return JobResponse(
            id="job-ont-1",
            name=job_data.name,
            status=JobStatus.QUEUED,
            model_id=job_data.model_id,
            mode=job_data.mode,
            params=job_data.params,
            created_at=datetime(2026, 7, 8),
            output_dir="/tmp/out/job-ont-1",
            design_count=0,
        )

    monkeypatch.setattr(ont_runs, "_create_pipeline_job", fake_create_pipeline_job)
    monkeypatch.setattr(ont_runs, "_confine_submitted_path", lambda value, _label, **_kwargs: str(value))
    from services import molbio_ngs_receipts, ont_ngs_launch_admission

    async def fake_molecular_reference(receipt):
        return {"topology": "circular", "receipt_id": receipt.id}

    monkeypatch.setattr(molbio_ngs_receipts, "resolve_molbio_receipt_reference", fake_molecular_reference)
    monkeypatch.setattr(ont_ngs_launch_admission, "validate_bam_reference_admission", lambda params: None)
    return TestClient(app)


@pytest.mark.asyncio
async def test_prepared_preview_reuses_global_authority_and_binds_receipt_before_commit(monkeypatch):
    from fastapi import BackgroundTasks, Response
    from services.global_experiments import launch_contexts
    captured = {}
    _client_with_fake_create(monkeypatch, captured)
    receipt = SimpleNamespace(id="receipt", sequence_id="seq", revision_id="rev", revision_sha256="a" * 64,
        reference_snapshot_path="/server/ref.fasta", reference_snapshot_sha256="b" * 64,
        consumed_at=None, consumed_job_id=None)
    calls = []

    async def validate_receipt(_session, **_kwargs):
        return receipt

    async def consume(_session, **_kwargs):
        calls.append("consume")
        return receipt

    async def resolve(_session, _context_id):
        return SimpleNamespace(run_attempt_id="attempt")

    async def global_compile(_session, _context, **kwargs):
        calls.append("global_compile")
        return {**kwargs["params"], "global_test_resource_authority": {"job": "reserved-job"}}

    async def create(job, *_args, **kwargs):
        assert kwargs["commit"] is True
        assert receipt.consumed_job_id == "reserved-job"
        assert "global_test_resource_authority" not in job.params
        calls.append("create")
        return SimpleNamespace(id="reserved-job")

    class Session:
        async def get(self, *_args):
            return SimpleNamespace(scheduler_job_id="reserved-job")
        async def commit(self): pass
        async def rollback(self): pass

    monkeypatch.setattr(ont_runs, "validate_molbio_ngs_receipt", validate_receipt)
    monkeypatch.setattr(ont_runs, "consume_molbio_ngs_receipt", consume)
    monkeypatch.setattr(ont_runs, "_create_pipeline_job", create)
    monkeypatch.setattr(launch_contexts, "resolve_launch_context", resolve)
    monkeypatch.setattr(launch_contexts, "validate_bound_job_request", global_compile)
    request = ont_runs.OntNgsSubmitRequest(params={"pod5_dir": "/data/pod5", "molbio_ngs_receipt_id": "receipt"})
    token = ont_runs.current_launch_context_id.set("prepared-context")
    try:
        session = Session()
        preview = await ont_runs.ont_preview_ngs_workflow("ont_basecall_dna", request,
            BackgroundTasks(), SimpleNamespace(), Response(), session, session, session)
        assert calls == ["global_compile"]
        assert preview["prepared_job_id"] == "reserved-job"
        assert preview["effective_request"]["params"]["global_test_resource_authority"] == {"job": "reserved-job"}
        await ont_runs.ont_submit_ngs_workflow("ont_basecall_dna",
            request.model_copy(update={"preview_digest": preview["preview_digest"]}),
            BackgroundTasks(), SimpleNamespace(), Response(), session, session, session)
        assert calls == ["global_compile", "global_compile", "consume", "create"]
    finally:
        ont_runs.current_launch_context_id.reset(token)


@pytest.mark.parametrize("with_receipt", [False, True])
def test_compiled_preview_preserves_independent_context_and_binds_mutation(monkeypatch, with_receipt):
    captured = {}
    client = _client_with_fake_create(monkeypatch, captured)
    context_calls, consumed = [], []

    async def context(_session, **kwargs):
        context_calls.append(kwargs)
        return {}

    receipt = SimpleNamespace(id="native-receipt", sequence_id="seq", revision_id="rev",
        revision_sha256="a" * 64, reference_snapshot_sha256="b" * 64,
        reference_snapshot_path="/data/reference.fa", consumed_at=None, consumed_job_id=None)

    async def validate(_session, **kwargs):
        return receipt

    async def consume(_session, **kwargs):
        consumed.append(kwargs)
        return receipt

    monkeypatch.setattr(ont_runs, "resolve_state_analysis_context", context)
    monkeypatch.setattr(ont_runs, "validate_molbio_ngs_receipt", validate)
    monkeypatch.setattr(ont_runs, "consume_molbio_ngs_receipt", consume)
    url = "/api/ont/ngs/ont_basecall_dna"
    payload = {"name": "reviewed", "params": {"pod5_dir": "/data/pod5", "min_qscore": 11},
        "experiment_context": {"global_domain_experiment_id": "domain", "molbio_ngs_state_revision_id": "state"}}
    if with_receipt:
        payload["params"]["molbio_ngs_receipt_id"] = receipt.id
    preview = client.post(url + "/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert not captured and not consumed
    effective = preview.json()["effective_request"]["params"]
    assert effective["global_domain_experiment_id"] == "domain"
    assert effective["molbio_ngs_state_revision_id"] == "state"
    assert context_calls[0]["canonical_workflow_id"] == "ont_basecall_dna"
    if not with_receipt:
        assert "reference_fasta" not in effective
    digest = preview.json()["preview_digest"]
    changed = {**payload, "params": {**payload["params"], "min_qscore": 12}, "preview_digest": digest}
    assert client.post(url + "/submit", json=changed).status_code == 409
    assert not captured and not consumed
    missing = client.post(url + "/submit", json=payload)
    assert missing.status_code == 409
    assert not captured and not consumed
    accepted = client.post(url + "/submit", json={**payload, "preview_digest": digest})
    assert accepted.status_code == 201, accepted.text
    assert captured["job_data"].params["ont_launch_receipt"]["preview_digest"] == digest
    assert len(consumed) == int(with_receipt)


def test_context_and_managed_reference_cannot_cross_bind():
    with pytest.raises(ValueError, match="same exact state"):
        ont_runs.OntNgsSubmitRequest(params={},
            experiment_context={"global_domain_experiment_id": "d", "molbio_ngs_state_revision_id": "s1"},
            managed_reference={"global_domain_experiment_id": "d", "molbio_ngs_state_revision_id": "s2", "ngs_reference_revision_id": "r"})


@pytest.mark.parametrize("m5,realign,workflow,accepted", [
    (True, False, "ont_plasmid_qc", True),
    (False, False, "ont_plasmid_qc", False),
    (False, True, "ont_plasmid_qc", True),
    (False, True, "wf_clone_validation", True),
    (False, False, "ont_methylation_analysis", False),
    (False, True, "ont_methylation_analysis", False),
])
def test_real_bam_prequeue_reference_proof_and_explicit_alternative(tmp_path, m5, realign, workflow, accepted):
    import hashlib
    import pysam
    from services.ont_ngs_launch_admission import validate_bam_reference_admission
    reference = tmp_path / "ref.fasta"
    reference.write_text(">ref\nACGTACGT\n")
    sq = {"SN": "ref", "LN": 8}
    if m5:
        sq["M5"] = hashlib.md5(b"ACGTACGT", usedforsecurity=False).hexdigest()
    bam_path = tmp_path / "input.bam"
    with pysam.AlignmentFile(str(bam_path), "wb", header={"HD": {"VN": "1.6"}, "SQ": [sq]}) as bam:
        read = pysam.AlignedSegment(bam.header)
        read.query_name = "read1"
        read.query_sequence = "ACGT"
        read.flag = 0
        read.reference_id = 0
        read.reference_start = 0
        read.mapping_quality = 60
        read.cigarstring = "4M"
        bam.write(read)
    params = {"bam_path": str(bam_path), "reference_fasta": str(reference),
        "bam_force_realign": realign, "ont_workflow_id": workflow}
    if accepted:
        validate_bam_reference_admission(params)
    else:
        with pytest.raises(ValueError, match="M5|realignment"):
            validate_bam_reference_admission(params)
    assert "bam_source_sha256" not in params and "bam_reference_sha256" not in params


@pytest.mark.parametrize("qc_params", [{"run_fastq_qc": False}, {"run_fastq_qc": True}, {}])
def test_named_fastq_qc_rejects_disabled_qc_before_receipt_consumption(monkeypatch, qc_params) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)
    receipt = SimpleNamespace(
        id="receipt-qc", sequence_id="seq", revision_id="rev",
        revision_sha256="a" * 64, reference_snapshot_sha256="b" * 64,
        reference_snapshot_path="/data/reference.fa", consumed_at=None, consumed_job_id=None,
    )
    consumed = []

    async def validate(_session, *, receipt_id):
        assert qc_params.get("run_fastq_qc") is not False, "disabled QC reached receipt validation"
        return receipt

    async def consume(_session, *, receipt_id):
        consumed.append(receipt_id)
        return receipt

    monkeypatch.setattr(ont_runs, "validate_molbio_ngs_receipt", validate)
    monkeypatch.setattr(ont_runs, "consume_molbio_ngs_receipt", consume)
    response = _post_with_preview(client, "/api/ont/ngs/ont_fastq_qc/submit", json={
        "name": "disabled-named-qc",
        "params": {"fastq_path": "/data/reads.fastq", "molbio_ngs_receipt_id": "receipt-qc", **qc_params},
    })
    if qc_params.get("run_fastq_qc") is False:
        assert response.status_code == 422, response.text
        assert "requires run_fastq_qc=true" in response.text
        assert consumed == []
        assert captured == {}
    else:
        assert response.status_code == 201, response.text
        assert consumed == ["receipt-qc"]
        assert captured["job_data"].params["run_fastq_qc"] is True
        assert captured["job_data"].params["reference_fasta"] == "/data/reference.fa"
        assert captured["job_data"].params["molbio_revision_binding"]["receipt_id"] == "receipt-qc"


@pytest.mark.parametrize("authority", ["receipt", "managed"])
@pytest.mark.parametrize("workflow_id,params,error", [
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "basecalling_mode": ""}, "dorado_basecall_mode"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "basecalling_mode": "invalid"}, "dorado_basecall_mode"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "basecalling_mode": "duplex", "dorado_basecall_mode": "simplex"}, "one exact choice"),
    ("ont_plasmid_qc", {"pod5_dir": "/data/pod5", "barcode_kit": "SQK-RBK114-96"}, "only supported by ont_basecall_dna"),
    ("ont_basecall_rna", {"pod5_dir": "/data/pod5", "barcode_kit": "SQK-RBK114-96"}, "only supported by ont_basecall_dna"),
    ("ont_construct_screening", {"pod5_dir": "/data/pod5", "barcode_kit": "SQK-RBK114-96"}, "only supported by ont_basecall_dna"),
    ("ont_methylation_analysis", {"pod5_dir": "/data/pod5", "barcode_kit": "SQK-RBK114-96"}, "only supported by ont_basecall_dna"),
    ("wf_clone_validation", {"pod5_dir": "/data/pod5", "barcode_kit": "SQK-RBK114-96"}, "only supported by ont_basecall_dna"),
    ("ont_plasmid_qc", {"pod5_dir": "/data/pod5", "sample_sheet": "/data/samples.csv"}, "sample_sheet requires barcode_kit"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "sample_sheet": "/data/samples.csv"}, "sample_sheet requires barcode_kit"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "ont_molecule_type": "rna"}, "requires ont_molecule_type=dna"),
    ("ont_basecall_rna", {"pod5_dir": "/data/pod5", "dorado_basecall_mode": "duplex", "duplex_pairs": "/data/pairs.txt"}, "RNA duplex is unsupported"),
    ("ont_basecall_rna", {"pod5_dir": "/data/pod5", "trim_adapters": False}, "RNA always trims adapters"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "dorado_basecall_mode": "duplex", "duplex_pairs": "/data/pairs.txt", "trim_adapters": False}, "duplex lacks an adapter-trim control"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "dorado_basecall_mode": "duplex", "duplex_pairs": "/data/pairs.txt", "barcode_kit": "SQK-RBK114-96"}, "barcode classification is incompatible with duplex"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "dorado_basecall_mode": "duplex"}, "duplex mode requires duplex_pairs"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "duplex_pairs": "/data/pairs.txt"}, "duplex_pairs is only valid in duplex mode"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "dorado_quality_mode": "fast", "modified_bases": "6mA"}, "modified-base selection requires DNA HAC simplex"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "dorado_quality_mode": "hac", "modified_bases": "6mA", "barcode_kit": "SQK-RBK114-96"}, "mutually exclusive"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "dorado_basecall_mode": "duplex", "duplex_pairs": "/data/pairs.txt", "emit_moves": True}, "emit_moves is unavailable for duplex"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "dorado_model": "fast", "dorado_quality_mode": "hac"}, "one exact quality choice"),
    ("wf_clone_validation", {"pod5_dir": "/data/pod5", "wf_clone_insert_reference": "/data/insert.fa"}, "wf_clone_insert_reference requires wf_clone_primers"),
    ("ont_construct_screening", {"pod5_dir": "/data/pod5", "run_assembly": True, "wf_clone_regions_bedfile": "/data/regions.bed"}, "wf_clone_regions_bedfile requires wf_clone_host_reference"),
    ("ont_methylation_analysis", {"fastq_path": "/data/reads.fastq"}, "does not accept"),
    ("ont_fastq_qc", {"fastq_path": "/data/reads.fastq", "run_fastq_qc": False}, "requires run_fastq_qc=true"),
])
def test_reference_independent_contract_rejected_before_authority(monkeypatch, authority, workflow_id, params, error):
    captured = {}
    client = _client_with_fake_create(monkeypatch, captured)

    async def forbidden(*args, **kwargs):
        pytest.fail("invalid operator input reached reference authority or queue")

    for name in (
        "resolve_state_analysis_launch_policy", "resolve_managed_reference_for_launch",
        "validate_molbio_ngs_receipt", "consume_molbio_ngs_receipt", "_create_pipeline_job",
    ):
        monkeypatch.setattr(ont_runs, name, forbidden)
    payload = {"params": dict(params)}
    if authority == "receipt":
        payload["params"]["molbio_ngs_receipt_id"] = "receipt-invalid"
    else:
        payload["managed_reference"] = {
            "global_domain_experiment_id": "experiment", "molbio_ngs_state_revision_id": "state",
            "ngs_reference_revision_id": "reference",
        }
    response = _post_with_preview(client, f"/api/ont/ngs/{workflow_id}/submit", json=payload)
    assert response.status_code == 422, response.text
    assert error in response.text
    assert captured == {}


@pytest.mark.parametrize("workflow_id,barcoding", [
    ("ont_basecall_dna", {"barcode_kit": "SQK-RBK114-96", "sample_sheet": "/data/samples.csv"}),
    ("ont_plasmid_qc", {"sample_sheet": "", "duplex_pairs": "", "modified_bases": "none"}),
    ("ont_plasmid_qc", {"sample_sheet": "  ", "duplex_pairs": "  "}),
])
def test_reference_independent_contract_preserves_valid_barcoding_and_placeholders(monkeypatch, workflow_id, barcoding):
    captured = {}
    client = _client_with_fake_create(monkeypatch, captured)
    receipt = SimpleNamespace(
        id="receipt-barcode", sequence_id="seq", revision_id="rev",
        revision_sha256="a" * 64, reference_snapshot_sha256="b" * 64,
        reference_snapshot_path="/data/reference.fa", consumed_at=None, consumed_job_id=None,
    )
    calls = []

    async def validate(_session, *, receipt_id):
        calls.append(("validate", receipt_id))
        return receipt

    async def consume(_session, *, receipt_id):
        calls.append(("consume", receipt_id))
        return receipt

    monkeypatch.setattr(ont_runs, "validate_molbio_ngs_receipt", validate)
    monkeypatch.setattr(ont_runs, "consume_molbio_ngs_receipt", consume)
    response = _post_with_preview(client, f"/api/ont/ngs/{workflow_id}/submit", json={
        "params": {"pod5_dir": "/data/pod5", "molbio_ngs_receipt_id": receipt.id, **barcoding},
    })
    assert response.status_code == 201, response.text
    assert calls == [("validate", receipt.id), ("validate", receipt.id), ("consume", receipt.id)]
    effective = captured["job_data"].params
    assert effective["dorado_basecall_mode"] == "simplex"
    assert effective["modified_bases"] == "none"
    if workflow_id == "ont_basecall_dna":
        assert effective["barcode_kit"] == barcoding["barcode_kit"]
        assert effective["sample_sheet"] == barcoding["sample_sheet"]
    else:
        assert "barcode_kit" not in effective
        assert "sample_sheet" not in effective
        assert "duplex_pairs" not in effective


@pytest.mark.parametrize("bad_params", [
    {"unknown_scientific_setting": 1},
    {"_global_resource_admission": {"approved": True}},
    {"bam_min_mapq": "20"},
    {"bam_min_mapq": True},
    {"bam_min_mapq": 10**400},
    {"bam_min_mapq": None},
    {"bam_min_mapq": 20.5},
    {"bam_force_realign": "false"},
    {"bam_path": ["/data/reads.bam"]},
    {"dorado_batch_size": 32},  # inactive for BAM
    {"wf_clone_approx_size": 7000},  # inactive workflow
])
def test_operator_settings_rejected_before_receipt_or_queue(monkeypatch, bad_params):
    captured = {}
    client = _client_with_fake_create(monkeypatch, captured)
    consumed = []
    receipt = SimpleNamespace(
        id="receipt-typed", sequence_id="seq", revision_id="rev",
        revision_sha256="a" * 64, reference_snapshot_sha256="b" * 64,
        reference_snapshot_path="/data/reference.fa", consumed_at=None, consumed_job_id=None,
    )

    async def validate(_session, *, receipt_id):
        return receipt

    async def consume(_session, *, receipt_id):
        consumed.append(receipt_id)
        return receipt

    monkeypatch.setattr(ont_runs, "validate_molbio_ngs_receipt", validate)
    monkeypatch.setattr(ont_runs, "consume_molbio_ngs_receipt", consume)
    response = _post_with_preview(client, "/api/ont/ngs/ont_plasmid_qc/submit", json={
        "params": {"bam_path": "/data/reads.bam", "molbio_ngs_receipt_id": "receipt-typed", **bad_params},
    })
    assert response.status_code == 422, response.text
    assert next(iter(bad_params)) in response.text
    assert consumed == []
    assert captured == {}


@pytest.mark.parametrize("workflow_id,input_mode", [
    (workflow_id, input_mode)
    for workflow_id, input_modes in {
        "ont_basecall_dna": ("pod5",), "ont_basecall_rna": ("pod5",),
        "ont_plasmid_qc": ("pod5", "bam", "fastq"),
        "ont_construct_screening": ("pod5", "bam", "fastq"),
        "ont_methylation_analysis": ("pod5", "bam"),
        "wf_clone_validation": ("pod5", "bam", "fastq"),
        "ont_fastq_qc": ("fastq",),
    }.items() for input_mode in input_modes
])
def test_ui_shaped_operator_payloads_preserve_effective_values(monkeypatch, workflow_id, input_mode):
    # Mirror NanoporeTemplate's serialized branches (undefined fields are omitted).
    captured = {}
    client = _client_with_fake_create(monkeypatch, captured)
    receipt = SimpleNamespace(
        id="receipt-ui", sequence_id="seq", revision_id="rev",
        revision_sha256="a" * 64, reference_snapshot_sha256="b" * 64,
        reference_snapshot_path="/data/reference.fa", consumed_at=None, consumed_job_id=None,
    )
    consumed = []

    async def validate(_session, *, receipt_id):
        return receipt

    async def consume(_session, *, receipt_id):
        consumed.append(receipt_id)
        return receipt

    monkeypatch.setattr(ont_runs, "validate_molbio_ngs_receipt", validate)
    monkeypatch.setattr(ont_runs, "consume_molbio_ngs_receipt", consume)
    params: dict[str, Any] = {"run_modkit": False}
    if input_mode == "pod5":
        params.update(pod5_dir="/data/pod5", dorado_quality_mode="hac",
                      dorado_basecall_mode="simplex", ont_molecule_type="rna" if workflow_id == "ont_basecall_rna" else "dna",
                      modified_bases="none", min_qscore=12, trim_adapters=True,
                      emit_summary=False, emit_moves=True, dorado_batch_size=32)
    elif input_mode == "bam":
        params.update(bam_path="/data/reads.bam", bam_force_realign=workflow_id != "ont_methylation_analysis", bam_min_mapq=17)
    else:
        params.update(fastq_path="/data/reads.fastq", expected_plasmid_size=8000,
                      min_fastq_read_length=25, fastq_minimap2_preset="map-ont",
                      fastq_minimap2_allow_secondary=False, igv_track_window_bp=150,
                      igv_report_max_sites=12, igv_report_flanking_bp=300, run_fastq_qc=True)
    if workflow_id == "ont_plasmid_qc" and input_mode != "fastq":
        params.update(run_fastq_qc=True, expected_plasmid_size=9123, min_fastq_read_length=17,
            fastq_minimap2_preset="map-ont", fastq_minimap2_allow_secondary=False,
            igv_track_window_bp=135, igv_report_max_sites=25, igv_report_flanking_bp=0)
    if workflow_id in {"ont_construct_screening", "wf_clone_validation"}:
        params.update(run_assembly=True, wf_clone_approx_size=8100,
                      wf_clone_expected_identity=98.5, wf_clone_primers="/data/primers.fa",
                      wf_clone_insert_reference="/data/insert.fa",
                      wf_clone_host_reference="/data/host.fa", wf_clone_regions_bedfile="/data/regions.bed")
        from services.ont_ngs_contract import WF_CLONE_DEFAULTS
        params["wf_clone_basecaller_model"] = WF_CLONE_DEFAULTS["wf_clone_basecaller_model"]
    if workflow_id in ont_runs.ONT_REFERENCE_REQUIRED_WORKFLOWS:
        params["molbio_ngs_receipt_id"] = receipt.id
    response = _post_with_preview(client, f"/api/ont/ngs/{workflow_id}/submit", json={"params": params})
    assert response.status_code == 201, response.text
    effective = captured["job_data"].params
    for key, value in params.items():
        if key != "molbio_ngs_receipt_id":
            assert effective[key] == value, key
    assert effective["ont_workflow_id"] == workflow_id
    assert effective["ont_input_mode"] == input_mode
    assert consumed == ([receipt.id] if workflow_id in ont_runs.ONT_REFERENCE_REQUIRED_WORKFLOWS else [])


@pytest.mark.parametrize("workflow_id,params", [
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "run_fastq_qc": True}),
    ("ont_basecall_rna", {"pod5_dir": "/data/pod5", "run_fastq_qc": True}),
])
def test_basecall_profile_rejects_inactive_qc_stage(monkeypatch, workflow_id, params):
    captured = {}
    client = _client_with_fake_create(monkeypatch, captured)
    response = _post_with_preview(client, f"/api/ont/ngs/{workflow_id}/submit", json={"params": params})
    assert response.status_code == 422, response.text
    assert "run_fastq_qc" in response.text
    assert captured == {}


@pytest.mark.parametrize("override", [{}, {"min_mapq": "20"}, {"min_mapq": True}, {"unknown": 1}, {"_global_resource_admission": {}}])
def test_pooled_profile_keeps_its_closed_typed_public_contract(monkeypatch, override):
    captured = {}
    client = _client_with_fake_create(monkeypatch, captured)
    reached = []

    async def submit(**kwargs):
        reached.append(kwargs["request"].model_dump())
        return {"job_id": "pooled-fake"}

    monkeypatch.setattr(ont_runs, "submit_pooled_reference_assignment", submit)
    payload = {
        "idempotency_key": "typed-pooled", "fastq_path": "/data/pooled.fastq",
        "targets": [
            {"target_id": "a", "label": "A", "molbio_ngs_receipt_id": "receipt-a"},
            {"target_id": "b", "label": "B", "molbio_ngs_receipt_id": "receipt-b"},
        ], "min_mapq": 17, "min_alignment_score_margin": 12, **override,
    }
    response = _post_with_preview(client, "/api/ont/ngs/pooled-reference-assignment/submit", json=payload)
    if override:
        assert response.status_code == 422, response.text
        assert reached == []
    else:
        assert response.status_code == 201, response.text
        assert reached[0]["min_mapq"] == 17
        assert reached[0]["min_alignment_score_margin"] == 12
        assert [target["molbio_ngs_receipt_id"] for target in reached[0]["targets"]] == ["receipt-a", "receipt-b"]
    assert captured == {}


@pytest.mark.parametrize("workflow_id,params,expected", [
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "min_qscore": "12"}, "min_qscore"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "trim_adapters": 1}, "trim_adapters"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "dorado_batch_size": "32"}, "dorado_batch_size"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "emit_summary": None}, "emit_summary"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "dorado_quality_mode": ["hac"]}, "dorado_quality_mode"),
    ("ont_basecall_dna", {"pod5_dir": "/data/pod5", "run_modkit": True}, "run_modkit"),
    ("ont_methylation_analysis", {"bam_path": "/data/reads.bam", "run_modkit": True, "modkit_filter_threshold": "0.5"}, "modkit_filter_threshold"),
    ("ont_methylation_analysis", {"bam_path": "/data/reads.bam", "run_modkit": True, "modkit_filter_threshold": True}, "modkit_filter_threshold"),
    ("wf_clone_validation", {"fastq_path": "/data/reads.fastq", "run_assembly": False}, "run_assembly"),
    ("ont_construct_screening", {"fastq_path": "/data/reads.fastq", "run_assembly": False, "wf_clone_sample": "inactive"}, "wf_clone_sample"),
    ("ont_fastq_qc", {"fastq_path": "/data/reads.fastq", "min_qscore": 12}, "min_qscore"),
])
def test_cross_workflow_scalar_and_inactive_settings_fail_before_authority(monkeypatch, workflow_id, params, expected):
    captured = {}
    client = _client_with_fake_create(monkeypatch, captured)

    async def forbidden(*args, **kwargs):
        pytest.fail("invalid operator settings must fail before receipt authority")

    monkeypatch.setattr(ont_runs, "validate_molbio_ngs_receipt", forbidden)
    monkeypatch.setattr(ont_runs, "consume_molbio_ngs_receipt", forbidden)
    if workflow_id in ont_runs.ONT_REFERENCE_REQUIRED_WORKFLOWS:
        params = {**params, "molbio_ngs_receipt_id": "unconsumed"}
    response = _post_with_preview(client, f"/api/ont/ngs/{workflow_id}/submit", json={"params": params})
    assert response.status_code == 422, response.text
    assert expected in response.text
    assert captured == {}


def test_basecalling_keeps_optional_reference_and_registry_keeps_server_enrichment(monkeypatch):
    captured = {}
    client = _client_with_fake_create(monkeypatch, captured)
    response = _post_with_preview(client, "/api/ont/ngs/ont_basecall_dna/submit", json={"params": {
        "pod5_dir": "/data/pod5", "reference_fasta": "/data/optional.fa", "run_fastq_qc": False,
    }})
    assert response.status_code == 201, response.text
    effective = captured["job_data"].params
    assert effective["reference_fasta"] == "/data/optional.fa"
    assert ModelRegistry().validate_job_params("nanopore", "basecall_dna", {
        **effective, "_global_resource_admission": {"server": True}, "server_future_enrichment": {},
    }) == []


@pytest.mark.parametrize("extra", [{}, {"params": {"bam_min_mapq": 30}}, {"_global_resource_admission": {}}])
def test_external_bam_lane_remains_closed_and_server_owned(monkeypatch, tmp_path, extra):
    import routers.ont_runs as canonical_ont_runs
    import routers.ont_signal_workbench as signal_router

    captured = {}
    client = _client_with_fake_create(monkeypatch, captured)
    assert isinstance(client.app, FastAPI)
    client.app.include_router(signal_router.router, prefix="/api/ont/signal-workbench")
    reference = tmp_path / "reference.fa"
    reference.write_text(">ref\nACGT\n")
    reached = []

    async def authority(*args, **kwargs):
        reached.append(kwargs)
        return {
            "bam_path": "/data/server.bam", "reference_fasta": str(reference), "dataset_id": "dataset-test",
            "params": {"source_instrument_run_id": "run-test", "source_instrument_observed_generation": 1},
        }

    monkeypatch.setattr(signal_router, "_comparison_principal", lambda request: "test-operator")
    monkeypatch.setattr(signal_router.service, "resolve_external_alignment_launch_authority", authority)
    monkeypatch.setattr(canonical_ont_runs, "_confine_submitted_path", lambda value, _label, **kwargs: str(value))
    monkeypatch.setattr(signal_router, "_create_pipeline_job", ont_runs._create_pipeline_job)
    response = _post_with_preview(client, "/api/ont/signal-workbench/external-alignment-jobs", json={
        "move_source_id": "move-test", "reference_revision_id": "reference-test",
        "global_domain_experiment_id": "experiment-test", "molbio_ngs_state_revision_id": "state-test",
        "name": "external-test", **extra,
    })
    if extra:
        assert response.status_code == 422, response.text
        assert reached == []
        assert captured == {}
    else:
        assert response.status_code == 201, response.text
        effective = captured["job_data"].params
        assert effective["bam_force_realign"] is True
        assert effective["run_fastq_qc"] is False
        assert effective["source_instrument_run_id"] == "run-test"
        assert effective["reference_fasta"] == str(reference)


@pytest.mark.parametrize("params,accepted", [
    ({"basecalling_mode": "simplex"}, True),
    ({"basecalling_mode": "duplex", "duplex_pairs": "/inputs/pairs.tsv"}, True),
    ({"basecalling_mode": "duplex", "dorado_basecall_mode": "duplex", "duplex_pairs": "/inputs/pairs.tsv"}, True),
    ({"basecalling_mode": "duplex", "dorado_basecall_mode": "simplex", "duplex_pairs": "/data/pairs.txt"}, False),
])
def test_existing_basecalling_alias_remains_typed_without_conflicting_choices(monkeypatch, params, accepted):
    captured = {}
    client = _client_with_fake_create(monkeypatch, captured)
    response = _post_with_preview(client, "/api/ont/ngs/ont_basecall_dna/submit", json={"params": {
        "pod5_dir": "/data/pod5", **params,
    }})
    if accepted:
        assert response.status_code == 201, response.text
        assert captured["job_data"].params["dorado_basecall_mode"] == params["basecalling_mode"]
    else:
        assert response.status_code == 422, response.text
        assert "basecalling_mode" in response.text
        assert captured == {}


def test_named_fastq_qc_rejects_missing_reference(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)
    response = _post_with_preview(client, "/api/ont/ngs/ont_fastq_qc/submit", json={
        "name": "qc-without-reference", "params": {"fastq_path": "/data/reads.fastq"},
    })
    assert response.status_code == 422
    assert "requires a server-issued" in response.text
    assert captured == {}


def test_named_fastq_qc_catalog_describes_required_reference_and_qc() -> None:
    import yaml
    from services.ont_ngs_contract import get_ont_workflow_spec

    model = yaml.safe_load((API_ROOT / "config/models/nanopore.yaml").read_text())
    mode = next(mode for mode in model["modes"] if mode["id"] == "fastq_qc")
    for description in (mode["description"], get_ont_workflow_spec("ont_fastq_qc").description):
        assert "reference-required" in description
        assert "always-on" in description.lower()


def test_reference_required_alias_rejects_caller_path_before_job_creation(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)

    response = _post_with_preview(client,
        "/api/ont/ngs/plasmid_qc/submit",
        json={
            "name": "plasmid A12",
            "params": {
                "fastq_path": "/data/run/A12.fastq.gz",
                "reference_fasta": "/data/refs/A12.fa",
            },
            "pinned_gpu": 0,
        },
    )

    assert response.status_code == 422
    assert "server-controlled" in response.text
    assert captured == {}


@pytest.mark.parametrize(
    ("workflow_id", "params", "mode"),
    [
        ("ont_basecall_dna", {"pod5_dir": "/data/run/pod5"}, "basecall_dna"),
        ("ont_basecall_rna", {"pod5_dir": "/data/run/pod5"}, "basecall_rna"),
    ],
)
def test_each_canonical_ont_workflow_has_a_typed_prelaunch_route(monkeypatch, workflow_id, params, mode) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)

    response = _post_with_preview(client, f"/api/ont/ngs/{workflow_id}/submit", json={"name": workflow_id, "params": params})

    assert response.status_code == 201, response.text
    job_data = captured["job_data"]
    assert job_data.mode == mode
    assert job_data.params["ont_workflow_id"] == workflow_id
    assert job_data.params["ont_input_mode"] in {"pod5", "bam", "fastq"}


@pytest.mark.parametrize(
    "legacy_params",
    [
        {"run_multimer_qc": True},
        {"run_multimer_qc": True, "run_fastq_qc": False},
    ],
)
def test_typed_ont_submit_rejects_legacy_multimer_qc_for_fresh_jobs(monkeypatch, legacy_params) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)

    response = _post_with_preview(client,
        "/api/ont/ngs/ont_basecall_dna/submit",
        json={
            "name": "fresh-legacy-alias",
            "params": {"pod5_dir": "/data/run/pod5", **legacy_params},
        },
    )

    assert response.status_code == 422
    assert "run_multimer_qc is read-only legacy compatibility" in response.text
    assert captured == {}


@pytest.mark.parametrize(
    "workflow_id",
    [
        "ont_plasmid_qc",
        "ont_construct_screening",
        "ont_methylation_analysis",
        "ont_fastq_qc",
        "wf_clone_validation",
    ],
)
def test_reference_required_routes_reject_mutable_reference_paths(monkeypatch, workflow_id: str) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)
    response = _post_with_preview(client,
        f"/api/ont/ngs/{workflow_id}/submit",
        json={
            "name": workflow_id,
            "params": {
                "fastq_path": "/data/run/reads.fastq.gz",
                "reference_fasta": "/data/refs/ref.fa",
            },
        },
    )
    assert response.status_code == 422
    assert "immutable MolBio receipt" in response.text
    assert captured == {}


@pytest.mark.parametrize(
    "params",
    [
        {"pod5_dir": "/data/run/pod5", "dorado_basecall_mode": "duplex", "duplex_pairs": "/data/run/pairs.tsv"},
        {"pod5_dir": "/data/run/pod5", "barcode_kit": "SQK-RBK114-96", "sample_sheet": "/data/run/samples.csv"},
    ],
)
def test_dna_duplex_and_rbk114_demux_remain_typed_basecall_modes(monkeypatch, params) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)

    response = _post_with_preview(client, "/api/ont/ngs/ont_basecall_dna/submit", json={"name": "dna mode", "params": params})

    assert response.status_code == 201, response.text
    assert captured["job_data"].params["ont_workflow_id"] == "ont_basecall_dna"


def test_ont_run_plasmid_handoff_submit_builds_and_submits_job(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)

    async def fake_build_plasmid_qc_handoff(run_id, payload):
        return {
            "model_id": "nanopore",
            "mode": "plasmid_qc",
            "params": {
                "ont_workflow_id": "ont_plasmid_qc",
                "fastq_path": "/data/run/A12.fastq.gz",
                "reference_fasta": payload["reference_fasta"],
                "source_instrument_run_id": run_id,
                "source_instrument_observed_generation": 11,
                "source_minknow_run_id": "MNK-001",
                "source_instrument_observed_generation": 1,
            },
            "fake_or_demo_devices": False,
        }

    monkeypatch.setattr(ont_runs.ont_run_control, "build_plasmid_qc_handoff", fake_build_plasmid_qc_handoff)

    receipt = SimpleNamespace(
        id="receipt-1",
        sequence_id="sequence-1",
        revision_id="revision-1",
        revision_sha256="a" * 64,
        reference_snapshot_sha256="b" * 64,
        reference_snapshot_path="/data/refs/A12.fa",
        consumed_at=None,
        consumed_job_id=None,
    )

    async def fake_validate_receipt(_session, *, receipt_id):
        assert receipt_id == "receipt-1"
        return receipt

    async def fake_consume_receipt(_session, *, receipt_id):
        assert receipt_id == "receipt-1"
        return receipt

    async def fake_attach_instrument_run_evidence(*_args, **kwargs):
        assert kwargs["global_domain_experiment_id"] == "domain-1"
        assert kwargs["state_revision_id"] == "state-revision-1"
        return SimpleNamespace(receipt_id="instrument-receipt-1", content_digest="c" * 64)

    monkeypatch.setattr(ont_runs, "validate_molbio_ngs_receipt", fake_validate_receipt)
    monkeypatch.setattr(ont_runs, "consume_molbio_ngs_receipt", fake_consume_receipt)
    monkeypatch.setattr(ont_runs, "attach_instrument_run_evidence", fake_attach_instrument_run_evidence)

    response = _post_with_preview(client,
        "/api/ont/runs/ont-run-1/handoff/plasmid-qc/submit",
        json={
            "name": "live run plasmid QC",
            "molbio_ngs_receipt_id": "receipt-1",
            "global_domain_experiment_id": "domain-1",
            "molbio_ngs_state_revision_id": "state-revision-1",
            "params": {"igv_report_max_sites": 12},
        },
    )

    assert response.status_code == 201
    job_data = captured["job_data"]
    assert job_data.name == "live run plasmid QC"
    assert job_data.model_id == "nanopore"
    assert job_data.mode == "plasmid_qc"
    assert job_data.params["ont_workflow_id"] == "ont_plasmid_qc"
    assert job_data.params["source_instrument_run_id"] == "ont-run-1"
    assert job_data.params["source_minknow_run_id"] == "MNK-001"
    assert job_data.params["igv_report_max_sites"] == 12


def test_instrument_handoff_submit_rejects_browser_reference_path_before_building_server_handoff(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    app.dependency_overrides[ont_runs.get_session] = lambda: object()
    monkeypatch.setattr(
        ont_runs.ont_run_control,
        "build_plasmid_qc_handoff",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("browser reference paths must not reach handoff builder")),
    )

    response = TestClient(app).post(
        "/api/ont/runs/ont-run-1/handoff/plasmid-qc/submit",
        json={"reference_fasta": "/caller/chosen/reference.fasta"},
    )

    assert response.status_code == 422
    assert "/caller/chosen/reference.fasta" not in response.text


def test_created_ont_job_receives_opaque_alignment_capability(monkeypatch) -> None:
    import routers.jobs as jobs_router
    from services import alignment_access, ont_submission_trust

    created = JobResponse(
        id="job-capability-1",
        name="capability test",
        status=JobStatus.QUEUED,
        model_id="nanopore",
        mode="plasmid_qc",
        params={},
        created_at=datetime(2026, 7, 18),
        output_dir="/tmp/out/job-capability-1",
        design_count=0,
    )
    captured: dict[str, str] = {}

    async def fake_create_job(_job, _tasks, _session, **_kwargs):
        digest = ont_submission_trust.alignment_capability_digest()
        assert digest is not None
        captured["digest"] = digest
        return created

    class Session:
        pass

    monkeypatch.setattr(jobs_router, "create_job", fake_create_job)
    response = Response()
    request = Request(
        {"type": "http", "method": "POST", "scheme": "https", "path": "/api/ont/ngs/plasmid_qc/submit", "headers": [(b"x-forwarded-proto", b"https")]}
    )
    session = Session()

    result = asyncio.run(
        ont_runs._create_pipeline_job(
            type("JobData", (), {})(),
            BackgroundTasks(),
            session,
            session,
            response,
            request,
        )
    )

    assert result.id == created.id
    assert ont_submission_trust.alignment_capability_digest() is None
    digest = captured["digest"]
    cookie_header = response.headers["set-cookie"]
    token = cookie_header.split("=", 1)[1].split(";", 1)[0]
    assert alignment_access.capability_matches(token, digest)
    assert "HttpOnly" in cookie_header
    assert "Secure" in cookie_header
    assert "SameSite=strict" in cookie_header
    assert "Max-Age=1800" in cookie_header
    assert "Path=/" in cookie_header
    assert cookie_header.startswith("__Host-bms-ngs-")


def test_capability_issuance_failure_occurs_before_ont_job_creation(monkeypatch) -> None:
    import routers.jobs as jobs_router
    from services import alignment_access, ont_submission_trust

    created = False

    async def fake_create_job(_job, _tasks, _session, **_kwargs):
        nonlocal created
        created = True
        raise AssertionError("job creation must not be reached")

    def fail_issuance():
        raise RuntimeError("injected issuance failure")

    monkeypatch.setattr(jobs_router, "create_job", fake_create_job)
    monkeypatch.setattr(alignment_access, "issue_alignment_access_token", fail_issuance)
    request = Request(
        {"type": "http", "method": "POST", "scheme": "https", "path": "/api/ont/ngs/plasmid_qc/submit", "headers": []}
    )

    with pytest.raises(RuntimeError, match="injected issuance failure"):
        asyncio.run(
            ont_runs._create_pipeline_job(
                type("JobData", (), {})(),
                BackgroundTasks(),
                object(),
                object(),
                Response(),
                request,
            )
        )
    assert created is False
    assert ont_submission_trust.is_trusted_ont_job_creation() is False
    assert ont_submission_trust.alignment_capability_digest() is None


def test_explicit_deferred_commit_survives_launch_context(monkeypatch) -> None:
    import routers.jobs as jobs_router

    created = JobResponse(
        id="job-deferred-1",
        name="deferred",
        model_id="nanopore",
        mode="plasmid_qc",
        status=JobStatus.QUEUED,
        params={},
        created_at=datetime(2026, 8, 28),
        output_dir="/tmp/out/job-deferred-1",
        design_count=0,
    )
    captured: dict[str, Any] = {}

    class JobData:
        def model_copy(self, *, update: dict[str, Any]):
            captured["launch_context_id"] = update["launch_context_id"]
            return self

    async def fake_create_job(_job, _tasks, _session, **kwargs):
        captured["commit"] = kwargs["_commit"]
        return created

    monkeypatch.setattr(jobs_router, "create_job", fake_create_job)
    request = Request({
        "type": "http", "method": "POST", "scheme": "https",
        "path": "/api/ont/signal-workbench/external-alignment-jobs", "headers": [],
    })
    token = ont_runs.current_launch_context_id.set("launch-context-1")
    try:
        asyncio.run(ont_runs._create_pipeline_job(
            JobData(), BackgroundTasks(), object(), object(), Response(), request,
            commit=False,
        ))
    finally:
        ont_runs.current_launch_context_id.reset(token)

    assert captured == {
        "launch_context_id": "launch-context-1",
        "commit": False,
    }


def test_nanopore_model_registry_accepts_direct_ont_product_modes() -> None:
    registry = ModelRegistry()
    for mode, params in {
        "plasmid_qc": {"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fa"},
        "fastq_qc": {"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fa"},
        "basecall_dna": {"pod5_dir": "/tmp/pod5"},
        "basecall_rna": {"pod5_dir": "/tmp/pod5"},
        "construct_screening": {"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fa"},
    }.items():
        assert registry.validate_job_params("nanopore", mode, params) == []


def test_public_jobs_route_rejects_all_direct_nanopore_creation() -> None:
    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api/jobs")
    app.dependency_overrides[jobs_router.get_session] = lambda: object()
    client = TestClient(app)

    for key in sorted(ont_runs.ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS | ont_runs.ONT_SERVER_CONTROLLED_RUNTIME_PARAMS):
        response = _post_with_preview(client,
            "/api/jobs",
            json={
                "name": "untrusted nanopore job",
                "model_id": "nanopore",
                "mode": "plasmid_qc",
                "params": {
                    "fastq_path": "/data/reads.fastq",
                    "reference_fasta": "/data/reference.fasta",
                    key: "/caller/value",
                },
            },
        )
        assert response.status_code == 422, (key, response.text)
        assert "typed /api/ont/ngs" in response.text

    unknown = client.post(
        "/api/jobs",
        json={
            "name": "unknown nanopore key",
            "model_id": "nanopore",
            "mode": "plasmid_qc",
            "params": {
                "fastq_path": "/data/reads.fastq",
                "reference_fasta": "/data/reference.fasta",
                "future_executable_selector": "/caller/code",
            },
        },
    )
    assert unknown.status_code == 422
    assert "typed /api/ont/ngs" in unknown.text


def test_ngs_execution_target_is_part_of_reviewed_effective_request():
    from schemas import JobCreate
    local = ont_runs.OntNgsSubmitRequest(params={}, execution_target_id=None)
    remote = ont_runs.OntNgsSubmitRequest(params={}, execution_target_id="vast:123")
    local_job = JobCreate(name="review", model_id="nanopore", mode="basecall", params={})
    remote_job = local_job.model_copy(update={"execution_target_id": "vast:123"})
    a = ont_runs._ngs_launch_preview("ont_basecall_dna", local, local_job)
    b = ont_runs._ngs_launch_preview("ont_basecall_dna", remote, remote_job)
    assert b["requested_settings"]["execution_target_id"] == "vast:123"
    assert b["effective_request"]["execution_target_id"] == "vast:123"
    assert a["preview_digest"] != b["preview_digest"]
