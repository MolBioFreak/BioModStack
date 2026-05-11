# Code-Level Spec — BMS DB service + Generic Host Agent

> Companion to `docs/plans/2026-05-06-bms-db-service-host-agent-changeover.md`.
>
> This is the file-by-file implementation spec. It intentionally keeps the first slice low-risk: expose the current analytical Postgres container as **BMS DB service**, remove boot hard-gates, and add clear degraded status before doing the stateful DB rename.

## Non-negotiable contracts

- Product/UI name: **BMS DB service**.
- Short top-bar label: `BMS DB`.
- Stable API/service identifiers:
  - `component = "db-service"`
  - `service_id = "bms-db-service"`
  - `display_name = "BMS DB service"`
- Degraded is valid runtime state. DB/Host Agent/Docker/RAPL/GPU absence must not fabricate success and must not crash generic status paths.
- API/Web remain unprivileged product/control-plane surfaces. Host Agent owns privileged host/service actions.
- Host Agent provider logic must be config/discovery driven, not hardcoded to this workstation.
- Lifecycle implementations must prefer existing container `docker start|restart|stop` or Host Agent action; Compose fallback must use `--no-build`.

## Implementation order

1. Phase 1: soft DB boot + BMS DB service API/UI using transitional direct-Docker control.
2. Phase 2: Host Agent scaffold with capabilities + service descriptors.
3. Phase 3: CPU/RAPL telemetry via Host Agent, old collector as fallback.
4. Phase 4: service lifecycle through Host Agent; remove API Docker socket.
5. Phase 5: rename DB compose service/container and add both logical DBs in one Postgres container.
6. Phase 6: remove one-off `bms-cpu-power` container and retire fallback collector.

Do these as separate commits/PR chunks. Do not combine Phase 5 stateful DB migration with Phase 1 UI/degraded-mode work.

---

## Phase 1 — Soft DB boot + BMS DB service API/UI

### 1. Backend tests first

Create `platform/api/tests/test_db_service_status.py`.

Test cases:

```python
def test_db_service_status_reports_product_name_and_transitional_container(monkeypatch): ...
def test_db_service_status_degrades_when_docker_missing(monkeypatch): ...
def test_db_service_status_redacts_database_urls(monkeypatch): ...
def test_db_service_start_uses_existing_container_without_compose_build(monkeypatch): ...
def test_db_service_missing_container_compose_fallback_uses_no_build(monkeypatch): ...
def test_db_service_rejects_unsupported_action(): ...
def test_db_service_logs_tail_is_bounded_and_redacted(monkeypatch): ...
```

Expected status payload minimum:

```json
{
  "component": "db-service",
  "service_id": "bms-db-service",
  "display_name": "BMS DB service",
  "state": "running|stopped|missing|unknown",
  "health": "healthy|degraded|offline|unknown",
  "runtime_available": true,
  "optional_at_boot": true,
  "control_mode": "docker-direct-transitional|host-agent|unavailable",
  "service_name": "bms-analytical-postgres",
  "container_name": "biomodstack-analytical-postgres",
  "host_agent_available": false,
  "offline_message": "db_service_offline — use BMS DB service → Start",
  "commands": [
    "bms db-service status",
    "bms db-service start",
    "bms db-service restart",
    "bms db-service logs --tail 120"
  ],
  "logical_databases": []
}
```

Add to `platform/api/tests/test_system_router.py`:

```python
def test_db_service_status_endpoint_invokes_service_layer(monkeypatch): ...
def test_db_service_lifecycle_endpoint_invokes_service_action(monkeypatch): ...
def test_db_service_lifecycle_rejects_unknown_action(): ...
```

Add/modify startup test in a new `platform/api/tests/test_main_lifespan.py` or targeted helper test:

```python
@pytest.mark.asyncio
async def test_analytical_init_failure_is_logged_not_raised(monkeypatch, caplog): ...
```

Implementation note: make `main.py` expose a small helper, e.g. `_init_analytical_store_optional()`, so this can be tested without booting the whole app.

Update `platform/api/tests/test_core_runtime_scaffold.py` to assert:

- `bms-api` no longer has `depends_on.bms-analytical-postgres.condition == service_healthy`.
- `bms-stats-tools` no longer has `depends_on.bms-analytical-postgres.condition == service_healthy`.
- `bms-analytical-postgres` keeps a healthcheck.
- DB service labels are present on the transitional Postgres service.

### 2. `platform/api/main.py`

Current hard point:

```python
if os.getenv("BMS_ANALYTICAL_INIT_ON_STARTUP", "0").strip().lower() in {"1", "true", "yes", "on"}:
    await init_analytical_store()
```

Replace with testable soft-init helper:

```python
ANALYTICAL_STARTUP_STATUS: dict[str, object] = {
    "attempted": False,
    "ok": None,
    "message": "not requested",
}


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


async def _init_analytical_store_optional() -> None:
    if not _truthy_env("BMS_ANALYTICAL_INIT_ON_STARTUP"):
        ANALYTICAL_STARTUP_STATUS.update({"attempted": False, "ok": None, "message": "not requested"})
        return
    ANALYTICAL_STARTUP_STATUS.update({"attempted": True, "ok": None, "message": "initializing"})
    try:
        await init_analytical_store()
    except Exception as exc:  # intentionally broad: DB-offline must not kill API boot
        logger.warning("[STARTUP] BMS DB service unavailable for analytical init: %s", exc)
        ANALYTICAL_STARTUP_STATUS.update({"attempted": True, "ok": False, "message": str(exc)})
        return
    ANALYTICAL_STARTUP_STATUS.update({"attempted": True, "ok": True, "message": "initialized"})
```

Then call `await _init_analytical_store_optional()` inside lifespan. Do not raise solely because Postgres is offline.

### 3. `platform/api/services/db_service.py`

Create this new service module. It should mirror the style of `services/stats_tools.py` but avoid copy/paste drift where possible.

Constants:

```python
DB_SERVICE_ID = "bms-db-service"
DB_SERVICE_COMPONENT = "db-service"
DB_SERVICE_DISPLAY_NAME = "BMS DB service"
DB_SERVICE_SHORT_NAME = "BMS DB"
OFFLINE_MESSAGE = "db_service_offline — use BMS DB service → Start"
DEFAULT_TRANSITIONAL_SERVICE_NAMES = ("bms-db", "bms-analytical-postgres")
DEFAULT_TRANSITIONAL_CONTAINER_NAMES = ("biomodstack-db", "biomodstack-analytical-postgres")
VISIBLE_ACTIONS = {"start", "restart", "logs", "health"}
ADVANCED_ACTIONS = {"stop"}
```

Public API:

```python
def describe_db_service(tail: int = 120) -> dict[str, Any]: ...
def run_db_service_action(action: str, tail: int = 120, *, advanced: bool = False) -> dict[str, Any]: ...
```

Private helpers:

```python
def _configured_service_names() -> list[str]: ...
def _configured_container_names() -> list[str]: ...
def _docker_available() -> tuple[bool, str | None]: ...
def _compose_available() -> tuple[bool, str | None]: ...
def _run_docker(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]: ...
def _run_compose(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]: ...
def _find_container_by_label_or_name() -> dict[str, Any] | None: ...
def _inspect_container(container_name: str) -> dict[str, Any]: ...
def _container_logs(container_name: str, tail: int) -> str: ...
def _logical_database_statuses() -> list[dict[str, Any]]: ...
def _redact_text(text: str) -> str: ...
def _base_descriptor(...) -> dict[str, Any]: ...
```

Lookup order:

1. Host Agent when `BMS_HOST_AGENT_URL` is set and reachable. This can be a no-op in Phase 1 if `host_agent_client.py` does not exist yet, but shape the module so Phase 4 can plug it in cleanly.
2. Docker label lookup:
   - `org.biomodstack.service_id=bms-db-service`
   - `org.biomodstack.component=db-service`
3. Configured container names from `BMS_DB_CONTAINER_NAME` / `BMS_DB_CONTAINER_NAMES`.
4. Transitional defaults: `biomodstack-db`, then `biomodstack-analytical-postgres`.
5. Configured compose service names from `BMS_DB_COMPOSE_SERVICE` / `BMS_DB_COMPOSE_SERVICES`.
6. Degraded `missing` descriptor.

Direct Docker actions:

- `start`: `docker start <container>` if container exists.
- `restart`: `docker restart <container>` if container exists.
- `logs`: `docker logs --tail <tail> <container>`.
- `health`: no mutation; refresh status.
- `stop`: only if `advanced=True` or a deliberately scary flag is passed through CLI/API later.

Compose fallback for missing container:

```bash
docker compose -f compose.core-runtime.yml up -d --no-build bms-analytical-postgres
```

Never use bare `up -d` without `--no-build` in lifecycle action code.

Logical database status in Phase 1:

```python
[
  {
    "name": os.getenv("BMS_CORE_DB_NAME", "bms_core_runtime"),
    "role": "core-runtime",
    "storage_mode": "sqlite-legacy",
    "status": "legacy-fallback-active",
    "reachable": True,
    "note": "Core runtime is still using SQLite during migration",
  },
  {
    "name": analytical_store_settings().database,
    "role": "assay-analytics",
    "storage_mode": "postgres",
    "status": analytical_store_status()["status"],
    "reachable": analytical_store_status()["available"],
    "note": analytical_store_status().get("message"),
  },
]
```

Redaction:

- Redact any `postgresql://user:password@host/db` or `postgresql+asyncpg://...` password segment.
- Redact env/log snippets containing `PASSWORD=`, `DATABASE_URL=`, `POSTGRES_PASSWORD=`, `BMS_*_DB_PASSWORD=`.

