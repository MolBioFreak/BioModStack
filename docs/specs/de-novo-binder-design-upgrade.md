# De Novo Binder Design Upgrade: Implementation Specification

**Status:** Revised after Astra's holistic source review and Christian's explicit scope clarification. Product scope is fixed below. This is a specification, not implementation, scientific-run, deployment or rental authorization. LigandMPNN's detailed scientific configuration remains open for discussion; its inclusion is not open.

**Companion:** [Upgrade outline](de-novo-binder-upgrade-outline.md).

**Evidence baseline:** BMS `96923d9470d86219451d6caf88f63308a369c007`, verified against canonical Development, `origin/test` and API build during review. BC2: `PacesaLab/BindCraft2` at `d5bae16e9fee95f4c97fc16bc05dcbde4ccb885f`, package `1.0.1`. Start implementation from current `origin/test` and reconcile changed owners against this baseline; the older documentation worktree is not the implementation base.

**Scope authority:** Christian's workflow direction and subsequent clarification control the deliverables. The review's implementation simplifications do not authorize narrowing them. This revision supersedes the earlier draft's core-versus-research release split, mandatory second-review ceremony, claims that Caliby has no implementation, and false choice between a nullable `Design.pdb_path` migration and mandatory PDB conversion.

**Repository policy:** Read `AGENTS.md`, [model configuration/operator parity policy](../Model_Configuration_Operator_Control_and_Agent_Parity.md), and existing model/workflow ownership in the [Protein In Silico SOW](2026-08-12-protein-in-silico-global-project-integration-sow.md). Reuse their working authorities. Do not mistake a Project-specific adapter gap for global model unavailability or silently import an unrelated Project/NGS programme as a prerequisite. Any genuine policy conflict requires an explicit decision, not hidden noncompliance.

**Naming:** The conversational “version 2” label is not a public product name, code namespace, new job ID convention or schema suffix. Version individual contracts only for actual compatibility changes.

## 1. Fixed outcome and complete scope

Deliver one first-class, modality-aware **De Novo Binder Design workflow**, replacing the nanobody-only product framing while retaining antibody-specific capabilities where relevant.

The user-facing structure is fixed:

1. Choose binder format/modality and scientific objective.
2. Choose any compatible existing generator or BC2 as a first-class peer option.
3. Configure and run generation; inspect that model's native results.
4. Either retain the generator result and stop, or select candidates for the upgraded optional refinement loop.
5. Choose compatible refinement, sequence design, validation and analysis operations; inspect their model-native results and parent/descendant comparisons.
6. Select descendants for another round or finish. Reopen the same work later without reconstructing identity from filenames.

The entire workflow is in scope, not just BC2's wrapper:

- Preserve RFantibody, BoltzGen and seeded PPIFlow choices and native routes, and reuse existing applicable general de novo/RFD3 capabilities without displacement or duplicate execution.
- Integrate BC2's entire supported native scientific campaign surface, relevant controls, outputs and lifecycle. It is optional as a generator choice, not partial in implementation.
- Generalize and upgrade the existing refinement loop for candidates from all these generators, using actual input/model compatibility rather than nanobody-only assumptions or producer-name restrictions.
- Correct existing workflow defects, including request handling, stage execution, selection, lineage, samples, results and continuation.
- Deliver working local and remote-bridge execution across generation and refinement, with efficient startup and reliable result return/reopening.
- Deliver solid per-model settings and results, human/agent parity, useful review and repeatable iteration.
- Include LigandMPNN as an optional refinement method. Continue discussion of its precise scientific use without demoting it to an uncommitted future feature.
- Retain the agreed FA-MPNN, repack/anchor/PPIFlow, independent validation, FrustraMPNN, Caliby and complex-contact-frustration work.

**Optional for the operator does not mean optional to deliver.** There is one completion scope. Engineering milestones are not a smaller accepted product. An in-scope operation left unavailable is unfinished work unless Christian explicitly changes that requirement.

Agnostic does not mean every checkpoint supports every molecule or format. Genuine native limitations must be explicit. They do not justify retaining antibody assumptions in otherwise generic infrastructure or abandoning qualification work that this upgrade requires.

## 2. Current implementation and reuse boundaries

### 2.1 Existing generator execution

`platform/api/services/nextflow.py:507–527` routes RFantibody to `workflows/antibody_denovo.nf`, BoltzGen nanobody to `workflows/protein_design.nf`, and seeded PPIFlow to `workflows/ppiflow_generator_design.nf`. Preserve their scientific identities and reuse those modules. Refactor shared staging/continuation where necessary; do not build a universal scientific DAG inside the RFantibody parent.

The current `AntibodyDenovoTemplate.tsx` and `antibody_denovo.yaml` contain VHH roles, CDR/framework defaults, duplicated selectors and handwritten parameter assembly. Replace the touched assembly with model-owned request components beneath the agnostic launcher. Do not merely rename the card or append another large BC2 conditional branch.

Existing general-design/RFD3 owners remain usable. Their existence is neither a reason to exclude BC2's general modalities nor a reason to create duplicate RFD3 execution.

