# RepA Local MSA Root-Cause and Fix Spec

> **For Hermes:** Use `subagent-driven-development` only after this spec is accepted. The first implementation slice must add tests/instrumentation before changing defaults. Keep unrelated dirty worktree changes untouched.

**Goal:** Restore BioModStack local high-quality MSA correctness for RepA-like targets by making the local MMseqs workflow semantically match ColabFold's local monomer workflow, then reintroducing target-DB sharding only with equivalence gates.

**Architecture:** Treat official `colabfold_search` / `colabfold.mmseqs.search.mmseqs_search_monomer` as the correctness oracle. Split the fix into: (1) fail/instrument degraded local runs, (2) repair BioModStack's local workflow semantics, (3) rebuild sharding as a stage controller with global profile barriers, and (4) only then make sharded high-quality EnvDB adaptive/default-on again.

**Tech Stack:** Python, MMseqs2, ColabFold 1.5.5 local search source, BioModStack `scripts/run_local_msa.py`, `scripts/lib/local_msa/`, Nextflow/API/frontend propagation, pytest, real RepA MSA regression artifacts under `/mnt/BioModStack/tmp`.

---

## 1. Executive decision

The RepA local depth regression is real. `maximum + EnvDB + expand + filter + target_sharding=auto` produced a 28-sequence MSA, while the ColabFold API comparator produced 4911 sequences.

The answer is not to raise `msa_threads`. The answer is to fix local workflow correctness and only then optimize throughput/latency through sharding.

Important accountability note: target-DB sharding was implemented, but only for the EnvDB target `search` stage. That means the search was broken up, but the whole ColabFold MSA workflow was not yet made sharded/equivalent. The current sharded path is not safe to call a validated high-quality replacement.

---

## 2. Evidence

### 2.1 Local maximum RepA run

Artifact:

```text
/home/dalab/biomodstack/biomodstack/work/98/be4fc01874f6265891cc960b388f43/prep_complex_RepA_E._coli_P03066.log
/home/dalab/biomodstack/biomodstack/work/98/be4fc01874f6265891cc960b388f43/msa/RepA_E._coli_P03066_A.a3m
/home/dalab/biomodstack/biomodstack/work/98/be4fc01874f6265891cc960b388f43/msa/RepA_E._coli_P03066_A_msa_quality.json
```

Key log lines:

```text
MSA Preset: maximum
Searching UniRef30 (3 iterations)...
Found profile at iteration 1
Expanding alignments to recover cluster members...
Invalid alignment result record.
WARNING: Alignment expansion failed ..., continuing without expansion
Searching environmental database (colabfold_envdb_202108_db)...
Target DB sharding enabled for EnvDB: 4 shard(s) x 8 thread(s) (total budget 32).
Final MSA depth: 28 sequences
Cache preserve: kept existing canonical cache (old_depth=4911 > new_depth=28)
```

Quality JSON confirms:

```json
{
  "preset": "maximum",
  "msa_depth": 28,
  "use_env_effective": true,
  "use_expand": true,
  "use_filter": true,
  "target_sharding": {
    "enabled": true,
    "shard_count": 4,
    "threads_per_worker": 8,
    "search_was_sharded": true
  }
}
```

The final local A3M has:

```text
headers: 28
unique headers: 27
unique normalized sequences: 27
duplicate query records: 2
median non-query coverage: ~0.26
median non-query identity against query: extremely low
```

The headers are almost entirely `UniRef100_*` plus a duplicate query. EnvDB did not contribute a meaningful homolog set.

### 2.2 ColabFold API comparator

Artifact:

```text
/home/dalab/biomodstack/biomodstack/work/45/b7494c7fd39d4193ed140cf2e4e404/prep_complex_RepA_E._coli_P03066.log
/home/dalab/biomodstack/biomodstack/work/45/b7494c7fd39d4193ed140cf2e4e404/msa/RepA_E._coli_P03066_A.a3m
/home/dalab/biomodstack/biomodstack/work/45/b7494c7fd39d4193ed140cf2e4e404/msa/RepA_E._coli_P03066_A_msa_quality.json
```

Key log lines:

