# BioModStack Core Runtime Containerization Spec

> **For Hermes:** Use this as the concrete first-wave containerization target for BioModStack itself. Keep the core runtime portable, keep workflow and hardware edges explicit, and do not regress `/bms/`, `/api`, or Molstar behavior.

**Goal:** Containerize BioModStack’s core software stack — API, production frontend, and persistent control-plane state — without turning Nextflow/Apptainer execution or BioXP runtime lifecycle into first-wave Docker problems.

**Architecture:** The target runtime is a two-container web/control plane: `bms-api` plus `bms-web`. The workstation still owns `systemd --user` supervision and the host-native execution edges. Nextflow/Apptainer and BioXP remain outside the first container bundle, accessed through explicit linkage/adapter boundaries.

**Tech Stack:** FastAPI backend in `platform/api/main.py`, SQLAlchemy + async SQLite in `platform/api/database.py`, path/env resolution in `platform/api/paths.py`, Vite/React frontend in `platform/frontend`, service-layer launcher in `biomodstack_services.py`, host-coupled Nextflow executor in `platform/api/services/nextflow.py`, BioXP linkage/proxy router in `platform/api/routers/bioxp.py`, and Molstar frontend loading in `platform/frontend/src/lib/molstar-loader.ts` plus `platform/frontend/src/components/MolstarViewer.tsx`.

---

## 1. Repo-grounded current state

This spec is grounded in the current repository surfaces:

- There is currently no container scaffolding in the repo:
  - no `Dockerfile*`
  - no `*compose*.y*ml`
- The API already exposes a stable health endpoint in `platform/api/main.py`:
  - `GET /api/health`
- The frontend already has a production build path in `platform/frontend/package.json`:
  - `build`
  - `preview`
- The frontend already preserves the production base path in `platform/frontend/vite.config.ts`:
  - `base: '/bms/'` in production
- The current desktop/runtime seam already exists and should be reused, not discarded:
  - `biomodstack_services.py`
  - `scripts/manage_desktop_services.py`
  - `scripts/run_biomodstack_api.sh`
  - `scripts/run_biomodstack_frontend.sh`
  - `docs/Desktop_Runtime_and_Shell_Architecture.md`
- The current database is SQLite-backed and already supports env/path indirection:
  - `platform/api/database.py`
  - `platform/api/paths.py`
- The current data-root resolution order is host-shaped:
  - `/mnt/BioModStack`
  - `~/.biomodstack`
- `platform/api/services/nextflow.py` is still host-coupled and should not be pulled into the first-wave container boundary as-is.
- `platform/api/routers/bioxp.py` is now correctly oriented around linkage/proxy semantics and explicitly disables workstation-owned daemon lifecycle control.
- Molstar is loaded from installed frontend dependencies rather than a CDN, which lowers frontend-containerization risk:
  - `platform/frontend/src/lib/molstar-loader.ts`
  - `platform/frontend/src/components/MolstarViewer.tsx`
- Existing regression anchors that must continue to pass:
  - `platform/api/tests/test_bioxp_router.py`
  - `platform/api/tests/test_nanopore_nextflow.py`
  - `platform/frontend/tests/structureViewerSemantics.test.ts`

---

## 2. First-wave decision

### In scope for the first container wave

Containerize these now:

- BioModStack API runtime
- BioModStack production frontend serving layer
- persistent control-plane state
- persistent inputs/results mounts used by the core runtime
- browser-facing reverse proxy/static serving layer for `/bms/`

### Explicitly out of scope for the first container wave

Do not containerize these in the same first-wave PR:

- Nextflow CLI ownership
- Apptainer/Singularity ownership
- GPU/model/runtime ownership for structure workflows
- BioXP robot-local daemon lifecycle
- robot SSH/Tailscale/process supervision
- any attempt to make the API container run host hardware workflows directly without an explicit adapter boundary

### Default interpretation

For this repo, “containerize BioModStack itself” means:

- yes: web/control plane containers
- no: shove Nextflow and robot runtime ownership into those same containers
- until a real host workflow adapter exists, treat the compose runtime as honest for UI/API/control-plane compatibility, not as the owner of workflow execution truth
- make that boundary executable: the core-runtime stack should run with `BMS_CORE_RUNTIME_MODE=1`, which disables workflow launch/resume/resubmit and GPU scheduler ownership inside the containerized API

