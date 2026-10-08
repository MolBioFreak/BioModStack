# BioXP OEM Parity Fresh Attempt Phase Plan — 2026-06-09

> **For Hermes:** Use `subagent-driven-development` only after Christian approves a phase. Implement one phase/slice at a time with RED-GREEN tests, source-anchor review, code-quality review, and no live robot motion unless that phase explicitly permits it.

**Goal:** Build a fresh, source-anchored BioXP OEM parity runtime that can eventually reproduce OEM startup/homing/runtime/job behavior while keeping the existing partial Linux homing work quarantined as evidence, not as the implementation substrate.

**Architecture:** The robot-local FastAPI/runtime remains the single hardware owner. BMS is a thin proxy/operator surface only. The new attempt starts with no-USB/no-motion source models and proof artifacts, then moves through dry-run, shadow/readback, stepwise supervised live validation, and only finally full OEM-equivalent runtime exposure.

**Tech Stack / Surfaces:** Python/FastAPI robot runtime under `/home/molbiofreak/bioxp_re`, decompiled OEM C# assets under `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup`, BMS proxy/frontend under `/home/dalab/biomodstack/biomodstack`, pytest, robot-local JSON/JSONL artifacts, optional BMS read-only cards.

---

## 0. Non-negotiable framing

This is a **fresh OEM parity attempt**.

The existing Linux/OEM-ish homing paths are useful as evidence but are not the new implementation base:

```text
legacy_partial_guarded_reconstruction
reference_only=true
not_oem_equivalent=true
```

Examples of old/legacy surfaces that must not be renamed into success:

```text
/motion/oem/startup_step
/motion/axis/home
/motion/axis/zero
motor_oem_home_axis
motor_oem_rehome
motor_oem_initialize_motion
oem_homing_model.py, unless intentionally promoted/split after review
```

The new attempt must create or consciously supersede these fresh surfaces:

```text
src/bioxp/oem_homing_spec.py
src/bioxp/oem_homing_runtime.py
src/bioxp/oem_homing_routes.py
src/bioxp/oem_parity_artifacts.py
src/bioxp/oem_parity_config.py
src/bioxp/oem_parity_predicates.py

tests/test_oem_homing_spec.py
tests/test_oem_homing_runtime_no_motion.py
tests/test_oem_homing_routes.py
tests/test_oem_parity_artifacts.py
tests/test_oem_parity_config.py
tests/test_oem_parity_predicates.py
```

If the implementer decides to reuse parts of `src/bioxp/oem_homing_model.py`, they must do it by copying/reviewing source-model data into the new modules or by formally renaming/splitting it. Do **not** make the old file silently authoritative.

---

## 1. Safety rules for the whole plan

### 1.1 Live robot restrictions

Until the named live phases below:

- no homing;
- no relative/absolute axis motion;
- no board reset/hard reset unless explicitly approved;
- no `run_homing=true`;
- no monolithic `initializeMotors`;
- no G/gripper movement;
- no USB opening in source/dry-run tests;
- no BMS buttons that imply parity before robot-local proof exists.

### 1.2 Operator gates

Any future live or hardware-mutating route must require all of:

```text
operator_ack: exact phase-specific token
reason/operator_note: non-empty
artifact_root: absolute, allow-listed, writable
mode: explicit dry_run | shadow | stepwise_live | live
```

ACK validation must occur **before** provider construction or USB open.

### 1.3 G-current invariant

The hot-idle current RCA changes the parity contract. Any OEM route/program that touches G/gripper current must include a named safety deviation:

```text
source_behavior: OEM may set G current high for clear/home
linux_safety_deviation: when G speed == 0, restore/sanitize G run+standby current to safe idle
required_safe_state:
  speed: 0
  param6_run_current: 10
  param7_standby_current: 10
  classification: G_CURRENT_IDLE_SAFE
```

Every such route artifact must record:

```text
g_current_before
g_current_after
g_speed_after
g_current_classification
idle_current_restore_attempted
idle_current_restore_ok
```

No route may return success with:

```text
G speed == 0 and (param6 > 10 or param7 > 10)
```

unless it returns `ok=false`, `failed_closed=true`, and `classification=G_CURRENT_UNSAFE_HOT_IDLE`.

### 1.4 Raw truth display rule

Any BMS surface must show raw truth, not just derived parity labels:

```text
raw route
OEM source mode
parity label
last probe timestamp
stale/timeout status
axis speeds
axis currents
switch truth
interlock/latch/24V truth
override state, if any
reference state
artifact path
```

---

## 2. Current state summary

### 2.1 Existing source docs

Local BMS docs already contain a preliminary source/spec package:

```text
docs/oem/bioxp_phase0_current_iteration_freeze_20260609.md
docs/oem/bioxp_oem_homing_call_graph.md
docs/oem/bioxp_oem_motion_constants.md
docs/oem/bioxp_oem_source_to_target_matrix.md
docs/oem/bioxp_oem_homing_replacement_design.md
docs/oem/bioxp_phase3_robot_quarantine_gate_20260609.md
docs/oem/specs/*.md
```

Those docs are directionally correct but not implementation-grade. This plan extends them into a phase-level execution roadmap.

### 2.2 Existing robot scaffold observed

Robot repo currently contains prior/partial files such as:

```text
src/bioxp/oem_homing_model.py
tests/test_oem_homing_source_model.py
tests/test_oem_stepwise_homing_scaffold.py
```

These are useful source-index material. They are not the full fresh scaffold, and they are not sufficient for live OEM parity.

### 2.3 Existing robot dirty state warning

The robot repo may contain many dirty tracked and untracked changes. Before any implementation phase touches robot files, create a backup-bin snapshot and preserve unrelated work.

Required robot backup artifact:

```text
/home/molbiofreak/bioxp_re/backup_bin/oem_parity_fresh_attempt_<timestamp>/
  MANIFEST.md
  git_status.txt
  git_head.txt
  git_diff.patch
  untracked_files.txt
  openapi_routes.json
  src_bioxp_api.py
  src_bioxp_usb_driver.py
  src_bioxp_oem_homing_model.py   # if present
  notes_current_behavior.md
```

---

## 3. Phase overview

| Phase | Name | Motion allowed | USB allowed | Primary output | Stop gate |
|---|---|---:|---:|---|---|
| 0 | Freeze/quarantine | No | No | backup + old/new boundary | backup verified |
| 1 | OEM oracle extraction | No | No | source anchors + executable step tables | source review pass |
| 2 | Fresh no-USB spec modules | No | No | `oem_homing_spec.py` + tests | pytest pass, no USB imports |
| 3 | Dry-run runtime/artifacts | No | No | `oem_homing_runtime.py`, artifact schema | artifact review pass |
| 4 | Robot-local dry-run routes | No | No | `/motion/oem/programs`, dry-run POSTs | OpenAPI + route tests |
| 5 | BMS read-only proxy/cards | No | No | thin proxy + read-only UI | raw truth displayed |
| 6 | Config/calibration binding | No | No | config.xml/default-mode gate | no live constants ambiguity |
| 7 | Switch predicate matrix | No | Shadow only | predicate audit model | per-axis predicates reviewed |
| 8 | Shadow/readback hardware probes | No motion | Yes, query-only | raw current/switch/interlock artifacts | no unsafe state |
| 9 | Stepwise supervised live homing | Single step only | Yes | per-step proof artifacts | physical/operator proof |
| 10 | InitializeMotion pipette/vision parity | Mostly no; later gated | Yes by subphase | pipette/vision source gates | no silent omission |
| 11 | OEM runtime worker integration | Controlled | Yes | app-like runtime state machine | all commands serialized |
| 12 | BMS operator parity cockpit | Controlled | Via robot only | thin operator cockpit | no BMS-owned semantics |
| 13 | Full OEM parity signoff | Controlled | Yes | compliance matrix + evidence pack | Christian review/signoff |

