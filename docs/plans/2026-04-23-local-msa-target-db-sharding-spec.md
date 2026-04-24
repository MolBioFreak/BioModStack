# Local High-Quality MSA Target-DB Sharding Implementation Spec

> **For Hermes:** Use `subagent-driven-development` only after this spec is approved. Implement this as staged PRs with tests first. Do not use this plan as permission to rewrite the full local-MSA stack or to change the remote ColabFold API provider.

**Goal:** Add a production-gated target-database sharding path for BioModStack local high-quality MSA generation so `balanced`/`maximum` EnvDB-backed runs can normally use parallel target-shard search once validated, without raising Christian's preferred total `msa_threads` budget above 32.

**Architecture:** Keep the existing ColabFold-compatible local workflow as the control path. Introduce a package-owned sharding layer that can split a target MMseqs database, run several independent search workers under a single total thread budget, merge the result DBs with `mmseqs mergedbs`, and hand the merged result back to the existing downstream expansion/filter/result2msa flow. Phase 1 targets EnvDB search only, because the recent maximum-quality runtime evidence shows the long tail is EnvDB and because keeping UniRef/profile construction unsharded minimizes quality-regression risk.

**Tech Stack:** Python orchestration, MMseqs2 (`splitdb`, `search`, `mergedbs`, `result2msa`, `unpackdb`), BioModStack `scripts/lib/local_msa`, legacy compatibility shims (`scripts/run_local_msa.py`, `scripts/batch_msa.py`), FastAPI launch plumbing, Nextflow parameter forwarding, pytest, and local real-DB benchmark artifacts.

---

## 1. Why this spec exists

Christian corrected the earlier framing: if the desired output is a higher-quality MSA, BioModStack should expect to use EnvDB. `fast`/UniRef-only is a screening path, with an automatic EnvDB rescue only when shallow. It is not the quality-equivalent replacement for `balanced` or `maximum`.

Recent local evidence also showed that simply increasing `--threads` is not the right first fix. A single maximum-quality run can pass `--threads 32` while MMseqs stages still expose low effective CPU occupancy. Raising the global thread request to 48 is therefore not a robust answer, and Christian explicitly wants local MSA to stay at 32 total threads unless revisited.

The re-assessed direction is:

1. Keep high-quality MSA semantics EnvDB-backed.
2. Keep the total requested MSA CPU budget at 32.
3. Rework the local high-quality path so one logical MSA can run multiple target-shard search workers concurrently.
4. Merge shard results before downstream ColabFold-compatible processing.
5. Treat target sharding as the intended default architecture for local EnvDB-backed quality runs once quality equivalence, fallback behavior, and benchmark gates pass; the initial implementation flag is for safe rollout, not because unsharded should remain the long-term default.

Christian's follow-up position is accepted here: for high-quality local MSA, BioModStack should basically always shard when the target database is large enough for sharding overhead to be amortized. The implementation should therefore aim for adaptive/default-on sharding for `balanced`/`maximum` EnvDB-backed jobs, with `off` retained as a control-path and emergency escape hatch.

---

## 2. Current repo-grounded truth

### 2.1 Local quality presets

`/home/dalab/biomodstack/biomodstack/scripts/run_local_msa.py` defines the current presets at lines `88-124`:

- `maximum`
  - `num_iterations: 3`
  - `use_env: True`
  - `use_expand: True`
  - `use_filter: True`
  - `max_seqs: 10000`
- `balanced`
  - `num_iterations: 2`
  - `use_env: True`
  - `use_expand: False`
  - `use_filter: True`
  - `max_seqs: 300`
- `fast`
  - `num_iterations: 1`
  - `use_env: False`
  - `use_expand: False`
  - `use_filter: False`
  - `max_seqs: 300`

This means the quality path is already conceptually EnvDB-backed. The sharding work should improve that path rather than treating `fast` as a sufficient replacement.

### 2.2 Local workflow shape

`run_colabfold_msa_workflow(...)` lives at `scripts/run_local_msa.py:2261-3248`. Its current high-level flow is:

1. create a query DB (`createdb`) at `2662-2670`
2. search UniRef30 at `2677-2750`
3. derive a profile DB with `result2profile` if needed at `2770-2777`
4. optionally expand UniRef alignments at `2804-2832`
5. optionally filter UniRef results at `2837-2850`
6. convert UniRef results to A3M DB at `2864-2870`
7. optionally search EnvDB at `2923-3015`
8. optionally expand/filter EnvDB results at `3017-3062`
9. convert EnvDB results to A3M DB at `3065-3072`
10. merge UniRef and EnvDB A3M DBs with `mergedbs` at `3077-3083`
11. `unpackdb`, sanitize, quality-report, and cache at `3087-3243`

The best Phase 1 insertion point is the EnvDB search block, specifically the code that currently builds `env_base_search_params` at `2943-2951` and then invokes one of the direct/gpuserver/CPU `run_mmseqs(... search ...)` branches at `2953-3015`.

### 2.3 Thread/default contract

`run_colabfold_msa_workflow(...)` currently accepts `num_threads: int = 32` at `scripts/run_local_msa.py:2270`, and the CLI parser defaults `--threads` to `32` at `3277-3278`.

