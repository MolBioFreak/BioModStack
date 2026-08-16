# BioModStack

BioModStack (BMS) is a workstation-first platform for governed biomolecular
workflows, results review, molecular biology, Nanopore/NGS, and selected
lab-adjacent operations. It combines a browser workbench with a managed API,
host-native scientific execution, and optional desktop/mobile launch surfaces.

BMS is designed around a simple separation of responsibilities:

- **managed control plane** — API, web application, authorization, job records,
  results, and artifact access;
- **workflow adapter** — host-native execution of supported scientific workflows
  and their verified runtime dependencies;
- **operator surfaces** — browser, optional Electron shell, and optional Android
  thin shell; these are clients of the managed service, never replacement backend
  owners;
- **governed integrations** — workflow launch, scientific artifacts, sequencing,
  and instrument-facing capabilities use explicit contracts rather than ad-hoc
  scripts or direct UI-to-host control.

## What is in this repository

The tracked repository is intentionally limited to the source required to build,
operate, test, and support current BMS behavior:

- FastAPI control-plane source, schemas, migrations, and focused tests;
- React workbench and its browser/component tests;
- Nextflow workflow definitions, modules, and configuration;
- container and Apptainer definitions, locked dependency manifests, and
  reproducible runtime scaffolding;
- supported desktop/mobile wrapper source and approved release assets;
- concise operational guidance in [`AGENTS.md`](AGENTS.md).

All scientific model integrations follow the mandatory
[`Model configuration, operator control, and agent parity policy`](docs/Model_Configuration_Operator_Control_and_Agent_Parity.md).
Every relevant model setting must be available through suitable browser controls
and the same typed API used by AI agents. A model is incomplete until its full
parameter, execution, data, analysis, visualization, capture, and result surface
has passed live acceptance.

Generated output, model weights, caches, databases, results, local logs,
credentials, installation-specific configuration, and historical planning
material do not belong in the repository.

## Supported product areas

Current BMS surfaces include:

- structure prediction, validation, and governed structure review;
- de novo/binder-design and protein-redesign workflows;
- conformational mapping and molecular-dynamics orchestration/review;
- molecular-biology design, construct, and verification workflows;
- Nanopore/NGS launch, quality-control, and result review;
- scientific result artifacts and the Structure Workbench;
- compact BioXP operator integration, where robot-local capability and safety
  contracts remain authoritative;
- managed service, workflow, and deployment status for operators.

Availability is capability- and installation-dependent. A workflow is only
launchable when its configured runtime, dependencies, authorization, and
preflight checks all succeed.

## Architecture

```text
Operator surface
  └─ Browser / Electron / optional Android thin shell
       └─ Managed BMS API and web service
            ├─ governed data, artifact, and job contracts
            ├─ supported workflow adapter
            │    └─ Nextflow + approved scientific runtimes
            └─ optional instrument or external-provider integrations
```

The API/web service owns interactive application state and service-facing
contracts. Scientific execution is deliberately separated from the web
container so that workflow dependencies, accelerators, reference data, and
host-native execution can be managed explicitly. A push to Git does not deploy
a service; deployed revision, service owner, health, and data identity must be
verified separately.

## Getting started

Use a clean checkout and the repository's locked dependency manifests. Configure
your installation through the supported install profile and environment files;
do not put host paths, service addresses, or credentials in source-controlled
files.

The standard launcher supports managed development and container runtime modes:

```bash
./start_ui.sh start --runtime dev
./start_ui.sh status --runtime dev
./start_ui.sh stop --runtime dev
```

For the managed container runtime, substitute `container` for `dev`:

```bash
./start_ui.sh start --runtime container
./start_ui.sh status --runtime container
./start_ui.sh stop --runtime container
```

The service manager also supports targeted and API-only actions:

```text
./start_ui.sh {start|start-api|start-target|stop|stop-api|status|restart|restart-api}
```

Confirm readiness using the supported service status and health surfaces before
launching work. Do not start detached API, frontend, or workflow processes as a
substitute for the managed service path.

## Development and promotion

- `test` is the sole development and integration branch.
- `main` is the default production/promotion branch.
- Develop and validate on `test`.
- Promotion from `test` to `main` is a reviewed, owner-authorized action.
- Never force-push shared branches, bulk-replay experimental history, or treat
  a Git push as a deployment.

Before pushing, fetch the current remote tip, make focused changes, run relevant
validation, check the diff, and verify the pushed commit. See
[`AGENTS.md`](AGENTS.md) for the complete operating, Tailscale Serve, validation,
and retirement rules.

## Repository entry points

| Surface | Entry point |
| --- | --- |
| Managed service launcher | [`start_ui.sh`](start_ui.sh) |
| Service/runtime management | [`biomodstack_services.py`](biomodstack_services.py) |
| Runtime/install-profile resolution | [`biomodstack_runtime_profile.py`](biomodstack_runtime_profile.py) |
| Workflow entrypoint | [`main.nf`](main.nf) |
| API | [`platform/api/main.py`](platform/api/main.py) |
| Frontend | [`platform/frontend/src/App.tsx`](platform/frontend/src/App.tsx) |
| Electron launcher | [`start_ui_electron.sh`](start_ui_electron.sh) |
| Runtime composition | [`compose.core-runtime.yml`](compose.core-runtime.yml) |

## Validation

Run the smallest relevant validation for the changed owner surface. For API
work, use the locked development environment:

```bash
cd platform/api
uv run --frozen --group dev python -m pytest <focused tests>
```

Always run:

```bash
git diff --check
```

before committing. Do not stage generated build output, virtual environments,
dependency directories, local runtime state, databases, workflow results, or
logs.

## Security and operational boundaries

- Configure service settings through supported installation/configuration paths.
  Keep credentials and machine-specific topology out of Git.
- Use the API/workflow registry and supported launch paths; do not treat
  historical scripts or disconnected Nextflow files as production interfaces.
- Private ingress is an explicit managed boundary. Preserve authorization and
  fail-closed admission checks when publishing a service.
- BioXP and other instrument integrations are capability-gated. The external or
  instrument-local authoritative runtime remains responsible for hardware safety.
- Retire unsupported code once reachability and dependency review proves it is
  not needed. Git history is the archive; do not keep inactive source or docs for
  archaeology.
