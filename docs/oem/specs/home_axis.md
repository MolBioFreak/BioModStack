# OEM Spec: home_axis

**OEM method/source:** ClassControlInterface.HomeAxis
**Source anchor:** ClassControlInterface.cs

## Summary

Generic home by axis with axis-specific current/stallguard/speed behavior; differs from manual button handlers and startup sequence.

## Implementation rule

Route must explicitly say HomeAxis, not manual_home or startup_step.

## Parity requirement

A future Linux/BMS implementation may only claim parity for this mode if its route, command order, constants, switch predicates, `setHome` timing, side effects, and failure behavior are source-mapped in the source-to-target matrix. Safety deviations are allowed only when explicitly named and surfaced to the operator.

## Extracted source anchors

- `ControlLib.rehome`: `ControlLib.cs:8784-8795` sha256 `367364cb1cc4`
- `ClassControlInterface.HomeAxis`: `ClassControlInterface.cs:4997-5052` sha256 `acd5ff0e05e1`
- `ClassControlInterface.HomeXY`: `ClassControlInterface.cs:5054-5070` sha256 `875310cf1630`
- `ClassControlInterface.MoveZHome`: `ClassControlInterface.cs:4623-4632` sha256 `7f7e6eb52807`
- `ClassControlInterface.homeGZ`: `ClassControlInterface.cs:4657-4687` sha256 `19887d5f0fd9`

## Live execution status

`not_approved_for_live_motion_in_this_phase`
