"""Tool installer CLI, exercised through start_ui.sh with a stand-in container runtime."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]

FAKE_RUNTIME = """#!/bin/bash
# Stand-in for apptainer: records calls, writes the requested image, obeys failure switches.
echo "$@" >> "$FAKE_CALLS"
case "$1" in
  build)
    [ -n "${FAKE_BUILD_SLEEP:-}" ] && sleep "$FAKE_BUILD_SLEEP"
    [ -n "${FAKE_FAIL_BUILD:-}" ] && { echo "simulated build error"; exit 1; }
    for a in "$@"; do case "$a" in *.sif) out="$a";; esac; done
    echo image > "$out" ;;
  test) [ -n "${FAKE_FAIL_SMOKE:-}" ] && exit 1; exit 0 ;;
  exec)
    case "$*" in
      *make-weights*) mkdir -p "$FAKE_WEIGHTS/demo/model" ;;
    esac
    exit 0 ;;
esac
"""


@pytest.fixture
def install(tmp_path):
    project = tmp_path / "project"
    (project / "tools" / "recipes").mkdir(parents=True)
    (project / "apptainer").mkdir()
    for name in ("start_ui.sh", "biomodstack_tools.py", "biomodstack_runtime_profile.py"):
        shutil.copy2(ROOT / name, project / name)
    (project / "apptainer" / "demo.def").write_text("Bootstrap: docker\nFrom: example\n")
    recipe = {
        "schema": "bms.tool-recipe.v1", "id": "demo", "name": "Demo tool", "label": "starting-point",
        "modules": ["ont"], "upstream": {"homepage": "https://example.org/demo"},
        "images": [{"filename": "demo.sif", "def": "apptainer/demo.def", "approx_size_gb": 0,
                    "smoke": [{"name": "version", "argv": ["demo", "--version"]},
                              {"name": "gpu-check", "gpu": True, "argv": ["demo", "--gpu"]}]}],
        "weights": [{"id": "model", "required": True, "license": {"id": "DEMO-1.0", "url": "https://example.org/l"},
                     "present_if": ["demo/model"],
                     "fetch": {"type": "container", "argv": ["make-weights"]}}],
    }
    (project / "tools" / "recipes" / "demo.json").write_text(json.dumps(recipe))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runtime = bin_dir / "apptainer"
    runtime.write_text(FAKE_RUNTIME)
    runtime.chmod(runtime.stat().st_mode | stat.S_IEXEC)
    data = tmp_path / "data"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home"),
           "XDG_CONFIG_HOME": str(tmp_path / "config"), "BMS_DATA": str(data),
           "FAKE_CALLS": str(tmp_path / "calls.txt"), "FAKE_WEIGHTS": str(data / "weights")}
    (tmp_path / "home").mkdir()

    def run(*args, extra=None, as_json=True):
        argv = ["bash", str(project / "start_ui.sh"), "tools", *args] + (["--json"] if as_json else [])
        done = subprocess.run(argv, env={**env, **(extra or {})}, capture_output=True, text=True, timeout=60)
        return done.returncode, (json.loads(done.stdout) if as_json else done.stdout)
    run.data, run.calls, run.tmp = data, tmp_path / "calls.txt", tmp_path
    return run


def test_list_and_plan_before_anything_is_installed(install):
    code, listing = install("list")
    assert code == 0
    assert [(t["tool"], t["status"], t["label"]) for t in listing["tools"]] == [("demo", "not-installed", "starting-point")]
    code, plan = install("plan", "demo")
    assert code == 0 and plan["status"] == "planned"
    assert [s["step"] for s in plan["steps"]] == ["build:demo.sif", "weights:model"]
    assert plan["upstream"]["homepage"] == "https://example.org/demo"
    assert not install.data.exists(), "plan must not create anything"


def test_build_needs_license_then_becomes_ready(install):
    code, status = install("build", "demo")
    assert code == 3 and status["status"] == "failed"
    assert status["reason_code"] == "license_not_accepted" and "DEMO-1.0" in status["message"]
    assert Path(status["log_path"]).is_file()
    code, status = install("build", "demo", "--accept-license", "DEMO-1.0")
    assert code == 0 and status["status"] == "ready" and status["origin"] == "built"
    assert (install.data / "apptainer" / "demo.sif").is_file()
    assert (install.data / "weights" / "demo" / "model").is_dir()
    results = {c["check"]: c["result"] for c in status["smoke"]}
    gpu = shutil.which("nvidia-smi", path="/usr/bin:/bin") and subprocess.call(
        ["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    assert results == {"definition-test": "passed", "version": "passed",
                       "gpu-check": "passed" if gpu else "skipped-no-gpu"}
    assert install.calls.read_text().count("build ") == 1, "the second run reuses the built image"
    code, listing = install("list")
    assert listing["tools"][0]["status"] == "ready"


def test_failed_build_reports_reason_and_log_and_leaves_no_image(install):
    code, status = install("build", "demo", extra={"FAKE_FAIL_BUILD": "1"})
    assert code == 3 and status["status"] == "failed"
    assert status["reason_code"] == "image_build_failed" and status["step"] == "build:demo.sif"
    assert "simulated build error" in Path(status["log_path"]).read_text()
    assert not (install.data / "apptainer" / "demo.sif").exists()
    code, log = install("log", "demo", "--tail", "2")
    assert code == 0 and any("image_build_failed" in line for line in log["lines"])


def test_image_failing_smoke_check_is_not_installed(install):
    code, status = install("build", "demo", "--accept-license", "DEMO-1.0", extra={"FAKE_FAIL_SMOKE": "1"})
    assert code == 3 and status["reason_code"] == "smoke_test_failed"
    assert not (install.data / "apptainer" / "demo.sif").exists()


def test_adopt_links_existing_image_and_remove_keeps_the_original(install):
    own = install.tmp / "my-own-demo.sif"
    own.write_text("image")
    code, status = install("adopt", "demo", str(own))
    assert code == 3 and status["reason_code"] == "weights_missing"
    (install.data / "weights" / "demo" / "model").mkdir(parents=True)
    code, status = install("verify", "demo")
    assert code == 0 and status["status"] == "ready" and status["origin"] == "adopted"
    code, removed = install("remove", "demo")
    assert code == 0 and removed["status"] == "not-installed"
    assert own.read_text() == "image", "the adopted original is never deleted"


def test_remove_does_not_delete_images_it_did_not_install(install):
    target = install.data / "apptainer" / "demo.sif"
    target.parent.mkdir(parents=True)
    target.write_text("hand-made")
    code, removed = install("remove", "demo")
    assert code == 0 and removed["removed"] == [] and target.read_text() == "hand-made"
    assert removed["status"] == "incomplete"


def test_detached_build_reports_building_and_refuses_a_second_one(install):
    slow = {"FAKE_BUILD_SLEEP": "3"}
    code, started = install("build", "demo", "--detach", "--accept-license", "DEMO-1.0", extra=slow)
    assert code == 0 and started["started"] and started["status"] == "building"
    code, second = install("build", "demo", extra=slow)
    assert code == 3 and second["reason_code"] == "build_already_running"
    deadline = time.time() + 30
    while time.time() < deadline:
        code, status = install("status", "demo")
        if status["status"] != "building":
            break
        time.sleep(0.3)
    assert status["status"] == "ready"


def test_module_selection_is_saved_and_filters_the_tool_list(install):
    code, overview = install("modules")
    assert code == 0 and overview["selection_made"] is False
    by_name = {m["module"]: m for m in overview["modules"]}
    assert by_name["robot"]["state"] == "included" and by_name["robot"]["selected"] is True
    assert by_name["ont"]["state"] == "needs-tools" and by_name["ont"]["selected"] is False
    assert by_name["ont"]["tools"] == [{"tool": "demo", "status": "not-installed"}]
    assert by_name["design"]["state"] == "no-recipe-yet"
    code, listing = install("list", "--selected")
    assert listing["tools"] == []
    code, overview = install("select", "ont")
    assert code == 0 and overview["selection_made"] is True
    assert {m["module"] for m in overview["modules"] if m["selected"]} == {"molecular_tools", "robot", "ont"}
    code, listing = install("list", "--selected")
    assert [t["tool"] for t in listing["tools"]] == ["demo"]
    code, blocked = install("select", "nonsense")
    assert code == 3 and blocked["reason_code"] == "unknown_module"
    install("build", "demo", "--accept-license", "DEMO-1.0")
    code, overview = install("modules")
    assert {m["module"]: m["state"] for m in overview["modules"]}["ont"] == "ready"


def test_unknown_tool_and_missing_runtime_are_blocked_with_reasons(install):
    code, result = install("plan", "nope")
    assert code == 3 and result["reason_code"] == "unknown_tool"
    code, plan = install("plan", "demo", extra={"PATH": "/usr/bin:/bin"})
    if shutil.which("apptainer", path="/usr/bin:/bin") or shutil.which("singularity", path="/usr/bin:/bin"):
        pytest.skip("a real container runtime is on the base PATH")
    assert code == 3 and plan["blockers"][0]["reason_code"] == "apptainer_missing"


def test_shipped_recipes_are_valid_and_point_at_existing_definitions():
    import sys
    sys.path.insert(0, str(ROOT))
    import biomodstack_tools
    recipes = biomodstack_tools.load_recipes(ROOT)
    assert recipes
    for recipe in recipes.values():
        for image in recipe["images"]:
            assert (ROOT / image["def"]).is_file(), image["def"]
