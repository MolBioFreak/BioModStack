# BioXP OEM Homing Replacement Design (Phase 2)

No implementation in this phase. This document defines the replacement architecture to be implemented only after review.

## Core design

1. Robot-local raw FastAPI owns all motion semantics.
2. BMS is a thin proxy/operator surface and must display raw route + OEM source mode + parity label.
3. Each OEM mode gets a distinct route and function. No route collapsing.
4. Full monolithic live homing is disabled by default.
5. Dry-run/no-motion route tests come before any live route.
6. Operator ACK validation must occur before provider/USB open.

## Proposed robot modules

```text
src/bioxp/oem_homing_spec.py      # source-derived no-USB programs
src/bioxp/oem_homing_runtime.py   # executor with dry_run, stepwise, artifact capture
src/bioxp/oem_homing_routes.py    # raw FastAPI route definitions or api.py integration
tests/test_oem_homing_spec.py
tests/test_oem_homing_runtime_no_motion.py
tests/test_oem_homing_routes.py
```

## Proposed raw robot routes

```text
GET  /motion/oem/programs
GET  /motion/oem/programs/{program_name}
POST /motion/oem/initialize_motors_without_motion
POST /motion/oem/initialize_motors
POST /motion/oem/initialize_motors/step
POST /motion/oem/initialize_motion
POST /motion/oem/rehome
POST /motion/oem/manual_home
POST /motion/oem/home_axis
POST /motion/oem/home_xy
POST /motion/oem/move_z_home
POST /motion/oem/home_gz
POST /motion/oem/door_search_home
```

## Proposed BMS proxy routes

BMS mirrors the raw route names under `/api/bioxp` and does not rename them into generic Home controls.

## First implementation tranche after approval

1. Add no-USB `oem_homing_spec.py` and tests.
2. Add dry-run program listing routes only.
3. Add BMS proxy for program listing only.
4. Add UI read-only program cards.
5. Commit. Stop for review.

## What is intentionally out of scope until later

- Live motion.
- Enabling monolithic `initialize_motors`.
- Z live homing.
- Manual `/motion/axis/home` re-exposure as safe.
- Tip/pipette physical cleanup.
- `homeGZ` caught-plate physical recovery.

## Hard blocker before robot-local implementation

The robot repo backup-bin from Phase 0 is still pending because this sandbox could not resolve the `bioxp` SSH alias. Before touching `/home/molbiofreak/bioxp_re`, create a robot-local backup artifact.
