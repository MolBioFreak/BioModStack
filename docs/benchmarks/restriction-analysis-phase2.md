# Phase 2 restriction-analysis benchmark and binding limits

Run on 2026-08-31 with CPython 3.12 / Biopython 1.87 on Linux x86_64, AMD Ryzen Threadripper 9960X. Fixtures use `random.Random(20260831)`, circular topology, the pinned catalog, and RFC 8785 serialization. Peak memory is process maximum RSS (`ru_maxrss`). The benchmark driver was kept under `/tmp`; no generated output is committed.

| Fixture | DNA bp | Patterns | Elapsed s | Peak RSS KiB | Occurrences | Events | Serialized result bytes | Disposition |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| representative plasmid, uniform A/C/G/T | 5,000 | 292 | 1.563268 | 178,644 | 18,300 | 18,392 | 16,856,570 | complete |
| bacterial chromosome-scale, uniform A/C/G/T | 1,000,000 | 292 | 1.291669 | 101,096 | >50,000 | — | — | bounded rejection during occurrence collection |
| ambiguous IUPAC, uniform 15-symbol alphabet | 100,000 | 292 | 0.184904 | 77,432 | >50,000 | — | — | bounded rejection during occurrence collection |
| worst-case short motif (`DpnI`, repeated `GATC`) | 40,000 | 1 | 0.611716 | 117,236 | 10,000 | 10,000 | 9,197,400 | complete |

The rejection runs measured the provisional 50,000-occurrence gate. The completed fixtures cost about 918–921 serialized bytes per occurrence. Final output bounds therefore use **25,000 occurrences**, **50,000 events**, and **32 MiB serialized response**, leaving material envelope/headroom for receipts and two-event records. Requests additionally bind **5,000,000 inline bp**, **256 explicit enzyme IDs**, and **128 nonoverlapping regions**. Large broad-scope requests fail with `request_too_large`; callers can use bounded explicit scopes/regions. The immutable cache holds 32 authority-complete entries and includes source SHA, topology, catalog SHA, normalized scope SHA, region/policy SHA, and algorithm version.
