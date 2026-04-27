# RepA local MSA `expandaln` root cause and fix evidence

## Scope

This report covers the high-quality local MSA failure where RepA/P03066 runs hit MMseqs2:

```text
Missing alignments for sequence ...
Invalid alignment result record.
```

The product-level requirement is that `balanced`/`maximum` local MSA runs must not silently degrade to shallow UniRef-only or no-expansion output unless `--allow-degraded-quality` is explicitly set.

## Root cause summary

The original failure was not caused by thread count, filtering alone, or GPU-vs-CPU execution. Direct `expandaln` probes showed that search hits referenced target IDs present in the target DB, while the active UniRef alignment DB could not provide corresponding alignment records. That is the precise substrate mismatch that makes `expandaln` fail with `Invalid alignment result record`.

Earlier forensic artifacts are documented in:

```text
docs/plans/2026-04-24-repa-local-msa-root-cause-and-fix-spec.md
/mnt/BioModStack/msa_validation/repa_official_probe/diagnostic_expandaln_matrix_20260424T210456Z/summary.txt
/mnt/BioModStack/msa_validation/repa_official_probe/diagnostic_expandaln_backup_aln_20260424T212148Z/summary.txt
```

Key conclusion from those artifacts: a preserved contiguous backup `_aln` substrate made direct `expandaln` succeed, while the then-active `_aln` substrate failed. That identifies DB keyspace/lineage mismatch as the concrete `expandaln` root cause.

## Runtime fixes now in the code path

`run_local_msa.py` now refuses degraded high-quality local MSA by default:

1. Missing EnvDB prefix/dbtype is fatal for `balanced`/`maximum` unless `--allow-degraded-quality` is set.
2. Missing or keyspace-invalid UniRef `_aln` is fatal for `balanced`/`maximum` unless `--allow-degraded-quality` is set.
3. UniRef `expandaln` failure is fatal for `balanced`/`maximum` unless `--allow-degraded-quality` is set.
4. EnvDB `_aln` keyspace failure is fatal for `balanced`/`maximum` unless `--allow-degraded-quality` is set.
5. High-quality command semantics were aligned closer to official local ColabFold monomer behavior: effective `-s`, filtered `--qsc 0.8`, filtered `--max-seq-id 1.0`, and `result2msa --msa-format-mode 6`.
6. EnvDB sharding now uses MMseqs native target splitting rather than manual splitdb + independent shard-local iterative searches.

Regression coverage:

```text
PYTHONPATH=scripts:scripts/lib:. python3 -m pytest scripts/test_local_msa_db_integrity.py scripts/test_run_local_msa.py scripts/test_validate_colabfold_db_integrity.py -q
# 19 passed in 0.23s, re-run 2026-04-25
```

## Forensic validator added

New validator:

```text
scripts/validate_colabfold_db_integrity.py
```

Library support:

```text
scripts/lib/local_msa/db_integrity.py
```

Regression tests:

```text
scripts/test_validate_colabfold_db_integrity.py
scripts/test_local_msa_db_integrity.py
```

The validator performs streaming index scans and reports count/min/max/gap shape for target, `_seq`, and `_aln` DB siblings. Important nuance: for UniRef and EnvDB, target and `_aln.index` must share the target-hit keyspace used by `expandaln`; `_seq.index` can legitimately be a much larger contiguous cluster-member sequence DB, so the validator checks `_seq` readiness/coverage rather than exact equality to target.

## Current live DB integrity evidence

Corrected validator output shows the active DB substrate is now compatible.

UniRef live artifact:

```text
/mnt/BioModStack/msa_validation/repa_official_probe/live_uniref_db_integrity_20260425T043317Z_corrected-validator.json
```

Summary:

```text
uniref30_2302_db.index:      count=36,293,491 min=0 max=36,293,490 gaps=0
uniref30_2302_db_aln.index:  count=36,293,491 min=0 max=36,293,490 gaps=0
uniref30_2302_db_seq.index:  count=350,950,053 min=0 max=350,950,052 gaps=0
compatible=true
```

