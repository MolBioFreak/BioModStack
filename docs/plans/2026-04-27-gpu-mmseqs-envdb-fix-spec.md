# GPU MMseqs EnvDB Fix Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make BioModStack local high-quality MSA acceleration truthful and, when requested, make the expensive EnvDB search stage actually run through GPU-capable MMseqs instead of silently taking the CPU native-split path.

**Architecture:** First add stage-level command/runtime evidence so the system cannot confuse request-level GPU selection with real stage-level GPU use. Then change the EnvDB native target-split branch to choose a GPU-capable strategy when GPU is requested, with strict failure for `gpu_mode=required` and explicit CPU fallback metadata for opportunistic/auto modes. Keep the 32-thread global budget and keep high-quality MMseqs semantics intact: one native `mmseqs search --split N --split-mode 0`, not manual splitdb/per-shard iterative searches.

**Tech Stack:** Python runtime in `scripts/run_local_msa.py`, local package helpers under `scripts/lib/local_msa/`, pytest regression tests, Nextflow command surfaces, MMseqs2 GPU/CPU binaries under `/mnt/BioModStack/colabfold_db`, NVIDIA telemetry for validation.

---

## Current verified defect

The corrected maximum-quality local MSA path is functional, but the current acceleration claims are wrong.

Verified code facts:

- `scripts/run_local_msa.py:3196` explicitly chooses CPU MMseqs for native EnvDB target splitting:
  `shard_mmseqs_bin = mmseqs_cpu if Path(str(mmseqs_cpu)).exists() else mmseqs_bin`

- `scripts/run_local_msa.py:3197-3208` then calls `run_native_target_split_search(...)` with only:
  `--db-load-mode 2`, controlled `--split`, `--split-mode 0`, and `--threads 32`.

- `scripts/lib/local_msa/sharding.py` currently appends split/thread controls but does not add GPU controls itself.

- Because `target_sharded_env_search = True`, the later EnvDB GPU/gpuserver branch at `scripts/run_local_msa.py:3220-3274` is skipped.

- `scripts/run_local_msa.py:3474` writes `used_gpu_mmseqs: use_gpu_flag`, which is currently a request/runtime-selection hint, not proof the expensive EnvDB stage used GPU.

- The GPU MMseqs binary supports the required command options according to local help:
  `--gpu`, `--gpu-server`, `--gpu-server-wait-timeout`, `--prefilter-mode`, `--db-load-mode`, `--split`, and `--split-mode`.

- The local DB has GPU-readiness markers for both core target DBs:
  `uniref30_2302_db.GPU_READY`, `uniref30_2302_db_h`, `uniref30_2302_db_seq_h`, `colabfold_envdb_202108_db.GPU_READY`, `colabfold_envdb_202108_db_h`, `colabfold_envdb_202108_db_seq_h`.

Impact:

- A max-quality run can spend almost all wall time in CPU EnvDB/native-split work while the quality JSON still says `used_gpu_mmseqs=true`.
- A few seconds of GPU power draw during UniRef/gpuserver startup can be misread as proof that the 30-minute max-quality job was GPU accelerated.
- The current UX/logging overclaims acceleration and makes performance debugging misleading.

---

## Non-negotiable constraints

1. Keep `msa_threads=32` as the global thread budget unless Christian explicitly changes that.
2. Do not reintroduce manual `splitdb` + independent per-shard iterative `search` + `mergedbs` for high-quality EnvDB. That is not guaranteed equivalent because it can break global iteration/profile barriers.
3. Preserve `balanced`/`maximum` semantics: EnvDB-backed high-quality runs must not silently degrade to UniRef-only or broken expansion.
4. Do not call local MSA API-equivalent unless depth/overlap evidence proves it mode-by-mode.
5. Do not call a run GPU-accelerated unless the actual expensive stage command path proves GPU use.
6. Do not rely on one `used_gpu_mmseqs` boolean anymore. Acceleration evidence must be per-stage.
7. Keep unrelated dirty worktree files untouched.

