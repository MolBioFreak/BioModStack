# BioModStack ONT Raw-Signal POD5/BLOW5 Specification v1

**Status:** Approved implementation-controlling design
**Scope:** ONT raw-signal registration, representation selection, conversion, validation, and bounded viewing
**Source baseline:** `d55da2a00311a3c8e378c5220abeceee61603088`
**Source tree:** `e3ce101ac1d77959fd06f6c21b7f6768c160a428`
**Evidence-review pin:** `6ce2139eaf515e4953c88b3507f466624f2e74cd`

`MUST`, `MUST NOT`, `SHOULD`, and `MAY` are normative.

## 1. Decision

BioModStack MUST support POD5 and indexed BLOW5 as first-class raw-signal representations.

| Representation | Primary role | Required policy |
|---|---|---|
| POD5 | Native MinKNOW acquisition evidence and direct input to qualified POD5 consumers | Preserve immutable source bytes. Use directly when the consumer accepts POD5 and current resources favor that path. |
| Indexed BLOW5 | Random-access analysis representation for SLOW5-family consumers | Use BLOW5 plus its adjacent `.idx`. The initial derived profile uses lossless `zstd` record compression and lossless `svb-zd` signal compression. |

The default user setting is `auto`. Each consuming workflow MUST also expose typed `pod5` and `blow5` choices through matching UI and API controls.

An explicit choice MUST be honored when it is qualified and admissible. BioModStack MUST return a reasoned refusal or deferral when it cannot honor that choice. It MUST NOT silently substitute another format.

## 2. Scope

This specification controls:

- native MinKNOW POD5 discovery and retention;
- external POD5, SLOW5, and BLOW5 registration;
- one logical dataset with several physical representations;
- resource-aware representation selection;
- qualified POD5-to-BLOW5 derivation;
- BLOW5 indexing and semantic validation;
- bounded Squigualiser use;
- raw-signal capability reporting and audit receipts.

This version excludes FAST5 conversion, remote BLOW5 access, instrument control changes, and BLOW5-to-POD5 derivation. It does not replace IGV or the NGS alignment authority.

## 3. Authority and identity

One logical raw-signal dataset is identified by exactly:

```text
(run_id, observed_generation)
```

For MinKNOW acquisition, this key refers to the existing `OntInstrumentRun` and its append-only generation event. The sealed terminal acquisition manifest remains the source authority.

For external material, the server MUST create an external run registration and a sealed observed generation. It MUST retain format-native acquisition and run IDs as provenance. It MUST NOT populate MinKNOW identity or invent POD5 ancestry.

Representations belong to the same dataset only after all applicable identity checks pass:

1. The generation is sealed.
2. Source artifact digests match the sealed source manifest.
3. POD5 `run_info.acquisition_id` matches the bound MinKNOW acquisition identity for native runs.
4. All source shards belong to the admitted acquisition or an explicit partition.
5. Complete read-ID multisets agree where two representations claim equivalent scope.
6. Derivation parent digests and the qualification-profile digest match.

A filename, directory, display run ID, or matching read count is insufficient identity evidence.

## 4. Source and retention rules

Native MinKNOW POD5 MUST remain immutable acquisition evidence. BioModStack MUST NOT delete it because BLOW5 exists.

An external native POD5, SLOW5, or BLOW5 file is a legitimate source artifact. The original bytes and their source manifest MUST remain authoritative. A known degraded BLOW5 source can be retained as supplied evidence, but it is unavailable to workflows that require exact raw samples. An unknown-history BLOW5 MUST carry `source_fidelity=unknown`.

Derived files, indexes, maps, and reports MUST live in a separate representation ledger. They MUST NOT be appended to the immutable terminal acquisition manifest.

Deleting any source representation requires a separate operator-controlled retention action outside this specification. Cache eviction MAY remove a reproducible derived artifact when no durable record pins it.

## 5. Representation contract

Each representation record MUST contain:

- representation ID;
- `run_id` and `observed_generation`;
- `source|derived` role and source kind;
- `pod5|slow5|blow5` format;
- immutable artifact manifest with artifact IDs, byte sizes, SHA-256 digests, and read counts;
- read-group and acquisition-group metadata;
- parent representation IDs and parent manifest digests;
- compression methods where applicable;
- converter, library, runtime, and profile identities;
- structural, index, and semantic validation receipt IDs;
- readiness state and reason code;
- creation, publication, and retention timestamps.

Backend paths MUST remain server-side. Public contracts use opaque artifact IDs.

The normal derived layout is one BLOW5 per validated source conversion unit. Every BLOW5 MUST have a prebuilt, adjacent `.idx` file with its own digest. A sharded representation MUST publish a digest-bound dataset locator from read ID to BLOW5 artifact ID. A monolithic merged BLOW5 is an optional consumer cache and requires separate admission and validation.

Duplicate read IDs across the complete logical dataset MUST fail admission or enter quarantine before conversion. They MUST never be overwritten, hidden by a batch dictionary, or omitted by an index.

## 6. Representation selection

Each raw-signal consumer MUST register:

- accepted formats;
- required indexes or mappings;
- exact qualified tool and runtime profile;
- measured CPU, memory, storage-I/O, and temporary-space bounds for each format;
- mode-specific limits and refusal reasons.

The request field is:

```text
representation_preference = auto | pod5 | blow5
```

The server applies this order:

1. Validate the consumer capability and any explicit format choice.
2. Reuse a ready, validated representation when possible.
3. Use POD5 directly when the consumer accepts it and no qualified policy justifies conversion.
4. Use indexed BLOW5 when the consumer requires SLOW5-family input or when the qualified local profile selects BLOW5 under current resource pressure.
5. Queue conversion only when a sealed POD5 source exists and conversion admission passes.
6. Return `preparable`, `deferred`, or `unavailable` when no ready representation can run.

For `auto`, the selector MAY choose either ready format. It MUST use consumer-specific local measurements plus current CPU, memory, disk, I/O, and acquisition pressure. A tie selects the ready source representation and avoids new conversion work.

The execution receipt MUST record the requested choice, selected representation ID, selected format, reason code, resource snapshot, and selector-profile digest. A changed resource decision affects a new execution only. It does not mutate earlier receipts.

Automatic whole-run conversion remains disabled until the fidelity and capacity gates in section 12 pass.

## 7. Initial BLOW5 profile

The initial profile ID is:

```text
bms.blow5.zstd-svb-zd.v1
```

It requires:

- binary BLOW5 output;
- `zstd` record compression;
- `svb-zd` signal compression;
- one blue-crab I/O process;
- four BLOW5 encoding threads;
- batch size 1000;
- one conversion job at a time;
- staging under `/mnt/BioModStack` on the final-output filesystem;
- a prebuilt `.idx` beside every BLOW5 artifact.

The canonical conversion shape is:

```bash
blue-crab p2s -c zstd -s svb-zd --iop 1 --threads 4 --batchsize 1000 input.pod5 -o output.blow5
slow5tools quickcheck output.blow5
slow5tools index output.blow5
```

The implementation MUST pass explicit input files and deterministic attempt-local output names. It MUST verify zstd support in the pinned runtime before admission. It MUST NOT fall back to zlib or another codec under the same profile ID.

`zstd + svb-zd` is an upstream-supported, lossless performance profile. It is a BioModStack choice. It is not declared as the universal upstream default. Another compression profile requires a new versioned qualification profile.

`slow5tools degrade` and all equivalent lossy transformations are forbidden.

## 8. Durable derivation and publication

Conversion, indexing, semantic validation, and rendering MUST run outside HTTP request threads. A durable queue owns requests, attempts, append-only events, conditional claims, leases, recovery, cancellation, and terminal receipts.

The derivation lifecycle is:

```text
requested
  -> admitted
  -> converting
  -> structural_check
  -> indexing
  -> index_validation
  -> semantic_validation
  -> publishing
  -> ready
```

Terminal alternatives are `deferred`, `failed`, and `cancelled`.

Admission requires all of these conditions:

- sealed dataset generation;
- verified source identity and readability;
- no duplicate read IDs;
- a qualified converter profile;
- consumer demand or an enabled measured policy;
- no matching ready representation;
- sufficient output, index, temporary, and reserve space;
- acceptable current CPU, memory, and storage pressure;
- no unsafe contention with active MinKNOW acquisition on the same storage device.

Conversion is a CPU-and-storage workload. It MUST NOT reserve a GPU.

Each attempt uses a unique private directory. Partial output is never a representation or checkpoint. Cancellation MUST stop the active child and suppress retries. Lease recovery MAY resume only from a fully validated unit boundary.

Publication MUST use a same-filesystem atomic rename after data, indexes, manifests, and parent directories are flushed. Startup reconciliation MUST resolve a crash between rename and database commit without publishing unverified bytes.

## 9. Validation and fidelity labels

A successful command exit is necessary and insufficient. Each ready derivative requires separate receipts for:

1. source preflight;
2. conversion process completion;
3. structural check;
4. index creation;
5. index opening and lookup validation;
6. exhaustive semantic comparison;
7. atomic publication.

`slow5tools quickcheck` checks limited structural readability. It does not establish complete semantic equality.

The semantic harness MUST compare the complete admitted scope for:

- exact read-ID multiset and duplicate absence;
- bit-exact raw `int16` sample arrays;
- sample counts and start-sample timing;
- calibration values;
- channel, well, mux, and pore data;
- end reason and forced state;
- read-group and acquisition-group identity;
- sample rate and required run metadata;
- tracked and predicted scaling values;
- open-pore, event-count, and auxiliary fields in the frozen mapping contract;
- every field classified as preserved, sidecar-preserved, or explicitly source-only.

An unmapped or new POD5 field MUST cause qualification failure, explicit sidecar preservation, or a documented source-only exclusion. Silent loss is forbidden.

A derivative can be labeled `verified_signal_and_mapping_contract_exact` only after this comparison passes. The codec can be labeled lossless independently. The derivative MUST NOT be called POD5-equivalent or archival-round-trip lossless unless every current source field and a qualified reverse replay also pass.

The current blue-crab v0.5.0 evidence does not establish complete POD5 archival round-trip preservation. Native POD5 retention therefore remains mandatory.

Index validation MUST bind the `.idx` digest to the BLOW5 digest and verify index opening, unique read IDs, entry count, offsets, record lengths, and selected-read retrieval. Renderers receive both files read-only and MUST never create an index.

## 10. API, UI, and readiness

Every consuming workflow UI and API MUST expose the same typed representation choice. The API MUST reject unknown values and client-supplied paths, commands, codec strings, converter settings, profiles, or output locations.

The raw-signal capability response MUST report each mode independently as `ready`, `preparable`, or `unavailable`, with machine-readable reasons. At minimum it reports:

```text
pod5_direct
blow5_indexed
raw_waveform
signal_to_read
signal_to_reference
signal_pileup
igv
```

Required readiness relationships are:

```text
qualified POD5 consumer + ready POD5 -> POD5 direct ready
indexed BLOW5 -> raw waveform lookup ready
indexed BLOW5 + qualified signal-to-read map -> aligned per-read signal ready
indexed BLOW5 + qualified reference map + reference -> reference and pileup ready
BAM + BAI + reference -> IGV ready
```

IGV readiness MUST remain independent from raw-signal and Squigualiser readiness.

The UI MUST show the selected format, source or derivative role, readiness, reason, profile, and provenance. It MUST distinguish `preparable` from `unavailable` and `deferred` from `failed`.

## 11. Squigualiser boundary

Squigualiser is one bounded consumer. It does not own the raw-signal data model.

The initial Squigualiser profile reads indexed BLOW5 through pyslow5. It does not read POD5 directly. A selected read from `RawReadInspector` MUST be lifted into a typed render request. Clients cannot submit command fragments, filesystem paths, arbitrary profiles, Bokeh settings, or output names.

Mode requirements are:

- raw waveform: indexed BLOW5 and read ID;
- signal-to-read: indexed BLOW5, sequence, and a qualified move-derived map;
- signal-to-reference or pileup: all prior inputs plus mapped sequence, reference, and a qualified realignment contract.

An ordinary BAM does not prove signal-map readiness. Dorado workflows that produce signal maps MUST request move tables explicitly and verify `mv`, `ts`, and `ns` tags. The k-mer length, offset, chemistry, model, mapping method, and Squigualiser profile MUST be digest-bound. Unsupported RNA, duplex, split, secondary, supplementary, or unmapped behavior MUST have explicit admission and exclusion receipts.

Rendering MUST run in a pinned non-root subprocess with read-only inputs, network disabled, and limits for reads, samples, locus size, CPU, memory, wall time, file descriptors, and output bytes.

Generated Bokeh HTML is active content. It MUST use an authenticated artifact endpoint, restrictive content security policy, and a script-enabled iframe sandbox without same-origin, forms, navigation, popups, or downloads. The application origin and credentials MUST remain inaccessible.

## 12. Initial qualification set and release gates

The initial candidate set is:

| Component | Initial candidate |
|---|---|
| BioModStack POD5 API | Current product pin `0.3.35` |
| blue-crab | `v0.5.0`, commit `4043e94e430b055c4cdc77d766b1954d6bca2d81` |
| slow5lib and pyslow5 | `v1.4.0`, commit `e4bf785d696ce70eec4e54c37cbbdda19c25cc50` |
| slow5tools | `v1.4.0`, commit `f73fc6b8f65813b7b1f5d787934d790e5d58b90f` |
| Squigualiser | `v0.7.0`, commit `5a2404f1f43bc3227a85475c59b2b77970078b2e` |

The qualification manifest MUST also freeze package hashes, container or environment digest, linked-library versions, zstd support, commands, schemas, field map, and validation code digest. A version change creates a new profile and repeats all affected gates.

The detailed POD5 field audit used POD5 `0.3.44`, while BioModStack currently pins `0.3.35`. Release qualification MUST audit the actual product pin. A POD5 upgrade requires an explicit lock update and a new field audit before it can replace that baseline.

Production release requires evidence for:

1. descriptor-safe MinKNOW POD5 discovery and immutable terminal-manifest retention;
2. acquisition-ID closure against the exact run generation;
3. direct POD5 use by a qualified consumer without BLOW5 creation;
4. native external BLOW5 registration without invented POD5 ancestry;
5. duplicate, mixed-run, unsupported-field, interrupted-attempt, and cancellation fixtures;
6. exhaustive signal and mapping-contract parity on qualified POD5-to-BLOW5 fixtures;
7. adjacent index validation and read lookup;
8. deterministic `auto`, `pod5`, and `blow5` selection with recorded reasons;
9. measured conversion throughput, peak temporary space, index time, validation time, and MinKNOW storage interference;
10. browser-proven Squigualiser sandbox and bounded-render behavior before viewer release;
11. unchanged IGV behavior for alignment-only sessions.

Until gates 1 through 9 pass, automatic whole-run conversion MUST remain disabled. Manual conversion requests also remain unavailable until the fidelity profile passes.

## 13. Implementation order

1. Add the raw-signal dataset and representation ledger.
2. Register and validate native external BLOW5/SLOW5 sources.
3. Implement descriptor-safe MinKNOW POD5 discovery and immutable retention.
4. Enforce POD5 acquisition identity against `(run_id, observed_generation)`.
5. Build the pinned converter fidelity harness.
6. Add the durable, bounded post-seal derivation queue.
7. Add typed UI/API representation selection and resource receipts.
8. Release selected-read raw waveform viewing.
9. Add move-table production and signal-to-read mapping.
10. Add reference-aligned and pileup modes.
11. Qualify remote BLOW5 separately if required later.

## 14. Current disposition

This specification is complete. The reviewed BioModStack source does not yet implement BLOW5 registration, conversion, indexing, semantic parity, signal mapping, or Squigualiser rendering. No capability may be advertised as ready until its release gate passes.
