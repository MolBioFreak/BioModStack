# Binder workflow completion specification

Status: final V2 scope and implementation specification, 24 September 2026. This is the controlling completion packet, not a claim that implementation, native acceptance or deployment is finished. Christian's direction is fixed. Remaining delivery includes model/request adapters, Python and scripts, Nextflow and data processing, UI refactoring, Project integration, efficiency and local/remote verification. This revision replaces the stale harmonization backlog and historical dispatch assumptions.

Companions form one packet:

- [Product outcome](de-novo-binder-upgrade-outline.md).
- [Complete scope and acceptance](de-novo-binder-design-upgrade.md): native denominator and A01–A14/C01–C14 definitions.
- [Implementation packet](de-novo-binder-concurrent-implementation-plan.md): interfaces, sole-writer boundaries, work order and focused verification.
- [Model configuration/operator/agent parity policy](../Model_Configuration_Operator_Control_and_Agent_Parity.md).

“V2”/“generation 2” is a development label, not a new product namespace, job family or schema suffix. The product remains **De Novo Binder Design**.

## 1. Fixed outcome and boundaries

The binder starting-generator lineup is **BC2, BoltzGen, PPIFlow and RFantibody**. Each has model-correct inputs, controls, native execution, results and compatible continuation. RFD3's currently integrated unconditional monomer generation and local redesign remain in separate De Novo Design; remove their presentation as a fifth binder-generator option without removing those products. Compatible imported structures may use appropriate selected operations without implying that they are binders.

PPIFlow has two independently required deliveries:

1. **Agnostic initial-candidate generation**, a peer of BoltzGen/BC2, covering native-supported general protein-binder and antibody/nanobody generation rather than a VHH-only or seeded partial-flow wrapper. It does not require another generator first producing a candidate.
2. **Retained, upgraded and verified use in our optional refinement loop**, including the actual partial-flow/maturation, source-bound regions/anchors, preparation and output behavior. Its narrower operation/checkpoint constraints stay local to that use case.

Neither PPIFlow role closes the other. Independent first-generation product placement does not mean target-free sampling: target-conditioned binder generation and native unconditional monomer generation are different tasks. Preserve real native limits without treating current BMS adapter limitations as the intended scope.

Every starting generator receives full applicable target/template acquisition and inspection, with BC2's delivered workspace as the interaction reference. Structural frameworks/scaffolds/templates, seed complexes, sequence templates and saved workflow configurations remain distinct. Reuse Project management, model schemas, Jobs, selection, artifacts/results and the bridge. No second workflow engine, candidate database, settings database, Project store, numerical authority or scheduler.

Generator-only completion is valid. After a round the operator may select exact compatible candidates/documents, independently choose operations, inspect/compare descendants and repeat. Optional to execute does not mean optional to deliver. Retain full global FrustraMPNN, Caliby, FA-MPNN/ProteinMPNN, supported prediction and the existing GROMACS handoff.

Cutoff-policy CRUD, saved classification/reclassification, automatic dual-pass/fail and diagnostic rejection machinery were superseded and are excluded. Independent raw LigandMPNN interface-context and blind-pose evidence remain in scope. Pairwise Frustratometer remains deferred. No new scientific thresholds, compulsory diagnostic/project, audit receipt, restart proof or extra preview is introduced.

Missing proof limits completion claims; it never authorizes a new runtime refusal. Preserve existing ownership, corruption, native-input and lifecycle checks. Any exact new restriction or consequential scientific method/default change requires Christian's decision. This specification authorizes neither campaigns, paid resources nor deployment.

## 2. Verified baseline and implementation credit

Binder source was reviewed at `77c5c5a2c952dfd70dad904352a5c4b7ce52cb13`. At finalization, canonical source and remote `origin/test` agreed on `d956efad063388d72f5a21aaf8ad1612375c6dda`, with canonical source clean. The intervening commits change BioXP transfer submission and its runtime authority/tests only; the reviewed binder, Project and bridge owners are unchanged. Earlier API/build equality at the review revision is recorded in the external baseline, not presented as a new live probe. Old status at `6e01a27da607e41c972f034f0b15744a619837dd` is superseded.

This documentation pass ran no model inference, application test suite, job submission, worker start or deployment. Source/test-contract inspection establishes implementation, not a newly observed test pass. Installed PPIFlow files were read with stdlib-only image inspection, without importing models.

Delivered foundations to preserve:

- BC2 designed authoring, typed nested settings, target/scaffold acquisition, preview-bound launch, native actions/resume, nested-root publication, native state/accounting and settings readback. Do not repeat draft-only, wrong-root or resume-false claims.
- Binder-neutral continuation, same-root ownership, immutable snapshots, independent refinement operations and prepared remote review. Do not weaken the legacy antibody route to make it universal.
- Selected Caliby, full FrustraMPNN, predictors, typed MD source lineage/handoff and independent raw diagnostic routes. Their presence does not certify complete UX or integrated native execution.
- Producer-bound PPIFlow sample recording, shared source/parent/state repair, native CIF handling and primary-result isolation, including manual return.
- Shared source, Project, workbench and bridge infrastructure. Connecting each binder path remains real work.

Specific remaining software gaps:

