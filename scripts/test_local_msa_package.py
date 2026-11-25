import ast
import io
import os
import subprocess
import sys
import tarfile
import types
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


NON_TEST_IMPORT_TARGETS = [
    SCRIPT_DIR / "prepare_protenix_msa.py",
    SCRIPT_DIR / "check_protenix_msa_preflight.py",
]

ENTRYPOINT_IMPORT_TARGETS = {
    SCRIPT_DIR / "run_local_msa.py": "lib.local_msa.cli.run_single",
}


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


def test_check_protenix_msa_preflight_stops_importing_prepare_protenix_script() -> None:
    imported = _imported_modules(SCRIPT_DIR / "check_protenix_msa_preflight.py")

    assert "prepare_protenix_msa" not in imported, "check_protenix_msa_preflight.py still imports prepare_protenix_msa"


def test_local_msa_entrypoints_import_cli_helpers() -> None:
    for path, expected_module in ENTRYPOINT_IMPORT_TARGETS.items():
        imported = _imported_modules(path)
        assert expected_module in imported, f"{path.name} should import {expected_module}"


def test_local_msa_runtime_package_surface_stops_static_import_of_run_local_msa() -> None:
    runtime_path = LIB_DIR / "local_msa" / "runtime.py"
    imported = _imported_modules(runtime_path)

    assert "run_local_msa" not in imported, "local_msa.runtime still statically imports run_local_msa"


def test_gitignore_allows_tracking_local_msa_package_sources() -> None:
    repo_root = SCRIPT_DIR.parent
    tracked_sources = [
        Path("scripts/lib/local_msa/runtime.py"),
        Path("scripts/lib/local_msa/adapters/protenix.py"),
    ]

    for rel_path in tracked_sources:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", str(rel_path)],
            cwd=repo_root,
            check=False,
        )
        assert proc.returncode == 1, f"{rel_path} is still ignored by git"


def test_gitignore_keeps_local_msa_bytecode_ignored() -> None:
    repo_root = SCRIPT_DIR.parent
    ignored_outputs = [
        Path("scripts/lib/local_msa/__pycache__/runtime.cpython-311.pyc"),
        Path("scripts/lib/local_msa/adapters/__pycache__/protenix.cpython-311.pyc"),
    ]

    for rel_path in ignored_outputs:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", str(rel_path)],
            cwd=repo_root,
            check=False,
        )
        assert proc.returncode == 0, f"{rel_path} should stay ignored by git"


def test_prepare_protenix_msa_cli_help_resolves_package_imports() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "prepare_protenix_msa.py"), "--help"],
        cwd=SCRIPT_DIR.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "usage: prepare_protenix_msa.py" in proc.stdout
    assert "--input_json" in proc.stdout


def test_check_protenix_msa_preflight_cli_help_resolves_package_imports() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "check_protenix_msa_preflight.py"), "--help"],
        cwd=SCRIPT_DIR.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "Check whether Protenix local MSA can run on GPU MMseqs" in proc.stdout


def test_local_msa_package_exposes_runtime_and_protenix_modules() -> None:
    from local_msa import runtime
    from local_msa.adapters import protenix

    assert callable(runtime.inspect_mmseqs_runtime)
    assert callable(runtime.parse_gpu_csv)
    assert not hasattr(protenix, "prepare_with_local_msa")
    assert callable(protenix.choose_backend)


def test_local_msa_providers_package_stays_lazy_on_import(monkeypatch) -> None:
    import importlib

    for name in [
        "local_msa.providers",
        "local_msa.providers.colabfold_api",
    ]:
        monkeypatch.delitem(sys.modules, name, raising=False)

    providers = importlib.import_module("local_msa.providers")

    assert providers.__all__ == ["run_colabfold_api_msa_workflow"]
    assert "local_msa.providers.colabfold_api" not in sys.modules


def test_colabfold_taxonomy_filter_imports_re_and_filters_bacteria() -> None:
    from local_msa.providers.colabfold_api import _postfilter_a3m_by_taxonomy

    filtered = _postfilter_a3m_by_taxonomy(
        ">query\nAAAA\n>hit_a Tax=Escherichia coli TaxID=562\nAAAA\n>hit_b Tax=Homo sapiens TaxID=9606\nAAAA\n",
        "2",
    )

    assert ">query" in filtered
    assert "Escherichia coli" in filtered
    assert "Homo sapiens" not in filtered


def test_colabfold_archive_extraction_rejects_path_traversal(tmp_path: Path) -> None:
    from local_msa.providers.colabfold_api import _extract_tar_archive_safely

    archive_path = tmp_path / "payload.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as tar:
        payload = b"nope"
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(RuntimeError, match="outside work dir"):
        _extract_tar_archive_safely(archive_path=archive_path, work_dir=tmp_path / "extract")


