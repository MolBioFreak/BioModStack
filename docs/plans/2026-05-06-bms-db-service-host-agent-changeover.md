# BMS DB Service + Generic Host Agent Changeover Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task after Christian approves the phase slice.

**Goal:** Rename/surface the database runtime as **BMS DB service**, remove DB-offline hard startup gates, and replace one-off privileged helpers such as `bms-cpu-power` with a generic, host-portable `bms-host-agent` boundary.

**Architecture:** Keep `bms-api` as the unprivileged UI/control-plane API. Add a local-only Host Agent that discovers host capabilities and performs narrow privileged host/service operations through typed providers. The BMS DB service remains one Postgres container with two logical databases (`bms_core_runtime`, `bms_analytical_data`), while the UI shows DB/container status in the top bar instead of making DB liveness an invisible boot prerequisite.

**Tech Stack:** FastAPI/Python backend, React/Vite frontend, Docker Compose core runtime, host-native systemd user/service runner, Postgres 16, SQLAlchemy/asyncpg, optional Docker CLI/SDK, optional NVML/nvidia-smi, Linux `/sys`/RAPL capability probes.

---

## Verified current state

From the live repo at `/home/dalab/biomodstack/biomodstack` on 2026-05-06:

- `compose.core-runtime.yml` currently defines:
  - `bms-analytical-postgres` / `biomodstack-analytical-postgres`
  - `bms-api` / `biomodstack-api`
  - `bms-cpu-power` / `biomodstack-cpu-power`
  - `bms-stats-tools` / `biomodstack-stats-tools`
  - `bms-web` / `biomodstack-web`
- `bms-api` currently has a hard compose `depends_on` health gate on `bms-analytical-postgres`.
- `bms-stats-tools` currently has a hard compose `depends_on` health gate on `bms-analytical-postgres`.
- `bms-api` currently mounts `/var/run/docker.sock` and uses direct Docker control for stats-tools lifecycle.
- `platform/api/main.py` initializes the analytical store during lifespan when `BMS_ANALYTICAL_INIT_ON_STARTUP` is truthy; that currently needs to become soft/degraded.
- `platform/api/database.py` is still the core SQLite-backed runtime DB surface.
- `platform/api/services/assay_analytical_store.py` is the analytical Postgres DB surface.
- `platform/frontend/src/components/StatsToolsControlPanel.tsx` already provides the model for top-bar lifecycle/status UI.

---

## Naming contract

### Product/UI name

Use exactly:

```text
BMS DB service
```

Where space is tight in the top bar, the button can read:

```text
BMS DB
```

But the panel title, API `display_name`, docs, and CLI help should use **BMS DB service**.

### Stable machine identifiers

Use these normalized IDs internally:

```text
component: db-service
service_id: bms-db-service
display_name: BMS DB service
```

### Compose/container target

Near-term transitional support:

```text
existing compose service: bms-analytical-postgres
existing container: biomodstack-analytical-postgres
```

Clean target after migration:

```text
compose service: bms-db
container: biomodstack-db
volume: bms_db_service_data
```

Do not make the UI say `analytical-postgres` once the operator-facing panel exists; that name is a transitional implementation detail.

As of the 2026-05-30 naming cleanup, source-level compose and UI names use `bms-db` / `biomodstack-db` / **BMS DB service**. Existing live deployments may still have the old `bms-analytical-postgres` container until the stateful DB is explicitly migrated or recreated from backup; discovery may tolerate that legacy name, but operator-facing status must not present it as the product name.

---

## Host Agent purpose

The Host Agent is the local-only trusted process for host/system reality:

- service/container state inspection
- service/container lifecycle actions
- CPU/RAPL telemetry
- GPU telemetry when containers cannot see GPUs correctly
- GPU power caps when writable
- fan controls via CoolerControl or `nvidia-settings` when available
- optional systemd/user-unit state
- host resource/OS capability discovery

The Host Agent is **not**:

- a second product API
- a generic shell executor
- a stats-tools worker
- a Postgres container
- a place to hardcode Christian's workstation topology

Rule:

```text
bms-api validates policy and serves the product API.
bms-host-agent performs narrow local host operations and reports capabilities.
```

---

## Portability requirements: no hardcoding OUR runtime

The Host Agent must work on different Linux hosts by discovery + config, not by assuming DALAB/fatboy hardware.

