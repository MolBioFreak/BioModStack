# BioXP OEM Parity Gap Assessment + Phase-Level Closure Plan — 2026-06-09

> **For Hermes:** Use `subagent-driven-development` only after Christian approves a specific phase. Execute one phase at a time with TDD, source-anchor review, code-quality review, and no live motion unless that phase explicitly permits it.

**Goal:** Convert the Phase 0–11 fresh scaffold audit into a piece-by-piece closure plan for every known gap between (1) original/fresh parity spec, (2) the current fresh scaffold implementation, and (3) the actual decompiled OEM implementation.

**Architecture:** Robot-local FastAPI/runtime remains the single hardware owner. BMS remains a thin proxy/operator surface. Gaps are closed in this order: source truth completeness → config truth → hardware readback truth → no-motion hardware-mutating setup → single-step supervised live motion → wrappers/runtime → BMS cockpit → evidence signoff.

**Tech Stack:** Python/FastAPI robot repo `/home/molbiofreak/bioxp_re`, decompiled OEM C# source under `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup`, BMS repo `/home/dalab/biomodstack/biomodstack`, pytest, JSON/JSONL artifacts, robot-local route verification, BMS read-only/proxy verification.

---

## Current truth label

```text
fresh_oem_parity_scaffold_phase_0_to_11_complete
source_anchored=true
dry_run_routes=true
bms_read_only_inventory=true
live_oem_equivalent=false
physical_homing_proven=false
```

Do not call current state OEM-equivalent live homing. Current work is a correct source/dry-run scaffold foundation.

## Non-negotiable safety boundaries

- No automatic homing.
- No monolithic live `initializeMotors` until all predecessor gates pass.
- No live axis motion without explicit Christian approval for that exact step.
- No `run_homing=true` via old strict-startup path.
- No BMS-owned motion semantics; BMS is proxy/operator UI only.
- No reference marking or `setHome` unless the selected predicate transition is physically/electronically proven for that step.
- ACK validation before provider construction, USB open, or worker activation.
- G idle invariant must be enforced any time G current is touched:

```text
speed == 0
param6_run_current == 10
param7_standby_current == 10
classification == G_CURRENT_IDLE_SAFE
```

---

## Gap inventory

### Gap A — Source oracle completeness is not yet implementation-grade

**What exists:** `docs/oem/oracle/bioxp_oem_source_oracle_20260609.json` has 27 records and 0 missing records.

**What is missing:** The oracle is broad but not yet complete enough for live implementation. Known issue: `manual_home_door` references `ClassControlInterface.btnDHome_Click`, but the Phase 1 oracle lacks a dedicated record for it. Board-level methods (`Class*Board.goHome`, `axisSearchHome`, `doorSearchHome`, `queryHome`, switch polarity, replies/errors/timing) are not fully normalized into executable command contracts.

**Why it matters:** Live parity depends on exact command/reply/timing/predicate behavior, not just high-level method names.

### Gap B — Machine config/calibration is not bound

**What exists:** `src/bioxp/oem_parity_config.py` distinguishes source defaults from real machine calibration and reports `machine_calibrated=false` when `config.xml` is absent.

**What is missing:** Actual machine `config.xml` / calibrated settings source remains unbound. Field-calibrated values for serial, gripper version, camera calibration, axis limits, temperature-door constants, stall thresholds, and table positions are unresolved.

**Why it matters:** OEM code uses settings; source defaults may be wrong for this machine.

### Gap C — Switch predicate matrix is not physically resolved

**What exists:** `src/bioxp/oem_parity_predicates.py` models switch predicates and blocks unknown truth.

**What is missing:** Live per-axis switch truth is not proven by deassert→active transitions. Z has known GAP9/GAP10 conflict history. X/Y had stale/false-active predicate incidents. Door and G need their own readback truth.

**Why it matters:** Homing is unsafe without correct predicate side/polarity/transition proof.

### Gap D — Shadow/readback is a model, not a deployed live audit loop

**What exists:** `src/bioxp/oem_shadow_readback.py` has provider interface/model and tests.

**What is missing:** A live query-only route/artifact that reads axes, speeds, GAP9/GAP10, currents, switch masks, latch/24V, door state, G idle invariant, reference rows, and stale/timeout state without motion.

**Why it matters:** Before any live step, we need a trusted preflight truth artifact and post-step truth artifact.

