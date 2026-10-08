# NGS Software Reliability and Workbench Improvement Specification

> **For Hermes:** Use subagent-driven-development for later implementation after Christian approves the completed contract. This document currently authorizes review and specification work only.

**Status:** Operator decisions D1–D6 are settled and reconciled. This document defines the full NGS software/UI repair. Package-local implementation details belong to the assigned work below. The earlier independent review applies to its earlier hash; final document review remains separate from implementation authorization.

**Goal:** Make existing NGS workflows reliable from input selection through durable scientific results and usable read inspection.

**Configuration principle:** Verify existing scientific settings against relevant literature and pinned upstream documentation. Preserve settings that align. Correct demonstrated mismatches within the existing workflows. Performance follows the global allocation on the actual execution target. Inherited product limits need a concrete technical purpose; they are not scientific requirements merely because an older spec contains them. Avoid unnecessary restrictions and duplicate policy.

**Architecture:** Existing scientific jobs and their verified source artifacts remain authoritative. Independently prepared read catalogs support complete queries, while optional preview and signal products have separate readiness. Shared Project links refer to native results without copying their scientific payloads.

**Tech stack:** Existing FastAPI, SQLAlchemy/SQLite, Nextflow and managed scientific runtimes; pysam/htslib, Parquet/DuckDB, React and IGV.js.

## Operator brief

**Deliverable:** Repair the eight existing NGS workflows across launch, execution, results, read/signal inspection and saved Project reopen. Ship working UI controls for those paths. Instrument control and new scientific workflows are excluded.

**Settled:** Global target-aware resource limits; workload-aware performance without fixed response-time gates; verification of existing scientific configuration with changes limited to demonstrated mismatches.

**Implementation sequence:** P1 launch/settings and scientific identity → P2 independent catalog/preview state → P3 complete queries and artifact delivery → P4 deterministic preview and selected-read overlay → P5 read/signal interaction and reopen → P6 Project/native links → P7 historical migration and managed Development acceptance. Section 5 defines package evidence. P3/P4 can be partitioned only after the P2 API is stable and file ownership is assigned.

**Decisions complete:** D3 separates catalog and preview; D4 selects portable storage; D5 requires a reference and QC for the named FASTQ-QC workflow. D6 checks 1440p/1080p desktop and Galaxy S25 FE mobile use while requiring responsive behavior beyond that device. Section 7.6 records all decisions.

**Technical dependencies:** Identify the global resolver's actual producer contract before wiring its consumer. Complete the portable delivery mechanism within the existing storage implementation. These dependencies must not produce invented NGS-only resource policy or restore mandatory fs-verity.

**Completion:** Each existing workflow produces and reopens its declared native result. Complete-read access works independently of preview failure. Saved references remain exact, and the affected UI paths work on the managed origin. Verification scope is defined in Section 6; running it requires execution authority.

## 1. Approved scope and current authority

Christian selected the full NGS software flow: inputs and references through execution, results, read/signal viewers, saved reopen, and Project links. Christian explicitly confirmed that UI is in scope. This includes launch forms, workflow selection, typed scientific controls, progress and result states, tables and viewers, saved-analysis discovery, historical navigation, Project panels, download actions, responsive layout and click/error feedback on these surfaces. UI improvements are part of each relevant package's deliverable and acceptance gate.

Instrument control and new scientific workflows are excluded. This pass creates the major bug-fix and improvement specification. Preserve all partial implementation work; do not run project tests, scientific jobs, migrations, or deployments during this review.

The old preview successor remains unchanged at:

- Path: `.hermes/plans/2026-09-02_110935-ngs-reusable-preview-full-read-access-successor.md`.
- SHA-256: `44ed80baea8afcd8ae90360d570e9acd94f5deb08e404b7f26e5bb7176793ab4`.

That document supplies the previous detailed read semantics. Its package coupling and BGZF packing premise require revision. Its earlier PASS cannot approve this new architecture. Final contract freeze must reconcile every affected schema and section; an implementer must not choose between conflicting documents.

### Source snapshot

- Repository: `/home/dalab/worktrees/bms-ngs-ui-copy-trim-20260831`.
- Reviewed HEAD: `f5f4ea01667aca433f334ffd89ae733d3b8a609e`.
- Dirty partial preview work is part of the reviewed candidate and must remain distinguishable from committed code.
- Local `origin/test` reference observed during review: `c2766c3cf8af50d8d40447822a2fc5b459df10b2`. The scoped diff across API, frontend, NGS workflows, configuration and NGS schemas was empty. This was a local ref comparison, not a fresh remote or deployed-source check.
- Review evidence is static source evidence. No current runtime, throughput, database correctness, or browser acceptance is inferred.

## 2. Existing workflow denominator

`platform/api/config/models/nanopore.yaml:16–29` declares eight canonical workflows. All eight corresponding entrypoint files exist. Presence establishes inventory only.

| Existing workflow | Entrypoint | Required acceptance coverage |
|---|---|---|
| `ont_basecall_dna` | `workflows/ngs/ont_basecall_dna.nf` | POD5 selection, effective DNA settings, accepted native outputs, result reopen |
| `ont_basecall_rna` | `workflows/ngs/ont_basecall_rna.nf` | RNA settings and valid inputs, output interpretation, result reopen |
| `ont_plasmid_qc` | `workflows/ngs/ont_plasmid_qc.nf` | Exact selected input mode, reference identity, QC result and read access |
| `ont_construct_screening` | `workflows/ngs/ont_construct_screening.nf` | Expected construct authority, execution settings, scientific verdict and lineage |
| `ont_methylation_analysis` | `workflows/ngs/ont_methylation_analysis.nf` | Required source tags, modification semantics, complete native evidence |
| `ont_fastq_qc` | `workflows/ngs/ont_fastq_qc.nf` | FASTQ admission, exact reference-backed QC and native result; D5 requires correcting the misleading reference-optional description |
| `ont_pooled_reference_assignment` | `workflows/ngs/ont_pooled_reference_assignment.nf` | Frozen competitive reference set, per-unit identity, attribution and ambiguity |
| `wf_clone_validation` | `workflows/ngs/wf_clone_validation.nf` | Pinned wrapper inputs/settings, native outputs and exact result interpretation |

The final parity ledger must cover each existing workflow's supported input modes and each public launch surface. A registry entry does not establish UI or scheduler reachability. New scientific analyses and new upstream tools require a separate scope decision.

## 3. Evidence-backed findings already checked

Paths are relative to the reviewed repository. Source line references bind this snapshot.

