"""
Helpers for interactive stage-review publication.

These helpers repair racy gate payloads from the filesystem and materialize
parent-visible review rows for stages that pause before final ingestion.
"""

from __future__ import annotations

import json
import csv
import re
import uuid
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from antibody_pipeline_contract import (
    infer_antibody_artifact_class_from_stage,
    is_antibody_pipeline_mode,
)
from database import Design, Job
from paths import get_data_root, resolve_allowed_path, to_allowed_relative
from services.result_ingester import (
    _apply_ppiflow_filter_fields,
    _apply_ppiflow_score_fields,
    _design_lineage_fields,
    _inherit_source_design_metrics,
    _parse_hlt_cdr_lengths,
    _resolve_parent_design_lineage,
    _job_stage_context,
    extract_plddt_from_pdb,
    parse_backbone_id,
    safe_float,
    safe_int,
)
from services.cdr_annotator import extract_sequence_from_pdb, identify_binder_chains
from services.rfantibody_metadata import load_rfantibody_trb_summary
from services.structure_utils import load_structure

REVIEWABLE_STAGES = {"post_rfantibody", "post_boltzgen", "post_ppiflow_generator", "post_fampnn", "post_structure_validation"}
STRUCTURE_PATTERNS = ("*.pdb", "*.cif")
METRIC_PATTERNS = ("*.json", "*.csv", "*.tsv")
NEXTFLOW_JOB_ID_RE = re.compile(r"--job_id\s+([0-9a-fA-F-]{36})")


def _is_protein_local_redesign_job(job: Job | None, payload: Optional[dict] = None) -> bool:
    if job is None and not payload:
        return False

    params = getattr(job, "params", None) or {}
    current_payload = payload or getattr(job, "awaiting_payload", None) or {}
    model_id = str(getattr(job, "model_id", "") or "").strip().lower()
    mode = str(getattr(job, "mode", "") or "").strip().lower()
    rfd_mode = str(params.get("rfd_mode") or "").strip().lower()
    framework_type = str(current_payload.get("framework_type") or "").strip().lower()

    return (
        model_id == "protein_local_redesign"
        or mode == "local_redesign"
        or rfd_mode == "protein_local_redesign"
        or framework_type == "protein_local_redesign"
    )


def _uses_rfantibody_review(stage: str | None, job: Job | None, payload: Optional[dict] = None) -> bool:
    normalized = str(stage or "").strip().lower()
    return normalized == "post_rfantibody" and not _is_protein_local_redesign_job(job, payload)


def _review_stage_identity(stage: str | None, job: Job | None = None, payload: Optional[dict] = None) -> tuple[Optional[str], Optional[str]]:
    normalized = str(stage or "").strip().lower()
    if normalized == "post_rfantibody":
        if _is_protein_local_redesign_job(job, payload):
            return "protein_local_redesign", "post_rfd3"
        return "rfantibody", normalized
    if normalized == "post_boltzgen":
        return "boltzgen", normalized
    if normalized == "post_ppiflow_generator":
        return "ppiflow", "generator_backbone_refine"
    if normalized == "post_fampnn":
        return "fampnn", normalized
    if normalized == "post_structure_validation":
        return "validation", normalized
    return None, None


def resolve_output_dir(output_dir: str | None) -> Optional[Path]:
    if not output_dir:
        return None
    output_path = Path(output_dir)
    if output_path.is_absolute():
        return output_path
    return get_data_root() / output_dir


def resolve_review_path(path_value: Any, output_dir: str | None = None) -> Optional[Path]:
    raw = str(path_value or "").strip()
    if not raw:
        return None

    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()

    try:
        return resolve_allowed_path(raw)
    except Exception:
        pass

    base = resolve_output_dir(output_dir)
    if base is not None:
        candidate = (base / raw).resolve()
        if candidate.exists():
            return candidate

    fallback = (get_data_root() / raw).resolve()
    return fallback


def normalize_review_path(path_value: Path | str | None) -> Optional[str]:
    if not path_value:
        return None
    resolved = Path(path_value).expanduser().resolve()
    try:
        return to_allowed_relative(resolved)
    except Exception:
        return str(resolved)


def resolve_nextflow_run_dir(output_dir: Path | str | None) -> Optional[Path]:
    if not output_dir:
        return None
    if isinstance(output_dir, Path):
        output_path = output_dir.expanduser().resolve()
    else:
        output_path = resolve_output_dir(output_dir)
    if output_path is None or not output_path.exists():
        return None

    direct_history = output_path / ".nextflow" / "history"
    if direct_history.exists():
        return output_path

    nested_candidates: list[tuple[float, Path]] = []
    for history_path in output_path.glob("*/.nextflow/history"):
        try:
            nested_candidates.append((history_path.stat().st_mtime, history_path.parent.parent))
        except Exception:
            continue

    if not nested_candidates:
        return output_path

    nested_candidates.sort(key=lambda item: item[0], reverse=True)
    return nested_candidates[0][1]


def _history_status_from_lines(lines: list[str], job_id: str | None = None) -> str:
    normalized_job_id = str(job_id or "").strip()
    saw_job_id_line = False

    if normalized_job_id:
        for line in reversed(lines):
            if "--job_id" not in line:
                continue
            saw_job_id_line = True
            match = NEXTFLOW_JOB_ID_RE.search(line)
            if not match or match.group(1) != normalized_job_id:
                continue
            parts = line.split("\t")
            if len(parts) <= 3:
                return ""
            return parts[3].strip().upper()

        if saw_job_id_line:
            return ""

    if not lines:
        return ""
    parts = lines[-1].split("\t")
    if len(parts) <= 3:
        return ""
    return parts[3].strip().upper()


def nextflow_history_status_for_run_dir(
    output_dir: Path | str | None,
    job_id: str | None = None,
) -> str:
    output_path = resolve_nextflow_run_dir(output_dir)
    if output_path is None:
        return ""
    history_path = output_path / ".nextflow" / "history"
    if not history_path.exists():
        return ""
    try:
        lines = history_path.read_text(errors="ignore").splitlines()
    except Exception:
        return ""
    return _history_status_from_lines(lines, job_id=job_id)


def _iter_matching_files(directory: Path, patterns: Iterable[str]) -> list[Path]:
    files: set[Path] = set()
    if not directory.exists():
        return []
    for pattern in patterns:
        files.update(path.resolve() for path in directory.glob(pattern))
    return sorted(files)


def _review_design_id(job_id: str, stage: str, artifact_group: str, design_name: str) -> str:
    seed = f"{job_id}:{stage}:{artifact_group}:{design_name}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def _review_structure_priority(path: Path) -> tuple[int, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdb":
        return (0, str(path))
    if suffix == ".cif":
        return (1, str(path))
    return (2, str(path))


