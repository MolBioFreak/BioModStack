# OEM Spec: initialize_motors_without_motion

**OEM method/source:** ClassControlInterface.initializeMotorsWithoutMotion
**Source anchor:** ClassControlInterface.cs

## Summary

OEM board/motor preparation without physical homing. Must remain distinct from physical initializeMotors().

## Implementation rule

No USB/motion side effects in dry-run model; live route must not move axes.

## Parity requirement

A future Linux/BMS implementation may only claim parity for this mode if its route, command order, constants, switch predicates, `setHome` timing, side effects, and failure behavior are source-mapped in the source-to-target matrix. Safety deviations are allowed only when explicitly named and surfaced to the operator.

## Extracted source anchors

- `BioXPMainWindow.initializeEnvironment`: `BioXPMainWindow.cs:973-1027` sha256 `3d33c1e89404`
- `BioXPMainWindow.initializeSystem`: `BioXPMainWindow.cs:1046-1342` sha256 `acaf79c48cea`
- `BioXPMainWindow.motion_thread_process`: `BioXPMainWindow.cs:2030-2101` sha256 `c79bc5d3a6b0`
- `ControlLib.initializeMotion`: `ControlLib.cs:8797-8856` sha256 `c5448efb5a5b`
- `ClassControlInterface.initializeMotorsWithoutMotion`: `ClassControlInterface.cs:3181-3265` sha256 `a39c9846a854`

## Live execution status

`not_approved_for_live_motion_in_this_phase`
