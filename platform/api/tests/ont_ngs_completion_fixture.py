from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def configure_valid_ont_terminal_completion(
    monkeypatch: Any, job: Any, tmp_path: Path, *, production_validation: bool = False,
) -> None:
    from services import ont_ngs_completion as service

    result_root = tmp_path / "state" / "bms_results" / str(job.id)
    outputs_by_stage: dict[str, list[str]] = {}
    for stage in service._REQUIRED_TERMINAL_STAGES:
        outputs_by_stage[stage] = []
        for suffix in service._REQUIRED_STAGE_OUTPUT_SUFFIXES[stage]:
            output = result_root / suffix
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(f"{stage}:{suffix}\n", encoding="utf-8")
            outputs_by_stage[stage].append(f"bms_results/{result_root.name}/{suffix}")

    reference_sha256 = "a" * 64
    input_path = tmp_path / "input.fastq.gz"
    input_path.write_bytes(b"FASTQ")
    fastq_dir = result_root / "fastq_qc"
    verification_dir = result_root / "verification"
    fastq_dir.mkdir(parents=True, exist_ok=True)
    verification_dir.mkdir(parents=True, exist_ok=True)
    summary_path = fastq_dir / "summary.tsv"
    summary_path.write_text("metric\tvalue\nreads\t1\n", encoding="utf-8")
    evidence_files = {
        "alignment_bam": fastq_dir / "aligned.bam",
        "alignment_bai": fastq_dir / "aligned.bam.bai",
        "alignment_stats": fastq_dir / "alignment.stats.txt",
        "reference": fastq_dir / "reference.fasta",
    }
    for kind, path in evidence_files.items():
        path.write_bytes((kind + "\n").encode("utf-8"))
    verification_path = verification_dir / "verification.json"
    verification_path.write_text("{}\n", encoding="utf-8")
    fastq_manifest = {
        "artifact_schema_version": 2,
        "schema": "sequence_qc.manifest.v1",
        "workflow_id": "ont_fastq_qc",
        "job_id": str(job.id),
        "input_mode": "fastq",
        "analysis_status": "completed",
        "alignment_session": {"mode": "primary", "reference_sequence_sha256": reference_sha256},
        "reference": {"expected_sha256": reference_sha256, "name": "eGFP_plasmid", "length": 5570},
        "artifacts": [
            {
                "kind": "summary",
                "state": "present",
                "required": True,
                "integrity_valid": True,
                "path": "summary.tsv",
                "sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                "actual_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                "size_bytes": summary_path.stat().st_size,
            },
            *[
                {
                    "kind": kind,
                    "state": "present",
                    "required": True,
                    "integrity_valid": True,
                    "path": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "actual_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size_bytes": path.stat().st_size,
                }
                for kind, path in evidence_files.items()
            ],
        ],
    }
    verification_inputs = {
        role: {
            "state": "present",
            "role": role,
            "semantic_validation": {
                "status": "valid",
                "validator": "terminal-cas-fixture",
                "reason": None,
            },
        }
        for role in ("reference", "observed", "support", "alignment", "alignment_index", "alignment_stats", "topology")
    }
    verification_inputs["observed"]["independent_from_expected"] = True
    for role, kind in {
        "alignment": "alignment_bam",
        "alignment_index": "alignment_bai",
        "alignment_stats": "alignment_stats",
        "reference": "reference",
    }.items():
        evidence_path = evidence_files[kind]
        verification_inputs[role]["sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        verification_inputs[role]["size_bytes"] = evidence_path.stat().st_size
    verification_inputs["reference"]["normalized_sequence_sha256"] = reference_sha256
    verification_inputs["source_reads"] = {
        "declared_path": "source_reads.fastq.gz",
        "declared_sequence_sha256": None,
        "independent_from_expected": None,
        "normalized_sequence_sha256": None,
        "reason": None,
        "role": "source_reads",
        "semantic_validation": {
            "status": "valid",
            "validator": "terminal-cas-source-fixture",
            "reason": None,
        },
        "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "size_bytes": input_path.stat().st_size,
        "source_kind": "retained_pipeline_input",
        "state": "present",
    }
    verification_manifest = {
        "artifact_schema_version": 2,
        "schema": service.VERIFICATION_SCHEMA,
        "job_id": str(job.id),
        "execution": {"status": "SUCCEEDED", "exit_code": 0},
        "verdict": "REVIEW",
        "reason_codes": ["REVIEW_REQUIRED"],
        "threshold_profile": {
            "id": "review-only",
            "version": "1",
            "calibration_status": "experimental",
            "public_accuracy_validated": False,
            "sha256": "0" * 64,
            "values": {"automatic_pass_eligible": False},
        },
        "provenance": {
            "workflow": {
                "name": "ConstructVerify",
                "module": "modules/ngs/construct_verify.nf",
                "version": "2",
            },
            "commands": [{"argv": ["terminal-cas-fixture"]}],
        },
        "checks": {
            name: {"status": "not_evaluated", "reason_codes": [], "metrics": {}}
            for name in ("sequence_identity", "read_support", "coverage", "contamination", "topology")
        },
        "summary": {
            "coverage_fraction": 1.0,
            "observed_length": 5570,
            "reference_length": 5570,
            "reference_name": "eGFP_plasmid",
            "reference_topology": "circular",
            "sequence_identity_fraction": 1.0,
            "unmapped_fraction": 0.0,
            "variant_count": 0,
        },
        "inputs": verification_inputs,
        "artifacts": [],
    }
    job.params.update(
        {
            "ont_workflow_id": "ont_fastq_qc",
            "ont_input_mode": "fastq",
            "reference_sequence_sha256": reference_sha256,
            "fastq_path": str(input_path),
        }
    )
    job.provenance = {
        **dict(getattr(job, "provenance", {}) or {}),
        "stage_terminal_states": {
            stage: {"status": "complete", "outputs": list(outputs_by_stage[stage])}
            for stage in service._REQUIRED_TERMINAL_STAGES
        },
    }
    job.completed_stages = ["dimer_qc"]
    job.stage_outputs = {"dimer_qc": ["stale"]}
    job.status = "running"
    job.queue_status = "running"
    job.paused = False
    job.current_stage = "construct_verification"
    job.stage_progress = {"stage": "construct_verification"}
    job.error_message = None

    if production_validation:
        import json

        sequence_specs = (
            ("reference", "reference_qc.fasta", "present"),
            ("modified_bases", None, "not_applicable_to_input_mode"),
            ("reference_index", "reference_qc.fasta.fai", "present"),
            ("summary", "fastq_qc_summary.tsv", "present"),
            ("read_lengths", "read_lengths.tsv", "present"),
            ("alignment_stats", "fastq_alignment_stats.tsv", "present"),
            ("coverage", "fastq_coverage.tsv", "present"),
            ("per_base_support", "per_base_support.tsv", "present"),
            ("consensus", "fastq_consensus.fasta", "present"),
            ("consensus_index", "fastq_consensus.fasta.fai", "present"),
            ("consensus_log", "fastq_consensus.log", "present"),
            ("alignment_bam", "aligned.bam", "present"),
            ("alignment_bai", "aligned.bam.bai", "present"),
            ("igv_coverage_depth", "igv_coverage_depth.bedgraph", "present"),
            ("igv_position_gradient", "igv_position_gradient.bedgraph", "present"),
            ("igv_gc_content", "igv_gc_content.bedgraph", "present"),
            ("igv_gc_zscore", "igv_gc_zscore.bedgraph", "present"),
            ("igv_split_read_density", "igv_split_read_density.bedgraph", "present"),
            ("igv_softclip_density", "igv_softclip_density.bedgraph", "present"),
            ("igv_junction_hotspots", "igv_junction_hotspots.bed", "present"),
            ("igv_report_sites_bed", "igv_report_sites.bed", "present"),
            ("igv_report_sites_tsv", "igv_report_sites.tsv", "present"),
            ("igv_track_config", "igv_track_config.json", "present"),
            ("igv_report", "igv_report.html", "present"),
            ("log", "igv_report.log", "present"),
            ("log", "fastq_qc.log", "present"),
        )
        fastq_manifest["artifacts"] = []
        for index, (kind, name, state) in enumerate(sequence_specs):
            if name is None:
                fastq_manifest["artifacts"].append({
                    "kind": kind, "state": state, "required": False,
                    "path": None,
                    "unavailable_reason": "FASTQ input does not retain modified-base tags",
                })
                continue
            artifact_path = fastq_dir / name
            artifact_path.write_bytes(f"{index}:{kind}\n".encode())
            digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            fastq_manifest["artifacts"].append({
                "kind": kind, "state": state, "required": True,
                "integrity_valid": True, "path": name, "sha256": digest,
                "actual_sha256": digest, "size_bytes": artifact_path.stat().st_size,
            })
        for role, kind in {
            "alignment": "alignment_bam", "alignment_index": "alignment_bai",
            "alignment_stats": "alignment_stats", "reference": "reference",
        }.items():
            record = next(item for item in fastq_manifest["artifacts"] if item["kind"] == kind)
            verification_inputs[role]["sha256"] = record["sha256"]
            verification_inputs[role]["size_bytes"] = record["size_bytes"]

        verification_specs = (
            ("verification_summary", "verification_summary.tsv"),
            ("normalized_variants", "variants.vcf"),
            ("per_base_metrics", "per_base_metrics.tsv"),
            ("human_evidence_report", "evidence.html"),
            ("observed_consensus", "observed_consensus.fasta"),
        )
        verification_manifest["artifacts"] = []
        for index, (kind, name) in enumerate(verification_specs):
            artifact_path = verification_dir / name
            artifact_path.write_bytes(f"verification:{index}:{kind}\n".encode())
            digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            verification_manifest["artifacts"].append({
                "kind": kind, "state": "present", "required": True,
                "integrity_valid": True, "path": name, "sha256": digest,
                "actual_sha256": digest, "size_bytes": artifact_path.stat().st_size,
            })
        observed_dir = fastq_dir / "construct_verification_input"
        observed_dir.mkdir(parents=True, exist_ok=True)
        observed_state = {
            "schema": "biomodstack.observed_sequence_state.v1", "state": "present",
            "source_reads_path": "source_reads.fastq.gz",
            "source_reads_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        }
        (observed_dir / "observed_state.json").write_text(json.dumps(observed_state), encoding="utf-8")

        (fastq_dir / "qc_manifest.json").write_text(json.dumps(fastq_manifest), encoding="utf-8")
        (verification_dir / "qc_manifest.json").write_text(json.dumps(verification_manifest), encoding="utf-8")
        monkeypatch.setenv("BMS_RESULTS_DIR", str(result_root.parent))
        job.output_dir = str(result_root)
        return

    monkeypatch.setattr(service, "resolve_persisted_job_result_root", lambda _job: result_root)
    monkeypatch.setattr(
        service,
        "_read_manifest",
        lambda path: (b"{}", "d" * 64 if path.parent.name == "fastq_qc" else "e" * 64),
    )
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
