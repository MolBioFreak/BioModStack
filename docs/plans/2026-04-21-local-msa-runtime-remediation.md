# BioModStack Local MSA Runtime Remediation Plan

> **For Hermes:** Treat this as a phased rollout, not a single PR. Land the hardening and host-supervised reuse layers before changing workflow batching. Do not claim local MSA is fixed until the host server smoke, isolated-task smoke, and multi-sequence batch smoke all pass.

**Goal:** Make local MSA fast, truthful, and repeatable by removing task-owned persistent gpuserver state, switching gpuserver-backed queries onto the documented fast-path defaults, and activating true batch search for eligible multi-sequence jobs.

**Architecture:** Keep the existing local-vs-ColabFold-API provider split and preserve the current quality presets, but split responsibilities cleanly: a host-owned gpuserver control plane manages long-lived MMseqs servers; task-side helpers become stateless clients that either reuse a confirmed host server or launch a task-local transient gpuserver; workflow layers batch eligible local-fast queries instead of invoking one MMseqs subprocess chain per protein component. Do not add jackhmmer or change DB layout in this tranche.

**Tech Stack:** `scripts/run_local_msa.py`, `scripts/batch_msa.py`, `scripts/prepare_protenix_msa.py`, new `scripts/local_msa_runtime.py`, `scripts/test_run_local_msa.py`, new `scripts/test_batch_msa.py`, new `scripts/test_local_msa_runtime.py`, `platform/api/services/msa_server.py`, `platform/api/routers/msa.py`, new `platform/api/tests/test_msa_server.py`, `modules/structure_prediction.nf`, `modules/boltz_cp_experimental.nf`, `modules/protenix.nf`, `modules/antibody_batch.nf`, `workflows/antibody_denovo.nf`, `platform/api/tests/test_structure_prediction_batch.py`, `platform/api/tests/test_boltz_cp_experimental.py`, and the canonical MSA/runtime docs.

---

## Current repo-grounded truth

These are the facts this plan is built around:

- `scripts/run_local_msa.py` still treats an empty `/proc/<pid>/cmdline` as effectively alive in `_is_matching_gpuserver_process(...)` (`1189-1202`). That is the wrong answer for stale PIDs and kernel threads.
- `platform/api/services/msa_server.py` duplicated the same liveness assumption (`57-63`), so the bug exists in both the task helper and the host control surface.
- `scripts/run_local_msa.py` defaults `--gpu-server-db-load-mode` to `0` (`3215-3223`) and passes that same value into gpuserver-backed search calls (`2563-2570`, `2870-2877`). That is doc-misaligned for ColabFold-style fast single-query reuse, which expects `db-load-mode 2` on the client search path.
- `scripts/run_local_msa.py` caps gpuserver wait to 30 seconds in opportunistic/auto mode (`882-892`) and defaults startup wait to `1.0` seconds (`863`, `3222-3223`), which is too eager for cold-start reality.
- `modules/structure_prediction.nf` still defaults `msa_gpu_server_db_load_mode` to `0` (`45-48`, `487-490`) and materializes MSA per chain via `run_local_msa.py` (`651-713`).
- `modules/structure_prediction.nf` already contains a dormant `BatchMSAGeneration` process (`112-146`), so the repo already has a place to wire true batch search without inventing a new workflow surface.
- `scripts/batch_msa.py` already implements a real batched MMseqs path for eligible fast-preset jobs and explicitly documents why it is better for throughput (`5-17`, `325-329`).
- `scripts/prepare_protenix_msa.py` already uses `run_batch_msa(...)` (`430-468`), which proves the codebase already knows how to fan in sequences, run batched local MSA, and map results back onto structured payloads.
- `platform/api/routers/msa.py` and `platform/api/services/msa_server.py` already expose `/server/status`, `/server/start`, and `/server/stop`, but they currently inherit the same stale-PID trust and `db_load_mode 0` defaults.
- Real task launch artifacts show Apptainer running with `--pid` in Nextflow task wrappers (`work/3f/1e3db8.../.command.run:101-102`), so shared PID metadata written from inside one task cannot be treated as trustworthy cross-task reuse state.

---

## Definition of done

The tranche is complete only when all of the following are true:

1. No isolated Nextflow/Apptainer task launches or reuses a so-called persistent gpuserver by trusting shared PID metadata.
2. Host-supervised gpuserver reuse is determined by a host-owned status contract, not by `/proc` inspection from inside isolated task namespaces.
3. Gpuserver-backed searches default to `db-load-mode 2` on the client side across all local-MSA entrypoints.
4. Multi-sequence local-fast jobs use one batched search whenever the request is eligible.
5. Balanced/maximum local workflows and remote ColabFold API workflows still work.
6. Logs and result JSON make it obvious whether a run used `host`, `transient`, or `off` gpuserver mode, and why.

---

## Core invariants to preserve

- Keep `msa_provider=local|colabfold_api` intact.
- Keep the existing preset names (`fast`, `balanced`, `maximum`) intact.
- Skip HMMER entirely in this tranche: do not add, validate, or plan jackhmmer/HMMER rescue stages.
- Do not rewrite DB preparation or ColabFold DB layout.
- Do not make frontend/UI changes a prerequisite for the backend/runtime fix.
- Do not bundle this with unrelated structure-prediction refactors.

---

## File-level implementation map

### Create

- `scripts/local_msa_runtime.py`
- `scripts/test_local_msa_runtime.py`
- `scripts/test_batch_msa.py`
- `platform/api/tests/test_msa_server.py`
- `docs/plans/2026-04-21-local-msa-runtime-remediation.md`

### Modify

- `scripts/run_local_msa.py`
- `scripts/batch_msa.py`
- `scripts/prepare_protenix_msa.py`
- `scripts/test_run_local_msa.py`
- `scripts/test_prepare_protenix_msa.py`
- `platform/api/services/msa_server.py`
- `platform/api/routers/msa.py`
- `modules/structure_prediction.nf`
- `modules/boltz_cp_experimental.nf`
- `modules/protenix.nf`
- `modules/antibody_batch.nf`
- `workflows/antibody_denovo.nf`
- `platform/api/tests/test_structure_prediction_batch.py`
- `platform/api/tests/test_boltz_cp_experimental.py`
- `platform/api/services/nextflow.py` only if API-side default/normalization logic must pin the new local-MSA defaults
- canonical docs that describe local MSA runtime ownership and batch behavior

---

## Explicit non-goals

Do not do these in this tranche:

- skip jackhmmer/HMMER entirely for this fix; no rescue-search implementation, validation pass, or benchmarking
- merge `batch_msa.py` into `run_local_msa.py`
- redesign the ColabFold API path
- change the ColabFold DB build/install procedure
- fold this into the broader core-runtime workflow-adapter cutover unless container-mode ownership forces it
- refactor every MSA-related script just because `run_local_msa.py` is large

---

## Target behavior contract after the fix

### gpuserver mode semantics

Preserve the existing `gpu_server_mode` flag names, but make them truthful:

- `off`
  - never use gpuserver
- `auto`
  - if a confirmed host server is available for the selected DB/GPU contract, reuse it
  - otherwise launch a task-local transient gpuserver when local GPU search is still viable
  - otherwise fall back according to the existing GPU/CPU rules
- `required`
  - require gpuserver-backed search
  - use a confirmed host server if available
  - otherwise allow a task-local transient gpuserver in the same task
  - fail clearly if neither host nor transient gpuserver succeeds
- `persistent`
  - outside isolated task contexts, host/manual operators may still start or reuse a long-lived server
  - inside isolated task contexts, never launch or trust cross-task persistent PID metadata
  - prefer confirmed host reuse; otherwise degrade to `transient` with a loud log message explaining why

### runtime decision telemetry

Every local-MSA execution path should surface a machine-readable decision block, either in the quality JSON or manifest entry, including at least:

- `msa_provider`
- `preset`
- `selected_gpu_id`
- `gpuserver_mode_requested`
- `gpuserver_mode_effective`
- `gpuserver_source` (`host`, `transient`, `off`)
- `gpuserver_db_load_mode`
- `host_server_checked`
- `host_server_ready`
- `batch_strategy` (`single`, `batch-fast`, `per-sequence-colabfold-compatible`)
- `fallback_reason`

---

## Phase 0 — harden truth and fence regressions

**Objective:** Lock down the bad assumptions with tests before changing behavior.

**Files:**
- Create: `scripts/test_local_msa_runtime.py`
- Create: `scripts/test_batch_msa.py`
- Create: `platform/api/tests/test_msa_server.py`
- Modify: `scripts/test_run_local_msa.py`
- Modify: `scripts/test_prepare_protenix_msa.py`
- Modify: `platform/api/tests/test_structure_prediction_batch.py`
- Modify: `platform/api/tests/test_boltz_cp_experimental.py`

**Required regression coverage:**