def _dedupe_review_structures(expected_files: Iterable[tuple[str, Path]]) -> list[tuple[str, Path]]:
    deduped: dict[tuple[str, str], tuple[str, Path]] = {}
    for artifact_group, structure_path in expected_files:
        key = (artifact_group, structure_path.stem)
        current = deduped.get(key)
        if current is None or _review_structure_priority(structure_path) < _review_structure_priority(current[1]):
            deduped[key] = (artifact_group, structure_path)
    return sorted(
        deduped.values(),
        key=lambda item: (item[0], item[1].stem, _review_structure_priority(item[1])),
    )


def list_preview_files(directory: Path | None, patterns: Iterable[str], limit: int = 25) -> list[str]:
    if not directory:
        return []
    return [normalize_review_path(path) for path in _iter_matching_files(directory, patterns)[:limit] if path]


def count_files(directory: Path | None, patterns: Iterable[str]) -> int:
    if not directory:
        return 0
    return len(_iter_matching_files(directory, patterns))


def _pick_best_review_dir(
    preferred_dir: Path | None,
    candidates: Iterable[Path | None],
    patterns: Iterable[str],
) -> Path | None:
    ordered: list[Path] = []
    for candidate in [preferred_dir, *list(candidates)]:
        if candidate is None:
            continue
        resolved = candidate.expanduser().resolve()
        if resolved not in ordered:
            ordered.append(resolved)

    if not ordered:
        return preferred_dir

    best_dir: Path | None = preferred_dir.expanduser().resolve() if preferred_dir else None
    best_count = count_files(best_dir, patterns) if best_dir and best_dir.exists() else 0

    if best_dir is None:
        best_dir = next((candidate for candidate in ordered if candidate.exists()), None)

    for candidate in ordered:
        if not candidate.exists():
            continue
        candidate_count = count_files(candidate, patterns)
        if candidate_count > best_count:
            best_dir = candidate
            best_count = candidate_count

    return best_dir


def _rfantibody_raw_dir_candidates(output_path: Path) -> list[Path]:
    return [
        output_path / "collected" / "rfantibody_raw",
        output_path / "collected" / "rfantibody",
        output_path / "run" / "rfantibody" / "output",
    ]


def _rfantibody_filtered_dir_candidates(output_path: Path) -> list[Path]:
    return [
        output_path / "collected" / "rfantibody_filtered",
    ]


def summarize_backbones(directory: Path | None, patterns: Iterable[str], preview_limit: int = 3) -> Optional[dict]:
    if not directory or not directory.exists():
        return None

    return summarize_structure_files(_iter_matching_files(directory, patterns), preview_limit=preview_limit)


def summarize_structure_files(paths: Iterable[Path], preview_limit: int = 3) -> dict:
    unique_files = sorted({path.resolve() for path in paths})
    backbones: dict[int, dict] = {}
    unassigned_count = 0
    unassigned_preview: list[str] = []

    for path in unique_files:
        backbone_id = parse_backbone_id(path.stem)
        if backbone_id is None:
            unassigned_count += 1
            if len(unassigned_preview) < preview_limit:
                normalized = normalize_review_path(path)
                if normalized:
                    unassigned_preview.append(normalized)
            continue

        entry = backbones.setdefault(
            backbone_id,
            {
                "count": 0,
                "representative_file": None,
                "preview": [],
                "sample_names": [],
            },
        )
        entry["count"] += 1
        normalized = normalize_review_path(path)
        if entry["representative_file"] is None:
            entry["representative_file"] = normalized
        if len(entry["preview"]) < preview_limit and normalized:
            entry["preview"].append(normalized)
        if len(entry["sample_names"]) < preview_limit:
            entry["sample_names"].append(path.name)

    return {
        "mode": "backbone_id",
        "total": len(unique_files),
        "assigned_total": sum(entry["count"] for entry in backbones.values()),
        "unassigned_total": unassigned_count,
        "unassigned_preview": unassigned_preview,
        "backbones": {str(backbone_id): data for backbone_id, data in sorted(backbones.items())},
    }


def screening_row_passed(row: Optional[dict]) -> bool:
    if not isinstance(row, dict):
        return False

    passed = row.get("passed_screen")
    if isinstance(passed, bool):
        return passed
    if passed not in (None, ""):
        return str(passed).strip().lower() in {"1", "true", "yes", "passed"}

    reason = str(row.get("screening_reason") or "").strip().lower()
    return reason == "passed"


def screening_row_for_structure(
    structure_path: Path,
    screening_by_name: dict[str, dict],
    screening_by_backbone: dict[int, dict],
) -> Optional[dict]:
    row = screening_by_name.get(structure_path.stem)
    if isinstance(row, dict):
        return row

    backbone_id = parse_backbone_id(structure_path.stem)
    if backbone_id is None:
        return None
    fallback = screening_by_backbone.get(backbone_id)
    return fallback if isinstance(fallback, dict) else None


def resolve_rfantibody_filtered_files(
    raw_dir: Path | None,
    filtered_dir: Path | None,
    screening_by_name: dict[str, dict],
    screening_by_backbone: dict[int, dict],
) -> list[Path]:
    explicit_filtered_files = _iter_matching_files(filtered_dir, STRUCTURE_PATTERNS) if filtered_dir else []
    if explicit_filtered_files:
        return explicit_filtered_files

    raw_files = _iter_matching_files(raw_dir, STRUCTURE_PATTERNS) if raw_dir else []
    if not raw_files:
        return []

    derived_filtered_files: list[Path] = []
    for structure_path in raw_files:
        row = screening_row_for_structure(structure_path, screening_by_name, screening_by_backbone)
        if screening_row_passed(row):
            derived_filtered_files.append(structure_path)
    return derived_filtered_files