### Gap E — `initializeMotorsWithoutMotion` is only source-modeled

**What exists:** `initialize_motors_without_motion` is modeled with 17 source steps and dry-run artifacts.

**What is missing:** A controlled live implementation for OEM no-motion setup: wait board, turn off heater, chiller PWM, speed/current/stallguard/switch masks, chiller/TC rates, LED color. It must be hardware-mutating but no-motion, with explicit ACK and rollback/safe-state.

**Why it matters:** OEM startup assumes this setup. But doing it live touches currents/switch masks, so it must be audited and reversible.

### Gap F — `initializeMotors` physical startup sequence is not live implemented in the fresh path

**What exists:** Source order is modeled: Z home; G current high; G clear + home; X home; X setHome; X speed restore; X park 6000; Y home; door home; Y setHome; G restore.

**What is missing:** Stepwise live executor and proof artifacts for each step using fresh scaffold, not old partial `/motion/oem/startup_step`. No monolithic sequence until all step artifacts pass.

**Why it matters:** This is the core physical OEM homing gap.

### Gap G — Manual home modes are modeled but blocked

**What exists:** `manual_home_x/y/z/g/door`, `home_axis`, `home_xy`, `move_z_home`, `home_gz`, `door_search_home` specs exist.

**What is missing:** Each manual route needs exact predicate proof, command mapping, current handling, timeout/runaway stops, and BMS/operator separation before exposure.

**Why it matters:** UI home buttons must not call a dangerous generic route or falsely claim OEM behavior.

### Gap H — `ControlLib.rehome` wrapper is not live equivalent

**What exists:** `rehome` modeled as save thermal-door state → `initializeMotors` → restore door/resume temperature.

**What is missing:** Actual thermal-door state preservation, `doorOpen(thermalDoorOpen)`, resumeTemperature, failure behavior, and artifactized state transitions.

**Why it matters:** OEM rehome is not just homing; it restores app/thermal state.

### Gap I — `ControlLib.initializeMotion` is not live equivalent

**What exists:** `src/bioxp/oem_initialize_motion_scaffold.py` lists blockers for initializeMotors, tip cleanup, thermal door, pipette status, eject tips, pipette initiate/retry, and vision/camera gate.

**What is missing:** Live pipette/tip cleanup parity, thermal-door handling, script-move/location model, pipette group init/status retry, and vision/camera inspection equivalence.

**Why it matters:** Windows app startup/door-close path ends here, not merely in motor homing.

### Gap J — App-level serialized runtime worker is dry-run only

**What exists:** `src/bioxp/oem_fresh_runtime_worker.py` dispatches `fresh_homing_dry_run` and rejects `fresh_homing_live` fail-closed.

**What is missing:** Real serialized command queue/state machine equivalent to `BioXPMainWindow.motion_thread_process` / app command handling with safe cancellation, artifacts, and no USB contention.

**Why it matters:** OEM app does not expose random independent motion routes; it serializes runtime commands.

### Gap K — BMS operator cockpit is inventory-only

**What exists:** BMS proxy/card shows source/dry-run inventory, `BMS THIN PROXY`, `LIVE HOMING BLOCKED`, `USB/MOTION: NO`.

**What is missing:** Operator cockpit for approved live phases: preflight status, exact phase ACK, step selection, artifact links, raw truth, stale/timeout, abort/kill state, and no hidden BMS semantics.

**Why it matters:** Christian needs idiot-proof controls that do not lie or route to wrong endpoints.

### Gap L — Evidence/signoff pack does not exist

**What exists:** Individual tests/routes/reports exist.

**What is missing:** A final compliance matrix and evidence pack tying OEM source lines → Linux fresh implementation → robot-local route → BMS proxy/UI → artifacts → physical/operator proof.

**Why it matters:** Without this, “done” stays ambiguous.

---

## Phase-level plan to assess and close gaps

### Phase G0 — Lock current baseline and assert no-regression gates

**Objective:** Freeze the current scaffold state and create a machine-readable gap ledger before new implementation.

**Motion/USB:** No motion. No USB.

**Files:**
- Create robot: `docs/oem_gap_assessment/baseline_20260609.md`
- Create robot: `docs/oem_gap_assessment/gap_ledger.json`
- Create BMS: `docs/oem/bioxp_oem_parity_gap_assessment_phase_plan_20260609.md` (this file)

