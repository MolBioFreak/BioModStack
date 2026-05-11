# BioXP Robot ↔ Workstation Interlink Control Panel Spec

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task after Christian approves the scope.

**Goal:** Add a top-right BMS control-panel button for explicit BioXP robot/workstation interlink setup, connect/disconnect, diagnostics, robot-local runtime reset, and full robot reboot while removing uncontrolled reset surfaces.

**Architecture:** BMS remains a thin operator/control plane over the robot-local BioXP runtime. Both BMS and the robot must boot into a quiet/disconnected state: no BMS auto-connect, no polling, no discovery/broadcast, and no robot outbound connection attempt. The operator explicitly opens the BioXP interlink panel, chooses/connects a saved URL/profile, and then can run controlled lifecycle actions through local-admin-gated BMS backend routes.

**Tech Stack:** FastAPI (`platform/api/routers/bioxp.py` plus a small service helper), React/Vite/TanStack Query (`platform/frontend/src/lib/bioxpClient.ts`, `BioXpInterlinkControlPanel.tsx`, `Layout.tsx`), existing BMS local-admin route pattern, SSH/systemd on the robot for reset/reboot fallback, robot-local FastAPI for passive status and maintenance recovery where available.

---

## Grounded current-state findings

Current BMS already has useful seams but they need to be corrected for the new operator contract:

- `platform/frontend/src/components/Layout.tsx` already imports and renders top-right utility menus such as `StatsToolsMenu` and `DbServiceMenu`.
- `platform/frontend/src/components/StatsToolsControlPanel.tsx` and `DbServiceControlPanel.tsx` are the correct UI pattern: top-bar button, dropdown panel, state/health, lifecycle buttons, logs, and copyable commands.
- `platform/api/routers/system.py` has a local-admin guard pattern via `_require_local_admin(...)` for lifecycle/control routes.
- `platform/api/routers/bioxp.py` already has `/api/bioxp/linkage`, `/api/bioxp/linkage/disconnect`, `/api/bioxp/runtime/status`, and `/api/bioxp/daemon/status`.
- Current `bioxp.py` initializes `_GLOBAL_LINKAGE_URL` from persisted state/env at import time. That violates the new requirement because it can make BMS connect/poll after restart without operator intent.
- Current BioXP UI still contains generic reset language/buttons such as motion `Hard Reset` and `Reconnect USB Runtime`. Those are not acceptable as uncontrolled default operator controls.

## Non-negotiable behavior contract

### Boot/restart defaults

After both workstation/BMS and robot are restarted:

1. **BMS must not auto-connect to the robot.**
2. **BMS must not poll robot status/readback unless the operator has connected in the current session.**
3. **BMS may retain saved settings, but saved settings are inactive until Connect is pressed.**
4. **The robot must not broadcast, discover, or initiate a workstation connection.**
5. **Robot-local `bioxp-api.service` may be running/listening locally or on the private robot network, but it is passive.**
6. **No motor/USB/motion recovery or arming occurs as a side effect of opening BMS.**

Recommended state model:

```text
configured = URL/profile exists
active = current BMS session has an operator-initiated connection
reachable = last operator-initiated status probe succeeded
hardware_connected = robot API reported hardware_connected=true
maintenance_blocked = robot API reported maintenance_state.motion_blocked=true
```

`configured=true` must not imply `active=true`.

### Button placement

Add a top-right utility button in `Layout.tsx`, same visual family as `BMS DB` and `Stats-tools`:

```text
BIOXP LINK
```

Status dot rules:

- gray: no active link / disconnected
- amber: configured but inactive, or active but degraded/maintenance-blocked
- green: active, robot API reachable, hardware connected
- red: active but unreachable/error
- blue/pulse: active lifecycle action in progress, e.g. runtime reset or robot reboot watch

### Main panel sections

Create `platform/frontend/src/components/BioXpInterlinkControlPanel.tsx` with these sections:

1. **Connection profile**
   - URL input, default suggestion shown but not auto-used.
   - Connection mode: `direct HTTP` first; reserve `SSH tunnel` for later if needed.
   - Fields: robot API URL, robot SSH host, API port, optional display name.
   - Save settings button stores config only; it does not connect.
   - Connect button activates the current session and starts status polling.
   - Disconnect button stops polling and clears active session state; saved settings remain unless user chooses Forget.
   - Forget saved profile button removes persisted config.

