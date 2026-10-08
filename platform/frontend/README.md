# BioModStack Frontend

The frontend is the operator UI for BioModStack. It covers workflow launch,
results review, molecular biology editing, nanopore review, workstation
telemetry, and BioXP control.

## Entry Point

- [src/App.tsx](src/App.tsx)

## Run Locally

From the repo root:

```bash
./start_ui.sh
```

Frontend only:

```bash
cd platform/frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

Default URL:

- `http://localhost:5173/bms/`

The base path is `/bms/`.

## Current Routes

- `/`
  dashboard
- `/submit`
  model/workflow launcher
- `/designs`
  results/data viewer
- `/jobs/:jobId`
  job detail page
- `/designer`
  molecular biology toolkit
- `/ngs`
  nanopore/NGS toolkit
- `/infra`
  workstation telemetry and controls
- `/bioxp`
  BioXP cockpit

## Frontend Responsibilities

The UI currently provides:

- dynamic model/workflow launch forms from API model definitions
- results browsing and stage-aware output review
- design analytics and comparison views
- sequence library and mol bio editing tools
- nanopore launch/review flows
- workstation telemetry surfaces
- robotics control via the BioXP cockpit

## Important Components

- `components/JobSubmission.tsx`
  main launcher surface
- `components/ResultsViewer.tsx`
  design/result browser
- `components/MolBioToolkit/*`
  mol bio toolkit
- `components/NGSToolkit.tsx`
  NGS review surface
- `components/NanoporeTemplate.tsx`
  nanopore launch form
- `components/BioXpCockpit.tsx`
  robotics control UI
- `components/InfraMonitorPage.tsx`
  system analytics page

## API Coupling

The frontend depends on the FastAPI backend for:

- model definitions
- job launch and status
- file browsing and artifact serving
- results/design metadata
- sequence libraries and mol bio operations
- BioXP and infra actions

## Related Docs

- [../../README.md](../../README.md)
- [../../docs/README.md](../../docs/README.md)
- [../../docs/Lab_Automation_MolBio_and_Sequencing.md](../../docs/Lab_Automation_MolBio_and_Sequencing.md)
- [../../docs/Results_and_Analysis.md](../../docs/Results_and_Analysis.md)
