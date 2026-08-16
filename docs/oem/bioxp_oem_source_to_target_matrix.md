# BioXP OEM Source-to-Target Matrix (Phase 2)

> **SUPERSEDED — HISTORICAL DESIGN EVIDENCE ONLY.** The proxy, motion,
> commissioning, and hardware route targets below are not present operator
> contracts and must not be restored from this matrix. The current implemented
> contract is [`../BioXP_Compact_Control_Plane.md`](../BioXP_Compact_Control_Plane.md).

Scope: design-only matrix. No robot code, BMS runtime, or live hardware was modified.

## Status labels

- `exact_port_candidate`: source and target appear structurally one-for-one, pending tests.
- `source_equivalent_with_named_safety_deviation`: intentionally safer than OEM in a named way; not exact physical parity.
- `partial_guarded_reconstruction`: useful current implementation, but not full OEM outcome parity.
- `missing`: no distinct source-mode target route/function exists yet.
- `blocked_unsafe`: must not be live-exposed until predicate/geometry issue is resolved.

## Matrix

### initializeMotorsWithoutMotion

- OEM source: `ClassControlInterface.initializeMotorsWithoutMotion` (ClassControlInterface.cs:3181-3265)
- Current Linux route/function: unknown from this sandbox; validate robot-local
- Current BMS/proxy: partial/adjacent: /api/bioxp/oem-compat/startup/dry-run and runtime surfaces, not proven direct raw mode
- Current status: **missing_or_unproven_direct_port**
- Replacement function: `oem_initialize_motors_without_motion`
- Replacement raw route: `POST /motion/oem/initialize_motors_without_motion`
- Replacement BMS route: `POST /api/bioxp/motion/oem/initialize_motors_without_motion`
- UI target: read-only/dry-run card initially
- Parity label now: **missing**
- Live validation required: False
- Tests required:
  - spec order no-motion
  - route dry_run opens no USB
  - BMS proxy mirrors raw status

### initializeMotors

- OEM source: `ClassControlInterface.initializeMotors` (ClassControlInterface.cs:3348-3421)
- Current Linux route/function: /motion/oem/startup_step exists in BMS proxy; raw implementation not verified in this phase
- Current BMS/proxy: /api/bioxp/motion/oem/startup_step
- Current status: **partial_guarded_reconstruction / stepwise adaptation, not full monolithic OEM parity**
- Replacement function: `oem_initialize_motors_program + oem_initialize_motors_step`
- Replacement raw route: `POST /motion/oem/initialize_motors and POST /motion/oem/initialize_motors/step`
- Replacement BMS route: `POST /api/bioxp/motion/oem/initialize_motors[/step]`
- UI target: stepwise OEM startup card with exact source step and parity label
- Parity label now: **source_equivalent_with_named_safety_deviation**
- Live validation required: True
- Tests required:
  - exact source step order
  - full route disabled by default
  - single-step dry-run trace
  - ACK before provider open

### initializeMotion

- OEM source: `ControlLib.initializeMotion` (ControlLib.cs:8797-8856)
- Current Linux route/function: /motion/oem/initialize_motion exists in BMS proxy list; raw implementation not verified here
- Current BMS/proxy: /api/bioxp/motion/oem/initialize_motion
- Current status: **partial or missing app-level side effects until tip/pipette branch proven**
- Replacement function: `oem_initialize_motion`
- Replacement raw route: `POST /motion/oem/initialize_motion`
- Replacement BMS route: `POST /api/bioxp/motion/oem/initialize_motion`
- UI target: blocked/dry-run until tip-pipette behavior implemented
- Parity label now: **partial_guarded_reconstruction**
- Live validation required: True
- Tests required:
  - tip-present branch modeled
  - eject failure branch modeled
  - no silent omit of pipette status

### rehome

- OEM source: `ControlLib.rehome` (ControlLib.cs:8784-8795)
- Current Linux route/function: /motion/oem/rehome exists in BMS proxy list; raw behavior not verified here
- Current BMS/proxy: /api/bioxp/motion/oem/rehome
- Current status: **partial until thermal door state + resumeTemperature side effects proven**
- Replacement function: `oem_rehome`
- Replacement raw route: `POST /motion/oem/rehome`
- Replacement BMS route: `POST /api/bioxp/motion/oem/rehome`
- UI target: blocked/dry-run until app-state restoration implemented
- Parity label now: **partial_guarded_reconstruction**
- Live validation required: True
- Tests required:
  - save thermalDoorOpen
  - initializeMotors call
  - doorOpen restore
  - resumeTemperature call

### manual goHome buttons

