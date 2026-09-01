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
