# Phase 2 restriction-analysis benchmark and binding limits

Run on 2026-08-31 with CPython 3.12 / Biopython 1.87 on Linux x86_64, AMD Ryzen Threadripper 9960X. The benchmark driver was kept under `/tmp`; no generated output is committed. Each fixture ran in a fresh process with circular topology, the pinned catalog, and RFC 8785 serialization. Peak memory is process maximum RSS (`ru_maxrss`).

| Fixture | DNA bp | Admitted patterns | Scan work | Wall s | Peak RSS KiB | Occurrences | Events | JCS result bytes | Disposition |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 5 Mb explicit `BsaI`, no-site `A` source | 5,000,000 | 1 | 5,000,000 | 2.054200 | 81,552 | 0 | 0 | 1,022 | complete |
| largest admitted full geometry-ready scope, no-site `A` source | 342,465 | 292 | 99,999,780 | 24.449547 | 68,364 | 0 | 0 | 199,242 | complete |
| worst-case short motif, `DpnI` over repeated `GATC` | 100,004 | 1 | 100,004 | 0.019893 | 68,296 | 10,902 observed before stop | 10,902 projected | — | bounded before append by conservative response budget |
| two-event `BcgI` | 54 | 1 | 54 | 0.000477 | 68,220 | 1 | 2 | 3,761 | complete |
| ambiguous `N` source with explicit `EcoRI` | 5,000 | 1 | 5,000 | 0.705386 | 110,184 | 5,000 possible | 5,000 | 8,118,930 | complete |

## Selected limits

The evidence supports the following published, machine-readable bounds:

- inline sequence: **5,000,000 bp**;
- explicit enzymes: **256**;
- nonoverlapping regions: **128**;
- catalog-wide forward recognition patterns: **619** (the geometry-ready scope has **292**);
- pre-scan work: **100,000,000 source-bp × forward-pattern units**;
- returned occurrences: **25,000**;
- returned cleavage events: **50,000**;
- encoded response: **32 MiB**;
- immutable cache: **32 entries**.

Admission computes source length × unique forward patterns before any scan. Thus a 5 Mb source remains admitted for a small explicit scope, while 5 Mb × 292 `all_geometry_ready` fails before scanning. Scanning yields matches incrementally. Before retaining each raw occurrence, the implementation enforces the occurrence cap, the record's event cardinality, and a conservative encoded-response budget (2,048 bytes per occurrence plus 1,024 bytes per event and a 64 KiB envelope). It constructs Pydantic occurrence/event models only after collection has remained within those bounds. Final RFC 8785 serialization remains a backstop.

The 32 MiB limit is intentionally above the observed 8.12 MiB ambiguous-input result and below an unbounded worst-case expansion. The conservative incremental budget stopped the short-motif fixture after 10,902 yielded matches without constructing result models. The full 292-pattern boundary completed in 24.45 seconds and the admitted 5 Mb explicit boundary completed in 2.05 seconds on the recorded host.