- OEM source: `btnHomeX/Y/Z/G_Click -> board.goHome(...)` (ClassControlInterface.cs:2046-2075,2262-2373)
- Current Linux route/function: /motion/axis/home and possibly generic manual helpers; raw robot not verified here
- Current BMS/proxy: /api/bioxp/motion/axis/home
- Current status: **unsafe/ambiguous for OEM parity; known route/predicate incidents**
- Replacement function: `oem_manual_go_home(axis)`
- Replacement raw route: `POST /motion/oem/manual_home`
- Replacement BMS route: `POST /api/bioxp/motion/oem/manual_home`
- UI target: Manual OEM Home card, disabled until predicate matrix proven
- Parity label now: **blocked_unsafe**
- Live validation required: True
- Tests required:
  - X/Y speed 500
  - Z speed 1791
  - G speed versioned 600/200
  - door uses doorSearchHome not goHome

### HomeAxis

- OEM source: `ClassControlInterface.HomeAxis` (ClassControlInterface.cs:4997-5052)
- Current Linux route/function: not proven direct; may be conflated with /motion/axis/home
- Current BMS/proxy: not distinct
- Current status: **missing distinct route**
- Replacement function: `oem_home_axis(axis)`
- Replacement raw route: `POST /motion/oem/home_axis`
- Replacement BMS route: `POST /api/bioxp/motion/oem/home_axis`
- UI target: Advanced source-mode card
- Parity label now: **missing**
- Live validation required: True
- Tests required:
  - axis-specific current/stall/speed
  - door special branch
  - not equal manual_home

### HomeXY

- OEM source: `ClassControlInterface.HomeXY` (ClassControlInterface.cs:5054-5070)
- Current Linux route/function: /motion/oem/home_xy exists in BMS proxy list; raw behavior not verified here
- Current BMS/proxy: /api/bioxp/motion/oem/home_xy
- Current status: **unproven; may be partial**
- Replacement function: `oem_home_xy`
- Replacement raw route: `POST /motion/oem/home_xy`
- Replacement BMS route: `POST /api/bioxp/motion/oem/home_xy`
- UI target: blocked until dry-run/order proof
- Parity label now: **partial_guarded_reconstruction**
- Live validation required: True
- Tests required:
  - 200/200 setup
  - parallel goHome false X/Y 200
  - restore X 1700/350 Y 1800/400
  - lost-step deltas

### MoveZHome

- OEM source: `ClassControlInterface.MoveZHome` (ClassControlInterface.cs:4623-4632)
- Current Linux route/function: not distinct/proven; Z adaptations exist from incidents
- Current BMS/proxy: not distinct
- Current status: **blocked due Z source-vs-live predicate conflict**
- Replacement function: `oem_move_z_home`
- Replacement raw route: `POST /motion/oem/move_z_home`
- Replacement BMS route: `POST /api/bioxp/motion/oem/move_z_home`
- UI target: blocked with Z predicate warning
- Parity label now: **blocked_unsafe**
- Live validation required: True
- Tests required:
  - sets Z max current
  - calls goHome Z 1791
  - does not silently use absolute zero

### homeGZ

- OEM source: `ClassControlInterface.homeGZ` (ClassControlInterface.cs:4657-4687)
- Current Linux route/function: not distinct/proven
- Current BMS/proxy: not exposed
- Current status: **missing caught-plate recovery chain**
- Replacement function: `oem_home_gz`
- Replacement raw route: `POST /motion/oem/home_gz`
- Replacement BMS route: `POST /api/bioxp/motion/oem/home_gz`
- UI target: not exposed until implemented
- Parity label now: **missing**
- Live validation required: True
- Tests required:
  - pseudo Z home
  - delay semantics
  - G goHome
  - caught-plate X home/solenoid/latch false/throw

### doorSearchHome

- OEM source: `doorSearchHome callers / thermal board` (ClassControlInterface.cs:1224-1246,3380-3387,4997-5052)
- Current Linux route/function: door-home startup step possibly exists; raw direct not verified
- Current BMS/proxy: through startup_step only, not direct source mode
- Current status: **partial/unproven**
- Replacement function: `oem_door_search_home`
- Replacement raw route: `POST /motion/oem/door_search_home`
- Replacement BMS route: `POST /api/bioxp/motion/oem/door_search_home`
- UI target: door OEM home card disabled until dry-run proof
- Parity label now: **partial_guarded_reconstruction**
- Live validation required: True
- Tests required:
  - doorSearchHome velocity/stallguard
  - closed preclear where source requires
  - confirm closed failure/open/throw path

## Phase 2 conclusion

The current exposed surfaces are not sufficient for a 1:1 OEM claim. The replacement must introduce/source-map distinct robot-local modes first, then expose only reviewed modes through BMS. Generic `/motion/axis/home` must not be treated as OEM startup, manual, HomeAxis, HomeXY, MoveZHome, or homeGZ parity.
