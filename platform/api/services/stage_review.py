"""
Helpers for interactive stage-review publication.

These helpers repair racy gate payloads from the filesystem and materialize
parent-visible review rows for stages that pause before final ingestion.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, Job
from paths import get_data_root, resolve_allowed_path, to_allowed_relative
from services.result_ingester import (
    _parse_hlt_cdr_lengths,
    extract_plddt_from_pdb,
    parse_backbone_id,
    safe_float,
    safe_int,
)

REVIEWABLE_STAGES = {"post_rfantibody", "post_fampnn"}
STRUCTURE_PATTERNS = ("*.pdb", "*.cif")
METRIC_PATTERNS = ("*.json", "*.csv", "*.tsv")


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


def _iter_matching_files(directory: Path, patterns: Iterable[str]) -> list[Path]:
    files: set[Path] = set()
    if not directory.exists():
        return []
    for pattern in patterns:
        files.update(path.resolve() for path in directory.glob(pattern))
    return sorted(files)


def list_preview_files(directory: Path | None, patterns: Iterable[str], limit: int = 25) -> list[str]:
    if not directory:
        return []
    return [normalize_review_path(path) for path in _iter_matching_files(directory, patterns)[:limit] if path]


def count_files(directory: Path | None, patterns: Iterable[str]) -> int:
    if not directory:
        return 0
    return len(_iter_matching_files(directory, patterns))


def summarize_backbones(directory: Path | None, patterns: Iterable[str], preview_limit: int = 3) -> Optional[dict]:
    if not directory or not directory.exists():
        return None

    unique_files = _iter_matching_files(directory, patterns)
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


def refresh_gate_payload(payload: Optional[dict], output_dir: str | None = None) -> dict:
    current = dict(payload or {})
    candidate_dir = resolve_review_path(current.get("candidate_dir"), output_dir)
    raw_dir = resolve_review_path(current.get("raw_dir"), output_dir)
    filtered_dir = resolve_review_path(current.get("filtered_dir"), output_dir)
    stage = str(current.get("stage") or "").strip().lower()

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

    current["filtered_dir"] = normalize_review_path(filtered_dir)
    current["filtered_candidate_count"] = count_files(filtered_dir, STRUCTURE_PATTERNS) if filtered_dir else None
    current["filtered_backbone_summary"] = summarize_backbones(filtered_dir, STRUCTURE_PATTERNS) if filtered_dir else None
    current["filtered_metric_count"] = count_files(filtered_dir, METRIC_PATTERNS) if filtered_dir else None
    current["review_grouping"] = "backbone_id" if stage == "post_rfantibody" else current.get("review_grouping")
    return current


def infer_antibody_stage_state(job: Job, completed: list[str], stage_outputs: dict[str, list[str]]) -> Tuple[list[str], dict[str, list[str]]]:
    if str(job.mode or "").strip() != "antibody_denovo_pipeline":
        return completed, stage_outputs

    output_path = resolve_output_dir(job.output_dir)
    if output_path is None or not output_path.exists():
        return completed, stage_outputs

    inferred: dict[str, Path] = {}
    rfa_dir = output_path / "collected" / "rfantibody"
    if rfa_dir.exists():
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


def nextflow_history_status(job: Job) -> str:
    output_path = resolve_output_dir(job.output_dir)
    if output_path is None:
        return ""
    history_path = output_path / ".nextflow" / "history"
    if not history_path.exists():
        return ""
    try:
        lines = history_path.read_text(errors="ignore").splitlines()
    except Exception:
        return ""
    if not lines:
        return ""
    parts = lines[-1].split("\t")
    if len(parts) <= 3:
        return ""
    return parts[3].strip().upper()


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


async def ensure_stage_review_rows(session: AsyncSession, job: Job, force: bool = False) -> int:
    stage = str(job.awaiting_stage or "").strip().lower()
    if stage not in REVIEWABLE_STAGES:
        return 0

    repaired_payload = refresh_gate_payload(job.awaiting_payload or {}, job.output_dir)
    job.awaiting_payload = repaired_payload

    candidate_dir = resolve_review_path(repaired_payload.get("candidate_dir"), job.output_dir)
    raw_dir = resolve_review_path(repaired_payload.get("raw_dir"), job.output_dir)
    candidate_files = _iter_matching_files(candidate_dir, STRUCTURE_PATTERNS) if candidate_dir else []
    candidate_count = len(candidate_files)

    existing_count = (
        await session.execute(
            select(func.count(Design.id)).where(
                Design.job_id == job.id,
                Design.source_stage == stage,
            )
        )
    ).scalar() or 0

    if existing_count == candidate_count and candidate_count > 0 and not force:
        return existing_count

    await session.execute(
        delete(Design).where(
            Design.job_id == job.id,
            Design.source_stage == stage,
        )
    )

    if not candidate_files:
        return 0

    screening_by_name, screening_by_backbone = _load_screening_rows(job.output_dir)
    rows: list[Design] = []

    for structure_path in candidate_files:
        design_name = structure_path.stem
        backbone_id = parse_backbone_id(design_name)
        structure_cdr_lengths = _parse_hlt_cdr_lengths(structure_path)
        avg_plddt, residue_plddt = extract_plddt_from_pdb(structure_path)
        avg_plddt = safe_float(avg_plddt)
        if avg_plddt is not None and avg_plddt <= 0:
            avg_plddt = None
            residue_plddt = None
        screening = screening_by_name.get(design_name)
        if screening is None and backbone_id is not None:
            screening = screening_by_backbone.get(backbone_id)

        json_path: Optional[Path] = None
        fampnn_psce = None
        mpnn_score = None
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

        rows.append(
            Design(
                id=str(uuid.uuid4()),
                job_id=job.id,
                name=design_name,
                pdb_path=str(structure_path),
                json_path=str(json_path) if json_path else None,
                plddt_overall=avg_plddt,
                residue_plddt=residue_plddt,
                mpnn_score=mpnn_score,
                fampnn_psce=fampnn_psce,
                backbone_id=backbone_id,
                epitope_contact_count=safe_int((screening or {}).get("epitope_contact_count")),
                epitope_min_distance=safe_float((screening or {}).get("epitope_min_distance")),
                epitope_min_atom_distance=safe_float((screening or {}).get("epitope_min_atom_distance")),
                epitope_nearest_antibody_residue=(screening or {}).get("epitope_nearest_antibody_residue"),
                epitope_nearest_target_residue=(screening or {}).get("epitope_nearest_target_residue"),
                epitope_nearest_antibody_atom=(screening or {}).get("epitope_nearest_antibody_atom"),
                epitope_nearest_target_atom=(screening or {}).get("epitope_nearest_target_atom"),
                target_contact_count=safe_int((screening or {}).get("target_contact_count")),
                target_min_distance=safe_float((screening or {}).get("target_min_distance")),
                target_min_atom_distance=safe_float((screening or {}).get("target_min_atom_distance")),
                target_nearest_antibody_residue=(screening or {}).get("target_nearest_antibody_residue"),
                target_nearest_target_residue=(screening or {}).get("target_nearest_target_residue"),
                target_nearest_antibody_atom=(screening or {}).get("target_nearest_antibody_atom"),
                target_nearest_target_atom=(screening or {}).get("target_nearest_target_atom"),
                screening_reason=(screening or {}).get("screening_reason"),
                cdr_h1_length=structure_cdr_lengths.get("H1"),
                cdr_h2_length=structure_cdr_lengths.get("H2"),
                cdr_h3_length=structure_cdr_lengths.get("H3"),
                cdr_l1_length=structure_cdr_lengths.get("L1"),
                cdr_l2_length=structure_cdr_lengths.get("L2"),
                cdr_l3_length=structure_cdr_lengths.get("L3"),
                source_stage=stage,
                artifact_group="candidate",
                created_at=datetime.utcnow(),
            )
        )

    session.add_all(rows)
    await session.flush()
    return len(rows)
