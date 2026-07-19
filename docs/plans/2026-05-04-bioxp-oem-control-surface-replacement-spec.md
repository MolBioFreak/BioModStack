# BioXP OEM Control-Surface Replacement Implementation Plan

> **SUPERSEDED AS THE CURRENT BMS CONTRACT (2026-07-18):** Retained as historical
> planning evidence only. Do not restore its proxy/hardware routes. See
> [BioXP Compact Control Plane](../BioXP_Compact_Control_Plane.md).

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task after the user approves the tranche. Live hardware validation must stay supervised.

**Goal:** Replace BioModStack's remaining generic/legacy BioXP motor/liquid-handler controls with a thin, truthful operator surface backed by the robot-local OEM-compatible BioXP runtime.

**Architecture:** The robot-local `bioxp-api.service` remains the hardware authority. BMS must not own motor semantics or run a second supervisor; it should proxy explicit robot-local OEM endpoints, display their capability/preflight/readback contracts, and block actuating controls unless linkage, hardware, startup/arming, and operator confirmation gates pass. The frontend should stop presenting raw demo-ish controls as proof of hardware behavior and instead expose OEM startup, motion, deck-position, protocol, liquid, camera, vision, and artifact/readback surfaces from the synced runtime.

**Tech Stack:** FastAPI router in `platform/api/routers/bioxp.py`, React/TanStack Query client in `platform/frontend/src/lib/bioxpClient.ts`, BioXP cockpit UI in `platform/frontend/src/components/BioXpCockpit.tsx`, protocol runner in `platform/frontend/src/components/BioXpProtocolRunner.tsx`, API tests in `platform/api/tests/test_bioxp_router.py`, frontend build/tests via `platform/frontend`.

---

## Ground truth from current source

### Already present in BMS

- BMS backend has a thin BioXP proxy router: `platform/api/routers/bioxp.py`.
- Linkage is persisted outside the repo through `BIOXP_LINKAGE_STATE_PATH` / BMS data root.
- Recommended runtime URL resolves away from `robot` alias; in this lab the operator target should be `http://[REDACTED-ROBOT-HOST]:8123`.
- BMS already proxies several live routes:
  - `/api/bioxp/status` -> robot `/status`
  - `/api/bioxp/motion/power/status` -> robot `/motion/power/status`
  - `/api/bioxp/motion/axis/{axis}/status` -> robot `/motion/axis/{axis}/status`
  - `/api/bioxp/motion/axes/status` -> robot `/motion/axes/status`
  - `/api/bioxp/motion/axis/relative` -> robot `/motion/axis/relative`
  - `/api/bioxp/motion/axis/absolute` -> robot `/motion/axis/absolute`
  - `/api/bioxp/motion/axis/home` -> robot `/motion/axis/home`
  - `/api/bioxp/motion/axes/current` -> robot `/motion/axes/current`
  - `/api/bioxp/motion/arm/strict_startup` -> robot `/motion/arm/strict_startup`
  - `/api/bioxp/liquid/*` -> robot `/liquid/*`
  - `/api/bioxp/protocol/*` -> robot `/protocol/*`
  - camera/vision/thermal/chiller/latch/LED proxies.
- `BioXpCockpit.tsx` is already the right operator surface to refactor, but it is too large and mixes linkage, motion, liquid, camera, thermal, and protocol controls in one file.
- `BioXpProtocolRunner.tsx` should stay the semantic protocol surface rather than raw liquid buttons becoming the preferred workflow.

### Already present in robot runtime after sync

- Robot local source and workstation source were manifest-verified in sync on 2026-05-04.
- Runtime OpenAPI includes:
  - `/motion/oem/startup_step`
  - `/motion/range/status`
  - `/motion/axis/absolute`
  - `/motion/axis/home`
  - `/oem/runtime/...`
  - `/oem/startup/...`
  - `/oem-compat/capabilities/test-prep`
- Current telemetry after restart was X `1000`, Y `0`, Z `-10000`, G `0`, all speed `0`; Z physical position remains operator-confirmation-required.

### Known gap this plan must close

The BMS cockpit still treats low-level motor controls as general controls. We need to switch it to the new system by making BMS consume the robot-local OEM capability matrix and expose controls as:

