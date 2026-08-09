"""Read-only projection of revision-bound NGS verification evidence."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from paths import get_results_dir

COMPARISON_SUMMARY_SCHEMA = "bms.ngs.comparison-attribution-summary.v1"
OCCURRENCE_MAP_SCHEMA = "bms.ngs.comparison-panel-occurrence-map.v1"
CATEGORY_KEYS = (
    "expected_plasmid_unique",
    "panel_reference_unique",
    "ambiguous_multimapping",
    "unclassified",
)
ROLE_KEYS = ("intended", "host", "plasmid_decoy", "ambiguous", "unclassified")
ARTIFACT_KINDS = (
    "comparison_panel_alignment_bam",
    "comparison_panel_alignment_bai",
    "comparison_panel_occurrence_map",
)
EXPECTED_REFERENCE_ID = "expected_plasmid"
VALID_PANEL_ROLES = frozenset({"host", "plasmid_decoy"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def safe_job_result_root(job: Any) -> Path:
    """Resolve only the declared job-owned root below the configured results root."""
    raw = getattr(job, "child_output_dir", None) or getattr(job, "output_dir", None)
    root = get_results_dir().expanduser().resolve()
    candidate = Path(str(raw or "")).expanduser()
    declared = candidate if candidate.is_absolute() else root / candidate
    if declared.is_symlink():
        raise ValueError("job result root is an unsafe symlink")
    resolved = declared.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("job result root is unavailable")
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("job result root is outside configured results") from exc
    return resolved


def safe_comparison_panel_root(job_root: Path) -> Path:
    """Resolve the published comparison directory without following symlinks."""
    base = job_root.resolve(strict=True)
    if job_root.is_symlink() or not base.is_dir():
        raise ValueError("job result root is an unsafe symlink")
    candidate = base / "comparison_panel"
    if candidate.is_symlink():
        raise ValueError("comparison panel root is an unsafe symlink")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("comparison panel root is unavailable")
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError("comparison panel root escapes the job result root") from exc
    return resolved


def _safe_comparison_file(root: Path, raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("comparison evidence path is missing")
    relative = Path(raw_path)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("comparison evidence paths must be simple relative paths")
    base = root.resolve(strict=True)
    if root.is_symlink() or not base.is_dir():
        raise ValueError("comparison panel root is an unsafe symlink")
    current = base
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("comparison evidence paths must not use symlinks")
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError("comparison evidence path escapes the comparison panel root") from exc
    if not resolved.is_file():
        raise ValueError("comparison evidence file is unavailable")
    return resolved


def validate_workup_manifest(manifest: dict[str, Any], binding: dict[str, Any]) -> None:
    if manifest.get("schema") != "biomodstack.construct_verification.v2" or manifest.get("artifact_schema_version") != 2:
        raise ValueError("workup requires the exact construct-verification manifest schema")
    expected = binding.get("reference_snapshot_sha256")
    artifacts = manifest.get("artifacts")
    reference = next((item for item in artifacts if isinstance(item, dict) and item.get("kind") == "reference"), None) if isinstance(artifacts, list) else None
    if not _valid_sha256(expected) or not isinstance(reference, dict) or reference.get("sha256") != expected:
        raise ValueError("workup manifest reference digest does not match the receipt snapshot")


def _strict_count(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} is malformed")
    return value


def _panel_binding(binding: dict[str, Any]) -> dict[str, Any]:
    panel = binding.get("comparison_panel_binding")
    if not isinstance(panel, dict):
        raise ValueError("comparison panel receipt binding is missing")
    if not isinstance(panel.get("panel_id"), str) or not panel["panel_id"]:
        raise ValueError("comparison panel receipt identity is malformed")
    if not isinstance(panel.get("panel_version"), int) or isinstance(panel["panel_version"], bool) or panel["panel_version"] < 1:
        raise ValueError("comparison panel receipt version is malformed")
    if not _valid_sha256(panel.get("panel_snapshot_sha256")):
        raise ValueError("comparison panel receipt digest is malformed")
    if not isinstance(panel.get("receipt_id"), str) or not panel["receipt_id"]:
        raise ValueError("comparison panel receipt id is malformed")
    return panel


def _declared_summary_digest(params: dict[str, Any]) -> str | None:
    declared: list[object] = []
    for key in ("comparison_panel_summary_sha256", "comparison_panel_summary_digest"):
        if key in params:
            declared.append(params[key])
    binding = params.get("comparison_panel_binding")
    if isinstance(binding, dict):
        for key in ("summary_sha256", "summary_digest"):
            if key in binding:
                declared.append(binding[key])
    if not declared:
        return None
    if any(not _valid_sha256(value) for value in declared) or len(set(declared)) != 1:
        raise ValueError("declared comparison summary digest is malformed or conflicting")
    return str(declared[0])


def _load_occurrence_map(path: Path, *, source_sha256: str, normalized_sha256: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("comparison occurrence map is unavailable or malformed") from exc
    if not isinstance(payload, dict) or payload.get("schema") != OCCURRENCE_MAP_SCHEMA:
        raise ValueError("comparison occurrence map has an unsupported schema")
    if payload.get("source_fastq_sha256") != source_sha256 or payload.get("normalized_fastq_sha256") != normalized_sha256:
        raise ValueError("comparison occurrence map digest binding is invalid")
    count = _strict_count(payload.get("input_read_count"), label="comparison occurrence map input count")
    occurrences = payload.get("occurrences")
    if not isinstance(occurrences, list) or len(occurrences) != count:
        raise ValueError("comparison occurrence map count mismatch")
    by_occurrence: dict[str, dict[str, Any]] = {}
    for ordinal, row in enumerate(occurrences, start=1):
        if not isinstance(row, dict) or set(row) != {"occurrence_id", "read_id", "ordinal"}:
            raise ValueError("comparison occurrence map row is malformed")
        occurrence_id = row.get("occurrence_id")
        original_read_id = row.get("read_id")
        if (
            not isinstance(occurrence_id, str)
            or not occurrence_id
            or not isinstance(original_read_id, str)
            or not original_read_id
            or row.get("ordinal") != ordinal
            or occurrence_id != f"bms_occurrence_{ordinal:012d}"
            or occurrence_id in by_occurrence
        ):
            raise ValueError("comparison occurrence map ordinals or IDs are not unique")
        by_occurrence[occurrence_id] = row
    return {"input_read_count": count, "occurrences": occurrences, "by_occurrence": by_occurrence}


def _validate_comparison_summary(
    manifest: dict[str, Any],
    binding: dict[str, Any],
    summary: dict[str, Any],
    *,
    comparison_panel_root: Path,
    comparison_summary_path: Path | None,
) -> dict[str, Any]:
    panel_binding = _panel_binding(binding)
    revision_binding = binding.get("molbio_revision_binding")
    if not isinstance(revision_binding, dict):
        raise ValueError("molecular revision binding is missing")
    if not isinstance(summary, dict) or summary.get("schema") != COMPARISON_SUMMARY_SCHEMA or summary.get("status") != "review_required":
        raise ValueError("comparison summary has an unsupported schema or status")

    expected_summary_path = _safe_comparison_file(comparison_panel_root, "comparison_panel_summary.json")
    if comparison_summary_path is not None:
        if comparison_summary_path.resolve(strict=True) != expected_summary_path:
            raise ValueError("comparison summary path is outside the comparison panel root")
        declared_digest = _declared_summary_digest(binding)
        if declared_digest is not None and _sha256_file(expected_summary_path) != declared_digest:
            raise ValueError("comparison summary digest does not match the job receipt")
    else:
        declared_digest = _declared_summary_digest(binding)
        if declared_digest is not None:
            raise ValueError("declared comparison summary digest cannot be verified")
    try:
        on_disk_summary = json.loads(expected_summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("comparison summary is unavailable or malformed") from exc
    if on_disk_summary != summary:
        raise ValueError("comparison summary object does not match its published file")

    reference = summary.get("reference")
    if not isinstance(reference, dict) or reference.get("id") != EXPECTED_REFERENCE_ID or reference.get("role") != "intended":
        raise ValueError("comparison summary expected-reference role is invalid")
    if not _valid_sha256(reference.get("source_file_sha256")) or reference.get("source_file_sha256") != revision_binding.get("reference_snapshot_sha256"):
        raise ValueError("comparison summary expected-reference digest is invalid")
    reference_path = _safe_comparison_file(comparison_panel_root, reference.get("path"))
    if _sha256_file(reference_path) != reference.get("sha256") or reference_path.stat().st_size != _strict_count(reference.get("size_bytes"), label="reference artifact size"):
        raise ValueError("comparison expected-reference artifact mismatch")
    if reference.get("sha256") != reference.get("source_file_sha256"):
        raise ValueError("comparison expected-reference source digest mismatch")

    source = summary.get("source_fastq")
    if not isinstance(source, dict) or not _valid_sha256(source.get("sha256")):
        raise ValueError("comparison source FASTQ evidence is malformed")
    if summary.get("source_fastq_sha256") != source.get("sha256"):
        raise ValueError("comparison source FASTQ digest closure failed")
    source_path = _safe_comparison_file(comparison_panel_root, source.get("path"))
    if _sha256_file(source_path) != source["sha256"] or source_path.stat().st_size != _strict_count(source.get("size_bytes"), label="source FASTQ size"):
        raise ValueError("comparison source FASTQ artifact mismatch")

    normalized = summary.get("normalized_fastq")
    if not isinstance(normalized, dict) or not _valid_sha256(normalized.get("sha256")):
        raise ValueError("comparison normalized FASTQ evidence is malformed")
    normalized_path = _safe_comparison_file(comparison_panel_root, normalized.get("path"))
    if _sha256_file(normalized_path) != normalized["sha256"] or normalized_path.stat().st_size != _strict_count(normalized.get("size_bytes"), label="normalized FASTQ size"):
        raise ValueError("comparison normalized FASTQ artifact mismatch")

    occurrence_descriptor = summary.get("occurrence_map")
    if not isinstance(occurrence_descriptor, dict) or not _valid_sha256(occurrence_descriptor.get("sha256")):
        raise ValueError("comparison occurrence-map evidence is malformed")
    if summary.get("occurrence_map_sha256") != occurrence_descriptor.get("sha256"):
        raise ValueError("comparison occurrence-map digest closure failed")
    occurrence_path = _safe_comparison_file(comparison_panel_root, occurrence_descriptor.get("path"))
    if _sha256_file(occurrence_path) != occurrence_descriptor["sha256"] or occurrence_path.stat().st_size != _strict_count(occurrence_descriptor.get("size_bytes"), label="occurrence-map size"):
        raise ValueError("comparison occurrence-map artifact mismatch")
    occurrence = _load_occurrence_map(
        occurrence_path,
        source_sha256=source["sha256"],
        normalized_sha256=normalized["sha256"],
    )

    panel = summary.get("panel")
    if not isinstance(panel, dict):
        raise ValueError("comparison panel identity is missing")
    if (
        panel.get("panel_id") != panel_binding["panel_id"]
        or panel.get("panel_version") != panel_binding["panel_version"]
        or panel.get("panel_manifest_sha256") != panel_binding["panel_snapshot_sha256"]
        or panel.get("schema") != "bms.ngs.comparison-panel.v1"
    ):
        raise ValueError("comparison panel receipt identity does not match the summary")
    if "snapshot_sha256" in panel and not _valid_sha256(panel.get("snapshot_sha256")):
        raise ValueError("comparison panel snapshot digest is malformed")
    entries = panel.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("comparison panel entries are missing")
    panel_ids: list[str] = []
    reference_roles: dict[str, str] = {EXPECTED_REFERENCE_ID: "intended"}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or not _ID.fullmatch(entry["id"])
            or entry["id"] == EXPECTED_REFERENCE_ID
            or entry["id"] in panel_ids
        ):
            raise ValueError("comparison panel entry IDs are not unique")
        if entry.get("role") not in VALID_PANEL_ROLES or not isinstance(entry.get("label"), str) or not entry["label"] or len(entry["label"]) > 256:
            raise ValueError("comparison panel roles must be exactly host or plasmid_decoy")
        if not _valid_sha256(entry.get("fasta_sha256")):
            raise ValueError("comparison panel entry digest is malformed")
        if "fasta_path" in entry:
            entry_path = _safe_comparison_file(comparison_panel_root, entry["fasta_path"])
            if _sha256_file(entry_path) != entry["fasta_sha256"]:
                raise ValueError("comparison panel entry FASTA digest mismatch")
        panel_ids.append(entry["id"])
        reference_roles[entry["id"]] = entry["role"]

    input_count = _strict_count(summary.get("input_read_count"), label="comparison input read count")
    classified_count = _strict_count(summary.get("classified_read_count"), label="comparison classified read count")
    if input_count != occurrence["input_read_count"] or classified_count != input_count:
        raise ValueError("comparison input/classified count mismatch")
    if summary.get("category_closure") != list(CATEGORY_KEYS):
        raise ValueError("comparison category closure is not exact")
    categories = summary.get("categories")
    role_counts = summary.get("role_counts")
    if not isinstance(categories, dict) or set(categories) != set(CATEGORY_KEYS):
        raise ValueError("comparison category keys are not exact")
    if not isinstance(role_counts, dict) or set(role_counts) != set(ROLE_KEYS):
        raise ValueError("comparison role keys are not exact")
    category_counts = {key: _strict_count(categories[key], label=f"comparison category {key}") for key in CATEGORY_KEYS}
    declared_role_counts = {key: _strict_count(role_counts[key], label=f"comparison role {key}") for key in ROLE_KEYS}
    if sum(category_counts.values()) != input_count or sum(declared_role_counts.values()) != input_count:
        raise ValueError("comparison category/role count closure failed")

    expected_references = {EXPECTED_REFERENCE_ID, *panel_ids}
    declared_reference_counts = summary.get("reference_counts")
    if not isinstance(declared_reference_counts, dict) or set(declared_reference_counts) != expected_references:
        raise ValueError("comparison reference-count keys are not exact")
    reference_counts = {key: _strict_count(declared_reference_counts[key], label=f"comparison reference {key}") for key in expected_references}

    rows = summary.get("reads")
    if not isinstance(rows, list) or len(rows) != input_count:
        raise ValueError("comparison row/input count mismatch")
    seen_occurrences: set[str] = set()
    seen_ordinals: set[int] = set()
    recomputed_categories = {key: 0 for key in CATEGORY_KEYS}
    recomputed_roles = {key: 0 for key in ROLE_KEYS}
    recomputed_references = {key: 0 for key in expected_references}
    required_row_keys = {"read_id", "ordinal", "occurrence_id", "accepted_references", "category", "role"}
    for expected_ordinal, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != required_row_keys:
            raise ValueError("comparison row schema is malformed")
        ordinal = row.get("ordinal")
        occurrence_id = row.get("occurrence_id")
        if not isinstance(row.get("read_id"), str) or not row["read_id"] or ordinal != expected_ordinal or ordinal in seen_ordinals:
            raise ValueError("comparison row ordinals are not unique or ordered")
        if not isinstance(occurrence_id, str) or occurrence_id in seen_occurrences or occurrence_id not in occurrence["by_occurrence"]:
            raise ValueError("comparison row occurrence IDs are not unique or mapped")
        mapped = occurrence["by_occurrence"][occurrence_id]
        if mapped["ordinal"] != ordinal or mapped["read_id"] != row["read_id"]:
            raise ValueError("comparison row does not match its occurrence map")
        accepted = row.get("accepted_references")
        if not isinstance(accepted, list) or any(not isinstance(reference, str) or reference not in expected_references for reference in accepted) or len(accepted) != len(set(accepted)):
            raise ValueError("comparison accepted-reference list is malformed")
        category = row.get("category")
        role = row.get("role")
        if category not in CATEGORY_KEYS or role not in ROLE_KEYS:
            raise ValueError("comparison row category or role is invalid")
        if category == "unclassified" and (accepted or role != "unclassified"):
            raise ValueError("unclassified comparison row has attribution")
        if category == "ambiguous_multimapping" and (len(accepted) < 2 or role != "ambiguous"):
            raise ValueError("ambiguous comparison row is not competitive")
        if category == "expected_plasmid_unique" and (accepted != [EXPECTED_REFERENCE_ID] or role != "intended"):
            raise ValueError("expected comparison row is not uniquely intended")
        if category == "panel_reference_unique" and (len(accepted) != 1 or accepted[0] == EXPECTED_REFERENCE_ID or role != reference_roles[accepted[0]]):
            raise ValueError("panel comparison row is not uniquely role-resolved")
        recomputed_categories[category] += 1
        recomputed_roles[role] += 1
        for reference in accepted:
            recomputed_references[reference] += 1
        seen_occurrences.add(occurrence_id)
        seen_ordinals.add(ordinal)
    if seen_occurrences != set(occurrence["by_occurrence"]) or seen_ordinals != set(range(1, input_count + 1)):
        raise ValueError("comparison rows do not close over the occurrence map")
    if recomputed_categories != category_counts or recomputed_roles != declared_role_counts or recomputed_references != reference_counts:
        raise ValueError("comparison row/category/role/reference closure failed")

    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, list) or {item.get("kind") for item in artifacts if isinstance(item, dict)} != set(ARTIFACT_KINDS) or len(artifacts) != len(ARTIFACT_KINDS):
        raise ValueError("comparison artifact closure is not exact")
    artifact_by_kind: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("kind") in artifact_by_kind:
            raise ValueError("comparison artifact descriptor is duplicated or malformed")
        kind = artifact.get("kind")
        if not _valid_sha256(artifact.get("sha256")):
            raise ValueError("comparison artifact digest is malformed")
        artifact_path = _safe_comparison_file(comparison_panel_root, artifact.get("path"))
        size = _strict_count(artifact.get("size_bytes"), label=f"comparison artifact {kind} size")
        if _sha256_file(artifact_path) != artifact["sha256"] or artifact_path.stat().st_size != size:
            raise ValueError("comparison artifact mismatch")
        artifact_by_kind[kind] = artifact
    map_artifact = artifact_by_kind["comparison_panel_occurrence_map"]
    if map_artifact.get("path") != occurrence_descriptor.get("path") or map_artifact.get("sha256") != occurrence_descriptor.get("sha256") or map_artifact.get("size_bytes") != occurrence_descriptor.get("size_bytes"):
        raise ValueError("comparison occurrence-map artifact binding mismatch")

    return {
        "panel_id": panel_binding["panel_id"],
        "panel_version": panel_binding["panel_version"],
        "panel_snapshot_sha256": panel_binding["panel_snapshot_sha256"],
        "candidate_counts": category_counts,
        "input_read_count": input_count,
        "classified_read_count": classified_count,
        "role_counts": declared_role_counts,
        "reference_counts": reference_counts,
        "organism_attribution": "not_claimed",
    }


def _validated_comparison_projection(
    manifest: dict[str, Any],
    binding: dict[str, Any],
    summary: dict[str, Any] | None,
    *,
    comparison_panel_root: Path | None = None,
    comparison_summary_path: Path | None = None,
    comparison_panel_authorized: bool | None = None,
) -> dict[str, Any] | None:
    if not isinstance(binding.get("comparison_panel_binding"), dict) or comparison_panel_authorized is False:
        return None
    if not isinstance(summary, dict) or comparison_panel_root is None:
        return None
    try:
        return _validate_comparison_summary(
            manifest,
            binding,
            summary,
            comparison_panel_root=comparison_panel_root,
            comparison_summary_path=comparison_summary_path,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def project_ngs_workup(
    job: Any,
    manifest: dict[str, Any] | None,
    current_revision: Any,
    comparison_summary: dict[str, Any] | None = None,
    *,
    comparison_panel_root: Path | None = None,
    comparison_summary_path: Path | None = None,
    comparison_panel_authorized: bool | None = None,
) -> dict[str, Any]:
    """Project evidence only; this intentionally never changes constructs or jobs."""
    params = getattr(job, "params", {}) or {}
    binding = params.get("molbio_revision_binding")
    if not isinstance(binding, dict):
        raise ValueError("job is not bound to an immutable molecular revision receipt")
    required = ("sequence_id", "revision_id", "revision_sha256", "reference_snapshot_sha256", "receipt_id")
    if any(not isinstance(binding.get(key), str) or not binding[key] for key in required):
        raise ValueError("job molecular revision receipt is malformed")
    if not _valid_sha256(binding["revision_sha256"]) or not _valid_sha256(binding["reference_snapshot_sha256"]):
        raise ValueError("job molecular revision receipt digest is malformed")

    manifest_valid = False
    if manifest is not None:
        try:
            validate_workup_manifest(manifest, binding)
            manifest_valid = True
        except ValueError:
            manifest = None

    panel_requested = "comparison_panel_binding" in params
    comparison = (
        _validated_comparison_projection(
            manifest,
            params,
            comparison_summary,
            comparison_panel_root=comparison_panel_root,
            comparison_summary_path=comparison_summary_path,
            comparison_panel_authorized=comparison_panel_authorized,
        )
        if manifest_valid and panel_requested
        else None
    )
    verdict = str((manifest or {}).get("verdict") or "REVIEW").upper()
    scientific_status = verdict if verdict in {"PASS", "FAIL", "REVIEW"} else "REVIEW"
    if panel_requested and comparison is None:
        scientific_status = "REVIEW"
    current_id = getattr(current_revision, "id", None)
    current_sha = getattr(current_revision, "content_sha256", None)
    relation = "current" if current_id == binding["revision_id"] and current_sha == binding["revision_sha256"] else "historical"
    return {
        "schema": "bms.molbio.ngs-workup.v1",
        "job_id": str(getattr(job, "id", "")),
        "job_status": str(getattr(job, "status", "unknown")),
        "scientific_status": scientific_status,
        "revision_relation": relation,
        "receipt": {key: binding[key] for key in required},
        "projection_state": "READY" if manifest_valid and (not panel_requested or comparison is not None) else "REVIEW",
        "manifest_available": manifest_valid,
        "read_only": True,
        "construct_mutation": "not_performed",
        "completion_is_scientific_pass": False,
        "comparison_panel": comparison,
    }
