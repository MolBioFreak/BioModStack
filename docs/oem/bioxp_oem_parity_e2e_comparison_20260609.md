# BioXP OEM Parity End-to-End Comparison — 2026-06-09

## Scope

Comparison requested: original/fresh phase spec vs actual fresh scaffold implementation vs decompiled OEM source implementation. This audit is no-motion/no-homing; it compares files, source anchors, dry-run route behavior, and live-blocking semantics, not physical homing proof.

## Inputs inspected

- Original/fresh phase plan: `docs/oem/bioxp_oem_parity_fresh_attempt_phase_plan_20260609.md`
- Source oracle: `docs/oem/oracle/bioxp_oem_source_oracle_20260609.json`
- Robot fresh scaffold summary from `/home/molbiofreak/bioxp_re` modules and live route behavior
- Decompiled OEM anchors under `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup`
- BMS proxy/card implementation committed as `fb2ba6e`

## Bottom line

**Result: source/dry-run scaffold through Phase 11 matches the approved safety-first plan, but it is not an OEM-equivalent live implementation.**

The scaffold correctly represents the OEM source modes, keeps live execution blocked, provides dry-run artifacts/routes, and surfaces BMS read-only inventory. The actual OEM implementation still does hardware-mutating setup, physical homing, pipette/tip cleanup, vision/door/thermal branches, and machine-calibrated config behavior that the fresh scaffold only models or explicitly blocks.

## Automated requirement checks

- **PASS** — Fresh files exist for Phase 2/3/4/6/7 plus added 8/9/10/11 modules
  - Evidence: implemented 21 fresh files; required base missing=[]
- **PASS** — Source oracle has no missing anchors
  - Evidence: oracle records=27, missing=0
- **PASS** — Robot source specs cover the expected program family
  - Evidence: program_count=14 names=door_search_home, home_axis, home_gz, home_xy, initialize_motion, initialize_motors, initialize_motors_without_motion, manual_home_door, manual_home_g, manual_home_x, manual_home_y, manual_home_z, move_z_home, rehome
- **PASS** — Dry-run runtime proves no USB/no physical motion
  - Evidence: opened_usb=False physical_motion=False
- **PASS** — Fresh worker rejects live execution fail-closed
  - Evidence: {'blockers': ['live_execution_not_enabled_in_fresh_worker', 'requires_stepwise_live_contract'], 'failed_closed': True, 'ok': False, 'opened_usb': False, 'physical_motion': False, 'program': 'initialize_motors', 'worker': 'fresh_oem_parity'}
- **PASS** — Default machine config does not pretend calibrated config.xml is bound
  - Evidence: {'blockers': ['config.xml_not_bound', 'source_defaults_not_machine_calibration'], 'calibration_source': 'source_defaults', 'machine_calibrated': False, 'unknown_keys': [], 'values': {'Calibrated': None, 'CameraCalibrated': None, 'G_GRIPPER_V0_ACCELERATION': 5, 'G_GRIPPER_V0_SPEED': 600, 'G_GRIPPER_V1_ACCELERATION': 20, 'G_GRIPPER_V1_SPEED': 1500, 'G_MOTOR_MAX_POSITION': 15000, 'G_SAFE_IDLE_CURRENT': 10, 'G_STARTUP_HOT_CURRENT': 31, 'GripperVersion': None, 'SerialNumber': None, 'TCDoorStallGuardThreshold': 6, 'TC_DOOR_ACCELERATION': 20, 'TC_DOOR_MAX_CURRENT': 31, 'TC_DOOR_VELOCITY': 50, 'X_MOTOR_ACCELERATION': 350, 'X_MOTOR_MAX_POSITION': 91919, 'X_MOTOR_SPEED': 1700, 'Y_MOTOR_ACCELERATION': 400, 'Y_MOTOR_MAX_POSITION': 95247, 'Y_MOTOR_SPEED': 1800, 'Z_MOTOR_ACCELERATION': 576, 'Z_MOTOR_MAX_CURRENT_DOWN': 25, 'Z_MOTOR_MAX_CURRENT_UP': 31, 'Z_MOTOR_MAX_POSITION': 160000, 'Z_MOTOR_SPEED': 1791}}
- **PASS** — initializeMotion scaffold preserves pipette/vision blockers
  - Evidence: blockers=['pipette_cleanup_not_live_ported', 'vision_inspection_not_oem_equivalent', 'requires_initialize_motors_stepwise_signoff']
- **PASS** — Stepwise live gate requires exact ACK and shadow readback
  - Evidence: {'blockers': ['operator_ack_required'], 'full_sequence_allowed': False, 'live_allowed': False, 'motion_may_be_commanded': True, 'ok': False, 'operator_ack': 'BAD', 'program': 'initialize_motors', 'required_ack': 'OEM_STEPWISE_LIVE', 'requires_clear_path_confirmation': True, 'requires_operator_present': True, 'shadow_readback_summary': {'g_current_invariant': {'classification': 'G_CURRENT_IDLE_SAFE'}, 'ok': True}, 'source_mode': 'physical_startup_homing', 'step_id': 'z.axisSearchHome'}

