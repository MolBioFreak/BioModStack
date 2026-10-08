# BioXP BMS current-control removal and OEM integration path — 2026-05-04

> **SUPERSEDED AS THE CURRENT BMS CONTRACT (2026-07-18):** Retained as historical
> planning evidence only. Do not restore its proxy/hardware routes. See
> [BioXP Compact Control Plane](../BioXP_Compact_Control_Plane.md).

## Scope

Christian asked for a repo-grounded removal path for the current BioXP robot controls in BioModStack, plus a full integration path for the new minimally functional robot-local OEM system.

Non-negotiable preservation requirement:

- Keep chiller control surfaces.
- Keep thermal cycler control surfaces.
- Keep thermal door control surface.

Safety/architecture boundary:

- BMS is the instrument-control API/UI surface.
- Robot-local `bioxp-api.service` remains the hardware authority.
- BMS must not start/stop/spawn/kill robot uvicorn or own motor semantics.
- `/api/bioxp/daemon/start` and `/api/bioxp/daemon/stop` stay disabled by design.
- Physical observation overrides telemetry, especially for Z.
- No fake/demo/dry-run output is hardware proof.

---

## Current BMS control inventory inspected

### Backend file

`platform/api/routers/bioxp.py`

Current route groups:

- Linkage/runtime:
  - `GET /api/bioxp/linkage`
  - `POST /api/bioxp/linkage`
  - `POST /api/bioxp/linkage/disconnect`
  - `GET /api/bioxp/daemon/status`
  - `GET /api/bioxp/runtime/status`
  - `POST /api/bioxp/daemon/start` — currently disabled with `409`
  - `POST /api/bioxp/daemon/stop` — currently disabled with `409`
- Status/capabilities:
  - `GET /api/bioxp/status`
  - `GET /api/bioxp/capabilities`
  - `GET /api/bioxp/capabilities/oem-test-prep`
- Existing motion/reference/power/raw controls:
  - `GET /api/bioxp/motion/reference/status`
  - `POST /api/bioxp/motion/reference/mark_referenced`
  - `POST /api/bioxp/motion/reference/mark_desynced`
  - `GET /api/bioxp/motion/axis/{axis}/status`
  - `GET /api/bioxp/motion/axes/status`
  - `POST /api/bioxp/motion/interlock/prepare`
  - `GET /api/bioxp/motion/power/status`
  - `POST /api/bioxp/motion/power/enable`
  - `POST /api/bioxp/motion/power/diag`
  - `POST /api/bioxp/motion/axes/current`
  - `POST /api/bioxp/motion/arm/strict_startup`
  - `POST /api/bioxp/motion/hard_reset`
  - `POST /api/bioxp/motion/clear_lock`
  - `POST /api/bioxp/motion/axis/relative`
  - `POST /api/bioxp/motion/axis/absolute`
  - `POST /api/bioxp/motion/axis/home`
- Liquid handler:
  - `GET /api/bioxp/liquid/status`
  - `POST /api/bioxp/liquid/init`
  - `POST /api/bioxp/liquid/tip`
  - `POST /api/bioxp/liquid/aspirate`
  - `POST /api/bioxp/liquid/dispense`
  - `POST /api/bioxp/liquid/mix`
- Deck IO / LED:
  - `GET /api/bioxp/latch/status`
  - `POST /api/bioxp/latch/lock`
  - `POST /api/bioxp/latch/unlock`
  - `POST /api/bioxp/led/off`
  - `POST /api/bioxp/led/on`
  - `POST /api/bioxp/led/pct`
  - `POST /api/bioxp/led/rgb`
- Thermal cycler — preserve:
  - `GET /api/bioxp/thermal/snapshot`
  - `POST /api/bioxp/thermal/baseline`
  - `POST /api/bioxp/thermal/set_temp`
  - `POST /api/bioxp/thermal/fan`
  - `POST /api/bioxp/thermal/pwm`
  - `POST /api/bioxp/thermal/rates`
  - `POST /api/bioxp/thermal/fast_profile`
  - `POST /api/bioxp/thermal/hard_reset`
- Chiller — preserve:
  - `GET /api/bioxp/chiller/snapshot`
  - `POST /api/bioxp/chiller/baseline`
  - `POST /api/bioxp/chiller/set_temp`
  - `POST /api/bioxp/chiller/fan`
  - `POST /api/bioxp/chiller/pwm`
  - `POST /api/bioxp/chiller/rates`
  - `POST /api/bioxp/chiller/hard_reset`
