from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable

from .config import DEFAULT_CACHE_DIR, DEFAULT_DB_PATH

_SCRIPT_DIR = Path(__file__).resolve().parents[2]
_LEGACY_BATCH_SCRIPT = _SCRIPT_DIR / "batch_msa.py"
_LEGACY_BATCH_MODULE_NAME = "_legacy_batch_msa_impl"
_DIRECT_BATCH_MODULE_NAME = "batch_msa"
_run_batch_msa_impl: Callable[..., Any] | None = None


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
    if _module_matches(main_module, required_attr, _LEGACY_BATCH_SCRIPT):
        return main_module
    return None


def _load_legacy_run_batch_msa_module() -> Any:
    main_module = _matching_main_module("run_batch_msa")
    if main_module is not None:
        sys.modules[_LEGACY_BATCH_MODULE_NAME] = main_module
        sys.modules[_DIRECT_BATCH_MODULE_NAME] = main_module
        return main_module

    existing = sys.modules.get(_LEGACY_BATCH_MODULE_NAME)
    if existing is not None:
        if _module_matches(existing, "run_batch_msa", _LEGACY_BATCH_SCRIPT):
            sys.modules[_DIRECT_BATCH_MODULE_NAME] = existing
            return existing
        del sys.modules[_LEGACY_BATCH_MODULE_NAME]

    direct = sys.modules.get(_DIRECT_BATCH_MODULE_NAME)
    if direct is not None:
        if _module_matches(direct, "run_batch_msa", _LEGACY_BATCH_SCRIPT):
            sys.modules[_LEGACY_BATCH_MODULE_NAME] = direct
            return direct
        del sys.modules[_DIRECT_BATCH_MODULE_NAME]

    spec = importlib.util.spec_from_file_location(_LEGACY_BATCH_MODULE_NAME, _LEGACY_BATCH_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load legacy batch MSA implementation from {_LEGACY_BATCH_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_LEGACY_BATCH_MODULE_NAME] = module
    sys.modules[_DIRECT_BATCH_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if sys.modules.get(_LEGACY_BATCH_MODULE_NAME) is module:
            sys.modules.pop(_LEGACY_BATCH_MODULE_NAME, None)
        if sys.modules.get(_DIRECT_BATCH_MODULE_NAME) is module:
            sys.modules.pop(_DIRECT_BATCH_MODULE_NAME, None)
        raise
    return module


def _load_legacy_run_batch_msa() -> Callable[..., Any]:
    global _run_batch_msa_impl
    if _run_batch_msa_impl is not None:
        return _run_batch_msa_impl
    module = _load_legacy_run_batch_msa_module()
    impl = getattr(module, "run_batch_msa", None)
    if impl is None:
        raise RuntimeError("Legacy batch MSA implementation is missing run_batch_msa")
    _run_batch_msa_impl = impl
    return _run_batch_msa_impl


def run_batch_msa(*args: Any, **kwargs: Any) -> Any:
    return _load_legacy_run_batch_msa()(*args, **kwargs)


def register_legacy_run_batch_msa(fn: Callable[..., Any]) -> Callable[..., Any]:
    global _run_batch_msa_impl, run_batch_msa
    _run_batch_msa_impl = fn
    run_batch_msa = fn
    return fn


__all__ = [
    "DEFAULT_CACHE_DIR",
    "DEFAULT_DB_PATH",
    "register_legacy_run_batch_msa",
    "run_batch_msa",
]
