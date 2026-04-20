# BioModStack

BioModStack (BMS) is a workstation-first platform for biomolecular design,
refinement, analysis, sequencing, and lab-adjacent operations. It combines:

- Nextflow workflows for heavy compute and staged pipelines
- a FastAPI control plane for orchestration, metadata, and artifact serving
- a React UI for submission, review, analytics, mol bio, NGS, and robotics
- optional GTK launchers for local workstation control

The repo is not just a protein-design launcher. The live system spans structure
design, binder refinement, sequence design, docking, analysis, nanopore
sequencing, molecular biology tooling, and a BioXP robotics control surface.

## Live Workflow and Model Surface

### Antibody and binder workflows

- Antibody de novo and staged refinement:
  RFantibody, FAMPNN, AntiFold, ProteinMPNN, Boltz-2, Protenix,
  ThermoMPNN, AntiBERTy, IgGM, OpenMM, and PPIFlow-linked review stages.
- Antibody toolkit / shell-driven design modes:
  template-driven antibody setup, nanobody/VHH flows, and antibody review /
  resume paths.
- Generic binder generation and redesign:
  RFdiffusion, BindCraft, BoltzGen, and constrained protein local redesign.
- Experimental non-binder protein CAD:
  La-Proteina and DISCO through one experimental workflow family, plus early
  experimental protein-hunting and Caliby design surfaces.
- Oligomer / nucleic-acid-aware generation:
  Oligo Designer / RFDpoly.

### Prediction, validation, and redesign models

- Structure prediction:
  AlphaFold2, RF3, Boltz-2, Protenix, and ImmuneBuilder-facing antibody
  structure prediction.
- Sequence design and redesign:
  FAMPNN, ProteinMPNN, LigandMPNN, AntiFold, FrustraMPNN, and IgGM.
- Mutation and local edit surfaces:
  mutagenesis and local structure redesign workflows.

### Docking, scoring, and post-processing

- Docking:
  DiffDock, Uni-Dock, and the generic docking wrapper surface.
- Scoring / analysis / cleanup:
  OpenMM, ThermoMPNN, AntiBERTy, and ANARCI / ANARCII.

### Sequencing, molecular biology, and operations systems

- Nanopore / NGS:
  Oxford Nanopore launch + review, Dorado basecalling/alignment, modkit
  methylation reporting, plasmid QC, and IGV-ready outputs.
- Molecular biology toolkit:
  DNA/RNA sequence libraries, construct editing, annotation, restriction-site
  mapping, primer design, PCR, Gibson, Golden Gate, search, diagnostics, and
  RNA secondary-structure review.
- Workstation and robotics:
  dashboard, results/review UI, infra telemetry and scheduler controls, BioXP
  cockpit, local GTK launchers, and system tray tooling.

### Model registry entrypoints

The current top-level model registry includes:

- `af2`
- `antibody_denovo`
- `antibody_design`
- `bindcraft`
- `boltz2`
- `boltzgen`
- `caliby_experimental`
- `diffdock`
- `docking`
- `fampnn`
- `ligandmpnn`
- `mutagenesis`
- `nanopore`
- `oligo_design`
- `protein_cad_experimental`
- `protein_hunter_experimental`
- `protein_local_redesign`
- `proteinmpnn`
- `protenix`
- `rf3`
- `rfdiffusion`
- `unidock`

There are also internal child/orchestrator entries such as
`antibody_child`, `rfantibody_child`, `fampnn_child`, and `boltzgen_child`
that exist for execution flow but are not primary user launch targets.

## What BMS Covers

### Structure design and refinement

- Antibody de novo and refinement workflows built around RFantibody, FAMPNN,
  AntiFold, ProteinMPNN, Boltz-2, Protenix, AntiBERTy, ThermoMPNN, IgGM,
  OpenMM, and PPIFlow.
- Generic design and prediction surfaces for RFdiffusion, RF3, AlphaFold2,
  Boltz-2, Protenix, BindCraft, BoltzGen, RFDpoly/Oligo Designer, DiffDock,
  and Uni-Dock.
- Constrained local redesign and staged validation flows for existing
  complexes.

### Analysis and review

- Job, design, and lineage tracking in the API database.
- Persisted design analyses, review metadata, and stage-aware output tracking.
- Results/analytics views for structure confidence, lineage, stage outputs,
  cached analyses, and design provenance.