---

## Definition of done

A fix is complete only when all of these are true:

1. Stage-level reporting exists in `<job>_msa_quality.json`.
   Required fields per MMseqs stage:
   - `stage`: e.g. `uniref_search`, `uniref_expandaln`, `envdb_search`, `envdb_expandaln`, `envdb_align`, `filterresult`, `result2msa`
   - `module`: MMseqs subcommand
   - `binary`: exact binary path
   - `binary_kind`: `gpu`, `cpu`, or `unknown`
   - `target_db`: when applicable
   - `uses_gpu_flag`: true when argv includes `--gpu 1`
   - `uses_gpu_server`: true when argv includes `--gpu-server 1`
   - `prefilter_mode`
   - `db_load_mode`
   - `split_count`
   - `split_mode`
   - `threads`
   - `elapsed_seconds`
   - `returncode`
   - `fallback_from_gpu`: true/false
   - `fallback_reason`: string/null

2. Top-level quality JSON separates biological quality from acceleration truth:
   - Keep `degraded_quality` for MSA quality/substrate problems.
   - Add `acceleration` or `envdb_acceleration` for GPU/CPU execution truth.
   - Deprecate `used_gpu_mmseqs` as an aggregate truth claim. If retained for compatibility, document it as request-level only and add a new field such as `effective_gpu_stages`.

3. In a GPU-requested high-quality run with target splitting enabled, the EnvDB search stage must either:
   - run through GPU MMseqs with `--gpu 1 --prefilter-mode 1 --split N --split-mode 0 --threads 32`, or
   - explicitly fall back to CPU with `envdb_acceleration.backend = cpu_native_split` and a concrete fallback reason, or
   - fail if `--gpu-mode required` or gpuserver mode `required` makes fallback invalid.

4. A `--gpu-mode required --target-shard-mode required --preset maximum` run must not silently execute CPU EnvDB search.

5. Logs must stop saying misleading things like “before EnvDB GPU search” when the next command is CPU native split.

6. A real RepA/P03066 validation run must include command-level proof and telemetry proof:
   - EnvDB search argv contains GPU flags, or the run explicitly says CPU fallback.
   - Monitor captures GPU util/power during the EnvDB stage, not only during a short UniRef startup blip.
   - Final A3M depth and parser compatibility are checked against the current CPU-native-split control.

---

## Phase 0: Truthfulness and observability before behavior changes

### Task 0.1: Add a stage-report helper module

**Objective:** Create a small reusable helper that classifies MMseqs commands and serializes stage metadata.

**Files:**
- Create: `scripts/lib/local_msa/mmseqs_stage_report.py`
- Create: `scripts/test_local_msa_stage_report.py`

**Test first:**

Add tests for command parsing. Minimum cases:

- EnvDB GPU native split search:
  `search profile colabfold_envdb_202108_db res tmp --gpu 1 --prefilter-mode 1 --db-load-mode 2 --split 4 --split-mode 0 --threads 32`
  should produce:
  - `module=search`
  - `target_db=colabfold_envdb_202108_db`
  - `uses_gpu_flag=true`
  - `uses_gpu_server=false`
  - `prefilter_mode=1`
  - `db_load_mode=2`
  - `split_count=4`
  - `split_mode=0`
  - `threads=32`

- EnvDB CPU native split search:
  same command without `--gpu`, with CPU binary path, should produce `uses_gpu_flag=false`, `binary_kind=cpu`.

- gpuserver search:
  command with `--gpu 1 --gpu-server 1 --gpu-server-wait-timeout 120` should produce `uses_gpu_server=true`.

- Non-search stages should still be represented but not overclaim GPU.

**Suggested implementation shape:**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Iterable