def refresh_gate_payload(payload: Optional[dict], output_dir: str | None = None) -> dict:
    current = dict(payload or {})
    stage = str(current.get("stage") or "").strip().lower()
    uses_rfantibody_review = stage == "post_rfantibody" and str(current.get("framework_type") or "").strip().lower() != "protein_local_redesign"
    output_path = resolve_output_dir(output_dir)
    candidate_dir = resolve_review_path(current.get("candidate_dir"), output_dir)
    raw_dir = resolve_review_path(current.get("raw_dir"), output_dir)
    filtered_dir = resolve_review_path(current.get("filtered_dir"), output_dir)
    screening_by_name: dict[str, dict] = {}
    screening_by_backbone: dict[int, dict] = {}

    if uses_rfantibody_review and output_path is not None:
        raw_dir = _pick_best_review_dir(raw_dir, _rfantibody_raw_dir_candidates(output_path), STRUCTURE_PATTERNS)
        filtered_dir = _pick_best_review_dir(filtered_dir, _rfantibody_filtered_dir_candidates(output_path), STRUCTURE_PATTERNS)
        screening_by_name, screening_by_backbone = _load_screening_rows(output_dir)
        derived_filtered_files = resolve_rfantibody_filtered_files(raw_dir, filtered_dir, screening_by_name, screening_by_backbone)

        candidate_count = count_files(candidate_dir, STRUCTURE_PATTERNS) if candidate_dir else 0
        if candidate_count == 0:
            if filtered_dir and count_files(filtered_dir, STRUCTURE_PATTERNS) > 0:
                candidate_dir = filtered_dir
            elif derived_filtered_files and raw_dir is not None:
                candidate_dir = raw_dir
            elif raw_dir and count_files(raw_dir, STRUCTURE_PATTERNS) > 0:
                candidate_dir = raw_dir
    elif stage in {"post_fampnn", "post_boltzgen", "post_ppiflow_generator"}:
        candidate_count = count_files(candidate_dir, STRUCTURE_PATTERNS) if candidate_dir else 0
        if candidate_count == 0:
            if filtered_dir and count_files(filtered_dir, STRUCTURE_PATTERNS) > 0:
                candidate_dir = filtered_dir
            elif raw_dir and count_files(raw_dir, STRUCTURE_PATTERNS) > 0:
                candidate_dir = raw_dir

    current["candidate_dir"] = normalize_review_path(candidate_dir)
    current["candidate_count"] = count_files(candidate_dir, STRUCTURE_PATTERNS)
    current["candidate_preview"] = list_preview_files(candidate_dir, STRUCTURE_PATTERNS)
    current["candidate_backbone_summary"] = summarize_backbones(candidate_dir, STRUCTURE_PATTERNS)
    current["metric_count"] = count_files(candidate_dir, METRIC_PATTERNS)
    current["metric_preview"] = list_preview_files(candidate_dir, METRIC_PATTERNS)

    current["raw_dir"] = normalize_review_path(raw_dir)
    current["raw_candidate_count"] = count_files(raw_dir, STRUCTURE_PATTERNS) if raw_dir else None
    current["raw_backbone_summary"] = summarize_backbones(raw_dir, STRUCTURE_PATTERNS) if raw_dir else None
    current["raw_metric_count"] = count_files(raw_dir, METRIC_PATTERNS) if raw_dir else None

    derived_filtered_files = (
        resolve_rfantibody_filtered_files(raw_dir, filtered_dir, screening_by_name, screening_by_backbone)
        if uses_rfantibody_review
        else []
    )
    current["filtered_dir"] = normalize_review_path(filtered_dir)
    current["filtered_candidate_count"] = len(derived_filtered_files) if uses_rfantibody_review else (count_files(filtered_dir, STRUCTURE_PATTERNS) if filtered_dir else None)
    current["filtered_backbone_summary"] = (
        summarize_structure_files(derived_filtered_files)
        if uses_rfantibody_review
        else (summarize_backbones(filtered_dir, STRUCTURE_PATTERNS) if filtered_dir else None)
    )
    current["filtered_metric_count"] = count_files(filtered_dir, METRIC_PATTERNS) if filtered_dir else None

    if (
        uses_rfantibody_review
        and current["candidate_count"] == 0
        and isinstance(current.get("raw_candidate_count"), int)
        and current["raw_candidate_count"] > 0
        and raw_dir is not None
    ):
        candidate_dir = raw_dir
        current["candidate_dir"] = normalize_review_path(candidate_dir)
        current["candidate_count"] = count_files(candidate_dir, STRUCTURE_PATTERNS)
        current["candidate_preview"] = list_preview_files(candidate_dir, STRUCTURE_PATTERNS)
        current["candidate_backbone_summary"] = summarize_backbones(candidate_dir, STRUCTURE_PATTERNS)
        current["metric_count"] = count_files(candidate_dir, METRIC_PATTERNS)
        current["metric_preview"] = list_preview_files(candidate_dir, METRIC_PATTERNS)

    current["review_grouping"] = "backbone_id" if uses_rfantibody_review else current.get("review_grouping")
    return current


def infer_antibody_stage_state(job: Job, completed: list[str], stage_outputs: dict[str, list[str]]) -> Tuple[list[str], dict[str, list[str]]]:
    if not is_antibody_pipeline_mode(job.mode):
        return completed, stage_outputs

    output_path = resolve_output_dir(job.output_dir)
    if output_path is None or not output_path.exists():
        return completed, stage_outputs

    inferred: dict[str, Path] = {}
    rfa_dir = _pick_best_review_dir(
        None,
        _rfantibody_raw_dir_candidates(output_path),
        STRUCTURE_PATTERNS,
    )
    if rfa_dir and rfa_dir.exists():
        inferred["rfantibody"] = rfa_dir

    fampnn_filtered = output_path / "collected" / "fampnn_filtered"
    fampnn_raw = output_path / "collected" / "fampnn"
    if fampnn_filtered.exists():
        inferred["fampnn"] = fampnn_filtered
    elif fampnn_raw.exists():
        inferred["fampnn"] = fampnn_raw

    for stage, path in inferred.items():
        existing = stage_outputs.get(stage)
        stage_outputs[stage] = [normalize_review_path(path)] if not existing else existing
        if stage not in completed:
            completed.append(stage)

    return completed, stage_outputs


def gate_file_for_stage(job: Job) -> Optional[Path]:
    if not job.output_dir or not job.awaiting_stage:
        return None
    output_path = resolve_output_dir(job.output_dir)
    if not output_path:
        return None
    return output_path / "gates" / f"gate_{job.awaiting_stage}.json"


def load_review_gate_snapshot(
    output_dir: str | None,
    stage: str | None = None,
) -> tuple[Optional[str], dict]:
    output_path = resolve_output_dir(output_dir)
    if output_path is None:
        return None, {}

    gate_dir = output_path / "gates"
    if not gate_dir.exists():
        return None, {}

    candidate_paths: list[Path] = []
    normalized_stage = str(stage or "").strip().lower()
    if normalized_stage:
        candidate_paths.append(gate_dir / f"gate_{normalized_stage}.json")
    candidate_paths.extend(sorted(gate_dir.glob("gate_*.json")))

    seen: set[Path] = set()
    for candidate in candidate_paths:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved in seen or not candidate.exists():
            continue
        seen.add(resolved)

        gate_data = _read_json(candidate)
        if not gate_data:
            continue

        payload = gate_data.get("awaiting_payload")
        if not isinstance(payload, dict):
            payload = gate_data if isinstance(gate_data, dict) else {}
        payload = dict(payload or {})

        gate_stage = str(
            gate_data.get("awaiting_stage")
            or payload.get("stage")
            or stage
            or candidate.stem.replace("gate_", "", 1)
        ).strip().lower()
        if not gate_stage:
            continue
        if gate_stage not in REVIEWABLE_STAGES:
            continue

        payload["stage"] = gate_stage
        return gate_stage, refresh_gate_payload(payload, output_dir)

    return None, {}


