from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "modules" / "conformational_mapping_protenix.nf"


def test_canonical_protenix_uses_api_interpreter_only_for_contract_scripts() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")

    assert (
        "${params.api_python} ${params.code_root}/scripts/prepare_protenix_conformational_mapping.py"
        in source
    )
    assert (
        "${params.api_python} ${params.code_root}/scripts/finalize_protenix_conformational_mapping.py"
        in source
    )
    assert "python3 ${params.code_root}/scripts/run_protenix_inference.py" in source
    assert "${params.api_python} ${params.code_root}/scripts/run_protenix_inference.py" not in source
