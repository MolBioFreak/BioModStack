# BioModStack Local MSA Runtime Refactor Spec

> **For Hermes:** Planning/spec only. Do not implement from this document without explicit approval. Preserve the working `--msa-provider colabfold_api` behavior while fixing the local MMseqs runtime shape. This file is the detailed active spec for the local-MSA refactor; the earlier remediation and roadmap docs remain useful context, but this document is the detailed execution target.

**Goal:** Turn BioModStack local MSA into one canonical, package-backed runtime that does request normalization once, deduplicates sequences once, batches compatible local MMseqs work once, reuses host-supervised gpuserver state truthfully, and only materializes per-chain/per-caller artifacts after search. Keep current top-level CLIs and the remote ColabFold API path working while the internals are split up.

**Architecture:** Keep the provider split (`local` vs `colabfold_api`) and keep the user-facing CLI flags stable, but move the real implementation behind a package rooted at `scripts/lib/local_msa/`. The package becomes the sole owner of request normalization, runtime selection, batch planning, provider dispatch, cache/artifact handling, and reporting. Workflow and API surfaces become thin adapters: they describe work, but they do not own MMseqs orchestration policy.

**Tech Stack:** `scripts/run_local_msa.py`, `scripts/batch_msa.py`, `scripts/prepare_protenix_msa.py`, `scripts/check_protenix_msa_preflight.py`, `platform/api/services/msa_server.py`, `platform/api/services/nextflow.py`, `platform/api/routers/msa.py`, `modules/structure_prediction.nf`, `modules/boltz_cp_experimental.nf`, `modules/protenix.nf`, `modules/antibody_batch.nf`, `workflows/antibody_child.nf`, pytest, MMseqs2 subprocess orchestration, and the existing host `/api/msa/server/*` control plane.

---

## 1. Problem statement

The current local-MSA system is not one runtime. It is several overlapping controllers that each own part of the truth:

- `scripts/run_local_msa.py` is a giant single-sequence orchestration script.
- `scripts/batch_msa.py` implements a real batch path, but is marked deprecated and is not the canonical owner.
- `scripts/prepare_protenix_msa.py` partly acts as a caller adapter and partly re-owns MSA orchestration.
- `platform/api/services/nextflow.py` still launches script entrypoints directly.
- `modules/structure_prediction.nf` still shells out to `run_local_msa.py` per chain.
- `platform/api/services/msa_server.py` still has script-layout coupling and partial runtime ownership.

That is why the flow feels wrong: the repo already contains correct primitives, but they are composed incorrectly.

The right question is not “should Python be faster?” The right question is “who owns the MMseqs runtime decisions, and are those decisions made once or re-made in five places?”

Right now, they are re-made in five places.

---

## 2. Repo-grounded current truth

These are the facts this spec is built around:

1. `scripts/run_local_msa.py` is still structurally monolithic.
   - `3387` lines total.
   - `54` functions.
   - `17` functions exceed `50` lines.
   - `8` functions exceed `100` lines.
   - `run_colabfold_msa_workflow(...)` spans `987` lines.
   - `run_colabfold_api_msa_workflow(...)` spans `264` lines.

2. `run_local_msa.py` still mixes many responsibilities in one file.
   - cache helpers
   - lock handling
   - runtime inspection / GPU selection / scheduler policy
   - MMseqs subprocess handling
   - gpuserver persistence / reuse / lifecycle
   - remote ColabFold API provider
   - local MMseqs provider
   - CLI parsing / dispatch

3. `run_local_msa.py` still exposes the provider split clearly at the top level.
   - `main()` dispatches `--msa-provider colabfold_api` to `run_colabfold_api_msa_workflow(...)`.
   - `main()` dispatches `--msa-provider local` to `run_colabfold_msa_workflow(...)`.
   - This is good and must be preserved.

4. `scripts/batch_msa.py` already has the correct high-level batch shape for local fast mode.
   - write one query FASTA
   - `mmseqs createdb`
   - one `mmseqs search`
   - `mmseqs result2msa`
   - `mmseqs unpackdb`
   - map back to per-sequence A3M files

5. `scripts/batch_msa.py` is marked deprecated but is still live infrastructure.
   - `platform/api/services/nextflow.py` launches it directly.
   - `modules/structure_prediction.nf` references it directly.
   - `scripts/prepare_protenix_msa.py` imports `run_batch_msa(...)` from it.

