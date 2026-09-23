# BioXP cockpit plate/cover transfer

Deck Movement retains **Move to destination**, explicitly labelled travel only.
The separate **Pick up and move** control submits one native `move_cover` or
`plate_move` action through `/api/bioxp/protocols/submit`, relayed unchanged to
robot `/protocol/execute`. **Inspect covers (may move covers)** submits the
existing `inspect` action. Its robot handler runs OEM cover inspection and may
relocate covers; it is not a passive photo button.

No new route, motor sequencing, source-location override, custody bookkeeping,
or robot interface is introduced. Source custody and operation expansion remain
robot-owned. UI object/destination options are exact OEM script tokens, not an
admission claim; the robot validates machine destinations and physical authority.

## Operator input (only when live execution is authorized)

Load the actual `live_execution` JSON object from the current operator-reviewed
preflight. This is **not** a prepared protocol/document. It contains:

- `operator_id`: the responsible operator;
- `physical_console_verified`: the operator's explicit console verification;
- `deck_manifest`: the verified physical setup;
- `preflight.reference_snapshot`: actual robot-owned reference observations;
- `preflight.artifact_refs` (or top-level `artifact_refs`): actual supporting artifacts.

Do not invent reference states, artifact IDs, deck contents or console verification.
The UI preserves this supplied evidence; it never creates a reference snapshot.
The physical-execution checkbox explicitly supplies `live_execution_ack` for the
selected action only and clears on submission. Robot live-contract validation is
unchanged. A missing/invalid contract is not replaced with permissive defaults.

Pick the object and destination, then explicitly authorize the selected operation.
The UI derives and retains the canonical job identity before POST, so a lost
response is reconciled by GET without automatic replay. Current live commands
block another transfer and deck travel. Terminal historical custody failures do
not independently freeze controls. Readback shows actual job status, runtime
custody/action results/failures and child command receipts; controller success is
not presented as independent physical verification. Existing stop/cancel actions
are untouched.

## Offline verification

- Mounted React tests run production protocol hooks against explicit HTTP doubles.
- API tests exercise the existing relay and strict bundle contracts for cover,
  plate and inspection actions, including dispatched/completed/failed readback.
- Existing cockpit, workflow and travel queue tests remain part of the selection.
- No physical robot operation, live preparation, deployment or push is part of
  this implementation verification.
