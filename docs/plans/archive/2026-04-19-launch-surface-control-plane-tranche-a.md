# BioModStack Safe Launch-Surface Tranche A Implementation Plan

> **For Hermes:** Use subagent-driven-development to implement this plan task-by-task. Keep this tranche narrow: runtime descriptor, structured status output, opt-in launcher, and explicit no-behavior-change protection for existing entrypoints.

**Goal:** Add the first safe implementation slice for browser-vs-Electron launch surfaces by introducing a structured Python runtime descriptor, an opt-in launcher path, and regression guards that preserve the current `start_ui.sh` service-control behavior.

**Architecture:** Keep runtime ownership in `biomodstack_services.py` and `systemd --user`. Do not make Electron, the new launcher, or `start_ui.sh` the owner of long-lived backend processes. This tranche adds a machine-readable control contract and a new opt-in UI launcher without changing the meaning of `./start_ui.sh start` on the live workstation checkout.

**Tech Stack:** Python 3, `argparse`, `json`, `pathlib`, `webbrowser`, `pytest`, existing `biomodstack_services.py`, `scripts/manage_desktop_services.py`, `start_ui.sh`, and the backend test suite under `platform/api/tests/`.

---

## Scope and non-goals

### In scope for this tranche
- add shared launch-surface constants and launch-preference helpers
- add `runtime_descriptor(...)` in `biomodstack_services.py`
- make runtime URLs/basenames explicit per runtime mode
- add `python3 scripts/manage_desktop_services.py status --json`
- add an opt-in launcher script: `scripts/launch_biomodstack_ui.py`
- make `browser` and `none` surfaces work now
- make `electron` fail clearly and intentionally until the Electron package exists
- add regression tests proving `start_ui.sh` still forwards directly to `scripts/manage_desktop_services.py`

### Explicitly out of scope for this tranche
- no Electron workspace yet (`platform/desktop-electron/` comes later)
- no panel/tray rewiring yet (`biomodstack_panel.py`, `biomodstack_tray.py` stay untouched)
- no default-behavior change for `./start_ui.sh start`
- no route-preserving “Open in Browser” work yet
- no Nextflow/BioXP behavior changes
- no container/runtime ownership changes

### Grounding from the current repo
- `start_ui.sh` currently does exactly one thing for valid actions: `exec python3 "$MANAGER" "$ACTION" "$@"`
- `scripts/manage_desktop_services.py` currently supports `start|stop|restart|restart-api|status` plus `--runtime dev|container`
- `biomodstack_services.py` already owns runtime-mode resolution, service names, start/stop/restart, health checks, and log paths
- `biomodstack_services.py` currently hardcodes `FRONTEND_URL = http://127.0.0.1:5173/bms/`, so this tranche should replace that with runtime-aware URL helpers instead of making Electron or the launcher guess
- there is already backend test coverage in `platform/api/tests/test_biomodstack_services.py`, but there is no script-level regression coverage yet for `start_ui.sh` or a future launcher

---

## Contract to implement in this tranche

The new Python runtime descriptor should be the source of truth for shells and launchers.

Recommended minimum JSON shape:

```json
{
  "runtime_mode": "container",
  "runtime_active": true,
  "runtime_manager": "systemd-user",
  "api_url": "http://127.0.0.1:8000",
  "frontend_origin": "http://127.0.0.1:5173",
  "frontend_url": "http://127.0.0.1:5173/bms/",
  "browser_url": "http://127.0.0.1:5173/bms/",
  "router_basename": "/bms/",
  "supported_launch_surfaces": ["browser", "electron", "none"],
  "launch_preferences": {
    "default_surface": "browser",
    "auto_open_hosted_web_on_start": true
  },
  "health": {
    "api_ready": true,
    "frontend_ready": true
  },
  "services": [
    {"name": "biomodstack-core-runtime.service", "active": true}
  ],
  "logs": [
    {"id": "runtime", "label": "Core runtime log", "path": "/home/dalab/.local/state/biomodstack/logs/core-runtime.log"}
  ],
  "capabilities": {
    "open_in_browser": true,
    "restart_all": true,
    "restart_api": true,
    "stop_all": true
  }
}
```

