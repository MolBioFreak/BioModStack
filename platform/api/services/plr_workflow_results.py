from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5


RESULT_SCHEMA = "bms.workflow.protein-local-redesign.results.v1"
_ARTIFACT_ID_NAMESPACE = NAMESPACE_URL
_MODEL_RE = re.compile(r"(?:^|_)model_(\d+)(?:_|$)")
_SAMPLE_RE = re.compile(r"(?:^|_)sample_(\d+)(?:\.|_|$)")

_STAGE_DEFINITIONS: tuple[tuple[str, str, str, str], ...] = (
    ("rfd3", "RFD3", "generator", "collected/protein_local_redesign_backbones/"),
    ("fampnn", "FA-MPNN", "sequence_design", "pdb_files/"),
    ("esmfold2", "ESMFold2", "structure_validation", "validation/esmfold2/"),
    ("protenix_v2", "Protenix V2", "structure_validation", "validation/protenix_v2/"),
)

_DIRECT_METRIC_FIELDS = (
    "plddt_overall",
    "plddt_binder",
    "plddt_target",
    "pae_overall",
    "pae_interaction",
    "rmsd_overall",
    "rog",
    "rfd_rog",
    "fampnn_psce",
    "iptm",
    "ptm",
    "conf_score",
    "disorder",
    "num_recycles",
)


def is_protein_local_redesign_job(job: Any) -> bool:
    model_id = str(getattr(job, "model_id", "") or "").strip().lower()
    mode = str(getattr(job, "mode", "") or "").strip().lower()
    params = getattr(job, "params", None)
    params = params if isinstance(params, Mapping) else {}
    stage_family = str(getattr(job, "stage_family", "") or "").strip().lower()
    return (
        model_id in {"protein_local_redesign", "protein_modification_experimental"}
        and (
            mode in {"local_redesign", "region_redesign"}
            or stage_family.startswith("protein_local_redesign")
            or str(params.get("rfd_mode", "")).strip().lower() == "protein_local_redesign"
        )
    )


def _root_for_job(job: Any) -> Path:
    raw_root = getattr(job, "output_dir", None)
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise ValueError("PLR result root is unavailable")
    lexical_root = Path(raw_root).expanduser()
    if lexical_root.is_symlink():
        raise ValueError("PLR result root must not be a symlink")
    root = lexical_root.resolve()
    if not root.is_dir():
        raise ValueError("PLR result root is unavailable")
    return root


