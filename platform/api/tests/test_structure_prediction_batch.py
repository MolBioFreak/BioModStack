from __future__ import annotations

import json
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers.jobs import (
    _normalize_structure_geometry_params,
    _validate_protenix_template_requirements,
)
from services.nextflow import _write_sequence_batch_payloads, build_nextflow_command


def test_normalize_structure_geometry_params_maps_legacy_flags() -> None:
    params = _normalize_structure_geometry_params(
        {
            "boltz_anchor_target": True,
            "protenix_anchor_target": False,
            "protenix_use_template": True,
            "target_template_threshold_angstrom": "2.5",
            "strict_target_rmsd": "1.25",
        }
    )

    assert params["boltz_target_geometry_mode"] == "conditioned"
    assert params["protenix_target_geometry_mode"] == "conditioned"
    assert params["target_geometry_mode"] == "conditioned"
    assert params["boltz_anchor_target"] is True
    assert params["protenix_anchor_target"] is True
    assert params["protenix_use_template"] is True
    assert params["target_template_threshold_angstrom"] == 2.5
    assert params["strict_target_rmsd"] == 1.25


def test_write_sequence_batch_payloads_writes_stable_names_and_csv(tmp_path: Path) -> None:
    manifest_path, batch_dir, _ = _write_sequence_batch_payloads(
        output_dir=str(tmp_path),
        params={
            "sequence_batch_entries": [
                {"name": "RBX1 A01", "sequence": "AAAA"},
                {"name": "RBX1 A01", "sequence": "CCCC"},
                {"sequence": "GGGG"},
            ],
            "sequence_batch_prefix": "rbx1_nb",
            "sequence_name": "fallback_name",
        },
        complex_components=None,
    )

    assert batch_dir is None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [entry["name"] for entry in manifest] == [
        "rbx1_nb_001_RBX1_A01",
        "rbx1_nb_002_RBX1_A01",
        "rbx1_nb_003_seq",
    ]
    csv_text = (tmp_path / "sequence_batch_manifest.csv").read_text(encoding="utf-8")
    assert "runtime_name,label,original_name" in csv_text
    assert "rbx1_nb_002_RBX1_A01,RBX1 A01,RBX1 A01" in csv_text


def test_build_nextflow_command_materializes_complex_batch_payloads(tmp_path: Path) -> None:
    cmd = build_nextflow_command(
        "protenix",
        "complex",
        {
            "pred_method": "protenix",
            "sequence_name": "rbx1_screen",
            "sequence_batch_entries": [
                {"name": "nb01", "sequence": "QVQLV"},
                {"name": "nb02", "sequence": "QVQLW"},
            ],
            "sequence_batch_prefix": "rbx1_scan",
            "sequence_batch_component_id": "A",
            "complex_components": [
                {"type": "protein", "id": "A", "sequence": "AAAAA", "name": "binder"},
                {"type": "protein", "id": "B", "sequence": "BBBBB", "name": "target"},
            ],
            "protenix_target_geometry_mode": "frozen",
        },
        str(tmp_path),
        job_id="job-xyz",
    )

    joined = " ".join(cmd)

    assert "--complex_json_path" in joined
    assert "--sequence_batch_json_path" in joined
    assert "--complex_batch_dir" in joined
    assert "--protenix_target_geometry_mode frozen" in joined

    manifest = json.loads((tmp_path / "sequence_batch_manifest.json").read_text(encoding="utf-8"))
    assert [entry["name"] for entry in manifest] == [
        "rbx1_scan_001_nb01",
        "rbx1_scan_002_nb02",
    ]
    variant_payload = json.loads(
        (tmp_path / "complex_batch_inputs" / "001_rbx1_scan_001_nb01.json").read_text(encoding="utf-8")
    )
    assert variant_payload["name"] == "rbx1_scan_001_nb01"
    binder = next(component for component in variant_payload["components"] if component["id"] == "A")
    assert binder["sequence"] == "QVQLV"


def test_write_sequence_batch_payloads_auto_inserts_binder_for_target_only_complex(tmp_path: Path) -> None:
    manifest_path, batch_dir, complex_components = _write_sequence_batch_payloads(
        output_dir=str(tmp_path),
        params={
            "sequence_batch_entries": [
                {"name": "nb01", "sequence": "QVQLV"},
                {"name": "nb02", "sequence": "QVQLW"},
            ],
            "sequence_batch_prefix": "rbx1_scan",
            "sequence_batch_component_id": "A",
        },
        complex_components=[
            {"type": "protein", "id": "A", "sequence": "TARGETSEQ", "name": "RCSB: 2LGV"},
            {"type": "ion", "id": "B", "ccd": "ZN", "name": "Zn #1"},
            {"type": "ion", "id": "C", "ccd": "ZN", "name": "Zn #2"},
            {"type": "ion", "id": "D", "ccd": "ZN", "name": "Zn #3"},
        ],
    )

    assert manifest_path is not None
    assert batch_dir is not None
    assert complex_components is not None

    base_target = next(component for component in complex_components if component["id"] == "A")
    assert base_target["sequence"] == "TARGETSEQ"

    binder = next(component for component in complex_components if component["id"] == "E")
    assert binder["type"] == "protein"
    assert binder["sequence"] == "QVQLV"

    variant_payload = json.loads(
        (batch_dir / "001_rbx1_scan_001_nb01.json").read_text(encoding="utf-8")
    )
    variant_target = next(component for component in variant_payload["components"] if component["id"] == "A")
    variant_binder = next(component for component in variant_payload["components"] if component["id"] == "E")
    assert variant_target["sequence"] == "TARGETSEQ"
    assert variant_binder["sequence"] == "QVQLV"
    assert [component["id"] for component in variant_payload["components"] if component["type"] == "ion"] == ["B", "C", "D"]


def test_protenix_fixed_target_geometry_does_not_require_global_template_db() -> None:
    params = _normalize_structure_geometry_params(
        {
            "protenix_target_geometry_mode": "frozen",
            "fixed_target_source_path": "/home/dalab/biomodstack/biomodstack/rcsb/2lgv.pdb",
            "fixed_target_source_chains": "A",
        }
    )

    _validate_protenix_template_requirements("protenix", params)
