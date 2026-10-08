# BioModStack Control Plane, Electron Surface, and Install Paths Upgrade Plan

> **For Hermes:** Use this as the next operator-facing tranche for BioModStack. Preserve `start_ui.sh` semantics, keep the core runtime containerized, keep workflow execution host-native through the workflow adapter, and make runtime/path management flow through one shared control plane instead of drifting across GTK scripts, shell env files, and repo-local compose overrides.

**Goal:** Deliver a portable operator control plane that can manage the containerized core runtime, launch the optional Electron shell, and let users choose persistent storage/runtime paths during install or first run without pretending Nextflow/BioXP execution has already been containerized.

**Architecture:** The shared service layer remains the source of truth. `systemd --user` plus `compose.core-runtime.yml` keep owning runtime lifecycle on Linux, while browser, Electron, GTK panel, and tray stay clients of that service layer rather than supervisors. A new persisted install profile becomes the single operator-facing source of truth for data/storage/runtime paths, with compatibility exports for existing shell scripts and compose env wiring.

**Tech Stack:** `biomodstack_services.py`, `scripts/manage_desktop_services.py`, `scripts/launch_biomodstack_ui.py`, `start_ui.sh`, `start_ui_electron.sh`, `scripts/run_biomodstack_core_runtime.sh`, `compose.core-runtime.yml`, `platform/api/routers/system.py`, `platform/api/paths.py`, `platform/frontend/src/components/InfraMonitorPage.tsx`, `platform/frontend/src/components/InfraLiveTelemetry.tsx`, `platform/frontend/src/lib/api.ts`, `platform/desktop-electron/src/main.ts`, `platform/desktop-electron/src/serviceControl.ts`, `platform/desktop-electron/src/shellPaths.ts`, `biomodstack_panel.py`, `biomodstack_tray.py`, and the existing workflow-adapter boundary on `127.0.0.1:8001`.

---

## 1. Repo-grounded current state

This plan is anchored to the code that already exists in the repo today.

### 1.1 Core runtime/containerization state

The current core-runtime/containerization tranche is already real enough to build on:

- `scripts/run_biomodstack_core_runtime.sh` already wraps compose actions:
  - `up`
  - `down`
  - `restart`
  - `logs`
  - `ps`
  - `config`
  - `pull`
- `compose.core-runtime.yml` already launches:
  - `bms-api`
  - `bms-web`
- container mode already has a live host-native workflow adapter seam:
  - API health on `http://127.0.0.1:8000/api/health`
  - workflow-adapter health on `http://127.0.0.1:8001/api/workflow-adapter/health`
- `biomodstack_services.py` already distinguishes:
  - `dev`
  - `container`
- container-mode readiness already has the longer startup budget it needs:
  - `DEFAULT_HTTP_WAIT_TIMEOUT_SECONDS = 30.0`
  - `CONTAINER_HTTP_WAIT_TIMEOUT_SECONDS = 180.0`

### 1.2 Launch-surface/control-plane state

The current launcher/control-plane seam is strong enough to extend instead of replacing:

- `start_ui.sh` remains the established operator service-control entrypoint and must stay that way.
- `start_ui_electron.sh` already exists as the additive Electron launcher wrapper.
- `scripts/launch_biomodstack_ui.py` already supports:
  - `--surface browser`
  - `--surface electron`
  - `--surface none`
- `biomodstack_services.py` already persists launch preferences in:
  - `~/.config/biomodstack/launch_preferences.json`
- explicit unsupported Electron requests already fail clearly before side effects if the shell is not installed.
- stale persisted Electron preferences already fall back to browser, which is the correct non-bricking behavior.

### 1.3 Electron-shell state

The Electron shell is not hypothetical anymore; it already has the right ownership shape:

- `platform/desktop-electron/src/serviceControl.ts` already shells into `scripts/manage_desktop_services.py` instead of trying to own backend processes itself.
- `platform/desktop-electron/src/main.ts` already exposes IPC for:
  - status
  - start
  - stop
  - restart
  - restart-api
  - open hosted web in browser
- `platform/desktop-electron/src/shellPaths.ts` already resolves project root, data root, logs, and icon paths.
- the Electron shell already uses a dedicated persistent storage partition:
  - `persist:biomodstack-shell`

### 1.4 Path/config state

The current path story works, but it is split across too many sources:

- `platform/api/paths.py` uses env vars first, then heuristics.
- `start_ui.sh` sources `~/.biomodstack/env.sh`.
- `scripts/run_biomodstack_core_runtime.sh` uses `.env.core-runtime.local` by default.
- `compose.core-runtime.yml` expects env values like:
  - `BMS_STATE_DIR`
  - `BMS_CONTAINER_STATE_PATH`
  - `BMS_API_HOST_PORT`
  - `BMS_WEB_HOST_PORT`
  - `BMS_WORKFLOW_ADAPTER_URL`
- `platform/desktop-electron/src/shellPaths.ts` duplicates some path heuristics on the JS side.

That split is acceptable for a developer repo, but it is not a clean install-time or first-run experience for operators.

### 1.5 Current operator UIs

There are already multiple operator surfaces, but they are inconsistent:

- `platform/frontend/src/components/InfraMonitorPage.tsx` exists, but today it is telemetry-first rather than full runtime control.
- `biomodstack_panel.py` and `biomodstack_tray.py` exist, but they are Linux-only helpers and still contain direct status heuristics that predate the stronger shared service layer.
- `Layout.tsx` currently hides the `/infra` page behind a localStorage debug toggle, which is too weak for a real operator control plane.

---

## 2. Upgrade decision

### 2.1 Yes: the cross-platform direction remains “containerized core + shared control plane”

This tranche should continue the already-correct direction:

- containerize the core web/control-plane runtime
- keep workflows host-native behind the workflow adapter
- keep the frontend/browser/Electron contract stable
- make the operator surface portable and honest

The right statement is:

- the core runtime/control plane should be containerized for portability
- the overall workflow execution plane is not yet fully containerized end-to-end
- this tranche should improve control, launch, and install UX around that honest boundary

### 2.2 No: do not turn this into “containerize everything now”

This plan explicitly does not attempt to:

- move Nextflow execution into the API container
- move Apptainer/Singularity ownership into the core runtime container
- move BioXP daemon ownership into the container stack
- make Electron own backend lifetime
- replace the existing service layer with frontend/Electron direct subprocess logic

### 2.3 Primary control-plane target

The long-term operator control plane should be:

- web-first, because it is already the product UI and is the most portable shared surface
- Electron-capable, because a packaged shell is a valid optional operator experience
- service-layer-backed, because the runtime owner must remain outside the shell

GTK panel and tray should remain additive convenience clients on Linux, not the architectural center of gravity.

---

## 3. Non-regression rules

These are hard constraints for the tranche.

1. `start_ui.sh` remains service-control only.
2. `start_ui_electron.sh` remains the additive opt-in Electron wrapper.
3. Explicit `--surface electron` must still fail clearly before side effects if the Electron runtime is absent.
4. Persisted browser/Electron preferences must still degrade safely to browser when Electron is unavailable.
5. Container-mode workflow ownership must remain honest:
   - workflow execution stays host-native through the adapter
   - container mode must not regress into local PID/`ps` assumptions as source of truth
6. Runtime-management UIs must call the shared service layer rather than shelling out to `docker compose` or `systemctl` directly from multiple places.
7. Operator-specific path choices must stop depending on repo-local mutable files alone.
8. Remote/hosted views must not silently gain dangerous runtime mutation powers unless that scope is explicitly enabled.

---

## 4. Target operator experience

## 4.1 First install / first run

On a fresh workstation or packaged install, the operator should get a single path-assignment/setup flow before the first serious runtime start.

The setup flow should let the operator confirm or change:

- project/repo root
- primary data root
- database path
- inputs path
- weights root
- Apptainer/container directory
- MSA cache path
- ColabFold DB path
- SABDAB cache path
- container-mode host state path
- optional host ports for API and web
- default runtime mode
- default launch surface
- whether hosted web auto-opens on runtime start

The defaults should remain conservative and repo-grounded:

- prefer `/mnt/BioModStack` when appropriate
- otherwise prefer `~/.biomodstack`
- default runtime mode for packaged/operator installs: `container`
- default runtime mode for explicit dev-repo workflows: existing behavior may remain `dev`
- default surface: hosted web
- optional surface: Electron

### 4.2 Daily runtime management

From the main product UI, the operator should be able to:

- see which runtime mode is active
- see container/API/frontend/adapter health
- see which services are actually active
- start/stop/restart the runtime
- restart just the API
- open the hosted web UI in a browser
- launch the Electron shell if installed
- see where logs live
- see where data/log/config roots live
- update path assignments later without hand-editing shell files

### 4.3 Advanced container controls

When the runtime mode is `container`, the operator should additionally have access to:

- compose service/process summary (`ps`)
- generated compose config summary (`config`)
- image refresh/pull action (`pull`)
- container log access through the existing runtime log surfaces

These should be clearly marked as runtime-admin controls, not general product features.

---

## 5. Single source of truth for operator path choices

## 5.1 New persisted install profile

Create one machine-readable install profile in the user config area, for example:

- `~/.config/biomodstack/install_profile.json`

Recommended shape:

```json
{
  "version": 1,
  "project_root": "/path/to/biomodstack",
  "default_runtime_mode": "container",
  "paths": {
    "data_root": "/mnt/BioModStack",
    "db_path": "/mnt/BioModStack/biomodstack.db",
    "inputs_dir": "/mnt/BioModStack/inputs",
    "weights_root": "/mnt/BioModStack/weights",
    "container_dir": "/mnt/BioModStack/apptainer",
    "msa_cache_dir": "/mnt/BioModStack/msa_cache",
    "colabfold_db": "/mnt/BioModStack/colabfold_db",
    "sabdab_cache_dir": "/mnt/BioModStack/sabdab_cache",
    "container_state_dir": "/mnt/BioModStack"
  },
  "ports": {
    "api": 8000,
    "web": 5173,
    "workflow_adapter": 8001
  },
  "launch_preferences": {
    "default_surface": "browser",
    "auto_open_hosted_web_on_start": true
  }
}
```

### 5.2 Precedence rules

The precedence must be explicit and consistent across Python, shell, and Electron:

1. explicit environment variables
2. persisted install profile
3. current repo heuristics/defaults

This preserves power-user overrides while making first-run configuration deterministic.

### 5.3 Compatibility outputs

The install profile should generate compatibility artifacts instead of forcing every existing entrypoint to parse JSON itself.

Generate or refresh:

- `~/.biomodstack/env.sh`
  - for existing shell-script compatibility
- `~/.config/biomodstack/core-runtime.env`
  - for container-runtime/compose compatibility

Important design rule:

- keep `.env.core-runtime.example` in the repo as documentation/template
- stop treating repo-local `.env.core-runtime.local` as the only durable operator state file
- allow developer override/fallback to repo-local `.env.core-runtime.local` when explicitly needed

This is important for packaged installs and for any future read-only app bundle or installer scenario.

### 5.4 Shared Python helper module

Create a shared helper module at repo root, for example:

- `biomodstack_runtime_config.py`

That module should own:

- config-dir resolution
- install-profile load/save/normalize
- path validation
- compatibility export rendering
- `core-runtime.env` rendering
- launch-preference merge rules

This prevents `biomodstack_services.py`, `platform/api/paths.py`, and shell wrappers from drifting.

---

## 6. Control-plane API contract

## 6.1 Do not make the frontend call service scripts directly

The React UI should not invent its own process model. It should consume a narrow backend contract which itself uses the shared service layer.

### 6.2 Extend `/api/system` with runtime-admin surfaces

Extend `platform/api/routers/system.py` with a runtime-admin sub-contract.

Recommended read surfaces:

- `GET /api/system/runtime-state`
  - wraps `runtime_descriptor(...)`
  - includes service status, health, logs, launch preferences, Electron availability, and install-profile summary
- `GET /api/system/install-profile`
  - returns the normalized install profile plus validation warnings
- `POST /api/system/install-profile/validate`
  - validates requested paths before saving

Recommended mutation surfaces:

- `POST /api/system/runtime/start`
- `POST /api/system/runtime/stop`
- `POST /api/system/runtime/restart`
- `POST /api/system/runtime/restart-api`
- `POST /api/system/runtime/launch-surface`
  - browser/electron/none
- `POST /api/system/runtime/pull`
  - container mode only
- `PUT /api/system/install-profile`
  - save profile and regenerate compatibility env outputs

### 6.3 Local-admin safety rule

Runtime mutation endpoints should be local-admin only by default.

Recommended rule set:

- read-only status/config endpoints can remain broadly available to the local app surface
- mutation endpoints should require loopback origin or an explicit admin-enable flag
- if the UI is being viewed remotely, the page should degrade to read-only runtime status rather than pretending remote runtime mutation is always allowed

This keeps the hosted web UI useful without accidentally turning it into an unauthenticated remote workstation controller.

### 6.4 Runtime descriptor expansion

Expand `runtime_descriptor(...)` in `biomodstack_services.py` to include enough UI metadata to avoid frontend guesswork.

Add fields for:

- `electron_available`
- `electron_launcher_path`
- `install_profile_present`
- `install_profile_summary`
- `paths_summary`
- `core_runtime_env_file`
- `container_runtime_actions`
- `runtime_control_read_only`