### Molecular biology

- Sequence library management for DNA and RNA constructs.
- Construct import, editing, annotation, primers, digest, PCR, Gibson, Golden
  Gate, search, sequence diagnostics, and RNA-aware review tooling.

### Sequencing / NGS

- Oxford Nanopore launch and review surface for POD5, BAM, and FASTQ inputs.
- Dorado basecalling/alignment, modkit methylation reporting, plasmid QC, and
  IGV-ready artifacts.

### Robotics

- BioXP remote cockpit for daemon linkage, remote status, cameras, motion
  control, thermal/chiller controls, and interlock-aware device actions.

## Main UI Surfaces

- `/`
  dashboard with quick structure viewing, live queue state, GPU scheduler, and
  telemetry
- `/submit`
  job launcher across workflow families
- `/results`, `/designs`, `/jobs/:jobId`
  results, lineage, analytics, and job detail review
- `/designer`
  molecular biology toolkit
- `/ngs`
  nanopore / sequencing toolkit
- `/infra`
  workstation telemetry and controls
- `/bioxp`
  BioXP robotics cockpit

## Quick Start

From the repo root:

```bash
./start_ui.sh
```

`start_ui.sh` installs and manages dedicated `systemd --user` units. In dev mode it uses
`biomodstack-api.service` and `biomodstack-frontend.service`. For the new containerized core
runtime, use:

```bash
./start_ui.sh start --runtime container
./start_ui.sh status --runtime container
./start_ui.sh stop --runtime container
```

That container mode launches `biomodstack-core-runtime.service`, which in turn runs the
repo-native compose stack from `compose.core-runtime.yml`.

Optional local desktop launcher:

```bash
./start_ui_gui.sh
```

The GTK panel / tray are control surfaces only. They should start, stop, and restart
services through `systemctl --user`, not own the backend process lifetime.

Default local URLs:

- UI: `http://localhost:5173/bms/`
- API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- Infra monitor: `http://localhost:5173/bms/infra`

## Runtime Layout

BMS is path-configurable, but the workstation layout is designed around a data
root separate from the repo. On the current workstation this is typically
`/mnt/BioModStack` on the NVMe data volume.

Important paths and env vars:

- `BMS_HOME`: repo root override
- `BMS_DATA`: data root override
- `BMS_INPUTS`: runtime-upload/input root
- `BMS_WEIGHTS`: model weights root
- `BMS_CONTAINER_DIR`: Apptainer container root
- `BMS_DB_PATH` or `DATABASE_URL`: database location
- `BMS_MSA_CACHE`, `BMS_COLABFOLD_DB`, `BMS_SABDAB_CACHE`: supporting data
- `BMS_FAN_CONTROL_BACKEND`: workstation fan backend

See [docs/Workstation Set Up and Install Guide.md](<docs/Workstation Set Up and Install Guide.md>)
for the current install and runtime setup.

## Documentation

Start with the docs index:

- [docs/README.md](docs/README.md)

Canonical docs:

- [Platform Overview](docs/Platform_Overview.md)
- [Workstation Setup and Runtime](<docs/Workstation Set Up and Install Guide.md>)
- [Structure Design and Refinement](docs/Structure_Design_and_Refinement.md)
- [Lab Automation, Mol Bio, and Sequencing](docs/Lab_Automation_MolBio_and_Sequencing.md)
- [Results and Analysis](docs/Results_and_Analysis.md)
- [Desktop Runtime and Shell Architecture](docs/Desktop_Runtime_and_Shell_Architecture.md)
- [Documentation Harmonization Strategy](docs/Documentation_Harmonization_Strategy.md)

Platform-specific docs:

- [API README](platform/api/README.md)
- [Frontend README](platform/frontend/README.md)

Reference inventory:

- [Model Integrations](docs/ai_guidance/Model_Integrations.md)

## Repository Entry Points

- Workflow entrypoint: [main.nf](main.nf)
- API entrypoint: [platform/api/main.py](platform/api/main.py)
- Frontend entrypoint: [platform/frontend/src/App.tsx](platform/frontend/src/App.tsx)
- Local launcher script: [start_ui.sh](start_ui.sh)
- GTK control panel: [biomodstack_panel.py](biomodstack_panel.py)
