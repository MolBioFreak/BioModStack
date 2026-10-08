# ONT Read and Signal Workbench End-to-End Closeout Specification

**Status:** Implementation specification

**Environment:** BioModStack Development only

**Primary corpus:** BFX6NB Q2-01

**Purpose:** Complete and accept the ONT Read and Signal Workbench on real indexed BLOW5 and matching move-table BAM data. Acceptance includes raw waveform, IGV, signal-to-read, signal-to-reference, bounded pileup, persisted viewer state, and governed Squigualiser output in the live browser.

## 1. Controlling outcome

A BioModStack operator shall open the exact BFX6NB Domain Experiment in Development, select the exact run generation, inspect a raw waveform, navigate the corresponding alignment in IGV, generate and inspect Squigualiser signal-to-read and signal-to-reference views, reopen the saved viewer session, and inspect complete scientific provenance without entering or viewing a server path.

The workflow is complete only when every gate in section 15 is `PASS`. Source presence, API reachability, terminal jobs, artifact counts, and automated tests cannot replace live browser acceptance.

## 2. Scope

This specification includes:

1. Completion of the current explicit fresh move-source attempt.
2. Exact preservation of the original failed move-source row.
3. Validated move-table authority for the correct BFX6NB run generation.
4. Approved calibration and mapping-profile creation.
5. Signal-to-read and signal-to-reference mapping.
6. Governed Squigualiser rendering for read, reference, and bounded pileup views.
7. Raw waveform and IGV interoperability in one persisted workbench session.
8. Complete browser function testing of critical operator paths.
9. Fail-closed tests for wrong, stale, missing, tampered, oversized, or cross-experiment authority.
10. Managed Development release proof and final exact-candidate review.

## 3. Exclusions

The following actions remain outside this specification:

- MinKNOW acquisition or hardware control.
- Basecalling or re-basecalling.
- POD5 reconversion when the accepted indexed BLOW5 is already ready.
- Production deployment or `main` promotion.
- An unmanaged Squigualiser or Bokeh server.
- A persistent Squigualiser service.
- Direct database repair of terminal scientific rows.
- Server path entry in the browser.
- Arbitrary file upload for move-table or raw-signal authority.
- Broad NGS redesign, multi-tenant controls, or a plugin framework.

Commit, push, Development deployment, and Production promotion remain separate authority gates. This specification does not grant those actions by itself.

## 4. Fixed corpus authority

The acceptance corpus is fixed to:

| Item | Required identity |
|---|---|
| Project | `4af72c1d-27d8-4e14-8f39-4259a80494a0` |
| Domain Experiment | `916a611b-6879-486f-bf9e-e1b5a796e01c` |
| Run | `ont-external-run-31c10a16eb10de7f8f70c16296182877` |
| Observed generation | `1` |
| POD5 representation | `ont-raw-rep-50dbd16081d34e3c88ca85c335b2cefa` |
| Indexed BLOW5 representation | `ont-raw-rep-0d0035175bad48bd82d30cd6e20cde69` |
| Original failed move source | `ont-moves-710c42e97bcc47709da2cb62f67f3746` |
| Active successor move source | `ont-moves-3b23d8384a624a728a4b883dad9ea945` |
| Reference revision | `molbio_ngs_reference_revision_1f508b7f-15f1-482a-9148-c3b2054ca56d` |
| Retention policy | `pod5_and_blow5` |

The known corpus evidence is:

- Indexed BLOW5 reads: `60,784`.
- Unique move-BAM reads: `61,708`.
- Exact BLOW5-to-BAM intersection: `60,784`.
- Missing BLOW5 reads in BAM: `0`.
- `mv`, `ts`, and `ns` coverage on intersecting BAM records: `60,784` each.

Final acceptance shall re-read these values from persisted receipts or governed artifacts. Chat history is not final evidence.

## 5. Invariants

### 5.1 Source and representation

- Native POD5 remains the authoritative acquired source.
- Indexed BLOW5 remains a derived raw-signal representation.
- The retention policy stays `pod5_and_blow5`.
- The accepted POD5, BLOW5, index, routing artifact, BAM, and reference digests remain immutable.
- No acceptance step copies or mutates source biology files.

### 5.2 Move-source lifecycle

- The original failed row remains byte-for-byte preserved.
- Registration replay returns the original attempt. Replay never creates a successor.
- A fresh attempt uses the explicit path-opaque operation.
- One predecessor produces at most one winning successor for the same next attempt number.
- The successor preserves the predecessor's run, generation, raw representation, artifact digest, external registration receipt, and raw-manifest authority.
- Terminal rows remain immutable.
- Candidate IDs and public errors contain no server path.