- BoltzGen still submits nanobody/VHH requests; its generic preparation branch can omit the target.
- BMS's current PPIFlow “generator” is `antibody_denovo/generator_backbone_refine`, requiring a seed complex and calling partial flow. It is not the requested agnostic native initial-generation integration. Optional light-chain input also conflicts with forced VHH metadata.
- Source entry points, structural-template controls and full-viewer access are uneven. PPIFlow's consumed seed lacks the equivalent source-bound workspace. BC2's compact hidden-controls Mol* embedding is not proof of full-workbench access.
- `BinderNativeRoute` transfers route identity but not populated sources. Saved ESMFold2 template selection has a concrete hydration omission.
- Neutral selected refinement lacks diagnostics' alternate document/state selector.
- Project setup, launch-context propagation and native zero-Design result adapters are incomplete for binder work.
- Native local/remote, real candidate handoff, repeated rounds, lifecycle and performance remain separate execution-evidence tasks.

### Installed PPIFlow source resolves the generation question

Packaged source reports `000ce45a4411e7b97c1523a22c0f1bece7ede5fc` and separate native entrypoints:

- `sample_binder.py` imports `experiments.inference_binder.Experiment` for target-conditioned general binder generation.
- `sample_antibody_nanobody.py` imports `experiments.inference_antibody.Experiment`, with separate antigen/framework inputs.
- `sample_antibody_nanobody_partial.py` and `sample_binder_partial.py` import their respective partial-flow inference owners.
- `sample_monomer.py` is a different monomer/scaffolding task, not the binder-generation deliverable.

The installed guide gives a different antibody partial-flow filename from the actual source; use source-discovered names. Native generation exists independently of the current BMS refinement-shaped wrapper. Source presence does not establish complete checkpoints, typed settings, reproducible packaging, native execution or BMS publication. Finish those connections rather than claiming a native capability is absent.

### Project integration fact-check

The reuse premise is correct for infrastructure, but “mostly connected” or “easy” is unproven. Project hierarchy, setups/drafts/revisions/preparations, run groups/attempts, launch contexts, datasets, attachments and reopening already exist. Concrete gaps are:

1. `JobSubmission.tsx` passes Project draft reporting to other native forms, not its mounted `AntibodyDenovoTemplate`.
2. The Project capability and native-owner registries do not register the complete binder setup/model-mode paths. Catalogue unavailability does not prove a standalone engine is missing.
3. Jobs have no standalone `project_id` here. Membership uses opaque `JobCreate.launch_context_id` and existing server binding/run-attempt machinery. BC2 `project_folder` is unrelated.
4. Selected continuation and diagnostic Jobs preserve scientific ancestry but omit destination launch contexts. Lineage is not automatic Project membership. Typed MD already forwards its context.
5. `TypedCoreJobResultAdapter` joins to Designs and requires a nonempty Design set; its registered models exclude BC2. It cannot unchanged cover valid zero-yield campaigns or zero-Design diagnostics.
6. Source selectors do not mount Project-resource browsing. Having setup context does not provide a structure picker.

Complete the existing adapters and handoffs. Do not add destination hierarchy to scientific params, infer destination from a source, reuse a consumed parent context for fan-out or create another Project system. Infrastructure reuse alone supports no effort estimate.

## 3. Shared authoring, source and draft contract

### U01. Presentation shell

Extract small `BinderWorkflowWorkspace.tsx` chrome: title/help, collapsed engine chooser, section navigation, editor, concise summary, existing placement/submit controls and saved-configuration action. Slots/callbacks are `title`, `description`, `sections`, `activeSection`, `onSectionChange`, `engineChooser`, `children`, `summary`, `executionControls`, `submitControls`, `library`.

The shell owns no scientific defaults/schema/compiler or launch decision. Sections are freely navigable, not a gated wizard. Preserve populated source/selection state when switching. No completion badges, validation dashboard or mandatory JSON review dump. Use existing theme tokens, responsive layout and focus/error styling. Advanced groups retain all relevant settings; sliders use real native bounds and precise inputs. Do not impose BC2 preview/digest requirements on other engines.

### U02. Full applicable acquisition

Each Target and structural Template/Framework/Scaffold/Seed role exposes applicable existing mechanisms: upload PDB/CIF/mmCIF, managed files, direct PDB ID, RCSB search/cache, saved Job → Design → exact available document/state, presets/structure libraries and Project resource/dataset members. Antibody library/SAbDab applies where scientifically meaningful. Sequence/FASTA entry applies only where natively supported.

Reuse `TargetAntigenSelector`, `JobBrowser`, `FrameworkBrowser`, governed file/RCSB/framework APIs and the richer `BindCraft2StructureInputs` mechanisms. Extend source identity at the existing owner: `SelectedTarget` currently has `upload|run|preset|rcsb`, and Your Runs is primary-Design selection rather than arbitrary-artifact selection. Project and exact-document references are real additions, not already delivered tabs.

Acquisition support and consumer format support are separate. Preserve original bytes; use checked conversion at a real PDB-only boundary, not renamed CIF. Extract truly shared mechanisms without adopting `BC2Request` or antibody masks as universal types. No mandatory scaffold for unscaffolded modes, fabricated coordinates for sequences, or automatic seed-complex assembly from unrelated structures.

Required role coverage is explicit: RFantibody target plus structural framework; BoltzGen target/context plus scaffold or sequence template when the native mode consumes one; PPIFlow general-generation target, antibody/nanobody-generation antigen plus framework, and separately the exact selected complex in refinement; BC2 each target/state plus applicable scaffold/template or sequence input. Apply the acquisition mechanisms above to every structural role its native consumer supports, not just the first target field. A saved workflow configuration never substitutes for a missing structural-template selector.

