from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np


@dataclass(frozen=True)
class ResidueConfidencePoint:
    chain_id: str
    residue_number: int
    insertion_code: str
    plddt: float
    loop_ids: tuple[str, ...] = ()


def _safe_scalar(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        array = np.asarray(value, dtype=float)
    except Exception:
        return None
    if array.size == 0:
        return None
    try:
        scalar = float(array.reshape(-1)[0])
    except Exception:
        return None
    if not np.isfinite(scalar):
        return None
    return scalar


def _safe_sequence(value: Any) -> Optional[list[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else None
    if isinstance(value, (list, tuple)):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return cleaned or None
    return None


def _normalize_plddt(value: Any) -> tuple[Optional[list[float]], Optional[float], Optional[float], Optional[float]]:
    if value is None:
        return None, None, None, None
    try:
        arr = np.asarray(value, dtype=float)
    except Exception:
        return None, None, None, None
    if arr.size == 0:
        return None, None, None, None

    if np.nanmax(arr) <= 1.5:
        arr = arr * 100.0

    if arr.ndim == 1:
        final = arr
        initial = arr
    else:
        initial = arr[0]
        final = arr[-1]

    try:
        final_list = [float(x) for x in np.asarray(final, dtype=float).tolist()]
    except Exception:
        final_list = None

    initial_mean = float(np.nanmean(initial)) if np.size(initial) else None
    final_mean = float(np.nanmean(final)) if np.size(final) else None
    delta = (final_mean - initial_mean) if initial_mean is not None and final_mean is not None else None
    return final_list, initial_mean, final_mean, delta


def _parse_pdb_residue_order_and_loop_labels(pdb_path: Path) -> tuple[list[tuple[str, int, str]], dict[tuple[str, int], set[str]]]:
    residue_order: list[tuple[str, int, str]] = []
    loop_labels: dict[tuple[str, int], set[str]] = {}
    seen_residues: set[tuple[str, int, str]] = set()
    try:
        with open(pdb_path, "r") as handle:
            for line in handle:
                if line.startswith("REMARK PDBinfo-LABEL:"):
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    try:
                        residue_id = int(parts[2])
                    except ValueError:
                        continue
                    loop_id = parts[3].strip().upper()
                    if loop_id in {"H1", "H2", "H3", "L1", "L2", "L3"}:
                        loop_labels.setdefault((loop_id[0], residue_id), set()).add(loop_id)
                    continue
                if not line.startswith(("ATOM", "HETATM")):
                    continue
                chain_id = (line[21] or "").strip()
                try:
                    residue_id = int(line[22:26].strip())
                except ValueError:
                    continue
                insertion_code = (line[26] or "").strip()
                residue_key = (chain_id, residue_id, insertion_code)
                if residue_key in seen_residues:
                    continue
                seen_residues.add(residue_key)
                residue_order.append(residue_key)
    except Exception:
        return [], {}
    return residue_order, loop_labels


def _normalize_design_loop_ids(selected_loops: Optional[list[str]]) -> set[str]:
    normalized_loops = set()
    for loop in selected_loops or []:
        loop_text = str(loop).strip().upper()
        if not loop_text:
            continue
        loop_id = loop_text.split(":", 1)[0].strip()
        if loop_id in {"H1", "H2", "H3", "L1", "L2", "L3"}:
            normalized_loops.add(loop_id)
    return normalized_loops


def _mean(values: list[float]) -> Optional[float]:
    return float(np.nanmean(values)) if values else None


def _compact_residue_ranges(residues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], list[int]] = {}
    for residue in residues:
        chain_id = str(residue.get("chain_id") or "").strip()
        label = str(residue.get("loop_id") or residue.get("label") or "").strip()
        residue_number = residue.get("residue_number")
        if not chain_id or not isinstance(residue_number, int):
            continue
        grouped.setdefault((chain_id, label), []).append(residue_number)

    for (chain_id, label), numbers in sorted(grouped.items()):
        sorted_numbers = sorted(set(numbers))
        if not sorted_numbers:
            continue
        start = previous = sorted_numbers[0]
        for number in sorted_numbers[1:]:
            if number == previous + 1:
                previous = number
                continue
            ranges.append({
                "chain_id": chain_id,
                "start_residue_number": start,
                "end_residue_number": previous,
                "label": label or None,
            })
            start = previous = number
        ranges.append({
            "chain_id": chain_id,
            "start_residue_number": start,
            "end_residue_number": previous,
            "label": label or None,
        })
    return ranges


def _build_confidence_scope(
    pdb_path: Path,
    residue_plddt: Optional[list[float]],
    selected_loops: Optional[list[str]],
    all_residue_mean: Optional[float],
) -> dict[str, Any]:
    base_counts = {
        "all_residue_count": len(residue_plddt or []),
        "modifiable_residue_count": 0,
        "nonmodifiable_residue_count": len(residue_plddt or []),
        "framework_residue_count": 0,
        "target_residue_count": 0,
    }
    base_plddt = {
        "primary": all_residue_mean,
        "all_residue": all_residue_mean,
        "modifiable": None,
        "nonmodifiable": None,
        "framework": None,
        "target": None,
    }
    normalized_loops = _normalize_design_loop_ids(selected_loops)
    if not residue_plddt or not normalized_loops:
        return {
            "metric_family": "rfantibody_plddt",
            "primary_scope": "all_residues",
            "source": "rfantibody_trb_config.antibody.design_loops",
            "modifiable_residues": [],
            "modifiable_ranges": [],
            "counts": base_counts,
            "plddt": base_plddt,
            "status": "no_modifiable_scope",
        }

    residue_order, loop_labels = _parse_pdb_residue_order_and_loop_labels(pdb_path)
    if not residue_order or len(residue_order) != len(residue_plddt):
        return {
            "metric_family": "rfantibody_plddt",
            "primary_scope": "all_residues",
            "source": "rfantibody_trb_config.antibody.design_loops",
            "modifiable_residues": [],
            "modifiable_ranges": [],
            "counts": base_counts,
            "plddt": base_plddt,
            "status": "mapping_failed",
        }

    points: list[ResidueConfidencePoint] = []
    for residue_key, plddt in zip(residue_order, residue_plddt):
        chain_id, residue_id, insertion_code = residue_key
        residue_loops = tuple(sorted(loop_labels.get((chain_id, residue_id), set())))
        points.append(ResidueConfidencePoint(chain_id, residue_id, insertion_code, float(plddt), residue_loops))

    modifiable_values: list[float] = []
    nonmodifiable_values: list[float] = []
    framework_values: list[float] = []
    target_values: list[float] = []
    modifiable_residues: list[dict[str, Any]] = []
    for point in points:
        is_modifiable = bool(set(point.loop_ids) & normalized_loops)
        if is_modifiable:
            modifiable_values.append(point.plddt)
            loop_id = next((loop for loop in point.loop_ids if loop in normalized_loops), point.loop_ids[0] if point.loop_ids else None)
            modifiable_residues.append({
                "chain_id": point.chain_id,
                "residue_number": point.residue_number,
                "insertion_code": point.insertion_code,
                "loop_id": loop_id,
            })
        else:
            nonmodifiable_values.append(point.plddt)
            if point.chain_id in {"H", "L"}:
                framework_values.append(point.plddt)
            else:
                target_values.append(point.plddt)

    modifiable_mean = _mean(modifiable_values)
    scope_status = "ok" if modifiable_residues else "no_matching_modifiable_residues"
    primary_scope = "modifiable_residues" if modifiable_residues else "all_residues"
    primary_plddt = modifiable_mean if modifiable_residues else all_residue_mean
    counts = {
        "all_residue_count": len(points),
        "modifiable_residue_count": len(modifiable_values),
        "nonmodifiable_residue_count": len(nonmodifiable_values),
        "framework_residue_count": len(framework_values),
        "target_residue_count": len(target_values),
    }
    plddt = {
        "primary": primary_plddt,
        "all_residue": all_residue_mean,
        "modifiable": modifiable_mean,
        "nonmodifiable": _mean(nonmodifiable_values),
        "framework": _mean(framework_values),
        "target": _mean(target_values),
    }
    return {
        "metric_family": "rfantibody_plddt",
        "primary_scope": primary_scope,
        "source": "rfantibody_trb_config.antibody.design_loops",
        "modifiable_residues": modifiable_residues,
        "modifiable_ranges": _compact_residue_ranges(modifiable_residues),
        "counts": counts,
        "plddt": plddt,
        "status": scope_status,
    }


def load_rfantibody_trb_summary(structure_path: str | Path | None) -> dict[str, Any]:
    if not structure_path:
        return {}

    pdb_path = Path(structure_path)
    candidate_paths: list[Path] = [pdb_path.with_suffix(".trb")]
    search_roots = [pdb_path.parent, *list(pdb_path.parents[:4])]
    for root in search_roots:
        candidate_paths.extend([
            root / f"{pdb_path.stem}.trb",
            root / "run" / "rfantibody" / f"{pdb_path.stem}.trb",
            root / "collected" / "rfantibody" / f"{pdb_path.stem}.trb",
            root / "collected" / "rfantibody_raw" / f"{pdb_path.stem}.trb",
            root / "collected" / "rfantibody_filtered" / f"{pdb_path.stem}.trb",
            root / "best_designs" / f"{pdb_path.stem}.trb",
            root / "pdb_files" / f"{pdb_path.stem}.trb",
        ])

    trb_path = next((path for path in dict.fromkeys(candidate_paths) if path.exists()), None)
    if trb_path is None:
        return {}

    try:
        with open(trb_path, "rb") as handle:
            trb = pickle.load(handle)
    except Exception:
        return {}

    config = trb.get("config") if isinstance(trb, dict) else None
    diffuser = config.get("diffuser") if isinstance(config, dict) else {}
    denoiser = config.get("denoiser") if isinstance(config, dict) else {}
    potentials = config.get("potentials") if isinstance(config, dict) else {}
    antibody = config.get("antibody") if isinstance(config, dict) else {}
    ppi = config.get("ppi") if isinstance(config, dict) else {}

    residue_plddt, initial_mean, final_mean, delta = _normalize_plddt(trb.get("plddt"))
    design_loops = _safe_sequence(antibody.get("design_loops")) if isinstance(antibody, dict) else None
    confidence_scope = _build_confidence_scope(pdb_path, residue_plddt, design_loops, final_mean)
    scoped_plddt = confidence_scope.get("plddt") if isinstance(confidence_scope.get("plddt"), dict) else {}
    primary_plddt = _safe_scalar(scoped_plddt.get("primary"))
    modifiable_plddt = _safe_scalar(scoped_plddt.get("modifiable"))
    nonmodifiable_plddt = _safe_scalar(scoped_plddt.get("nonmodifiable"))
    framework_plddt = _safe_scalar(scoped_plddt.get("framework"))
    target_plddt = _safe_scalar(scoped_plddt.get("target"))
    modifiable_residues = confidence_scope.get("modifiable_residues") or []
    modifiable_ranges = confidence_scope.get("modifiable_ranges") or []

    metadata = {
        "trb_path": str(trb_path),
        "device": str(trb.get("device")).strip() if trb.get("device") else None,
        "time_seconds": _safe_scalar(trb.get("time")),
        "mindist": _safe_scalar(trb.get("mindist")),
        "averagemin": _safe_scalar(trb.get("averagemin")),
        "design_loops": design_loops,
        "plddt_primary": primary_plddt,
        "plddt_modifiable": modifiable_plddt,
        "plddt_all_residue": final_mean,
        "plddt_selected": modifiable_plddt,
        "plddt_nonselected": nonmodifiable_plddt,
        "plddt_nonmodifiable": nonmodifiable_plddt,
        "plddt_framework": framework_plddt,
        "plddt_target": target_plddt,
        "modifiable_residues": modifiable_residues,
        "modifiable_ranges": modifiable_ranges,
        "confidence_scope": confidence_scope,
        "hotspots": _safe_sequence(ppi.get("hotspot_res")) if isinstance(ppi, dict) else None,
        "diffusion_steps": int(diffuser.get("T")) if isinstance(diffuser, dict) and diffuser.get("T") is not None else None,
        "noise_scale_ca": _safe_scalar(denoiser.get("noise_scale_ca")) if isinstance(denoiser, dict) else None,
        "noise_scale_frame": _safe_scalar(denoiser.get("noise_scale_frame")) if isinstance(denoiser, dict) else None,
        "guide_scale": _safe_scalar(potentials.get("guide_scale")) if isinstance(potentials, dict) else None,
    }

    return {
        "rfa_trb_path": str(trb_path),
        "rfa_hotspot_min_distance": _safe_scalar(trb.get("mindist")),
        "rfa_hotspot_avg_min_distance": _safe_scalar(trb.get("averagemin")),
        "rfa_runtime_seconds": _safe_scalar(trb.get("time")),
        "rfa_device": str(trb.get("device")).strip() if trb.get("device") else None,
        "rfa_plddt_initial": initial_mean,
        "rfa_plddt_final": final_mean,
        "rfa_plddt_delta": delta,
        "rfa_plddt_selected": modifiable_plddt,
        "rfa_plddt_nonselected": nonmodifiable_plddt,
        "rfa_plddt_all_residue": final_mean,
        "rfa_plddt_primary": primary_plddt,
        "rfa_plddt_modifiable": modifiable_plddt,
        "rfa_plddt_nonmodifiable": nonmodifiable_plddt,
        "rfa_plddt_framework": framework_plddt,
        "rfa_plddt_target": target_plddt,
        "rfa_modifiable_residues": modifiable_residues,
        "rfa_modifiable_ranges": modifiable_ranges,
        "rfa_confidence_scope": confidence_scope,
        "residue_plddt": residue_plddt,
        "plddt_overall": final_mean,
        "rfa_diffusion_steps": int(diffuser.get("T")) if isinstance(diffuser, dict) and diffuser.get("T") is not None else None,
        "rfa_noise_scale_ca": _safe_scalar(denoiser.get("noise_scale_ca")) if isinstance(denoiser, dict) else None,
        "rfa_noise_scale_frame": _safe_scalar(denoiser.get("noise_scale_frame")) if isinstance(denoiser, dict) else None,
        "rfa_guide_scale": _safe_scalar(potentials.get("guide_scale")) if isinstance(potentials, dict) else None,
        "rfa_design_loops": design_loops,
        "rfa_hotspots": _safe_sequence(ppi.get("hotspot_res")) if isinstance(ppi, dict) else None,
        "rfa_metadata": metadata,
    }
