# OEM Spec: rehome

**OEM method/source:** ControlLib.rehome
**Source anchor:** ControlLib.cs

## Summary

Save thermal door state, initializeMotors(), short sleep, restore thermal door state via doorOpen(), resumeTemperature().

## Implementation rule

Must preserve app-state side effects; not equivalent to startup_step homing alone.

## Parity requirement

A future Linux/BMS implementation may only claim parity for this mode if its route, command order, constants, switch predicates, `setHome` timing, side effects, and failure behavior are source-mapped in the source-to-target matrix. Safety deviations are allowed only when explicitly named and surfaced to the operator.

## Extracted source anchors

- `ControlLib.rehome`: `ControlLib.cs:8784-8795` sha256 `367364cb1cc4`

## Live execution status

`not_approved_for_live_motion_in_this_phase`