### 5.3 Runtime and deployment

- Squigualiser runs as a bounded job in the pinned immutable image.
- The container uses network isolation, a read-only root, a non-root user, bounded CPU, memory, PIDs, file size, wall time, and output size.
- Only governed inputs are mounted read-only.
- Outputs are written below the job-owned governed output directory.
- Automatic Development sync shall not interrupt an active acceptance job. The sync controller must report `deployment_paused=true` or the active-work admission gate must defer deployment.
- A service restart during a leased job must recover the same row after lease expiry. It must not create a duplicate successor.

### 5.4 Scientific truth

- Raw waveform readiness is independent of IGV and aligned-signal readiness.
- IGV readiness requires the accepted alignment, index, and reference.
- Signal-to-read readiness requires indexed BLOW5, validated move source, approved mapping profile, and ready reform mapping.
- Signal-to-reference readiness also requires the accepted reference revision, primary reference alignment, ready alignment session, and ready realign mapping.
- Pileup readiness requires signal-to-reference readiness and bounded eligible reads.
- Missing later-stage authority does not disable an earlier ready mode.

## 6. Work package A: finish the fresh move source

### Requirements

1. Observe the existing successor row. Do not create another row while it is non-terminal.
2. Let the governed worker complete or recover the same row through its lease rules.
3. If it fails, capture the exact attempt receipt, container exit, failure code, message digest, API log window, and retained artifacts before any repair.
4. Repair only the proven root cause through a new reviewed candidate. Never edit the failed row.
5. A later explicit successor requires a new specification revision and operator authority.

### Success criteria

- Successor state is `ready`.
- `attempt_number == 2`.
- `predecessor_move_source_id` equals the original failed source ID.
- `record_count == 61,708` unless the governed BAM receipt records a different authoritative count and the discrepancy is resolved before acceptance.
- `unique_read_count == 61,708`.
- `mv_tag_count`, `ts_tag_count`, and `ns_tag_count` equal the accepted BAM record count.
- The read inventory digest is present.
- The raw-manifest digest and external registration receipt match the predecessor.
- The original failed row is unchanged.
- A repeated registration returns the original failed source.
- A repeated fresh-attempt request returns the same successor and creates no duplicate.

## 7. Work package B: calibration and mapping profile

### Requirements

1. Select a deterministic digest-bound sample of `1..100` eligible intersecting reads.
2. Run governed calibration in the pinned Squigualiser runtime.
3. Record every tested offset and score in the immutable calibration artifact.
4. Select the approved base shift from the calibration result.
5. Create one immutable mapping profile bound to the calibration artifact.
6. Use DNA molecule semantics.
7. Preserve these closed policies:
   - `primary_alignment_policy = primary_only`
   - `minimum_mapq = 0`
   - `include_supplementary = false`
   - `parameter_source = approved_calibration`
8. Keep `kmer_length`, `signal_move_offset`, and `base_shift_value` distinct.

### Success criteria

- Calibration reaches `ready`.
- The calibration receipt binds the BLOW5 digest, move-BAM digest, read-set digest, runtime image digest, sample count, tested offsets, scores, and selected base shift.
- The selected base shift is displayed in the browser with its calibration artifact ID.
- The mapping profile is immutable and visible by name and opaque ID.
- The UI shows molecule type, basecaller authority, `k`, `m`, base shift, read policy, and calibration provenance.
- A profile with missing or mismatched calibration authority is rejected.

## 8. Work package C: signal mappings

### 8.1 Signal-to-read mapping

- Filter the move BAM to the exact BLOW5 read inventory.
- Record included and excluded counts.
- Run Squigualiser `reform` with PAF output and `ss:Z` tags.
- Bind every PAF row to the exact BLOW5 read, primary BAM record, move tags, `k`, and `m`.
- Validate nonzero `ts`, stride, transformed query coordinates, target length, and sample count.
- Reject duplicate or missing IDs.

**PASS conditions**

- Mapping state is `ready`.
- Included read count is `60,784`.
- BAM-only excluded count is `924`.
- BLOW5-only missing count is `0`.
- Output PAF and index digests are present.
- Every admitted row contains a valid `ss:Z` tag.
- Parent raw representation, move source, mapping profile, and calibration IDs match the selected workbench identity.

### 8.2 Signal-to-reference mapping

