# Experiment and MolBio/NGS startup audit scope

API initialization now calls each store's `run_all(path, verify_data=False)`.
For an already-current migration ledger this verifies the complete migration-owned
schema definitions, indexes/triggers and exact ledger, without scanning historical
foreign keys. MolBio/NGS also avoids its historical authority-coherence queries and
managed reference artifact reads/hashes. SQLite FK enforcement and all triggers
remain enabled for normal writes; no scientific data or migration SQL changes.

If the ledger requires an actual migration (including a new store), the migration
runner forces full data attestation even when startup requested schema-only work.
Migration-local verification remains intact. Experiment initialization no longer
executes the same final `foreign_key_check` twice: full attestation owns that check.
No new gate, scheduled audit, background task or automatic history repair is added.
Existing migration transaction/commit boundaries are unchanged; this does not add
an all-migrations atomic rollback guarantee.

## Explicit integrity owners

`experiment_migrations.run_all(path)` and `molbio_ngs_migrations.run_all(path)`
retain full verification by default. Both `attest_schema(connection)` defaults
remain full. Experiment `health(path)`, MolBio/NGS `health(path, deep=True)`, and
existing backup/restore/release verification callers retain their full checks.
Schema-only results explicitly return `data_integrity_checked: false` and
`foreign_key_errors: null` (NGS also returns null for its other omitted data checks),
not a false claim that historical data was audited successfully. A schema-valid
current store can therefore start with historical corruption that an explicit full
audit will report; this is the approved change, not a guarantee of healthy history.

Core DB and remote execution are outside this change. API main/startup ordering is
unchanged; these changes do not qualify total API boot or eliminate other owners'
startup work.

## Qualification

Run from `platform/api`, with isolated BMS state/artifact paths:

```sh
uv run --frozen --group dev python -m pytest -q -s \
  tests/test_startup_history_audit.py \
  tests/test_native_setup_route_migration.py \
  tests/test_molbio_ngs_experiment_management.py
```

21 passed. Tests exercise real SQLite stores: 2,000 experiment resources and 100
NGS binding/state pairs, full-vs-shallow SQL trace counts, exact dump preservation,
missing-FK corruption, same-length artifact corruption, schema/ledger drift,
API initializer dispatch, fresh-store full checks, v3-to-v4 NGS upgrades (including
corrupt predecessor rejection), and a populated v21-to-v22 experiment migration
with exact historical-row preservation and full-audit/reopen scope assertions.
Backup/restore artifact tamper and schema-definition checks remain covered.

The wider `test_experiment_workspace_meta_layer.py` has 7 failures and 6 passes;
the exact same seven failing tests reproduce on pristine `ac8c4b8`. These concern
existing dispatch/resource-admission and HTTP authorization expectations and were
not changed in this scope.

An offline reflink/copy of the retained 20261028T024752Z backup was measured under
current host load (not the live database):

| Store / mode | Elapsed | historical FK checks |
| --- | ---: | ---: |
| Experiment baseline ac8c4b8 | 0.197644 s | 2 |
| Experiment repaired startup | 0.031414 s | 0 |
| NGS repaired startup | 0.010367 s | 0 (covered by SQL-trace tests) |

The experiment copy was 2,725,957,632 bytes, with 108,036 run events, 107,943 log
chunks and 110,152 audit events. Every table's row count was preserved and neither
startup emitted any INSERT/UPDATE/DELETE/REPLACE statement. The NGS copy had 6
domain states, 20 state revisions, 87 outbox events and one reference artifact;
its complete SQL dump was unchanged. The retained backups were not modified.

These are single sequential local measurements, not cold-cache or end-to-end
latency guarantees. The experiment table-count pass warmed caches before timing.
No full NGS audit of the retained copy was run because its external artifact bytes
were not copied; no live artifact tree was scanned. Full artifact auditing is
qualified with isolated populated fixture bytes instead. No push/deployment was
performed by this change.
