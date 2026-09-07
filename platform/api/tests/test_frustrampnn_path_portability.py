"""Fresh-process tests of installation-owned runtime identity (no SIF execution)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from biomodstack_runtime_profile import get_install_profile_path


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]


@pytest.fixture
def installed_runtime(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("BMS_") or name == "DATABASE_URL":
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path / "other operator"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(API_ROOT), str(REPO_ROOT))))
    profile = {
        "data_root": str(tmp_path / "state data"),
        "container_dir": str(tmp_path / "approved images"),
    }
    profile_path = get_install_profile_path()
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    return Path(profile["container_dir"])


def _run(source, *args):
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source), *map(str, args)],
        cwd=API_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_profile_configured_image_accepted_without_opening_sif(installed_runtime):
    _run('''
        import sys
        from pathlib import Path
        from services.frustrampnn import runtime
        expected = str(Path(sys.argv[1]) / "frustrampnn.sif")
        assert not Path(expected).exists()
        assert runtime.validate_configured_container_path(expected) == expected
        identity = runtime.FRUSTRAMPNN_RUNTIME_IDENTITY
        assert identity.configured_sif_path == expected
        assert runtime.runtime_identity_dict()["configured_sif_path"] == expected
        assert runtime.FRUSTRAMPNN_RUNTIME_REGISTRY["runtime_identity"]["configured_sif_path"] == expected
        assert identity.sif_sha256 == "c4bd2ad605d49eee37d836f718d3d826d52c8b237a37e6081be2952ac3be72da"
        assert identity.executable_path == "/opt/venv/bin/frustrampnn"
        assert identity.checkpoint_path == "/opt/frustrampnn_weights/megascale.ckpt"
        assert Path(runtime.__file__).resolve().is_relative_to(Path.cwd())
    ''', installed_runtime)


def test_environment_container_root_precedes_profile(installed_runtime, tmp_path, monkeypatch):
    override = tmp_path / "environment images"
    monkeypatch.setenv("BMS_CONTAINER_DIR", str(override))
    _run('''
        import sys
        from pathlib import Path
        from services.frustrampnn import runtime
        expected = str(Path(sys.argv[1]) / "frustrampnn.sif")
        assert runtime.validate_configured_container_path(expected) == expected
        try:
            runtime.validate_configured_container_path(Path(sys.argv[2]) / "frustrampnn.sif")
        except runtime.RuntimeValidationError:
            pass
        else:
            raise AssertionError("profile path bypassed environment authority")
    ''', override, installed_runtime)


def test_unconfigured_caller_selected_image_remains_denied(installed_runtime, tmp_path):
    _run('''
        import sys
        from pathlib import Path
        from services.frustrampnn import runtime
        for candidate in (Path(sys.argv[1]) / "unconfigured/frustrampnn.sif",
                          Path("/mnt/BioModStack/apptainer/frustrampnn.sif"),
                          Path(sys.argv[2]) / "other.sif"):
            try:
                runtime.validate_configured_container_path(candidate)
            except runtime.RuntimeValidationError:
                pass
            else:
                raise AssertionError(f"unconfigured image accepted: {candidate}")
    ''', tmp_path, installed_runtime)


def test_cm_projection_retains_pinned_digest_at_configured_root(installed_runtime):
    installed_runtime.mkdir()
    # Only a regular-file fixture: the projection is not byte authentication.
    (installed_runtime / "frustrampnn.sif").write_bytes(b"projection fixture, not a SIF")
    _run('''
        import sys
        from pathlib import Path
        from services.frustrampnn import runtime
        root = Path(sys.argv[1])
        projection = runtime.cm_analysis_runtime_registry_v1(root)
        assert projection == {
            "container_name": "frustrampnn.sif",
            "container_sha256": "c4bd2ad605d49eee37d836f718d3d826d52c8b237a37e6081be2952ac3be72da",
        }
        assert runtime.validate_configured_container_path(root / projection["container_name"]) == str(root / "frustrampnn.sif")
        try:
            runtime.open_verified_container(root / "frustrampnn.sif", projection["container_sha256"])
        except runtime.RuntimeValidationError as exc:
            assert "does not match installed bytes" in str(exc)
        else:
            raise AssertionError("relocated image bypassed digest authentication")
    ''', installed_runtime)
