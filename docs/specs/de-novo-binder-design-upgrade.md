# De Novo Binder Design Upgrade: Implementation Specification

**Status:** Updated to Christian's three concurrent work tranches: agnostic workflow/core/bridge, full BC2, and experimental LigandMPNN sanity-check validation. This is a specification and implementation plan, not a claim of implementation or authorization to launch scientific campaigns, deploy or rent workers. Separate optional target-context and pose assessments, post-review user-defined cutoffs and subset requeue are fixed; native input/method applicability still requires verification.

**Companions:** [Upgrade outline](de-novo-binder-upgrade-outline.md) and [concurrent implementation SOW with subagent prompts and pass/fail gates](de-novo-binder-concurrent-implementation-plan.md). The concurrent SOW supersedes this document's earlier workstream allocation and ordinary-redesign interpretation of LigandMPNN, not the remaining upgrade scope.

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
5. Choose compatible refinement, sequence design, optional target-context and/or blind pose validation, and analysis operations; inspect their model-native results and parent/descendant comparisons without requiring a pass/fail cutoff.
6. After a round finishes, select any compatible subset of candidates/descendants for another bounded round of chosen operations or finish. Reopen the same work later without reconstructing identity from filenames; retain originals and their native evidence.

The entire workflow is in scope, not just BC2's wrapper:

- Preserve RFantibody, BoltzGen and seeded PPIFlow choices and native routes, and reuse existing applicable general de novo/RFD3 capabilities without displacement or duplicate execution.
- Integrate BC2's entire supported native scientific campaign surface, relevant controls, outputs and lifecycle. It is optional as a generator choice, not partial in implementation.
- Generalize and upgrade the existing refinement loop for candidates from all these generators, using actual input/model compatibility rather than nanobody-only assumptions or producer-name restrictions.
- Correct existing workflow defects, including request handling, stage execution, selection, lineage, samples, results and continuation.
- Deliver working local and remote-bridge execution across generation and refinement, with efficient startup and reliable result return/reopening.
- Deliver solid per-model settings and results, human/agent parity, useful review and repeatable iteration.
- Include independent operator-selectable LigandMPNN target amino-acid/structural-context assessment and blind pose verification in refinement/review. Either or both can be run on selected subsets after a round, locally or on the existing remote bridge. Ordinary sequence redesign does not satisfy the LigandMPNN deliverable.
- Retain the agreed FA-MPNN, repack/anchor/PPIFlow, independent validation, FrustraMPNN, Caliby and complex-contact-frustration work.

**Optional for the operator does not mean optional to deliver.** There is one completion scope. Engineering milestones are not a smaller accepted product. An in-scope operation left unavailable is unfinished work unless Christian explicitly changes that requirement.

The implementation is divided into three concurrent tranches, not sequential release reductions: **Phase 1** generalizes and repairs the whole existing workflow/refinement/results/remote bridge; **Phase 2** integrates full native BC2 as a peer generator; **Phase 3** implements the experimental LigandMPNN sanity-check validator. Shared contracts create specific producer/consumer dependencies, not a requirement to finish Phase 1 before starting the others.

Agnostic does not mean every checkpoint supports every molecule or format. Genuine native limitations must be explicit. They do not justify retaining antibody assumptions in otherwise generic infrastructure or abandoning qualification work that this upgrade requires.

## 2. Current implementation and reuse boundaries

### 2.1 Existing generator execution

`platform/api/services/nextflow.py:507–527` routes RFantibody to `workflows/antibody_denovo.nf`, BoltzGen nanobody to `workflows/protein_design.nf`, and seeded PPIFlow to `workflows/ppiflow_generator_design.nf`. Preserve their scientific identities and reuse those modules. The existing `/antibody-iteration/from-designs` endpoint rejects non-antibody roots and creates only `template_antibody_denovo/antibody_refinement_pipeline` jobs; the full-root model-registry rule requires antibody `target_pdb` and `epitope_residues`. Repair this parent-owned API admission/launch boundary for generic sources rather than merely unlocking the frontend or routing every generator into the RFantibody parent. Keep antibody-native input requirements on antibody requests. Refactor shared staging/continuation where necessary; do not build a universal scientific DAG inside the RFantibody parent.

