# BioXP passive collection removal

Base: `d956efad063388d72f5a21aaf8ad1612375c6dda` (`origin/test`, fetched before work).

## Owner and scope

The competing BMS owner was `BioXpConnectionService._snapshot_refresh_loop`, launched on connect/reconnect independently of the existing active status monitor. It called `BioXpRobotClient.probe`, which POSTed `/hardware/snapshot/collect` with `automatic: true` when cached observations needed refresh. This change deletes that worker, its scheduling/backoff helpers, and implicit collection from `probe`. `probe` is now a passive status read.

History reviewed before editing: `7a1da6d` introduced automatic snapshots; `aec08cb` added the independent periodic owner; `925c8c9` coalesced display reads; `8798f82` deferred automatic reads to foreground work; `77f2901` and `1400896` refined automatic scheduling while preserving control/duplicate protection. These scheduling fixes do not remove the competing owner. No broad historical patch was cherry-picked. Existing display coalescing, generation leases, retained in-flight requests, duplicate protection, command dispatch and interrupt lanes remain unchanged.

Explicit `collect_hardware_snapshot` retains its POST route and timeout. Explicit deck-refresh actions and UI controls are unchanged. Status/catalog/dashboard reads remain available. Source snapshot identity, timestamps, missing/stale state and expiry remain unchanged; reads cannot fabricate fresh observations. No frontend, robot, scientific/model, controller-check or admission-policy changes.

## Local verification

All robot HTTP in these tests uses doubles; no robot was contacted.

Before editing, the five observer/client/connection suites passed: **113 passed**. Tests of the deliberately removed automatic scheduler were replaced with passive-only/explicit-action regressions, rather than retaining expectations of automatic collection.

After editing, run from `platform/api` using the canonical Development `.venv/bin/python`:

```
python -m pytest --capture=no -q \
  tests/test_bioxp_connection.py tests/test_bioxp_robot_client.py \
  tests/test_bioxp_manual_readiness_schedule.py \
  tests/test_bioxp_deck_refresh_observation.py tests/test_bioxp_sampling_age.py \
  tests/test_bioxp_deck_harmonization_boundary.py \
  tests/test_bioxp_operator_controls.py tests/test_bioxp_protocol_relay.py \
  tests/test_bioxp_stop_source_contract.py
```

Result: **286 passed, 2 skipped**; one existing Starlette/httpx deprecation warning.

New tests exercise the real periodic status loop and client through mock HTTP, including missing, stale and half-expired observations: status/catalog/dashboard calls remain GET-only. Explicit collection makes exactly one POST and can read back a fresh result. While explicit collection is held in flight, commands and independent Stop still submit, and robot rejection still raises. Repeated passive reads of one snapshot identity cannot renew its capture time or extend expiry. Existing generation/retained-request/duplicate protections are covered by the unchanged connection suite.

From `platform/frontend`, unchanged lockfile/dependencies reused from canonical Development:

```
./node_modules/.bin/vitest run --config vitest.md.config.ts \
  tests/vitest/bioxpCockpitAdmissionFanoutMounted.test.tsx \
  tests/vitest/bioxpOperatorCriticalControlsMounted.test.tsx \
  tests/vitest/bioxpTransferControlsMounted.test.tsx
```

Result: **198 passed, 2 skipped**, all three files collected. Includes explicit deck-refresh control, passive display, pending-duplicate and independent Stop coverage.

No push, deployment, restart or robot timing measurement was performed. This proves removal of the BMS automatic collection path, not the end-to-end 2–3 second move target.
