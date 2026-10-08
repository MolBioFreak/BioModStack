from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "modules" / "conformational_mapping_protenix.nf"
CONFIG_PATH = REPO_ROOT / "nextflow.config"
RUNTIME_SCRIPT_PATH = REPO_ROOT / "scripts" / "run_protenix_inference.py"
ADAPTER_LAUNCHER_PATH = REPO_ROOT / "scripts" / "run_biomodstack_workflow_adapter.sh"


def test_protenix_coordinate_ledger_is_appendable_until_inference_completes() -> None:
    source = RUNTIME_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "0o640," in source
    assert "os.chmod(ledger_path, 0o440)" in source


def test_protenix_binds_a_self_contained_declared_canonical_runtime() -> None:
    config = CONFIG_PATH.read_text(encoding="utf-8")
    module_config = config[config.index("withLabel: Protenix {") :]
    launcher = ADAPTER_LAUNCHER_PATH.read_text(encoding="utf-8")

    assert "cm_api_runtime_dir = System.getenv('BMS_CM_API_RUNTIME_DIR')" in config
    assert 'api_python = System.getenv(\'BMS_API_PYTHON\') ?: "${params.cm_api_runtime_dir}/current/venv/bin/python"' in config
    assert "--bind ${params.cm_api_runtime_dir}:${params.cm_api_runtime_dir}:ro" in module_config
    assert 'CM_API_RUNTIME_DIR="${BMS_CM_API_RUNTIME_DIR:-${BMS_DATA:-/mnt/BioModStack}/runtime/cm-api-python}"' in launcher
    assert "uv sync --locked" in launcher
    assert "provision_cm_api_runtime" in launcher
    assert "flock -x 9" in launcher
    assert "rewrite_cm_api_pyvenv_home" in launcher
    assert 'target_python="$runtime_dir/python-runtime/bin/' in launcher
    assert 'stage="$(mktemp -d "${CM_API_RUNTIME_DIR}/.stage.XXXXXX")"' in launcher
    assert '--bind "$CM_API_RUNTIME_DIR:$CM_API_RUNTIME_DIR"' in launcher
    assert 'if [ -e "$runtime_dir" ] || [ -L "$runtime_dir" ]; then' in launcher
    assert 'mv -T "$stage" "$runtime_dir"' in launcher
    assert 'export BMS_API_PYTHON="$CM_API_RUNTIME_DIR/current/venv/bin/python"' in launcher
    assert 'ln -s "releases/$runtime_name" "$next_link"' in launcher
    assert 'mv -Tf "$next_link" "$CM_API_RUNTIME_DIR/current"' in launcher


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