The current `AntibodyDenovoTemplate.tsx` and `antibody_denovo.yaml` contain VHH roles, CDR/framework defaults, duplicated selectors and handwritten parameter assembly. Replace the touched assembly with model-owned request components beneath the agnostic launcher. The visible entry card's nanobody label/stages actually live in `platform/frontend/src/lib/launcherCatalog.ts`; update that owner too. There is no active antibody-denovo template YAML to rename: `config/templates/binder_design.yaml` is a disabled historical RFdiffusion/ProteinMPNN/AF2 pipeline, not this product. Do not merely rename the card, enable that template or append another large BC2 conditional branch.

Existing general-design/RFD3 owners remain usable. Their existence is neither a reason to exclude BC2's general modalities nor a reason to create duplicate RFD3 execution.

### 2.2 Existing downstream implementations

Generic constrained FA-MPNN exists in `modules/fampnn.nf`, `workflows/protein_sequence_design.nf` and `scripts/prep_fampnn_constraints_generic.py`. Reuse explicit design/fixed-region handling rather than passing generic binders through antibody masks.

Caliby has a parent-workflow path (`antibody_denovo.nf:2511–2555`), `modules/caliby.nf` and `scripts/run_caliby_sequence_design.py`. Its installed runtime, setting completeness and suitability for each proposed complex remain to be qualified. Describe it as implemented-but-needing-qualification, not absent or already proven.

FrustraMPNN has canonical model settings, scheduler fan-out, artifact/result persistence and viewers. Reuse these owners, repairing workflow attachment and failure isolation instead of inventing another analysis runner.

Foundry remains the single LigandMPNN execution owner under the existing architecture, but the reviewed entrypoint/workflow/module inventory has no explicit LigandMPNN executable route. Implement the missing experimental binder diagnostic there, not a second runner. The existing `ligandmpnn.yaml` describes chemistry-oriented modes and says `enabled: true, experimental: false`; those modes are not the diagnostic and their saved meaning must not be overwritten. Establish truthful executable/readiness admission for the diagnostic and any already advertised but unexecutable mode. A registry row is not a working integration; a Project catalogue denial alone is not a complete diagnosis of core execution.

### 2.3 Existing result and bridge infrastructure

Reuse Job/Design, scientific artifact receipts/datasets, model-native result adapters, selection and review services. `result_contracts.py` primarily routes viewer/analyzer capabilities; adding a row there does not establish native result integrity or implement a viewer.

Reuse `NativeInvocation`, `SelectedExecutionPlan`, selected asset provisioning, worker lifecycle, checkpoint continuation and journaled remote result publication. Do not create BC2-specific scheduling, hydration, transfer or resume services beside these owners.

## 3. Launcher, settings and plan ownership

### 3.1 Modality-aware authoring

Select format/objective first, then show compatible generators and their native controls. Preserve complete BC2 access to advanced formats/objectives. Do not force a lowest-common-denominator target or binder form across models. A model-specific input editor is appropriate when the native input differs.

RFantibody, BoltzGen, seeded PPIFlow and BC2 must remain selectable within the same product. Seeded PPIFlow visibly requires a compatible prior complex; do not advertise it as unseeded generation. Existing supported non-VHH capabilities should become reachable where the generator actually supports them.

When a user changes generator or modality, retain valid shared inputs, preserve model-specific drafts, identify incompatible values and require an explicit correction. Do not silently relabel chains, discard settings or substitute an engine.

`routers/user_templates.py` currently normalizes antibody-template framework paths, residue selections and the first antigen chain even during reads. Keep that historical repair bounded to genuine legacy antibody records; new generic templates and saved model drafts must round-trip without server-side coercion. Test server read/write as well as frontend hydration.

The current `retiredWorkflowRemovalContract.test.ts` rejects the substring `bindcraft` in `JobSubmission.tsx`, which also catches a legitimate `bindcraft2` label. Update the contract deliberately when exposing BC2: keep exact retired-v1 identifier/routing assertions and add positive BC2 admission rather than hiding the new model from the test.

Separate independent facts in the backend projection: scientific applicability, executable adapter, selected-target readiness, experimental designation and whether an operation is selected. “Not requested” is plan state, not a capability. Do not build a second general-purpose capability framework to represent these facts.

### 3.2 One scientific authority per model

