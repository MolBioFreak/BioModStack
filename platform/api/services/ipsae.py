from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np

from services.aligned_error_utils import AlignedErrorArtifact, ResidueRecord


def _ptm_func(values: np.ndarray, d0: float) -> np.ndarray:
    return 1.0 / (1.0 + np.square(values / d0))


def calc_d0(length: int | float, pair_type: str) -> float:
    length_value = max(float(length), 0.0)
    min_value = 2.0 if pair_type == "nucleic_acid" else 1.0
    if length_value > 27.0:
        d0 = 1.24 * math.pow(length_value - 15.0, 1.0 / 3.0) - 1.8
    else:
        d0 = 1.0
    return max(min_value, d0)


def calc_d0_array(lengths: np.ndarray, pair_type: str) -> np.ndarray:
    length_values = np.maximum(np.asarray(lengths, dtype=float), 26.0)
    min_value = 2.0 if pair_type == "nucleic_acid" else 1.0
    return np.maximum(min_value, 1.24 * np.power(length_values - 15.0, 1.0 / 3.0) - 1.8)


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


@dataclass(frozen=True)
class DirectedPairSummary:
    chain_1: str
    chain_2: str
    pair_type: str
    iptm_d0chn_asym: float
    ipsae_d0chn_asym: float
    ipsae_d0dom_asym: float
    ipsae_d0res_asym: float
    n0chn: int
    n0dom: int
    n0res: int
    d0chn: float
    d0dom: float
    d0res: float
    best_iptm_residue: ResidueRecord | None
    best_ipsae_d0chn_residue: ResidueRecord | None
    best_ipsae_d0dom_residue: ResidueRecord | None
    best_ipsae_d0res_residue: ResidueRecord | None
    interface_residue_count_chain_1: int
    interface_residue_count_chain_2: int
    interface_dist_residue_count_chain_1: int
    interface_dist_residue_count_chain_2: int
    valid_pair_count: int
    dist_valid_pair_count: int


@dataclass(frozen=True)
class IpsaePairSummary:
    chain_1: str
    chain_2: str
    pair_type: str
    iptm_d0chn_asym: float
    iptm_d0chn_max: float
    ipsae_d0chn_asym: float
    ipsae_d0chn_max: float
    ipsae_d0dom_asym: float
    ipsae_d0dom_max: float
    ipsae_d0res_asym: float
    ipsae_d0res_max: float
    n0chn: int
    n0dom: int
    n0dom_max: int
    n0res: int
    n0res_max: int
    d0chn: float
    d0dom: float
    d0dom_max: float
    d0res: float
    d0res_max: float
    residue_label_iptm_asym: str | None
    residue_label_ipsae_d0chn_asym: str | None
    residue_label_ipsae_d0dom_asym: str | None
    residue_label_ipsae_d0res_asym: str | None
    residue_label_ipsae_d0res_max: str | None
    interface_residue_count_chain_1: int
    interface_residue_count_chain_2: int
    interface_dist_residue_count_chain_1: int
    interface_dist_residue_count_chain_2: int
    valid_pair_count: int
    dist_valid_pair_count: int


def _residue_label(residue: ResidueRecord | None) -> str | None:
    if residue is None:
        return None
    return f"{residue.residue_name:>3} {residue.chain_id:>2} {residue.residue_number:>4}"


def _round(value: Any, digits: int = 6) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return round(numeric, digits)


