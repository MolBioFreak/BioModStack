# Phase 2 restriction-analysis benchmark and binding limits

Re-run on 2026-08-31 with CPython 3.12 / Biopython 1.87 on Linux x86_64, AMD Ryzen Threadripper 9960X. The benchmark driver remained under `/tmp`; no generated benchmark artifact is committed. Every row ran in a fresh process against the pinned catalog with a linear molecule and RFC 8785 serialization. Peak memory is process maximum RSS (`ru_maxrss`).

The scanner-job inventory was derived from the current catalog bytes, not from `str(enzyme.site)`: the complete `all_analysis_capable` scope has exactly **1,056** deduplicated forward plus distinct reverse-complement scanner jobs; `all_geometry_ready` has exactly **408**. Palindromic reverse jobs and duplicate grouped-pattern consumers do not add scanner jobs.

| Fixture | DNA bp | Actual scan jobs | Charged work | Wall s | Peak RSS KiB | Occurrences | Events | JCS result bytes | Disposition |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| largest admitted explicit `BsaI`; repeated `GGTCTA` forces full-width forward near-misses | 2,666,666 | 2 | 31,999,932 | 1.409210 | 74,392 | 0 | 0 | 2,055 | complete |
| largest admitted full geometry-ready scope; no-site `A` source | 11,845 | 408 | 31,999,699 | 1.032891 | 69,264 | 0 | 0 | 200,274 | complete |
| largest admitted full analysis-capable scope; no-site `A` source | 4,452 | 1,056 | 31,994,922 | 1.026062 | 69,776 | 0 | 0 | 287,001 | complete |
| two-event `BcgI` | 54 | 2 | 1,032 | 0.000654 | 69,748 | 1 | 2 | 4,794 | complete |
| ambiguous `N` source with explicit `EcoRI` | 5,000 | 1 | 29,970 | 0.640293 | 109,352 | 4,995 possible | 4,995 | 8,111,844 | complete; retained weight 12,663,084 bytes, so cache bypassed |

All rows carried resource-policy SHA-256 `94d0ab410dec1f2510e3b13f0434cc1561ec133f8c042e4e8c76ec32ba647e64`.

## Selected limits and exact semantics

- inline sequence: **5,000,000 bp** (subject independently to charged scan work);
- explicit enzymes: **256**;
- nonoverlapping regions: **128**;
- actual scanner jobs: **1,056**, exactly the catalog-wide deduplicated forward plus distinct reverse-complement set;
- charged scan work: **32,000,000 motif comparisons** under `candidate-starts-times-motif-width` version `1.0.0`;
- returned occurrences: **25,000**;
- returned cleavage events: **50,000**;
- encoded response: **32 MiB**;
- CPU worker concurrency: **2**, with no queue (`reject_when_all_workers_busy`) and a **60 s** request wait timeout;
- cache: **32 entries**, **64 MiB** total retained-object weight, and **8 MiB** per result.

For each admitted scanner job with motif width `m` and molecule length `L`, charged work is `candidate_starts × m`. Linear candidate starts are `max(L-m+1, 0)`. Circular jobs use `L` starts when `m <= L`; when `m > L`, they charge zero scan work and produce the typed `recognition_motif_longer_than_molecule` limitation without calling the scanner. Admission and execution consume the same immutable deduplicated job plan.

The former nominal 100,000,000 source-bp × forward-pattern policy was removed: it omitted reverse jobs and motif width and admitted a 24.45 s geometry-wide boundary. The 32,000,000 comparison bound preserves a multi-megabase explicit Type IIS lane while bringing both catalog-wide measured boundaries to about one second on the recorded host.

The worker lane is process-wide and fixed at two threads. A request is rejected with `analysis_busy` when both workers are occupied. Timeout or caller cancellation does not cancel a running Python worker and does not release its capacity; capacity is released only by the worker future's completion callback.

Cache weight uses `canonical-json-entry-and-complete-cache-graph` version `2.0.0`. Each entry retains an immutable canonical JSON byte representation and its exact cache key; the per-entry limit counts that complete entry graph once by identity. The total limit independently counts the complete `OrderedDict` graph, including keys, tuples, strings, values, entry objects, and container overhead, once by identity after insertion and each eviction. Hits strictly reconstruct a fresh `AnalysisResult`. LRU eviction enforces entry count and total weight together, and a result above the per-entry threshold bypasses caching. The complete policy receipt and digest are included in the request authority, inner analysis-result JCS hash, outer public result hash, and cache key.
