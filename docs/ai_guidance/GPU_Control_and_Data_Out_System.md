GPU Control and Data-Out System
===============================

Purpose
-------
This document is the operational and engineering source of truth for GPU telemetry
(data out) and GPU control (power/fan writes) in BioModStack.

Use this before modifying any of the following:
- GPU monitoring payloads shown in the UI
- Power limits and persisted power state
- Fan control logic (`nvidia-settings` or `coolercontrol` backends)
- Scheduler GPU policies and overrides

Primary Files
-------------
- API router: `platform/api/routers/gpu.py`
- GPU metadata: `platform/api/services/gpu_metadata.py`
- Scheduler config I/O: `platform/api/services/gpu_config.py`
- Orchestrator logic: `platform/api/services/gpu_orchestrator.py`
- API startup/orchestrator lifecycle: `platform/api/main.py`
- Hardware controls UI: `platform/frontend/src/components/Layout.tsx`
- System GPU display/scheduler UI: `platform/frontend/src/components/dashboard/SystemResources.tsx`
- Frontend API types: `platform/frontend/src/lib/api.ts`
- Service launcher scripts: `start_ui.sh`, `restart_api.sh`
- CoolerControl setup helper: `scripts/fix_coolercontrol_backend.sh`

Architecture Overview
---------------------
There are two separate but related planes:

1. Data-Out Plane (read/observe)
- GPU/CPU/RAM metrics from API endpoints consumed by frontend polling.
- GPU metrics come from NVML and `nvidia-smi`-adjacent state in `get_gpu_stats()`.
- Scheduler metadata (reservations, overrides, pins, locks) affects what UI displays.

2. Control-In Plane (write/apply)
- Power control writes via `nvidia-smi -pl`.
- Fan control writes via selected backend:
  - `nvidia-settings` backend: direct fan target objects.
  - `coolercontrol` backend: per-device per-channel REST writes to CoolerControl daemon.

Data-Out Endpoints
------------------
Core status endpoints in `platform/api/routers/gpu.py`:
- `GET /api/gpu/status`: full system payload (gpus + cpu + ram + history)
- `GET /api/gpu/gpus`: GPU-only payload
- `GET /api/gpu/cpu`: CPU-only payload
- `GET /api/gpu/ram`: RAM-only payload

Power/fan/scheduler data-out endpoints:
- `GET /api/gpu/power-control`
- `GET /api/gpu/fan-control`
- `GET /api/gpu/scheduler-config`
- `GET /api/gpu/workflow-pins`
- `GET /api/gpu/gpu-locks`
- `GET /api/gpu/concurrency-limits`

Important Data-Out Inputs
-------------------------
- Hardware limits and GPU capabilities: `HARDWARE_LIMITS` / `GPU_CAPABILITIES`
  from `platform/api/services/gpu_metadata.py`.
- Scheduler config file: `.gpu_config.json` (via `read_scheduler_config()`).
- Reservation overlay file: `.gpu_reservations.json`.

Power Control System
--------------------
Backend implementation:
- `GET /api/gpu/power-control` returns live+persisted state.
- `POST /api/gpu/power-control` supports:
  - `preset`: `eco` or `stock`
  - `toggle`: flips between saved profile and stock defaults
  - per-GPU manual write: `{gpu_index, limit_watts}`

Write path:
- `set_gpu_power_limit()` attempts:
  1) direct `nvidia-smi -i <gpu> -pl <watts>`
  2) `sudo -n nvidia-smi ...` fallback

Persisted power state:
- File: `.gpu_power_state.json`
- Keys:
  - `current_limits`
  - `saved_limits`
  - `enabled`
  - `updated_at`

Fan Control System
------------------
API endpoints:
- `GET /api/gpu/fan-control`
- `POST /api/gpu/fan-control` with `{gpu_index, mode, target_percent}`
- `PUT /api/gpu/fan-control/mapping` (nvidia-settings backend only)