### 4. `platform/api/routers/system.py`

Add imports:

```python
from services import db_service
```

Add request model near stats-tools action request:

```python
class DbServiceActionRequest(BaseModel):
    tail: int = Field(default=120, ge=1, le=500)
    advanced: bool = False
```

Routes:

```python
@router.get("/system/db-service")
def db_service_status(tail: int = Query(120, ge=1, le=500)) -> dict[str, Any]:
    return db_service.describe_db_service(tail=tail)


@router.post("/system/db-service/{action}")
def db_service_action(action: str, request: DbServiceActionRequest | None = None) -> dict[str, Any]:
    if action not in {"start", "restart", "logs", "health", "stop"}:
        raise HTTPException(status_code=400, detail=f"unsupported db-service action: {action}")
    payload = request or DbServiceActionRequest()
    return db_service.run_db_service_action(action, tail=payload.tail, advanced=payload.advanced)
```

Policy:

- `GET` must not 500 because Docker/DB/Host Agent is down.
- Lifecycle action failures can return 502/503 only when the action itself failed; include post-action status payload when possible.
- Do not expose raw command/env values with secrets.

### 5. DB-backed route degraded behavior

Modify `platform/api/services/assay_analytical_store.py`:

Add exception:

```python
class AnalyticalStoreUnavailable(RuntimeError):
    def __init__(self, message: str, *, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause
```

Add helper:

```python
def _is_connectivity_error(exc: Exception) -> bool:
    return isinstance(exc, (OSError, TimeoutError, ConnectionError, sqlalchemy.exc.OperationalError)) or exc.__class__.__module__.startswith("asyncpg")
```

Wrap engine/session creation call sites that touch Postgres:

```python
try:
    async with session_factory() as session:
        ...
except Exception as exc:
    if _is_connectivity_error(exc):
        raise AnalyticalStoreUnavailable("BMS DB service unavailable", cause=exc) from exc
    raise
```

Modify `platform/api/routers/assay_analytics.py`:

Import exception and add helper:

```python
def _db_service_unavailable(detail: str = "BMS DB service unavailable") -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "message": detail,
            "component": "db-service",
            "degraded_by": "bms-db-service",
            "offline_message": "db_service_offline — use BMS DB service → Start",
        },
    )
```

Catch only around durable DB-backed calls:

- `qpcr_imports`
- `qpcr_import_detail`
- `assay_datasets`
- `assay_dataset_detail`
- `_persist_qpcr_response_if_requested` when `persist=True`
- chromatography/Empower persistence points when they write durable rows

Do not block pure in-memory qPCR/HPLC/DOE calculations when `persist=False`.
Do not fabricate persisted records when DB is offline.

### 6. Compose changes in `compose.core-runtime.yml`

On `bms-analytical-postgres` transitional service add labels:

```yaml
labels:
  org.biomodstack.service_id: bms-db-service
  org.biomodstack.component: db-service
  org.biomodstack.display_name: BMS DB service
  org.biomodstack.optional_at_boot: "true"
```

On `bms-stats-tools` add labels if absent:

```yaml
labels:
  org.biomodstack.service_id: bms-stats-tools
  org.biomodstack.component: stats-tools
  org.biomodstack.display_name: Stats-tools
  org.biomodstack.optional_at_boot: "true"
```

Remove DB hard boot gates:

```yaml
bms-api:
  # remove depends_on: bms-analytical-postgres: condition: service_healthy

bms-stats-tools:
  # remove depends_on: bms-analytical-postgres: condition: service_healthy
```

Keep DB healthcheck for visibility.
Keep `bms-web` depending on API health.

### 7. `.env.core-runtime.example`

Add/normalize names:

```bash
# BMS DB service — product-facing DB runtime
BMS_DB_SERVICE_ID=bms-db-service
BMS_DB_DISPLAY_NAME="BMS DB service"
BMS_DB_COMPOSE_SERVICES=bms-db,bms-analytical-postgres
BMS_DB_CONTAINER_NAMES=biomodstack-db,biomodstack-analytical-postgres
BMS_CORE_DB_NAME=bms_core_runtime
BMS_ANALYTICAL_DB_NAME=bms_analytical_data

# Host Agent, added in Phase 2/4
BMS_HOST_AGENT_URL=http://host.docker.internal:8798
BMS_HOST_AGENT_TIMEOUT_SECONDS=2.0
```

Do not put real passwords in examples.

### 8. Frontend `DbServiceControlPanel.tsx`

Create `platform/frontend/src/components/DbServiceControlPanel.tsx` modeled on `StatsToolsControlPanel.tsx`.

Types:

```ts
type DbServiceHealth = 'healthy' | 'degraded' | 'offline' | 'unknown';
type DbServiceState = 'running' | 'stopped' | 'missing' | 'unknown';

type LogicalDatabaseStatus = {
  name: string;
  role: string;
  storage_mode: string;
  status: string;
  reachable: boolean;
  note?: string | null;
};

type DbServiceStatus = {
  component: 'db-service';
  service_id: 'bms-db-service';
  display_name: string;
  state: DbServiceState;
  health: DbServiceHealth;
  runtime_available: boolean;
  optional_at_boot: boolean;
  control_mode: string;
  service_name?: string | null;
  container_name?: string | null;
  host_agent_available?: boolean;
  offline_message: string;
  commands: string[];
  logical_databases: LogicalDatabaseStatus[];
  logs?: string;
  logs_tail?: number;
  runtime_note?: string | null;
};
```

Functions:

```ts
async function fetchDbServiceStatus(tail = 120): Promise<DbServiceStatus> { ... }
async function runDbServiceAction(action: 'start' | 'restart' | 'logs' | 'health', tail = 120): Promise<DbServiceStatus> { ... }
function statusTone(status: DbServiceStatus | null, error: string | null): 'green' | 'yellow' | 'red' | 'gray' { ... }
function statusLabel(status: DbServiceStatus | null): string { ... }
```

Components:

```tsx
export function DbServiceMenu(): JSX.Element { ... }
export function DbServiceControlPanel({ embeddedContext }: { embeddedContext?: string }): JSX.Element { ... }
```

UI requirements:

- Top-bar button text: `BMS DB`.
- Panel title: `BMS DB service`.
- Show state/health/control mode/container/service/logical databases.
- Show command snippets:
  - `bms db-service status`
  - `bms db-service start`
  - `bms db-service restart`
  - `bms db-service logs --tail 120`
- Prominent actions: Refresh, Start, Restart, Health, Logs.
- Do not show Stop as a primary button.
- Use `data-bms-db-service-menu="true"` on menu root for tests.
- Use `data-bms-db-service-control-panel={embeddedContext}` on panel root.

### 9. `platform/frontend/src/components/Layout.tsx`

Import:

```ts
import { DbServiceMenu } from './DbServiceControlPanel';
```

Place next to `StatsToolsMenu` in the top bar, likely after it:

```tsx
<StatsToolsMenu />
<DbServiceMenu />
```

Do not bury DB status under Assay Analytics; it is global runtime state.

### 10. Frontend contract tests

Create `platform/frontend/tests/dbServiceMenuContract.test.ts`.

Assertions:

```ts
assert.match(layoutSource, /import \{ DbServiceMenu \} from '\.\/DbServiceControlPanel';/);
assert.match(layoutSource, /<DbServiceMenu \/>/);
assert.match(controlSource, /data-bms-db-service-menu="true"/);
assert.match(controlSource, /data-bms-db-service-control-panel=\{embeddedContext\}/);
assert.match(controlSource, /\/api\/system\/db-service/);
assert.match(controlSource, /\/api\/system\/db-service\/\$\{action\}/);
assert.match(controlSource, /BMS DB service/);
assert.match(controlSource, /Start BMS DB service/);
assert.match(controlSource, /Restart BMS DB service/);
assert.match(controlSource, /db_service_offline — use BMS DB service → Start/);
assert.match(controlSource, /bms db-service status/);
assert.match(controlSource, /bms db-service logs --tail 120/);
assert.doesNotMatch(controlSource, /Stop BMS DB service/); // unless advanced UI explicitly added later
```

`tsconfig.tests.json` already includes `tests/**/*.ts`; no change should be needed unless helper files are added outside that glob.

### 11. CLI `scripts/bms`

Add `db-service` command group using the same environment loading pattern as `stats-tools`.

Target commands:

```bash
bms db-service status
bms db-service start
bms db-service restart
bms db-service logs --tail 120
bms db-service stop --i-know-this-disables-db-backed-features
```

Implementation options:

- Near term: wrap Docker/Compose exactly like `services/db_service.py`; use `docker start biomodstack-analytical-postgres` when present and compose `up -d --no-build bms-analytical-postgres` only when missing.
- After Phase 4: prefer Host Agent HTTP calls, with direct Docker fallback only while transitional.

Do not add a normal `bms db-service stop` without the scary explicit flag.

---

## Phase 2 — Generic Host Agent scaffold

### 1. Package layout

Create:

```text
platform/host_agent/__init__.py
platform/host_agent/app.py
platform/host_agent/config.py
platform/host_agent/models.py
platform/host_agent/providers/__init__.py
platform/host_agent/providers/docker_service.py
platform/host_agent/providers/systemd_service.py
platform/host_agent/providers/rapl_power.py
platform/host_agent/providers/gpu_inventory.py
platform/host_agent/providers/fan_control.py
platform/host_agent/providers/os_inventory.py
scripts/run_bms_host_agent.sh
platform/api/services/host_agent_client.py
```

Run with existing API Python deps to avoid a second dependency lock initially:

```bash
PYTHONPATH="$PROJECT_DIR/platform:$PROJECT_DIR/platform/api" \
  uv run --project "$PROJECT_DIR/platform/api" \
  uvicorn host_agent.app:app --host "${BMS_HOST_AGENT_BIND_HOST:-127.0.0.1}" --port "${BMS_HOST_AGENT_PORT:-8798}"
```

### 2. `platform/host_agent/models.py`

Use Pydantic models for stable JSON contracts:

```python
class CapabilityStatus(BaseModel):
    available: bool
    writable: bool = False
    provider: str | None = None
    reason: str | None = None

class HostSummary(BaseModel):
    hostname: str | None = None
    os_id: str | None = None
    os_version: str | None = None
    kernel: str | None = None
    arch: str | None = None

class ServiceActionAvailability(BaseModel):
    available: bool
    reason: str | None = None

class ContainerSummary(BaseModel):
    name: str | None = None
    image: str | None = None
    status: str | None = None
    health: str | None = None
    created_at: str | None = None
    started_at: str | None = None

class ManagedServiceStatus(BaseModel):
    id: str
    display_name: str
    component: str
    provider: str
    state: str
    health: str
    runtime_available: bool
    optional_at_boot: bool = True
    container: ContainerSummary | None = None
    actions: dict[str, ServiceActionAvailability]
    message: str | None = None

class ServiceActionResult(BaseModel):
    service: ManagedServiceStatus
    action: str
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    logs: str | None = None

class CpuPowerTelemetry(BaseModel):
    source: str
    available: bool
    status: str
    watts: float | None = None
    message: str
    discovered_sources: int = 0
    readable_sources: int = 0
    setup_hint: str | None = None
```

### 3. `platform/host_agent/config.py`

Use stdlib `tomllib` when available; no new dependency required for Python 3.11+.

Dataclasses:

```python
@dataclass(frozen=True)
class ServiceTarget:
    id: str
    display_name: str
    component: str
    provider: str = "docker"
    optional_at_boot: bool = True
    compose_project: str | None = None
    compose_file: Path | None = None
    compose_services: tuple[str, ...] = ()
    container_names: tuple[str, ...] = ()
    labels: dict[str, str] = field(default_factory=dict)
    allowed_actions: tuple[str, ...] = ("status", "start", "restart", "logs")

@dataclass(frozen=True)
class HostAgentConfig:
    bind_host: str = "127.0.0.1"
    port: int = 8798
    allow_write_actions: bool = True
    powercap_root: Path = Path("/sys/class/powercap")
    services: tuple[ServiceTarget, ...] = ()
```

Load precedence:

1. `BMS_HOST_AGENT_CONFIG` TOML.
2. Env overrides like `BMS_HOST_AGENT_PORT`, `BMS_HOST_AGENT_ALLOW_WRITE_ACTIONS`, `BMS_HOST_AGENT_POWERCAP_ROOT`.
3. Default service descriptors for `bms-db-service` and `bms-stats-tools`.
4. Provider returns unavailable/degraded.

Default descriptors may include current service/container names but only in config defaults, not provider logic.

### 4. `providers/docker_service.py`

Responsibilities:

- Discover Docker availability via `docker version` or `docker ps`.
- Find a service by labels first, then configured names.
- Inspect state and health using `docker inspect` JSON.
- Actions: status/start/stop/restart/logs only if allowlisted.
- No shell=True. Use `subprocess.run([...], capture_output=True, text=True, timeout=...)`.
- No arbitrary command execution endpoint.
- Always redact secret-looking text before returning stdout/stderr/logs.

Functions:

```python
def docker_capability() -> CapabilityStatus: ...
def describe_service(target: ServiceTarget) -> ManagedServiceStatus: ...
def run_service_action(target: ServiceTarget, action: str, tail: int = 120) -> ServiceActionResult: ...
def list_services(targets: Sequence[ServiceTarget]) -> list[ManagedServiceStatus]: ...
```

Label lookup command shape:

```bash
docker ps -a --filter label=org.biomodstack.service_id=bms-db-service --format '{{json .}}'
```

### 5. `providers/rapl_power.py`

Port existing RAPL logic from `platform/api/routers/gpu.py` / `tools/cpu_power_collector.py`, but make root configurable and generic.

Rules:

- Discover `energy_uj` by globbing under `powercap_root`, not a single hardcoded `intel-rapl:0` file.
- Treat permission denied as `available=false`, `status="unreadable"`, not 500.
- Return `watts=None` until there are two samples for a given domain.
- Handle energy counter wrap using `max_energy_range_uj` when present.
- No fake TDP-derived watts.

Public function:

```python
def sample_cpu_power(config: HostAgentConfig) -> CpuPowerTelemetry: ...
```

### 6. `providers/gpu_inventory.py`

Initial scope: status/capability only, no mutation yet.

- Discover `nvidia-smi` via `shutil.which`.
- Query GPU index/name/memory/power caps if available.
- If not present, return capability unavailable with reason.
- Do not hardcode GPU ordinals/names.

