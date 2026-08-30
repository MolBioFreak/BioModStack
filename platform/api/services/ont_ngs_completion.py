from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping

import rfc8785

from database import Job
from services import ngs_alignment_sessions
from services.job_result_roots import resolve_persisted_job_result_root
from services.resource_usage_evidence import (
    ResourceUsageEvidenceError,
    attach_resource_usage_receipt,
)
from services.sequence_qc_manifest import (
    SequenceQcManifestError,
    VERIFICATION_SCHEMA,
    load_sequence_qc_manifest,
)

_FASTQ_QC_WORKFLOW = "ont_fastq_qc"
_EXTERNAL_SIGNAL_ALIGNMENT_WORKFLOW = "ont_plasmid_qc"
_EXTERNAL_SIGNAL_ALIGNMENT_STAGE = "dorado_align"
_EXTERNAL_SIGNAL_ALIGNMENT_OUTPUT_SUFFIXES = frozenset({
    "align/aligned.bam",
    "align/aligned.bam.bai",
    "align/reference.fasta",
    "align/reference.fasta.fai",
    "align/align.log",
    "qc_manifest.json",
})
_REQUIRED_TERMINAL_STAGES = (
    "fastq_align",
    "dimer_qc",
    "fastq_qc",
    "construct_verification",
)
_REQUIRED_STAGE_OUTPUT_SUFFIXES = {
    "fastq_align": (
        "align/aligned.bam", "align/aligned.bam.bai", "align/reference.fasta",
        "align/reference.fasta.fai", "align/fastq_align.log",
    ),
    "dimer_qc": (
        "multimer_qc/dimer_breakpoint_call.tsv", "multimer_qc/dimer_evidence_by_position.tsv",
        "multimer_qc/dimer_read_events.tsv", "multimer_qc/dimer_breakpoint_sequences.tsv",
        "multimer_qc/dimer_secondary_anomalies.tsv", "multimer_qc/dimer_secondary_summary.tsv",
    ),
    "fastq_qc": (
        "fastq_qc/read_lengths.tsv", "fastq_qc/fastq_qc_summary.tsv",
        "fastq_qc/fastq_alignment_stats.tsv", "fastq_qc/fastq_coverage.tsv",
        "fastq_qc/per_base_support.tsv", "fastq_qc/qc_manifest.json",
        "fastq_qc/igv_report.html", "fastq_qc/fastq_consensus.fasta",
    ),
    "construct_verification": (
        "verification/qc_manifest.json", "verification/verification_summary.tsv",
        "verification/variants.vcf", "verification/per_base_metrics.tsv",
        "verification/evidence.html", "verification/topology_evidence.json",
    ),
}
_MAX_MANIFEST_ARTIFACTS = 256


class OntNgsCompletionError(RuntimeError):
    """Raised when an ONT NGS result package cannot pass its terminal barrier."""


def is_ont_fastq_qc_job(job: Job) -> bool:
    params = job.params if isinstance(job.params, dict) else {}
    workflow_values = {
        str(params[key]).strip()
        for key in ("ont_workflow_id", "ont_request_workflow_id", "workflow_id")
        if params.get(key) is not None and str(params[key]).strip()
    }
    input_values = {
        str(params[key]).strip()
        for key in ("ont_input_mode", "input_mode")
        if params.get(key) is not None and str(params[key]).strip()
    }
    if _FASTQ_QC_WORKFLOW in workflow_values and workflow_values != {_FASTQ_QC_WORKFLOW}:
        raise OntNgsCompletionError("canonical FASTQ-QC workflow identities conflict")
    if "fastq" in input_values and input_values != {"fastq"}:
        raise OntNgsCompletionError("canonical FASTQ-QC input-mode identities conflict")
    return (
        str(job.model_id or "").strip().lower() == "nanopore"
        and workflow_values == {_FASTQ_QC_WORKFLOW}
        and input_values == {"fastq"}
    )