1. `scripts/test_local_msa_runtime.py`
- empty `/proc/<pid>/cmdline` means `not alive / not reusable`
- kernel-thread-shaped or cmdline-less PID metadata is treated as stale
- `db-load-mode 2` is the default for gpuserver-backed client searches
- isolated task context never selects task-owned persistent reuse
- host-server status lookup failure does not crash `auto`; it downgrades cleanly

2. `scripts/test_run_local_msa.py`
- `persistent` inside isolated task context does not call `ensure_persistent_mmseqs_gpuserver(...)`
- host-server-ready path runs search with `--gpu-server 1 --db-load-mode 2`
- missing host server plus `persistent` logs a downgrade and uses the transient path
- the 30-second opportunistic cap is gone or explicitly replaced with the new policy

3. `scripts/test_batch_msa.py`
- fast preset chooses true batch search
- balanced/maximum still choose the per-sequence ColabFold-compatible path
- batch mode passes through the new gpuserver policy/defaults consistently

4. `platform/api/tests/test_msa_server.py`
- `list_servers()` marks cmdline-less entries stale
- `ensure_server_for_db()` defaults to `db_load_mode=2`
- `/api/msa/server/status` and `/api/msa/server/start` default to `2`
- returned server payload includes enough contract info for task-side host reuse

5. workflow text/regression tests
- `test_structure_prediction_batch.py` must assert that the eligible local-fast multi-sequence path uses batch materialization instead of per-chain `run_local_msa.py`
- `test_boltz_cp_experimental.py` must assert the CP MSA materialization path is wired to the batch-capable helper when eligible

**Acceptance gate:** All new tests fail before implementation and pass after implementation.

**Verification commands:**
- `source venv/bin/activate && python -m pytest scripts/test_run_local_msa.py scripts/test_prepare_protenix_msa.py scripts/test_local_msa_runtime.py scripts/test_batch_msa.py -q`
- `source venv/bin/activate && python -m pytest platform/api/tests/test_msa_server.py platform/api/tests/test_structure_prediction_batch.py platform/api/tests/test_boltz_cp_experimental.py -q`

---

## Phase 1 — extract a shared task-side runtime helper and fix gpuserver truthfulness

**Objective:** Stop lying about persistent reuse inside tasks and make the local-MSA scripts share one correct runtime policy.

**Files:**
- Create: `scripts/local_msa_runtime.py`
- Modify: `scripts/run_local_msa.py`
- Modify: `scripts/batch_msa.py`
- Modify: `scripts/prepare_protenix_msa.py`
- Modify: `scripts/test_run_local_msa.py`
- Modify: `scripts/test_prepare_protenix_msa.py`
- Modify: `scripts/test_local_msa_runtime.py`
- Modify: `scripts/test_batch_msa.py`

**Implementation details:**

### 1. Move runtime-policy logic out of `run_local_msa.py`
Create `scripts/local_msa_runtime.py` with small, testable helpers for:

- `is_isolated_task_context(env: dict[str, str]) -> bool`
  - detect Apptainer/Singularity/Nextflow task context via environment (`APPTAINER_CONTAINER`, `SINGULARITY_CONTAINER`, `NXF_TASK_WORKDIR`, or an explicit override such as `BMS_ISOLATED_MSA_TASK=1`)
- `read_proc_cmdline(pid: int) -> str`
- `pid_matches_gpuserver(pid: int, target_db: Path) -> bool`
  - empty cmdline = `False`
- `normalize_gpuserver_defaults(...)`
  - `db_load_mode=2`
  - startup wait increased to a sane non-1s value (target: `5.0` unless benchmarking proves another value)
  - no hard-coded 30-second opportunistic cap
- `query_host_msa_server_status(...)`
  - lightweight HTTP GET against the host status surface
  - returns contract info only; never does `/proc` inspection inside the task
- `resolve_gpuserver_plan(...)`
  - returns `host`, `transient`, or `off` plus reason metadata

### 2. Fix `run_local_msa.py`
Refactor it so:

- `_is_matching_gpuserver_process(...)` is strict, not permissive
- `ensure_persistent_mmseqs_gpuserver(...)` is never called from isolated tasks for cross-task reuse
- host-server reuse is chosen from the helper’s status contract, not shared PID files
- `gpu_server_db_load_mode` defaults to `2`
- gpuserver-backed client searches always pass the resolved client `db_load_mode`
- logs clearly state one of:
  - `Using host-supervised gpuserver`
  - `Starting transient gpuserver for this task`
  - `gpuserver disabled / falling back`

