# Desktop Runtime and Shell Architecture

This document describes the current BioModStack runtime/service model and the
shells that sit on top of it.

## Core idea

BioModStack shells are clients, not supervisors.

The browser, Electron shell, GTK panel, tray, and optional Android thin-shell
path all sit on top of one shared runtime/service layer. They should open the
hosted `/bms/` UI, inspect status, and request start/stop/restart actions
through the shared control plane. They should not own long-lived API/frontend
processes directly.

## Current runtime ownership

### Dev runtime

Dev mode is still supported for explicit repository-first work:

- `biomodstack-api.service`
- `biomodstack-frontend.service`

Those units own the dev API/frontend process shape and write to:

- `~/.local/state/biomodstack/logs/api.log`
- `~/.local/state/biomodstack/logs/frontend.log`

### Containerized core runtime

The default runtime mode is now `container`.

Container mode uses:

- `biomodstack-core-runtime.service`
- `compose.core-runtime.yml`
- `scripts/run_biomodstack_core_runtime.sh`

That service owns the hosted API/web pair and writes to:

- `~/.local/state/biomodstack/logs/core-runtime.log`

### Host-native workflow adapter

Container mode does not make Nextflow execution container-owned. The honest
runtime boundary is:

- API/web runtime is containerized
- workflow launch/cancel/running-job ownership remains host-native
- the bridge is `biomodstack-workflow-adapter.service`
- the API reaches it through `BMS_WORKFLOW_ADAPTER_URL`
- the local health endpoint is
  `http://127.0.0.1:8001/api/workflow-adapter/health`

This is the current production stance for BioModStack container mode.

### Container runtime robustness

The container runtime is robust enough to be the default control-plane/runtime
shape, but it is intentionally bounded. The stable pieces are:

- `bms-api`, `bms-web`, and the CPU-power helper are managed through
  `compose.core-runtime.yml`
- API health checks hit `/api/health`; web health checks hit `/bms/`
- startup readiness uses a container-mode wait budget rather than a short dev
  process timeout, which avoids false failures on first image builds/recreates
- the service manager recognizes the active Compose-backed containers by labels
  before cleaning up legacy dev listeners on the same ports
- `BMS_CORE_RUNTIME_MODE=1` guards the API/web container from directly owning
  workflow launches; workflow launch/cancel/running-job calls are either
  forwarded to `BMS_WORKFLOW_ADAPTER_URL` or rejected clearly
- mounted state/cache roots are injected for database, inputs, weights,
  ColabFold DB, MSA cache, and SAbDab cache paths so the API does not silently
  create home-directory fallback stores inside a recreated container

The bounded pieces are just as important:

- this is not full end-to-end workflow containerization; Nextflow/Apptainer
  execution remains host-native through the workflow adapter
- GPU and workstation hardware telemetry can degrade if the container cannot see
  the host NVIDIA/tooling stack; host-native adapter/proxy surfaces remain the
  safer ownership boundary for workstation hardware
- BioXP is a local hardware integration, not a generic core-runtime dependency;
  the dashboard should degrade when linkage is absent rather than fail startup

A general Linux host should be able to start the core dashboard/API/web stack and
show explicit degraded capability messages. It should not be expected to run the
full scientific workflow catalog until the host-side assets and adapters are
installed.

## Default runtime and launch rules

Runtime selection resolves in this order:

1. explicit `--runtime ...`
2. `BMS_RUNTIME_MODE`
3. `container`

That behavior is implemented in `biomodstack_services.py` and surfaced by
`scripts/manage_desktop_services.py`.

Launch-surface selection is separate from runtime selection:

- default surface: browser
- optional additive surface: Electron
- explicit `--surface none` is allowed for service-only startup

Launch preferences live at:

- `~/.config/biomodstack/launch_preferences.json`

## Concrete repo surfaces

The current service/runtime/shell split lives in these files:

- `biomodstack_services.py`
  - runtime-mode resolution
  - systemd unit rendering/install
  - service lifecycle and readiness checks
  - browser/Electron launch preference handling
- `biomodstack_runtime_profile.py`
  - install-profile persistence
  - generated compatibility env exports
  - generated container-runtime env file
  - runtime-path resolution and precedence rules
- `scripts/manage_desktop_services.py`
  - shared desktop/runtime control-plane CLI
- `scripts/launch_biomodstack_ui.py`
  - raises browser or Electron after ensuring services are running
- `start_ui.sh`
  - stable service-control entrypoint
- `start_ui_electron.sh`
  - additive Electron wrapper around `launch_biomodstack_ui.py`
- `scripts/run_biomodstack_core_runtime.sh`
  - compose wrapper for the containerized API/web runtime
- `platform/api/routers/system.py`
  - runtime-state and install-profile API routes
- `platform/api/routers/workflow_adapter.py`
  - host-native workflow launch/cancel/running-jobs surface