## Program-by-program comparison

### door_search_home

- OEM source symbol: `ClassControlInterface.doorSearchHome callers`
- Source mode: `door_search_home`
- Modeled steps: 1
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: True
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `door_closed_failure_branch_required`
- Operations modeled: `doorSearchHome`

### home_axis

- OEM source symbol: `ClassControlInterface.HomeAxis`
- Source mode: `generic_home_axis`
- Modeled steps: 5
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: True
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `axis_parameterized_live_route_blocked`
- Operations modeled: `axisSearchHome, axisSearchHome, axisSearchHome, axisSearchHome, doorSearchHome`

### home_gz

- OEM source symbol: `ClassControlInterface.homeGZ`
- Source mode: `caught_plate_gz_recovery`
- Modeled steps: 3
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: True
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `caught_plate_recovery_not_live_proven`
- Operations modeled: `pseudoZHome, goHome, caughtPlateRecovery`

### home_xy

- OEM source symbol: `ClassControlInterface.HomeXY`
- Source mode: `parallel_home_xy`
- Modeled steps: 4
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: True
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `lost_step_result_semantics_required`
- Operations modeled: `setSpeedAcc, goHome, goHome, setSpeedAcc`

### initialize_motion

- OEM source symbol: `ControlLib.initializeMotion`
- Source mode: `app_level_initialize_motion`
- Modeled steps: 3
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: Indirect/inherited only via modeled `initializeMotors`; no live enforcement in this scaffold route.
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `pipette_cleanup_not_ported, vision_inspection_not_ported`
- Operations modeled: `setFlags, initializeMotors, tipPipetteCleanup`

### initialize_motors

- OEM source symbol: `ClassControlInterface.initializeMotors`
- Source mode: `physical_startup_homing`
- Modeled steps: 12
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: True
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `monolithic_live_homing_blocked_until_stepwise_proof`
- Operations modeled: `axisSearchHome, setMaxCurrent, moveSteps, axisSearchHome, axisSearchHome, setHome, setSpeed, moveX, axisSearchHome, doorSearchHome, setHome, setMaxCurrent`

### initialize_motors_without_motion

- OEM source symbol: `ClassControlInterface.initializeMotorsWithoutMotion`
- Source mode: `no_motion_hardware_setup`
- Modeled steps: 17
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: True
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `hardware_mutating_no_motion_requires_ack_before_live`
- Operations modeled: `waitForBoard, turnOffHeater, setChillerPWM, setSpeedAcc, setMaxCurrent, setStallGuardThreshold, setSpeedAcc, setMaxCurrent, setStallGuardThreshold+disableRightSwitch, setSpeedAcc, setMaxCurrent/readMaxCurrent/setStallGuardThreshold, setSpeedAcc/current/stallguard/RDIV/PDIV, setSpeedAcc/current/stallguard/disable switches, setChillerCoolRate, setChillerCoolRate, setTCHeatRate+setTCCoolRate, setColor`

### manual_home_door

- OEM source symbol: `ClassControlInterface.btnDHome_Click`
- Source mode: `manual_door_home`
- Modeled steps: 1
- Source anchors covered in scaffold: Partial — source step is represented, but the Phase 1 oracle did not include a dedicated `btnDHome_Click` record.
- Live default blocked: True
- G-current invariant represented where applicable: True
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `door_predicate_matrix_required`
- Operations modeled: `doorSearchHome`

### manual_home_g

- OEM source symbol: `ClassControlInterface.btnGripperHome_Click`
- Source mode: `manual_button_goHome`
- Modeled steps: 3
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: True
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `manual_g_live_requires_operator_physical_proof`
- Operations modeled: `setMaxCurrent, goHome, setMaxCurrent`

### manual_home_x

- OEM source symbol: `ClassControlInterface.manual_x`
- Source mode: `manual_button_goHome`
- Modeled steps: 1
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: True
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `manual_home_live_requires_switch_predicate_matrix`
- Operations modeled: `goHome`

### manual_home_y

- OEM source symbol: `ClassControlInterface.manual_y`
- Source mode: `manual_button_goHome`
- Modeled steps: 1
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: True
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `manual_home_live_requires_switch_predicate_matrix`
- Operations modeled: `goHome`

### manual_home_z

- OEM source symbol: `ClassControlInterface.manual_z`
- Source mode: `manual_button_goHome`
- Modeled steps: 1
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: True
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `manual_home_live_requires_switch_predicate_matrix`
- Operations modeled: `goHome`

### move_z_home

- OEM source symbol: `ClassControlInterface.MoveZHome`
- Source mode: `distinct_z_home`
- Modeled steps: 2
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: True
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `z_live_predicate_conflict_unresolved`
- Operations modeled: `setMaxCurrent, goHome`

### rehome