def nextflow_history_status(job: Job) -> str:
    return nextflow_history_status_for_run_dir(job.output_dir, str(job.id))


def has_stage_gate(job: Job) -> bool:
    gate_file = gate_file_for_stage(job)
    return bool(gate_file and gate_file.exists())


def _load_screening_rows(output_dir: str | None) -> Tuple[dict[str, dict], dict[int, dict]]:
    output_path = resolve_output_dir(output_dir)
    if output_path is None:
        return {}, {}

    json_candidates = [
        output_path / "run" / "rfantibody_screen" / "screening_summary.json",
        output_path / "rfantibody_screening_summary.json",
    ]

    rows: list[dict] = []
    for candidate in json_candidates:
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text())
            if isinstance(data, dict):
                rows = [row for row in (data.get("results") or []) if isinstance(row, dict)]
                break
        except Exception:
            continue

    if not rows:
        csv_candidates = [
            output_path / "collected" / "rfantibody_filtered" / "rfantibody_screening_summary.csv",
            output_path / "run" / "rfantibody_screen" / "rfantibody_screening_summary.csv",
            output_path / "rfantibody_screening_summary.csv",
        ]
        for candidate in csv_candidates:
            if not candidate.exists():
                continue
            try:
                with open(candidate, "r") as handle:
                    reader = csv.DictReader(handle)
                    rows = [dict(row) for row in reader]
                    break
            except Exception:
                continue

    by_name: dict[str, dict] = {}
    by_backbone: dict[int, dict] = {}
    for row in rows:
        design_name = str(row.get("design_name") or "").strip()
        if design_name:
            by_name[design_name] = row
            backbone_id = parse_backbone_id(design_name)
            if backbone_id is not None:
                by_backbone[backbone_id] = row
    return by_name, by_backbone


def _read_json(path: Optional[Path]) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _coerce_json_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except Exception:
                return value
    return value


def _infer_binder_metrics(structure_path: Path) -> tuple[Optional[int], Optional[str]]:
    try:
        sequences = extract_sequence_from_pdb(str(structure_path))
        if not sequences:
            return None, None

        binder_chains = identify_binder_chains(sequences, str(structure_path))
        if binder_chains:
            unique_chain_ids = [chain_id for chain_id in dict.fromkeys(binder_chains.values()) if chain_id in sequences]
            binder_length = sum(len(sequences[chain_id]) for chain_id in unique_chain_ids)
            antibody_type = 'vhh' if len(unique_chain_ids) == 1 else 'fab'
            return binder_length or None, antibody_type

        heavy_chain = sequences.get("H")
        if heavy_chain:
            return len(heavy_chain), 'vhh'

        if len(sequences) == 1:
            only_seq = next(iter(sequences.values()))
            return len(only_seq), None
    except Exception:
        return None, None

    return None, None


def _load_boltzgen_review_metrics(
    structure_path: Path,
    *,
    candidate_dir: Optional[Path],
    raw_dir: Optional[Path],
    filtered_dir: Optional[Path],
) -> dict[str, Any]:
    design_name = structure_path.stem
    confidence_candidates = [
        structure_path.with_suffix(".json"),
        (candidate_dir / f"confidence_{design_name}.json") if candidate_dir else None,
        (candidate_dir / f"{design_name}.json") if candidate_dir else None,
        (raw_dir / f"confidence_{design_name}.json") if raw_dir else None,
        (raw_dir / f"{design_name}.json") if raw_dir else None,
        (filtered_dir / f"confidence_{design_name}.json") if filtered_dir else None,
        (filtered_dir / f"{design_name}.json") if filtered_dir else None,
    ]
    metrics_path = next((path for path in confidence_candidates if path and path.exists()), None)
    metrics = _read_json(metrics_path)

    affinity_candidates = [
        (candidate_dir / f"affinity_{design_name}.json") if candidate_dir else None,
        (raw_dir / f"affinity_{design_name}.json") if raw_dir else None,
        (filtered_dir / f"affinity_{design_name}.json") if filtered_dir else None,
    ]
    affinity_path = next((path for path in affinity_candidates if path and path.exists()), None)
    affinity_metrics = _read_json(affinity_path)

    return {
        "json_path": str(metrics_path) if metrics_path else None,
        "confidence_metrics": metrics or None,
        "conf_score": safe_float(metrics.get("confidence_score")),
        "ptm": safe_float(metrics.get("ptm")),
        "iptm": safe_float(metrics.get("iptm")),
        "protein_iptm": safe_float(metrics.get("protein_iptm")),
        "ligand_iptm": safe_float(metrics.get("ligand_iptm")),
        "complex_iplddt": safe_float(metrics.get("complex_iplddt")),
        "complex_ipde": safe_float(metrics.get("complex_ipde")),
        "chains_ptm": _coerce_json_value(metrics.get("chains_ptm")),
        "pair_chains_iptm": _coerce_json_value(metrics.get("pair_chains_iptm")),
        "affinity_score": safe_float(affinity_metrics.get("affinity_pred_value")),
        "binder_probability": safe_float(affinity_metrics.get("affinity_probability_binary")),
    }


def _load_ppiflow_review_metrics(
    structure_path: Path,
    *,
    candidate_dir: Optional[Path],
    raw_dir: Optional[Path],
    filtered_dir: Optional[Path],
) -> dict[str, Any]:
    design_name = structure_path.stem
    score_candidates = [
        structure_path.with_name(f"{design_name}_partial_flow_score.json"),
        structure_path.with_name(f"{design_name}_maturation_score.json"),
        (candidate_dir / f"{design_name}_partial_flow_score.json") if candidate_dir else None,
        (candidate_dir / f"{design_name}_maturation_score.json") if candidate_dir else None,
        (raw_dir / f"{design_name}_partial_flow_score.json") if raw_dir else None,
        (raw_dir / f"{design_name}_maturation_score.json") if raw_dir else None,
        (filtered_dir / f"{design_name}_partial_flow_score.json") if filtered_dir else None,
        (filtered_dir / f"{design_name}_maturation_score.json") if filtered_dir else None,
    ]
    score_path = next((path for path in score_candidates if path and path.exists()), None)
    score_payload = _read_json(score_path)

    filter_candidates = [
        structure_path.with_name(f"{design_name}_maturation_filter.json"),
        (candidate_dir / f"{design_name}_maturation_filter.json") if candidate_dir else None,
        (raw_dir / f"{design_name}_maturation_filter.json") if raw_dir else None,
        (filtered_dir / f"{design_name}_maturation_filter.json") if filtered_dir else None,
    ]
    filter_path = next((path for path in filter_candidates if path and path.exists()), None)
    filter_payload = _read_json(filter_path)

    if not score_payload and isinstance(filter_payload.get("score_data"), dict):
        score_payload = dict(filter_payload["score_data"])

    return {
        "json_path": str(score_path or filter_path) if (score_path or filter_path) else None,
        "score_path": str(score_path) if score_path else None,
        "score_data": score_payload if isinstance(score_payload, dict) else {},
        "filter_path": str(filter_path) if filter_path else None,
        "filter_payload": filter_payload if isinstance(filter_payload, dict) else {},
    }


