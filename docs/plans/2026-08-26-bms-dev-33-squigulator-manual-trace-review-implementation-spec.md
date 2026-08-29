# BMS-DEV-33 Squigulator Manual Trace Review Implementation Specification

> **For Hermes:** Implement only after Christian approves this specification. Use bounded work packages, TDD, two exact-tree reviews, managed Development deployment, and Christian-visible browser acceptance.

**Goal:** Add a governed Squigulator ideal-signal comparison capability to the existing BioModStack Read and Signal Workbench so an operator can compare one real retained ONT trace with one sequence-derived ideal trace through Squigualiser.

**Architecture:** Reuse the current real raw-signal authority, signal-to-reference mapping, viewer session, bounded worker, descriptor broker, artifact route, and sandboxed HTML viewer. Add one immutable comparison job that orchestrates a separately pinned Squigulator producer runtime and a separately pinned Squigualiser comparison-render runtime. Keep real instrument signal, simulated signal, simulator truth mapping, and comparison HTML in separate authority classes.

**Tech stack:** FastAPI, SQLAlchemy, SQLite migrations, Python worker/runtime wrappers, OCI containers, Squigulator 0.5.0, Squigualiser 0.7.0, slow5lib/SLOW5-BLOW5, React, TypeScript, Vitest, and Bokeh output embedded as bounded self-contained HTML.

**Issue:** `BMS-DEV-33`, tracker component spelling `Squigalator`, live status `Open` during planning.

**Planning source baseline:** `a917208c46bc06f90a57dde40ef37841138dd3ae` on `origin/test`.

**Planning worktree:** `/home/dalab/worktrees/bms-dev-33-squigulator-spec-20260826`.

---

## 1. Product decision

### 1.1 Required capability

BioModStack shall provide this exact operator path:

1. Open a governed NGS result and its **Read and Signal Workbench**.
2. Select one retained real read and a bounded reference interval that the read covers.
3. Select one compatible Squigulator profile.
4. Preview the complete effective request, profile values, approximation warnings, parent identities, and configuration digest.
5. Generate one ideal simulated trace from the exact managed reference sequence.
6. Render the real and simulated traces together with synchronized x-axis navigation.
7. Review the trace manually and save an immutable manual-review revision.
8. Reopen the same comparison from the saved viewer session and retrieve the same digest-bound artifacts.

### 1.2 Tool roles

- **Squigulator** generates simulated ONT current from a known sequence and a declared profile.
- **Squigualiser** maps current to sequence coordinates and renders the real and simulated traces.
- **BioModStack** owns request validation, parent authority, execution bounds, artifact registration, provenance, review persistence, access, and operator presentation.

UI copy, API schemas, database receipts, and acceptance reports shall keep these roles separate.

### 1.3 Completion name

This work delivers **Squigulator ideal trace comparison**. It does not deliver the complete general-purpose Squigulator dataset-generation surface.

The following upstream modes remain outside BMS-DEV-33:

- arbitrary read-count, coverage, or read-length simulation;
- noisy synthetic dataset generation;
- cDNA and transcript-abundance simulation;
- transcript truncation;
- methylation-frequency simulation;
- operator-provided custom pore or methylation models;
- unrestricted ADC/profile overrides;
- basecalling, variant calling, benchmarking, or automated pass/fail interpretation.
- simulated instrument-run registration or basecalling of simulated BLOW5.

The API shall reject these fields. The UI shall not imply that they are available.

### 1.4 Scientific interpretation

The simulated trace is a model-derived expectation. It is not instrument evidence, a ground-truth trace, or proof that a real read is correct.

A manual-review criterion uses the existing `bms.scientific-criterion.manual-review.v1` contract:

- `review_question` records the exact comparison question;
- `required_outcome` is `approve`, `reject`, or `record_only`.

The immutable review revision also stores a bounded note that records what the operator observed.

The outcome applies to the manual-review criterion. It does not convert the simulated trace into scientific truth or create an automatic result PASS or FAIL.

---

## 2. Evidence from the paper and upstream repositories

### 2.1 Paper findings

The peer-reviewed paper defines Squigulator as a C program that accepts reference or read sequences and generates current signal from k-mer pore models. [1] It varies amplitude noise, dwell-time behavior, translocation and acquisition properties, and other profile variables. [1] The paper shows ideal signal beside experimental signal and reports BLOW5 as the simulated raw-signal output. [1] It used Squigulator commit `7422d7384be428ac334caa61c019473f31f1e633` for its experiments. [1]

The paper supports these BMS decisions:

- sequence identity and pore-model identity are scientific parents; [1]
- amplitude and dwell-time settings alter scientific output; [1]
- simulated BLOW5 can enter signal-analysis tooling; [1]
- visual comparison is useful, while simulated data remains an approximation; [1]
- paper results from the archived commit do not automatically validate later release behavior or every chemistry. [1]

Source: [1].

### 2.2 Pinned Squigulator source

The implementation pin shall be the released `v0.5.0` source. [8][9]

| Field | Value |
|---|---|
| Release | `v0.5.0` |
| Commit | `c5f0c619a28b9532388877096acb7568c34b9c4b` |
| Release source asset | `squigulator-v0.5.0-release.tar.gz` |
| Release source asset SHA-256 | `f8b428655d586427c6e0c939d4a0383fa8569523234e3c21951edcd23372a66a` |
| Generic codeload archive SHA-256 | `eff1024ae0020da9f37919fd9869d3d94bdb409e89eb2cf94a0ff1a24d85b347` (verified, not the build input) |
| License | Squigulator MIT; bundled slow5lib MIT; bundled streamvbyte Apache-2.0 |
| Build requirement | C99/POSIX compiler and zlib; bundled slow5lib source |

Source commit and licenses: [7][9]. Release and asset metadata: [8][14].

