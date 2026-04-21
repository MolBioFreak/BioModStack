# Platform Overview

BioModStack is a local control platform built around four layers:

- Nextflow workflows for compute-heavy design, prediction, docking, and NGS
  stages
- a FastAPI backend for orchestration, data model management, file serving,
  analytics, and hardware proxying
- a React frontend for launch, review, visualization, mol bio, sequencing, and
  robotics
- local launcher scripts and optional GTK tools for workstation operation

## Primary Entry Points

- Workflow entrypoint: [main.nf](../main.nf)
- API entrypoint: [platform/api/main.py](../platform/api/main.py)
- Frontend entrypoint: [platform/frontend/src/App.tsx](../platform/frontend/src/App.tsx)
- Service launcher: [start_ui.sh](../start_ui.sh)
- Desktop control panel: [biomodstack_panel.py](../biomodstack_panel.py)

## Frontend Surfaces

The current frontend routes are:

- `/`
  Dashboard
- `/submit`
  Job launcher across model/workflow surfaces
- `/designs` and `/jobs/:jobId`
  results and job detail views
- `/designer`
  molecular biology toolkit
- `/ngs`
  nanopore/NGS launch and review surface
- `/infra`
  workstation telemetry and controls
- `/bioxp`
  BioXP control surface

## Backend Responsibilities

The API does more than submit jobs:

- serves model definitions and submission schemas
- persists jobs, designs, lineage, and review metadata
- runs a GPU orchestrator and analysis worker on startup
- serves files and stage artifacts to the frontend
- exposes sequence libraries, mol bio operations, framework lookup, MSA
  management, analytics, and system actions
- manages BioXP runtime linkage and proxies hardware calls to the linked
  robot-local runtime

## Workflow Families

The live workflow surface includes:

- antibody de novo and refinement
- antibody toolkit modes
- generic structure prediction and validation
- RFdiffusion-based generation
- protein local redesign
- BindCraft
- BoltzGen
- Oligo Designer / RFDpoly
- docking
- nanopore methylation and QC

See [Structure_Design_and_Refinement.md](Structure_Design_and_Refinement.md)
for the workflow-level view.

## Runtime Layout

Path resolution is centralized in [platform/api/paths.py](../platform/api/paths.py).

Important logical roots:

- code root:
  repo root, overridable with `BMS_HOME`
- data root:
  `BMS_DATA` if set, otherwise `/mnt/BioModStack` when present, otherwise a
  fallback under the home directory or repo
- results:
  `${data_root}/bms_results`
- work:
  `${data_root}/work`
- analysis cache:
  `${data_root}/analysis_cache`
- containers:
  `${data_root}/apptainer` or `BMS_CONTAINER_DIR`
- weights:
  `BMS_WEIGHTS` or `${data_root}/weights`
- runtime inputs:
  `BMS_INPUTS` or `platform/api/inputs`

## Data Model

At a high level the API tracks:

- `Job`
  orchestration-level runs and stage state
- `Design`
  concrete outputs, structures, lineage, and metrics
- `AnalysisRun`
  persisted analysis tasks and cache records

The UI and results flows are built around those records rather than raw files
alone.

## Canonical Docs To Read Next

- [Workstation Setup and Runtime](<Workstation Set Up and Install Guide.md>)
- [Structure Design and Refinement](Structure_Design_and_Refinement.md)
- [Lab Automation, Mol Bio, and Sequencing](Lab_Automation_MolBio_and_Sequencing.md)
- [Results and Analysis](Results_and_Analysis.md)