Do not skip phases. A later phase may be split into smaller PRs, but it may not be pulled earlier.

---

## 4. Phase 0 — Freeze/quarantine current iteration

**Objective:** Establish that all old Linux/BMS homing paths are quarantined and that robot state is backed up before fresh work.

**Files:**

- Create on robot: `backup_bin/oem_parity_fresh_attempt_<timestamp>/MANIFEST.md`
- Create local doc update: `docs/oem/bioxp_phase0_current_iteration_freeze_20260609.md` or follow-up note
- Do not modify live runtime behavior.

**Tasks:**

1. On robot, capture repo state:

```bash
cd /home/molbiofreak/bioxp_re
TS=$(date -u +%Y%m%dT%H%M%SZ)
DEST=backup_bin/oem_parity_fresh_attempt_$TS
mkdir -p "$DEST"
git status --short --branch > "$DEST/git_status.txt"
git rev-parse HEAD > "$DEST/git_head.txt"
git diff -- src/bioxp tests docs scripts > "$DEST/git_diff.patch" || true
git ls-files --others --exclude-standard > "$DEST/untracked_files.txt"
cp src/bioxp/api.py "$DEST/src_bioxp_api.py"
cp src/bioxp/usb_driver.py "$DEST/src_bioxp_usb_driver.py"
[ -f src/bioxp/oem_homing_model.py ] && cp src/bioxp/oem_homing_model.py "$DEST/src_bioxp_oem_homing_model.py"
```

2. Dump OpenAPI without live motion:

```bash
curl -fsS http://127.0.0.1:8123/openapi.json > "$DEST/openapi_routes.json"
```

3. Write `MANIFEST.md` containing:

```text
backup timestamp
repo head
dirty status summary
no motion performed
old homing routes quarantined as legacy_partial_guarded_reconstruction
```

**Acceptance gate:** Backup artifact exists and can be listed. No code changes. No robot motion.

---

## 5. Phase 1 — OEM oracle extraction and source truth pack

**Objective:** Turn decompiled OEM source into an explicit source oracle for startup, homing, pipette cleanup, door handling, and runtime state.

**Inputs:**

```text
/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassControlInterface.cs
/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ControlLib.cs
/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_genbotapp/GenBotApp/BioXPMainWindow.cs
/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_bioxpcommon/BioXPCommonLib/ClassBioXPSettings.cs
/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_can/BioXPControlLib/ClassPipette.cs
/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_can/BioXPControlLib/ClassPipetteCollection.cs
/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_vision/CVisionLib/ClassFrameGrabber.cs
```

**Files:**

- Create: `docs/oem/oracle/bioxp_oem_source_oracle_20260609.json`
- Create: `docs/oem/oracle/bioxp_oem_source_oracle_20260609.md`
- Update or supersede: `docs/oem/bioxp_oem_source_to_target_matrix.md`

**Oracle must include:**

```text
source file
symbol
line range
sha256 of line slice
ordered operation ID
operation name
axis/board/motor
params/constants
wait/sleep
branch condition
side effects
failure/throw behavior
parity relevance
```

**Required OEM methods:**

```text
BioXPMainWindow.initializeEnvironment
BioXPMainWindow.initializeSystem
BioXPMainWindow.motion_thread_process
ControlLib.initialCheck
ControlLib.rehome
ControlLib.initializeMotion
ControlLib.parkGantry
ControlLib.startup / PrepareToRunJob deck inspection path
ClassControlInterface.initializeMotorsWithoutMotion
ClassControlInterface.initializeMotors
ClassControlInterface.HomeAxis
ClassControlInterface.HomeXY
ClassControlInterface.MoveZHome
ClassControlInterface.homeGZ
ClassControlInterface manual home button handlers
Class*Board.goHome
Class*Board.axisSearchHome
Class*Board.doorSearchHome
ClassMotor.queryLeftSwitchStatus
ClassMotor.queryRightSwitchStatus
ClassMotor.setHome
ClassPipetteCollection.initiateGroup
ClassPipetteCollection.checkedPipetteStatus
ClassPipetteCollection.ejectAllTips
ClassPipette.QueryTipStatus
ClassPipette.QueryPressure
CVisionLib barcode/cover/pool-plate/deck inspection methods
```