### 2.2 Existing downstream implementations

Generic constrained FA-MPNN exists in `modules/fampnn.nf`, `workflows/protein_sequence_design.nf` and `scripts/prep_fampnn_constraints_generic.py`. Reuse explicit design/fixed-region handling rather than passing generic binders through antibody masks.

Caliby has a parent-workflow path (`antibody_denovo.nf:2511–2555`), `modules/caliby.nf` and `scripts/run_caliby_sequence_design.py`. Its installed runtime, setting completeness and suitability for each proposed complex remain to be qualified. Describe it as implemented-but-needing-qualification, not absent or already proven.

FrustraMPNN has canonical model settings, scheduler fan-out, artifact/result persistence and viewers. Reuse these owners, repairing workflow attachment and failure isolation instead of inventing another analysis runner.

Foundry remains the single LigandMPNN execution owner under the existing architecture. Inspect and complete that path's actual missing pieces. A registry row is not a working integration; a Project catalogue denial alone is not a complete diagnosis of core execution.

### 2.3 Existing result and bridge infrastructure

Reuse Job/Design, scientific artifact receipts/datasets, model-native result adapters, selection and review services. `result_contracts.py` primarily routes viewer/analyzer capabilities; adding a row there does not establish native result integrity or implement a viewer.

Reuse `NativeInvocation`, `SelectedExecutionPlan`, selected asset provisioning, worker lifecycle, checkpoint continuation and journaled remote result publication. Do not create BC2-specific scheduling, hydration, transfer or resume services beside these owners.

## 3. Launcher, settings and plan ownership

### 3.1 Modality-aware authoring

Select format/objective first, then show compatible generators and their native controls. Preserve complete BC2 access to advanced formats/objectives. Do not force a lowest-common-denominator target or binder form across models. A model-specific input editor is appropriate when the native input differs.

RFantibody, BoltzGen, seeded PPIFlow and BC2 must remain selectable within the same product. Seeded PPIFlow visibly requires a compatible prior complex; do not advertise it as unseeded generation. Existing supported non-VHH capabilities should become reachable where the generator actually supports them.

When a user changes generator or modality, retain valid shared inputs, preserve model-specific drafts, identify incompatible values and require an explicit correction. Do not silently relabel chains, discard settings or substitute an engine.

Separate independent facts in the backend projection: scientific applicability, executable adapter, selected-target readiness, experimental designation and whether an operation is selected. “Not requested” is plan state, not a capability. Do not build a second general-purpose capability framework to represent these facts.

### 3.2 One scientific authority per model

Each model/operation owns one versioned scientific schema and native compiler. Registry and workflow definitions reference it; they do not redefine defaults in YAML, frontend state, Project schema, Nextflow and shell independently. Reuse BMS's typed control mechanisms with model-specific editors for targets, chains, regions, arrays and nested settings.

Required fields include native mapping, type/shape, effective default, units, applicability, incompatibilities, explanation, reproducibility significance and operator/profile/scheduler ownership. Browser and agent API expose the same relevant scientific options. Advanced sections manage density; raw JSON import/export supplements rather than replaces typed controls.

Presets are explicit initial values or visibly fixed profile choices. Loading, cloning, retrying, changing stages or refreshing global configuration must not overwrite saved/operator values. Unknown scientific selectors fail before queue insertion; aliases are interpreted only by one documented historical adapter.

Preview uses the existing submission/approval mechanism and displays exact requested and resolved effective settings, input identities and incompatibilities. Launch binds to that request identity. Nextflow consumes compiled settings and must not introduce another scientific-default layer.

Runtime paths, credentials, storage roots, container identities and physical placement stay system-owned. Record applicable runtime identity once through the existing receipt mechanism. Treat worker/batching choices that change native sampling behavior explicitly rather than silently dismissing them as irrelevant.

### 3.3 Workflow plan and repeated rounds

The workflow stores explicit generator and selected operation references. It connects existing model jobs/stages; it is not another scientific engine. Support both preselected continuation and selection after generator review using the existing job/child/review mechanisms.

The refinement experience is an actual loop: select an exact candidate/state set, choose operations and their settings, run a bounded round, review descendants, compare with parents and select another round. Persist round/root/parent relationships. A user may branch multiple refinement alternatives from one parent without overwriting it.

Bound per-round samples and selected inputs, and preview expected fan-out and selected dependencies. Do not introduce open-ended automatic optimization or a generic graph programming language. Reject cycles and incompatible artifact transitions using the owning operation's contract.

## 4. Full native BC2 integration

### 4.1 Native denominator

Inventory the pinned installed code: core reference/defaults/profiles, all modality/property/target presets, `bindcraft/settings.py`, preflight, CLI, loss/filter/metric registries, campaign and sweep execution, output writers and resume state. The reference and CLI listings alone are not exhaustive. Include preset-only and registry-generated fields and nested settings; the reviewed VHH `paratope_conformations` field illustrates this requirement.

Generate a machine-checkable field/authority coverage register from that inventory and the authoritative BMS schema. For every relevant field, map UI, API, native compilation, saved values and result identity; explain any implementation-only exclusion. This register is coverage evidence, not a separately edited settings database. Resolve discrepancies before implementing the dependent controls and tests. Upstream changes require a scoped reconciliation, not blind inherited approval.