- Camera/vision/protocol:
  - camera devices/controls/snapshot/stream health/recovery/MJPEG
  - vision inspect/barcode
  - protocol compile/execute/jobs/review

### Frontend files

- `platform/frontend/src/lib/bioxpClient.ts`
  - typed hooks already exist for linkage, status, runtime, motion reference, liquid, raw motion, latch, LED, thermal, chiller, camera, vision, protocol.
- `platform/frontend/src/components/BioXpCockpit.tsx`
  - route-level cockpit with tabs:
    - `Linkage & Status`
    - `Protocol Operator`
    - `Motion, Latch & Thermals`
    - `Camera Feed`
  - current UI panels include:
    - Linked Runtime Status
    - Proxy Linkage
    - Recovery & Interlocks
    - Latch & Deck IO
    - LED Control
    - Motion Reference Truth
    - Liquid Handler
    - Vision / Barcode Smoke Tests
    - Motion Power & Recovery
    - Motion Control System
    - Thermal Cycler
    - Chiller System
    - camera panels
- `platform/frontend/src/App.tsx`
  - exposes `/bioxp`.
- `platform/frontend/src/components/Layout.tsx`
  - nav entry: `BioXP Cockpit`.

### Current old/raw control surfaces in the UI

These are the main surfaces to remove from the default operator path:

- `Motion Control System` panel:
  - `AxisControls` for `x`, `y`, `z`, `g`, `door`.
  - relative moves.
  - absolute moves.
  - home.
  - capture bundle / dry-run bundle options.
- `CameraFeed` calibration overlays:
  - `CameraHoldJogPad` for X/Y/Z relative jogs.
  - `CameraAxisQuickControls` for gripper relative/home.
- `Motion Power & Recovery`:
  - power enable.
  - power diag.
  - strict startup.
  - hard reset.
  - current-setting controls.
- `Recovery & Interlocks`:
  - reconnect USB runtime.
  - prepare motion interlock.
  - clear head lock.
- `Motion Reference Truth`:
  - mark referenced.
  - mark desynced.
- `Liquid Handler`:
  - init.
  - load/eject tip.
  - aspirate.
  - dispense.
  - mix.

Surfaces explicitly preserved:

- `Thermal Cycler` panel and hooks.
- `Chiller System` panel and hooks.
- Thermal door axis readback/control must be preserved, but not as part of generic all-axis raw motion. It should become an OEM/deck thermal-door control/readback panel.

---

## Current robot-local control inventory inspected

Robot OpenAPI from `http://[REDACTED-ROBOT-HOST]:8123/openapi.json` currently exposes 97 paths.

Existing old-compatible routes still present on robot:

- raw axis status/move/home:
  - `/motion/axis/{axis}/status`
  - `/motion/axes/status`
  - `/motion/axis/relative`
  - `/motion/axis/absolute`
  - `/motion/axis/home`
- recovery/power/interlock:
  - `/motion/interlock/prepare`
  - `/motion/power/status`
  - `/motion/power/enable`
  - `/motion/power/diag`
  - `/motion/axes/current`
  - `/motion/arm/strict_startup`
  - `/motion/hard_reset`
  - `/motion/clear_lock`
- liquid:
  - `/liquid/status`
  - `/liquid/init`
  - `/liquid/tip`
  - `/liquid/aspirate`
  - `/liquid/dispense`
  - `/liquid/mix`
- preserved thermals/chiller:
  - `/thermal/*`
  - `/chiller/*`

New/minimal OEM system routes currently present on robot:

- motion/readback:
  - `GET /motion/range/status`
  - `POST /motion/oem/startup_step`
- OEM startup:
  - `POST /oem/startup/request`
  - `POST /oem/startup/door_event`
  - `GET /oem/startup/status/latest`
  - `GET /oem/startup/status/{session_id}`
- OEM runtime:
  - `GET /oem/runtime/status`
  - `GET /oem/runtime/state`
  - `GET /oem/runtime/worker/status`
  - `POST /oem/runtime/recover`
  - `POST /oem/runtime/emergency_stop`
  - `POST /oem/runtime/events/door`
  - `POST /oem/runtime/events/pause`
  - `POST /oem/runtime/events/resume`
  - `GET /oem/runtime/events/latest`
