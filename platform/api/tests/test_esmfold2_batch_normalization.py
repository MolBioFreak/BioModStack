"""Synthetic producer-contract fixtures; not native ESMFold2 inference acceptance."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
from Bio.PDB import MMCIFIO, PDBParser

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("esmfold2_batch_normalizer", ROOT / "scripts/normalize_esmfold2_validation.py")
normalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(normalizer)


def _fixture(tmp_path, count=2):
    design = tmp_path / "designs" / "candidate.pdb"
    design.parent.mkdir()
    design.write_text("ATOM      1  CA  ALA A   1      11.000  12.000  13.000  1.00 80.00           C\nEND\n")
    raw = tmp_path / "raw" / design.stem
    raw.mkdir(parents=True)
    structure = PDBParser(QUIET=True).get_structure("fixture", str(design))
    rows = []
    for index in range(count):
        sample_id = f"candidate_{index:03d}"
        cif = f"{sample_id}.cif"
        metric_name = f"{sample_id}.metrics.json"
        writer = MMCIFIO()
        writer.set_structure(structure)
        writer.save(str(raw / cif))
        metric = {"sample_id": sample_id, "sequence_name": design.stem, "cif": cif, "ptm": index / 10}
        (raw / metric_name).write_text(json.dumps(metric))
        rows.append({**metric, "metrics": metric_name})
    (raw / "manifest.json").write_text(json.dumps({
        "schema_version": 2, "workflow": "esmfold2", "sequence_name": design.stem,
        "sample_count": count, "samples": rows,
    }))
    return design, raw


def test_all_samples_keep_producer_pairing(tmp_path):
    design, raw = _fixture(tmp_path)
    out = tmp_path / "normalized"
    out.mkdir()
    records = normalizer.normalize_design(design, raw.parent, out)
    assert len(records) == 2
    for index, record in enumerate(records):
        sample_id = f"candidate_{index:03d}"
        assert record["sample_id"] == sample_id
        assert record["ptm"] == index / 10
        assert record["source_design_pdb"] == design.name
        assert (out / record["predicted_cif"]).is_file()
        assert (out / record["predicted_pdb"]).is_file()
        assert json.loads((out / f"{sample_id}_esmfold2.metrics.json").read_text())["sample_id"] == sample_id


def test_cli_preserves_sample_count_and_native_summary(tmp_path):
    _fixture(tmp_path)
    output = tmp_path / "predictions"
    subprocess.run([
        sys.executable, str(ROOT / "scripts/normalize_esmfold2_validation.py"),
        "--design-dir", str(tmp_path / "designs"),
        "--raw-root", str(tmp_path / "raw"),
        "--output-dir", str(output),
    ], check=True)
    summary = json.loads((output / "esmfold2_validation_summary.json").read_text())
    assert summary["design_count"] == 1
    assert summary["sample_count"] == len(summary["records"]) == 2
    assert [row["sample_id"] for row in summary["records"]] == ["candidate_000", "candidate_001"]
    assert summary["binding_confidence_metric"] is None


@pytest.mark.parametrize("damage", [
    "missing_cif", "missing_metrics", "metric_peer", "manifest_peer", "duplicate", "count", "extra",
    "foreign_sequence", "traversal", "empty",
])
def test_rejects_missing_mismatched_or_unlisted_samples(tmp_path, damage):
    design, raw = _fixture(tmp_path)
    manifest_file = raw / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    if damage == "missing_cif":
        (raw / "candidate_000.cif").unlink()
    elif damage == "missing_metrics":
        (raw / "candidate_000.metrics.json").unlink()
    elif damage == "metric_peer":
        metrics = raw / "candidate_000.metrics.json"
        value = json.loads(metrics.read_text())
        value["sample_id"] = "candidate_001"
        metrics.write_text(json.dumps(value))
    elif damage == "manifest_peer":
        manifest["samples"][0]["cif"] = "candidate_001.cif"
    elif damage == "duplicate":
        manifest["samples"].append(manifest["samples"][0])
        manifest["sample_count"] += 1
    elif damage == "count":
        manifest["sample_count"] = 1
    elif damage == "extra":
        (raw / "candidate_002.cif").write_text("extra")
    elif damage == "foreign_sequence":
        manifest["sequence_name"] = "other"
    elif damage == "traversal":
        manifest["samples"][0]["cif"] = "../candidate_000.cif"
    elif damage == "empty":
        manifest["samples"] = []
        manifest["sample_count"] = 0
    manifest_file.write_text(json.dumps(manifest))
    with pytest.raises((ValueError, FileNotFoundError)):
        normalizer.normalize_design(design, raw.parent, tmp_path / "normalized")
