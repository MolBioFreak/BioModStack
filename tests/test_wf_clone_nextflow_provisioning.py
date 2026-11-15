"""The clone recipe installs its own Nextflow launcher and framework; no host launcher is assumed."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts")]
LOCK = ROOT / "config/ngs/wf_clone_validation_v1.8.4.lock.json"


def _lock(tmp_path, launcher=b"#!/bin/sh\necho launcher\n", jar=b"jar bytes"):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "nextflow").write_bytes(launcher)
    (upstream / "one.jar").write_bytes(jar)
    lock = json.loads(LOCK.read_text())
    lock["nextflow"]["executable"] = str(tmp_path / "runtime" / "nextflow-25.10.0" / "nextflow")
    lock["nextflow"]["launcher"] = {"url": (upstream / "nextflow").as_uri(), "sha256": hashlib.sha256(launcher).hexdigest()}
    lock["nextflow"]["framework"] = {"url": (upstream / "one.jar").as_uri(), "sha256": hashlib.sha256(jar).hexdigest()}
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(lock))
    return path, lock


@pytest.fixture
def clean(monkeypatch, tmp_path):
    for name in ("BMS_NEXTFLOW_EXECUTABLE", "BMS_WF_CLONE_NXF_HOME", "BMS_NEXTFLOW_HOME", "BMS_DATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NXF_HOME", str(tmp_path / "nxf-home"))
    return tmp_path


def test_tracked_lock_names_no_host_launcher_and_pins_what_it_installs():
    nextflow = json.loads(LOCK.read_text())["nextflow"]
    assert nextflow["executable"].startswith("${BMS_RUNTIME_HOME}/")
    assert "/usr/local/bin" not in LOCK.read_text()
    assert len(nextflow["launcher"]["sha256"]) == len(nextflow["framework"]["sha256"]) == 64


def test_clean_install_gets_launcher_and_framework_and_a_second_run_reuses_them(clean):
    import provision_wf_clone_runtime as provision
    lock_path, lock = _lock(clean)
    provision.provision_nextflow(lock_path, lock)
    launcher = Path(lock["nextflow"]["executable"])
    jar = clean / "nxf-home" / "framework" / "25.10.0" / "nextflow-25.10.0-one.jar"
    assert launcher.read_bytes().startswith(b"#!/bin/sh") and launcher.stat().st_mode & 0o111
    assert jar.read_bytes() == b"jar bytes"
    stamps = (launcher.stat().st_mtime_ns, jar.stat().st_mtime_ns)
    # Nothing to download from on the second run: the installed files are reused as they are.
    for name in ("nextflow", "one.jar"):
        (clean / "upstream" / name).unlink()
    provision.provision_nextflow(lock_path, lock)
    assert (launcher.stat().st_mtime_ns, jar.stat().st_mtime_ns) == stamps


def test_a_download_that_does_not_match_the_lock_is_not_installed(clean):
    import provision_wf_clone_runtime as provision
    lock_path, lock = _lock(clean)
    lock["nextflow"]["launcher"]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="the lock expects"):
        provision.provision_nextflow(lock_path, lock)
    assert not Path(lock["nextflow"]["executable"]).exists()
    assert not list(Path(lock["nextflow"]["executable"]).parent.glob(".*"))


def test_an_operator_launcher_is_left_alone(clean, monkeypatch):
    import provision_wf_clone_runtime as provision
    lock_path, lock = _lock(clean)
    monkeypatch.setenv("BMS_NEXTFLOW_EXECUTABLE", str(clean / "my-nextflow"))
    provision.provision_nextflow(lock_path, lock)
    assert not Path(lock["nextflow"]["executable"]).exists()
    assert (clean / "nxf-home" / "framework" / "25.10.0" / "nextflow-25.10.0-one.jar").is_file()


def test_the_launcher_location_resolves_for_this_install(clean, monkeypatch, capsys):
    from validate_wf_clone_runtime import main
    monkeypatch.setenv("XDG_DATA_HOME", str(clean / "share"))
    monkeypatch.setattr("sys.argv", ["validate", "--lock", str(LOCK), "--resolve", "nextflow.executable"])
    assert main() == 0
    assert capsys.readouterr().out.strip() == str(clean / "share/biomodstack/runtime/nextflow-25.10.0/nextflow")