The moving `main` branch was at `6f59e70df1149103e7612274747c0a851f805322` during review. [2] Its only change after `v0.5.0` was a `docs/man.md` correction. [2][9] Runtime code shall remain pinned to the released commit and named release source asset. The image shall retain all bundled dependency notices.

### 2.3 Upstream CLI and outputs

The upstream CLI supports the following comparison-relevant options. [3][4][10]

- `-o` for SLOW5/BLOW5;
- `-x` for a profile;
- `--ideal` for no amplitude or time noise;
- `--full-contigs` for one complete signal record per input sequence;
- `--seed` for reproducibility;
- `-q` for perfect simulated sequences;
- `-c` for PAF;
- `-a` for signal-to-reference SAM;
- `-t` and `-K` for execution shape.

Its documented outputs are BLOW5, perfect-read FASTA, PAF, and SAM with `si` and `ss` signal-alignment tags. [5]

Sources:

- Squigulator options [4]
- Squigulator outputs [5]
- Executable profile and CLI source [10]

### 2.4 Upstream discrepancy that BMS must handle

`docs/profile.md` still states 4 kHz and dwell mean 10 for DNA R10 profiles. [6] The `v0.5.0` executable source uses these different values. [10]

- sample rate `5000`;
- translocation speed `400` bases/s;
- dwell mean `13` samples/base;
- dwell standard deviation `4`.

The executable also warns that the 5 kHz R10 parameters and models remain crude. [10] RNA004 profiles carry a similar warning. [10]

BMS shall use executable-source values. [10] The capability response and UI shall display the R10 approximation warning. Documentation values shall never silently override runtime values.

Source: [6][10].

### 2.5 Squigualiser integration pattern

The pinned Squigualiser documentation already demonstrates the following comparison pattern. [11][12][13]

- ideal Squigulator output rendered with real signal; [12]
- reference and alternate simulated tracks; [13]
- multi-track output with `plot_tracks --shared_x`; [11][12]
- real-variant review pipelines using simulated reference and alternate traces. [13]

BMS-DEV-33 shall implement the smaller required denominator: one real trace and one ideal reference trace. Alternate-allele tracks require a later approved extension.

Sources: [11][12][13].

---

## 3. Current BioModStack implementation

### 3.1 Existing authority and runtime

Current source already provides:

- retained POD5/BLOW5 authority with run and generation identity;
- retained move-table BAM authority;
- calibration and immutable mapping profiles;
- signal-to-read and signal-to-reference mapping jobs;
- immutable mapping artifacts;
- bounded Squigualiser view jobs;
- descriptor-retained parents;
- CAS-fenced worker leases and cancellation;
- a network-denied, read-only, non-root container;
- self-contained HTML/SVG validation and governed artifact routes;
- a sandboxed React iframe with network-silent CSP;
- a persisted viewer session with IGV, read, locus, mapping, and view state.

The generated runtime implementation record classifies this baseline as `implemented_unverified`, with capability and dataset exposure `fail_closed` and release acceptance `open`. Existing source is the integration baseline. It is not accepted product proof.

### 3.2 Existing source seams

| Layer | Existing file | Required reuse |
|---|---|---|
| Models | `platform/api/database.py` | Follow current ONT immutable lifecycle patterns. |
| Migration | `platform/api/migrations/add_ont_signal_workbench.py` | Reuse checks, foreign keys, immutable triggers, append-only receipts, and no-delete rules. |
| Migration registry | `platform/api/migrations/runner.py` | Add the next migration, currently version 41 at the planning baseline. |
| Service | `platform/api/services/ont_signal_workbench.py` | Reuse authority resolution, closed render validation, fingerprints, public sanitization, and artifact access. |
| Worker | `platform/api/services/ont_signal_worker.py` | Extend the single worker owner with a comparison queue and two separate pinned runtime identities. |
| Runtime wrapper | `scripts/ont_signal_runtime.py` | Reuse broker protocol, bounded subprocess handling, BLOW5/PAF validation patterns, and active-resource HTML scan. |
| Router | `platform/api/routers/ont_signal_workbench.py` | Add closed preview, create, read, cancel, fresh-attempt, artifact, and review routes. |
| Frontend API | `platform/frontend/src/lib/api.ts` | Add exact types and fetch functions. |
| Workbench | `platform/frontend/src/components/ngs/ReadAndSignalWorkbench.tsx` | Add the visible ideal-comparison mode without a second NGS viewer. |
| Raw waveform | `platform/frontend/src/components/ngs/RawReadInspector.tsx` | Preserve instrument waveform behavior unchanged. |
| Service config | `biomodstack_services.py` | Inject the Squigulator producer and Squigualiser comparison-render image identities into the managed Development API unit. |
| Capability inventory | `schemas/ngs_molbio/capability-inventory-v1.schema.json` and `platform/api/config/ngs_molbio/capability_inventory_v1.json` | Preserve v1 and create a versioned successor with a separate Squigulator capability row and reconciled `emit_moves` classification. |
| Runtime denominator | `schemas/ngs_molbio_runtime/runtime-source-denominator-v1.json` | Preserve v1 and create a versioned successor containing every new executable/runtime/config/test source path before generating a successor runtime implementation record. |

### 3.3 Preserved behavior

The implementation shall preserve:

- `BMS_ONT_RAW_SIGNAL_RETENTION_POLICY=pod5_and_blow5`;
- existing raw waveform, single-read, reference, and pileup modes;
- the current Squigualiser single-track runtime policy and OCI digest for existing modes;
- current IGV authority and viewer-session compatibility checks;
- path-opaque API responses;
- explicit `legacy_unknown` authority when old provenance is unavailable;
- failed-row immutability and fresh-successor semantics;
- the network-denied bounded-container model;
- self-contained artifact delivery;
- Development and Production lane separation.

### 3.4 Delayed-review reconciliation

Three read-only reviews completed after the first document handoff. Their material findings were reconciled as follows:

