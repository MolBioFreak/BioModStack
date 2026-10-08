# Desktop Runtime and Shell Architecture

## What was broken

The original BioModStack desktop launcher model let a GTK panel or shell script start
long-lived API/frontend children directly with `nohup` / `subprocess.Popen(...)`.
That looked detached, but the processes still inherited the caller's GNOME app scope.
On the live workstation this meant:

- panel PID, uvicorn, and Vite all sat inside the same
  `app-gnome-biomodstack-*.scope`
- that scope used `KillMode=control-group`
- if the panel died, logged out, restarted strangely, or the scope was collected,
  the backend died too

That is a supervision/ownership bug, not a web-stack problem.

## Fix direction

Keep the familiar entrypoints (`./start_ui.sh`, panel buttons, tray menu), but make
those control surfaces talk to dedicated `systemd --user` services:

- `biomodstack-api.service`
- `biomodstack-frontend.service`
- `biomodstack.target`

Control surfaces should:

- `systemctl --user start biomodstack.target`
- `systemctl --user stop biomodstack.target`
- `systemctl --user restart biomodstack-api.service biomodstack-frontend.service`
- inspect logs / health
- open the browser or embedded shell

They should not own backend process lifetime.

## Runtime contract

### Service layer

Dev runtime services
- `biomodstack-api.service`
- `biomodstack-frontend.service`
- continue to own the explicit development process shape
- write to `~/.local/state/biomodstack/logs/api.log` and `frontend.log`

Containerized core runtime
- `biomodstack-core-runtime.service`
- runs `scripts/run_biomodstack_core_runtime.sh`
- uses `compose.core-runtime.yml` to launch `bms-api` and `bms-web`
- writes to `~/.local/state/biomodstack/logs/core-runtime.log`
- preserves the same browser/API health contract on ports 5173 and 8000

Target unit
- `biomodstack.target`
- groups either the dev pair or the container runtime for start/stop convenience
- may be enabled later for login-time auto-start if desired

### Concrete repo surfaces

The service-layer/control split now lives in these files:

- `biomodstack_services.py`
  - renders/install user units
  - runs `systemctl --user`
  - owns health checks, status text, and legacy listener cleanup
- `scripts/manage_desktop_services.py`
  - single control-plane entry for `start`, `stop`, `restart`, `restart-api`, `status`
  - supports `--runtime dev|container`
- `scripts/run_biomodstack_api.sh`
  - API runtime wrapper used by `biomodstack-api.service`
  - sources `~/.biomodstack/env.sh` before snapshotting launch env
  - makes `BMS_API_MODE=dev|prod` explicit instead of assuming reload semantics forever
- `scripts/run_biomodstack_frontend.sh`
  - frontend runtime wrapper used by `biomodstack-frontend.service`
  - keeps dev-mode ownership explicit and points production runtime to the container stack
- `scripts/run_biomodstack_core_runtime.sh`
  - compose wrapper for `compose.core-runtime.yml`
  - used by `biomodstack-core-runtime.service`
- `docker/api.Dockerfile`, `docker/web.Dockerfile`, `docker/web/nginx.conf`
  - first-wave container scaffold for `bms-api` and `bms-web`
- `start_ui.sh`, `start_ui_electron.sh`, `restart_api.sh`, `stop_services.sh`
  - stable shell entrypoints preserved for operators and desktop launchers
  - `start_ui.sh` remains service control only
  - `start_ui_electron.sh` is the additive opt-in Electron shell launcher
- `biomodstack_panel.py`, `biomodstack_tray.py`
  - GUI control surfaces that should remain clients of the service layer

### Safety rules

- GUI may request service changes, but never spawn the long-lived child directly
- port cleanup may only kill listeners that positively identify as BioModStack
- if a foreign process owns port 8000 or 5173, fail loudly instead of killing it
- logs and health checks remain stable regardless of which shell is used
- stale GUI-scope API listeners may appear as worker children rather than the top-level
  shell/uv process, so cleanup must walk ancestor PIDs before deciding whether a port
  owner is foreign

## Shells are clients, not supervisors

Once the service layer is correct, multiple shells become possible at the same time:

- browser tab
- GTK panel
- tray app
- Tauri desktop app
- Electron desktop app
- CLI or remote admin surface

That is the big architectural win: the shell becomes optional.

## Recommended desktop-shell priority

### 1. Browser + thin native controller (immediate default)

Best for now if the goal is reliability with minimal work.

Use for:
- service control
- opening the web UI
- notifications
- tray/menu integration
- log/status access

Pros
- least moving parts
- no extra Chromium bundle
- preserves current UI exactly
- keeps all feature work in the web frontend

Cons
- does not feel like a self-contained app
- weaker native desktop affordances than a packaged shell