Cover all native format presets: `binder`, `large_binder`, `peptide`, `cyclic_peptide`, `homo_oligomer`, `multidomain`, `VHH`, `ARP`, `scFv`, `Fab`; conformational `induced_fit` and `fold_switch` objectives; all property and target presets; multi-positive/detarget design; native scientific model/stage/loss/filter/seed/sweep/acceptance/output controls; and supported standalone result ranking/filtering actions where exposed by the pinned CLI. Such postprocessing creates a new publication/view, not an unnoticed mutation of an already selected result.

Preserve target structures and FASTA forms, chain/residue targeting, scaffolds and editable regions, topology, multichain sequences and state-specific observations. Native restrictions remain truthful: FASTA does not supply structure-numbered hotspots; scFv does not imply a designed linker; a cyclic closure proxy is not experimental chemical closure. These caveats do not remove those modes.

### 4.2 Use native semantics, not a competing implementation

Use pinned native resolution/preflight functions as semantic authority. Differential tests compare BMS preview and the actual CLI-loaded settings, including target accumulation, property order, implicit binder insertion, nested settings, filter aliases and sparse-output behavior. Compatibility follows resolved native features, not only a manually maintained modality label matrix.

Keep resolution isolated from accelerator/model initialization. Ordinary launcher rendering, schema discovery and API startup must not import GPU models, download weights or scan runtime trees. If a lightweight adapter is needed to call native settings logic, its behavior must be checked against the actual CLI rather than becoming a forked resolver.

BC2 owns its native stages, workers, adaptive mechanisms, sweeps, filtering and ranking. No BMS attempt scheduler, second autotuner, forced external redesign stage or separate pipeline per modality.

### 4.3 Execution, budgets and runtime

Add a dedicated BC2 model identity, Nextflow entrypoint/module and reproducible image, deliberately admitting it without reviving retired BindCraft code/images. Nextflow stages inputs and invokes one complete compiled native campaign document in a job-owned durable campaign directory.

Require a finite effective attempt budget and expose accepted-design targets where applicable. Preserve native sweep semantics: per-arm allocation can clamp to at least one and alter the effective total. Preview the true aggregate allowance; reject a request that cannot honor an explicitly hard cap rather than silently exceeding it. Preserve trajectory-only mode without requiring accepted designs.

Distinguish requested/claimed attempts, emitted trajectories, scored draws, passing draws and retained sequences. Interrupted or deduplicated attempts need not yield corresponding CSV rows. Budget exhaustion and zero accepted yield are explicit native outcomes, not generic ingestion failure or grounds to fabricate a Design.

Pin Python/JAX/CUDA and resolved image dependencies for intended hardware. Reuse shared AF2 parameters and packaged MPNN assets. First-use downloads are provisioning, not a supposedly ready launch. BC2 itself constructs single-row features and requires no external MSA search/database; downstream predictors retain their own preparation requirements.

Reserve the selected GPU allocation once and let BC2 manage workers only within it. Admission uses padded complex length, actual allocation, usable VRAM and job host-memory limits, not binder length or host-wide `/proc/meminfo` alone. Native clamping to one worker is not sufficient admission when even one cannot fit. Bind a persistent compilation cache with compatible image/device/runtime identity and record resolved worker allocation.

### 4.4 Adaptive settings and resume

Native recipe hashes exclude some scientific settings and are not full effective-request identity. Capture campaign/arm settings and each attempt's actual effective settings, sampled values and adaptive changes. Use a narrow producer-bound recorder or demonstrably complete reconstruction; do not claim native output already includes a full settings snapshot. Preserve native hashes unchanged alongside BMS's complete setting identity.

Native resume permits changed settings and can consume interrupted claims. BMS must enforce matching immutable scientific request/input identity for same-campaign continuation. A changed scientific request creates a new campaign/descendant lineage. Resuming means continuing native state, not necessarily replaying the interrupted trajectory or reproducing uninterrupted scheduling.

Preserve hidden campaign counters/claims, deduplication state, sweep state and other pinned resume requirements. Ranked files alone are not a resume package. Keep BC2 continuation, Nextflow resume, interactive review continuation and a new refinement Job distinct; remote handling is specified in section 8.

## 5. Per-model results, selection and lineage

### 5.1 Shared mechanics, native science

Extend existing Job/Design and scientific artifact persistence. Common references connect source, producer/model/mode, candidate/document/state, round/root/parent, immutable artifact identity and settings/runtime receipts. Native metrics and ranking remain owned by their producer. No universal binder score, parallel candidate database or workflow-specific copy of each model's viewer.

Persist model-native typed data with explicit missingness; expose bounded tables/detail, appropriate structure/sequence/state views, compatible comparisons, exports and existing shared saved-view/review facilities. Satisfy applicable model-result guarantees through these owners. Do not turn this upgrade into a prerequisite to rebuild unrelated global analytics or Project infrastructure. A genuine missing applicable capability remains an explicit work item.

