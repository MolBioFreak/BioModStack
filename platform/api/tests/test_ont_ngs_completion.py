from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.ont_ngs_completion import (  # noqa: E402
    _REQUIRED_TERMINAL_STAGES,
    _REQUIRED_STAGE_OUTPUT_SUFFIXES,
    OntNgsCompletionError,
    _validate_terminal_stages,
)


def test_production_completion_entry_pins_result_root_across_aba_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from services import ont_ngs_completion as service

    root = tmp_path / "job-root"
    root.mkdir()
    (root / "authority.txt").write_text("original", encoding="utf-8")
    job = SimpleNamespace(id="job-1")
    monkeypatch.setattr(service, "resolve_persisted_job_result_root", lambda _job: root)

    async def inspect_pinned(_job, **kwargs):
        pinned = kwargs["pinned_result_root"]
        held = tmp_path / "held-root"
        root.rename(held)
        root.mkdir()
        (root / "authority.txt").write_text("replacement", encoding="utf-8")
        return {"authority": (pinned / "authority.txt").read_text(encoding="utf-8")}

    monkeypatch.setattr(service, "_validate_and_prepare_from_pinned_root", inspect_pinned)
    result = asyncio.run(service.validate_and_prepare_ont_fastq_qc_completion(job))
    assert result == {"authority": "original"}


@pytest.mark.asyncio
async def test_external_signal_alignment_completion_persists_primary_package_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from services import ont_ngs_completion as service

    source_bam = tmp_path / "source.bam"
    source_bam.write_bytes(b"source-bam")
    output_root = tmp_path / "result"
    output_root.mkdir()
    alignment_root = output_root / "align"
    alignment_root.mkdir()
    for name in (
        "aligned.bam",
        "aligned.bam.bai",
        "reference.fasta",
        "reference.fasta.fai",
        "align.log",
    ):
        (alignment_root / name).write_bytes(name.encode("utf-8"))
    manifest_path = output_root / "qc_manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    reference_sha256 = "a" * 64
    job = SimpleNamespace(
        id="external-alignment-job",
        model_id="nanopore",
        output_dir=str(output_root),
        child_output_dir=None,
        completed_stages=["dorado_align"],
        stage_outputs={},
        params={
            "ont_workflow_id": "ont_plasmid_qc",
            "ont_request_workflow_id": "ont_plasmid_qc",
            "ont_input_mode": "bam",
            "run_fastq_qc": False,
            "bam_path": str(source_bam),
            "bam_source_sha256": hashlib.sha256(source_bam.read_bytes()).hexdigest(),
            "reference_sequence_sha256": reference_sha256,
            "source_move_source_id": "ont-moves-1",
            "source_external_move_registration_receipt_id": "ont-external-move-1",
        },
        provenance={
            "stage_terminal_states": {
                "dorado_align": {
                    "status": "complete",
                    "outputs": [
                        f"bms_results/{output_root.name}/align/aligned.bam",
                        f"bms_results/{output_root.name}/align/aligned.bam.bai",
                        f"bms_results/{output_root.name}/align/reference.fasta",
                        f"bms_results/{output_root.name}/align/reference.fasta.fai",
                        f"bms_results/{output_root.name}/align/align.log",
                        f"bms_results/{output_root.name}/qc_manifest.json",
                    ],
                }
            }
        },
    )
    validator: Any = getattr(service, "validate_and_prepare_ont_signal_alignment_completion", None)
    assert callable(validator), "external alignment completion validator is missing"
    monkeypatch.setattr(service, "resolve_persisted_job_result_root", lambda _job: output_root)
    monkeypatch.setattr(
        service,
        "_read_manifest",
        lambda _path: (b"{}", "c" * 64),
    )
    monkeypatch.setattr(
        service,
        "load_sequence_qc_manifest",
        lambda *_args, **_kwargs: {
            "schema": "sequence_qc.manifest.v1",
            "artifact_schema_version": 2,
            "workflow_id": "ont_plasmid_qc",
            "job_id": job.id,
            "input_mode": "bam",
            "analysis_status": "completed",
            "alignment_session": {
                "mode": "primary",
                "reference_sequence_sha256": reference_sha256,
                "source_reference_sequence_sha256": reference_sha256,
            },
            "artifacts": [],
        },
    )
    descriptors = [
        {
            "source": "sequence_qc",
            "kind": kind,
            "state": "present",
            "sha256": str(index) * 64,
            "size_bytes": index,
        }
        for index, kind in enumerate(
            ("sequence_qc_manifest", "alignment_bam", "alignment_bai", "reference", "reference_index"),
            start=1,
        )
    ]
    monkeypatch.setattr(
        service.ngs_alignment_sessions,
        "build_ngs_package_artifacts",
        lambda *_args, **_kwargs: descriptors,
    )
    monkeypatch.setattr(
        service.ngs_alignment_sessions,
        "build_alignment_sessions",
        lambda *_args, **_kwargs: [{"mode": "primary", "ready": True}],
    )
    monkeypatch.setattr(service, "attach_resource_usage_receipt", lambda params, _receipt: dict(params))

    result = await validator(job, resource_usage_receipt=None)

    assert result["artifact_set_sha256"] == job.provenance["result_integrity"]["artifact_set_sha256"]
    assert result["declared_artifact_count"] == 5
    assert job.provenance["result_integrity"]["result_kind"] == "ngs_alignment_session"
    assert job.provenance["result_integrity"]["sequence_qc_manifest_sha256"] == "c" * 64
    assert "resource_evidence_status" not in result
    assert job.params["run_fastq_qc"] is False


