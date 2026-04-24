import importlib.util
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

RUN_LOCAL_MSA_PATH = SCRIPT_DIR / "run_local_msa.py"


def _load_run_local_msa_module():
    spec = importlib.util.spec_from_file_location("run_local_msa_provider_test_module", RUN_LOCAL_MSA_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_package_providers_match_run_local_msa_dispatch_targets() -> None:
    run_local_msa = _load_run_local_msa_module()

    from local_msa.providers.colabfold_api import run_colabfold_api_msa_workflow
    from local_msa.providers.local_mmseqs import run_colabfold_msa_workflow

    assert run_colabfold_api_msa_workflow is run_local_msa.run_colabfold_api_msa_workflow
    assert run_colabfold_msa_workflow is run_local_msa.run_colabfold_msa_workflow


def test_package_cli_build_arg_parser_preserves_default_gpuserver_db_load_mode() -> None:
    from local_msa.cli.args import build_arg_parser

    args = build_arg_parser().parse_args(
        [
            "--sequence",
            "ACDEFGHIK",
            "--name",
            "pkg_parser_defaults",
            "--out_dir",
            "/tmp/out",
        ]
    )

    assert args.gpu_server_db_load_mode == 2