- Use the ready signal-to-read mapping as the parent.
- Use the managed reference revision and exact primary reference-alignment authority.
- Run Squigualiser `realign` with PAF output.
- Validate reference coordinates, strand, CIGAR ancestry, topology, and the admitted terminal trim behavior.
- Apply the documented orientation rule for reverse-strand DNA.
- Do not fabricate an `si:Z` tag.

**PASS conditions**

- Mapping state is `ready`.
- Parent mapping ID and digest match the signal-to-read mapping.
- Reference revision, reference digest, alignment job, alignment session, and alignment digest are present.
- Each output row has valid coordinates and an accepted `ss:Z` topology.
- Reverse-strand and nonzero-`ts` compatibility checks pass.
- The mapping is indexed for bounded read and locus lookup.

## 9. Work package D: governed Squigualiser views

The workbench shall support these view jobs:

1. `raw_waveform`
2. `read`
3. `reference`
4. `pileup`

### Output contract

Each ready view publishes:

- one immutable output manifest;
- one self-contained HTML artifact when the mode uses Bokeh;
- one SVG artifact when available;
- a render log capped at `256 KiB`;
- artifact size and SHA-256;
- input and runtime provenance;
- the complete effective render parameters;
- a governed artifact URL.

### Bounds

- HTML size: at most `48 MiB`.
- SVG size: at most `4 MiB`.
- Region span: at most `250,000 bp`.
- Base limit: at most `100,000`.
- Signal sample limit: at most `2,000,000`.
- Pileup read limit: at most `100`.
- Calibration reads: `1..100`.

### Network and browser isolation

- Bokeh resources are inline.
- Final HTML contains no external HTTP(S) resources, API routes, file URLs, source paths, or secrets.
- The HTTP response repeats the restrictive content security policy.
- The browser renders HTML only inside an iframe with `sandbox="allow-scripts"`.
- The sandbox omits same-origin, forms, top navigation, popups, and downloads.
- The renderer has no network access.
- Squigualiser never starts a persistent or externally reachable server.

### Success criteria

- A selected read produces a readable signal-to-read squiggle with bases and signal aligned.
- The same selected read produces a readable signal-to-reference view at the accepted locus.
- A bounded locus produces a readable pileup with no more than the requested read limit.
- The plotted read ID, contig, coordinates, strand, base shift, and mapping mode match the persisted request.
- Artifact digests match bytes served through the governed route.
- Refresh and reopen use the same immutable artifact unless a meaning-bearing input changes.

## 10. Work package E: browser function acceptance

Acceptance shall use the managed Development browser origin and the exact deployed build. API-only checks cannot pass this work package.

### 10.1 Entry and identity

1. Open the Domain Experiment NGS instrument surface with the exact project, Domain Experiment, run ID, generation, and section context.
2. Confirm that the page displays the exact experiment and run generation.
3. Confirm that candidate selectors remain path-opaque.
4. Confirm that the workbench resolves the accepted BLOW5 representation and successor move source.
5. Confirm that no console error, page alert, or failed network request is present.

### 10.2 Critical interactions

The operator shall complete these actions with trusted browser input:

1. Select the exact run and generation.
2. Inspect the move-source attempt number, predecessor lineage, state, read counts, tag counts, and validation provenance.
3. Create or select the approved mapping profile.
4. Run or reopen calibration and inspect the selected base shift.
5. Prepare or reopen signal-to-read mapping.
6. Select one deterministic eligible read from the alignment session.
7. Open the raw waveform for that read.
8. Navigate the same read and locus in IGV.
9. Open signal-to-read output.
10. Open signal-to-reference output.
11. Open one bounded pileup.
12. Move the locus in IGV and verify the synchronized workbench locus.
13. Change the selected read and verify that stale artifacts do not remain visible.
14. Save the viewer session.
15. Reload the browser and reopen the same session.

### 10.3 Payload and persistence proof

For each state-changing browser operation:

- capture the exact request method, route, and JSON payload;
- verify that every visible choice appears in the payload;
- read the persisted API response and database row;
- confirm run, generation, representation, source, profile, mapping, reference, alignment, selected read, locus, and revision fencing;
- reacquire browser element references after reload or HMR remount.

### 10.4 Visible acceptance criteria

- Raw waveform renders inspectable sample data for the selected read.
- IGV renders the accepted alignment and reference locus.
- Signal-to-read renders aligned bases and current signal.
- Signal-to-reference renders the same read against the accepted reference coordinates.
- Pileup renders a bounded read set at the selected locus.
- Base shift and calibration provenance are visible.
- All artifact links use governed routes.
- Loading, ready, failed, unavailable, and stale states are clear and truthful.
- Earlier ready capabilities remain usable when a later capability is unavailable.
- Reload restores the persisted read, locus, IGV state, raw representation, move source, mapping profile, and mapping IDs.
- The browser console has zero uncaught exceptions and zero CSP/network-isolation violations.

