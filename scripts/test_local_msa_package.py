import ast
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


NON_TEST_IMPORT_TARGETS = [
    SCRIPT_DIR / "prepare_protenix_msa.py",
    SCRIPT_DIR / "check_protenix_msa_preflight.py",
]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
    return modules


def test_non_test_local_msa_callers_stop_importing_script_entrypoints() -> None:
    for path in NON_TEST_IMPORT_TARGETS:
        imported = _imported_modules(path)
        assert "run_local_msa" not in imported, f"{path.name} still imports run_local_msa"
        assert "batch_msa" not in imported, f"{path.name} still imports batch_msa"


def test_local_msa_package_exposes_runtime_batching_and_protenix_modules() -> None:
    from local_msa import batching, runtime
    from local_msa.adapters import protenix

    assert callable(runtime.inspect_mmseqs_runtime)
    assert callable(runtime.parse_gpu_csv)
    assert callable(batching.run_batch_msa)
    assert callable(protenix.prepare_with_local_msa)
    assert callable(protenix.choose_backend)