**Assessment tasks:**
1. Record robot HEAD and BMS HEAD.
2. Record current fresh modules and route inventory.
3. Record test baseline.
4. Encode Gaps A–L in JSON with owner file, status, required evidence, and stop gate.

**Acceptance gate:**
- Robot focused tests pass.
- BMS proxy still returns `opened_usb=false`, `physical_motion=false`, `live_homing=blocked`.
- No runtime behavior changed.

**Expected status after phase:** assessment baseline complete; no gap closed except planning.

---

### Phase G1 — Complete OEM source oracle to command-contract grade

**Closes/assesses:** Gap A.

**Motion/USB:** No motion. No USB.

**Files:**
- Modify: `docs/oem/oracle/bioxp_oem_source_oracle_20260609.json`
- Create: `docs/oem/oracle/bioxp_oem_command_contracts_20260609.json`
- Modify robot: `src/bioxp/oem_homing_spec.py`
- Tests: `tests/test_oem_homing_spec.py`

**Required additions:**
- Dedicated `ClassControlInterface.btnDHome_Click` source record.
- Board-level source records:
  - `Class*Board.goHome`
  - `Class*Board.axisSearchHome`
  - `Class*Board.doorSearchHome`
  - `ClassMotor.queryLeftSwitchStatus`
  - `ClassMotor.queryRightSwitchStatus`
  - `ClassMotor.setHome`
  - event/reply behavior for target reached/stall/timeout.
- For every program step, record:
  - board/CAN ID,
  - motor index,
  - command family,
  - expected reply/event,
  - sleep/wait/retry behavior,
  - side effects,
  - fail/throw behavior.

**Assessment questions:**
- Which source lines prove each command?
- Which steps remain decompiler-ambiguous?
- Which commands are impossible to map without live traces?

**Acceptance gate:**
- Oracle has all required records.
- Every `OemProgramStep` has source anchor + command contract or explicit blocker.
- Tests fail if any program uses `manual` / vague line tags.

**Output label:**
```text
oem_command_contract_oracle_complete=true
live_behavior_changed=false
```

---

### Phase G2 — Locate/bind machine config and calibration truth

**Closes/assesses:** Gap B.

**Motion/USB:** No motion. No USB. Filesystem/search only.

**Files:**
- Modify robot: `src/bioxp/oem_parity_config.py`
- Create robot: `tests/test_oem_parity_config_machine.py`
- Create docs: `docs/oem_gap_assessment/config_binding_report.md`
- Optional artifact copy path: `artifacts/oem_config_sources/<timestamp>/`

**Assessment tasks:**
1. Search restored SSD/app directories for `config.xml`, `Operation_parameters.xml`, `InspectionSettings.xml`, `config_history.csv`, `ProcessTime.xml`.
2. Hash and copy only non-secret config artifacts.
3. Parse `AxisLimits`, gripper version, serial/calibration flags, camera calibration, temperature/door constants, table positions.
4. If not found, produce explicit negative search evidence.
5. Bind config loader to artifact path only after provenance is clear.

**Acceptance gate:**
- If config exists: `machine_calibrated=true`, source path/hash recorded, tests cover parsed values.
- If absent: report remains `machine_calibrated=false` with exhaustive search evidence; no defaults presented as machine truth.

**Output label:**
```text
machine_config_bound=true|false
source_defaults_only=true|false
```

---

### Phase G3 — Implement live query-only shadow audit route

**Closes/assesses:** Gap D; prerequisite for G4–G10.

**Motion/USB:** USB query-only allowed. No motion. No current mutation. No switch mask mutation.

**Files:**
- Modify robot: `src/bioxp/oem_shadow_readback.py`
- Modify robot: `src/bioxp/oem_homing_routes.py` or `src/bioxp/api.py`
- Tests: `tests/test_oem_shadow_readback.py`, `tests/test_oem_shadow_routes.py`
- Artifact: `/tmp/bioxp-oem-parity/<timestamp>/shadow_readback.json`

**Route target:**
```text
GET /motion/oem/shadow_readback
POST /motion/oem/shadow_readback/capture
```

**Required raw fields:**
- X/Y/Z/G/door position and speed.
- GAP9/GAP10 raw states.
- left/right disabled mask states.
- effective left/right active states.
- run/standby currents param6/param7.
- latch sensor, 24V raw truth, override state.
- reference rows and stale/desynced state.
- API/USB provider freshness and error state.