Each model/operation owns one versioned scientific schema and native compiler. Registry and workflow definitions reference it; they do not redefine defaults in YAML, frontend state, Project schema, Nextflow and shell independently. Reuse BMS's typed control mechanisms with model-specific editors for targets, chains, regions, arrays and nested settings.

Required fields include native mapping, type/shape, effective default, units, applicability, incompatibilities, explanation, reproducibility significance and operator/profile/scheduler ownership. Browser and agent API expose the same relevant scientific options. Advanced sections manage density; raw JSON import/export supplements rather than replaces typed controls.

Presets are explicit initial values or visibly fixed profile choices. Loading, cloning, retrying, changing stages or refreshing global configuration must not overwrite saved/operator values. Unknown scientific selectors fail before queue insertion; aliases are interpreted only by one documented historical adapter.

Preview uses the existing submission/approval mechanism and displays exact requested and resolved effective settings, input identities and incompatibilities. Launch binds to that request identity. Nextflow consumes compiled settings and must not introduce another scientific-default layer.

Runtime paths, credentials, storage roots, container identities and physical placement stay system-owned. Record applicable runtime identity once through the existing receipt mechanism. Treat worker/batching choices that change native sampling behavior explicitly rather than silently dismissing them as irrelevant.

### 3.3 Workflow plan and repeated rounds

The workflow stores explicit generator and selected operation references. It connects existing model jobs/stages; it is not another scientific engine. Support both preselected continuation and selection after generator review using the existing job/child/review mechanisms.

The refinement experience is an actual loop: select an exact candidate/state set, choose operations and their settings, run a bounded round, review descendants, compare with parents and select a subset for another round. A completed round must support requeue of independently chosen compatible analyses/assessments or refinement on that subset, not force a whole-campaign restart or re-run of off-operations. Persist round/root/parent relationships for every source, including non-antibody roots. A user may branch multiple alternatives from one parent without overwriting it.

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

The current `JobDetailsPanel.tsx` gates the shared execution-settings pane to ESMFold2 and antibody IDs. BC2 review must expose the complete effective campaign and per-attempt adaptive settings from the actual job-detail route, using that pane only if it faithfully represents the native data; otherwise use the model-owned view there. A parser-only settings record that the operator cannot inspect is not result parity.

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

Preserve and reconcile existing supported sequence-design alternatives instead of deleting them to simplify the launcher. Remove duplicate selectors/booleans from new writes; translate historical requests at one boundary. Caliby has the specific obligations below. The required LigandMPNN deliverable is the separate experimental sanity check in section 6.8, not an ordinary sequence-design selector.

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

Reuse the **full existing global FrustraMPNN implementation**: its pinned native capability, complete typed scientific settings and UI/agent parity, scheduler fan-out, full 20-substitution landscape, model-native records, statistics, structure/sequence-linked workbench, exports, captures, persistence, and immutable parent/document/round lineage. Do not replace this with a reduced binder-only runner, a single summary number, or a workflow-local viewer. Score the actual selected binder structure (including its intrachain domain context where applicable) and retain the unchanged parent for comparison. The binder was generated against its target, so its modeled conformation and sequence can carry target-specific design history; FrustraMPNN's binder-local landscape is useful exploratory evidence for that candidate. However, official inference parses each requested chain separately: changing only the target chain while keeping the binder atoms unchanged does not change its prediction. Label that distinction clearly: *target-designed binder signal* is not *direct target-conditioned inference*, a binding measurement, or cross-chain contact frustration. Score distinct binder chains as distinct native chains rather than joining them. Artificially joining binder and target into one chain or changing the native multi-chain graph is future research, not this workflow's implementation or a validated shortcut.

