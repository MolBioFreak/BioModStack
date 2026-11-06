# Project run reconciliation and idle cost

The API's global experiment worker (`services/global_experiments/worker.py`)
sweeps every 2 seconds. For each active run group, `reconcile_run_group`
(`experiment_services.py`) projects the canonical Job's state into the Project
attempt. While nothing changes, a sweep must not write to the database or re-read
result files. This page describes how the reconciler stays idle and what to do
when it doesn't.

## Rules the reconciler follows

- **A live receipt contains observed state only.** `_core_job_live_receipt`
  records status, queue status, stage, progress, `started_at`, the assigned GPU
  and public provenance. It does not store wall-clock values. Readers work out
  elapsed time from `started_at`, and from `completed_at` once the Job has
  finished (`read_models._elapsed_from_receipts`). An unchanged Job gives a
  byte-identical receipt, so the sweep writes nothing: no attempt update, run
  event, status log chunk or audit event.
- **The terminal status chunk is always written.** An attempt's `status` log
  stream accepts at most `_ATTEMPT_LOG_CHUNK_LIMIT` (10,000) chunks while it is
  open. The chunk that closes the stream is accepted even past that limit. It is
  one extra row, and refusing it rolls back the terminal projection, so the
  attempt can never finish.
- **Late-output linking is rate-limited.** A terminal native attempt that has
  output evidence but no linked output is re-verified when the Job's provenance
  changes, which is how a late publication arrives. Otherwise it is re-verified
  at most every `_LATE_OUTPUT_RECHECK_SECONDS` (600 s). Verification reopens the
  producer's whole publication; for BindCraft2 that means hashing and parsing
  every campaign file. The rate limit is held in process memory, so an API
  restart re-checks every pending attempt once.

## What went wrong before (October 2026)

The live receipt used to include `elapsed_seconds`. Each sweep therefore saw a
"changed" receipt for every running attempt. It appended a run event, a status
log chunk and an audit event, and bumped generations. Over about two months this
wrote 107,921 near-identical `core_live_projected` events, about 2.4 GB of the
2.7 GB `experiments.db`. Ten attempts filled their status stream to the cap.
After that, their terminal projection raised `attempt log stream chunk limit
reached` and rolled back. The attempts stayed `running` in Projects while their
Jobs had long finished, and the full reconcile, including BC2 publication
verification, repeated every 2 seconds. With no jobs running, the API used about
70 % of one CPU core and read about 700 MB/s from the page cache.

## Diagnosing

```sh
curl -s localhost:18002/api/experiment-workspaces/ops/worker-health | python3 -m json.tool
```

A `failure_count` that keeps rising with the same `last_error`, while
`last_sweep_counts.reconciled` stays 0, means one group fails on every sweep. To
see where the time goes, profile the live process for 30 seconds. This is
read-only.

```sh
sudo py-spy record --pid "$(systemctl --user show -p MainPID --value biomodstack-api)" \
  --duration 30 --nonblocking --format raw --output /tmp/api-profile.txt
```

## Compacting historical live events

`scripts/compact_experiment_run_events.py` removes `core_live_projected` events
that differ from the previous kept event for the same run only in
`elapsed_seconds`. Every real state change is kept. Other event types are not
touched. `run_events` is append-only by trigger, so the tool drops the two
immutability triggers, deletes the rows and restores the exact stored trigger
definitions in one transaction. The schema attestation is unchanged.

Run a dry run first, then apply with the API stopped. The tool takes the
experiment dispatcher lock and refuses if the API holds it:

```sh
python3 scripts/compact_experiment_run_events.py ~/.biomodstack-dev/experiments.db
systemctl --user stop biomodstack-api
python3 scripts/compact_experiment_run_events.py ~/.biomodstack-dev/experiments.db \
  --apply --backup ~/.local/state/biomodstack/backups/experiments-pre-compact.db --vacuum
systemctl --user start biomodstack-api
```

On the October 2026 Development store this removed 107,788 events, kept 133,
and shrank the file from 2.7 GB to about 110 MB. The full `run_all(...,
verify_data=True)` attestation passed afterwards.

Audit events (`run_group_reconciled`, about 108,000 rows, 2 MB) and status log
chunks are left as they are. Both are append-only history, and they are small.

## Database read load

An idle API should not read the databases at all. These are the reads that were
still happening after the reconcile fix, and what keeps them bounded.

- **Retired runtime inventories.** A remote worker's `provider_metadata` keeps
  its managed inventory, preload ledger, critical-runtime manifest and artifact
  inventory. Retired workers kept those forever: 715 MB across 68 inactive rows
  on Development, so any query touching `execution_targets` read and parsed
  hundreds of megabytes. `scripts/compact_retired_execution_targets.py` replaces
  those keys on inactive, unleased targets that have no nonterminal Job with a
  compact `retired_runtime` record. Row identity, endpoint, host-key binding,
  the small runtime binding and every Job receipt stay. Dry run by default;
  `--apply` requires a backup. On the Development store it took 714.2 MB to
  0.1 MB and the core database from 1.16 GB to 360 MB after VACUUM.
- **Active-target reads.** Telemetry reads `execution_targets WHERE active = 1`.
  After retirement the table is small, so that read is cheap without an index.
  Do not recreate `uq_execution_targets_one_active`: migration 46 drops it on
  purpose so several workers can be active at once, and restoring it makes the
  second activation fail with a uniqueness error.
- **Jobs list.** The list joined a subquery that grouped the whole Designs
  table, so one page of Jobs read the entire retained design history. It now
  counts Designs for the page's Jobs only (0.39 s to 0.02 s of CPU per call on
  Development).
- **Binder-round recovery.** It scans completed Jobs through provenance JSON.
  It used to run on every 3 s scheduler cycle; it now runs at most once a
  minute (`BINDER_RECOVERY_INTERVAL_SECONDS`), which bounds the recovery delay
  without reading the Jobs table several times a minute for nothing.

`read_bytes` in `/proc/<pid>/io` stayed near zero throughout: this was page
cache and CPU, not SSD wear. What did write continuously was the run-event
churn described above, and that is gone.
- **Results lists.** Both design lists (`GET /api/designs` and
  `GET /api/designs/by-job/{job_id}`) verified the producer publication once per
  row and read a full Job once per row for the contract revision. They now share
  `_resolve_core_protein_documents`, which does both once per Job. Each artifact
  query still opens its own DuckDB connection: that costs about 5 ms, and a
  fresh connection keeps every query's settings independent.
- **Measuring this.** `/proc/<pid>/io` separates `rchar` (bytes returned by read
  syscalls, page cache included) from `read_bytes` (actual device reads). A large
  `rchar` with a zero `read_bytes` is repeated scanning, not storage wear. Set
  `BMS_SQL_STATS_SECONDS=N` to log, every N seconds, the ten statements with the
  most total time, with their count and slowest single run; statement work runs on aiosqlite
  worker threads, so a Python profile shows an idle event loop instead.
