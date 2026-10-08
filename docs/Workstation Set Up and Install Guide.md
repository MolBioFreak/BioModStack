# BioModStack Workstation Setup and Runtime Guide

This is the current operator guide for installing and running BioModStack on a
local Linux workstation.

## Runtime model

BioModStack is a local stack with:

- a FastAPI backend at `platform/api`
- a React frontend at `platform/frontend`
- Nextflow workflows rooted at [main.nf](../main.nf)
- a default containerized API/web runtime
- a host-native workflow adapter for Nextflow/process ownership
- data, containers, and large model assets stored outside the repo where
  possible

The main launcher remains:

- [start_ui.sh](../start_ui.sh)

If runtime is omitted, startup resolves as:

1. explicit `--runtime ...`
2. `BMS_RUNTIME_MODE`
3. `container`

## Core dependencies

Install these first:

- Python with `uv`
- Node.js and `npm`/`pnpm`
- Java for Nextflow
- Nextflow
- Docker/Compose for the core runtime
- Apptainer for workflow containers
- NVIDIA drivers and working `nvidia-smi`

Optional but relevant depending on subsystem usage:

- GTK4 / Libadwaita for the desktop control panel
- CoolerControl or `nvidia-settings` for workstation fan integration
- network reachability to the robot-local BioXP runtime for robotics
- a stable private URL/TLS path if you plan to use the optional Android shell
  against hosted BMS

## Recommended data layout

The preferred workstation layout keeps the repo and large runtime assets
separate.

Recommended pattern:

- repo:
  `BMS_HOME=/path/to/biomodstack`
- data root:
  `BMS_DATA=/mnt/BioModStack`
- weights:
  `BMS_WEIGHTS=/mnt/BioModStack/weights`
- containers:
  `BMS_CONTAINER_DIR=/mnt/BioModStack/apptainer`

The runtime profile/path resolver already prefers `/mnt/BioModStack` when it
exists and looks like a BMS data root.

## Profile and environment files

BioModStack now uses both an install profile and compatibility env exports.

Key files:

- `~/.config/biomodstack/install_profile.json`
  persisted machine-readable runtime/path profile
- `~/.config/biomodstack/core-runtime.env`
  generated env file consumed by the core-runtime compose stack
- `~/.config/biomodstack/launch_preferences.json`
  default shell/browser-open preferences
- `~/.biomodstack/env.sh`
  compatibility exports and shell-driven local overrides

Path precedence is:

1. explicit environment variables
2. install profile
3. heuristics

## Environment setup

`start_ui.sh` still sources:

- `~/.biomodstack/env.sh`

That remains the most direct place for shell-oriented local overrides. A typical
example is:

```bash
export BMS_HOME=/home/you/biomodstack
export BMS_DATA=/mnt/BioModStack
export BMS_WEIGHTS=/mnt/BioModStack/weights
export BMS_CONTAINER_DIR=/mnt/BioModStack/apptainer
export BMS_MSA_CACHE=/mnt/BioModStack/msa_cache
export BMS_COLABFOLD_DB=/mnt/BioModStack/colabfold_db
export BMS_SABDAB_CACHE=/mnt/BioModStack/sabdab_cache
export BMS_DB_PATH=/mnt/BioModStack/biomodstack.db
export BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:8001
export BMS_FAN_CONTROL_BACKEND=coolercontrol
```

Useful env vars:

- `BMS_HOME`
- `BMS_DATA`
- `BMS_INPUTS`
- `BMS_WEIGHTS`
- `BMS_CONTAINER_DIR`
- `BMS_MSA_CACHE`
- `BMS_COLABFOLD_DB`
- `BMS_SABDAB_CACHE`
- `BMS_DB_PATH`
- `DATABASE_URL`
- `BMS_WORKFLOW_ADAPTER_URL`
- `BMS_MOBILE_UI_UPDATES_DIR`
- `BMS_FAN_CONTROL_BACKEND`
- `BMS_API_RELOAD`

## Starting and stopping services

Default startup from the repo root:

```bash
./start_ui.sh start
./start_ui.sh status
./start_ui.sh stop
```

Because the default runtime falls back to `container`, those commands usually
manage the containerized core runtime unless you explicitly select `dev`.

Explicit runtime examples:

```bash
./start_ui.sh start --runtime dev
./start_ui.sh start --runtime container
./start_ui.sh restart-api --runtime container
./start_ui.sh stop --runtime container
```

You can also drive the compose stack directly through the repo-native wrapper:

```bash
./scripts/run_biomodstack_core_runtime.sh up
./scripts/run_biomodstack_core_runtime.sh ps
./scripts/run_biomodstack_core_runtime.sh down
```

Optional GTK control panel:

```bash
./start_ui_gui.sh
```

## Browser and Electron shells

Default browser launch:

```bash
python3 scripts/launch_biomodstack_ui.py --surface browser --runtime container
```

