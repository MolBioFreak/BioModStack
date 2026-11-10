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
      *make-weights*) mkdir "$FAKE_WEIGHTS/demo/model" || exit 1 ;;  # like dorado: the parent must exist
    esac
    exit 0 ;;
esac
"""


@pytest.fixture
def install(tmp_path):
    project = tmp_path / "project"
    (project / "tools" / "recipes").mkdir(parents=True)
    (project / "apptainer").mkdir()
    for name in ("start_ui.sh", "biomodstack_tools.py", "biomodstack_runtime_profile.py",
                 "biomodstack_local_resources.py"):
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


def test_listing_works_when_the_saved_ram_budget_exceeds_this_process(install):
    total_gib = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    profile = install.tmp / "config" / "biomodstack" / "install_profile.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(json.dumps({"local_memory_gib": round(total_gib + 64, 1)}))
    code, listing = install("list")
    assert code == 0 and [t["tool"] for t in listing["tools"]] == ["demo"]


def test_hand_placed_weights_ask_for_the_file_not_for_a_license(install):
    path = install.tmp / "project" / "tools" / "recipes" / "demo.json"
    recipe = json.loads(path.read_text())
    recipe["weights"][0]["fetch"] = {"type": "manual", "instructions": "Get model from its owner."}
    path.write_text(json.dumps(recipe))
    code, status = install("build", "demo")
    assert code == 3 and status["reason_code"] == "weights_manual_step"
    assert "Get model from its owner." in status["message"]
    assert (install.data / "apptainer" / "demo.sif").is_file(), "the image is still built and kept"
    (install.data / "weights" / "demo" / "model").mkdir(parents=True)
    code, status = install("verify", "demo")
    assert code == 0 and status["status"] == "ready"


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
            source = biomodstack_tools._image_source(image)
            if source is not None:
                assert (ROOT / source).is_file(), source
            if image["kind"] == "script":
                assert (ROOT / image["script"]["argv"][1]).is_file(), image["script"]["argv"]


FAKE_DOCKER = """#!/bin/bash
# Stand-in for docker: an image exists once it was built.
echo "docker $@" >> "$FAKE_CALLS"
case "$1 $2" in
  "build "*) for a in "$@"; do prev2="$prev"; prev="$a"; [ "$prev2" = "--tag" ] && touch "$FAKE_DOCKER_DIR/$(echo "$a" | tr '/:' '__')"; done ;;
  "image inspect") [ -e "$FAKE_DOCKER_DIR/$(echo "${@: -1}" | tr '/:' '__')" ] || exit 1; echo sha256:abc ;;
  "image rm") rm -f "$FAKE_DOCKER_DIR/$(echo "$3" | tr '/:' '__')" ;;
esac
exit 0
"""


def _add_recipe(install, tool, images):
    project = install.tmp / "project"
    (project / "tools" / "recipes" / f"{tool}.json").write_text(json.dumps({
        "schema": "bms.tool-recipe.v1", "id": tool, "name": tool, "label": "starting-point", "modules": ["ont"],
        "upstream": {"homepage": "https://example.org"}, "images": images, "weights": []}))
    return project


def test_dockerfile_image_is_built_with_docker_and_needs_no_apptainer(install):
    project = _add_recipe(install, "signal", [{"docker": {"file": "docker/signal.Dockerfile", "tag": "example/signal:1"},
                                               "smoke": [{"name": "version", "argv": ["signal", "--version"]}]}])
    (project / "docker").mkdir()
    (project / "docker" / "signal.Dockerfile").write_text("FROM example\n")
    images = install.tmp / "docker-images"
    images.mkdir()
    extra = {"FAKE_DOCKER_DIR": str(images)}
    code, plan = install("plan", "signal", extra={**extra, "BMS_TOOLS_CONTAINER_RUNTIME": "not-a-runtime"})
    assert code == 3 and plan["blockers"][0]["reason_code"] == "docker_missing"
    docker = install.tmp / "bin" / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    code, plan = install("plan", "signal", extra=extra)
    assert code == 0 and plan["steps"][0]["kind"] == "docker" and not plan["steps"][0]["already_present"]
    code, status = install("build", "signal", extra=extra)
    assert code == 0 and status["status"] == "ready", status
    calls = install.calls.read_text()
    assert "docker build --file" in calls and "--tag example/signal:1" in calls
    assert "docker run --rm --network none example/signal:1 signal --version" in calls
    assert not (install.data / "apptainer" / "signal.sif").exists()
    code, listing = install("list", extra=extra)
    assert {t["tool"]: t["status"] for t in listing["tools"]}["signal"] == "ready"
    code, _ = install("remove", "signal", extra=extra)
    assert code == 0 and not list(images.iterdir())


def test_image_built_on_another_image_waits_for_its_base(install):
    project = _add_recipe(install, "layered", [{"filename": "layered.sif", "def": "apptainer/layered.def",
                                                "needs": ["base.sif"], "build_args": {"base": "{container_dir}/base.sif"}}])
    (project / "apptainer" / "layered.def").write_text("Bootstrap: localimage\nFrom: {{ base }}\n")
    code, status = install("build", "layered")
    assert code == 3 and status["reason_code"] == "base_image_missing" and "base.sif" in status["message"]
    (install.data / "apptainer").mkdir(parents=True, exist_ok=True)
    (install.data / "apptainer" / "base.sif").write_text("base")
    code, status = install("build", "layered")
    assert code == 0 and status["status"] == "ready"
    assert f"--build-arg base={install.data / 'apptainer' / 'base.sif'}" in install.calls.read_text()


def test_build_input_that_is_not_shipped_blocks_with_instructions(install):
    project = _add_recipe(install, "packed", [{"filename": "packed.sif", "def": "apptainer/demo.def",
                                               "requires_files": ["apptainer/env.tar.gz"],
                                               "requires_note": "Pack the environment first."}])
    code, plan = install("plan", "packed")
    assert code == 3 and plan["blockers"][0]["reason_code"] == "build_input_missing"
    assert "Pack the environment first." in plan["blockers"][0]["message"]


def test_managed_store_image_and_layout_weights_count_as_present(install):
    """A managed install keeps images in the image store and weight sets under layouts/."""
    store = install.data / "apptainer" / ".image-store"
    image = store / "objects" / "sha256" / ("a" * 64) / "runtime.sif"
    image.parent.mkdir(parents=True)
    image.write_text("image")
    (store / "references").mkdir()
    (store / "references" / "state.json").write_text(json.dumps({
        "current": {"production": "r1"},
        "releases": {"r1": {"images": {"BMS_RUNTIME_IMAGE_DEMO_SIF": {"path": str(image)}}}}}))
    (install.data / "weights" / "layouts" / "demo" / ("b" * 64) / "model").mkdir(parents=True)
    code, listing = install("list")
    tool = listing["tools"][0]
    assert code == 0 and tool["status"] == "present-unverified"
    assert [i["present"] for i in tool["images"]] == [True]
    assert [w["present"] for w in tool["weights"]] == [True]
    code, done = install("verify", "demo")
    assert code == 0 and done["status"] == "ready"
    assert f"test {image}" in install.calls.read_text()
    assert not (install.data / "apptainer" / "demo.sif").exists()


def test_recipes_install_pytorch_releases_not_nightlies():
    for definition in sorted((ROOT / "apptainer").glob("*.def")):
        assert "whl/nightly" not in definition.read_text(), definition.name