def test_package_builder_rejects_exact_five_field_duplicate_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from tests.ont_ngs_completion_fixture import configure_valid_ont_terminal_completion
    from services import ngs_alignment_sessions

    job = SimpleNamespace(id="duplicate-package", params={}, provenance={}, model_id="nanopore")
    configure_valid_ont_terminal_completion(monkeypatch, job, tmp_path, production_validation=True)
    manifest_path = Path(job.output_dir) / "fastq_qc" / "qc_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    duplicate = next(item for item in manifest["artifacts"] if item["kind"] == "log")
    manifest["artifacts"].append(dict(duplicate))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ngs_alignment_sessions.AlignmentSessionError, match="duplicate five-field artifact record"):
        ngs_alignment_sessions.build_ngs_package_artifacts(
            job.id,
            source_reference_sha256=job.params["reference_sequence_sha256"],
            workflow_id="ont_fastq_qc",
            input_mode="fastq",
            source_input_path=job.params["fastq_path"],
            job_output_dir=job.output_dir,
        )


def _job_with_terminal_states(outputs_by_stage: dict[str, list[str]]) -> SimpleNamespace:
    return SimpleNamespace(
        params={},
        completed_stages=["dimer_qc"],
        stage_outputs={"dimer_qc": ["stale-output"]},
        provenance={
            "stage_terminal_states": {
                stage: {"status": "complete", "outputs": list(outputs_by_stage[stage])}
                for stage in _REQUIRED_TERMINAL_STAGES
            }
        },
    )


def test_full_package_authority_is_order_invariant_and_count_closed() -> None:
    from services import ont_ngs_completion as service

    summarize = cast(Any, getattr(service, "canonical_ngs_package_authority", None))
    assert callable(summarize)
    descriptors = [
        {
            "source": "sequence_qc",
            "kind": "summary",
            "state": "present",
            "sha256": "a" * 64,
            "size_bytes": 10,
        },
        {
            "source": "input_mode",
            "kind": "modified_bases",
            "state": "not_applicable_to_input_mode",
            "sha256": None,
            "size_bytes": None,
        },
    ]

    first = summarize(descriptors)
    second = summarize(list(reversed(descriptors)))

    assert first == second
    assert first["declared_artifact_count"] == 2
    assert first["present_artifact_count"] == 1
    assert first["unavailable_artifact_count"] == 1
    assert len(first["artifact_set_sha256"]) == 64


