# BioXP hot gripper current recurrence RCA — 2026-06-09

## Trigger

Christian reset the robot and physically felt the gripper motor was again **incredibly hot**, indicating the gripper was likely being held at high/full current while idle.

This report is based on:

- Christian's operator physical observation of the hot gripper motor.
- Prior motor-current RCA report: `docs/reports/bioxp-motor-current-hold-rca-2026-05-27.md`.
- OEM source anchors from decompiled `ClassControlInterface.cs`.
- Current inability to reach robot SSH/API from this environment.

No live robot motion was performed during this RCA. No POST/arming/current-write was performed.

## Current access limitation

Read-only robot checks attempted after the report:

```text
robot -> SSH timeout to [REDACTED-ROBOT-HOST]:22
molbiofreak@robot -> SSH timeout to [REDACTED-ROBOT-HOST]:22
```

Therefore, this RCA cannot truthfully include fresh live readback of G param 6 / param 7 from the robot controller. The physical observation is treated as the primary evidence, and the exact current-register values remain unverified until robot access returns.

## Bottom line

The previous “fixed and live” conclusion from 2026-05-27 was **overstated or incomplete**.

The recurrence means the system still has at least one path that can leave the gripper motor in a hot idle/high-current state after reset/recovery/startup. The prior fixes may have corrected specific routes/defaults, but they did not establish a global gripper-current lifecycle invariant.

Correct classification:

```text
gripper current lifecycle regression / incomplete fix
```

Not:

```text
BMS UI issue
OEM parity issue only
harmless warmth
resolved previous bug
```

## What is proven

### 1. OEM does not intend full-current idle hold for the newer gripper path

Prior RCA source anchors:

- `DefaultParameters.cs` lines 41-43:
  - `G_MOTOR_MAX_CURRENT = 31`
  - `G_MOTOR_HOLD_CURRENT = 10`

Interpretation:

- `31` is an OEM action/max current.
- `10` is the OEM hold/idle current.
- Treating `31` as standby/idle is not OEM-shaped.

### 2. OEM gripper actions raise current temporarily, then restore low current for GripperVersion 1

Source: `ClassControlInterface.cs`.

Manual gripper home:

- lines 2055-2064: `setGripperCurrent(31)` then `goHome(...)` at versioned speed.
- lines 2066-2069: if `GripperVersion == 1`, `setGripperCurrent(10)`.

Manual gripper move:

- lines 2082-2086: `setGripperCurrent(31)` then `moveG(...)`.
- lines 2088-2090: if `GripperVersion == 1`, `setGripperCurrent(10)`.

Startup initialize motors:

- line 3354: `setGripperCurrent(31)`.
- line 3355: gripper `moveSteps(..., 10000, true)`.
- lines 3358-3365: gripper `axisSearchHome(...)` at 600 or 200 depending `GripperVersion`.
- lines 3417-3420: if `GripperVersion == 1`, `setGripperCurrent(10)`.

Interpretation:

OEM uses high gripper current only around gripper work and restores low current afterward for the relevant gripper version.

### 3. Prior Linux/BMS fix claimed specific routes were corrected

Prior report claimed:

- robot raw current defaults changed to `10/10`;
- gripper profile default changed to `10/10`;
- `gripper-clear` temporarily uses high current then restores in `finally`;
- BMS proxy/frontend defaults changed to `10/10`;
- explicit hot standby `31/31` rejected.

Those may have been true for the tested paths, but recurrence proves they were not a complete safety invariant across all reset/recovery/startup paths.

## Most likely root cause

The fix was **path-local**, not **state-invariant**.

Likely repaired paths:

- `/motion/axes/current` default payload;
- BMS current-setting defaults;
- at least one `gripper-clear` path;
- maybe the gripper preset profile.

Likely unsealed paths still able to leave G hot:

1. Service startup / daemon init after robot reset.
2. Maintenance USB reconnect/recover.
3. Hard reset recovery.
4. Strict startup no-homing path.
5. OEM startup-step `gripper-clear` / `g-home` exception or abort path.
6. Legacy raw `usb_driver.py` menu/script path.
7. `initializeMotors` / `initializeMotion` / `rehome` partial ports.
8. Any code path that raises G current to 31 but does not use a guaranteed `finally` restore.
9. Any board/controller reset path that preserves or re-applies prior param 6/7 high-current state.

## Why this recurred after robot reset

A reset is exactly where a route-specific patch can fail:

- controller current registers may persist or boot into a prior high state;
- the service may reinitialize motor presets and accidentally write high G current;
- a recovery/startup path may call a gripper high-current prep without the later restore path;
- if the daemon/service crashes or the operator resets during a high-current window, cleanup code may never execute;
- a BMS/frontend default fix does nothing for robot-local boot/recover paths.

So the likely failure mode is:

```text
G current was raised to action/full current, then the restore-to-idle step did not run or was overwritten after reset/recovery.
```

## Why prior verification missed it

The 2026-05-27 verification was too narrow:

- It verified one default current route.
- It verified one explicit hot-standby rejection.
- It verified final G status after a controlled restore.
- It did not prove every lifecycle path exits with G `10/10`.
- It did not prove robot reset/service startup leaves G safe.
- It did not prove abort/exception paths restore G current.
- It did not install a periodic/boot-time safety guard that forces safe G idle when no gripper operation is active.

The statement “Fixed and live” should have been scoped as:

```text
fixed for the tested route/defaults, not globally proven across reset/recovery/all gripper paths
```

## Immediate safety verdict

This is a stop condition.

Do not proceed with OEM homing replacement, gripper clear, g-home, initializeMotors, initializeMotion, rehome, or any motion sequence until G current lifecycle is audited and guarded.

If the gripper motor is still hot:

- remove/disable motion power if possible;
- let the motor cool;
- do not keep the instrument idling in that state;
- do not run homing/motion as a diagnostic.

## Required evidence once robot is reachable

Perform read-only/passive first:

1. Read G axis status.
2. Read G current controller params:
   - param 6 / max-active current;
   - param 7 / standby-idle current.
3. Read all axis speeds.
4. Read recent service logs around reset/startup.
5. Inspect live repo diff for `api.py` and `usb_driver.py`.

Expected unsafe confirmation:

```text
G speed = 0
G param6 and/or param7 high, especially 31/31 or standby=31
motor physically hot
```

That would prove hot idle current directly.

If params read `10/10` but motor is still hot, then the RCA shifts from software current-register lifecycle to physical driver/output fault or stale heat from earlier high-current state. That distinction requires fresh readback.

## Corrective action required before more homing work

### 1. Add a global gripper idle-current invariant

When no gripper operation is actively executing:

```text
G param6 <= 10
G param7 <= 10
```

High G current is allowed only inside an explicit scoped operation.

### 2. Scope all G high-current operations

Pattern required everywhere:

```python
try:
    set_g_standby_current(10)
    set_g_run_current(31)
    perform_gripper_action()
finally:
    set_g_standby_current(10)
    set_g_run_current(10)
```

This must cover success, error, timeout, abort, and operator reset as much as software can.

### 3. Force safe G idle on daemon boot/reconnect/recover

On service startup/reconnect/recovery, before exposing motion readiness, if no gripper operation is active:

```text
write G standby/current to 10/10
verify readback
report unsafe if write/readback fails
```

### 4. Add hot-idle detection to status surfaces

Robot/BMS should classify:

```text
G_CURRENT_SAFE_IDLE
G_CURRENT_ACTION_ACTIVE
G_CURRENT_UNSAFE_HOT_IDLE
G_CURRENT_UNKNOWN
```

`UNSAFE_HOT_IDLE` when:

```text
G speed == 0 and (param6 > 10 or param7 > 10)
```

Especially if `param7 > 10`.

### 5. Block homing/startup if G hot-idle is detected

Before `initializeMotors`, `initializeMotion`, `rehome`, `homeGZ`, `gripper-clear`, and `g-home`:

- check G current state;
- if idle-hot, restore `10/10` or fail closed;
- do not continue silently.

### 6. Add tests for every lifecycle path

Required no-hardware tests:

- service startup/reconnect forces G safe idle;
- strict startup no-homing exits with G safe idle;
- gripper-clear success restores G `10/10`;
- gripper-clear exception restores G `10/10`;
- g-home success restores G `10/10`;
- g-home exception restores G `10/10`;
- initializeMotors model restores G for GripperVersion 1;
- initializeMotion/rehome wrappers do not bypass the invariant;
- hot standby requires explicit commissioning override;
- status classifies `speed=0,param6=31,param7=31` as unsafe hot idle.

## Relationship to OEM homing replacement

This must become a pre-phase gate before continuing OEM homing replacement.

The OEM source itself uses gripper current transitions during startup homing. A 1:1-ish port that reproduces `setGripperCurrent(31)` without a robust restore/invariant will cook the motor again.

Therefore the next implementation phase should be:

```text
Phase 2.5: gripper current lifecycle safety invariant
```

before robot-local OEM homing mode implementation.

## Final RCA statement

Christian’s observation invalidates the prior broad “fixed and live” claim. The actual defect is that the Linux/BMS fixes corrected some current defaults and one or more gripper operation paths, but failed to enforce a global robot-local invariant that idle gripper current must return to OEM hold current. After reset/recovery, some unsealed path can still leave the gripper motor energized at high/full current while stationary.

Until fresh robot readback proves otherwise, treat the recurrence as a safety-critical gripper current lifecycle regression and halt further homing/motion work.