- OEM runtime command bridge:
  - `POST /oem/runtime/commands/initializeSystem`
  - `POST /oem/runtime/commands/PrepareToRunJob`
  - `POST /oem/runtime/commands/validateJob`
  - `POST /oem/runtime/commands/enqueue`
  - `POST /oem/runtime/commands/abortjob`
  - `POST /oem/runtime/commands/unlockProcess`
  - `POST /oem/runtime/commands/wakefrompause`
  - `GET /oem/runtime/commands/history`
- audit/worker:
  - `POST /oem/switch_audit`
  - `GET /oem/motion_worker/status`
  - `POST /oem/motion_worker/abort`
  - `POST /oem/motion_worker/run_next`
- preserved protocol/camera/vision:
  - `/protocol/*`
  - `/camera/*`
  - `/vision/*`

---

## Classification: remove, quarantine, keep, replace

### Remove from normal BMS operator UI immediately

These should not be visible in the default `/bioxp` cockpit flow:

- Generic `Motion Control System` all-axis raw controls for `x/y/z/g`.
- Generic raw absolute move.
- Generic raw relative jog.
- Generic home buttons.
- Motion current setter as a normal operator primitive.
- Hard reset as a routine operator primitive.
- Prepare interlock as a routine operator primitive.
- Clear head lock as a routine operator primitive.
- Mark referenced / mark desynced as routine operator buttons.
- Liquid init/tip/aspirate/dispense/mix as naked actuator buttons.
- Camera overlay jog pads for X/Y/Z/G as a default camera-control behavior.
- Any `dry-run bundle` UI language in a live hardware surface, unless it is explicitly labeled as validation artifact generation and never as hardware proof.

### Keep, but re-home as commissioning-only

Do not delete the backend routes yet. Keep them callable only through an explicitly named commissioning surface until the OEM replacement has enough live coverage:

- `/api/bioxp/motion/axis/relative`
- `/api/bioxp/motion/axis/absolute`
- `/api/bioxp/motion/axis/home`
- `/api/bioxp/motion/axes/current`
- `/api/bioxp/motion/interlock/prepare`
- `/api/bioxp/motion/hard_reset`
- `/api/bioxp/motion/clear_lock`
- `/api/bioxp/motion/reference/mark_referenced`
- `/api/bioxp/motion/reference/mark_desynced`
- `/api/bioxp/liquid/init`
- `/api/bioxp/liquid/tip`
- `/api/bioxp/liquid/aspirate`
- `/api/bioxp/liquid/dispense`
- `/api/bioxp/liquid/mix`

Commissioning surface requirements:

- Not the default tab.
- Label: `Commissioning / Raw Motion — supervised only`.
- Explicit enable gate, e.g. typed phrase or local toggle.
- Every actuating button must show exact payload, response, readback, and physical-evidence-required flag.
- Z motion must stay controller-reported unless operator-confirmed.

### Keep in the default operator UI

- Linkage/readiness/status/capabilities.
- Protocol compile/review/jobs, but execution must be gated by OEM readiness.
- Camera/vision evidence, without default raw jog controls.
- Latch/deck IO readback.
- LED control can remain lower priority/internal, but should not be mixed with motor bring-up.
- Chiller controls.
- Thermal cycler controls.
- Thermal door controls, but as a thermal/deck door semantic surface rather than generic raw all-axis motion.
- Emergency stop once proxied from the new OEM runtime route.

### Replace with new OEM/instrument-control API surface

Replace old default motor/liquid panels with:

- OEM Runtime panel:
  - `/api/bioxp/oem/runtime/status`
  - `/api/bioxp/oem/runtime/state`
  - `/api/bioxp/oem/runtime/worker/status`
  - `/api/bioxp/oem/runtime/recover`
  - `/api/bioxp/oem/runtime/emergency_stop`
- OEM Startup panel:
  - `/api/bioxp/oem/startup/request`
  - `/api/bioxp/oem/startup/door_event`
  - `/api/bioxp/oem/startup/status/latest`
  - `/api/bioxp/motion/oem/startup_step`
- Range/switch/reference panel:
  - `/api/bioxp/motion/range/status`
  - `/api/bioxp/motion/reference/status`
  - `/api/bioxp/oem/switch_audit`