Optional Electron shell install and launch:

```bash
pnpm --dir platform/desktop-electron install
./start_ui_electron.sh --runtime container
```

The Electron shell wraps the same hosted `/bms/` UI and calls the shared
service-control layer instead of supervising API/frontend processes directly.

## Optional Android thin-shell/update path

BioModStack itself still runs on the workstation/server. The phone is only a
client shell.

The repo currently provides the compatibility pieces for that path:

- shell-ready frontend startup hook in `platform/frontend/src/runtime/cordovaShell.ts`
- mobile update/feed endpoints at `/api/mobile-ui/*`
- default mobile update storage under `${BMS_DATA}/mobile-ui-updates`

If you use this path, first ensure the hosted `/bms/` UI is reachable from the
phone at a stable private URL.

## What `start_ui.sh` actually does

In dev mode the launcher:

- loads `~/.biomodstack/env.sh` if present
- ensures `uv` is on `PATH`
- starts the API with `uv run uvicorn main:app`
- starts the frontend with `npm run dev`
- keeps runtime-generated inputs out of the watched API source tree by setting
  `BMS_INPUTS`

In container mode (`--runtime container`) the launcher instead manages:

- `biomodstack-workflow-adapter.service`
- `biomodstack-core-runtime.service`

The core runtime launches `compose.core-runtime.yml`, while workflow launch
ownership stays on the host via the workflow adapter.

## Local system/runtime routes

The API exposes local-only runtime/admin routes, including:

- `GET /api/system/runtime-state`
- `GET /api/system/install-profile`
- `PUT /api/system/install-profile`

These routes are intentionally limited to localhost/testclient callers.

## Containers and models

BMS expects workflow containers and large weights to already exist or be
buildable/downloadable for the workflows you plan to run.

Typical examples:

- Apptainer images under `${BMS_CONTAINER_DIR}`
- model weights under `${BMS_WEIGHTS}`

Not every workflow has the same runtime footprint. Antibody design,
Boltz/Protenix, Fold-CP, Protein Hunter, Caliby, Nanopore, docking, and
robotics all have different external dependencies.

## Subsystem-specific requirements

### Structure workflows

Require the relevant containers, weights, and reference databases for the
chosen models and validators.

### Molecular biology toolkit

Works primarily through the API/frontend stack and the local sequence database.

### Nanopore

Requires the Dorado/modkit toolchain plus accessible POD5/BAM/FASTQ/reference
data.

### BioXP robotics

Requires network reachability from BMS to the robot-local BioXP runtime. In
normal operation BMS stores a linkage URL and proxies the robot API over HTTP;
it does not supervise the robot daemon from the cockpit.

The current BMS BioXP surface is intentionally narrower than the full
robot-local runtime. For example, the robot already exposes
`/motion/reference/status` and `/liquid/*` routes that are not yet mirrored
through `/api/bioxp/*` in BMS.

Also note that BMS connection/status surfaces can disagree briefly during
reconnect or recovery windows. `/api/bioxp/status` and
`/api/bioxp/daemon/status` are both useful, but they are not identical signals
and should not be collapsed into a single hardware-truth claim.

Operationally, current camera/UVC failures and the historical Novo USB/CAN
reset pattern should be described as unresolved transport/recovery instability,
not as a proven blanket hardware-failure diagnosis.

Relevant env vars include:

- `BIOXP_SERVER_URL`
- `BIOXP_LINKAGE_STATE_PATH`
- `BIOXP_SSH_HOST`
- `BIOXP_DAEMON_PORT`

## Database and data roots

By default BMS writes job/design metadata to a SQLite database resolved by
[biomodstack_runtime_profile.py](../biomodstack_runtime_profile.py) and
[platform/api/paths.py](../platform/api/paths.py).

Priority order:

1. `DATABASE_URL`
2. `BMS_DB_PATH`
3. install profile
4. `${BMS_DATA}/biomodstack.db`
5. repo-local fallback

Keep the API, scripts, workflows, and shells pointed at the same data root and
DB to avoid split-state behavior.

## Suggested first checks

After startup:

1. open `http://127.0.0.1:8000/api/health`
2. open `http://127.0.0.1:8001/api/workflow-adapter/health`
3. open `http://127.0.0.1:5173/bms/`
4. verify models appear in the launcher
5. confirm the resolved install profile/runtime paths if needed through
   `/api/system/install-profile`
6. confirm the expected data root, weights, container, and mobile-update
   directories exist

## Read next

- [Platform Overview](Platform_Overview.md)
- [Desktop Runtime and Shell Architecture](Desktop_Runtime_and_Shell_Architecture.md)
- [Structure Design and Refinement](Structure_Design_and_Refinement.md)
- [Lab Automation, Mol Bio, and Sequencing](Lab_Automation_MolBio_and_Sequencing.md)