### 3. Fix `batch_msa.py`
Do not let `batch_msa.py` keep a second inconsistent policy. It must use the same helper and the same defaults, especially for:

- isolated-task detection
- host-server reuse
- transient fallback
- `db_load_mode=2`
- runtime decision telemetry in `msa_manifest.json`

### 4. Align `prepare_protenix_msa.py`
Do not re-implement policy here. It should either:

- consume the new helper directly for default resolution, or
- rely on the batch helper’s normalized defaults

**Acceptance gate:** single-sequence local MSA no longer depends on cross-task PID metadata to decide whether persistent reuse is safe.

**Verification commands:**
- `source venv/bin/activate && python -m pytest scripts/test_run_local_msa.py scripts/test_local_msa_runtime.py scripts/test_batch_msa.py -q`
- `python3 scripts/run_local_msa.py --sequence ACDEFGHIK --name msa_smoke --out_dir /tmp/bms_msa_smoke --db_path /mnt/BioModStack/colabfold_db --preset fast --gpu-server-mode off`

---

## Phase 2 — make the host MSA server control plane truthful

**Objective:** Make the existing MSA server endpoints a real host-supervised reuse contract instead of a second stale-PID implementation.

**Files:**
- Modify: `platform/api/services/msa_server.py`
- Modify: `platform/api/routers/msa.py`
- Modify: `platform/api/tests/test_msa_server.py`
- Modify: canonical docs that describe local MSA runtime ownership

**Implementation details:**

### 1. Fix stale-PID handling in the API service
`platform/api/services/msa_server.py` must match the stricter semantics from the script helper:

- empty `/proc/<pid>/cmdline` means stale
- stale metadata is deleted, not trusted
- all defaults use client `db_load_mode=2`

### 2. Return a host-usable reuse contract from `/api/msa/server/status`
The status payload must tell task-side callers what they need without making them inspect `/proc` themselves. Include at least:

- `gpu_id`
- `db_alias`
- `target_db`
- `running`
- `prefilter_mode`
- `max_seqs`
- `db_load_mode`
- `include_envdb`
- `server_owner_scope` (`host`)
- optional `reason` if not ready

The task-side client only needs enough information to answer: “Can I safely attempt a gpuserver-backed client search against the host-managed server for this DB/GPU contract?”

### 3. Preserve the current REST surface
Do not invent a new user-facing API if the current one works. Keep:

- `GET /api/msa/server/status`
- `POST /api/msa/server/start`
- `POST /api/msa/server/stop`

Just make the contract truthful.

### 4. Container-mode note
If the API process itself is not host-native in some deployments, do not let that ambiguity reintroduce PID lies. In that case the eventual ownership must fold into the host workflow-adapter/service boundary, not the container-local API process. That is a follow-on integration concern, not a reason to keep task-owned persistence now.

**Acceptance gate:** a host operator can pre-warm UniRef/EnvDB once, and isolated tasks can reuse that server without reading shared PID files.

**Verification commands:**
- `source venv/bin/activate && python -m pytest platform/api/tests/test_msa_server.py -q`
- host start smoke:
  - `python - <<'PY'
import json, urllib.request
req = urllib.request.Request(
    'http://127.0.0.1:8000/api/msa/server/start',
    data=json.dumps({'gpu_id': 0, 'include_envdb': False, 'db_load_mode': 2}).encode('utf-8'),
    headers={'Content-Type': 'application/json'},
    method='POST',
)
with urllib.request.urlopen(req, timeout=60) as r:
    print(r.read().decode())
PY`
- host status smoke:
  - `python - <<'PY'
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:8000/api/msa/server/status?gpu_id=0&db_load_mode=2', timeout=30) as r:
    print(r.read().decode())
PY`

---

## Phase 3 — cut workflow MSA over to real batching where it already fits

**Objective:** Stop paying one full local-MSA orchestration cost per protein chain when the job is eligible for one batch search.

**Files:**
- Modify: `modules/structure_prediction.nf`
- Modify: `modules/boltz_cp_experimental.nf`
- Modify: `modules/protenix.nf`
- Modify: `modules/antibody_batch.nf`
- Modify: `workflows/antibody_denovo.nf`
- Modify: `platform/api/tests/test_structure_prediction_batch.py`
- Modify: `platform/api/tests/test_boltz_cp_experimental.py`
- Modify: `scripts/batch_msa.py`
- Modify: `scripts/prepare_protenix_msa.py`

**Implementation details:**