- `platform/api/routers/mobile_ui_updates.py`
  - manifest/bundle/file endpoints for optional mobile shell updates
- `platform/desktop-electron/src/main.ts`
  - Electron shell bootstrap, tray/menu wiring, and IPC registration
- `platform/desktop-electron/src/serviceControl.ts`
  - Electron bridge to `scripts/manage_desktop_services.py`
- `platform/desktop-electron/src/shellPaths.ts`
  - Electron-side path resolution using env, install profile, and heuristics
- `platform/frontend/src/runtime/navigation.ts`
  - router basename handling for hosted/browser/Electron shells
- `platform/frontend/src/runtime/cordovaShell.ts`
  - optional Cordova shell readiness hook
- `biomodstack_panel.py`, `biomodstack_tray.py`
  - GTK control surfaces that remain clients of the shared service layer

## Path and profile contract

Runtime/data-path state is now centered around the install profile and generated
env files.

Primary files:

- `~/.config/biomodstack/install_profile.json`
- `~/.config/biomodstack/core-runtime.env`
- `~/.biomodstack/env.sh`
- `~/.config/biomodstack/launch_preferences.json`

Path precedence is:

1. explicit environment variables
2. install profile
3. repo/workstation heuristics

Important install-profile outputs include:

- `data_root`
- `inputs_dir`
- `db_path`
- `container_dir`
- `weights_root`
- `colabfold_db`
- `msa_cache_dir`
- `sabdab_cache_dir`
- `workflow_adapter_url`
- API/web host ports
- container-state paths
- `core_runtime_mode`

The API system routes expose this through local-only endpoints such as:

- `GET /api/system/runtime-state`
- `GET /api/system/install-profile`
- `PUT /api/system/install-profile`

These routes are intentionally limited to localhost/testclient callers.

## Browser and GTK surfaces

The browser remains the default operator surface.

GTK panel/tray remain useful local helpers for:

- start/stop/restart
- status inspection
- log access
- opening the hosted UI

They should never become the owner of backend lifetime.

## Electron shell

The Electron shell is now a real optional launch surface rather than a future
placeholder.

Current Electron behavior includes:

- launching the same hosted `/bms/` UI as the browser path
- persistent shell storage partition via `persist:biomodstack-shell`
- preload-provided shell context including router basename/runtime mode
- tray and application menu integration
- open-in-browser support
- runtime status/start/stop/restart/restart-api through the shared Python
  service-control layer
- open results folder, logs, and shell-data folder helpers
- zoom controls and always-on-top toggle
- safe fallback to browser when Electron is only a persisted preference and the
  shell runtime is unavailable

Important guardrail:

- `start_ui_electron.sh` and the Electron app do not own API/frontend process
  lifetime directly; they call the shared service layer

## Optional Android thin-shell and update channel

BioModStack does not move the runtime onto the phone.

The current Android/mobile contract is additive:

- the hosted `/bms/` UI remains the product truth
- the frontend exposes `signalCordovaAppReady()` so a Cordova-style shell can
  confirm readiness exactly once through `__BMS_CORDOVA_CONFIRM_READY__`
- the API exposes mobile update endpoints under `/api/mobile-ui/*`
- update assets default to `${BMS_DATA}/mobile-ui-updates` unless
  `BMS_MOBILE_UI_UPDATES_DIR` overrides the location

The update surface currently serves:

- channel manifests
- versioned ZIP bundles
- versioned extracted asset files

That gives BioModStack an update/feed contract for an optional APK shell without
pretending the repo's primary runtime now lives on Android.

## Safety and non-regression rules

- shells may request service actions, but must not become backend supervisors
- container mode must stay honest about workflow ownership through the adapter
- browser/Electron/mobile shells should all point at the same hosted UI contract
- explicit Electron launch requests should fail clearly when the shell runtime is absent
- persisted Electron preferences should degrade safely to browser when needed
- local admin/runtime mutation routes stay localhost-only unless the scope is
  intentionally widened later

## Operator commands

Default startup:

```bash
./start_ui.sh start
./start_ui.sh status
```

Explicit container mode:

```bash
./start_ui.sh start --runtime container
./start_ui.sh restart-api --runtime container
./start_ui.sh stop --runtime container
```

Browser or Electron shell launch:

```bash
python3 scripts/launch_biomodstack_ui.py --surface browser --runtime container
./start_ui_electron.sh --runtime container
```

Direct compose wrapper:

```bash
./scripts/run_biomodstack_core_runtime.sh up
./scripts/run_biomodstack_core_runtime.sh ps
./scripts/run_biomodstack_core_runtime.sh down
```

## Bottom line

The current BioModStack architecture is:

- service-layer-owned runtime
- containerized core API/web surface by default
- host-native workflow execution through the workflow adapter
- browser as the default shell
- Electron as an additive packaged desktop shell
- optional Android thin-shell/update support around the same hosted UI

That separation is what keeps the runtime honest while still allowing multiple
operator surfaces to coexist.