```text
MSA Provider: colabfold_api
ColabFold API ticket ... (mode=nofilter)
Final MSA depth: 4911 sequences
Saved to canonical cache ... depth=4911
```

Quality JSON confirms:

```json
{
  "provider": "colabfold_api",
  "api_mode": "nofilter",
  "preset": "fast",
  "use_env_effective": false,
  "use_filter": false,
  "msa_depth": 4911
}
```

The canonical cache object is byte-identical to the API comparator A3M:

```text
/mnt/BioModStack/msa_cache/c4/c4aaabaad31ece761b9ad350017cd0cc5ea1820e3f48a66bb61e3dee1c8f6502.a3m.gz
```

Uncompressed SHA256:

```text
075ebffb98306ba7198c35ada2b7cdcb1e6f37590f3b75a032e8b77452d3e182
```

The API A3M has:

```text
headers: 4911
unique normalized sequences: 4821
median non-query coverage: ~0.95
median non-query identity: ~0.72
```

Exact normalized-sequence overlap between local maximum and API comparator is essentially only the query.

### 2.3 Local UniRef nofilter/noexpand CPU API-like probe

Artifact:

```text
/mnt/BioModStack/msa_validation/repa_corrected_20260424T195500Z/uniref_nofilter_noexpand_cpu_run.log
/mnt/BioModStack/msa_validation/repa_corrected_20260424T195500Z/UNIREF_NOEXPAND_CPU_METADATA.txt
/mnt/BioModStack/msa_validation/repa_corrected_20260424T195500Z/uniref_nofilter_noexpand_cpu_api_like/RepA_E_coli_P03066_uniref_nofilter_noexpand_cpu_api_like.a3m
/mnt/BioModStack/msa_validation/repa_corrected_20260424T195500Z/uniref_nofilter_noexpand_cpu_api_like/RepA_E_coli_P03066_uniref_nofilter_noexpand_cpu_api_like_msa_quality.json
```

Command shape:

```text
preset=fast
use_env=0
use_filter=0
use_expand=0
num_iterations=3
sensitivity=8.0
evalue=0.1
max_seqs=10000
cpu_only=true
gpuserver_mode=off
target_shard_mode=off
threads=32
```

Result:

```text
exit_code=0
Final MSA depth: 28 sequences
headers in A3M: 28
cache_profile: fast_70e69137b314
use_env_effective: false
used_gpu_mmseqs: false
use_expand: false
use_filter: false
```

Interpretation: this isolates a local raw UniRef/no-filter/no-expand path and still returns only 28 sequences. It confirms the shallow local result is not caused by GPU/gpuserver execution or filtering. It does **not** validate the high-quality path because expansion and EnvDB are intentionally disabled; it is a degraded diagnostic comparator, not a fix.

### 2.4 Reassessment: active UniRef alignment DB is internally inconsistent

Artifacts:

```text
/mnt/BioModStack/msa_validation/repa_official_probe/diagnostic_expandaln_matrix_20260424T210456Z/summary.txt
/mnt/BioModStack/msa_validation/repa_official_probe/diagnostic_expandaln_backup_aln_20260424T212148Z/summary.txt
/mnt/BioModStack/msa_validation/repa_official_probe/diagnostic_backup_aln_downstream_20260424T212440Z/uniref_backup_aln.a3m
/mnt/BioModStack/msa_validation/repa_official_probe/diagnostic_backup_aln_downstream_nofilter_20260424T212528Z/uniref_backup_aln_nofilter.a3m
```

Direct `expandaln` matrix against the active DB failed in every tested mode:

```text
cpu + .idx/.idx:        exit=1, Missing alignments..., Invalid alignment result record.
cpu + _seq/_aln:        exit=1, Missing alignments..., Invalid alignment result record.
blackwell + .idx/.idx:  exit=1, Missing alignments..., Invalid alignment result record.
blackwell + _seq/_aln:  exit=1, Missing alignments..., Invalid alignment result record.
```

The missing sequence IDs are present in the base target DB and sequence DB but absent from the active alignment DB index:

```text
example IDs: 12459233, 13194497, 8127574, 11947269, 8538011, 8528124, 8826086, 4014312, 7698723, 8196074, 9228281, 8991744, 8875490
uniref30_2302_db.index:        all present
uniref30_2302_db_seq.index:    all present
uniref30_2302_db_aln.index:    all absent
backup_aln_corrupted/...index: all present
```

Index-shape check:

```text
uniref30_2302_db.index: contiguous ids 0..36,293,490
uniref30_2302_db_seq.index: contains the same queried ids
active uniref30_2302_db_aln.index: 36,293,491 rows, max id 350,950,052, 25,612,435 gaps
backup_aln_corrupted/uniref30_2302_db_aln.index: contiguous ids 0..36,293,490
```

The active `_aln` DB therefore appears to have been remapped onto a non-target-ID keyspace while the base UniRef target DB still uses the original contiguous MMseqs IDs. That exactly matches `expandaln`'s complaint: search results reference target IDs that exist in the target/seq DBs, but the alignment DB cannot provide cluster alignments for those same IDs.

Read-only proof-of-hypothesis: rerunning `expandaln` against the preserved contiguous backup `_aln` DB succeeds with both MMseqs binaries:

```text
cpu_backup_aln exit=0        Time for processing: 11.508s
blackwell_backup_aln exit=0  Time for processing: 4.417s
```

Continuing the official downstream UniRef steps with the backup `_aln` DB also succeeds without missing/invalid alignment errors:

```text
filtered UniRef downstream A3M:   56 headers
nofilter UniRef downstream A3M:   78 headers
```

This does not close the API/canonical 4911-depth gap by itself, because that gap is not an apples-to-apples local UniRef-only comparison. It does prove the local active UniRef expansion substrate is broken and must be repaired before any local high-quality EnvDB or sharding conclusion is trusted.

---

## 3. Root cause

### Root cause A: local high-quality workflow is not ColabFold-equivalent

Official local ColabFold monomer workflow lives at:

```text
/home/dalab/.local/lib/python3.10/site-packages/colabfold/mmseqs/search.py
```

Important official semantics:

```text
lines 83-88: search_param includes --num-iterations 3, -a, -e 0.1, --max-seqs 10000, and -s 8.0 or --k-score
lines 91-102: UniRef search -> mvdb tmp/latest/profile_1 -> expandaln -> align -> filterresult -> result2msa --msa-format-mode 6
lines 117-133: EnvDB search -> expandaln -> align with tmp3/latest/profile_1 -> filterresult -> result2msa --msa-format-mode 6
lines 53-58: when filter=true, qsc becomes 0.8 and max_accept becomes 100000
lines 96-99 and 126-129: filterresult uses --max-seq-id 1.0
```

BioModStack diverges in `scripts/run_local_msa.py`:

```text
lines 2700-2762: GPU/gpuserver search paths do not pass -s 8.0 or the official --k-score fallback, even though the quality report says sensitivity=8.0
lines 2817-2845: UniRef expandaln failure is caught and the job continues without expansion
lines 2853-2860 and 3129-3136: filterresult uses max_seq_id from config, currently 0.95, rather than official 1.0
lines 2877-2883 and 3143-3149: result2msa uses --msa-format-mode 5 rather than official monomer mode 6
lines 3107-3112: EnvDB realignment looks for tmp_env/latest/profile_{num_iterations}; official uses tmp3/latest/profile_1
```

Impact: the local run is not actually executing the same high-quality ColabFold workflow. The `maximum` label is therefore misleading until these semantics are repaired.

### Root cause B: alignment expansion failed, and the job silently accepted a degraded MSA

The local maximum run explicitly hit:

```text
Invalid alignment result record.
WARNING: Alignment expansion failed ..., continuing without expansion
```

For a high-quality MSA preset, this should not be silently accepted. Expansion is one of the core steps that recovers cluster members. Continuing after expansion failure collapses depth and hides the real failure from the launcher.

Impact: very high. This alone can explain a tiny local MSA.

### Root cause B2: active UniRef `_aln` DB was rebuilt into the wrong keyspace

The direct DB inspection changes the diagnosis from generic “expandaln failed” to a concrete substrate mismatch:

```text
search result IDs -> exist in uniref30_2302_db.index and uniref30_2302_db_seq.index
same IDs -> absent from active uniref30_2302_db_aln.index
same IDs -> present in preserved backup_aln_corrupted/uniref30_2302_db_aln.index
```

The active `_aln.index` has the right row count but the wrong key domain: it has 36,293,491 rows but reaches ID 350,950,052 with millions of gaps. That is not equivalent to the contiguous base target DB keyspace 0..36,293,490. The likely culprit is the GPU-specific remapping logic in the local DB rebuild scripts:

```text
awk 'NR == FNR { f[$3] = $1; next; } { $1 = f[$1]; print }' ${OUT}.lookup ${OUT}_reorder_aln.tsv | sort -s -k1,1n > ${OUT}_mapped_aln.tsv
```

That remap may have been appropriate for a different TSV key convention, but the active DB now breaks the contract required by `expandaln`: the alignment DB must be keyed compatibly with the target/search result DB.

Operational impact: do not rebuild, swap, or bless any UniRef `_aln` artifact until a validator proves:

```text
active target index IDs are contiguous and match _seq/_aln keyspace
RepA direct expandaln succeeds for both .idx and _seq/_aln modes, or .idx is regenerated from the repaired sibling DBs
official local ColabFold monomer control completes without missing-alignment warnings
```

### Root cause C: EnvDB sharding was implemented, but it is search-only and not yet equivalence-safe

Implemented code:

```text
scripts/lib/local_msa/sharding.py
scripts/run_local_msa.py:2969-3019
```

What it does:

```text
splitdb EnvDB into N target shards
run one mmseqs search per shard
mergedbs shard result DBs into res_env
continue downstream EnvDB expand/filter/result2msa against the original EnvDB sibling DBs
```

What it does not do:

```text
no global profile barrier across shard iterations
no merged global EnvDB profile_1 for official align semantics
no real MMseqs equivalence test against unsharded output
no per-stage metrics proving EnvDB contributed hits
no cache isolation for experimental sharded outputs
no fallback_used / fallback_error recorded in quality JSON
```

Specific risk:

```text
scripts/run_local_msa.py:2964 passes --num-iterations from the preset into each per-shard search
scripts/lib/local_msa/sharding.py:398-409 runs each shard independently
```

That means iterative profile updates can be shard-local, not global. Official ColabFold's profile updates are global. For a correctness-sensitive high-quality MSA, this is not a proven equivalent transformation.

Impact: high. It explains why “we broke it up” still did not guarantee high-quality output.

### Root cause D: the 4911 comparator is API nofilter, not local maximum filtered

The 4911 cache came from:

```text
provider: colabfold_api
api_mode: nofilter
preset: fast
use_env_effective: false
use_filter: false
```

That means it is not a perfect apples-to-apples proof that local `maximum + filter + EnvDB` should equal exactly 4911. But it is still valid evidence that the local run failed badly: the local high-quality MSA is shallow, low-coverage, and biologically unlike the API homolog set.

---

## 4. Fix strategy

### Principle 1: correctness oracle first

Before optimizing or sharding, create a local correctness oracle:

```text
official colabfold_search local monomer run
exact same 285-aa RepA sequence
same DB path
--use-env 1
--filter 1
--threads 32
--db-load-mode 2
preserve all raw outputs and logs
```

This establishes what local DB + official ColabFold semantics can produce on this machine.

### Principle 2: no degraded high-quality success

For `preset=maximum` and `preset=balanced`:

```text
expandaln failure must be fatal unless an explicit --allow-degraded-quality flag is set
EnvDB enabled but EnvDB contributing <= query-only must fail or downgrade visibly
final depth below configured quality floor must fail when min_depth_fail is set by product policy
```

### Principle 3: sharding must preserve stage semantics

Search-only sharding is not enough for an iterative/profile workflow.

Sharded EnvDB must become a stage controller:

```text
current_query = UniRef global profile DB
for iteration in 1..N:
    run shard searches with --num-iterations 1 against current_query
    merged_result_i = mergedbs(current_query, shard_result_i...)
    global_profile_i = result2profile(current_query_or_original_query, original_envdb, merged_result_i)
    current_query = global_profile_i

save global_profile_1 for official EnvDB align step
run expandaln/align/filterresult/result2msa against original EnvDB sibling DBs
```