### 1. Use `BatchMSAGeneration` in `modules/structure_prediction.nf`
The repo already has a batch process. Wire it into the real path.

Eligibility rule for the first tranche:

- `msa_provider == local`
- `preset == fast`
- no advanced per-sequence overrides that force the full ColabFold-compatible path
- more than one unique protein sequence needs materialization

For ineligible requests, keep the existing per-sequence `run_local_msa.py` fallback.

### 2. Batch by unique sequence, not by chain count
The batch manifest should be built from unique protein sequences after:

- homodimer/duplicate-sequence reuse
- existing precomputed-MSA detection
- cache-only filtering

That keeps the new path strictly better than the current per-chain loop.

### 3. Rehydrate outputs back into existing payload shapes
Do not force downstream model stages to understand batch manifests. The workflow should continue to hand downstream stages resolved `.a3m` paths in the same shape they expect today.

### 4. Cut `modules/boltz_cp_experimental.nf` over to the same batch-capable local path
The current CP MSA materialization still shells out one `run_local_msa.py` call per protein sequence. Replace that with the same unique-sequence batching strategy for eligible local-fast jobs.

### 5. Align the other local-MSA module defaults
Even if `protenix.nf`, `antibody_batch.nf`, and `workflows/antibody_denovo.nf` already use the batch helper, they still surface `db_load_mode 0` defaults. Update those surfaces to `2` so the repo stops fighting itself.

**Acceptance gate:** multi-sequence local-fast jobs hit one batched search path and still produce the same downstream `.a3m` references the model stages expect.

**Verification commands:**
- `source venv/bin/activate && python -m pytest platform/api/tests/test_structure_prediction_batch.py platform/api/tests/test_boltz_cp_experimental.py -q`
- create a small 3-sequence local-fast smoke payload and verify one `msa_manifest.json` plus three `.a3m` outputs
- inspect workflow logs to confirm the batch process ran once instead of launching one full local-MSA subprocess chain per unique protein sequence

---

## Phase 4 — benchmark, document, and make rollout honest

**Objective:** Prove the new path is better and update the canonical docs so nobody reintroduces the old model later.

**Files:**
- Modify: `docs/README.md`
- Modify: the canonical structure/runtime docs that describe local MSA behavior
- Modify: `platform/api/README.md` if it documents the MSA server endpoints or runtime ownership

**Benchmark matrix to capture:**

1. single-sequence local-fast, cold cache, no host server
2. single-sequence local-fast, warm cache, no host server
3. single-sequence local-fast, host server pre-warmed
4. multi-sequence local-fast, pre-fix per-sequence path vs post-fix batch path
5. balanced preset local path to prove no regression
6. remote ColabFold API path to prove no accidental breakage

For each case, record:

- wall-clock time
- whether `host`, `transient`, or `off` gpuserver mode was used
- selected GPU
- whether any CPU fallback happened
- whether the result depth/quality summary stayed within expected bounds

**Docs must explicitly state:**

- persistent gpuserver ownership is host-supervised, not task-supervised
- isolated task runtimes never trust shared PID metadata for persistence
- fast multi-sequence local MSA uses true batch search when eligible
- `db-load-mode 2` is the default client fast path for gpuserver-backed reuse

**Acceptance gate:** the docs match the code, and the benchmark notes demonstrate a clear improvement on the live workstation.

---

## Recommended PR slicing

Do not land this in one giant blob. The safest order is:

1. PR 1 — regression fence + strict liveness semantics + default `db_load_mode=2`
2. PR 2 — shared task-side runtime helper + truthful host-server reuse contract
3. PR 3 — `structure_prediction.nf` batch cutover
4. PR 4 — `boltz_cp_experimental.nf` batch cutover + remaining module default alignment
5. PR 5 — docs and benchmark notes

---

## Rollback strategy

If Phase 3 batch cutover causes trouble, rollback only the workflow wiring and keep Phases 0-2. The stale-PID fix, truthful host reuse, and `db_load_mode=2` default are independently valuable and should not be reverted just because batching needs iteration.

---

## Blunt summary

The real fix is not “tweak one timeout.”

It is:

1. stop trusting shared PIDs across isolated tasks
2. move persistent reuse truth to a host-owned contract
3. default gpuserver-backed searches to the fast client load mode the docs actually describe
4. stop doing one full local-MSA orchestration per protein chain when a batch path already exists in the repo

If those four things land cleanly, local MSA stops being a flaky monolith-shaped control-plane problem and becomes a boring, auditable runtime path again.
