# Platform Overview

BioModStack is a workstation-first control platform built around four main
layers:

- Nextflow workflows for compute-heavy design, prediction, sequencing,
  and experimental runs
- a FastAPI backend for orchestration, metadata, artifact serving, runtime
  administration, and bounded instrument integrations
- a React frontend for launch, review, visualization, mol bio, sequencing,
  infra, and robotics
- shared local service/shell entrypoints for browser, Electron, GTK panel/tray,
  and optional mobile-shell compatibility

## Primary entry points

- workflow dispatch: [workflow adapter registry](../platform/api/services/workflow_adapter_registry.py)
- legacy-compatible workflow wrapper: [main.nf](../main.nf)
- API entrypoint: [platform/api/main.py](../platform/api/main.py)
- frontend entrypoint: [platform/frontend/src/App.tsx](../platform/frontend/src/App.tsx)
- service launcher: [start_ui.sh](../start_ui.sh)
- Electron launcher: [start_ui_electron.sh](../start_ui_electron.sh)
- runtime/service layer: [biomodstack_services.py](../biomodstack_services.py)
- install-profile/path resolver: [biomodstack_runtime_profile.py](../biomodstack_runtime_profile.py)

## Runtime model

The managed runtime architecture supports these roles; actual ownership and
readiness must be verified for the selected installation:

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

## Where work happens

Start in [Projects](Project_to_Result.md) to keep an experiment, its setups and its retained results together. The [workspace guide](Workspaces.md) lists browser destinations, including compatibility redirects. The [service and module map](Services.md) traces those workspaces to their API and data owners.

The browser submits requests to the managed API. The workflow adapter provides the host execution boundary; specialist services interpret native output. Job status, successful artifact publication and Project attachment answer different questions. For an existing run, keep the Job ID rather than searching by name. For a workstation or dependency problem, use the [operator guide](Operations.md).

BioXP follows a different authority model: BMS hosts a feature-gated cockpit and typed integration; the robot owns physical readiness and action admission. See the [control-plane contract](BioXP_Compact_Control_Plane.md).

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

- [Nonproduction Installation](Nonproduction_Installation.md)
- [Repository Maintenance](Repository_Maintenance.md)
- [Desktop Runtime and Shell Architecture](Desktop_Runtime_and_Shell_Architecture.md)
- [Structure Design and Refinement](Structure_Design_and_Refinement.md)
- [Lab Automation, Mol Bio, and Sequencing](Lab_Automation_MolBio_and_Sequencing.md)
- [Results and Analysis](Results_and_Analysis.md)
