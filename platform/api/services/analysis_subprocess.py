from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import traceback
from typing import Any

from sqlalchemy import select

from database import AnalysisRun, Design, Job, async_session
from paths import resolve_allowed_path, resolve_runtime_data_path
from services.analysis_registry import (
    ANTIBODY_ANNOTATION_PACK_ANALYSIS,
    CHAIN_METRICS_ANALYSIS,
    CONTACT_MAP_ANALYSIS,
    FAMPNN_PSCE_PROFILE_ANALYSIS,
    JOB_AA_COMPOSITION_ANALYSIS,
    IPSAE_INTERFACE_ANALYSIS,
    JOB_CDR_LOGO_PACK_ANALYSIS,
    JOB_CORRELATION_MATRIX_ANALYSIS,
    PAE_MATRIX_ANALYSIS,
    STRUCTURE_SUMMARY_ANALYSIS,
)
from services.analysis_runs import build_artifact_manifest_for_run
from services.aligned_error_utils import load_aligned_error_artifact
from services.ipsae import compute_ipsae_interface
from services.cdr_annotator import annotate_pdb, extract_sequence_from_pdb, identify_binder_chains
from services.structure_utils import (
    compute_contact_map,
    compute_gyration_radius,
    get_chain_ids,
    get_per_chain_fampnn_psce,
    get_per_chain_metrics,
    get_residue_count,
    get_secondary_structure,
)


STANDARD_AAS = "ACDEFGHIKLMNPQRSTVWY"


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _safe_allowed_relative(path_str: str | None) -> str | None:
    if not path_str:
        return None
    try:
        from paths import to_allowed_relative

        return to_allowed_relative(Path(path_str))
    except Exception:
        return None


def _round_nullable(value: Any, digits: int) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not (numeric == numeric and abs(numeric) != float("inf")):
        return None
    return round(numeric, digits)


def _chain_info(chain_metrics: dict[str, Any], chain_id: str | None) -> dict[str, Any]:
    if not chain_id:
        return {}
    value = chain_metrics.get(chain_id)
    return value if isinstance(value, dict) else {}


def _annotation_field(annotation: Any, field: str) -> Any:
    if annotation is None:
        return None
    return getattr(annotation, field, None)


def _design_chain_lists(design: Design) -> tuple[list[str], list[str]]:
    binder_chains = [chain.strip() for chain in str(design.detected_antibody_chains or '').split(',') if chain.strip()]
    target_chains = [chain.strip() for chain in str(design.detected_target_chain or '').split(',') if chain.strip()]
    return binder_chains, target_chains