def test_colabfold_archive_extraction_rejects_special_entries(tmp_path: Path) -> None:
    from local_msa.providers.colabfold_api import _extract_tar_archive_safely

    archive_path = tmp_path / "payload.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="named_pipe")
        info.type = tarfile.FIFOTYPE
        tar.addfile(info)

    with pytest.raises(RuntimeError, match="special entry"):
        _extract_tar_archive_safely(archive_path=archive_path, work_dir=tmp_path / "extract")


def test_colabfold_provider_imports_with_scripts_lib_only_pythonpath() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(LIB_DIR)
    proc = subprocess.run(
        [sys.executable, "-c", "import local_msa.providers.colabfold_api"],
        cwd=SCRIPT_DIR.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr


def test_protenix_adapter_choose_backend_works_with_scripts_lib_only_pythonpath() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(LIB_DIR)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from local_msa.adapters.protenix import choose_backend; "
                "print(choose_backend('auto', {'tasks': 1, 'protein_chains': 1, 'total_residues': 10}, 1, 4, 1500))"
            ),
        ],
        cwd=SCRIPT_DIR.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "colabfold_api"



def test_protenix_adapter_loader_registers_direct_prepare_protenix_alias(monkeypatch) -> None:
    import importlib

    adapter = importlib.import_module("local_msa.adapters.protenix")
    for name in [
        adapter._LEGACY_PREPARE_PROTENIX_MODULE_NAME,
        adapter._DIRECT_PREPARE_PROTENIX_MODULE_NAME,
    ]:
        monkeypatch.delitem(sys.modules, name, raising=False)

    module = adapter._prepare_protenix_module()
    direct = importlib.import_module("prepare_protenix_msa")

    assert sys.modules[adapter._LEGACY_PREPARE_PROTENIX_MODULE_NAME] is module
    assert sys.modules[adapter._DIRECT_PREPARE_PROTENIX_MODULE_NAME] is module
    assert direct is module


def test_protenix_adapter_loader_heals_conflicting_valid_aliases(monkeypatch) -> None:
    import importlib

    adapter = importlib.import_module("local_msa.adapters.protenix")
    legacy = types.ModuleType(adapter._LEGACY_PREPARE_PROTENIX_MODULE_NAME)
    legacy.__file__ = str(adapter._LEGACY_PREPARE_PROTENIX_SCRIPT)
    legacy.choose_backend = object()
    direct = types.ModuleType(adapter._DIRECT_PREPARE_PROTENIX_MODULE_NAME)
    direct.__file__ = str(adapter._LEGACY_PREPARE_PROTENIX_SCRIPT)
    direct.choose_backend = object()

    monkeypatch.setitem(sys.modules, adapter._LEGACY_PREPARE_PROTENIX_MODULE_NAME, legacy)
    monkeypatch.setitem(sys.modules, adapter._DIRECT_PREPARE_PROTENIX_MODULE_NAME, direct)

    module = adapter._prepare_protenix_module()

    assert module is legacy
    assert sys.modules[adapter._DIRECT_PREPARE_PROTENIX_MODULE_NAME] is legacy


def test_protenix_adapter_loader_rejects_foreign_direct_module(monkeypatch) -> None:
    import importlib

    adapter = importlib.import_module("local_msa.adapters.protenix")
    foreign = types.ModuleType(adapter._DIRECT_PREPARE_PROTENIX_MODULE_NAME)
    foreign.__file__ = "/tmp/prepare_protenix_msa.py"
    foreign.choose_backend = object()

    monkeypatch.delitem(sys.modules, adapter._LEGACY_PREPARE_PROTENIX_MODULE_NAME, raising=False)
    monkeypatch.setitem(sys.modules, adapter._DIRECT_PREPARE_PROTENIX_MODULE_NAME, foreign)

    module = adapter._prepare_protenix_module()

    assert module is not foreign
    assert sys.modules[adapter._DIRECT_PREPARE_PROTENIX_MODULE_NAME] is module


def test_protenix_adapter_loader_reuses_matching_main_module(monkeypatch) -> None:
    import importlib

    adapter = importlib.import_module("local_msa.adapters.protenix")
    main_module = types.ModuleType("__main__")
    main_module.__file__ = str(adapter._LEGACY_PREPARE_PROTENIX_SCRIPT)
    main_module.choose_backend = object()

    monkeypatch.delitem(sys.modules, adapter._LEGACY_PREPARE_PROTENIX_MODULE_NAME, raising=False)
    monkeypatch.delitem(sys.modules, adapter._DIRECT_PREPARE_PROTENIX_MODULE_NAME, raising=False)
    monkeypatch.setitem(sys.modules, "__main__", main_module)

    module = adapter._prepare_protenix_module()

    assert module is main_module
    assert sys.modules[adapter._LEGACY_PREPARE_PROTENIX_MODULE_NAME] is main_module
    assert sys.modules[adapter._DIRECT_PREPARE_PROTENIX_MODULE_NAME] is main_module