### Forbidden assumptions

Do not hardcode:

- `/mnt/BioModStack` as the only state root
- `/home/dalab` as an install root
- `workstation_ryzen7960x` as a profile
- a four-GPU host
- GPU IDs `0,1,2,3`
- GPU 1 as display GPU
- RTX 3090/5090/5060 names
- Intel-only RAPL path like `/sys/class/powercap/intel-rapl:0/energy_uj`
- Docker as always present
- systemd as always available inside containers
- CoolerControl as always installed
- NVIDIA as always installed
- current container names as the only service lookup path

### Required behavior on unsupported hosts

Unsupported capabilities return structured unavailable diagnostics, not fake values and not 500s.

Example:

```json
{
  "capability": "cpu.rapl_power",
  "available": false,
  "writable": false,
  "reason": "no readable energy_uj counters discovered under /sys/class/powercap"
}
```

### Discovery precedence

For every host-specific thing:

1. explicit config file
2. environment override
3. runtime discovery by labels/probes
4. unavailable/degraded response

Never silently fall back to Christian's specific workstation.

---

## Host Agent deployment shape

Preferred first implementation: host-native local service.

```text
bms-host-agent
bind: 127.0.0.1 only
port default: 8798
URL env: BMS_HOST_AGENT_URL=http://127.0.0.1:8798
config env: BMS_HOST_AGENT_CONFIG=/path/to/host-agent.toml
```

Alternative later: privileged container, but only if its mounts/capabilities are explicit. Do not use a vague `privileged: true` junk drawer as the default product design.

### Proposed files

Create:

- `platform/host_agent/__init__.py`
- `platform/host_agent/app.py`
- `platform/host_agent/config.py`
- `platform/host_agent/models.py`
- `platform/host_agent/providers/__init__.py`
- `platform/host_agent/providers/docker_service.py`
- `platform/host_agent/providers/systemd_service.py`
- `platform/host_agent/providers/rapl_power.py`
- `platform/host_agent/providers/gpu_inventory.py`
- `platform/host_agent/providers/fan_control.py`
- `platform/host_agent/providers/os_inventory.py`
- `platform/api/services/host_agent_client.py`
- `scripts/run_bms_host_agent.sh`

Modify:

- `biomodstack_services.py`
- `compose.core-runtime.yml`
- `.env.core-runtime.example`
- `platform/api/routers/system.py`
- `platform/api/services/stats_tools.py`
- `platform/api/routers/gpu.py`
- `platform/api/main.py`
- `platform/frontend/src/components/Layout.tsx`
- `platform/frontend/src/components/StatsToolsControlPanel.tsx` or new shared utility panel

Tests:

- `platform/api/tests/test_host_agent_app.py`
- `platform/api/tests/test_host_agent_config.py`
- `platform/api/tests/test_db_service_status.py`
- `platform/api/tests/test_core_runtime_scaffold.py`
- `platform/api/tests/test_cpu_power_telemetry.py`
- `platform/frontend/tests/dbServiceMenuContract.test.ts`
- `platform/frontend/tests/hostAgentSourcePortability.test.ts`

---

## Host Agent config contract

Config should support both static descriptors and label-based discovery.

Example TOML:

```toml
[agent]
bind_host = "127.0.0.1"
port = 8798
allow_write_actions = true

[discovery]
docker_enabled = true
systemd_enabled = true
prefer_docker_labels = true

[[services]]
id = "bms-db-service"
display_name = "BMS DB service"
component = "db-service"
provider = "docker"
optional_at_boot = true
compose_project = "biomodstack-core-runtime"
compose_file = "compose.core-runtime.yml"
compose_services = ["bms-db", "bms-analytical-postgres"]
container_names = ["biomodstack-db", "biomodstack-analytical-postgres"]
labels = { "org.biomodstack.component" = "db-service" }
allowed_actions = ["status", "start", "restart", "logs"]

[[services]]
id = "bms-stats-tools"
display_name = "Stats-tools"
component = "stats-tools"
provider = "docker"
optional_at_boot = true
compose_project = "biomodstack-core-runtime"
compose_file = "compose.core-runtime.yml"
compose_services = ["bms-stats-tools"]
container_names = ["biomodstack-stats-tools"]
labels = { "org.biomodstack.component" = "stats-tools" }
allowed_actions = ["status", "start", "stop", "restart", "logs"]
```