- OEM source symbol: `ControlLib.rehome`
- Source mode: `rehome_wrapper`
- Modeled steps: 3
- Source anchors covered in scaffold: True
- Live default blocked: True
- G-current invariant represented where applicable: Indirect/inherited only via modeled `initializeMotors`; no live enforcement in this scaffold route.
- Result: **PASS as source/dry-run scaffold; FAIL as live OEM parity**
- Blockers: `thermal_door_restore_not_live_proven`
- Operations modeled: `saveDoorState, initializeMotors, restoreDoorAndResumeTemperature`

## Critical gaps against actual OEM behavior

- **Not OEM-equivalent live implementation:** Fresh scaffold intentionally does not execute OEM motor/pipette/vision behavior; all programs have live_allowed_default=false and live worker rejects fresh_homing_live.
- **initializeMotorsWithoutMotion is source-modeled but not live-mutating:** OEM actually mutates heater/chiller/current/speed/stallguard/switch masks/LED; fresh scaffold only models steps and blocks hardware mutation.
- **initializeMotors physical sequence not implemented live in fresh scaffold:** OEM performs Z home, G clear/home, X home/setHome/park, Y home, door home, Y setHome, chiller/status effects; fresh scaffold dry-runs only.
- **initializeMotion not live equivalent:** OEM performs initializeMotors plus thermal door/tip/pipette cleanup and status retries; fresh scaffold records blockers.
- **Machine config.xml not bound:** Defaults are source constants with machine_calibrated=false; config.xml field calibration remains unresolved.
- **Switch predicates are matrix/scaffold, not physically resolved:** Predicate matrix exists, but no live per-axis transition proof; manual home remains blocked.
- **Vision/pipette parity only anchored, not ported:** Source oracle anchors exist for pipette/vision methods; fresh implementation only lists blockers/scaffold.
- **BMS is read-only inventory, not operator parity cockpit:** Card shows program inventory and blocked live status only; no live OEM controls.

## Original phase-plan conformance

- Phase 0 backup/quarantine: **PASS** — robot note/commit exists; old partial paths are labelled reference-only.
- Phase 1 source oracle: **PASS** — 27 records, 0 missing anchors.
- Phase 2 fresh no-USB spec: **PASS** — `oem_homing_spec.py` exists and exposes 14 source programs.
- Phase 3 dry-run runtime/artifacts: **PASS** — dry-run artifact has `opened_usb=false`, `physical_motion=false`.
- Phase 4 robot dry-run routes: **PASS** — `/motion/oem/programs`, detail, dry-run route were present/live in prior check.
- Phase 5 BMS read-only proxy/cards: **PASS** — proxy returned `bms_role=thin_proxy_only`, `live_homing=blocked`; UI card labels were present.
- Phase 6 config binding: **PASS for gate, GAP for calibration** — loader distinguishes unbound source defaults; actual machine `config.xml` not bound.
- Phase 7 predicate matrix: **PASS for scaffold, GAP for live truth** — matrix exists; per-axis switch transitions are not physically proven.
- Phase 8 shadow/readback: **PASS for model, GAP for live readback integration** — provider interface/model exists; this audit did not command live probes.
- Phase 9 stepwise live gates: **PASS for contract, not live execution** — bad ACK blocks before live route; future execution still unimplemented.
- Phase 10 initializeMotion scaffold: **PASS for blockers, GAP for actual behavior** — pipette/vision/thermal branches are listed as blockers, not ported.
- Phase 11 fresh worker integration: **PASS for dry-run dispatch/fail-closed live rejection** — worker dry-runs and rejects fresh live command.

## OEM source behavior still not implemented live

Actual OEM source includes these live behaviors that remain unimplemented or blocked in the fresh path:

- `ClassControlInterface.initializeMotorsWithoutMotion`: waits for board, turns off heater, sets chiller PWM, sets X/Y/Z/G/door speed/current/stallguard/switch masks, sets chiller/TC heat/cool rate, LED color.
- `ClassControlInterface.initializeMotors`: physical Z/G/X/Y/door homing sequence, G clear move, X `setHome`, X park to 6000, Y final `setHome`, thermal-door/camera-calibration failure branch, chiller/status side effects.
- `ControlLib.rehome`: saves/restores thermal door state, calls `initializeMotors`, resumes temperature.
- `ControlLib.initializeMotion`: calls `initializeMotors`, handles thermal door, queries tip status, moves to cleanup positions, ejects tips, initializes pipette group, retries pipette status.
- `ClassFrameGrabber`/vision and `ClassPipette*` methods: source-anchored but not ported to live parity.

## Correct status label

Use this label for the current state:

```text
fresh_oem_parity_scaffold_phase_0_to_11_complete
source_anchored=true
dry_run_routes=true
bms_read_only_inventory=true
live_oem_equivalent=false
physical_homing_proven=false
```

Do **not** call it full OEM homing parity. Do **not** call it live-ready OEM initializeMotors. It is the correct fresh scaffold foundation with explicit blockers.
