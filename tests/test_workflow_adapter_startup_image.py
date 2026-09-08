"""Execute the real launcher with recording tools; never start a service/image."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEY = "BMS_PROTENIX_CONTAINER_PATH"


def executable(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


@pytest.mark.parametrize("mode", [
    "selected", "current", "legacy_projection", "production_legacy",
    "unregistered", "corrupt", "writable", "symlink", "hardlink",
])
def test_actual_startup_probe(tmp_path, mode):
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/select_workflow_adapter_image.py", scripts)
    shutil.copytree(ROOT / "scripts/lib", scripts / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    # A tiny stand-in interpreter tree exercises real copying, link rewriting,
    # locking and generation publication without copying/installing a live venv.
    source_python = project / "python-runtime/bin/python3.12"
    executable(source_python, "#!/bin/sh\nexit 0\n")
    venv = project / "platform/api/.venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").symlink_to(source_python)
    (venv / "pyvenv.cfg").write_text("home = /old/runtime/bin\n")
    tools = tmp_path / "tools/bin"
    executable(tools / "java", "#!/bin/sh\nexit 0\n")
    log = tmp_path / "calls.jsonl"
    recorder = ("#!/usr/bin/python3\nimport json, os, sys\n"
                "with open(os.environ['PROBE_LOG'], 'a') as f:\n"
                " f.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + '\\n')\n")
    executable(tools / "uv", recorder)
    executable(tools / "apptainer", recorder)
    home = tmp_path / "home"
    home.mkdir()
    containers = tmp_path / "containers"
    containers.mkdir()
    store = containers / ".image-store"
    payload = b"synthetic startup probe image - no inference"
    digest = hashlib.sha256(payload).hexdigest()
    image = store / "objects/sha256" / digest / "runtime.sif"
    image.parent.mkdir(parents=True)
    image.write_bytes(payload)
    image.chmod(0o400)
    release = {"schema_version": 1, "lane": "development", "created_ns": 0,
               "images": {KEY: {"path": str(image), "sha256": digest}}}
    references = store / "references"
    references.mkdir()
    if mode == "legacy_projection":
        (references / "development.env").write_text(
            "# Managed shared runtime references; image digest is encoded in each path.\n"
            f"BMS_RUNTIME_IMAGE_STORE={store}\n{KEY}={image}\n")
    else:
        (references / "state.json").write_text(json.dumps({
            "schema_version": 1, "generation": 1, "current": {"development": "selected"},
            "releases": {"selected": release}, "leases": {}}))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BMS_", "UV_", "NXF_", "PYTHON", "XDG_"))}
    runtime = tmp_path / "cm-api-runtime"
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
               BMS_HOME=str(project), BMS_WORKFLOW_ADAPTER_LANE="development",
               BMS_STATE_DIR=str(tmp_path / "state"), BMS_DB_PATH=str(tmp_path / "state/db"),
               BMS_WORK=str(tmp_path / "work"), BMS_RESULTS_DIR=str(tmp_path / "results"),
               BMS_DATA=str(tmp_path / "data"), BMS_CONTAINER_DIR=str(containers),
               BMS_CM_API_RUNTIME_DIR=str(runtime), BMS_NEXTFLOW_JAVA_HOME=str(tools.parent),
               UV_CACHE_DIR=str(tmp_path / "uv-cache"), PROBE_LOG=str(log))
    if mode not in {"current", "production_legacy"}:
        env[KEY] = str(image)
    if mode == "production_legacy":
        env["BMS_WORKFLOW_ADAPTER_LANE"] = "production"
        (containers / "protenix.sif").write_bytes(payload)
    else:
        assert not (containers / "protenix.sif").exists()
    if mode == "unregistered":
        env[KEY] = str(tmp_path / "unapproved.sif")
        Path(env[KEY]).write_bytes(payload)
    elif mode == "corrupt":
        image.chmod(0o600)
        image.write_bytes(b"wrong scientific bytes")
        image.chmod(0o400)
    elif mode == "writable":
        image.chmod(0o600)
    elif mode == "symlink":
        other = tmp_path / "other.sif"
        image.rename(other)
        image.symlink_to(other)
    elif mode == "hardlink":
        os.link(image, tmp_path / "other.sif")
    image.parent.chmod(0o500)
    try:
        result = subprocess.run([str(ROOT / "scripts/run_biomodstack_workflow_adapter.sh")],
                                env=env, text=True, capture_output=True, timeout=30)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        launches = [call for call in calls if call[0] == "apptainer"]
        if mode in {"unregistered", "corrupt", "writable", "symlink", "hardlink"}:
            assert result.returncode == 78, result.stderr
            assert "probe image is unavailable" in result.stderr
            assert launches == []
            assert not runtime.exists()
            assert calls == [["uv", "sync", "--locked"]]
        else:
            assert result.returncode == 0, result.stderr
            expected = containers / "protenix.sif" if mode == "production_legacy" else image
            assert len(launches) == 1
            assert launches[0][1:6] == ["exec", "--no-home", "--bind", f"{runtime}:{runtime}", str(expected)]
            assert launches[0][-2:] == ["-c", "import jsonschema"]
            current = runtime / "current"
            assert current.is_symlink()
            assert Path(launches[0][6]) == current.resolve() / "venv/bin/python"
            assert (current / "venv/pyvenv.cfg").read_text() == f"home = {current.resolve()}/python-runtime/bin\n"
            assert calls[-1][0:4] == ["uv", "run", "--no-sync", "uvicorn"]
            assert list(runtime.rglob("*.sif")) == []
        print(f"{mode}: exit={result.returncode}, apptainer={launches}")
    finally:
        image.parent.chmod(0o700)