### 5.2 BC2 publication contract

Implement one versioned BC2-native publication contract consumed after either local output or verified remote return:

- **Campaign/arm:** existing Job plus immutable request/input/settings identity, source/runtime version, sweep arm and continuation lineage.
- **Attempt:** campaign/arm plus native trajectory identity/hash and claim identity where available; effective-attempt settings reference, outcome and optional artifacts.
- **Scored draw:** attempt plus native `_candidateN`, sequence/chain identity, passed/rejected outcome, reasons, metrics and optional state structures.
- **Retained sequence:** attempt plus native `_seqN`, explicit scored-draw association, native acceptance and rank within a publication snapshot. Rank is not identity.
- **Structure document:** subject plus target-state and role such as complex, trajectory, free binder or relaxed derivative, with format, digest and complete chain/residue map.

The pinned producer sorts/truncates scored draws before assigning retained `_seqN` names. It does not persist the original `ValidatedBinder.candidate_number` in the inspected accepted metadata. Capture this association explicitly at the producer boundary; never join ordinals. Historical reconstruction is permitted only when independently unambiguous and labeled as reconstructed, otherwise unknown.

Preserve attempts/refolded/ranked records, emitted per-target CIFs, native summaries/metadata, settings and declared optional artifacts. Read emitted target names/weights and ordering before parsing semicolon metrics. Preserve `/`-separated multichain sequences. Do not require intentionally suppressed structures or stage directories that were never produced.

Store structureless rows in model-native scientific datasets rather than fake Designs. Project reviewable structure-bearing records into existing Designs and manifests; group target-state documents under their retained/scored subject so they are not counted as extra accepted sequences. An emitted/reviewable raw structure remains distinct from native acceptance.

### 5.3 Native mmCIF handling

Keep native CIF/mmCIF authoritative. Existing `Design.pdb_path` already stores CIF/mmCIF paths; its non-nullability does not force a migration for structure-bearing rows. Use explicit artifact format and the existing chain/residue mapping contract. Repair format detection, review discovery and actual PDB-only consumers.

Convert only at a genuinely PDB-only operation, retaining native input, derivative digest and residue/chain map. Reject loss that violates the operation's declared identity/coordinate requirements; do not rename CIF bytes, silently renumber residues or flatten unsupported assemblies. Do not undertake a wholesale database rename/migration just to add BC2.

### 5.4 Immutable selection and publication isolation

Use one existing-owner selection resolver for all generators and refinement rounds. Resolve source/root ownership, producer candidate/document/state, expected artifact digest, role map, required format and operation eligibility before materialization. Reject foreign IDs and incompatible mixed states. Use immutable snapshots or verified immutable references for local and remote inputs alike; mutable links alone do not prove approved input identity.

Delete stem/global-name/path fallback as scientific identity for new results. Historical readers may display missing identity as unknown but must not infer proven lineage. Preserve origin Design and origin Job consistently across multiple generations.

Publish verified generator/operation outputs independently from optional analysis attachments. A failed selected analysis is a visible failed stage, not grounds to roll back valid primary results. Existing remote publication journaling and model-specific corruption checks remain intact; separate transaction/completion ownership rather than weakening verification.

A sequence or coordinate change produces a descendant with its own validation state. Unchanged sequence alone does not preserve validation of changed coordinates. Analyses attach to exact artifacts; no generator acceptance, predictor score or analysis is advertised as measured affinity or experimental validation.

## 6. Upgraded optional refinement loop

### 6.1 Common operation contract

Every operation declares input artifact/sequence class, native format, modality/objective/state compatibility, binder/target roles, numbering, design/fixed masks, required side-chain context/checkpoint, settings, output cardinality, terminal artifacts and validation-state transition. Resolve these through model-owned schemas and existing workflow mechanisms, not a new universal operation engine.

Eligibility is based on actual artifacts and supported science, not the generating model's name. Qualify inputs from all retained generators and BC2. Preserve antibody CDR/framework convenience controls but supply generic chain/region/protected-position controls for other formats. Source-model differences must not force separate user experiences for the same supported operation.

The loop supports a selected subset of operations in scientifically valid orders, including branches that compare alternative sequence designers. Operations may be skipped; no hidden default adds repack, flow, redesign or independent prediction. User-requested native BC2 internal stages remain part of BC2, not these optional external operations.

### 6.2 Sequence redesign: FA-MPNN and existing alternatives

Reuse generic constrained FA-MPNN for full complexes with explicit designed binder regions/chains, fixed target and protected binder residues. Keep target-sidechain and sequence-lock semantics distinct. Retain antibody-specialized masks only for antibody operations. Every sampled sequence is an identifiable child with native outputs, masks, scores and source association.

Preserve and reconcile existing supported sequence-design alternatives instead of deleting them to simplify the launcher. Remove duplicate selectors/booleans from new writes; translate historical requests at one boundary. Caliby and LigandMPNN have the specific obligations below.

### 6.3 Repack and anchor analysis

Extract a separately selectable Rosetta repack operation from current PPIFlow preparation. Declare whether it changes side chains only or includes an explicitly selected backbone shell. Verify actual coordinate changes and protected regions; report changed residues rather than equating shell membership with repacking.