### 7. `providers/fan_control.py`

Initial scope: capability diagnostics only.

- Detect `coolercontrol-cli`, `nvidia-settings`, X display availability.
- Return unavailable reasons if missing.
- Do not implement fan mutation until separate validation.

### 8. `providers/os_inventory.py`

Return host summary from:

- `platform.uname()`
- `/etc/os-release` if readable
- hostname

No personal path assumptions.

### 9. `platform/host_agent/app.py`

FastAPI routes:

```python
@app.get("/health")
def health() -> dict[str, Any]: ...

@app.get("/api/host-agent/capabilities")
def capabilities() -> HostAgentCapabilities: ...

@app.get("/api/host-agent/services")
def services() -> list[ManagedServiceStatus]: ...

@app.get("/api/host-agent/services/{service_id}")
def service_status(service_id: str) -> ManagedServiceStatus: ...

@app.post("/api/host-agent/services/{service_id}/{action}")
def service_action(service_id: str, action: str, request: ServiceActionRequest | None = None) -> ServiceActionResult: ...

@app.get("/api/host-agent/telemetry/cpu-power")
def cpu_power() -> CpuPowerTelemetry: ...
```

Binding must default to `127.0.0.1`. If someone sets `0.0.0.0`, log a warning and require explicit env/config (`BMS_HOST_AGENT_ALLOW_REMOTE_BIND=1`) if implemented.

### 10. Host Agent tests

Create `platform/api/tests/test_host_agent_config.py`:

- default service descriptors include `bms-db-service` and `bms-stats-tools`.
- env overrides are honored.
- TOML services override defaults.
- current workstation paths/GPU ordinals are not baked into provider source.

Create `platform/api/tests/test_host_agent_app.py`:

- `/health` returns component/version.
- no Docker returns capabilities with `docker.available=false` and service statuses degraded.
- service action rejects unallowed action.
- logs/action output are redacted.

Create `platform/api/tests/test_host_agent_rapl_power.py`:

- no powercap root → unavailable/no_sources.
- unreadable `energy_uj` → unavailable/unreadable.
- two samples compute watts.
- wraparound handled.

Create `platform/api/tests/test_host_agent_source_portability.py`:

Search `platform/host_agent` provider source and reject workstation-only literals:

- `/home/dalab`
- `/mnt/BioModStack` in provider logic
- `workstation_ryzen7960x`
- `RTX 3090`, `RTX 5090`, `RTX 5060`
- `GPU 1 is display`
- fixed ordinal lists like `[0, 1, 2, 3]` as policy

Allow those only in docs/tests marked as examples.

---

## Phase 3 — API client + CPU power migration

### 1. `platform/api/services/host_agent_client.py`

Create typed client using stdlib `urllib` or existing `httpx`. Since `httpx` is already in `platform/api/pyproject.toml`, use `httpx` with short timeouts.

Public API:

```python
class HostAgentUnavailable(RuntimeError): ...
class HostAgentRequestError(RuntimeError):
    status_code: int | None

def host_agent_url() -> str | None: ...
def host_agent_enabled() -> bool: ...
def request_host_agent(method: str, path: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]: ...
def get_host_agent_capabilities() -> dict[str, Any]: ...
def get_host_agent_service(service_id: str) -> dict[str, Any]: ...
def run_host_agent_service_action(service_id: str, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]: ...
def get_host_agent_cpu_power() -> dict[str, Any]: ...
```

Timeout defaults:

- status/capabilities: 1.5–2.0s.
- action: 10–30s depending on action.
- logs: 5s.

Failure behavior:

- Connection refused/timeouts raise `HostAgentUnavailable`.
- HTTP 4xx/5xx raise `HostAgentRequestError` with status and redacted detail.
- Client never logs raw DSNs/secrets.

### 2. Modify `platform/api/routers/gpu.py`

Current CPU power order:

1. `BMS_CPU_POWER_COLLECTOR_URL`
2. local RAPL fallback

New order:

1. `BMS_HOST_AGENT_URL` → `GET /api/host-agent/telemetry/cpu-power`.
2. `BMS_CPU_POWER_COLLECTOR_URL` transitional fallback.
3. local RAPL fallback.
4. unavailable diagnostics.

Add helper:

```python
def _sample_cpu_power_from_host_agent() -> tuple[float | None, dict[str, Any]]: ...
```

Update tests in `platform/api/tests/test_cpu_power_telemetry.py`:

```python
def test_cpu_power_sampler_prefers_host_agent(monkeypatch): ...
def test_cpu_power_sampler_falls_back_to_legacy_collector_when_host_agent_errors(monkeypatch): ...
def test_cpu_power_sampler_reports_host_agent_unavailable_without_fake_watts(monkeypatch): ...
```

Keep existing collector tests until Phase 6.

---

## Phase 4 — Service lifecycle through Host Agent

### 1. Modify `platform/api/services/db_service.py`

