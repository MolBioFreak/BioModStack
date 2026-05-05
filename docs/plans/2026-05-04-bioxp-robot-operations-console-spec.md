# BioXP Robot Operations Console Spec

> **For Hermes:** Use subagent-driven-development skill to implement approved slices task-by-task.

**Goal:** Build a usable BMS BioXP service/commissioning console on top of the robot-local BioXP API, exposing named safe operations instead of buried raw primitives.

**Architecture:** The robot-local FastAPI service remains the hardware/runtime owner. BMS remains a web/API cockpit and proxy: it links to the robot runtime, presents gated operations, records evidence, and never owns the robot systemd process. High-risk motion is exposed as named service recipes with explicit operator acknowledgement and physical-observation fields, while raw controls stay in a commissioning-only tab.

**Tech Stack:** Robot-local FastAPI (`/home/molbiofreak/bioxp_re/src/bioxp/api.py`), BMS API proxy (`platform/api/routers/bioxp.py`), React/TypeScript cockpit (`platform/frontend/src/components/BioXpCockpit.tsx`), React Query client (`platform/frontend/src/lib/bioxpClient.ts`).

---

## Current live robot API surface

Robot-local OpenAPI was queried from `http://127.0.0.1:8123/openapi.json` over SSH on 2026-05-05.

Relevant available primitives:

- Motion/power/reference:
  - `GET /motion/power/status`
  - `POST /motion/power/enable`
  - `POST /motion/power/diag`
  - `POST /motion/arm/strict_startup`
  - `POST /motion/interlock/prepare`
  - `POST /motion/clear_lock`
  - `POST /motion/hard_reset`
  - `GET /motion/range/status`
  - `GET /motion/axes/status`
  - `GET /motion/axis/{axis}/status`
  - `POST /motion/axis/relative`
  - `POST /motion/axis/absolute`
  - `POST /motion/axis/home`
  - `GET /motion/reference/status`
  - `POST /motion/reference/mark_referenced`
  - `POST /motion/reference/mark_desynced`
  - `POST /motion/axes/current`
- Latch/interlock:
  - `GET /latch/status`
  - `POST /latch/lock`
  - `POST /latch/unlock`
- OEM runtime/startup:
  - `POST /oem/initial_check`
  - `POST /oem/startup/request`
  - `GET /oem/startup/status/latest`
  - `GET /oem/startup/status/{session_id}`
  - `POST /oem/startup/door_event`
  - `GET /oem/runtime/status`
  - `GET /oem/runtime/state`
  - `GET /oem/runtime/worker/status`
  - `POST /oem/runtime/recover`
  - `POST /oem/runtime/emergency_stop`
  - `POST /oem/runtime/commands/*`
  - `GET /oem/runtime/events/latest`
- Liquid/thermal/chiller/camera/vision are also present and should remain separated from gantry commissioning unless a named recipe intentionally uses them.

## Current BMS state and gap

Source already contains partial wiring:

- `platform/frontend/src/lib/bioxpClient.ts`
  - `usePrepareInterlock()` -> `/api/bioxp/motion/interlock/prepare`
  - `useMotionPowerStatus()` -> `/api/bioxp/motion/power/status`
  - `useMotionPowerEnable()` -> `/api/bioxp/motion/power/enable`
  - `useMotionPowerDiag()` -> `/api/bioxp/motion/power/diag`
  - `useMotionArmStrictStartup()` -> `/api/bioxp/motion/arm/strict_startup`
  - `useMotionHardReset()` -> `/api/bioxp/motion/hard_reset`
  - `useClearLock()` -> `/api/bioxp/motion/clear_lock`
  - `useMoveRelative()` / absolute / home -> raw axis routes
  - `useLatchLock()` / unlock -> latch routes
- `platform/frontend/src/components/BioXpCockpit.tsx`
  - has hidden commissioning controls, raw axis cards, latch controls, motion power/recovery panel, and camera jog.

But the live operator experience is inadequate:

1. BMS can show robot unreachable even when SSH/robot-local API works, disabling/hiding controls.
2. Useful service actions are hidden behind a single commissioning toggle rather than presented as named workflows.
3. There are raw X/Y/Z controls, but not operator-friendly named recipes such as “clear head”, “interlock from zero”, or “full sweep validation”.
4. There is no consistent operation-result record requiring physical confirmation versus controller-only telemetry.
5. High-risk actions are not grouped by risk/intent, so the UI feels both too limited and too scary.