@dataclass
class MmseqsStageReport:
    stage: str
    module: str
    binary: str
    binary_kind: str
    target_db: str | None
    argv: list[str]
    uses_gpu_flag: bool
    uses_gpu_server: bool
    prefilter_mode: int | None
    db_load_mode: int | None
    split_count: int | None
    split_mode: int | None
    threads: int | None
    elapsed_seconds: float | None = None
    returncode: int | None = None
    fallback_from_gpu: bool = False
    fallback_reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def classify_mmseqs_binary(mmseqs_bin: str | Path) -> str:
    text = str(mmseqs_bin).lower()
    if "gpu" in text or "blackwell" in text:
        return "gpu"
    if "cpu" in text or "/mmseqs/bin/mmseqs" in text:
        return "cpu"
    return "unknown"


def _get_option(args: list[str], name: str) -> str | None:
    try:
        idx = args.index(name)
    except ValueError:
        return None
    if idx + 1 >= len(args):
        return None
    return args[idx + 1]


def command_report(stage: str, mmseqs_bin: str | Path, params: Iterable[Any]) -> MmseqsStageReport:
    argv = [str(p) for p in params]
    module = argv[0] if argv else "unknown"
    target_db = None
    if module == "search" and len(argv) >= 3:
        target_db = Path(argv[2]).name
    elif module in {"expandaln", "align", "filterresult", "result2msa"} and len(argv) >= 3:
        target_db = Path(argv[2]).name
    return MmseqsStageReport(
        stage=stage,
        module=module,
        binary=str(mmseqs_bin),
        binary_kind=classify_mmseqs_binary(mmseqs_bin),
        target_db=target_db,
        argv=argv,
        uses_gpu_flag=_get_option(argv, "--gpu") == "1",
        uses_gpu_server=_get_option(argv, "--gpu-server") == "1",
        prefilter_mode=int(_get_option(argv, "--prefilter-mode")) if _get_option(argv, "--prefilter-mode") else None,
        db_load_mode=int(_get_option(argv, "--db-load-mode")) if _get_option(argv, "--db-load-mode") else None,
        split_count=int(_get_option(argv, "--split")) if _get_option(argv, "--split") else None,
        split_mode=int(_get_option(argv, "--split-mode")) if _get_option(argv, "--split-mode") else None,
        threads=int(_get_option(argv, "--threads")) if _get_option(argv, "--threads") else None,
    )
```

**Run:**
`PYTHONPATH=scripts:scripts/lib:. pytest scripts/test_local_msa_stage_report.py -q`

Expected: new tests fail before implementation, pass after.

---

### Task 0.2: Wire stage reports into `run_colabfold_msa_workflow`

**Objective:** Every MMseqs call made by `run_colabfold_msa_workflow` must have a stage record in the final quality report.

**Files:**
- Modify: `scripts/run_local_msa.py`
- Modify: `scripts/test_run_local_msa.py`

**Test first:**

Add a unit test that runs the existing fake-MMseqs path through `preset="maximum"`, `target_shard_mode="off"`, then stops at `unpackdb` as the current tests do. Assert the generated/final quality report or captured stage list contains records for:

- `createdb`
- `uniref_search`
- `uniref_expandaln` when expand is enabled
- `envdb_search` when EnvDB is enabled
- downstream align/filter/result2msa stages

For error-path tests, assert the exception still preserves any available stage records in a sidecar such as `<job>_msa_stage_report.json` if final quality JSON is not reached.

**Implementation notes:**

- Do not change `run_mmseqs` globally first. Add a local wrapper inside `run_colabfold_msa_workflow`:

```python
mmseqs_stage_reports = []

def run_stage(stage: str, mmseqs_bin_arg, params, env_arg):
    report = command_report(stage, mmseqs_bin_arg, params)
    started = monotonic()
    try:
        result = run_mmseqs(mmseqs_bin_arg, params, env_arg)
        report.returncode = getattr(result, "returncode", 0)
        return result
    except Exception as exc:
        report.returncode = 1
        report.fallback_reason = str(exc)
        raise
    finally:
        report.elapsed_seconds = monotonic() - started
        mmseqs_stage_reports.append(report.to_json())
