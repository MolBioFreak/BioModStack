# BioModStack Core Runtime Containerization and Robustness Plan

> **For Hermes:** Use this as the approved boundary map before implementing containerization. Do not collapse host-native workflow/hardware edges into the first container wave.

**Goal:** Make BioModStack's core runtime more portable and robust by preparing a safe container boundary around the web/control plane without breaking Nextflow execution, BioXP integration, or Molstar-based frontend viewers.

**Architecture:** Treat BioModStack as two layers. Layer 1 is the portable core runtime: API service, production frontend serving layer, config/state/results volumes, and a launcher that can run under `systemd --user` on Linux. Layer 2 is a set of host-native adapters that continue to own workflow execution and hardware integration until their interfaces are formalized.

**Tech Stack:** FastAPI control plane, Vite/React frontend, `systemd --user` desktop service layer (`biomodstack_services.py`), Nextflow + Apptainer/Singularity execution in `platform/api/services/nextflow.py`, BioXP linkage/proxy router in `platform/api/routers/bioxp.py`, PDBe Molstar frontend viewer in `platform/frontend/src/lib/molstar-loader.ts` and `platform/frontend/src/components/MolstarViewer.tsx`.

---

## Live repo findings that matter

### 1. There is no container scaffolding yet

Repo search found no existing:
- `Dockerfile*`
- `*compose*.y*ml`

So this is a fresh runtime design, not a cleanup of an existing Docker path.

### 2. The desktop service layer is now a good launcher seam

Recent work introduced a proper service boundary:
- `biomodstack_services.py`
- `scripts/manage_desktop_services.py`
- `scripts/run_biomodstack_api.sh`
- `scripts/run_biomodstack_frontend.sh`
- `docs/Desktop_Runtime_and_Shell_Architecture.md`

That is useful because Linux can keep `systemd --user` as the top-level launcher even after containerization. The service layer should eventually launch the container stack, not raw long-lived uvicorn/Vite processes.

### 3. Nextflow is heavily host-shaped today

`platform/api/services/nextflow.py` is not a thin RPC client. It directly:
- builds `nextflow run ...` commands
- hardcodes profile suffixes like `workstation_ryzen7960x`
- injects explicit host paths for:
  - `code_root`
  - `data_root`
  - `weights_root`
  - `msa_local_db`
  - `msa_cache_dir`
  - `container_dir`
- assumes host-local Apptainer/Singularity-style assets
- manages `NXF_CACHE_DIR` and resume semantics using host work/output directories
- shells out to host tools like `apptainer`

This is the single biggest reason a naive API container would be fragile.

### 4. BioXP is still a host/hardware adapter surface

`platform/api/routers/bioxp.py` still mixes:
- linkage persistence
- robot HTTP proxying
- workstation-side SSH daemon probing/control

The new `docs/plans/2026-04-19-bioxp-connection-revision.md` documents the correct direction: robot-local runtime ownership, BioModStack as linkage/proxy first, SSH only as break-glass maintenance.

### 5. The frontend already has a production build path

`platform/frontend/package.json` already supports:
- `npm run build`
- `vite build`
- `vite preview`

`platform/frontend/vite.config.ts` already has a production base path:
- `base: '/bms/'` in production
- `/` in dev

That means the frontend can move away from dev-server ownership without changing the route contract.

### 6. Molstar/viewers are lower risk if the browser contract stays stable

Viewer-specific findings:
- `platform/frontend/src/lib/molstar-loader.ts` dynamically imports the installed `pdbe-molstar` package instead of relying on an external CDN
- `platform/frontend/src/components/MolstarViewer.tsx` builds absolute URLs from `window.location.origin` for `/...` structure paths
- production `/bms/` routing is already explicit in Vite config

This is a good sign: the viewer surface is mostly a frontend packaging/base-path concern, not a reason to block runtime containerization.

---

## First-wave containerization boundary

## Inside the first container wave

These are the safe early targets:
- BioModStack API runtime, but only after workflow/hardware edges are behind stable host-adapter interfaces
- production frontend serving layer
- persistent config/state/results mounts needed by the web/control plane
- optional reverse proxy/static serving layer if needed for `/bms/` and `/api`

## Explicitly outside the first container wave

These remain host-native at first:
- Nextflow launcher/executor logic
- Apptainer/Singularity runtime ownership
- MSA/cache/model-drive path ownership
- BioXP bridge/control path
- robot/Tailscale/SSH glue
- GPU/hardware-adjacent maintenance shims

## Key rule

Do not describe the first wave as “containerize everything.”
The safe target is:
- containerized core runtime
- host-native execution/hardware adapters
- a stable interface between them

---

## What must be true before the API can safely live in a container

1. Nextflow launches cannot depend on container-internal path assumptions.
2. Resume behavior cannot depend on ambiguous mixed host/container work directories.
3. BioXP control cannot rely on workstation-owned SSH daemon supervision.
4. API startup must not assume dev-only runtime patterns (`--reload` as the default steady state).
5. Frontend serving must have a production mode that is not just a permanently supervised Vite dev server.