Important: defaults can include the current BMS service names, but they must live in config/default descriptors and be overrideable. Provider logic must not hardcode them.

---

## Docker label contract

Add labels so the Host Agent can discover services without relying only on container names.

Example for DB service:

```yaml
labels:
  org.biomodstack.service_id: bms-db-service
  org.biomodstack.component: db-service
  org.biomodstack.display_name: BMS DB service
  org.biomodstack.optional_at_boot: "true"
```

Example for stats-tools:

```yaml
labels:
  org.biomodstack.service_id: bms-stats-tools
  org.biomodstack.component: stats-tools
  org.biomodstack.display_name: Stats-tools
  org.biomodstack.optional_at_boot: "true"
```

Discovery order for service lookup:

1. label match
2. configured container name
3. configured compose service
4. degraded `missing` descriptor

---

## Host Agent API contract

### Health

```http
GET /health
```

Response:

```json
{
  "ok": true,
  "component": "bms-host-agent",
  "version": "0.1.0",
  "host_id": "opaque-local-id"
}
```

### Capability summary

```http
GET /api/host-agent/capabilities
```

Response shape:

```json
{
  "component": "bms-host-agent",
  "host": {
    "os_id": "pop",
    "os_version": "22.04",
    "kernel": "...",
    "arch": "x86_64"
  },
  "capabilities": {
    "docker": { "available": true, "writable": true, "reason": null },
    "systemd_user": { "available": true, "writable": true, "reason": null },
    "cpu_rapl_power": { "available": true, "writable": false, "reason": null },
    "gpu_inventory": { "available": true, "provider": "nvidia-smi", "count": 4 },
    "gpu_power_control": { "available": true, "writable": true, "reason": null },
    "fan_control": { "available": false, "writable": false, "reason": "CoolerControl not configured" }
  }
}
```

### Service status list

```http
GET /api/host-agent/services
```

Each service descriptor:

```json
{
  "id": "bms-db-service",
  "display_name": "BMS DB service",
  "component": "db-service",
  "provider": "docker",
  "state": "running",
  "health": "healthy",
  "runtime_available": true,
  "optional_at_boot": true,
  "container": {
    "name": "biomodstack-analytical-postgres",
    "image": "postgres:16-alpine",
    "status": "running",
    "health": "healthy",
    "created_at": "...",
    "started_at": "..."
  },
  "actions": {
    "status": { "available": true },
    "start": { "available": true },
    "restart": { "available": true },
    "logs": { "available": true },
    "stop": { "available": false, "reason": "hidden for DB service by policy" }
  }
}
```

### One service status

```http
GET /api/host-agent/services/bms-db-service
```

### Service action

```http
POST /api/host-agent/services/bms-db-service/start
POST /api/host-agent/services/bms-db-service/restart
POST /api/host-agent/services/bms-db-service/logs
```

Rules:

- actions are allowlisted per service
- no arbitrary shell command endpoint
- action output is tailed/truncated
- no secret-bearing env vars are returned
- post-action status is included in the response

---

## BMS API DB service contract

Add BMS API endpoints that the frontend calls. The API can combine:

- Host Agent service/container state
- direct DB connectivity/schema checks
- current core storage mode

Routes:

```http
GET  /api/system/db-service
POST /api/system/db-service/{action}
```

Response:

```json
{
  "component": "db-service",
  "service_id": "bms-db-service",
  "display_name": "BMS DB service",
  "state": "running",
  "health": "degraded",
  "runtime_available": false,
  "optional_at_boot": true,
  "control_mode": "host-agent",
  "container_name": "biomodstack-analytical-postgres",
  "service_name": "bms-analytical-postgres",
  "host_agent_available": true,
  "offline_message": "db_service_offline — use BMS DB service → Start",
  "commands": [
    "bms db-service status",
    "bms db-service start",
    "bms db-service restart",
    "bms db-service logs --tail 120"
  ],
  "logical_databases": [
    {
      "name": "bms_core_runtime",
      "role": "core-runtime",
      "storage_mode": "sqlite-legacy",
      "status": "legacy-fallback-active",
      "reachable": true,
      "note": "Core runtime is still using SQLite during migration"
    },
    {
      "name": "bms_analytical_data",
      "role": "assay-analytics",
      "storage_mode": "postgres",
      "status": "unreachable",
      "reachable": false,
      "note": "Postgres container is stopped or DB connection failed"
    }
  ],
  "logs": "..."
}
```

