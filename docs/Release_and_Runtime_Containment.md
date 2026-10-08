# BioModStack Release, Readiness, and Runtime Containment

This document is the Phase 5 production contract. It does **not** authorize a
release by itself. The only supported container release path is
`scripts/biomodstack_release.py`.

## Safety gate

The release CLI is inert unless the operator selects `deploy` **and** supplies
`--confirm-runtime-activation`.

```bash
# No side effects; review this first.
python scripts/biomodstack_release.py plan

# Production transaction: explicit build, unit install, restart, health checks,
# and automatic rollback if any later step fails.
python scripts/biomodstack_release.py deploy --confirm-runtime-activation
```

The deploy transaction:

1. derives a full 40-character Git SHA plus UTC build ID/time;
2. snapshots all four known-good image IDs and generated user-unit contents;
3. explicitly rebuilds `bms-api`, `bms-host-agent`, `bms-cpu-power`, and
   `bms-web` with `--pull`;
4. verifies every built image's OCI revision label before activation;
5. renders and installs the repository's existing user-unit set;
6. restarts the existing container-runtime lifecycle boundary;
7. validates API readiness, the API build SHA, and browser HTTP health; and
8. atomically records the accepted manifest under
   `~/.local/state/biomodstack/releases/current.json`.

A failed build, install, restart, or validation restores the snapshotted image
IDs and unit files, reloads/restarts the existing lifecycle, and validates the
restored runtime. A rollback failure is reported separately and never disguised
as a successful deploy.

A fresh installation without a complete known-good image set is rejected by
default. `--allow-first-install` is an explicit acknowledgment that image
rollback cannot exist before the first successful release.

## Readiness contract

`GET /api/health` always distinguishes process liveness from feature readiness:

- `liveness.alive`: the API process can answer;
- `readiness.checks.core_database`: core SQLite can execute `SELECT 1`;
- `readiness.checks.molbio_database`: molecular-biology DB health;
- `readiness.checks.workflow_adapter`: required in core-runtime/container mode,
  but explicitly `not_required` in native development mode;
- `readiness.checks.frontend`: the configured frontend URL answers; and
- `readiness.checks.workflow_launch`: policy permits workflow launch.

`GET /api/version` and `/api/health.build` expose the same `revision`,
`build_id`, and `build_time` tuple baked into images. Frontend Vite metadata and
Electron persistent diagnostics carry that tuple as well.

## Conservative resource boundaries

### User-systemd units

| Unit | MemoryHigh | MemoryMax | TasksMax | LimitNOFILE |
|---|---:|---:|---:|---:|
| API | 8G | 16G | 2048 | 131072 |
| Frontend | 2G | 4G | 1024 | 65536 |
| Workflow adapter | 32G | 64G | 8192 | 262144 |
| Core-runtime supervisor | 4G | 8G | 2048 | 131072 |

Every value can be overridden before rendering units with:

```text
BMS_<PREFIX>_MEMORY_HIGH
BMS_<PREFIX>_MEMORY_MAX
BMS_<PREFIX>_TASKS_MAX
BMS_<PREFIX>_LIMIT_NOFILE
```

`<PREFIX>` is `API`, `FRONTEND`, `WORKFLOW_ADAPTER`, or `CORE_RUNTIME`.
Memory values must be positive systemd size values; task/FD values must be
positive integers. Invalid or injection-shaped values are rejected.

### Compose services

| Service | Default memory | Default PIDs |
|---|---:|---:|
| bms-api | 16g | 4096 |
| bms-host-agent | 2g | 512 |
| bms-cpu-power | 1g | 256 |
| bms-web | 2g | 512 |

Override names follow `BMS_<SERVICE>_MEMORY_LIMIT` and
`BMS_<SERVICE>_PIDS_LIMIT` as written in `compose.core-runtime.yml`. All
services default to `BMS_CONTAINER_NOFILE_SOFT=131072` and
`BMS_CONTAINER_NOFILE_HARD=131072`.

These are containment ceilings, not workflow admission bypasses. The GPU
orchestrator remains the sole GPU admission owner.

## Test and artifact isolation

Ordinary API tests execute in the route-free namespace and block direct or
shell-wrapped `systemctl`, `systemd-run`, Docker, Podman, Nerdctl, and Compose
control commands. Runtime integration requires **both**:

- `@pytest.mark.runtime_integration`, and
- `BMS_RUNTIME_INTEGRATION_TESTS=1`.

BioXP live integration retains its separate two-key gate. Generated frontend
and Electron test/build artifacts must use package scripts and `/tmp`-backed
output/cache paths; production build outputs must remain owned by the invoking
user.
