#!/usr/bin/env python3
"""Normalize ESMFold2 de-novo validation artifacts into BioModStack contracts.

ESMFold2 predicts from the sequence content of each staged design PDB.  This
normalizer converts the all-atom mmCIF result to PDB for downstream workflow
compatibility and emits an auditable metrics document.  It deliberately does
not invent ipSAE or reuse ESMFold2's iPTM field as a binding metric.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from Bio.PDB import MMCIFParser, PDBIO


def _latest_match(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No artifact matching {pattern!r} under {directory}")
    return matches[-1]


def normalize_design(design_pdb: Path, raw_root: Path, output_dir: Path) -> dict:
    design_name = design_pdb.stem
    prediction_dir = raw_root / design_name
    cif_path = _latest_match(prediction_dir, "*.cif")
    metrics_path = _latest_match(prediction_dir, "*.metrics.json")

    output_pdb = output_dir / f"{design_name}_esmfold2.pdb"
    output_cif = output_dir / f"{design_name}_esmfold2.cif"
    output_metrics = output_dir / f"{design_name}_esmfold2.metrics.json"

    structure = MMCIFParser(QUIET=True).get_structure(design_name, str(cif_path))
    writer = PDBIO()
    writer.set_structure(structure)
    writer.save(str(output_pdb))
    shutil.copy2(cif_path, output_cif)

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    normalized = {
        **metrics,
        "workflow": "esmfold2",
        "engine": "esmfold2",
        "source_design_pdb": design_pdb.name,
        "predicted_pdb": output_pdb.name,
        "predicted_cif": output_cif.name,
        "validation_semantics": "sequence_and_complex_cofold",
        "binding_confidence_metric": None,
        "binding_confidence_reason": "ESMFold2 does not provide ipSAE; iPTM is not used as a substitute.",
    }
    output_metrics.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design-dir", required=True, type=Path)
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    designs = sorted(args.design_dir.glob("*.pdb"))
    if not designs:
        raise SystemExit(f"No design PDB files found under {args.design_dir}")

    records = [normalize_design(path, args.raw_root, args.output_dir) for path in designs]
    summary = {
        "engine": "esmfold2",
        "design_count": len(records),
        "binding_confidence_metric": None,
        "binding_confidence_reason": "ESMFold2 does not provide ipSAE; iPTM is not used as a substitute.",
        "records": records,
    }
    (args.output_dir / "esmfold2_validation_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