Endpoint requirements:

- never 500 just because Docker/Host Agent/Postgres is offline
- local-admin gated for lifecycle actions
- status route can return degraded diagnostics without requiring write permissions
- password/DSN values must be redacted

---

## Frontend UI contract

Add a top-bar menu modeled after `StatsToolsMenu`.

Proposed file:

- `platform/frontend/src/components/DbServiceControlPanel.tsx`

Top bar:

```text
[BMS DB ●]
```

Panel title:

```text
BMS DB service
```

Panel fields:

- state
- health
- service/container name
- control mode: `host-agent`, `docker-direct-transitional`, or `unavailable`
- DB container state
- current core DB mode: `SQLite legacy fallback` or `Postgres`
- analytical DB status
- DBs present/reachable:
  - `bms_core_runtime`
  - `bms_analytical_data`
- operator commands
- logs tail

Controls:

- Refresh
- Start BMS DB service
- Restart BMS DB service
- Health
- Logs
- Stop only under an explicit advanced/admin section, not as the prominent DB action

Offline copy:

```text
db_service_offline — use BMS DB service → Start
```

---

## Remove DB hard startup gates

### Compose changes

Modify `compose.core-runtime.yml`:

1. Remove `bms-api.depends_on.bms-analytical-postgres.condition: service_healthy`.
2. Remove `bms-stats-tools.depends_on.bms-analytical-postgres.condition: service_healthy`.
3. Keep DB healthcheck for visibility.
4. Keep `bms-web` depending on API health.
5. Add labels for BMS DB service and stats-tools.

Near-term API startup should not wait for DB. If DB is off, API starts and status endpoint reports `BMS DB service` offline.

### API startup changes

Modify `platform/api/main.py`:

```python
if os.getenv("BMS_ANALYTICAL_INIT_ON_STARTUP", "0").strip().lower() in {"1", "true", "yes", "on"}:
    try:
        await init_analytical_store()
        logger.info("[STARTUP] Assay analytical PostgreSQL store initialized")
    except Exception as exc:
        logger.warning("[STARTUP] BMS DB service unavailable for analytical init: %s", exc)
```

Do not raise from lifespan solely because analytical Postgres is down.

### DB-backed endpoint behavior

DB-backed assay/stat routes should return an honest degraded response when the DB is unavailable:

```json
{
  "detail": "BMS DB service unavailable",
  "component": "db-service",
  "degraded_by": "bms-db-service",
  "offline_message": "db_service_offline — use BMS DB service → Start"
}
```

Do not fabricate successful qPCR/HPLC/DOE results if DB or stats-tools is offline.

---

## One Postgres container / two logical DBs

Clean target service:

```yaml
bms-db:
  image: postgres:16-alpine
  container_name: biomodstack-db
  restart: unless-stopped
  environment:
    POSTGRES_DB: postgres
    POSTGRES_USER: ${BMS_DB_SUPERUSER:-bms_admin}
    POSTGRES_PASSWORD: ${BMS_DB_PASSWORD:-bms_dev}
    BMS_CORE_DB_NAME: ${BMS_CORE_DB_NAME:-bms_core_runtime}
    BMS_ANALYTICAL_DB_NAME: ${BMS_ANALYTICAL_DB_NAME:-bms_analytical_data}
  volumes:
    - bms_db_data:/var/lib/postgresql/data
    - ./docker/postgres/init-bms-databases.sh:/docker-entrypoint-initdb.d/010-init-bms-databases.sh:ro
  labels:
    org.biomodstack.service_id: bms-db-service
    org.biomodstack.component: db-service
    org.biomodstack.display_name: BMS DB service
    org.biomodstack.optional_at_boot: "true"
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U ${BMS_DB_SUPERUSER:-bms_admin} -d postgres"]
```

Create:

- `docker/postgres/init-bms-databases.sh`

Responsibilities:

- create `bms_core_runtime` if missing
- create `bms_analytical_data` if missing
- create app users/roles if needed
- be idempotent

Do the rename/migration in a later phase. First phase can expose the current `bms-analytical-postgres` as **BMS DB service** so the UX/control-plane gets right before the stateful rename.

---

## Changeover phases