Anchor identification is independently selectable/read-only unless repack was explicitly selected. Repack-off must preserve input coordinates. Save anchor definitions against the exact source or repacked document. Repack, anchor analysis and partial flow must not share an ambiguous user-facing toggle.

Generalize supported complex/chain handling beyond antibody labels. Qualification of generic repack is part of this work, not a reason to leave the entire loop VHH-only.

### 6.4 PPIFlow partial-flow refinement

Retain generator-seeded and downstream refinement identities. Expose stage placement, regions/CDRs, anchor policy, checkpoint, sampling controls and per-sample identity. Current antibody/nanobody checkpoint limitations remain explicit: generic binder support cannot be invented by relabeling chains. Qualify BC2 and other compatible antibody outputs against the real sampler; other modalities still use compatible generic loop operations.

Resolve binder/target roles once and reject disagreement; do not substitute the first chain, drop requested chains or silently remap hotspots. Post-flow sequence redesign is an explicit selected operation, not enabled by omission. Preserve source, flow sample and optional redesign identities rather than enumerating recursive globs. Zero eligible anchors/seeds must produce a clear terminal outcome instead of an apparently successful empty workflow.

### 6.5 Independent prediction and validation

Expose the existing supported predictor choices through their model-owned contracts and native outputs. Reject unknown validators rather than falling back to Boltz-2. Preserve every requested native sample with the correct structure/metric pairing, target-state mapping and missingness. Saved validator choice must survive load/clone/retry.

Independent validation is optional to request, but no modified descendant may claim inherited validation. A user may inspect unvalidated generator/refinement results without being forced through another model. When requested, validation runs against the exact child sequence/complex and remains separate from native generator filtering and scores.

### 6.6 FrustraMPNN and complex-contact frustration

Reuse FrustraMPNN's canonical fan-out/settings/results machinery and preserve complete parent/document/round lineage. Its official inference parses requested chains separately; a complex file does not make a binder-local prediction target-conditioned or provide cross-chain contact frustration. Present its mutation/local-frustration landscape with that scope and retain the unchanged parent for comparison.

The agreed complex-contact-frustration work remains in scope. Qualify a genuinely complex-aware method such as FrustratometeR/FrustraPy through an explicitly chosen scientific contract: chain/contact-pair identity, reference/decoy ensemble, metric meaning and exact structure. Christian must resolve the method/reference choice before that scientific implementation. Do not substitute FrustraMPNN or Rosetta energy under the same label. If qualification fails, report the unresolved requirement for a decision; a disabled placeholder is not completion.

### 6.7 Caliby

Reuse and qualify the existing parent runner rather than reimplementing it. Establish checkpoint/context applicability, masks, complete scientific controls, native output cardinality and sequence/structure identity. Preserve interface versus non-interface design semantics and compare with exact unchanged parents and matched alternative designers. Bring its usable selection, execution and per-model results into the agnostic loop; do not label source implementation as either absent or already live-qualified.

### 6.8 LigandMPNN — in scope, scientific choices pending discussion

Deliver LigandMPNN as an optional refinement/sequence-design method through the single Foundry-owned execution path, integrated with the same candidate selection, local/remote placement, lineage and model-native result experience. It is not a new generator, compulsory step or unknown-partner finder. Inclusion is fixed; the choices below remain proposals until discussion.

The agreed scientific starting point is an intact modeled binder–target structure/sequence complex. Preserve the target and protected binder regions; redesign declared binder positions with explicit structural context. Do not substitute excised free CDR peptides for the intended complex-conditioned question.

Proposed selectable use cases are region-limited redesign and broader binder redesign with explicit protected positions. A controlled fixed-target-side-chain-context on/off comparison is a proposed experimental option, not an assumed improvement. Keep checkpoints, other settings, source structure and masks matched and preserve all results. Conditional sequence probabilities/confidences are model-native sequence compatibility evidence, not affinity, specificity, partner identity or ΔΔG.

Separate optional packing from sequence redesign and retain both identities. Changed sequences can enter the existing explicit independent-validation and comparison steps. No silent replacement of FA-MPNN, Caliby or an unsuccessful requested method.

Discussion must settle the initial selectable modes/defaults, target-context policy, masking/region UX, optional packing placement, comparator design and downstream assessment before implementing those scientific choices. The rest of the workflow can proceed once authorized without inventing answers or dropping this deliverable. Its final accepted settings, runtime, output and UI/API contract must be added to the same completion evidence.

## 7. Required correction ledger for the current workflow

These findings are mandatory implementation work. Extend the ledger if integration exposes more in-scope defects; do not preserve a bug merely for historical behavioral parity. Locations refer to the reviewed source and will move.