- OEM command/event panel:
  - `/api/bioxp/oem/runtime/events/*`
  - `/api/bioxp/oem/runtime/commands/*`
  - command history/readback
- Protocol/liquid semantic panel:
  - protocol compile/review/execute gates through OEM runtime readiness.
  - liquid operations are shown as semantic protocol steps with ACK/readback, not naked buttons.

---

## Removal path

### Phase R0 — assert daemon lifecycle is retired

Files:

- `platform/api/routers/bioxp.py`
- `platform/api/tests/test_bioxp_router.py`
- `platform/frontend/src/components/BioXpCockpit.tsx`

Actions:

1. Keep `/api/bioxp/daemon/start` and `/api/bioxp/daemon/stop` registered for compatibility.
2. Make tests assert both always return `409` with a detail saying robot runtime lifecycle is robot-local and BMS uses instrument-control linkage.
3. Remove any frontend interpretation of `admin_control_available` that implies BMS can start/stop the robot runtime.
4. Runtime panel wording becomes: `Linked robot-local runtime`, not `daemon`.

Validation:

- Backend tests prove no code path calls process start/stop.
- Frontend build artifact has no `Start Daemon` / `Stop Daemon` wording.

### Phase R1 — split cockpit into default operator vs commissioning

Files:

- `platform/frontend/src/components/BioXpCockpit.tsx`
- possibly new `platform/frontend/src/components/bioxp/CommissioningPanel.tsx`

Actions:

1. Remove `Motion Control System` from the default `Motion, Latch & Thermals` tab.
2. Create a non-default commissioning panel or tab.
3. Move `AxisControls` for `x/y/z/g` into commissioning-only.
4. Do **not** move `door` with generic axes; thermal door is preserved in the thermal/deck area.
5. Move `CameraHoldJogPad` and `CameraAxisQuickControls` into commissioning-only or hide them behind a camera calibration/commissioning toggle.
6. Remove raw jog/home/absolute controls from the camera feed default.

Validation:

- Default `/bioxp` source no longer renders `Move Absolute`, generic `Home`, raw jog arrows for X/Y/Z/G, or the `Motion Control System` title in the default path.
- Commissioning route/panel still compiles for supervised testing.

### Phase R2 — demote recovery/interlock/power buttons

Files:

- `platform/frontend/src/components/BioXpCockpit.tsx`
- `platform/frontend/src/lib/bioxpClient.ts`

Actions:

1. Keep read-only `motion/power/status` in default UI if it helps readiness.
2. Move `motion/power/enable`, `motion/power/diag`, `motion/arm/strict_startup`, `motion/hard_reset`, `motion/clear_lock`, `motion/interlock/prepare`, and `motion/axes/current` into commissioning-only.
3. Replace default panel with read-only readiness plus links to OEM startup/recover once added.

Validation:

- Default UI has no normal operator `Prepare Motion Interlock`, `Clear Head Lock`, `Hard Reset`, current-setting, or raw power-enable buttons.

### Phase R3 — demote naked liquid buttons

Files:

- `platform/frontend/src/components/BioXpCockpit.tsx`
- `platform/frontend/src/lib/bioxpClient.ts`

Actions:

1. Keep `liquid/status` in default UI as readback.
2. Move `liquid/init`, `tip`, `aspirate`, `dispense`, `mix` buttons into commissioning-only or remove from default entirely.
3. Replace default `Liquid Handler` with `Liquid readiness/readback` and semantic protocol/liquid-step status.
4. Later wire liquid actions through OEM runtime command/protocol flow, not naked buttons.

Validation:

- Default UI lacks `Init`, `Load Tip`, `Eject Tip`, `Aspirate`, `Dispense`, `Mix` as direct buttons.
- Status/readback remains.

### Phase R4 — preserve thermal/chiller/thermal-door

Files:

- `platform/frontend/src/components/BioXpCockpit.tsx`
- `platform/frontend/src/lib/bioxpClient.ts`
- `platform/api/routers/bioxp.py`

Actions:

1. Keep all `/thermal/*` BMS proxies and hooks.
2. Keep all `/chiller/*` BMS proxies and hooks.
3. Keep `Thermal Cycler` UI panel.
4. Keep `Chiller System` UI panel.
5. Preserve thermal door as a named deck/thermal-door semantic surface:
   - keep door status/readback through `/motion/axis/door/status`, `/motion/reference/status`, `/motion/range/status`, and/or OEM runtime state.
   - do not show thermal door only as one row in generic all-axis raw controls.
   - if movement action remains, label it as `Thermal Door` and gate it with OEM startup/readiness semantics.