The UI should not infer these by scraping logs or checking arbitrary files itself.

---

## 7. Frontend control-plane plan

## 7.1 Make `/infra` the first-class runtime admin page

Evolve the existing `/infra` page from telemetry-only into a combined:

- runtime control plane
- install/path configuration page
- system telemetry page

Do not create a second disconnected admin page unless there is a very strong reason.

### 7.2 Route/nav expectations

Promote `/infra` out of the debug-only localStorage gate.

Recommended nav behavior:

- show the page as a real first-class system/control-plane surface
- keep naming honest and operator-friendly, for example:
  - `System Control`
  - `Control Plane`
  - `System Analytics & Control`
- do not leave core runtime management hidden behind a debug checkbox

### 7.3 Recommended UI sections

The upgraded page should have these sections in order:

1. Runtime summary
   - active mode
   - API/frontend/adapter health
   - service states
   - current URLs and basename
2. Runtime actions
   - start
   - stop
   - restart
   - restart API
3. Launch surfaces
   - browser
   - electron
   - none
   - current default surface
   - auto-open hosted web toggle
4. Container tools
   - pull images
   - compose status/ps summary
   - config summary
   - log descriptors
5. Install paths / storage assignment
   - editable path cards with validation
   - reset-to-default suggestions
6. Live workstation telemetry
   - keep the current telemetry value already provided by `InfraLiveTelemetry`

### 7.4 Exact frontend files likely to change

Modify:

- `platform/frontend/src/components/InfraMonitorPage.tsx`
- `platform/frontend/src/components/InfraLiveTelemetry.tsx`
- `platform/frontend/src/components/Layout.tsx`
- `platform/frontend/src/lib/api.ts`
- `platform/frontend/src/App.tsx`

Create focused subcomponents rather than one giant page file, for example:

- `platform/frontend/src/components/infra/RuntimeControlPanel.tsx`
- `platform/frontend/src/components/infra/LaunchSurfacePanel.tsx`
- `platform/frontend/src/components/infra/InstallPathsPanel.tsx`
- `platform/frontend/src/components/infra/ContainerAdminPanel.tsx`

The important point is separation of responsibilities, not those exact filenames.

---

## 8. Electron-shell plan

## 8.1 Launch integration

The control-plane upgrade should not invent a new Electron launch path.

Reuse the existing launcher stack:

- `start_ui_electron.sh`
- `scripts/launch_biomodstack_ui.py`

The upgraded control plane should call the same launch seam the repo already trusts.

### 8.2 Use the install profile in Electron path resolution

Modify `platform/desktop-electron/src/shellPaths.ts` so it reads the persisted install profile before falling back to current heuristics.

This removes a major source of drift between:

- Python path resolution
- shell-script env exports
- Electron shell path assumptions

### 8.3 Keep Electron as a client, not the runtime owner

Do not add Electron-side logic that:

- launches raw uvicorn/Vite children
- shells out to `docker compose` directly
- bypasses `scripts/manage_desktop_services.py`

Electron should continue to use:

- `createServiceControl()`
- `runtime_descriptor(...)`
- the existing launcher/service layer

### 8.4 Extend Electron menus/tray only where it adds operator value

Good additive shell features for this tranche:

- open control-plane page directly
- show configured data/log roots
- open install-profile config directory
- show whether Electron is using the expected runtime mode and router basename
- launch the hosted web UI in the external browser

Avoid turning the shell into a second separate admin system.

### 8.5 Exact Electron files likely to change

Modify:

- `platform/desktop-electron/src/main.ts`
- `platform/desktop-electron/src/serviceControl.ts`
- `platform/desktop-electron/src/shellPaths.ts`
- `platform/desktop-electron/src/menu.ts`
- `platform/desktop-electron/src/tray.ts`
- `platform/desktop-electron/src/preload.ts` only if new IPC is truly needed

Update tests:

- `platform/desktop-electron/tests/serviceControl.test.ts`
- `platform/desktop-electron/tests/shellPaths.test.ts`

---

## 9. GTK panel and tray plan

## 9.1 Treat them as additive Linux clients, not the main architecture

The existing GTK panel and tray are still useful on Linux workstations, but they should be brought into alignment with the shared control plane rather than expanded as the primary future surface.

### 9.2 Minimum worthwhile upgrades

If time is available in the tranche, update:

- `biomodstack_panel.py`
- `biomodstack_tray.py`

So that they:

- use `scripts/manage_desktop_services.py status --json` instead of stale pgrep/process heuristics where practical
- expose an `Open Electron Shell` action that routes through `start_ui_electron.sh` or `scripts/launch_biomodstack_ui.py`
- understand `dev` versus `container` runtime mode explicitly
- expose path/config locations from the install profile

### 9.3 What not to do here

Do not spend this tranche rewriting large GTK UI flows that the portable web/Electron control plane will supersede.

---

## 10. Installer / first-run path-assignment plan

## 10.1 Provide both GUI and scriptable setup paths

A serious operator setup flow should exist in two forms:

1. first-run UI flow inside the web/Electron control plane
2. scriptable CLI fallback for headless or admin-driven installs

Recommended CLI helper:

- `scripts/configure_biomodstack_runtime.py`

Its job should be:

- prompt or accept flags for path choices
- validate directories and permissions
- write `install_profile.json`
- regenerate `~/.biomodstack/env.sh`
- regenerate `~/.config/biomodstack/core-runtime.env`

### 10.2 First-run UI behavior

When no install profile exists, the control plane should show a setup-required state instead of pretending the runtime is fully configured.

Recommended behavior:

- allow the user to review/edit defaults
- validate before save
- optionally create missing directories
- show the exact files that were written
- only then offer the first container-runtime start

### 10.3 Migration behavior for existing installs

Do not force existing developers/operators through a breaking setup wall.

Migration rule:

- if `~/.biomodstack/env.sh` already exists, seed the new install profile from it
- if repo-local `.env.core-runtime.local` exists, import compatible values into the generated operator env file where appropriate
- if no structured profile exists, heuristics may still render a read-only inferred summary until the operator saves a real profile

This preserves backwards compatibility while creating a clean upgrade path.

---

## 11. Exact implementation phases

## Phase 1: Shared runtime/install profile foundation

**Objective:** Introduce one normalized operator config source of truth and make both Python and Electron able to read it.

### Create

- `biomodstack_runtime_config.py`
- `platform/api/tests/test_runtime_install_profile.py`
- optionally `scripts/configure_biomodstack_runtime.py`

### Modify

- `platform/api/paths.py`
- `biomodstack_services.py`
- `scripts/run_biomodstack_core_runtime.sh`
- `scripts/run_biomodstack_api.sh` only if compatibility export handling needs cleanup
- `start_ui.sh` only if needed for profile bootstrap compatibility, not semantic change
- `platform/desktop-electron/src/shellPaths.ts`

### Acceptance gate

- env overrides still win
- no-profile developer setups still behave sensibly
- saved profile propagates to both Python and Electron path resolution
- generated `core-runtime.env` is outside the repo by default

## Phase 2: Runtime-admin backend contract

**Objective:** Expose runtime/path/admin state to the frontend through one local-admin API surface.

### Create

- `platform/api/tests/test_system_runtime_control.py`

### Modify

- `platform/api/routers/system.py`
- `platform/api/main.py` only if router wiring changes are needed
- `biomodstack_services.py`
- `scripts/manage_desktop_services.py` if new structured action output is needed

### Acceptance gate

- read-only runtime state is returned in structured JSON
- mutation routes call the shared service layer, not duplicated shell logic
- local-admin/read-only behavior is explicit and test-covered

## Phase 3: Web control-plane upgrade

**Objective:** Turn `/infra` into the actual operator control-plane page.

### Create

- `platform/frontend/src/components/infra/RuntimeControlPanel.tsx`
- `platform/frontend/src/components/infra/LaunchSurfacePanel.tsx`
- `platform/frontend/src/components/infra/InstallPathsPanel.tsx`
- `platform/frontend/src/components/infra/ContainerAdminPanel.tsx`

### Modify

- `platform/frontend/src/components/InfraMonitorPage.tsx`
- `platform/frontend/src/components/Layout.tsx`
- `platform/frontend/src/lib/api.ts`
- `platform/frontend/src/App.tsx`

### Acceptance gate

- operator can manage runtime without leaving the product UI
- `/infra` is no longer a hidden debug-only surface
- telemetry remains intact
- UI wording stays honest about host-native workflow ownership

## Phase 4: Electron + Linux helper alignment

**Objective:** Make Electron launch and helper surfaces consume the same control-plane contract.

### Modify

- `platform/desktop-electron/src/main.ts`
- `platform/desktop-electron/src/serviceControl.ts`
- `platform/desktop-electron/src/shellPaths.ts`
- `platform/desktop-electron/src/menu.ts`
- `platform/desktop-electron/src/tray.ts`
- `biomodstack_panel.py`
- `biomodstack_tray.py`