2. **Runtime truth**
   - Shows active/configured/reachable/hardware state separately.
   - Shows last `/status` payload summary.
   - Shows maintenance state if present: `usb_owner`, `motion_blocked`, `recovery_required`, `blocked_by`, `recovery_hint`.
   - Shows route compatibility summary from robot OpenAPI when explicitly refreshed.

3. **Lifecycle controls**
   - `Reset robot local runtime` — controlled restart of robot-local BioXP runtime only.
   - `Restart robot OS` — full robot reboot.
   - `Diagnostics` — explicit one-shot probes for `/status`, `/openapi.json`, `/maintenance/usb/state`, `/motion/range/status`; no motion.
   - `Logs` — tails robot `bioxp-api.service` logs via SSH/systemd where available.

4. **Safety notice / arming boundary**
   - Clear text: lifecycle controls do not home, arm, recover motion, or move axes.
   - Link back to the BioXP cockpit for OEM startup and supervised motion once connected.

5. **Command preview**
   - Copyable commands for operator visibility, e.g. equivalent `bms bioxp-link status`, `connect`, `disconnect`, `runtime-reset`, `reboot`, `logs --tail 120` if CLI helpers are added.

## Backend API contract

Keep these routes under the BioXP domain, not generic system routes:

```text
GET    /api/bioxp/interlink/state
PUT    /api/bioxp/interlink/settings
DELETE /api/bioxp/interlink/settings
POST   /api/bioxp/interlink/connect
POST   /api/bioxp/interlink/disconnect
POST   /api/bioxp/interlink/diagnostics
POST   /api/bioxp/interlink/runtime-reset
POST   /api/bioxp/interlink/robot-reboot
POST   /api/bioxp/interlink/logs
```

All non-read operations must be local-admin-only using the same host allowlist contract as `platform/api/routers/system.py`.

### `GET /state`

Returns saved settings plus active-session status without initiating a robot network call unless `?probe=true` is explicitly passed.

Example:

```json
{
  "component": "bioxp-interlink",
  "configured": true,
  "active": false,
  "connection_mode": "direct_http",
  "robot_api_url": "http://robot:8123",
  "recommended_url": "http://robot:8123",
  "reachable": null,
  "hardware_connected": null,
  "maintenance_state": null,
  "last_probe_at": null,
  "control_mode": "bms-thin-proxy",
  "runtime_note": "Saved profile is inactive. Press Connect to start polling the robot."
}
```

### `PUT /settings`

Saves profile only. It does not activate the link.

Payload:

```json
{
  "robot_api_url": "http://robot:8123",
  "robot_ssh_host": "robot",
  "connection_mode": "direct_http",
  "display_name": "BioXP3200"
}
```

### `POST /connect`

Activates the saved or supplied profile for the current BMS API process/session and performs a passive one-shot `/status` probe. It must not arm, recover, home, or move.

### `POST /disconnect`

Stops all BMS-side BioXP polling/proxy attempts and marks the active session disconnected. Saved settings remain.

### `POST /diagnostics`

Explicit one-shot diagnostics only. Suggested probes:

- robot `/status`
- robot `/openapi.json` route list/hash
- robot `/maintenance/usb/state` if present
- robot `/motion/range/status?axes=x,y,z,g` because this is config/readback and does not move

Do not call `/motion/axes/status`, `/latch/status`, or `/motion/power/status` by default if the runtime says USB is released/blocked; report them as skipped until recovery.

### `POST /runtime-reset`

Controlled reset of robot-local runtime, not motion hardware.

Payload:

```json
{
  "operator_ack": "RESET BIOXP RUNTIME",
  "reason": "recover from stale USB runtime or API drift",
  "sudo_password": "optional one-shot value; never persist or log",
  "watch_until_ready": true,
  "tail": 120
}
```

Semantics:

1. Require exact `operator_ack`.
2. Require local-admin request.
3. Stop active BMS polling before reset.
4. Prefer a robot-local safe endpoint if the robot API exposes one for runtime restart.
5. Otherwise use SSH/systemd: `sudo systemctl restart bioxp-api.service` or the known robot-local service name.
6. Never run `killall`, `pkill`, broad USB resets, or raw uvicorn spawns from BMS.
7. Never run homing, strict startup, USB recovery, or motion-arm as part of this button.
8. Optionally watch `/status` until robot API returns, then leave the link active/degraded or inactive according to the operator choice.
9. Redact sudo password and secrets in all logs/artifacts.

### `POST /robot-reboot`

Full robot OS reboot.

Payload:

```json
{
  "operator_ack": "REBOOT ROBOT",
  "reason": "operator requested full robot restart",
  "sudo_password": "optional one-shot value; never persist or log",
  "watch_until_ready": false,
  "tail": 120
}
```

Semantics:

1. Require exact `operator_ack`.
2. Require local-admin request.
3. Display a danger warning: reboot drops robot API, interrupts cameras/USB, and invalidates current motion assumptions.
4. Stop BMS polling and mark connection as `rebooting` for this session only.
5. Use SSH/systemd (`sudo reboot`) or a robot-local reboot endpoint if one exists and is intentionally designed for this.
6. Do not auto-reconnect after page/BMS restart. If `watch_until_ready=true`, poll only in the current operator-initiated action window.
7. After robot returns, state must be `configured=true`, `active=false` unless the operator explicitly reconnects.

### `POST /logs`

Tails robot-local service logs through SSH/systemd where available. Does not connect or recover motion.

## Frontend API contract

Modify `platform/frontend/src/lib/bioxpClient.ts`:

- Add `BioXpInterlinkState`, `BioXpInterlinkSettings`, `BioXpInterlinkActionRequest`, `BioXpInterlinkActionResponse` types.
- Add hooks:
  - `useBioXpInterlinkState({ probe?: boolean, enabled?: boolean })`
  - `useSaveBioXpInterlinkSettings()`
  - `useForgetBioXpInterlinkSettings()`
  - `useBioXpInterlinkConnect()`
  - `useBioXpInterlinkDisconnect()`
  - `useBioXpInterlinkDiagnostics()`
  - `useBioXpRuntimeReset()`
  - `useBioXpRobotReboot()`
  - `useBioXpInterlinkLogs()`

Query enabling rules:

- The topbar menu may read `/state` passively.
- No robot-proxying query should run unless `state.active === true` or the user explicitly clicks Diagnostics/Connect.
- `BioXpCockpit.tsx` should not run hardware status hooks when inactive; it should show an interlink-required empty state.

## Remove uncontrolled reset surfaces

This is part of the spec, not optional cleanup.

1. Remove the generic motion `Hard Reset` button from `BioXpCockpit.tsx` default and commissioning surfaces.
2. Remove `useMotionHardReset` from the default frontend imports/usages unless a later approved advanced recovery panel reintroduces it with typed confirmation and local-admin audit. For this tranche, leave it gone.
3. Do not expose `/motion/power/hard_reset` or equivalent in default BMS capabilities.
4. Replace `Reconnect USB Runtime` wording with explicit, safe lifecycle naming:
   - `Recover service USB ownership` only if the endpoint is known to be non-motion and requires typed ack.
   - Otherwise move it under the new interlink panel as `Reset robot local runtime`.
5. Any remaining thermal/chiller reset-like controls must be re-audited and labeled by subsystem. They must not look like a generic robot reset. If they remain, labels should be specific: `Reset thermal controller profile`, `Reset chiller profile`, etc., with readback.
6. No reset/restart action may run from a single accidental click. Runtime reset and OS reboot require typed confirmation plus optional sudo-password prompt.

## Persistence model

Create a small service helper, likely `platform/api/services/bioxp_interlink.py`, for state persistence and action execution.

Suggested persisted file:

```text
get_data_root() / "bioxp_interlink_profile.json"
```

Persist only non-secret settings:

```json
{
  "schema_version": "bms.bioxp_interlink_profile.v1",
  "robot_api_url": "http://robot:8123",
  "robot_ssh_host": "robot",
  "connection_mode": "direct_http",
  "display_name": "BioXP3200",
  "auto_connect_on_launch": false
}
```

