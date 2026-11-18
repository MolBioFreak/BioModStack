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

## Find the owning screen

Use the [workspace guide](../../docs/Workspaces.md) for the complete route list and compatibility redirects. [App.tsx](src/App.tsx) selects the component; a route alone does not imply the selected record exists or that a feature is enabled.

- [Project Manager](src/pages/ProjectManager.tsx) owns navigation through Projects, experiments and domains. [Job Submission](src/components/JobSubmission.tsx) is the launch screen, not the owner of a Project record.
- [Results Viewer](src/components/ResultsViewer.tsx) opens designs and results; an individual Job has a separate detail page. Telemetry is [part of the Dashboard](src/components/dashboard/DashboardTelemetry.tsx), not a standalone `/infra` page.
- The [Mol Bio editor](src/components/MolBioToolkit/MolBioToolkitV2.tsx) and [NGS Toolkit](src/components/NGSToolkit.tsx) are distinct workspaces. Family alignments have their own library, comparison and result routes.
- The [BioXP cockpit](src/components/BioXpCockpit.tsx) hosts the workflows and live-deck views. The UI consumes the typed BMS integration; physical state is not inferred from a tab being visible.

For a cross-layer issue, use the [service map](../../docs/Services.md) to locate the corresponding API module rather than adding a second interpretation of the record in the UI.

## Related docs

- [../../README.md](../../README.md)
- [../../docs/README.md](../../docs/README.md)
- [../../docs/Desktop_Runtime_and_Shell_Architecture.md](../../docs/Desktop_Runtime_and_Shell_Architecture.md)
- [../../docs/Lab_Automation_MolBio_and_Sequencing.md](../../docs/Lab_Automation_MolBio_and_Sequencing.md)
- [../../docs/Results_and_Analysis.md](../../docs/Results_and_Analysis.md)