This keeps global profile barriers between iterations instead of letting each shard build a private profile universe.

---

## 5. Implementation plan

### PR -1: Repair and validate the local UniRef alignment substrate

**Objective:** Fix the concrete active `_aln` DB/keyspace mismatch before interpreting any local high-quality MSA result.

**Files/artifacts:**

```text
Create: scripts/validate_colabfold_db_integrity.py
Create or update: docs/reports/2026-04-24-repa-local-db-integrity.md
Repair artifact: /mnt/BioModStack/colabfold_db/uniref30_2302_db_aln* only after validator passes on a staged copy
Regenerate: /mnt/BioModStack/colabfold_db/uniref30_2302_db.idx* only from the repaired sibling DB set, or force `_seq/_aln` mode until idx is proven equivalent
```

**Required validator checks:**

```text
1. For each DB family, parse *.index IDs and report count/min/max/gap count.
2. Assert UniRef target, _seq, and _aln keyspaces are compatible for active high-quality expansion.
3. Given a preserved qdb/res pair, run direct expandaln in a small matrix:
   - CPU .idx/.idx
   - CPU _seq/_aln
   - Blackwell .idx/.idx
   - Blackwell _seq/_aln
4. Fail if logs contain "Missing alignments for sequence" or "Invalid alignment result record".
5. Run downstream align/filterresult/result2msa on the RepA control and record depth.
```

**Repair rule:**

```text
Do not blindly promote backup_aln_corrupted despite the name. It is currently a proof artifact because it has the contiguous keyspace and makes expandaln pass. The safe repair is either:
- rebuild _aln from original TSV without the bad GPU remap, preserving target IDs, then regenerate .idx; or
- stage a read-only overlay using the backup _aln, run full official local UniRef+EnvDB controls, then promote only if checks pass and the artifact lineage is documented.
```

**Acceptance:**

```text
active UniRef _aln.index keyspace matches active target/seq keyspace
RepA direct expandaln no longer emits missing alignments/invalid record
filtered and nofilter UniRef downstream controls are non-query and reproducible
full official local `--use-env 1 --threads 32` completes or fails for a new reason unrelated to UniRef _aln integrity
```

### PR 0: Add forensic instrumentation and stop accepting degraded quality

**Objective:** Make the failure impossible to hide.

**Files:**

```text
Modify: scripts/run_local_msa.py
Modify: scripts/test_run_local_msa.py
Possibly modify: scripts/lib/local_msa/types.py
```

**Changes:**

1. Add quality-report fields:

```json
{
  "stages": {
    "uniref_search": {"ran": true, "error": null},
    "uniref_expand": {"ran": true, "error": "Invalid alignment result record", "fatal": true},
    "uniref_a3m_depth": null,
    "env_search": {"ran": true, "sharded": true, "backend": "cpu"},
    "env_a3m_depth": null,
    "final_a3m_depth": 28
  },
  "degraded_quality": true,
  "degraded_reasons": ["uniref_expandaln_failed", "envdb_no_nonquery_hits"]
}
```

2. Add `--allow-degraded-quality` default false for `balanced`/`maximum`.

3. Add `--keep-temp-on-failure` and `--keep-temp` so failed full-DB reproductions leave MMseqs result DBs for inspection.

4. Make `Invalid alignment result record` fatal in `maximum` unless `--allow-degraded-quality` is set.

**Tests:**

```text
pytest scripts/test_run_local_msa.py -q
```

Add mocked tests asserting:

```text
maximum + expandaln RuntimeError -> job fails by default
maximum + expandaln RuntimeError + allow_degraded_quality -> report has degraded_quality=true
balanced/maximum + EnvDB query-only A3M -> degraded reason recorded
quality JSON includes target_sharding fallback/error fields
```

### PR 1: Repair local workflow semantics to match official ColabFold

**Objective:** Make BioModStack's unsharded local `maximum` path equivalent to official local ColabFold monomer semantics before sharding.

**Files:**

```text
Modify: scripts/run_local_msa.py
Create or modify: scripts/lib/local_msa/colabfold_workflow.py
Modify: scripts/test_run_local_msa.py
Create: scripts/test_colabfold_local_semantics.py
```

