# BioModStack Workstation Setup and Runtime Guide

This is the current operator guide for installing and running BMS on a local
Linux workstation.

## Runtime Model

BMS is a local stack with:

- a FastAPI backend at `platform/api`
- a React frontend at `platform/frontend`
- Nextflow workflows rooted at [main.nf](../main.nf)
- data, containers, and large model assets stored outside the repo where
  possible

The launcher script is:

- [start_ui.sh](../start_ui.sh)

## Core Dependencies

Install these first:

- Python with `uv`
- Node.js and `npm`
- Java for Nextflow
- Nextflow
- Apptainer
- NVIDIA drivers and working `nvidia-smi`

Optional but relevant depending on subsystem usage:

- GTK4 / Libadwaita for the desktop control panel
- CoolerControl or `nvidia-settings` for workstation fan integration
- SSH access to a BioXP host for robotics

## Recommended Data Layout

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

The API path resolver already prefers `/mnt/BioModStack` when it exists and
looks like a BMS data root.

## Environment Setup

`start_ui.sh` sources:

- `~/.biomodstack/env.sh`

That is the best place to keep local workstation overrides. A typical example:

```bash
export BMS_HOME=/home/you/biomodstack
export BMS_DATA=/mnt/BioModStack
export BMS_WEIGHTS=/mnt/BioModStack/weights
export BMS_CONTAINER_DIR=/mnt/BioModStack/apptainer
export BMS_MSA_CACHE=/mnt/BioModStack/msa_cache
export BMS_COLABFOLD_DB=/mnt/BioModStack/colabfold_db
export BMS_SABDAB_CACHE=/mnt/BioModStack/sabdab_cache
export BMS_DB_PATH=/mnt/BioModStack/biomodstack.db
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
- `BMS_FAN_CONTROL_BACKEND`
- `BMS_API_RELOAD`

## Starting and Stopping Services

From the repo root:

```bash
./start_ui.sh
./start_ui.sh status
./start_ui.sh stop
```

Optional GTK control panel:

```bash
./start_ui_gui.sh
```

Default local URLs:

- UI: `http://localhost:5173/bms/`
- API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`

## What `start_ui.sh` Actually Does

The launcher:

- loads `~/.biomodstack/env.sh` if present
- ensures `uv` is on `PATH`
- starts the API with `uv run uvicorn main:app`
- starts the frontend with `npm run dev`
- defaults `BMS_FAN_CONTROL_BACKEND` to `coolercontrol` unless overridden
- keeps runtime-generated inputs out of the watched API source tree by setting
  `BMS_INPUTS`

## Containers and Models

BMS expects model containers and large weights to already exist or be
downloadable/buildable for the workflows you plan to run.

Typical examples:

- Apptainer images under `${BMS_CONTAINER_DIR}`
- model weights under `${BMS_WEIGHTS}`

Not every workflow has the same runtime footprint. Antibody, Boltz/Protenix,
Nanopore, docking, and robotics all have different external dependencies.

## Subsystem-Specific Requirements

### Structure workflows

Require the relevant containers, weights, and reference databases for the
chosen models and validators.

### Molecular biology toolkit

Works primarily through the API/frontend stack and the local sequence database.

### Nanopore

Requires the Dorado/modkit toolchain via the workflow runtime plus accessible
POD5/BAM/FASTQ/reference data.

### BioXP robotics

Requires working SSH and/or proxy linkage to the remote BioXP daemon. Relevant
env vars include:

- `BIOXP_SSH_USER`
- `BIOXP_SSH_HOST`
- `BIOXP_DAEMON_PORT`
- `BIOXP_REPO_DIR`
- `BIOXP_SERVER_URL`

## Database and Data Roots

By default BMS writes job/design metadata to a SQLite database resolved by
[platform/api/paths.py](../platform/api/paths.py).

Priority order:

1. `DATABASE_URL`
2. `BMS_DB_PATH`
3. `${BMS_DATA}/biomodstack.db`
4. repo-local fallback

Keep the API, scripts, and workflows pointed at the same data root and DB to
avoid split-state behavior.

## Suggested First Checks

After startup:

1. open `http://localhost:8000/api/health`
2. open `http://localhost:5173/bms/`
3. verify models appear under the job launcher
4. confirm the database path under the API/system endpoints if needed
5. confirm the expected data root, weights, and container directories exist

## Read Next

- [Platform Overview](Platform_Overview.md)
- [Structure Design and Refinement](Structure_Design_and_Refinement.md)
- [Lab Automation, Mol Bio, and Sequencing](Lab_Automation_MolBio_and_Sequencing.md)