Until those are addressed, a Dockerized API would mostly be a new failure mode generator.

---

## Recommended phased roadmap

## Phase 0: Harden the production runtime contract before containerizing it

**Objective:** Remove dev-server assumptions from the steady-state runtime.

**Why first:** The current service layer is robust about ownership, but it still launches dev-shaped processes (`uvicorn --reload` depending on env defaults, `npm run dev`). Containerizing those directly would preserve the wrong runtime model.

**Likely files:**
- `scripts/run_biomodstack_api.sh`
- `scripts/run_biomodstack_frontend.sh`
- `biomodstack_services.py`
- `docs/Desktop_Runtime_and_Shell_Architecture.md`
- `README.md`

**Desired outcome:**
- explicit dev vs production runtime modes
- production frontend serving path that uses built assets instead of Vite dev server
- production API launch mode that does not rely on reload-oriented behavior
- `systemd --user` remains the launcher, not the raw process owner of dev-only commands

## Phase 1: Extract host-native adapter seams for Nextflow and BioXP

**Objective:** Make workflow execution and robotics look like explicit host adapters instead of direct internals of the portable API runtime.

**Likely files:**
- `platform/api/services/nextflow.py`
- `platform/api/routers/jobs.py`
- `platform/api/routers/bioxp.py`
- `platform/api/tests/test_nanopore_nextflow.py`
- `platform/api/tests/test_bioxp_router.py`
- new adapter/helper modules under `platform/api/services/` or `platform/api/adapters/`

**Required outcome:**
- the core API has a clear contract for “launch workflow”, “query workflow state”, and “query/control BioXP linkage”
- host-coupled pathing and process ownership are isolated behind those contracts
- later containerization can redirect those contracts to host services without rewriting the whole API

## Phase 2: Add container scaffolding for the core runtime only

**Objective:** Introduce reproducible images/manifests for the web/control plane once the boundaries are clean enough.

**Likely files to create:**
- `docker/api.Dockerfile`
- `docker/frontend.Dockerfile`
- `compose.core-runtime.yml` or equivalent
- `.dockerignore`
- optional runtime env templates/docs

**Likely files to modify:**
- `biomodstack_services.py`
- `scripts/manage_desktop_services.py`
- `README.md`
- `docs/Workstation Set Up and Install Guide.md`

**Required outcome:**
- API/frontend can run as a containerized core stack
- Linux workstation launcher can manage the stack predictably
- persistent paths are mounted explicitly
- `/bms/` and `/api` contracts stay stable for browsers and viewers

## Phase 3: Live smoke validation and only then expansion

**Objective:** Prove the new boundary works before expanding scope.

**Required live gates:**
1. BioModStack loads in the browser through the production path
2. Molstar structure rendering still works
3. a real Nextflow smoke job launches successfully through the host adapter path
4. a Nextflow resume flow still works
5. BioXP cockpit can connect
6. BioXP daemon/runtime status remains understandable and honest
7. one supervised safe-motion check succeeds while the operator watches

Only after these pass should you consider broadening container ownership.

---

## Recommended next concrete work package

This is the next package I would actually implement, in order:

### Work Package A: production runtime hardening

**Objective:** Stop treating dev commands as the durable workstation runtime.

**Scope:**
- add explicit production mode for API launch
- add explicit production frontend serving mode using built assets
- keep the current service layer, but let it choose dev vs prod runners intentionally

**Why this is the right next package:**
- improves robustness immediately, even before Docker exists
- gives the future containers a sane steady-state process model
- avoids baking `npm run dev` and reload-oriented uvicorn behavior into the container plan

### Work Package B: adapter seam extraction for Nextflow/BioXP

**Objective:** isolate host-native execution and hardware boundaries.

**Scope:**
- define internal adapter interfaces for workflow launch/state and BioXP linkage/runtime control
- move direct host/process assumptions behind those adapters
- preserve existing route/UI contracts as much as possible

**Why before Docker:**
- otherwise the API container would still be forced to understand host-only path/process reality directly

### Work Package C: container manifests for the core runtime

**Objective:** package the now-cleaner API/frontend core.

**Scope:**
- add image builds
- add compose/runtime manifests
- wire the Linux service layer to launch the container stack

---

## Acceptance criteria for the planning phase

This plan is good enough to execute if everyone agrees on these points:

1. no existing Docker path needs preservation because none exists yet
2. the service layer is the right launcher seam for a future container stack
3. Nextflow is the biggest disruption risk because it is host/path/resume shaped today
4. BioXP is still a host adapter boundary and should not be absorbed into first-wave containers
5. Molstar/viewers are not the main blocker as long as `/bms/`, `/api`, and structure URL behavior stay stable
6. the next implementation package is production runtime hardening first, not “Dockerize everything immediately”

---

## Definition of done

The broader containerization/robustness effort is done when:
- the BioModStack core runtime can be launched reproducibly as a portable stack
- the Linux workstation still has a robust local launcher path
- Nextflow and BioXP have clean host-adapter boundaries
- viewer/browser contracts remain stable
- the first-wave migration does not break workflow execution, resume behavior, or robot connectivity