def _normalize_chain_ids(chain_hint: Any) -> list[str]:
    if chain_hint is None:
        return []
    if isinstance(chain_hint, str):
        parts = [part.strip() for part in chain_hint.split(",") if part.strip()]
    elif isinstance(chain_hint, (list, tuple, set)):
        parts = [str(part).strip() for part in chain_hint if str(part).strip()]
    else:
        parts = [str(chain_hint).strip()] if str(chain_hint).strip() else []
    return [chain_id for chain_id in dict.fromkeys(parts)]


def _infer_antibody_chain_ids(structure_path: Path, antibody_chain_hint: Any = None) -> list[str]:
    hinted_chain_ids = _normalize_chain_ids(antibody_chain_hint)
    if hinted_chain_ids:
        return hinted_chain_ids

    try:
        sequences = extract_sequence_from_pdb(str(structure_path))
        if not sequences:
            return []

        binder_chains = identify_binder_chains(sequences, str(structure_path))
        if binder_chains:
            return [chain_id for chain_id in dict.fromkeys(binder_chains.values()) if chain_id in sequences]

        fallback_chain_ids: list[str] = []
        if "H" in sequences:
            fallback_chain_ids.append("H")
        if "L" in sequences:
            fallback_chain_ids.append("L")
        if fallback_chain_ids:
            return fallback_chain_ids

        if len(sequences) == 1:
            return [next(iter(sequences.keys()))]
    except Exception:
        return []

    return []


@lru_cache(maxsize=16384)
def _compute_antibody_ca_rog_cached(
    structure_path_str: str,
    antibody_chain_hint_key: tuple[str, ...],
) -> Optional[float]:
    structure_path = Path(structure_path_str)
    chain_ids = list(antibody_chain_hint_key) or _infer_antibody_chain_ids(structure_path)
    if not chain_ids:
        return None

    try:
        structure = load_structure(str(structure_path))
        antibody_ca = structure[(structure.atom_name == "CA") & np.isin(structure.chain_id, chain_ids)]
        if len(antibody_ca) == 0:
            return None

        coords = antibody_ca.coord
        centroid = np.mean(coords, axis=0)
        squared_distances = np.sum((coords - centroid) ** 2, axis=1)
        return float(np.sqrt(np.mean(squared_distances)))
    except Exception:
        return None


def _compute_antibody_ca_rog(structure_path: Path, antibody_chain_hint: Any = None) -> Optional[float]:
    return _compute_antibody_ca_rog_cached(
        str(structure_path.expanduser().resolve()),
        tuple(_normalize_chain_ids(antibody_chain_hint)),
    )


def _rfantibody_cdr_refresh_required(sample_pdb_path: str | None) -> bool:
    """Detect stale review rows by checking whether a missing-annotation PDB still carries RF loop labels."""
    if not sample_pdb_path:
        return False

    try:
        return bool(_parse_hlt_cdr_lengths(Path(sample_pdb_path)))
    except Exception:
        return False


def _rfantibody_rog_refresh_required(
    sample_pdb_path: str | None,
    antibody_chain_hint: Any = None,
) -> bool:
    """Detect stale review rows by checking whether a missing-RoG PDB can yield antibody-only CA RoG."""
    if not sample_pdb_path:
        return False

    try:
        return _compute_antibody_ca_rog(Path(sample_pdb_path), antibody_chain_hint) is not None
    except Exception:
        return False


def _metadata_value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _rfantibody_review_metadata_refresh_required(
    row: Design,
    screening_by_name: dict[str, dict],
    screening_by_backbone: dict[int, dict],
) -> bool:
    sample_pdb_path = str(getattr(row, "pdb_path", "") or "").strip()
    if not sample_pdb_path:
        return False

    structure_path = Path(sample_pdb_path)
    screening = screening_row_for_structure(structure_path, screening_by_name, screening_by_backbone)
    rfa_trb = load_rfantibody_trb_summary(structure_path) or {}

    trb_field_sources = {
        "confidence_metrics": rfa_trb.get("rfa_metadata"),
        "rfa_hotspot_min_distance": safe_float(rfa_trb.get("rfa_hotspot_min_distance")),
        "rfa_hotspot_avg_min_distance": safe_float(rfa_trb.get("rfa_hotspot_avg_min_distance")),
        "rfa_runtime_seconds": safe_float(rfa_trb.get("rfa_runtime_seconds")),
        "rfa_device": rfa_trb.get("rfa_device"),
        "rfa_diffusion_steps": safe_int(rfa_trb.get("rfa_diffusion_steps")),
        "rfa_noise_scale_ca": safe_float(rfa_trb.get("rfa_noise_scale_ca")),
        "rfa_noise_scale_frame": safe_float(rfa_trb.get("rfa_noise_scale_frame")),
        "rfa_guide_scale": safe_float(rfa_trb.get("rfa_guide_scale")),
        "rfa_plddt_initial": safe_float(rfa_trb.get("rfa_plddt_initial")),
        "rfa_plddt_final": safe_float(rfa_trb.get("rfa_plddt_final")),
        "rfa_plddt_delta": safe_float(rfa_trb.get("rfa_plddt_delta")),
        "rfa_plddt_selected": safe_float(rfa_trb.get("rfa_plddt_selected")),
        "rfa_plddt_nonselected": safe_float(rfa_trb.get("rfa_plddt_nonselected")),
        "rfa_design_loops": rfa_trb.get("rfa_design_loops"),
        "rfa_hotspots": rfa_trb.get("rfa_hotspots"),
    }
    for field_name, source_value in trb_field_sources.items():
        if not _metadata_value_present(getattr(row, field_name, None)) and _metadata_value_present(source_value):
            return True

    if not isinstance(screening, dict):
        return False

    screening_field_sources = {
        "detected_antibody_chains": str(screening.get("detected_antibody_chains") or "").strip() or None,
        "detected_target_chain": str(screening.get("detected_target_chain") or "").strip() or None,
        "epitope_contact_count": safe_int(screening.get("epitope_contact_count")),
        "epitope_min_distance": safe_float(screening.get("epitope_min_distance")),
        "epitope_min_atom_distance": safe_float(screening.get("epitope_min_atom_distance")),
        "epitope_nearest_antibody_residue": screening.get("epitope_nearest_antibody_residue"),
        "epitope_nearest_target_residue": screening.get("epitope_nearest_target_residue"),
        "epitope_nearest_antibody_atom": screening.get("epitope_nearest_antibody_atom"),
        "epitope_nearest_target_atom": screening.get("epitope_nearest_target_atom"),
        "epitope_mapping_mode": screening.get("epitope_mapping_mode"),
        "epitope_centroid_distance": safe_float(screening.get("epitope_centroid_distance")),
        "target_contact_count": safe_int(screening.get("target_contact_count")),
        "target_min_distance": safe_float(screening.get("target_min_distance")),
        "target_min_atom_distance": safe_float(screening.get("target_min_atom_distance")),
        "target_nearest_antibody_residue": screening.get("target_nearest_antibody_residue"),
        "target_nearest_target_residue": screening.get("target_nearest_target_residue"),
        "target_nearest_antibody_atom": screening.get("target_nearest_antibody_atom"),
        "target_nearest_target_atom": screening.get("target_nearest_target_atom"),
        "target_centroid_distance": safe_float(screening.get("target_centroid_distance")),
        "antibody_residue_count": safe_int(screening.get("antibody_residue_count")),
        "target_residue_count": safe_int(screening.get("target_residue_count")),
        "epitope_residue_count": safe_int(screening.get("epitope_residue_count")),
        "screening_reason": str(screening.get("screening_reason") or "").strip() or None,
        "rfa_loop_metrics": _coerce_json_value(screening.get("rfa_loop_metrics")),
        "rfa_hotspot_metrics": _coerce_json_value(screening.get("rfa_hotspot_metrics")),
        "rfa_hotspot_covered_count": safe_int(screening.get("rfa_hotspot_covered_count")),
    }
    for field_name, source_value in screening_field_sources.items():
        if not _metadata_value_present(getattr(row, field_name, None)) and _metadata_value_present(source_value):
            return True

    screening_has_pass_fail = (
        screening.get("passed_screen") not in (None, "")
        or _metadata_value_present(screening_field_sources["screening_reason"])
    )
    if getattr(row, "passed_screen", None) is None and screening_has_pass_fail:
        return True

    return False