EnvDB live artifact:

```text
/mnt/BioModStack/msa_validation/repa_official_probe/live_envdb_integrity_20260425T043443Z_corrected-validator.json
```

Summary:

```text
colabfold_envdb_202108_db.index:      count=209,335,862 min=0 max=209,335,861 gaps=0
colabfold_envdb_202108_db_aln.index:  count=209,335,862 min=0 max=209,335,861 gaps=0
colabfold_envdb_202108_db_seq.index:  count=738,695,581 min=0 max=738,695,580 gaps=0
compatible=true
```

## Completed end-to-end validation

A real RepA/P03066 `maximum` local MSA validation completed with degraded quality disallowed and a depth floor so a shallow degraded result could not pass:

```text
/mnt/BioModStack/msa_validation/repa_expandaln_fix_validation_20260425T043703Z/RUN_METADATA.txt
/mnt/BioModStack/msa_validation/repa_expandaln_fix_validation_20260425T043703Z/run.log
/mnt/BioModStack/msa_validation/repa_expandaln_fix_validation_20260425T043703Z/.exitcode
/mnt/BioModStack/msa_validation/repa_expandaln_fix_validation_20260425T043703Z/RepA_E_coli_P03066_local_maximum_fix_validation_msa_quality.json
/mnt/BioModStack/msa_validation/repa_expandaln_fix_validation_20260425T043703Z/RepA_E_coli_P03066_local_maximum_fix_validation.a3m
```

Command shape:

```text
PYTHONPATH=scripts:scripts/lib:. python scripts/run_local_msa.py \
  --sequence <RepA_P03066_285aa> \
  --name RepA_E_coli_P03066_local_maximum_fix_validation \
  --out_dir /mnt/BioModStack/msa_validation/repa_expandaln_fix_validation_20260425T043703Z \
  --db_path /mnt/BioModStack/colabfold_db \
  --cache_dir /mnt/BioModStack/msa_validation/repa_expandaln_fix_validation_20260425T043703Z/cache \
  --force_refresh \
  --threads 32 \
  --cpu-only \
  --preset maximum \
  --target-shard-mode required \
  --target-shards 4 \
  --target-shard-min-size-gb 0 \
  --min-depth-fail 50
```

Observed result:

```text
.exitcode: 0
Final MSA depth: 167 sequences
A3M header count: 167
canonical cache depth: 167
preset: maximum
use_env_effective: true
allow_degraded_quality: false
degraded_quality: false
auto_env_fallback_triggered: false
target_sharding.implementation: mmseqs_native_search_split
target_sharding.search_was_sharded: true
target_sharding.shard_count: 4
target_sharding.total_threads: 32
target_sharding.threads_per_worker: 8
```

Acceptance criteria status:

1. Exit code 0: passed.
2. No `Invalid alignment result record` in `run.log`: passed.
3. No `refusing degraded high-quality local MSA` in `run.log`: passed.
4. Quality JSON reports `preset=maximum`: passed.
5. Quality JSON reports `use_env_effective=true`: passed.
6. Quality JSON reports `degraded_quality=false`: passed.
7. Quality JSON reports `target_sharding.implementation=mmseqs_native_search_split`: passed.
8. Final depth is at least 50 by `--min-depth-fail 50`: passed with depth 167.

## Still not claimed

This does not claim the local maximum MSA is byte-identical or depth-identical to the remote ColabFold API nofilter comparator. The remote comparator used different API/database/filter semantics. The claim here is narrower and evidence-backed:

```text
The concrete local MMseqs `expandaln` invalid-record substrate is detectable and now validated; high-quality local runs no longer silently degrade past expansion/EnvDB failures; active DB keyspaces currently pass the validator; the end-to-end RepA maximum validation completed successfully with EnvDB enabled, native target splitting, degraded fallback disabled, and depth 167.
```