| ID | Finding | Evidence | Classification and effect |
|---|---|---|---|
| NGS-01 | Complete catalog availability remains coupled to preview publication. | Old spec:7,194–203,491–497; membership and identity coupling at 288–289,783–794. | Spec contradiction. Preview failure blocks complete table access and preview changes alter table identity. |
| NGS-02 | The BGZF bound assumes dense packing that the standard BAM writer does not guarantee. | Installed htslib `sam.c:881` calls `bgzf_flush_try`; `bgzf.c:1989–1992` flushes when a record exceeds remaining space. Old spec:639–645. | Spec defect. One handle does not establish the stated byte bound. No actual overflow measurement was performed in this review. |
| NGS-03 | The ready-package loader scans all locator rows for each catalog row. | `platform/api/services/ngs_alignment_sessions.py:3005–3019`. | Candidate performance defect. Ordinary package resolution can perform population-by-record work. |
| NGS-04 | Artifact delivery snapshots the complete source and rejects files above 2 GiB. | `platform/api/services/ngs_alignment_sessions.py:413–454`; `platform/api/routers/ngs_alignment_sessions.py:1341–1354`. | Existing delivery limit conflicts with large complete-BAM access. Capacity failure is returned as an integrity conflict. |
| NGS-05 | Shared analytical reads hash the entire Parquet artifact on each snapshot opening. | `platform/api/services/scientific_artifacts/writer.py:309–359`; query callers in `scientific_artifacts/query.py`. | Interactive I/O cost. Bounded response rows do not bound bytes read before the query. |
| NGS-06 | The file renamed v5 still contains the rejected deadline and overflow rewrite. | `platform/api/services/ngs_alignment_presentation_v5.py:758–775,868–874,1094–1113`. | Unfinished implementation. It is not evidence that the intended core-only preview failed. |
| NGS-07 | Recovery retains every decoded BAM record and complete locator rows. | `platform/api/services/ngs_alignment_presentation_v5.py:535–570,582–584`. | Candidate RAM-scaling defect. Compressed artifact limits do not bound decoded memory. |
| NGS-08 | Presentation reopen chooses the newest job/session row without source or policy filtering. | `platform/api/services/ngs_alignment_presentation.py:104–125`. | Candidate authority-selection defect when generations coexist. |
| NGS-09 | Local alignment selection precedes a saved viewer's session; a mismatch then removes the saved URL identity. | `platform/frontend/src/components/NGSToolkit.tsx:2688–2738`. | Existing saved-reopen defect. Explicit saved nondefault selection can lose authority to local state. |
| NGS-10 | Dedicated NGS terminal dispatch covers FASTQ-QC and the bounded external signal-alignment lane; remaining NGS modes take the generic result-finalization branch. | `platform/api/services/nextflow.py:2832–2878`; `platform/api/services/ont_ngs_completion.py:74–120`; generic `result_ingester.py:4335–4344`. | Broader completion-coverage gap. Freeze and enforce each existing workflow's native result contract before claiming common NGS acceptance. Static evidence does not prove a specific historical result is corrupt. |
| NGS-11 | Browser reference gating applies to reference-optional basecalling. | `platform/frontend/src/components/NanoporeTemplate.tsx:1061,1090–1098,1263–1267`; backend required set in `platform/api/routers/ont_runs.py:143–151,1042–1049`. | Existing launch contradiction. A valid POD5-only scientific input is blocked by a mandatory reference selection. Preserve any separately required Project context. |
| NGS-12 | FASTQ-QC can disable QC although completion requires the skipped artifacts. | `workflows/ngs/ont_fastq_qc.nf:104–149`; `platform/api/services/ont_ngs_completion.py:613–629`; `NanoporeTemplate.tsx:2077–2084`. | Existing admitted-request/terminal-contract contradiction. Product decision D5 applies. |
| NGS-13 | Clone fallback maps POD5 plasmid QC to DNA basecalling. | `platform/frontend/src/lib/nanoporeCloneState.ts:16–31`; `NanoporeTemplate.tsx:1270–1282`. | Existing scientific-operation substitution. Pooled assignment also needs an explicit clone disposition. |
| NGS-14 | Clone-validation auxiliary scientific inputs are omitted from browser submission. | Backend confinement at `platform/api/routers/ont_runs.py:568–571`; clone payload at `NanoporeTemplate.tsx:1326–1341`; native consumer `modules/ngs/clone_validation.nf:56–63,112–115`. | Existing settings-parity gap affecting primers, insert reference, host reference and regions BED. |
| NGS-15 | The typed submission envelope contains an open operator parameter dictionary. | `platform/api/routers/ont_runs.py:214–223`; `platform/api/model_registry.py:239–267`. | Existing admission gap. Generic registry validation ignores unknown keys and does not enforce general scalar types. Workflow-specific validation must close the operator boundary. |
| NGS-16 | Historical Project membership is projected through the current molecular revision. | `platform/api/routers/ngs_molbio_n5.py:661–668,719–725`. | Existing historical identity defect. Current revision content can be paired with an older receipt/digest. |
| NGS-17 | Full Project context intercepts existing domain section routes. | `platform/frontend/src/components/molbio-ngs/DomainExperimentWorkspace.tsx:888–927`; sibling `ProjectHubShell.tsx:12–19,427`. | Existing navigation gap. Dataset, workflow-plan, evidence and history controls require explicit reachable destinations. |
| NGS-18 | Saved NGS discovery uses fewer identities than result routing recognizes. | `platform/frontend/src/components/NGSToolkit.tsx:2371–2389`; `platform/frontend/src/lib/ngsResultRouting.ts:10–20,52–61`. | Existing compatibility mismatch. Directly reopenable historical/native jobs can be absent from discovery. Affected historical row counts were not inspected. |
| NGS-19 | Receipt-bound workup history requires a mutable saved-sequence projection. | `platform/api/routers/molbio_ops.py:383–409`. | Existing availability dependency. Valid immutable evidence can be hidden when its mutable projection is unavailable. No data-loss incident is claimed. |
| NGS-20 | Project receipt admission rejects recognized historical NGS model IDs. | `platform/api/services/molbio_ngs_member_receipts.py:572–574,615–617`; searches in `services/global_experiments/adapters.py:3071–3078,3139–3146`. | Existing compatibility boundary. Normalize only identities with sufficient verified ownership and scientific authority. |
| NGS-21 | Delayed exact-read lookup can overwrite newer selection. | `platform/frontend/src/components/ngs/ReadAndSignalWorkbench.tsx:793–807,848–854`. | Existing asynchronous-selection defect. Lookup response fencing uses workbench identity without a per-selection request token. |
| NGS-22 | Signal-pane IGV navigation derives reference span from read length. | `platform/frontend/src/components/ngs/ReadAndSignalWorkbench.tsx:961–967`. | Existing coordinate defect for clipping, insertions, deletions and skipped reference spans. |
| NGS-23 | Schema installation, historical backfill and frontend cutover are separate unfinished obligations. | Registered migration: `platform/api/migrations/runner.py:87–88,168–169`; old spec:1272–1296; `platform/frontend/src/lib/ngsAlignmentSession.ts:612,629–634`; `RawReadInspector.tsx:258–272`. | Unfinished preview scope. The additive table migration exists; the prescribed historical backfill and new complete-read frontend path remain unfinished. |

| NGS-24 | FASTQ-QC is advertised as alignment-optional although its entrypoint requires a reference. | `platform/api/services/ont_ngs_contract.py:267–271`; `workflows/ngs/ont_fastq_qc.nf:49–58`. | Existing contract/copy mismatch. D5 retains the implemented reference-backed workflow and correcting the misleading description. Adding reference-free science requires separate scope approval. |

### 3.1 Upstream configuration verification

This is verification of existing settings. Preserve aligned behavior; change only demonstrated mismatches. No tool upgrade, scientific retuning or new analysis is implied.