### U03. Full target/template inspection

Each structural role has reachable full shared Mol* inspection, directly or through “Open full viewer”, with inline preview retained if useful. Reuse `StructureWorkbench`, `StandardStructureWorkbench` and `StructureViewerHost`; no second viewer runtime. Compact `hideControls` preview alone is insufficient.

Show source name/type, exact document/model/state and native/derived format. Target and template/seed remain independently inspectable. Synchronize numbered sequence, chain and residue tools with the exact document. Preserve case-sensitive author/label IDs and insertions; model adapters translate only to supported native representation. Do not imply insertion-specific native masks when a grammar groups insertion variants.

Multiple targets/states stay distinct where supported. Display focus does not change roles or selection. Inspect the consumed seed, not merely a context target. Scaffold inspection does not invent scaffold editing. Sequence-only inputs have a sequence view and no fictitious metrics. Viewer failure is a display error, not a new launch refusal.

### U04. Faithful handoff and persistence

Keep per-model/mode drafts in existing authoring/template persistence. Restore the destination's draft first; adopt compatible shared source information only into untouched fields. Preserve explicit false/zero/null/empty versus omission/native defaults. Keep incompatible settings in their original draft, not coerced into another engine.

Extend `BinderNativeRoute` with optional typed source/draft handoff using existing materialized source references and role selections. Each native editor maps supported fields before default initialization; this is not a new universal scientific API. Persist references, not live File/WebGL objects. Keep `pendingTemplateRoute`, source revision guards and deliberate-clear protection against late asynchronous responses.

Use existing UserTemplate and Project setup APIs. Saved configurations, structural templates, retries and native resume remain distinct. Verify real mounted parent routing, actual submitted native request and server save/read normalization; retained React state alone is not evidence.

## 4. Model-specific workflow and UI changes

### U05. BoltzGen

Sections: **Sources**, **Binder & protocol**, **Generation**, **Native selection**, **Expert**. Label the engine BoltzGen; format/objective/protocol is selected within it. Preserve full target/context and applicable structural/sequence-template experience.

New protein/peptide/antibody generation belongs to the existing `boltzgen` scientific owner, not generic requests disguised as antibody parent jobs. Retain legacy VHH and ligand/nucleotide semantics. Add exact model-mode/schema/compiler/runner/result registration together, never launcher-only modes. The current compiler rejects `boltzgen` and `ppiflow` model IDs before consulting its mode map; replace that blanket internal-engine restriction only for the implemented typed public paths, including normalization/discovery and related boundary tests. The PPIFlow model YAML is currently absent and must be added. Concrete route decisions and sole writers are in I1 of the implementation packet.

Factor target inclusion out of the nanobody-only preparation branch. Prepared input must contain the selected target plus distinct binder entities: protocol selection alone cannot supply the missing target. Peptide uses a native protein entity under its protocol, not an invented YAML entity. Antibody modes need the real scaffold/chain/design-mask mapping; a protocol name alone proves no particular antibody assembly format. Preserve source-to-native residue identity.

Reuse `prep_boltzgen.py`, scaffold preparation, `boltzgen_inputs.py`, wrapper/module/child workflow and ranking/compatibility owner. Preserve referenced source trees in staging. Existing optional BoltzGen preview stays optional; no new BC2-style digest requirement.

### U06. PPIFlow: two complete deliveries

Generation sections: **Targets & templates**, **Design**, **Generation**, **Expert**, with common review/placement. General binder mode invokes native initial binder generation on the target; antibody/nanobody generation invokes its separate native generator with antigen/framework inputs. Expose mode-correct settings and checkpoints, not unconditional VHH metadata or copied partial-flow controls.

Deliver the BMS generation route end to end: typed model/mode, browser/agent parity, source preparation, native Python adapter, Nextflow process/entrypoint, selected runtime closure/resources, producer identity, native result publication/viewing, Project and local/remote execution. Reuse the scientific engine, not a second generative implementation. Preserve historical `antibody_denovo/generator_backbone_refine` meaning and results rather than reinterpreting saved jobs.

Retain, reassess, upgrade and verify our refinement-loop use case separately: exact selected complex, movable/protected regions, independent repack/anchors, native partial flow, explicit sequence redesign if selected, sample accounting and descendant publication. Keep actual refinement role/checkpoint constraints local to refinement. Repair light-chain/VHH metadata mismatch, case-changing chain inputs and unsupported multiple-antigen presentation. No silent checkpoint/geometry/default substitution.

Generation and refinement have separate regression/native acceptance lines. Initial generation starts from target plus applicable template, not a candidate from another generator. Refinement starts from exact selected documents and proves requested region/operation behavior. Agnostic generation does not silently broaden refinement science; refinement limitations do not constrain native generation without source evidence.

### U07. RFantibody and BC2

RFantibody sections: **Target**, **Framework & regions**, **Generation**, **Optional operations**, **Expert**. Preserve target/framework discovery, full inspection, CDR/framework editor, masks, native settings, explicit optional operations and generator-only exit. Recompose existing controls/requests; do not generalize its checkpoint by relabeling.