def _build_antibody_overlay_selections(
    *,
    imgt_url: str | None,
    annotation: Any,
    binder_chains: dict[str, str],
    chain_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    if annotation is None:
        return []

    selections: list[dict[str, Any]] = []
    region_specs = [
        ("H1", "H", _annotation_field(annotation, "cdr_h1_range"), _annotation_field(annotation, "cdr_h1_seq_range")),
        ("H2", "H", _annotation_field(annotation, "cdr_h2_range"), _annotation_field(annotation, "cdr_h2_seq_range")),
        ("H3", "H", _annotation_field(annotation, "cdr_h3_range"), _annotation_field(annotation, "cdr_h3_seq_range")),
        ("L1", "L", _annotation_field(annotation, "cdr_l1_range"), _annotation_field(annotation, "cdr_l1_seq_range")),
        ("L2", "L", _annotation_field(annotation, "cdr_l2_range"), _annotation_field(annotation, "cdr_l2_seq_range")),
        ("L3", "L", _annotation_field(annotation, "cdr_l3_range"), _annotation_field(annotation, "cdr_l3_seq_range")),
    ]

    for region, chain_type, imgt_range, seq_range in region_specs:
        if imgt_url and imgt_range:
            selections.append(
                {
                    "region": region,
                    "chain_id": chain_type,
                    "start_residue_number": int(imgt_range[0]),
                    "end_residue_number": int(imgt_range[1]),
                }
            )
            continue

        chain_id = binder_chains.get(chain_type)
        residue_numbers = _chain_info(chain_metrics, chain_id).get("residue_numbers")
        if not chain_id or not seq_range or not isinstance(residue_numbers, list):
            continue

        start_idx, end_idx = int(seq_range[0]), int(seq_range[1])
        if start_idx < 0 or end_idx < start_idx or end_idx >= len(residue_numbers):
            continue

        selections.append(
            {
                "region": region,
                "chain_id": str(chain_id),
                "start_residue_number": int(residue_numbers[start_idx]),
                "end_residue_number": int(residue_numbers[end_idx]),
            }
        )

    return selections


def _confidence_file_candidates(pdb_path: Path) -> list[Path]:
    parent_dir = pdb_path.parent
    design_stem = pdb_path.stem
    candidates: list[Path] = []
    candidates.extend(sorted(parent_dir.glob("*_confidences.json")))
    candidates.extend(sorted(parent_dir.parent.glob("*_confidences.json")))
    candidates.extend(sorted(parent_dir.glob(f"confidence_{design_stem}.json")))
    candidates.extend(sorted(parent_dir.glob("confidence_*.json")))
    candidates.extend(sorted(parent_dir.parent.glob(f"confidence_{design_stem}.json")))
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = str(candidate.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(candidate)
    return deduped


def _choose_confidence_file(design: Design) -> Path | None:
    pdb_path = _resolve_design_structure_path(design)
    candidates = _confidence_file_candidates(pdb_path)
    for candidate in candidates:
        if pdb_path.stem in candidate.stem:
            return candidate
    return candidates[0] if candidates else None


def _resolve_design_structure_path(design: Design) -> Path:
    if not design.pdb_path:
        raise ValueError(f"Design {design.id} has no structure file")
    return resolve_runtime_data_path(design.pdb_path)


def _resolve_design_aligned_error_path(design: Design) -> Path:
    if not design.aligned_error_path:
        raise ValueError(f"Design {design.id} has no aligned-error artifact")
    return resolve_runtime_data_path(design.aligned_error_path)


def _compute_structure_summary(design: Design) -> tuple[dict[str, Any], dict[str, Any], Any]:
    structure_path = _resolve_design_structure_path(design)
    result = {
        "design_id": design.id,
        "design_name": design.name,
        "residue_count": get_residue_count(structure_path),
        "chain_ids": [str(chain_id) for chain_id in get_chain_ids(structure_path)],
        "gyration_radius": compute_gyration_radius(structure_path),
        "secondary_structure": get_secondary_structure(structure_path),
    }
    summary = {
        "residue_count": result["residue_count"],
        "chain_count": len(result["chain_ids"]),
        "gyration_radius": result["gyration_radius"],
    }
    return result, summary, result


def _compute_contact_map(design: Design, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Any]:
    structure_path = _resolve_design_structure_path(design)
    max_size = int((params or {}).get("max_size") or 300)
    distance_matrix, residue_numbers, chain_ids = compute_contact_map(structure_path, max_size=max_size)
    if distance_matrix is None or residue_numbers is None or chain_ids is None:
        raise ValueError("Could not compute contact map for this structure")
    result = {
        "design_id": design.id,
        "design_name": design.name,
        "distance_matrix": distance_matrix,
        "residue_numbers": residue_numbers,
        "chain_ids": chain_ids,
        "size": len(distance_matrix),
    }
    summary = {
        "size": result["size"],
        "max_size": max_size,
        "chain_count": len(sorted(set(chain_ids))),
    }
    return result, summary, None


def _compute_chain_metrics(design: Design) -> tuple[dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
    structure_path = _resolve_design_structure_path(design)
    metrics = get_per_chain_metrics(str(structure_path))
    result = metrics or {}
    polymer_chains = [value for value in result.values() if isinstance(value, dict) and value.get("type") != "ligand"]
    summary = {
        "chain_count": len(result),
        "polymer_chain_count": len(polymer_chains),
        "residue_count": sum(int(value.get("length") or 0) for value in polymer_chains if isinstance(value, dict)),
    }
    return result, summary, None, {"chain_metrics": result}


def _compute_fampnn_psce_profile(design: Design, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Any]:
    structure_path = _resolve_design_structure_path(design)
    ignore_cbeta = bool((params or {}).get("ignore_cbeta", False))
    chains = get_per_chain_fampnn_psce(str(structure_path), ignore_cbeta=ignore_cbeta) or {}
    all_scores = [
        float(score)
        for chain in chains.values()
        if isinstance(chain, dict)
        for score in (chain.get("psce") or [])
        if isinstance(score, (int, float))
    ]
    residue_count = sum(int(chain.get("length") or 0) for chain in chains.values() if isinstance(chain, dict))
    result = {
        "design_id": design.id,
        "design_name": design.name,
        "metric_kind": "fampnn_psce",
        "direction": "lower_is_better",
        "scope": "all_chains",
        "ignore_cbeta": ignore_cbeta,
        "chains": chains,
    }
    summary = {
        "chain_count": len(chains),
        "residue_count": residue_count,
        "avg_psce": round(sum(all_scores) / len(all_scores), 4) if all_scores else None,
        "max_psce": round(max(all_scores), 4) if all_scores else None,
        "min_psce": round(min(all_scores), 4) if all_scores else None,
        "ignore_cbeta": ignore_cbeta,
    }
    return result, summary, result


def _compute_pae_matrix(design: Design, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Any]:
    structure_path = _resolve_design_structure_path(design)
    aligned_error_path = _resolve_design_aligned_error_path(design)
    max_size = int((params or {}).get("max_size") or 200)
    artifact = load_aligned_error_artifact(
        aligned_error_path=str(aligned_error_path),
        aligned_error_format=design.aligned_error_format,
        matrix_key=design.aligned_error_key,
        structure_path=str(structure_path),
    )
    pae_matrix = artifact.matrix.tolist()
    size = len(pae_matrix)
    if size > max_size:
        step = max(1, size // max_size)
        pae_matrix = [[pae_matrix[i][j] for j in range(0, size, step)] for i in range(0, size, step)]
        size = len(pae_matrix)

    result = {
        "design_id": design.id,
        "design_name": design.name,
        "pae_matrix": pae_matrix,
        "size": size,
        "source_mode": artifact.format,
        "aligned_error_path": _safe_allowed_relative(str(artifact.path)),
    }
    summary = {
        "size": size,
        "max_size": max_size,
        "source_mode": artifact.format,
    }
    return result, summary, None


def _compute_ipsae_interface(design: Design, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
    structure_path = _resolve_design_structure_path(design)
    aligned_error_path = _resolve_design_aligned_error_path(design)
    artifact = load_aligned_error_artifact(
        aligned_error_path=str(aligned_error_path),
        aligned_error_format=design.aligned_error_format,
        matrix_key=design.aligned_error_key,
        structure_path=str(structure_path),
    )
    binder_chains, target_chains = _design_chain_lists(design)
    result = compute_ipsae_interface(
        artifact,
        pae_cutoff=float((params or {}).get("pae_cutoff") or 10.0),
        dist_cutoff=float((params or {}).get("dist_cutoff") or 10.0),
        binder_chains=binder_chains or None,
        target_chains=target_chains or None,
    )
    summary = {
        "ipsae": result.get("ipsae"),
        "ipsae_chain_pair": result.get("ipsae_chain_pair"),
        "pair_count": len(result.get("pair_scores") or []),
        "pae_cutoff": result.get("pae_cutoff"),
        "dist_cutoff": result.get("dist_cutoff"),
    }
    design_updates = {
        "ipsae": result.get("ipsae"),
        "ipsae_binder_to_target": result.get("ipsae_binder_to_target"),
        "ipsae_target_to_binder": result.get("ipsae_target_to_binder"),
        "ipsae_d0chn": result.get("ipsae_d0chn"),
        "ipsae_d0dom": result.get("ipsae_d0dom"),
        "ipsae_chain_pair": result.get("ipsae_chain_pair"),
        "ipsae_pae_cutoff": result.get("pae_cutoff"),
        "ipsae_dist_cutoff": result.get("dist_cutoff"),
    }
    return result, summary, result, design_updates


def _compute_antibody_annotation_pack(design: Design) -> tuple[dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
    structure_path = _resolve_design_structure_path(design)
    sequences = extract_sequence_from_pdb(str(structure_path))
    binder_chains = identify_binder_chains(sequences, str(structure_path)) if sequences else {}
    detected_antibody_chains = ",".join(
        binder_chains[chain_type] for chain_type in ("H", "L") if binder_chains.get(chain_type)
    ) or None

    annotation = annotate_pdb(str(structure_path), binder_chains or None)
    annotation_payload = annotation.to_dict() if annotation else {}
    chain_metrics = design.chain_metrics if isinstance(design.chain_metrics, dict) else get_per_chain_metrics(str(structure_path))

    imgt_url = None
    if "_imgt" in structure_path.name:
        imgt_url = f"/api/designs/{design.id}/pdb"
    else:
        imgt_path = structure_path.parent / f"{structure_path.stem}_imgt.pdb"
        if imgt_path.exists():
            imgt_url = f"/api/designs/{design.id}/pdb-imgt"

    overlay_selections = _build_antibody_overlay_selections(
        imgt_url=imgt_url,
        annotation=annotation,
        binder_chains=binder_chains,
        chain_metrics=chain_metrics if isinstance(chain_metrics, dict) else {},
    )

    cdrs = {
        "H1": _annotation_field(annotation, "cdr_h1"),
        "H2": _annotation_field(annotation, "cdr_h2"),
        "H3": _annotation_field(annotation, "cdr_h3"),
        "L1": _annotation_field(annotation, "cdr_l1"),
        "L2": _annotation_field(annotation, "cdr_l2"),
        "L3": _annotation_field(annotation, "cdr_l3"),
    }
    cdr_lengths = {
        "H1": _annotation_field(annotation, "cdr_h1_length"),
        "H2": _annotation_field(annotation, "cdr_h2_length"),
        "H3": _annotation_field(annotation, "cdr_h3_length"),
        "L1": _annotation_field(annotation, "cdr_l1_length"),
        "L2": _annotation_field(annotation, "cdr_l2_length"),
        "L3": _annotation_field(annotation, "cdr_l3_length"),
    }

    result = {
        "design_id": design.id,
        "cdrs": cdrs,
        "cdr_lengths": cdr_lengths,
        "binder_length": _annotation_field(annotation, "binder_length") or design.binder_length,
        "antibody_type": _annotation_field(annotation, "antibody_type") or design.antibody_type,
        "humanness_score": design.humanness_score,
        "stability_data": design.stability_data,
        "imgt_pdb_url": imgt_url,
        "detected_antibody_chains": detected_antibody_chains or design.detected_antibody_chains,
        "overlay_selections": overlay_selections,
        "framework_regions": {
            "fr2_contacts": _annotation_field(annotation, "fr2_contacts") or design.fr2_contacts,
            "de_loop": _annotation_field(annotation, "de_loop") or design.de_loop,
            "fr3_contacts": _annotation_field(annotation, "fr3_contacts") or design.fr3_contacts,
            "fr4_contacts": _annotation_field(annotation, "fr4_contacts") or design.fr4_contacts,
        },
        "binder_chains": binder_chains,
    }
    summary = {
        "detected_antibody_chains": result["detected_antibody_chains"],
        "overlay_count": len(overlay_selections),
        "antibody_type": result["antibody_type"],
        "binder_length": result["binder_length"],
    }
    design_updates = {
        "detected_antibody_chains": result["detected_antibody_chains"],
        "binder_length": result["binder_length"],
        "antibody_type": result["antibody_type"],
        "cdr_h1": cdrs["H1"],
        "cdr_h2": cdrs["H2"],
        "cdr_h3": cdrs["H3"],
        "cdr_l1": cdrs["L1"],
        "cdr_l2": cdrs["L2"],
        "cdr_l3": cdrs["L3"],
        "cdr_h1_length": cdr_lengths["H1"],
        "cdr_h2_length": cdr_lengths["H2"],
        "cdr_h3_length": cdr_lengths["H3"],
        "cdr_l1_length": cdr_lengths["L1"],
        "cdr_l2_length": cdr_lengths["L2"],
        "cdr_l3_length": cdr_lengths["L3"],
        "fr2_contacts": result["framework_regions"]["fr2_contacts"],
        "de_loop": result["framework_regions"]["de_loop"],
        "fr3_contacts": result["framework_regions"]["fr3_contacts"],
        "fr4_contacts": result["framework_regions"]["fr4_contacts"],
    }
    return result, summary, result, design_updates


JOB_CORRELATION_METRICS = (
    "plddt_overall",
    "plddt_binder",
    "pae_overall",
    "pae_interaction",
    "rmsd_binder",
    "rmsd_overall",
    "mpnn_score",
    "conf_score",
    "ptm",
    "rog",
    "ligand_iptm",
    "affinity_score",
    "binder_probability",
)


def _metric_value(design: Design, metric_name: str) -> float | None:
    value = getattr(design, metric_name, None)
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not (numeric == numeric and abs(numeric) != float("inf")):
        return None
    return numeric


def _extract_metric_values(designs: list[Design], metric_name: str) -> list[float]:
    values: list[float] = []
    for design in designs:
        value = _metric_value(design, metric_name)
        if value is not None:
            values.append(value)
    return values


def _extract_metric_pairs(designs: list[Design], metric_x: str, metric_y: str) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for design in designs:
        value_x = _metric_value(design, metric_x)
        value_y = _metric_value(design, metric_y)
        if value_x is None or value_y is None:
            continue
        pairs.append((value_x, value_y))
    return pairs


def _pearson_r(values_x: list[float], values_y: list[float]) -> float:
    if len(values_x) < 3 or len(values_y) < 3:
        return 0.0
    try:
        import numpy as np

        x_arr = np.array(values_x)
        y_arr = np.array(values_y)
        mask = ~(np.isnan(x_arr) | np.isnan(y_arr))
        x_arr = x_arr[mask]
        y_arr = y_arr[mask]
        if len(x_arr) < 3:
            return 0.0
        value = float(np.corrcoef(x_arr, y_arr)[0, 1])
        if value != value:
            return 0.0
        return value
    except Exception:
        return 0.0


async def _job_scope_designs(session, job: Job, params: dict[str, Any], columns: tuple[Any, ...]) -> list[Design]:
    include_children = bool(params.get("include_children", True))
    requested_design_ids = [str(design_id) for design_id in (params.get("design_ids") or []) if str(design_id).strip()]

    job_ids = [str(job.id)]
    if include_children:
        child_result = await session.execute(select(Job.id).where(Job.parent_job_id == str(job.id)))
        job_ids.extend(str(row[0]) for row in child_result.all())

    query = select(Design).where(Design.job_id.in_(job_ids)).order_by(Design.id.asc())
    if requested_design_ids:
        query = query.where(Design.id.in_(requested_design_ids))
    if columns:
        from sqlalchemy.orm import load_only

        query = query.options(load_only(*columns))
    result = await session.execute(query)
    return list(result.scalars().all())


async def _compute_job_correlation_matrix(session, job: Job, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Any]:
    designs = await _job_scope_designs(
        session,
        job,
        params,
        (
            Design.id,
            Design.plddt_overall,
            Design.plddt_binder,
            Design.pae_overall,
            Design.pae_interaction,
            Design.rmsd_binder,
            Design.rmsd_overall,
            Design.mpnn_score,
            Design.conf_score,
            Design.ptm,
            Design.rog,
            Design.ligand_iptm,
            Design.affinity_score,
            Design.binder_probability,
        ),
    )
    metric_names = [metric_name for metric_name in JOB_CORRELATION_METRICS if len(_extract_metric_values(designs, metric_name)) >= 5]
    matrix: list[list[float]] = []
    sample_sizes: list[list[int]] = []

    for metric_x in metric_names:
        row: list[float] = []
        size_row: list[int] = []
        for metric_y in metric_names:
            pairs = _extract_metric_pairs(designs, metric_x, metric_y)
            if metric_x == metric_y:
                row.append(1.0)
                size_row.append(len(pairs))
                continue
            row.append(round(_pearson_r([value_x for value_x, _value_y in pairs], [value_y for _value_x, value_y in pairs]), 4))
            size_row.append(len(pairs))
        matrix.append(row)
        sample_sizes.append(size_row)

    result = {
        "job_id": job.id,
        "metrics": metric_names,
        "matrix": matrix,
        "sample_sizes": sample_sizes,
    }
    summary = {
        "metric_count": len(metric_names),
        "design_count": len(designs),
    }
    return result, summary, result


async def _compute_job_aa_composition(session, job: Job, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Any]:
    designs = await _job_scope_designs(
        session,
        job,
        params,
        (
            Design.id,
            Design.cdr_h1,
            Design.cdr_h2,
            Design.cdr_h3,
            Design.cdr_l1,
            Design.cdr_l2,
            Design.cdr_l3,
        ),
    )
    cdr_fields = ["cdr_h1", "cdr_h2", "cdr_h3", "cdr_l1", "cdr_l2", "cdr_l3"]
    overall_counts = {aa: 0 for aa in STANDARD_AAS}
    by_cdr: list[dict[str, Any]] = []

    for cdr_name in cdr_fields:
        cdr_counts = {aa: 0 for aa in STANDARD_AAS}
        total = 0
        for design in designs:
            seq = getattr(design, cdr_name, None)
            if not seq:
                continue
            for aa in str(seq).upper():
                if aa in cdr_counts:
                    cdr_counts[aa] += 1
                    overall_counts[aa] += 1
                    total += 1
        if total > 0:
            by_cdr.append(
                {
                    "cdr_name": cdr_name.upper().replace("_", "-"),
                    "total_residues": total,
                    "composition": [
                        {"aa": aa, "count": count, "frequency": round(count / total, 4)}
                        for aa, count in sorted(cdr_counts.items())
                        if count > 0
                    ],
                }
            )

    overall_total = sum(overall_counts.values())
    result = {
        "job_id": job.id,
        "overall": [
            {"aa": aa, "count": count, "frequency": round(count / overall_total, 4) if overall_total else 0.0}
            for aa, count in sorted(overall_counts.items())
            if count > 0
        ],
        "by_cdr": by_cdr,
    }
    summary = {
        "design_count": len(designs),
        "overall_residue_total": overall_total,
        "cdr_count": len(by_cdr),
    }
    return result, summary, result


async def _compute_job_cdr_logo_pack(session, job: Job, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Any]:
    designs = await _job_scope_designs(
        session,
        job,
        params,
        (
            Design.id,
            Design.cdr_h1,
            Design.cdr_h2,
            Design.cdr_h3,
            Design.cdr_l1,
            Design.cdr_l2,
            Design.cdr_l3,
        ),
    )
    cdr_fields = ["cdr_h1", "cdr_h2", "cdr_h3", "cdr_l1", "cdr_l2", "cdr_l3"]
    logos: list[dict[str, Any]] = []
    for cdr_name in cdr_fields:
        sequences = [str(getattr(design, cdr_name)).upper() for design in designs if getattr(design, cdr_name, None)]
        if len(sequences) < 2:
            continue
        lengths = [len(sequence) for sequence in sequences]
        modal_length = max(set(lengths), key=lengths.count)
        aligned = [sequence for sequence in sequences if len(sequence) == modal_length]
        if len(aligned) < 2:
            continue

        positions: list[dict[str, Any]] = []
        consensus = ""
        for index in range(modal_length):
            counts = {aa: 0 for aa in STANDARD_AAS}
            for sequence in aligned:
                aa = sequence[index]
                if aa in counts:
                    counts[aa] += 1
            total = sum(counts.values())
            frequencies = {aa: round(count / total, 4) for aa, count in counts.items() if count > 0}
            positions.append({"position": index + 1, "frequencies": frequencies})
            if frequencies:
                consensus += max(frequencies, key=frequencies.get)

        logos.append(
            {
                "cdr_name": cdr_name.upper().replace("_", "-"),
                "length": modal_length,
                "positions": positions,
                "consensus": consensus,
                "sequence_count": len(aligned),
            }
        )

    result = {
        "job_id": job.id,
        "logos": logos,
    }
    summary = {
        "design_count": len(designs),
        "logo_count": len(logos),
    }
    return result, summary, result


async def _mark_failed(run_id: str, message: str) -> int:
    async with async_session() as session:
        result = await session.execute(select(AnalysisRun).where(AnalysisRun.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            return 1
        run.status = "failed"
        run.error_message = message[:4000]
        run.completed_at = datetime.utcnow()
        await session.commit()
    return 1


async def _run_analysis(run_id: str) -> int:
    started_at = datetime.utcnow()
    started_perf = time.perf_counter()
    async with async_session() as session:
        result = await session.execute(select(AnalysisRun).where(AnalysisRun.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            return 1

        if run.status not in {"queued", "running"}:
            return 0

        if not isinstance(run.artifact_manifest, dict):
            run.artifact_manifest = build_artifact_manifest_for_run(run)
        manifest = dict(run.artifact_manifest or {})
        cache_dir_rel = manifest.get("cache_dir")
        if not cache_dir_rel:
            manifest = build_artifact_manifest_for_run(run)
            run.artifact_manifest = manifest
            cache_dir_rel = manifest["cache_dir"]
        cache_dir = resolve_allowed_path(str(cache_dir_rel))
        cache_dir.mkdir(parents=True, exist_ok=True)

        run.status = "running"
        run.started_at = started_at
        run.error_message = None
        await session.commit()

        params = dict(run.params_json or {})

        design_updates: dict[str, Any] | None = None
        inline_payload = None
        if run.subject_kind == "design":
            design_result = await session.execute(select(Design).where(Design.id == run.subject_id))
            design = design_result.scalar_one_or_none()
            if design is None:
                raise ValueError(f"Design {run.subject_id} not found")
            structure_path = _resolve_design_structure_path(design)
            if not structure_path.exists():
                raise FileNotFoundError(f"Structure file not found: {design.pdb_path}")

            if run.analysis_type == STRUCTURE_SUMMARY_ANALYSIS:
                result_payload, summary_payload, inline_payload = _compute_structure_summary(design)
            elif run.analysis_type == CONTACT_MAP_ANALYSIS:
                result_payload, summary_payload, inline_payload = _compute_contact_map(design, params)
            elif run.analysis_type == CHAIN_METRICS_ANALYSIS:
                result_payload, summary_payload, inline_payload, design_updates = _compute_chain_metrics(design)
            elif run.analysis_type == FAMPNN_PSCE_PROFILE_ANALYSIS:
                result_payload, summary_payload, inline_payload = _compute_fampnn_psce_profile(design, params)
            elif run.analysis_type == PAE_MATRIX_ANALYSIS:
                result_payload, summary_payload, inline_payload = _compute_pae_matrix(design, params)
            elif run.analysis_type == IPSAE_INTERFACE_ANALYSIS:
                result_payload, summary_payload, inline_payload, design_updates = _compute_ipsae_interface(design, params)
            elif run.analysis_type == ANTIBODY_ANNOTATION_PACK_ANALYSIS:
                result_payload, summary_payload, inline_payload, design_updates = _compute_antibody_annotation_pack(design)
            else:
                raise ValueError(f"Unsupported design analysis type: {run.analysis_type}")
        elif run.subject_kind == "job":
            job_result = await session.execute(select(Job).where(Job.id == run.subject_id))
            job = job_result.scalar_one_or_none()
            if job is None:
                raise ValueError(f"Job {run.subject_id} not found")

            if run.analysis_type == JOB_CORRELATION_MATRIX_ANALYSIS:
                result_payload, summary_payload, inline_payload = await _compute_job_correlation_matrix(session, job, params)
            elif run.analysis_type == JOB_AA_COMPOSITION_ANALYSIS:
                result_payload, summary_payload, inline_payload = await _compute_job_aa_composition(session, job, params)
            elif run.analysis_type == JOB_CDR_LOGO_PACK_ANALYSIS:
                result_payload, summary_payload, inline_payload = await _compute_job_cdr_logo_pack(session, job, params)
            else:
                raise ValueError(f"Unsupported job analysis type: {run.analysis_type}")
        else:
            raise ValueError(f"Unsupported subject kind: {run.subject_kind}")

        completed_at = datetime.utcnow()
        summary_record = {
            "analysis_type": run.analysis_type,
            "analysis_version": run.code_version,
            "subject_kind": run.subject_kind,
            "subject_id": run.subject_id,
            "params": params,
            "input_signature": run.input_signature,
            "code_version": run.code_version,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": round(time.perf_counter() - started_perf, 3),
            "summary": summary_payload,
        }

        result_path = resolve_allowed_path(str(manifest["result_json"]))
        summary_path = resolve_allowed_path(str(manifest["summary_json"]))
        _write_json(result_path, result_payload)
        _write_json(summary_path, summary_record)

        if run.subject_kind == "design" and design_updates:
            design_result = await session.execute(select(Design).where(Design.id == run.subject_id))
            design = design_result.scalar_one_or_none()
            if design is not None:
                for field, value in design_updates.items():
                    setattr(design, field, value)

        run.summary_json = summary_record
        run.result_inline_json = inline_payload
        run.status = "completed"
        run.completed_at = completed_at
        run.last_accessed_at = completed_at
        run.artifact_manifest = manifest
        await session.commit()
    return 0


async def _async_main(run_id: str) -> int:
    try:
        return await _run_analysis(run_id)
    except Exception:
        message = traceback.format_exc()
        return await _mark_failed(run_id, message)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python -m services.analysis_subprocess <run_id>", file=sys.stderr)
        return 2
    return asyncio.run(_async_main(argv[1]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