| Surface | Current implementation and upstream evidence | Disposition |
|---|---|---|
| FASTQ alignment preset | `NanoporeTemplate`/registry uses `map-ont`; `modules/ngs/fastq_align.nf:22,57–64` passes that preset. [minimap2 v2.24 README](https://github.com/lh3/minimap2/blob/v2.24/README.md) documents `map-ont` for Nanopore reads. Registry documentation names 2.24 compatibility. | Preserve. Newer upstream documentation also lists `lr:hq` for Q20 reads on v2.27+, which does not establish a defect in this version-specific setting. Installed runtime version was not exercised in this check. |
| Modkit probability threshold | Registry default is `0.5`; `modules/ngs/modkit_pileup.nf:73–92` validates [0,1] and passes `--filter-threshold`. [Modkit v0.6.4 filtering examples](https://github.com/nanoporetech/modkit/blob/v0.6.4/book/src/filtering_details.md) define that probability threshold; its [pileup documentation](https://github.com/nanoporetech/modkit/blob/v0.6.4/book/src/intro_pileup.md) also describes data-derived thresholds. | The explicit threshold is supported; it is not the tool's adaptive default. No evidence here establishes that 0.5 is scientifically wrong or universally optimal. Preserve operator-selected values. Any default change needs assay-specific evidence. |
| Modified-base tag precheck | `modules/ngs/modkit_pileup.nf:31–32` counts regex matches without MM/ML cardinality validation and accepts wider ML integer types than [SAMtags](https://github.com/samtools/hts-specs/blob/master/SAMtags.tex). The standard permits empty coordinate lists and defines ML as unsigned-byte `B:C` with matching cardinality. | Precheck validation gap, NGS-25 below. Keep legitimate no-call/empty evidence distinct from malformed tags and from whether pileup has informative sites. The downstream modkit parser can still reject invalid input; no corrupt successful result is claimed. |

The Dorado lock records explicit model identities and chemistry constraints; their presence alone is not proof of scientific alignment. This bounded check does not certify every Dorado model, clone-validation setting, installed container or assay threshold. Remaining source verification stays within existing workflows and proposes changes only when upstream behavior and the product demonstrably disagree.

**NGS-25: Modified-base tag precheck is not semantic validation.** Source: `modules/ngs/modkit_pileup.nf:24–51`. The regex accepts ML array element types outside the specified unsigned-byte type and does not check MM/ML cardinality. Empty modification lists are legal SAM but do not satisfy this precheck's informative-call requirement. Reuse the pinned tool's semantic parsing where available instead of duplicating it, and distinguish malformed input from valid data with no informative sites. Cover skip flags, empty calls and cardinality in the existing integration acceptance. The current MM wildcard already accepts skip flags; their presence alone is not a demonstrated rejection bug. This is a precheck/diagnostic finding, not evidence of incorrect completed methylation results. No code or test was changed or executed.

### Review reconciliation and evidence limits

All three complete outputs from `deleg_96ab3dd0` were read. Duplicate findings were merged into the ledger above. The executor reopened the decisive source for the added launch, historical-revision, discovery, workup, receipt-admission and selected-read defects. The workspace citation was resolved to its actual `components/molbio-ngs/` path.

| Reviewer group | Disposition | Canonical findings / contract |
|---|---|---|
| Launch review 1–5 | Accept source contradictions; narrow the reference fix to avoid removing unrelated ownership rules. QC-off semantics remain an operator decision. | NGS-11–15; Sections 4.1 and 7. |
| Launch review 6 | Merge with the existing terminal-coverage finding. | NGS-10; Section 4.2. |
| Persistence review 1–5 | Accept bounded source findings. Historical row counts and actual data loss remain unproven. | NGS-16–20; Section 4.7. |
| Viewer review 1–5 | Merge with prior catalog, writer, delivery and resource findings. | NGS-01–07; Sections 4.3–4.5. |
| Viewer review 6 | Accept the qualification: table migration exists, while backfill and reader activation remain unfinished. | NGS-23; Section 4.8. |
| Viewer review 7–8 | Accept selected-read race and reference-span defects. | NGS-21–22; Section 4.6. |

Retain the existing native MolBio/NGS database, immutable receipts and Project adapters. A new store is not justified by these findings. Selected-job reopen already fetches full detail; the older summary-only criticism is excluded. Pooled assignment has an explicit submission/profile path, so its absence from one direct mapping is not recorded as a routing defect.

This bounded review covers the named seams across the existing workflow family. It does not exhaustively prove every vendor parameter, scheduler/reconciliation writer, connector convergence path or historical row. Those enumerations remain P0 acceptance obligations. The reviews examined source, not the final draft's exact bytes, and therefore do not constitute an independent specification PASS.

## 4. Product requirements for the enlarged repair

### 4.1 Input, reference and settings authority

- Each launch resolves an explicit existing workflow and supported input mode. Validate incompatibilities before creating a queued job or allocating execution output.
- Preserve exact selected dataset members and immutable reference revisions. A multi-record selection must not silently become a first-record selection or an invented concatenated reference.
- Human and agent requests use the same typed settings and validation. Apply `docs/Model_Configuration_Operator_Control_and_Agent_Parity.md` to existing integrated workflows.
- Distinguish operator settings from runtime paths and scheduler placement. Saved, cloned and replayed requests must preserve effective scientific values.
- Preview and submission must agree on the compiled request. Unsupported combinations produce actionable typed errors.
- A single shared resolver derives source and reference authority from persisted scientific evidence. Completion, presentation workers and retry/reopen readers must use it. Historical compatibility must be explicit.

### 4.2 Scientific execution and completion

- Preserve existing scheduler and Nextflow ownership. Do not create another scientific queue to repair visualization.
- Validate native scientific outputs before one guarded terminal publication. An unsuccessful terminal compare-and-swap publishes neither a result nor a derived-work request.
- Transactional insertion of derived-work intent can remain part of successful scientific completion. Building a preview stays outside that transaction.
- Failure of a derived catalog, preview or signal operation must not rewrite accepted scientific job status or source artifacts.
- Retrying a derived operation must not rerun the scientific analysis. Automatic retry loops remain excluded from the preview repair.

### 4.3 Complete catalog and exact record access

- Publish catalog and record-locator authority independently from preview success. One worker may initially prepare both, but it must expose separate durable outcomes.
- Define logical reads from exact accepted BAM query names. Preserve anomaly states and all source-record identities from the prior successor unless a recorded correction replaces them.
- Catalog identity binds scientific source and catalog semantics. Preview membership is a separately bound relation; preview policy does not redefine the underlying read population.
- Complete filtering, counts and supported sorting operate before pagination. Exact lookup uses a body-safe identity. Record pages verify locators against source records.
- Optional raw metrics use an exact left join that preserves the catalog population. Missing or invalid signal metrics leave alignment fields usable.
- Separate alignment-population identity from optional signal-metric generation. Freeze cursor invalidation rules for each supported sort before implementation.

### 4.4 Preview and selected-read overlay

- Retain one reusable core-alignment preview and one owned selected-read track. Normal locus navigation creates no derivative BAM.
- Keep the original complete BAM unchanged. The preview drops optional tags under an explicit projection policy; tag-dependent methylation evidence remains available through the scientific result and original artifacts.
- Retain the prior preview policy targets for reassessment: 5,000 logical reads, 20,000 records, and 67,108,864 bytes. Preserve the separate selected-read overlay limits of 16 MiB, 256 records and 10 seconds unless a recorded contract decision changes them. The removed presentation wall-clock deadline must not be reintroduced through these overlay controls.
- The prior accepted plasmid sample remains a required regression fixture. Its target of 5,000 preview reads must be checked against the corrected writer bound. If it cannot be met, report the conflict before changing the target, projection or byte ceiling.
- Determine deterministic whole-read admission before opening preview output. Retain one write and one indexing operation per attempt.
- Derive a conservative bound from the pinned standard writer's actual packing. A custom raw-BAM serializer is outside the default approach and requires a separate decision if the standard writer cannot meet the approved target.
- A reduced or empty valid preview can be ready. Unexpected serializer or bound failures are terminal and never trigger remove-one rewrites.
- Preserve the existing browser auto-mount ceiling of 536,870,912 bytes. Explicit complete-file download has a separate delivery contract.
- Overlay failure leaves the preview and catalog usable. Keep exact-read eligibility and explicit user retry behavior.

### 4.5 Verified serving and resource control

- Perform full semantic validation at publication and independently when adopting unreferenced or recovered artifacts. Ordinary status, table and range requests must not repeat full package reconstruction.
- Reuse verified immutable objects with a defined ownership and mutation model. A pathname or timestamp cache alone does not establish byte identity.
- Specify replacement, same-inode modification, active-stream lifetime, eviction and restart behavior before accepting verification reuse.
- Large-file range delivery must avoid a full private copy for every request. Preserve authorization, exact content identity, range and conditional-request semantics.
- Report capacity exhaustion separately from source corruption. Check disk admission before expensive construction.
- Stream catalog/locator batches and source verification. Do not retain all decoded BAM records in memory during recovery.
- NGS follows the global resource-limit parameter for the selected execution target. Christian expects most scientific work to use cloud/remote jobs. Host-specific NGS resource defaults are excluded. Account for temporary database files, sorts, Parquet output, preview output and concurrent work within the effective global allocation. The local workstation's measured capacity does not define the scientific input envelope or remote allocation.
- Trace the global parameter through submission, persisted effective settings, scheduler placement and the actual worker. Define its exact field, units, default resolution and enforcement owner from the global contract before wiring the NGS consumer. Preserve that global owner; this specification does not create a competing resource policy or authorize building unrelated global infrastructure.
- Resource acceptance covers the configured local and remote/cloud targets separately. Record requested and effective limits with the actual execution target. Catalog construction, API serving and browser rendering can run on different targets; each follows the allocation of the process that performs the work. A missing global-contract field is an explicit dependency, not permission to invent an NGS-only fallback.
- Record bytes hashed, source decodes, locator seeks and package reloads in performance evidence. A writer-call count alone is insufficient.

### 4.6 Read and signal UI

- Catalog, Detail and IGV remain accessible to alignment-only jobs.
- One parent owns selected job/session/read identity. A saved explicit destination takes precedence over stale local selection after server validation.
- Signal is enabled by exact server-issued per-read capability. Keep calibrated-pA units and current signal-to-read mapping semantics.
- Latest selection wins during asynchronous requests. Cancel or retire stale work and remove only the owned selected-read overlay.
- Failures remain local to the affected optional operation. Keep compact primary controls with direct click feedback and closed Diagnostics.
- Preserve existing scientific views, including methylation-specific evidence that the core preview cannot display.

### 4.7 Saved results and Project links

- Native NGS stores own scientific result identity. Project/Experiment/Dataset references bind immutable native receipts and revisions without copying source payloads.
- Inventory direct NGS reopen, saved signal sessions, MolBio handoffs, and Project-origin links separately. A working direct route does not establish Project reopen.
- Reload and navigation must recover the same accepted job, reference revision, alignment session and supported read selection.
- Cross-store delivery must be idempotent and visible when pending or rejected. Never infer successful attachment from the existence of an outbox row.
- Do not silently reinterpret an old result under a new scientific reference or current dataset head.

### 4.8 Migration and compatibility

- Preserve historical accepted analyses and protected rows. Keep predecessor documents and scientific source bytes unchanged.
- Separate schema installation, historical request backfill, derived construction and reader activation.
- Backfill must be explicit, idempotent and source-bound; GET routes must not compensate for missing migration by creating rows or building artifacts.
- Resolve exact supported source/policy authority during reopen. Newest-row selection is insufficient when generations coexist.
- Define treatment of the dirty unpublished migration and any already-installed schema after inspecting actual migration authority. Never assume an old number is safe to rewrite.

### 4.9 UI behavior and usability across the product

UI acceptance is mandatory for every changed operator path. Preserve the existing BMS shell and domain ownership. Any proposed change to navigation structure or scientific behavior must be explicit in the phase contract.

| Surface | Required UI repair or improvement | Browser acceptance |
|---|---|---|
| Workflow selection and launch | Gate reference selection by the chosen scientific workflow; keep any independently required Project context. Match displayed input requirements to API admission. | Reference-optional basecalling can submit without an invented reference; required-reference workflows show an actionable blocker. |
| Clone and scientific settings | Use exhaustive canonical workflow mapping. Expose the existing clone-validation auxiliary inputs through typed selectors and preserve saved values. Show any profile-fixed value and reason. | Clone each supported workflow/input combination; operation, reference and settings remain unchanged. Unsupported clone destinations refuse explicitly. |
| Submission and progress | Keep request errors near the relevant control. Show accepted submission and server-owned progress; distinguish scientific result status from catalog, preview and signal preparation. | One click has visible pending/result feedback; failure does not leave a false success indication or create an accidental duplicate. |
| Results and complete-read table | Expose complete-population search, sorting, filtering and pages. Keep Detail available when preview preparation fails. Display truthful total and filtered counts. | Reach reads beyond the old bounded population; failed preview does not hide available rows or exact detail. |
| IGV and raw signal | Use one selected-read owner. Fence delayed responses by current read/population; navigate using authoritative reference-consuming coordinates. | Delayed lookup A cannot replace selected B. Clipped and gapped reads navigate correctly; units remain calibrated pA. |
| Saved analyses and historical reopen | Use consistent supported NGS identities for discovery and direct routes. Restore explicit saved job/session/read selection before local defaults. | Every recognized eligible identity can be found and reopened; refresh and Back retain the supported selection. |
| Project and MolBio panels | Retain reachable existing dataset, workflow-plan, evidence and history controls. Historical views display attached revisions with exact historical links. | Attach revision A, advance to B, and reopen the old state: content and link remain A. Existing section deep links reach their intended controls. |
| Download and failure recovery | Keep complete BAM/BAI actions available independently of preview. Explain capacity and integrity failures separately; keep technical internals in closed Diagnostics. | Approved large-file downloads work through the real UI. Optional failures leave other ready content usable and offer only the valid retry action. |

Use compact controls and readable labels. Preserve keyboard access and visible focus. Loading, empty, unavailable and error states need distinct behavior; a missing optional signal is not a failed scientific analysis. Responsive acceptance covers the affected desktop and narrow/mobile layouts, with exact widths set in P0. Existing mobile read-first behavior remains the baseline; complex editing remains desktop-first unless Christian approves a changed interaction.

### 4.10 Specific fixes and checks from the broader review

- NGS-12: recommend making QC visibly mandatory for the named FASTQ-QC workflow and rejecting `run_fastq_qc=false` before scheduling. D5 must settle this; preserving QC-off instead requires an explicit reduced terminal contract.
- NGS-14–15: add the supported primers, insert-reference, host-reference and regions-BED controls. Close workflow-specific operator settings at the ONT boundary without rejecting legitimate server enrichment in the generic registry. Unknown keys, wrong scalar types and inactive fields must fail before receipt consumption or queue insertion.
- NGS-10: give every canonical workflow required and conditional native-output validation. Missing or corrupt required outputs block scientific completion; a legitimate negative scientific verdict remains a completed analysis.
- NGS-16–20: resolve historical views from immutable receipts/revisions. Treat current-head comparison as optional metadata. Use explicit supported historical identity normalization and preserve ownership checks. Missing historical authority must remain a visible compatibility blocker.
- NGS-21–22: tie request tokens to the selected read and population. Reference span comes from authoritative alignment coordinates, never sequence length.
- NGS-23: exercise old database plus retained v3 artifacts through explicit backfill and reader activation. A table-creation migration alone does not satisfy this gate.

## 5. Proposed work packages and acceptance gates

These are specification-level packages. They require contract freeze before implementation tasks are dispatched.

| Package | Scope | Required evidence before acceptance |
|---|---|---|
| P0: Contract, source and UI reconciliation | Complete findings ledger, workflow/mode inventory, source snapshot, schema/version decisions, UI route/control inventory and performance envelope. | Every material finding has a disposition. Each supported workflow has a traced launch-to-result path with a named UI entrypoint, state behavior and browser check. Freeze affected desktop/mobile viewports and any changed layout. |
| P1: Launch UI and scientific identity | Repair input/reference/settings defects, clone behavior and shared source resolution. | Mounted launch and clone round trips preserve operation and values; browser and agent payloads agree. Invalid input creates no queued work. Native scientific outputs receive workflow-specific validation. |
| P2: Durable derived-work split and result states | Separate catalog readiness from optional preview and show those independent states in results. | Scientific completion remains unchanged; failed preview leaves catalog usable in the UI. Lease loss, shutdown and explicit retry preserve ownership. |
| P3: Artifact delivery and complete-table interaction | Verified object reuse, large-file delivery, streamed catalogs and complete queries with UI search/filter/sort/page controls. | Real UI reaches records beyond the old cap and starts authenticated downloads. Source files over the prior snapshot limit work within approved resources; warm page access avoids full-population reload. |
| P4: Deterministic preview and selected-read controls | Correct writer bound, core projection, fixed-pass preview and exact selected-read overlay behavior. | Native parser and block-packing checks pass. The browser keeps one preview and one owned overlay; navigation creates no locus BAM. Reduced/empty previews have truthful visible states. |
| P5: Unified read/signal and saved reopen | Parent-owned selection, exact signal capability, authoritative coordinates and saved-view restoration. | Mounted tests and approved browser replay cover delayed responses, rapid switches, nondefault session restore, calibrated pA and local optional failure containment. |
| P6: Native and Project UI continuity | Fix cross-store identity, saved discovery and Project-panel routing using existing owners. | Historical revision content and links remain exact. Dataset, workflow-plan, evidence and history deep links reach functional controls. Pending/conflict states are visible; scientific payloads are not copied. |
| P7: Historical cutover and release | Explicit migration/backfill, contract closure, reviewed candidate and managed Development release. | Fresh verified backup and preserved historical rows; idempotent migration; exact source/API/UI identity. Accept the affected desktop and narrow/mobile flows on the managed origin with approved real data. |

Dependencies: P0 precedes implementation. P1 authority work feeds P2 and P6. P3 and P4 use P2 identities; P5 follows their API freeze. P7 requires acceptance of all relevant packages. Distinct file ownership is required for parallel work.

### Existing source and test surfaces

The following paths are verified to exist. The final change allowlist is narrower than this review map and must follow confirmed findings.

- Launch/settings: `platform/api/config/models/nanopore.yaml`, `platform/frontend/src/lib/nanoporeLaunchPayload.ts`, `platform/frontend/src/lib/nanoporeCloneState.ts`, `platform/api/services/nextflow.py`, and the eight entrypoints in Section 2. Existing API gates include `test_ont_ngs_submission.py`, `test_ont_ngs_operator_settings.py`, `test_ont_ngs_runtime_controls.py`, and `test_ont_ngs_workflow_products.py` under `platform/api/tests/`.
- Scientific terminal publication: `platform/api/services/ont_ngs_completion.py` and `platform/api/services/nextflow.py`. Reuse `platform/api/tests/test_ont_ngs_completion.py`; preserve terminal CAS coverage when resolving the partial candidate.
- Catalog/presentation: `platform/api/services/ngs_alignment_sessions.py`, `ngs_alignment_presentation.py`, `ngs_alignment_presentation_worker.py`, `ngs_alignment_presentation_v5.py`, and `platform/api/routers/ngs_alignment_sessions.py`. Existing focused tests are `test_ngs_alignment_sessions.py`, `test_ngs_alignment_presentation_worker.py`, `test_ngs_alignment_presentation_migration.py`, and `test_ngs_alignment_presentation_v5_performance.py` in `platform/api/tests/`.
- Query/delivery: `platform/api/services/scientific_artifacts/query.py`, `platform/api/services/scientific_artifacts/writer.py`, and the alignment service/router above. Shared helper changes require consumer-specific regression coverage; the enlarged NGS scope does not authorize unrelated scientific redesign.
- Viewer ownership: `platform/frontend/src/components/NGSToolkit.tsx`, `platform/frontend/src/components/ngs/RawReadInspector.tsx`, `platform/frontend/src/components/ngs/ReadAndSignalWorkbench.tsx`, `platform/frontend/src/lib/ngsAlignmentSession.ts`, and `platform/frontend/src/lib/ngsAlignmentViewer.ts`.
- Native/Project persistence: `platform/api/routers/molbio_ngs_experiments.py` and `platform/api/routers/ngs_molbio_n5.py`. Existing tests include `test_molbio_ngs_cross_plane_lineage.py`, `test_molbio_ngs_restart_reopen.py`, `test_molbio_ngs_experiment_management.py`, and `test_ont_ngs_hierarchy_authority.py` in `platform/api/tests/`.

A file name establishes an available test surface, not executed or sufficient coverage.

## 6. Validation plan, deferred during this review

Existing API invocation, from `platform/api`:

```sh
uv run --frozen --group dev python -m pytest <approved-focused-test-files>
```

The initial focused test surface is named below. Add assertions to these existing files where they cover the changed behavior. A new test file must be assigned to its owning package and added to the runner before that package's implementation review; this does not authorize unrelated suites.

| Package | Initial focused acceptance files |
|---|---|
| P1 | API: `test_ont_ngs_submission.py`, `test_ont_ngs_operator_settings.py`, `test_ont_ngs_runtime_controls.py`, `test_ont_ngs_workflow_products.py`, `test_ont_ngs_completion.py`. Frontend: `tests/nanoporeSettingsContract.test.ts`, `tests/vitest/ngsWorkflowChooserMounted.test.tsx`, `tests/vitest/ngsPayloadMounted.test.tsx`. |
| P2 | API: `test_ont_ngs_completion.py`, `test_ngs_alignment_presentation_worker.py`. Frontend: `tests/vitest/ngsResultRoutingMounted.test.tsx`, `tests/vitest/rawReadSortableWorkbench.test.tsx`. |
| P3 | API: `test_ngs_alignment_sessions.py`, `test_ngs_alignment_presentation_v5_performance.py`. Frontend: `tests/ngsAlignmentSession.test.ts`, `tests/vitest/rawReadSortableWorkbench.test.tsx`, `tests/vitest/ngsAlignmentAccessLifetimeMounted.test.tsx`. |
| P4 | API: `test_ngs_alignment_sessions.py`, `test_ngs_alignment_presentation_v5_performance.py`. Frontend: `tests/ngsAlignmentViewer.test.ts`, `tests/vitest/readAndSignalWorkbench.test.tsx`. |
| P5 | Frontend: `tests/vitest/readAndSignalWorkbench.test.tsx`, `tests/vitest/rawReadSortableWorkbench.test.tsx`, `tests/vitest/ontSignalIdealComparison.test.tsx`, `tests/vitest/ngsResultRoutingMounted.test.tsx`. API: `test_ngs_alignment_sessions.py`. |
| P6 | API: `test_molbio_ngs_cross_plane_lineage.py`, `test_molbio_ngs_restart_reopen.py`, `test_molbio_ngs_experiment_management.py`, `test_ont_ngs_hierarchy_authority.py`. Frontend: `tests/vitest/ngsProjectPanelMounted.test.tsx`, `tests/vitest/molBioProjectHubMounted.test.tsx`. |
| P7 | API: `test_ngs_alignment_presentation_migration.py`, plus accepted P1–P6 regression assertions and the source-bound historical/browser checks below. |

API basenames resolve under `platform/api/tests/`; frontend paths resolve under `platform/frontend/`. The current `vitest.md.config.ts` explicitly includes the listed mounted files. Structural tests in `test_ont_ngs_workflow_products.py` currently establish entrypoint and wiring facts; semantic completion assertions must be added for P1. File presence is not acceptance evidence.

After execution approval, run the selected API files with the invocation above. From `platform/frontend`, use `pnpm exec tsx --test <selected-top-level-test-files>` for Node tests and `pnpm exec vitest run --config vitest.md.config.ts <selected-mounted-test-files>` for mounted tests. The release build uses `pnpm run build:isolated` to keep generated output away from the managed runtime. Any added mounted file must be included in the Vitest configuration. No test or build command in this section has been executed during this review.

Required later coverage includes malformed identities, missing raw metrics, all alignment anomaly states, empty and reduced previews, records at BGZF boundaries, long CIGAR exclusion, original optional tags, same-inode mutation, stale cursors, expired leases, losing terminal CAS, active-stream eviction and failed historical backfill. Test declared error semantics rather than source-token presence alone.

Maintain separate evidence for static source review, focused tests, fixture execution, accepted scientific data, migration rehearsal and deployed browser behavior. Retained historical measurements do not approve this candidate.

### 6.1 Historical and release preflight

These are execution gates, with defined failure behavior. They require later execution approval and must run against the selected managed Development target immediately before mutation.

1. Pin the actual service owner, canonical checkout, API/frontend revision, native database paths and migration history. Compare the intended NGS source paths against this reviewed worktree. A changed controlling path requires a scoped diff and reconciliation; unrelated upstream movement does not justify another whole-product audit.
2. Keep `add_ngs_alignment_presentation_jobs.py` and any installed rows intact. Introduce split-readiness storage through a new named additive migration registered once in `platform/api/migrations/runner.py`; choose its ordinal from the then-current registry. Never rewrite an applied migration to fit the new contract.
3. Record fresh verified backups and the preimage of protected job `e48193a5-f6e1-47af-9c1d-30fda50090a8`. Preserve its observed state exactly, regardless of older transcript labels. Failure to verify a backup blocks migration.
4. Inventory completed eligible native results through canonical workflow/input identity and verified artifact authority. Backfill one idempotent request per exact source generation. A missing reference, broken receipt or mismatched digest produces an explicit blocked row in the migration report; it does not create scientific completion or force a legacy package into the new schema.
5. Adopt old derived bytes only through full validation under the new contract. Otherwise retain them unchanged while building new independently published products. Status GET remains read-only. Protected/rejected scientific rows are excluded from reprocessing.
6. Reuse the accepted source from old-spec Section 15, job `d08ca589-af8b-46dc-98bd-f17ed512cecd`, after rechecking its current immutable BAM/BAI authority. Retain its exact detail and calibrated-waveform assertions. The historical preview population target remains a regression constraint subject to the corrected writer proof; a conflict returns to the operator without a cap increase or rewritten source.
7. After review and deployment, verify the managed operator origin, accepted release identity and browser flows. The origin is discovered from the actual service configuration and recorded in the receipt; a localhost-only check cannot stand in for that origin. Preserve source, tests, data, migration and UI evidence as separate gates.

### 6.2 Document control and execution order

The canonical specification is `docs/plans/ngs-software-reliability-and-workbench-improvement.md`. It supersedes the working draft under ignored `.hermes/plans/`. The predecessor hash and source snapshot are recorded above. This file has been written but is not yet committed; the current specification task does not authorize commit or push. Subsequent changes and final review target this canonical file only.

P0 ends after the operator decisions are recorded and an independent reviewer accepts the exact final document and its source reconciliation. The first implementation package is P1. It owns canonical workflow admission, launch UI and native completion validation. P2 follows P1's authority contract; P3/P4 consume P2's product identities. P5 consumes the stable APIs, while P6 uses the exact native receipt contract from P1. P7 runs only after all applicable package checks pass. Shared files in `nextflow.py`, `NGSToolkit.tsx` and the alignment services have one active editor at a time.

Review the final document against the recorded decisions. Further operator questions are limited to newly demonstrated material conflicts. The assigned implementer resolves routine technical detail within the package and records the resulting contract before dependent code consumes it. Spec review and execution authority remain separate.

## 7. Proposed closure contracts and remaining approval decisions

This section incorporates Christian's settled D1–D6 decisions. Implementation must follow these choices. Approval of the design alone does not authorize tests, spending or deployment.

### 7.1 Catalog/preview contract, approved D3

This contract controls the catalog/preview split described in major-spec §§4.3–4.4 and implements the D3 decision in §7.6. In the **2026-09-02 successor**, it supersedes:

- §3.2’s single six-file package and combined identity;
- §3.4’s persisted preview membership/candidate fields and preview-gated catalog publication;
- §§4.1–4.4’s single readiness authority and retry target;
- §5.1’s dense-packing bound and prohibition on standard-writer internal flushes;
- §§6.1–6.4’s combined population identity, presentation prerequisites and unconditional signal-driven cursor invalidation;
- §6.6’s affected readiness/cursor errors;
- §§7.2–7.3’s presentation-dependent overlay eligibility and cache identity;
- §§8–9 and §§12–19 **only where they require those superseded contracts**, including all-presentations-ready migration gates.

Preserve exact query-name identity, anomaly classification, locator/fingerprint semantics, coordinates, signal metrics and optional-tag policies unless explicitly changed below.

Use new closed schemas: `alignment-presentation.v3`, `read-catalog.v2`, `read-record-locators.v2`, `read-page.v3`, `read-population.v2`, `alignment-record-page.v2`; advance lookup, record-query and overlay request schemas to v2. Preview manifest/policy becomes v6; BGZF admission becomes v2. Old bytes must not be parsed as these versions.

#### Product state

The observational presentation endpoint returns independent `catalog` and `preview` objects, **not one aggregate ready/failed state**.

Each applicable product has durable state:

```text
requested → running → ready | failed
failed → requested                 # authorized explicit retry only
```

- Each product owns its request identity, claim token, lease, attempt counters, terminal error and published manifest.
- API-only `unavailable` means no current request or unsupported source, with closed reason `request_missing | unsupported_source`; GET never synthesizes a request.
- A requested preview waiting for its catalog reports `blocked_on=catalog`, without holding a running claim.
- Catalog publication atomically seals **catalog + locators + catalog manifest**. Preview publication separately seals **BAM + BAI + membership relation + preview manifest**.
- Catalog readiness never requires preview, coverage or signal success. Optional coverage failure must not invalidate either catalog or preview.
- A preview binds one ready catalog authority. Preview retry cannot reset, rebuild or republish that catalog.
- Source-invalid or missing-catalog failures prohibit complete-table access; preview failure does not.
- Lease recovery/adoption validates and publishes each product independently. Claims cannot publish after loss of ownership.
- No automatic rebuild/requeue and no presentation elapsed-time deadline. Preserve cooperative abort and quiescent cleanup.
- A zero-read preview is ready only if its valid header-only BAM/BAI fits the bound. **If the header alone exceeds the ceiling, preview fails `resource_limit`; catalog remains ready.**

Historical cutover requires every designated catalog ready and every applicable preview in an explicit terminal disposition. Preview failure alone cannot block complete-read reader activation; the named accepted-preview fixture remains a separate release requirement.

#### Identity

New product identities use a schema-discriminated canonical JSON object with closed keys, explicit nulls and exact strings. Serialize with UTF-8, sorted keys, compact separators, ensure_ascii=False and allow_nan=False; hashed identity fields contain only integers, strings, booleans, nulls and ordered arrays/objects. Existing source receipts retain their own canonical hash contracts. This avoids introducing an unrelated canonicalization library or reinterpreting old hashes.

- **Source identity:** job/session/mode; accepted source manifest and artifact-set digests; complete BAM/BAI digests and lengths; immutable reference identity.
- **Catalog request identity:** source identity plus catalog/locator/fingerprint semantic versions.
- **Catalog authority:** catalog request identity plus catalog and locator artifact digests/lengths and schema versions.
- **`population_id`:** hash of `{schema:"bms.ngs.read-population.v2", job_id, session_id, mode, catalog_authority_sha256}`.
- **Preview request identity:** catalog authority plus complete preview policy, projection/header policy and pinned writer contract.
- **Preview authority:** preview request identity plus preview BAM, BAI and membership digests/lengths.
- **Signal snapshot identity:** requested and resolved raw selectors, metrics state, representation-manifest digest and metrics-artifact digest; null rules preserve predecessor semantics.

Preview policy, preview membership and optional signal changes **never redefine `population_id`**. Implementation provenance remains in manifests; preview-only implementation changes must not enter catalog identity.

Remove `in_preview`, `preview_rank`, and `preview_candidate_*` from the catalog. Store preview-specific eligibility/admission evidence with the preview. The membership relation contains exact admitted IDs and ranks and binds both catalog and preview authorities.

Responses carry nullable `preview_authority` and `signal_snapshot_id` separately. `in_preview` is nullable: null means no ready matching preview, not “definitely absent.”

Exact overlay identity replaces `presentation_authority_sha256` with catalog authority and binds source/header identity, literal read ID and the complete overlay writer/policy contract. An eligible read can open an overlay when preview is preparing or failed. Only membership in a **ready matching preview** produces `already_in_preview`.

#### Cursors

Retain the predecessor’s closed sort allowlist, full-population filtering/counting, nulls-last and exact binary-UTF-8 ordering. Tie-breaking is `read_id ASC`, regardless of primary sort direction.

| Operation | Cursor/identity dependencies |
|---|---|
| Alignment-field/read-ID sort and alignment filters | Population, query contract and keyset only |
| Any raw or derived signal sort/filter | Above plus exact signal snapshot |
| Exact alignment-record page | Population, locator identity, literal read ID, sequence policy, limit and last source ordinal |
| Preview membership decoration | Response preview authority; never table cursor identity |

Bind normalized query, locus, metric bounds, sort/direction, null policy, limit and schema in table cursors. Signal sorts use a pinned unavailable/invalid zero-row relation when metrics are absent; becoming ready invalidates that signal-dependent cursor.

- Preview changes invalidate **no** read or record cursor.
- Signal changes invalidate **only signal-dependent** table cursors.
- Source/catalog changes invalidate all associated read/record cursors and explicit population assertions.
- Malformed cursor or changed request parameters: `400 ..._CURSOR_INVALID`.
- Well-formed cursor with changed bound authority: `409 ..._CURSOR_STALE`.
- Preserve `400 NGS_READ_POPULATION_INVALID` / `409 NGS_READ_POPULATION_STALE`.

Alignment-sorted pages may refresh optional decorations between requests, but clients must not treat decorations from different preview/signal identities as one snapshot. Fence asynchronous selection by population, exact read and per-selection request token; signal operations additionally fence signal identity.

#### Standard-writer bound

**Reject** `ceil(total_raw_bytes / 65280) * 65536 + 28`.

Use the pinned ordinary BAM writer, one output handle, one close and one subsequent indexing operation. Permit its mandatory header flush and record-boundary `bgzf_flush_try`; prohibit application-added flushes, custom serialization and rewrite-on-overflow.

Before opening output, model actual packing using exact header bytes and **individual serialized record lengths in final output order**:

```text
U = 65280
header_blocks = ceil(header_raw_bytes / U)
record_blocks = 0
pending = 0

for record_length in final_record_order:
    if pending > 0 and pending + record_length > U:
        record_blocks += 1
        pending = 0
    record_blocks += floor((pending + record_length) / U)
    pending = (pending + record_length) mod U

if pending > 0:
    record_blocks += 1

bound = (header_blocks + record_blocks) * 65536 + 28
```

The header is flushed separately. Oversized ordinary records may span blocks. Exact multiples leave no residual block. Bind writer implementation, compression configuration and threading behavior; initially specify single-threaded BGZF writing to match this recurrence.

Retain 5,000 candidates, 20,000 records and 67,108,864 preview BAM bytes. Evaluate ranked whole-read prefixes using metadata ordered as the final BAM; stop at the first violating prefix. Do not substitute rank order for source-coordinate output order. Admission may revisit stored size metadata, but must not rescan source BAM or write probe BAMs. The predecessor’s claimed linear admission algorithm is not retained without a demonstrated matching implementation.

Overlay admission uses the same recurrence on its deterministic coordinate-sorted **exact-tag** records, preserving 256 records, 16 MiB BAM and the separate 10-second boundary.

Actual output must not exceed either calculated bound or ceiling. Violations fail terminally as `integrity_mismatch`; no shrinking/rewrite. Pin and later validate header serialization, internal flush behavior, long-record boundaries, compression and EOF handling before release.


### 7.2 UI and native-output contract, approved D6

Paths below are **router-relative**. Apply the configured basename; `/bms/` is explicitly recognized by `src/runtime/navigation.ts:65–66` and resolved in `src/main.tsx:31–47`. Do not assume a deployment origin.

| Operator surface | Existing path / identity | Acceptance requirement |
|---|---|---|
| NGS launch | `/ngs`; `NanoporeTemplate.tsx`, mounted by `NGSToolkit.tsx:4545` | Exercise the actual workflow selector and typed controls. Workflow selection is component state—not an established `workflow_id` query parameter. |
| Analyses and native results | `/ngs?section=analyses`, then `&job_id=<job>`; `section=evidence` also selects runs | Open from discovery, refresh, Back/Forward, and direct URL. Preserve exact job and inherited context. Exercise logs, full-detail parameter reuse, native artifacts, QC/verification, barcode and pooled-review panels when applicable. |
| Saved read/signal workbench | `/ngs?section=analyses&job_id=<job>&view=workbench&viewer_session_id=<saved-session>` | Restore server-bound alignment session, reference revision and saved read before local defaults. A saved session may resolve its job when `job_id` is absent. Do not invent `read_id` or `alignment_session_id` URL contracts: selection is persisted in the viewer session. |
| Contextual result links | Above result route plus `run_id`, `reference_set_id`, `assignment_id` when emitted | Validate the referenced authority, not merely the presence of query keys. `NGSToolkit.tsx:2185–2213` contains their consumers. |
| Generic job/result redirects | `/jobs/<job>` and `/designs/<job>` | Eligible NGS identities must redirect to the native NGS route while preserving context. Sources: `JobDetailPage.tsx:117`, `ResultsViewer.tsx:2242–2243`, `lib/ngsResultRouting.ts`. |
| Project discovery and selection | `/projects?scope=ngs-molbio`; `/projects/<project>?focus=<experiment>&selected=domain_experiment:<domain>&state_revision_id=<revision>`; nested Project/Experiment/Domain routes also exist | Resolve immutable historical content and canonical native reopen destinations. Cover local NGS/MolBio and broader Project context independently. |
| Project-origin NGS actions | `/ngs?workspace_id=<project>&global_experiment_id=<experiment>&domain_experiment_id=<domain>&section=workflow-plans` or `section=analyses`; preserve emitted `ownership_scope` and optional `state_revision_id` | These are actual emitted routes, not proof the intended controls mount. `pages/ProjectManager.tsx:576–602` generates them. Repair the destination rather than accepting a successful navigation to the wrong screen. |
| Domain dataset, plan, evidence and history controls | Existing section keys: `datasets`, `workflow-plans`, `evidence`, `history`; auxiliary keys `dataset_revision_ids`, `run_group_id`, `run_group_action=clone`, `source_run_id`, `source_attempt_id` | Each existing control must have a reachable destination retaining its exact inputs/receipt/revision. Do not silently rename or collapse these into unrelated hub tabs. |
| MolBio handoff and historical reopen | `/designer?molbio_sequence_id=<sequence>&molbio_revision_id=<revision>`; `/molbio?sequence_id=<sequence>&revision_id=<revision>` redirects to that identity. `/molbio-ngs/domain-experiments/<domain>` redirects to `/ngs?domain_experiment_id=<domain>` preserving query | Preserve existing construct editing and immutable reference handoff. Historical redirects alone do not prove complete Project context or read-evidence reopen. |
| Generic/prepared submission | `/submit`, including `/submit?launch_context_id=<issued-context>` | Include any NGS-capable generic model selection and prepared destination in parity acceptance. This is a separate source-visible submit surface, not proven equivalent to `NanoporeTemplate`. |

**Important routing correction:** `NGSToolkit` mounts `NgsMolBioProjectHub` with `presentation="launcher-dialog"`, which returns a **Projects link**, not `DomainExperimentWorkspace` (`NgsMolBioProjectHub.tsx:36–49`). Thus `/ngs?section=datasets|workflow-plans|history` currently falls through to the launch view unless another recognized result key intervenes. Separately, when `DomainExperimentWorkspace` does mount with full Project context, it returns `project-hub/ProjectHubShell.tsx`; that shell recognizes only `overview`, `plasmids`, `sequence-data`, `experiments`, `results`, `activity`, defaulting other sections to `overview` (`:12–19,427`). Both routing seams require closure.

Discovery must use the same eligible identity policy as `ngsResultRouting.ts:10–61`; current discovery queries only five model IDs (`NGSToolkit.tsx:2371–2389`).

#### Viewports, approved D6

Check desktop layouts at 1440p (2560×1440) and 1080p (1920×1080). Record browser zoom, display scaling and the actual CSS viewport so display resolution is not confused with available layout space.

Use Samsung Galaxy S25 FE as the mobile acceptance case. Record its actual browser CSS viewport and device-pixel ratio during acceptance; do not substitute the screen's physical pixels for CSS dimensions. Cover portrait and landscape, scrolling, viewer close/navigation, table/detail, downloads and visible error states.

Build responsive behavior for the supported mobile layout, not a device-specific S25 FE branch. Check resizing and widths around the layout's existing breakpoints. Scientific tables may scroll inside their own container; controls and messages must remain reachable without uncontrolled page overflow. Preserve keyboard access where available and usable touch controls. Mobile remains read-first, with complex editing desktop-first.

#### Native outputs

All eight map to `workflows/ngs/<canonical-id>.nf` through `platform/api/services/nextflow.py:487–494`. Input modes below come from `ont_ngs_contract.py:142–305`; required references are enforced by `routers/ont_runs.py:143–150`.

For every accepted branch: bind the effective settings and immutable inputs; parse required outputs with format-appropriate readers; validate BAM/index/reference consistency, manifest paths/hashes and scientific identity; retain negative or REVIEW scientific outcomes without converting them into execution failures. Optional catalog/preview/signal failures must not invalidate native scientific completion.

| Workflow / supported inputs | Native-output acceptance |
|---|---|
| `ont_basecall_dna` / POD5 | Require `basecall/calls.bam`, `basecall.log`, `dorado_preflight.json`, `dorado_runtime_provenance.json`; validate model/mode/read inventory and requested move-tag provenance. `sequencing_summary.tsv` is conditional. Unbarcoded + reference requires `align/aligned.bam(.bai)`, `reference.fasta(.fai)`, `align.log`. Barcoded branch instead requires `demux/demux_manifest.json`, `per_barcode_units.json` and declared `demux/units` products; do not require the mutually exclusive alignment branch. |
| `ont_basecall_rna` / POD5 | Same basecall core with RNA-effective settings; alignment products only when a reference is selected. No inline demultiplexing branch. Reference-free basecalling must not be blocked by invented reference authority. |
| `ont_plasmid_qc` / POD5, BAM, FASTQ; reference required | Require branch-native alignment/reference evidence. When QC enabled, require `fastq_qc` evidence, canonical `multimer_qc` outputs and `verification` bundle. POD5/BAM branches obtain QC FASTQ through `BamToFastqForQC`; do not assume QC is FASTQ-input-only. Preserve separately admitted bounded external signal-alignment behavior and its smaller terminal contract. |
| `ont_construct_screening` / POD5, BAM, FASTQ; reference required | Alignment is baseline. `run_assembly=true` requires native `assembly/wf_clone_out`, report, sample status, log and runtime provenance. **Only FASTQ + QC enabled currently invokes dimer/QC/ConstructVerify.** Do not require or advertise that verification bundle for POD5/BAM as if already generated. |
| `ont_methylation_analysis` / POD5, BAM; reference required | With modkit enabled require `methylation/modified_base_input.bam(.bai)`, `modified_base_tag_check.log`, `methylation.bed`, `pileup.log`, `modkit_summary.tsv`, `summary.log`, plus input-branch/reference products. Validate semantic MM/ML consistency and BED/summary coordinates/modification meanings. FASTQ is unsupported. Registry artifact kinds are not proof that consensus/per-base-support artifacts are emitted. QC-disabled/modkit-disabled settings must have explicit branch contracts, not false methylation readiness. |
| `ont_fastq_qc` / FASTQ; **reference required** | Require the existing four terminal stages: `fastq_align`, `dimer_qc`, `fastq_qc`, `construct_verification`. Exact current suffix lists are in `ont_ngs_completion.py:39–65`; validate those and the declared native module products. No existing reference-free branch was found. |
| `ont_pooled_reference_assignment` / FASTQ + frozen competitive reference-set snapshot | Require `pooled_reference_assignment/{assignment_summary.json,per_read_assignment.tsv,fastq_preflight.json,occurrence_map.json,combined_intended_reference.fasta(.fai),pooled_assignment.bam(.bai),intended_pool.igv_session.json}`, alignment log and target/ambiguous/unclassified read-ID and FASTQ partitions. Reconcile **record occurrences**, duplicate names, counts, frozen targets and ambiguity; scientific execution ends at operator review, not automatic construct verification. |
| `wf_clone_validation` / POD5, BAM, FASTQ; reference required | Require native assembly report/status/log/runtime provenance, `assembly/adapter/{adapter_manifest.json,verification_input,per_base_support.tsv,alignment_stats.tsv,dimer_breakpoint_call.tsv,dimer_secondary_summary.tsv}`, and ConstructVerify outputs for **all three modes**. Additional FASTQ QC/dimer outputs are conditional on FASTQ input and QC enabled; they are not the authoritative assembly-verification substitute. |

**Bundle source authorities:**  
`modules/ngs/fastq_plasmid_qc.nf:21–48` declares alignment copies, QC tables, reference/consensus indexes, IGV tracks/config/report and logs. `construct_verify.nf:20–26` declares the verification directory, manifest, summary, VCF, per-base metrics and HTML; the FASTQ completion contract additionally requires topology evidence. Clone outputs are in `clone_validation.nf:16–21,137–143`; methylation outputs in `modkit_pileup.nf:14–16,68–70` and `modkit_summary.nf:14–16`. Conditional comparison-panel products must also be validated where requested (`comparison_panel_attribution.nf:14–22`).



#### Route repair selected for approval

Keep the existing emitted section keys and the configured basename. For a request carrying `domain_experiment_id` plus `section=datasets|workflow-plans|evidence|history`, resolve that exact domain context and dispatch the existing DomainExperimentWorkspace section before the generic launcher or ProjectHubShell fallback. Require owner/revision validation; an unavailable context produces a visible error. For ProjectManager entrypoints, retain `workspace_id`, `global_experiment_id`, `domain_experiment_id`, `state_revision_id` and emitted auxiliary keys through navigation. Without domain context, `section=evidence` keeps its existing analyses behavior. No new global navigation taxonomy or replacement Project screen is proposed.

Generic `/submit` must either preserve the same canonical NGS inputs/settings and contextual receipt as `/ngs`, or visibly hand off to the canonical native launcher with those values intact. It may not silently submit a different scientific operation. P1 acceptance checks both direct and prepared-context entrypoints.

The native-output table fixes the required and conditional denominator. During P1, bind each declared artifact to the existing emitter's exact field/path and format validator before changing terminal publication. Optional modkit/QC/assembly branches retain current admitted semantics, with the approved D5 FASTQ-QC correction. A disabled stage has a visible disabled/not-requested result rather than invented scientific output.

### 7.3 Global resource integration, settled owner

The source already exposes `_global_resource_admission` with schema `bms.global-resource-admission-handoff.v1` in `platform/api/services/resource_usage_evidence.py:28–51`. It binds `cpu_threads`, `dram_bytes`, GPU identity, policy source/version, owner and attempt identity. `ont_ngs_results.py` consumes it. This is an observed server handoff, not proof that it is the future global operator parameter Christian described. Its current validator has fixed bounds (`resource_usage_evidence.py:152–155`) and must not become the sizing policy for remote work by inheritance.

NGS consumes the approved global resolver's effective allocation for each selected target and persists its reference/version. It must not accept a client-forged internal admission object. The owner of the global contract must identify the public parameter/resolver and its target mapping before the consumer is wired. CPU and RAM can map to the existing handoff only where its version and admitted target range match. Disk/concurrency fields absent from that contract remain an explicit global-owner dependency. This NGS spec neither invents those fields nor expands the global service. Derived work uses its own valid current admission; an expired scientific-job lease cannot authorize it.

### 7.4 Portable verified delivery, approved D4

Use a portable verified-file cache on existing storage. No mandatory fs-verity, filesystem conversion or new scientific store. Native result receipts remain authoritative; the cache holds reconstructible delivery copies.

Verify exact digest and size during import, publish completed files atomically and reuse them for viewer access and complete downloads. Authorization remains tied to the owning result. Full, Range, HEAD and conditional responses must resolve the same accepted file identity. Cache copies must not silently follow a changed original.

Manage publication and active-reader ownership through the existing application. Eviction must leave active readers and scientific originals intact. Recover incomplete imports explicitly after restart. Resource admission follows the global allocation, with capacity errors distinguished from corruption. Warm requests must avoid copying or hashing the whole source repeatedly.

Portable verification must cover pathname replacement, same-inode modification and bytes already being streamed. Read-only permissions, timestamps or a pinned descriptor alone do not prove immutable bytes. Select the smallest existing mechanism that meets those checks during the scoped storage implementation design; document its actual guarantees. Do not restore a filesystem-specific dependency or claim protection that has not been established.

This section replaces the earlier fs-verity proposal in full. Remaining technical detail belongs to the portable implementation; the operator's platform choice is settled.

### 7.5 Performance approach, settled

Favor faster interaction through sound implementation within the global resource allocation. Preserve complete results and scientific meaning. Stream large data, reuse verified products and avoid redundant scans. Expensive preparation runs asynchronously with visible state. Normal navigation must not regenerate artifacts.

Measure representative workloads on the actual execution target and record the effective global allocation. Separate cold preparation, warm query, network and browser timings. Investigate material regressions and fix avoidable work. No fixed millisecond response target or presentation elapsed-time deadline determines acceptance. The earlier numeric timing proposal is withdrawn from the controlling requirements.

Inherited preview/overlay size, record and timeout values are product defaults under review, not literature-backed scientific restrictions. This paragraph controls every retained numeric preview/overlay reference elsewhere in this document and its predecessor: those references identify the existing behavior to evaluate, not an exemption from this review. Preserve the original scientific data and complete-read access while checking each value against pinned upstream behavior and representative workloads. The existing browser auto-mount ceiling remains a display behavior; explicit complete downloads use the full artifact. The overlay's inherited 10-second boundary is part of D3's technical review and must not be treated as an approved universal performance target.

### 7.6 Settled decision ledger

| ID / name | Recommendation | Status and effect |
|---|---|---|
| D1: Global resource authority | Consume the global resolved allocation on the actual execution target. Bind the public resolver to the existing server handoff only after its owner confirms version and target coverage. | Policy settled by Christian. Exact global producer contract is a dependency; no NGS budget approval is requested. |
| D2: Workload-aware performance | Apply Section 7.5: remove avoidable work, measure representative workloads and preserve responsive asynchronous behavior within global limits. | Settled by Christian. No fixed response-time gate. Slow preprocessing has no presentation deadline. |
| D3: Independent catalog and preview | Separate the complete read catalog from the optional preview. Keep existing limits only where upstream behavior or workload evidence justifies them. Section 7.1 defines the product/schema/cursor/writer proposal implementing this decision. | Approved by Christian. Preview failure cannot block complete-read search or detail. Existing numeric limits remain subject to evidence-based justification; accepted-fixture feasibility is checked during authorized execution. |
| D4: Verified large-file delivery | Use a portable verified-file cache on existing storage. Reuse verified files for viewer access and complete downloads, with authorization and explicit file-identity checks. | Approved by Christian. Mandatory fs-verity is withdrawn. Application-controlled publication alone does not establish protection against same-inode mutation; the portable implementation must address that explicitly. |
| D5: FASTQ-QC contract | Require a reference and keep QC on for the named FASTQ-QC workflow. Correct the UI/API inconsistency and reject `run_fastq_qc=false` before scheduling. | Approved by Christian. Other workflows retain their existing optional QC stages. No new reference-free workflow is introduced. |
| D6: UI acceptance and existing routes | Repair existing routes and controls. Check 1440p and 1080p desktop layouts, with Samsung Galaxy S25 FE as the mobile acceptance case. Record actual CSS viewports and verify responsive behavior across widths and orientations. | Approved by Christian. Mobile design must work beyond the S25 FE; no device-specific layout. Preserve the BMS shell and mobile read-first behavior. |

All six operator decisions are recorded. Package-local work must resolve the named technical dependencies without reopening settled scope. Commit/push, tests, scientific replay, cloud expenditure and deployment are separate execution-authority items; none is implied by approval of a design choice.

### 7.7 Closure review reconciliation

The complete outputs of `deleg_f5ff78df` were read. Catalog/preview, cursor and writer proposals were incorporated with the existing canonical-JSON mechanism retained instead of adding RFC 8785. The executor checked the installed BGZF record-flush implementation. The UI/output matrix was incorporated, and the executor confirmed the launcher-dialog Project-link behavior. The mutable-descriptor seam was checked in `scientific_artifacts/writer.py`. Christian subsequently selected portable storage; the earlier mandatory fs-verity proposal is superseded in full.

The entire host-sized numeric resource table from Task 2 is rejected as superseded by Christian's global-resource correction. No RAM, scratch/cache size or worker-count default from that table is part of this spec. Fixed response-time targets are withdrawn under the settled D2 decision. Current local resource observations provide no remote capacity authority.

The route and native-output matrices establish proposed coverage, not executed acceptance. This closure draft supersedes earlier draft checksums. A fresh independent exact-byte review is required for this candidate; approval of earlier source reviews does not transfer.

## 8. Definition of done

The major repair is complete only when each supported existing workflow reaches its declared input-to-result acceptance, complete read access remains available independently of optional viewers, historical results reopen through their supported native and Project routes, and the exact released candidate passes the approved resource and live acceptance gates. Every changed UI path must pass its mounted behavior checks and the approved browser flow on the managed origin. Backend success cannot close a package whose operator controls or navigation remain broken. Report any deliberately excluded or blocked path individually.

## 9. Review snapshot for principal surfaces

SHA-256 values captured during this review. These distinguish dirty implementation bytes from the base commit; they do not certify correctness.

```text
78a930fedc8a9f586fe9e8a50fad7b2c17699f8c045ce8a92a66dfac9cd57354  platform/api/services/ngs_alignment_sessions.py
c622ef53f4a95da3bda5992c44a40d6fdbf3783e78ff49efb0c7a369f468dd65  platform/api/services/ngs_alignment_presentation_v5.py
e019ff2dc4f6e31dd7816f2e9cb3e38e718f6f85ddd5a1243ba1bf3a412ed822  platform/api/services/ngs_alignment_presentation.py
5089e040317dde438202e39b452acc297e6a2d1195f319fe66fdad72129c05f4  platform/api/services/ngs_alignment_presentation_worker.py
3ccec26b00ad5badc82e24e15a15dde42f41745b53c135347fc4c89d953a7d5d  platform/api/routers/ngs_alignment_sessions.py
7c307351f792cacaee6962cc023c6510fafb89359a779b46e1f63b76c0be7a83  platform/api/services/ont_ngs_completion.py
02223a42475455cadd6b4aab41fad177ba411b796e311624454c830f271a0ccf  platform/frontend/src/components/NGSToolkit.tsx
```