6. `scripts/prepare_protenix_msa.py` is still coupled to script internals.
   - imports `run_batch_msa` from `batch_msa.py`
   - imports `inspect_mmseqs_runtime` and `parse_gpu_csv` from `run_local_msa.py`
   - forces `gpu-server-mode=off` in local Protenix mode to work around handshake/runtime issues

7. Workflow callers still shell out per chain in places where the repo already knows how to batch.
   - `modules/structure_prediction.nf` launches `run_local_msa.py` per protein chain.
   - `modules/boltz_cp_experimental.nf` expects `run_local_msa.py` materialization behavior.
   - This means repeated Python startup, repeated runtime selection, repeated gpuserver checks, repeated manifest/report work, and missed batching opportunities.

8. The host control plane is real, but task-side logic and host-side logic are not yet cleanly separated.
   - `platform/api/services/msa_server.py` manages the persistent server surface.
   - task code still contains gpuserver lifecycle logic and task-local runtime metadata handling.
   - the package boundary must preserve host ownership where it belongs.

9. The current dirty worktree is broad.
   - The implementation plan must stay scoped to local-MSA surfaces and not opportunistically touch unrelated frontend/runtime work.

---

## 3. What is already correct vs what is wrong

### Correct primitives already present

These are worth keeping:

1. A persistent gpuserver keyed by the actual runtime contract is a valid primitive.
2. A true batched MMseqs path is a valid primitive.
3. Exact-sequence deduplication before local MSA is a valid primitive.
4. Per-sequence A3M outputs are a valid primitive.
5. Per-chain materialization after the search is a valid primitive.
6. The provider split between `local` and `colabfold_api` is a valid primitive.

### Wrong composition / ownership today

These are the real problems:

1. There is no single canonical engine that owns request normalization.
2. There is no single canonical engine that owns batchability decisions.
3. There is no single canonical engine that owns runtime/gpuserver selection.
4. Callers still build giant arg lists and shell out directly instead of calling a stable package API.
5. Workflow code still thinks in terms of “generate one chain now” instead of “solve the sequence set, then materialize outputs.”
6. The deprecated batch path is still functionally required, which means the deprecation is lying.

Bluntly: the mechanisms are partly proper; the ownership model is not.

---

## 4. Desired end state

The correct runtime shape for BioModStack local MMseqs is:

1. Normalize the request once.
2. Dedupe identical sequences once.
3. Check cache once per unique sequence / compatible execution group.
4. Group uncached work into batchable local-MMseqs execution groups.
5. Resolve runtime/gpuserver once per execution group.
6. Run MMseqs once per compatible group.
7. Split results back to per-sequence artifacts.
8. Materialize per-chain / Protenix / workflow-specific outputs after MSA generation.
9. Emit one manifest/decision report that makes the runtime choice obvious.

A proper local runtime should behave like a controller/service with thin adapters, not like a pile of callers shelling out to `run_local_msa.py` independently.

---

## 5. Non-negotiables

1. Do not churn or redesign the working remote ColabFold API path during the local-runtime refactor.
2. Keep top-level CLIs working first:
   - `scripts/run_local_msa.py`
   - `scripts/batch_msa.py`
   - `scripts/prepare_protenix_msa.py`
3. Preserve current external output names for the first compatibility cycle where practical:
   - `<name>.a3m`
   - `<name>_msa_quality.json`
   - `msa_manifest.json`
   - Protenix `pairing.a3m` / `non_pairing.a3m`
4. Do not pull HMMER/jackhmmer into this work.
5. Do not make frontend work a prerequisite.
6. Do not begin with Rust.
7. Do not touch unrelated dirty files.

---

## 6. Explicit non-goals

Do not do these in the first execution tranche:

- rewrite the local runtime in Rust
- redesign the ColabFold DB layout
- redesign the remote ColabFold API protocol
- merge every script into one file “for cleanliness”
- introduce a second host daemon outside the existing `/api/msa/server/*` control plane
- redesign every structure-prediction workflow surface at once
- change user-facing preset names (`fast`, `balanced`, `maximum`)
- change top-level flag names unless a shim preserves them

---

## 7. Canonical ownership model

This is the core of the spec. Each concern gets exactly one canonical owner.

### 7.1 Request normalization

Canonical owner:
- `scripts/lib/local_msa/requests.py`
- or `types.py` plus `cli/args.py` if kept smaller

This layer owns:
- default filling
- override normalization
- validation of `provider`, `preset`, GPU policy, gpuserver policy
- converting CLI / workflow / API inputs into one internal request model

