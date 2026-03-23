from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np


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


def _loop_scoped_plddt(
    pdb_path: Path,
    residue_plddt: Optional[list[float]],
    selected_loops: Optional[list[str]],
) -> tuple[Optional[float], Optional[float]]:
    if not residue_plddt or not selected_loops:
        return None, None
    residue_order, loop_labels = _parse_pdb_residue_order_and_loop_labels(pdb_path)
    if not residue_order or len(residue_order) != len(residue_plddt):
        return None, None

    normalized_loops = set()
    for loop in selected_loops:
        loop_text = str(loop).strip().upper()
        if not loop_text:
            continue
        loop_id = loop_text.split(":", 1)[0].strip()
        if loop_id in {"H1", "H2", "H3", "L1", "L2", "L3"}:
            normalized_loops.add(loop_id)
    if not normalized_loops:
        return None, None

    selected_values: list[float] = []
    nonselected_values: list[float] = []
    for residue_key, plddt in zip(residue_order, residue_plddt):
        chain_id, residue_id, _icode = residue_key
        residue_loops = loop_labels.get((chain_id, residue_id), set())
        if not residue_loops and chain_id in {"H", "L"}:
            residue_loops = loop_labels.get((chain_id, residue_id), set())
        if residue_loops & normalized_loops:
            selected_values.append(float(plddt))
        else:
            nonselected_values.append(float(plddt))

    selected_mean = float(np.nanmean(selected_values)) if selected_values else None
    nonselected_mean = float(np.nanmean(nonselected_values)) if nonselected_values else None
    return selected_mean, nonselected_mean


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
    selected_plddt, nonselected_plddt = _loop_scoped_plddt(pdb_path, residue_plddt, design_loops)

    return {
        "rfa_trb_path": str(trb_path),
        "rfa_hotspot_min_distance": _safe_scalar(trb.get("mindist")),
        "rfa_hotspot_avg_min_distance": _safe_scalar(trb.get("averagemin")),
        "rfa_runtime_seconds": _safe_scalar(trb.get("time")),
        "rfa_device": str(trb.get("device")).strip() if trb.get("device") else None,
        "rfa_plddt_initial": initial_mean,
        "rfa_plddt_final": final_mean,
        "rfa_plddt_delta": delta,
        "rfa_plddt_selected": selected_plddt,
        "rfa_plddt_nonselected": nonselected_plddt,
        "residue_plddt": residue_plddt,
        "plddt_overall": final_mean,
        "rfa_diffusion_steps": int(diffuser.get("T")) if isinstance(diffuser, dict) and diffuser.get("T") is not None else None,
        "rfa_noise_scale_ca": _safe_scalar(denoiser.get("noise_scale_ca")) if isinstance(denoiser, dict) else None,
        "rfa_noise_scale_frame": _safe_scalar(denoiser.get("noise_scale_frame")) if isinstance(denoiser, dict) else None,
        "rfa_guide_scale": _safe_scalar(potentials.get("guide_scale")) if isinstance(potentials, dict) else None,
        "rfa_design_loops": design_loops,
        "rfa_hotspots": _safe_sequence(ppi.get("hotspot_res")) if isinstance(ppi, dict) else None,
        "rfa_metadata": {
            "trb_path": str(trb_path),
            "device": str(trb.get("device")).strip() if trb.get("device") else None,
            "time_seconds": _safe_scalar(trb.get("time")),
            "mindist": _safe_scalar(trb.get("mindist")),
            "averagemin": _safe_scalar(trb.get("averagemin")),
            "design_loops": design_loops,
            "plddt_selected": selected_plddt,
            "plddt_nonselected": nonselected_plddt,
            "hotspots": _safe_sequence(ppi.get("hotspot_res")) if isinstance(ppi, dict) else None,
            "diffusion_steps": int(diffuser.get("T")) if isinstance(diffuser, dict) and diffuser.get("T") is not None else None,
            "noise_scale_ca": _safe_scalar(denoiser.get("noise_scale_ca")) if isinstance(denoiser, dict) else None,
            "noise_scale_frame": _safe_scalar(denoiser.get("noise_scale_frame")) if isinstance(denoiser, dict) else None,
            "guide_scale": _safe_scalar(potentials.get("guide_scale")) if isinstance(potentials, dict) else None,
        },
    }