def _directed_pair_summary(
    artifact: AlignedErrorArtifact,
    *,
    chain_1: str,
    chain_2: str,
    pae_cutoff: float,
    dist_cutoff: float,
) -> DirectedPairSummary:
    residues = artifact.residues
    pae_matrix = artifact.matrix
    chains = np.asarray([residue.chain_id for residue in residues], dtype=object)
    coords = np.asarray([residue.cb_coord for residue in residues], dtype=float)
    distances = np.sqrt(((coords[:, np.newaxis, :] - coords[np.newaxis, :, :]) ** 2).sum(axis=2))

    chain_1_mask = chains == chain_1
    chain_2_mask = chains == chain_2
    residue_chain_types = {residue.chain_id: residue.chain_type for residue in residues}
    pair_type = (
        "nucleic_acid"
        if residue_chain_types.get(chain_1) == "nucleic_acid" or residue_chain_types.get(chain_2) == "nucleic_acid"
        else "protein"
    )

    n0chn = int(np.sum(chain_1_mask) + np.sum(chain_2_mask))
    d0chn = calc_d0(n0chn, pair_type)
    ptm_matrix_d0chn = _ptm_func(pae_matrix, d0chn)
    valid_pairs_matrix = np.outer(chain_1_mask, chain_2_mask) & (pae_matrix < pae_cutoff)

    size = len(residues)
    iptm_d0chn_byres = np.zeros(size, dtype=float)
    ipsae_d0chn_byres = np.zeros(size, dtype=float)
    ipsae_d0dom_byres = np.zeros(size, dtype=float)
    ipsae_d0res_byres = np.zeros(size, dtype=float)

    valid_pair_count = 0
    dist_valid_pair_count = 0
    unique_residues_chain_1: set[int] = set()
    unique_residues_chain_2: set[int] = set()
    dist_unique_residues_chain_1: set[int] = set()
    dist_unique_residues_chain_2: set[int] = set()

    for idx, residue in enumerate(residues):
        if residue.chain_id != chain_1:
            continue

        valid_pairs_ipsae = valid_pairs_matrix[idx]
        iptm_d0chn_byres[idx] = float(ptm_matrix_d0chn[idx, chain_2_mask].mean()) if np.any(chain_2_mask) else 0.0
        ipsae_d0chn_byres[idx] = float(ptm_matrix_d0chn[idx, valid_pairs_ipsae].mean()) if np.any(valid_pairs_ipsae) else 0.0

        valid_pair_count += int(np.sum(valid_pairs_ipsae))
        if np.any(valid_pairs_ipsae):
            unique_residues_chain_1.add(residue.residue_number)
            for match_idx in np.where(valid_pairs_ipsae)[0]:
                unique_residues_chain_2.add(residues[int(match_idx)].residue_number)

        valid_pairs_dist = chain_2_mask & (pae_matrix[idx] < pae_cutoff) & (distances[idx] < dist_cutoff)
        dist_valid_pair_count += int(np.sum(valid_pairs_dist))
        if np.any(valid_pairs_dist):
            dist_unique_residues_chain_1.add(residue.residue_number)
            for match_idx in np.where(valid_pairs_dist)[0]:
                dist_unique_residues_chain_2.add(residues[int(match_idx)].residue_number)

    n0dom = len(unique_residues_chain_1) + len(unique_residues_chain_2)
    d0dom = calc_d0(n0dom, pair_type)
    ptm_matrix_d0dom = _ptm_func(pae_matrix, d0dom)
    n0res_byres = valid_pairs_matrix.sum(axis=1)
    d0res_byres = calc_d0_array(n0res_byres, pair_type)

    chain_1_indices = np.where(chain_1_mask)[0]
    for idx in chain_1_indices:
        valid_pairs = valid_pairs_matrix[idx]
        ipsae_d0dom_byres[idx] = float(ptm_matrix_d0dom[idx, valid_pairs].mean()) if np.any(valid_pairs) else 0.0
        ptm_row_d0res = _ptm_func(pae_matrix[idx], float(d0res_byres[idx]))
        ipsae_d0res_byres[idx] = float(ptm_row_d0res[valid_pairs].mean()) if np.any(valid_pairs) else 0.0

    if not chain_1_indices.size:
        raise ValueError(f"No residues found for chain {chain_1}")

    best_iptm_idx = int(chain_1_indices[np.argmax(iptm_d0chn_byres[chain_1_indices])])
    best_d0chn_idx = int(chain_1_indices[np.argmax(ipsae_d0chn_byres[chain_1_indices])])
    best_d0dom_idx = int(chain_1_indices[np.argmax(ipsae_d0dom_byres[chain_1_indices])])
    best_d0res_idx = int(chain_1_indices[np.argmax(ipsae_d0res_byres[chain_1_indices])])

    return DirectedPairSummary(
        chain_1=chain_1,
        chain_2=chain_2,
        pair_type=pair_type,
        iptm_d0chn_asym=float(iptm_d0chn_byres[best_iptm_idx]),
        ipsae_d0chn_asym=float(ipsae_d0chn_byres[best_d0chn_idx]),
        ipsae_d0dom_asym=float(ipsae_d0dom_byres[best_d0dom_idx]),
        ipsae_d0res_asym=float(ipsae_d0res_byres[best_d0res_idx]),
        n0chn=int(n0chn),
        n0dom=int(n0dom),
        n0res=int(n0res_byres[best_d0res_idx]),
        d0chn=float(d0chn),
        d0dom=float(d0dom),
        d0res=float(d0res_byres[best_d0res_idx]),
        best_iptm_residue=residues[best_iptm_idx],
        best_ipsae_d0chn_residue=residues[best_d0chn_idx],
        best_ipsae_d0dom_residue=residues[best_d0dom_idx],
        best_ipsae_d0res_residue=residues[best_d0res_idx],
        interface_residue_count_chain_1=len(unique_residues_chain_1),
        interface_residue_count_chain_2=len(unique_residues_chain_2),
        interface_dist_residue_count_chain_1=len(dist_unique_residues_chain_1),
        interface_dist_residue_count_chain_2=len(dist_unique_residues_chain_2),
        valid_pair_count=int(valid_pair_count),
        dist_valid_pair_count=int(dist_valid_pair_count),
    )