**Acceptance gate:** Source oracle reviewed and line anchors verified by spot-check against decompiled source. No implementation yet.

---

## 6. Phase 2 — Fresh no-USB spec modules

**Objective:** Create source-derived Python data models that describe OEM programs without importing live hardware or old Linux homing helpers.

**Files:**

- Create: `src/bioxp/oem_homing_spec.py`
- Create: `src/bioxp/oem_parity_types.py`
- Create: `tests/test_oem_homing_spec.py`

**Core types:**

```python
@dataclass(frozen=True)
class OemSourceAnchor:
    file: str
    symbol: str
    lines: str
    sha256: str

@dataclass(frozen=True)
class OemProgramStep:
    step_id: str
    source: OemSourceAnchor
    operation: str
    axis: str | None = None
    board: str | None = None
    motor: int | None = None
    params: Mapping[str, object] = field(default_factory=dict)
    wait_ms: int | None = None
    branch_condition: str | None = None
    side_effects: tuple[str, ...] = ()
    failure_modes: tuple[str, ...] = ()
    safety_deviations: tuple[str, ...] = ()

@dataclass(frozen=True)
class OemProgramSpec:
    name: str
    oem_symbol: str
    source_mode: str
    live_allowed_default: bool
    steps: tuple[OemProgramStep, ...]
    required_artifact_fields: tuple[str, ...]
    blockers: tuple[str, ...]
```

**Programs required in Phase 2:**

```text
initialize_motors_without_motion
initialize_motors
manual_home_x
manual_home_y
manual_home_z
manual_home_g
manual_home_door
home_axis
home_xy
move_z_home
home_gz
door_search_home
rehome
initialize_motion
```

**Tests:**

- `initialize_motors` exact order includes Z, G clear/home, X home/park, Y home, door home, Y setHome, G current restore condition.
- `initialize_motors_without_motion` includes heater/chiller/TC/LED setup, not only motor setup.
- `initialize_motion` explicitly blocks on pipette cleanup until implemented.
- `home_gz` and `move_z_home` exist as distinct specs.
- No module imports `usb`, `pyusb`, `BioXpTester`, or old runtime helpers.

**Acceptance gate:**

```bash
PYTHONPATH=src python -m pytest tests/test_oem_homing_spec.py -q
python -m py_compile src/bioxp/oem_homing_spec.py src/bioxp/oem_parity_types.py
```

Expected: pass. No USB access. No motion.

---

## 7. Phase 3 — Dry-run runtime and proof artifact schema

**Objective:** Execute OEM program specs in dry-run mode to produce proof artifacts without USB or motion.

**Files:**

- Create: `src/bioxp/oem_homing_runtime.py`
- Create: `src/bioxp/oem_parity_artifacts.py`
- Create: `tests/test_oem_homing_runtime_no_motion.py`
- Create: `tests/test_oem_parity_artifacts.py`

**Required artifact schema:**

```json
{
  "artifact_format": "bioxp-oem-parity-v1",
  "program": "initialize_motors",
  "mode": "dry_run",
  "source_mode": "ClassControlInterface.initializeMotors",
  "opened_usb": false,
  "physical_motion": false,
  "operator_ack": null,
  "steps_planned": [],
  "steps_executed": [],
  "raw_truth": {
    "axis_speeds": null,
    "axis_currents": null,
    "switches": null,
    "interlocks": null,
    "reference_state": null
  },
  "g_current_invariant": {
    "required": true,
    "classification": "not_applicable_in_dry_run"
  },
  "safety_deviations": [],
  "blockers": [],
  "ok": true
}
```

