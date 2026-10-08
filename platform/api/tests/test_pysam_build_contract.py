"""Offline guard for the authenticated in-environment native build transaction."""
import importlib.util
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10; pytest supplies tomli there.
    import tomli as tomllib

import pytest

API = Path(__file__).resolve().parents[1]


def backend():
    spec = importlib.util.spec_from_file_location("pysam_build_contract", API / "vendor/pysam/bms_build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("changed", ["Cython", "setuptools", "wheel"])
def test_backend_rejects_tool_drift_before_source_download(monkeypatch, changed):
    build = backend()
    expected = {"Cython": "3.1.3", "setuptools": "80.9.0", "wheel": "0.45.1"}
    monkeypatch.setattr(build, "installed_version", lambda name: "999" if name == changed else expected[name])
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("download before tool admission"))
    with pytest.raises(RuntimeError, match="build tools differ from authenticated lock"):
        with build.source():
            pytest.fail("unqualified tools entered source build")


def test_frozen_project_cannot_replace_authenticated_wheel_tool():
    project = tomllib.loads((API / "pyproject.toml").read_text())
    lock = tomllib.loads((API / "uv.lock").read_text())
    assert "wheel==0.45.1" in project["tool"]["uv"]["constraint-dependencies"]
    assert [p["version"] for p in lock["package"] if p["name"] == "wheel"] == ["0.45.1"]
    tools = (API / "vendor/pysam/build-requirements.lock").read_text()
    assert "wheel==0.45.1" in tools