def _contained_file(root: Path, raw_path: str | Path) -> Path:
    path = Path(raw_path).expanduser()
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("PLR result artifact is not contained by the job root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("PLR result artifact path contains a symlink")
    if not resolved.is_file():
        raise ValueError("PLR result artifact is unavailable")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item, depth=depth + 1) for key, item in value.items() if str(key) not in {"path", "storage_path"}}
    if isinstance(value, (list, tuple)):
        if len(value) > 32:
            return {"count": len(value), "truncated": True}
        return [_json_safe(item, depth=depth + 1) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(float(value)) else None
    return str(value)


def _compact_metrics(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    compact: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text in {"path", "storage_path", "diffused_index_map", "residue_plddt", "pae_matrix"}:
            continue
        if isinstance(item, (str, bool, int)) or item is None:
            compact[key_text] = item
        elif isinstance(item, float) and math.isfinite(item):
            compact[key_text] = item
        elif isinstance(item, Mapping) and len(item) <= 16:
            nested = _compact_metrics(item)
            if nested:
                compact[key_text] = nested
    return compact


def _candidate_index(name: str) -> int | None:
    match = _MODEL_RE.search(name)
    return int(match.group(1)) if match else None


def _sample_index(name: str) -> int | None:
    match = _SAMPLE_RE.search(name)
    return int(match.group(1)) if match else None


def _candidate_id(name: str, fallback: str) -> str:
    index = _candidate_index(name)
    return f"candidate_{index:03d}" if index is not None else fallback


def _stage_for_relative(relative_path: str, name: str) -> str | None:
    for stage_id, _label, _role, prefix in _STAGE_DEFINITIONS:
        if relative_path.startswith(prefix):
            if stage_id == "fampnn" and "_seq_" not in name:
                return None
            return stage_id
    return None


def _stage_definition(stage_id: str) -> tuple[str, str, str, str]:
    return next(definition for definition in _STAGE_DEFINITIONS if definition[0] == stage_id)


def _sequence_from_design(design: Any) -> str | None:
    for container in (getattr(design, "confidence_metrics", None), getattr(design, "provenance", None)):
        if not isinstance(container, Mapping):
            continue
        nested = container.get("fampnn")
        if isinstance(nested, Mapping) and isinstance(nested.get("sequence"), str) and nested["sequence"].strip():
            return nested["sequence"].strip()
        if isinstance(container.get("sequence"), str) and container["sequence"].strip():
            return container["sequence"].strip()
    return None


def _design_metrics(design: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for field in _DIRECT_METRIC_FIELDS:
        value = getattr(design, field, None)
        if value is None:
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        metrics[field] = value
    confidence = getattr(design, "confidence_metrics", None)
    if isinstance(confidence, Mapping):
        for key in ("fampnn", "esmfold2", "protenix_v2", "protenix"):
            nested = confidence.get(key)
            if isinstance(nested, Mapping):
                metrics[key] = _compact_metrics(nested)
    return metrics


def _media_type(path: Path) -> str:
    if path.name.endswith(".cif.gz"):
        return "chemical/x-mmcif+gzip"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _artifact_id(job_id: str, relative_path: str) -> str:
    return str(uuid5(_ARTIFACT_ID_NAMESPACE, f"bms:plr-result:{job_id}:{relative_path}"))


def _register_artifact(
    *,
    job_id: str,
    root: Path,
    raw_path: str | Path,
    kind: str,
    label: str,
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    path = _contained_file(root, raw_path)
    relative_path = path.relative_to(root).as_posix()
    artifact_id = _artifact_id(job_id, relative_path)
    if artifact_id not in artifacts:
        artifacts[artifact_id] = {
            "artifact_id": artifact_id,
            "kind": kind,
            "label": label,
            "relative_path": relative_path,
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "media_type": _media_type(path),
            "content_url": f"/api/jobs/{job_id}/workflow-results/artifacts/{artifact_id}",
        }
    return artifacts[artifact_id]


def _companion_artifacts(
    *,
    job_id: str,
    root: Path,
    stage_id: str,
    structure_path: Path,
    name: str,
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if stage_id == "esmfold2":
        metrics_paths = sorted(structure_path.parent.glob("*.metrics.json"))
        if metrics_paths:
            artifact = _register_artifact(
                job_id=job_id,
                root=root,
                raw_path=metrics_paths[0],
                kind="metrics",
                label="ESMFold2 metrics",
                artifacts=artifacts,
            )
            result["metrics_artifact"] = artifact["artifact_id"]
            result["metrics"] = _compact_metrics(_read_json(metrics_paths[0]) or {})
    elif stage_id == "protenix_v2":
        sample = _sample_index(name)
        confidence_paths = sorted(structure_path.parent.glob(f"*_summary_confidence_sample_{sample}.json")) if sample is not None else []
        if confidence_paths:
            confidence_artifact = _register_artifact(
                job_id=job_id,
                root=root,
                raw_path=confidence_paths[0],
                kind="metrics",
                label="Protenix V2 confidence",
                artifacts=artifacts,
            )
            result["confidence_artifact"] = confidence_artifact["artifact_id"]
            result["metrics"] = _compact_metrics(_read_json(confidence_paths[0]) or {})
        msa_path = structure_path.parent / "msa_report.json"
        if msa_path.is_file():
            msa_artifact = _register_artifact(
                job_id=job_id,
                root=root,
                raw_path=msa_path,
                kind="msa_receipt",
                label="Protenix V2 MSA receipt",
                artifacts=artifacts,
            )
            result["msa_artifact"] = msa_artifact["artifact_id"]
            msa = _read_json(msa_path) or {}
            result["msa"] = {
                key: value
                for key, value in msa.items()
                if key in {"backend", "state", "cache_key", "cache_hit", "sequence_sha256", "source"}
            }
    return result


def _source_artifacts(job_id: str, root: Path, artifacts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    source: list[dict[str, Any]] = []
    input_root = root / "inputs" / "protein_local_redesign"
    if input_root.is_dir():
        structures = sorted(path for path in input_root.iterdir() if path.is_file() and path.suffix.lower() in {".pdb", ".cif", ".mmcif", ".gz"})
        if structures:
            source.append(_register_artifact(job_id=job_id, root=root, raw_path=structures[0], kind="source_structure", label="Source structure", artifacts=artifacts))
        manifest = input_root / "region_manifest.json"
        if manifest.is_file():
            source.append(_register_artifact(job_id=job_id, root=root, raw_path=manifest, kind="request_manifest", label="PLR region manifest", artifacts=artifacts))
    return source


def build_protein_local_redesign_result_surface(job: Any, designs: Iterable[Any]) -> dict[str, Any]:
    root = _root_for_job(job)
    job_id = str(getattr(job, "id", ""))
    if not job_id:
        raise ValueError("PLR job identity is unavailable")
    design_rows = list(designs)

    artifacts: dict[str, dict[str, Any]] = {}
    source = _source_artifacts(job_id, root, artifacts)
    stage_items: dict[str, list[dict[str, Any]]] = {stage_id: [] for stage_id, _label, _role, _prefix in _STAGE_DEFINITIONS}

    for design in design_rows:
        raw_structure = getattr(design, "pdb_path", None)
        if not isinstance(raw_structure, str) or not raw_structure.strip():
            continue
        structure_path = _contained_file(root, raw_structure)
        relative_path = structure_path.relative_to(root).as_posix()
        name = str(getattr(design, "name", "") or structure_path.stem)
        stage_id = _stage_for_relative(relative_path, name)
        if stage_id is None:
            continue
        structure_artifact = _register_artifact(
            job_id=job_id,
            root=root,
            raw_path=structure_path,
            kind="structure",
            label=_stage_definition(stage_id)[1],
            artifacts=artifacts,
        )
        companion = _companion_artifacts(
            job_id=job_id,
            root=root,
            stage_id=stage_id,
            structure_path=structure_path,
            name=name,
            artifacts=artifacts,
        )
        item: dict[str, Any] = {
            "item_id": str(getattr(design, "id", "") or structure_artifact["artifact_id"]),
            "design_id": getattr(design, "id", None),
            "candidate_id": _candidate_id(name, structure_artifact["artifact_id"]),
            "candidate_label": f"Candidate {_candidate_index(name)}" if _candidate_index(name) is not None else name,
            "sample_index": _sample_index(name),
            "name": name,
            "structure": structure_artifact,
            "metrics": _design_metrics(design),
            "metadata": {
                key: getattr(design, key, None)
                for key in ("stage_family", "stage_mode", "review_profile_id", "artifact_class", "artifact_schema_version")
                if getattr(design, key, None) is not None
            },
        }
        sequence = _sequence_from_design(design)
        if sequence is not None:
            item["sequence"] = sequence
        item.update(companion)
        stage_items[stage_id].append(item)

        json_path = getattr(design, "json_path", None)
        if isinstance(json_path, str) and json_path.strip() and Path(json_path).is_file():
            item["native_metadata_artifact"] = _register_artifact(
                job_id=job_id,
                root=root,
                raw_path=json_path,
                kind="native_metadata",
                label=f"{_stage_definition(stage_id)[1]} native metadata",
                artifacts=artifacts,
            )["artifact_id"]

    receipt_path = root / "validation" / "validator_suite_receipt.json"
    receipt = _read_json(receipt_path) if receipt_path.is_file() else None
    expected_candidates = receipt.get("expected_candidate_count") if isinstance(receipt, Mapping) else None
    if not isinstance(expected_candidates, int) or expected_candidates < 1:
        candidate_ids = {item["candidate_id"] for items in stage_items.values() for item in items}
        expected_candidates = len(candidate_ids)

    tabs: list[dict[str, Any]] = []
    for stage_id, label, role, _prefix in _STAGE_DEFINITIONS:
        items = sorted(stage_items[stage_id], key=lambda item: (item["candidate_id"], item.get("sample_index") is None, item.get("sample_index") or -1, item["item_id"]))
        if not items:
            continue
        candidate_count = len({item["candidate_id"] for item in items})
        validator_complete = True
        if isinstance(receipt, Mapping) and stage_id in {"esmfold2", "protenix_v2"}:
            validator_name = "protenix_v2" if stage_id == "protenix_v2" else stage_id
            summary = next((entry for entry in receipt.get("validator_summaries", []) if isinstance(entry, Mapping) and entry.get("validator") == validator_name), None)
            validator_complete = bool(summary and summary.get("state") == "complete" and summary.get("completed_candidates") == expected_candidates)
        tabs.append(
            {
                "id": stage_id,
                "label": label,
                "role": role,
                "status": "complete" if candidate_count == expected_candidates and validator_complete else "partial",
                "count": len(items),
                "candidate_count": candidate_count,
                "expected_candidate_count": expected_candidates,
                "items": items,
            }
        )

    public_receipt = _json_safe(receipt) if receipt is not None else None
    params = getattr(job, "params", None)
    params = params if isinstance(params, Mapping) else {}
    request_sha256 = params.get("rfd3_request_sha256") or params.get("request_sha256")
    surface: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "job": {
            "id": job_id,
            "name": str(getattr(job, "name", "") or ""),
            "status": str(getattr(job, "status", "") or ""),
            "model_id": str(getattr(job, "model_id", "") or ""),
            "mode": str(getattr(job, "mode", "") or ""),
            "request_sha256": request_sha256 if isinstance(request_sha256, str) else None,
        },
        "source": {"artifacts": source},
        "receipt": public_receipt,
        "tabs": tabs,
        "artifacts": list(artifacts.values()),
        "capabilities": {
            "model_native_tabs": [tab["id"] for tab in tabs],
            "structure_viewer": any(tab["items"] for tab in tabs),
            "sequence_viewer": any("sequence" in item for tab in tabs for item in tab["items"]),
            "volume_viewer": (root / "viewer" / "volumes.json").is_file(),
        },
        "counts": {
            "persisted_design_rows": len(design_rows),
            "source_artifacts": len(source),
            "tabs": {tab["id"]: tab["count"] for tab in tabs},
        },
    }
    canonical_bytes = json.dumps(surface, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    surface["composition"] = {
        "algorithm": "sha256-canonical-json-v1",
        "sha256": hashlib.sha256(canonical_bytes).hexdigest(),
    }
    return surface


def artifact_from_surface(surface: Mapping[str, Any], artifact_id: str) -> Mapping[str, Any] | None:
    artifacts = surface.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    return next((artifact for artifact in artifacts if isinstance(artifact, Mapping) and artifact.get("artifact_id") == artifact_id), None)


def resolve_protein_local_redesign_artifact(job: Any, surface: Mapping[str, Any], artifact_id: str) -> tuple[Path, Mapping[str, Any]]:
    artifact = artifact_from_surface(surface, artifact_id)
    if artifact is None:
        raise ValueError("PLR result artifact is not registered")
    root = _root_for_job(job)
    path = _contained_file(root, str(artifact.get("relative_path") or ""))
    expected_bytes = artifact.get("bytes")
    expected_sha256 = artifact.get("sha256")
    if not isinstance(expected_bytes, int) or path.stat().st_size != expected_bytes:
        raise ValueError("PLR result artifact byte length changed")
    if not isinstance(expected_sha256, str) or _sha256(path) != expected_sha256:
        raise ValueError("PLR result artifact hash changed")
    return path, artifact