BC2 retains **Sources**, **Binder design**, **Campaign**, **Objectives**, **Expert**, native preview/launch/readback and all modalities/actions/settings defined in the acceptance companion. Extend full-viewer/source/Project handoffs without rebuilding its delivered core. Generated native inventory and source-qualified typed overlay retain their distinct responsibilities.

Remove the misleading RFD3 binder-chooser entry, preserve separate RFD3 routes and do not activate retired BindCraft or historical `binder_design.yaml` to populate the lineup.

## 5. Selected rounds, native results and Project

### U08. Selected-operation workspace and interfaces

Sections: **Selected sources**, **Operations**, **Settings**, **Review & next round**. Replace generic registry rows and no-op picker callbacks with actual source-bound role/mask controls. Display exact selected Designs/documents/states and source Jobs, including cross-page selections. Inspecting one item does not change the selected set.

Retain `POST /api/binder-continuation/selected` and its current fields: `source_job_id`, `design_ids`, `operation`, `params?`, `frustrampnn_settings?`, `execution_target_id?`. Operations remain `refine`, `caliby`, `frustrampnn`, `fampnn`, `proteinmpnn`, `predict_boltz2`, `predict_protenix`. MD and diagnostics retain their separate model-owned routes.

Add optional `candidate_documents`, reusing the existing Design-keyed `CandidateDocument` shape (`artifact_id?`, `target_state?`) and producer-owned resolver. Omission keeps current primary-document behavior. Explicit selection resolves the owned artifact/state before snapshotting; no name/stem join. One explicit document per Design per invocation preserves current unique-ID semantics; other states can use separate child requests/fan-out. Record exact document, derivative map and per-item parent in existing manifests. Share the reusable resolver at its owner, not duplicate diagnostic logic.

Independent repack/anchors/flow/redesign retain separate switches and applicable settings. Off stages produce no work/assets or hidden mutation. Preserve full FrustraMPNN settings/results and scheduler placement; its current rejection of a generic execution-target override is a real integration constraint, not a reason to bypass its owner or quietly exclude remote scope. Caliby, FA-MPNN, ProteinMPNN and prediction remain model-owned. GROMACS uses the existing typed MD source handoff, not translated OpenMM options.

Keep `/api/blind-pose/selected` and `/api/ligandmpnn/interface-context/selected` independent, with exact input conditioning and raw native readers. Supplied geometry is not blind recovery; sequence-only cofolding alone does not prove pose recovery. Ordinary LigandMPNN chemistry-context design is a separate contract. No universal diagnostic request, composite affinity score or automatic verdict.

### U09. Results, identity and lifecycle

Reuse model-native readers/numerical authority and applicable global pagination, structure/sequence selection, comparisons, export, saved views/capture/annotations. Finish native row → exact candidate/document navigation; a generic Job link alone is incomplete. Persist selections through reopen using existing owners.

Keep campaign/attempt/sample/retained candidate/target-state document distinct. Preserve native ranking, intentionally absent outputs, zero yield and unknown associations without fake Designs or scientific joins. Missing associations remain evidence, not a new `eligible=false` policy. Changed bytes/sequences create descendants with fresh validation state; parent evidence stays on the parent.

Primary outputs survive optional failure through real finalization/manual return/retry. Native child results remain accessible with zero generic Designs or failed generic Design fetching. Parent/descendant comparison and next-round selection do not overwrite parents or require diagnostic success.

BC2 native resume/actions, fresh retry, saved-draft reopen and selected child round remain distinct. Prepared remote review reuses exact snapshots and allocated output, not a new selection/materialization call. Preserve sealed outputs, historical publication inventories and controller-owned reopen after worker loss.

### U10. Existing Project integration

Use existing Project setup/draft/preparation, capability/native-owner registry, launch contexts, run/attempt binding, datasets, attachment and result surfaces.

- Project setup opens the same native workspace, saves/reopens exact model/mode/settings/source selections, then prepares/launches through current contexts. Add `onDraftChange`/hydration to the actual binder branch. Project forms do not define a second scientific schema.
- Register precise supported binder model-mode/setup adapters. Preserve existing hierarchy/ownership/idempotency protections and truthful experimental labels. Complete missing adapters rather than add gates or weaken registry checks wholesale.
- `JobCreate.launch_context_id` is the native Job membership handoff; server resolves hierarchy. Carry optional destination context through selected/lifecycle endpoints and prepared remote review. Never put destination hierarchy in scientific params or depend on an ambient URL during delayed child submission.
- A single resulting Job uses its prepared context; fan-out gets distinct existing run-attempt contexts per Job server-side. Never reuse the parent's consumed context. Scientific ancestry remains separate from scheduler parenthood and Project membership.
- Use the explicit current Project destination when present; standalone remains standalone unless selected otherwise. Importing sources from another Project never moves them or changes the destination silently.
- Add Project source browsing by resolving current resource/dataset members to existing source identities and revisions.
- Add native publication-backed adapters for BC2 campaign and zero-Design diagnostic attachment/reopen. Preserve valid zero yield without fake Designs or weakened Design-set checks. Reuse existing dedicated MD/FrustraMPNN adapters.
- From Project detail/tasks/runs reopen exact native settings/results/candidates, return to Project and launch compatible selected follow-ups. Every actual fan-out Job is associated correctly; internal Nextflow processes do not each become artificial Project steps.

