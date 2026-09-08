# Non-production installation: supported setup and Development startup

This is the human and automation interface for a clean **Development** installation.
Production deployment, model-license acceptance, scientific qualification and GPU
acceptance are separate operations, not implied by a running control plane.

## Native launcher

Reopen **BioModStack Control Panel** from the desktop launcher after an update.
The **Installation Setup (Development)** section is directly below Status. Choose
an action and click **Run setup action**; expand **Full setup report** to inspect
the complete CLI receipt, blockers and diagnostics (including nonzero exits).
Python/frontend plan, install and verify use the same commands documented below.
Install and configuration mutations require confirmation; opening the menu never
installs dependencies. **Configuration and model options** accepts an absolute
install JSON path, a recovery operation ID, or comma-separated model IDs. Preview
the document before Apply; copy recovery IDs from the full report. Discovery and
setup planning explicitly select Development, independent of Quick Actions.

Scientific artifact planning and verification are read-only. Actual provisioning,
license acceptance and provisioning resume remain in the explicit reviewed CLI
workflow; the launcher never downloads scientific artifacts or rents remote hosts.
Setup does not activate services or production. Use existing Quick Actions for
service operation after reviewing configuration. The panel no longer collects,
caches or passes an admin password; managed user-service operation is unchanged,
and foreign port/privilege errors are reported rather than bypassed.

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

## First database creation

The existing pre-launch migration command now initializes only a genuinely empty
core SQLite schema, using the ORM-owned base tables in an explicit atomic DDL
transaction. It then executes every registered historical migration normally;
no migration-ledger rows are fabricated. Migration-owned tables are created by
their historical migrations, not today's ORM schema. Existing nonempty databases
are never treated as fresh installs, and normal application startup remains
attest-only. A process-scoped file lock serializes initialization/migrations and
is released on process death; redirected/nonregular locks are rejected.

## Reproducible isolated acceptance

The committed harness is `scripts/acceptance/run_kvm.py`; its guest helper is not
an alternate installer. It invokes the supported commands above on a tracked Git
export inside a fresh rootless QEMU/KVM guest, then exercises actual user-systemd
services, HTTP endpoints, repeated startup, stop, and fail-closed provisioning.
It verifies tracked file hashes before/after and exports digest-checked evidence.

```sh
python3 scripts/acceptance/run_kvm.py \
  --source "$PWD" --ref HEAD --base /absolute/task-owned/base.img
```

Prerequisites: existing rootless Podman, QEMU/KVM access, qemu-img, Git, and modern
Python stdlib. No host installation, privilege escalation, host service launch,
Docker socket or HOME/tool/cache/SIF mount is performed. The seed builder uses a
digest-pinned public Python image (downloaded if absent). Guest-only OS tooling is
installed through the distro package manager; guest Node 22.16.0 is checked against
its official SHA256 manifest. The VM is powered down and evidence is retained.

The base is the official Ubuntu 24.04 image at
`https://cloud-images.ubuntu.com/releases/noble/release-20260826/ubuntu-24.04-server-cloudimg-amd64.img`,
SHA256 `d0fe84bb5f80853425fa6be28e2c106f30104c3cfe8611933f2e65c9b63f0e30`.
Obtain it in a task-owned directory; the harness refuses a digest mismatch and
never changes the base. It does not require any existing BMS installation.

An absent optional scientific container backend blocks that worker's recovery
and dispatch—not API startup. Persisted scientific work is left untouched until
an explicit restart can complete recovery. Model readiness still requires its
actual acquisition, license and qualification authorities; control-plane health
is not substituted for them.

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
