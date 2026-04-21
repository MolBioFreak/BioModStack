# BioModStack Core-Runtime Workflow Adapter Cutover Plan

> **For Hermes:** Use subagent-driven-development or direct TDD execution. Do not claim container-owned workflow truth until the adapter service, API routing, and live cutover smoke all pass.

**Goal:** Convert BioModStack container mode from an honest first-wave guarded control plane into an honest operational default that can launch, monitor, and cancel host-native Nextflow workflows through a real workflow adapter boundary.

**Architecture:** Keep the browser/web/API stack containerized, but move workflow ownership to a host-native adapter service that shares the mounted BioModStack state and database with the containerized API. The containerized API and GPU orchestrator stop assuming local PIDs/process tables and instead use a small HTTP contract for launch, cancel, and running-job reconciliation.

**Tech Stack:** FastAPI (`platform/api/main.py`), existing Nextflow launcher (`platform/api/services/nextflow.py`), GPU scheduler (`platform/api/services/gpu_orchestrator.py`), job cancellation helpers (`platform/api/services/job_control.py`), runtime policy guard (`platform/api/runtime_policy.py`), workstation service manager (`biomodstack_services.py`), core-runtime compose stack (`compose.core-runtime.yml`), and new host workflow-adapter FastAPI service.

---

## Current repo-grounded truth

These are the facts this cutover plan is built around:

- `platform/api/services/nextflow.py` still launches workflows as host-local subprocesses with `asyncio.create_subprocess_exec(...)`.
- `job.nextflow_run_id` is currently set to `str(process.pid)`.
- `services.gpu_orchestrator.GPUOrchestrator.check_job_completions()` still trusts:
  - `services.nextflow.get_running_jobs()`
  - host `ps aux`
  - launcher PID ancestry and process-name matching.
- `services.job_control.cancel_job_lineage()` still cancels through `cancel_nextflow_job(job.nextflow_run_id)`.
- `compose.core-runtime.yml` already reserves `BMS_WORKFLOW_ADAPTER_URL`, but the API code does not yet consume it.
- `biomodstack_services.py` already supports a `container` runtime mode and a `biomodstack-core-runtime.service`, but there is no host-native workflow-adapter systemd unit yet.
- `platform/api/runtime_policy.py` currently blocks workflow launches entirely in `BMS_CORE_RUNTIME_MODE=1`.

That means container mode is still not the truthful owner of workflow lifecycle today.

---

## Target boundary after this tranche

### Runtime ownership model

After this cutover:

- `bms-api` container owns:
  - API routes
  - queue state
  - browser-facing control plane
  - GPU scheduling decisions
- host workflow adapter owns:
  - actual Nextflow process launch
  - host PID/process registry truth
  - cancellation signal delivery
  - host-side completion/finalization logic already implemented in `services.nextflow`
- shared mounted BioModStack state owns:
  - SQLite DB
  - outputs
  - logs
  - work dirs

### Honest rule

Container mode becomes operationally honest only when all of the following are true:

1. container-mode launch/resume/resubmit no longer 409 when adapter is configured
2. actual Nextflow launch happens through the host adapter, not from inside the container
3. running-job reconciliation no longer depends on container-local process tables
4. cancellation works against adapter-owned runs
5. live cutover succeeds repeatedly on the workstation

---

## Concrete adapter contract

Use a dedicated host-local FastAPI app bound to `127.0.0.1:8001`.

### Endpoints

#### `GET /api/workflow-adapter/health`
Returns readiness and mode:

```json
{
  "status": "healthy",
  "service": "biomodstack-workflow-adapter",
  "mode": "native-host"
}
```

#### `POST /api/workflow-adapter/launch`
Request:

```json
{
  "job_id": "uuid",
  "model_id": "boltz2",
  "mode": "predict",
  "params": {"gpu_id": 0},
  "output_dir": "/mnt/BioModStack/bms_results/..."
}
```

Response:

```json
{
  "accepted": true,
  "job_id": "uuid",
  "launch_mode": "native-host"
}
```

Behavior:
- schedules `launch_nextflow_job_detached(...)` on the host
- does not recursively route back through the adapter client
- may return before host PID exists
- duplicate active launches should be treated as idempotent/accepted, not as a second launch

#### `POST /api/workflow-adapter/cancel`
Request:

```json
{
  "nextflow_run_id": "opaque-run-id-or-pid"
}
```

Response:

```json
{
  "cancelled": true
}
```

Behavior:
- initially supports the existing PID-shaped `nextflow_run_id`
- contract remains opaque so we can stop treating it as an integer everywhere else

#### `GET /api/workflow-adapter/running-jobs`
Response:

```json
{
  "running_jobs": {
    "job-1": 12345,
    "job-2": 0
  }
}
```

Behavior:
- proxies host-native `get_running_jobs()`
- preserves the existing `0 = launching but subprocess not registered yet` convention so orchestrator semantics stay stable

---

## File-level implementation map

### Create

- `platform/api/services/workflow_adapter.py`
- `platform/api/routers/workflow_adapter.py`
- `platform/api/workflow_adapter_app.py`
- `platform/api/tests/test_workflow_adapter.py`
- `scripts/run_biomodstack_workflow_adapter.sh`
- `docs/plans/2026-04-20-core-runtime-workflow-adapter-cutover.md`

### Modify

- `platform/api/runtime_policy.py`
- `platform/api/main.py`
- `platform/api/services/nextflow.py`
- `platform/api/services/gpu_orchestrator.py`
- `platform/api/services/job_control.py`
- `platform/api/tests/test_core_runtime_workflow_guard.py`
- `platform/api/tests/test_core_runtime_scaffold.py`
- `biomodstack_services.py`
- `platform/api/tests/test_biomodstack_services.py`
- `compose.core-runtime.yml`
- `.env.core-runtime.example`
- `README.md`

### Explicit non-goals for this tranche

Do not do these now:

- containerize Nextflow itself
- move Apptainer/Singularity into the container stack
- change BioXP runtime ownership
- redesign result parsing or stage-progress extraction
- redesign the desktop shell/launch-surface policy again

---

## Implementation tasks

### Task 1: Write failing tests for the adapter client contract

**Objective:** Prove the API has a real way to discover and use the workflow adapter instead of just carrying a dead env seam.

**Files:**
- Create: `platform/api/tests/test_workflow_adapter.py`
- Modify later: `platform/api/services/workflow_adapter.py`

**Tests to add first:**
- `test_workflow_adapter_disabled_when_env_missing()`
- `test_workflow_adapter_enabled_when_base_url_is_set()`
- `test_workflow_launch_mode_is_adapter_in_core_runtime_when_url_is_set()`
- `test_launch_request_posts_expected_payload_to_adapter()`
- `test_cancel_request_posts_expected_payload_to_adapter()`
- `test_running_jobs_request_reads_adapter_response()`

**Verification command:**
`source venv/bin/activate && python -m pytest platform/api/tests/test_workflow_adapter.py -q`

Expected initial result: FAIL because the module does not exist yet.

---

### Task 2: Implement the adapter client module

**Objective:** Centralize all environment parsing and HTTP client calls for the workflow adapter.

**Files:**
- Create: `platform/api/services/workflow_adapter.py`
- Test: `platform/api/tests/test_workflow_adapter.py`

**Required functions:**
- `workflow_adapter_base_url()`
- `workflow_adapter_enabled()`
- `workflow_launch_mode()` returning `native|adapter|guarded`
- `launch_via_workflow_adapter(...)`
- `cancel_via_workflow_adapter(nextflow_run_id)`
- `get_adapter_running_jobs()`

**Rules:**
- use stdlib `urllib.request`, not a new HTTP dependency
- accept either `http://host:port` or a trailing-slash base URL
- fail with precise `RuntimeError` text when adapter calls fail
- JSON parse defensively and validate expected keys

**Verification command:**
`source venv/bin/activate && python -m pytest platform/api/tests/test_workflow_adapter.py -q`

---

### Task 3: Replace the blanket container guard with mode-aware policy tests

**Objective:** Make container mode allowed only when a real adapter is configured.

**Files:**
- Modify: `platform/api/runtime_policy.py`
- Modify: `platform/api/tests/test_core_runtime_workflow_guard.py`

**Behavior changes:**
- `BMS_CORE_RUNTIME_MODE=1` and no adapter URL -> still guarded
- `BMS_CORE_RUNTIME_MODE=1` and adapter URL set -> launches allowed
- detail text should explain whether the runtime is guarded vs adapter-backed

