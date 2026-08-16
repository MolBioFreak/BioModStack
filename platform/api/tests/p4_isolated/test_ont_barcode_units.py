import hashlib
import json
from pathlib import Path

import pytest

from services.ont_barcode_units import load_barcode_unit


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    result_root = tmp_path / "job"
    demux = result_root / "demux" / "demux"
    demux.mkdir(parents=True)
    bam = demux / "barcode01.bam"
    bam.write_bytes(b"BAM-fixture")
    bam_sha = hashlib.sha256(bam.read_bytes()).hexdigest()
    unit_manifest = result_root / "demux" / "demux" / "manifests" / "barcode01.json"
    unit_manifest.parent.mkdir(parents=True)
    unit_payload = {
        "schema": "biomodstack.dorado_barcode_unit.v1",
        "unit_id": "barcode01",
        "bam_path": "demux/barcode01.bam",
        "bam_sha256": bam_sha,
        "read_count": 4,
        "source_calls_sha256": "a" * 64,
        "preflight_sha256": "b" * 64,
    }
    unit_manifest.write_text(json.dumps(unit_payload), encoding="utf-8")
    manifest = result_root / "demux" / "demux_manifest.json"
    manifest.write_text(json.dumps({
        "schema": "biomodstack.dorado_demux.v1",
        "preflight_sha256": "b" * 64,
        "source_calls": {"sha256": "a" * 64, "read_count": 4},
        "total_reads": 4,
        "units": [{
            "unit_id": "barcode01",
            "bam_path": "demux/barcode01.bam",
            "bam_sha256": bam_sha,
            "unit_manifest_path": "demux/manifests/barcode01.json",
            "unit_manifest_sha256": hashlib.sha256(unit_manifest.read_bytes()).hexdigest(),
            "read_count": 4,
            "source_calls_sha256": "a" * 64,
            "preflight_sha256": "b" * 64,
        }],
    }), encoding="utf-8")
    return result_root, manifest


def test_exact_barcode_unit_is_digest_bound_and_confined(tmp_path: Path) -> None:
    root, manifest = _fixture(tmp_path)
    unit = load_barcode_unit(manifest, root, "barcode01")
    assert unit["schema"] == "biomodstack.ont_barcode_resubmission_unit.v1"
    assert unit["unit_id"] == "barcode01"
    assert unit["read_count"] == 4
    assert Path(unit["bam_path"]).is_relative_to(root)
    assert unit["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_barcode_unit_rejects_unknown_tampered_and_escaping_paths(tmp_path: Path) -> None:
    root, manifest = _fixture(tmp_path)
    with pytest.raises(ValueError, match="unknown"):
        load_barcode_unit(manifest, root, "barcode99")

    (root / "demux" / "demux" / "barcode01.bam").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="identity"):
        load_barcode_unit(manifest, root, "barcode01")

    outside = tmp_path / "outside.bam"
    outside.write_bytes(b"outside")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["units"][0]["bam_path"] = "../../outside.bam"
    payload["units"][0]["bam_sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="confined"):
        load_barcode_unit(manifest, root, "barcode01")


@pytest.mark.parametrize("invalid_unit", ["barcode00", "barcode1", "barcode97", "barcode123"])
def test_barcode_unit_accepts_only_canonical_barcode_range(tmp_path: Path, invalid_unit: str) -> None:
    root, manifest = _fixture(tmp_path)
    with pytest.raises(ValueError, match="unknown or malformed"):
        load_barcode_unit(manifest, root, invalid_unit)


def test_barcode_unit_rejects_tampered_per_unit_manifest(tmp_path: Path) -> None:
    root, manifest = _fixture(tmp_path)
    unit_manifest = root / "demux" / "demux" / "manifests" / "barcode01.json"
    unit_manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="unit manifest identity"):
        load_barcode_unit(manifest, root, "barcode01")


@pytest.mark.parametrize(("field", "replacement"), (
    ("source_calls_sha256", "c" * 64),
    ("preflight_sha256", "d" * 64),
))
def test_barcode_unit_rejects_coherently_rehashed_false_provenance(
    tmp_path: Path, field: str, replacement: str
) -> None:
    root, manifest = _fixture(tmp_path)
    unit_manifest = root / "demux" / "demux" / "manifests" / "barcode01.json"
    unit_payload = json.loads(unit_manifest.read_text(encoding="utf-8"))
    unit_payload[field] = replacement
    unit_manifest.write_text(json.dumps(unit_payload), encoding="utf-8")
    aggregate = json.loads(manifest.read_text(encoding="utf-8"))
    aggregate["units"][0][field] = replacement
    aggregate["units"][0]["unit_manifest_sha256"] = hashlib.sha256(unit_manifest.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(aggregate), encoding="utf-8")
    with pytest.raises(ValueError, match="provenance"):
        load_barcode_unit(manifest, root, "barcode01")
