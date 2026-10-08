"""Typed parser and discovery helpers for sequence-QC artifact manifests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from paths import get_results_dir

MANIFEST_FILENAME = "qc_manifest.json"
MANIFEST_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 10 * 1024 * 1024
SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,255}$")

SCHEMA_BY_KIND = {
    "summary": "sequence_qc.summary.v1",
    "alignment_stats": "sequence_qc.alignment_stats.v1",
    "coverage": "sequence_qc.coverage_depth.v1",
    "raw_reads": "sequence_qc.raw_reads.v1",
    "basecall_reads": "sequence_qc.basecall_reads.v1",
    "read_qc_summary": "sequence_qc.read_qc_summary.v1",
    "per_base_support": "sequence_qc.per_base_support.v1",
    "consensus": "sequence_qc.consensus_fasta.v1",
    "consensus_index": "sequence_qc.fasta_index.v1",
    "log": "sequence_qc.log.v1",
    "alignment_bam": "sequence_qc.alignment_bam.v1",
    "alignment_bai": "sequence_qc.alignment_index.v1",
    "reference": "sequence_qc.reference_fasta.v1",
    "reference_index": "sequence_qc.fasta_index.v1",
    "igv_track_config": "sequence_qc.igv_track_config.v1",
    "igv_report": "sequence_qc.igv_report.v1",
    "igv_track": "sequence_qc.igv_track.v1",
    "modified_bases": "sequence_qc.modified_bases.v1",
    "modkit_summary": "sequence_qc.modkit_summary.v1",
    "methylation_bed": "sequence_qc.methylation_bed.v1",
    "plasmid_qc_summary": "sequence_qc.plasmid_qc_summary.v1",
    "construct_screening_summary": "sequence_qc.construct_screening_summary.v1",
    "clone_validation_assembly": "sequence_qc.clone_validation_assembly.v1",
    "clone_validation_report": "sequence_qc.clone_validation_report.v1",
}

ARTIFACT_STATES = {
    "present",
    "not_requested",
    "not_applicable_to_input_mode",
    "failed",
    "missing_after_workflow",
    "legacy_unavailable",
    "missing_optional",
    "missing_required",
}

PATHLESS_ARTIFACT_STATES = {
    "not_requested",
    "not_applicable_to_input_mode",
    "failed",
    "legacy_unavailable",
}


class SequenceQcManifestError(ValueError):
    """Raised when a sequence-QC manifest is malformed or unsafe."""


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SequenceQcManifestError(f"manifest not found: {path}")
    if not path.is_file():
        raise SequenceQcManifestError(f"manifest path is not a file: {path}")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise SequenceQcManifestError(f"manifest too large: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SequenceQcManifestError(f"manifest is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise SequenceQcManifestError("manifest root must be a JSON object")
    return payload


def _resolve_manifest_relative_path(manifest_dir: Path, raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise SequenceQcManifestError("artifact path must be a non-empty string")
    artifact_path = Path(raw_path)
    if artifact_path.is_absolute():
        raise SequenceQcManifestError(f"artifact path must be relative to manifest: {raw_path}")
    candidate = (manifest_dir / artifact_path).resolve()
    manifest_root = manifest_dir.resolve()
    try:
        candidate.relative_to(manifest_root)
    except ValueError as exc:
        raise SequenceQcManifestError(f"artifact path escapes manifest directory: {raw_path}") from exc
    return candidate


def _normalize_artifact_state(artifact: dict[str, Any], *, exists: bool, required: bool, has_path: bool) -> str:
    raw_state = artifact.get("state")
    if raw_state is not None:
        state = str(raw_state).strip().lower()
        if state not in ARTIFACT_STATES:
            raise SequenceQcManifestError(f"unsupported artifact state: {raw_state!r}")
    else:
        state = ""

    if exists:
        return "present"
    if required:
        return state or "missing_required"
    if not has_path:
        if not state:
            raise SequenceQcManifestError("pathless artifact must declare an explicit unavailable state")
        if state not in PATHLESS_ARTIFACT_STATES:
            raise SequenceQcManifestError(f"pathless artifact cannot use state: {state}")
        return state
    return state or "missing_optional"


def _normalize_artifacts(manifest_dir: Path, artifacts: object) -> list[dict[str, Any]]:
    if artifacts is None:
        return []
    if not isinstance(artifacts, list):
        raise SequenceQcManifestError("artifacts must be a list")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(artifacts):
        if not isinstance(item, dict):
            raise SequenceQcManifestError(f"artifact {index} must be an object")

        artifact = dict(item)
        kind = str(artifact.get("kind") or "artifact")
        required = bool(artifact.get("required", False))
        raw_path = artifact.get("path")
        has_path = isinstance(raw_path, str) and bool(raw_path.strip())
        full_path: Path | None = None
        exists = False
        declared_path: str | None = None

        if has_path:
            declared_path = str(Path(str(raw_path)).as_posix())
            full_path = _resolve_manifest_relative_path(manifest_dir, raw_path)
            exists = full_path.exists()
        elif required:
            raise SequenceQcManifestError(f"required artifact missing path: {kind}")

        state = _normalize_artifact_state(artifact, exists=exists, required=required, has_path=has_path)

        if required and not exists:
            raise SequenceQcManifestError(f"required artifact missing: {raw_path or kind}")

        artifact["kind"] = kind
        artifact["required"] = required
        artifact["declared_path"] = declared_path
        artifact["path"] = declared_path if exists else None
        artifact["exists"] = exists
        artifact["state"] = state
        artifact["size_bytes"] = full_path.stat().st_size if full_path is not None and exists and full_path.is_file() else None
        artifact.setdefault("schema", SCHEMA_BY_KIND.get(kind, "sequence_qc.artifact.v1"))
        if state == "missing_after_workflow" and not artifact.get("missing_reason"):
            artifact["missing_reason"] = f"artifact path not found after workflow: {declared_path or kind}"
        elif state == "missing_optional" and not artifact.get("missing_reason"):
            artifact["missing_reason"] = f"optional artifact not present: {declared_path or kind}"
        elif state in PATHLESS_ARTIFACT_STATES and not artifact.get("unavailable_reason"):
            artifact["unavailable_reason"] = f"artifact unavailable: {state}"
        normalized.append(artifact)

    return normalized


def _normalize_top_level_path_sections(manifest_dir: Path, manifest: dict[str, Any]) -> None:
    for section_name in ("reference", "consensus"):
        section = manifest.get(section_name)
        if not isinstance(section, dict):
            continue
        raw_path = section.get("path")
        if raw_path is None or raw_path == "":
            section["path"] = None
            manifest[section_name] = section
            continue
        try:
            full_path = _resolve_manifest_relative_path(manifest_dir, raw_path)
        except SequenceQcManifestError as exc:
            message = str(exc).replace("artifact path", f"{section_name} path")
            raise SequenceQcManifestError(message) from exc
        declared_path = str(Path(str(raw_path)).as_posix())
        exists = full_path.exists()
        section["declared_path"] = declared_path
        section["path"] = declared_path if exists else None
        section["exists"] = exists
        section["size_bytes"] = full_path.stat().st_size if exists and full_path.is_file() else None
        manifest[section_name] = section


def _normalize_consensus_and_interpretation(manifest: dict[str, Any]) -> None:
    consensus = manifest.get("consensus")
    if not isinstance(consensus, dict):
        return

    status = str(consensus.get("status") or "").strip().lower()
    method = str(consensus.get("method") or "").strip().lower()
    fallback = bool(consensus.get("fallback", False)) or status == "reference_copy_fallback" or method == "reference_copy_fallback"
    consensus["fallback"] = fallback
    manifest["consensus"] = consensus

    if not fallback:
        return

    interpretation = manifest.get("interpretation")
    if not isinstance(interpretation, dict):
        interpretation = {}
    interpretation["verified_construct_status"] = "fail"
    notes = interpretation.get("notes")
    if not isinstance(notes, list):
        notes = []
    note = "reference-copy fallback consensus is not verified"
    if note not in notes:
        notes.append(note)
    interpretation["notes"] = notes
    manifest["interpretation"] = interpretation


def load_sequence_qc_manifest(manifest_path: str | Path) -> dict[str, Any]:
    """Load and normalize a sequence-QC manifest without reading large artifacts."""
    path = Path(manifest_path).expanduser().resolve()
    payload = _read_json_object(path)

    version = payload.get("artifact_schema_version")
    if version != MANIFEST_SCHEMA_VERSION:
        raise SequenceQcManifestError(
            f"unsupported artifact_schema_version {version!r}; expected {MANIFEST_SCHEMA_VERSION}"
        )

    manifest = dict(payload)
    manifest.pop("manifest_path", None)
    manifest.pop("manifest_dir", None)
    _normalize_top_level_path_sections(path.parent, manifest)
    manifest["artifacts"] = _normalize_artifacts(path.parent, manifest.get("artifacts", []))
    _normalize_consensus_and_interpretation(manifest)
    return manifest


def _validate_job_id(job_id: str) -> str:
    normalized = job_id.strip()
    if (
        not normalized
        or "/" in normalized
        or "\\" in normalized
        or ".." in normalized
        or not SAFE_JOB_ID_RE.match(normalized)
    ):
        raise SequenceQcManifestError(f"unsafe job_id: {job_id!r}")
    return normalized


def find_manifest_for_job(job_id: str, *, results_dir: str | Path | None = None) -> Path:
    """Find the first sequence-QC manifest for a BMS result directory."""
    safe_job_id = _validate_job_id(job_id)
    root = Path(results_dir) if results_dir is not None else get_results_dir()
    job_dir = (root / safe_job_id).resolve()
    root_resolved = root.resolve()
    try:
        job_dir.relative_to(root_resolved)
    except ValueError as exc:
        raise SequenceQcManifestError(f"job directory escapes results root: {job_id!r}") from exc

    candidates = [
        job_dir / "fastq_qc" / MANIFEST_FILENAME,
        job_dir / MANIFEST_FILENAME,
    ]
    candidates.extend(sorted(job_dir.glob(f"**/{MANIFEST_FILENAME}"))) if job_dir.exists() else None

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not (resolved.exists() and resolved.is_file()):
            continue
        try:
            resolved.relative_to(job_dir)
            resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise SequenceQcManifestError(f"manifest path escapes job/results root: {candidate}") from exc
        return resolved

    raise SequenceQcManifestError(f"sequence-QC manifest not found for job_id: {safe_job_id}")