1. **OEM startup / arming** first.
2. **Read-only status/readback and capability display** always visible.
3. **Supervised motion controls** only after linkage + hardware + startup + operator confirmation gates.
4. **Semantic deck/protocol/liquid controls** preferred over raw axis moves.
5. **Truthful artifact/readback output** after every live command.
6. **No BMS daemon/process start/stop for robot runtime.** Runtime supervision remains robot-local.

---

## Non-negotiable guardrails

- Do not present telemetry-only Z movement as physical success. The UI must label Z as controller-reported unless the command response or operator note includes independent evidence.
- Do not add fake/demo/dry-run outputs to the live operator surface.
- Do not let BMS mutate robot runtime process lifecycle; `/api/bioxp/daemon/start` and `/api/bioxp/daemon/stop` must be explicitly disabled by design, not treated as unavailable only because the current BMS runtime is off. The replacement path is a new instrument-control API surface that links to robot-local services and exposes safe operator actions.
- Do not hide hardware controls only in the frontend if public/internal release profile matters. Backend route registration/capabilities must be profile-gated in a later release-profile tranche if public source release is in scope.
- Every actuating UI command must show target, profile/speed/acc/current when available, preflight state, response, readback, and whether physical evidence is still required.
- Prefer OEM/startup/range/deck/protocol semantics over raw coordinate guessing.
- Motion control defaults should use conservative known profiles. For current live testing, do not silently raise Z current or bypass limit semantics.

---

## Instrument-control API boundary

The new system should be treated as a BMS instrument-control subsystem, not as a BioXP-specific pile of frontend buttons.

### Intended BMS shape

- `platform/api/routers/bioxp.py` can remain the first concrete implementation, but the contract should be instrument-control shaped:
  - linkage/readiness
  - capability discovery
  - status/readback
  - command submission
  - command audit/artifacts
  - emergency stop
- Future naming can either stay under `/api/bioxp/...` for the BioXP instrument or be wrapped by a generic `/api/instruments/{instrument_id}/...` layer once more instruments exist.
- The frontend should consume this as a normal API system with typed hooks and panels, not as ad hoc local process controls.

### Process lifecycle rule

`/api/bioxp/daemon/start` and `/api/bioxp/daemon/stop` must stay disabled even after BMS is running. They are not temporarily failing because BMS is off; they represent an old daemon-supervision model that we are retiring. The live API should link to `bioxp-api.service` on the robot and expose safe instrument actions, not spawn or kill the robot runtime from BMS.

### API contract categories

- **Readiness:** linkage, runtime status, hardware connected, motion arm state, startup state.
- **Discovery:** capabilities, route parity, axis profile matrix, supported OEM protocol/liquid/vision commands.
- **Readback:** axis positions/speeds/switches, range status, reference state, board status, liquid status, camera/vision state.
- **Commands:** OEM startup request/step, strict startup/arming, protocol compile/review/execute, liquid semantic actions, supervised commissioning motion.
- **Safety:** emergency stop, recover, clear fault/lock only where explicitly safe, disabled daemon start/stop.
- **Audit:** command payload, response, readback, artifacts, operator note, snapshot refs, physical-evidence-required flag.

---

## Route replacement map

### Backend proxy additions required now

Add these BMS proxy endpoints because the robot runtime already exposes them and the new cockpit needs them:

- `GET /api/bioxp/motion/range/status` -> robot `/motion/range/status`
- `POST /api/bioxp/motion/oem/startup_step` -> robot `/motion/oem/startup_step`
- `GET /api/bioxp/oem/runtime/status` -> robot `/oem/runtime/status`
- `GET /api/bioxp/oem/runtime/state` -> robot `/oem/runtime/state`
- `GET /api/bioxp/oem/runtime/worker/status` -> robot `/oem/runtime/worker/status`
- `POST /api/bioxp/oem/runtime/recover` -> robot `/oem/runtime/recover`
- `POST /api/bioxp/oem/runtime/emergency_stop` -> robot `/oem/runtime/emergency_stop`
- `GET /api/bioxp/oem/startup/status/latest` -> robot `/oem/startup/status/latest`
- `POST /api/bioxp/oem/startup/request` -> robot `/oem/startup/request`
- `POST /api/bioxp/oem/startup/door_event` -> robot `/oem/startup/door_event`
- `GET /api/bioxp/oem/switch_audit` -> robot `/oem/switch_audit`

Update both dictionaries in `platform/api/routers/bioxp.py`:

- `ROBOT_LOCAL_EXPECTED_ROUTES`
- `BMS_PROXIED_ROUTES`

### Existing backend routes to keep but demote in UI

- `/api/bioxp/motion/axis/relative`
- `/api/bioxp/motion/axis/absolute`
- `/api/bioxp/motion/axis/home`

These remain available for supervised commissioning but should not be the primary handler-control UX.

---

## Frontend target UX

Split `BioXpCockpit.tsx` into smaller panels or at least separate internal sections so the operator flow is explicit:

1. **Connection / runtime truth panel**
   - Linkage URL, recommended URL, runtime reachable, hardware connected.
   - Clear distinction between BMS health and robot-local runtime health.

2. **OEM startup / arming panel**
   - Strict startup state.
   - Latest OEM startup status.
   - Startup request / door event actions.
   - Motion arm status and reason.
   - `motion_arm.armed=false` must block routine motion buttons.

3. **Motion truth / axis status panel**
   - Batch X/Y/Z/G status.
   - Range status.
   - Reference state.
   - Switch state.
   - Board status and 24V state.
   - Explicit warning when Z is controller-reported only.

4. **Supervised commissioning controls**
   - Home axis, absolute, relative remain available but behind a confirmation modal and a commissioning mode affordance.
   - Display exact payload before submit.
   - Always capture/display command response and readback.

5. **Deck/semantic controls**
   - Use OEM capability matrix and protocol/deck targets where possible.
   - Prefer protocol compile/review/execute over ad hoc raw move/liquid actions.

6. **Liquid handler controls**
   - Status first.
   - Tip/init/aspirate/dispense/mix require ACK/readback display.
   - Require referenced axes and startup/arming gates before live liquid operations.

7. **Camera/vision evidence panel**
   - Camera stream/snapshot and barcode/inspection controls stay linked to motion artifacts/evidence.
   - Operator can attach `snapshot_refs` / `operator_note` to live motion commands.

8. **Emergency/stop path**
   - Prominent robot runtime emergency stop route once proxied.
   - Must invalidate all BioXP queries after success/failure.

---

## Implementation tasks

### Task 1: Add missing robot-local OEM proxy routes

**Objective:** Make BMS expose the robot runtime's new OEM system without inventing semantics.

**Files:**
- Modify: `platform/api/routers/bioxp.py`
- Test: `platform/api/tests/test_bioxp_router.py`

**Steps:**
1. Add expected/proxied route entries listed in the route replacement map.
2. Add FastAPI route functions that call `proxy_request(...)` with suitable timeouts.
3. Add tests that monkeypatch `proxy_request` and verify method/path/payload/timeout for each new route.
4. Run:
   ```bash
   cd /home/dalab/biomodstack/biomodstack/platform/api
   uv run --group dev python -m pytest tests/test_bioxp_router.py -q
   ```

### Task 2: Extend `bioxpClient.ts` for OEM runtime/startup/range APIs

**Objective:** Provide typed frontend hooks for the new OEM control system.

**Files:**
- Modify: `platform/frontend/src/lib/bioxpClient.ts`

**Steps:**
1. Add types for range status, OEM startup latest status, OEM runtime status/state, worker status, emergency stop response, startup-step payload/response.
2. Add `useMotionRangeStatus` query.
3. Add `useOemRuntimeStatus`, `useOemRuntimeState`, `useOemRuntimeWorkerStatus` queries.
4. Add `useOemStartupLatest`, `useOemStartupRequest`, `useOemStartupDoorEvent` hooks.
5. Add `useOemStartupStep`, `useOemRuntimeRecover`, `useOemEmergencyStop` mutations.
6. Ensure every mutation invalidates BioXP status, axis batch, range, power, runtime, and startup queries.

### Task 3: Build explicit readiness gate helpers

**Objective:** Prevent the UI from enabling actuating controls when linkage/hardware/startup state is not safe.

**Files:**
- Create or modify: `platform/frontend/src/components/bioxpReadiness.ts`
- Test if frontend test harness exists; otherwise cover with TypeScript build.

**Rules:**
- `linkage_configured !== true` -> block live controls.
- robot runtime unreachable -> block live controls.
- `hardware_connected !== true` -> block live controls.
- `motion_power.motion_arm.armed !== true` -> block routine live motion/liquid controls.
- commissioning mode can expose raw move/home controls but still requires operator confirmation.
- emergency stop is always enabled when linkage is configured.
- Z positive/down or arbitrary Z commands must show `physical_evidence_required` unless the robot response explicitly proves it.