### Phase 1 — Soft DB boot + BMS DB service UI

Objective: stop blocking startup and show DB state visibly.

Tasks:

1. Add failing backend tests for API startup soft-failing analytical init.
2. Patch `platform/api/main.py` to log/warn instead of raise on analytical init failure.
3. Add `platform/api/services/db_service.py` for DB/container status, initially using direct Docker transitional logic if Host Agent is absent.
4. Add `/api/system/db-service` and `/api/system/db-service/{action}` to `platform/api/routers/system.py`.
5. Add frontend `DbServiceControlPanel.tsx` and `DbServiceMenu` in `Layout.tsx`.
6. Update compose tests to remove hard DB `depends_on` gates for API/stats-tools.
7. Add frontend contract test for `BMS DB service` top-bar menu.

Acceptance:

- API starts with DB container stopped.
- Web loads with DB container stopped.
- Top bar shows BMS DB service red/offline.
- DB-backed routes fail honestly/degraded.
- No fake assay/stat output.

### Phase 2 — Generic Host Agent scaffold

Objective: introduce host agent without moving all controls yet.

Tasks:

1. Add `platform/host_agent` package.
2. Add config loader supporting TOML/env/defaults.
3. Add capability models with `available`, `writable`, `reason`, `provider`.
4. Add OS inventory provider.
5. Add Docker service provider with label/config/name lookup.
6. Add RAPL provider using glob discovery under configurable powercap root.
7. Add minimal FastAPI app routes.
8. Add `scripts/run_bms_host_agent.sh`.
9. Add systemd/user service generation in `biomodstack_services.py`.

Acceptance:

- Host Agent starts on `127.0.0.1:8798`.
- `/health` is 200.
- `/api/host-agent/capabilities` works on hosts with no GPU/RAPL/Docker by returning unavailable diagnostics.
- No DALAB/fatboy/GPU ordinal literals in Host Agent source.

### Phase 3 — Move CPU power into Host Agent

Objective: replace `bms-cpu-power` functionality while keeping compatibility.

Tasks:

1. Add Host Agent route `GET /api/host-agent/telemetry/cpu-power`.
2. Add API client support in `platform/api/routers/gpu.py` or a dedicated service layer.
3. Make `BMS_HOST_AGENT_URL` preferred over `BMS_CPU_POWER_COLLECTOR_URL`.
4. Keep `BMS_CPU_POWER_COLLECTOR_URL` as a transitional fallback.
5. Update tests to verify nullable CPU power when RAPL is missing/unreadable.

Acceptance:

- CPU watts still reports on current workstation when readable.
- On unsupported hosts, CPU watts is null/unavailable with a reason.
- No fabricated `0W`, TDP, or fixed Threadripper budget.

### Phase 4 — Move service lifecycle through Host Agent

Objective: remove Docker-socket dependency from `bms-api`.

Tasks:

1. Add Host Agent service status/action routes for `bms-db-service` and `bms-stats-tools`.
2. Add `platform/api/services/host_agent_client.py`.
3. Update `platform/api/services/stats_tools.py` to prefer Host Agent, with direct Docker fallback only during transition.
4. Update new `db_service.py` to prefer Host Agent.
5. Remove `/var/run/docker.sock` mount from `bms-api` once tests/live smoke pass.
6. Update compose tests to assert API no longer mounts Docker socket.

Acceptance:

- BMS DB service Start/Restart/Logs works through Host Agent.
- Stats-tools Start/Stop/Restart/Logs works through Host Agent.
- API container no longer needs Docker socket.
- If Host Agent is stopped, API status endpoints degrade clearly instead of crashing.

### Phase 5 — Rename DB container and add second logical DB

Objective: make the stateful DB match the target architecture.

Tasks:

1. Add `docker/postgres/init-bms-databases.sh`.
2. Add new compose service name `bms-db` / container `biomodstack-db`.
3. Preserve migration path from existing `bms_analytical_postgres_data` volume.
4. Add env vars:
   - `BMS_DB_HOST`
   - `BMS_DB_PORT`
   - `BMS_CORE_DATABASE_URL`
   - `BMS_ANALYTICAL_DATABASE_URL`
   - `BMS_CORE_DB_NAME=bms_core_runtime`
   - `BMS_ANALYTICAL_DB_NAME=bms_analytical_data`
