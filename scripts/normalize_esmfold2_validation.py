#!/usr/bin/env python3
"""Normalize every ESMFold2 sample using its producer-bound manifest identity."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from Bio.PDB import MMCIFParser, PDBIO


def _local_artifact(directory: Path, name: object, suffix: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name or not name.endswith(suffix):
        raise ValueError(f"Invalid ESMFold2 artifact name: {name!r}")
    path = directory / name
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or unsafe ESMFold2 artifact: {path}")
    return path


def _samples(design_pdb: Path, raw_root: Path) -> list[tuple[str, Path, dict]]:
    design_name = design_pdb.stem
    prediction_dir = raw_root / design_name
    manifest = json.loads((prediction_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = manifest.get("samples")
    if (manifest.get("schema_version") != 2 or manifest.get("workflow") != "esmfold2"
            or manifest.get("sequence_name") != design_name or not isinstance(rows, list)
            or not rows or manifest.get("sample_count") != len(rows)):
        raise ValueError(f"Invalid ESMFold2 sample manifest for {design_name}")
    samples = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"Invalid ESMFold2 sample record for {design_name}")
        sample_id = row.get("sample_id")
        if (not isinstance(sample_id, str) or not sample_id.startswith(f"{design_name}_")
                or not sample_id[len(design_name) + 1:].isdigit() or sample_id in seen
                or row.get("sequence_name") != design_name):
            raise ValueError(f"Invalid or duplicate ESMFold2 sample identity: {sample_id!r}")
        seen.add(sample_id)
        cif = _local_artifact(prediction_dir, row.get("cif"), ".cif")
        metrics_file = _local_artifact(prediction_dir, row.get("metrics"), ".metrics.json")
        if cif.name != f"{sample_id}.cif" or metrics_file.name != f"{sample_id}.metrics.json":
            raise ValueError(f"ESMFold2 sample peers disagree for {sample_id}")
        metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
        if (not isinstance(metrics, dict) or metrics.get("sample_id") != sample_id
                or metrics.get("sequence_name") != design_name or metrics.get("cif") != cif.name):
            raise ValueError(f"ESMFold2 sample metrics disagree for {sample_id}")
        samples.append((sample_id, cif, metrics))
    if ({p.name for p in prediction_dir.glob("*.cif")} != {f"{s[0]}.cif" for s in samples}
            or {p.name for p in prediction_dir.glob("*.metrics.json")} != {f"{s[0]}.metrics.json" for s in samples}):
        raise ValueError(f"Unlisted ESMFold2 sample artifacts for {design_name}")
    return samples


def normalize_design(design_pdb: Path, raw_root: Path, output_dir: Path) -> list[dict]:
    records = []
    for sample_id, cif_path, metrics in _samples(design_pdb, raw_root):
        output_pdb = output_dir / f"{sample_id}_esmfold2.pdb"
        output_cif = output_dir / f"{sample_id}_esmfold2.cif"
        output_metrics = output_dir / f"{sample_id}_esmfold2.metrics.json"
        structure = MMCIFParser(QUIET=True).get_structure(sample_id, str(cif_path))
        writer = PDBIO()
        writer.set_structure(structure)
        writer.save(str(output_pdb))
        shutil.copy2(cif_path, output_cif)
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
        records.append(normalized)
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design-dir", required=True, type=Path)
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    designs = sorted(args.design_dir.glob("*.pdb"))
    if not designs:
        raise SystemExit(f"No design PDB files found under {args.design_dir}")
    # Validate all producer manifests before writing any normalized samples.
    for design in designs:
        _samples(design, args.raw_root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    records = [record for design in designs for record in normalize_design(design, args.raw_root, args.output_dir)]
    summary = {
        "engine": "esmfold2",
        "design_count": len(designs),
        "sample_count": len(records),
        "binding_confidence_metric": None,
        "binding_confidence_reason": "ESMFold2 does not provide ipSAE; iPTM is not used as a substitute.",
        "records": records,
    }
    (args.output_dir / "esmfold2_validation_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
