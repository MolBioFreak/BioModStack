# BioXP Phase 0 Current Iteration Freeze — 2026-06-09

## Scope

Freeze/quarantine planning artifact for the current BioXP homing/init iteration before starting a 1:1 OEM-spec replacement effort.

No live robot motion was performed. No BMS runtime/container changes were performed.

## Local BMS repository state

Repository: `/home/dalab/biomodstack/biomodstack`

Command run:

```bash
git diff -- platform/api/routers/bioxp.py \
  platform/frontend/src/components/BioXpCockpit.tsx \
  platform/frontend/src/lib/bioxpClient.ts \
  platform/frontend/tests/bioxpInterlinkMenuContract.test.ts \
  platform/frontend/tests/bioxpInterlinkStatus.test.ts \
  platform/frontend/src/components/BioXpInterlinkControlPanel.tsx \
  platform/frontend/src/components/bioxpInterlinkStatus.ts \
  > /tmp/bms_bioxp_relevant_diff_20260609T015531Z.patch
wc -c /tmp/bms_bioxp_relevant_diff_20260609T015531Z.patch
```

Result:

```text
0 /tmp/bms_bioxp_relevant_diff_20260609T015531Z.patch
```

Interpretation: at this checkout, there is no tracked local diff in the relevant BMS BioXP proxy/frontend files. The branch itself has unrelated dirty work in other areas; those files are intentionally untouched by this effort.

## Robot repository backup status

Intended robot repo:

```text
/home/molbiofreak/bioxp_re
```

Attempted read-only capture from this sandbox:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 bioxp \
  'cd /home/molbiofreak/bioxp_re && git status --short --branch && git diff -- src/bioxp/usb_driver.py src/bioxp/api.py tests || true'
```

Result:

```text
ssh: Could not resolve hostname bioxp: Name or service not known
```

Interpretation: robot-local backup/quarantine cannot be truthfully completed from this sandbox using the `bioxp` alias. Before modifying robot code, a robot-local backup-bin artifact must be created on the robot host, including at minimum:

```text
/home/molbiofreak/bioxp_re/backup_bin/oem_homing_iteration_<timestamp>/
  MANIFEST.md
  usb_driver.py
  api.py
  git_diff.patch
  route_inventory.json
  notes_current_behavior.md
```

## Quarantine rule

Until a source-to-target OEM matrix is reviewed, any existing Linux/BMS homing path should be treated as:

```text
legacy_partial_guarded_reconstruction
not_oem_equivalent=true
```

This is not a deletion request. The current iteration remains useful evidence, but must not be called OEM parity.

## Phase 0 conclusion

- BMS BioXP-relevant local diff: empty at this checkout.
- Robot backup-bin: pending due unavailable SSH alias from this environment.
- No live motion or runtime deployment occurred.
- Proceeding source-spec work may use local decompiled OEM artifacts only.