**New tests:**
- `test_core_runtime_mode_with_adapter_allows_workflow_launches()`
- `test_guard_message_mentions_adapter_requirement_when_missing()`

**Verification command:**
`source venv/bin/activate && python -m pytest platform/api/tests/test_core_runtime_workflow_guard.py -q`

---

### Task 4: Route Nextflow launch/cancel/running-jobs through the adapter seam

**Objective:** Preserve the existing Nextflow implementation for host-native execution while making container mode delegate through the adapter.

**Files:**
- Modify: `platform/api/services/nextflow.py`
- Modify: `platform/api/services/job_control.py`
- Modify: `platform/api/tests/test_core_runtime_workflow_guard.py`
- Modify/Create: `platform/api/tests/test_workflow_adapter.py`

**Required code changes:**
- in `launch_nextflow_job(...)`, before host subprocess creation:
  - if adapter mode is active, call `launch_via_workflow_adapter(...)` and return
- keep host-native launch path untouched when adapter mode is inactive
- make the duplicate-running DB guard tolerant of adapter-backed bootstrapping:
  - do not skip merely because `job.status == running` if `job.nextflow_run_id` is still empty and there is no local active process
- in `cancel_nextflow_job(...)`:
  - when adapter mode is active, call `cancel_via_workflow_adapter(...)`
  - do not require `nextflow_run_id` to be integer-shaped outside the native host path
- in `get_running_jobs()`:
  - when adapter mode is active, proxy adapter response
  - otherwise keep existing local registry behavior

**Important invariant:**
The host workflow-adapter service must run with adapter-client routing disabled to avoid recursive self-calls.

**Verification command:**
`source venv/bin/activate && python -m pytest platform/api/tests/test_workflow_adapter.py platform/api/tests/test_core_runtime_workflow_guard.py platform/api/tests/test_cancel_lineage.py -q`

---

### Task 5: Add the host workflow-adapter FastAPI app and router

**Objective:** Stand up a tiny host-native HTTP boundary that reuses the real Nextflow implementation.

**Files:**
- Create: `platform/api/routers/workflow_adapter.py`
- Create: `platform/api/workflow_adapter_app.py`
- Modify: `platform/api/tests/test_workflow_adapter.py`

**Router responsibilities:**
- `/api/workflow-adapter/health`
- `/api/workflow-adapter/launch`
- `/api/workflow-adapter/cancel`
- `/api/workflow-adapter/running-jobs`

**App responsibilities:**
- initialize FastAPI app for adapter-only surface
- no need to mount the entire BioModStack API router set
- should not start the GPU orchestrator
- should not expose unrelated app routes

**Verification command:**
`source venv/bin/activate && python -m pytest platform/api/tests/test_workflow_adapter.py -q`

---

### Task 6: Make GPU completion reconciliation adapter-aware

**Objective:** Stop container-mode workflow truth from depending on container-local `ps aux` or container-local launch registries.

**Files:**
- Modify: `platform/api/services/gpu_orchestrator.py`
- Add/update tests near the adapter test module or an orchestrator-focused test file if one already exists

**Required changes:**
- reuse `services.nextflow.get_running_jobs()` after it becomes adapter-aware
- keep `ps aux` fallback only for host-native mode
- treat adapter mode as the authoritative source for active launches
- keep the rest of the stale-reconcile logic intact
- do not require `nextflow_run_id` to parse as int for general reconciliation logic
- only use PID ancestry optimizations when the run id is actually PID-shaped

**Verification command:**
`source venv/bin/activate && python -m pytest platform/api/tests/test_workflow_adapter.py platform/api/tests/test_core_runtime_workflow_guard.py -q`

---

### Task 7: Add host workflow-adapter service management to workstation runtime control

**Objective:** Make container mode operationally complete from `start_ui.sh` and `manage_desktop_services.py`.

**Files:**
- Create: `scripts/run_biomodstack_workflow_adapter.sh`
- Modify: `biomodstack_services.py`
- Modify: `platform/api/tests/test_biomodstack_services.py`

**Required behavior:**
- add a new unit name, e.g. `biomodstack-workflow-adapter.service`
- container runtime target should want both:
  - `biomodstack-workflow-adapter.service`
  - `biomodstack-core-runtime.service`