## 6. Holistic code, processing, hardening and efficiency

W01–W08 are mandatory implementation work, not an optional audit appendix. Review the complete reachable generation/refinement path and fix demonstrated problems. This is not permission for a repository-wide rewrite, scientific changes or a permanent validation/logging product.

### W01. Reachability and simplification

Trace UI/agent request → API normalization/Project context → compilation → Nextflow module → Python/script/native invocation → producer records → publication/return → selection/reopen. Include imports, dynamic script dispatch, dependency/runtime manifests and supported historical readers. Record keep/fix/consolidate/retire dispositions with actual consumers. Inventory alone is not substantive review.

Remove replaced code/imports/tests only after reference checks across current runtime, packaging, supported workflows and recovery/history needs. Retain bounded historical reads where necessary, not duplicate active writes. Lack of a run during review does not make a supported operation obsolete.

### W02. Python, scripts and request boundaries

Review single default authority, explicit-value semantics, nested collection transport, role/source mapping, exact mode dispatch and native outputs. Fix hidden overrides, silent fallback, swallowed errors and false/zero/null loss. Keep native scientific semantics/defaults unless explicitly approved otherwise.

Review argument-safe process calls, shell interpolation, paths/containment, file ownership, bounded parsing, temporary files, atomic output, cancellation and resource cleanup at real boundaries. Reuse current helpers; no duplicate validation framework. Errors identify the failed stage/input instead of empty success or fabricated sequence/results.

### W03. Nextflow graph correctness and overhead

Review schema/`allOf`, optional parameter access, module I/O, staged basenames, channel tuple arity, fan-out/join keys, empty/zero-yield channels, producer attribution, pause/review and resume. Exact source/sample keys travel through channels; no sorted-glob lineage. Off stages are absent from the actual graph and selected dependencies.

Separate PPIFlow native generation from partial-flow contracts. Use narrow native invocation/preparation/publication adapters, not new generation forced through maturation inputs. Reconcile `NativeInvocation`/`SelectedExecutionPlan` descriptors with final processes, not asset totals alone.

Reduce duplicated processes, staging/conversion and serial bottlenecks when demonstrated and behaviorally verified. Prevent staged-name collisions even for identical original/repacked bytes. Keep scheduler resources and bounded native fan-out; do not accelerate by oversubscription, changing sample semantics or silently changing science.

### W04. Data processing and publication

Review producer manifests/sidecars, requested/emitted/missing sample counts, document/state maps, sequence extraction, replay identity and native/derived formats. Report zero yield and unresolved identity honestly. Review transactions, async/session boundaries, primary/optional isolation, manual return/retry and historical reopen.

Avoid unbounded materialization, N+1 result queries and repeated directory scans where an authoritative manifest exists. Use bounded/paginated processing and existing result authorities. No fake candidates, rank-as-ID, filename joins or cached summaries becoming another numerical database.

### W05. Source/UI efficiency

Review download/materialization reuse, parse work, large results, async cancellation/races, lazy model/viewer loading and section navigation. Do not repeatedly fetch/parse identical bytes just to switch panes, or copy them for every draft. Reuse bounded caches/in-flight requests keyed by actual identity. Avoid eager initialization of every model, global polling and duplicate Mol* instances. Preserve correctness under latency/error.

Test real source selectors and state adapters; mock transport/WebGL internals when necessary, not whole source tools to null. Interaction hierarchy is separate from control counts or no-overflow measurements.

### W06. Runtime/bridge efficiency and lifecycle

Review selected images/weights/support assets, portable trees, archive/extraction/hashing, image views, cache identity, native initialization/compilation and result return. No per-job installs, unchanged downloads, unselected preparation or GPU import during discovery. Keep attempt-private writes and compatible persistent caches. Preserve volatile resource, ownership and corruption checks; do not cascade redundant checks over immutable unchanged bytes.

Reconcile PPIFlow's installed source/checkpoint/config identity with runtime manifests and image recipe; an unpinned clone does not become reproducible through source readback. Use existing provisioning, not workflow-specific installers or all-model bundles. Preserve native resume state and worker-offline native/Project reopening.

### W07. Measured speed and resource use

Measure changed owners before/after with equivalent scientific requests, outputs, hardware/runtime and relevant data sizes. Separate source/viewer readiness, API preparation, parsing/materialization, Nextflow dispatch, publication/querying, cold provisioning, selected missing assets, warm launch, native initialization/compilation, continuation and return. Record phase durations and bytes/files/memory/query/process counts where they explain cost.

Use existing logs/receipts and bounded development probes, not a telemetry product. Distinguish native compute from orchestration and cold from warm. A speed claim cannot come from fewer samples, omitted selected stages, different science or fixture timing. Remove demonstrated redundant work and prove semantic equivalence. Numeric commitments follow measured baseline and Christian's expectations, not invented universal seconds or permissive after-the-fact budgets. Missing measurements mean unverified performance, not a runtime refusal.

### W08. Hardening evidence

Each correction receives the owner regression and affected consumer tests. Use real pinned Nextflow compilation plus explicitly non-science transport fixtures for graph behavior; native execution is separately authorized evidence. Cover relevant malformed/corrupt inputs, explicit empties, multiple samples/states, zero yield, duplicate basenames, partial output, optional failure, interrupted return, replay and worker-offline reopen.