### 10.5 Visual evidence

Capture normal-scale screenshots for:

1. exact experiment/run/generation selection;
2. validated move-source lineage and counts;
3. calibration and base-shift provenance;
4. raw waveform;
5. IGV at the selected locus;
6. signal-to-read squiggle;
7. signal-to-reference squiggle;
8. bounded pileup;
9. persisted session after reload;
10. one representative fail-closed state.

Text and plot labels must remain readable. A single downscaled full-page screenshot is insufficient.

## 11. Work package F: fail-closed acceptance matrix

Each row shall produce the specified refusal without creating a ready artifact or mutating accepted authority.

| Case | Required result |
|---|---|
| Wrong Domain Experiment | Reject before job creation |
| Wrong run or generation | Reject before job creation |
| POD5 or BLOW5 digest mismatch | Reject and identify stale/tampered authority without exposing a path |
| Missing or altered BLOW5 index | Raw aligned modes unavailable |
| Move BAM identity or digest mismatch | Reject mapping and view preparation |
| Move source is failed or running | Mapping unavailable |
| Arbitrary failed row with NULL `validated_at` | Fresh attempt rejected |
| Preserved exact retry-exhausted legacy row | Explicit fresh-attempt path admitted once |
| Duplicate concurrent fresh requests | One successor winner |
| Registration replay after failure | Return original attempt |
| Missing `mv`, `ts`, or `ns` | Reject affected read set |
| Duplicate read ID | Reject mapping |
| BLOW5 read absent from BAM | Reject exact full-set mapping |
| Unapproved calibration | Reject profile creation |
| Mismatched profile parents | Reject mapping |
| Invalid `k`, `m`, or base shift | Reject request |
| Wrong reference revision | Reject signal-to-reference |
| Missing alignment session | Keep IGV/raw waveform independent; block signal-to-reference |
| Stale viewer revision | Reject update with revision conflict |
| Oversized region, samples, bases, or pileup | Reject before renderer launch |
| Oversized HTML, SVG, or log | Fail publication and retain no served artifact |
| External URL, API path, or file URL in HTML | Fail publication |
| Network attempt by renderer | Fail the job |
| Symlink or non-regular publication path | Fail without touching outside bytes |
| Service restart during active lease | Recover same row; no duplicate job |
| Cancellation | Stop child processes, publish no ready artifact, and suppress retry |

Public errors shall be stable, actionable, and path-free.

## 12. Automated verification denominator

Use focused deterministic checks for the owning surfaces.

### Backend

At minimum, the final source bytes shall pass:

- `platform/api/tests/test_ont_signal_workbench.py`
- `platform/api/tests/test_ont_raw_signal_conversion_automation.py`
- `platform/api/tests/test_ont_signal_runtime_contract.py`
- `platform/api/tests/test_migration_runner_version_reconciliation.py`
- `platform/api/tests/test_ngs_molbio_runtime_record_builder.py`

Required coverage includes:

- real migrated SQLite schema and trigger enforcement;
- move-source preservation and successor lifecycle;
- calibration/profile/mapping/view persistence;
- PAF coordinate and CIGAR-topology validation;
- no-follow descriptor confinement;
- bounded rendering and artifact publication;
- cancellation and lease recovery;
- runtime/source authority.

### Frontend

At minimum, the final source bytes shall pass:

- `platform/frontend/tests/vitest/readAndSignalWorkbench.test.tsx`
- TypeScript project build.

Required mounted behavior includes:

- exact-generation identity refresh;
- controlled-input payload accuracy;
- mode independence;
- selected-read and IGV synchronization;
- stale async completion rejection;
- object URL replacement and revocation;
- iframe sandbox attributes;
- persisted viewer-session reopen;
- visible fail-closed reasons.

### Real-corpus qualification

Synthetic tests do not close PAF or Squigualiser compatibility. Run one network-denied real-corpus qualification that creates and validates reform, realign, read, reference, and pileup outputs from the fixed BFX6NB corpus.

## 13. Managed Development release

Before restart:

1. Require a current exact candidate identity: HEAD, staged tree, binary patch SHA-256, path counts, and successor source authority.
2. Require two independent `PASS` reviews against the same identity.
3. Require no active unrelated Development jobs.
4. Create and verify a database backup before a migration.
5. Preserve the Development and Production lane split.

