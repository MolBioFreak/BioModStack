BioModStack Database Instructions
=================================

Purpose
-------
This document defines the canonical database location, how it is resolved,
and the rules for reading/writing the DB across services, scripts, and
background tasks. The goal is path agnosticism and consistent access on
any machine.

Canonical DB
------------
- Primary DB is SQLite.
- Default location (current behavior): `platform/api/biomodstack.db`
- If `BMS_DATA` is set and no DB override is provided, DB defaults to
  `${BMS_DATA}/biomodstack.db`.
- Overrides:
  - `DATABASE_URL` (full SQLAlchemy URL, takes precedence)
  - `BMS_DB_PATH` (absolute path to sqlite file)

Path Resolution Rules
---------------------
All DB paths MUST be resolved through `platform/api/paths.py`:
- `get_db_path()` for sqlite file path
- `get_db_url()` for SQLAlchemy engine URL

Do NOT use:
- Hardcoded absolute paths
- `Path(__file__).parent...` ad hoc pathing
- Relative `./biomodstack.db` unless it is resolved by `paths.py`

Session Usage Rules
-------------------
- API routes MUST use async SQLAlchemy sessions from `database.get_session()`.
- Background workers SHOULD use async sessions where possible.
- If a sync sqlite connection is required (e.g., legacy scripts), it MUST use
  `get_db_path()` and should set a busy timeout.

Schema & Migrations
-------------------
- Core schema is defined in `platform/api/database.py`.
- Manual migration scripts live in `platform/api/migrations/`.
- If using migrations, always point them at the canonical DB path via
  `get_db_path()` (no relative paths).
- If auto schema-ensure is enabled, it must only add nullable columns to
  avoid destructive changes.
- Use the migration runner to apply all known migrations:
  - `python platform/api/run_migrations.py`

Operational Notes
-----------------
- Re-ingest deletes and rebuilds `designs` rows for a job.
- Large jobs can create many rows; DB locks can occur if sync and async
  connections are mixed.
- Always prefer a single active DB file per installation.

Sanity Checks
-------------
- On startup, log the resolved DB path.
- Keep `DATABASE_URL` consistent across services (API, workers, scripts).
- If multiple `.db` files exist, archive or remove non-canonical ones.

DB Audit Script
---------------
- Audit all DB files and optionally archive extras:
  - Dry run: `python scripts/db_audit.py`
  - Archive extras: `python scripts/db_audit.py --apply`