```

- Replace direct `run_mmseqs(...)` calls in the workflow with `run_stage("stage_name", ...)` gradually.
- For helpers that accept `run_mmseqs`, pass a lambda that records `envdb_search`.
- Write `mmseqs_stage_reports` into quality JSON and into a sidecar even on fatal errors.

**Run:**
`PYTHONPATH=scripts:scripts/lib:. pytest scripts/test_run_local_msa.py::test_quality_report_records_mmseqs_stage_reports -q`

Expected after implementation: test passes and quality JSON has stage-level evidence.

---

### Task 0.3: Fix misleading aggregate fields and logs

**Objective:** Stop equating request-level GPU selection with effective GPU acceleration.

**Files:**
- Modify: `scripts/run_local_msa.py`
- Modify: `scripts/test_run_local_msa.py`

**Test first:**

Add tests asserting:

- A CPU EnvDB native-split run writes `envdb_acceleration.backend = "cpu_native_split"` even when `use_gpu_flag` was initially true.
- `used_gpu_mmseqs` is either removed or documented as request-level by adding:
  - `gpu_mmseqs_requested`
  - `effective_gpu_stages`
  - `envdb_acceleration`

Suggested report shape:

```json
{
  "gpu_mmseqs_requested": true,
  "used_gpu_mmseqs": true,
  "effective_gpu_stages": ["uniref_search"],
  "envdb_acceleration": {
    "backend": "cpu_native_split",
    "requested_gpu": true,
    "effective_gpu": false,
    "target_split": true,
    "fallback_reason": "native EnvDB split currently used CPU path"
  }
}
```

After GPU fix, desired shape for EnvDB GPU native split:

```json
{
  "envdb_acceleration": {
    "backend": "gpu_native_split",
    "requested_gpu": true,
    "effective_gpu": true,
    "target_split": true,
    "uses_gpu_server": false,
    "split_count": 4,
    "threads": 32
  }
}
```

**Implementation notes:**

- Replace log text `before EnvDB GPU search` with backend-neutral text until a GPU backend is actually selected.
- Compute `effective_gpu_stages` from stage reports, not from `use_gpu_flag`.

---

## Phase 1: GPU native target-split strategy

### Task 1.1: Extend native target-split search argument construction to support controlled GPU knobs

**Objective:** Make `run_native_target_split_search` able to produce a single MMseqs search command with both native target splitting and controlled GPU flags.

**Files:**
- Modify: `scripts/lib/local_msa/sharding.py`
- Modify: `scripts/test_local_msa_sharding.py`

**Test first:**

Add `test_run_native_target_split_search_can_append_gpu_controls`:

Input:

```python
run_native_target_split_search(
    mmseqs_bin="mmseqs-gpu",
    base_search_params=[
        "search", "qdb", "envdb", "res", "tmp_env",
        "--num-iterations", "3", "-a",
        "--threads", "999", "--split", "99", "--gpu", "0"
    ],
    split_count=4,
    total_threads=32,
    env={},
    run_mmseqs=fake_run_mmseqs,
    extra_search_params=[
        "--db-load-mode", "2",
        "--gpu", "1",
        "--prefilter-mode", "1",
    ],
    split_mode=0,
)
```

Expected command:

- exactly one `--split 4`
- exactly one `--split-mode 0`
- exactly one `--threads 32`
- exactly one `--gpu 1`
- exactly one `--prefilter-mode 1`
- keeps `--db-load-mode 2`
- does not preserve caller conflicts like `--threads 999`, `--split 99`, or `--gpu 0`

**Implementation note:**

Current conflict stripping covers split/thread. Extend it to controlled GPU/preload keys when supplied:

- `--gpu`
- `--gpu-server`
- `--gpu-server-wait-timeout`
- `--prefilter-mode`
- `--db-load-mode`

This avoids duplicate contradictory argv.

---

### Task 1.2: Make the EnvDB target-split branch choose GPU MMseqs when GPU is requested

**Objective:** Replace the hard-coded CPU binary choice for target-split EnvDB search.

**Files:**
- Modify: `scripts/run_local_msa.py:3143-3279`
- Modify: `scripts/test_run_local_msa.py`

**Current bad line:**

```python
shard_mmseqs_bin = mmseqs_cpu if Path(str(mmseqs_cpu)).exists() else mmseqs_bin
```

**Desired strategy:**

```python
if target_shard_plan.enabled:
    if use_gpu_flag:
        shard_mmseqs_bin = mmseqs_bin
        extra = [
            "--db-load-mode", str(gpu_server_db_load_mode),
            "--gpu", "1",
            "--prefilter-mode", "1",
        ]
        backend = "gpu_native_split"
    else:
        shard_mmseqs_bin = mmseqs_cpu if Path(str(mmseqs_cpu)).exists() else mmseqs_bin
        extra = ["--db-load-mode", "2"]
        backend = "cpu_native_split"
