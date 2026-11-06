"""Return-only packaging checks against explicitly extracted native sources."""
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "schedule_patch", Path(__file__).resolve().parents[1]
    / "scripts/rfd3_generation/install_schedule_return.py")
assert _SPEC is not None and _SPEC.loader is not None
patch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(patch)


@pytest.mark.parametrize("name,expected", [
    ("foundry", patch.PATCHED_SHA256),
    ("shape_rfd3", patch.SHAPE_PATCHED_SHA256),
])
def test_exact_native_lineage(name, expected):
    root = os.environ.get("BMS_RFD3_PACKAGING_SOURCE_DIR")
    if not root:
        pytest.skip("set BMS_RFD3_PACKAGING_SOURCE_DIR to extracted selected-image sources")
    source = (Path(root) / f"{name}-sampler.py").read_bytes()
    changed = patch.patch_source(source)
    assert hashlib.sha256(changed).hexdigest() == expected
    assert changed == source.replace(patch.OLD, patch.NEW, 1)
    assert patch.patch_source(changed) == changed


def test_unrecognized_source_is_not_modified():
    with pytest.raises(RuntimeError, match="unexpected"):
        patch.patch_source(b"        return t_hat\n")