**Rules:**

- dry-run must never instantiate hardware providers;
- dry-run must never read or write USB/CAN/camera;
- dry-run must preserve source order and branch conditions;
- unsupported/missing program parts must be explicit blockers, not omitted.

**Acceptance gate:** Runtime tests prove `opened_usb=false`, `physical_motion=false`, and artifact JSON validates for every program.

---

## 8. Phase 4 — Robot-local dry-run routes

**Objective:** Expose fresh dry-run/spec routes on robot-local FastAPI without changing live hardware behavior.

**Files:**

- Create: `src/bioxp/oem_homing_routes.py`
- Modify minimally: `src/bioxp/api.py` to include the router
- Create: `tests/test_oem_homing_routes.py`

**Routes:**

```text
GET  /motion/oem/programs
GET  /motion/oem/programs/{program_name}
POST /motion/oem/{program_name}/dry_run
```

Where `{program_name}` initially includes:

```text
initialize_motors_without_motion
initialize_motors
manual_home_x
manual_home_y
manual_home_z
manual_home_g
manual_home_door
home_axis
home_xy
move_z_home
home_gz
door_search_home
rehome
initialize_motion
```

**Do not add live routes in this phase.**

**Route response must include:**

```text
program
source_mode
parity_label
live_allowed=false
opened_usb=false
physical_motion=false
artifact_path if artifact_root supplied
blockers
```

**Acceptance gate:**

- FastAPI route tests pass.
- `/openapi.json` includes only dry-run/spec routes for new scaffold.
- Direct `curl` dry-run on robot returns `opened_usb=false` and `physical_motion=false`.
- No old `/motion/oem/startup_step` route is relabeled as the new implementation.

---

## 9. Phase 5 — BMS read-only proxy and program cards

**Objective:** Let BMS show the new robot-local OEM program inventory without enabling motion or implying parity.

**Files:**

- Modify: `platform/api/routers/bioxp.py`
- Modify: `platform/frontend/src/lib/bioxpClient.ts`
- Modify: `platform/frontend/src/components/BioXpCockpit.tsx`
- Test: BMS API/router tests and frontend contract tests

**BMS routes:**

```text
GET  /api/bioxp/motion/oem/programs
GET  /api/bioxp/motion/oem/programs/{program_name}
POST /api/bioxp/motion/oem/{program_name}/dry_run
```

**UI card must say:**

```text
OEM PARITY: SOURCE MODEL / DRY-RUN ONLY
LIVE HOMING: BLOCKED
BMS ROLE: PROXY ONLY
ROBOT RAW ROUTE: <route>
```

**Acceptance gate:** UI cannot start live homing. UI displays raw truth and artifact path for dry-runs. BMS code contains no OEM motion semantics beyond proxying/formatting.

---

## 10. Phase 6 — Machine config/calibration binding

**Objective:** Bind recovered OEM `config.xml` or explicitly mark source defaults as diagnostic-only.

**Files:**

- Create: `src/bioxp/oem_parity_config.py`
- Create: `config/oem/bioxp_machine_config_binding.json`
- Create: `tests/test_oem_parity_config.py`
- Update: `docs/oem/bioxp_oem_motion_constants.md`

**Required fields:**

```text
SerialNumber
GripperVersion
Calibrated
CameraCalibrated
Z_MOTOR_MAX_CURRENT_UP
Z_MOTOR_MAX_CURRENT_DOWN
Z_MOTOR_STALL_GUARD_THRESHOLD
TC_DOOR_VELOCITY
TC_DOOR_ACCELERATION
TC_DOOR_MAX_CURRENT
TCDoorStallGuardThreshold
axis limits
camera calibration/template availability
```

**Modes:**

```text
machine_config_bound=true      # real recovered config
source_defaults_only=true      # diagnostic only, no live parity claim
```

