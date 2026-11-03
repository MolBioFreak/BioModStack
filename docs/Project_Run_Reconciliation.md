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
