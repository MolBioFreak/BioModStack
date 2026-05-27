# BioXP motor current hold RCA — 2026-05-27

## Bottom line

Christian was right: **default full-current holding is not OEM-shaped behavior**. The OEM code distinguishes temporary operation current from low hold/idle current. The Linux robot API had drifted into unsafe semantics by making a raw current route default to full run + full standby current, and by leaving the gripper axis able to stay at high current after gripper service actions.

This was fixed in three places:

1. Robot raw API defaults now fail safe: `run_current=10`, `standby_current=10`.
2. Robot gripper defaults and gripper-clear lifecycle now restore G to hold/idle current after any temporary high-current action.
3. BMS proxy/frontend defaults now use `10/10`, while hot standby above OEM idle requires explicit commissioning override.

No homing or axis movement was used for the final verification; only current-parameter writes and passive status reads were used.

## OEM source comparison

### Gripper has separate max current and hold current

Source: `decompiled_src_bioxpcommon/BioXPCommonLib/DefaultParameters.cs`

- lines 41-43:
  - `G_MOTOR_MAX_CURRENT = 31`
  - `G_MOTOR_HOLD_CURRENT = 10`

Interpretation: `31` exists in OEM source, but it is the max/action current, not the default idle/hold current.

### OEM gripper actions temporarily raise current, then restore hold current

Source: `decompiled_src/BioXPControlLib/ClassControlInterface.cs`

- lines 2055-2069: gripper home calls `setGripperCurrent(31)`, performs home, then for `GripperVersion == 1` calls `setGripperCurrent(10)`.
- lines 2082-2090: gripper move calls `setGripperCurrent(31)`, performs the move, then for `GripperVersion == 1` calls `setGripperCurrent(10)`.
- lines 3234-3238: initialization for `GripperVersion != 0` sets the gripper max current to `10`, not `31`.

Interpretation: OEM behavior for the newer gripper path is explicitly high current during work, low hold current afterward.

### OEM gantry disable drops currents instead of holding full current

Source: `decompiled_src/BioXPControlLib/ClassControlInterface.cs`

- lines 5142-5152: `enableXYZ(false)` sets X/Y/Z max current to `1`.
- lines 5183-5191: `enableXY(false)` sets X/Y max current to `1`.
- lines 5233-5236: `enableYZ(false)` sets Z/Y max current to `1`.

Interpretation: OEM does not use full-current hold as a default disabled/idle mechanism. Full current appears in enable/startup/action paths, not as the default parked state.

### OEM max-current command maps to controller param 6

Source: `decompiled_src_can/ClassCanLib/ClassMotor.cs`

- lines 438-447: `setMaxCurrent()` sends command bytes including type/parameter `6`.
- lines 464-489: `readMaxCurrent()` reads parameter `6`.

Interpretation: Linux TMCL param 6 read/write is the same current concept that OEM calls max current. Param 7 is the standby/idle current path in the Linux driver.

## Linux/BMS issue origin

### Root cause class

The bug was not that OEM ever used `31`. OEM absolutely does use `31` for action current. The bug was that Linux/BMS semantics let `31` become a default hold/standby behavior.

Most likely origin: while reverse-engineering the motor path, the Linux code copied the OEM **max current** value into default route/profile values without preserving the OEM lifecycle that restores low hold current afterward.

### Robot raw API before fix

The robot raw `/motion/axes/current` route had default current semantics equivalent to:

- `run_current = 31`
- `standby_current = 31`

That means an omitted payload could write full current to both active/run and standby/idle current. That is the insane behavior Christian called out.

### Gripper profile/lifecycle before fix

The robot gripper profile and service-operation path had two bad shapes:

1. Gripper profile defaulted toward high `run_current=31` instead of low hold by default.
2. `gripper-clear` set gripper current to `31` before moving but did not source-shape the lifecycle with a guaranteed restore to hold current afterward.

That is how G could end up looking like a full-current hold instead of a temporary action-current state.

### BMS layer before fix

BMS proxy was already safer than the raw robot route on standby current because it defaulted `standby_current=10` and rejected hot standby without override. But BMS/frontend still defaulted `run_current=31`, which was too aggressive as an operator/default control surface.

BMS is now aligned with the robot fix: default route/client current is `10/10`; an explicit high run current can still be requested with standby held at `10`; high standby requires explicit commissioning override.

## Fix applied

### Robot: `/home/molbiofreak/bioxp_re/src/bioxp/api.py`