- service manager status should expose adapter service readiness and adapter log path
- `start_all(..., runtime_mode='container')` should wait for adapter health in addition to API and frontend
- service runner script should:
  - activate the same repo/runtime environment style as the API script
  - bind to `127.0.0.1:8001`
  - explicitly unset or ignore `BMS_WORKFLOW_ADAPTER_URL` so native host launch stays local

**Verification command:**
`source venv/bin/activate && python -m pytest platform/api/tests/test_biomodstack_services.py -q`

---

### Task 8: Update compose/env/docs to make container mode operational by default

**Objective:** Make the adapter a first-class part of the documented and configured runtime.

**Files:**
- Modify: `compose.core-runtime.yml`
- Modify: `.env.core-runtime.example`
- Modify: `README.md`
- Modify: `platform/api/tests/test_core_runtime_scaffold.py`

**Required changes:**
- default `BMS_WORKFLOW_ADAPTER_URL` should point at `http://host.docker.internal:8001`
- docs should stop saying the adapter is reserved for later
- docs should describe the real ownership split:
  - container web/control plane
  - host-native workflow adapter
- scaffold tests should assert the new default env and service contract

**Verification command:**
`source venv/bin/activate && python -m pytest platform/api/tests/test_core_runtime_scaffold.py -q`

---

### Task 9: Run targeted regression tests, then broader API/service validation

**Objective:** Prove the refactor works before live cutover.

**Command set:**

```bash
source venv/bin/activate
python -m pytest \
  platform/api/tests/test_workflow_adapter.py \
  platform/api/tests/test_core_runtime_workflow_guard.py \
  platform/api/tests/test_biomodstack_services.py \
  platform/api/tests/test_cancel_lineage.py \
  platform/api/tests/test_core_runtime_scaffold.py -q
```

Then run a broader sweep if time allows:

```bash
source venv/bin/activate
python -m pytest platform/api/tests/ -q
```

---

### Task 10: Perform the live workstation cutover and repeated smoke

**Objective:** Prove container mode is now the operational default on the actual workstation.

**Live sequence:**

1. Stop any existing dev runtime:
   - `./start_ui.sh stop --runtime dev`
2. Start container runtime:
   - `./start_ui.sh start --runtime container`
3. Verify systemd/user status:
   - `./start_ui.sh status --runtime container`
4. Verify adapter health:
   - `curl -fsS http://127.0.0.1:8001/api/workflow-adapter/health`
5. Verify API health through the container stack:
   - `curl -fsS http://127.0.0.1:8000/api/health`
6. Verify browser surface:
   - `curl -I http://127.0.0.1:5173/bms/`
7. Submit a real lightweight workflow smoke job through the API
8. Confirm the job launches through the adapter and reaches running/completed truth in the shared DB
9. Repeat the stop/start/smoke cycle at least twice to prove persistence and restart honesty

**Evidence to capture:**
- service status output
- adapter health output
- API health output
- core-runtime log tail
- workflow-adapter log tail
- smoke job ID, queue transitions, and final state

---

## Acceptance criteria

This tranche is done only when all of these are true:

- `BMS_WORKFLOW_ADAPTER_URL` is consumed by real code, not just compose/tests
- container mode with adapter configured no longer returns 409 for launch/resume/resubmit
- actual launches are performed by the host adapter service
- cancellation and running-job queries route through the adapter when active
- `start_ui.sh start --runtime container` brings up both the compose stack and the host workflow adapter
- `start_ui.sh status --runtime container` reports adapter state honestly
- repeated live cutover/smoke succeeds on the workstation
- docs clearly describe the new ownership boundary

---

## Pitfalls to avoid

- Do not let the host adapter inherit `BMS_WORKFLOW_ADAPTER_URL` or it will recursively call itself.
- Do not keep treating `nextflow_run_id` as guaranteed-int everywhere once the adapter boundary exists.
- Do not claim success after unit tests alone; live restart and smoke are required.
- Do not break the existing dev runtime path while making container mode adapter-backed.
- Do not overwrite the unrelated local `platform/api/services/nextflow.py` Boltz CP normalization diff unless intentionally keeping it.

---

## Definition of done for reporting back

The final report must include:

- what changed in the code
- which tests were added/updated
- exact live cutover commands run
- smoke job evidence
- any remaining caveats or second-wave work
