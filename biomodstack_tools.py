"""Tool installer: build or adopt the third-party images and weights BMS runs.

BioModStack ships its own code only. Every integrated scientific tool is built
by the person installing it, from that tool's normal upstream home, using a
recipe in ``tools/recipes``. This module is the single engine behind the
``./start_ui.sh tools ...`` commands and the Tools screen.

Contract for people and for their own automation or AI assistant:

* every command accepts ``--json`` and prints one JSON document;
* exit 0 means the command did what it says, exit 3 means blocked or failed;
* a failure always carries ``reason_code``, ``message`` and ``log_path``;
* "ready" means the smoke checks passed on this machine, nothing else.

Standard library only: it must run before any BMS environment exists.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request

SCHEMA = "bms.tools.v1"
RECIPE_SCHEMA = "bms.tool-recipe.v1"
BLOCKED_EXIT = 3
LABELS = ("builds-from-clean", "starting-point")
PROJECT_ROOT = Path(__file__).resolve().parent


# Parts of BioModStack a person can choose. First-party parts ship in the
# repository and need no build; the rest are served by recipes naming the module.
MODULES = {
    "molecular_tools": ("Molecular biology tools and plasmid viewer", True),
    "robot": ("Liquid-handler robot control", True),
    "ont": ("Nanopore sequencing (ONT)", False),
    "structure_prediction": ("Structure prediction", False),
    "design": ("Protein and binder design", False),
    "molecular_dynamics": ("Molecular dynamics", False),
}


class ToolError(Exception):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


# ---------------------------------------------------------------- recipes

def recipes_dir(project_root: Path = PROJECT_ROOT) -> Path:
    return project_root / "tools" / "recipes"


def _validate_recipe(recipe: dict, path: Path) -> dict:
    def need(cond, text):
        if not cond:
            raise ToolError("recipe_invalid", f"{path.name}: {text}")
    need(isinstance(recipe, dict), "recipe must be an object")
    need(recipe.get("schema") == RECIPE_SCHEMA, f"schema must be {RECIPE_SCHEMA}")
    need(recipe.get("id") == path.stem, "id must match the file name")
    need(isinstance(recipe.get("name"), str) and recipe["name"], "name is required")
    need(recipe.get("label") in LABELS, f"label must be one of {LABELS}")
    need(isinstance(recipe.get("modules"), list) and recipe["modules"], "modules is required")
    need(all(m in MODULES and not MODULES[m][1] for m in recipe["modules"]),
         f"modules must be among {sorted(m for m in MODULES if not MODULES[m][1])}")
    upstream = recipe.get("upstream")
    need(isinstance(upstream, dict) and upstream.get("homepage"), "upstream.homepage is required")
    images = recipe.get("images")
    need(isinstance(images, list) and images, "at least one image is required")
    for image in images:
        need(isinstance(image, dict), "image must be an object")
        name = image.get("filename", "")
        need(isinstance(name, str) and name.endswith(".sif") and "/" not in name,
             "image.filename must be a plain .sif file name")
        need(isinstance(image.get("def"), str) and not Path(image["def"]).is_absolute()
             and ".." not in Path(image["def"]).parts, "image.def must be a path inside the repository")
        for check in image.get("smoke", []):
            need(isinstance(check.get("argv"), list) and check["argv"], "smoke.argv must be a list")
    for weight in recipe.get("weights", []):
        need(isinstance(weight.get("id"), str) and weight["id"], "weights.id is required")
        need(isinstance(weight.get("present_if"), list) and weight["present_if"],
             "weights.present_if is required")
        for rel in weight["present_if"]:
            need(not Path(rel).is_absolute() and ".." not in Path(rel).parts,
                 "weights.present_if must be relative to the weights root")
        fetch = weight.get("fetch") or {}
        need(fetch.get("type") in ("url", "container", "manual"),
             "weights.fetch.type must be url, container or manual")
        if fetch["type"] == "url":
            for item in fetch.get("files", []):
                need(str(item.get("url", "")).startswith("https://"), "weights url must be https")
                need(not Path(item["dest"]).is_absolute() and ".." not in Path(item["dest"]).parts,
                     "weights dest must be relative to the weights root")
    return recipe


def load_recipes(project_root: Path = PROJECT_ROOT) -> dict[str, dict]:
    found = {}
    directory = recipes_dir(project_root)
    for path in sorted(directory.glob("*.json")):
        try:
            recipe = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ToolError("recipe_invalid", f"{path.name}: {exc}") from exc
        found[path.stem] = _validate_recipe(recipe, path)
    return found


def get_recipe(tool: str, project_root: Path = PROJECT_ROOT) -> dict:
    recipes = load_recipes(project_root)
    if tool not in recipes:
        raise ToolError("unknown_tool", f"No recipe named {tool!r}. Known: {', '.join(sorted(recipes)) or 'none'}")
    return recipes[tool]


# ------------------------------------------------------------ locations

def locations(project_root: Path = PROJECT_ROOT) -> dict[str, Path]:
    """Per-install locations, from the install profile and BMS_* overrides."""
    import biomodstack_runtime_profile as profiles
    profile = {}
    profile_path = profiles.get_install_profile_path()
    if profile_path.exists():
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
        profiles.validate_install_profile_raw(raw)
        profile = profiles.normalize_install_profile(raw)
    paths = profiles.resolve_runtime_paths(project_root=project_root, profile=profile)
    return {"container_dir": Path(str(paths["container_dir"])),
            "weights_root": Path(str(paths["weights_root"])),
            "state_dir": profiles.get_biomodstack_config_dir() / "tools-v1"}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read_state(state_dir: Path, tool: str) -> dict:
    try:
        return json.loads((state_dir / tool / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _build_running(state_dir: Path, tool: str) -> bool:
    lock = state_dir / tool / "lock"
    if not lock.exists():
        return False
    with open(lock, "a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def _recipe_digest(recipe: dict, project_root: Path) -> str:
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode())
    for image in recipe["images"]:
        try:
            digest.update((project_root / image["def"]).read_bytes())
        except OSError:
            digest.update(b"missing-def")
    return digest.hexdigest()


def _weight_present(weight: dict, weights_root: Path) -> bool:
    return all((weights_root / rel).exists() for rel in weight["present_if"])


def tool_status(recipe: dict, where: dict[str, Path], project_root: Path = PROJECT_ROOT) -> dict:
    tool = recipe["id"]
    state = _read_state(where["state_dir"], tool)
    images = []
    for image in recipe["images"]:
        path = where["container_dir"] / image["filename"]
        images.append({"filename": image["filename"], "path": str(path), "present": path.exists()})
    weights = [{"id": w["id"], "required": bool(w.get("required", False)),
                "present": _weight_present(w, where["weights_root"])}
               for w in recipe.get("weights", [])]
    all_images = all(i["present"] for i in images)
    required_weights = all(w["present"] for w in weights if w["required"])
    if _build_running(where["state_dir"], tool):
        status = "building"
    elif state.get("status") == "building":
        status = "failed"
        state = {**state, "status": "failed", "reason_code": "build_interrupted",
                 "message": "The build stopped before it finished. Run build again; downloads are reused."}
    elif state.get("status") == "failed":
        status = "failed"
    elif not any(i["present"] for i in images):
        status = "not-installed"
    elif not all_images or not required_weights:
        status = "incomplete"
    elif state.get("status") == "ready" and state.get("recipe_digest") == _recipe_digest(recipe, project_root):
        status = "ready"
    elif state.get("status") == "ready":
        status = "ready-recipe-changed"
    else:
        status = "present-unverified"
    return {"tool": tool, "name": recipe["name"], "label": recipe["label"],
            "modules": recipe["modules"], "status": status, "images": images, "weights": weights,
            "origin": state.get("origin"), "verified_at": state.get("verified_at"),
            "step": state.get("step"), "reason_code": state.get("reason_code"),
            "message": state.get("message"), "smoke": state.get("smoke"),
            "log_path": str(where["state_dir"] / tool / "build.log")}


# ------------------------------------------------------------------ plan

def _apptainer() -> str | None:
    return shutil.which("apptainer") or shutil.which("singularity")


def _free_bytes(path: Path) -> int:
    ancestor = path
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    return shutil.disk_usage(ancestor).free


def tool_plan(recipe: dict, where: dict[str, Path], project_root: Path = PROJECT_ROOT) -> dict:
    blockers = []
    runtime = _apptainer()
    if not runtime:
        blockers.append({"reason_code": "apptainer_missing",
                         "message": "Install Apptainer (https://apptainer.org/docs/admin/main/installation.html) and re-run."})
    steps = []
    image_gb = 0.0
    for image in recipe["images"]:
        definition = project_root / image["def"]
        if not definition.is_file():
            blockers.append({"reason_code": "recipe_definition_missing", "message": f"Missing {image['def']}"})
        image_gb += float(image.get("approx_size_gb", 0))
        steps.append({"step": f"build:{image['filename']}", "definition": image["def"],
                      "output": str(where["container_dir"] / image["filename"]),
                      "sources": image.get("sources", []), "approx_size_gb": image.get("approx_size_gb"),
                      "already_present": (where["container_dir"] / image["filename"]).exists()})
    for weight in recipe.get("weights", []):
        fetch = weight["fetch"]
        steps.append({"step": f"weights:{weight['id']}", "method": fetch["type"],
                      "required": bool(weight.get("required", False)),
                      "selected_by_default": bool(weight.get("default", weight.get("required", False))),
                      "license": weight.get("license"), "approx_size_gb": weight.get("approx_size_gb"),
                      "source": fetch.get("source") or [f["url"] for f in fetch.get("files", [])],
                      "instructions": fetch.get("instructions"),
                      "already_present": _weight_present(weight, where["weights_root"])})
    needed = int(image_gb * 3 * 1024 ** 3)
    free = _free_bytes(where["container_dir"])
    if needed and free < needed:
        blockers.append({"reason_code": "disk_space_low",
                         "message": f"Building needs about {image_gb * 3:.0f} GB free at {where['container_dir']}; {free / 1024 ** 3:.0f} GB available."})
    return {"schema": SCHEMA, "tool": recipe["id"], "name": recipe["name"], "label": recipe["label"],
            "upstream": recipe["upstream"], "license": recipe.get("license"),
            "notes": recipe.get("notes"), "container_runtime": runtime,
            "container_dir": str(where["container_dir"]), "weights_root": str(where["weights_root"]),
            "steps": steps, "blockers": blockers, "status": "blocked" if blockers else "planned"}


# ----------------------------------------------------------------- build

class _Run:
    """One build/verify run: owns the lock, the log and the state file."""

    def __init__(self, recipe, where, project_root):
        self.recipe, self.where, self.project_root = recipe, where, project_root
        self.dir = where["state_dir"] / recipe["id"]
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.dir / "build.log"
        self.state = _read_state(where["state_dir"], recipe["id"])
        self._lock = open(self.dir / "lock", "a+")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ToolError("build_already_running",
                            f"{recipe['id']} is already being built. Follow it with: tools log {recipe['id']}") from exc

    def save(self, **changes):
        self.state.update(changes)
        self.state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_json(self.dir / "state.json", self.state)

    def log(self, text):
        with open(self.log_path, "a", encoding="utf-8") as stream:
            stream.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")

    def command(self, argv, *, step, reason_code, env=None, cwd=None):
        self.save(status=self.state.get("status", "building"), step=step)
        self.log("$ " + " ".join(shlex.quote(str(a)) for a in argv))
        with open(self.log_path, "a", encoding="utf-8") as stream:
            code = subprocess.call([str(a) for a in argv], stdout=stream, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, env=env, cwd=cwd)
        if code != 0:
            raise ToolError(reason_code, f"Step {step} exited with code {code}. See the log.")

    def close(self):
        self._lock.close()


def _gpu_available() -> bool:
    return bool(shutil.which("nvidia-smi")) and subprocess.call(
        ["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def _smoke(run: _Run, image: dict, path: Path) -> list[dict]:
    runtime = _apptainer()
    if not runtime:
        raise ToolError("apptainer_missing", "Apptainer is required to check an image.")
    results = []
    gpu = _gpu_available()
    if image.get("run_definition_test", True):
        run.command([runtime, "test", path], step=f"smoke:{image['filename']}:definition-test",
                    reason_code="smoke_test_failed")
        results.append({"check": "definition-test", "image": image["filename"], "result": "passed"})
    for check in image.get("smoke", []):
        name = check.get("name") or " ".join(check["argv"][:2])
        if check.get("gpu") and not gpu:
            results.append({"check": name, "image": image["filename"], "result": "skipped-no-gpu"})
            run.log(f"skipped (no NVIDIA GPU visible): {name}")
            continue
        argv = [runtime, "exec"] + (["--nv"] if check.get("gpu") else []) + [path] + check["argv"]
        run.command(argv, step=f"smoke:{image['filename']}:{name}", reason_code="smoke_test_failed")
        results.append({"check": name, "image": image["filename"], "result": "passed"})
    return results


def _download(run: _Run, url: str, destination: Path, sha256: str | None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    offset = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(url, headers={"User-Agent": "biomodstack-tools"})
    if offset:
        request.add_header("Range", f"bytes={offset}-")
    run.log(f"download {url} -> {destination} (resume at {offset})")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            mode = "ab" if offset and response.status == 206 else "wb"
            with open(partial, mode) as stream:
                shutil.copyfileobj(response, stream, 1024 * 1024)
    except OSError as exc:
        raise ToolError("weights_download_failed", f"{url}: {exc}. Run build again to resume.") from exc
    if sha256:
        digest = hashlib.sha256()
        with open(partial, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != sha256:
            partial.unlink()
            raise ToolError("weights_hash_mismatch", f"{url} does not match the recipe hash; the partial file was removed.")
    os.replace(partial, destination)


def _fetch_weights(run: _Run, selected: set[str] | None, accepted: set[str]) -> None:
    recipe, where = run.recipe, run.where
    for weight in recipe.get("weights", []):
        chosen = (weight["id"] in selected) if selected is not None else bool(
            weight.get("default", weight.get("required", False)))
        if not chosen or _weight_present(weight, where["weights_root"]):
            continue
        step = f"weights:{weight['id']}"
        run.save(step=step)
        license_id = (weight.get("license") or {}).get("id")
        if license_id and license_id not in accepted:
            raise ToolError("license_not_accepted",
                            f"{weight['id']} is under {license_id} ({(weight.get('license') or {}).get('url', 'see upstream')}). "
                            f"Read it, then re-run with --accept-license {license_id}")
        fetch = weight["fetch"]
        if fetch["type"] == "manual":
            raise ToolError("weights_manual_step",
                            f"{weight['id']} must be placed by hand: {fetch.get('instructions', 'see upstream')}")
        if fetch["type"] == "url":
            for item in fetch["files"]:
                target = where["weights_root"] / item["dest"]
                if not target.exists():
                    _download(run, item["url"], target, item.get("sha256"))
        else:
            where["weights_root"].mkdir(parents=True, exist_ok=True)
            image = where["container_dir"] / (fetch.get("image") or recipe["images"][0]["filename"])
            run.command([_apptainer(), "exec", "--bind", f"{where['weights_root']}:/weights", image] + fetch["argv"],
                        step=step, reason_code="weights_download_failed")
        if not _weight_present(weight, where["weights_root"]):
            raise ToolError("weights_incomplete", f"{weight['id']} finished but expected files are missing: {weight['present_if']}")


def _finish_ready(run: _Run, smoke: list[dict], origin: str) -> None:
    run.save(status="ready", step=None, reason_code=None, message=None, origin=origin, smoke=smoke,
             recipe_digest=_recipe_digest(run.recipe, run.project_root),
             verified_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def _guard(run: _Run, work) -> dict:
    try:
        work()
    except ToolError as exc:
        run.log(f"FAILED [{exc.reason_code}] {exc.message}")
        run.save(status="failed", reason_code=exc.reason_code, message=exc.message)
    finally:
        run.close()
    return tool_status(run.recipe, run.where, run.project_root)


def build_tool(recipe, where, project_root=PROJECT_ROOT, *, replace=False, weights=None,
               accepted_licenses=(), skip_weights=False) -> dict:
    plan = tool_plan(recipe, where, project_root)
    if plan["blockers"]:
        first = plan["blockers"][0]
        raise ToolError(first["reason_code"], first["message"])
    run = _Run(recipe, where, project_root)

    def work():
        run.log_path.write_text("", encoding="utf-8")
        run.save(status="building", reason_code=None, message=None, origin="built", smoke=None,
                 started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        staging = where["container_dir"] / ".tools-build" / recipe["id"]
        staging.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.setdefault("APPTAINER_TMPDIR", str(staging))
        extra = shlex.split(os.environ.get("BMS_TOOLS_APPTAINER_BUILD_FLAGS", ""))
        smoke = []
        for image in recipe["images"]:
            target = where["container_dir"] / image["filename"]
            if target.exists() or target.is_symlink():
                if not replace:
                    run.log(f"keeping existing {target} (use --replace to rebuild it)")
                    smoke.extend(_smoke(run, image, target))
                    continue
            candidate = staging / image["filename"]
            argv = [_apptainer(), "build", "--force", *extra]
            for key, value in sorted((image.get("build_args") or {}).items()):
                argv += ["--build-arg", f"{key}={value}"]
            run.command(argv + [candidate, project_root / image["def"]], step=f"build:{image['filename']}",
                        reason_code="image_build_failed", env=env, cwd=project_root)
            # Checked before it is installed: a failing image never replaces a working one.
            smoke.extend(_smoke(run, {**image, "smoke": [c for c in image.get("smoke", [])]}, candidate))
            os.replace(candidate, target)
            run.save(installed=sorted(set(run.state.get("installed", [])) | {image["filename"]}))
        shutil.rmtree(staging, ignore_errors=True)
        if not skip_weights:
            _fetch_weights(run, set(weights) if weights else None, set(accepted_licenses))
        _finish_ready(run, smoke, "built")
    return _guard(run, work)


def verify_tool(recipe, where, project_root=PROJECT_ROOT) -> dict:
    run = _Run(recipe, where, project_root)

    def work():
        run.log("verify")
        smoke = []
        for image in recipe["images"]:
            path = where["container_dir"] / image["filename"]
            if not path.exists():
                raise ToolError("image_missing", f"{path} is not present. Build it or adopt an existing image.")
            smoke.extend(_smoke(run, image, path))
        missing = [w["id"] for w in recipe.get("weights", [])
                   if w.get("required") and not _weight_present(w, where["weights_root"])]
        if missing:
            raise ToolError("weights_missing", f"Required weights are not present: {', '.join(missing)}")
        _finish_ready(run, smoke, run.state.get("origin") or "existing")
    return _guard(run, work)


def adopt_tool(recipe, where, source: Path, project_root=PROJECT_ROOT, *, image_name=None, copy=False) -> dict:
    names = [i["filename"] for i in recipe["images"]]
    name = image_name or (names[0] if len(names) == 1 else None)
    if name not in names:
        raise ToolError("image_name_required", f"This tool has several images; pass --image with one of: {', '.join(names)}")
    source = source.expanduser().resolve()
    if not source.is_file():
        raise ToolError("adopt_source_missing", f"{source} is not a file")
    target = where["container_dir"] / name
    if target.exists() or target.is_symlink():
        if target.resolve() != source:
            raise ToolError("image_already_present", f"{target} already exists. Remove it first or verify it as it is.")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        if copy:
            shutil.copy2(source, target)
        else:
            target.symlink_to(source)
    state_dir = where["state_dir"] / recipe["id"]
    state_dir.mkdir(parents=True, exist_ok=True)
    state = _read_state(where["state_dir"], recipe["id"])
    state.update({"status": "adopted", "origin": "adopted", "reason_code": None, "message": None})
    if copy:
        state["installed"] = sorted(set(state.get("installed", [])) | {name})
    else:
        state["linked"] = sorted(set(state.get("linked", [])) | {name})
    _write_json(state_dir / "state.json", state)
    if all((where["container_dir"] / n).exists() for n in names):
        return verify_tool(recipe, where, project_root)
    return tool_status(recipe, where, project_root)


def remove_tool(recipe, where, project_root=PROJECT_ROOT) -> dict:
    """Remove only what this installer put in place. Adopted originals are never deleted."""
    run = _Run(recipe, where, project_root)
    try:
        removed, kept = [], []
        for image in recipe["images"]:
            path = where["container_dir"] / image["filename"]
            if image["filename"] in run.state.get("linked", []) and path.is_symlink():
                path.unlink()
                removed.append(str(path))
            elif image["filename"] in run.state.get("installed", []) and path.is_file() and not path.is_symlink():
                path.unlink()
                removed.append(str(path))
            elif path.exists() or path.is_symlink():
                kept.append(str(path))
        _write_json(run.dir / "state.json", {})
    finally:
        run.close()
    status = tool_status(recipe, where, project_root)
    return {**status, "removed": removed, "kept_not_installed_by_tools": kept}


# -------------------------------------------------------------- modules

def _selection_path(where: dict[str, Path]) -> Path:
    return where["state_dir"] / "selection.json"


def selected_modules(where: dict[str, Path]) -> list[str] | None:
    """The modules this install chose, or None when nothing was chosen yet."""
    try:
        chosen = json.loads(_selection_path(where).read_text(encoding="utf-8")).get("modules")
    except (OSError, ValueError):
        return None
    return [m for m in chosen if m in MODULES] if isinstance(chosen, list) else None


def modules_overview(where: dict[str, Path], project_root: Path = PROJECT_ROOT) -> dict:
    chosen = selected_modules(where)
    rows = [tool_status(r, where, project_root) for r in load_recipes(project_root).values()]
    modules = []
    for key, (title, first_party) in MODULES.items():
        tools = [{"tool": r["tool"], "status": r["status"]} for r in rows if key in r["modules"]]
        if first_party:
            state = "included"
        elif not tools:
            state = "no-recipe-yet"
        elif all(t["status"] == "ready" for t in tools):
            state = "ready"
        else:
            state = "needs-tools"
        modules.append({"module": key, "title": title, "first_party": first_party,
                        "selected": first_party or (chosen is not None and key in chosen),
                        "state": state, "tools": tools})
    return {"schema": SCHEMA, "selection_made": chosen is not None, "modules": modules}


def select_modules(where: dict[str, Path], names: list[str]) -> None:
    unknown = [n for n in names if n not in MODULES]
    if unknown:
        raise ToolError("unknown_module", f"Unknown module: {', '.join(unknown)}. Known: {', '.join(MODULES)}")
    _write_json(_selection_path(where), {"schema": SCHEMA, "modules": sorted(set(names))})


def _render_modules(overview: dict) -> str:
    lines = [f"{'':<3} {'MODULE':<22} {'STATE':<15} TOOLS"]
    for row in overview["modules"]:
        tools = ", ".join(f"{t['tool']} ({t['status']})" for t in row["tools"]) or "-"
        lines.append(f"{'[x]' if row['selected'] else '[ ]'} {row['module']:<22} {row['state']:<15} {tools}")
    lines.append("")
    lines.append("Choose: ./start_ui.sh tools select MODULE [MODULE ...]   then   ./start_ui.sh tools list --selected")
    return "\n".join(lines)


# ------------------------------------------------------------------- CLI

def _render_list(rows: list[dict]) -> str:
    lines = [f"{'TOOL':<16} {'STATUS':<22} {'RECIPE':<18} MODULES"]
    for row in rows:
        lines.append(f"{row['tool']:<16} {row['status']:<22} {row['label']:<18} {', '.join(row['modules'])}")
    lines.append("")
    lines.append("Next: ./start_ui.sh tools plan TOOL   then   ./start_ui.sh tools build TOOL")
    return "\n".join(lines)


def _render_plan(plan: dict) -> str:
    lines = [f"{plan['name']} ({plan['tool']}) - recipe: {plan['label']}",
             f"Upstream: {plan['upstream'].get('homepage')}"]
    if plan.get("license"):
        lines.append(f"License: {plan['license'].get('id')} {plan['license'].get('url', '')}")
    for step in plan["steps"]:
        size = f", about {step['approx_size_gb']} GB" if step.get("approx_size_gb") else ""
        done = " (already present)" if step["already_present"] else ""
        lines.append(f"  {step['step']}{size}{done}")
    if plan.get("notes"):
        lines.append(f"Notes: {plan['notes']}")
    lines.extend(f"BLOCKED [{b['reason_code']}]: {b['message']}" for b in plan["blockers"])
    return "\n".join(lines)


def _render_status(status: dict) -> str:
    lines = [f"{status['name']} ({status['tool']}): {status['status']}"]
    for image in status["images"]:
        lines.append(f"  image {image['filename']}: {'present' if image['present'] else 'missing'}")
    for weight in status["weights"]:
        lines.append(f"  weights {weight['id']}: {'present' if weight['present'] else 'missing'}"
                     f"{' (required)' if weight['required'] else ''}")
    if status["status"] == "failed":
        lines.append(f"FAILED [{status['reason_code']}] at {status['step']}: {status['message']}")
        lines.append(f"Log: {status['log_path']}")
    return "\n".join(lines)


def main(argv=None, project_root: Path = PROJECT_ROOT) -> int:
    parser = argparse.ArgumentParser(prog="start_ui.sh tools", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_text, tool=True):
        command = sub.add_parser(name, help=help_text)
        if tool:
            command.add_argument("tool")
        command.add_argument("--json", action="store_true", dest="json_output")
        return command
    listing = add("list", "show every tool and its state on this machine", tool=False)
    listing.add_argument("--selected", action="store_true", help="only tools for the modules you chose")
    add("modules", "show the parts of BioModStack you can choose and what each needs", tool=False)
    select = add("select", "choose which parts this install uses", tool=False)
    select.add_argument("modules", nargs="*", metavar="MODULE")
    add("plan", "show what building a tool will fetch, build and need")
    build = add("build", "build the images, fetch selected weights, run the smoke checks")
    build.add_argument("--detach", action="store_true", help="run in the background; follow with status/log")
    build.add_argument("--replace", action="store_true", help="rebuild images that already exist")
    build.add_argument("--weights", action="append", default=[], help="weights id to fetch; repeatable")
    build.add_argument("--skip-weights", action="store_true")
    build.add_argument("--accept-license", action="append", default=[], help="license id you have read and accept")
    adopt = add("adopt", "use an image you already built yourself")
    adopt.add_argument("path", type=Path)
    adopt.add_argument("--image", help="which image file this is, for tools with several")
    adopt.add_argument("--copy", action="store_true", help="copy instead of linking")
    add("verify", "run the smoke checks on what is installed")
    add("status", "state, failure reason and log path for one tool")
    log = add("log", "print the build log")
    log.add_argument("--tail", type=int, default=0)
    add("remove", "remove what this installer put in place")
    args = parser.parse_args(argv)

    def emit(document, text, ok=True):
        print(json.dumps(document, indent=2, sort_keys=True) if args.json_output else text)
        return 0 if ok else BLOCKED_EXIT
    try:
        where = locations(project_root)
        if args.command == "list":
            rows = [tool_status(r, where, project_root) for r in load_recipes(project_root).values()]
            if args.selected:
                chosen = set(selected_modules(where) or [])
                rows = [r for r in rows if chosen & set(r["modules"])]
            return emit({"schema": SCHEMA, "tools": rows}, _render_list(rows))
        if args.command in ("modules", "select"):
            if args.command == "select":
                select_modules(where, args.modules)
            overview = modules_overview(where, project_root)
            return emit(overview, _render_modules(overview))
        recipe = get_recipe(args.tool, project_root)
        if args.command == "plan":
            plan = tool_plan(recipe, where, project_root)
            return emit(plan, _render_plan(plan), not plan["blockers"])
        if args.command == "log":
            path = where["state_dir"] / recipe["id"] / "build.log"
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
            lines = lines[-args.tail:] if args.tail else lines
            return emit({"schema": SCHEMA, "tool": recipe["id"], "log_path": str(path), "lines": lines}, "\n".join(lines))
        if args.command == "status":
            status = tool_status(recipe, where, project_root)
        elif args.command == "verify":
            status = verify_tool(recipe, where, project_root)
        elif args.command == "adopt":
            status = adopt_tool(recipe, where, args.path, project_root, image_name=args.image, copy=args.copy)
        elif args.command == "remove":
            status = remove_tool(recipe, where, project_root)
        elif args.detach:
            plan = tool_plan(recipe, where, project_root)
            if plan["blockers"]:
                raise ToolError(plan["blockers"][0]["reason_code"], plan["blockers"][0]["message"])
            if _build_running(where["state_dir"], recipe["id"]):
                raise ToolError("build_already_running", f"{recipe['id']} is already being built.")
            forwarded = [a for a in (argv if argv is not None else sys.argv[1:]) if a not in ("--detach", "--json")]
            (where["state_dir"] / recipe["id"]).mkdir(parents=True, exist_ok=True)
            with open(where["state_dir"] / recipe["id"] / "launch.log", "w") as out:
                subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), *forwarded],
                                 stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 start_new_session=True, cwd=str(project_root))
            for _ in range(50):
                if _build_running(where["state_dir"], recipe["id"]):
                    break
                time.sleep(0.1)
            status = tool_status(recipe, where, project_root)
            status = {**status, "started": True}
            return emit({"schema": SCHEMA, **status},
                        f"Started building {recipe['id']} in the background.\n"
                        f"Follow: ./start_ui.sh tools status {recipe['id']}   or   tools log {recipe['id']} --tail 40")
        else:
            status = build_tool(recipe, where, project_root, replace=args.replace, weights=args.weights,
                                accepted_licenses=args.accept_license, skip_weights=args.skip_weights)
        return emit({"schema": SCHEMA, **status}, _render_status(status), status["status"] != "failed")
    except ToolError as exc:
        document = {"schema": SCHEMA, "status": "blocked", "reason_code": exc.reason_code, "message": exc.message}
        print(json.dumps(document, indent=2, sort_keys=True) if getattr(args, "json_output", False)
              else f"BLOCKED [{exc.reason_code}]: {exc.message}")
        return BLOCKED_EXIT


if __name__ == "__main__":
    sys.exit(main())