def _pair_summaries(
    artifact: AlignedErrorArtifact,
    *,
    pae_cutoff: float,
    dist_cutoff: float,
) -> list[IpsaePairSummary]:
    unique_chains = _ordered_unique([residue.chain_id for residue in artifact.residues])
    directed: dict[tuple[str, str], DirectedPairSummary] = {}
    for chain_1 in unique_chains:
        for chain_2 in unique_chains:
            if chain_1 == chain_2:
                continue
            directed[(chain_1, chain_2)] = _directed_pair_summary(
                artifact,
                chain_1=chain_1,
                chain_2=chain_2,
                pae_cutoff=pae_cutoff,
                dist_cutoff=dist_cutoff,
            )

    pair_summaries: list[IpsaePairSummary] = []
    for chain_1 in unique_chains:
        for chain_2 in unique_chains:
            if chain_1 == chain_2:
                continue
            forward = directed[(chain_1, chain_2)]
            reverse = directed[(chain_2, chain_1)]
            max_d0res_source = forward if forward.ipsae_d0res_asym >= reverse.ipsae_d0res_asym else reverse
            max_d0dom_source = forward if forward.ipsae_d0dom_asym >= reverse.ipsae_d0dom_asym else reverse
            pair_summaries.append(
                IpsaePairSummary(
                    chain_1=chain_1,
                    chain_2=chain_2,
                    pair_type=forward.pair_type,
                    iptm_d0chn_asym=forward.iptm_d0chn_asym,
                    iptm_d0chn_max=max(forward.iptm_d0chn_asym, reverse.iptm_d0chn_asym),
                    ipsae_d0chn_asym=forward.ipsae_d0chn_asym,
                    ipsae_d0chn_max=max(forward.ipsae_d0chn_asym, reverse.ipsae_d0chn_asym),
                    ipsae_d0dom_asym=forward.ipsae_d0dom_asym,
                    ipsae_d0dom_max=max(forward.ipsae_d0dom_asym, reverse.ipsae_d0dom_asym),
                    ipsae_d0res_asym=forward.ipsae_d0res_asym,
                    ipsae_d0res_max=max(forward.ipsae_d0res_asym, reverse.ipsae_d0res_asym),
                    n0chn=forward.n0chn,
                    n0dom=forward.n0dom,
                    n0dom_max=max_d0dom_source.n0dom,
                    n0res=forward.n0res,
                    n0res_max=max_d0res_source.n0res,
                    d0chn=forward.d0chn,
                    d0dom=forward.d0dom,
                    d0dom_max=max_d0dom_source.d0dom,
                    d0res=forward.d0res,
                    d0res_max=max_d0res_source.d0res,
                    residue_label_iptm_asym=_residue_label(forward.best_iptm_residue),
                    residue_label_ipsae_d0chn_asym=_residue_label(forward.best_ipsae_d0chn_residue),
                    residue_label_ipsae_d0dom_asym=_residue_label(forward.best_ipsae_d0dom_residue),
                    residue_label_ipsae_d0res_asym=_residue_label(forward.best_ipsae_d0res_residue),
                    residue_label_ipsae_d0res_max=_residue_label(max_d0res_source.best_ipsae_d0res_residue),
                    interface_residue_count_chain_1=forward.interface_residue_count_chain_1,
                    interface_residue_count_chain_2=forward.interface_residue_count_chain_2,
                    interface_dist_residue_count_chain_1=forward.interface_dist_residue_count_chain_1,
                    interface_dist_residue_count_chain_2=forward.interface_dist_residue_count_chain_2,
                    valid_pair_count=forward.valid_pair_count,
                    dist_valid_pair_count=forward.dist_valid_pair_count,
                )
            )
    return pair_summaries