`auto_connect_on_launch` must default to `false`. If implemented at all, it should stay disabled unless Christian explicitly approves it later.

Do not persist:

- sudo password
- SSH private key material
- session active flag
- transient reachability/hardware state

## Robot-side contract

Robot behavior should stay passive after reboot:

- `bioxp-api.service` may start if that is the robot-local service policy, but it must not attempt to connect to BMS.
- No broadcast/discovery daemon is required.
- No motion startup/homing/USB recovery should happen simply because the robot booted or because BMS launched.
- If robot-local API has maintenance state like `maintenance_usb_release`, BMS should display it and offer controlled recovery guidance, not silently clear it.

## Implementation tasks

### Task 1: Backend state model tests

**Objective:** Prove saved BioXP interlink settings are inactive after API startup.

**Files:**
- Test: `platform/api/tests/test_bioxp_interlink_router.py`
- Modify: `platform/api/routers/bioxp.py`
- Create: `platform/api/services/bioxp_interlink.py`

Test cases:

- `GET /api/bioxp/interlink/state` with saved profile returns `configured=true`, `active=false`, `reachable=null`.
- Reading state does not call robot HTTP.
- `PUT /settings` saves config but does not activate.
- `POST /connect` activates and performs exactly one passive `/status` probe.
- `POST /disconnect` deactivates and stops probe status.

### Task 2: Backend lifecycle action tests

**Objective:** Prove reset/reboot are gated and do not run uncontrolled commands.

**Files:**
- Test: `platform/api/tests/test_bioxp_interlink_lifecycle.py`
- Modify/Create: `platform/api/services/bioxp_interlink.py`

Test cases:

- `runtime-reset` rejects missing/wrong `operator_ack`.
- `robot-reboot` rejects missing/wrong `operator_ack`.
- Both reject non-local clients.
- Password is redacted from response/log payload.
- Command executor receives a scoped systemd/reboot command, never `killall`, `pkill`, `nohup uvicorn`, or broad USB reset.
- Runtime reset does not call strict startup, homing, motion arm, or USB recover endpoints.

### Task 3: Backend route implementation

**Objective:** Add the `/api/bioxp/interlink/*` route family with a thin service helper.

**Files:**
- Modify: `platform/api/routers/bioxp.py`
- Create: `platform/api/services/bioxp_interlink.py`

Implementation notes:

- Reuse URL normalization from current `bioxp.py`, but split `configured` from `active`.
- Keep old `/api/bioxp/linkage` routes as compatibility wrappers if needed, but change them to the new inactive-by-default semantics.
- Avoid import-time activation from `_read_persisted_linkage()` or `BIOXP_SERVER_URL`.
- Make `get_current_url()` fail with a clear `400/409` if no active link exists.

### Task 4: Frontend client hooks

**Objective:** Add typed hooks for interlink panel actions.

**Files:**
- Modify: `platform/frontend/src/lib/bioxpClient.ts`
- Test: `platform/frontend/tests/bioxpInterlinkClient.test.ts` if client-hook tests exist, otherwise cover via component tests.

### Task 5: Topbar control panel component

**Objective:** Add the `BIOXP LINK` menu matching the existing BMS DB / Stats-tools pattern.

**Files:**
- Create: `platform/frontend/src/components/BioXpInterlinkControlPanel.tsx`
- Modify: `platform/frontend/src/components/Layout.tsx`
- Test: `platform/frontend/tests/bioxpInterlinkMenuContract.test.ts`

Required visible markers:

- `BIOXP LINK`
- `BioXP robot interlink`
- `Connect`
- `Disconnect`
- `Reset robot local runtime`
- `Restart robot OS`
- `Diagnostics`
- `Logs`
- `Saved profile is inactive` when configured but inactive

### Task 6: Cockpit inactive-state and polling gate

**Objective:** Prevent BioXP cockpit from polling/connecting unless the interlink is active.