The agreed complex-contact-frustration work remains in scope. The [FrustraMPNN preprint](https://doi.org/10.64898/2026.01.22.701012) and [inference repository](https://github.com/RosettaCommons/frustraMPNN) provide *single-residue* mutation profiles, explicitly noting pairwise contacts as a future model extension; the preprint describes configurational and mutational **FrustratometeR** contact modes but does not endorse a configurational-first interface workflow. Mutational pairwise analysis has actual protein–protein interface precedent ([Ma et al. 2025](https://doi.org/10.1038/s41467-025-63713-7); [Wei et al. 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12262341/)). Qualify a genuinely complex-aware pairwise method via an explicitly chosen scientific contract: chain/contact-pair identity, reference/decoy ensemble, metric meaning and exact bound complex. FrustraPy is an unofficial implementation and requires agreement with FrustratometeR for the selected case; it is not the FrustraMPNN contact mode. Christian chooses the method/reference before that scientific implementation. Do not substitute binder-local FrustraMPNN or Rosetta energy under the same label. If qualification fails, report the unresolved requirement rather than inventing a surrogate.

### 6.7 Caliby

Reuse and qualify the existing parent runner rather than reimplementing it. Establish checkpoint/context applicability, masks, complete scientific controls, native output cardinality and sequence/structure identity. Preserve interface versus non-interface design semantics and compare with exact unchanged parents and matched alternative designers. Bring its usable selection, execution and per-model results into the agnostic loop; do not label source implementation as either absent or already live-qualified.

### 6.8 Phase 3: experimental LigandMPNN interface-context sanity check

Deliver the custom target amino-acid/structural-context compatibility diagnostic by adding its missing executable path under Foundry's sole LigandMPNN ownership, using the same exact candidate selection, local/remote placement and model-native publication. Offer it and the B-owned blind structure/pose verification as **separate, independently selectable options** after any round, including on a user-selected subset requeued for another round. Preserve a selected CDR or binder/target patch and evaluate opposing interface context under a declared masked-input recovery/compatibility policy. Do not substitute a routine binder-redesign option, a new generator or an unknown-partner finder. Preserve the older chemistry-mode request contract separately; its current `enabled` flag and Foundry's unpinned/deferred-download recipe are not evidence of diagnostic readiness.

A small assessment region need not mean an excised peptide: retain the declared surrounding structural context. LigandMPNN requires supplied backbone geometry; its sequence/context evidence is not independent pose recovery, affinity, specificity, partner identity or ΔΔG. The information being recovered must not leak into conditioning through visible residue identities or side-chain atoms. Keep diagnostic sequences as diagnostic artifacts rather than silently changing the assessed target or binder.

Bind every result to exact candidate/state/artifact and round, fixed and assessed regions, visible/held-out context, checkpoint/settings and comparator identity. Preserve native outputs and distinguish execution status, scientific qualification, raw metrics and **unclassified** (no cutoff yet) from user-classified pass/fail/inconclusive outcomes. A later user-defined cutoff is saved/versioned separately and can explicitly re-evaluate retained raw results without rewriting them. Default-off assessments add no dependencies, preparation or work. Use typed UI/API controls and the existing result experience; no duplicate runner, validator framework or scoring database.

Either check may run alone; pairing is optional. If both are selected for the same candidate/context, complete validly and are classified by their user-saved cutoffs, only joint failure triggers computational rejection under the *selected* dual-failure policy. Joint pass reports support, not proven binding; disagreement reports mixed evidence. Unrun, unclassified, unsupported, cancelled, errored, stale, unqualified or inconclusive checks do not count as scientific failures. Preserve rejected candidates and all evidence; do not delete native records or overwrite generator acceptance.

Verify what the blind predictor actually receives before claiming independent recovery. Establish the applicable patch/direction, conditioning/masking, native metric/comparator and sample aggregation; display exploratory/calibration evidence before asking operators to define score cutoffs. Do not invent fixed thresholds for implementation convenience. A subsequently selected policy classifies current/prior results without altering them; freeze that policy before claiming independent performance on separate held-out cases. Plumbing and unrelated lanes proceed concurrently. The exact dispatch prompt and optional decision rules are in sections 12–13 of the concurrent SOW.

Demonstrate that the chosen pinned checkpoint and Foundry invocation support the *protein–protein interface* conditional task, not merely the existing ligand/metal/nucleotide/DNA modes. If applicability cannot be established, retain diagnostic data as inconclusive and keep that scientific gate open; do not turn model output into a binary rejection by default. A valid optional assessment with raw evidence is deliverable *without* a cutoff; the operator chooses one after inspection. The separate Foundry LigandMPNN chemistry-context sequence-design obligation in the global Protein In Silico SOW is neither completed by this diagnostic nor an unrelated Project adapter prerequisite for the binder upgrade.

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

### 8.4 Internal remote-bridge execution and third-party exposure

BC2 and the full selected workflow must execute through **our existing remote bridge** with installed/downloaded pinned models; renting an external compute worker to execute our internal jobs does not alone make the BMS product a third-party hosted API. Qualify local and remote request-to-result paths, result return/reopening and model dependencies rather than postponing bridge support behind a licensing discussion. If BMS is actually offered as a service to outside users, separately check upstream/dependency terms and enforce the authorized audience before such access; that is not an internal-remote release gate. Specific rental/starts, live campaigns and Production promotion retain their separate authorization requirements.

## 9. Concurrent subagent-first implementation plan

The [concurrent implementation SOW](de-novo-binder-concurrent-implementation-plan.md) is the dispatch authority. It supplies strict scope rules, sole-writer boundaries, full prompts, required work, per-lane pass/fail tests, localized scientific decisions, performance gates and integrated acceptance. Read its common instructions and the complete lane packet before implementation; this summary is not a substitute.

The parent owns shared API/registry/schema integration, baseline reconciliation and final closure. Eight implementation lanes run concurrently: A launcher/request experience; B refinement and blind structure validation; C candidate selection/shared results; D existing alternatives/analyses; E BC2 settings/native campaign; F BC2-native publication; G shared bridge/runtime/performance; H experimental LigandMPNN validator. One writer owns each shared file. Children return code/evidence and do not independently push, deploy or close requirements.

Agree concrete, small contracts for model requests, selected candidate/documents, native publication and existing bridge placement. The parent records their field names and representative typed examples in the dispatch packet or current tests and lands minimal shared-hub hooks before dependent agents edit divergent production adapters. Inventory, fixtures and independent leaf work can start across all three phases immediately; do not serialize the entire project by phase number. E/F agree the exact native producer metadata. B/H bring the blind-input and recovery-policy decisions, and D the complex-contact method/reference decision, to Christian early; unrelated work proceeds while those scientific choices are resolved. Integrate coherent slices early rather than maintaining divergent independent applications.

Use focused combined-tree checks, a bounded cross-lane review and authorized actual native/local/remote execution with measured startup, then supported Development deployment/readback when authorized. No second settings database, generic workflow engine, duplicate runner, per-job bootstrap or new validation bureaucracy. A partial slice may be integrated truthfully but the entire upgrade remains unfinished until all three tranches close their required gates. Only Christian can change the SOW.

## 10. Acceptance and completion checklist

Each item requires named evidence from the owning model/operation and the integrated workflow. Fixtures are not live acceptance, an enabled registry row is not execution, and a successful transport is not a verified scientific result.

- **A01 — Product structure:** the launcher is agnostic; all existing generator choices plus BC2 are retained as first-class options; relevant modality-specific controls appear without VHH assumptions in generic paths.
- **A02 — Full native BC2:** all relevant installed-code settings, presets, modalities/objectives, supported native campaign/postprocessing actions, outputs and lifecycle behavior reconcile to UI/API/compilation/persistence with no unexplained omissions. Cover feature interactions with data-driven tests, not an unnecessary exhaustive Cartesian-product pipeline matrix.
- **A03 — Request fidelity:** browser/agent equivalence and save/load/clone/retry round trips; requested/effective settings agree with native consumption; no silent engine fallback, hidden override or duplicated scientific authority.
- **A04 — BC2 accounting:** claimed/emitted/scored/passing/retained counts remain distinct and reconcilable; exact scored-to-retained joins; state ordering; adaptive-setting identity; suppressed artifacts; sweeps; trajectory-only; budget-exhausted zero yield; and honest native resume behavior.
- **A05 — Per-model results:** native tables/metrics/ranks and applicable views/exports/review tools use one numerical authority; model-native data and structures reopen after restart and remote worker loss; no universal confidence/affinity substitution.
- **A06 — Selection and lineage:** source/root ownership, artifact/state identity, formats and role maps are preserved; repeated rounds and branches have correct parents/origins; foreign/mixed-incompatible/corrupt selections fail at the boundary; duplicate basenames do not collapse records. Generic non-antibody roots pass actual server iteration admission and launch without being mislabeled as antibody.
- **A07 — Optional loop delivery:** the upgraded loop can receive qualified candidates from every retained generator and BC2; a real, provenance-bound BC2 candidate must reach compatible selection/refinement to close BC2 handoff, not merely produce zero-yield accounting. Generator-only exit works; after each completed round an operator can select a compatible subset for another bounded refinement or assessment round, retaining exact roots/parents and all prior results. Off-stages neither execute, alter structures nor acquire assets. Genuine model limitations are explicit, not generic infrastructure limitations disguised as science.
- **A08 — Refinement semantics:** repack-only, anchor-only, flow-only and explicitly composed redesign have distinct outputs; correct masks/roles/checkpoints and sample cardinality; coordinate/sequence changes invalidate inherited validation; exact predictor sample/metric pairing.
- **A09 — Analyses and experimental validation:** FrustraMPNN, Caliby and separately qualified *pairwise cross-chain* complex-contact work close their agreed scopes. LigandMPNN target amino-acid/structural-context compatibility and blind pose verification are independently selectable; the optional dual-failure rule consumes them when both are selected, applicable and classified. The LigandMPNN operation is not ordinary redesign and requires checkpoint applicability to protein–protein context. Expose raw metrics before any operator-defined, saved/versioned cutoffs; re-evaluate retained evidence explicitly when policy changes. The existing chemistry-context sequence-design contract remains a separately tracked obligation. Experimental labels do not waive execution, leakage controls or result correctness; absence of a user cutoff alone does not block the delivered assessment. Pending scientific method choices/unimplemented requirements keep the upgrade incomplete until resolved or explicitly changed by Christian.
- **A10 — Failure isolation:** a failed optional stage leaves verified primary/earlier candidates available; zero yield, failure, missing artifacts and unrequested stages remain distinct; retries do not duplicate scientific identities or destroy parents.
- **A11 — Existing workflow correction:** C01–C14 have focused regressions and integrated evidence; existing generator/refinement/review/reopen routes remain usable with unsafe behavior corrected. Further in-scope issues discovered during work join the checklist.
- **A12 — Local/remote parity:** authorized runs exercise the same compiled request and native result contract on both placements, including generation, candidate selection, refinement and return. Preserve model-appropriate stochastic behavior; parity does not require bitwise identical predictions across different hardware.
- **A13 — Startup and lifecycle:** cold/warm phase measurements, selected asset/cache reuse and elimination of demonstrated redundant work; resume/review continuation; interruption/cancellation/result-journal recovery; reopened results without a live worker. Do not claim a warm performance budget from static inspection.
- **A14 — Integrated delivery:** focused tests on the combined current tree, including deliberate retired-v1 guard/BC2-admission coverage, representative real native branch execution after authorization, actual Development deployment revision/readback when authorized, clean repository and retirement of replaced code. A small smoke run need not yield an accepted binder; execution correctness and scientific yield are reported separately.

Complete relevant model settings and solid results remain mandatory. Avoid redundant validation by locating checks at the owning request, immutable input, producer publication and transfer/selection boundaries and reusing their established evidence. Do not remove a necessary ownership or integrity check to make the path appear simpler.

## 11. Remaining decisions, without reopening scope

- **Candidate checks:** target amino-acid/structural-context compatibility via LigandMPNN and blind pose verification are separate operator-selectable options, singly or jointly. Verify native masking/metric and predictor input facts during implementation; expose raw metrics for inspection before the operator defines any cutoff. A saved user policy governs later classification or re-evaluation. Dual valid scientific failure under a selected dual policy means computational rejection; unrun/error/inconclusive does not. No chosen threshold is a feature-installation prerequisite; supplied pose is not blind recovery.
- **Complex-contact frustration:** choose and qualify a pairwise cross-chain method/reference ensemble. FrustraMPNN's per-residue predictions are not a substitute. Mutational pairwise indices have direct protein–protein-interface precedent; the earlier configurational-first suggestion was not established by the FrustraMPNN preprint and is not an approved decision.
- **Concrete native interfaces:** finish generated BC2 coverage and exact producer metadata/publication details before dependent code; this is implementation work, not permission to narrow native scope.
- **Execution acceptance:** select authorized representative inputs/hardware and set measured startup expectations. The full selected workflow must actually run via our remote bridge. No specific paid worker start or live scientific campaign is implicitly approved by this document.
- **Exposure:** only actual third-party hosted/API access creates a separate licensing/audience question; our installed model's internal remote placement is not that blocker.

No open item permits changing the user-facing workflow structure, dropping existing generators, reducing BC2 coverage, leaving the loop nanobody-only, treating remote execution as later work, or omitting LigandMPNN because it is optional to run.
