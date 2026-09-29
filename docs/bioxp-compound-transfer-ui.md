# BioXP cockpit plate/cover transfer

Move to destination is travel only. Pick up and move submits one native
`move_cover` or `plate_move` action through `/api/bioxp/protocols/submit` to
robot `/protocol/execute`. Inspect covers may relocate covers; it is not a
passive photo operation. Custody, sequencing and execution admission stay
robot-owned.

Select the object and destination, then choose transfer or inspection. Only
the current in-flight submission reserves this control. Historical ambiguity
and failed observations do not prevent a new explicit intent. Each click gets
its own original key and canonical job ID before POST. Lost replies retain
that identity for GET reconciliation; no automatic POST replay is performed.
Connection replacement does not send the old request to the new robot.

The obsolete BMS transfer-preflight and local nonexecutable compile endpoints
are retired. There is no browser preflight, proof form or local compiler.
Robot native `/protocol/compile` and live request validation are unchanged.
Prepared requests retain their existing live-contract input fields.

Typed command outcomes, action failures and child errors remain readable;
noncritical raw JSON dumps are not rendered. Controller success is not an
independent physical observation. Cooperative Abort remains distinct from
addressed motor Stop.

Offline API and mounted frontend tests replace network/physical transport.
They do not establish physical performance or hardware acceptance.