## Safety boundary

Non-negotiable:

- BMS must not start/stop/restart the robot-local `bioxp-api.service` during normal operator use.
- BMS must label controller deltas as controller-only unless independently observed.
- BMS must not imply dry-runs or controller responses prove physical motion.
- Raw motion remains commissioning-only.
- Named service recipes must require explicit operator acknowledgement and a clear physical-precondition checklist.
- Physical observation beats telemetry.

## Feature requests and realistic assessment

### 1. Fix BMS-to-robot linkage and capability truth

**Request:** BMS should reliably know whether the robot runtime is reachable and which controls are available.

**Realistic:** Yes, high priority, mostly software.

**Implementation:**

- Keep `/api/bioxp/linkage`, `/api/bioxp/status`, `/api/bioxp/daemon/status`, `/api/bioxp/capabilities` as the BMS truth surface.
- Add a short-timeout `GET /api/bioxp/operations/capabilities` that queries robot OpenAPI once, caches route presence, and returns per-operation availability.
- Separate:
  - BMS API health
  - robot runtime reachability
  - hardware connectivity
  - operation availability
- Add UI banner when BMS proxy cannot reach robot but the linkage URL is configured.

**Acceptance:** BMS shows exactly which layer failed instead of “not reachable” ambiguity.

### 2. Robot Operations landing panel

**Request:** One obvious screen for useful robot controls.

**Realistic:** Yes, mostly frontend plus light API aggregation.

**Implementation:** Add a new panel under BioXP Handler:

- `Runtime Linkage`
- `Operator Startup`
- `Service Operations`
- `Commissioning Motion`
- `Camera Feed`

`Service Operations` should show named cards:

- Power / arm
- Latch / interlock
- Head clearance
- Sweep validation
- Reference state
- Emergency/recovery

**Acceptance:** Christian can see the intended robot actions without hunting for hidden raw controls.

### 3. Lock engage/disengage controls

**Request:** Ability to lock/unlock from BMS.

**Realistic:** Already possible as primitives; UI exposure is easy. Safety gating required.

**Robot API:**

- `GET /latch/status`
- `POST /latch/lock`
- `POST /latch/unlock`

**BMS changes:**

- Add `Latch & Interlock` service card.
- Require acknowledgement: “operator confirms no hands/tools in latch/door area”.
- Display before/after `latch/status` and deck IO.

**Risk:** Low-to-medium. Mechanical lock action can pinch; keep confirmation and readback.

### 4. Enable 24V / prep axes / strict startup

**Request:** Ability to power/arm the motion path from UI/API.

**Realistic:** Already supported by robot API; BMS UI needs a guided sequence.

**Robot API:**

- `GET /motion/power/status`
- `POST /motion/power/enable`
- `POST /motion/interlock/prepare`
- `POST /motion/arm/strict_startup` with `run_homing=false` default
- `POST /motion/power/diag`

**BMS operation:** `POST /api/bioxp/operations/motion/prepare-safe`

Suggested sequence:

1. Read `/status`, `/motion/power/status`, `/latch/status`, `/motion/axes/status`.
2. Confirm all speeds are zero.
3. Enable 24V if needed.
4. Prepare interlock.
5. Run strict startup with `run_homing=false`.
6. Re-read power/arm/latch/axes.
7. Return a single operation report.

**Risk:** Medium. Hardware-energizing, no intentional axis movement. Requires operator clear-path acknowledgement.

### 5. Clear head / clear lock operation

**Request:** One obvious “clear head” / “clear lock” button.

**Realistic:** Supported by robot API and historically useful. Must be treated as live movement.

**Robot API:**

- `POST /motion/clear_lock`

**BMS operation:** `POST /api/bioxp/operations/head/clear-lock`

**UI copy:** “Lift head via configured clearance primitive. This is live Z/head motion; operator must watch and stop if direction is wrong.”

**Acceptance:** Shows before/after Z position, result payload, controller-only caveat, and physical confirmation prompt.

**Risk:** Medium-to-high because it moves Z/head. Still more realistic and safer than arbitrary raw Z controls because the robot API already has the specific clearance primitive.

### 6. Small head lift increments

**Request:** Controlled incremental head clearing, not only one full action.

