#!/usr/bin/env python3
"""Normalize an authoritative conformational candidate and emit its identity map."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "platform" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.conformational_mapping.structure_normalizer import (  # noqa: E402
    StructureMapError,
    load_authoritative_complex_snapshot,
    normalize_conformational_mapping_structure,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a deterministic single-model PDB and cm_structure_map_v1 "
            "from an authoritative mmCIF candidate"
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="Authoritative .cif/.mmcif path")
    parser.add_argument("--output", required=True, type=Path, help="Normalized PDB output path")
    parser.add_argument("--map", required=True, dest="map_path", type=Path, help="Structure-map JSON output path")
    parser.add_argument("--target-id", required=True, help="Canonical conformational-mapping target ID")
    parser.add_argument("--candidate-id", required=True, help="Canonical candidate ID")
    parser.add_argument(
        "--complex-snapshot",
        required=True,
        type=Path,
        help="Authoritative cm_complex_snapshot_v1 JSON path",
    )
    parser.add_argument(
        "--source-model",
        type=int,
        default=None,
        help="Explicit 1-based source model number; required when the source has multiple models",
    )
    parser.add_argument(
        "--altloc",
        default="A",
        help="One-character alternate-location ID preferred over blank records (default: A)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        snapshot = load_authoritative_complex_snapshot(args.complex_snapshot)
        structure_map = normalize_conformational_mapping_structure(
            input_path=args.input,
            output_pdb_path=args.output,
            map_path=args.map_path,
            target_id=args.target_id,
            candidate_id=args.candidate_id,
            complex_snapshot=snapshot,
            source_model=args.source_model,
            selected_altloc=args.altloc,
        )
    except (OSError, StructureMapError) as exc:
        print(f"Normalization failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Normalized {args.input} -> {args.output}; map={args.map_path}; "
        f"model={structure_map['selected_source_model']}; rows={len(structure_map['rows'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
