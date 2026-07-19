# BioModStack

BioModStack (BMS) is a workstation-first platform for biomolecular design,
validation, sequencing, molecular biology, results analysis, and lab-adjacent
operations.

The live stack combines:

- Nextflow workflows for heavy compute, staged pipelines, and experimental runs
- a FastAPI control plane for orchestration, metadata, artifact serving, and
  system/runtime APIs
- a React frontend for launch, review, analytics, mol bio, NGS, infra, and
  BioXP control
- optional local shells around the hosted `/bms/` UI, including browser,
  Electron, GTK panel/tray, and an additive Android thin-shell/update path

## Runtime and launch surface

The current workstation/runtime model is:

- containerized core runtime for the API and web UI via
  `compose.core-runtime.yml`
- host-native workflow execution through `biomodstack-workflow-adapter.service`
  and `BMS_WORKFLOW_ADAPTER_URL`
- service ownership through `systemd --user`, not through the browser, GTK, or
  Electron shells
- optional Electron shell in `platform/desktop-electron` as an additive launch
  surface, not a separate backend owner
- optional Android compatibility/update surface through the hosted `/bms/` UI,
  the frontend Cordova-ready hook, and `/api/mobile-ui/*` bundle endpoints

If runtime is omitted, BioModStack resolves it as:

1. explicit `--runtime ...`
2. `BMS_RUNTIME_MODE`
3. `container`

Current robustness snapshot:

- container mode has a dedicated Compose stack, generated env file, health checks,
  and a longer startup-readiness budget for first builds/recreates
- workflow execution is intentionally not owned by the API/web container; launches,
  cancels, and running-job inspection cross the host workflow-adapter boundary
- guarded core-runtime mode should keep the dashboard/API alive even when workflow
  assets, GPUs, BioXP linkage, or adapter-side capabilities are missing
- full workflow capability still depends on host-native Nextflow, Apptainer,
  NVIDIA/GPU visibility, model weights, reference databases, and workflow caches

## Workflow surface

BMS is not just a protein-design launcher. The live repo covers:

- antibody de novo and staged refinement workflows
- generic structure prediction and validation with AlphaFold2, RF3, Boltz-2,
  and Protenix
- generic binder generation and redesign with RFdiffusion, BindCraft,
  BoltzGen, and local redesign workflows
- experimental workflow families including La-Proteina/DISCO protein CAD,
  Protein Hunter Experimental, Caliby Experimental, and Fold-CP Experimental
- docking via DiffDock, Uni-Dock, and shared docking surfaces
- nanopore/NGS launch and review
- a molecular biology toolkit for DNA/RNA libraries and construct operations
- a BioXP cockpit plus workstation infra/runtime controls

The BioXP surface is a compact, status-first control plane, not a generic robot
proxy. It exposes bounded profile, connection, readiness, offline protocol, local
job, typed command, and emergency-delivery contracts. Retired motion, liquid,
thermal, camera, vision, arbitrary proxy, host-lifecycle, and remote-log routes
are absent. The robot-local OEM-compatible runtime remains authoritative for
hardware behavior; normal BMS commands stay disabled until their exact live
contracts are independently verified.

For the full live model inventory, see
[docs/ai_guidance/Model_Integrations.md](docs/ai_guidance/Model_Integrations.md).

## Quick start

From the repo root:

```bash
./start_ui.sh start
./start_ui.sh status
```

That uses the default runtime resolution above, which means container mode unless
`BMS_RUNTIME_MODE` overrides it.

Explicit dev/runtime commands:

```bash
./start_ui.sh start --runtime dev
./start_ui.sh start --runtime container
./start_ui.sh stop --runtime container
```

Important local URLs:

- Stable hosted UI: `http://127.0.0.1:18080/bms/`
- Dev browser UI: `http://127.0.0.1:5173/`
- API: `http://127.0.0.1:8000`
- API docs: `http://127.0.0.1:8000/docs`
- workflow adapter health: `http://127.0.0.1:8001/api/workflow-adapter/health`

## Optional local shells

Browser launch:

```bash
python3 scripts/launch_biomodstack_ui.py --surface browser --runtime container
```

Electron shell:

```bash
pnpm --dir platform/desktop-electron install
./start_ui_electron.sh --runtime container
```

The Electron shell wraps the same hosted `/bms/` UI, keeps its own persistent
storage partition, and calls the shared desktop-service control plane rather
than supervising API/frontend processes directly.

The Android path is intentionally additive. BMS remains hosted on the
workstation/server; the mobile shell is expected to consume the existing web UI
and the `/api/mobile-ui/*` update endpoints rather than move runtime ownership
onto the phone.

## Runtime/profile files

Key local config/state files:

- `~/.config/biomodstack/install_profile.json`
  persisted runtime/data-path profile
- `~/.config/biomodstack/core-runtime.env`
  generated env file for the containerized core runtime
- `~/.config/biomodstack/launch_preferences.json`
  default launch surface and browser auto-open preferences
- `~/.biomodstack/env.sh`
  compatibility env exports and local overrides for shell-driven workflows

## Documentation map

Start here:

- [docs/README.md](docs/README.md)
- [Platform Overview](docs/Platform_Overview.md)
- [Workstation Setup and Runtime](docs/Workstation%20Set%20Up%20and%20Install%20Guide.md)
- [Desktop Runtime and Shell Architecture](docs/Desktop_Runtime_and_Shell_Architecture.md)
- [Structure Design and Refinement](docs/Structure_Design_and_Refinement.md)
- [Lab Automation, Mol Bio, and Sequencing](docs/Lab_Automation_MolBio_and_Sequencing.md)
- [Results and Analysis](docs/Results_and_Analysis.md)

Focused workflow/runtime references:

- [Experimental Protein CAD Workflow](docs/Experimental_Protein_CAD_Workflow.md)
- [Caliby Experimental Workflow](docs/Caliby_Experimental_Workflow.md)
- [Protein Hunter Experimental Workflow](docs/Protein_Hunter_Experimental_Workflow.md)
- [Active Plans](docs/plans/README.md)

Subsystem references:

- [API README](platform/api/README.md)
- [Frontend README](platform/frontend/README.md)
- [Electron shell README](platform/desktop-electron/README.md)

## Repository entry points

- workflow entrypoint: [main.nf](main.nf)
- API entrypoint: [platform/api/main.py](platform/api/main.py)
- frontend entrypoint: [platform/frontend/src/App.tsx](platform/frontend/src/App.tsx)
- service manager: [start_ui.sh](start_ui.sh)
- Electron launcher: [start_ui_electron.sh](start_ui_electron.sh)
- runtime/service layer: [biomodstack_services.py](biomodstack_services.py)
- install-profile/path resolution: [biomodstack_runtime_profile.py](biomodstack_runtime_profile.py)