def _select_best_pair(
    pair_summaries: list[IpsaePairSummary],
    *,
    preferred_chain_1: set[str] | None,
    preferred_chain_2: set[str] | None,
    score_field: str,
) -> IpsaePairSummary | None:
    candidates = pair_summaries
    if preferred_chain_1 and preferred_chain_2:
        narrowed = [
            pair
            for pair in pair_summaries
            if pair.chain_1 in preferred_chain_1 and pair.chain_2 in preferred_chain_2
        ]
        if narrowed:
            candidates = narrowed
    if not candidates:
        return None
    return max(candidates, key=lambda pair: float(getattr(pair, score_field)))


def compute_ipsae_interface(
    artifact: AlignedErrorArtifact,
    *,
    pae_cutoff: float = 10.0,
    dist_cutoff: float = 10.0,
    binder_chains: list[str] | None = None,
    target_chains: list[str] | None = None,
) -> dict[str, Any]:
    pair_summaries = _pair_summaries(artifact, pae_cutoff=pae_cutoff, dist_cutoff=dist_cutoff)
    binder_set = {chain.strip() for chain in (binder_chains or []) if str(chain).strip()}
    target_set = {chain.strip() for chain in (target_chains or []) if str(chain).strip()}

    binder_to_target = _select_best_pair(
        pair_summaries,
        preferred_chain_1=binder_set or None,
        preferred_chain_2=target_set or None,
        score_field="ipsae_d0res_asym",
    )
    target_to_binder = _select_best_pair(
        pair_summaries,
        preferred_chain_1=target_set or None,
        preferred_chain_2=binder_set or None,
        score_field="ipsae_d0res_asym",
    )
    global_best = _select_best_pair(
        pair_summaries,
        preferred_chain_1=None,
        preferred_chain_2=None,
        score_field="ipsae_d0res_max",
    )

    selected = binder_to_target or global_best
    if binder_to_target and target_to_binder:
        selected = binder_to_target if binder_to_target.ipsae_d0res_asym >= target_to_binder.ipsae_d0res_asym else target_to_binder

    result_pairs = []
    for pair in pair_summaries:
        result_pairs.append(
            {
                "chain_1": pair.chain_1,
                "chain_2": pair.chain_2,
                "pair_type": pair.pair_type,
                "iptm_d0chn_asym": _round(pair.iptm_d0chn_asym),
                "iptm_d0chn_max": _round(pair.iptm_d0chn_max),
                "ipsae_d0chn_asym": _round(pair.ipsae_d0chn_asym),
                "ipsae_d0chn_max": _round(pair.ipsae_d0chn_max),
                "ipsae_d0dom_asym": _round(pair.ipsae_d0dom_asym),
                "ipsae_d0dom_max": _round(pair.ipsae_d0dom_max),
                "ipsae_d0res_asym": _round(pair.ipsae_d0res_asym),
                "ipsae_d0res_max": _round(pair.ipsae_d0res_max),
                "n0chn": int(pair.n0chn),
                "n0dom": int(pair.n0dom),
                "n0dom_max": int(pair.n0dom_max),
                "n0res": int(pair.n0res),
                "n0res_max": int(pair.n0res_max),
                "d0chn": _round(pair.d0chn, 3),
                "d0dom": _round(pair.d0dom, 3),
                "d0dom_max": _round(pair.d0dom_max, 3),
                "d0res": _round(pair.d0res, 3),
                "d0res_max": _round(pair.d0res_max, 3),
                "residue_label_iptm_asym": pair.residue_label_iptm_asym,
                "residue_label_ipsae_d0chn_asym": pair.residue_label_ipsae_d0chn_asym,
                "residue_label_ipsae_d0dom_asym": pair.residue_label_ipsae_d0dom_asym,
                "residue_label_ipsae_d0res_asym": pair.residue_label_ipsae_d0res_asym,
                "residue_label_ipsae_d0res_max": pair.residue_label_ipsae_d0res_max,
                "interface_residue_count_chain_1": int(pair.interface_residue_count_chain_1),
                "interface_residue_count_chain_2": int(pair.interface_residue_count_chain_2),
                "interface_dist_residue_count_chain_1": int(pair.interface_dist_residue_count_chain_1),
                "interface_dist_residue_count_chain_2": int(pair.interface_dist_residue_count_chain_2),
                "valid_pair_count": int(pair.valid_pair_count),
                "dist_valid_pair_count": int(pair.dist_valid_pair_count),
            }
        )

    return {
        "ipsae": _round(selected.ipsae_d0res_asym if selected else None),
        "ipsae_binder_to_target": _round(binder_to_target.ipsae_d0res_asym if binder_to_target else None),
        "ipsae_target_to_binder": _round(target_to_binder.ipsae_d0res_asym if target_to_binder else None),
        "ipsae_global_max": _round(global_best.ipsae_d0res_max if global_best else None),
        "ipsae_d0chn": _round(selected.ipsae_d0chn_asym if selected else None),
        "ipsae_d0dom": _round(selected.ipsae_d0dom_asym if selected else None),
        "ipsae_chain_pair": f"{selected.chain_1}->{selected.chain_2}" if selected else None,
        "ipsae_pair_type": selected.pair_type if selected else None,
        "ipsae_n0res": int(selected.n0res) if selected else None,
        "ipsae_n0chn": int(selected.n0chn) if selected else None,
        "ipsae_n0dom": int(selected.n0dom) if selected else None,
        "ipsae_selected_d0res": _round(selected.d0res, 3) if selected else None,
        "ipsae_selected_d0chn": _round(selected.d0chn, 3) if selected else None,
        "ipsae_selected_d0dom": _round(selected.d0dom, 3) if selected else None,
        "ipsae_selected_residue": selected.residue_label_ipsae_d0res_asym if selected else None,
        "pae_cutoff": _round(pae_cutoff, 3),
        "dist_cutoff": _round(dist_cutoff, 3),
        "pair_scores": result_pairs,
    }
