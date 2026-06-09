# OEM Spec: home_xy

**OEM method/source:** ClassControlInterface.HomeXY
**Source anchor:** ClassControlInterface.cs

## Summary

Home X/Y together at speed/acc 200/200, wait both, restore X 1700/350 and Y 1800/400, return lost-step deltas.

## Implementation rule

Parallel/concurrent semantics must be modeled; serial fallback is a named deviation.

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
