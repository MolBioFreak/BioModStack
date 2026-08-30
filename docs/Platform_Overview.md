# Platform Overview

BioModStack is a workstation-first control platform built around four main
layers:

- Nextflow workflows for compute-heavy design, prediction, docking, sequencing,
  and experimental runs
- a FastAPI backend for orchestration, metadata, artifact serving, runtime
  administration, and hardware proxying
- a React frontend for launch, review, visualization, mol bio, sequencing,
  infra, and robotics
- shared local service/shell entrypoints for browser, Electron, GTK panel/tray,
  and optional mobile-shell compatibility

## Primary entry points

- workflow entrypoint: [main.nf](../main.nf)
- API entrypoint: [platform/api/main.py](../platform/api/main.py)
- frontend entrypoint: [platform/frontend/src/App.tsx](../platform/frontend/src/App.tsx)
- service launcher: [start_ui.sh](../start_ui.sh)
- Electron launcher: [start_ui_electron.sh](../start_ui_electron.sh)
- runtime/service layer: [biomodstack_services.py](../biomodstack_services.py)
- install-profile/path resolver: [biomodstack_runtime_profile.py](../biomodstack_runtime_profile.py)

## Runtime model

The live workstation/runtime model is:

- default runtime mode: `container`
- containerized API/web runtime under `biomodstack-core-runtime.service`
- host-native workflow launch/cancel/running-job ownership under
  `biomodstack-workflow-adapter.service`
- browser as the default shell
- optional Electron shell via `platform/desktop-electron`
- optional Android thin-shell/update compatibility around the hosted `/bms/` UI

That means container mode is real for the control plane and hosted web UI, but
workflow execution still remains host-native through the workflow adapter. On a
general Linux host, the expected baseline is dashboard/API/web availability with
clear degraded-capability messages for missing workflow assets, GPU hardware,
BioXP linkage, or adapter services. Full workflow readiness requires the
host-native scientific runtime stack in addition to a healthy core container
runtime.

## Frontend surfaces

The current frontend routes are:

- `/`
  dashboard
- `/submit`
  job launcher across model/workflow families
- `/results`, `/designs`, `/designs/:jobId`, `/jobs/:jobId`
  results and job-detail review surfaces
- `/designer`
  molecular biology toolkit
- `/ngs`
  nanopore/NGS launch and review
- `/infra`
  workstation telemetry and runtime controls
- `/bioxp`
  BioXP control surface

## Backend responsibilities

The API does more than submit jobs. It currently:

- serves model definitions and launch schemas
- persists jobs, designs, lineage, stage-review metadata, and analyses
- serves files and stage artifacts back to the frontend
- exposes system/install-profile/runtime routes
- proxies workflow launch/cancel/running-job calls to the host workflow adapter
- exposes mobile update/feed endpoints for optional shell packaging
- manages the bounded BioXP profile/status/connection/protocol/job control plane

For BioXP specifically, BMS is not a robot proxy. The compact route inventory is
defined in [BioXP Compact Control Plane](BioXP_Compact_Control_Plane.md). Retired
hardware-family, arbitrary-path, host-lifecycle, shell, and remote-log routes are
absent; robot-local runtime evidence remains authoritative.

## Workflow families

The live workflow surface includes:

- antibody de novo and refinement
- antibody toolkit modes
- generic structure prediction and validation
- RFdiffusion-based generation
- protein local redesign
- retired binder workflow
- BoltzGen
- Oligo Designer / RFDpoly
- docking
- nanopore methylation and QC
- experimental protein CAD (La-Proteina / DISCO)
- Protein Hunter Experimental
- Caliby Experimental
- NVIDIA Fold-CP inside Structure Prediction

See [Structure_Design_and_Refinement.md](Structure_Design_and_Refinement.md)
for the workflow-level view.

## Runtime layout

Path resolution is centralized in
[biomodstack_runtime_profile.py](../biomodstack_runtime_profile.py) and
[platform/api/paths.py](../platform/api/paths.py).

Important logical roots include:

- code root:
  repo root, overridable with `BMS_HOME`
- data root:
  `BMS_DATA`, then install profile, then workstation heuristics
- results:
  `${data_root}/bms_results`
- work:
  `${data_root}/work`
- analysis cache:
  `${data_root}/analysis_cache`
- containers:
  `${data_root}/apptainer` or `BMS_CONTAINER_DIR`
- weights:
  `${data_root}/weights` or `BMS_WEIGHTS`
- mobile update payloads:
  `${data_root}/mobile-ui-updates` or `BMS_MOBILE_UI_UPDATES_DIR`

## Data model

At a high level the API tracks:

- `Job`
  orchestration-level runs and stage state
- `Design`
  concrete outputs, structures, lineage, and metrics
- `AnalysisRun`
  persisted analysis tasks and cache records

The UI and results flows are built around those records rather than raw files
alone.

## Canonical docs to read next

- [Workstation Setup and Runtime](Workstation%20Set%20Up%20and%20Install%20Guide.md)
- [Desktop Runtime and Shell Architecture](Desktop_Runtime_and_Shell_Architecture.md)
- [Structure Design and Refinement](Structure_Design_and_Refinement.md)
- [Lab Automation, Mol Bio, and Sequencing](Lab_Automation_MolBio_and_Sequencing.md)
- [Results and Analysis](Results_and_Analysis.md)