- **C01 — Hidden request changes:** remove unknown-validator and unknown-gate fallback and silent stage-optimized overwrites in `routers/jobs.py:1357–1435`; reconcile duplicate frontend/Nextflow authorities. Test explicit values, profile changes, unknowns and saved round trips.
- **C02 — Fabricated/incorrect sequence extraction:** remove the synthetic fallback in `antibody_denovo.nf:90–125`; use a canonical parser preserving insertion codes and chain identity. Test corrupt/empty structures and real residue identity edge cases.
- **C03 — Chain-role substitution:** remove first-chain fallback, dropped requested chains and silent targeting remaps in `prepare_ppiflow_maturation.py` and `modules/ppiflow.nf`. Test exact roles and disagreement rejection.
- **C04 — ESMFold2 sample loss/pairing:** replace independent last-glob selection in `normalize_esmfold2_validation.py:19–30` with producer-bound sample records. Preserve sample cardinality and reject missing/mismatched peers. Repair validator hydration in `AntibodyDenovoTemplate.tsx`.
- **C05 — Entangled refinement:** separate repack/anchors/flow/redesign, remove omission-driven redesign and contradictory flag precedence, and correct repacked-residue reporting. Test repack-off invariance and each selected composition.
- **C06 — Stale validation/terminal producer:** changed post-validation outputs must become unvalidated descendants, not reused `validated_structures`; terminal method follows actual producer, including preserved ancillary refinement branches such as IgGM. Test no stale parent confidence/acceptance inheritance.
- **C07 — Sample identity/zero yield:** replace PPIFlow basename/flat-directory/glob enumeration with explicit source/sample/role manifests; retain cardinality checks and expose zero-anchor/zero-seed outcomes.
- **C08 — Optional failure rollback:** separate primary publication from failed FrustraMPNN attachment in `result_ingester.py:4950–4977` without weakening component checks. Test fresh publication, preexisting parents and failure/retry isolation.
- **C09 — Selection ownership/immutability:** repair global-ID ordinary iteration/manual-mutation lookup, missing state/digest bindings and mutable local selection references. Reuse the stronger existing dedicated selection ownership pattern. Test foreign sources and changed artifacts.
- **C10 — Lineage/state collapse:** replace global path/name `.first()` parent inference, inconsistent origin Design/Job assignment and stem-based deduplication in new publications/review. Test duplicate basenames across stages/states, multiple rounds and same-stem native/derived formats.
- **C11 — Format boundaries:** fix `.cif`/`.mmcif` case-insensitive explicit-format consumption, review discovery and PDB-only iteration admission/conversion. Test author/label chain IDs, insertion codes and refusal of lossy conversion.
- **C12 — Duplicate request assembly:** collapse competing scientific defaults and duplicate new-write selectors in launcher, registry, router and Nextflow for touched models/operations. Preserve historical read adapters, not duplicate active write paths.
- **C13 — Remote repeated work:** measure and correct avoidable selected-weight rescans/hashing, archive reconstruction/extraction and redundant bundle verification at their existing owners. Preserve immutable handoff/reconnect guarantees and private attempt trees. Test warm reuse and corruption/reconnect behavior.
- **C14 — Existing branch continuity:** preserve established generator-only behavior, validation choices, analyses, pause/review, retries and reopen paths while generalizing the workflow. Resolve any discovered unsupported or nonfunctional advertised option explicitly; do not delete it silently to meet a smaller scope.

Delete superseded code/tests/imports after checking references. Do not leave two competing normalizers or result adapters as a permanent compatibility strategy. Historical interpretation may remain in one bounded read adapter.

## 8. Local and remote-bridge execution

### 8.1 One compiled plan, two placements

Compile the same model-owned scientific request to `NativeInvocation` and `SelectedExecutionPlan` for local and remote execution. Register BC2's model/image/weights bindings, native components, portable input/output roles, resources and terminal result contract in the existing registry/provisioning authorities. A Nextflow module alone is not remote integration.

Refinement child jobs use the same placement mechanism. Portable selections bind exact artifacts and maps, not controller absolute paths. Only selected generators/stages contribute dependencies, preparation or transfer. Do not require all alternative generators and refiners to be installed for one selected job.

No new remote-specific scientific defaults, BC2 runner, output scanner, result database or alternative hydration path. The returned native publication is consumed by the same model adapter as local output. Results must reopen with the worker unavailable.

### 8.2 Fast startup and asset lifecycle

Preposition pinned images, shared weights and required support runtime through existing provisioning. Advertise a lane as ready only for its actual selected closure. Separate unavailable assets from scientific incompatibility and from provider liveness. No model import, weight discovery or broad runtime validation on ordinary form rendering/API startup.

Warm execution must not reinstall dependencies, redownload unchanged images/weights, rebuild unchanged image views unnecessarily or run preparation for off-stages. Use persistent native compilation caches where compatible; explain legitimate new-shape/device compilation rather than promising universal cache portability.

The reviewed bridge already reuses assets but still records runtime trees, copies/verifies/extracts source archives, and verifies bundles at prepare/start. Measure these costs. Reuse immutable inventory/identity where valid and remove repeated work at the owner. Do not delete the start-time reconnect guarantee merely because prepare previously succeeded, share writable source trees, or remove rechecks of volatile GPU/CPU/RAM capacity.