**Acceptance gate:**
- Artifact reports `motion_commanded=false`.
- If G idle current is unsafe, route returns `ok=false`, classification `G_CURRENT_UNSAFE_HOT_IDLE`, and does not hide raw current truth.
- Query-only route works robot-local; BMS may proxy it read-only after robot proof.

**Output label:**
```text
shadow_readback_live_query_only=true
motion_commanded=false
```

---

### Phase G4 — Resolve switch predicate truth per axis

**Closes/assesses:** Gap C.

**Motion/USB:** Query-only first. Later phases may request tiny supervised diagnostic motion, but this phase defaults no motion unless Christian explicitly approves a substep.

**Files:**
- Modify robot: `src/bioxp/oem_parity_predicates.py`
- Create robot: `src/bioxp/oem_switch_audit.py`
- Tests: `tests/test_oem_switch_audit.py`
- Artifact: `/tmp/bioxp-oem-parity/<timestamp>/switch_predicate_matrix.json`

**Assessment tasks:**
1. For each axis X/Y/Z/G/door, record source predicate from OEM.
2. Record current live raw GAP9/GAP10/masks from shadow route.
3. Classify each predicate:
   - `source_anchored_unverified`
   - `implementation_mapped_only`
   - `live_query_consistent`
   - `live_transition_verified`
   - `conflict_blocked`
4. Explicitly handle known Z GAP9/GAP10 conflict and X/Y false-active history.

**Acceptance gate:**
- No axis is promoted to live homing unless its predicate reaches `live_transition_verified` or a specific no-motion already-at-reference exception is source+operator approved.
- Manual home remains blocked for axes below threshold.

**Output label:**
```text
switch_predicate_matrix_reviewed=true
live_homing_predicates_verified_axes=[...]
blocked_axes=[...]
```

---

### Phase G5 — Live no-motion OEM setup implementation (`initializeMotorsWithoutMotion`)

**Closes/assesses:** Gap E.

**Motion/USB:** USB allowed. Hardware mutation allowed only for no-motion setup. No axis motion.

**Files:**
- Create robot: `src/bioxp/oem_no_motion_setup.py`
- Modify robot: `src/bioxp/oem_fresh_runtime_worker.py`
- Modify robot: `src/bioxp/oem_homing_routes.py` / `src/bioxp/api.py`
- Tests: `tests/test_oem_no_motion_setup.py`

**Route target:**
```text
POST /motion/oem/initialize_motors_without_motion/apply
```

**ACK:**
```json
{
  "operator_ack": "OEM_NO_MOTION_SETUP",
  "reason": "non-empty",
  "artifact_root": "/tmp/bioxp-oem-parity/..."
}
```

**Required behavior:**
- Validate ACK before USB/provider construction.
- Capture shadow before.
- Apply only no-motion OEM setup commands.
- Capture shadow after.
- Enforce G idle invariant after setup.
- Return failed-closed if currents/switch masks unsafe.

**Acceptance gate:**
- All speeds remain 0 before/after.
- No position delta beyond read jitter.
- Artifact shows every source step applied/skipped with command result.
- G current safe at idle.

**Output label:**
```text
initializeMotorsWithoutMotion_live_no_motion_equivalent=proven|blocked
```

---

### Phase G6 — Build fresh stepwise live executor, still blocked by default

**Closes/assesses:** foundation for Gap F/G.

**Motion/USB:** No motion during implementation/tests. Live executor remains blocked until G7.

**Files:**
- Create robot: `src/bioxp/oem_stepwise_executor.py`
- Modify robot: `src/bioxp/oem_stepwise_live_gates.py`
- Tests: `tests/test_oem_stepwise_executor.py`

**Required behavior:**
- Execute exactly one OEM step per request.
- Reject full/plan/all live execution.
- Require phase-specific ACK and artifact root.
- Run shadow preflight before provider construction if request is invalid.
- Never call `setHome` unless step contract says predicate transition proof exists.
- Abort/stop on wrong predicate, speed not zero after timeout, unexpected limit, stale readback, or unsafe G idle.

**Acceptance gate:**
- Unit tests prove bad ACK does not construct provider/USB.
- Unit tests prove full sequence rejected.
- Simulated provider tests cover success/failure artifacts.

**Output label:**
```text
fresh_stepwise_executor_ready_for_supervised_single_step=false_until_G7
```