A review report without required fixes does not close the work. Finding no defect does not justify rewriting correct code. Report retained-correct areas and evidence, not artificial changes. Preserve execution behavior except specified corrections; never harden by adding proof-based interlocks.

## 7. Retained H concern disposition

- **H01:** BC2 launch implemented; finish source/Project/native acceptance. E/A/parent.
- **H02:** four-generator agnostic authoring and actual wider BoltzGen/PPIFlow adapters remain. A/B/parent.
- **H03:** no generic AntiFold/ThermoMPNN defaults; retain supported historical semantics. Further retirement needs a decision. A/B/D.
- **H04:** GROMACS typed handoff exists; finish Project/round integration and acceptance. Parent/C/D.
- **H05:** designed BC2 typed controls exist; retain full native/source/Project parity. E/A.
- **H06:** neutral continuation exists; add exact document/destination handoff and real repeated-round evidence. C/A/parent.
- **H07:** independent refinement/repairs exist; finish hydration, roles/accounting and W01–W08. B/C/A.
- **H08:** Caliby/full FrustraMPNN adapters exist; finish usable controls, owner-correct placement and native evidence. D/G.
- **H09:** separate raw diagnostics exist; finish input/execution evidence and Project/round UX. No classifier. H/C/parent.
- **H10:** native/shared results improved; finish exact navigation, workbench and Project reopen. C/F.
- **H11:** shared transport exists; complete new generation/selected closure and real remote runs. G/B/E/H.
- **H12:** warm/lifecycle mechanisms exist; measure and fix actual repeated work/recovery defects. G/parent.
- **H13:** BC2 nested root mismatch repaired; preserve nested/historical contract and prove native return. F/E/G.
- **H14:** BC2 actions/resume/state inventory exist; finish native action/runtime evidence, not another lifecycle owner. E/F/G.

## 8. Source evidence index

Paths are relative to the verified source. These identify implementation/test-contract evidence, not fresh passing tests.

- **E01 Authoring/source:** `platform/frontend/src/components/AntibodyDenovoTemplate.tsx`, `BinderGeneratorChooser.tsx`, `JobSubmission.tsx`, `TargetAntigenSelector.tsx`, `BindCraft2StructureInputs.tsx`, `EpitopeMolstarViewerImpl.tsx`; `platform/frontend/src/lib/bindcraft2StructureInputs.ts`.
- **E02 BoltzGen:** `scripts/prep_boltzgen.py`, `scripts/lib/boltzgen_inputs.py`, `scripts/lib/boltzgen_native_source.json`; `platform/api/routers/boltzgen.py`; `platform/api/services/boltzgen_request_compatibility.py`; `platform/api/config/models/boltzgen.yaml`; `modules/boltzgen.nf`.
- **E03 PPIFlow:** `workflows/ppiflow_generator_design.nf`, `modules/ppiflow.nf`, `scripts/validate_ppiflow_roles.py`, `scripts/ppiflow_sample_identity.py`, `scripts/prepare_ppiflow_maturation.py`, `apptainer/ppiflow.def`; installed native entrypoints in section 2.
- **E04 BC2:** `platform/frontend/src/components/BindCraft2Campaign.tsx`, `BindCraft2Settings.tsx`; `platform/frontend/src/lib/bindcraft2Lifecycle.ts`; `platform/api/services/bindcraft2_launch.py`, `bindcraft2_runtime.py`, `bindcraft2_native.py`; `platform/api/config/models/bindcraft2.yaml`; `apptainer/bindcraft2.def`.
- **E05 Selection:** `platform/api/routers/binder_continuation.py`; `platform/api/services/binder_continuation.py`, `binder_diagnostic_selection.py`; `platform/frontend/src/lib/binderContinuation.ts`; `platform/frontend/src/components/BinderSelectedControls.tsx`.
- **E06 Refinement/analysis:** `workflows/binder_refinement.nf`, `workflows/caliby_binder.nf`; `platform/api/config/models/binder_refinement.yaml`, `caliby_binder.yaml`; `platform/api/services/frustrampnn/jobs.py`; `platform/api/routers/molecular_dynamics.py`; `platform/api/services/md/starting_structures.py`; `scripts/publish_binder_refinement.py`.
- **E07 Results:** `platform/api/services/result_ingester.py`, `stage_review.py`, `result_state_integrity.py`, `bindcraft2_publication.py`, `bindcraft2_result_readback.py`; `platform/frontend/src/components/ResultsViewer.tsx`, `BindCraft2NativeResults.tsx`, `BindCraft2JobResults.tsx`.
- **E08 Diagnostics:** `platform/api/routers/binder_blind_pose.py`, `ligandmpnn_interface_context.py`; `platform/frontend/src/components/BlindPoseSelectedControls.tsx`, `BinderDiagnosticRawResults.tsx`.
- **E09 Project:** `platform/api/schemas.py:JobCreate`; `platform/api/services/protein_project_capabilities.py`, `workflow_adapter_registry.py`; `platform/api/services/global_experiments/workflow_setups.py`, `launch_contexts.py`, `project_datasets.py`, `adapters.py`, `result_surfaces.py`; `platform/api/routers/project_manager.py`; `platform/frontend/src/components/project-manager/ProjectWorkflowSetup.tsx`, `ProteinProjectWorkspace.tsx`.
- **E10 Compiler/bridge:** `platform/api/services/nextflow.py`, `platform/api/native_components.py`, `platform/api/services/remote_execution/bundle.py`, `cache.py`; `scripts/lib/portable_inputs.py`; `platform/api/tools/bms_remote_worker.py`; `nextflow_schema.json`.