async def ensure_stage_review_rows(session: AsyncSession, job: Job, force: bool = False) -> int:
    stage = str(job.awaiting_stage or "").strip().lower()
    if stage not in REVIEWABLE_STAGES:
        return 0

    repaired_payload = refresh_gate_payload(job.awaiting_payload or {}, job.output_dir)
    job.awaiting_payload = repaired_payload
    uses_rfantibody_review = _uses_rfantibody_review(stage, job, repaired_payload)

    raw_dir = resolve_review_path(repaired_payload.get("raw_dir"), job.output_dir)
    filtered_dir = resolve_review_path(repaired_payload.get("filtered_dir"), job.output_dir)
    screening_by_name, screening_by_backbone = _load_screening_rows(job.output_dir)

    expected_files: list[tuple[str, Path]] = []
    if uses_rfantibody_review:
        raw_files = _iter_matching_files(raw_dir, STRUCTURE_PATTERNS) if raw_dir else []
        filtered_files = resolve_rfantibody_filtered_files(raw_dir, filtered_dir, screening_by_name, screening_by_backbone)
        expected_files = [("raw", path) for path in raw_files]
        expected_files.extend(("filtered", path) for path in filtered_files)
    elif stage == "post_rfantibody":
        candidate_dir = resolve_review_path(repaired_payload.get("candidate_dir") or repaired_payload.get("raw_dir"), job.output_dir)
        expected_files = [("candidate", path) for path in (_iter_matching_files(candidate_dir, STRUCTURE_PATTERNS) if candidate_dir else [])]
    else:
        candidate_dir = resolve_review_path(repaired_payload.get("candidate_dir"), job.output_dir)
        expected_files = [("candidate", path) for path in (_iter_matching_files(candidate_dir, STRUCTURE_PATTERNS) if candidate_dir else [])]

    expected_files = _dedupe_review_structures(expected_files)
    candidate_count = len(expected_files)

    existing_count = (
        await session.execute(
            select(func.count(Design.id)).where(
                Design.job_id == job.id,
                Design.source_stage == stage,
            )
        )
    ).scalar() or 0
    legacy_malformed_count = (
        await session.execute(
            select(func.count(Design.id)).where(
                Design.job_id == job.id,
                Design.source_stage.is_(None),
                Design.stage_family == "refinement",
                Design.stage_mode.in_(tuple(REVIEWABLE_STAGES)),
            )
        )
    ).scalar() or 0
    if legacy_malformed_count > 0:
        force = True

    if existing_count == candidate_count and candidate_count > 0 and not force:
        if uses_rfantibody_review:
            group_rows = (
                await session.execute(
                    select(Design.artifact_group, func.count(Design.id))
                    .where(
                        Design.job_id == job.id,
                        Design.source_stage == stage,
                    )
                    .group_by(Design.artifact_group)
                )
            ).all()
            group_counts = {str(group or ""): int(count or 0) for group, count in group_rows}
            expected_raw_count = sum(1 for artifact_group, _ in expected_files if artifact_group == "raw")
            expected_filtered_count = sum(1 for artifact_group, _ in expected_files if artifact_group == "filtered")
            populated_rf_metadata = (
                await session.execute(
                    select(func.count(Design.id)).where(
                        Design.job_id == job.id,
                        Design.source_stage == stage,
                        Design.rfa_plddt_final.is_not(None),
                    )
                )
            ).scalar() or 0
            has_legacy_candidate_rows = group_counts.get("candidate", 0) > 0
            raw_count_matches = group_counts.get("raw", 0) == expected_raw_count
            filtered_count_matches = group_counts.get("filtered", 0) == expected_filtered_count
            rf_metadata_partially_populated = 0 < populated_rf_metadata < existing_count
            if has_legacy_candidate_rows or not raw_count_matches or not filtered_count_matches or rf_metadata_partially_populated:
                force = True
        elif stage == "post_rfantibody":
            legacy_noncandidate_rows = (
                await session.execute(
                    select(func.count(Design.id)).where(
                        Design.job_id == job.id,
                        Design.source_stage == stage,
                        Design.artifact_group != "candidate",
                    )
                )
            ).scalar() or 0
            legacy_rfa_family_rows = (
                await session.execute(
                    select(func.count(Design.id)).where(
                        Design.job_id == job.id,
                        Design.source_stage == stage,
                        Design.stage_family == "rfantibody",
                    )
                )
            ).scalar() or 0
            if legacy_noncandidate_rows > 0 or legacy_rfa_family_rows > 0:
                force = True

    if existing_count == candidate_count and candidate_count > 0 and not force:
        populated_binder_lengths = (
            await session.execute(
                select(func.count(Design.id)).where(
                    Design.job_id == job.id,
                    Design.source_stage == stage,
                    Design.binder_length.is_not(None),
                )
            )
        ).scalar() or 0
        if uses_rfantibody_review:
            missing_cdr_sample = (
                await session.execute(
                    select(Design.pdb_path).where(
                        Design.job_id == job.id,
                        Design.source_stage == stage,
                        Design.cdr_h1_length.is_(None),
                        Design.cdr_h2_length.is_(None),
                        Design.cdr_h3_length.is_(None),
                        Design.cdr_l1_length.is_(None),
                        Design.cdr_l2_length.is_(None),
                        Design.cdr_l3_length.is_(None),
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if _rfantibody_cdr_refresh_required(missing_cdr_sample):
                force = True

            if not force:
                missing_rog_sample = (
                    await session.execute(
                        select(Design.pdb_path, Design.detected_antibody_chains).where(
                            Design.job_id == job.id,
                            Design.source_stage == stage,
                            Design.rog.is_(None),
                        ).limit(1)
                    )
                ).first()
                if missing_rog_sample and _rfantibody_rog_refresh_required(
                    missing_rog_sample[0],
                    missing_rog_sample[1],
                ):
                    force = True

            if not force:
                incomplete_rf_metadata_rows = (
                    await session.execute(
                        select(Design).where(
                            Design.job_id == job.id,
                            Design.source_stage == stage,
                            or_(
                                Design.confidence_metrics.is_(None),
                                Design.rfa_hotspot_min_distance.is_(None),
                                Design.rfa_hotspot_avg_min_distance.is_(None),
                                Design.rfa_plddt_final.is_(None),
                                Design.rfa_design_loops.is_(None),
                                Design.rfa_hotspots.is_(None),
                                Design.detected_antibody_chains.is_(None),
                                Design.detected_target_chain.is_(None),
                                Design.target_contact_count.is_(None),
                                Design.target_min_distance.is_(None),
                                Design.epitope_contact_count.is_(None),
                                Design.passed_screen.is_(None),
                                Design.screening_reason.is_(None),
                                Design.rfa_loop_metrics.is_(None),
                                Design.rfa_hotspot_metrics.is_(None),
                                Design.rfa_hotspot_covered_count.is_(None),
                            ),
                        ).limit(25)
                    )
                ).scalars().all()
                if any(
                    _rfantibody_review_metadata_refresh_required(
                        row,
                        screening_by_name,
                        screening_by_backbone,
                    )
                    for row in incomplete_rf_metadata_rows
                ):
                    force = True

        populated_geometry = (
            await session.execute(
                select(func.count(Design.id)).where(
                    Design.job_id == job.id,
                    Design.source_stage == stage,
                    Design.epitope_contact_count.is_not(None),
                    Design.target_contact_count.is_not(None),
                )
            )
        ).scalar() or 0
        populated_cdr_lengths = (
            await session.execute(
                select(func.count(Design.id)).where(
                    Design.job_id == job.id,
                    Design.source_stage == stage,
                    Design.cdr_h1_length.is_not(None),
                    Design.cdr_h2_length.is_not(None),
                    Design.cdr_h3_length.is_not(None),
                )
            )
        ).scalar() or 0

        if (
            populated_binder_lengths == existing_count
            and (stage != "post_fampnn" or (populated_geometry == existing_count and populated_cdr_lengths == existing_count))
            and not force
        ):
            return existing_count

    await session.execute(
        delete(Design).where(
            Design.job_id == job.id,
            or_(
                Design.source_stage == stage,
                and_(
                    Design.source_stage.is_(None),
                    Design.stage_family == "refinement",
                    Design.stage_mode.in_(tuple(REVIEWABLE_STAGES)),
                ),
            ),
        )
    )

    if not expected_files:
        return 0

    review_stage_family, review_stage_mode = _review_stage_identity(stage, job, repaired_payload)
    job_context = _job_stage_context(job)
    lineage_cache: dict[str, Optional[Design]] = {}
    rows: list[Design] = []

    for artifact_group, structure_path in expected_files:
        design_name = structure_path.stem
        backbone_id = parse_backbone_id(design_name)
        structure_cdr_lengths = _parse_hlt_cdr_lengths(structure_path)
        binder_length, antibody_type = _infer_binder_metrics(structure_path)
        lineage = await _resolve_parent_design_lineage(
            session,
            job_context,
            design_name,
            cache=lineage_cache,
        )
        rfa_trb = load_rfantibody_trb_summary(structure_path) if uses_rfantibody_review else {}
        if uses_rfantibody_review:
            avg_plddt = safe_float(rfa_trb.get("plddt_overall"))
            residue_plddt = rfa_trb.get("residue_plddt")
        else:
            avg_plddt, residue_plddt = extract_plddt_from_pdb(structure_path)
            avg_plddt = safe_float(avg_plddt)
            if avg_plddt is not None and avg_plddt <= 0:
                avg_plddt = None
                residue_plddt = None
        screening = screening_by_name.get(design_name) if uses_rfantibody_review else None
        if screening is None and backbone_id is not None and uses_rfantibody_review:
            screening = screening_by_backbone.get(backbone_id)
        antibody_ca_rog = None
        if uses_rfantibody_review:
            antibody_ca_rog = safe_float((screening or {}).get("antibody_ca_rog"))
            if antibody_ca_rog is None:
                antibody_ca_rog = _compute_antibody_ca_rog(
                    structure_path,
                    (screening or {}).get("detected_antibody_chains"),
                )

        json_path: Optional[Path] = None
        fampnn_psce = None
        mpnn_score = None
        boltzgen_metrics: dict[str, Any] = {}
        if stage == "post_fampnn":
            candidate_json = (raw_dir / f"{design_name}.json") if raw_dir else None
            if candidate_json and candidate_json.exists():
                json_path = candidate_json
                metrics = _read_json(candidate_json)
                fampnn_psce = safe_float(
                    metrics.get("fampnn_avg_psce")
                    or metrics.get("chain_avg_psce")
                    or metrics.get("seq_fampnn_psce")
                )
                mpnn_score = safe_float(metrics.get("mpnn_score"))
        elif stage == "post_boltzgen":
            boltzgen_metrics = _load_boltzgen_review_metrics(
                structure_path,
                candidate_dir=candidate_dir,
                raw_dir=raw_dir,
                filtered_dir=filtered_dir,
            )
            json_path_value = boltzgen_metrics.get("json_path")
            if json_path_value:
                json_path = Path(str(json_path_value))
        elif stage == "post_ppiflow_generator":
            ppiflow_metrics = _load_ppiflow_review_metrics(
                structure_path,
                candidate_dir=candidate_dir,
                raw_dir=raw_dir,
                filtered_dir=filtered_dir,
            )
            json_path_value = ppiflow_metrics.get("json_path")
            if json_path_value:
                json_path = Path(str(json_path_value))
        else:
            ppiflow_metrics = {}

        review_row = Design(
                id=_review_design_id(job.id, stage, artifact_group, design_name),
                job_id=job.id,
                name=design_name,
                pdb_path=str(structure_path),
                json_path=str(json_path) if json_path else None,
                plddt_overall=avg_plddt,
                rog=antibody_ca_rog,
                residue_plddt=residue_plddt,
                mpnn_score=mpnn_score,
                fampnn_psce=fampnn_psce,
                conf_score=safe_float(boltzgen_metrics.get("conf_score")),
                ptm=safe_float(boltzgen_metrics.get("ptm")),
                iptm=safe_float(boltzgen_metrics.get("iptm")),
                protein_iptm=safe_float(boltzgen_metrics.get("protein_iptm")),
                ligand_iptm=safe_float(boltzgen_metrics.get("ligand_iptm")),
                complex_iplddt=safe_float(boltzgen_metrics.get("complex_iplddt")),
                complex_ipde=safe_float(boltzgen_metrics.get("complex_ipde")),
                chains_ptm=_coerce_json_value(boltzgen_metrics.get("chains_ptm")),
                pair_chains_iptm=_coerce_json_value(boltzgen_metrics.get("pair_chains_iptm")),
                affinity_score=safe_float(boltzgen_metrics.get("affinity_score")),
                binder_probability=safe_float(boltzgen_metrics.get("binder_probability")),
                binder_length=binder_length,
                antibody_type=antibody_type,
                backbone_id=backbone_id,
                epitope_contact_count=safe_int((screening or {}).get("epitope_contact_count")),
                epitope_min_distance=safe_float((screening or {}).get("epitope_min_distance")),
                epitope_min_atom_distance=safe_float((screening or {}).get("epitope_min_atom_distance")),
                epitope_nearest_antibody_residue=(screening or {}).get("epitope_nearest_antibody_residue"),
                epitope_nearest_target_residue=(screening or {}).get("epitope_nearest_target_residue"),
                epitope_nearest_antibody_atom=(screening or {}).get("epitope_nearest_antibody_atom"),
                epitope_nearest_target_atom=(screening or {}).get("epitope_nearest_target_atom"),
                epitope_mapping_mode=(screening or {}).get("epitope_mapping_mode"),
                epitope_centroid_distance=safe_float((screening or {}).get("epitope_centroid_distance")),
                target_contact_count=safe_int((screening or {}).get("target_contact_count")),
                target_min_distance=safe_float((screening or {}).get("target_min_distance")),
                target_min_atom_distance=safe_float((screening or {}).get("target_min_atom_distance")),
                target_nearest_antibody_residue=(screening or {}).get("target_nearest_antibody_residue"),
                target_nearest_target_residue=(screening or {}).get("target_nearest_target_residue"),
                target_nearest_antibody_atom=(screening or {}).get("target_nearest_antibody_atom"),
                target_nearest_target_atom=(screening or {}).get("target_nearest_target_atom"),
                target_centroid_distance=safe_float((screening or {}).get("target_centroid_distance")),
                detected_antibody_chains=(screening or {}).get("detected_antibody_chains"),
                detected_target_chain=(screening or {}).get("detected_target_chain"),
                antibody_residue_count=safe_int((screening or {}).get("antibody_residue_count")),
                target_residue_count=safe_int((screening or {}).get("target_residue_count")),
                epitope_residue_count=safe_int((screening or {}).get("epitope_residue_count")),
                passed_screen=(
                    None
                    if not screening or (
                        (screening or {}).get("passed_screen") in (None, "")
                        and not (screening or {}).get("screening_reason")
                    )
                    else screening_row_passed(screening)
                ),
                screening_reason=(screening or {}).get("screening_reason"),
                rfa_loop_metrics=_coerce_json_value((screening or {}).get("rfa_loop_metrics")),
                rfa_hotspot_metrics=_coerce_json_value((screening or {}).get("rfa_hotspot_metrics")),
                rfa_hotspot_covered_count=safe_int((screening or {}).get("rfa_hotspot_covered_count")),
                rfa_hotspot_min_distance=safe_float(rfa_trb.get("rfa_hotspot_min_distance")),
                rfa_hotspot_avg_min_distance=safe_float(rfa_trb.get("rfa_hotspot_avg_min_distance")),
                rfa_runtime_seconds=safe_float(rfa_trb.get("rfa_runtime_seconds")),
                rfa_device=rfa_trb.get("rfa_device"),
                rfa_diffusion_steps=safe_int(rfa_trb.get("rfa_diffusion_steps")),
                rfa_noise_scale_ca=safe_float(rfa_trb.get("rfa_noise_scale_ca")),
                rfa_noise_scale_frame=safe_float(rfa_trb.get("rfa_noise_scale_frame")),
                rfa_guide_scale=safe_float(rfa_trb.get("rfa_guide_scale")),
                rfa_plddt_initial=safe_float(rfa_trb.get("rfa_plddt_initial")),
                rfa_plddt_final=safe_float(rfa_trb.get("rfa_plddt_final")),
                rfa_plddt_delta=safe_float(rfa_trb.get("rfa_plddt_delta")),
                rfa_plddt_selected=safe_float(rfa_trb.get("rfa_plddt_selected")),
                rfa_plddt_nonselected=safe_float(rfa_trb.get("rfa_plddt_nonselected")),
                rfa_design_loops=rfa_trb.get("rfa_design_loops"),
                rfa_hotspots=rfa_trb.get("rfa_hotspots"),
                confidence_metrics=(
                    rfa_trb.get("rfa_metadata")
                    if uses_rfantibody_review
                    else _coerce_json_value(boltzgen_metrics.get("confidence_metrics"))
                ),
                cdr_h1_length=structure_cdr_lengths.get("H1"),
                cdr_h2_length=structure_cdr_lengths.get("H2"),
                cdr_h3_length=structure_cdr_lengths.get("H3"),
                cdr_l1_length=structure_cdr_lengths.get("L1"),
                cdr_l2_length=structure_cdr_lengths.get("L2"),
                cdr_l3_length=structure_cdr_lengths.get("L3"),
                stage_family=review_stage_family,
                stage_mode=review_stage_mode,
                source_stage=stage,
                artifact_class=infer_antibody_artifact_class_from_stage(review_stage_family, review_stage_mode),
                artifact_group=artifact_group,
                created_at=datetime.utcnow(),
        )
        if stage == "post_ppiflow_generator":
            _apply_ppiflow_score_fields(review_row, ppiflow_metrics.get("score_data") or {})
            _apply_ppiflow_filter_fields(review_row, ppiflow_metrics.get("filter_payload") or {})

        rows.append(review_row)
        for field_name, field_value in _design_lineage_fields(job_context, lineage).items():
            if getattr(review_row, field_name, None) in (None, "", [], {}, ()):
                setattr(review_row, field_name, field_value)
        _inherit_source_design_metrics(
            review_row,
            lineage.get("source_design"),
            structure_path=structure_path,
        )

    session.add_all(rows)
    await session.flush()
    await session.commit()
    return len(rows)