---

### Phase G7 — Supervised live `initializeMotors` step proof, one step at a time

**Closes/assesses:** Gap F.

**Motion/USB:** Single approved step only. Requires Christian approval per step.

**Files:**
- Modify robot: `src/bioxp/oem_stepwise_executor.py`
- Tests: simulation tests before every live step
- Artifacts: `/tmp/bioxp-oem-parity/<timestamp>/step_<name>.json`

**OEM step order:**
1. `z.axisSearchHome`
2. `g.setMaxCurrent.before_clear`
3. `g.clear.moveSteps`
4. `g.axisSearchHome`
5. `x.axisSearchHome`
6. `x.setHome`
7. `x.setSpeed.restore`
8. `x.park_6000`
9. `y.axisSearchHome`
10. `door.doorSearchHome`
11. `y.setHome.final`
12. `g.restore_current.version1`

**Per-step acceptance gate:**
- Explicit Christian approval for that step.
- Preflight shadow artifact safe.
- Correct step ACK.
- Command artifact includes raw before/during/after, speed, position, switch states, current, masks, interlock truth.
- Operator/camera physical proof when motion occurs.
- Stop immediately on first failed step; do not chain.

**Output label per step:**
```text
step=<id>
source_equivalent=true|false
live_proven=true|false
operator_confirmed=true|false
next_step_allowed=true|false
```

---

### Phase G8 — Manual home and recovery modes, after predicates are proven

**Closes/assesses:** Gap G.

**Motion/USB:** Single supervised manual mode only after predicate proof for that axis.

**Files:**
- Create robot: `src/bioxp/oem_manual_modes.py`
- Tests: `tests/test_oem_manual_modes.py`
- BMS later: only after robot-local proof.

**Modes:**
- `manual_home_x`
- `manual_home_y`
- `manual_home_z`
- `manual_home_g`
- `manual_home_door`
- `home_xy`
- `move_z_home`
- `home_gz`
- `door_search_home`

**Assessment tasks:**
- For each mode, compare source command, speed, current, switch predicate, setHome behavior, restore behavior.
- Build simulated provider tests.
- Only expose live route for modes with proven predicate and safe preflight.

**Acceptance gate:**
- No manual route exposed through BMS until robot-local artifacts prove it.
- UI route names distinguish `Switch Home`, `Zero`, `Rehome`, `InitializeMotion`; no silent rewrite.

**Output label:**
```text
manual_mode_<name>_status=blocked|simulated|robot_local_proven|bms_exposed
```

---

### Phase G9 — Implement `ControlLib.rehome` wrapper parity

**Closes/assesses:** Gap H.

**Motion/USB:** Controlled. Depends on G7 full stepwise proof.

**Files:**
- Create robot: `src/bioxp/oem_rehome_runtime.py`
- Tests: `tests/test_oem_rehome_runtime.py`

**Required behavior:**
- Save thermal door state.
- Call fresh proven `initializeMotors` sequence or approved stepwise sequence.
- Restore door state via equivalent `doorOpen(thermalDoorOpen)` behavior.
- Resume temperature.
- Artifact all state transitions.

**Acceptance gate:**
- Simulation first.
- Robot-local dry-run second.
- Live only after G7 complete and Christian approval.

**Output label:**
```text
ControlLib.rehome_live_equivalent=blocked|simulated|robot_local_proven
```

---

### Phase G10 — Implement `ControlLib.initializeMotion` pipette/vision parity by subphase

**Closes/assesses:** Gap I.

**Motion/USB:** Mostly query/control; motion substeps require explicit approval.

**Files:**
- Create robot: `src/bioxp/oem_initialize_motion_runtime.py`
- Modify robot: `src/bioxp/oem_initialize_motion_scaffold.py`
- Tests: `tests/test_oem_initialize_motion_runtime.py`

**Subphases:**
- G10a: Source command contract for pipette query/eject/init/status retry.
- G10b: Query-only pipette status route and artifact.
- G10c: Thermal door state handling dry-run and query-only proof.
- G10d: Script-move/location model binding; no live movement until source/position proof.
- G10e: Tip cleanup live substep, if physically needed and approved.
- G10f: Vision/camera inspection parity source contract and query-only camera proof.

**Acceptance gate:**
- Each pipette/vision behavior has source line → command contract → simulated provider test → robot-local query/control artifact.
- No silent omission of tip/vision branches.