Validation:

- Built UI still contains `Thermal Cycler`, `Chiller System`, and `Thermal Door`.
- Built UI does not contain default `Motion Control System` all-axis raw panel.

### Phase R5 — backend capability classification

Files:

- `platform/api/routers/bioxp.py`
- `platform/api/tests/test_bioxp_router.py`

Actions:

1. Expand `/api/bioxp/capabilities` to classify surfaces:
   - `default_operator`
   - `commissioning_only`
   - `preserved_thermal`
   - `preserved_chiller`
   - `oem_runtime`
   - `oem_startup`
2. Keep old raw routes present for internal supervised use.
3. Do not advertise old raw motion/liquid as normal operator capabilities.

Validation:

- Test verifies raw motion/liquid routes are `commissioning_only`.
- Test verifies thermal/chiller/thermal-door are preserved.

---

## New integration path

### Phase I0 — BMS backend OEM route proxies

Files:

- `platform/api/routers/bioxp.py`
- `platform/api/tests/test_bioxp_router.py`

Add BMS proxies:

- `GET /api/bioxp/motion/range/status` -> robot `GET /motion/range/status`
- `POST /api/bioxp/motion/oem/startup_step` -> robot `POST /motion/oem/startup_step`
- `GET /api/bioxp/oem/runtime/status` -> robot `GET /oem/runtime/status`
- `GET /api/bioxp/oem/runtime/state` -> robot `GET /oem/runtime/state`
- `GET /api/bioxp/oem/runtime/worker/status` -> robot `GET /oem/runtime/worker/status`
- `POST /api/bioxp/oem/runtime/recover` -> robot `POST /oem/runtime/recover`
- `POST /api/bioxp/oem/runtime/emergency_stop` -> robot `POST /oem/runtime/emergency_stop`
- `POST /api/bioxp/oem/runtime/events/door` -> robot `POST /oem/runtime/events/door`
- `POST /api/bioxp/oem/runtime/events/pause` -> robot `POST /oem/runtime/events/pause`
- `POST /api/bioxp/oem/runtime/events/resume` -> robot `POST /oem/runtime/events/resume`
- `GET /api/bioxp/oem/runtime/events/latest` -> robot `GET /oem/runtime/events/latest`
- `POST /api/bioxp/oem/startup/request` -> robot `POST /oem/startup/request`
- `POST /api/bioxp/oem/startup/door_event` -> robot `POST /oem/startup/door_event`
- `GET /api/bioxp/oem/startup/status/latest` -> robot `GET /oem/startup/status/latest`
- `GET /api/bioxp/oem/startup/status/{session_id}` -> robot `GET /oem/startup/status/{session_id}`
- `POST /api/bioxp/oem/switch_audit` -> robot `POST /oem/switch_audit`
- optional second tranche: OEM runtime command bridge under `/api/bioxp/oem/runtime/commands/*`.

Validation:

- Unit test every proxy path/method/payload/timeout.
- `/api/bioxp/capabilities` includes these as `oem_runtime` or `oem_startup`.

### Phase I1 — typed frontend hooks

File:

- `platform/frontend/src/lib/bioxpClient.ts`

Add hooks:

- `useBioXpMotionRangeStatus`
- `useBioXpOemStartupStep`
- `useBioXpOemRuntimeStatus`
- `useBioXpOemRuntimeState`
- `useBioXpOemRuntimeWorkerStatus`
- `useBioXpOemRuntimeRecover`
- `useBioXpOemRuntimeEmergencyStop`
- `useBioXpOemRuntimeDoorEvent`
- `useBioXpOemRuntimePause`
- `useBioXpOemRuntimeResume`
- `useBioXpOemRuntimeLatestEvent`
- `useBioXpOemStartupRequest`
- `useBioXpOemStartupDoorEvent`
- `useBioXpOemStartupLatestStatus`
- `useBioXpOemSwitchAudit`

Validation:

- TypeScript build.
- No ad hoc component-level API paths for new OEM routes.

### Phase I2 — new default cockpit layout

File:

- `platform/frontend/src/components/BioXpCockpit.tsx`

New default tab structure:

1. `Connection & Readiness`
   - linkage
   - runtime status
   - hardware connected
   - OEM runtime status
   - emergency stop
2. `OEM Startup`
   - startup request
   - latest startup session/status
   - startup step, if still useful
   - door event
   - arm/readiness display
3. `Deck / Motion Readback`
   - range status
   - switch audit
   - reference status
   - axis status read-only
   - thermal door readback/action as semantic door, not generic raw axis
4. `Protocol Operator`
   - compile
   - review
   - execute only when readiness gates pass
   - job state/history
5. `Liquid Readback`
   - liquid status and last ACK/readback
   - no naked aspirate/dispense/mix default buttons
6. `Thermals`
   - thermal cycler preserved
   - chiller preserved
   - thermal door preserved
7. `Camera / Evidence`
   - camera stream
   - snapshot
   - barcode/vision
   - no default raw jog overlay
8. `Commissioning`
   - hidden/non-default
   - raw motion/liquid/recovery controls only with explicit gate

Validation:

- Default panels are OEM-first.
- Thermal/chiller/thermal-door are still visible.
- Raw motion/liquid are absent from default path.

### Phase I3 — readiness gates

Every live command in default UI must check:

- linkage configured.
- linked runtime reachable.
- hardware connected.
- OEM runtime state allows the action.
- startup/readiness session is acceptable.
- motion range/switch status has no active block for the requested semantic operation.
- for Z-related or ambiguous motion: physical evidence required unless independent evidence is present.

Protocol execute gates:

- compile succeeded.
- review approved.
- OEM runtime ready.
- liquid readiness OK if protocol contains liquid actions.
- thermal/chiller state OK if protocol needs thermal/chiller.
- thermal door state OK if door action is required.

### Phase I4 — audit and evidence

For every actuating command, show and store:

- route and HTTP method.
- payload.
- preflight/readiness snapshot.
- response.
- post-command readback.
- operator note.
- camera/snapshot refs where available.
- `physical_evidence_required` boolean.
- `controller_reported_only` boolean for uncertain motion.

### Phase I5 — live validation order

1. BMS backend unit tests.
2. BMS frontend build.
3. OpenAPI check on running BMS: new `/api/bioxp/oem/*` and `/api/bioxp/motion/range/status` are present.
4. Linkage/status checks only; no motion.
5. OEM runtime status/state/readback checks only.
6. Emergency stop route dry invocation only if safe/no motion, or defer until operator present.
7. Startup status/request with operator present.
8. Range/switch audit readback.
9. Thermal/chiller/thermal-door verification.
10. Protocol compile/review.
11. Protocol execute only after operator confirmation and readiness gates.
12. Commissioning raw motion only as separate supervised tests.

---

## Dev/prod safety and commit plan

BioModStack has separate operator surfaces/runtime modes. Treat Vite/dev as the fast iteration path and stable/Electron/prod as the mature operator path. The swap must be validated in both before any live robot action is presented as ready.

### Environment safety rules

- **Dev web UI first:** use the Vite/web surface for rapid component and hook iteration. No live motion validation from dev until backend route tests and string/artifact checks pass.
- **Stable web/Electron second:** verify the built/stable `/bms` and Electron shell consume the same BMS API contract. Do not add an Electron-only robot-control path.
- **One API contract:** Electron and web UI must call the same `/api/bioxp/...` routes and typed `bioxpClient.ts` hooks.
- **No process lifecycle regression:** `/api/bioxp/daemon/start` and `/api/bioxp/daemon/stop` remain disabled in both dev and prod/stable runtime.
- **No default raw controls in either environment:** old raw motor/liquid labels must be absent from the default dev bundle and the stable/Electron bundle.
- **Commissioning is explicit:** any raw fallback controls must be non-default and gated in both dev and prod/stable.
- **Thermal/chiller/thermal-door preservation:** both environments must still expose thermal cycler, chiller, and thermal door surfaces.

### Commit plan

Use small commits so any bad tranche can be reverted cleanly:

1. `docs(bioxp): spec BMS control removal and OEM integration path`
   - Current plan docs and README link.
2. `test(bioxp): lock retired daemon lifecycle and capability classification`
   - Tests for disabled daemon start/stop.
   - Tests for default-vs-commissioning capability classes.
