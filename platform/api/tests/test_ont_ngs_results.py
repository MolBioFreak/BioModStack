from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import rfc8785

from tests.resource_usage_receipt_fixture import valid_resource_receipt_authority

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.ont_ngs_results import _stages  # noqa: E402


def test_normal_reopen_pins_result_root_across_aba_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from services import ont_ngs_results as service

    root = tmp_path / "job-root"
    root.mkdir()
    (root / "authority.txt").write_text("original", encoding="utf-8")
    job = SimpleNamespace(id="job-1")
    monkeypatch.setattr(service, "resolve_persisted_job_result_root", lambda _job: root)

    def inspect_pinned(_job, pinned: Path):
        held = tmp_path / "held-root"
        root.rename(held)
        root.mkdir()
        (root / "authority.txt").write_text("replacement", encoding="utf-8")
        return {"authority": (pinned / "authority.txt").read_text(encoding="utf-8")}

    monkeypatch.setattr(service, "_build_file_projection_from_pinned_root", inspect_pinned)
    assert service._build_file_projection(cast(Any, job)) == {"authority": "original"}


def test_stage_projection_is_path_opaque_and_uses_terminal_receipt_counts() -> None:
    job = SimpleNamespace(
        completed_stages=["dimer_qc"],
        stage_outputs={"dimer_qc": ["/secret/result/dimer_qc/a", "/secret/result/dimer_qc/b"]},
        provenance={
            "stage_terminal_states": {
                "fastq_align": {"status": "complete", "outputs": ["bms_results/retry3/align/a.bam"]},
                "dimer_qc": {"status": "complete", "outputs": ["bms_results/retry3/dimer_qc/a", "bms_results/retry3/dimer_qc/b"]},
                "fastq_qc": {"status": "complete", "outputs": ["bms_results/retry3/fastq_qc/qc_manifest.json"]},
                "construct_verification": {"status": "complete", "outputs": ["bms_results/retry3/verification/qc_manifest.json"]},
            }
        },
    )

    stages = _stages(job)

    assert stages == [
        {"stage": "fastq_align", "status": "complete", "output_count": 1},
        {"stage": "dimer_qc", "status": "complete", "output_count": 2},
        {"stage": "fastq_qc", "status": "complete", "output_count": 1},
        {"stage": "construct_verification", "status": "complete", "output_count": 1},
    ]
    assert "/secret/result" not in repr(stages)


def _observed_package_authority() -> dict[str, Any]:
    return {
        "artifact_set_sha256": "a" * 64,
        "declared_artifact_count": 36,
        "present_artifact_count": 35,
        "unavailable_artifact_count": 1,
    }


def _persisted_authority_record() -> dict[str, Any]:
    return {
        "state": "validated",
        "partial": False,
        "result_kind": "ngs_sequence_qc",
        "workflow_id": "ont_fastq_qc",
        "input_mode": "fastq",
        "reference_sequence_sha256": "b" * 64,
        "source_fastq_sha256": "c" * 64,
        "resource_evidence_status": "accepted",
        "resource_usage_receipt_sha256": "9" * 64,
        "sequence_qc_manifest_sha256": "d" * 64,
        "construct_verification_manifest_sha256": "e" * 64,
        **_observed_package_authority(),
    }


def test_result_reopen_accepts_exact_fresh_terminal_package_authority() -> None:
    from services import ont_ngs_results as service

    require = cast(Any, getattr(service, "_require_persisted_package_authority", None))
    assert callable(require)
    params, receipt = valid_resource_receipt_authority()
    authority = _persisted_authority_record()
    authority["resource_usage_receipt_sha256"] = receipt["receipt_sha256"]
    job = SimpleNamespace(
        id="job-1",
        status="completed",
        provenance={"result_integrity": authority},
        params=params,
    )

    require(
        job,
        _observed_package_authority(),
        sequence_qc_manifest_sha256="d" * 64,
        construct_verification_manifest_sha256="e" * 64,
        reference_sequence_sha256="b" * 64,
        source_fastq_sha256="c" * 64,
    )