Some Nextflow/global config surfaces still show `48` in the current worktree. That is a separate default-reconciliation issue. This sharding spec must not use a default thread bump as the solution. In sharded mode, `msa_threads` is the total budget, not the per-worker budget.

### 2.4 Batch behavior

`scripts/batch_msa.py` has two materially different paths:

- `fast` without advanced overrides uses a true batched UniRef search starting at `516`.
- Higher-quality presets or advanced overrides use `_run_colabfold_per_sequence(...)` at `155-298`, which shells out to `run_local_msa.py` once per sequence.

Therefore, target DB sharding belongs first in `run_local_msa.py`/the local package path. `batch_msa.py` only needs to forward the new flags for high-quality per-sequence mode in Phase 1.

### 2.5 Package extraction state

The package surface exists but still delegates heavily to legacy scripts:

- `scripts/lib/local_msa/types.py` contains request dataclasses.
- `scripts/lib/local_msa/cli/run_single.py` builds and dispatches `SingleMSARequest`.
- `scripts/lib/local_msa/cli/run_batch.py` builds and dispatches `BatchMSARequest`.
- `scripts/lib/local_msa/providers/local_mmseqs.py` lazy-loads `run_local_msa.py` and registers the legacy implementation.
- `scripts/lib/local_msa/batching.py` lazy-loads `batch_msa.py`.

Sharding should be implemented in the package, not buried as another large nested block in `run_local_msa.py`. The legacy scripts can remain compatibility entrypoints, but the new sharding policy/runner should live under `scripts/lib/local_msa/`.

### 2.6 MMseqs primitive availability

The local MMseqs binary at `/mnt/BioModStack/colabfold_db/mmseqs/bin/mmseqs` exposes the required primitives:

- `splitdb <i:DB> <o:DB> --split N`
- `search <queryDB> <targetDB> <alignmentDB> <tmpDir>`
- `mergedbs <queryDB> <mergedResultDB> <resultShard...>`
- `result2msa <queryDB> <targetDB> <resultDB> <msaDB>`

A toy proof was already created at `/mnt/BioModStack/tmp/hermes-mmseqs-shard-feasibility/` and produced a valid merged A3M from split target shards. That proves the primitive chain is viable. It does not prove production equivalence on full UniRef/EnvDB yet.

---

## 3. Non-negotiable guardrails

1. **Do not increase the default MSA thread budget.**
   - Treat `msa_threads` as the total budget.
   - Example: `msa_threads=32`, `target_shard_workers=4` means four concurrent workers with `--threads 8`, not four workers with `--threads 32`.

2. **Default policy should become adaptive sharding for high-quality local EnvDB jobs after validation.**
   - The first implementation tranche may default to `off` for bisectability and benchmark collection.
   - The rollout target is `target_shard_mode=auto` by default for local `balanced`/`maximum` jobs where `use_env=True`, the target database is large enough, disk space is safe, and a shard set is available or can be built.
   - `target_shard_mode=off` must remain available for controls, debugging, and emergency rollback.
   - Default-on means `auto`, not `required`: production jobs may fall back to the unsharded control path and must record the fallback.
   - `fast`/screening mode and the remote ColabFold API provider are unchanged.

3. **Quality mode remains EnvDB-backed.**
   - Do not claim that `fast` is equivalent to `balanced`/`maximum`.
   - Sharding should accelerate the EnvDB-backed path, not dodge it.

4. **Remote ColabFold API provider is out of scope.**
   - No change to `msa_provider=colabfold_api` behavior.
   - New target-sharding options are local-MMseqs-only.

5. **Phase 1 does not shard gpuserver.**
   - MMseqs gpuserver is keyed around target DB identity.
   - Split target DBs imply separate target identities and can create VRAM/server churn.
   - Initial sharded workers should run with CPU MMseqs by default, or with GPU/gpuserver explicitly disabled for the sharded stage, until benchmarks justify a GPU-backed shard mode.

6. **No independent multi-iteration search per shard for production UniRef semantics.**
   - Running `--num-iterations 3` independently on each target shard can build shard-local profiles and then merge results. That is not guaranteed to match a global iterative search.
   - If UniRef search is sharded later, implement explicit global iteration barriers: one shard search round, merge, `result2profile` against the original target DB, then the next round.

7. **Cache isolation until equivalence is proven.**
   - Initial sharded outputs must either avoid writing the canonical sequence cache or use an isolated cache profile suffix such as `maximum_sharded_envdb_v1_<policyhash>`.
   - Do not let experimental sharded results silently overwrite the canonical cache for unsharded quality runs.

8. **Fallback must be explicit and recorded.**
   - `target_shard_mode=auto`: fallback to unsharded search is allowed and must be reported.
   - `target_shard_mode=required`: any shard/split/merge failure fails the job.
   - `target_shard_mode=off`: current path only.

9. **Every new parameter must cross every active local-MSA seam.**
   - script parser
   - `local_msa.types`
   - `local_msa.cli.run_single`
   - `local_msa.cli.run_batch`
   - `batch_msa.py` high-quality fallback forwarding
   - FastAPI/Nextflow command construction
   - child-job/iteration propagation where MSA parameters are copied
   - tests for propagation