| Finding | Disposition | Binding change |
|---|---|---|
| The named release asset has SHA-256 `f8b428...`; the earlier `eff1024...` value is the generic codeload archive. | Accepted | The named release asset is the build input; both digests are labeled distinctly. |
| Squigulator and Squigualiser require separate capability and runtime identities. | Accepted | The job orchestrates two new bounded images and preserves the current single-track image. |
| Capability inventory v1 is closed and current ONT `emit_moves` classification is stale. | Accepted | Create exact v2 inventory/schema/registry successors and reconcile `emit_moves`. |
| Upstream generated IDs need an explicit relation to the input sequence ID. | Accepted | Add a generated-ID mapping artifact and fingerprint field. |
| Existing manual-review vocabulary is `approve`, `reject`, or `record_only`. | Accepted | Reuse that schema instead of introducing a parallel assessment enum. |
| Register simulated BLOW5 as a simulated instrument generation and basecall it into a move BAM. | Deferred outside BMS-DEV-33 | This issue requires one ideal-versus-real manual comparison. General simulated-run production and synthetic basecalling are a separate capability. |

---

## 4. Scientific request contract

### 4.1 Capability inventory and versioning

Capability inventory v1 is closed at exactly 21 rows and cannot receive an additive Squigulator row. Create these v2 successors with exactly 22 capability rows:

- `schemas/ngs_molbio/capability-inventory-v2.schema.json`;
- `platform/api/config/ngs_molbio/capability_inventory_v2.json`;
- `platform/api/config/ngs_molbio/schema_registry_v2.json`.

The successor inventory shall add Squigulator as its own capability. It shall not relabel Squigualiser. The row shall bind the parameter schema, source and viewer destinations, accepted source roles, exposure state, readiness authority, result contract, receipt contract, and parity ledger.

The successor shall also reconcile the existing ONT basecalling `emit_moves` parameter because real aligned-trace authority depends on it and current source already enforces it.

### 4.2 Versioned parameter schema

Create `platform/api/config/ont_signal_workbench/squigulator_ideal_comparison_schema_v1.json` with schema identity `bms.ont-squigulator-ideal-comparison.v1`.

The schema shall inventory every meaning-bearing upstream option. Each option is classified as operator-owned, profile-fixed, workflow-fixed, runtime-owned, or unsupported for this capability. Unsupported flags retain an explicit reason and fail closed if supplied. The schema shall define type, units, bounds, upstream mapping, authority class, default, applicability, incompatibilities, digest participation, and UI control for every supported field.

Unknown fields fail before queue insertion.

### 4.3 Operator-owned fields

| Field | Type and bound | Upstream or render mapping |
|---|---|---|
| `profile_id` | Closed enum of the eight pinned profiles | `squigulator -x` |
| `seed` | Integer `1..2147483647`, default `1` | `squigulator --seed`; zero is rejected because upstream auto-generates it |
| `scale` | `none`, `medmad`, or `znorm`; default `none` | Squigualiser scale for both tracks |
| `point_size` | Existing bounded values | Squigualiser render |
| `fixed_width` | Boolean | Squigualiser render |
| `base_width` | Existing `1..100` | Squigualiser render |
| `base_limit` | `1..1000`, default `1000` | Manual comparison interval ceiling |
| `signal_sample_limit` | `1..2,000,000` | Existing bounded render limit |
| `show_samples` | Boolean | Squigualiser render |
| `show_base_colours` | Boolean | Squigualiser render |
| `remove_signal_outliers` | Boolean | Squigualiser render |

### 4.4 Profile-fixed visible fields

The selected profile fixes and exposes these values read-only:

- molecule type;
- flow-cell generation;
- device class;
- pore-model identity and k-mer length;
- digitisation;
- sample rate;
- translocation speed;
- range;
- offset mean and standard deviation;
- median-before mean and standard deviation;
- dwell mean and standard deviation;
- model-quality warning.

The complete effective object is persisted and appears in the preview, job response, review surface, and execution receipt.

### 4.5 Workflow-fixed visible fields

The comparison capability fixes:

- `simulation_mode=ideal`;
- `full_contigs=true`;
- `amplitude_noise_factor=0`;
- `dwell_noise=0`;
- `prefix=false`;
- one input sequence and one simulated signal record;
- `threads=1`;
- `batch_size=1`;
- `signal_units=pA`;
- one real read;
- one reference hypothesis.
- `sequence_basis=managed_reference`.

`threads` and `batch_size` are runtime-owned. Their exact values remain in the effective request and receipt.

### 4.6 Profile compatibility

The server derives compatibility from the real raw-signal header, instrument/run receipt, move source, basecall model, molecule type, and selected profile.

Allowed dispositions:

- `matched_profile`
- `approximate_profile`
- `legacy_unknown`
- `incompatible`

`incompatible` blocks preview. `approximate_profile` and `legacy_unknown` are visible warnings and remain in every receipt. The source warning makes DNA R10 and RNA004 profiles approximate even when header fields match. [10]

The frontend may suggest a profile. It shall not silently select a different profile after saved state or an operator edit.

---

## 5. Preview and launch authority

### 5.1 Preview

Add:

`POST /api/ont/signal-workbench/comparisons/preview`

The closed request contains:

- `viewer_session_id`;
- `expected_viewer_revision`;
- `mapping_artifact_id`;
- `selected_read_id`;
- `reference_contig`;
- 1-based closed `reference_start` and `reference_end`;
- requested `simulation_settings`;
- requested `render_params`.

The server resolves and returns:

- exact run and observed generation;
- raw representation and manifest digests;
- move-source and mapping-profile identities;
- mapping job and artifact digests;
- exact reference revision, artifact, FASTA digest, topology, and coordinate contract;
- selected real read, strand, mapped span, and coverage of the requested interval;
- simulation orientation;
- derived padded window;
- complete profile constants;
- compatibility disposition and warnings;
- complete effective request;
- `preview_digest`.

Preview creates no job and writes no artifacts.

### 5.2 Coordinate and context rules