Supporting reviews are outside the repository under `/home/dalab/audits/binder-workflow-ui-spec/`: `review-baseline.json`, `authoring-review.md`, `continuation-review.md`, `bc2-remote-review.md`, `source-selection-review.md`, `project-integration-review.md`, `ppiflow-generation-native-review.md`. The PPIFlow native correction and four-generator clarification supersede earlier seeded-only/five-generator recommendations. Reports are evidence, not scope authority.

## 9. Reconciled acceptance ledger

No native acceptance is certified by this document. “Implemented” credits connected source and existing test contracts; remaining software and execution evidence stay distinct. Owner letters refer to the implementation packet; parent owns shared/Project integration. Overlapping identifiers are not independent completed-feature counts.

### Product acceptance

- **A01 — Partial.** BC2 workspace exists; wider BoltzGen/PPIFlow, four-generator sources and Project entry remain. E01–E04/E09. Owner A/B/parent. Close actual mounted/model-native requests and correct product placement.
- **A02 — Foundation implemented; closure unverified.** BC2 typed controls/actions/compiler exist. E04. Owner E. Close native capability/control accounting and representative execution families.
- **A03 — Partial.** BC2 digest/generic template repairs exist; source handoff, ESMFold2 hydration, new generator and Project drafts remain. E01/E02/E04/E09. Owner A/parent. Close browser/agent and save/reopen/clone/retry fidelity.
- **A04 — Publisher implemented; native evidence open.** BC2 accounting/joins/nested roots exist. E04/E07. Owner F. Close actual draw/state/adaptive/sweep/zero-yield/action output cases.
- **A05 — Partial.** Native readers exist; exact navigation, applicable review facilities and native zero-Design Project adapters remain. E07/E09. Owner C/F. Close direct/Project/worker-offline reopen and export/comparison.
- **A06 — Partial.** Same-root immutable selection exists; alternate-document refinement and destination contexts remain. E05/E07/E09. Owner C/parent. Close exact state/parent identity and cross-project source import.
- **A07 — Core implemented; whole-path evidence open.** Neutral repeated requests exist; designed UI and real generator/descendant handoffs remain. E05/E06. Owner B/C. Include real BC2 candidate and another selected round, with generator-only exit.
- **A08 — Partial.** Independent operations/freshness repairs exist; native roles/sample/composition and accounting/UX remain. E03/E06/E07. Owner B. Verify PPIFlow generation and refinement separately.
- **A09 — Adapters implemented; qualification open.** Full analyses and raw diagnostics exist. E06/E08. Owner D/H. Close native correctness and honest conditioning evidence, not classification machinery.
- **A10 — Repair implemented; live coverage open.** Primary isolation includes manual return. E07. Owner C/G. Close optional failure/interruption/retry/replay without erased or duplicate results.
- **A11 — Partial.** Credit C01–C14; residuals and W01–W08 remain mandatory. E01–E10. Owner parent. Close corrections on the combined path, not leaf totals.
- **A12 — Partly wired; native parity open.** Existing bridge supports current contracts; new generation/Project/selected closure remains. E05/E06/E09/E10. Owner G. Verify actual local/remote paths at each operation owner.
- **A13 — Mechanisms present; unmeasured.** Cache reuse and lifecycle exist. E04/E10. Owner G/E. Close matched cold/warm and approved recovery evidence.
- **A14 — Delivery evidence open.** Source baseline verified; this pass neither tests nor deploys software. E01–E10. Owner parent. Close combined tests and authorized native/deployed/worker readback.

### Corrections

- **C01 — Core repaired; authoring partial.** Explicit settings/unknown-choice handling improved. E01/E06/E10. Parent/A: finish save/profile/native fidelity without new defaults authority.
- **C02 — Prior repair retained.** Synthetic sequence fallback removed. E06/E07. B: verify supported residue/format/native use; no additional parser rewrite justified here.
- **C03 — Partial.** Selected role checks exist; generator VHH/light metadata, chain case and mode-specific controls remain. E01/E03. B/A: exact generation/refinement role mapping.
- **C04 — Concrete residual.** Producer pairing fixed; saved-template branch omits ESMFold2 restoration. E01/E07. A/B/C: repair hydration and prove native sample pairing/cardinality.
- **C05 — Independent execution delivered.** Four-toggle neutral workflow/off defaults exist. E06. B/A: designed controls and native invariance/composition evidence.
- **C06 — Freshness/terminal repair delivered.** IgGM descendants/terminal attribution fixed; automatic post-IgGM revalidation remains unwired. E06/E07. B/C: retain explicit separate validation, no new gate; verify native ancillary reopen.
- **C07 — Producer identity fixed; accounting partial.** Sample hooks replace glob ordinals. E03. B/C: duplicate/requested/emitted/missing counts and zero-outcome review without treating valid zero yield as failure.
- **C08 — Production repair delivered.** Primary isolation includes manual return/retry. E07. C/D/G: actual optional failure/return evidence; no new rollback defect demonstrated here.
- **C09 — Owned snapshots delivered; document seam open.** Native/derived identity and same-root checks exist. E05. C/parent: exact alternate-document selection and returned-candidate handoff.
- **C10 — Shared repairs delivered.** Full document paths and explicit parent/origin replace stem/global-name inference. E07. C: native repeated rounds, historical reopen and exact UI links.
- **C11 — Format repair delivered; coverage open.** Native discovery and checked representable CIF conversion exist. E05/E07. C/B: source/derivative display and real consumer mapping evidence.
- **C12 — Partial.** BC2/selected paths share model definitions; remaining authoring/new-generation adapters need consolidation. E01–E06. A/parent: remove replaced active writes, retain bounded historical reads.
- **C13 — Reuse retained; performance open.** Archive/helper reuse and selected transport exist. E10. G: measure/fix W06/W07 costs, not another cache by assumption.
- **C14 — Substantial continuity delivered; full closure open.** Neutral operations/MD/analyses/diagnostics exist. E01/E05–E09. Parent: retained branches, four-generator/Project/round/reopen acceptance.

