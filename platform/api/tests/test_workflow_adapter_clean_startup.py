"""Isolated launcher command fixtures; these are not scientific qualification."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.runtime_image_lifecycle import commit_release, transaction
from lib.shared_runtime_images import publish_image

spec = importlib.util.spec_from_file_location("cm_api_support_runtime", ROOT / "scripts/cm_api_support_runtime.py")
assert spec and spec.loader
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key in list(os.environ):
        if key.startswith("BMS_") or key.startswith("XDG_"):
            monkeypatch.delenv(key)
    for key, value in {
        "HOME": str(tmp_path), "BMS_HOME": str(ROOT),
        "BMS_DATA": str(tmp_path / "data"),
        "BMS_CONTAINER_DIR": str(tmp_path / "containers"),
        "BMS_RUNTIME_IMAGE_LANE": "development",
        "BMS_WORKFLOW_ADAPTER_LANE": "development",
        "BMS_STATE_DIR": str(tmp_path / "state"),
        "BMS_DB_PATH": str(tmp_path / "state/db.sqlite"),
        "BMS_WORK": str(tmp_path / "work"),
        "BMS_RESULTS_DIR": str(tmp_path / "results"),
        "BMS_CM_API_RUNTIME_DIR": str(tmp_path / "support"),
    }.items():
        monkeypatch.setenv(key, value)
    return tmp_path


def test_no_optional_images_is_read_only(isolated):
    assert support.support_image(ROOT) is None
    assert list(isolated.iterdir()) == []


def test_canonical_reference_selected_without_legacy_alias(isolated):
    import hashlib
    source = isolated / "fixture.sif"
    source.write_bytes(b"synthetic support-image fixture, not scientific evidence")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = isolated / "containers/.image-store"
    image = publish_image(source, store, digest)
    try:
        with transaction(store):
            commit_release(store, "development", {"BMS_PROTENIX_CONTAINER_PATH": {"sha256": digest, "path": str(image)}})
        before = (store / "references/state.json").read_bytes()
        assert support.support_image(ROOT) == image
        assert not (store.parent / "protenix.sif").exists()
        assert (store / "references/state.json").read_bytes() == before
        image.chmod(0o600)
        with pytest.raises(RuntimeError):
            support.support_image(ROOT)
    finally:
        image.parent.chmod(0o700)


def test_explicit_missing_selection_never_falls_back(isolated, monkeypatch):
    monkeypatch.setenv("BMS_PROTENIX_CONTAINER_PATH", str(isolated / "missing.sif"))
    with pytest.raises(ValueError, match="retained managed reference"):
        support.support_image(ROOT)


def test_frustra_only_legacy_install_remains_usable_for_support(isolated):
    path = isolated / "containers/frustrampnn.sif"
    path.parent.mkdir()
    path.write_bytes(b"fixture")
    assert support.support_image(ROOT) == path


def test_system_python_is_rejected_before_copy():
    with pytest.raises(ValueError, match="system prefix"):
        support.portable_runtime(Path("/usr/bin/python3"))


def fake_uv_fixture(home):
    """Command capture only: no dependency resolution or server simulation."""
    binary = home / ".local/bin/uv"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$HOME/uv.commands"\n')
    binary.chmod(0o700)


def test_clean_development_shell_reaches_server_command_without_copy(isolated):
    fake_uv_fixture(isolated)
    result = subprocess.run(["bash", str(ROOT / "scripts/run_biomodstack_workflow_adapter.sh")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "support runtime unprovisioned" in result.stderr
    commands = (isolated / "uv.commands").read_text().splitlines()
    assert commands == ["sync --locked", "run --no-sync uvicorn workflow_adapter_app:app --port 18001 --host 127.0.0.1 --no-proxy-headers --no-access-log"]
    assert not (isolated / "support").exists()
    assert not (isolated / "containers").exists()


def test_production_does_not_gain_unprovisioned_exception(isolated, monkeypatch):
    fake_uv_fixture(isolated)
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_LANE", "production")
    result = subprocess.run(["bash", str(ROOT / "scripts/run_biomodstack_workflow_adapter.sh")], capture_output=True, text=True)
    assert result.returncode == 78
    assert "CM support image is unprovisioned" in result.stderr
    assert (isolated / "uv.commands").read_text().splitlines() == ["sync --locked"]


def test_invalid_managed_receipt_never_reaches_legacy_uv(isolated, monkeypatch):
    fake_uv_fixture(isolated)
    managed = isolated / "managed-python"
    managed.mkdir()
    (managed / "state.json").write_text('{}')
    monkeypatch.setenv("BMS_PYTHON_ROOT", str(managed))
    result = subprocess.run(["bash", str(ROOT / "scripts/run_biomodstack_workflow_adapter.sh")], capture_output=True, text=True)
    assert result.returncode == 78
    assert "Python prerequisite environment is blocked" in result.stderr
    assert not (isolated / "uv.commands").exists()


def test_managed_launcher_uses_external_venv_not_checkout():
    launcher = (ROOT / "scripts/run_biomodstack_workflow_adapter.sh").read_text()
    assert launcher.index("bms_configuration_read_finish") < launcher.index("bms_python_runtime_resolve")
    assert 'API_SOURCE_VENV="$(dirname "$(dirname "$BMS_MANAGED_PYTHON")")"' in launcher
    assert 'exec "$BMS_MANAGED_PYTHON" -m uvicorn' in launcher
    assert 'local source_venv="$API_SOURCE_VENV"' in launcher


def test_broken_selection_blocks_shell_before_server(isolated, monkeypatch):
    fake_uv_fixture(isolated)
    monkeypatch.setenv("BMS_PROTENIX_CONTAINER_PATH", str(isolated / "missing.sif"))
    result = subprocess.run(["bash", str(ROOT / "scripts/run_biomodstack_workflow_adapter.sh")], capture_output=True, text=True)
    assert result.returncode == 78
    assert "retained managed reference" in result.stderr
    assert (isolated / "uv.commands").read_text().splitlines() == ["sync --locked"]
    assert not (isolated / "support").exists()