This layer must not own:
- MMseqs execution
- caller-specific output shaping

### 7.2 Runtime selection

Canonical owner:
- `scripts/lib/local_msa/runtime.py`
- `scripts/lib/local_msa/gpu_policy.py`
- `scripts/lib/local_msa/gpuserver.py`

This layer owns:
- GPU vs CPU decision
- chosen GPU ID
- isolated-task detection
- host gpuserver status query
- transient vs host reuse decision
- effective gpuserver contract normalization

This layer must not own:
- Nextflow concerns
- Protenix-specific behavior
- remote ColabFold API behavior

### 7.3 Batching decisions

Canonical owner:
- `scripts/lib/local_msa/batching.py`

This layer owns:
- dedupe of identical sequences
- grouping of requests by compatible execution key
- deciding whether a group is `batch-fast`, `single-local`, or `remote-provider`

This layer must not own:
- sequence-to-chain caller mapping rules specific to Protenix or Boltz-CP

### 7.4 Provider execution

Canonical owners:
- `scripts/lib/local_msa/providers/local_mmseqs.py`
- `scripts/lib/local_msa/providers/colabfold_api.py`

The provider modules own the actual execution details after planning is done.

### 7.5 Artifact/report materialization

Canonical owners:
- `scripts/lib/local_msa/cache.py`
- `scripts/lib/local_msa/reporting.py`
- `scripts/lib/local_msa/adapters/*.py`

The core package writes generic per-sequence results and manifests.
Caller adapters own caller-specific shaping.

### 7.6 Host gpuserver control plane

Canonical owner:
- `platform/api/services/msa_server.py`

The package may query the host control plane.
The package must not silently grow a second competing control plane.

---

## 8. Target package layout

Use the existing `scripts/lib/` root and create a real package with narrow modules.

```text
scripts/
  run_local_msa.py
  batch_msa.py
  prepare_protenix_msa.py
  check_protenix_msa_preflight.py
  local_msa_runtime.py                  # compatibility shim during transition
  lib/
    local_msa/
      __init__.py
      types.py
      requests.py
      config.py
      cache.py
      runtime.py
      gpu_policy.py
      gpuserver.py
      mmseqs_exec.py
      reporting.py
      batching.py
      providers/
        __init__.py
        local_mmseqs.py
        colabfold_api.py
      adapters/
        __init__.py
        protenix.py
        materialize_complex.py
      cli/
        __init__.py
        args.py
        run_single.py
        run_batch.py
```

### Module responsibilities

- `types.py`
  - dataclasses / typed dicts / literals for request and result contracts

- `requests.py`
  - normalize raw input into package request types
  - define grouping keys for batch planning

- `config.py`
  - default path/env resolution
  - provider defaults and global constants

- `cache.py`
  - sequence hash helpers
  - cache lookup/load/save
  - preserve current cache layout initially

- `runtime.py`
  - high-level runtime inspection / decision assembly

- `gpu_policy.py`
  - GPU selection helpers
  - preferred/excluded parsing and policy resolution

- `gpuserver.py`
  - gpuserver contract normalization
  - host-status querying
  - transient launch/reuse helpers if still required task-side

- `mmseqs_exec.py`
  - safe subprocess wrappers
  - MMseqs command assembly and logging helpers

- `reporting.py`
  - quality JSON payloads
  - manifest entries
  - runtime decision telemetry output

- `batching.py`
  - dedupe unique sequences
  - plan eligible groups
  - run canonical fast-batch path
  - map back to per-sequence outputs

- `providers/local_mmseqs.py`
  - single local workflow
  - fast-batch execution helpers
  - balanced/maximum fallback path until a better grouped local flow exists

- `providers/colabfold_api.py`
  - remote provider moved with behavior as close to byte-for-byte as practical

- `adapters/protenix.py`
  - Protenix-specific input shaping
  - binder-chain row cap logic
  - paired/unpaired A3M materialization

- `adapters/materialize_complex.py`
  - shared chain/complex materialization logic for structure workflows if useful

- `cli/args.py`
  - one source of truth for CLI flags and defaults

- `cli/run_single.py`
  - package entrypoint for one logical request

- `cli/run_batch.py`
  - package entrypoint for many logical requests

---

## 9. Internal interface spec

The detailed code can vary, but the package must converge on stable internal models equivalent to the following.

### 9.1 RuntimeOptions

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

GpuMode = Literal["auto", "opportunistic", "required", "cpu"]
GpuServerMode = Literal["auto", "required", "persistent", "off"]
MsaProvider = Literal["local", "colabfold_api"]
MsaPreset = Literal["fast", "balanced", "maximum"]

