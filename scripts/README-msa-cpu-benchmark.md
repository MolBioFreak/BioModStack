# Local synthetic CPU alignment smoke benchmark

Run from the repository root (Apptainer and GNU `/usr/bin/time` required):

```sh
python scripts/msa_cpu_benchmark.py --image "$TMPDIR/msa-engines.sif" \
  --out "$TMPDIR/msa-benchmark-run-01" --threads 1 --repeats 2 --timeout 60
python -m pytest -q tests/test_msa_cpu_benchmark.py
```

The output directory must not exist; it contains artificial FASTA inputs, native outputs/logs, GNU time files, and incremental JSON. The script sends no data to a network service. Image hash, executable paths/hashes/version/help excerpts, platform, requested threads, environment, exact argv, fixture hashes/lengths, raw process outcome, wall/user/system times, maximum RSS and exact symbol-preserving degap observations are recorded. GNU time measures the Apptainer invocation and its children; RSS is not an aggregate simultaneous process-tree peak. Image startup dominates tiny workloads. First versus subsequent repeats describe invocation order per fixture/engine, not a controlled OS cold-cache experiment. Shared host load and actual scheduling are not controlled; thread count is requested through engine flags and common environment variables, not independently measured.

Comparisons are only within the same molecule/fixture; FAMSA is protein-only here. Identical synthetic strings and gap observations are not biological truth or scientific accuracy. Case or other engine transformations can appear as mismatches and are retained without modifying output or status. Failures and timeouts are reported separately and are not replaced by successful results. TWILIGHT/tree preparation, MUSCLE Super5/ensembles, other strategies, RNA, real-family data, independent-reference SP/TC accuracy, viewer measurements, and integrated acceptance remain explicitly incomplete. Existing DRT results are not imported or transmitted. This harness does not claim WP7/AC16/AC17 completion.