At the beginning of `describe_db_service()`:

```python
if host_agent_client.host_agent_enabled():
    try:
        return _normalize_host_agent_db_service(host_agent_client.get_host_agent_service(DB_SERVICE_ID), tail=tail)
    except HostAgentUnavailable as exc:
        return _degraded_descriptor(control_mode="host-agent-unavailable", runtime_note=str(exc))
```

At the beginning of `run_db_service_action()`:

```python
if host_agent_client.host_agent_enabled():
    result = host_agent_client.run_host_agent_service_action(DB_SERVICE_ID, action, {"tail": tail, "advanced": advanced})
    return _normalize_host_agent_action_result(result)
```

Normalize Host Agent payload into the existing `/api/system/db-service` frontend contract. Do not force the frontend to know two response shapes.

### 2. Modify `platform/api/services/stats_tools.py`

Add Host Agent preference while retaining current direct-Docker fallback during transition:

```python
if host_agent_client.host_agent_enabled():
    try:
        return _normalize_host_agent_stats_tools(...)
    except HostAgentUnavailable:
        pass  # degraded/fallback depending policy
```

Tests:

```python
def test_stats_tools_status_prefers_host_agent(monkeypatch): ...
def test_stats_tools_action_prefers_host_agent(monkeypatch): ...
def test_stats_tools_host_agent_absent_degrades_or_falls_back_cleanly(monkeypatch): ...
```

### 3. Compose/API unprivileged cleanup

After live validation of Host Agent actions:

- Remove API mount:

```yaml
- type: bind
  source: /var/run/docker.sock
  target: /var/run/docker.sock
```

- Remove API `group_add` for Docker socket if present.
- Add for `bms-api`:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
environment:
  BMS_HOST_AGENT_URL: ${BMS_HOST_AGENT_URL:-http://host.docker.internal:8798}
```

Add `platform/api/tests/test_core_runtime_scaffold.py` assertions:

```python
assert "/var/run/docker.sock" not in rendered_api_mounts
assert api_env["BMS_HOST_AGENT_URL"].startswith("http://host.docker.internal:")
```

---

## Phase 5 — One Postgres container, two logical DBs

Do this only after Phase 1–4 are green.

### 1. `docker/postgres/init-bms-databases.sh`

Create idempotent init script:

```bash
#!/usr/bin/env bash
set -euo pipefail

core_db="${BMS_CORE_DB_NAME:-bms_core_runtime}"
analytical_db="${BMS_ANALYTICAL_DB_NAME:-bms_analytical_data}"
app_user="${BMS_DB_APP_USER:-bms_app}"
app_password="${BMS_DB_APP_PASSWORD:-}"

create_db_if_missing() {
  local db="$1"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
SELECT 'CREATE DATABASE "' || :'db' || '"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db')\gexec
SQL
}

# create role/dbs idempotently; grant per-db privileges
```

Need robust quoting. Prefer using psql variables and `format('%I', ...)` in `DO $$` blocks rather than interpolating untrusted shell text directly.

Responsibilities:

- Create `bms_core_runtime` if missing.
- Create `bms_analytical_data` if missing.
- Create app user/role if configured.
- Grant app role to both DBs.
- Be safe to rerun on an existing volume.

### 2. `compose.core-runtime.yml`

Replace transitional service in a stateful migration commit:

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
  networks:
    default:
      aliases:
        - bms-analytical-postgres  # transitional DNS alias during code/env cutover
  labels:
    org.biomodstack.service_id: bms-db-service
    org.biomodstack.component: db-service
    org.biomodstack.display_name: BMS DB service
    org.biomodstack.optional_at_boot: "true"
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U ${BMS_DB_SUPERUSER:-bms_admin} -d postgres"]
    interval: 10s
    timeout: 5s
    retries: 10
```

Update env for API/stats-tools:

```yaml
BMS_ANALYTICAL_DATABASE_URL: ${BMS_ANALYTICAL_DATABASE_URL:-postgresql+asyncpg://bms_assay:${BMS_ANALYTICAL_DB_PASSWORD:-bms_assay_dev}@bms-db:5432/${BMS_ANALYTICAL_DB_NAME:-bms_analytical_data}}
BMS_CORE_STORAGE_MODE: ${BMS_CORE_STORAGE_MODE:-sqlite-legacy}
BMS_CORE_DATABASE_URL: ${BMS_CORE_DATABASE_URL:-}
```

Volume migration strategy must be explicit before deleting old volume:

1. `pg_dump` existing `bms_analytical_data` from `biomodstack-analytical-postgres`.
2. Start `bms-db` with fresh `bms_db_data`.
3. Restore into `bms_analytical_data`.
4. Verify row/table counts and API status.
5. Only then remove old service/volume references.

### 3. `platform/api/database.py`

Do not switch core runtime off SQLite in this phase unless a separate tested migration exists.

Add status helper only:

```python
def core_database_status() -> dict[str, Any]:
    return {
        "name": os.getenv("BMS_CORE_DB_NAME", "bms_core_runtime"),
        "role": "core-runtime",
        "storage_mode": os.getenv("BMS_CORE_STORAGE_MODE", "sqlite-legacy"),
        "status": "legacy-fallback-active",
        "reachable": True,
        "note": "Core runtime is still using SQLite during migration",
    }
```

Future SQLite→Postgres migration is a separate plan with export/import, rollback, and integrity tests.

---

## Phase 6 — Remove old one-off CPU power container

After Host Agent CPU telemetry is validated on live hardware:

- Remove `bms-cpu-power` from `compose.core-runtime.yml`.
- Remove `BMS_CPU_POWER_COLLECTOR_URL` default from API compose env, or leave as documented fallback only if old script remains.
- Remove `platform/api/tools/cpu_power_collector.py` only when no tests or docs require fallback.
- Update `test_core_runtime_scaffold.py` expected service list.
- Update docs/operator commands to use `bms host-agent ...`.

Acceptance:

- Clean target has no `biomodstack-cpu-power` container.
- CPU watts come from Host Agent where supported.
- Unsupported hosts show nullable/unavailable CPU power with reason.

---

## `biomodstack_services.py` + service scripts

### `scripts/run_bms_host_agent.sh`

Create:

```bash
#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"

export BMS_HOME="$PROJECT_DIR"
if [ -f "$HOME/.biomodstack/env.sh" ]; then source "$HOME/.biomodstack/env.sh"; fi
# Also load ${XDG_CONFIG_HOME:-$HOME/.config}/biomodstack/core-runtime.env if present, same as other launchers.

exec env PYTHONPATH="$PROJECT_DIR/platform:$PROJECT_DIR/platform/api" \
  uv run --project "$PROJECT_DIR/platform/api" \
  uvicorn host_agent.app:app \
    --host "${BMS_HOST_AGENT_BIND_HOST:-127.0.0.1}" \
    --port "${BMS_HOST_AGENT_PORT:-8798}" \
    --no-access-log
```

### `biomodstack_services.py`

Add a service definition analogous to existing core/workflow services:

- Unit name: `biomodstack-host-agent.service`.
- ExecStart: `scripts/run_bms_host_agent.sh`.
- Restart: on-failure.
- Environment file: same profile/core-runtime env path as current services.
- Default bind: 127.0.0.1.

Add CLI/service manager verbs so operator commands can do:

```bash
python biomodstack_services.py install host-agent
python biomodstack_services.py start host-agent
python biomodstack_services.py status host-agent
python biomodstack_services.py logs host-agent --tail 120
```

Then wire `scripts/bms host-agent ...` to these service-manager commands.

---

## Validation commands

Backend targeted:

```bash
PYTHONPATH=platform/api python3 -m pytest -q \
  platform/api/tests/test_db_service_status.py \
  platform/api/tests/test_host_agent_config.py \
  platform/api/tests/test_host_agent_app.py \
  platform/api/tests/test_host_agent_rapl_power.py \
  platform/api/tests/test_cpu_power_telemetry.py \
  platform/api/tests/test_system_router.py \
  platform/api/tests/test_core_runtime_scaffold.py
```

Frontend targeted:

```bash
cd platform/frontend
./node_modules/.bin/tsc -p tsconfig.tests.json --pretty false
node --test node_modules/.tmp/frontend-tests/tests/dbServiceMenuContract.test.js
node --test node_modules/.tmp/frontend-tests/tests/statsToolsMenuContract.test.js
```

Compose static check:

```bash
./scripts/run_biomodstack_core_runtime.sh config >/tmp/bms-core-runtime.rendered.yml
```

Live gates:

1. Stop DB container.
2. Restart core runtime.
3. Verify API `/api/health` returns 200.
4. Verify Web `/bms/` loads.
5. Verify `/api/system/db-service` returns offline/degraded, not 500.
6. Verify top bar shows BMS DB offline.
7. Start DB through top bar or `bms db-service start`.
8. Verify `/api/system/db-service` running/healthy and analytical DB reachable.
9. Stop Host Agent after Phase 4 and confirm status endpoints degrade rather than crash.
10. Confirm API container has no Docker socket mount after Phase 4 cleanup.

## Final done criteria

- API/Web start with DB stopped.
- Top bar exposes BMS DB service status/actions.
- DB-backed routes return honest 503/degraded payloads when DB is down.
- Host Agent is local-only and capability-based.
- Host Agent source has no workstation-specific hardware/path policy.
- CPU power telemetry comes from Host Agent with nullable/unavailable diagnostics on unsupported hosts.
- API no longer mounts `/var/run/docker.sock` for routine service lifecycle.
- One Postgres container owns both `bms_core_runtime` and `bms_analytical_data`; core remains explicitly `sqlite-legacy` until a separate migration is implemented.
