# NGS historical catalog cutover (F5/P7)

**Source tooling, not an executed migration or deployment approval.** Run only after
separate authorization on the selected managed **Development** target. Do not use
these commands on production or assume this checkout owns a running service.

## Separate gates

1. **Preflight/schema:** discover service owner, canonical checkout, API/frontend
   revision, native database paths and migration ledger. Reconcile controlling
   source changes. Quiesce NGS scientific and derived workers through their managed
   service owner; confirm no queued/running science or live workflow units. Record
   paused/review exclusions. Take fresh verified database and artifact backups.
   Record *all columns* of protected job
   `e48193a5-f6e1-47af-9c1d-30fda50090a8`, without assuming a historical status label.
   A failed backup/preimage verification blocks schema mutation too.
2. **Inventory:** explicitly designate jobs (or enumerate all jobs) through the
   existing identity and source owners. Verify source BAM/BAI digests and lengths,
   immutable reference and accepted receipt; show exclusions and typed blockers.
3. **Admission:** review the inventory JSON, then admit its exact source and preview
   policy generations in one `BEGIN IMMEDIATE` transaction. Only product rows are
   changed. Historical ownership is bound to the exact scientific receipt/params
   and result root on those rows, **not appended to or substituted for science**.
4. **Construction:** separately authorize the existing
   `NgsAlignmentPresentationWorker`. No second scheduler or builder entrypoint is
   introduced. Workers re-resolve source ownership before and after construction.
5. **Reader/release:** obtain a source-rechecked activation report; review/deploy
   readers using the ordinary release process. A report never flips a flag,
   starts services, creates requests or authorizes deployment.

## Commands

From the reviewed checkout's `platform/api`, using its locked environment. `$DB`,
`$BACKUP`, `$REVISION` and evidence paths below are operator-discovered values,
not defaults supplied by the tool. Keep evidence outside the repository.

After backup verification and **separate schema authorization**, use the existing
runner with the explicit database (do not directly invoke individual migrations):

```bash
uv run --frozen python -c 'import sys; from migrations.runner import run_all; run_all(sys.argv[1])' "$DB"
```

Migration **49 `add_ngs_historical_request_ownership`** adds one nullable JSON
column to migration 47's existing product table. Migrations 46–48 and all old
presentation bytes/rows are untouched. Reconcile ordinal 49 against the integration
registry before release; never renumber an installed migration. The CLI requires
an exact installed registry prefix at the current head and valid recorded checksums.
Schema installation **does not backfill** or adopt anything.

Database-read-only inventory (`inventory` and `dry-run` are equivalent):

```bash
uv run --frozen python -m scripts.ngs_history dry-run --database "$DB" \
  --job-id d08ca589-af8b-46dc-98bd-f17ed512cecd \
  --job-id e48193a5-f6e1-47af-9c1d-30fda50090a8 > "$EVIDENCE/inventory.json"
# Or explicitly enumerate every job, with non-NGS/failed/protected exclusions:
uv run --frozen python -m scripts.ngs_history inventory --database "$DB" \
  --all-jobs > "$EVIDENCE/all-inventory.json"
```

Inventory/report open the named SQLite database with `mode=ro`, never create it,
never install schema, never build derived products. Source validation may use the
existing resource-accounted verified delivery cache; this is not a promise of
zero cache I/O. Full artifact verification can be expensive and requires the
existing target allocation/capability. Missing native single-reference linkage
is reported, never repaired by inventing topology or rewriting terminal science.

For preflight evidence, inspect the current schema/protected preimage with:

```bash
uv run --frozen python -c 'import json,sys; from scripts.ngs_history import inspect_database; print(json.dumps(inspect_database(sys.argv[1]),indent=2))' "$DB"
sha256sum "$BACKUP"
git rev-parse HEAD
```

A release owner supplies a closed JSON preflight receipt with these exact fields:

```json
{
  "schema": "bms.ngs.historical-preflight.v1",
  "database": "<absolute resolved Development database path>",
  "plan_sha256": "<inventory plan_sha256>",
  "source_revision": "<full clean checkout commit>",
  "backup_path": "<absolute fresh verified SQLite backup>",
  "backup_sha256": "<exact backup file SHA-256>",
  "protected_preimage_sha256": "<inspect_database value, also checked against backup>",
  "service_owner": "<observed managed service identity/evidence reference>",
  "authorization": "<actual operator authorization receipt reference>",
  "managed_origin": "<discovered configured operator origin>",
  "expires_at": "<operator-approved expiry, ISO-8601 with timezone>",
  "workers_quiescent": true
}
```