### Acceptance gate

- Electron launch still routes through the existing launcher seam
- Electron/panel/tray display the same runtime/path truth the backend reports
- no new direct process-supervision logic is added in UI clients

## Phase 5: Install/first-run UX and docs

**Objective:** Make the new path model usable by a real operator and documented well enough for repeated setup/cutover.

### Modify

- `docs/Workstation Set Up and Install Guide.md`
- `docs/Desktop_Runtime_and_Shell_Architecture.md`
- `docs/plans/2026-04-19-launch-surface-control-plane-tranche-a.md`
  - only if cross-linking is useful
- `README.md` if operator install instructions live there

### Acceptance gate

- new installs can choose paths without manual shell-file editing
- existing installs can migrate without losing state
- docs explain the difference between containerized core runtime and host-native workflow ownership

---

## 12. Validation and test plan

## 12.1 Backend tests

Run from the BioModStack API environment, not from an unrelated root venv pattern.

Recommended command shape:

```bash
uv run --directory platform/api python -m pytest \
  tests/test_manage_desktop_services_cli.py \
  tests/test_launch_biomodstack_ui.py \
  tests/test_start_ui_entrypoint.py \
  tests/test_paths.py \
  tests/test_runtime_install_profile.py \
  tests/test_system_runtime_control.py \
  -q
```

## 12.2 Electron tests

```bash
pnpm --dir platform/desktop-electron test
```

At minimum this tranche should expand coverage for:

- shared install-profile path resolution
- Electron launcher/environment behavior
- any new IPC surface, if one is added

## 12.3 Frontend verification

At minimum:

```bash
pnpm --dir platform/frontend build
```

If focused node tests are added for new control-plane components, run those too using the repo’s existing frontend test pattern instead of inventing a second frontend test runner.

## 12.4 Shell/script sanity

```bash
bash -n start_ui.sh
bash -n start_ui_electron.sh
bash -n scripts/run_biomodstack_core_runtime.sh
```

## 12.5 Required live smoke gates

The tranche is not done until these live checks pass:

1. Install-profile save writes the expected config/env artifacts.
2. `python3 scripts/manage_desktop_services.py status --runtime container --json` returns valid JSON.
3. Container runtime starts successfully with the generated operator env file.
4. `GET http://127.0.0.1:8001/api/workflow-adapter/health` returns healthy before API readiness is declared complete.
5. `GET http://127.0.0.1:8000/api/health` returns healthy.
6. `GET http://127.0.0.1:5173/bms/` loads successfully.
7. The `/infra` page shows runtime controls plus telemetry.
8. Electron can be launched through the approved launcher seam when installed.
9. `start_ui.sh` still behaves like a service-control entrypoint and does not silently become an Electron launcher.
10. Workflow execution messaging remains honest:
    - core runtime is containerized
    - workflow execution remains host-native via the adapter

---

## 13. Cutover/defaults recommendation

For operator-facing installs after this tranche:

- default runtime mode should be `container`
- default launch surface should be hosted web
- Electron should remain an explicit optional shell
- first-run setup should strongly encourage choosing a non-repo data root

For developer checkout workflows:

- preserve the ability to run `dev` mode intentionally
- do not force every developer into a setup wizard before basic repo work
- allow inferred defaults until they explicitly save an install profile

---

## 14. Definition of done

This upgrade plan is complete only when all of the following are true:

1. There is one persisted operator install profile outside the repo.
2. `platform/api/paths.py`, shell scripts, and Electron all agree on path resolution precedence.
3. The main product UI exposes real runtime/container/path controls on `/infra`.
4. Electron launch is available from the approved control plane without bypassing the existing launcher seam.
5. GTK panel/tray no longer depend primarily on stale direct process heuristics if they are touched in this tranche.
6. `start_ui.sh` remains semantically stable.
7. Container-mode control remains honest about host-native workflow ownership.
8. Live smoke proves the containerized core runtime can be managed and relaunched as the operational default.

---

## 15. Recommended execution order

If this plan is approved, execute in this exact order:

1. shared install-profile/config foundation
2. backend runtime-admin API and descriptor expansion
3. `/infra` control-plane upgrade
4. Electron alignment
5. Linux panel/tray alignment if still worth the time
6. docs + live smoke + defaulting packaged installs toward container mode

That ordering keeps the contracts stable before UI work and avoids redoing path logic in three languages twice.