10. **One sharding owner.**
    - The sharding policy and worker logic belongs in `scripts/lib/local_msa/sharding.py`.
    - Do not duplicate worker arithmetic in API, Nextflow, and shell scripts.

---

## 4. Target architecture

### 4.1 New package module

Create:

`/home/dalab/biomodstack/biomodstack/scripts/lib/local_msa/sharding.py`

This module owns:

- parameter normalization
- total-thread arithmetic
- shard root resolution
- shard manifest validation
- `splitdb` execution and locking
- worker command construction
- concurrent worker supervision
- result DB merge
- shard timing/metrics payload construction

The minimum useful data model:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

TargetShardMode = Literal["off", "auto", "required"]
TargetShardStage = Literal["envdb", "uniref", "all"]
TargetShardBackend = Literal["cpu", "inherit"]

@dataclass(frozen=True)
class TargetShardPolicy:
    mode: TargetShardMode
    stages: tuple[TargetShardStage, ...]
    shard_count: int
    workers: int
    total_threads: int
    threads_per_worker: int
    backend: TargetShardBackend
    shard_root: Path
    keep_temp: bool = False

    @property
    def enabled(self) -> bool:
        return self.mode != "off" and self.shard_count > 1 and self.workers > 1

@dataclass(frozen=True)
class TargetShard:
    index: int
    db_path: Path
    tmp_dir: Path
    result_db: Path
    log_path: Path

@dataclass(frozen=True)
class TargetShardSet:
    source_db: Path
    alias: str
    split_prefix: Path
    shards: tuple[Path, ...]
    manifest_path: Path

@dataclass(frozen=True)
class ShardedSearchResult:
    result_db: Path
    fallback_used: bool
    per_shard: tuple[dict, ...]
    merged_result_count: int | None
    elapsed_seconds: float
