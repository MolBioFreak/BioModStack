"""Read-only projection of revision-bound NGS verification evidence."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from paths import get_results_dir


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


def validate_workup_manifest(manifest: dict[str, Any], binding: dict[str, Any]) -> None:
    if manifest.get("schema") != "biomodstack.construct_verification.v2" or manifest.get("artifact_schema_version") != 2:
        raise ValueError("workup requires the exact construct-verification manifest schema")
    expected = binding.get("reference_snapshot_sha256")
    artifacts = manifest.get("artifacts")
    reference = next((item for item in artifacts if isinstance(item, dict) and item.get("kind") == "reference"), None) if isinstance(artifacts, list) else None
    if not isinstance(expected, str) or len(expected) != 64 or not isinstance(reference, dict) or reference.get("sha256") != expected:
        raise ValueError("workup manifest reference digest does not match the receipt snapshot")


def _validated_comparison_projection(manifest: dict[str, Any], binding: dict[str, Any], summary: dict[str, Any] | None) -> dict[str, Any] | None:
    panel = binding.get("comparison_panel_binding")
    if not isinstance(panel, dict):
        return None
    required = ("panel_id", "panel_version", "panel_snapshot_sha256", "receipt_id")
    if any(not panel.get(key) for key in required):
        return None
    if not isinstance(summary, dict) or summary.get("schema") != "bms.ngs.comparison-attribution-summary.v1":
        return None
    if summary.get("artifacts_integrity_valid") is not True:
        return None
    source = summary.get("panel")
    categories = summary.get("categories")
    if not isinstance(source, dict) or source.get("panel_id") != panel["panel_id"] or source.get("panel_version") != panel["panel_version"] or source.get("panel_manifest_sha256") != panel["panel_snapshot_sha256"]:
        return None
    allowed = ("expected_plasmid_unique", "panel_reference_unique", "ambiguous_multimapping", "unclassified")
    if not isinstance(categories, dict) or any(not isinstance(categories.get(key), int) or categories[key] < 0 for key in allowed):
        return None
    return {"panel_id": panel["panel_id"], "panel_version": panel["panel_version"], "panel_snapshot_sha256": panel["panel_snapshot_sha256"],
            "candidate_counts": {key: categories[key] for key in allowed}, "organism_attribution": "not_claimed"}


def project_ngs_workup(job: Any, manifest: dict[str, Any] | None, current_revision: Any, comparison_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project evidence only; this intentionally never changes constructs or jobs."""
    binding = (getattr(job, "params", {}) or {}).get("molbio_revision_binding")
    if not isinstance(binding, dict):
        raise ValueError("job is not bound to an immutable molecular revision receipt")
    required = ("sequence_id", "revision_id", "revision_sha256", "reference_snapshot_sha256", "receipt_id")
    if any(not isinstance(binding.get(key), str) or not binding[key] for key in required):
        raise ValueError("job molecular revision receipt is malformed")
    manifest_valid = False
    if manifest is not None:
        try:
            validate_workup_manifest(manifest, binding)
            manifest_valid = True
        except ValueError:
            manifest = None
    comparison = _validated_comparison_projection(manifest, getattr(job, "params", {}) or {}, comparison_summary) if manifest_valid and manifest else None
    verdict = str((manifest or {}).get("verdict") or "REVIEW").upper()
    scientific_status = verdict if verdict in {"PASS", "FAIL", "REVIEW"} else "REVIEW"
    if isinstance((getattr(job, "params", {}) or {}).get("comparison_panel_binding"), dict) and comparison is None:
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
        "projection_state": "READY" if manifest_valid else "REVIEW",
        "manifest_available": manifest_valid,
        "read_only": True,
        "construct_mutation": "not_performed",
        "completion_is_scientific_pass": False,
        "comparison_panel": comparison,
    }
