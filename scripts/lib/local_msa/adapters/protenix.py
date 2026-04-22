from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parents[3]
_LEGACY_PREPARE_PROTENIX_SCRIPT = _SCRIPT_DIR / "prepare_protenix_msa.py"
_LEGACY_PREPARE_PROTENIX_MODULE_NAME = "_legacy_prepare_protenix_msa_impl"
_DIRECT_PREPARE_PROTENIX_MODULE_NAME = "prepare_protenix_msa"


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


def _matching_main_module(required_attr: str):
    main_module = sys.modules.get("__main__")
    if _module_matches(main_module, required_attr, _LEGACY_PREPARE_PROTENIX_SCRIPT):
        return main_module
    return None


def _prepare_protenix_module():
    main_module = _matching_main_module("choose_backend")
    if main_module is not None:
        sys.modules[_LEGACY_PREPARE_PROTENIX_MODULE_NAME] = main_module
        sys.modules[_DIRECT_PREPARE_PROTENIX_MODULE_NAME] = main_module
        return main_module

    existing = sys.modules.get(_LEGACY_PREPARE_PROTENIX_MODULE_NAME)
    if existing is not None:
        if _module_matches(existing, "choose_backend", _LEGACY_PREPARE_PROTENIX_SCRIPT):
            sys.modules[_DIRECT_PREPARE_PROTENIX_MODULE_NAME] = existing
            return existing
        del sys.modules[_LEGACY_PREPARE_PROTENIX_MODULE_NAME]

    direct = sys.modules.get(_DIRECT_PREPARE_PROTENIX_MODULE_NAME)
    if direct is not None:
        if _module_matches(direct, "choose_backend", _LEGACY_PREPARE_PROTENIX_SCRIPT):
            sys.modules[_LEGACY_PREPARE_PROTENIX_MODULE_NAME] = direct
            return direct
        del sys.modules[_DIRECT_PREPARE_PROTENIX_MODULE_NAME]

    spec = importlib.util.spec_from_file_location(_LEGACY_PREPARE_PROTENIX_MODULE_NAME, _LEGACY_PREPARE_PROTENIX_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load legacy Protenix MSA implementation from {_LEGACY_PREPARE_PROTENIX_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_LEGACY_PREPARE_PROTENIX_MODULE_NAME] = module
    sys.modules[_DIRECT_PREPARE_PROTENIX_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if sys.modules.get(_LEGACY_PREPARE_PROTENIX_MODULE_NAME) is module:
            sys.modules.pop(_LEGACY_PREPARE_PROTENIX_MODULE_NAME, None)
        if sys.modules.get(_DIRECT_PREPARE_PROTENIX_MODULE_NAME) is module:
            sys.modules.pop(_DIRECT_PREPARE_PROTENIX_MODULE_NAME, None)
        raise
    return module


def load_json(*args: Any, **kwargs: Any):
    return _prepare_protenix_module().load_json(*args, **kwargs)


def dump_json(*args: Any, **kwargs: Any):
    return _prepare_protenix_module().dump_json(*args, **kwargs)


def iter_protein_chains(*args: Any, **kwargs: Any):
    return _prepare_protenix_module().iter_protein_chains(*args, **kwargs)


def summarize_payload(*args: Any, **kwargs: Any):
    return _prepare_protenix_module().summarize_payload(*args, **kwargs)


def all_protein_chains_have_msa(*args: Any, **kwargs: Any):
    return _prepare_protenix_module().all_protein_chains_have_msa(*args, **kwargs)


def choose_backend(*args: Any, **kwargs: Any):
    return _prepare_protenix_module().choose_backend(*args, **kwargs)


def write_msa_report(*args: Any, **kwargs: Any):
    return _prepare_protenix_module().write_msa_report(*args, **kwargs)


def prepare_with_colabfold_api(*args: Any, **kwargs: Any):
    return _prepare_protenix_module().prepare_with_colabfold_api(*args, **kwargs)


def prepare_with_local_msa(*args: Any, **kwargs: Any):
    return _prepare_protenix_module().prepare_with_local_msa(*args, **kwargs)


__all__ = [
    "all_protein_chains_have_msa",
    "choose_backend",
    "dump_json",
    "iter_protein_chains",
    "load_json",
    "prepare_with_colabfold_api",
    "prepare_with_local_msa",
    "summarize_payload",
    "write_msa_report",
]
