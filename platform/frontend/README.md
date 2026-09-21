# BioModStack Frontend

The frontend is the operator UI for BioModStack. It covers workflow launch,
results review, molecular biology editing, nanopore review, workstation
telemetry, and BioXP control while targeting the hosted `/bms/` web app
contract.

## Entry point

- [src/App.tsx](src/App.tsx)

## Runtime and shell contract

The frontend is designed to run as one hosted UI surface that can be consumed
through multiple shells:

- normal browser launch
- optional Electron shell
- GTK/tray helpers that open the same hosted UI
- optional Cordova/Android thin-shell path

The product truth remains the hosted `/bms/` app, not separate frontend forks
per shell.

Relevant runtime helpers include:

- `src/runtime/navigation.ts`
  basename/origin handling for hosted shells
- `src/runtime/cordovaShell.ts`
  one-shot readiness signal hook for Cordova-style shells

## Managed Development startup

Complete [Nonproduction Installation](../../docs/Nonproduction_Installation.md)
using the supported Python/frontend bootstrap and verification actions. The root
`pnpm-lock.yaml` and workspace configuration are authoritative; do not regenerate
lockfiles or substitute a different package manager.

From the repository root after setup:

```bash
./start_ui.sh start --runtime dev
./start_ui.sh status --runtime dev --json
./start_ui.sh stop --runtime dev
```

Read the configured origin from managed status; do not assume a fixed host or
port. The hosted router basename is `/bms/`. Do not start an independent Vite
server as the managed-service path.

Development deliberately uses React's non-profiling production runtime while
Vite keeps its `development` mode and serves source modules. Do not remove
`NODE_ENV=production` to identify Development; use Vite's mode and the managed
runtime identity. See [AGENTS.md](../../AGENTS.md).

## Current routes

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
  workstation telemetry and runtime/admin controls
- `/bioxp`
  BioXP cockpit

## Frontend responsibilities

The UI currently provides:

- dynamic model/workflow launch forms from API model definitions
- results browsing and stage-aware output review
- design analytics and comparison views
- sequence library and mol bio editing tools
- nanopore launch/review flows
- workstation telemetry/runtime surfaces
- status-first BioXP operation through the compact control-plane contract
- shell-friendly hosted navigation for browser/Electron/mobile wrappers

The frontend BioXP route consumes only the compact profile/status/connection,
offline protocol, local job, typed command, and emergency-delivery contract. It
does not expose retired hardware-family, arbitrary proxy, host-lifecycle, shell,
or remote-log controls.

## Important components

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
  system analytics/runtime page

## API coupling

The frontend depends on the FastAPI backend for:

- model definitions and launch schemas
- job launch and status
- file browsing and artifact serving
- results/design metadata
- sequence libraries and mol bio operations
- runtime/install-profile data for local control surfaces
- BioXP and infra actions
- optional mobile update/feed compatibility for shell packaging

## Related docs

- [../../README.md](../../README.md)
- [../../docs/README.md](../../docs/README.md)
- [../../docs/Desktop_Runtime_and_Shell_Architecture.md](../../docs/Desktop_Runtime_and_Shell_Architecture.md)
- [../../docs/Lab_Automation_MolBio_and_Sequencing.md](../../docs/Lab_Automation_MolBio_and_Sequencing.md)
- [../../docs/Results_and_Analysis.md](../../docs/Results_and_Analysis.md)