Rules for this tranche:
- `dev` mode uses `/` as `router_basename` and `http://127.0.0.1:5173/` as `frontend_url`
- `container` mode uses `/bms/` as `router_basename` and `http://127.0.0.1:5173/bms/` as `frontend_url`
- `browser_url` may equal `frontend_url` in this tranche
- supported surfaces are always `browser`, `electron`, `none`
- stored launch preferences live at `~/.config/biomodstack/launch_preferences.json` unless `XDG_CONFIG_HOME` overrides the base config directory
- explicit `--surface ...` beats stored defaults
- until the Electron package exists, `--surface electron` must fail with a clear, intentional diagnostic instead of pretending to succeed

---

## Task 1: Add shared launch-surface constants and launch-preference helpers

**Objective:** Create one Python source of truth for supported launch surfaces and persisted launch preferences.

**Files:**
- Modify: `biomodstack_services.py`
- Test: `platform/api/tests/test_biomodstack_services.py`

**Step 1: Write failing tests**

Add these tests to `platform/api/tests/test_biomodstack_services.py`:

```python
def test_launch_preferences_default_to_browser_and_auto_open(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    prefs = services.load_launch_preferences()

    assert prefs == {
        "default_surface": services.BROWSER_LAUNCH_SURFACE,
        "auto_open_hosted_web_on_start": True,
    }


def test_launch_preferences_normalize_invalid_surface_to_browser(tmp_path: Path, monkeypatch) -> None:
    config_home = tmp_path / "config"
    prefs_path = config_home / "biomodstack" / "launch_preferences.json"
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    prefs_path.write_text(
        '{"default_surface": "sideways", "auto_open_hosted_web_on_start": false}',
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    prefs = services.load_launch_preferences()

    assert prefs == {
        "default_surface": services.BROWSER_LAUNCH_SURFACE,
        "auto_open_hosted_web_on_start": False,
    }
```

**Step 2: Run the targeted tests and verify they fail**

Run:

```bash
cd /home/dalab/biomodstack/biomodstack
/home/dalab/.local/bin/uv run --directory platform/api python -m pytest \
  tests/test_biomodstack_services.py::test_launch_preferences_default_to_browser_and_auto_open \
  tests/test_biomodstack_services.py::test_launch_preferences_normalize_invalid_surface_to_browser -q
```

Expected: FAIL because `load_launch_preferences` and the launch-surface constants do not exist yet.

**Step 3: Write the minimal implementation**

Add this near the top of `biomodstack_services.py`:

```python
import json
from collections.abc import Mapping

BROWSER_LAUNCH_SURFACE = "browser"
ELECTRON_LAUNCH_SURFACE = "electron"
NONE_LAUNCH_SURFACE = "none"
SUPPORTED_LAUNCH_SURFACES = (
    BROWSER_LAUNCH_SURFACE,
    ELECTRON_LAUNCH_SURFACE,
    NONE_LAUNCH_SURFACE,
)
DEFAULT_LAUNCH_PREFERENCES = {
    "default_surface": BROWSER_LAUNCH_SURFACE,
    "auto_open_hosted_web_on_start": True,
}


def get_biomodstack_config_dir() -> Path:
    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser().resolve() / "biomodstack"
    return Path.home().resolve() / ".config" / "biomodstack"


def get_launch_preferences_path() -> Path:
    return get_biomodstack_config_dir() / "launch_preferences.json"


def normalize_launch_preferences(raw: Mapping[str, object] | None) -> dict[str, object]:
    raw = raw or {}
    surface = str(raw.get("default_surface") or BROWSER_LAUNCH_SURFACE).strip().lower()
    if surface not in SUPPORTED_LAUNCH_SURFACES:
        surface = BROWSER_LAUNCH_SURFACE
    return {
        "default_surface": surface,
        "auto_open_hosted_web_on_start": bool(raw.get("auto_open_hosted_web_on_start", True)),
    }


def load_launch_preferences() -> dict[str, object]:
    path = get_launch_preferences_path()
    if not path.exists():
        return DEFAULT_LAUNCH_PREFERENCES.copy()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return DEFAULT_LAUNCH_PREFERENCES.copy()
    if not isinstance(data, Mapping):
        return DEFAULT_LAUNCH_PREFERENCES.copy()
    return normalize_launch_preferences(data)
```

**Step 4: Re-run the targeted tests**

Run the same `uv run --directory platform/api python -m pytest ...` command from Step 2.