```

Exact names can change during implementation, but these concepts should not be spread across unrelated files.

### 4.2 Parameter semantics

Expose these local-MMseqs parameters:

CLI flags on `run_local_msa.py` and `batch_msa.py`:

```text
--target-shard-mode off|auto|required
--target-shard-stages envdb|uniref|all
--target-shard-count INT
--target-shard-workers INT
--target-shard-backend cpu|inherit
--target-shard-root PATH
--target-shard-keep-temp
```

API/Nextflow parameter names:

```text
msa_target_shard_mode
msa_target_shard_stages
msa_target_shard_count
msa_target_shard_workers
msa_target_shard_backend
msa_target_shard_root
msa_target_shard_keep_temp
```

Initial defaults:

```text
mode: off
stages: envdb
count: 0
workers: 0
backend: cpu
root: <db_path>/shards
keep_temp: false
```

When `mode=auto` or `required` and `count=0`, the resolver may choose a conservative default for EnvDB only:

```text
shard_count = 4 if total_threads >= 16 else 2
workers = min(shard_count, max(1, total_threads // 8))
threads_per_worker = max(1, total_threads // workers)
```

With Christian's preferred `msa_threads=32`, normal useful configurations are:

```text
2 workers x 16 threads = 32 total
4 workers x 8 threads  = 32 total
8 workers x 4 threads  = 32 total
```

The implementation must reject or clamp any configuration where `workers * threads_per_worker > total_threads`.

### 4.3 Shard storage layout

Default shard root:

```text
<db_path>/shards/<db_alias>/<mmseqs_version_or_hash>/<source_db_fingerprint>/split_<N>/
```

Example:

```text
/mnt/BioModStack/colabfold_db/shards/colabfold_envdb_202108_db/bd01c2229f02/size122G_mtime.../split_4/
  manifest.json
  colabfold_envdb_202108_db_split_0_4.*
  colabfold_envdb_202108_db_split_1_4.*
  colabfold_envdb_202108_db_split_2_4.*
  colabfold_envdb_202108_db_split_3_4.*
```

The manifest should include:

```json
{
  "schema_version": 1,
  "source_db": "/mnt/BioModStack/colabfold_db/colabfold_envdb_202108_db",
  "source_dbtype_size": 4,
  "source_index_size": 123456,
  "source_payload_size": 987654321,
  "source_mtime_ns": 123456789,
  "mmseqs_bin": "/mnt/BioModStack/colabfold_db/mmseqs/bin/mmseqs",
  "mmseqs_version": "bd01c2229f027d8d8e61947f44d11ef1a7669212",
  "shard_count": 4,
  "split_cmd": ["mmseqs", "splitdb", "...", "--split", "4"],
  "created_at": "2026-04-23T00:00:00Z",
  "shards": [
    {"index": 0, "db_path": "..._split_0_4", "dbtype_exists": true},
    {"index": 1, "db_path": "..._split_1_4", "dbtype_exists": true}
  ]
}
```

Use a lock file beside the manifest, for example `split_4/.split.lock`, so two jobs do not run `splitdb` into the same destination concurrently.

### 4.4 Phase 1 EnvDB search-only flow

Phase 1 changes only the EnvDB search block.

Current unsharded EnvDB search:

```text
search profile_db_or_query_db envdb env_result_db tmp_env ...
```

Phase 1 sharded EnvDB search:

```text
ensure splitdb(envdb, split_prefix, shard_count)

for each shard concurrently, bounded by target_shard_workers:
    search profile_db_or_query_db shard_db shard_result_db shard_tmp \
        --num-iterations <current config value or phase-gated override> \
        -a -e <evalue> --max-seqs <max_seqs> \
        -s <sensitivity> --threads <threads_per_worker> \
        --db-load-mode 2

mergedbs profile_db_or_query_db env_result_db shard_result_0 ... shard_result_N

continue existing code:
    optional expandaln/align against original envdb_seq/envdb_aln
    optional filterresult against original envdb_seq
    result2msa against original envdb_seq
```

Important: the merged result DB must be consumed with the original `envdb`/`envdb_seq` database, not the split shard DBs. The toy proof showed this works at small scale; real EnvDB validation is still required.

### 4.5 Why Phase 1 starts with EnvDB only

EnvDB is the right first target because:

- quality modes already require EnvDB
- the recent maximum-quality runtime tail was EnvDB-heavy
- UniRef/profile creation remains unchanged, reducing risk
- downstream `result2profile`/UniRef expansion semantics stay stable
- if search is not the dominant tail after this patch, the benchmark will reveal that before more complex work starts

### 4.6 Phase 1.5 optional UniRef sharding

If profiling later shows UniRef search is also worth sharding, do not run independent multi-iteration searches per shard and merge them as if they were equivalent.

Use explicit global iteration barriers:

```text
current_query = query_db
for iteration in 1..num_iterations:
    run one sharded search round:
        search current_query uniref_shard_i result_i tmp_i --num-iterations 1 ...
    merged_result = mergedbs(current_query, result_i...)
    if iteration < num_iterations:
        current_query = result2profile(query_db, uniref_db, merged_result, profile_iteration_i)

final uniref result_db = merged_result
continue existing expansion/filter/result2msa flow
```

This preserves the key property that profile updates happen after global merged evidence, not independently inside each target shard.

### 4.7 Phase 2 downstream sharding

Only consider Phase 2 if Phase 1 metrics show the remaining tail is `expandaln`, `align`, `filterresult`, or `result2msa` rather than `search`.

Phase 2 is harder because expansion/filtering use sibling databases such as:

```text
<target>_seq
<target>_aln
```

A safe Phase 2 would need consistent shard bundles for the main target DB and its sibling `_seq`/`_aln` databases, plus validation that target IDs remain compatible with `result2msa` and downstream parsers. This is explicitly not Phase 1.

---

## 5. Implementation plan

### PR 1: Add sharding policy tests and dataclasses

**Objective:** Create the package-owned sharding contract without touching runtime behavior.

**Files:**

- Create: `scripts/lib/local_msa/sharding.py`
- Create: `scripts/test_local_msa_sharding.py`
- Modify: `scripts/lib/local_msa/__init__.py` only if exports are needed

**Tests to write first:**

1. `mode=off` returns disabled policy.
2. `mode=auto`, `total_threads=32`, `count=4`, `workers=4` resolves `threads_per_worker=8`.
3. `mode=auto`, `total_threads=32`, `count=8`, `workers=8` resolves `threads_per_worker=4`.
4. invalid `count=1` disables or rejects depending on `mode`.
5. `required` rejects impossible arithmetic instead of silently falling back.
6. stage parsing accepts `envdb`, `uniref`, `all`, and comma-separated `envdb,uniref` if implemented.

**Verification command:**

```bash
pytest scripts/test_local_msa_sharding.py -q
```

Expected: new tests pass; no local-MSA runtime behavior has changed.

### PR 2: Implement splitdb shard manager

**Objective:** Build and validate reusable target shards safely.

**Files:**

- Modify: `scripts/lib/local_msa/sharding.py`
- Test: `scripts/test_local_msa_sharding.py`

**Implementation requirements:**

- Resolve default `shard_root` under `<db_path>/shards` unless explicitly supplied.
- Fingerprint source DB using at least `.dbtype`, `.index`, main payload size, and mtime.
- Use a lock file during `splitdb`.
- Discover MMseqs split outputs by the observed pattern:

```text
<split_prefix>_split_0_<N>
<split_prefix>_split_1_<N>
...
```

- Write `manifest.json` only after all shard `.dbtype` files exist.
- Treat manifest mismatch as stale and rebuild in `auto`/`required` mode.

**Unit tests:**

- manifest match reuses existing shards
- manifest mismatch triggers rebuild decision
- missing one `.dbtype` invalidates the shard set
- lock path is deterministic

**Optional integration test:**

Create a tiny FASTA DB in a temporary directory and run:

```bash
/mnt/BioModStack/colabfold_db/mmseqs/bin/mmseqs createdb target.fasta target_db
/mnt/BioModStack/colabfold_db/mmseqs/bin/mmseqs splitdb target_db target_db --split 2
```

Skip this test if the local MMseqs binary is absent.

### PR 3: Implement concurrent sharded search + merge runner

**Objective:** Given a query/profile DB and a target shard set, run bounded concurrent searches and merge result DBs.

**Files:**

- Modify: `scripts/lib/local_msa/sharding.py`
- Possibly modify: `scripts/lib/local_msa/mmseqs_exec.py` if shared process supervision is needed
- Test: `scripts/test_local_msa_sharding.py`

**Implementation requirements:**

- Use `concurrent.futures.ThreadPoolExecutor` or explicit `subprocess.Popen` supervision.
- Limit active workers to `policy.workers`.
- Pass `--threads policy.threads_per_worker` to each shard `search`.
- Put each shard temp directory under the current job temp directory, not under the persistent shard root.
- Write one log per shard.
- If any shard fails:
  - terminate pending/running shard workers if possible
  - include the failing shard index and log tail in the raised exception
- Merge only after all shard result DBs have `.dbtype`.
- Use:

```text
mmseqs mergedbs <query_or_profile_db> <merged_result_db> <result_shard_0> ... <result_shard_N>
```

**Unit tests:**

- command construction includes correct `--threads`
- worker count never exceeds policy workers
- merge command ordering is deterministic
- failure in one shard prevents merge
- `auto` fallback decision is visible to caller, not swallowed inside worker code

### PR 4: Wire EnvDB search-only sharding into `run_local_msa.py`

**Objective:** Allow `run_colabfold_msa_workflow(...)` to use sharded EnvDB search when explicitly requested.

**Files:**

- Modify: `scripts/run_local_msa.py`
- Modify: `scripts/lib/local_msa/types.py`
- Modify: `scripts/lib/local_msa/cli/run_single.py`
- Test: `scripts/test_run_local_msa.py`
- Test: `scripts/test_local_msa_package.py` if import/package identity is affected

**Implementation details:**

1. Extend `RuntimeOptions` or add a nested `TargetShardOptions` dataclass.
2. Add parser flags to `build_arg_parser()`.
3. Parse flags in `build_single_request_from_namespace(...)`.
4. Dispatch the parsed policy into `run_colabfold_msa_workflow(...)`.
5. Add optional keyword parameters to `run_colabfold_msa_workflow(...)` with safe defaults:

```python
target_shard_mode: str = "off"
target_shard_stages: str = "envdb"
target_shard_count: int = 0
target_shard_workers: int = 0
target_shard_backend: str = "cpu"
target_shard_root: str | None = None
target_shard_keep_temp: bool = False
```

6. Build a `TargetShardPolicy` after DB paths and runtime are resolved.
7. In the EnvDB block at `scripts/run_local_msa.py:2923-3015`, replace only the actual search invocation with a helper such as:

```python
env_search_report = None
try:
    if should_shard_stage(policy, "envdb"):
        env_search_report = run_sharded_search(
            mmseqs_bin=mmseqs_cpu if policy.backend == "cpu" else mmseqs_bin,
            query_db=profile_db if has_profile else query_db,
            target_db=envdb,
            result_db=Path(env_result_db),
            tmp_root=Path(tmp_dir) / "tmp_env_sharded",
            base_search_args=[
                "--num-iterations", str(config["num_iterations"]),
                "-a",
                "-e", str(config["evalue"]),
                "--max-seqs", str(config["max_seqs"]),
                "-s", str(config["sensitivity"]),
                "--db-load-mode", "2",
            ],
            policy=policy,
            env=env,
        )
    else:
        existing_unsharded_env_search()
except Exception:
    if policy.mode == "auto":
        existing_unsharded_env_search()
        mark_shard_fallback_used()
    else:
        raise
```

The actual implementation should avoid an inner function named `existing_unsharded_env_search()` unless the local code is refactored cleanly; the point is to isolate search dispatch without moving unrelated post-processing.

8. Continue existing expansion/filter/result2msa against the original EnvDB.

**Quality report additions:**

Add to `<job>_msa_quality.json`:

```json
{
  "target_sharding_requested": true,
  "target_sharding_effective": true,
  "target_shard_mode": "auto",
  "target_shard_stages": ["envdb"],
  "target_shard_count": 4,
  "target_shard_workers": 4,
  "target_shard_threads_per_worker": 8,
  "target_shard_total_threads_budget": 32,
  "target_shard_backend": "cpu",
  "target_shard_fallback_used": false,
  "target_shard_metrics": {
    "envdb": {
      "elapsed_seconds": 123.4,
      "per_shard": [
        {"index": 0, "elapsed_seconds": 31.2, "returncode": 0},
        {"index": 1, "elapsed_seconds": 35.9, "returncode": 0}
      ]
    }
  }
}
```

**Tests:**

- Default parser args produce sharding mode `off`.
- Explicit parser args reach `run_colabfold_msa_workflow(...)` through `dispatch_single_request(...)`.
- EnvDB sharding uses `mmseqs_cpu` when `backend=cpu` even if the main run selected GPU.
- `mode=auto` falls back and records fallback when the sharded runner raises.
- `mode=required` raises and does not continue unsharded.
- Existing unsharded tests still pass.

**Verification command:**

```bash
pytest scripts/test_run_local_msa.py scripts/test_local_msa_package.py scripts/test_local_msa_sharding.py -q
python -m py_compile scripts/run_local_msa.py scripts/lib/local_msa/sharding.py scripts/lib/local_msa/cli/run_single.py scripts/lib/local_msa/types.py
```

### PR 5: Forward flags through `batch_msa.py`

**Objective:** High-quality batch jobs that fall back to per-sequence `run_local_msa.py` can request sharded EnvDB search.

**Files:**

- Modify: `scripts/batch_msa.py`
- Modify: `scripts/lib/local_msa/types.py`
- Modify: `scripts/lib/local_msa/cli/run_batch.py`
- Test: `scripts/test_batch_msa.py`

**Implementation requirements:**

- Add the same parser flags to `batch_msa.py`.
- Add fields to `BatchMSARequest`.
- Pass the fields through `_run_colabfold_per_sequence(...)` into the `run_local_msa.py` subprocess command.
- Do not apply sharding to the existing true-batch-fast path in Phase 1.
- Preserve existing behavior when flags are unset.

**Tests:**

- In high-quality mode (`preset=maximum` or `balanced`), `_run_colabfold_per_sequence(...)` appends all target-shard flags.
- In default `fast` true-batch mode, sharding flags are either rejected with a clear warning or ignored with a clear report. Prefer rejecting `target_shard_mode != off` for true-batch-fast until implemented.

**Verification command:**

```bash
pytest scripts/test_batch_msa.py scripts/test_local_msa_sharding.py -q
python -m py_compile scripts/batch_msa.py scripts/lib/local_msa/cli/run_batch.py scripts/lib/local_msa/types.py
```

### PR 6: Forward flags through API and Nextflow launch surfaces

**Objective:** API-created local MSA jobs can request sharding without manual CLI invocation.

**Files:**

- Modify: `platform/api/services/nextflow.py`
- Modify: `platform/api/routers/jobs.py`
- Modify: `modules/structure_prediction.nf`
- Modify: `nextflow.config`
- Test: `platform/api/tests/test_nextflow_msa_batch.py`
- Test: `platform/api/tests/test_jobs_msa_batch_param_propagation.py`
- Add/modify any model-config tests that assert MSA CLI forwarding

**Implementation requirements:**

1. Add `msa_target_shard_*` to accepted/forwarded MSA parameter sets.
2. In `platform/api/services/nextflow.py::_build_msa_batch_command(...)`, append the corresponding `batch_msa.py` CLI flags.
3. In general Nextflow command building, pass new params through to workflow params where direct structure-prediction jobs use `run_local_msa.py`.
4. In `modules/structure_prediction.nf`, add conditional CLI string fragments for `run_local_msa.py` invocations.
5. In `nextflow.config`, define default params with safe defaults:

```groovy
msa_target_shard_mode = 'off'
msa_target_shard_stages = 'envdb'
msa_target_shard_count = 0
msa_target_shard_workers = 0
msa_target_shard_backend = 'cpu'
msa_target_shard_root = null
msa_target_shard_keep_temp = false
```

6. Keep frontend exposure out of Phase 1 unless Christian explicitly asks for UI controls.

**Verification command:**

```bash
TMPDIR=/mnt/BioModStack/tmp/hermes-pytemp /home/dalab/.local/bin/uv run --directory platform/api pytest tests/test_jobs_msa_batch_param_propagation.py tests/test_nextflow_msa_batch.py -q
pytest scripts/test_batch_msa.py scripts/test_run_local_msa.py scripts/test_local_msa_sharding.py -q
```

### PR 7: Add local toy integration test for split/search/merge/result2msa

**Objective:** Keep the proof-of-concept from becoming tribal knowledge.

**Files:**

- Add to: `scripts/test_local_msa_sharding.py` or create `scripts/test_local_msa_sharding_integration.py`

**Test shape:**

1. Create temporary query and target FASTA files.
2. Run `createdb` for both.
3. Run the sharding helper to split target into two shards.
4. Run sharded search with two workers and small thread count.
5. Merge result DBs.
6. Run `result2msa` against the original target DB.
7. Run `unpackdb` and assert the final A3M contains hits from both shards.

Skip if `/mnt/BioModStack/colabfold_db/mmseqs/bin/mmseqs` is unavailable.

**Verification command:**

```bash
pytest scripts/test_local_msa_sharding_integration.py -q
```

### PR 8: Real DB benchmark harness

**Objective:** Measure whether Phase 1 actually improves latency and CPU utilization without lowering MSA quality.

**Files:**

- Create: `scripts/benchmark_local_msa_sharding.py`
- Or create under an existing benchmark/tools directory if one is canonical

**Benchmark inputs:**

Use a small representative panel:

- one short/easy protein
- one GFP-style case where observed `maximum` had much deeper EnvDB-backed depth than `fast`
- one medium protein
- one difficult/shallow UniRef case

**Runs:**

For each sequence:

```text
control: preset=maximum, use_env=true, threads=32, target_shard_mode=off
candidate A: preset=maximum, use_env=true, threads=32, target_shard_mode=required, count=2, workers=2
candidate B: preset=maximum, use_env=true, threads=32, target_shard_mode=required, count=4, workers=4
candidate C: preset=balanced, use_env=true, threads=32, target_shard_mode=required, count=4, workers=4
```

Optional exploratory candidate:

```text
count=8, workers=8, threads_per_worker=4
```

**Metrics:**

- total wall time
- EnvDB search wall time
- downstream expansion/filter/result2msa wall time
- final MSA depth
- UniRef-only depth if available
- top N header overlap vs control
- exact query preservation
- output parser compatibility
- CPU occupancy summary
- disk read throughput if practical
- whether fallback occurred

**Acceptance gates for initial implementation merge:**

- Explicit `target_shard_mode=off` unsharded control path is unaffected.
- Sharded mode produces a valid A3M and valid quality JSON.
- No loss of query sequence/header.
- Final depth is at least 98% of unsharded control for every benchmark sequence, or any deficit is explicitly explained and sharded mode remains experimental-only.
- Top 100 non-query header overlap is at least 95% where control has at least 100 non-query hits.
- Median wall-clock improvement is at least 20% for `maximum` EnvDB-backed runs, or the team explicitly decides Phase 1 is not worth enabling.
- Total concurrent MMseqs worker thread requests never exceed `msa_threads`.

**Acceptance gates for flipping high-quality local defaults to adaptive sharding:**

- Same quality gates over a larger panel.
- Median wall-clock improvement at least 25% for maximum-quality EnvDB-backed runs.
- p95 wall-clock does not regress.
- No increase in failure rate.
- No unsafe shard-cache growth.
- At least one cold-cache and one warm-cache benchmark recorded.
- Operators can force `target_shard_mode=off` from CLI/API/Nextflow without code changes.

---

## 6. Operational behavior

### 6.1 Fallback modes

`off`:

- never uses sharding
- current control behavior

`auto`:

- attempts sharded search
- on split/search/merge failure, falls back to unsharded search
- quality report records fallback and error summary

`required`:

- attempts sharded search
- any sharding failure fails the MSA job
- use this for benchmarks because silent fallback hides evidence

### 6.2 Disk and cleanup policy

Persistent shard DBs can consume substantial disk. The implementation must:

- keep only one default split count unless explicitly requested
- store shard sets under the configured shard root, not the job temp dir
- store per-job search temps under the job temp dir
- delete per-job temp dirs unless `--target-shard-keep-temp` is set
- refuse to build shards if available space is below a conservative threshold
- report shard root and estimated shard bytes in quality JSON

### 6.3 Cache behavior

For Phase 1, use one of these safe policies:

Preferred initial policy:

```text
if target_sharding_effective:
    do not save to canonical sequence cache unless --target-shard-cache-write is later added and explicitly enabled
```

Alternative acceptable policy:

```text
cache_profile = f"{cache_profile}_sharded_envdb_v1_<policyhash>"
```

Do not silently save experimental sharded results into the same canonical cache object used by unsharded high-quality runs.

### 6.4 Observability

Add a top-level report section:

```json
"target_sharding": {
  "requested": true,
  "effective": true,
  "mode": "required",
  "stages": ["envdb"],
  "backend": "cpu",
  "shard_count": 4,
  "workers": 4,
  "threads_per_worker": 8,
  "total_threads_budget": 32,
  "shard_root": "/mnt/BioModStack/colabfold_db/shards/...",
  "fallback_used": false,
  "fallback_reason": null,
  "stages_report": {
    "envdb_search": {
      "elapsed_seconds": 123.4,
      "merge_elapsed_seconds": 2.1,
      "per_shard": [
        {"index": 0, "elapsed_seconds": 30.2, "returncode": 0, "log_path": "..."}
      ]
    }
  }
}
```

Also print a concise runtime line to stdout:

```text
Target sharding: envdb, 4 shards, 4 workers x 8 threads = 32 total, backend=cpu
```

### 6.5 Safety around gpuserver

Initial policy:

- If `target_shard_backend=cpu`, sharded workers use the CPU MMseqs binary and do not pass `--gpu`, `--gpu-server`, or gpuserver flags.
- If `target_shard_backend=inherit`, do not allow gpuserver in Phase 1 unless an explicit future PR implements shard-aware server handling.
- If the main run selected GPU for UniRef, this does not require the EnvDB sharded stage to use GPU.

Future GPU shard work must define:

- one gpuserver per shard vs direct GPU search tradeoff
- VRAM cap
- server lifecycle
- target alias naming
- cleanup semantics
- benchmark evidence that it beats CPU sharding

---

## 7. Validation matrix

### 7.1 Unit and propagation tests

Run after each implementation tranche:

```bash
pytest scripts/test_local_msa_sharding.py -q
pytest scripts/test_run_local_msa.py scripts/test_batch_msa.py scripts/test_local_msa_package.py -q
TMPDIR=/mnt/BioModStack/tmp/hermes-pytemp /home/dalab/.local/bin/uv run --directory platform/api pytest tests/test_jobs_msa_batch_param_propagation.py tests/test_nextflow_msa_batch.py -q
python -m py_compile scripts/run_local_msa.py scripts/batch_msa.py scripts/lib/local_msa/sharding.py scripts/lib/local_msa/cli/run_single.py scripts/lib/local_msa/cli/run_batch.py scripts/lib/local_msa/types.py platform/api/services/nextflow.py platform/api/routers/jobs.py
```

### 7.2 Toy MMseqs integration test

Must verify:

```text
createdb query/target
splitdb target into N shards
search each shard
mergedbs shard results
result2msa against original target
unpackdb final A3M
assert hits from multiple shards are present
```

### 7.3 Real database smoke test

Use a clean output dir and either disable cache write or use isolated cache profile.

Example command shape:

```bash
python3 scripts/run_local_msa.py \
  --sequence '<SEQUENCE>' \
  --name sharded_envdb_smoke \
  --out_dir /mnt/BioModStack/tmp/msa-sharding-smoke \
  --db_path /mnt/BioModStack/colabfold_db \
  --cache_dir /mnt/BioModStack/msa_cache \
  --preset maximum \
  --use-env 1 \
  --threads 32 \
  --target-shard-mode required \
  --target-shard-stages envdb \
  --target-shard-count 4 \
  --target-shard-workers 4 \
  --target-shard-backend cpu \
  --force_refresh
```

Compare to:

```bash
python3 scripts/run_local_msa.py \
  --sequence '<SEQUENCE>' \
  --name unsharded_control \
  --out_dir /mnt/BioModStack/tmp/msa-sharding-control \
  --db_path /mnt/BioModStack/colabfold_db \
  --cache_dir /mnt/BioModStack/msa_cache \
  --preset maximum \
  --use-env 1 \
  --threads 32 \
  --target-shard-mode off \
  --force_refresh
```

`--force_refresh` alone may preserve older canonical cache behavior. For clean benchmarks, delete or isolate the relevant cache entry first instead of relying only on force refresh.

---

## 8. Rollout policy

### Stage A: Hidden experimental CLI

- Implement CLI-only sharding.
- No frontend controls.
- Default off only for the first implementation/benchmark tranche.
- Use `target_shard_mode=required` for benchmark evidence.

### Stage B: API/Nextflow pass-through

- Add backend param forwarding.
- Still no prominent UI control.
- Allow power users/operators to pass `msa_target_shard_*` params through API payloads or config.

### Stage C: Benchmark-gated UI exposure

Only after acceptance gates pass:

- Add an advanced local-MSA setting in the structure launcher.
- Label it experimental until a larger panel passes.
- Move local `balanced`/`maximum` EnvDB-backed jobs toward `target_shard_mode=auto` as the normal/default behavior, with a visible/advanced override to force `off`.

### Stage D: Production adaptive default

Enable adaptive/default-on sharding for local `balanced`/`maximum` EnvDB-backed jobs if:

- quality equivalence holds across the larger panel
- failure rate does not increase
- wall time materially improves
- shard-cache disk usage is bounded
- operator controls can disable it quickly

---

## 9. Non-goals

- No remote ColabFold API changes.
- No default increase from 32 to 48 MSA threads.
- No frontend UI exposure in Phase 1.
- No permanent unsharded default for high-quality local EnvDB jobs if sharding passes equivalence and performance gates.
- No gpuserver-per-shard implementation in Phase 1.
- No full rewrite of `run_colabfold_msa_workflow(...)` in this tranche.
- No Phase 2 sharding of `_seq`/`_aln` expansion/filter/result2msa until Phase 1 profiling proves it is needed.

---

## 10. Recommended first implementation sequence

1. Add `scripts/lib/local_msa/sharding.py` with policy resolver and tests.
2. Add splitdb shard-set creation/reuse with manifest and lock.
3. Add sharded search worker supervision and merge.
4. Add a toy integration test that proves split/search/merge/result2msa works in CI/local skip mode.
5. Add parser/request fields to `run_local_msa.py`, `types.py`, and `cli/run_single.py`.
6. Wire EnvDB search-only sharding into `run_colabfold_msa_workflow(...)` behind `target_shard_mode`.
7. Add quality-report metrics and fallback semantics.
8. Add `batch_msa.py` forwarding for high-quality per-sequence mode.
9. Add API/Nextflow pass-through and propagation tests.
10. Run real DB smoke tests with `required` mode, no cache poisoning, and compare against unsharded control.
11. Decide from metrics whether Phase 1 is useful enough or whether the long tail moved to expansion/filter/result2msa.
12. Only then consider Phase 2 downstream shard bundles or UI exposure.

---

## 11. Bottom line

The correct re-assessment is that higher-quality local MSA means EnvDB-backed `balanced`/`maximum`, and better single-job CPU utilization probably requires structural concurrency over target database shards. MMseqs already has the necessary primitives, and the toy proof validates the basic chain, but production integration must be cautious: keep `msa_threads=32` as the total budget, start with EnvDB search-only, avoid gpuserver shard complexity, isolate cache effects, and require quality/latency benchmarks before enabling it beyond an experimental path.