**Files:**
- Modify: `platform/frontend/src/components/BioXpCockpit.tsx`
- Modify: `platform/frontend/src/lib/bioxpClient.ts`
- Tests: `platform/frontend/tests/bioxpControlSurfaceCompliance.test.ts`, possibly new `bioxpCockpitInterlinkGate.test.ts`

Required behavior:

- When inactive, cockpit shows a clear `Connect from BIOXP LINK first` message.
- Hardware hooks are disabled while inactive.
- Opening the cockpit does not call `/api/bioxp/status`, `/runtime/status`, or hardware probes.

### Task 7: Remove uncontrolled reset UI

**Objective:** Remove generic/unsafe reset controls from the default BioXP operator surface.

**Files:**
- Modify: `platform/frontend/src/components/BioXpCockpit.tsx`
- Modify: `platform/frontend/tests/bioxpCockpitSafetySurface.test.ts`
- Modify: `platform/frontend/tests/bioxpControlSurfaceCompliance.test.ts`

Required tests:

- Default cockpit text does not include generic `Hard Reset` for robot motion.
- Commissioning surface does not expose motion hard reset.
- Runtime reset/reboot strings appear only in `BioXpInterlinkControlPanel.tsx` and require typed confirmation.

### Task 8: Documentation and operator command helpers

**Objective:** Document the operator flow and optional CLI helpers.

**Files:**
- Create/Modify: `docs/BioXP_Workstation_Interlink.md`
- Optional: extend `scripts/bms` with `bms bioxp-link status/connect/disconnect/runtime-reset/reboot/logs`.

Operator flow:

1. Launch BMS.
2. Topbar shows `BIOXP LINK` gray/inactive.
3. Open panel, confirm saved URL or enter robot URL.
4. Press Connect.
5. Review passive status and maintenance state.
6. If needed, run Diagnostics.
7. If needed, reset robot local runtime with typed confirmation.
8. For full robot OS restart, use `Restart robot OS` with typed confirmation and optional sudo password.
9. After any restart/reboot, reconnect explicitly before OEM startup/motion.

## Validation commands

Backend:

```bash
uv run --directory platform/api python -m pytest \
  tests/test_bioxp_router.py \
  tests/test_bioxp_interlink_router.py \
  tests/test_bioxp_interlink_lifecycle.py \
  tests/test_system_router.py -q
```

Frontend:

```bash
npm run test --prefix platform/frontend -- \
  bioxpInterlinkMenuContract.test.ts \
  bioxpControlSurfaceCompliance.test.ts \
  bioxpCockpitSafetySurface.test.ts
npm run build --prefix platform/frontend
```

Static/smoke:

```bash
python3 -m py_compile platform/api/routers/bioxp.py platform/api/services/bioxp_interlink.py
bash -n scripts/bms
```

Live/local-admin smoke after implementation:

```text
1. Restart BMS API/web.
2. Verify BIOXP LINK is gray/inactive.
3. Verify no robot API access occurs until Connect.
4. Connect and confirm one passive /status probe.
5. Disconnect and confirm polling stops.
6. Trigger Diagnostics and confirm only explicit passive probes run.
7. Attempt runtime reset with wrong ack: rejected.
8. Attempt runtime reset with correct ack on a test double or supervised robot: scoped systemd restart only.
9. Attempt robot reboot with wrong ack: rejected.
10. Confirm generic Hard Reset is absent from the BioXP cockpit.
```

## Acceptance criteria

- BMS restart + robot restart leaves BioXP interlink disconnected/inactive by default.
- Saved URL/profile does not cause BMS to connect or poll.
- Operator can Connect, Disconnect, Forget, Diagnostics, Logs from a top-right `BIOXP LINK` panel.
- Runtime reset is controlled, typed-confirmed, local-admin-gated, password-redacted, and scoped to robot-local runtime service restart.
- Full robot OS restart is controlled, typed-confirmed, local-admin-gated, password-redacted, and never auto-reconnects after a fresh BMS launch.
- No uncontrolled/generic robot reset/hard-reset button remains in the default or commissioning BioXP cockpit surface.
- BMS remains a thin proxy/control plane; robot-local `bioxp-api.service` remains the hardware authority.
- No action in this panel homes, arms, recovers motion, or moves axes.