These are placeholders, **not an authorization template to rubber-stamp**. Service
ownership, all native-store/artifact backups, quiescence and origin are independently
reviewed evidence, not verified by a string field. The tool verifies expiry, clean
local revision, exact plan/DB binding, backup SHA/SQLite integrity and protected
preimage in both backup and current DB; it rechecks that preimage under the writer
lock. It verifies source inventory again before flushing any request.

Only after admission authorization:

```bash
uv run --frozen python -m scripts.ngs_history backfill --database "$DB" \
  --plan "$EVIDENCE/inventory.json" --apply \
  --preflight "$EVIDENCE/preflight.json" --revision "$REVISION" \
  > "$EVIDENCE/backfill-receipt.json"
```

Rerun after a lost response is safe: exact catalog and preview intents are reused;
ready, failed, cancelled and running products are not reset. Interrupted admission
rolls back ownership and requests together. On writer/source drift, review a new
inventory; never edit science to make the old plan pass. A crash after product
rename but before publication remains a worker-owned orphan: lease expiry fails
it, and only an authorized exact-product retry permits full semantic validation
and adoption. Predecessor v3/v5 files are not copied/relabelled into v2/v6. The
builder retains incompatible predecessors and independently constructs new bytes;
it validates compatible split-product orphans against current source, schemas,
rows, projection, membership, policy and artifact digests before publication.

Resume the existing managed derived worker **only with construction authorization**.
Do not run the retired coupled worker. Observe and, when authorized, retry only
the exact failed catalog/preview using the existing source-bound product endpoint.

```bash
uv run --frozen python -m scripts.ngs_history report --database "$DB" \
  --plan "$EVIDENCE/inventory.json" > "$EVIDENCE/activation-report.json"
```

Every designated catalog must be ready and its sealed catalog artifacts verify.
Applicable previews must be explicitly `ready` or `failed`; **preview failure does
not block complete-read activation**. Pending preview is a cutover disposition
obligation, not a catalog-readiness dependency. Blocked sources and source drift
remain visible. Reports do not silently drop blocked rows from the denominator.
The default preview request is policy-bound in the inventory; a writer-policy
change requires new review. Status GET is observational.

## Accepted fixture is a separate release gate

Recheck current immutable authority for
`d08ca589-af8b-46dc-98bd-f17ed512cecd`; never manufacture acceptance. Retain the
predecessor §15 assertions under canonical §7's corrected writer contract:

- Complete BAM 818,274,983 bytes and unchanged digest; authenticated full/Range
  download. Automatic mounting retains its current 536,870,912-byte display bound.
- 5,000 preview read IDs / 9,522 mapped primary-plus-supplementary projections,
  core projection with no optional tags, below 67,108,864 bytes, one writer/index;
  corrected BGZF feasibility must be proved without cap increases or source rewrites.
- Exact `eGFP_plasmid:1-4252`; read
  `b0aabdbd-d617-4392-83db-9c0c7083e688` detail and calibrated waveform:
  84,356 source samples / 16,872 displayed pA points.
- Complete population query, outside-preview selected overlay miss/hit, exact
  detail and saved reopen, optional failure containment, unchanged protected
  preimage. Repeat builds preserve deterministic semantic/content identities.

The report deliberately says `accepted_preview_fixture: not_verified_by_this_report`.
A failed preview may permit catalog reader activation but cannot waive this named
fixture's separate release proof. A corrected-writer conflict returns to the operator.

## Compatibility and deferred verification

Legacy presentation retry and locus creation return typed **410
`NGS_LEGACY_MUTATION_RETIRED`**, never enqueue unconsumed work. Nonready legacy
GET reports retirement rather than claiming active preparation. Existing ready
legacy reads/downloads remain; ambiguous implicit generation lookup returns 409,
and authority-addressed artifact reads select the exact ready row. Active v5
semantic helpers remain because the split builder/reader/overlay still use them.

Definitions added in `tests/test_ngs_historical_backfill.py`: repeat/lost response,
failed-preview preservation, cancellation/crash atomicity, source drift, expired
claim fencing, preview-independent readiness, protected exclusions, exact science
binding, additive ordinal compatibility and fully validated orphan adoption.
Legacy route/ordinal expectations were updated. **Not executed in this source pass.**
After authorization, from `platform/api`:

```bash
uv run --frozen --group dev python -m pytest tests/test_ngs_historical_backfill.py \
  tests/test_ngs_alignment_presentation_migration.py tests/test_ngs_alignment_sessions.py \
  tests/test_ngs_alignment_presentation_worker.py
```

Then separately rehearse old DB + retained artifacts; crash/cancel and publication
fencing; source/reference/digest failures; concurrent/lost-response admission;
accepted-fixture writer/detail/waveform; managed-origin reader and saved UI flows.
No throughput, migration rehearsal, native parser portability, test pass, or
live acceptance is claimed by this document. Portable native reading and F3/F4
UI/Project work are separately owned integration gates.
