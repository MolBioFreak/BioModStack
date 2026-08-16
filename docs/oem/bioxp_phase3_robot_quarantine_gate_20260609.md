# BioXP Phase 3 Gate — Robot-Local Quarantine Required Before Implementation

> **HISTORICAL EVIDENCE — NOT THE CURRENT BMS CONTRACT.** Retained for audit
> provenance only. Current BioXP ownership and routes are defined by
> [`../BioXP_Compact_Control_Plane.md`](../BioXP_Compact_Control_Plane.md).

## Status

Phase 3 is **blocked** in this environment.

No robot code was modified. No BMS code was modified. No live robot motion was attempted.

## Why this gate exists

The replacement architecture requires robot-local raw FastAPI/runtime ownership. Implementing only BMS proxy/frontend shims would repeat the failure mode we are trying to eliminate: route names and UI surfaces that imply OEM parity while the robot-local behavior remains partial/unproven.

Therefore, no implementation phase may proceed until the robot-local repo can be accessed and backed up.

## Robot access attempts

A saved SSH agent env was present:

```text
~/.ssh/hermes-bioxp-agent.env
```

Read-only connection attempts from this sandbox:

```text
bioxp -> DNS resolution failed
robot -> TCP/SSH timeout to [REDACTED-ROBOT-HOST]:22
bioxp3200 -> DNS resolution failed
molbiofreak@bioxp -> DNS resolution failed
molbiofreak@robot -> TCP/SSH timeout to [REDACTED-ROBOT-HOST]:22
```

## Required next step before code implementation

On the robot host, create a backup-bin artifact before touching runtime files:

```text
/home/molbiofreak/bioxp_re/backup_bin/oem_homing_iteration_<timestamp>/
  MANIFEST.md
  usb_driver.py
  api.py
  git_diff.patch
  route_inventory.json
  notes_current_behavior.md
```

Suggested read-only commands once robot access is restored:

```bash
cd /home/molbiofreak/bioxp_re
mkdir -p backup_bin/oem_homing_iteration_$(date -u +%Y%m%dT%H%M%SZ)
git status --short --branch
git diff -- src/bioxp/usb_driver.py src/bioxp/api.py tests > backup_bin/oem_homing_iteration_<timestamp>/git_diff.patch
cp src/bioxp/usb_driver.py backup_bin/oem_homing_iteration_<timestamp>/usb_driver.py
cp src/bioxp/api.py backup_bin/oem_homing_iteration_<timestamp>/api.py
python3 - <<'PY'
# Route inventory script should import the FastAPI app without opening USB and dump /openapi.json route keys.
PY
```

## Implementation hold

Until that backup exists, the only approved work is docs/spec/test planning. Do **not**:

- edit `/home/molbiofreak/bioxp_re/src/bioxp/usb_driver.py`,
- edit `/home/molbiofreak/bioxp_re/src/bioxp/api.py`,
- expose new BMS buttons/routes as if robot behavior is fixed,
- deploy to BMS test/runtime,
- run live homing.