5. Do not switch core runtime from SQLite to Postgres until backup/export/import tests exist.
6. Later add Alembic or equivalent migration discipline for core DB.

Acceptance:

- One Postgres container runs.
- Both logical DBs exist.
- Analytical store points to `bms_analytical_data`.
- Core status reports `sqlite-legacy` until migration is explicitly done.

### Phase 6 — Remove old one-off CPU container

Objective: finish the Host Agent changeover.

Tasks:

1. Remove `bms-cpu-power` from compose after Host Agent CPU power is validated.
2. Delete or archive `platform/api/tools/cpu_power_collector.py` only after fallback is no longer needed.
3. Update `test_core_runtime_scaffold.py` expected service set.
4. Update docs and operator commands.

Acceptance:

- No standalone `bms-cpu-power` container remains in clean target.
- Host Agent is the sole CPU/RAPL telemetry source.
- API still renders nullable/unavailable CPU power correctly on unsupported hosts.

---

## Test strategy

### Backend targeted tests

Run from repo root:

```bash
PYTHONPATH=platform/api python3 -m pytest -q \
  platform/api/tests/test_db_service_status.py \
  platform/api/tests/test_host_agent_config.py \
  platform/api/tests/test_host_agent_app.py \
  platform/api/tests/test_system_router.py \
  platform/api/tests/test_core_runtime_scaffold.py \
  platform/api/tests/test_cpu_power_telemetry.py
```

### Frontend tests

From `platform/frontend`:

```bash
./node_modules/.bin/tsc -p tsconfig.tests.json --pretty false
node --test node_modules/.tmp/frontend-tests/tests/dbServiceMenuContract.test.js
```

### Source portability guard

Add a source guard test that checks Host Agent source does not contain workstation-only literals:

- `workstation_ryzen7960x`
- `RTX 3090`
- `RTX 5090`
- `0,1,2,3`
- `GPU 1 is the display`
- `/home/dalab`
- `/mnt/BioModStack` as a non-config default inside Host Agent provider logic

Allowed locations: docs/tests/examples only when clearly marked.

---

## Live validation gates

### DB-offline boot gate

1. Stop BMS DB service.
2. Start/restart core runtime.
3. Verify:
   - `/api/health` returns 200
   - `/bms/` loads
   - `/api/system/db-service` returns offline/degraded payload
   - top bar shows BMS DB service offline
   - assay DB-backed actions fail honestly with degraded DB message

### DB-online gate

1. Start BMS DB service from top bar or CLI.
2. Verify:
   - `/api/system/db-service` reports running/healthy
   - `bms_analytical_data` reachable
   - `bms_core_runtime` reports `sqlite-legacy` until migration phase
   - logs display without secrets

### Host Agent absent gate

1. Stop Host Agent.
2. Verify:
   - API remains up
   - `/api/system/db-service` reports `host_agent_available=false`
   - lifecycle actions report unavailable instead of 500
   - UI shows degraded message

### Host Agent portability gate

Run Host Agent with Docker unavailable, no readable RAPL, and no GPUs if possible/simulated in tests.

Verify:

- no crash
- no fake CPU watts
- `gpus: []` or `gpu_inventory.available=false`
- all unavailable capabilities include reasons

---

## CLI/operator command target

Add commands under `scripts/bms` or service manager equivalent:

```bash
bms db-service status
bms db-service start
bms db-service restart
bms db-service logs --tail 120

bms host-agent status
bms host-agent start
bms host-agent restart
bms host-agent logs --tail 120
```

Stop command for DB should exist only as an explicit advanced/admin command if needed:

```bash
bms db-service stop --i-know-this-disables-db-backed-features
```

---

## Definition of done

This changeover is done when:

- Product-facing name is **BMS DB service**.
- Core runtime starts when DB container is stopped.
- UI top bar shows DB status and actions.
- DB-backed routes degrade honestly.
- Host Agent is local-only and capability-based.
- Host Agent does not hardcode Christian's specific host/GPU/path topology.
- CPU/RAPL telemetry comes from Host Agent, with old collector removed or explicitly transitional.
- API no longer mounts Docker socket for routine service lifecycle actions.
- One Postgres container has both logical DBs available, with core SQLite migration staged separately and safely.
- Tests cover soft DB boot, DB status UI, Host Agent absent/degraded behavior, and host portability guards.
