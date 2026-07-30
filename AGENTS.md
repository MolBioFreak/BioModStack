# BioModStack Agent Guide

## Scope

BioModStack is a small-team scientific workbench. Keep the repository to code, configuration, tests, schemas, reproducible runtime definitions, and the intentionally tracked mobile APK. Do not add plans, status reports, explainers, generated build output, caches, credentials, or historical source copies.

## Git policy

- `test` is the only development and integration branch.
- `main` is the production/default branch. Never push or merge into it unless Christian explicitly requests a `test → main` promotion.
- Before editing, fetch and record the remote `test` SHA. Rebase a candidate on the current remote tip before pushing.
- Do not force-push, reset remote branches, or bulk-merge historical experimental branches.
- Integrate only current, tested semantic slices; reject older code that weakens current contracts.

## Runtime ownership

- Operator frontend: `http://127.0.0.1:5173`
- Managed API: `http://127.0.0.1:8000`
- Managed database: `/mnt/BioModStack/biomodstack.db`
- The frontend must not proxy an isolated API at port `18002`.
- Prove the source owner, listener, API, and branch SHA before claiming a deployment is live.

## Validation

- Python API tests: run from `platform/api` with:
  ```bash
  uv run --frozen --group dev python -m pytest <focused tests>
  ```
- Frontend: use the repository pnpm workspace/lockfile; do not replace it with npm or alter lockfiles unless dependency work is deliberate.
- Run `git diff --check` before every commit.
- Never stage virtual environments, `node_modules`, frontend build output, logs, generated job results, model weights, or local databases.

## Domain boundaries

- MolBio and NGS exchange immutable handoffs. Keep their code and state contracts separate.
- Protect these independently owned Gibson files unless Christian explicitly directs otherwise:
  - `platform/api/services/assembly/pydna_gibson.py`
  - `platform/api/tests/test_pydna_gibson.py`
- BioXP is a thin relay to robot-authoritative capabilities. Do not add host-side motion, homing, or maintenance authority. Unsupported command families must remain denied/fail-closed.
- Molecular-dynamics work is a supported feature but do not add playback, frame/time mapping, WebM, or new representation-default work without explicit scope.

## Documentation rule

Keep this file as the sole human-facing repository guide for agents. Machine-consumed contracts must live as structured config/schema/test data, not prose under `docs/`. If a current runtime or test reads a document, migrate the consumed data to a code/config location and update the consumer before deleting the document.
