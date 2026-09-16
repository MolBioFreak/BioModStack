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


def test_shared_selector_snapshots_only_path_not_digest(installed_runtime, monkeypatch):
    digest = "c4bd2ad605d49eee37d836f718d3d826d52c8b237a37e6081be2952ac3be72da"
    store = installed_runtime / ".image-store"
    selected = store / "objects" / "sha256" / digest / "runtime.sif"
    monkeypatch.setenv("BMS_FRUSTRAMPNN_SIF", str(selected))
    monkeypatch.setenv("BMS_FRUSTRAMPNN_SHA256", "0" * 64)
    _run('''
        import sys
        from pathlib import Path
        from services.frustrampnn import runtime
        identity = runtime.FRUSTRAMPNN_RUNTIME_IDENTITY
        assert identity.configured_sif_path == sys.argv[1]
        assert identity.sif_sha256 == sys.argv[2]
        assert runtime.validate_configured_container_path(sys.argv[1]) == sys.argv[1]
        legacy = Path(sys.argv[3]) / 'frustrampnn.sif'
        assert runtime.validate_configured_container_path(legacy) == sys.argv[1]
    ''', selected, digest, installed_runtime)


def test_fresh_reopen_and_retry_retained_configuration_with_original_absent(installed_runtime, tmp_path, monkeypatch):
    retained = tmp_path / 'retained.json'
    # Create the historical configuration in a different process, before selection.
    _run('''
        import json, sys
        from pathlib import Path
        sys.path.insert(0, str(Path.cwd() / 'tests'))
        from test_frustrampnn_global_configuration import _effective
        from services.frustrampnn.configuration import execution_configuration, global_configuration
        Path(sys.argv[1]).write_text(json.dumps(execution_configuration(_effective()).model_dump(mode='json')))
        Path(sys.argv[1] + '.v1').write_text(json.dumps(global_configuration()))
    ''', retained)
    original = retained.read_bytes()
    digest = "c4bd2ad605d49eee37d836f718d3d826d52c8b237a37e6081be2952ac3be72da"
    canonical = tmp_path / 'shared/objects/sha256' / digest / 'runtime.sif'
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'shared'))
    monkeypatch.setenv('BMS_FRUSTRAMPNN_SIF', str(canonical))
    assert not (installed_runtime / 'frustrampnn.sif').exists()
    _run('''
        import json, sys
        from pathlib import Path
        from services.frustrampnn.configuration import (
            FrustraMPNNExecutionConfigurationV3, execution_configuration, configuration_sha256, global_configuration)
        from services.frustrampnn.contracts import canonical_sha256
        from services.frustrampnn import runtime
        payload = json.loads(Path(sys.argv[1]).read_text())
        assert global_configuration() == json.loads(Path(sys.argv[1] + '.v1').read_text())
        reopened = FrustraMPNNExecutionConfigurationV3.model_validate(payload)
        assert reopened.model_dump(mode='json') == payload
        current = runtime.runtime_identity_dict()
        assert current['configured_sif_path'] == sys.argv[2]
        assert runtime.compatible_runtime_identity(payload['runtime'], current)
        # The supported retry compiler preserves effective science, selects current
        # placement, and creates a NEW receipt rather than editing old evidence.
        retry = execution_configuration(reopened.effective_settings)
        assert retry.runtime.configured_sif_path == sys.argv[2]
        assert retry.runtime.sif_sha256 == reopened.runtime.sif_sha256
        assert retry.effective_settings == reopened.effective_settings
        assert runtime.validate_configured_container_path(payload['runtime']['configured_sif_path']) == sys.argv[2]
        payload['runtime']['sif_sha256'] = '0' * 64
        payload['runtime_identity_sha256'] = canonical_sha256(payload['runtime'])
        payload['configuration_sha256'] = configuration_sha256(payload)
        try:
            FrustraMPNNExecutionConfigurationV3.model_validate(payload)
        except ValueError:
            pass
        else:
            raise AssertionError('changed scientific digest accepted')
    ''', retained, canonical)
    assert retained.read_bytes() == original


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
