# BioXP cockpit plate/cover transfer

Deck Movement retains **Move to destination**, labelled travel only. The separate
**Pick up and move** control submits one native `move_cover` or `plate_move`
action through the existing `/api/bioxp/protocols/submit` relay to robot
`/protocol/execute`. **Inspect covers (may move covers)** submits the existing
`inspect` action: OEM inspection observes and may relocate covers, not a passive
photo operation. Source custody, motor sequencing and execution admission remain
robot-owned. Existing scientific workflow and stop/cancel behavior is unchanged.

## Operator interaction

1. Select the object and destination (OEM intent tokens, not readiness claims).
2. Enter the responsible operator's name/account.
3. Deliberately confirm physical console verification and intended execution.
4. Choose transfer or cover inspection. No JSON authoring or upload is required.

Before every submission BMS makes uncached, fresh, generation-fenced GETs to
robot `/motion/reference/status` and `/operator/v2/control-catalog`, exposed by
read-only `/api/bioxp/protocols/transfer-preflight`. Missing or false
`ok/persisted/verified/durable_clean`, absent or unreferenced X/Y/Z/gripper rows,
missing deck revisions, and action/dashboard revision mismatch refuse preflight.
No reference flags are promoted and no blanket age or historical custody gate
is added. A connection change during preflight prevents submission.

The live contract preserves the actual reference snapshot. Artifact references
identify the robot's position-table and destination-catalog revisions plus the
SHA-256 of the returned reference JSON (sorted keys, compact separators, UTF-8).
The deck manifest records observed robot deck context and selected intent only;
it never asserts loaded plates, independently observed cover positions, or camera
proof. Physical verification/acknowledgement come only from the operator checkbox.
The robot still validates current authority when executing; preflight is not a
lease or a replacement for robot admission.

Confirmation clears on selection, operator, connection changes and submission.
The canonical job ID/key are retained before POST. Any uncertain/error reply
retains that identity for GET reconciliation, with no automatic POST replay.
Reconnection does not query the old identity on the new robot. Current live or
unresolved work blocks a second transfer. Historical terminal errors do not
independently freeze controls. Job outcomes, action failures and child errors
appear as plain text; full robot details remain optional expandable JSON.
Controller success is not independent physical verification.

## Offline verification

API tests exercise strict reference/catalog validation, generation drift, and
real HTTP-client route registration using a mock transport (no robot sockets).
Mounted React tests exercise real hooks against path-specific HTTP doubles,
including absent references, connection drift, failed child results and lost
submission replies. Integration tests retain existing cover, plate, inspection,
workflow and travel semantics. No physical motion, push or deployment occurs
in this worktree; parent integration owns publication and runtime freeze/bind.
