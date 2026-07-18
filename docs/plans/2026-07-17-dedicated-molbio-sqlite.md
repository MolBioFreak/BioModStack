# Dedicated Mol Bio SQLite implementation plan

**Date:** 2026-07-17
**Owner:** Mol Bio subsystem
**Canonical database:** `${BMS_MOLBIO_DB_PATH:-<BMS data root>/molbio.db}`
**Legacy source:** `${BMS_DB_PATH:-<BMS data root>/biomodstack.db}`

## Non-negotiable invariants

- Mol Bio owns its own SQLAlchemy metadata, engine, async session factory, migrations, health status, backup, and recovery path.
- SQLite connections enable `foreign_keys=ON`, `journal_mode=WAL`, and `busy_timeout=30000`.
- The legacy core tables may remain empty for compatibility, but Mol Bio rows do not remain in the core store after cutover. Evacuation requires retained online backups of both stores, exact row/ID/content parity, a single transactional row purge, and post-purge integrity plus unrelated-core-count verification.
- Extraction starts with SQLite's online backup API and verifies row counts, total bases, per-sequence SHA-256, foreign keys, and orphan references.
- Molecular revisions, operation lineage, PCR experiment revisions, Tm model revisions, polymerase preset revisions, audit events, and outbox events are append-only.
- PCR experiment records are separate from optional generated sequence documents.
- No cross-database foreign key or distributed transaction is introduced.
- Polymerase values are never seeded or inferred without an attributable source.

## Execution slices

### 1. RED — owned database boundary

Create `platform/api/tests/test_molbio_database.py` covering:

1. independent metadata and configuration precedence;
2. SQLite PRAGMAs and ordered migration ledger;
3. append-only trigger enforcement;
4. WAL-safe online backup;
5. idempotent, checksum-verified extraction and explicit conflict failure.

Run only this test and confirm it fails because the new modules do not exist.

### 2. GREEN — database foundation

Add:

- `platform/api/molbio_models.py` — compatibility projections plus immutable domain/history models;
- `platform/api/molbio_database.py` — engine, session, PRAGMAs, ordered migrations, health;
- `platform/api/services/sqlite_backup.py` — online backup with integrity verification;
- `platform/api/molbio_migrations.py` — backup-first legacy extraction and verification;
- `platform/api/services/molbio_persistence.py` — sequence/primer revisions, operation lineage, and PCR experiment persistence.

Migration `0001_initial` creates the complete schema. Migration `0002_append_only_guards` creates SQLite update/delete rejection triggers on immutable tables.

### 3. RED/GREEN — route ownership and scientific persistence

Create focused route/service tests proving:

- sequence CRUD reads/writes only the Mol Bio session;
- each sequence/primer mutation creates a new immutable revision;
- PCR simulation persists an experiment by default even when no product document is saved;
- the experiment snapshot includes template revision/checksum, primer snapshots, Biopython Tm implementation/version/settings, cycling/reaction assumptions, result coordinates/product checksum, warnings, notes, review state, provenance, and timestamps;
- `save=true` links the experiment operation to a separate product document/revision;
- an unknown polymerase preset revision fails explicitly;
- idempotency keys do not create duplicate experiments/products.

Cut `nucleotide_sequences.py`, `molbio_ops.py`, and `rna_structure.py` to the dedicated dependency. Keep core `Job` operations on the core session; `msa.py` receives both core and Mol Bio sessions for `sequence_id` resolution.

Startup initializes the Mol Bio store before serving requests and exposes a non-secret health snapshot.

### 4. Backup-first extraction and cutover

1. Quiesce Mol Bio writes for the short extraction window.
2. Online-back up the live core SQLite database into the data-root backup directory.
3. Initialize `/mnt/BioModStack/molbio.db` through owned migrations.
4. Copy legacy sequence and primer rows transactionally.
5. Create initial immutable document/revision records from the copied rows.
6. Verify expected source inventory: **11 sequences, 51,954 total bases, 0 primers**; all per-sequence SHA-256 values match.
7. Verify `PRAGMA foreign_key_check`, orphan queries, and destination `quick_check`.
8. Retain readable online backups of both stores, prove exact ID/content parity and no external FK references, then purge core `primers` followed by `nucleotide_sequences` in one immediate transaction. Retain the now-empty compatibility tables and verify unrelated core row counts are unchanged.

### 5. Verification gates

- focused Mol Bio database/migration tests;
- focused sequence/Mol Bio route tests;
- existing API baseline regression set;
- frontend focused Mol Bio tests and TypeScript build;
- temporary-database backup/restore drill;
- live development API sequence inventory/checksum comparison on port `8002` after restart;
- `git diff --check`, scoped diff audit, secret scan, and two independent read-only reviews.

No commit, merge, production restart, or Turso provisioning occurs without a separate gate.