Acceptance measurements separate cold attach, missing selected assets, warm new campaign, native initialization/compilation, same-campaign resume, review continuation, refinement round and result return. Capture phase durations, transferred/hashed bytes, scanned files and cache behavior in bounded acceptance evidence, not a new permanent telemetry subsystem. Establish hardware/size-qualified budgets from the actual baseline; no invented universal startup number and no completion claim without the measurements.

### 8.3 Resume, review and cancellation

BC2 and other resumable operations preserve their native durable state in job-owned storage. Same-worker continuation uses existing checkpoint/generation ownership. New-worker recovery stages the declared native resume package and remaps system-owned paths while preserving scientific identity; if native exact continuation is impossible, expose an explicit new attempt rather than silently starting over. Loss of indispensable state is a clear limitation/failure, not fabricated resume success.

Do not relaunch into sealed result generations or make parent publications writable. Preserve native ranked files against destructive UI selection/filtering. A changed request or refinement round writes a new owned output context.

Use the bridge's selected review-artifact transfer mechanism. Multistate candidate sets may exceed its current bounded review budget; page/select documents or use the existing full-result transfer route. Do not silently truncate states or add an arbitrary BC2-only transfer service.

Reuse result publication journals and exact generation/manifest identity. Cover interruption between filesystem publication and database commit, idempotent retries and cancellation during staging/return. Ordinary stop, controller restart and worker-supervisor loss are distinct cases. A missing supervisor PID alone does not prove orphan writers stopped; retain ownership until quiescence is established through the existing lifecycle.

### 8.4 Internal execution and licensing

BC2's hosting restriction concerns third-party invocable functionality, not merely remote infrastructure. Qualify authorized internal local/private-remote execution and retain dependency/weight terms. Third-party API/workflow exposure requires an explicit audience/licensing decision. Rental/start, live scientific campaigns and Production promotion retain their separate authorization requirements.

## 9. Subagent-first implementation plan

The holistic Astra review is complete as a source review, not live acceptance. This revised document incorporates its technical corrections without changing Christian's workflow direction. Implementation begins only when authorized. No extra ceremonial review is required to collect read-only inventory; actual unresolved science/contracts must be resolved before the dependent lane changes them.

### 9.1 Ownership and dependencies

The parent/integrator owns shared contract decisions, registry/router integration, source reconciliation and final acceptance. Assign disjoint files or clearly bounded symbols in isolated short-lived worktrees. Agents may own coherent vertical model slices; no blanket prohibition on that ownership. Children provide exact diffs/commits and evidence, but do not push/deploy or close the overall task independently.

- **BC2 model lane:** complete native inventory and model-owned schema, native resolution bridge, image/campaign invocation, runtime state and necessary producer metadata capture. Own BC2-specific files and native fixtures.
- **Native results/selection lane:** BC2 publication parser with model lane's agreed format, existing result/selection extensions, state-aware identity, mmCIF consumers, repeated-round lineage and primary/optional publication isolation. One assigned editor owns overlapping ingester/selection symbols.
- **Launcher/API lane:** modality/objective and generator choice, model-owned forms, discovery/preview/submission, saved/clone/retry fidelity and one historical compatibility adapter. Preserve all existing generator surfaces.
- **Refinement lane:** general FA-MPNN handoff, repack versus read-only anchors versus supported PPIFlow, independent validation, native sample identity, round orchestration and existing branch repairs. Coordinate explicit input/output contracts before parallel consumers land.
- **Analysis/alternative-method lane:** FrustraMPNN reuse and lineage, Caliby qualification/integration, agreed complex-contact analysis, and Foundry-owned LigandMPNN completion after its scientific choices are discussed. These are delivery workstreams, not optional engineering extras.
- **Bridge/runtime lane:** selected BC2 and refinement dependency closure, portable inputs/native resume state, cache bindings, warm-path corrections and local/remote acceptance instrumentation using existing lifecycle owners.

The parent assigns actual file/symbol ownership before dispatch to avoid simultaneous changes to `jobs.py`, `model_registry.py`, `nextflow.py`, `result_ingester.py`, `JobSubmission.tsx` and shared schemas. Split oversized lanes into disjoint tasks when useful; do not create paperwork-only agents or a new global framework workstream.

### 9.2 Engineering sequence, not reduced releases

1. Capture existing-generator/refinement behavior and defect fixtures; finish native field/output inventory and concrete shared input/result references.
2. Implement model-owned request boundaries and targeted identity/publication repairs; remove proven unsafe fallbacks as replacements land.
3. Build BC2 invocation, native publication and bridge closure in parallel with launcher generalization against the agreed interfaces.
4. Integrate and qualify the agnostic repeated refinement loop, existing alternatives/analyses and LigandMPNN's agreed design. Parallelize independent model work; unresolved scientific decisions block their own implementation, not a fictitious change of scope.
5. Run focused combined-tree checks, then authorized local/remote model and workflow acceptance, including startup measurements and result reopening.
6. Reconcile current `test`, follow supported Development deployment after authorization and verify the actual served/runtime revision and results. Remove integrated temporary worktrees and obsolete implementation code.