---

## 3. Target runtime topology

### Recommended deployed shape

```text
Browser / desktop shell / Tailscale Serve
                |
                v
          +-------------+
          |   bms-web   |
          |   nginx     |
          |  serves     |
          |   /bms/     |
          | proxies     |
          |   /api      |
          +-------------+
                 |
                 v
          +-------------+
          |   bms-api   |
          |  FastAPI    |
          | SQLite on   |
          | mounted dir |
          +-------------+
                 |
       -------------------------
       |                       |
       v                       v
host-native workflow     BioXP robot-local runtime
adapter / executor       linked over HTTP proxy
```

### Default service names

Use these names in the container stack:

- `bms-api`
- `bms-web`

### Default host port contract

Keep the current host-facing contract stable:

- `127.0.0.1:8000` -> API
- `127.0.0.1:5173` -> web entrypoint serving `/bms/`

This preserves compatibility with the current service-layer health checks and existing browser/open-shell expectations while replacing the Vite dev server with a real production web server.

---

## 4. Exact repo file map for the first-wave containerization work

### Create

```text
docker/
├── api.Dockerfile
├── web.Dockerfile
└── web/
    └── nginx.conf

compose.core-runtime.yml
.env.core-runtime.example
.dockerignore
scripts/run_biomodstack_core_runtime.sh
```

### Modify

```text
biomodstack_services.py
scripts/manage_desktop_services.py
docs/Desktop_Runtime_and_Shell_Architecture.md
docs/Workstation Set Up and Install Guide.md
README.md
scripts/run_biomodstack_api.sh
scripts/run_biomodstack_frontend.sh
platform/api/services/nextflow.py
```

### Preserve, but reclassify as dev-only helpers

These should remain available for local development and debugging, but should stop being described as the durable production runtime:

- `scripts/run_biomodstack_api.sh`
- `scripts/run_biomodstack_frontend.sh`

---

## 5. Proposed image structure

## 5.1 `docker/api.Dockerfile`

### Purpose

Build the production BioModStack API image.

### Default image design

- base image: pinned Python slim image
- build/install using the existing `platform/api/pyproject.toml` and `platform/api/uv.lock`
- run as non-root
- expose port `8000`
- use `uvicorn main:app --host 0.0.0.0 --port 8000`
- no `--reload`
- writable state only through mounted paths

### Required build context assumptions

The Docker build context should be repo root because the API imports and runtime path helpers rely on repo-relative assets.

### Default runtime env contract

Inside the API container:

- `BMS_HOME=/app`
- `BMS_DATA=/var/lib/biomodstack`
- `BMS_INPUTS=/var/lib/biomodstack/inputs`
- `BMS_DB_PATH=/var/lib/biomodstack/biomodstack.db`
- `DATABASE_URL=sqlite+aiosqlite:////var/lib/biomodstack/biomodstack.db`

### Default health check

Use the existing API health route:

- `GET /api/health`

---

## 5.2 `docker/web.Dockerfile`

### Purpose

Build the production frontend and serve it from a real web server.

### Default image design

- multi-stage build
- builder stage uses Node + pnpm workspace install from repo root
- frontend build target is `platform/frontend`
- runtime stage uses nginx
- compiled frontend assets are served under `/bms/`
- `/api/` is proxied to `bms-api:8000`

### Why this is the correct default

The repo already has:

- frontend build support in `platform/frontend/package.json`
- workspace packages declared in `pnpm-workspace.yaml`
- production `/bms/` base-path handling in `platform/frontend/vite.config.ts`

This means the correct production runtime is “built assets behind nginx”, not “long-lived `npm run dev` supervised by systemd”.

---

## 5.3 `docker/web/nginx.conf`

### Purpose

Own the stable browser contract.

### Required responsibilities

- serve `/bms/` from built frontend assets
- proxy `/api/` to `http://bms-api:8000`
- keep websocket/proxy headers sane if needed later
- avoid redirect churn that would break viewer asset paths

### Default route contract

- `/bms/` -> frontend
- `/api/` -> backend
- `/` -> redirect to `/bms/`

---

## 6. Proposed `compose.core-runtime.yml`

This is the default compose shape the repo should target.

