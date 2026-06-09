#!/usr/bin/env python3
"""Extract FA-MPNN sequence-probability confidence metrics from sample_pkls.

Upstream FA-MPNN seq_design.py writes sample PKLs containing seq_probs and pred_aatype.
BioModStack uses this script to report sequence confidence separately from pSCE sidechain QC.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import pickle
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import numpy as np
except Exception:  # pragma: no cover - exercised only in minimal runtime images
    np = None  # type: ignore[assignment]


AA_ALPHABET = "ACDEFGHIKLMNPQRSTVWY"
EPS = 1e-12


def _to_array(value: Any) -> Any:
    if np is None:
        return value
    return np.asarray(value)


def _as_bool_mask(value: Any, length: int) -> List[bool]:
    if value is None:
        return [True] * length
    arr = _to_array(value)
    try:
        flat = arr.reshape(-1).tolist()
    except Exception:
        flat = list(value) if isinstance(value, Iterable) else []
    mask = [bool(item) for item in flat[:length]]
    if len(mask) < length:
        mask.extend([True] * (length - len(mask)))
    return mask


def _as_int_list(value: Any, length: int, default_start: int = 0) -> List[int]:
    if value is None:
        return list(range(default_start, default_start + length))
    arr = _to_array(value)
    try:
        flat = arr.reshape(-1).tolist()
    except Exception:
        flat = list(value) if isinstance(value, Iterable) else []
    out: List[int] = []
    for item in flat[:length]:
        try:
            out.append(int(item))
        except Exception:
            out.append(default_start + len(out))
    if len(out) < length:
        out.extend(range(default_start + len(out), default_start + length))
    return out


def _round(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _entropy(probabilities: List[float]) -> float:
    total = sum(max(float(p), 0.0) for p in probabilities)
    if total <= 0:
        return 0.0
    entropy = 0.0
    for prob in probabilities:
        p = max(float(prob), 0.0) / total
        if p > 0:
            entropy -= p * math.log(p)
    return entropy


def analyze_sample_pkl(path: Path) -> Dict[str, Any]:
    with path.open("rb") as handle:
        payload = pickle.load(handle)

    base = {
        "design": path.stem,
        "sample_pkl_path": str(path),
        "metric_source": "fampnn_sample_pkl_seq_probs",
        "fampnn_seq_probs_available": False,
        "missing": [],
    }
    if not isinstance(payload, dict):
        return {**base, "missing": ["dict_payload"]}

    seq_probs_raw = payload.get("seq_probs")
    pred_raw = payload.get("pred_aatype")
    if seq_probs_raw is None:
        return {**base, "missing": ["seq_probs"]}
    if pred_raw is None:
        return {**base, "missing": ["pred_aatype"]}

    probs_arr = _to_array(seq_probs_raw)
    pred_arr = _to_array(pred_raw)
    try:
        probs = probs_arr.reshape((-1, probs_arr.shape[-1]))
        pred = pred_arr.reshape(-1)
    except Exception as exc:
        return {**base, "missing": ["array_shape"], "error": str(exc)}

    length = min(len(probs), len(pred))
    if length == 0:
        return {**base, "missing": ["empty_seq_probs"]}

    seq_mask = _as_bool_mask(payload.get("seq_mask"), length)
    residue_index = _as_int_list(payload.get("residue_index"), length, default_start=1)
    chain_index = _as_int_list(payload.get("chain_index"), length, default_start=0)

    sampled_probs: List[float] = []
    entropies: List[float] = []
    low_confidence_positions: List[Dict[str, Any]] = []
    for idx in range(length):
        if not seq_mask[idx]:
            continue
        row = [float(x) for x in probs[idx].tolist()]
        aa_index = int(pred[idx])
        if aa_index < 0 or aa_index >= len(row):
            continue
        row_total = sum(max(x, 0.0) for x in row)
        normalized_row = [max(x, 0.0) / row_total for x in row] if row_total > 0 else row
        sampled_prob = max(float(normalized_row[aa_index]), EPS)
        entropy = _entropy(normalized_row)
        sampled_probs.append(sampled_prob)
        entropies.append(entropy)
        if sampled_prob < 0.5 or entropy > 1.5:
            low_confidence_positions.append(
                {
                    "chain_index": chain_index[idx],
                    "residue_index": residue_index[idx],
                    "aa_index": aa_index,
                    "aa": AA_ALPHABET[aa_index] if aa_index < len(AA_ALPHABET) else str(aa_index),
                    "sampled_prob": _round(sampled_prob),
                    "entropy": _round(entropy),
                }
            )

    if not sampled_probs:
        return {**base, "missing": ["designed_residue_probabilities"]}

    log_probs = [math.log(max(p, EPS)) for p in sampled_probs]
    result = {
        **base,
        "fampnn_seq_probs_available": True,
        "missing": [],
        "total_residue_count": length,
        "designed_residue_count": len(sampled_probs),
        "fampnn_mean_sampled_prob": _round(sum(sampled_probs) / len(sampled_probs)),
        "fampnn_min_sampled_prob": _round(min(sampled_probs)),
        "fampnn_mean_sampled_log_prob": _round(sum(log_probs) / len(log_probs)),
        "fampnn_total_sampled_log_prob": _round(sum(log_probs)),
        "fampnn_mean_entropy": _round(sum(entropies) / len(entropies)),
        "fampnn_max_entropy": _round(max(entropies)),
        "fampnn_low_confidence_positions": low_confidence_positions,
    }
    return result


def iter_sample_pkls(sample_pkl_dir: Path) -> Iterable[Path]:
    for suffix in ("*.pkl", "*.pickle"):
        yield from sorted(sample_pkl_dir.glob(suffix))


def write_jsonl(rows: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scalar_keys = sorted(
        key
        for row in rows
        for key, value in row.items()
        if not isinstance(value, (list, dict))
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=scalar_keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in scalar_keys})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-pkl-dir", required=True, type=Path)
    parser.add_argument("--out-jsonl", required=True, type=Path)
    parser.add_argument("--out-csv", type=Path)
    args = parser.parse_args()

    if not args.sample_pkl_dir.exists():
        raise SystemExit(f"sample PKL directory does not exist: {args.sample_pkl_dir}")

    rows = [analyze_sample_pkl(path) for path in iter_sample_pkls(args.sample_pkl_dir)]
    write_jsonl(rows, args.out_jsonl)
    if args.out_csv:
        write_csv(rows, args.out_csv)
    print(json.dumps({"sample_count": len(rows), "seq_probs_available": sum(1 for row in rows if row.get("fampnn_seq_probs_available"))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