A partially integrated slice may be reviewed or merged under truthful availability, but the whole upgrade remains incomplete until every required deliverable closes. No autonomous reclassification into a smaller core release or deferred expansion.

## 10. Acceptance and completion checklist

Each item requires named evidence from the owning model/operation and the integrated workflow. Fixtures are not live acceptance, an enabled registry row is not execution, and a successful transport is not a verified scientific result.

- **A01 — Product structure:** the launcher is agnostic; all existing generator choices plus BC2 are retained as first-class options; relevant modality-specific controls appear without VHH assumptions in generic paths.
- **A02 — Full native BC2:** all relevant installed-code settings, presets, modalities/objectives, supported native campaign/postprocessing actions, outputs and lifecycle behavior reconcile to UI/API/compilation/persistence with no unexplained omissions. Cover feature interactions with data-driven tests, not an unnecessary exhaustive Cartesian-product pipeline matrix.
- **A03 — Request fidelity:** browser/agent equivalence and save/load/clone/retry round trips; requested/effective settings agree with native consumption; no silent engine fallback, hidden override or duplicated scientific authority.
- **A04 — BC2 accounting:** claimed/emitted/scored/passing/retained counts remain distinct and reconcilable; exact scored-to-retained joins; state ordering; adaptive-setting identity; suppressed artifacts; sweeps; trajectory-only; budget-exhausted zero yield; and honest native resume behavior.
- **A05 — Per-model results:** native tables/metrics/ranks and applicable views/exports/review tools use one numerical authority; model-native data and structures reopen after restart and remote worker loss; no universal confidence/affinity substitution.
- **A06 — Selection and lineage:** source/root ownership, artifact/state identity, formats and role maps are preserved; repeated rounds and branches have correct parents/origins; foreign/mixed-incompatible/corrupt selections fail at the boundary; duplicate basenames do not collapse records.
- **A07 — Optional loop delivery:** the upgraded loop can receive qualified candidates from every retained generator and BC2; generator-only exit works; repeated selected rounds work; off-stages neither execute, alter structures nor acquire assets. Genuine model limitations are explicit, not generic infrastructure limitations disguised as science.
- **A08 — Refinement semantics:** repack-only, anchor-only, flow-only and explicitly composed redesign have distinct outputs; correct masks/roles/checkpoints and sample cardinality; coordinate/sequence changes invalidate inherited validation; exact predictor sample/metric pairing.
- **A09 — Analyses and alternatives:** FrustraMPNN, Caliby, complex-contact-frustration work and LigandMPNN each close their agreed scientific request/execution/result/UI/API scope. Experimental labels do not waive execution or result correctness. Pending scientific decisions/unimplemented requirements keep the upgrade incomplete until resolved or explicitly changed by Christian.
- **A10 — Failure isolation:** a failed optional stage leaves verified primary/earlier candidates available; zero yield, failure, missing artifacts and unrequested stages remain distinct; retries do not duplicate scientific identities or destroy parents.
- **A11 — Existing workflow correction:** C01–C14 have focused regressions and integrated evidence; existing generator/refinement/review/reopen routes remain usable with unsafe behavior corrected. Further in-scope issues discovered during work join the checklist.
- **A12 — Local/remote parity:** authorized runs exercise the same compiled request and native result contract on both placements, including generation, candidate selection, refinement and return. Preserve model-appropriate stochastic behavior; parity does not require bitwise identical predictions across different hardware.
- **A13 — Startup and lifecycle:** cold/warm phase measurements, selected asset/cache reuse and elimination of demonstrated redundant work; resume/review continuation; interruption/cancellation/result-journal recovery; reopened results without a live worker. Do not claim a warm performance budget from static inspection.
- **A14 — Integrated delivery:** focused tests on the combined current tree, representative real native branch execution after authorization, actual Development deployment revision/readback when authorized, clean repository and retirement of replaced code. A small smoke run need not yield an accepted binder; execution correctness and scientific yield are reported separately.

Complete relevant model settings and solid results remain mandatory. Avoid redundant validation by locating checks at the owning request, immutable input, producer publication and transfer/selection boundaries and reusing their established evidence. Do not remove a necessary ownership or integrity check to make the path appear simpler.

## 11. Remaining decisions, without reopening scope

- **LigandMPNN:** discuss region-limited versus broader binder redesign, structural target context, side-chain-context comparison, packing, matched baselines and downstream assessment. Inclusion in the optional loop is already required.
- **Complex-contact frustration:** choose and qualify the actual complex-aware method/reference ensemble. Binder-local FrustraMPNN is not a substitute.
- **Concrete native interfaces:** finish generated BC2 coverage and exact producer metadata/publication details before dependent code; this is implementation work, not permission to narrow native scope.
- **Execution acceptance:** select authorized representative inputs/hardware and set measured startup expectations. No paid instance or scientific campaign is implicitly approved by this document.
- **Exposure:** settle any intended third-party hosted access under upstream/dependency terms; internal remote placement is a separate question.

No open item permits changing the user-facing workflow structure, dropping existing generators, reducing BC2 coverage, leaving the loop nanobody-only, treating remote execution as later work, or omitting LigandMPNN because it is optional to run.