### Task 4: Refactor BioXP cockpit into OEM-first panels

**Objective:** Replace generic control ordering with OEM startup -> readiness -> semantic controls -> supervised raw controls.

**Files:**
- Modify: `platform/frontend/src/components/BioXpCockpit.tsx`
- Optionally create:
  - `platform/frontend/src/components/bioxp/BioXpRuntimePanel.tsx`
  - `platform/frontend/src/components/bioxp/BioXpOemStartupPanel.tsx`
  - `platform/frontend/src/components/bioxp/BioXpMotionTruthPanel.tsx`
  - `platform/frontend/src/components/bioxp/BioXpSupervisedMotionPanel.tsx`
  - `platform/frontend/src/components/bioxp/BioXpLiquidPanel.tsx`
  - `platform/frontend/src/components/bioxp/BioXpEvidencePanel.tsx`

**Acceptance criteria:**
- Operator sees runtime truth and startup/arming state before any motion buttons.
- Raw axis controls are visually labeled as supervised commissioning controls, not normal protocol operation.
- Motion command responses show before/after/readback/artifact/truth fields.
- Z status UI explicitly says controller-reported unless physically confirmed.
- Liquid operations display ACK/readback requirements from `/api/bioxp/capabilities/oem-test-prep` when available.

### Task 5: Add emergency stop and response artifact UX

**Objective:** Make live testing safer and more auditable from BMS.

**Files:**
- Modify: `platform/frontend/src/components/BioXpCockpit.tsx` or new panel component.
- Modify: `platform/frontend/src/lib/bioxpClient.ts`

**Acceptance criteria:**
- Emergency stop calls `/api/bioxp/oem/runtime/emergency_stop`.
- UI invalidates/polls status immediately after the command.
- Last command output panel persists the full response until replaced.
- Operator note/snapshot refs can be sent on motion commands where supported.

### Task 6: Validate live linkage and build

**Objective:** Prove source and running product expose the new control contract.

**Commands:**

```bash
cd /home/dalab/biomodstack/biomodstack/platform/api
uv run --group dev python -m pytest tests/test_bioxp_router.py tests/test_bioxp_protocol_jobs.py -q

cd /home/dalab/biomodstack/biomodstack/platform/frontend
npm run build
```

If the running BMS API is stale, rebuild/recreate the core runtime services after tests pass:

```bash
cd /home/dalab/biomodstack/biomodstack
docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml build bms-api bms-web
docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml up -d --no-deps bms-api bms-web
```

Then verify:

```bash
python3 - <<'PY'
import json, urllib.request
base='http://127.0.0.1:8000'
openapi=json.loads(urllib.request.urlopen(base+'/openapi.json', timeout=5).read())
cap=json.loads(urllib.request.urlopen(base+'/api/bioxp/capabilities', timeout=5).read())
for route in [
    '/api/bioxp/motion/range/status',
    '/api/bioxp/motion/oem/startup_step',
    '/api/bioxp/oem/runtime/status',
    '/api/bioxp/oem/runtime/emergency_stop',
    '/api/bioxp/oem/startup/status/latest',
]:
    print(route, route in openapi.get('paths', {}))
print(cap.get('recommended_url'))
PY
```

---

## Definition of done

- BMS backend proxies the OEM runtime/startup/range routes listed above.
- `/api/bioxp/capabilities` reports the new routes.
- Frontend uses typed hooks for the new routes, not raw axios calls in components.
- BioXP cockpit order is OEM-first: linkage/status -> startup/arming -> truth/readback -> semantic protocol/liquid -> supervised raw moves.
- All actuating controls are gated and show exact payload + response/readback.
- Emergency stop is present and not hidden behind startup/arming gates.
- API tests pass from `platform/api`.
- Frontend production build passes from `platform/frontend`.
- Running BMS OpenAPI contains the new routes after deployment.

---

## Immediate execution recommendation

Do **Task 1 + Task 2 first** as the smallest safe slice. That changes BMS to understand the new robot-local OEM system without yet rearranging the huge cockpit UI. Once those tests/build pass, do the cockpit refactor in Task 3-5.
