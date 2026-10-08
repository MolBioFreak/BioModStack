from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable

_SCRIPT_DIR = Path(__file__).resolve().parents[3]
_LEGACY_RUN_LOCAL_MSA_SCRIPT = _SCRIPT_DIR / "run_local_msa.py"
_LEGACY_RUN_LOCAL_MSA_MODULE_NAME = "_legacy_run_local_msa_impl"
_DIRECT_RUN_LOCAL_MSA_MODULE_NAME = "run_local_msa"
_run_colabfold_msa_workflow_impl: Callable[..., Any] | None = None


def _module_matches(module: Any, required_attr: str, expected_path: Path) -> bool:
    if module is None or not hasattr(module, required_attr):
        return False
    module_file = getattr(module, "__file__", None)
    if not isinstance(module_file, str):
        return False
    try:
        return Path(module_file).resolve() == expected_path.resolve()
    except Exception:
        return False


def _matching_main_module(required_attr: str) -> Any | None:
    main_module = sys.modules.get("__main__")
    if _module_matches(main_module, required_attr, _LEGACY_RUN_LOCAL_MSA_SCRIPT):
        return main_module
    return None


def _load_legacy_run_local_msa_module() -> Any:
    main_module = _matching_main_module("run_colabfold_msa_workflow")
    if main_module is not None:
        sys.modules[_LEGACY_RUN_LOCAL_MSA_MODULE_NAME] = main_module
        sys.modules[_DIRECT_RUN_LOCAL_MSA_MODULE_NAME] = main_module
        return main_module

    existing = sys.modules.get(_LEGACY_RUN_LOCAL_MSA_MODULE_NAME)
    if existing is not None:
        if _module_matches(existing, "run_colabfold_msa_workflow", _LEGACY_RUN_LOCAL_MSA_SCRIPT):
            sys.modules[_DIRECT_RUN_LOCAL_MSA_MODULE_NAME] = existing
            return existing
        del sys.modules[_LEGACY_RUN_LOCAL_MSA_MODULE_NAME]

    direct = sys.modules.get(_DIRECT_RUN_LOCAL_MSA_MODULE_NAME)
    if direct is not None:
        if _module_matches(direct, "run_colabfold_msa_workflow", _LEGACY_RUN_LOCAL_MSA_SCRIPT):
            sys.modules[_LEGACY_RUN_LOCAL_MSA_MODULE_NAME] = direct
            return direct
        del sys.modules[_DIRECT_RUN_LOCAL_MSA_MODULE_NAME]

    spec = importlib.util.spec_from_file_location(_LEGACY_RUN_LOCAL_MSA_MODULE_NAME, _LEGACY_RUN_LOCAL_MSA_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load legacy local MSA implementation from {_LEGACY_RUN_LOCAL_MSA_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_LEGACY_RUN_LOCAL_MSA_MODULE_NAME] = module
    sys.modules[_DIRECT_RUN_LOCAL_MSA_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if sys.modules.get(_LEGACY_RUN_LOCAL_MSA_MODULE_NAME) is module:
            sys.modules.pop(_LEGACY_RUN_LOCAL_MSA_MODULE_NAME, None)
        if sys.modules.get(_DIRECT_RUN_LOCAL_MSA_MODULE_NAME) is module:
            sys.modules.pop(_DIRECT_RUN_LOCAL_MSA_MODULE_NAME, None)
        raise
    return module


def _load_legacy_run_colabfold_msa_workflow() -> Callable[..., Any]:
    global _run_colabfold_msa_workflow_impl
    if _run_colabfold_msa_workflow_impl is not None:
        return _run_colabfold_msa_workflow_impl
    module = _load_legacy_run_local_msa_module()
    impl = getattr(module, "run_colabfold_msa_workflow", None)
    if impl is None:
        raise RuntimeError("Legacy local MSA implementation is missing run_colabfold_msa_workflow")
    _run_colabfold_msa_workflow_impl = impl
    return _run_colabfold_msa_workflow_impl


def run_colabfold_msa_workflow(*args: Any, **kwargs: Any) -> Any:
    return _load_legacy_run_colabfold_msa_workflow()(*args, **kwargs)


def register_legacy_run_colabfold_msa_workflow(fn: Callable[..., Any]) -> Callable[..., Any]:
    global _run_colabfold_msa_workflow_impl, run_colabfold_msa_workflow
    _run_colabfold_msa_workflow_impl = fn
    run_colabfold_msa_workflow = fn
    return fn


__all__ = ["register_legacy_run_colabfold_msa_workflow", "run_colabfold_msa_workflow"]
