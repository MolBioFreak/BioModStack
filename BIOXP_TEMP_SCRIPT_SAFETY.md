# BioXP temporary script safety boundary — Phase 0

**Scope:** the seven root-level files matching `tmp_bioxp_*` that are listed and
hashed in `docs/audits/bioxp-phase0-temporary-script-manifest.json`.

## Phase 0 handling

- These scripts were **classified by static source inspection only**.
- None of the seven scripts was imported, sourced, invoked, or executed during
  Phase 0.
- No SSH connection, robot HTTP request, service action, USB operation, homing,
  initialization, or motion command was issued from these scripts.
- The BioXP Linux host remained off and was not probed.
- The scripts were not moved or deleted because the shared worktree was already
  heavily dirty and preserving forensic provenance takes priority.

## Safety classification

- `tmp_bioxp_current_grep.py` and `tmp_bioxp_rca_inspect.py` are read-only
  offline inspection helpers.
- `tmp_bioxp_nomove_diag.sh` is a read-only **live-host** diagnostic and still
  requires a separately authorized maintenance window.
- `tmp_bioxp_z_minus50k.py`, `tmp_bioxp_z_nonmotion_reset_retry.py`,
  `tmp_bioxp_z_rehome_minus15k.py`, and `tmp_bioxp_z_zero_minus15k.py` contain
  one-off live commissioning/control behavior and must be treated as
  motion-capable or state-mutating. **Do not execute them as tests.**

The JSON manifest is the machine-checked inventory. This note is human
provenance and is intentionally excluded from runtime-consumer reference checks.