def test_result_reopen_accepts_exact_additive_retry3_reconciliation_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ont_ngs_results as service

    require = cast(Any, getattr(service, "_require_persisted_package_authority", None))
    assert callable(require)
    reconciliation = {
        "schema": "bms.ont-fastq-qc-reconciliation.v1",
        "job_id": "retry3",
        "workflow_id": "ont_fastq_qc",
        "input_mode": "fastq",
        "reference_sequence_sha256": "b" * 64,
        "source_fastq_sha256": "c" * 64,
        "resource_evidence_status": "historical_unavailable",
        "sequence_qc_manifest_sha256": "d" * 64,
        "verification_manifest_sha256": "e" * 64,
        **_observed_package_authority(),
    }
    validated: list[dict[str, Any]] = []
    monkeypatch.setattr(service, "validate_persisted_reconciliation_receipt", validated.append)
    job = SimpleNamespace(
        id="retry3",
        provenance={
            "result_integrity": {"result_kind": "design"},
            "ont_fastq_qc_reconciliation_v1": reconciliation,
        },
        params={},
    )

    require(
        job,
        _observed_package_authority(),
        sequence_qc_manifest_sha256="d" * 64,
        construct_verification_manifest_sha256="e" * 64,
        reference_sequence_sha256="b" * 64,
        source_fastq_sha256="c" * 64,
    )
    assert validated == [reconciliation]


def test_result_reopen_rejects_fresh_resource_receipt_without_persisted_bytes() -> None:
    from services import ont_ngs_results as service

    require = cast(Any, getattr(service, "_require_persisted_package_authority", None))
    params, _receipt = valid_resource_receipt_authority()
    params["resource_usage_receipts"] = []
    job = SimpleNamespace(
        id="job-1",
        status="completed",
        provenance={"result_integrity": _persisted_authority_record()},
        params=params,
    )
    with pytest.raises(service.OntNgsResultError, match="resource receipt"):
        require(
            job,
            _observed_package_authority(),
            sequence_qc_manifest_sha256="d" * 64,
            construct_verification_manifest_sha256="e" * 64,
            reference_sequence_sha256="b" * 64,
            source_fastq_sha256="c" * 64,
        )


def test_result_reopen_rejects_package_authority_mismatch() -> None:
    from services import ont_ngs_results as service

    require = cast(Any, getattr(service, "_require_persisted_package_authority", None))
    assert callable(require)
    authority = _persisted_authority_record()
    authority["artifact_set_sha256"] = "f" * 64
    params, receipt = valid_resource_receipt_authority()
    authority["resource_usage_receipt_sha256"] = receipt["receipt_sha256"]
    job = SimpleNamespace(
        id="job-1",
        status="completed",
        provenance={"result_integrity": authority},
        params=params,
    )

    with pytest.raises(service.OntNgsResultError, match="terminal package authority"):
        require(
            job,
            _observed_package_authority(),
            sequence_qc_manifest_sha256="d" * 64,
            construct_verification_manifest_sha256="e" * 64,
            reference_sequence_sha256="b" * 64,
            source_fastq_sha256="c" * 64,
        )


def test_historical_resource_projection_does_not_invent_a_receipt() -> None:
    from services import ont_ngs_results as service

    project = cast(Any, getattr(service, "_execution_resources", None))
    assert callable(project)
    job = SimpleNamespace(assigned_gpu=0, params={"dorado_device": "cuda:0"})

    result = project(job, {"resource_evidence_status": "historical_unavailable"})

    assert result == {
        "evidence_status": "historical_unavailable",
        "receipt_schema": None,
        "receipt_id": None,
        "receipt_sha256": None,
        "run_attempt_id": None,
        "execution_invocation_id": None,
        "outcome": None,
        "admitted_cpu_threads": None,
        "observed_memory_peak_bytes": None,
        "observed_pids_peak": None,
        "gpu_index": None,
        "gpu_uuid": None,
        "admitted_vram_bytes": None,
        "accelerator_applicability": "not_applicable",
        "reason": "No accepted producer resource-use receipt exists for this historical execution; scheduler and configuration fields are not execution evidence",
        "dorado_invoked": False,
        "scheduler_gpu_assignment": 0,
        "configured_dorado_device_ignored": "cuda:0",
    }