```yaml
services:
  bms-api:
    build:
      context: .
      dockerfile: docker/api.Dockerfile
    container_name: biomodstack-api
    restart: unless-stopped
    environment:
      BMS_HOME: /app
      BMS_DATA: /var/lib/biomodstack
      BMS_INPUTS: /var/lib/biomodstack/inputs
      BMS_DB_PATH: /var/lib/biomodstack/biomodstack.db
      DATABASE_URL: sqlite+aiosqlite:////var/lib/biomodstack/biomodstack.db
      CORS_ORIGINS: http://127.0.0.1:5173,http://localhost:5173
      BMS_WORKFLOW_ADAPTER_URL: http://host.docker.internal:8787
      BIOXP_SERVER_URL: ${BIOXP_SERVER_URL:-}
    extra_hosts:
      - host.docker.internal:host-gateway
    volumes:
      - ${BMS_STATE_DIR:-/mnt/BioModStack}:/var/lib/biomodstack
    ports:
      - 127.0.0.1:8000:8000
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5)"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 20s

  bms-web:
    build:
      context: .
      dockerfile: docker/web.Dockerfile
    container_name: biomodstack-web
    restart: unless-stopped
    depends_on:
      bms-api:
        condition: service_healthy
    ports:
      - 127.0.0.1:5173:80
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1/bms/"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 10s
```

### Why this compose contract is the right default

- it preserves the current host-facing API/frontend ports
- it removes Vite dev-server ownership from production
- it keeps API and web images separate
- it keeps state mounted explicitly
- it leaves room for a host-side workflow adapter without pretending the API container owns host GPU/runtime reality

---

## 7. Data and volume contract

### Default persistent host mount

Use a bind mount, not an opaque Docker named volume, for the first wave.

Default host path:

- `/mnt/BioModStack` if present
- otherwise `~/.biomodstack`

### Why bind mounts are the correct default here

The repo’s current path logic in `platform/api/paths.py` already treats workstation-visible host paths as the authoritative state/results location. Bind mounts make the container runtime compatible with that operational model and make it easier to inspect results, logs, and DB files from the host.

### Minimum required persisted directories/files

Under the chosen state root, preserve:

- `biomodstack.db`
- `inputs/`
- `bms_results/`
- `analysis_cache/`
- any future lightweight control-plane state files

### Transitional path-compatibility mode

If the Nextflow boundary cleanup is incomplete and the API still emits absolute host paths that must remain identical from both sides, use a temporary same-path bind strategy:

- host `/mnt/BioModStack` mounted into container at `/mnt/BioModStack`
- `BMS_DATA=/mnt/BioModStack`

This is not the clean long-term contract, but it is the least risky bridge while the workflow adapter boundary is being extracted.

---

## 8. Workstation launcher integration

### Required direction

Do not discard the current service-layer architecture. Repoint it.

The Linux workstation should keep using `systemd --user`, but `systemd --user` should launch the compose stack instead of owning raw uvicorn and Vite dev children.

### Required repo changes

#### `scripts/run_biomodstack_core_runtime.sh`

Create a wrapper script that:

- resolves repo root
- loads `~/.biomodstack/env.sh` if present
- invokes `docker compose -f compose.core-runtime.yml up --build --remove-orphans`
- can be used by `systemd --user`

#### `biomodstack_services.py`

Add a production/container runtime mode that:

- renders a `biomodstack-core-runtime.service`
- uses the compose wrapper as `ExecStart`
- uses `docker compose -f compose.core-runtime.yml down` for `ExecStop`
- preserves the existing API and frontend health URLs
- keeps the current status UX intact

#### `scripts/manage_desktop_services.py`

Extend the control script so operators can manage:

- dev runtime mode
- container runtime mode

without changing the shell entrypoints they already know.

### Recommended service naming

For production/container runtime:

- `biomodstack-core-runtime.service`
- `biomodstack.target`

For local development, keep the existing:

- `biomodstack-api.service`
- `biomodstack-frontend.service`

This keeps dev and prod ownership models explicit instead of pretending they are the same process shape.

---

## 9. `.dockerignore` contract

Create `.dockerignore` at repo root.

Minimum exclusions:

```text
.git
.venv
**/__pycache__
**/.pytest_cache
platform/frontend/node_modules
platform/frontend/dist
platform/api/.venv
work
bms_results
analysis_cache
*.db
```