Persisted fan state:
- File: `.gpu_fan_state.json`
- Keys:
  - `profiles`: per-GPU desired mode/target hints
  - `mapping_overrides`: explicit nvidia-settings fan target overrides
  - `updated_at`

Backend selection:
- Env var: `BMS_FAN_CONTROL_BACKEND`
- Accepted values:
  - `nvidia-settings`
  - `coolercontrol` (or `cctv` alias)

NVIDIA-Settings Backend Details
-------------------------------
Mapping problem:
- `nvidia-smi` GPU index is not guaranteed to equal `nvidia-settings [gpu:N]` target.

Resolution order in `_resolve_fan_mapping()`:
1) UUID direct mapping
2) target-index direct fallback
3) stable index-order fallback
4) explicit override merge (`/fan-control/mapping`)

Write behavior:
- mode write: `[gpu:<settings_target>]/GPUFanControlState`
- manual target write: `[fan:<fan_target>]/GPUTargetFanSpeed`

CoolerControl Backend Details
-----------------------------
Key point:
- Current implementation writes per GPU/device/channel directly.
- It does not rely on global mode activation for UI fan writes.

Mapping path:
1) Query `nvidia-smi` for `index,uuid,name,pci.bus_id`.
2) Query CoolerControl `/devices`.
3) Match GPU to device by normalized PCI bus location.
4) Derive writable channels from device `speed_options.fixed_enabled`.

Write path in `POST /api/gpu/fan-control`:
- `manual`: `PUT /devices/{uid}/settings/{channel}/manual` with `{"speed_fixed": <pct>}`
- `auto`: `PUT /devices/{uid}/settings/{channel}/reset`

Read path for snapshot:
- `GET /devices/{uid}/settings` (cookie auth)
- `GET /status/{uid}` (channel duty/rpm)

Auth/session:
- Login call: `POST /login` with Basic auth.
- API uses `cc=...` cookie from `Set-Cookie` header.

cctv Tool Role
--------------
`cctv` is now an auxiliary operational tool:
- used by API to list available modes for observability (`available_modes`)
- used by setup scripts for mode bootstrap and diagnostics
- not required for per-channel fan writes if daemon REST and auth are healthy

Runtime cctv config:
- generated at `.cctv.generated.json` unless overridden by `BMS_COOLERCONTROL_CCTV_CONFIG`

Recommended env for CoolerControl backend:
- `BMS_FAN_CONTROL_BACKEND=coolercontrol`
- `BMS_COOLERCONTROL_DAEMON_ADDRESS=127.0.0.1`
- `BMS_COOLERCONTROL_DAEMON_PORT=11987`
- `BMS_COOLERCONTROL_USERNAME=CCAdmin`
- `BMS_COOLERCONTROL_PASSWORD=<password>`
- `BMS_COOLERCONTROL_CLI=<absolute path to cctv>`

UI Behavior and Polling
-----------------------
Hardware Controls (`Layout.tsx`):
- Polls `GET /api/gpu/power-control` + `GET /api/gpu/fan-control` every 5s.
- Applies writes through:
  - `POST /api/gpu/power-control`
  - `POST /api/gpu/fan-control`

Important fan UI behavior:
- Draft fan mode/target are synced from live backend state.
- For CoolerControl rows, channel names are displayed (not numeric fan object IDs).
- Snapshot `profile_mode/profile_target_percent` is aligned to effective live state to
  avoid stale 100% hints from old behavior.

System Resources panel (`SystemResources.tsx`):
- Reads `/api/gpu/status` and scheduler endpoints.
- Displays utilization, VRAM (including reservation overlays), temps, clocks, power.
- Manages scheduler global/per-GPU overrides, workflow pins, and locks.

Scheduler and Orchestrator Data
-------------------------------
Scheduler config file:
- `.gpu_config.json` via `platform/api/services/gpu_config.py`.