**Required changes:**

1. Search parameters:

```text
Every UniRef/EnvDB search path, including GPU/gpuserver/direct GPU/CPU/sharded, must include either:
-s <effective_sensitivity>
or official --k-score fallback
```

2. Official filter semantics:

```text
if use_filter:
    qsc for filterresult = 0.8
    max_accept = 100000
filterresult --max-seq-id = 1.0
result2msa filter_param --max-seq-id = 0.95
```

3. Official profile semantics:

```text
UniRef profile source: tmp/latest/profile_1, or a derived prof_res that is explicitly equivalent
EnvDB alignment profile source: tmp_env/latest/profile_1 for unsharded, or global_env_profile_1 for sharded controller
```

4. Official result2msa format:

```text
monomer unpaired result2msa uses --msa-format-mode 6
paired/multimer path can keep mode 5 where appropriate
```

5. Add a `--local-workflow official` path or internal official-compatible controller.

Preferred implementation:

```text
scripts/lib/local_msa/colabfold_workflow.py owns command construction
scripts/run_local_msa.py becomes a thin adapter over that controller
```

**Tests:**

Mock `run_mmseqs` and assert command vectors include:

```text
-s 8.0 on GPU/gpuserver searches
filterresult --qsc 0.8 when filter=true
filterresult --max-seq-id 1.0
result2msa --msa-format-mode 6
EnvDB align uses profile_1, not profile_3
```

### PR 2: Establish real local correctness control

**Objective:** Produce a durable benchmark packet for exact RepA.

**Files:**

```text
Create: scripts/reproduce_repa_local_msa_controls.py
Create: docs/plans or docs/reports/2026-04-24-repa-local-msa-control-results.md
```

**Commands to run:**

Use the exact canonical 285-aa query from the API/local workdirs, not the earlier 278-aa mistyped FASTA.

```bash
colabfold_search \
  /mnt/BioModStack/tmp/repa_285.fasta \
  /mnt/BioModStack/colabfold_db \
  /mnt/BioModStack/tmp/repa_official_colabfold_local_filter_env \
  --mmseqs /mnt/BioModStack/colabfold_db/mmseqs/bin/mmseqs \
  --use-env 1 \
  --filter 1 \
  --threads 32 \
  --db-load-mode 2
```

Then run BioModStack repaired unsharded:

```bash
PYTHONPATH=scripts:scripts/lib:. python scripts/run_local_msa.py \
  --sequence '<exact 285-aa RepA>' \
  --name RepA_repaired_unsharded_maximum \
  --out_dir /mnt/BioModStack/tmp/repa_repaired_unsharded_maximum \
  --db_path /mnt/BioModStack/colabfold_db \
  --cache_dir /mnt/BioModStack/tmp/repa_cache_isolated \
  --preset maximum \
  --use-env 1 \
  --use-expand 1 \
  --use-filter 1 \
  --target-shard-mode off \
  --threads 32 \
  --force_refresh \
  --keep-temp
```

Acceptance:

```text
BioModStack repaired unsharded must not silently degrade.
If official local succeeds, repaired BioModStack must match official local depth/header overlap within a defined tolerance.
If official local also fails/shallow, the problem is local DB/search binary/data parity vs ColabFold API, not BioModStack sharding.
```

### PR 3: Replace search-only sharding with an equivalence-safe stage controller

**Objective:** Actually break up the search while preserving global profile semantics.

**Files:**

```text
Modify: scripts/lib/local_msa/sharding.py
Create: scripts/lib/local_msa/sharded_workflow.py
Modify: scripts/run_local_msa.py
Modify: scripts/test_local_msa_sharding.py
Create: scripts/test_local_msa_sharded_workflow.py
```

**Required changes:**

1. Add sharded iterative search helper:

```python
def run_sharded_iterative_target_search(
    *,
    query_db: Path,
    target_db: Path,
    target_seq_db: Path,
    initial_profile_db: Path,
    result_db: Path,
    profile_out_dir: Path,
    iterations: int,
    shards: Sequence[Path],
    threads_per_worker: int,
    max_parallel_workers: int,
    search_params_without_iterations: Sequence[str],
) -> ShardedIterativeSearchResult:
    ...
```