def test_package_authority_rejects_an_exact_duplicate_descriptor() -> None:
    from services import ont_ngs_completion as service

    descriptor = {
        "source": "sequence_qc",
        "kind": "summary",
        "state": "present",
        "sha256": "a" * 64,
        "size_bytes": 12,
    }

    with pytest.raises(service.OntNgsCompletionError, match="duplicate"):
        service.canonical_ngs_package_authority([descriptor, dict(descriptor)])


def test_fastq_manifest_authority_excludes_root_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services import ngs_alignment_sessions
    from services.global_experiments import adapters

    candidates = getattr(ngs_alignment_sessions, "_sequence_manifest_candidates", None)
    assert callable(candidates)
    assert candidates(tmp_path, "fastq") == (tmp_path / "fastq_qc" / "qc_manifest.json",)

    (tmp_path / "qc_manifest.json").write_text("{}", encoding="utf-8")
    job = SimpleNamespace(
        params={"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
    )
    monkeypatch.setattr(adapters, "resolve_persisted_job_result_root", lambda _job: tmp_path)
    with pytest.raises(adapters.AdapterError, match="native sequence-QC manifest is unavailable"):
        adapters._sequence_qc_manifest_path(cast(Any, job))

    canonical = tmp_path / "fastq_qc" / "qc_manifest.json"
    canonical.parent.mkdir()
    canonical.write_text("{}", encoding="utf-8")
    assert adapters._sequence_qc_manifest_path(cast(Any, job)) == canonical


def test_fastq_normalization_and_resubmit_drop_all_accelerator_and_dorado_fields() -> None:
    from services.ont_ngs_contract import normalize_ont_launch_params

    stale = {
        "fastq_path": "/managed/input.fastq.gz",
        "dorado_basecall_mode": "duplex",
        "dorado_enable_modified_bases": True,
        "dorado_future_control": "unsafe",
        "gpu_id": 3,
        "gpu_priority": 99,
        "gpu_memory_fraction": 0.9,
        "gpu_future_control": "unsafe",
        "pinned_gpus": [0, 1],
        "cuda_visible_devices": "0,1",
        "msa_local_db": "/stale/db",
        "anarcii_gpu_id": 2,
    }
    normalized = normalize_ont_launch_params("ont_fastq_qc", stale)
    assert normalized["fastq_path"] == stale["fastq_path"]
    assert not any(
        key.startswith(("dorado_", "gpu_", "msa_", "anarcii_"))
        or key in {"pinned_gpu", "pinned_gpus", "cuda_visible_devices", "cpus_per_gpu"}
        for key in normalized
    )

    jobs_source = (Path(__file__).resolve().parents[1] / "routers" / "jobs.py").read_text(encoding="utf-8")
    resubmit = jobs_source[jobs_source.index("async def resubmit_job(") : jobs_source.index("# RE-INGESTION ENDPOINT")]
    call = "ont_ngs_contract.normalize_ont_launch_params("
    assert call in resubmit
    assert resubmit.index(call) < resubmit.index("estimate_vram(")


def test_canonical_fastq_adapter_requires_cpu_resource_authority() -> None:
    from routers import workflow_adapter

    requires = cast(Any, getattr(workflow_adapter, "_requires_ont_fastq_resource_authority", None))
    assert callable(requires)
    assert requires(
        SimpleNamespace(model_id="nanopore"),
        {"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
    ) is True
    assert requires(SimpleNamespace(model_id="nanopore"), {"ont_workflow_id": "ont_raw_signal"}) is False
    source = Path(workflow_adapter.__file__).read_text(encoding="utf-8")
    assert "canonical FASTQ-QC launch requires resource admission authority" in source


@pytest.mark.asyncio
async def test_stage_start_cas_cannot_resurrect_a_concurrently_terminal_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import jobs

    publish = cast(Any, getattr(jobs, "_publish_generic_stage_start", None))
    assert callable(publish)
    initial = SimpleNamespace(
        id="job-a",
        status="running",
        queue_status="running",
        awaiting_input=False,
        current_stage=None,
        stage_progress=None,
        completed_stages=[],
        stage_outputs={},
        provenance={"stage-callback-token-sha256": "token"},
    )
    terminal = SimpleNamespace(
        **{
            **vars(initial),
            "completed_stages": ["fastq_qc"],
            "stage_outputs": {"fastq_qc": ["receipt"]},
            "provenance": {
                **initial.provenance,
                "stage_terminal_states": {
                    "fastq_qc": {"status": "complete", "outputs": ["receipt"]},
                },
            },
        }
    )

    class Result:
        def __init__(self, *, job=None, rowcount=None):
            self.job = job
            self.rowcount = rowcount

        def scalar_one_or_none(self):
            return self.job

    class Session:
        def __init__(self):
            self.selects = 0
            self.rollbacks = 0

        async def execute(self, statement):
            if getattr(statement, "is_select", False):
                self.selects += 1
                return Result(job=initial if self.selects == 1 else terminal)
            return Result(rowcount=0)

        def expunge(self, _job):
            return None

        async def rollback(self):
            self.rollbacks += 1

        async def commit(self):
            raise AssertionError("lost stage-start CAS committed")

    monkeypatch.setattr(jobs.stage_reporting, "token_is_authorized", lambda *_args: True)
    session = Session()
    with pytest.raises(jobs.HTTPException) as denied:
        await publish(
            session=cast(Any, session),
            job_id="job-a",
            stage="fastq_qc",
            token="token",
        )
    assert denied.value.status_code == 409
    assert "terminal" in str(denied.value.detail)
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_stage_terminal_retry_preserves_a_concurrently_newer_current_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import jobs

    initial = SimpleNamespace(
        id="job-a",
        status="running",
        queue_status="running",
        awaiting_input=False,
        current_stage="fastq_align",
        stage_progress={"stage": "fastq_align"},
        completed_stages=[],
        stage_outputs={},
        provenance={"stage-callback-token-sha256": "token"},
    )
    concurrent = SimpleNamespace(**{**vars(initial), "current_stage": "fastq_qc", "stage_progress": {"stage": "fastq_qc"}})

    class Result:
        def __init__(self, *, job=None, rowcount=None):
            self.job = job
            self.rowcount = rowcount

        def scalar_one_or_none(self):
            return self.job

    class Session:
        def __init__(self):
            self.selects = 0
            self.updates = 0
            self.update_current_stages: list[object] = []

        async def execute(self, statement):
            if getattr(statement, "is_select", False):
                self.selects += 1
                return Result(job=initial if self.selects == 1 else concurrent)
            self.updates += 1
            self.update_current_stages.append(statement.compile().params.get("current_stage"))
            return Result(rowcount=0 if self.updates == 1 else 1)

        def expunge(self, _job):
            return None

        async def rollback(self):
            return None

        async def commit(self):
            return None

    monkeypatch.setattr(jobs.stage_reporting, "token_is_authorized", lambda *_args: True)
    session = Session()
    response = await jobs._publish_generic_stage_terminal(
        session=cast(Any, session),
        job_id="job-a",
        stage="fastq_align",
        status="complete",
        outputs=["receipt"],
        token="token",
    )
    assert response["status"] == "complete"
    assert session.update_current_stages[-1] == "fastq_qc"


def test_terminal_stage_validation_accepts_canonical_result_relative_receipts(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    result_root = state_root / "bms_results" / "retry3"
    output_by_stage: dict[str, list[str]] = {}
    for stage in _REQUIRED_TERMINAL_STAGES:
        output_by_stage[stage] = []
        for suffix in _REQUIRED_STAGE_OUTPUT_SUFFIXES[stage]:
            output = result_root / suffix
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(stage, encoding="utf-8")
            output_by_stage[stage].append(f"bms_results/{result_root.name}/{suffix}")

    job = _job_with_terminal_states(output_by_stage)
    completed_stages, stage_outputs = _validate_terminal_stages(job, result_root, result_root)

    assert completed_stages == list(_REQUIRED_TERMINAL_STAGES)
    assert stage_outputs == output_by_stage


@pytest.mark.parametrize("mutation", ["reorder", "extra", "cross_stage_duplicate"])
def test_terminal_stage_receipts_reject_noncanonical_order_extras_and_cross_stage_reuse(
    tmp_path: Path, mutation: str,
) -> None:
    state_root = tmp_path / "state"
    result_root = state_root / "bms_results" / "retry3"
    output_by_stage: dict[str, list[str]] = {}
    for stage in _REQUIRED_TERMINAL_STAGES:
        output_by_stage[stage] = []
        for suffix in _REQUIRED_STAGE_OUTPUT_SUFFIXES[stage]:
            output = result_root / suffix
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(stage, encoding="utf-8")
            output_by_stage[stage].append(f"bms_results/{result_root.name}/{suffix}")
    if mutation == "reorder":
        output_by_stage["fastq_align"][:2] = reversed(output_by_stage["fastq_align"][:2])
    elif mutation == "extra":
        extra = result_root / "align" / "extra.txt"
        extra.write_text("extra", encoding="utf-8")
        output_by_stage["fastq_align"].append(f"bms_results/{result_root.name}/align/extra.txt")
    else:
        output_by_stage["dimer_qc"][0] = output_by_stage["fastq_align"][0]
    with pytest.raises(OntNgsCompletionError, match="output contract mismatch|duplicated across stages"):
        _validate_terminal_stages(cast(Any, _job_with_terminal_states(output_by_stage)), result_root, result_root)


def test_terminal_stage_validation_rejects_a_relative_receipt_for_another_result_root(
    tmp_path: Path,
) -> None:
    result_root = tmp_path / "state" / "bms_results" / "retry3"
    result_root.mkdir(parents=True)
    output_by_stage = {
        stage: [f"bms_results/foreign-run/{suffix}" for suffix in _REQUIRED_STAGE_OUTPUT_SUFFIXES[stage]]
        for stage in _REQUIRED_TERMINAL_STAGES
    }
    job = _job_with_terminal_states(output_by_stage)

    with pytest.raises(OntNgsCompletionError, match="result root"):
        _validate_terminal_stages(job, result_root, result_root)


def test_terminal_stage_validation_rejects_non_regular_output(
    tmp_path: Path,
) -> None:
    result_root = tmp_path / "state" / "bms_results" / "retry3"
    output_by_stage: dict[str, list[str]] = {}
    for stage in _REQUIRED_TERMINAL_STAGES:
        output_by_stage[stage] = []
        for index, suffix in enumerate(_REQUIRED_STAGE_OUTPUT_SUFFIXES[stage]):
            output = result_root / suffix
            output.parent.mkdir(parents=True, exist_ok=True)
            if stage == "fastq_qc" and index == 0:
                output.mkdir()
            else:
                output.write_text(stage, encoding="utf-8")
            output_by_stage[stage].append(str(output))
    job = _job_with_terminal_states(output_by_stage)

    with pytest.raises(OntNgsCompletionError, match="regular file"):
        _validate_terminal_stages(job, result_root, result_root)


@pytest.mark.asyncio
async def test_finalizer_persists_stage_mirrors_without_a_transient_all_stages_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ont_ngs_completion as service

    result_root = tmp_path / "state" / "bms_results" / "retry3"
    outputs_by_stage: dict[str, list[str]] = {}
    for stage in _REQUIRED_TERMINAL_STAGES:
        outputs_by_stage[stage] = []
        for suffix in _REQUIRED_STAGE_OUTPUT_SUFFIXES[stage]:
            output = result_root / suffix
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(stage, encoding="utf-8")
            outputs_by_stage[stage].append(f"bms_results/{result_root.name}/{suffix}")

    reference_sha256 = "a" * 64
    fastq_manifest = {
        "reference": {"expected_sha256": reference_sha256, "name": "eGFP_plasmid", "length": 5570},
        "artifacts": [
            {
                "kind": "summary",
                "state": "present",
                "required": True,
                "integrity_valid": True,
                "actual_sha256": "b" * 64,
                "size_bytes": 10,
            }
        ],
    }
    verification_manifest = {
        "schema": service.VERIFICATION_SCHEMA,
        "summary": {"reference_name": "eGFP_plasmid", "reference_length": 5570},
        "inputs": {"source_reads": {"sha256": "f" * 64}},
        "verdict": "review_required",
        "artifacts": [
            {
                "kind": "verification_summary",
                "state": "present",
                "required": True,
                "integrity_valid": True,
                "actual_sha256": "c" * 64,
                "size_bytes": 20,
            }
        ],
    }
    job = SimpleNamespace(
        id="job-a",
        model_id="nanopore",
        params={
            "ont_workflow_id": "ont_fastq_qc",
            "ont_input_mode": "fastq",
            "reference_sequence_sha256": reference_sha256,
            "fastq_path": str(tmp_path / "input.fastq.gz"),
        },
        provenance={
            "historical": {"preserved": True},
            "stage_terminal_states": {
                stage: {"status": "complete", "outputs": list(outputs_by_stage[stage])}
                for stage in _REQUIRED_TERMINAL_STAGES
            },
        },
        completed_stages=["dimer_qc"],
        stage_outputs={"dimer_qc": ["stale"]},
        status="running",
        queue_status="running",
        paused=False,
        current_stage="construct_verification",
        stage_progress={"stage": "construct_verification"},
        error_message=None,
    )

    monkeypatch.setattr(service, "resolve_persisted_job_result_root", lambda _job: result_root)
    monkeypatch.setattr(service, "_read_manifest", lambda path: (b"{}", "d" * 64 if path.parent.name == "fastq_qc" else "e" * 64))
    monkeypatch.setattr(
        service,
        "load_sequence_qc_manifest",
        lambda path, **_kwargs: fastq_manifest if path.parent.name == "fastq_qc" else verification_manifest,
    )
    monkeypatch.setattr(
        service.ngs_alignment_sessions,
        "build_ngs_package_artifacts",
        lambda *_args, **_kwargs: [
            {
                "source": "fixture",
                "kind": f"artifact_{index}",
                "state": "present",
                "sha256": f"{index + 1:064x}",
                "size_bytes": index + 1,
            }
            for index in range(34)
        ] + [
            {
                "source": "input_mode",
                "kind": kind,
                "state": "not_applicable_to_input_mode",
                "sha256": None,
                "size_bytes": None,
            }
            for kind in ("modified_bases", "signal_data")
        ],
    )
    attached_receipts: list[dict] = []

    def fake_attach(params, receipt):
        attached_receipts.append(dict(receipt))
        return {**dict(params or {}), "resource_usage_receipts": [dict(receipt)]}

    monkeypatch.setattr(service, "attach_resource_usage_receipt", fake_attach, raising=False)

    integrity = await service.validate_and_prepare_ont_fastq_qc_completion(
        cast(Any, job),
        resource_usage_receipt={"complete": True, "receipt_sha256": "9" * 64},
    )

    assert job.completed_stages == list(_REQUIRED_TERMINAL_STAGES)
    assert job.stage_outputs == outputs_by_stage
    assert not hasattr(job, "all_stages")
    assert job.provenance["historical"] == {"preserved": True}
    assert integrity["declared_artifact_count"] == 36
    assert integrity["present_artifact_count"] == 34
    assert integrity["unavailable_artifact_count"] == 2
    assert integrity["source_fastq_sha256"] == "f" * 64
    assert attached_receipts == [{"complete": True, "receipt_sha256": "9" * 64}]
    assert job.params["resource_usage_receipts"] == attached_receipts