**Realistic:** Partially. The API has generic Z relative moves, but not currently a first-class “head lift increment” endpoint. Better to add a robot-local named endpoint or BMS recipe that calls `POST /motion/axis/relative` with strict limits.

**Option A:** BMS-only recipe over existing relative move.

- Use axis `z`
- Negative/up direction based on current robot convention
- Fixed steps: 500, 1000, 2500
- Always `reuse_prepared=false`
- Capture evidence bundle

**Option B, preferred:** robot-local endpoint:

- `POST /motion/head/lift_increment`
- request: `{ steps_abs: 2500, ensure_interlock: true, operator_ack: ... }`
- response: same motion-truth payload plus clearance semantics

**Risk:** Medium-to-high. Accomplishable, but should not be hidden as “just relative Z”.

### 7. Full sweep moves / travel validation

**Request:** Full X/Y sweeps and defined movement tests.

**Realistic:** Technically possible, but should be staged after small proof moves and reference-state repair. Not first-day UI.

**Available primitives:**

- `/motion/range/status`
- `/motion/axes/status`
- `/motion/axis/relative`
- `/motion/axis/absolute`
- `/motion/reference/status`

**Needed before safe full sweep:**

- trusted reference state for target axes
- explicit software bounds from `/motion/range/status`
- limit-switch interpretation verified
- operator camera/physical observation
- single-axis micro-move proof
- stop/abort path understood

**Recommended operation endpoints:**

- `POST /api/bioxp/operations/motion/micro-move-proof`
- `POST /api/bioxp/operations/motion/sweep-axis`
- `POST /api/bioxp/operations/motion/sweep-xy-envelope`

**Risk:** High. Accomplishable, but only after readiness gates. Do not expose as casual button.

### 8. Interlock-from-zero / return-to-safe-position

**Request:** Defined movements from 0 or to known safe poses.

**Realistic:** Partially. X/Y reference currently may be valid; Z/G/door may be desynced. Absolute “safe positions” are not safe unless reference state is trusted.

**Implementation path:**

- Add read-only pose panel first:
  - current controller position
  - reference state
  - last motion kind
  - range distance-to-min/max
- Only enable absolute safe-pose recipes when all required axes are `referenced`.
- For desynced axes, expose only supervised relative micro-moves or homing/re-reference workflows.

**Risk:** Medium-to-high. Avoid “absolute coordinate” promises until reference state is proven.

### 9. Mark referenced/desynced from UI

**Request:** Ability to correct reference truth when operator physically verifies position.

**Realistic:** Already supported by API; needs better UI affordance.

**Robot API:**

- `GET /motion/reference/status`
- `POST /motion/reference/mark_referenced`
- `POST /motion/reference/mark_desynced`

**UI:**

- Show per-axis rows.
- Require reason/operator text for mark referenced.
- Allow per-axis, not only all axes.
- Strong warning: marking referenced does not move robot.

**Risk:** Medium because bad reference state can make future absolute moves dangerous.

### 10. Emergency stop / abort / pause / resume

**Request:** Operator needs obvious stop/recovery controls.

**Realistic:** OEM runtime has endpoints. Motion primitive abort semantics need verification before promising true motor stop.

**Robot API:**

- `POST /oem/runtime/emergency_stop`
- `POST /oem/motion_worker/abort`
- `POST /oem/runtime/events/pause`
- `POST /oem/runtime/events/resume`

**Implementation:**

- Add a red “Runtime emergency stop” control if route is available.
- Add route-specific labels explaining what is stopped: OEM runtime queue vs active low-level motor move.
- Do not claim it is a physical E-stop replacement.

**Risk:** Medium. Useful, but semantics must be validated.

### 11. Physical confirmation and proof bundles

**Request:** The UI should reflect whether moves truly happened.

**Realistic:** Yes for operator-confirmed evidence; camera/fiducial proof is later.

**Implementation:**

- Every named motion operation returns:
  - operation id
  - route(s) called
  - before snapshots
  - after snapshots
  - controller delta
  - speed/settle info
  - `physical_motion_confirmed: null | true | false`
  - `operator_observation_note`
  - artifact refs
- UI asks after motion: “Did you observe physical movement?” with Yes/No/Unsure.

**Risk:** Low. This improves honesty immediately.

### 12. BMS proxy operation API

**Request:** Build upon the new API rather than make the frontend call low-level routes everywhere.

**Realistic:** Yes and recommended.