**Acceptance gate:** Any live/shadow route requiring constants refuses to proceed unless config mode is explicit and artifacted.

---

## 11. Phase 7 — Switch predicate matrix

**Objective:** Define per-axis switch/home predicates before any movement.

**Files:**

- Create: `src/bioxp/oem_parity_predicates.py`
- Create: `tests/test_oem_parity_predicates.py`
- Update: `docs/oem/bioxp_oem_homing_call_graph.md` or create `docs/oem/bioxp_oem_switch_predicate_matrix.md`

**For each axis/program, define:**

```text
axis
board
motor
OEM primitive: goHome | axisSearchHome | doorSearchHome
home switch query source
right/left switch query source
active polarity
required preclear
required deassertion before search
required active condition after search
setHome timing
failure classification
allowed safety deviation
```

**Acceptance gate:** Predicate matrix exists for X/Y/Z/G/door and is reviewed before shadow/live route work.

---

## 12. Phase 8 — Shadow/readback hardware probes

**Objective:** Query hardware truth without commanding motion.

**Files:**

- Create/modify: `src/bioxp/oem_homing_shadow.py`
- Extend: `src/bioxp/oem_homing_routes.py`
- Create: `tests/test_oem_homing_shadow.py`

**Allowed operations:**

```text
read axis speed
read current params 6/7
read position
read switch states
read interlock/latch/24V
read reference state
read camera/pipette availability only where passive
```

**Forbidden operations:**

```text
move
home
setHome
set current
set speed
activate if activation is hardware-mutating beyond passive read, unless separately approved
```

**Acceptance gate:** Shadow artifacts show raw truth and never command motion. If G current is unsafe at speed 0, route must fail closed and call only the already-approved idle-current sanitizer if Christian approves that remediation action.

---

## 13. Phase 9 — Stepwise supervised live homing, one step at a time

**Objective:** Validate single OEM homing steps under operator supervision with artifact capture and rollback.

**Allowed first steps only after Phases 0-8 pass:**

1. `initialize_motors_without_motion` hardware-mutating but no axis motion, with ACK.
2. A single selected low-risk step after physical setup/photo/operator confirmation.

**Files:**

- Extend: `src/bioxp/oem_homing_runtime.py`
- Extend: `src/bioxp/oem_homing_routes.py`
- Create: `tests/test_oem_homing_stepwise_live_contract.py`
- Create scripts only if needed: `scripts/bioxp_supervised_oem_parity_step.sh`

**Route shape:**

```text
POST /motion/oem/{program_name}/stepwise_live
```

**Payload:**

```json
{
  "step_id": "z.axisSearchHome",
  "operator_ack": "OEM_STEPWISE_LIVE",
  "reason": "commissioning supervised step",
  "artifact_root": "/tmp/bioxp-live-runs/...",
  "expected_observation": "...",
  "run_motion": true
}
```

**Step result must include:**

```text
pre raw truth
command issued
controller response
during telemetry if available
post raw truth
position delta
speed seen
switch transition seen
operator observation required=true
physical_motion_confirmed=false unless operator/camera proof supplied
G current invariant result
rollback/safe-state result
```

**Acceptance gate:** One step passes with physical proof and no unsafe current/interlock state. Stop for review after each step.

---

## 14. Phase 10 — `initializeMotion` pipette and vision parity

**Objective:** Close the large app-level gap where OEM `initializeMotion` performs tip/pipette cleanup and machine-state changes.

**Files:**

- Extend/create: `src/bioxp/oem_pipette_collection.py`
- Extend/create: `src/bioxp/oem_vision_inspection.py`
- Extend: `src/bioxp/oem_homing_spec.py`
- Tests:
  - `tests/test_oem_initialize_motion_pipette_parity.py`
  - `tests/test_oem_vision_inspection_contract.py`

**Must model from OEM source:**