2. Per iteration:

```text
run per-shard search with --num-iterations 1
mergedbs shard results into global iteration result
result2profile against original target DB to build global profile_i
use global profile_i as next iteration query
```

3. Save `global_profile_1` for the EnvDB `align` step.

4. Record per-shard/per-iteration timings and result DB presence in quality JSON.

5. Use cache profile isolation until parity is proven:

```text
maximum_sharded_envdb_v2_<policyhash>
balanced_sharded_envdb_v2_<policyhash>
```

6. Add `fallback_used` and `fallback_error` fields.

**Tests:**

Unit tests with fake MMseqs must assert command sequence:

```text
splitdb
search shard 0 --num-iterations 1
search shard 1 --num-iterations 1
mergedbs
result2profile
search shard 0 --num-iterations 1 using global profile_1
...
```

Integration test with tiny DB:

```text
createdb tiny target/query
splitdb target into 2 shards
run sharded iterative helper
result2msa against original target
assert hits from both shards are present
```

### PR 4: Re-enable default adaptive sharding only after parity gates pass

**Objective:** Make high-quality local MSA fast without regressing quality.

**Files:**

```text
Modify: nextflow.config
Modify: modules/structure_prediction.nf
Modify: platform/api/services/nextflow.py
Modify: platform/api/routers/jobs.py
Modify: frontend MSA controls if needed
Modify: regression tests already covering propagation
```

**Default target after parity:**

```text
msa_threads = 32
msa_target_shard_mode = 'auto'
msa_target_shards = 4
msa_target_shard_min_size_gb = 1.0
```

**Gate before default-on:**

Run exact RepA and at least two other representative proteins:

```text
official local unsharded
BioModStack repaired unsharded
BioModStack sharded 4x8
```

Acceptance metrics:

```text
No silent degraded-quality reports
No expandaln failure in accepted high-quality output
EnvDB contributes non-query records when use_env=true
Sharded depth >= 95% of repaired unsharded depth, or explicit investigated reason
Top/header or sequence overlap >= defined threshold for same DB/settings
Boltz YAML consumes the intended A3M path, not a discarded shallow refresh artifact
Canonical cache profile separates API/no-filter, local unsharded, and sharded experimental outputs
```

---

## 6. Immediate operational workaround

Until this is fixed:

1. Do not trust local `maximum` RepA output if the log contains:

```text
Invalid alignment result record
continuing without expansion
Final MSA depth: 28
```

2. For production-ish RepA structure attempts, use one of:

```text
ColabFold API nofilter A3M already in canonical cache
or official local colabfold_search output once exact 285-aa run completes successfully
```

3. Keep `target_shard_mode=off` as the control path for local correctness comparisons.

4. Do not overwrite canonical API/no-filter cache with experimental sharded outputs.

---

## 7. Definition of done

This incident is fixed only when all of the following are true:

```text
RepA local high-quality run no longer returns a 28-sequence degraded A3M as success
quality JSON explains per-stage MSA depth and EnvDB contribution
BioModStack unsharded local run matches official local ColabFold semantics
sharded local run is tested against unsharded local output for depth/content parity
sharding implementation uses global profile barriers, not independent shard-local iterative searches
API/no-filter cache and local high-quality cache profiles are not conflated
Nextflow/API/frontend surfaces pass the corrected knobs without stale defaults
```

---

## 8. Current priority order

1. Repair/validate the UniRef `_aln` substrate first: active `_aln.index` is in the wrong keyspace and direct `expandaln` fails; backup contiguous `_aln` makes `expandaln` pass.
2. Add degraded-quality failure and `--keep-temp` so high-quality runs cannot silently accept query-only/shallow output.
3. Repair search/filter/profile/result2msa semantics against official ColabFold source.
4. Run exact RepA 285-aa official local control and repaired BioModStack unsharded control with EnvDB enabled under `threads=32`.
5. Replace EnvDB search-only sharding with global-barrier sharded workflow.
6. Re-enable adaptive/default-on sharding after parity gates pass.