Expected: PASS.

**Step 5: Commit**

```bash
git add biomodstack_services.py platform/api/tests/test_biomodstack_services.py
git commit -m "feat: add launch preference helpers"
```

---

## Task 2: Add runtime-aware URL helpers and `runtime_descriptor(...)`

**Objective:** Make runtime URLs, basenames, service lists, and log descriptors explicit in Python so shells do not guess.

**Files:**
- Modify: `biomodstack_services.py`
- Test: `platform/api/tests/test_biomodstack_services.py`

**Step 1: Write failing tests**

Add these tests:

```python
def test_runtime_descriptor_for_dev_mode(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(services, "service_is_active", lambda name, project_root=None: name == services.API_SERVICE)
    monkeypatch.setattr(services, "url_is_ready", lambda url, timeout_seconds=2.0: True)
    monkeypatch.setattr(
        services,
        "load_launch_preferences",
        lambda: {
            "default_surface": services.BROWSER_LAUNCH_SURFACE,
            "auto_open_hosted_web_on_start": True,
        },
    )

    descriptor = services.runtime_descriptor(project_root=project_root, runtime_mode="dev")

    assert descriptor["runtime_mode"] == "dev"
    assert descriptor["frontend_url"] == "http://127.0.0.1:5173/"
    assert descriptor["browser_url"] == "http://127.0.0.1:5173/"
    assert descriptor["router_basename"] == "/"
    assert descriptor["supported_launch_surfaces"] == ["browser", "electron", "none"]
    assert descriptor["services"] == [
        {"name": services.API_SERVICE, "active": True},
        {"name": services.FRONTEND_SERVICE, "active": False},
    ]


def test_runtime_descriptor_for_container_mode(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(services, "service_is_active", lambda name, project_root=None: name == services.CORE_RUNTIME_SERVICE)
    monkeypatch.setattr(services, "url_is_ready", lambda url, timeout_seconds=2.0: True)
    monkeypatch.setattr(
        services,
        "load_launch_preferences",
        lambda: {
            "default_surface": services.BROWSER_LAUNCH_SURFACE,
            "auto_open_hosted_web_on_start": True,
        },
    )

    descriptor = services.runtime_descriptor(project_root=project_root, runtime_mode="container")

    assert descriptor["runtime_mode"] == "container"
    assert descriptor["frontend_url"] == "http://127.0.0.1:5173/bms/"
    assert descriptor["browser_url"] == "http://127.0.0.1:5173/bms/"
    assert descriptor["router_basename"] == "/bms/"
    assert descriptor["logs"] == [
        {
            "id": "runtime",
            "label": "Core runtime log",
            "path": str(services.CORE_RUNTIME_LOG),
        }
    ]
```

**Step 2: Run the targeted tests and verify they fail**

Run:

```bash
cd /home/dalab/biomodstack/biomodstack
/home/dalab/.local/bin/uv run --directory platform/api python -m pytest \
  tests/test_biomodstack_services.py::test_runtime_descriptor_for_dev_mode \
  tests/test_biomodstack_services.py::test_runtime_descriptor_for_container_mode -q
```

Expected: FAIL because `runtime_descriptor` and the runtime-aware URL helpers do not exist yet.

**Step 3: Write the minimal implementation**

Add these helpers to `biomodstack_services.py`:

```python
def runtime_frontend_origin() -> str:
    return f"http://127.0.0.1:{FRONTEND_PORT}"


def runtime_router_basename(runtime_mode: str | None = None) -> str:
    mode = resolve_runtime_mode(runtime_mode)
    return "/bms/" if mode == CONTAINER_RUNTIME_MODE else "/"


def runtime_frontend_url(runtime_mode: str | None = None) -> str:
    origin = runtime_frontend_origin()
    basename = runtime_router_basename(runtime_mode)
    return f"{origin}{basename.lstrip('/')}" if basename != "/" else f"{origin}/"


def runtime_log_descriptors(runtime_mode: str | None = None) -> list[dict[str, str]]:
    mode = resolve_runtime_mode(runtime_mode)
    if mode == CONTAINER_RUNTIME_MODE:
        return [{"id": "runtime", "label": "Core runtime log", "path": str(CORE_RUNTIME_LOG)}]
    return [
        {"id": "api", "label": "API log", "path": str(API_LOG)},
        {"id": "frontend", "label": "Frontend log", "path": str(FRONTEND_LOG)},
    ]


def runtime_service_descriptors(project_root: Path | None = None, runtime_mode: str | None = None) -> list[dict[str, object]]:
    root = (project_root or get_project_root()).resolve()
    return [
        {"name": service_name, "active": service_is_active(service_name, root)}
        for service_name in runtime_service_names(runtime_mode)
    ]


def runtime_descriptor(project_root: Path | None = None, runtime_mode: str | None = None) -> dict[str, object]:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    frontend_url = runtime_frontend_url(mode)
    descriptor = {
        "runtime_mode": mode,
        "runtime_active": any(item["active"] for item in runtime_service_descriptors(root, mode)),
        "runtime_manager": "systemd-user",
        "api_url": f"http://127.0.0.1:{API_PORT}",
        "frontend_origin": runtime_frontend_origin(),
        "frontend_url": frontend_url,
        "browser_url": frontend_url,
        "router_basename": runtime_router_basename(mode),
        "supported_launch_surfaces": list(SUPPORTED_LAUNCH_SURFACES),
        "launch_preferences": load_launch_preferences(),
        "health": {
            "api_ready": url_is_ready(API_HEALTH_URL),
            "frontend_ready": url_is_ready(frontend_url),
        },
        "services": runtime_service_descriptors(root, mode),
        "logs": runtime_log_descriptors(mode),
        "capabilities": {
            "open_in_browser": True,
            "restart_all": True,
            "restart_api": True,
            "stop_all": True,
        },
    }
    return descriptor
```

**Step 4: Re-run the targeted tests**

Run the same command from Step 2.

Expected: PASS.

**Step 5: Commit**

```bash
git add biomodstack_services.py platform/api/tests/test_biomodstack_services.py
git commit -m "feat: add biomodstack runtime descriptor"
```

---

## Task 3: Route startup waits and human-readable status through the runtime-aware URLs

**Objective:** Ensure the service layer itself uses the same runtime-specific frontend URLs that the descriptor exposes, without changing the human-readable CLI contract.

**Files:**
- Modify: `biomodstack_services.py`
- Test: `platform/api/tests/test_biomodstack_services.py`

**Step 1: Write failing tests**

Add these tests:

```python
def test_start_all_dev_mode_waits_for_dev_frontend_url(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: calls.append(("ensure", runtime_mode)))
    monkeypatch.setattr(services, "cleanup_legacy_listener", lambda kind, project_root=None: calls.append(("cleanup", kind)))
    monkeypatch.setattr(services, "run_systemctl", lambda *args, **kwargs: calls.append(("systemctl", args)) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(services, "should_cleanup_legacy_listeners_before_start", lambda runtime_mode=None, project_root=None: True)
    monkeypatch.setattr(services, "wait_for_http", lambda url, timeout_seconds=30.0: calls.append(("wait", url)))

    services.start_all(project_root=project_root, runtime_mode="dev")

    assert calls[-1] == ("wait", "http://127.0.0.1:5173/")


def test_status_lines_keep_existing_container_human_output(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    monkeypatch.setattr(services, "ensure_user_units", lambda root, runtime_mode=None: None)
    monkeypatch.setattr(
        services,
        "runtime_descriptor",
        lambda project_root=None, runtime_mode=None: {
            "runtime_mode": "container",
            "runtime_active": True,
            "api_url": "http://127.0.0.1:8000",
            "frontend_url": "http://127.0.0.1:5173/bms/",
            "health": {"api_ready": True, "frontend_ready": False},
            "logs": [{"id": "runtime", "label": "Core runtime log", "path": str(services.CORE_RUNTIME_LOG)}],
        },
    )

    lines = services.status_lines(project_root=project_root, runtime_mode="container")

    assert lines == [
        f"Runtime: active ({services.CORE_RUNTIME_SERVICE})",
        f"API: ready ({services.API_HEALTH_URL})",
        "Frontend: not ready (http://127.0.0.1:5173/bms/)",
        f"Runtime log: {services.CORE_RUNTIME_LOG}",
    ]
```

**Step 2: Run the targeted tests and verify they fail**

Run:

```bash
cd /home/dalab/biomodstack/biomodstack
/home/dalab/.local/bin/uv run --directory platform/api python -m pytest \
  tests/test_biomodstack_services.py::test_start_all_dev_mode_waits_for_dev_frontend_url \
  tests/test_biomodstack_services.py::test_status_lines_keep_existing_container_human_output -q
```

Expected: FAIL because `start_all(...)` still uses the global `FRONTEND_URL` constant and `status_lines(...)` is not yet descriptor-backed.

**Step 3: Write the minimal implementation**

Update `start_all`, `restart_all`, and `status_lines`:

```python
def start_all(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    frontend_url = runtime_frontend_url(mode)
    ensure_user_units(root, runtime_mode=mode)
    incompatible_services = incompatible_runtime_service_names(mode)
    run_systemctl("stop", *incompatible_services, check=False, project_root=root)
    if should_cleanup_legacy_listeners_before_start(mode, project_root=root):
        cleanup_legacy_listener("api", root)
        cleanup_legacy_listener("frontend", root)
    run_systemctl("start", *runtime_service_names(mode), TARGET_UNIT, project_root=root)
    wait_for_http(API_HEALTH_URL)
    wait_for_http(frontend_url)


def restart_all(project_root: Path | None = None, runtime_mode: str | None = None) -> None:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    frontend_url = runtime_frontend_url(mode)
    ensure_user_units(root, runtime_mode=mode)
    run_systemctl("stop", TARGET_UNIT, check=False, project_root=root)
    run_systemctl("stop", *all_runtime_service_names(), check=False, project_root=root)
    cleanup_legacy_listener("api", root)
    cleanup_legacy_listener("frontend", root)
    run_systemctl("start", *runtime_service_names(mode), TARGET_UNIT, project_root=root)
    wait_for_http(API_HEALTH_URL)
    wait_for_http(frontend_url)


def status_lines(project_root: Path | None = None, runtime_mode: str | None = None) -> list[str]:
    root = (project_root or get_project_root()).resolve()
    mode = resolve_runtime_mode(runtime_mode)
    ensure_user_units(root, runtime_mode=mode)
    descriptor = runtime_descriptor(project_root=root, runtime_mode=mode)
    if mode == CONTAINER_RUNTIME_MODE:
        return [
            f"Runtime: {'active' if descriptor['runtime_active'] else 'inactive'} ({CORE_RUNTIME_SERVICE})",
            f"API: {'ready' if descriptor['health']['api_ready'] else 'not ready'} ({API_HEALTH_URL})",
            f"Frontend: {'ready' if descriptor['health']['frontend_ready'] else 'not ready'} ({descriptor['frontend_url']})",
            f"Runtime log: {CORE_RUNTIME_LOG}",
        ]
    services_by_name = {item['name']: item['active'] for item in descriptor['services']}
    return [
        f"API: {'active' if services_by_name.get(API_SERVICE, False) else 'inactive'} ({API_SERVICE})",
        f"Frontend: {'active' if services_by_name.get(FRONTEND_SERVICE, False) else 'inactive'} ({FRONTEND_SERVICE})",
        f"API log: {API_LOG}",
        f"Frontend log: {FRONTEND_LOG}",
    ]
```

**Step 4: Re-run the targeted tests**

Run the same command from Step 2.

Expected: PASS.

**Step 5: Commit**

```bash
git add biomodstack_services.py platform/api/tests/test_biomodstack_services.py
git commit -m "refactor: make runtime urls explicit across service manager"
```

---

## Task 4: Add `status --json` to `scripts/manage_desktop_services.py`

**Objective:** Expose the new runtime descriptor through the existing CLI without changing default human-readable output.

**Files:**
- Modify: `scripts/manage_desktop_services.py`
- Create: `platform/api/tests/test_manage_desktop_services_cli.py`

**Step 1: Write failing tests**

Create `platform/api/tests/test_manage_desktop_services_cli.py` with this content:

```python
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_module():
    script_path = REPO_ROOT / "scripts" / "manage_desktop_services.py"
    spec = importlib.util.spec_from_file_location("manage_desktop_services", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_status_json_prints_runtime_descriptor(monkeypatch, capsys) -> None:
    module = load_module()
    payload = {
        "runtime_mode": "dev",
        "frontend_url": "http://127.0.0.1:5173/",
        "supported_launch_surfaces": ["browser", "electron", "none"],
    }
    monkeypatch.setattr(module, "runtime_descriptor", lambda runtime_mode=None: payload)
    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "status", "--runtime", "dev", "--json"])

    assert module.main() == 0
    assert json.loads(capsys.readouterr().out) == payload
```