```text
queryTipStatus(-1)
TipExist branch
openThermalDoor
scriptmoveTo(locationID 28 -> locationID 6)
updateLocation(6,0)
ejectAllTips(false,true)
moveZ(80000)
moveX(79000)
queryTipStatus retry
TipDirty/TipLoaded state clearing
initiateGroup
checkedPipetteStatus with retry
```

**Vision/deck inspection scope:**

Do not treat camera snapshot success as CVisionLib parity. Implement explicit unavailable/blocker artifacts until barcode/cover/pool-plate/deck inspection semantics are source-bound.

**Acceptance gate:** `initialize_motion` can dry-run the full pipette/vision branch without silent omission. Live remains blocked until pipette CAN ACK/readback and vision artifact contracts are proven.

---

## 15. Phase 11 — OEM runtime worker integration

**Objective:** Move from route-per-operation thinking to OEM app-like runtime ownership.

**Files:**

- Extend: `src/bioxp/oem_runtime_worker.py`
- Extend: `src/bioxp/oem_runtime_commands.py`
- Extend: `src/bioxp/oem_runtime_api.py`
- Tests:
  - `tests/test_oem_runtime_worker.py`
  - `tests/test_oem_runtime_events.py`
  - `tests/test_oem_runtime_api.py`

**Runtime must own:**

```text
initializeSystem
initializeMotion
initialCheck
rehome
PrepareToRunJob
unlockProcess
abortjob
validateJob
wakefrompause
pause/resume
emergency stop state
```

**Rules:**

- all OEM commands serialized through one worker;
- no direct scattered motion endpoints for OEM parity path;
- durable command queue/history/event journal;
- API restart recovery detects incomplete commands;
- door/latch events route through runtime state machine.

**Acceptance gate:** Dry-run/runtime commands produce durable artifacts and fail closed for unimplemented live behavior.

---

## 16. Phase 12 — BMS operator parity cockpit

**Objective:** Expose the proven robot-local OEM runtime safely in BMS without moving semantics into BMS.

**Files:**

- Modify: `platform/api/routers/bioxp.py`
- Modify: `platform/frontend/src/lib/bioxpClient.ts`
- Modify: `platform/frontend/src/components/BioXpCockpit.tsx`
- Add tests under `platform/frontend/tests/` and `platform/api/tests/`

**UI design:**

Cards should be grouped by proof level:

```text
Source model
Dry-run
Shadow/readback
Stepwise live
Runtime/app parity
Blocked/unavailable
```

Every action card must show:

```text
robot raw route
required ACK
artifact root
last artifact
parity status
raw truth
blockers
```

**Acceptance gate:** BMS never displays “OEM ready” unless robot-local route returns proven parity status and fresh raw truth. BMS never owns USB/motion semantics.

---

## 17. Phase 13 — Full OEM parity evidence and signoff

**Objective:** Produce an evidence pack showing which OEM behaviors are equivalent, safety-deviated, unavailable, or blocked.

**Files:**

- Create: `docs/oem/bioxp_oem_parity_compliance_matrix_<date>.md`
- Create artifact folder: `/mnt/BioModStack/bms_results/bioxp_oem_parity/<timestamp>/`

**Compliance statuses:**

```text
source_exact_dry_run
source_equivalent_safety_deviation
shadow_readback_proven
stepwise_live_proven
full_live_proven
blocked_missing_pipette
blocked_missing_vision
blocked_config_unbound
blocked_switch_predicate_unproven
legacy_not_parity
```

**Evidence pack:**

```text
source oracle JSON/MD
dry-run artifacts
shadow artifacts
stepwise live artifacts
G-current invariant artifacts
BMS screenshots/API JSON
test logs
operator observations/photos when live motion is involved
rollback/safe-state proof
```

**Acceptance gate:** Christian reviews and explicitly accepts any named safety deviations from OEM physical behavior.

---

## 18. First approved implementation tranche

If Christian says “start,” do **only** this tranche first:

### Tranche A — no USB, no motion