@dataclass(frozen=True)
class RuntimeOptions:
    db_path: Path
    cache_dir: Optional[Path]
    cpu_only: bool
    gpu_mode: GpuMode
    gpu_threshold: int
    preferred_gpus: tuple[int, ...]
    excluded_gpus: tuple[int, ...]
    gpu_server_mode: GpuServerMode
    gpu_server_wait_timeout: int
    gpu_server_db_load_mode: int
    gpu_server_startup_wait: float
    disallow_cpu_fallback: bool
```

### 9.2 SequenceRequest

```python
@dataclass(frozen=True)
class SequenceRequest:
    name: str
    sequence: str
    reference_sequence: str | None
```

### 9.3 RequestOverrides

```python
@dataclass(frozen=True)
class RequestOverrides:
    num_iterations: int | None
    use_env: bool | None
    use_expand: bool | None
    use_filter: bool | None
    evalue: float | None
    sensitivity: float | None
    max_seqs: int | None
    min_seq_id: float | None
    min_coverage: float | None
    taxon_list: str | None
    min_depth_warning: int | None
    min_depth_fail: int | None
    fast_env_fallback_min_depth: int | None
```

### 9.4 MSARequest

```python
@dataclass(frozen=True)
class MSARequest:
    provider: MsaProvider
    preset: MsaPreset
    sequence: SequenceRequest
    runtime: RuntimeOptions
    overrides: RequestOverrides
    out_dir: Path
```

### 9.5 BatchRequest

```python
@dataclass(frozen=True)
class BatchRequest:
    provider: MsaProvider
    preset: MsaPreset
    sequences: tuple[SequenceRequest, ...]
    runtime: RuntimeOptions
    overrides: RequestOverrides
    out_dir: Path
```

### 9.6 GPUServerContract

```python
@dataclass(frozen=True)
class GPUServerContract:
    target_db: Path
    db_alias: str
    gpu_id: int | None
    cuda_visible_devices: str | None
    max_seqs: int
    prefilter_mode: int
    db_load_mode: int
```

### 9.7 RuntimeDecision

```python
@dataclass(frozen=True)
class RuntimeDecision:
    use_gpu_mmseqs: bool
    selected_gpu_id: int | None
    mmseqs_bin: Path
    normalized_gpu_mode: str
    gpuserver_mode_requested: str
    gpuserver_mode_effective: str
    gpuserver_source: str  # host | transient | off
    gpuserver_contract: GPUServerContract | None
    host_server_checked: bool
    host_server_ready: bool
    isolated_task_context: bool
    failure_reason: str | None
    summary_message: str
```

### 9.8 BatchPlan

```python
@dataclass(frozen=True)
class BatchPlan:
    strategy: str  # batch-fast | per-sequence-local | remote-provider
    group_key: str
    sequence_names: tuple[str, ...]
    deduped_sequence_count: int
    cache_hit_count: int
    cache_miss_count: int
```

### 9.9 SequenceMSAResult

```python
@dataclass(frozen=True)
class SequenceMSAResult:
    name: str
    sequence_hash: str
    msa_path: Path | None
    cache_hit: bool
    success: bool
    depth: int | None
    error: str | None
    runtime_decision: RuntimeDecision | None
    batch_strategy: str