3. `refactor(bioxp): remove raw controls from default cockpit`
   - Move raw axis/recovery/liquid controls out of the default UI.
   - Preserve thermal cycler, chiller, and thermal door.
   - Add source/string tests or focused frontend tests for absence of old labels in default path.
4. `feat(bioxp): add OEM runtime and startup API proxies`
   - Backend proxies for OEM runtime/startup/range/switch-audit routes.
   - Unit tests for method/path/payload/timeout.
5. `feat(bioxp): add typed OEM frontend hooks`
   - `bioxpClient.ts` hooks only; no component-level ad hoc route calls.
6. `feat(bioxp): add OEM-first instrument cockpit panels`
   - Runtime/readiness, startup, range/switch/reference, emergency stop, protocol/liquid readback, thermals, evidence.
7. `test(bioxp): verify dev and stable BioXP control surfaces`
   - Backend tests.
   - Frontend build.
   - Stable bundle string checks.
   - Electron/web contract parity notes.
8. Optional hardening commit: `refactor(bioxp): profile-gate commissioning-only routes`
   - Only after default UI is clean and live testing no longer needs broad raw access.

### Pre-commit verification per tranche

- Backend:

```bash
cd /home/dalab/biomodstack/biomodstack/platform/api
uv run --group dev python -m pytest tests/test_bioxp_router.py tests/test_bioxp_protocol_jobs.py -q
```

- Frontend:

```bash
cd /home/dalab/biomodstack/biomodstack/platform/frontend
npm run build
```

- Source/bundle assertions:
  - default operator surface does not expose `Motion Control System`, `Move Absolute`, `Prepare Motion Interlock`, `Clear Head Lock`, naked `Aspirate`, `Dispense`, `Mix`, `Start Daemon`, or `Stop Daemon`.
  - default operator surface still exposes `Thermal Cycler`, `Chiller System`, and `Thermal Door`.

### Repository/commit caveat from current workstation checkout

The current `/home/dalab/biomodstack/biomodstack` tree does not report as a Git worktree via `git rev-parse --show-toplevel` in this shell. Before creating actual commits, resolve the authoritative Git checkout/branch or reinitialize work in the correct repo/worktree. Do not pretend commits were made from this non-Git tree.

---

## Concrete implementation order

1. R0: lock daemon lifecycle contract and tests.
2. R1/R2/R3: remove old raw motion/recovery/liquid from default cockpit.
3. R4: preserve and re-home thermal/chiller/thermal-door.
4. R5: classify capabilities.
5. I0: add backend OEM proxies.
6. I1: add frontend hooks.
7. I2: build OEM-first cockpit panels.
8. I3/I4: readiness gates + audit/evidence rendering.
9. I5: dev web validation, stable web/Electron validation, then live robot validation.

---

## Minimum test/build commands

Backend:

```bash
cd /home/dalab/biomodstack/biomodstack/platform/api
uv run --group dev python -m pytest tests/test_bioxp_router.py tests/test_bioxp_protocol_jobs.py -q
```

Frontend:

```bash
cd /home/dalab/biomodstack/biomodstack/platform/frontend
npm run build
```

Running OpenAPI check after BMS is up:

```bash
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', timeout=5) as r:
    paths = json.load(r)['paths']
for p in sorted(paths):
    if p.startswith('/api/bioxp/oem') or p in {'/api/bioxp/motion/range/status', '/api/bioxp/motion/oem/startup_step'}:
        print(p)
PY
```

Frontend source/artifact checks:

- default operator path should preserve:
  - `Thermal Cycler`
  - `Chiller System`
  - `Thermal Door`
- default operator path should not expose:
  - `Motion Control System`
  - `Move Absolute`
  - `Prepare Motion Interlock`
  - `Clear Head Lock`
  - naked `Aspirate` / `Dispense` / `Mix`
  - `Start Daemon` / `Stop Daemon`

---

## Final target state

BMS BioXP becomes a real instrument-control subsystem:

- Stable API boundary.
- OEM runtime/startup/readback first.
- Default UI is semantic/operator-safe.
- Raw motion/liquid exists only as supervised commissioning fallback until no longer needed.
- Chiller, thermal cycler, and thermal door remain first-class surfaces.
- Live tests proceed against robot-local OEM semantics, not legacy BMS motor buttons.