This keeps images reproducible and prevents local workstation artifacts from being baked into them.

---

## 10. Nextflow boundary rule

`platform/api/services/nextflow.py` is the primary reason this cannot be treated as a trivial Docker retrofit.

### Required rule

Before the API container becomes the default production runtime, the workflow-launch path must stop assuming that the API process directly owns:

- host repo root
- host work directory
- host Apptainer container directory
- host MSA/cache/database path layout
- direct `nextflow run`/resume semantics from inside the API runtime itself

### Practical target

Move toward this contract:

- API owns job intent, metadata, and result presentation
- host adapter owns workflow execution reality

That can be implemented as a thin host adapter later, but the spec boundary must be honored now.

### Minimum spec consequence

The compose file should already reserve room for a future workflow adapter via:

- `BMS_WORKFLOW_ADAPTER_URL`
- `host.docker.internal`

Even if the first implementation pass keeps a transitional bridge internally, the spec should not bless direct long-term API-container ownership of host Nextflow/Apptainer state.

---

## 11. BioXP handling in this spec

BioXP should be included in the spec as an integration boundary, not as a first-wave container.

### Current repo-aligned handling

`platform/api/routers/bioxp.py` already reflects the right direction:

- persisted linkage URL
- robot-local runtime recommendation
- HTTP proxying through the BMS API
- explicit refusal to own robot daemon lifecycle from the workstation path

### Spec decision

For first-wave containerization:

- keep BioXP runtime robot-local
- let containerized BMS keep linkage/proxy semantics
- do not bundle robot runtime ownership into `compose.core-runtime.yml`

### Allowed env/config in the containerized API

- `BIOXP_SERVER_URL`
- persisted linkage state inside the mounted data root if desired

That keeps robotics present in the spec sheet without letting it block BMS core-runtime containerization.

---

## 12. Not chosen for the first wave

These are explicit non-goals for this spec:

- no Postgres migration in the first container PR
- no Celery/Redis/Kafka sidecars
- no all-in-one “one big container” image
- no Electron/Tauri packaging work mixed into the runtime container PR
- no attempt to ship Nextflow/Apptainer/GPU workflows inside the API image
- no BioXP runtime container in the first core-runtime bundle

This is intentionally a disciplined first wave, not an everything-bagel infrastructure rewrite.

---

## 13. Acceptance criteria

The first-wave containerization is successful when all of the following are true:

1. `docker compose -f compose.core-runtime.yml up` brings up the BMS web/control plane reproducibly.
2. `http://127.0.0.1:5173/bms/` loads successfully.
3. `http://127.0.0.1:8000/api/health` returns healthy.
4. The frontend is being served from built assets, not `npm run dev`.
5. The API is running without `--reload`.
6. SQLite state survives restart because it lives on the host bind mount.
7. Molstar viewers still render through the same browser contract.
8. Existing BioXP linkage/proxy behavior still works when configured.
9. A real Nextflow smoke launch still works through the host-native path.
10. Nextflow resume behavior still works.
11. The desktop/service launcher can start, stop, and inspect the containerized runtime cleanly.

---

## 14. Implementation order implied by this spec

1. Harden dev-vs-prod launcher behavior in:
   - `scripts/run_biomodstack_api.sh`
   - `scripts/run_biomodstack_frontend.sh`
   - `biomodstack_services.py`
2. Extract or formalize the workflow adapter boundary around `platform/api/services/nextflow.py`.
3. Add the repo-root container scaffolding:
   - `docker/api.Dockerfile`
   - `docker/web.Dockerfile`
   - `docker/web/nginx.conf`
   - `compose.core-runtime.yml`
   - `.dockerignore`
   - `.env.core-runtime.example`
4. Repoint the Linux launcher to the compose runtime.
5. Run browser + workflow + BioXP smoke validation before expanding scope.

---

## 15. Summary

The correct first-wave containerization target for this repository is:

- `bms-api` container
- `bms-web` container
- explicit bind-mounted state
- current browser route contract preserved
- current service-layer launcher reused
- Nextflow and BioXP kept as explicit host/runtime edges

That is the cleanest way to containerize BioModStack itself, without pretending the host-coupled workflow and robotics surfaces are already portable.