### Phases and cross-phase delivery

- **P1.1 — Partial:** agnostic four-generator UI, full sources/viewer/Project and real wider BoltzGen/PPIFlow. E01–E03/E09. A/B/parent; close A01/A03.
- **P1.2 — Core delivered; UX/native work open:** independent repeated refinement, including preserved PPIFlow use case. E03/E05/E06. B; close A07/A08.
- **P1.3 — Partial:** immutable selection/publication exist; exact document/Project association/reopen remain. E05/E07/E09. C; close A05/A06/A10.
- **P1.4 — Adapters delivered; native qualification open:** full FrustraMPNN/Caliby, no pairwise requirement. E06. D; close A09.
- **P1.5 — Bridge foundation delivered; extensions/evidence open:** selected/new-generation closure and measured warm behavior. E10. G; close A12/A13.
- **P1.6 — Partial:** C01–C14 plus W01–W08, real generic requests and saved/Project fidelity. E01–E10. Parent; close A11/A14.
- **P2.1 — Foundation delivered; coverage open:** full BC2 field/action/modality/source parity. E04. E; close A02/A03.
- **P2.2 — Routes/actions delivered; native evidence open:** bounded campaign, sweeps/adaptive settings/resume. E04. E; close A02/A04/A13.
- **P2.3 — Publisher delivered; native evidence open:** exact joins/states/nested/state inventory. E07. F; close A04/A05.
- **P2.4 — Partial:** BC2 peer launch exists; real candidate continuation, Project/full-view and native local/remote handoff remain. E01/E04–E07/E09/E10. Parent; close A01/A06/A07/A12.
- **P3.1 — Adapter exists; native execution open:** selected interface-context operation, not redesign. E08. H; close A09.
- **P3.2 — Inputs recorded; interpretation unqualified:** actual conditioning/applicability evidence. E08. H/B; close A09 without classifier/threshold work.
- **P3.3 — Raw persistence exists; Project/round evidence open:** exact diagnostic identity/reopen, no policy CRUD. E05/E08/E09. C/H; close A05/A06/A09.
- **P3.4 — Partial:** usable typed controls, placement parity and off-stage exclusion. E01/E08/E10. A/G; close A03/A09/A12.
- **X.1 — Reuse established; consolidation remains:** one scientific/result/lifecycle authority, including Project and W01–W06. E01–E10. Parent; close A03/A05/A11.
- **X.2 — Not executed here:** combined focused tests with exact skips/baseline failures. E01–E10. Parent; close A14.
- **X.3 — Not exercised here:** authorized actual native/local/remote and returned readback, including both PPIFlow roles. E03–E10. Parent/G; close A07/A09/A12.
- **X.4 — Unmeasured:** matched cold/warm/processing evidence, no invented universal threshold. E10. G/parent; close A13.
- **X.5 — Mechanisms present; recovery evidence open:** stop/interruption/return/replay/continuation at changed boundaries. E04–E10. C/G/E; close A10/A13.
- **X.6 — Source verified; deployed/worker closure pending:** separate consumed identities and worker-offline native/Project reopen when authorized. E07/E09/E10. Parent/G; close A05/A12/A14.

## 10. Completion and delivery

Close every original identifier and clarified source/Project/PPIFlow/W01–W08 obligation with actual evidence or an explicit Christian-approved scope change. Keep source implementation, fixtures, native runs, remote runs and scientific qualification distinguishable. Do not invent a completion percentage.

Native acceptance uses bounded approved benign inputs and actual model-owned requests. Include both PPIFlow roles, wider BoltzGen, RFantibody and complete BC2 feature-family accounting. A real native BC2 structure-bearing candidate must enter a compatible selected operation and a further descendant round. Zero yield is valid accounting but not candidate-handoff evidence; no fixture candidate or loosened filter substitutes.

Existing visual renders are design references only. Their PPIFlow seeded-only layout is superseded by U06; they did not depict complete Project/source/full-view behavior. Capture the implemented sections/roles after asynchronous loading, desktop/narrow and light/dark, with actual controls and emitted requests. Mockup approval is not approval for hidden science changes.

Implementation closeout provides changes/deletions, the requirement crosswalk, exact tests/skips, native Job/artifact references when authorized, processing/local/remote/lifecycle measurements, separately verified deployment/worker identities and remaining scientific limits. Keep generated outputs/audit data outside source. Execute the bounded owners in the implementation packet; do not reopen the fixed product direction.
