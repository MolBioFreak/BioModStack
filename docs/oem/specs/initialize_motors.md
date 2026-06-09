# OEM Spec: initialize_motors

**OEM method/source:** ClassControlInterface.initializeMotors
**Source anchor:** ClassControlInterface.cs

## Summary

Physical startup homing sequence. Expected source order: Z home; G current high; G clear +10000; G home; X home; setHome(X); setSpeed(X,1700); move X to 6000; Y home; door home; setHome(Y); app-specific gripper current restore when applicable.

## Implementation rule

Do not collapse into generic axis home. Full live monolithic route disabled until stepwise validation.

## Parity requirement

A future Linux/BMS implementation may only claim parity for this mode if its route, command order, constants, switch predicates, `setHome` timing, side effects, and failure behavior are source-mapped in the source-to-target matrix. Safety deviations are allowed only when explicitly named and surfaced to the operator.

## Extracted source anchors

- `BioXPMainWindow.initializeEnvironment`: `BioXPMainWindow.cs:973-1027` sha256 `3d33c1e89404`
- `BioXPMainWindow.initializeSystem`: `BioXPMainWindow.cs:1046-1342` sha256 `acaf79c48cea`
- `ControlLib.initializeMotion`: `ControlLib.cs:8797-8856` sha256 `c5448efb5a5b`
- `ClassControlInterface.initializeMotorsWithoutMotion`: `ClassControlInterface.cs:3181-3265` sha256 `a39c9846a854`
- `ClassControlInterface.initializeMotors`: `ClassControlInterface.cs:3348-3421` sha256 `c99f0220a0c0`

## Live execution status

`not_approved_for_live_motion_in_this_phase`
