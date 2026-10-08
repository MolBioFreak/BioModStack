#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any

from caliby_runtime import canonicalize_self_consistency_metrics, load_json, safe_float


def _resolve_metric(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        numeric = safe_float(payload.get(key))
        if numeric is not None:
            return numeric
    return None


def _enforce_threshold(
    *,
    name: str,
    value: float | None,
    lower_bound: float | None = None,
    upper_bound: float | None = None,
    reasons: list[str],
) -> bool:
    if lower_bound is None and upper_bound is None:
        return True
    if value is None:
        reasons.append(f"{name}=missing")
        return False
    if lower_bound is not None and value < lower_bound:
        reasons.append(f"{name}<{lower_bound}")
        return False
    if upper_bound is not None and value > upper_bound:
        reasons.append(f"{name}>{upper_bound}")
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter Caliby-designed structures before downstream validation.")
    parser.add_argument("--jsons", required=True, help="Directory containing Caliby generator_*.json sidecars")
    parser.add_argument("--pdbs", required=True, help="Directory containing Caliby-designed PDBs")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-potts-energy", type=float, default=None)
    parser.add_argument("--min-sc-plddt", type=float, default=None)
    parser.add_argument("--max-sc-rmsd", type=float, default=None)
    args = parser.parse_args()

    json_dir = Path(args.jsons).resolve()
    pdb_dir = Path(args.pdbs).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    passed = 0
    total = 0

    for metrics_path in sorted(json_dir.glob("generator_*.json")):
        total += 1
        payload = load_json(metrics_path)
        design_name = metrics_path.stem.removeprefix("generator_")
        structure_path = pdb_dir / f"{design_name}.pdb"
        if not structure_path.exists():
            continue

        sc_payload = canonicalize_self_consistency_metrics(payload.get("self_consistency")) if isinstance(payload.get("self_consistency"), dict) else {}
        merged_payload = {**payload, **sc_payload}

        potts_energy = _resolve_metric(merged_payload, "caliby_potts_energy", "U")
        sc_plddt = _resolve_metric(merged_payload, "caliby_sc_plddt", "sc_plddt", "avg_plddt", "mean_plddt", "plddt")
        sc_rmsd = _resolve_metric(merged_payload, "caliby_sc_rmsd", "sc_rmsd", "rmsd", "ca_rmsd", "bb_rmsd", "backbone_rmsd")

        keep = True
        reasons: list[str] = []
        keep = _enforce_threshold(
            name="potts_energy",
            value=potts_energy,
            upper_bound=args.max_potts_energy,
            reasons=reasons,
        ) and keep
        keep = _enforce_threshold(
            name="sc_plddt",
            value=sc_plddt,
            lower_bound=args.min_sc_plddt,
            reasons=reasons,
        ) and keep
        keep = _enforce_threshold(
            name="sc_rmsd",
            value=sc_rmsd,
            upper_bound=args.max_sc_rmsd,
            reasons=reasons,
        ) and keep

        if keep:
            shutil.copy2(structure_path, output_dir / structure_path.name)
            shutil.copy2(metrics_path, output_dir / metrics_path.name)
            passed += 1
            print(
                f"[CalibyFilter] PASS {design_name} "
                f"(potts={potts_energy}, sc_plddt={sc_plddt}, sc_rmsd={sc_rmsd})"
            )
        else:
            print(
                f"[CalibyFilter] DROP {design_name} "
                f"(potts={potts_energy}, sc_plddt={sc_plddt}, sc_rmsd={sc_rmsd}; {';'.join(reasons)})"
            )

    print(f"[CalibyFilter] Kept {passed}/{total} designs")


if __name__ == "__main__":
    main()