- Public intervals are 1-based closed.
- Runtime extraction uses 0-based half-open coordinates.
- The selected read must cover the complete padded interval.
- Padding is:

  `max(real_kmer_length - 1 + abs(real_base_shift), simulated_kmer_length - 1 + abs(simulated_base_shift))`

- The operator interval is at most `1000` bases.
- The derived simulation window is at most `2048` bases.
- The managed reference FASTA is at most `64 MiB` for this v1 capability.
- A circular interval that crosses the origin returns `circular_origin_crossing_not_supported_v1`.
- A linear interval with insufficient flanking sequence returns `insufficient_sequence_context`.

For reverse-strand real reads, BioModStack materializes the reverse complement of the exact window, preserves the source coordinates, and normalizes the simulated alignment back to the original reference orientation.

### 5.3 Create

Add:

`POST /api/ont/signal-workbench/comparisons`

The request repeats the preview request and supplies `preview_digest`. The server recompiles the preview from current immutable parents. A mismatch blocks queue insertion.

An exact ready or active fingerprint replays the existing job. A failed or cancelled job cannot be reused. A fresh attempt uses a new row with a predecessor link.

### 5.4 Read, cancel, retry, artifacts, and reviews

Add:

- `GET /api/ont/signal-workbench/comparisons/{comparison_job_id}`
- `POST /api/ont/signal-workbench/comparisons/{comparison_job_id}/cancel`
- `POST /api/ont/signal-workbench/comparisons/{comparison_job_id}/fresh-attempt`
- `GET /api/ont/signal-workbench/comparisons/{comparison_job_id}/artifacts/{artifact_id}`
- `GET /api/ont/signal-workbench/comparisons/{comparison_job_id}/reviews`
- `POST /api/ont/signal-workbench/comparisons/{comparison_job_id}/reviews`

All JSON models use `extra="forbid"`. Artifact responses remain binary and authenticated through the existing router policy.

---

## 6. Persistence contract

### 6.1 Migration

Create `platform/api/migrations/add_ont_signal_comparisons.py` and register the next migration, currently version 41.

### 6.2 `ont_signal_comparison_jobs`

Required identity fields:

- `id`
- `viewer_session_id`
- `viewer_session_revision`
- `run_id`
- `observed_generation`
- `raw_representation_id`
- `mapping_artifact_id`
- `reference_revision_id`
- `selected_read_id`
- `reference_contig`
- `reference_start`
- `reference_end`
- `simulation_orientation`
- `simulation_settings`
- `sequence_basis`
- `generated_read_id`
- `render_params`
- `preview_digest`
- `request_fingerprint`
- `attempt_number`
- `predecessor_job_id`
- lifecycle, lease, cancellation, receipts, output, failure, and timestamp fields.

States are `requested`, `running`, `ready`, `failed`, and `cancelled`.

Identity columns never change. Terminal evidence never changes. Rows cannot be deleted. A predecessor can have at most one successor. Attempt number is capped at three.

### 6.3 `ont_signal_comparison_events`

Events are append-only and cannot be updated or deleted. Each event records state, reason code, bounded receipt, and timestamp.

### 6.4 `ont_signal_comparison_artifacts`

Each artifact row records:

- comparison job;
- kind;
- authority class;
- managed relative path;
- media type;
- SHA-256;
- size;
- complete parent identities;
- Squigulator runtime identity where applicable;
- Squigualiser runtime identity where applicable;
- validation receipt;
- creation time.

Allowed authority classes are:

- `simulated_derived`
- `comparison_derived`

Instrument-acquired authority is never allowed in this table.

### 6.5 `ont_signal_manual_reviews`

Manual reviews are immutable revisions. Fields include:

- review ID;
- comparison job ID;
- predecessor review ID;
- `review_question`;
- `required_outcome` using `approve`, `reject`, or `record_only`;
- bounded note;
- reviewed interval;
- comparison HTML artifact ID and SHA-256;
- comparison request fingerprint;
- reviewer identity;
- creation time.

A correction creates a successor review. It does not update or delete the prior review.

### 6.6 Viewer session

Extend `signal_state` with closed optional fields:

- `comparison_job_id`
- `comparison_preview_digest`
- `comparison_settings`
- `comparison_review_id`

Existing sessions remain readable. New state shall pass the same authority-coherence checks as current mapping and view IDs.

---

## 7. Runtime and artifact design

### 7.1 Separate producer and renderer runtimes

Keep Squigulator and Squigualiser as separate scientific capability and runtime identities. Preserve the current single-track Squigualiser image for existing jobs.

Create the Squigulator producer runtime:

- `docker/ont-squigulator.Dockerfile`
- `scripts/build_ont_squigulator_runtime.sh`
- `scripts/ont_squigulator_runtime.py`
- `platform/api/config/ont_signal_workbench/squigulator_runtime_policy_v1.json`

It contains Squigulator `v0.5.0` from the named release source asset and retains the Squigulator, slow5lib, and streamvbyte notices. [8][9]

Create the Squigualiser comparison-render runtime:

- `docker/ont-squigualiser-comparison.Dockerfile`
- `scripts/build_ont_squigualiser_comparison_runtime.sh`
- `scripts/ont_signal_comparison_runtime.py`
- `platform/api/config/ont_signal_workbench/comparison_render_runtime_policy_v1.json`

It contains Squigualiser `0.7.0` at `5a2404f1f43bc3227a85475c59b2b77970078b2e` and only the dependencies needed for governed multi-track rendering. [11]

Each policy records one upstream identity, exact build inputs, image digest, wrapper digest, and `network=none`. Neither policy implies acceptance of the other runtime.

### 7.2 Worker ownership

Modify `platform/api/services/ont_signal_worker.py` so the current single worker also claims comparison jobs. Do not create a second daemon or persistent service.

Existing move, calibration, mapping, and single-track view jobs use the current Squigualiser policy unchanged. A comparison job invokes the Squigulator producer first and the Squigualiser comparison renderer second. The job receipt records both runtime identities and both stage receipts.

