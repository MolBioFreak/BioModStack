from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from services import ngs_molbio_runtime_status


ROOT = Path(__file__).resolve().parents[3]
BUILDER_PATH = ROOT / "scripts/build_ngs_molbio_runtime_implementation_record.py"
N0_REPORT = ROOT / "docs/reports/ngs-molbio-phase-n0-verification-v1.json"


def _load_builder():
    specification = importlib.util.spec_from_file_location(
        "ngs_molbio_runtime_record_builder_test", BUILDER_PATH
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_builder_uses_current_n0_authority_and_status_accepts_generated_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builder = _load_builder()
    output = tmp_path / "runtime_implementation_v1.json"
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
    accepted = ngs_molbio_runtime_status.runtime_implementation_record()
    assert accepted == generated