**New BMS API prefix:** `/api/bioxp/operations/*`

Suggested endpoints:

- `GET /api/bioxp/operations/capabilities`
- `GET /api/bioxp/operations/readiness`
- `POST /api/bioxp/operations/latch/lock`
- `POST /api/bioxp/operations/latch/unlock`
- `POST /api/bioxp/operations/motion/prepare-safe`
- `POST /api/bioxp/operations/head/clear-lock`
- `POST /api/bioxp/operations/head/lift-increment`
- `POST /api/bioxp/operations/motion/micro-move-proof`
- `POST /api/bioxp/operations/motion/sweep-axis`
- `POST /api/bioxp/operations/reference/mark`
- `POST /api/bioxp/operations/emergency-stop`

These can initially be BMS orchestration wrappers over existing robot-local primitives. Longer term, high-risk recipes should move robot-local so the hardware owner enforces sequencing even if a different client calls it.

## Proposed implementation phases

### Phase 0: Repair reachability truth

Objective: make BMS correctly report robot linkage/reachability and route availability.

Files:

- Modify `platform/api/routers/bioxp.py`
- Modify `platform/frontend/src/lib/bioxpClient.ts`
- Modify `platform/frontend/src/components/BioXpCockpit.tsx`
- Add tests in `platform/api/tests/test_bioxp_router.py`
- Add frontend source-level tests under `platform/frontend/tests/`

Acceptance:

- BMS distinguishes API up, proxy unreachable, robot status timeout, and route missing.
- No controls disappear solely because one long-polling hardware endpoint hangs.

### Phase 1: Service Operations panel, no new motion recipes

Objective: expose existing primitives as named, gated cards.

Include:

- latch lock/unlock
- enable 24V / prepare interlock
- strict startup no homing
- clear head lock
- driver power diag
- reference status/marking

Acceptance:

- The user can find these actions without raw-axis hunting.
- All mutating actions require explicit acknowledgement.
- Results show before/after snapshots and controller-only caveat.

### Phase 2: BMS operation-wrapper API

Objective: create orchestration endpoints so the UI calls named operations instead of raw primitives.

Acceptance:

- `GET /api/bioxp/operations/readiness` returns one coherent readiness object.
- `POST /api/bioxp/operations/motion/prepare-safe` performs the no-motion power/interlock/arm sequence and records evidence.
- `POST /api/bioxp/operations/head/clear-lock` wraps the clear-lock route and evidence.

### Phase 3: Incremental head-lift and micro-move proof

Objective: add bounded movements with physical confirmation.

Acceptance:

- Z/head lift increments are limited and direction-labeled.
- X/Y/Z micro-move proof requires active operator confirmation.
- UI records whether physical movement was observed.

### Phase 4: Full sweep validation

Objective: add full sweep operations only after references and micro-move proof pass.

Acceptance:

- Sweep button is disabled unless readiness gates pass.
- Sweep has dry-plan preview of intended waypoints.
- Sweep records before/after/status/artifacts and requires observation confirmation.

## Feasibility summary

- BMS linkage/capabilities: **high confidence, software only**.
- Lock/unlock UI: **high confidence**, existing API.
- Enable 24V / prep / strict startup UI: **high confidence**, existing API, hardware-energizing.
- Clear head lock: **high confidence**, existing API, live movement risk.
- Incremental head lift: **medium confidence**, best with new named robot endpoint or carefully bounded BMS wrapper.
- Raw movement controls: **already exists**, but needs better discoverability/gating.
- Full sweeps: **medium confidence technically, high safety sensitivity**; do after micro-move/reference gates.
- Absolute safe positions: **conditional** on reference state; unsafe if axes are desynced.
- Physical proof: **operator-confirmation feasible now**; camera/fiducial proof later.

## Definition of done

- Live BMS `/bms/` shows the new operations panel.
- BMS API OpenAPI includes `/api/bioxp/operations/*` endpoints.
- Robot route availability is reflected from live OpenAPI/capabilities, not hardcoded assumptions only.
- Mutating controls are gated by acknowledgement and disabled when route/readiness is missing.
- Motion operations report controller-only vs physically confirmed status honestly.
- API tests pass from `platform/api`.
- Frontend tests and build pass from `platform/frontend`.
- Stable core-runtime `bms-api` and `bms-web` are rebuilt/recreated and verified live before claiming user-visible completion.