The worker passes retained parent descriptors through the existing broker. It does not reopen ambient host paths inside the container.

### 7.3 Container limits

Both invocations use:

- `--pull=never`
- `--network none`
- `--read-only`
- numeric non-root user
- `--cap-drop ALL`
- `no-new-privileges`

Squigulator producer limits are:

- `--pids-limit 64`
- `--memory 1g`
- `--cpus 1`
- `256 MiB` no-exec temporary filesystem
- 5-minute deadline
- 4 MiB combined log ceiling
- 16 MiB per-file ceiling
- 32 MiB total output ceiling

Squigualiser comparison-render limits preserve the current worker precedent:

- `--pids-limit 128`
- `--memory 4g`
- `--cpus 4`
- `512 MiB` no-exec temporary filesystem
- 15-minute deadline
- 8 MiB combined log ceiling
- 48 MiB HTML ceiling
- 64 MiB total output ceiling

Container removal failure remains fatal to the worker owner.

### 7.4 Runtime stages

The wrapper performs these stages in order:

1. Validate broker metadata and exact retained parent descriptors.
2. Revalidate real mapping, reference, selected read, run, generation, and raw-manifest identities.
3. Extract the exact sequence window from the managed FASTA.
4. Reverse-complement it when the real read maps to the reverse strand.
5. Write a one-record, digest-derived virtual FASTA.
6. Run Squigulator with one full contig, ideal mode, explicit seed, one thread, and one-record batch.
7. Produce BLOW5, perfect-read FASTA, source PAF, and source SAM.
8. Validate the BLOW5 record count, read identity, header fields, signal length, and adjacency index.
9. Persist an exact input-sequence-ID to generated-read-ID mapping because `--full-contigs` does not preserve the input identifier verbatim.
10. Preserve original PAF/SAM and create separately labeled coordinate-normalized PAF/SAM artifacts.
11. Validate `si`, `ss`, sequence, CIGAR, orientation, coordinate span, generated ID mapping, and reference digest binding.
12. Render the real trace and simulated truth PAF through the separate Squigualiser comparison runtime.
13. Combine tracks with shared x-axis navigation.
14. Scan HTML for external active resources, file URLs, oversize payloads, malformed output, and unexpected files.
15. Write a bounded manifest with every command argument, parent digest, generated ID relation, output digest, and both runtime identities.

The effective Squigulator command uses the pinned upstream options and is equivalent to the following argument vector. [4][10]

```text
squigulator \
  -x <profile_id> \
  --full-contigs \
  --ideal \
  --seed <seed> \
  -t 1 \
  -K 1 \
  -q /output/simulated_reads.fasta \
  -c /output/simulated_source.paf \
  --paf-ref \
  -a /output/simulated_source.sam \
  /parents/simulation_input.fasta \
  -o /output/simulated.blow5
```

The runtime constructs the argument vector. The browser never supplies a command, path, filename, or container option.

### 7.5 Required artifacts

| Kind | Label | Authority |
|---|---|---|
| `simulation_input_fasta` | Exact digest-derived reference window | simulated input, derived from managed reference |
| `simulation_coordinate_map` | Original to virtual coordinate receipt | comparison-derived |
| `simulated_blow5` | **SIMULATED IDEAL SIGNAL** | simulated-derived |
| `simulated_blow5_index` | Adjacent synthetic index | simulated-derived |
| `simulated_read_fasta` | Perfect sequence passed through upstream output | simulated-derived |
| `simulated_read_id_map` | Input sequence ID to generated Squigulator read ID | simulated-derived |
| `simulated_source_paf` | Unmodified Squigulator PAF with `ss:Z` | simulated-derived |
| `simulated_normalized_paf` | BMS coordinate-normalized simulator truth for rendering | comparison-derived |
| `simulated_source_sam` | Unmodified Squigulator SAM | simulated-derived |
| `simulated_normalized_sam` | BMS coordinate-normalized SAM | comparison-derived |
| `comparison_html` | Real and ideal manual-review view | comparison-derived |
| `comparison_manifest` | Complete digest and command receipt | comparison-derived |

Synthetic BLOW5 is not entered into `ont_raw_signal_representations`. It has no POD5 parent and cannot be presented as acquired signal.

Simulator truth PAF/SAM is not a Dorado move BAM and is never registered as an `OntMoveTableSource`. It is a bounded derivative used only by the comparison renderer. The existing real mapping remains the authority for the acquired trace.

### 7.6 HTML contract

The final HTML shall be:

- self-contained;
- network-silent;
- free of `file://` and host paths;
- under 48 MiB;
- served only through the governed artifact route;
- loaded through a blob URL after client-side CSP insertion;
- sandboxed with `allow-scripts` only;
- labeled visibly within the plot, not only outside the iframe.

Required track labels:

- `REAL · INSTRUMENT ACQUIRED · <read_id>`
- `SIMULATED IDEAL · SQUIGULATOR 0.5.0 · <profile_id>`

A visible banner shall state:

> Simulated signal is model-derived from the selected reference and profile. It is not instrument-acquired evidence.

No Bokeh server, HTTP listener, notebook server, or long-lived plotting process is allowed.

---

## 8. Frontend operator surface

### 8.1 Composition

Add an `Ideal comparison` mode to `ReadAndSignalWorkbench.tsx`. Extract the new dense panel to:

`platform/frontend/src/components/ngs/OntSignalIdealComparison.tsx`

Keep IGV, shared read/locus selection, mapping preparation, and raw waveform in their existing components.

### 8.2 Controls and state

The panel shall show:

- current real run and generation;
- selected real read and strand;
- managed reference revision and FASTA digest;
- review interval and derived context window;
- profile selector;
- complete profile-fixed values in a readable disclosure;
- R10/RNA004 approximation warning where applicable;
- seed numeric control;
- existing meaningful render controls;
- preview action;
- effective request and preview digest;
- blockers and warnings;
- `Generate and compare` action enabled only for the current preview digest;
- job lifecycle and cancellation;
- fresh-attempt action for terminal failure within the attempt bound;
- sandboxed comparison iframe;
- review question, criterion outcome, note, and revision history;
- complete provenance disclosure.