Patched line anchors from live robot after fix:

- lines 634-645:
  - `OEM_IDLE_STANDBY_CURRENT = 10`
  - `MotionAxisCurrentRequest.run_current = Field(OEM_IDLE_STANDBY_CURRENT, ...)`
  - `MotionAxisCurrentRequest.standby_current = Field(OEM_IDLE_STANDBY_CURRENT, ...)`
  - `operator_ack` + `commissioning_override` fields added for hot standby.
- lines 1701-1710:
  - standby above `10` is rejected unless both `operator_ack` and `commissioning_override` are true.
- lines 1720-1723:
  - standby param 7 is written first, then run/max current param 6 is reasserted.
- lines 2897-2932:
  - `gripper-clear` now sets standby to idle, temporarily sets param 6 to `31` only for the move, and restores both standby and max/hold current in `finally`.

### Robot: `/home/molbiofreak/bioxp_re/src/bioxp/usb_driver.py`

Patched line anchors from live robot after fix:

- lines 104-117:
  - gripper default preset now has `run_current=10`, `standby_current=10`.
- lines 3371-3378:
  - `_motion_oem_gripper_version()` defaults to `1`, matching `ClassBioXPSettings` default and avoiding silent fallback to the older high-current profile.
- lines 3427-3434:
  - gripper operation profile uses `op_current = 31 if startup else 10`, always with `standby_current=10` and `restore_current=10`.

### BMS local repo

- `platform/api/routers/bioxp.py`
  - `_validated_axes_current_payload()` now defaults missing `run_current` to `OEM_IDLE_STANDBY_CURRENT` instead of `OEM_MAX_RUN_CURRENT`.
  - hot standby above `10` still requires explicit commissioning override and operator ack.
- `platform/frontend/src/lib/bioxpClient.ts`
  - `useSetMotionAxesCurrent()` now defaults `runCurrent` to `10` instead of `31`.
- Tests updated:
  - `platform/api/tests/test_bioxp_motion_safety.py`
  - `platform/frontend/tests/bioxpCockpitSafetySurface.test.ts`

## Verification performed

### Robot code validation

- `python3 -m py_compile src/bioxp/api.py src/bioxp/usb_driver.py` passed on robot.
- Robot FastAPI was restarted by terminating exact uvicorn/python PIDs and waiting for `/openapi.json` readiness.

### Robot live no-motion current verification

Direct robot-local checks against `http://127.0.0.1:8123`:

- OpenAPI schema now reports:
  - `run_current.default = 10`
  - `standby_current.default = 10`
  - `operator_ack.default = false`
  - `commissioning_override.default = false`
- Default robot current route for `g` writes only `10/10` and commands no motion:
  - `applied.run_current_param6 = 10`
  - `applied.standby_current_param7 = 10`
  - `motion_commanded = false`
- Explicit hot standby `run_current=31, standby_current=31` is rejected with HTTP `409`.
- Final G status after restore:
  - param 6 / max-current readback = `10`
  - param 7 / standby-current readback = `10`
  - speed = `0`
- Passive multi-axis speed snapshot showed X/Y/Z/G/door all at `0`.

### BMS live container verification

Inside running `biomodstack-api`, without proxying to hardware:

- default payload with `axes=['x']` sanitizes to:
  - `run_current=10`
  - `standby_current=10`
- explicit payload `run_current=31` sanitizes to:
  - `run_current=31`
  - `standby_current=10`
- hot standby `run_current=31, standby_current=31` is rejected before proxy with HTTP `400`.

### Frontend/build/deploy verification

- Frontend type/test command passed: 25/25 tests.
- Frontend production build passed.
- BMS API and web images rebuilt.
- `biomodstack-api` and `biomodstack-web` recreated and reached healthy state.
- Running BMS API container source contains the new idle-default/hot-standby guard.

## Remaining safety caveats

- Controller current readback is not independent proof that physical phase current is flowing. It only proves controller register state.
- High current can still be legitimate during a deliberate motion/startup/gripper action. The fix is not “never use 31”; it is “never use 31 as an implicit parked/standby/default hold.”
- Any future current route must keep the OEM lifecycle: low by default, explicit high current only for an operation, restore low current afterward.

## Current verdict

Fixed and live. The immediate unsafe behavior — default full-current hold/standby semantics — has been removed from robot raw API, gripper lifecycle, BMS proxy defaults, and frontend client defaults.