**Step 2: Run the targeted test and verify it fails**

Run:

```bash
cd /home/dalab/biomodstack/biomodstack
/home/dalab/.local/bin/uv run --directory platform/api python -m pytest \
  tests/test_manage_desktop_services_cli.py::test_status_json_prints_runtime_descriptor -q
```

Expected: FAIL because `--json` is not recognized and `runtime_descriptor` is not imported into the script.

**Step 3: Write the minimal implementation**

Modify `scripts/manage_desktop_services.py`:

```python
import json

from biomodstack_services import (
    API_LOG,
    CORE_RUNTIME_LOG,
    FRONTEND_LOG,
    ServiceManagerError,
    resolve_runtime_mode,
    restart_all,
    restart_api,
    runtime_descriptor,
    start_all,
    status_lines,
    stop_all,
)
```

Add the flag:

```python
parser.add_argument("--json", action="store_true", dest="json_output", help="emit structured JSON for supported actions")
```

Handle `status --json` without changing plain-text default behavior:

```python
if args.action == "status":
    if args.json_output:
        print(json.dumps(runtime_descriptor(runtime_mode=runtime_mode), indent=2, sort_keys=True))
        return 0
    print("\n".join(status_lines(runtime_mode=runtime_mode)))
    if runtime_mode == "container":
        print(f"Core runtime log: {CORE_RUNTIME_LOG}")
    else:
        print(f"Frontend log: {FRONTEND_LOG}")
    return 0
```

Do not add `--json` behavior to `start|stop|restart|restart-api` in this tranche.

**Step 4: Re-run the targeted test**

