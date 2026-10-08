# Non-production installation: supported setup and Development startup

This is the human and automation interface for a clean **Development** installation.
Production deployment, model-license acceptance, scientific qualification and GPU
acceptance are separate operations, not implied by a running control plane.

## Base prerequisites and boundaries

Provide Linux, a supported Python with `pip`, and compatible Node/npm (Node 22 is
recommended). A real `systemd --user` manager is required for managed Development
startup. The installer does not elevate privileges, install host distro packages,
start a system manager, or silently download a Python/Node interpreter. Missing
base prerequisites produce an explicit blocker. Containers without systemd can
exercise the setup interfaces but cannot establish managed-service acceptance.

Source may be a tracked Git archive: `.git`, existing caches, SIFs and developer
virtual environments are not installation prerequisites. Python dependencies live
outside the source. Frontend workspace dependency linking needs a writable
checkout for ordinary untracked `node_modules`; no tracked manifest/source edits
are part of dependency installation.

## Supported commands

```sh
./start_ui.sh discover --runtime dev --json
./start_ui.sh plan --runtime dev --json
./start_ui.sh python-plan --json
./start_ui.sh python-bootstrap --json
./start_ui.sh python-verify --json
./start_ui.sh frontend-plan --json
./start_ui.sh frontend-bootstrap --json
./start_ui.sh frontend-verify --json
./start_ui.sh configure-preview --document /absolute/path/install.json --json
./start_ui.sh configure --document /absolute/path/install.json --json
./start_ui.sh recover --operation-id OPERATION_FROM_CONFIGURE --json
./start_ui.sh start --runtime dev
./start_ui.sh status --runtime dev --json
./start_ui.sh stop --runtime dev
```

Use the same commands from a terminal or an AI agent. There is no separate
agent-only installer. Example local-only input:

```json
{
  "schema_version": "bms.install.v1",
  "profile": {"data_root": "/absolute/writable/data"},
  "ingress": {"mode": "local-only"}
}
```

Configuration resolves resource defaults from usable capacity, bounded on Linux
by process CPU affinity and cgroup hard CPU/memory limits. It does not mistake a
container's view of host `/proc/meminfo` for its own memory allowance. Configuration
and recovery commit the existing install-profile/env generation authority; they
are not scientific readiness or service activation.

## Python dependency authority

`python-bootstrap` explicitly installs pinned uv **0.8.22** into an owned external
root, then consumes the repository's `platform/api/uv.lock` using `--frozen
--no-dev --no-build --no-install-project`. No licensed weight, SIF
store, source lockfile update, interpreter download or service start is part of
this action. The full executed commands, exit codes and logs are returned/stored
in its JSON operation receipt. Base Python must supply `pip`; installing that
base prerequisite is distinct from automatic BMS setup.

Default storage is under `$XDG_DATA_HOME/biomodstack/python/<source-identity>`
(or `~/.local/share`). `BMS_PYTHON_ROOT` may select another absolute, external
root. Repeating the operation validates offline and returns `already-installed`.
An interrupted same-identity install resumes using the same command. Changed
lockfiles/source location/Python policy require a new external root; existing
state is preserved. Concurrent operations, redirected control paths and missing
or divergent managed dependencies fail closed, without falling back to an old
checkout venv.

Setup dispatch and native service consumers use this same validated environment.
API migrations and Uvicorn use its interpreter directly; telemetry and the mobile
publisher do likewise. Generated units record the selected external root and
validate it again at launch. Legacy installations without managed prerequisite
state retain their existing native launch path. Stop is not made dependent on a
successful Python-bootstrap dispatch.

## Frontend dependency authority

`frontend-bootstrap` installs pinned pnpm **10.11.0** into an owned external root
and consumes the existing workspace with `--filter frontend... install
--frozen-lockfile --ignore-scripts --ignore-pnpmfile`. This pin matches the web
Dockerfile. Its obsolete pnpm 9 pin could not consume the repository's already
committed SHA-256 patch identities/workspace patch configuration; the toolchain
pin is corrected rather than rewriting or re-resolving the dependency lock.
No Electron binary or production bundle build is requested.

`BMS_FRONTEND_ROOT` selects external toolchain/cache/state (default under
`$XDG_DATA_HOME/biomodstack/frontend/<source-identity>`). Source linking is limited
to ordinary untracked `node_modules`. The receipt records the absolute Node
interpreter, so a systemd service need not inherit the interactive shell's Node
PATH. `BMS_FRONTEND_NODE` can select an explicit absolute Node binary at bootstrap;
a conflicting managed launch selection is rejected. Verification probes actual
Vite, esbuild and Rollup without installing or running lifecycle scripts.

## Readiness is checked, not inferred

Development start starts and waits for its workflow adapter, then API and frontend.
An unready adapter cannot be hidden behind an already-running API/UI. A committed
`local-only` configuration does not activate Tailnet through the mobile publisher's
service dependency; pre-existing installations without an explicit managed ingress
policy keep their original dependency behavior.

Dependency installation reports `dependencies_installed`, not `ready=true` or
scientific qualification. Empty model selection remains a provisioning blocker.
Use the existing `provision-plan`, `provision`, `resume`, and `verify` commands for
selected scientific runtimes; no bootstrap receipt substitutes for their pinned
artifact/license/qualification authorities. A control-plane health check alone
never approves a scientific job.