Orchestrator lifecycle:
- Started/stopped from FastAPI lifespan in `platform/api/main.py`.
- Poll loop in `GPUOrchestrator` packs jobs with VRAM-aware logic.

Scheduler-related endpoints in `gpu.py`:
- `/scheduler-config` (+ per-GPU override and toggle)
- `/workflow-pins`
- `/gpu-locks`
- `/concurrency-limits`
- debug bypass: `/force-run/{job_id}`

Startup and Environment Loading
-------------------------------
Service scripts source env from:
- `~/.biomodstack/env.sh`

Scripts:
- `start_ui.sh`:
  - starts API (`uv run uvicorn`) and frontend (`npm run dev`)
  - defaults fan backend to coolercontrol when unset
- `restart_api.sh`:
  - restarts API only
  - also defaults fan backend to coolercontrol when unset

CoolerControl bootstrap helper:
- `scripts/fix_coolercontrol_backend.sh`
- Responsibilities:
  - persist relevant env vars
  - write cctv config (`~/.config/coolercontrol/cctv.json`)
  - ensure daemon install/start
  - optional mode bootstrap (`BMS Auto`, `BMS Manual`)
  - restart BMS services

Operational Verification Commands
---------------------------------
Use these to verify end-to-end behavior.

1) Check fan-control snapshot
```bash
curl -sS http://127.0.0.1:8000/api/gpu/fan-control | jq
```

2) Set one GPU manual target
```bash
curl -sS -X POST http://127.0.0.1:8000/api/gpu/fan-control \
  -H 'Content-Type: application/json' \
  -d '{"gpu_index":1,"mode":"manual","target_percent":70}' | jq
```

3) Return that GPU to auto
```bash
curl -sS -X POST http://127.0.0.1:8000/api/gpu/fan-control \
  -H 'Content-Type: application/json' \
  -d '{"gpu_index":1,"mode":"auto"}' | jq
```

4) Verify power state payload
```bash
curl -sS http://127.0.0.1:8000/api/gpu/power-control | jq
```

Troubleshooting Playbook
------------------------
1) `supported: false` for CoolerControl
- Check daemon health: `curl -sS http://127.0.0.1:11987/health`
- Check API snapshot message for `login failed`, `no writable channels`, or mapping errors.

2) `cctv` errors but control writes still work
- Per-channel writes rely on daemon REST, not `cctv` mode activation.
- Treat `available_modes` issues as secondary unless scripts depend on modes.

3) No mapping for one GPU
- Confirm PCI bus IDs exist in both `nvidia-smi` and CoolerControl `/devices` payload.
- Mapping source should show `pci_bus` for CoolerControl entries.

4) UI appears stale or inconsistent
- Hard refresh UI and re-check `/api/gpu/fan-control` JSON directly.
- Ensure API restarted with expected env (`~/.biomodstack/env.sh` sourced).

5) Command failures from shell wrapping
- Use single-line commands or scripts; avoid line-wrapped fragments copied from terminal.

AI Change Rules (Do/Do Not)
---------------------------
Do:
- Treat `nvidia-smi` GPU indices as canonical user-facing IDs.
- Preserve both fan backends and keep backend selection explicit.
- Verify behavior via API payloads after any fan/power change.
- Keep persisted state files backward-compatible when possible.

Do not:
- Assume `nvidia-settings [gpu:N]` equals `nvidia-smi index`.
- Reintroduce global-only CoolerControl mode writes for per-GPU UI actions.
- Gate writable CoolerControl operations on mode list availability.
- Hardcode user-specific paths in control code.

Maintenance Notes
-----------------
- `.cctv.generated.json` is runtime-generated and ignored by git.
- If future CoolerControl API versions change payload shape, update:
  - `_coolercontrol_device_fan_channels()`
  - `_coolercontrol_device_pci_location()`
  - `_coolercontrol_settings_map()`
  - `_coolercontrol_channel_status_map()`

Last Updated
------------
- Updated after CoolerControl per-GPU/channel control migration and UI stale-state fix.