1. Phase 0 robot backup/quarantine.
2. Phase 1 source oracle extraction.
3. Phase 2 fresh no-USB spec modules.
4. Phase 3 dry-run runtime/artifact schema.
5. Phase 4 dry-run route listing only.
6. Stop for review.

Do not implement BMS buttons or live steps in Tranche A.

### Tranche A files

Robot repo:

```text
src/bioxp/oem_parity_types.py
src/bioxp/oem_homing_spec.py
src/bioxp/oem_parity_artifacts.py
src/bioxp/oem_homing_runtime.py
src/bioxp/oem_homing_routes.py
tests/test_oem_homing_spec.py
tests/test_oem_parity_artifacts.py
tests/test_oem_homing_runtime_no_motion.py
tests/test_oem_homing_routes.py
```

BMS docs repo:

```text
docs/oem/oracle/bioxp_oem_source_oracle_20260609.json
docs/oem/oracle/bioxp_oem_source_oracle_20260609.md
docs/oem/bioxp_oem_parity_fresh_attempt_phase_plan_20260609.md
```

### Tranche A validation commands

On robot:

```bash
cd /home/molbiofreak/bioxp_re
PYTHONPATH=src .venv/bin/python -m py_compile \
  src/bioxp/oem_parity_types.py \
  src/bioxp/oem_homing_spec.py \
  src/bioxp/oem_parity_artifacts.py \
  src/bioxp/oem_homing_runtime.py \
  src/bioxp/oem_homing_routes.py \
  src/bioxp/api.py

PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_oem_homing_spec.py \
  tests/test_oem_parity_artifacts.py \
  tests/test_oem_homing_runtime_no_motion.py \
  tests/test_oem_homing_routes.py \
  -q
```

Expected:

```text
all pass
no USB opened
no physical_motion=true artifacts
```

Safe live route smoke after API include/restart, if approved:

```bash
curl -fsS http://127.0.0.1:8123/motion/oem/programs
curl -fsS -X POST http://127.0.0.1:8123/motion/oem/initialize_motors/dry_run \
  -H 'Content-Type: application/json' \
  -d '{"artifact_root":"/tmp/bioxp-oem-parity-dryrun"}'
```

Expected response fields:

```text
opened_usb=false
physical_motion=false
program=initialize_motors
mode=dry_run
ok=true or ok=false with explicit blockers, never silent success
```

---

## 19. Rollback boundaries

Each phase must be independently revertible.

- Phase 1-3 are docs/source/dry-run only: rollback by removing new files.
- Phase 4 touches robot API include only: rollback by removing router include.
- Phase 5 touches BMS only: rollback BMS proxy/UI without touching robot runtime.
- Phases 8+ may touch hardware: rollback must include safe-state proof and artifacted G-current check.

Never roll back the already-fixed G-current invariant unless Christian explicitly requests a debug branch and the route is isolated from live operation.

---

## 20. Definition of OEM parity

OEM parity is not route-name parity.

A behavior may be called OEM-parity only when all are true:

```text
source anchor exists
ordered operation table exists
branch/failure behavior modeled
machine config source is bound or explicitly defaulted as diagnostic
switch/interlock predicates proven
runtime artifact proves no hidden omissions
BMS displays raw truth and parity label
live behavior, if any, has physical/operator/camera proof
safety deviations are named and accepted
```

Until then, use labels like:

```text
source_model_only
no_usb_dry_run
shadow_readback_only
stepwise_live_commissioning
source_equivalent_with_named_safety_deviation
legacy_partial_guarded_reconstruction
blocked_missing_pipette
blocked_missing_vision
blocked_config_unbound
```

---

## 21. Immediate next action

Recommended next action after this plan:

```text
Execute Tranche A only: Phase 0 backup + Phase 1 oracle + Phase 2/3/4 no-USB dry-run scaffold.
```

Stop after Tranche A and review artifacts before BMS UI or any live hardware work.