After restart:

1. Prove API, frontend, and workflow-adapter listener ownership.
2. Prove API and frontend build identity from live responses and process working directories.
3. Prove Development database path and migration head.
4. Prove the exact runtime/source implementation record.
5. Prove automatic deployment cannot interrupt active acceptance work.
6. Leave Production untouched.

## 14. Final evidence package

The closeout package shall contain:

- exact source commit, tree, binary patch SHA-256, and patch ID;
- two independent final `PASS` reviews;
- deployed Development build identity;
- database migration head and backup path;
- fixed corpus identity table and artifact digests;
- move-source predecessor and successor receipts;
- calibration artifact and mapping profile;
- reform and realign mapping manifests;
- read, reference, and pileup view manifests;
- browser request/persistence ledger;
- browser screenshots listed in section 10.5;
- browser console and network summary;
- fail-closed matrix results;
- active-process and container-bound proof;
- source and derived artifact immutability recheck;
- explicit Production-isolation statement.

No credential value, server source path, or raw secret may enter the package.

## 15. Final gate ledger

The specification is complete only when all rows are `PASS`.

| Gate | Pass condition |
|---|---|
| G1 Corpus identity | Exact project, experiment, run, generation, raw representation, reference, and source digests match |
| G2 Move source | Successor is `ready`; original failed row is unchanged |
| G3 Intersection | `60,784` admitted reads, `924` BAM-only exclusions, zero BLOW5-only reads |
| G4 Calibration | Approved immutable artifact and visible base-shift provenance |
| G5 Profile | Immutable profile binds exact calibration and closed alignment policy |
| G6 Signal-to-read | Valid indexed reform PAF with complete parent authority |
| G7 Signal-to-reference | Valid indexed realign PAF bound to reference and primary alignment |
| G8 Squigualiser views | Read, reference, and bounded pileup artifacts pass content, size, and network checks |
| G9 Raw waveform and IGV | Both render correctly and remain independently available |
| G10 Viewer persistence | Read, locus, IGV state, mapping lineage, and revision restore after reload |
| G11 Browser function test | Every critical action in section 10 passes with trusted input and payload proof |
| G12 Fail-closed matrix | Every row in section 11 passes without authority mutation or path disclosure |
| G13 Automated checks | Current exact source bytes pass the focused backend, frontend, and TypeScript denominator |
| G14 Runtime ownership | Managed Development services and bounded renderer own the expected listeners/processes |
| G15 Final seal | One clean post-proof identity receives two independent PASS reviews |
| G16 Isolation | Production and MinKNOW hardware remain untouched |

## 16. Completion wording

Use `COMPLETE` only after G1 through G16 are `PASS`.

Use `INCOMPLETE` when implementation or evidence remains open.

Use `BLOCKED` when required authority, source data, runtime, browser access, or operator approval is unavailable.

Use `FAILED` when a scientific, security, immutability, or live-browser acceptance gate fails. A `FAILED` gate blocks deployment and readiness claims until a new exact candidate closes it.

## 17. Implementation surfaces

Expected owning files include:

- `platform/api/services/ont_signal_worker.py`
- `platform/api/services/ont_signal_workbench.py`
- `platform/api/services/ont_raw_signal.py`
- `platform/api/routers/ont_signal_workbench.py`
- `platform/api/database.py`
- `platform/api/migrations/add_ont_move_source_attempt_lineage.py`
- `platform/api/migrations/runner.py`
- `platform/api/tests/test_ont_signal_workbench.py`
- `platform/api/tests/test_ont_raw_signal_conversion_automation.py`
- `platform/api/tests/test_ont_signal_runtime_contract.py`
- `platform/api/tests/test_migration_runner_version_reconciliation.py`
- `platform/frontend/src/components/ngs/ReadAndSignalWorkbench.tsx`
- `platform/frontend/src/components/ngs/RawReadInspector.tsx`
- `platform/frontend/src/lib/api.ts`
- `platform/frontend/tests/vitest/readAndSignalWorkbench.test.tsx`
- `scripts/build_ngs_molbio_runtime_implementation_record.py`
- `schemas/ngs_molbio_runtime/runtime-source-denominator-v1.json`
- `platform/api/config/ngs_molbio_runtime/runtime_implementation_v1.json`

Implementation shall reuse these seams. It shall not introduce a parallel viewer, mapping database, artifact server, or persistent Squigualiser service.