def test_accepted_resource_projection_uses_the_exact_persisted_receipt() -> None:
    from services import ont_ngs_results as service

    project = cast(Any, getattr(service, "_execution_resources", None))
    assert callable(project)
    params, receipt = valid_resource_receipt_authority()
    receipt_sha256 = cast(str, receipt["receipt_sha256"])
    job = SimpleNamespace(id="job-1", status="completed", assigned_gpu=None, params=params)

    result = cast(dict[str, Any], project(
        job,
        {"resource_evidence_status": "accepted", "resource_usage_receipt_sha256": receipt_sha256},
    ))

    assert result["evidence_status"] == "accepted"
    assert result["receipt_schema"] == "bms.workflow-resource-usage.v1"
    assert result["receipt_id"] == "admission-1"
    assert result["receipt_sha256"] == receipt_sha256
    assert result["run_attempt_id"] == "run-attempt-1"
    assert result["execution_invocation_id"] == "invocation-1"
    assert result["outcome"] == "completed"
    assert result["admitted_cpu_threads"] == 8
    assert result["observed_memory_peak_bytes"] == 4096
    assert result["observed_pids_peak"] == 7
    assert result["gpu_index"] is None
    assert result["gpu_uuid"] is None
    assert result["admitted_vram_bytes"] == 0
    assert result["scheduler_gpu_assignment"] is None


def test_accepted_resource_projection_rejects_fully_rehashed_foreign_job_receipt() -> None:
    from services import ont_ngs_results as service

    project = cast(Any, getattr(service, "_execution_resources", None))
    assert callable(project)
    params, receipt = valid_resource_receipt_authority()
    receipt["job_id"] = "job-other"
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256")
    receipt["receipt_sha256"] = hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()
    job = SimpleNamespace(id="job-1", status="completed", assigned_gpu=None, params=params)

    with pytest.raises(service.OntNgsResultError, match="receipt history is invalid"):
        project(
            job,
            {
                "resource_evidence_status": "accepted",
                "resource_usage_receipt_sha256": receipt["receipt_sha256"],
            },
        )


def test_accepted_resource_projection_rejects_fully_rehashed_false_enforcement() -> None:
    from services import ont_ngs_results as service

    project = cast(Any, getattr(service, "_execution_resources", None))
    assert callable(project)
    params, receipt = valid_resource_receipt_authority()
    receipt["enforcement"]["cpu_only_device_denial"] = False
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256")
    receipt["receipt_sha256"] = hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()
    job = SimpleNamespace(id="job-1", status="completed", assigned_gpu=None, params=params)

    with pytest.raises(service.OntNgsResultError, match="receipt history is invalid"):
        project(
            job,
            {
                "resource_evidence_status": "accepted",
                "resource_usage_receipt_sha256": receipt["receipt_sha256"],
            },
        )