No raw JSON editor is used as the primary control surface.

### 8.3 Visible signal requirement

A ready job shall display actual current-versus-base/sample squiggle traces in the workbench. A link, manifest, receipt, output directory, chart count, or empty iframe does not satisfy this requirement.

The operator must be able to pan or zoom through the selected interval and keep both tracks synchronized.

### 8.4 Session and stale-response handling

Every preview, create, poll, artifact fetch, and review mutation is bound to the current dataset, viewer session, and request generation.

Changing dataset, run, generation, viewer session, read, reference, locus, profile, seed, or render setting shall:

- invalidate the prior preview digest;
- stop prior polling;
- revoke prior blob URLs;
- prevent late responses from replacing the current state;
- preserve the immutable old job for reopen.

---

## 9. Security, governance, and failure semantics

### 9.1 Parent authority

A comparison is admitted only when all of these are exact and ready:

- real raw representation;
- retained BLOW5 partition and index;
- move source;
- signal-to-read parent mapping;
- signal-to-reference mapping;
- mapping profile;
- managed reference revision and artifact;
- selected read in the exact alignment/mapping inventory;
- viewer session tuple.

### 9.2 Path and data handling

- Requests contain opaque IDs and typed coordinates only.
- Public responses contain no host paths.
- Managed paths are resolved server-side under approved roots.
- Parent files are opened with no-follow descriptor rules and retained through publication.
- Outputs publish only after hash and size revalidation.
- Unexpected files, links, directories, or active resources fail the job.

### 9.3 Failure, retry, and cancellation

- A terminal job is immutable.
- A failed or cancelled job cannot be retried in place.
- A fresh attempt creates one successor row.
- The attempt cap is three.
- Cancellation fences terminal publication and stops the owned bounded container.
- Container timeout, log limit, output limit, malformed signal, malformed SAM, parent drift, lease loss, and cleanup failure have distinct reason codes.
- No simulation or comparison component retries rejected NGS work.

### 9.4 Retention and labels

`BMS_ONT_RAW_SIGNAL_RETENTION_POLICY=pod5_and_blow5` remains unchanged for acquired signal.

Simulation artifacts use their own derived-artifact retention. They must retain source/reference and runtime provenance. They must never satisfy an instrument raw-signal lookup.

---

## 10. Exact implementation file plan

### Work package 0: Baseline and authority lock

**Read and record:**

- live `BMS-DEV-33` record;
- current `origin/test`;
- target-scoped status;
- current raw-signal and Squigualiser runtime policies;
- current runtime-source denominator;
- current Development service owner and database.

No implementation begins if the ONT surfaces changed materially after this planning baseline. Reconcile the changed source first.

### Work package 1: Capability, runtime pins, and parameter schema

**Create:**

- `schemas/ngs_molbio/capability-inventory-v2.schema.json`
- `platform/api/config/ngs_molbio/capability_inventory_v2.json`
- `platform/api/config/ngs_molbio/schema_registry_v2.json`
- `docker/ont-squigulator.Dockerfile`
- `scripts/build_ont_squigulator_runtime.sh`
- `scripts/ont_squigulator_runtime.py`
- `platform/api/config/ont_signal_workbench/squigulator_runtime_policy_v1.json`
- `docker/ont-squigualiser-comparison.Dockerfile`
- `scripts/build_ont_squigualiser_comparison_runtime.sh`
- `scripts/ont_signal_comparison_runtime.py`
- `platform/api/config/ont_signal_workbench/comparison_render_runtime_policy_v1.json`
- `platform/api/config/ont_signal_workbench/squigulator_ideal_comparison_schema_v1.json`

**Modify:**

- `biomodstack_services.py`
- `platform/api/tests/test_biomodstack_services.py`

### Work package 2: Persistence and migration

**Create:**

- `platform/api/migrations/add_ont_signal_comparisons.py`
- `platform/api/migrations/ont_signal_comparison_schema_contract.py`

**Modify:**

- `platform/api/database.py`
- `platform/api/migrations/runner.py`
- `platform/api/tests/test_migration_runner_version_reconciliation.py`

### Work package 3: Preview, create, and review services

**Modify:**

- `platform/api/services/ont_signal_workbench.py`
- `platform/api/routers/ont_signal_workbench.py`

**Create:**

- `platform/api/tests/test_ont_signal_comparison.py`

### Work package 4: Worker and runtime execution

**Modify:**

- `platform/api/services/ont_signal_worker.py`

**Create:**

- `platform/api/tests/test_ont_signal_comparison_runtime_contract.py`

Reuse helpers from `scripts/ont_signal_runtime.py`. Extract a shared helper only when both wrappers consume it and focused tests cover the extraction. Preserve the current implemented-unverified single-track runtime behavior and policy identity.

### Work package 5: Typed frontend API

**Modify:**

- `platform/frontend/src/lib/api.ts`

Add exact request, preview, job, artifact, review, profile, warning, and failure types. Avoid open records for authority-bearing fields.

### Work package 6: Operator UI

**Create:**

- `platform/frontend/src/components/ngs/OntSignalIdealComparison.tsx`
- `platform/frontend/tests/vitest/ontSignalIdealComparison.test.tsx`

**Modify:**

- `platform/frontend/src/components/ngs/ReadAndSignalWorkbench.tsx`
- `platform/frontend/tests/vitest/readAndSignalWorkbench.test.tsx`
- `platform/frontend/vitest.md.config.ts`

### Work package 7: Runtime denominator and canonical documentation

**Create:**

- `schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json`
- `platform/api/config/ngs_molbio_runtime/runtime_implementation_v2.json` after the final source commit and required reviews