Run the same command from Step 2.

Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/manage_desktop_services.py platform/api/tests/test_manage_desktop_services_cli.py
git commit -m "feat: add json status output for desktop service manager"
```

---

## Task 5: Create the opt-in launcher script with safe surface handling

**Objective:** Add a new launcher path for `browser|electron|none` without changing `start_ui.sh`, and make the Electron surface intentionally fail until the Electron package exists.

**Files:**
- Create: `scripts/launch_biomodstack_ui.py`
- Create: `platform/api/tests/test_launch_biomodstack_ui.py`

**Step 1: Write failing tests**

Create `platform/api/tests/test_launch_biomodstack_ui.py` with this content:

```python
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_module():
    script_path = REPO_ROOT / "scripts" / "launch_biomodstack_ui.py"
    spec = importlib.util.spec_from_file_location("launch_biomodstack_ui", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_explicit_surface_overrides_preferences() -> None:
    module = load_module()
    prefs = {"default_surface": "browser", "auto_open_hosted_web_on_start": False}

    assert module.resolve_surface_choice("electron", prefs) == "electron"
    assert module.resolve_surface_choice(None, prefs) == "browser"


def test_none_surface_does_not_open_browser(monkeypatch) -> None:
    module = load_module()
    opened: list[str] = []
    monkeypatch.setattr(module, "start_all", lambda project_root=None, runtime_mode=None: None)
    monkeypatch.setattr(
        module,
        "runtime_descriptor",
        lambda project_root=None, runtime_mode=None: {
            "browser_url": "http://127.0.0.1:5173/bms/",
            "launch_preferences": {"default_surface": "browser", "auto_open_hosted_web_on_start": True},
        },
    )
    monkeypatch.setattr(module.webbrowser, "open_new_tab", lambda url: opened.append(url))

    module.launch_ui(runtime_mode="container", surface="none")

    assert opened == []


def test_electron_surface_fails_clearly_until_shell_exists(monkeypatch) -> None:
    module = load_module()
    started: list[str] = []
    monkeypatch.setattr(module, "start_all", lambda project_root=None, runtime_mode=None: started.append(runtime_mode or "dev"))
    monkeypatch.setattr(
        module,
        "load_launch_preferences",
        lambda: {"default_surface": "browser", "auto_open_hosted_web_on_start": True},
    )

    try:
        module.launch_ui(runtime_mode="container", surface="electron")
    except module.ServiceManagerError as exc:
        assert "Electron launch surface requested" in str(exc)
    else:
        raise AssertionError("expected ServiceManagerError")

    assert started == []
```

**Step 2: Run the targeted tests and verify they fail**

Run:

```bash
cd /home/dalab/biomodstack/biomodstack
/home/dalab/.local/bin/uv run --directory platform/api python -m pytest \
  tests/test_launch_biomodstack_ui.py::test_explicit_surface_overrides_preferences \
  tests/test_launch_biomodstack_ui.py::test_none_surface_does_not_open_browser \
  tests/test_launch_biomodstack_ui.py::test_electron_surface_fails_clearly_until_shell_exists -q
```

Expected: FAIL because the launcher script does not exist yet.

**Step 3: Write the minimal implementation**

Create `scripts/launch_biomodstack_ui.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from biomodstack_services import (
    BROWSER_LAUNCH_SURFACE,
    ELECTRON_LAUNCH_SURFACE,
    NONE_LAUNCH_SURFACE,
    SUPPORTED_LAUNCH_SURFACES,
    ServiceManagerError,
    load_launch_preferences,
    runtime_descriptor,
    start_all,
)


def resolve_surface_choice(explicit_surface: str | None, launch_preferences: dict[str, object]) -> str:
    if explicit_surface:
        return explicit_surface
    surface = str(launch_preferences.get("default_surface") or BROWSER_LAUNCH_SURFACE).strip().lower()
    if surface not in SUPPORTED_LAUNCH_SURFACES:
        return BROWSER_LAUNCH_SURFACE
    return surface


def should_open_browser(surface: str, explicit_surface: str | None, launch_preferences: dict[str, object]) -> bool:
    if surface == NONE_LAUNCH_SURFACE:
        return False
    if surface == ELECTRON_LAUNCH_SURFACE:
        return False
    if explicit_surface == BROWSER_LAUNCH_SURFACE:
        return True
    return bool(launch_preferences.get("auto_open_hosted_web_on_start", True))


def launch_ui(runtime_mode: str = "dev", surface: str | None = None) -> dict[str, object]:
    prefs = load_launch_preferences()
    chosen_surface = resolve_surface_choice(surface, prefs)

    if chosen_surface == ELECTRON_LAUNCH_SURFACE:
        raise ServiceManagerError(
            "Electron launch surface requested, but no Electron shell is installed yet. Implement Phase 2 before enabling this path."
        )

    start_all(runtime_mode=runtime_mode)
    descriptor = runtime_descriptor(runtime_mode=runtime_mode)
    prefs = descriptor["launch_preferences"]
    if should_open_browser(chosen_surface, surface, prefs):
        webbrowser.open_new_tab(str(descriptor["browser_url"]))
    return descriptor


def main() -> int:
    parser = argparse.ArgumentParser(description="Start BioModStack and optionally raise a UI surface")
    parser.add_argument("--runtime", choices=["dev", "container"], default="dev")
    parser.add_argument("--surface", choices=list(SUPPORTED_LAUNCH_SURFACES))
    args = parser.parse_args()
    try:
        launch_ui(runtime_mode=args.runtime, surface=args.surface)
    except ServiceManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 4: Re-run the targeted tests**

Run the same command from Step 2.

Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/launch_biomodstack_ui.py platform/api/tests/test_launch_biomodstack_ui.py
git commit -m "feat: add opt-in BioModStack UI launcher"
```

---

## Task 6: Add regression guards proving `start_ui.sh` did not change meaning

**Objective:** Protect the current operator entrypoint from accidental launch-surface rewiring during this tranche.

**Files:**
- Create: `platform/api/tests/test_start_ui_entrypoint.py`
- Verify only: `start_ui.sh`

**Step 1: Write failing tests**

Create `platform/api/tests/test_start_ui_entrypoint.py`:

```python
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
START_UI = REPO_ROOT / "start_ui.sh"


def test_start_ui_sh_still_forwards_directly_to_manage_desktop_services(tmp_path: Path) -> None:
    fake_home = tmp_path / "fake-home"
    manager = fake_home / "scripts" / "manage_desktop_services.py"
    manager.parent.mkdir(parents=True, exist_ok=True)
    manager.write_text(
        "import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}))\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["BMS_HOME"] = str(fake_home)

    result = subprocess.run(
        ["bash", str(START_UI), "start", "--runtime", "container"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"argv": ["start", "--runtime", "container"]}


def test_start_ui_sh_usage_contract_remains_service_control_only() -> None:
    text = START_UI.read_text(encoding="utf-8")

    assert "launch_biomodstack_ui.py" not in text
    assert 'Usage: $0 {start|stop|status|restart|restart-api} [--runtime dev|container]' in text
    assert 'exec python3 "$MANAGER" "$ACTION" "$@"' in text
```

**Step 2: Run the targeted tests and verify they fail**

Run:

```bash
cd /home/dalab/biomodstack/biomodstack
/home/dalab/.local/bin/uv run --directory platform/api python -m pytest \
  tests/test_start_ui_entrypoint.py::test_start_ui_sh_still_forwards_directly_to_manage_desktop_services \
  tests/test_start_ui_entrypoint.py::test_start_ui_sh_usage_contract_remains_service_control_only -q
```

Expected: the forwarding test should PASS immediately today; keep it anyway because it becomes the regression guard for future edits. If the static test fails later, stop and do not proceed with a start-ui rewrite in this tranche.

**Step 3: Keep `start_ui.sh` unchanged in this tranche**

Do not modify `start_ui.sh` in this implementation slice.

That is the whole point of the regression guard.

**Step 4: Re-run the tests after all other tasks land**

Run the same command from Step 2 after Tasks 1-5 are complete.

Expected: PASS.

**Step 5: Commit**

```bash
git add platform/api/tests/test_start_ui_entrypoint.py
# start_ui.sh should NOT be staged in this tranche
git commit -m "test: guard start_ui service-control contract"
```

---

## Final verification for the tranche

After Tasks 1-6 are complete, run all targeted checks together.

**Backend/service-manager tests:**

```bash
cd /home/dalab/biomodstack/biomodstack
/home/dalab/.local/bin/uv run --directory platform/api python -m pytest \
  tests/test_biomodstack_services.py \
  tests/test_manage_desktop_services_cli.py \
  tests/test_launch_biomodstack_ui.py \
  tests/test_start_ui_entrypoint.py -q
```

**Shell syntax check:**

```bash
cd /home/dalab/biomodstack/biomodstack
bash -n start_ui.sh
bash -n scripts/run_biomodstack_core_runtime.sh
```

**Descriptor smoke check:**

```bash
cd /home/dalab/biomodstack/biomodstack
python3 scripts/manage_desktop_services.py status --runtime dev --json | python3 -m json.tool >/dev/null
python3 scripts/manage_desktop_services.py status --runtime container --json | python3 -m json.tool >/dev/null
```

**Opt-in launcher smoke checks:**

```bash
cd /home/dalab/biomodstack/biomodstack
python3 scripts/launch_biomodstack_ui.py --runtime dev --surface none
python3 scripts/launch_biomodstack_ui.py --runtime container --surface browser
python3 scripts/launch_biomodstack_ui.py --runtime container --surface electron
```

Expected:
- `--surface none` starts runtime and opens no browser window
- `--surface browser` starts runtime and opens the descriptor’s `browser_url`
- `--surface electron` exits non-zero with a clear “Electron launch surface requested...” message before any browser-open side effect
- `./start_ui.sh start --runtime container` still behaves as the existing service-control path and does not know anything about the new launcher yet

**Diff hygiene:**

```bash
cd /home/dalab/biomodstack/biomodstack
git diff --check
```

---

## Definition of done

This tranche is done only when all of the following are true:
- `biomodstack_services.py` exposes a runtime descriptor for both `dev` and `container`
- runtime-specific frontend URLs are explicit and reused by the service layer itself
- `scripts/manage_desktop_services.py status --json` emits valid JSON
- `scripts/launch_biomodstack_ui.py` exists and supports `browser|electron|none`
- `browser` and `none` behave correctly now
- `electron` fails clearly and intentionally until the desktop shell exists
- `start_ui.sh` remains unchanged and protected by tests
- no panel/tray/Electron package work leaked into this tranche

---

## Execution handoff

Plan complete and saved. Ready to execute using subagent-driven-development — start with Task 1, keep strict TDD, and stop immediately if any step pressures you to change `start_ui.sh` semantics in this tranche.