def test_verification_projection_contains_only_schema_owned_decision_fields() -> None:
    from services import ont_ngs_results as service

    project = cast(Any, getattr(service, "_verification_projection", None))
    assert callable(project)
    verification_manifest = {
        "verdict": "REVIEW",
        "reason_codes": ["MIXED_ALLELES_DETECTED", "VARIANT_SUPPORT_AMBIGUOUS"],
        "summary": {"reference_name": "eGFP_plasmid", "reference_length": 5570},
        "checks": {
            "contamination": {
                "status": "pass",
                "reason_codes": [],
                "metrics": {
                    "screen_basis": "expected_reference_mapping_only",
                    "organism_identity_claimed": False,
                    "total_reads": 61708,
                    "mapped_reads": 61573,
                    "unmapped_reads": 135,
                    "unmapped_fraction": 0.0021877,
                },
            },
            "coverage": {
                "status": "pass",
                "reason_codes": [],
                "metrics": {
                    "row_count": 5570,
                    "coverage_fraction": 1.0,
                    "low_depth_fraction": 0.0,
                    "low_depth_positions": 0,
                    "minimum_depth": 49126,
                    "mixed_allele_positions": 21,
                    "strand_imbalanced_positions": 0,
                },
            },
            "read_support": {
                "status": "review",
                "reason_codes": ["MIXED_ALLELES_DETECTED"],
                "metrics": {
                    "row_count": 5570,
                    "coverage_fraction": 1.0,
                    "low_depth_fraction": 0.0,
                    "low_depth_positions": 0,
                    "minimum_depth": 49126,
                    "mixed_allele_positions": 21,
                    "strand_imbalanced_positions": 0,
                },
            },
            "sequence_identity": {
                "status": "review",
                "reason_codes": ["VARIANT_SUPPORT_AMBIGUOUS"],
                "metrics": {
                    "canonicalization": "exhaustive_minimum_edit_lexicographic_rotation_v1",
                    "consensus_support_validation": {
                        "reason": None,
                        "status": "valid",
                        "validator": "observed consensus to BAM-derived support v1",
                    },
                    "edit_cost": 1,
                    "identity_fraction": 0.9998204667863555,
                    "observed_length": 5569,
                    "orientation": "forward",
                    "reference_length": 5570,
                    "rotation_offset": 0,
                },
            },
            "topology": {
                "status": "pass",
                "reason_codes": [],
                "metrics": {
                    "aligned_dimer_reads": 2415,
                    "alignment_records": 117310,
                    "contradictory_breakpoint_evidence": False,
                    "edge_window_bp": 100,
                    "evidence_basis": "primary_and_supplementary_alignment_edges_plus_dimer_screen",
                    "expected_topology": "circular",
                    "mapped_unique_reads": 61573,
                    "non_boundary_split_reads": 0,
                    "origin_spanning_reads": 19882,
                    "provenance": {
                        "alignment_bam_sha256": "1" * 64,
                        "breakpoint_call_sha256": "2" * 64,
                        "reference_sha256": "3" * 64,
                        "samtools_command": ["apptainer", "exec", "/mnt/private/runtime.sif", "samtools"],
                        "samtools_returncode": 0,
                        "samtools_stderr": "",
                        "secondary_summary_sha256": "4" * 64,
                    },
                    "reason": None,
                    "schema": "biomodstack.construct_topology_evidence.v1",
                    "secondary_anomaly_fraction": 0.0,
                    "state": "present",
                },
            },
        },
        "variants": [
            {
                "id": "egfp_del_3515",
                "position_1based": 3515,
                "end_1based": 3516,
                "ref": "GC",
                "alt": "G",
                "kind": "DEL",
                "depth": 54191,
                "support_fraction": 0.5615,
                "support_status": "ambiguous",
                "circular_event_id": None,
            }
        ],
        "threshold_profile": {
            "id": "profile-a",
            "version": "1",
            "sha256": "a" * 64,
            "calibration_status": "experimental",
            "public_accuracy_validated": False,
            "values": {"min_depth": 20},
        },
        "interpretation": "not part of biomodstack.construct_verification.v2",
    }

    projected = cast(dict[str, Any], project(verification_manifest))

    assert set(projected) == {
        "verdict",
        "reason_codes",
        "summary",
        "checks",
        "variants",
        "threshold_profile",
    }
    assert "interpretation" not in projected
    assert set(projected["checks"]) == {
        "expected_reference_screen",
        "coverage",
        "read_support",
        "sequence_identity",
        "topology",
    }
    expected_screen = projected["checks"]["expected_reference_screen"]
    assert expected_screen["purpose"] == "Expected-reference mapping and unmapped-fraction screen only."
    assert expected_screen["metrics"]["screen_basis"] == "expected_reference_mapping_only"
    assert expected_screen["metrics"]["organism_identity_claimed"] is False
    assert expected_screen["units"] == {
        "screen_basis": "categorical",
        "organism_identity_claimed": "boolean",
        "total_reads": "reads",
        "mapped_reads": "reads",
        "unmapped_reads": "reads",
        "unmapped_fraction": "fraction",
    }
    assert projected["checks"]["coverage"]["units"]["minimum_depth"] == "alignment_observations"
    topology_metrics = cast(dict[str, Any], projected["checks"]["topology"]["metrics"])
    assert "provenance" not in topology_metrics
    assert topology_metrics["evidence_sha256"] == {
        "alignment_bam": "1" * 64,
        "breakpoint_call": "2" * 64,
        "reference": "3" * 64,
        "secondary_summary": "4" * 64,
    }
    assert topology_metrics["samtools_returncode"] == 0
    assert "/mnt/private" not in repr(projected)
    variant = projected["variants"][0]
    assert variant["normalization"] == "vcf_left_anchored_v1"
    assert variant["record_start_1based"] == 3515
    assert variant["record_end_1based"] == 3516
    assert variant["affected_interval_kind"] == "reference_bases"
    assert variant["affected_start_1based"] == 3516
    assert variant["affected_end_1based"] == 3516


