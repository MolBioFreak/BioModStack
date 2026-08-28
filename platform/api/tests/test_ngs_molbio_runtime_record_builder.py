from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from services import ngs_molbio_capabilities, ngs_molbio_runtime_status


ROOT = Path(__file__).resolve().parents[3]
BUILDER_PATH = ROOT / "scripts/build_ngs_molbio_runtime_implementation_record.py"
N0_REPORT = ROOT / "docs/reports/ngs-molbio-phase-n0-verification-v1.json"
DENOMINATOR_V2 = ROOT / "schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json"
DENOMINATOR_V2_RELATIVE = "schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json"


def _load_builder():
    specification = importlib.util.spec_from_file_location(
        "ngs_molbio_runtime_record_builder_test", BUILDER_PATH
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_live_capability_authority_uses_v2_squigulator_inventory(monkeypatch) -> None:
    monkeypatch.setattr(
        ngs_molbio_capabilities,
        "_verify_phase_n0_receipt",
        lambda *_args: None,
    )
    inventory = ngs_molbio_capabilities.capability_inventory()

    assert inventory["schema"] == "bms.ngs-molbio.capability-inventory.v2"
    assert len(inventory["capabilities"]) == 22
    assert ngs_molbio_capabilities.capability_record(
        "ngs.ont.squigulator_ideal_comparison"
    )["capability_id"] == "ngs.ont.squigulator_ideal_comparison"


def test_builder_defaults_to_active_v2_runtime_authority() -> None:
    builder = _load_builder()

    assert builder.OUTPUT.name == "runtime_implementation_v2.json"
    assert builder.DENOMINATOR == DENOMINATOR_V2
    assert builder.DENOMINATOR_RELATIVE == DENOMINATOR_V2_RELATIVE
    assert builder.DENOMINATOR_SCHEMA == "bms.ngs-molbio.runtime-source-denominator.v2"


def test_builder_uses_current_n0_authority_and_status_accepts_generated_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builder = _load_builder()
    output = tmp_path / "runtime_implementation_v2.json"
    commit_object = tmp_path / "successor.commit"
    commit_object.write_bytes(b"test bypassed by exact verifier seam")
    monkeypatch.setattr(builder, "OUTPUT", output)
    monkeypatch.setattr(
        builder,
        "_verify_successor_authority",
        lambda _commit, _tree, _object: None,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(BUILDER_PATH),
            "--successor-source-commit",
            "1" * 40,
            "--successor-source-tree",
            "2" * 40,
            "--successor-commit-object",
            str(commit_object),
        ],
    )

    assert builder.main() == 0

    generated = json.loads(output.read_text(encoding="utf-8"))
    n0_report = json.loads(N0_REPORT.read_text(encoding="utf-8"))
    assert generated["n0_receipt_content_sha256"] == n0_report["content_sha256"]
    assert generated["n0_package_fingerprint"] == n0_report[
        "payload_fingerprint_sha256"
    ]
    monkeypatch.setattr(ngs_molbio_runtime_status, "_RECORD", output)
    monkeypatch.setattr(ngs_molbio_runtime_status, "_DENOMINATOR", DENOMINATOR_V2)
    monkeypatch.setattr(
        ngs_molbio_runtime_status, "_DENOMINATOR_RELATIVE", DENOMINATOR_V2_RELATIVE
    )
    monkeypatch.setattr(ngs_molbio_capabilities, "_RUNTIME_RECORD", output)
    accepted = ngs_molbio_runtime_status.runtime_implementation_record()
    assert accepted == generated