```

### 9.10 Manifest contract

The batch/single manifest and per-sequence quality JSON must expose enough telemetry to debug the runtime truthfully.

Minimum required fields:

- `msa_provider`
- `preset`
- `selected_gpu_id`
- `gpuserver_mode_requested`
- `gpuserver_mode_effective`
- `gpuserver_source`
- `gpuserver_db_load_mode`
- `host_server_checked`
- `host_server_ready`
- `batch_strategy`
- `fallback_reason`
- `from_cache`

---

## 10. Execution flow spec

### 10.1 Single-sequence local request

1. Parse CLI into `MSARequest`.
2. Normalize runtime/defaults once.
3. Check cache.
4. If cache hit, emit result and quality JSON.
5. If cache miss, resolve runtime once.
6. Resolve gpuserver plan once.
7. Execute local provider once.
8. Save generic per-sequence result.
9. Emit quality JSON with runtime decision block.

### 10.2 Multi-sequence local request

1. Parse into `BatchRequest`.
2. Normalize once.
3. Deduplicate by effective cache-key sequence.
4. Check cache for unique sequences.
5. Group misses by compatible execution key.
6. For each group:
   - resolve runtime once
   - decide batch strategy once
   - execute once per compatible group
7. Map sequence results back to requested names.
8. Materialize manifest.

### 10.3 Protenix local request

1. Parse and summarize payload.
2. Collect protein chains.
3. Deduplicate exact sequences across the payload.
4. Reuse existing `pairedMsaPath` / `unpairedMsaPath` where already present.
5. For remaining sequences, call the canonical package batch/single surface.
6. After generic sequence A3Ms exist, create Protenix `pairing.a3m` / `non_pairing.a3m` outputs.
7. Apply binder-specific pruning only in the adapter layer.

### 10.4 Remote `colabfold_api` request

1. Parse into the same request model.
2. Dispatch straight to `providers/colabfold_api.py`.
3. Preserve current request flow and result shape.
4. Do not pull local-only gpuserver logic into this path.

---

## 11. Batching eligibility rules

The batch planner must make eligibility explicit and deterministic.

### 11.1 Initial batchable group definition

A request group is batchable in the first local-MMseqs tranche only if all of the following are true:

1. `provider == "local"`
2. `preset == "fast"`
3. the execution group shares the same:
   - `db_path`
   - effective runtime policy
   - gpuserver contract knobs relevant to the search
   - cache-key/reference-sequence policy
4. none of these advanced overrides are set:
   - `use_expand`
   - `use_env`
   - `num_iterations`
   - `evalue`
   - `min_seq_id`
   - `min_coverage`
   - `taxon_list`
   - `min_depth_warning`
   - `min_depth_fail`
5. the caller has not explicitly requested remote provider behavior

That matches the current repo reality: fast local mode is the clean first batch target.

### 11.2 Initial non-batchable cases

Treat these as non-batchable initially:

- `balanced` or `maximum`
- any advanced local override that changes the canonical fast pipeline
- remote `colabfold_api`
- mixed requests with incompatible cache-key/reference behavior

These cases may still be grouped later, but the first refactor should not pretend that they are already simple.

### 11.3 Important distinction

Not batchable does not mean “let every caller shell out whenever it wants.”
It means the canonical engine may still execute per-sequence under one controller, with one manifest and one runtime-planning surface.

---

## 12. Cache and artifact policy

### 12.1 Initial rule

Preserve the current on-disk cache shape during the first refactor unless a correctness bug forces change.

Reason:
- the main current problems are orchestration and ownership
- changing cache semantics and runtime ownership at the same time makes regressions harder to isolate

### 12.2 Cache responsibility

The package owns:
- `compute_sequence_hash(...)`
- cache lookup
- cache load/save
- legacy cache migration if needed

Caller adapters must not re-implement cache behavior.

### 12.3 Artifact policy

The core runtime produces generic per-sequence artifacts first.
Caller adapters then derive caller-specific output files.

Good:
- sequence A3M first, chain materialization second

Bad:
- chain-driven orchestration where each chain independently decides its own runtime path

---

## 13. gpuserver policy spec

### 13.1 Ownership

The host API service remains the canonical owner of persistent, operator-visible gpuserver state.
Task-side helpers are consumers of that truth, not competing authorities.

### 13.2 Mode semantics

Preserve the existing flag names but make them truthful:

- `off`
  - never use gpuserver

- `auto`
  - reuse a confirmed host server when one exists for the selected contract
  - otherwise use a local transient gpuserver only if local GPU search still makes sense
  - otherwise fall back under the normal GPU/CPU rules

- `required`
  - require gpuserver-backed search
  - prefer host reuse
  - allow transient gpuserver only if that is part of the request semantics and succeeds
  - fail clearly if neither host nor transient gpuserver works

- `persistent`
  - outside isolated task contexts, reuse/start long-lived persistent server behavior as allowed by the host control plane
  - inside isolated task contexts, never treat task-local PID metadata as trustworthy cross-task persistence
  - degrade to host reuse or transient behavior with loud telemetry if persistence is not valid in-context

### 13.3 Required host status contract fields

The package runtime needs a stable minimum status contract from `/api/msa/server/status` or equivalent helper.
At minimum the host payload must expose enough information to evaluate:

- target DB path / alias
- GPU ID / `cuda_visible_devices`
- `max_seqs`
- `prefilter_mode`
- `db_load_mode`
- ready/running status
- pid if operator-facing diagnostics need it

The runtime package should match on contract fields, not fuzzy “something is running” logic.

### 13.4 Runtime decision telemetry

Every local run must say whether it used:
- host reuse
- transient gpuserver
- no gpuserver
- CPU fallback

If a mode was downgraded, say why.

---

## 14. CLI compatibility spec

### 14.1 `scripts/run_local_msa.py`

Target state:
- parse CLI arguments using `local_msa.cli.args`
- construct an `MSARequest`
- dispatch to package single-run entrypoint
- keep current flags and output names

This file should become mostly compatibility glue, not implementation.

### 14.2 `scripts/batch_msa.py`

Target state:
- parse CLI arguments using the same canonical arg definitions where possible
- construct a `BatchRequest`
- dispatch to package batch entrypoint
- keep current manifest/output contract
- stop pretending it is deprecated until the shim replacement is truly in place

### 14.3 `scripts/prepare_protenix_msa.py`

Target state:
- import from `local_msa.adapters.protenix`
- stop importing behavior directly from script entrypoints
- keep current CLI surface

### 14.4 `scripts/check_protenix_msa_preflight.py`

Target state:
- import package runtime inspection helpers instead of `run_local_msa.py`

---

## 15. Caller integration spec

### 15.1 `platform/api/services/nextflow.py`

Current problem:
- directly launches `batch_msa.py`
- knows too much about script-level argument building

Target state:
- may continue launching `scripts/batch_msa.py` during the compatibility cycle, but that script must be a thin shim over the package
- should not need to know internal batch-vs-single logic beyond request parameters

### 15.2 `modules/structure_prediction.nf`

Current problem:
- shells out to `run_local_msa.py` per chain

Target state:
- for eligible local-fast multi-sequence jobs, resolve all unique sequences up front and use one batch-capable path
- materialize chain outputs after results are available
- keep per-sequence fallback for ineligible cases only

### 15.3 `modules/boltz_cp_experimental.nf`

Current problem:
- still expects `run_local_msa.py`-driven materialization

Target state:
- same as structure prediction: use a batch-capable path when eligible, then materialize per-sequence/per-chain outputs afterwards

### 15.4 `modules/protenix.nf` and `modules/antibody_batch.nf`

Target state:
- keep calling `prepare_protenix_msa.py` if that is still the stable external surface
- but `prepare_protenix_msa.py` must become a thin adapter over the package

---

## 16. File-by-file change map

### 16.1 Create

- `scripts/lib/local_msa/__init__.py`
- `scripts/lib/local_msa/types.py`
- `scripts/lib/local_msa/requests.py`
- `scripts/lib/local_msa/config.py`
- `scripts/lib/local_msa/cache.py`
- `scripts/lib/local_msa/runtime.py`
- `scripts/lib/local_msa/gpu_policy.py`
- `scripts/lib/local_msa/gpuserver.py`
- `scripts/lib/local_msa/mmseqs_exec.py`
- `scripts/lib/local_msa/reporting.py`
- `scripts/lib/local_msa/batching.py`
- `scripts/lib/local_msa/providers/__init__.py`
- `scripts/lib/local_msa/providers/local_mmseqs.py`
- `scripts/lib/local_msa/providers/colabfold_api.py`
- `scripts/lib/local_msa/adapters/__init__.py`
- `scripts/lib/local_msa/adapters/protenix.py`
- `scripts/lib/local_msa/adapters/materialize_complex.py` if structure/CP sharing proves worthwhile
- `scripts/lib/local_msa/cli/__init__.py`
- `scripts/lib/local_msa/cli/args.py`
- `scripts/lib/local_msa/cli/run_single.py`
- `scripts/lib/local_msa/cli/run_batch.py`
- `scripts/test_batch_msa.py` if still missing from repo state
- `scripts/test_local_msa_package.py`
- `scripts/test_colabfold_api_provider.py`
- `scripts/bench_local_msa.py`

### 16.2 Modify

- `scripts/run_local_msa.py`
- `scripts/batch_msa.py`
- `scripts/prepare_protenix_msa.py`
- `scripts/check_protenix_msa_preflight.py`
- `scripts/local_msa_runtime.py`
- `scripts/test_run_local_msa.py`
- `scripts/test_prepare_protenix_msa.py`
- `platform/api/services/msa_server.py`
- `platform/api/routers/msa.py` if request/response defaults must be aligned
- `platform/api/services/nextflow.py`
- `modules/structure_prediction.nf`
- `modules/boltz_cp_experimental.nf`
- `modules/protenix.nf`
- `modules/antibody_batch.nf`
- `workflows/antibody_child.nf`
- `platform/api/tests/test_msa_server.py`
- `platform/api/tests/test_structure_prediction_batch.py`
- `platform/api/tests/test_boltz_cp_experimental.py`
- canonical runtime docs after the rollout stabilizes

### 16.3 Compatibility shims to keep temporarily

- `scripts/run_local_msa.py`
- `scripts/batch_msa.py`
- `scripts/local_msa_runtime.py`

These are allowed to stay as wrappers for one compatibility cycle.

---

## 17. Exact function migration targets

### 17.1 From `scripts/run_local_msa.py`

Move or wrap these into package modules:

- cache helpers -> `cache.py`
- GPU parsing / scheduler helpers -> `gpu_policy.py`
- `inspect_mmseqs_runtime(...)` -> `runtime.py`
- `run_mmseqs(...)` -> `mmseqs_exec.py`
- gpuserver metadata / matching / launch helpers -> `gpuserver.py`
- `run_colabfold_api_msa_workflow(...)` -> `providers/colabfold_api.py`
- `run_colabfold_msa_workflow(...)` -> `providers/local_mmseqs.py`
- CLI parser construction -> `cli/args.py`

### 17.2 From `scripts/batch_msa.py`

Move or wrap these into package modules:

- `run_batch_msa(...)` -> `batching.py`
- fast true-batch MMseqs execution -> `batching.py` plus `providers/local_mmseqs.py`
- per-sequence fallback for higher-quality local behavior -> `providers/local_mmseqs.py`

### 17.3 From `scripts/prepare_protenix_msa.py`

Move or wrap these into package modules:

- local payload collection / dedupe -> `adapters/protenix.py`
- binder pruning / A3M sanitation -> `adapters/protenix.py`
- direct imports from script entrypoints -> replace with package imports

---

## 18. PR / phase plan

## Phase 0 — freeze behavior and add missing tests

**Objective:** Make later refactors safe.

Deliverables:
- package-level regression tests exist
- remote provider freeze tests exist
- batch fast vs per-sequence fallback behavior is pinned in tests

Acceptance gate:
- current targeted tests pass
- new failing tests cover the exact new package boundaries

Validation:
- `pytest scripts/test_run_local_msa.py scripts/test_prepare_protenix_msa.py platform/api/tests/test_msa_server.py -q`
- `pytest scripts/test_batch_msa.py scripts/test_local_msa_package.py scripts/test_colabfold_api_provider.py -q`

## Phase 1 — create package skeleton and extract shared policy/helpers

**Objective:** Break script-to-script imports and centralize request/runtime logic.

Deliverables:
- package skeleton exists
- `run_local_msa.py` no longer owns helper logic directly
- `prepare_protenix_msa.py` and preflight stop importing from script internals
- `platform/api/services/msa_server.py` has a stable import path that does not depend on ad hoc script internals

Acceptance gate:
- no non-test file imports live behavior from `run_local_msa.py`
- `scripts/local_msa_runtime.py` is only a compatibility shim or is retired from direct ownership

## Phase 2 — split providers cleanly

**Objective:** Freeze the remote provider and isolate local work.

Deliverables:
- `providers/colabfold_api.py`
- `providers/local_mmseqs.py`
- CLI dispatch is just provider routing

Acceptance gate:
- remote provider behavior is unchanged under tests
- local refactors can proceed without editing the remote provider module

## Phase 3 — make batching canonical

**Objective:** Replace “deprecated but required” with one true canonical batch engine.

Deliverables:
- `batching.py` becomes the owner of fast local batching
- `scripts/batch_msa.py` becomes a thin shim
- eligible callers use one group execution instead of repeated per-sequence shellouts

Acceptance gate:
- local-fast multi-sequence work no longer shells out N times when a single batch path is possible
- `batch_msa.py` no longer re-owns policy independently

## Phase 4 — move caller-specific logic into adapters

**Objective:** Make the core runtime generic and keep caller-specific shaping narrow.

Deliverables:
- Protenix adapter module
- optional shared complex-materialization adapter
- structure/CP callers thin out

Acceptance gate:
- core runtime has no Protenix-specific behavior
- workflow surfaces are thinner and easier to reason about

## Phase 5 — telemetry, docs, compatibility cleanup

**Objective:** Make runtime truth obvious and remove dead internal paths after stability is proven.

Deliverables:
- manifests / quality JSON include runtime decision blocks
- docs updated
- old redundant helpers deleted or shimmed only

Acceptance gate:
- package is canonical
- scripts are wrappers, not implementation blobs

---

## 19. Recommended first implementation tranche

The safest first implementation slice is:

1. Phase 0
2. Phase 1
3. Phase 2

Do not start with workflow cutover.
Do not start with structure_prediction batching rewrites.
Do not start with deleting `batch_msa.py`.

Why:
- package extraction and provider freeze create safe internal seams
- once the seams exist, the batching and workflow cutover become much less risky
- this keeps the currently working remote path insulated

---

## 20. Validation matrix

### 20.1 Unit / targeted tests

Run at minimum:

- `pytest scripts/test_run_local_msa.py -q`
- `pytest scripts/test_prepare_protenix_msa.py -q`
- `pytest scripts/test_batch_msa.py -q`
- `pytest scripts/test_local_msa_package.py -q`
- `pytest scripts/test_colabfold_api_provider.py -q`
- `pytest platform/api/tests/test_msa_server.py -q`

### 20.2 Workflow / integration tests

Run at minimum:

- `pytest platform/api/tests/test_structure_prediction_batch.py -q`
- `pytest platform/api/tests/test_boltz_cp_experimental.py -q`

### 20.3 Syntax / import validation

Run at minimum:

- `python -m py_compile scripts/run_local_msa.py scripts/batch_msa.py scripts/prepare_protenix_msa.py scripts/check_protenix_msa_preflight.py platform/api/services/msa_server.py`
- `python -m py_compile scripts/lib/local_msa/*.py scripts/lib/local_msa/providers/*.py scripts/lib/local_msa/adapters/*.py scripts/lib/local_msa/cli/*.py`

### 20.4 Benchmark harness

Add and run a small benchmark harness such as:

- single-sequence local fast
- 4-sequence local fast batch
- representative Protenix local payload with repeated sequences
- optional structure materialization batch case

The benchmark goal is not scientific publication. It is proving the orchestration shape got better.

---

## 21. Acceptance criteria / definition of done

This refactor is done only when all of the following are true:

1. There is exactly one canonical package-backed local-MSA engine.
2. `run_local_msa.py`, `batch_msa.py`, and `prepare_protenix_msa.py` are thin adapters or compatibility shims.
3. Local-fast multi-sequence requests use the canonical batch engine when eligible.
4. Per-chain shellouts are eliminated for eligible structure workflows.
5. Caller-specific shaping lives in adapters, not the core provider.
6. Remote `colabfold_api` behavior remains unchanged.
7. Runtime decision telemetry makes it obvious how a result was produced.
8. The host gpuserver control plane remains the persistent-runtime owner.

---

## 22. Risks and tradeoffs

### 22.1 Risk: touching too many callers at once

Mitigation:
- do package extraction and provider freeze first
- delay workflow cutover until after that seam exists

### 22.2 Risk: cache behavior regressions

Mitigation:
- preserve current cache layout initially
- add explicit cache-hit/cache-miss regression tests

### 22.3 Risk: workflow code still expects current file names

Mitigation:
- preserve top-level CLI/output contracts for one compatibility cycle

### 22.4 Risk: remote path accidentally changes during cleanup

Mitigation:
- isolate remote provider into its own module early
- add remote-provider freeze tests first

### 22.5 Tradeoff: balanced/maximum remain less optimal initially

This is acceptable.
The first goal is to fix the runtime shape and the batch-first fast path.
Do not overpromise grouped balanced/maximum execution in the first tranche.

---

## 23. Open questions that should not block phase 1

1. Should `batching.py` keep the name `batching.py`, or should it be `batch.py` for brevity?
   - Recommendation: `batching.py` to avoid confusion with `scripts/batch_msa.py`.

2. Should the package import path be exposed through `scripts/local_msa_runtime.py` for a full cycle?
   - Recommendation: yes, keep a compatibility shim until all live callers are migrated.

3. Should grouped balanced/maximum local workflows exist later?
   - Recommendation: maybe, but not in the opening tranche.

4. Should the API layer eventually call the package directly instead of shelling out to script shims?
   - Recommendation: maybe later; keep the command surface stable first.

---

## 24. Blunt conclusion

The correct fix is not “rewrite local MSA in another language.”
The correct fix is:

- one canonical local-MSA engine
- one canonical batch planner
- one truthful runtime/gpuserver decision layer
- thin workflow/API/script adapters
- per-sequence search first, per-chain materialization second

The repo already contains the right primitives.
This spec makes them the actual architecture instead of accidental side paths.
