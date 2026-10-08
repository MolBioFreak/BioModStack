# Restriction-enzyme catalog and digest API v2 contract

## Authority

The backend-owned catalog is the only new-write authority for recognition motifs, cleavage geometry, enzyme identity, relationships, and catalog provenance. A request names immutable sequence and catalog receipts plus enzyme IDs. Browser-supplied `site`, `cut_index`, recognition geometry, cleavage geometry, or chemistry is forbidden. The v1 operation schema remains only for explicit legacy/inexact read compatibility; it is not new-write authority.

A recognition match never implies a cut. A complete double-strand event requires exact top- and bottom-strand boundaries from the selected catalog record. Unknown geometry cannot simulate fragments. Known nickases produce one strand event, zero double-strand breaks, and cannot be submitted as fragment-producing digest enzymes.

## Coordinate system

All scientific coordinates are **zero-based interbase boundaries** on the canonical forward reference axis.

- For a linear sequence of length `L`, valid boundaries are **[0, L]**, inclusive. A derived cut outside that range is reported as out of bounds and cannot construct fragments.
- For a circular sequence, normalized boundaries use **modulo L**, while every occurrence and cut retains its signed **unwrapped derivation**. Normalization never discards which recognition occurrence produced a cut.
- A canonical forward recognition occurrence starting at unwrapped boundary `s` converts a catalog offset `o` to `s + o`.
- Biopython primary geometry converts as `top_offset = fst5` and `bottom_offset = recognition_length + fst3`. Secondary geometry converts identically: `top_offset = scd5` and `bottom_offset = recognition_length + scd3`. Raw opposite-end-relative `fst3`/`scd3` values never become API boundaries.
- Reverse orientation **mirrors and swaps strands**. For a recognition length `R`, the primary reverse offsets are `top = -fst3` and `bottom = R - fst5`; the secondary pair uses `top = -scd3` and `bottom = R - scd5`.

Using unwrapped boundaries on the common reference axis, `delta = bottom - top`: zero is blunt, positive is a 5-prime overhang of `delta` nucleotides, and negative is a 3-prime overhang of `abs(delta)` nucleotides. No midpoint cut may be synthesized.

Overhang sequence is derived only after the orientation transform, on that common axis. For `delta > 0`, it is the top/reference interval `[top, bottom]` in 5-prime-to-3-prime order and the protruding strand is `top`. For `delta < 0`, it is the reverse complement of reference interval `[bottom, top]` in 5-prime-to-3-prime order and the protruding strand is `bottom`. Motif orientation never reverse-complements a 5-prime overhang by itself. Circular slicing retains signed unwrapped boundaries and winding.

A circular recognition motif longer than the molecule is unsupported because matching it would reuse physical base pairs. It yields zero occurrences and a deterministic `recognition_motif_longer_than_molecule` limitation grouped by motif with the affected enzyme IDs. Ordinary origin-spanning matches remain supported when motif length is at most molecule length.

## Catalog capabilities

The frozen base source is Biopython 1.87 / REBASE EMBOSS release 404 (2024): 1,088 source records, of which 754 are double-strand geometry-ready and 334 are recognition-only. The release adds four separately reviewed official-REBASE nickase receipts, for 1,092 discoverable/selectable records total. Capability is record-specific:

- `digest_simulation`: complete primary double-strand geometry; secondary events are retained when both `scd5` and `scd3` exist.
- `nicking_analysis`: exactly one sourced strand boundary; never fragment-producing.
- `recognition_only`: motif discovery only; geometry is incomplete and fails closed.

Historical supplier codes are provenance only. They do not establish current availability, activity, reaction conditions, or wet-lab success.

## v2 operation binding

`bms.operation-parameters.molbio.restriction_digest.v2` is closed at every authority-bearing object. It requires:

1. immutable `sequence_id`, `revision_id`, and normalized sequence `content_sha256`;
2. exact `catalog_id` and catalog `content_sha256`;
3. a unique ordered set of `enzyme_ids` only;
4. the exact algorithm/version, topology, definite-site-only rule, and fail-closed geometry policies;
5. exact request and simulation SHA-256 result binding;
6. idempotency and persistence policy.

The server must reload and verify both immutable authorities and rerun simulation before a saved write. Stale source/catalog receipts, unknown enzymes, recognition-only records, nickases, out-of-bounds linear cuts, non-identical overlapping geometry, crossing geometry, or result-digest disagreement reject without a partial saved result.

## Determinism and updates

Catalog and manifest bytes are RFC 8785/JCS canonical JSON. Each record, catalog, and manifest digest omits only its own digest field from the canonical preimage. Generated wall-clock time is deliberately omitted (`null`) under `omitted_for_deterministic_release_bytes`; Git history records publication chronology. Updates are reviewed source changes, generated twice to exact-byte equality, and never fetched or activated at runtime.

Analysis results include ordered per-enzyme summaries, complete enzyme/occurrence/event identities on raw cleavage evidence, and an explicit ordered grouped-cleavage projection with all contributor references. These fields, typed limitations, and the closed resource-policy receipt are inside the RFC 8785 inner analysis-result hash. The outer public `result_sha256` binds the complete strict analysis-response document—exact response schema, source receipt, complete catalog receipt, request digest, and complete analysis—omitting only that outer digest field from its JCS preimage. The resource-policy SHA-256 is also bound into normalized request authority and the cache key, so a policy revision cannot reuse prior-policy authority or cached results.

Readiness, OpenAPI, and every result publish `bms.molbio.restriction-analysis-resource-policy.v1`. The receipt includes sequence, explicit-enzyme, region, actual scanner-job, charged-work, occurrence, event, response-byte and conservative incremental response-budget limits; worker concurrency, queue, timeout and cancellation behavior; and cache entry, total retained-weight and per-result thresholds. The current policy identity uses:

- exactly 1,056 catalog-wide actual scanner jobs (408 for the geometry-ready scope), derived from deduplicated forward plus distinct reverse-complement motifs in the frozen catalog bytes;
- `candidate-starts-times-motif-width` version `1.0.0`, charging `max(L-m+1, 0) × m` for each linear job and `L × m` for each circular job when `m <= L`; circular `m > L` jobs charge zero and produce the typed unsupported limitation without scanning;
- 32,000,000 charged motif comparisons, 25,000 occurrences, 50,000 events, and a 32 MiB encoded response;
- two process-wide analysis worker threads, no queue (`reject_when_all_workers_busy`), a 60-second request wait timeout, and capacity retained until the CPU future completes after timeout or caller cancellation;
- a 32-entry LRU cache with a 64 MiB complete retained-container-graph bound and an 8 MiB complete retained-entry cacheability threshold, measured by `canonical-json-entry-and-complete-cache-graph` version `2.0.0`; entries retain immutable canonical JSON bytes and exact keys, and hits strictly reconstruct fresh result models.

Admission builds one exact immutable scanner-job plan before `_scan`; execution consumes only that admitted plan. Matching and result construction remain incremental and fail closed. Final serialization is a backstop rather than the first resource gate. Phase 2 performs analysis only: it does not construct or persist fragments.