**Output label:**
```text
initializeMotion_parity_status=blocked|source_contract|query_only|substep_proven|complete
```

---

### Phase G11 — App-level serialized runtime worker

**Closes/assesses:** Gap J.

**Motion/USB:** Initially no motion. Later controlled through proven subcommands only.

**Files:**
- Modify robot: `src/bioxp/oem_fresh_runtime_worker.py`
- Create robot: `src/bioxp/oem_runtime_queue.py`
- Tests: `tests/test_oem_runtime_queue.py`

**Required behavior:**
- Single serialized worker queue equivalent to OEM app `motion_thread_process` behavior.
- Command records include source mode, artifact path, preflight, result, abort/cancel state.
- Prevent USB contention by construction.
- Reject live commands whose underlying phase is not proven.

**Acceptance gate:**
- Tests prove two live commands cannot overlap.
- Tests prove bad ACK fails before provider construction.
- Tests prove worker can dry-run, query-only, and dispatch only approved proven live subcommands.

**Output label:**
```text
oem_runtime_worker_serialized=true
unproven_live_commands_rejected=true
```

---

### Phase G12 — BMS operator parity cockpit, thin proxy only

**Closes/assesses:** Gap K.

**Motion/USB:** Via robot only; no BMS-owned semantics.

**Files:**
- Modify BMS API: `platform/api/routers/bioxp.py`
- Modify frontend: `platform/frontend/src/components/BioXpCockpit.tsx`
- Modify client: `platform/frontend/src/lib/bioxpClient.ts`
- Tests: `platform/frontend/tests/bioxpInterlinkMenuContract.test.ts` plus new cockpit/proxy tests.

**Required UI surfaces:**
- Raw route and OEM source mode.
- Current phase/gap status.
- Last probe timestamp/stale/timeout.
- Axis speeds/positions/currents.
- Switch truth raw/effective.
- Interlock/latch/24V truth + override state.
- Reference state.
- Artifact path links.
- Exact ACK input per step.
- Explicit disabled state explaining unmet blockers.
- Emergency/abort status surface; no hidden actions.

**Acceptance gate:**
- Browser DOM and API tests prove BMS labels itself thin proxy.
- No BMS endpoint mutates robot state except forwarding an approved robot route with same ACK/body.
- UI never shows unproven route as ready.

**Output label:**
```text
bms_operator_cockpit=thin_proxy_truthful
```

---

### Phase G13 — Evidence pack and signoff matrix

**Closes/assesses:** Gap L.

**Motion/USB:** None unless reviewing existing artifacts.

**Files:**
- Create: `docs/oem/bioxp_oem_parity_signoff_matrix_<date>.md`
- Create: `docs/oem/bioxp_oem_parity_signoff_matrix_<date>.json`
- Optional archive: `artifacts/bioxp_oem_parity_signoff_<date>.tar.gz`

**Matrix columns:**
- OEM source symbol and line range.
- Source command contract hash.
- Linux fresh module/function.
- Test file and test name.
- Robot-local route.
- BMS proxy route/UI component.
- Artifact path.
- Physical/operator proof path.
- Current status: `source_only`, `dry_run`, `query_only`, `simulated`, `single_step_live_proven`, `wrapper_proven`, `bms_exposed`, `signed_off`.
- Known deviations and reason.

**Acceptance gate:**
- Every OEM mode has a non-ambiguous status.
- No “done” claim exists without artifact and proof.
- Christian review/signoff can be done from one file.

**Output label:**
```text
oem_parity_signoff_pack_complete=true
```

---

## Recommended execution order

Do not jump straight to motion. The correct next phase is **G0**, then **G1**, then **G2/G3/G4**.

```text
G0 baseline/gap ledger
G1 command-contract oracle
G2 config/calibration binding
G3 live query-only shadow route
G4 switch predicate truth
G5 no-motion setup apply
G6 stepwise executor implementation
G7 supervised initializeMotors live steps
G8 manual modes
G9 rehome wrapper
G10 initializeMotion pipette/vision
G11 serialized runtime worker
G12 BMS operator cockpit
G13 signoff pack
```

## Immediate next action if approved

Start **Phase G0** only:

1. Create robot gap ledger JSON.
2. Record baseline route/test evidence.
3. Commit robot docs only.
4. Do not touch runtime behavior.

Then start **Phase G1** only after G0 passes.