```

If gpuserver + native split proves safe in Phase 2, add:

```python
if normalized_gpu_server_mode != "off" and envdb_gpuserver_ready:
    extra += [
        "--gpu-server", "1",
        "--gpu-server-wait-timeout", str(effective_gpu_server_wait_timeout),
    ]
    backend = "gpu_server_native_split"
```

**Test first:**

Add `test_envdb_target_split_uses_gpu_binary_and_flags_when_gpu_requested`:

- Monkeypatch `inspect_mmseqs_runtime` to return GPU ready with `mmseqs_bin="/gpu/mmseqs"`, `use_gpu_mmseqs=True`, `selected_gpu_id=1`, `effective_preferred_gpus=[1]`.
- Monkeypatch `resolve_mmseqs_binaries` to return `Path("/gpu/mmseqs"), Path("/cpu/mmseqs")`.
- Force `preset="maximum"`, `target_shard_mode="required"`, `target_shards=4`, `use_gpu=True`.
- Fake `run_mmseqs` records commands and stops before unpack.
- Assert EnvDB search command uses `/gpu/mmseqs`, includes `--gpu 1`, `--prefilter-mode 1`, `--split 4`, `--split-mode 0`, `--threads 32`.
- Assert it did not use `/cpu/mmseqs` for EnvDB search.

**Run:**
`PYTHONPATH=scripts:scripts/lib:. pytest scripts/test_run_local_msa.py::test_envdb_target_split_uses_gpu_binary_and_flags_when_gpu_requested -q`

Expected: fails on current code, passes after implementation.

---

### Task 1.3: Add strict fallback semantics for GPU-required mode

**Objective:** If the user or pipeline requires GPU MMseqs, do not silently execute CPU EnvDB.

**Files:**
- Modify: `scripts/run_local_msa.py`
- Modify: `scripts/test_run_local_msa.py`

**Test first:**

Add `test_envdb_target_split_gpu_required_does_not_cpu_fallback`:

- Setup GPU target-split branch.
- Fake first GPU `run_mmseqs` call raises `RuntimeError("Database ... is not a valid GPU database")`.
- Run with `gpu_mode="required"`, `use_gpu=True`, `target_shard_mode="required"`.
- Assert the workflow raises.
- Assert no CPU EnvDB native-split command was attempted.

Add `test_envdb_target_split_opportunistic_records_cpu_fallback`:

- Same fake GPU failure.
- Run with `gpu_mode="opportunistic"` or `auto`.
- Assert CPU fallback is allowed.
- Assert quality/sidecar metadata reports:
  - `envdb_acceleration.requested_gpu=true`
  - `envdb_acceleration.effective_gpu=false`
  - `envdb_acceleration.backend="cpu_native_split"`
  - `envdb_acceleration.fallback_reason` contains the GPU failure.

**Implementation rules:**

- Required means required. No CPU fallback.
- Opportunistic/auto may fallback, but only with explicit acceleration metadata and log lines.
- Quality degradation and acceleration fallback are different. CPU fallback should not set `degraded_quality=true` if MSA quality semantics were otherwise intact.

---

### Task 1.4: Add GPU DB readiness into runtime DB preflight

**Objective:** If GPU EnvDB is requested, validate the GPU-search substrate before the long run starts.

**Files:**
- Modify: `scripts/run_local_msa.py`
- Possibly modify: `scripts/lib/local_msa/db_integrity.py`
- Modify: `scripts/test_run_local_msa.py` or `scripts/test_local_msa_db_integrity.py`

**Checks:**

For each target DB that may be searched on GPU, record:

- `gpu_ready_marker`: `<prefix>.GPU_READY`
- `header_payload`: `<prefix>_h`
- `seq_header_payload`: `<prefix>_seq_h`
- optional: tiny direct GPU smoke when explicitly requested by validation command, not every production run

For `gpu_mode=required`:

- missing GPU substrate should fail before long MMseqs search.

For `gpu_mode=auto/opportunistic`:

- missing GPU substrate should select CPU fallback with explicit `envdb_acceleration.fallback_reason="gpu_db_not_ready"`.

**Do not overclaim:** marker/header checks prove local GPU substrate shape, not actual performance. Real proof still requires the stage command and telemetry.

---

## Phase 2: gpuserver + native split validation

### Task 2.1: Keep direct GPU native split separate from gpuserver native split

**Objective:** Avoid assuming gpuserver and native target splitting are compatible at full EnvDB scale just because direct GPU split accepted a tiny probe.

**Files:**
- Modify: `scripts/run_local_msa.py`
- Modify: `scripts/test_run_local_msa.py`

**Initial default:**

- Use direct GPU native split for the first implementation unless a separate full-scale gpuserver+split validation passes.
- Do not append `--gpu-server 1` to the native-split command by default until validated.

**Reason:** existing gpuserver lifecycle is target-DB keyed and has had stale/liveness/contract issues. Combining it with EnvDB target splitting needs its own evidence.

**Test:**

- Assert GPU native split command initially includes `--gpu 1 --prefilter-mode 1` but not `--gpu-server 1` unless an explicit internal strategy flag enables it.

---

### Task 2.2: Add an isolated validation script for GPU native split

**Objective:** Provide a repeatable way to validate direct GPU split and gpuserver split outside the full BioModStack job launcher.

**Files:**
- Create: `scripts/validate_mmseqs_gpu_native_split.py`
- Create: `scripts/test_validate_mmseqs_gpu_native_split.py`

**Script behavior:**

Inputs:

- `--db-path /mnt/BioModStack/colabfold_db`
- `--sequence-file ...` or `--sequence ...`
- `--target-db colabfold_envdb_202108_db`
- `--gpu-id 1`
- `--split 4`
- `--threads 32`
- `--mode direct-gpu|gpuserver-gpu|cpu-control`
- `--out-dir ...`
- `--timeout-seconds` for validation only

Outputs:

- `command.json`
- `stage_report.json`
- `nvidia_smi_monitor.jsonl`
- MMseqs stdout/stderr logs
- result DB existence checks

The script should not claim success based only on command launch. Success means:

- command exits 0
- result DB materializes
- no known fatal strings in logs:
  - `Database ... is not a valid GPU database`
  - `Invalid alignment result record`
  - `Missing alignments for sequence`
- monitor shows GPU activity during the command window for GPU modes

**Run after implementation:**

Direct GPU split probe:

`CUDA_VISIBLE_DEVICES=1 python3 scripts/validate_mmseqs_gpu_native_split.py --db-path /mnt/BioModStack/colabfold_db --target-db colabfold_envdb_202108_db --sequence-file /mnt/BioModStack/msa_validation/repa_runtime_verification/repa_sequence.fasta --mode direct-gpu --split 4 --threads 32 --out-dir /mnt/BioModStack/msa_validation/gpu_mmseqs_split/direct_gpu_$(date -u +%Y%m%dT%H%M%SZ)`

CPU control:

`python3 scripts/validate_mmseqs_gpu_native_split.py --db-path /mnt/BioModStack/colabfold_db --target-db colabfold_envdb_202108_db --sequence-file /mnt/BioModStack/msa_validation/repa_runtime_verification/repa_sequence.fasta --mode cpu-control --split 4 --threads 32 --out-dir /mnt/BioModStack/msa_validation/gpu_mmseqs_split/cpu_control_$(date -u +%Y%m%dT%H%M%SZ)`

gpuserver mode only after direct GPU proves sane:

`CUDA_VISIBLE_DEVICES=1 python3 scripts/validate_mmseqs_gpu_native_split.py --db-path /mnt/BioModStack/colabfold_db --target-db colabfold_envdb_202108_db --sequence-file /mnt/BioModStack/msa_validation/repa_runtime_verification/repa_sequence.fasta --mode gpuserver-gpu --split 4 --threads 32 --out-dir /mnt/BioModStack/msa_validation/gpu_mmseqs_split/gpuserver_gpu_$(date -u +%Y%m%dT%H%M%SZ)`

---

## Phase 3: End-to-end runtime validation

### Task 3.1: Add generated command verification for Nextflow/API paths

**Objective:** Ensure source-level fixes survive the API/Nextflow launch surface.

**Files:**
- Modify as needed:
  - `platform/api/services/nextflow.py`
  - `platform/api/routers/jobs.py`
  - `modules/structure_prediction.nf`
  - `scripts/lib/local_msa/cli/args.py`
  - `scripts/lib/local_msa/cli/run_single.py`
- Tests:
  - existing API/Nextflow tests where launch command args are asserted

**Checks:**

For high-quality local MSA jobs with GPU requested, generated `.command.sh` or Python subprocess argv must include:

- `--use-gpu` or equivalent `msa_use_gpu=true`
- `--gpu-mode required` when user requests strict GPU
- `--gpu-server-db-load-mode 2` unless explicitly overridden
- `--gpu-server-startup-wait 5.0` unless explicitly overridden
- `--target-shard-mode required|auto`
- `--target-shards 4`
- `--threads 32`

Do not rely on repo defaults alone. Verify generated `.command.sh`.

---

### Task 3.2: Run a real RepA/P03066 maximum-quality validation

**Objective:** Prove the fix on the exact class of job that exposed the problem.

**Command shape:**

`python3 scripts/run_local_msa.py --sequence <RepA sequence> --name repa_gpu_maximum_force --out_dir /mnt/BioModStack/msa_validation/gpu_mmseqs_split/repa_gpu_maximum_<timestamp> --db_path /mnt/BioModStack/colabfold_db --cache_dir /mnt/BioModStack/msa_cache --preset maximum --threads 32 --use-gpu --gpu-id 1 --gpu-mode required --gpu-server-mode off --target-shard-mode required --target-shards 4 --target-shard-min-size-gb 0 --force_refresh`

Initial validation should use `--gpu-server-mode off` to prove direct GPU native split independently. gpuserver is a later optimization.

**Required evidence:**

- `<job>_msa_quality.json` shows:
  - `from_cache=false`
  - `preset=maximum`
  - `use_env_effective=true`
  - `degraded_quality=false`
  - `envdb_acceleration.backend="gpu_native_split"`
  - `envdb_acceleration.effective_gpu=true`
  - stage report for EnvDB search includes `--gpu 1`, `--prefilter-mode 1`, `--split 4`, `--split-mode 0`, `--threads 32`
- A3M exists and header count is nonzero.
- No known degradation signatures in log:
  - `Invalid alignment result record`
  - `Missing alignments for sequence`
  - `WARNING: Alignment expansion failed`
- Monitor shows GPU activity during EnvDB search, not only during UniRef.
- Compare final depth/header overlap to current CPU-native-split control (`167` for the latest fresh RepA maximum run) and flag any depth/content regression.

---

### Task 3.3: Only then evaluate gpuserver native split

**Objective:** Determine whether persistent gpuserver improves direct GPU native split without breaking correctness.

**Validation command shape:** same as Task 3.2 but with:

- `--gpu-server-mode persistent`
- `--gpu-server-db-load-mode 2`
- `--gpu-server-startup-wait 5.0`

**Acceptance gate:**

- Same output/depth/signature requirements as direct GPU.
- Stage report shows `uses_gpu_server=true` for EnvDB search.
- If gpuserver hangs with low GPU/disk IO, preserve logs, kill validation, and keep default on direct GPU or CPU fallback. Do not promote gpuserver native split by assumption.

---

## Phase 4: UX/API truthfulness

### Task 4.1: Update user-facing labels after runtime truth is fixed

**Objective:** Make the UI/API describe the real backend instead of implying universal GPU acceleration.

**Files likely involved:**

- `platform/frontend/src/components/StructurePredictionTemplate.tsx`
- `platform/frontend/src/components/structurePredictionUiState.ts`
- `platform/frontend/tests/structurePredictionUiState.test.ts`
- API template/config files if they expose local MSA copy

**Wording rules:**

- Before GPU fix is validated: “Local high-quality MSA uses EnvDB; acceleration backend is reported per run.”
- After GPU direct split is validated: “EnvDB search: GPU native split when available; CPU native split fallback is reported.”
- Never say simply “GPU MMseqs” without exposing whether EnvDB search used GPU.

---

## Test suite checklist

Run targeted tests after each phase:

`PYTHONPATH=scripts:scripts/lib:. pytest scripts/test_local_msa_stage_report.py scripts/test_local_msa_sharding.py scripts/test_run_local_msa.py -q`

If API/Nextflow surfaces changed:

`TMPDIR=/mnt/BioModStack/tmp/hermes-pytemp /home/dalab/.local/bin/uv run --directory platform/api pytest tests/test_jobs_msa_batch_param_propagation.py tests/test_nextflow_msa_batch.py -q`

Syntax checks:

`python3 -m py_compile scripts/run_local_msa.py scripts/lib/local_msa/sharding.py scripts/lib/local_msa/mmseqs_stage_report.py scripts/validate_mmseqs_gpu_native_split.py`

Real runtime validation:

- direct GPU native split RepA maximum
- CPU native split control, if output changes materially
- gpuserver native split only after direct GPU is stable

---

## Rollback plan

Each phase should be separately revertible.

- Phase 0 observability is safe and should stay even if GPU behavior is rolled back.
- Phase 1 behavior change can be guarded behind existing `gpu_mode` semantics:
  - `required`: fail if GPU EnvDB cannot be used
  - `auto/opportunistic`: fallback to CPU native split with explicit metadata
  - `cpu`: CPU native split only
- If GPU native split causes correctness regressions, default back to CPU native split but keep stage reporting and the explicit acceleration metadata.

---

## Implementation order summary

1. Add stage reporting tests and helper.
2. Wire stage reporting into `run_local_msa.py` and quality JSON.
3. Fix misleading `used_gpu_mmseqs`/logs by adding acceleration metadata.
4. Extend native target-split helper to support controlled GPU flags.
5. Change EnvDB native split branch to use GPU binary/flags when GPU is requested.
6. Enforce required-vs-opportunistic fallback semantics.
7. Add GPU DB readiness checks.
8. Validate direct GPU native split on real RepA maximum.
9. Only after direct GPU works, validate gpuserver native split.
10. Update UI/API wording to report effective backend.

---

## Blunt risk assessment

Most likely good fix:

- direct GPU native split for EnvDB search with stage-level proof, CPU fallback only when GPU is not required.

Most likely footgun:

- trying to make persistent gpuserver + huge EnvDB + native split the default without isolated validation.

Most important reporting fix:

- stop using `used_gpu_mmseqs=true` as evidence. The quality report needs to say exactly which stages used GPU.
