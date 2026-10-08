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
- Results/analytics views for structure confidence, lineage, and stage outputs.

### Molecular biology

- Sequence library management for DNA and RNA constructs.
- Construct import, editing, annotation, primers, digest, PCR, Gibson, Golden
  Gate, search, and GC-content visualization.

### Sequencing / NGS

- Oxford Nanopore launch and review surface for POD5, BAM, and FASTQ inputs.
- Dorado basecalling/alignment, modkit methylation reporting, plasmid QC, and
  IGV-ready artifacts.

### Robotics

- BioXP remote cockpit for daemon linkage, remote status, cameras, motion
  control, thermal/chiller controls, and interlock-aware device actions.

## Quick Start

From the repo root:

```bash
./start_ui.sh
```

Optional local desktop launcher:

```bash
./start_ui_gui.sh
```

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
- [Documentation Harmonization Strategy](docs/Documentation_Harmonization_Strategy.md)

Platform-specific docs:

- [API README](platform/api/README.md)
- [Frontend README](platform/frontend/README.md)

Reference inventory:

- [Model Integrations](docs/ai_guidance/Model_Integrations.md)

## Documentation Status

The repo still contains many dated plan/spec/revision notes under `docs/`.
Those are historical design artifacts unless they are linked from
[docs/README.md](docs/README.md) as current documentation.

## Repository Entry Points

- Workflow entrypoint: [main.nf](main.nf)
- API entrypoint: [platform/api/main.py](platform/api/main.py)
- Frontend entrypoint: [platform/frontend/src/App.tsx](platform/frontend/src/App.tsx)
- Local launcher script: [start_ui.sh](start_ui.sh)
- GTK control panel: [biomodstack_panel.py](biomodstack_panel.py)
