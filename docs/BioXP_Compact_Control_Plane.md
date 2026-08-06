# BioXP Compact Control Plane

## Status

This document is the canonical BioModStack-side contract for the current BioXP
Development integration. Dated BioXP plans and phase audit artifacts are
historical evidence, not runtime authority.

Production BioXP linkage is intentionally disabled. Current integration work is
performed only through the Development `test` lane and the robot's selected
serial-206 runtime.

## Authority boundary

BioModStack owns only:

- one persisted, policy-validated robot target profile;
- one process-local connection generation;
- connect, disconnect, probe, and typed non-homing recovery relays;
- private-ingress mutation authorization;
- presentation and typed translation of robot-owned operator contracts;
- offline protocol validation and local job records.

The robot owns:

- the operator action catalog and admission decisions;
- OEM motion, homing, initialization, mechanism, safety, and recovery semantics;
- ownership/readiness/hardware truth;
- action receipts and operator assessments;
- the physical aggregate emergency-stop implementation.

BioModStack has no independent BioXP command registry, command coordinator,
command availability calculation, command history, or emergency-stop
implementation. Git history is the archive for those retired implementations.

## API inventory

The feature-gated router is mounted at `/api/bioxp` only when
`BMS_FEATURE_BIOXP` is enabled.

### Connection and local state

| Method | Path | Contract |
|---|---|---|
| GET, PUT, DELETE | `/profile` | Read masked profile, save a validated target, or forget it |
| GET | `/status` | BMS connection generation plus robot-observed state |
| POST | `/connection/connect` | Activate the saved Development target |
| POST | `/connection/disconnect` | Close the active connection and advance generation |
| POST | `/connection/probe` | Refresh robot status for the active generation |
| POST | `/connection/recover-motion-non-homing` | Thin typed relay to the robot's exact non-homing recovery route |

### Robot-owned operator plane

| Method | Path | Contract |
|---|---|---|
| GET | `/operator-controls/catalog` | Robot action definitions and current admission projections |
| GET | `/operator-controls/dashboard` | Robot-owned dashboard projection |
| POST | `/operator-controls/actions/{action_id}/admission` | Re-evaluate one action against current robot state |
| POST | `/operator-controls/actions/{action_id}` | Invoke one robot action with both connection and ownership generations |
| GET | `/operator-controls/history` | Robot-owned action receipts |
| GET | `/operator-controls/receipts/{command_id}` | Read one robot receipt |
| POST | `/operator-controls/receipts/{command_id}/assessment` | Persist an operator assessment on the robot receipt |

Camera, source-shaped OEM lifecycle planning, protocols, and local job routes are
separate typed surfaces. No arbitrary robot path proxy is exposed.

The retired routes are absent:

- `/commands`;
- `/commands/{command_id}`;
- `/emergency-stop`;
- `/logs` as BMS-local command history.

## Connection and generation truth

A saved profile is configuration, not authority to issue a robot mutation. Every
operator mutation carries:

1. the active BMS connection generation;
2. the robot ownership generation returned by the operator catalog;
3. an idempotency key;
4. typed action inputs.

A stale connection generation is rejected by BMS. A stale ownership generation,
unavailable provider, failed readiness dependency, or invalid action input is
rejected by the robot.

When connection restoration is enabled, the Development API may restore its
saved Development target during startup. This does not authorize a physical
command. Production's BioXP connection setting and target remain disabled.

## Persistence

Under the resolved BMS data root:

- `bioxp/profile.json` stores the masked-target source profile with private file
  permissions;
- `bioxp/jobs.sqlite3` stores local jobs and append-only transition events;
- legacy profile/job state is migrated once or quarantined when malformed.

BioXP command receipts are not persisted by BMS. They are read from the robot's
operator receipt store.

## Target policy

Saved targets must satisfy the BMS BioXP target policy:

- HTTP or HTTPS root URL only;
- no credentials, query, or fragment;
- explicitly approved port and hostname/CIDR;
- only private/Tailscale address classes;
- pinned validated address, preserved Host/SNI, no redirects, and no environment
  proxy discovery.

The persisted URL is never returned raw through read/status routes.

## Mutation enablement

Robot-facing mutations require `BMS_BIOXP_MUTATIONS_ENABLED=1`. Connection-only
management additionally follows `BMS_BIOXP_CONNECTION_ENABLED`. Deterministic
offline protocol compile remains non-mutating.

The typed non-homing recovery relay supplies the robot's exact recovery
acknowledgement and operator reason. BMS does not determine whether recovery is
admissible or complete; the robot route does.

## Operator surface

`/bioxp` uses the robot-owned operator plane for:

- serial-206 24 V / prepare-without-motion activation;
- exact manual movement and mechanism actions;
- per-axis stop;
- the physical aggregate emergency stop;
- full advanced action catalog access;
- robot-owned recent receipts.

The cockpit's recovery button uses the single typed connection recovery relay.
No UI control reads BMS-local command availability or BMS-local command history.

## Verification boundary

Source/unit/browser validation is separate from physical commissioning. A green
software check does not establish OEM parity. Physical completion still requires
the approval-gated serial-206 commissioning sequence and robot-authoritative
receipts/readbacks.