**Modify:**

- `docs/Lab_Automation_MolBio_and_Sequencing.md`

Preserve the v1 denominator and v1 terminal implementation record. The v2 denominator shall include every new capability, runtime, wrapper, schema, migration, API, UI, test, and canonical-document path. Canonical ONT documentation shall add the current signal workbench and Squigualiser behavior, add Squigulator only when live acceptance supports it, and correct the stale `workflows/nanopore_methylation.nf` link if that path remains absent.

---

## 11. Required test denominator for implementation

These are future implementation gates. No test was executed while producing this specification.

### 11.1 API and persistence

Tests shall prove:

- closed schema and unknown-field rejection;
- explicit defaults and complete effective settings;
- complete classification of every upstream option, including unsupported flags and reasons;
- versioned capability inventory with a separate Squigulator row and reconciled `emit_moves` classification;
- `seed=0` rejection;
- executable R10 profile values;
- exact parent and selected-read authority;
- profile compatibility and visible warnings;
- preview digest invalidation;
- request fingerprint coverage of every scientific and render field;
- active/ready idempotent replay;
- immutable failed/cancelled rows;
- fresh-successor lineage and attempt cap;
- terminal and artifact immutability triggers;
- append-only event and review tables;
- viewer session coherence;
- path sanitization;
- exact artifact serving.

### 11.2 Runtime

Tests shall prove:

- exact pinned image policy;
- exact Squigulator argument vector;
- one thread and one full-contig record;
- deterministic fixture output for the pinned image, seed, and input;
- exact input-sequence-ID to generated-read-ID mapping;
- forward and reverse sequence extraction;
- coordinate-normalized PAF with preserved `ss:Z` and source SAM with preserved `si:Z`/`ss:Z`;
- BLOW5 header, record, index, and signal-length validation;
- complete parent and output digests;
- real/simulated authority separation;
- output allowlist;
- network and file-URL rejection;
- HTML and total-output limits;
- command deadline and log limits;
- cancellation and lease fencing;
- fatal container cleanup behavior;
- no persistent listener or Bokeh server.

### 11.3 Frontend

Mounted tests shall prove:

- the `Ideal comparison` mode is discoverable;
- profile and fixed values are visible;
- R10 approximation warning is visible;
- every operator-owned setting has a typed control;
- preview digest is required and invalidates after any edit;
- old async responses cannot replace current state;
- artifact blob URLs are replaced and revoked;
- iframe CSP and sandbox remain network-silent;
- both exact track labels are visible;
- the ready iframe contains actual plot content;
- manual-review criterion vocabulary and revision behavior;
- saved viewer state reopens the exact comparison;
- existing raw waveform, read, reference, pileup, and IGV modes remain usable.

### 11.4 Focused commands after implementation approval

From `platform/api`:

```bash
uv run --frozen --group dev python -m pytest \
  tests/test_ont_signal_comparison.py \
  tests/test_ont_signal_comparison_runtime_contract.py \
  tests/test_ont_signal_workbench.py \
  tests/test_ont_signal_runtime_contract.py \
  tests/test_biomodstack_services.py
```

From `platform/frontend`:

```bash
pnpm exec vitest run \
  tests/vitest/ontSignalIdealComparison.test.tsx \
  tests/vitest/readAndSignalWorkbench.test.tsx
pnpm exec tsc -b --pretty false
pnpm build
```

The exact commands may be narrowed during TDD. The final gate must include the listed affected surfaces.

---

## 12. Review, release, and acceptance

### 12.1 Candidate review

Before integration:

1. Freeze the staged candidate bytes.
2. Record commit, tree, target status, runtime policy digests, and built image digest.
3. Obtain two independent PASS reviews against the same exact final tree and runtime identity.
4. Re-run affected tests after the final edit.
5. Treat any later edit or rebase as invalidating both review verdicts.

### 12.2 Development release

After explicit release authority:

1. Fast-forward `origin/test` with the accepted commit.
2. Let canonical Development reach that commit through the managed path.
3. Build and install the approved Squigulator producer and Squigualiser comparison-render images.
4. Restart only the managed Development services that consume the change.
5. Prove frontend, API, source, process, listener, database, current single-track Squigualiser image, Squigulator producer image, and comparison-render image identities.
6. Keep Production unchanged.

### 12.3 Scientific fixture acceptance

A repository fixture shall prove deterministic source-to-signal-to-view behavior. It does not replace live user acceptance.

The fixture must include:

- one small reference FASTA;
- one retained real BLOW5 record and index;
- one exact mapping artifact;
- the expected selected read and interval;
- expected simulation and manifest digests for the pinned runtime;
- a visible shape difference or agreement that a mounted test can distinguish.

### 12.4 Christian-visible acceptance

BMS-DEV-33 remains open until Christian personally sees the workflow.

Use an actual governed BioModStack ONT dataset. The preferred current acceptance target is a retained BFX6NB real read that covers a meaningful plasmid interval, including the unresolved `eGFP_plasmid:3515` review locus when an eligible read and exact managed reference are available.

Required evidence:

1. Open the deployed Development NGS result route.
2. Open the Read and Signal Workbench.
3. Select the real read and exact managed reference interval.
4. Show the preview with exact source and profile provenance.
5. Generate the ideal trace through the product control.
6. Display real and simulated squiggle traces together.
7. Show the two permanent real/simulated labels.
8. Pan or zoom through the meaningful interval with synchronized tracks.
9. Save and reopen one manual review revision.
10. Show Christian the exact live UI directly or send an in-chat capture from that exact live state.
11. Confirm no external network request was made by the artifact.
12. Record job, artifact, runtime, reference, real raw-signal, mapping, and screenshot/capture digests.

Assistant-only browser automation, API receipts, generated files, DOM counts, and hidden screenshots do not satisfy this gate.

### 12.5 Issue and reminder closure

Only after all required gates pass:

- resolve `BMS-DEV-33` with the accepted commit, runtime image, comparison job, artifact, and Christian-visible evidence;
- allow the completion-triggered Scarab genome reminder to deliver once;
- verify cron job `aee4b36a6b11` and its script remove themselves after confirmed delivery;
- confirm no recurring BMS-DEV-33 watchdog remains.

The Scarab genome work is not part of this implementation.

---

## 13. Acceptance matrix

| Gate | Pass condition |
|---|---|
| Upstream pin | Squigulator release, commit, archive digest, license, and executable version match the approved policy. |
| Capability inventory | A versioned successor has a separate Squigulator row, complete option classification, and reconciled `emit_moves` authority. |
| Scientific schema | Every relevant ideal-comparison setting is typed, visible, persisted, compiled, and received. |
| Real authority | Real signal resolves from the exact run/generation/raw/move/mapping/read tuple. |
| Sequence authority | Simulated input resolves from the exact managed reference revision and digest. |
| Simulation | One deterministic ideal BLOW5 record and alignment pass structural validation. |
| Separation | Synthetic output is never registered or labeled as instrument-acquired raw signal. |
| Comparison | Real and simulated traces render in one synchronized, clearly labeled artifact. |
| Governance | Parents, commands, versions, settings, outputs, and runtime digests are complete. |
| Bounds | Network, CPU, memory, time, log, input, output, and file-type limits are enforced. |
| Persistence | Job, artifacts, events, sessions, and manual reviews follow immutable lifecycle rules. |
| Regression | Existing waveform, mapping, Squigualiser, IGV, and retention behavior remain unchanged and pass the required regression acceptance. |
| Exact review | Two independent PASS verdicts name the same final tree and runtime identity. |
| Development | Managed Development serves the accepted source and approved runtime images. |
| Operator | Christian sees actual real and simulated squiggle data and uses the manual-review workflow. |
| Closeout | BMS-DEV-33 is resolved and its one-shot reminder/watchdog is removed after delivery. |

Any failed row blocks completion.

---

## 14. Explicit non-goals

- Production promotion.
- General Squigulator benchmarking service.
- Persistent Squigulator or Squigualiser service.
- Unmanaged Bokeh server.
- Alternate-allele simulated tracks.
- Circular origin-crossing trace normalization.
- Large managed references over 64 MiB.
- Automatic variant verdicts.
- Basecalling simulated BLOW5.
- Registering a simulated instrument run or storing synthetic BLOW5 as acquired/raw-signal authority.
- Retrying or republishing rejected NGS rows or artifacts.
- Changes to acquired POD5/BLOW5 retention.
- Scarab C/E-6787 genome-reference implementation.

---

## 15. Approval decisions encoded by this specification

This specification recommends these decisions for Christian’s approval:

1. Deliver reference-derived **ideal** signal comparison first.
2. Keep alternate-allele simulation outside BMS-DEV-33.
3. Use separate pinned Squigulator producer and Squigualiser comparison-render runtimes while preserving the current single-track Squigualiser runtime identity.
4. Bind one real read and one simulated reference trace per immutable job.
5. Limit v1 to intervals of at most 1,000 bases and managed references of at most 64 MiB.
6. Permit `approximate_profile` and `legacy_unknown` with permanent visible warnings; reject incompatible profiles.
7. Require immutable manual review revisions and Christian-visible acceptance before issue closure.
8. Keep simulated instrument generations and synthetic basecalling outside this issue.

---

## Sources

[1] [Simulation of nanopore sequencing signal data with tunable parameters](https://pmc.ncbi.nlm.nih.gov/articles/PMC11216307/)

[2] [Squigulator upstream source at `6f59e70`](https://github.com/hasindu2008/squigulator/tree/6f59e70df1149103e7612274747c0a851f805322)

[3] [Squigulator README at `6f59e70`](https://github.com/hasindu2008/squigulator/blob/6f59e70df1149103e7612274747c0a851f805322/README.md)

[4] [Squigulator options at `6f59e70`](https://github.com/hasindu2008/squigulator/blob/6f59e70df1149103e7612274747c0a851f805322/docs/man.md)

[5] [Squigulator outputs at `6f59e70`](https://github.com/hasindu2008/squigulator/blob/6f59e70df1149103e7612274747c0a851f805322/docs/output.md)

[6] [Squigulator profiles at `6f59e70`](https://github.com/hasindu2008/squigulator/blob/6f59e70df1149103e7612274747c0a851f805322/docs/profile.md)

[7] [Squigulator MIT license at `6f59e70`](https://github.com/hasindu2008/squigulator/blob/6f59e70df1149103e7612274747c0a851f805322/LICENSE)

[8] [Squigulator `v0.5.0` release](https://github.com/hasindu2008/squigulator/releases/tag/v0.5.0)

[9] [Squigulator `v0.5.0` source](https://github.com/hasindu2008/squigulator/tree/c5f0c619a28b9532388877096acb7568c34b9c4b)

[10] [Squigulator `v0.5.0` executable profiles and CLI parser](https://github.com/hasindu2008/squigulator/blob/c5f0c619a28b9532388877096acb7568c34b9c4b/src/sim.c)

[11] [Squigualiser 0.7.0 Squigulator and multi-track workflow](https://github.com/hiruna72/squigualiser/blob/5a2404f1f43bc3227a85475c59b2b77970078b2e/README.md)

[12] [Squigualiser real versus simulated signal guidance](https://github.com/hiruna72/squigualiser/blob/5a2404f1f43bc3227a85475c59b2b77970078b2e/docs/real_vs_simulated_signal.md)

[13] [Squigualiser real variant comparison pipeline](https://github.com/hiruna72/squigualiser/blob/5a2404f1f43bc3227a85475c59b2b77970078b2e/docs/pipeline_variant_detection_real.md)

[14] [Squigulator v0.5.0 GitHub release metadata and asset digests](https://api.github.com/repos/hasindu2008/squigulator/releases/tags/v0.5.0)