def is_ont_signal_alignment_job(job: Job) -> bool:
    """Return whether one Job is the bounded external move-BAM alignment lane."""

    params = job.params if isinstance(job.params, dict) else {}
    workflow_values = {
        str(params[key]).strip()
        for key in ("ont_workflow_id", "ont_request_workflow_id", "workflow_id")
        if params.get(key) is not None and str(params[key]).strip()
    }
    input_values = {
        str(params[key]).strip()
        for key in ("ont_input_mode", "input_mode")
        if params.get(key) is not None and str(params[key]).strip()
    }
    return (
        str(job.model_id or "").strip().lower() == "nanopore"
        and workflow_values == {_EXTERNAL_SIGNAL_ALIGNMENT_WORKFLOW}
        and input_values == {"bam"}
        and params.get("run_fastq_qc") is False
        and isinstance(params.get("source_move_source_id"), str)
        and bool(params.get("source_move_source_id"))
        and isinstance(params.get("source_external_move_registration_receipt_id"), str)
        and bool(params.get("source_external_move_registration_receipt_id"))
    )


async def validate_and_prepare_ont_signal_alignment_completion(
    job: Any,
    *,
    resource_usage_receipt: Mapping[str, Any] | None = None,
    pinned_result_root: Path | None = None,
) -> dict[str, Any]:
    """Validate and persist authority for one bounded external signal alignment."""

    persisted_result_root = resolve_persisted_job_result_root(job)
    if pinned_result_root is not None:
        return await _validate_signal_alignment_from_pinned_root(
            job,
            resource_usage_receipt=resource_usage_receipt,
            pinned_result_root=pinned_result_root,
            persisted_result_root=persisted_result_root,
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(persisted_result_root, flags)
    except OSError as exc:
        raise OntNgsCompletionError("persisted signal-alignment result root could not be pinned") from exc
    try:
        identity = os.fstat(descriptor)
        if not stat.S_ISDIR(identity.st_mode):
            raise OntNgsCompletionError("persisted signal-alignment result root is not a directory")
        return await _validate_signal_alignment_from_pinned_root(
            job,
            resource_usage_receipt=resource_usage_receipt,
            pinned_result_root=Path(f"/proc/self/fd/{descriptor}"),
            persisted_result_root=persisted_result_root,
        )
    finally:
        os.close(descriptor)


async def _validate_signal_alignment_from_pinned_root(
    job: Any,
    *,
    resource_usage_receipt: Mapping[str, Any] | None,
    pinned_result_root: Path,
    persisted_result_root: Path,
) -> dict[str, Any]:
    if not is_ont_signal_alignment_job(job):
        raise OntNgsCompletionError("job is not a bounded external signal alignment owner")
    if resource_usage_receipt is not None:
        if not isinstance(resource_usage_receipt, Mapping) or resource_usage_receipt.get("complete") is not True:
            raise OntNgsCompletionError("provided producer resource evidence is incomplete")
        try:
            job.params = attach_resource_usage_receipt(job.params, resource_usage_receipt)
        except ResourceUsageEvidenceError as exc:
            raise OntNgsCompletionError("producer resource evidence is invalid") from exc

    params = job.params if isinstance(job.params, dict) else {}
    reference_sha256 = params.get("reference_sequence_sha256")
    source_bam_path = params.get("bam_path")
    source_bam_sha256 = params.get("bam_source_sha256")
    if (
        not isinstance(reference_sha256, str)
        or len(reference_sha256) != 64
        or any(character not in "0123456789abcdef" for character in reference_sha256)
        or not isinstance(source_bam_path, str)
        or not source_bam_path
        or not isinstance(source_bam_sha256, str)
        or len(source_bam_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_bam_sha256)
    ):
        raise OntNgsCompletionError("signal-alignment source authority is invalid")
    observed_source_sha256, _observed_source_size = ngs_alignment_sessions._stable_file_identity(
        source_bam_path,
        label="persisted external move-BAM input",
    )
    if observed_source_sha256 != source_bam_sha256:
        raise OntNgsCompletionError("signal-alignment source BAM disagrees with persisted authority")

    manifest_path = pinned_result_root / "qc_manifest.json"
    manifest_raw, manifest_sha256 = _read_manifest(manifest_path)
    manifest = load_sequence_qc_manifest(
        manifest_path,
        raw_bytes=manifest_raw,
        expected_job_id=str(job.id),
        expected_workflow_id=_EXTERNAL_SIGNAL_ALIGNMENT_WORKFLOW,
        expected_input_mode="bam",
        expected_analysis_status="completed",
    )
    alignment_session = manifest.get("alignment_session")
    if (
        not isinstance(alignment_session, dict)
        or alignment_session.get("mode") != "primary"
        or alignment_session.get("reference_sequence_sha256") != reference_sha256
        or alignment_session.get("source_reference_sequence_sha256") != reference_sha256
    ):
        raise OntNgsCompletionError("primary signal-alignment manifest authority is invalid")

    provenance = job.provenance if isinstance(job.provenance, dict) else {}
    terminal_states = provenance.get("stage_terminal_states")
    terminal = terminal_states.get(_EXTERNAL_SIGNAL_ALIGNMENT_STAGE) if isinstance(terminal_states, dict) else None
    outputs = terminal.get("outputs") if isinstance(terminal, dict) else None
    if terminal is None or terminal.get("status") != "complete" or not isinstance(outputs, list):
        raise OntNgsCompletionError("signal-alignment terminal stage authority is missing")
    observed_suffixes: set[str] = set()
    for value in outputs:
        if not isinstance(value, str) or not value:
            raise OntNgsCompletionError("signal-alignment terminal output authority is invalid")
        _path, suffix = _resolve_terminal_output(
            value,
            pinned_result_root,
            persisted_result_root,
            stage=_EXTERNAL_SIGNAL_ALIGNMENT_STAGE,
        )
        observed_suffixes.add(suffix)
    if observed_suffixes != _EXTERNAL_SIGNAL_ALIGNMENT_OUTPUT_SUFFIXES or len(outputs) != len(observed_suffixes):
        raise OntNgsCompletionError("signal-alignment terminal output contract mismatch")

    from starlette.concurrency import run_in_threadpool

    descriptors = await run_in_threadpool(
        ngs_alignment_sessions.build_ngs_package_artifacts,
        str(job.id),
        source_reference_sha256=reference_sha256,
        workflow_id=_EXTERNAL_SIGNAL_ALIGNMENT_WORKFLOW,
        input_mode="bam",
        source_input_path=source_bam_path,
        job_output_dir=pinned_result_root,
        pinned_root_descriptor=True,
    )
    package_authority = canonical_ngs_package_authority(descriptors)
    if (
        package_authority["declared_artifact_count"] != 5
        or package_authority["present_artifact_count"] != 5
        or package_authority["unavailable_artifact_count"] != 0
    ):
        raise OntNgsCompletionError("signal-alignment package artifact denominator is not canonical")
    sessions = await run_in_threadpool(
        ngs_alignment_sessions.build_alignment_sessions,
        str(job.id),
        source_reference_sha256=reference_sha256,
        package_artifact_set_sha256=package_authority["artifact_set_sha256"],
        workflow_id=_EXTERNAL_SIGNAL_ALIGNMENT_WORKFLOW,
        input_mode="bam",
        job_output_dir=pinned_result_root,
        pinned_root_descriptor=True,
    )
    primary_sessions = [item for item in sessions if item.get("mode") == "primary" and item.get("ready") is True]
    if len(primary_sessions) != 1:
        raise OntNgsCompletionError("exactly one ready primary signal-alignment session is required")
    try:
        for ready_session in (item for item in sessions if item.get("ready") is True):
            alignment_path, alignment_metadata, index_path, index_metadata = await run_in_threadpool(
                ngs_alignment_sessions.resolve_session_alignment_bundle,
                str(job.id),
                ready_session["session_id"],
                source_reference_sha256=reference_sha256,
                workflow_id=_EXTERNAL_SIGNAL_ALIGNMENT_WORKFLOW,
                input_mode="bam",
                job_output_dir=pinned_result_root,
                pinned_root_descriptor=True,
            )
            await run_in_threadpool(
                ngs_alignment_sessions.build_alignment_presentation,
                alignment_path,
                bam_sha256=alignment_metadata["sha256"],
                bam_size_bytes=alignment_metadata["size_bytes"],
                index=index_path,
                index_sha256=index_metadata["sha256"],
                index_size_bytes=index_metadata["size_bytes"],
                source_manifest_sha256=alignment_metadata["source_manifest_sha256"],
                job_id=str(job.id),
                session_id=ready_session["session_id"],
                mode=ready_session["mode"],
                cache_root=persisted_result_root / ".alignment-presentations",
                artifact_set_sha256=ready_session["artifact_set_sha256"],
                alignment_pair_sha256=ready_session["alignment_pair_sha256"],
            )
    except ngs_alignment_sessions.AlignmentSessionError as exc:
        raise OntNgsCompletionError(f"alignment presentation materialization failed: {exc}") from exc

    result_integrity = {
        "state": "validated",
        "partial": False,
        "result_kind": "ngs_alignment_session",
        "workflow_id": _EXTERNAL_SIGNAL_ALIGNMENT_WORKFLOW,
        "input_mode": "bam",
        "reference_sequence_sha256": reference_sha256,
        "source_bam_sha256": source_bam_sha256,
        "sequence_qc_manifest_sha256": manifest_sha256,
        **package_authority,
    }
    if resource_usage_receipt is not None:
        result_integrity["resource_evidence_status"] = "accepted"
        result_integrity["resource_usage_receipt_sha256"] = resource_usage_receipt.get("receipt_sha256")
    updated_provenance = dict(provenance)
    updated_provenance["result_integrity"] = result_integrity
    job.provenance = updated_provenance
    job.completed_stages = [_EXTERNAL_SIGNAL_ALIGNMENT_STAGE]
    job.stage_outputs = {_EXTERNAL_SIGNAL_ALIGNMENT_STAGE: list(outputs)}
    job.status = "completed"
    job.queue_status = "completed"
    job.paused = False
    job.current_stage = "Complete"
    job.stage_progress = None
    job.error_message = None
    return result_integrity


def _read_manifest(path: Path) -> tuple[bytes, str]:
    try:
        handle = ngs_alignment_sessions._open_regular_file_no_symlinks(path)
        try:
            size_bytes = handle.seek(0, 2)
            handle.seek(0)
            if size_bytes < 2 or size_bytes > 10 * 1024 * 1024:
                raise OntNgsCompletionError(f"required NGS manifest size is invalid: {path.name}")
            raw = handle.read(size_bytes + 1)
            if len(raw) != size_bytes:
                raise OntNgsCompletionError(f"required NGS manifest changed while it was read: {path.name}")
        finally:
            handle.close()
    except OSError as exc:
        raise OntNgsCompletionError(f"required NGS manifest is unavailable: {path.name}") from exc
    return raw, hashlib.sha256(raw).hexdigest()


def _resolve_terminal_output(
    declared_value: str,
    pinned_result_root: Path,
    persisted_result_root: Path,
    *,
    stage: str,
) -> tuple[Path, str]:
    declared = Path(declared_value).expanduser()
    if any(part in {"", ".", ".."} for part in declared.parts):
        raise OntNgsCompletionError(f"required NGS stage output has an unsafe path: {stage}")

    if declared.is_absolute():
        try:
            relative = declared.relative_to(persisted_result_root)
        except ValueError as exc:
            raise OntNgsCompletionError(f"required NGS stage output escapes the job result root: {stage}") from exc
    else:
        parts = declared.parts
        if len(parts) < 3 or parts[0] != "bms_results" or parts[1] != persisted_result_root.name:
            raise OntNgsCompletionError(f"required NGS stage output names a different result root: {stage}")
        relative = Path(*parts[2:])
    candidate = pinned_result_root.joinpath(*relative.parts)
    try:
        handle = ngs_alignment_sessions._open_regular_file_no_symlinks(candidate)
        handle.close()
    except (OSError, ngs_alignment_sessions.AlignmentSessionError) as exc:
        raise OntNgsCompletionError(f"required NGS stage output is not a regular file: {stage}") from exc
    return candidate, relative.as_posix()


def _validate_terminal_stages(
    job: Job,
    pinned_result_root: Path,
    persisted_result_root: Path,
) -> tuple[list[str], dict[str, list[str]]]:
    provenance = job.provenance if isinstance(job.provenance, dict) else {}
    terminal_states = provenance.get("stage_terminal_states")
    if not isinstance(terminal_states, dict):
        raise OntNgsCompletionError("NGS terminal stage authority is missing")

    stage_outputs: dict[str, list[str]] = {}
    observed_paths: set[str] = set()
    for stage in _REQUIRED_TERMINAL_STAGES:
        terminal = terminal_states.get(stage)
        if not isinstance(terminal, dict) or terminal.get("status") != "complete":
            raise OntNgsCompletionError(f"required NGS stage is not complete: {stage}")
        outputs = terminal.get("outputs")
        if not isinstance(outputs, list) or not outputs or not all(isinstance(item, str) and item for item in outputs):
            raise OntNgsCompletionError(f"required NGS stage has no authoritative outputs: {stage}")
        observed_suffixes: list[str] = []
        for item in outputs:
            _path, suffix = _resolve_terminal_output(
                item, pinned_result_root, persisted_result_root, stage=stage,
            )
            if suffix in observed_paths:
                raise OntNgsCompletionError("required NGS stage output is duplicated across stages")
            observed_paths.add(suffix)
            observed_suffixes.append(suffix)
        if tuple(observed_suffixes) != _REQUIRED_STAGE_OUTPUT_SUFFIXES[stage]:
            raise OntNgsCompletionError(f"required NGS stage output contract mismatch: {stage}")
        stage_outputs[stage] = list(outputs)
    return list(_REQUIRED_TERMINAL_STAGES), stage_outputs


def canonical_ngs_package_authority(descriptors: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the order-invariant authority for one complete governed package inventory."""

    if len(descriptors) > _MAX_MANIFEST_ARTIFACTS:
        raise OntNgsCompletionError("NGS package artifact inventory exceeds its bound")
    unavailable_states = {
        "missing_required",
        "missing_optional",
        "not_applicable",
        "not_produced",
        "not_applicable_to_input_mode",
        "unavailable",
    }
    records: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str, str | None, int | None]] = set()
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            raise OntNgsCompletionError("NGS package contains a malformed artifact descriptor")
        source = descriptor.get("source")
        kind = descriptor.get("kind")
        state = descriptor.get("state")
        digest = descriptor.get("sha256")
        size_bytes = descriptor.get("size_bytes")
        if not isinstance(source, str) or not source or not isinstance(kind, str) or not kind:
            raise OntNgsCompletionError("NGS package artifact identity is invalid")
        if state == "present":
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or isinstance(size_bytes, bool)
                or not isinstance(size_bytes, int)
                or size_bytes < 0
            ):
                raise OntNgsCompletionError("NGS package present artifact integrity is invalid")
        elif state in unavailable_states:
            if digest is not None or size_bytes is not None:
                raise OntNgsCompletionError("NGS package unavailable artifact carries false integrity")
        else:
            raise OntNgsCompletionError("NGS package artifact state is invalid")
        identity = (source, kind, state, digest, size_bytes)
        if identity in identities:
            raise OntNgsCompletionError("NGS package contains a duplicate artifact descriptor")
        identities.add(identity)
        records.append({
            "source": source,
            "kind": kind,
            "state": state,
            "sha256": digest,
            "size_bytes": size_bytes,
        })

    records.sort(key=lambda item: (
        item["source"].encode("utf-8"),
        item["kind"].encode("utf-8"),
        item["state"].encode("utf-8"),
        (item["sha256"] or "").encode("utf-8"),
        ("" if item["size_bytes"] is None else str(item["size_bytes"])).encode("utf-8"),
    ))
    canonical = rfc8785.dumps({"schema": "bms.ngs.package-authority.v1", "records": records})
    present_count = sum(record["state"] == "present" for record in records)
    return {
        "artifact_set_sha256": hashlib.sha256(canonical).hexdigest(),
        "declared_artifact_count": len(records),
        "present_artifact_count": present_count,
        "unavailable_artifact_count": len(records) - present_count,
    }


async def validate_and_prepare_ont_fastq_qc_completion(
    job: Any,
    *,
    resource_usage_receipt: Mapping[str, Any] | None = None,
    historical_reconciliation: bool = False,
    pinned_result_root: Path | None = None,
) -> dict[str, Any]:
    """Pin the result-root inode for the full terminal validation interval."""

    persisted_result_root = resolve_persisted_job_result_root(job)
    if pinned_result_root is not None:
        return await _validate_and_prepare_from_pinned_root(
            job,
            resource_usage_receipt=resource_usage_receipt,
            historical_reconciliation=historical_reconciliation,
            pinned_result_root=pinned_result_root,
            persisted_result_root=persisted_result_root,
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(persisted_result_root, flags)
    except OSError as exc:
        raise OntNgsCompletionError("persisted result root could not be pinned") from exc
    try:
        identity = os.fstat(descriptor)
        if not stat.S_ISDIR(identity.st_mode):
            raise OntNgsCompletionError("persisted result root is not a directory")
        return await _validate_and_prepare_from_pinned_root(
            job,
            resource_usage_receipt=resource_usage_receipt,
            historical_reconciliation=historical_reconciliation,
            pinned_result_root=Path(f"/proc/self/fd/{descriptor}"),
            persisted_result_root=persisted_result_root,
        )
    finally:
        os.close(descriptor)


async def _validate_and_prepare_from_pinned_root(
    job: Any,
    *,
    resource_usage_receipt: Mapping[str, Any] | None = None,
    historical_reconciliation: bool = False,
    pinned_result_root: Path,
    persisted_result_root: Path,
) -> dict[str, Any]:
    """Validate and stage one ONT FASTQ-QC terminal generation without committing it."""

    if not is_ont_fastq_qc_job(job):
        raise OntNgsCompletionError("job is not an ONT FASTQ-QC result owner")
    if historical_reconciliation:
        if resource_usage_receipt is not None:
            raise OntNgsCompletionError("historical reconciliation cannot attach producer resource evidence")
        resource_authority: dict[str, Any] = {
            "resource_evidence_status": "historical_unavailable",
        }
    else:
        if not isinstance(resource_usage_receipt, Mapping) or resource_usage_receipt.get("complete") is not True:
            raise OntNgsCompletionError("complete producer resource evidence is required before ONT success")
        try:
            job.params = attach_resource_usage_receipt(job.params, resource_usage_receipt)
        except ResourceUsageEvidenceError as exc:
            raise OntNgsCompletionError("producer resource evidence is invalid") from exc
        resource_authority = {
            "resource_evidence_status": "accepted",
            "resource_usage_receipt_sha256": resource_usage_receipt.get("receipt_sha256"),
        }
    result_root = pinned_result_root
    fastq_manifest_path = result_root / "fastq_qc" / "qc_manifest.json"
    verification_manifest_path = result_root / "verification" / "qc_manifest.json"

    fastq_raw, fastq_digest = _read_manifest(fastq_manifest_path)
    fastq_manifest = load_sequence_qc_manifest(
        fastq_manifest_path,
        raw_bytes=fastq_raw,
        expected_job_id=str(job.id),
        expected_workflow_id=_FASTQ_QC_WORKFLOW,
        expected_input_mode="fastq",
        expected_analysis_status="completed",
    )
    verification_raw, verification_digest = _read_manifest(verification_manifest_path)
    verification_manifest = load_sequence_qc_manifest(
        verification_manifest_path,
        raw_bytes=verification_raw,
    )
    if verification_manifest.get("schema") != VERIFICATION_SCHEMA:
        raise SequenceQcManifestError("construct verification manifest schema is invalid")

    params = job.params if isinstance(job.params, dict) else {}
    fastq_reference = fastq_manifest.get("reference")
    verification_summary = verification_manifest.get("summary")
    source_input_path = params.get("fastq_path")
    if not isinstance(fastq_reference, dict) or not isinstance(verification_summary, dict):
        raise OntNgsCompletionError("NGS result reference authority is incomplete")
    if (
        fastq_reference.get("expected_sha256") != params.get("reference_sequence_sha256")
        or not isinstance(source_input_path, str)
        or not source_input_path
    ):
        raise OntNgsCompletionError("NGS result authority disagrees with persisted job authority")
    if (
        verification_summary.get("reference_name") != fastq_reference.get("name")
        or verification_summary.get("reference_length") != fastq_reference.get("length")
    ):
        raise OntNgsCompletionError("FASTQ-QC and verification reference identities disagree")
    verification_inputs = verification_manifest.get("inputs")
    source_reads = verification_inputs.get("source_reads") if isinstance(verification_inputs, dict) else None
    source_fastq_sha256 = source_reads.get("sha256") if isinstance(source_reads, dict) else None
    if (
        not isinstance(source_fastq_sha256, str)
        or len(source_fastq_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_fastq_sha256)
    ):
        raise OntNgsCompletionError("construct verification source FASTQ authority is invalid")

    from starlette.concurrency import run_in_threadpool

    package_artifacts = await run_in_threadpool(
        ngs_alignment_sessions.build_ngs_package_artifacts,
        str(job.id),
        source_reference_sha256=str(fastq_reference["expected_sha256"]),
        workflow_id=_FASTQ_QC_WORKFLOW,
        input_mode="fastq",
        source_input_path=source_input_path,
        job_output_dir=result_root,
        pinned_root_descriptor=True,
    )
    package_authority = canonical_ngs_package_authority(package_artifacts)
    if (
        package_authority["declared_artifact_count"] != 36
        or package_authority["present_artifact_count"] != 34
        or package_authority["unavailable_artifact_count"] != 2
    ):
        raise OntNgsCompletionError("NGS package artifact denominator is not canonical")

    completed_stages, stage_outputs = _validate_terminal_stages(job, result_root, persisted_result_root)

    provenance = dict(job.provenance) if isinstance(job.provenance, dict) else {}
    provenance["result_integrity"] = {
        "state": "validated",
        "partial": False,
        "result_kind": "ngs_sequence_qc",
        "workflow_id": _FASTQ_QC_WORKFLOW,
        "input_mode": "fastq",
        "reference_sequence_sha256": fastq_reference["expected_sha256"],
        "source_fastq_sha256": source_fastq_sha256,
        "sequence_qc_manifest_sha256": fastq_digest,
        "construct_verification_manifest_sha256": verification_digest,
        "construct_verification_verdict": verification_manifest.get("verdict"),
        **resource_authority,
        **package_authority,
    }
    job.provenance = provenance
    job.completed_stages = completed_stages
    job.stage_outputs = stage_outputs
    job.status = "completed"
    job.queue_status = "completed"
    job.paused = False
    job.current_stage = "Complete"
    job.stage_progress = None
    job.error_message = None
    return provenance["result_integrity"]