def test_coverage_projection_freezes_minmax_algorithm_and_global_minimum(tmp_path: Path) -> None:
    from services import ont_ngs_results as service

    coverage = tmp_path / "coverage.tsv"
    coverage.write_text(
        "reference\tposition\tdepth\n"
        "eGFP_plasmid\t1\t10\n"
        "eGFP_plasmid\t2\t3\n"
        "eGFP_plasmid\t3\t8\n"
        "eGFP_plasmid\t4\t1\n"
        "eGFP_plasmid\t5\t5\n"
        "eGFP_plasmid\t6\t7\n",
        encoding="utf-8",
    )

    projected = service._load_coverage(coverage)

    assert projected["method"] == "minmax_envelope_v1"
    assert projected["source_row_count"] == 6
    assert projected["maximum_point_count"] == 2048
    assert projected["bucket_width_rows"] == 1
    assert projected["minimum_depth"] == 1
    assert projected["minimum_depth_position_1based"] == 4
    assert projected["depth_basis"] == "samtools_depth_aa_default_filters_excludes_deletions_v1"
    assert projected["depth_unit"] == "base_covering_alignment_records"
    assert projected["tie_breaking"] == "minimum:earliest_position;maximum:earliest_position"
    assert projected["endpoint_policy"] == "natural_bucket_extrema_only"
    assert projected["circular_policy"] == "linearized_1based_reference_order_no_wrap"
    assert any(point["position_1based"] == 4 and point["depth"] == 1 for point in projected["points"])


def test_coverage_construction_attestation_binds_source_and_projection(tmp_path: Path) -> None:
    from services import ont_ngs_results as service

    coverage = tmp_path / "coverage.tsv"
    coverage.write_text(
        "reference\tposition\tdepth\n"
        "eGFP_plasmid\t1\t10\n"
        "eGFP_plasmid\t2\t3\n",
        encoding="utf-8",
    )
    base = service._load_coverage(coverage)
    attestation = {
        "projection_sha256": hashlib.sha256(
            json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "source_row_count": 2,
        "source_rows_sha256": hashlib.sha256(coverage.read_bytes()).hexdigest(),
        "validated_at": "2026-08-16T23:07:27Z",
        "validator": "bms.ngs.fastq-qc-result-construction-validator.v1",
    }

    validated = service._load_coverage(coverage, construction_attestation=attestation)
    assert validated["construction_attestation"] == attestation

    tampered = dict(attestation, source_rows_sha256="0" * 64)
    with pytest.raises(service.OntNgsResultError, match="construction attestation is inconsistent"):
        service._load_coverage(coverage, construction_attestation=tampered)


@pytest.mark.parametrize(
    "body",
    [
        "reference\tposition\tdepth\neGFP_plasmid\t1\t10\neGFP_plasmid\t1\t9\n",
        "reference\tposition\tdepth\neGFP_plasmid\t1\t10\nforeign\t2\t9\n",
    ],
)
def test_coverage_projection_rejects_noncanonical_source_order(tmp_path: Path, body: str) -> None:
    from services import ont_ngs_results as service

    coverage = tmp_path / "coverage.tsv"
    coverage.write_text(body, encoding="utf-8")

    with pytest.raises(service.OntNgsResultError, match="coverage table is invalid"):
        service._load_coverage(coverage)


@pytest.mark.parametrize(
    ("kind", "ref", "alt", "record_end", "interval_kind", "affected_start", "affected_end"),
    [
        ("SNV", "A", "G", 2, "reference_bases", 2, 2),
        ("MNV", "AC", "GT", 3, "reference_bases", 2, 3),
        ("DEL", "AC", "A", 3, "reference_bases", 3, 3),
        ("INS", "AC", "ACG", 3, "between_bases", 3, 3),
        ("COMPLEX", "AC", "G", 3, "reference_bases", 2, 3),
    ],
)
def test_variant_projection_freezes_kind_specific_affected_intervals(
    kind: str,
    ref: str,
    alt: str,
    record_end: int,
    interval_kind: str,
    affected_start: int,
    affected_end: int,
) -> None:
    from services.ont_ngs_decision_projection import _project_variant

    projected = _project_variant(
        {
            "id": "variant-1",
            "kind": kind,
            "position_1based": 2,
            "end_1based": record_end,
            "ref": ref,
            "alt": alt,
            "support_status": "supported",
            "depth": 10,
            "support_fraction": 0.5,
            "circular_event_id": None,
        }
    )

    assert projected["record_start_1based"] == 2
    assert projected["record_end_1based"] == record_end
    assert projected["affected_interval_kind"] == interval_kind
    assert projected["affected_start_1based"] == affected_start
    assert projected["affected_end_1based"] == affected_end
