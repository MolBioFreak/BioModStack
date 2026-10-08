# OEM Spec: move_z_home

**OEM method/source:** ClassControlInterface.MoveZHome
**Source anchor:** ClassControlInterface.cs

## Summary

Set Z current max, read current/lost steps, goHome/rehome Z at 1791; not equivalent to controller absolute zero unless named safety deviation.

## Implementation rule

Live Z predicate mismatch must be explicitly blocked or safety-deviated.

## Parity requirement

A future Linux/BMS implementation may only claim parity for this mode if its route, command order, constants, switch predicates, `setHome` timing, side effects, and failure behavior are source-mapped in the source-to-target matrix. Safety deviations are allowed only when explicitly named and surfaced to the operator.

## Extracted source anchors

- `BioXPMainWindow.initializeEnvironment`: `BioXPMainWindow.cs:973-1027` sha256 `3d33c1e89404`
- `BioXPMainWindow.initializeSystem`: `BioXPMainWindow.cs:1046-1342` sha256 `acaf79c48cea`
- `ControlLib.rehome`: `ControlLib.cs:8784-8795` sha256 `367364cb1cc4`
- `ControlLib.initializeMotion`: `ControlLib.cs:8797-8856` sha256 `c5448efb5a5b`
- `ClassControlInterface.initializeMotorsWithoutMotion`: `ClassControlInterface.cs:3181-3265` sha256 `a39c9846a854`

## Live execution status

`not_approved_for_live_motion_in_this_phase`