### 2. Tauri (recommended native shell if you want a "real app")

Tauri is the best general next step if the goal is a desktop wrapper around an already
web-based BioModStack UI without paying the full Electron tax.

Why Tauri is strong here
- much lighter RAM footprint than Electron
- uses the system webview on Linux instead of bundling Chromium
- good fit for a control shell that opens the existing web UI and talks to the API
- easy place to add tray integration, native notifications, autostart, log viewer,
  file pickers, and service-control commands
- better appliance feel without making the desktop shell the owner of the runtime

Where Tauri is especially useful for BioModStack
- workstation control app for launch/stop/status
- results browser / recent jobs shell
- native notifications for job completion / queue failures
- saved connection profiles (local workstation, remote workstation, robot-adjacent
  node, future cluster frontends)
- local file/drop integration for design imports or result export workflows

Tauri downsides
- adds Rust build/package complexity
- Linux webview differences must be tested carefully with heavy React/plot/3D views
- if you rely on Chromium-specific behavior for visualization widgets, Electron may
  still be simpler

### 3. Electron (good when you need maximum web-app compatibility)

Electron is still a valid choice, just not my first recommendation for this stack.

Choose Electron if you specifically want:
- the most predictable Chromium behavior for complex web viewers
- a large Node desktop ecosystem
- easier reuse of existing web tooling and dev habits
- multi-window desktop behavior with fewer platform-specific surprises
- deep JS-first desktop development with minimal Rust involvement

Where Electron can help BioModStack generally
- wrap the existing React UI with almost no frontend rewrite
- package a single-app desktop distribution for workstation users
- provide native menus, tray, notifications, auto-updates, and log panes
- create focused operator surfaces like:
  - BioXP cockpit app
  - job monitor app
  - results/review app
  - workstation infra console

Electron downsides
- highest RAM and disk overhead
- duplicates a Chromium runtime per packaged app
- does not solve supervision by itself; if it launches the backend directly, it can
  recreate the same ownership bug in a shinier form

## Recommendation: Tauri first, Electron only if webview compatibility forces it

If the question is "what is better than Electron here?", the answer is:

- Tauri is better if the desktop shell is mostly a native wrapper/control surface
  around the existing local web app.
- Electron is better only if the frontend needs Chromium-level consistency or the
  team wants the easiest all-JS desktop path.

For BioModStack specifically, I would choose:

1. service decoupling first
2. browser + GTK/tray controller as the near-term steady state
3. Tauri as the first serious native-shell candidate
4. Electron only if visualization compatibility or packaging UX clearly outweighs
   the resource cost

## How a native shell should be useful generally

A desktop shell should add native workstation value that the browser alone is bad at.
It should not duplicate every feature of the main UI blindly.

Good shell responsibilities:
- runtime status
  - API up/down
  - frontend up/down
  - queue depth
  - active jobs
  - GPU / disk / thermal summaries
- native notifications
  - job finished
  - job failed
  - queue stalled
  - robot/manual intervention required
- service management
  - start/stop/restart
  - log tail
  - diagnostics bundle export
- local file workflow
  - open results folders
  - drag/drop imports
  - reveal artifacts in file manager
- connection/profile management
  - local workstation
  - remote workstation over Tailscale
  - future cluster control endpoints
- focused operator modes
  - infra console
  - BioXP operator panel
  - review/triage dashboard

Things the shell should usually avoid owning:
- job orchestration semantics
- queue/business logic
- model configuration truth
- artifact/database truth
- long-running pipeline execution lifecycle

Those belong in the API/runtime layer.

## Future frontend cleanup

The current frontend service still runs Vite dev mode. That is acceptable for now as a
service-lifetime fix, but the long-term production posture should be one of:

1. static production build served by the API or a tiny web server, or
2. a packaged shell that points at the API plus a production-built local frontend

That would reduce dev-server fragility and make either Tauri or Electron packaging
cleaner.

## Suggested rollout

### Phase 1 — landed now
- decouple API/frontend from GUI scope
- standardize on `systemd --user` ownership
- keep shell entrypoints stable

### Phase 2 — productionize frontend
- build frontend assets in production mode
- decide whether API serves them or a tiny dedicated static server does
- keep `/bms/` path semantics stable

### Phase 3 — native shell
- prototype Tauri first
- expose service controls, logs, notifications, and profile switching
- load the existing web UI rather than rewriting it

### Phase 4 — specialized operator shells
- optional dedicated shells for robotics, infra, and review workflows
- all of them remain clients of the same API/runtime

## Related implementation plan

For a concrete Electron-first execution plan that preserves direct browser access,
see:
- `docs/plans/2026-04-19-electron-shell-port-with-web-access.md`
