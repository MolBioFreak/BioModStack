# Statement of Work: Full Protein In Silico Integration with Global Project and Experiment Management

**Status:** Implementation-ready scope specification

**Date:** 2026-08-12

**Source baseline:** `test` and `origin/test` at commit `493356b7f063a7d0a6366ccfe88722cca3240104`, tree `0f73fccdda5e6501b75edd43fcba856401502b49`

**Controlling parent specification:** `docs/specs/global-bms-project-experiment-manager.md`

**Companion SOW:** `docs/specs/2026-08-12-ngs-molbio-global-project-integration-sow.md`

**Implementation authority:** This document specifies work. Source edits, test execution, computational runs, deployment, and Production promotion require separate authorization.

## 1. Outcome

Deliver the complete Protein In Silico half of the global research layer:

```text
Project
→ Global Experiment
→ Protein In Silico Domain Experiment
→ immutable targets and Dataset revisions
→ typed folding, prediction, design, simulation, and analysis Workflow Plans
→ preparations, run groups, scientific replicas, attempts, and canonical Jobs/runs
→ structures, ensembles, trajectories, comparisons, evidence, and saved reviews
→ notes, observations, decisions, and conclusions
```

The operator can use the Project as an ELN-like research record while every launcher, scheduler, scientific store, artifact service, and viewer keeps its existing authority.

The required scientific loop is:

```text
bind exact sequence / structure / complex authority
→ state the objective and constraints
→ save a typed immutable Workflow Plan
→ prepare without launching
→ launch through the canonical typed surface
→ reconcile runtime and terminal scientific authority
→ review structures, ensembles, trajectories, metrics, and provenance
→ compare only compatible results
→ record evidence-linked observations and decisions
→ derive the next design or analysis from exact prior authority
→ restart and reopen every selected revision
```

## 2. Scope boundary

### 2.1 In scope

- Protein In Silico Domain Experiment creation, revision, archive, restore, and exact reopening.
- Server-owned protein capability inventory and typed Project intent.
- Immutable target, input, candidate, result, review, and comparison Datasets.
- Workflow planning and launch-context integration for folding, structure prediction, validation, de novo design, redesign, conformational analysis, molecular dynamics, FrustraMPNN analysis, and compatible comparisons.
- Complete global adapter coverage for every accepted and exposed protein capability.
- Canonical result and viewer reopening with exact Project return context.
- Cross-run lineage, statistical/review grouping, and iterative design lineage.
- ELN-like notes, observations, decisions, conclusions, and evidence links.
- Focused automated evidence, exact-tree acceptance, Development deployment, and browser proof.

### 2.2 Scientific product policy

- RFD3 is the chief de novo engine for general and non-nanobody protein design.
- RFD3 local redesign is a child capability under De Novo Design. It is not a top-level experimental product family.
- Specialized antibody and nanobody workflows retain their typed scientific semantics. They do not change the chief general/non-nanobody engine policy.
- Foundry is the sole LigandMPNN implementation. No parallel upstream LigandMPNN runner is introduced.
- Current structure prediction and validation targets are Boltz-2, ESMFold2, and Protenix V2.
- AlphaFold2 and AlphaFold3 are outside the new launch and validation denominator.
- RF3 is not an approved validator in this SOW. Historical RF3 records may remain attachable through exact receipts.
- GROMACS remains the molecular-dynamics authority.
- Conformational Mapping does not use SAbDab as an orchestration authority.
- FrustraMPNN is a global protein-structure analysis authority. It publishes evidence and guidance. It does not generate structures or establish experimental conclusions.
- Workflow GPU selection remains a typed UI/API scheduling control. The scheduler validates the selected device set and records the actual GPU UUID/index in runtime evidence.
- Implement one shared managed-workflow admission policy that caps aggregate active CPU allocations at 24 threads and aggregate active workflow DRAM allocations at 96 GiB. These are required product limits. The pinned source does not prove one complete aggregate enforcement authority.

### 2.3 Authority boundaries

| Authority | Owner |
|---|---|
| Project, Global Experiment, Protein In Silico Domain Experiment | Global Project/Experiment services |
| Scientific target bytes and revisions | Existing sequence, structure, input, design, or artifact authority |
| Saved Protein In Silico intent | Global immutable Domain Experiment revision |
| Exact model settings and execution request | Immutable Workflow Plan revision and canonical typed launcher/compiler |
| Core scheduling and generic Job lifecycle | Existing Job API and scheduler |
| RFD3 request, candidates, native artifacts, and result manifest | Existing RFD3 services and result contract |
| Structure prediction and design results | Existing Jobs, Design rows, producer manifests, and result contracts |
| Conformational Mapping | Existing CM request, ensemble, analysis, and result services |
| Molecular dynamics | Existing `MdRun`, replica/segment state, GROMACS artifacts, and MD result services |
| FrustraMPNN | Existing configuration, invocation, landscape, comparison, guidance, and workbench services |
| Global attachment, Dataset membership, lineage, projection, and ELN records | Global Project/Experiment services |
| Scientific rendering | Existing Results Viewer, Structure Workbench, CM Viewer, MD Results Pane, and FrustraMPNN Workbench |

Project Manager never copies complete structures, trajectories, landscapes, PAE matrices, raw model outputs, or domain result payloads into `experiments.db`.

### 2.4 Out of scope

- A replacement scheduler, model registry, result registry, scientific viewer, or artifact store.
- User-authored adapters, commands, scripts, executables, or server paths in Workflow Plans.
- Automatic Project membership inferred from job names, directories, timestamps, or sequence similarity.
- A universal cross-model score or automatic scientific consensus.
- RFDpoly or nucleotide/oligo design. RFDpoly remains in the NGS/MolBio vertical and enters protein work only through an explicit immutable cross-domain receipt when a later approved workflow needs it.
- Wet-lab conclusions from computational evidence.
- New AF2/AF3 launch or validation support.
- NGS/MolBio work specified in the companion SOW.
- Production promotion.

### 2.5 Shared global dependency and ownership

The companion NGS/MolBio SOW is the single contract and implementation owner for the shared global closeout: direct attempt-to-preparation and prepared launch-context v2 authority; canonical nested Workflow Plan, preparation, launch, run-group, retry, resubmit, cancellation, result-surface, and comparison wrappers; outer `bms.domain-experiment.v2`; canonical Project Dataset APIs/UI; global validation-artifact and bounded-log writers; authority-context query isolation; the shared 24-thread CPU and 96-GiB DRAM aggregate admission ledger; common pagination/response limits; and operational-health, backup, and export fields.

This protein tranche reuses those exact migrations, schemas, routes, services, components, and acceptance artifacts. It cannot create a parallel workflow route implementation, launch-context authority, Dataset service, artifact/log writer, query-isolation mechanism, resource-admission ledger, pagination policy, or health model. Protein implementation adds protein-specific payload schemas, capability adapters, read projections, viewers, and payload-ownership rows. If scheduling starts with protein work, the shared package remains a separately accepted prerequisite owned by the companion SOW.

The shared `bms.payload-ownership-manifest.v1` is extended with these protein classes:

| Payload class | Active authority |
|---|---|
| Sequences, complexes, target snapshots, structures, ensembles, trajectories, landscapes, model-native metrics, and result manifests | Producer-native protein/CM/MD/Frustra store and governed artifact roots |
| Project membership, receipt identity, digests, bounded labels/metadata, lineage, and ELN text | Global `experiments.db` |

The companion scanner and `bms.payload-ownership-audit.v1.json` rules apply unchanged. Protein payload bytes cannot be copied into `experiments.db`; transient scheduler staging and non-serving backup/export copies use the explicit classifications in the companion SOW.

## 3. Starting-state ledger

### 3.1 Inherited global capabilities

These are reusable prerequisites. They do not count as completion of the protein vertical.

| Capability | Current state |
|---|---|
| Project → Global Experiment → Domain Experiment hierarchy | Implemented with immutable revisions and generation-checked lifecycle operations |
| Project tree, relationship map, inspector, bounded collections | Implemented |
| Verified external receipts and typed attachment | Implemented |
| Launch-context issue, redemption, binding, and return route | Implemented for canonical Job submission |
| Managed global dispatcher and reconciliation loop | Implemented with one process-owned file lock |
| Run groups, attempts, retry, and resubmit | Implemented; direct attempt-to-preparation authority needs additive closure |
| Immutable Dataset revision members | Implemented in persistence and read models; operator and protein comparison flows remain partial |
| ELN-lite append-only records | Implemented |
| Manual dispatch | Correctly disabled with HTTP `409` |
| Worker health | Partial; adapter readiness, reconciliation lag, receipt failures, and verified backup/export times remain incomplete |

### 3.2 Existing protein implementation

- A typed `bms.protein-in-silico-experiment.v1` Domain payload with experiment mode, targets, constraints, planned capabilities, comparison groups, and validation strategy.
- Typed core Workflow Plan adapters for several core Job models.
- A generic core protein result adapter and per-model typed core Job result adapters for a subset of models.
- Specialized global reference adapters for RFD3 local redesign, CM Protenix, CM ConforNets, MD, FrustraMPNN results, FrustraMPNN comparisons, and FrustraMPNN guidance.
- Result-surface builders for core designs, typed Jobs, RFD3, CM, MD, and FrustraMPNN.
- Canonical result contracts for de novo generation, sequence design, structure prediction, binder design, RFD3 local redesign, CM, and FrustraMPNN.
- Canonical viewers for ordinary structures/designs, RFD3 local redesign, CM, MD, and FrustraMPNN.
- Project launch-context handling in Job Submission and a validated Project return banner.
- Current Project Manager frontend fixtures for a protein-oriented DRT4 Project with runs and attempt provenance.
- Existing registries and launch surfaces for Boltz-2, ESMFold2, Protenix V2, Fold-CP Experimental, molecular dynamics, RFD3 local redesign, De Novo Design, Protein CAD Experimental, Protein Hunter Experimental, CM, FrustraMPNN, sequence designers, and specialized antibody/nanobody workflows.
- The current Protenix managed profile explicitly selects `${params.container_dir}/protenix.sif` and composes its immutable weight, MSA database/cache, CM runtime, script, and source binds through `task.ext.containerOptions` so the workstation GPU label cannot discard them. This closes the profile-composition image/bind defect only. Full Project planning, typed settings parity, result receipt, viewer, and owner-path qualification remain required by this SOW.
- A governed CM→FrustraMPNN compatibility importer exists at `platform/api/services/frustrampnn/cm_legacy_import.py`, with the bounded operator CLI `scripts/import_legacy_cm_frustrampnn.py`. It accepts a caller-selected persisted CM `job_id`, then requires a completed five-candidate CM request, immutable snapshot, exact coordinate plan, native candidate/artifact authority, and exactly 54,000 persisted landscape rows before publishing digest-bound records through the canonical persisted FrustraMPNN result authority with journaled database/filesystem recovery. These checks constrain result shape and provenance. They do not bind the selected job, request, target, or snapshot to DRT4 identity. The importer is a historical recovery seam, not a general launcher, upload path, fresh-runtime receipt, or DRT4 authority.

### 3.3 Blocking gaps

1. Protein planned capabilities and validation strategy are entered as comma-separated free text. They are not selected from one server-owned capability registry.
2. Target IDs can be operator-authored strings without a required verified source receipt.
3. Workflow-adapter, result-adapter, model-registry, result-contract, launcher, and viewer inventories do not share one closure denominator.
4. The typed core Workflow Plan model set differs from the typed result-adapter set and the broader model registry.
5. General RFD3 de novo work lacks the same specialized global closure as RFD3 local redesign.
6. RFD3 local redesign remains exposed as a separate top-level model instead of a De Novo Design child in global taxonomy.
7. Ordinary structure predictions can fall back to generic Job/result references without proving exact result-contract and artifact authority.
8. MD uses a specialized result adapter but still needs a specialized preparation/materialization contract for the global control plane.
9. FrustraMPNN reference adapters exist, while launch, child-analysis, comparison, guidance, and iterative reanalysis need one Project-owned workflow graph.
10. CM has specialized global adapters, but complete Project-owned multi-node and comparison semantics remain unproven.
11. No Protein In Silico domain workspace provides targets, plans, Dataset revisions, capability-specific status, comparisons, and ELN context as one operator surface.
12. Global Dataset creation and exact revision reopening are not complete operator flows.
13. Run attempts do not persist direct immutable preparation authority.
14. Global artifact and log tables lack complete production writers/projections.
15. React Query placeholder data can remain visible across an authority-context change.
16. No current Development browser path proves folding, de novo design, CM/MD, FrustraMPNN, Dataset, comparison, ELN, restart, and exact reopening as one vertical.
17. AlphaFold2 remains enabled in the current model registry even though AF2/AF3 are outside the approved prediction/validation policy for this vertical.
18. LigandMPNN is enabled in the model registry, but no executable entrypoint, module invocation, typed Project adapter, dedicated result authority, or viewer closure is present.
19. Shape Blueprint applies a server-default validator suite that includes Boltz-2, ESMFold2, and Protenix V2. The current operator surface does not expose this result-affecting selection.
20. DISCO and La-Proteina are executable experimental generators, while their terminal authority remains weaker and more generic than RFD3, Shape Blueprint, CM, MD, and FrustraMPNN.
21. The typed core result adapter binds Job parameters, stage outputs, and provenance, but it does not independently verify every producer-native artifact byte required by each scientific result contract.
22. Project Manager can display plans and Datasets but does not currently author protein Workflow Plan revisions, Dataset revisions, preparations, or run groups.
23. The user-visible Mutagenesis Library and canonical `mutagenesis/batch_predict` route are absent from the supposed complete protein capability denominator.
24. The CM→FrustraMPNN compatibility importer contains DRT4-labelled cardinality errors but accepts any caller-selected persisted CM job that satisfies its five-candidate and 54,000-row checks. No immutable DRT4 accession, `WP_031606642.1` target receipt, approved job/request identity, or source-snapshot digest gate exists.

## 4. Canonical protein capability inventory

### 4.1 Capability IDs

Add a server-owned capability inventory. User-authored IDs are invalid.

Required capability families are:

```text
protein.target_binding
protein.structure_prediction
protein.complex_prediction
protein.structure_validation
protein.de_novo_design
protein.binder_design
protein.antibody_nanobody_design
protein.local_redesign
protein.mutation_variant_exploration
protein.shape_conditioned_design
protein.sequence_design
protein.conformational_mapping
protein.molecular_dynamics
protein.frustration_analysis
protein.structure_comparison
protein.cross_run_statistics
protein.viewer_review
```

Each record contains:

- canonical capability ID and contract version;
- operator label and scientific role;
- allowed Domain Experiment modes;
- approved engine/model/mode mappings;
- preparation adapter ID;
- result contract IDs and result adapter IDs;
- canonical launcher and viewer descriptors;
- required source roles;
- supported typed settings schema;
- exposure state: `accepted`, `experimental`, `internal`, `historical`, or `disabled`;
- runtime-readiness reference and registry digest.

The capability inventory is a validated server projection over the current ModelRegistry, Workflow adapter registry, result-contract definitions, materializers, result adapters, launcher descriptors, and viewer descriptors. A small explicit mapping may join those authorities. It does not duplicate model definitions or create a plugin framework.

An enabled registry row has no launch authority by itself. In particular, LigandMPNN remains unavailable until the Foundry-owned execution path, typed input authority, materializer, terminal result contract, adapter, and viewer all pass the same closure check.

### 4.2 Required accepted denominator

| Family | Required accepted engines or authorities | Requirement |
|---|---|---|
| Structure prediction and validation | Boltz-2, ESMFold2, Protenix V2 | Typed plan, settings, launch, artifacts, result receipt, viewer, and provenance |
| General/non-nanobody de novo | RFD3 | Chief engine under De Novo Design |
| Local redesign | RFD3 native local-redesign contract | Child mode under De Novo Design |
| Mutation and variant exploration | Governed mutagenesis library/manual authority plus accepted structure predictor | Exact parent sequence/structure, variant set, predictor settings, per-variant result identity, comparison, and viewer |
| Sequence design | Existing approved FAMPNN/ProteinMPNN paths; Foundry LigandMPNN for atom-context design | Producer-native FASTA/masks/scores and downstream structure binding |
| Conformational mapping | Protenix V2 ensembles and ConforNets | Specialized adapters and backend-native ensemble semantics |
| Molecular dynamics | GROMACS | Specialized request, run, replicas, trajectories, analysis, and viewer |
| Frustration analysis | Global FrustraMPNN | Exact configuration, landscape, comparison, guidance, and workbench |
| Structure/result comparison | Registered compatibility adapters only | Explicit reference/target roles and missingness |
| Antibody/nanobody | Specialized workflows that pass Phase P0 closure | Preserve framework/CDR/chain semantics and typed validators |

### 4.3 Experimental and historical lanes

Fold-CP, shape/CAD, Protein Hunter, DISCO, La-Proteina, BoltzGen, legacy RFdiffusion, RF3, AF2, and other experimental or compatibility entries must be reconciled explicitly. Mutation/variant exploration is part of the required accepted denominator because it is user-visible and scheduler-routed on the baseline. AF2 has no accepted new-launch state in this SOW. It is historical attachment-only or disabled.

For each lane, Phase P0 chooses one truthful state:

1. **Accepted:** complete every contract in this SOW and expose it through the capability inventory.
2. **Experimental:** expose only through its current explicit experimental surface and give it full typed Project planning/result closure before Project Manager advertises it.
3. **Internal:** keep it non-public and use it only as a typed child of an accepted workflow.
4. **Historical:** allow verified attachment and reopening; reject new Project launch.
5. **Disabled:** hide it and fail closed before Job creation.

A registry declaration or visible card cannot remain outside this classification. Full vertical completion means every server-advertised and user-visible protein capability is accepted or explicitly experimental with complete typed closure. Unqualified entries remain hidden.

### 4.4 Capability inventory integrity

At startup and in health:

- enumerate model definitions, modes, Workflow Plan adapters, materializers, result contracts, result adapters, launchers, and viewer routes;
- compute one inventory digest;
- reject an exposed capability when any required link is absent or ambiguous;
- keep system-owned image, checkpoint path, executable, and GPU identity out of public settings;
- bind runtime/checkpoint/source digests in readiness and immutable execution receipts.

## 5. Protein In Silico Domain Experiment contract

### 5.1 Scientific intent

The Domain Experiment revision stores intent and references. Exact model settings stay in Workflow Plan revisions.

Required fields are:

- experiment mode;
- scientific objective;
- one or more targets with stable identity, role, and verified source receipt IDs;
- typed design constraints;
- canonical planned capability IDs;
- typed comparison groups;
- canonical validation capability IDs;
- acceptance criteria and evidence plan;
- source Dataset revision IDs when applicable.

A target is valid only when every source receipt resolves uniquely to one immutable sequence, structure, complex snapshot, motif, template, ligand-context structure, or accepted Dataset member.

Create and edit write outer `bms.domain-experiment.v2` from the companion SOW with `domain_contract_version="2"` and `domain_payload.schema="bms.protein-in-silico-experiment.v2"`. The protein payload contains exactly:

```text
schema: const bms.protein-in-silico-experiment.v2
experiment_mode: exploration | design | redesign | prediction | validation | comparison | simulation | analysis
scientific_objective: string, max 8192 characters
targets: array of bms.protein-target.v2, 1..128
design_constraints: array of bms.protein-constraint.v1, max 256
planned_capability_ids: array of unique registered capability IDs, max 64
comparison_groups: array of bms.protein-comparison-group.v1, max 128
validation_capability_ids: array of unique registered validation capability IDs, max 32
acceptance_criteria: array of bms.scientific-criterion.v1, max 128
evidence_plan: array of bms.evidence-requirement.v1, max 128
source_dataset_revision_ids: array of unique exact Dataset revision resource IDs, max 128
```

`bms.protein-target.v2` contains exactly `target_id`, `label`, `role`, `source_receipt_ids`, `dataset_member_refs`, `entity_map`, and `expected_content_sha256`. Role is `target`, `binder`, `partner`, `template`, `reference`, `control`, `motif`, `ligand_context`, or `other`. `source_receipt_ids` must be non-empty unless one or more exact `dataset_member_refs` provide the authority. A Dataset member reference contains exactly `dataset_revision_id` and `member_id`. `entity_map` uses a separately registered closed schema for chain/entity/residue identity. The server computes or verifies `expected_content_sha256`; browser text cannot establish it.

`bms.protein-constraint.v1` contains exactly `constraint_id`, `schema_id`, `target_ids`, and `payload`. `bms.protein-comparison-group.v1` contains exactly `group_id`, `label`, `members`, and `compatibility_contract_id`; each member has `target_id`, `role`, and `ordinal`, where role is `reference`, `target`, `panel`, or `control`. The shared criterion and evidence objects are those frozen in the companion SOW. Every nested object is closed and each `schema_id` resolves to a server-registered closed JSON Schema.

The outer `dataset_revision_ids` and payload `source_dataset_revision_ids` must be equal after canonical ordering. Stable Dataset IDs and current heads are invalid. Draft intent can have empty capability, validation, criterion, and evidence arrays. A transition to `planned` or `active` requires at least one planned capability, acceptance criterion, and evidence requirement.

Existing outer/domain v1 and `bms.protein-in-silico-experiment.v1` revisions remain byte-for-byte readable. No backfill invents criteria, evidence, constraints, roles, digests, or Dataset revisions. The first edit of v1 creates a v2 successor. The operator must resolve every free-text capability/validator to a registered ID, verify every target receipt, choose exact Dataset revisions, and supply the new criterion/evidence fields. Unresolvable v1 intent stays historical/read-only until corrected.

### 5.2 Target authority

Target selection uses registered adapters and exact receipts. Required authority includes:

- stable native entity ID;
- exact revision/generation;
- sequence or structure content digest;
- semantic role;
- entity/chain/partner identity where applicable;
- canonical source/read route;
- canonical viewer route where available.

A filename, path, accession label, Job name, or free-text target ID is not sufficient authority.

Missing, foreign, stale, duplicate, or ambiguous targets fail closed. Changing target authority creates a new Domain Experiment revision and new Workflow Plan revisions. It never rewrites launched work.

### 5.3 Canonical taxonomy

Project Manager presents one Protein In Silico Domain. Its workflows are grouped by scientific capability.

De Novo Design contains RFD3 general generation and RFD3 Local Redesign. Specialized antibody/nanobody design remains a distinct typed child capability inside the same Protein In Silico domain. Experimental engines appear only under their classified capability and never as a default substitute.

## 6. Workflow Plan and preparation contracts

### 6.1 Shared lifecycle

Every accepted capability supports:

```text
create plan
→ save immutable revision
→ prepare and validate
→ inspect normalized request and expected outputs
→ explicit launch
→ run group / scientific replicas / attempts
→ reconcile canonical lifecycle
→ publish verified terminal result receipts
→ reopen native result
```

Saving and preparing create no Job.

### 6.2 Settings parity

Every result-affecting scientific setting supported by an accepted model is available through suitable typed UI controls and the same typed API used by agents.

Each accepted model must pass all ten gates in `docs/Model_Configuration_Operator_Control_and_Agent_Parity.md`. Registry YAML, selected launcher controls, or a model-detail endpoint cannot establish parameter parity alone. Hidden active defaults are forbidden. Shape Blueprint must expose its validator suite as one typed UI/API setting and persist both requested and effective validator identities.

The immutable preparation stores:

- requested settings;
- normalized effective settings;
- settings schema/version and digest;
- capability, engine, model, mode, adapter, and result-contract identity;
- exact source receipt and Dataset revision IDs;
- canonical target/role map;
- expected cardinality and scientific-replica plan;
- typed scheduling controls, including the operator-selected allowed GPU/device set when applicable, CPU threads, and memory request;
- normalized request digest;
- validation receipt.

The scheduler validates selected devices against live capability. Before a run group or child Job can enter dispatch, the companion-owned resource-admission service atomically reserves the plan's effective CPU-thread and DRAM requests against one shared ledger scoped to all active managed BMS workflow children on this deployment. `pending`, `dispatching`, `queued`, and `running` allocations count. Terminal or atomically cancelled allocations release once. Retries use a new reservation and cannot overlap a still-live predecessor allocation. The service rejects the complete launch with a typed `resource_admission_denied` response when the proposed aggregate would exceed 24 CPU threads or 96 GiB DRAM; it does not queue an over-limit plan or silently lower its request. The policy source, ledger owner, lease/recovery rules, effective requests, aggregate totals, rejection, and actual child-process usage enter health and runtime evidence. Phase P0 must identify and reuse any exact authoritative enforcement already present; otherwise this ledger and gate are new shared work.

A launcher cannot silently substitute a model, algorithm, validator, sequence designer, or runtime.

### 6.3 Ordinary core Jobs

Use the shared typed core Job adapter only when the core Job request, lifecycle, result contract, artifacts, and viewer are fully represented by core authority.

Before adding a model to this adapter, prove:

- exact model/mode resolves to a static entrypoint and profile;
- unknown parameters fail before Job creation;
- every critical setting round-trips through UI, API, persistence, clone/reopen, and runtime;
- terminal success requires the canonical result contract;
- exact artifacts and result rows can be verified;
- one canonical result viewer exists.

A generic Job route or output directory scan is insufficient.

### 6.4 Specialized plans

Specialized preparation and materialization adapters are mandatory for:

- RFD3 general de novo design;
- RFD3 local redesign;
- multi-stage design → sequence → validation workflows;
- Conformational Mapping;
- GROMACS molecular dynamics;
- FrustraMPNN child analysis, comparison, guidance, and reanalysis;
- any experimental workflow with additional native request or result authorities.

## 7. Capability-specific integration

### 7.1 Folding, structure prediction, and validation

For Boltz-2, ESMFold2, and Protenix V2, the global layer must preserve:

- exact input sequence/complex snapshot authority;
- component roles and copy/chain identities;
- MSA/template policy and exact registered assets when used;
- seeds, samples, recycles/steps, and expected cardinality;
- model/checkpoint/runtime identity;
- native structures and confidence/error artifacts;
- producer-native metrics and missingness;
- canonical Design/result identity and artifact manifest;
- execution status separate from scientific acceptance;
- exact Structure/Results Viewer route.

Complex binder selection uses the accepted validator's native compatible evidence. Boltz-2 complex selection uses authoritative full PAE/ipSAE when that contract applies. Monomer results use documented monomer metrics. Protenix and ESMFold2 retain their native metrics. No metric is relabeled as another model's score.

A validation strategy can name several validators. Each output remains separate evidence. The platform does not create an automatic majority verdict unless a later versioned policy is separately approved.

### 7.2 RFD3 de novo design

RFD3 general/non-nanobody de novo integration must have a specialized request and result authority. It preserves:

- source-less or source-conditioned design mode;
- target/partner/motif/ligand context receipts;
- exact RFD3 sampler/checkpoint/runtime identity;
- all-atom versus guidance representation;
- seeds, candidate count, length regime, constraints, and guidance schedule;
- native mmCIF and metadata;
- candidate IDs and fan-out coordinates;
- guidance and chain-validity evidence as separate fields;
- optional sequence-design handoff only when the generator contract requires it;
- downstream validator results as separate evidence;
- immutable aggregate and per-candidate manifests;
- canonical viewer and download routes.

RFD3 execution success does not establish design acceptance. Geometry agreement, chain continuity, clashes, role integrity, and selected validation evidence remain distinct.

### 7.3 RFD3 local redesign

Reuse `rfd3_local_redesign_v1` and existing request/result services. Preserve:

- exact source structure and selected editable region;
- fixed/context chains and atom selections;
- partial-diffusion or minimal-insertion mode;
- sequence policy;
- native request and producer input;
- candidates, optional trajectories, result manifest, and provenance;
- source → candidate `derived_from` lineage.

Global taxonomy places this plan beneath De Novo Design. Historical model IDs and routes remain readable.

### 7.4 Sequence design

Sequence design is a typed downstream capability. It never becomes an implicit part of every RFD3 run.

For every accepted producer, preserve:

- exact structure and atom-context authority;
- designed and fixed residue masks;
- chain/entity/residue mapping;
- model/checkpoint/runtime identity;
- requested/effective temperature, bias, omit, symmetry, and cardinality settings;
- native FASTA, scores, and sequence-to-structure identity;
- optional packing as a separate typed stage;
- downstream validation and FrustraMPNN analysis as separate activities.

The accepted sequence-design matrix is fixed as follows:

| Capability/producer ID | Mode and consuming workflow | Request/result authority | Launcher and materializer | Terminal verification and viewer | Release state |
|---|---|---|---|---|---|
| `protein.sequence_design.fampnn.general.v1` | `design`, `fixed_backbone`, or `binder_design`; explicit child of general RFD3 de novo, native RFD3 local-redesign output, or an operator-authored sequence-design plan | closed `bms.protein.sequence-design.fampnn.v1`; `sequence_design_v1` plus a digest-bound producer manifest | canonical protein plan/launcher; one `workflows/protein_sequence_design.nf` wrapper that reuses `PrepFAMPNN`, `RunFAMPNN`, and `FilterFAMPNN` from `modules/fampnn.nf` | verify source structure, masks, chain map, requested/effective settings, all native PDB/FASTA/JSON/score bytes, record cardinality, and source-to-sequence mapping; Results Viewer sequence-design pane | Required accepted |
| `protein.sequence_design.proteinmpnn.general.v1` | `design`; explicit compatibility choice in the same three plan contexts | closed `bms.protein.sequence-design.proteinmpnn.v1`; `sequence_design_v1` plus producer manifest | the same canonical wrapper with mode `proteinmpnn`, reusing `RunMPNN` and `FilterMPNN` from `modules/proteinmpnn.nf` | verify fixed/design masks, checkpoint/noise/temperature settings, all native PDB/FASTA/JSON/score bytes, cardinality, and mapping; same viewer contract | Required accepted compatibility lane |
| `protein.sequence_design.fampnn.antibody.v1` | internal CDR/framework-aware child of accepted antibody/nanobody plans only | closed antibody child request with framework/CDR/target-lock authority; specialized antibody result contract | accepted antibody parent plus existing `fampnn_child/sequence_design` entrypoint and `workflows/fampnn_child.nf` | verify antibody constraint map, H/L or VHH identity, target locks, FAMPNN outputs, candidate family, and parent lineage; specialized antibody viewer | Required internal child; cannot launch as a generic antibody-free plan |
| `protein.sequence_design.ligandmpnn.foundry.v1` | coordinate-aware ligand, nucleotide, metal, or nucleic-acid context design | closed `bms.protein.sequence-design.foundry-ligandmpnn.v1`; specialized `bms.protein.ligandmpnn-result.v1` | canonical plan plus one BMS wrapper that invokes the sole Foundry LigandMPNN entrypoint; no second LigandMPNN implementation | verify coordinate-bearing pose/context, designed/fixed masks, chain/entity map, native FASTA/scores, optional packed outputs, exact cardinality, manifest, and lineage; specialized viewer | Required accepted after the missing Foundry path is implemented |

`workflows/protein_sequence_design.nf` is an orchestration wrapper over the existing FAMPNN and ProteinMPNN modules. It cannot fork their scientific implementation. It accepts only immutable server-staged inputs and a closed compiled settings document. Current `fampnn_extra_config` and `mpnn_extra_config` free-form passthroughs are excluded from accepted Project plans until each supported field becomes a typed schema property; arbitrary CLI fragments fail validation.

The accepted antibody matrix enables only the FAMPNN child above by default. Antibody ProteinMPNN, AntiFold, Caliby, maturation-specific FAMPNN uses, Shape Blueprint consumers, and any other FAMPNN/ProteinMPNN child remain `internal`, `experimental`, `historical`, or `disabled` until their own row names an exact parent workflow, request/result versions, materializer, verifier, viewer, settings parity, and owner-path receipt. The accepted UI hides or disables those rows and cannot enable them from a registry/module presence. `seq_method`, `plr_seq_method`, and antibody sequence toggles compile to one explicit matrix row; null or unknown values fail instead of selecting a producer.

Foundry owns all LigandMPNN execution. The global layer must not add a second implementation or infer chemical pose from SMILES/CCD alone. LigandMPNN consumes an immutable coordinate-bearing parent pose and context map.

The Foundry LigandMPNN lane must provide:

- one explicit executable entrypoint and scheduler profile;
- immutable source structure, ligand/nucleic-acid coordinate context, residue mask, chain/entity map, and expected digest authority;
- typed settings with requested/effective parity;
- producer-native multi-record FASTA, scores, masks, and sequence identities;
- optional packed or refolded structures as separate derived results;
- a digest-bound terminal manifest, specialized Project adapter, result surface, and canonical viewer;
- no use of ligand SMILES or CCD identity as a substitute for a coordinate-bearing pose.

### 7.5 Mutation and variant exploration

`protein.mutation_variant_exploration` governs the current library and manual mutagenesis surface plus its `mutagenesis/batch_predict` scheduler path. Variant generation becomes a server-owned immutable scientific authority. The browser may edit and preview a draft. It cannot establish canonical variant identities, mutation coordinates, sequences, digests, or comparison membership.

The immutable variant-set request preserves:

- exact parent sequence or structure receipt, parent digest, chain/entity map, and residue numbering authority;
- library or manual mode;
- selected chains, regions, residue positions, and any fixed/excluded positions;
- substitution strategy, allowed/blocked amino acids, source-residue exclusions, mutation-count rule, insertions/deletions, indel sizes/probability, random seed, and requested cardinality;
- explicit manual mutation sets when manual mode is used;
- accepted structure predictor capability and its complete versioned settings;
- expected per-variant and aggregate result contracts;
- deterministic normalized request and variant-set digests.

The server validates wild-type residues, residue numbering, chain scope, mutation syntax, duplicate variants, expected cardinality, and predictor policy before preparation. RF3 and AF2 cannot enter a new variant plan. Predictor choice is limited to accepted Protein In Silico structure authorities, currently Boltz-2, ESMFold2, and Protenix V2 after their own parity and closure gates pass. A multi-predictor request creates explicit child plans and results; `both` is not an opaque model selector.

The terminal authority preserves one immutable variant-set manifest, stable variant IDs, exact mutations and derived sequences, parent-to-variant `derived_from` edges, child predictor attempts/results, failures and missingness per variant, comparison compatibility, and canonical result/viewer routes. Mutation count and result cardinality come from producer-native rows. Retry attempts cannot inflate variant or scientific-replica counts. Manual antibody/CDR mutagenesis remains under its specialized antibody authority while using the same mutation identity rules.

### 7.6 Antibody and nanobody workflows

The accepted specialized parent capability is `protein.antibody_nanobody_design.v1`, implemented through canonical `template_antibody_denovo` modes `antibody_denovo_pipeline` and `antibody_refinement_pipeline` under `bms.antibody-pipeline.v1`. It remains a typed child of Protein In Silico. It preserves:

- exact antigen/epitope receipts and chain/entity map;
- framework format `fab|vhh|scfv`, framework receipt, numbering scheme/version, H/L or VHH roles, and CDR identities;
- locked framework, target, and VHH-tetrad constraints where applicable;
- explicit generator ID, sequence-designer child IDs, validator capability ID, scoring stages, maturation stages, and complete requested/effective settings;
- candidate family IDs and artifact-class transitions from backbone complex through sequence-designed, validated, and post-validation-refined complex;
- native structures, sequence outputs, framework/CDR annotations, interface and validator-native PAE/ipSAE evidence where applicable, missingness, and terminal manifests;
- exact parent/child Job, selection, refinement, mutation, validation, and result lineage;
- canonical antibody/nanobody result viewer and Project return context.

The release matrix is fixed:

| Child family | Accepted state in this SOW |
|---|---|
| RFantibody generation | Accepted only after its typed child request/result, runtime/checkpoint authority, candidate cardinality, specialized parent lineage, and current owner-path receipt pass |
| BoltzGen nanobody/antibody generation | Experimental until the same specialized closure passes; it cannot inherit acceptance from generic BoltzGen |
| Seeded PPIFlow generation/refinement | Experimental or internal until its objective, anchors/loops, source stage, request/result, and viewer lineage close |
| FAMPNN antibody sequence design | Required internal child through `protein.sequence_design.fampnn.antibody.v1` |
| ProteinMPNN, AntiFold, and Caliby antibody sequence design | Internal/experimental and hidden unless a later matrix row closes every contract; current toggles cannot enter an accepted Project plan |
| Boltz-2, Protenix V2, ESMFold2 validation | Accepted validator choices only after each general validator and antibody-specific chain/interface/result mapping passes; each remains separate evidence |
| AntiBERTy, ThermoMPNN, IgGM, PPIFlow maturation, and other scoring/refinement children | Internal/experimental until typed settings, scientific semantics, native result authority, lineage, and viewer closure pass |

The server requires one explicit child ID for each enabled stage. Empty values may use only a versioned schema default visible to UI and agents. Unknown generator, sequence designer, validator, scorer, or maturation ID returns `422` before preparation. Remove the current behavior that coerces an unknown `structure_validator` to Boltz-2 in the Job normalizer and antibody Nextflow children. Child failure cannot trigger another engine.

Phase P4 implements this specialized closure after Phase P0 classification. Any child row that does not pass remains unavailable in Project Manager, even if current registry YAML, frontend controls, or a workflow include exposes it. Release requires at least one current owner-path acceptance receipt for each accepted parent mode and for every child row classified accepted. The retained browser scenario must create, launch, review, restart, and exactly reopen one accepted VHH/nanobody plan, its framework/CDR authority, generator child, FAMPNN child, one explicit validator, candidate family, result viewer, and lineage.

General protein adapters cannot erase these semantics. CM orchestration does not use SAbDab.

### 7.7 Conformational Mapping

Move the existing global CM workflows under Protein In Silico Domain Experiments without changing CM authority.

The Project graph preserves:

- immutable source receipts and snapshots;
- backend-specific plan revisions for Protenix V2 ensembles and ConforNets;
- dependency order and expected cardinality;
- complete backend coordinates;
- ensemble manifests and structures;
- residue maps;
- per-candidate FrustraMPNN analysis;
- partial failure and retry state;
- comparisons and support/exclusion evidence;
- canonical CM Viewer route.

Protenix samples are stochastic structural hypotheses. ConforNets outputs are conformers, not trajectory frames. Any density-derived display remains a post-hoc sample-density view, not thermodynamic free energy.

A cross-backend comparison is a separate immutable comparison authority. It binds complete source ensemble and analysis digests. One-backend request APIs do not stand in for a multi-node Project graph.

### 7.8 Molecular dynamics

Use a specialized MD adapter around GROMACS authority. Preserve:

- immutable `bms.md.job` request and request digest;
- `MdRun` identity and state version;
- starting structure, topology, force-field/chemistry profile, solvent, boundary, ensemble, duration, timestep, and restraint authority;
- scientific replica identities separate from retry attempts;
- scheduler child Jobs and runtime/GPU identity;
- topology, trajectory, checkpoint, energy, and analysis artifacts;
- aggregate and replica manifest digests;
- terminal failure and partial analysis state;
- MD Results Pane and governed structure/trajectory playback.

File presence never implies MD completion. Project Manager shows actual replica rows and their own attempts.

Current Development MD acceptance is limited to the exact installed `gmx_amber99sb_ildn_tip3p_smoke_v1` profile and its pinned structure digest. It permits one GROMACS replica at 2 fs, 300 K, 1 bar, 0.15 M salt, and 1.0 nm padding, with at most 50,000 minimization steps, 50,000 NVT steps, 50,000 NPT steps, and 5,000 production steps. This proves infrastructure, topology closure, finite short trajectory, checkpoint, artifact, provenance, and playback behavior only. It cannot support a scientific production or convergence claim.

DRT4 cannot enter this MD profile. A DRT4 MD run remains blocked until a separately accepted chemistry/topology profile closes exact accession and sequence boundaries; chain and residue numbering; protein–DNA support; dCTP and Mn²⁺ parameters; the Tyr125-linked nucleotide covalent topology; protonation; restraints; replica design; convergence/stationarity criteria; and target observables. The accepted receipt must bind the installed force-field/topology assets, preparation/runtime identity, source structure, all modelled chemical species, and the approved analysis plan.

### 7.9 FrustraMPNN

Use the global FrustraMPNN configuration, normalized structure authority, exact residue map, complete residue × 20-substitution landscape, comparison, guidance, statistics, review, capture, and export services.

Project integration must support:

- standalone analysis of any accepted structure result;
- automatic child analysis when an accepted parent workflow requires it;
- exact landscape receipt and viewer reopening;
- pairwise and multi-state comparison with compatibility and missingness;
- guidance linked to an exact landscape or comparison;
- approved handoff into a typed redesign plan;
- fresh result, reanalysis, and comparison after redesign;
- immutable lineage from source → landscape → comparison/guidance → redesign → outcome.

Guidance remains decision support. It cannot directly dispatch a generator without an explicit operator launch and a validated child plan.

### 7.10 Structure and result comparisons

A comparison runs only through a registered compatibility adapter. It verifies:

- result contract and schema versions;
- model/checkpoint/configuration semantics where direct numerical comparison requires them;
- target, chain, entity, residue, and candidate identity;
- units, formulas, thresholds, and missingness;
- complete source digests;
- explicit reference, target, panel, and control roles.

The global result index shows `not comparable` with the adapter's reason when proof fails. It never coerces incompatible metrics into one chart.

## 8. Additive migration and historical integration

Migration numbers are allocated from the implementation baseline. Issued SQL and checksums remain unchanged.

### 8.1 Shared attempt-to-preparation migration

Each `run_attempts` row must reference its exact immutable preparation.

The companion NGS/MolBio SOW is the sole contract and implementation owner for this shared migration in every implementation order. On the pinned planning baseline it is Global V11. The owner revalidates the next available number when implementation starts, implements and accepts the migration once, and publishes its exact schema/migration receipt. This Protein tranche consumes that accepted migration as a prerequisite. It cannot author, duplicate, renumber, or replace the authority even when Protein work is scheduled first.

Backfill only when the owning workflow run identifies one unique preparation. Ambiguous rows fail migration and require an explicit repair ledger.

Retry semantics are identical to the companion SOW. A new attempt revalidates its source preparation against current one-time and revisioned authority. It may reuse the exact preparation ID only when normalized request, targets, Dataset revisions, settings, and validation receipt remain valid and unchanged. Any stale or changed authority returns `replacement_preparation_required`; dispatch waits for a successor immutable preparation. That successor stores `supersedes` from new preparation to prior preparation, and the attempt binds the successor directly. Scheduler materialization reads the attempt's preparation, never a mutable workflow-run projection.

### 8.2 Existing CM workflows

For each existing CM workflow already parented to a Global Experiment:

- create or resolve one Protein In Silico Domain Experiment;
- bind the workflow under that Domain Experiment;
- retain every workflow, revision, preparation, run group, run, attempt, request, result, and receipt ID;
- record migration lineage and audit events.

A workflow parented only to a Project stays `needs_domain_assignment`. Migration does not guess scientific context.

### 8.3 Existing Jobs and results

- Existing core Jobs, Designs, RFD3 requests, CM requests, MD runs, FrustraMPNN records, artifacts, and viewers retain their native IDs.
- Existing verified Project receipts remain attached.
- Unassigned legacy Jobs stay unassigned until an operator uses `Add existing`.
- Historical AF2, RF3, legacy RFdiffusion, and other non-current results may be attached and reopened when exact native authority exists. They are not promoted into new accepted launch capabilities.
- No migration invents a target, objective, role, hypothesis, acceptance criterion, comparison, or conclusion.

#### 8.3.1 Retained CM→FrustraMPNN compatibility authority

Preserve the current database-driven importer as a narrow recovery path for an exact completed five-candidate CM result. It must continue to require the persisted immutable CM request, complex snapshot, coordinate plan, native candidate/artifact identities, exact 54,000-row landscape authority, and digest agreement before publication. It cannot accept uploads, caller-selected files, a different cardinality, an inferred snapshot, or a current-head substitution. The CLI's caller-selected `job_id` is selection only. It cannot establish target identity.

Add one closed, server-issued `bms.cm-legacy-frustrampnn-import-authority.v1` receipt before any imported result can be classified as DRT4. Persist it as an immutable existing `domain_adapter_receipts` resource with adapter ID `bms.cm-legacy-frustrampnn-import-authority.adapter.v1`, adapter version `1`, and operation kind `classify_historical_cm_import`. No new receipt table or registry is allowed. The normalized request contains only the exact Protein Domain ID, target receipt ID, and selected CM job ID; a persisted Project owner authorizes it and the server derives every remaining field. The receipt contains exactly `receipt_id`, `job_id`, `request_id`, `request_sha256`, `target_id`, `target_receipt_id`, `target_receipt_sha256`, `target_accession`, `target_sequence_sha256`, `source_snapshot_id`, `source_snapshot_sha256`, `source_snapshot_entity_id`, `source_snapshot_sequence_sha256`, `coordinate_plan_sha256`, ordered `candidate_ids`, `expected_landscape_rows`, `issued_by`, and `issued_at`. `target_accession` is exactly `WP_031606642.1`. The server resolves `target_receipt_id` independently, extracts the exact DRT4 protein entity from the persisted CM source snapshot, and requires its normalized amino-acid sequence digest to equal `target_sequence_sha256`; ambiguous or absent entity mapping fails. It also requires the same `target_id` and source snapshot used by the persisted CM request. The importer loads the receipt from the global store, derives `job_id`, and requires byte-for-byte agreement with every named core-store CM authority. It records the canonical classification-receipt SHA-256 in every imported manifest and terminal receipt. Reuse of an authority receipt with changed source state fails closed.

The operator CLI accepts exactly `--authority-receipt-id`, `--core-database`, and `--experiment-database`. Database selectors choose infrastructure only and cannot choose scientific identity. The experiment database opens read-only; the core database opens read/write. The CLI loads and verifies the immutable global receipt first, derives the core `job_id`, re-verifies the target/source sequence binding, and then calls the core importer. `--job-id`, target IDs, receipt bodies, snapshots, candidates, digests, and cardinality overrides are invalid arguments.

Until that receipt and resolver exist, imported records are labelled only `historical_cm_compatibility`. They cannot carry a DRT4 label, satisfy a DRT4 acceptance item, or be advertised as a DRT4-specific import. The importer publishes and reopens accepted records through the canonical persisted FrustraMPNN result, artifact, landscape-row, result-surface, and viewer authorities. Native CM IDs, candidate IDs, source digests, request provenance, and import identity remain unchanged. Imported compatibility records may attach to Project/Dataset/lineage through verified exact receipts. They cannot prove a fresh FrustraMPNN invocation, advertise a general import capability, or satisfy a current owner-path qualification receipt.

### 8.4 Schema minimality

No new protein scientific-result table is added to `experiments.db`. Existing global revisions, Dataset members, receipts, adapter receipts, lineage, artifacts, validations, logs, and research records are the integration substrate.

Add schema only when an existing global record cannot enforce an authority required by this SOW. Any addition must be transactional, additive, attested for fresh and upgraded databases, and included in backup/export.

## 9. Global adapter closure

### 9.1 Adapter classes

| Authority class | Current state | Required result |
|---|---|---|
| Core persisted Design/result | Present | Require producer/result contract and digest-grade artifact authority |
| Typed core Job result | Present for a subset | Derive exact set from accepted capability inventory; reject generic unsupported results |
| Structure prediction result | Generic/partial | Add or harden result-contract-aware adapter with exact native artifacts and viewer |
| Structure validation result | Partial | Preserve validator-native evidence and validation role |
| RFD3 general de novo | Missing specialized closure | Add request/candidate/result adapter family |
| RFD3 local redesign | Present | Keep and place under De Novo Design taxonomy |
| Sequence design result | Partial | Add typed FASTA/mask/context result adapter where core Design is insufficient |
| Antibody/nanobody result | Generic/partial | Preserve framework/CDR/chain and specialized result contract |
| CM Protenix | Present | Keep specialized preparation, result, and reopen behavior |
| CM ConforNets | Present | Keep specialized preparation, result, and reopen behavior |
| MD | Result adapter present | Add specialized preparation/materialization and complete replica/result linkage |
| FrustraMPNN result | Present | Keep exact invocation and landscape authority |
| FrustraMPNN comparison | Present | Preserve reference/target role direction and compatibility |
| FrustraMPNN guidance | Present | Preserve source landscape/comparison and decision-support semantics |
| Shape/CAD or experimental design | Partial/conditional | Complete only when classified accepted/experimental; otherwise hide new launch |

### 9.2 Adapter protocol

Every accepted adapter must:

1. Validate the Domain Experiment mode and capability.
2. Resolve exact input receipt and Dataset revisions.
3. Normalize through the same compiler used by the canonical launcher.
4. Compute one deterministic request digest.
5. Run canonical admission checks without creating a Job.
6. Publish an immutable preparation and validation receipt.
7. Materialize only through the existing scheduler or specialized run service.
8. Persist canonical Job/run/request identities.
9. Reconcile canonical state without overwriting it.
10. Verify terminal result contracts and artifacts.
11. Publish result receipts and lineage.
12. Return canonical launcher, result, viewer, and Project-return routes.
13. Preserve native failure detail.

Registry membership without a materializer and terminal verifier is invalid for an exposed capability.

For typed core results, terminal verification must resolve and hash the canonical producer-native artifacts required by the model's result contract. Hashing only Job parameters, stage-output labels, or provenance is insufficient. DISCO and La-Proteina require their own typed manifest/ingestion and comparison authority before Project Manager can advertise them as complete experimental capabilities.

## 10. Immutable Datasets

### 10.1 Dataset kinds

Protein In Silico adds these rows to the NGS-owned shared Dataset registry:

| Exact `dataset_kind` ID | Meaning |
|---|---|
| `protein.target_set.v1` | Target sequence, structure, or complex set |
| `protein.template_motif_partner_control_set.v1` | Template, motif, partner, and control set |
| `protein.generated_candidate_cohort.v1` | Generated candidate cohort |
| `protein.selected_finalist_cohort.v1` | Selected finalist cohort |
| `protein.structure_prediction_validation_result_cohort.v1` | Structure-prediction or validation result cohort |
| `protein.cm_ensemble_conformer_cohort.v1` | CM ensemble or conformer cohort |
| `protein.md_replica_analysis_cohort.v1` | MD replica or analysis cohort |
| `protein.frustrampnn_landscape_guidance_cohort.v1` | FrustraMPNN landscape or guidance cohort |
| `protein.compatible_comparison_cohort.v1` | Compatible comparison cohort |
| `protein.saved_review_filter_selection.v1` | Saved review or filter selection |

Each row supplies only Protein-specific allowed receipt kinds, roles, member bounds, and compatibility rules. The companion SOW remains the sole registry and API implementation owner. An unknown, disabled, or wrong-Domain kind fails as `422 unsupported_dataset_kind`, and a Dataset kind cannot change after creation.

### 10.2 Membership authority

Canonical membership is persisted in `dataset_revision_members`.

Each member stores:

- global receipt ID;
- native stable entity ID;
- exact revision/generation or composite result identity;
- content digest and contract digest where present;
- semantic role and ordinal;
- bounded canonical member JSON and its own SHA-256 digest;
- media type and size when the native contract provides them.

The Dataset does not duplicate structure, sequence, trajectory, landscape, or metric payloads.

A Workflow Plan pins Dataset revision IDs. Preparation cannot resolve current Dataset heads. Historical Dataset reopening retrieves exact persisted membership independently of later revisions.

### 10.3 Scientific replicas and retries

Dataset cohorts and workflow read models distinguish:

- independent scientific replicas or stochastic samples, supported by native coordinates;
- retry attempts of one run;
- resubmitted run groups;
- cloned plans;
- derived analyses.

Retry attempts never inflate scientific replica counts.

## 11. Lineage contract

Direction is fixed as follows:

| Edge | Stored direction |
|---|---|
| `derived_from` | derived result, Dataset, guidance, or design → immediate immutable source |
| `compared_with` | comparison authority → each compared result or Dataset revision |
| `uses_input` | preparation/activity → source receipt or Dataset revision |
| `produced` | attempt/activity → result receipt |
| `validated_by` | candidate/result → validation result or evidence receipt |
| `references` | Domain Experiment or plan revision → target/source receipt |
| `retried_from` | new attempt → immediate prior attempt |
| `resubmitted_from` | new run group → source run group |
| `supersedes` | new immutable record → superseded record |
| `supports_conclusion` | evidence receipt → operator conclusion |

Every comparison edge includes role, ordinal, compatibility adapter/contract, source contract, and digest. `compared_with` is rendered as a comparison relationship but persists from the comparison record to each member.

Required representative paths are:

```text
source sequence / structure / complex
→ Domain Experiment revision
→ Dataset revision
→ Workflow Plan revision
→ preparation
→ run group
→ workflow run
→ attempt
→ canonical Job / RFD3 request / CM request / MdRun
→ result and artifact receipts
→ FrustraMPNN / validation / comparison
→ observation
→ decision or conclusion
```

and:

```text
source result
→ FrustraMPNN landscape
→ comparison or guidance
→ approved redesign plan
→ RFD3 or sequence-design attempt
→ validated result
→ fresh landscape
→ outcome comparison
```

A related target must resolve uniquely by stable identity and expected digest. Missing or ambiguous lineage fails closed.

## 12. APIs

### 12.1 Capability API

Add a bounded read-only endpoint over the server-owned protein capability inventory at this final route:

```text
GET /api/domain-capabilities/protein-in-silico
```

It returns only fields safe for operator/agent use and one registry digest. Runtime paths and physical GPU identities remain server-owned.

### 12.2 Domain Experiment and canonical workflow APIs

Extend current Project APIs so Protein Domain create and patch reject unregistered capability IDs, verify target receipt ownership/digest, validate mode against selected capabilities, preserve exact Dataset revisions, and create immutable revisions with generation checks.

The companion NGS/MolBio SOW section 6.5 is incorporated by reference as the sole controlling contract for every shared Domain Workflow Plan, attachment, draft/revision, preparation, prepared handoff, run-group, retry, resubmit, cancellation, result-surface, and comparison route. Its methods, paths, strict request fields, response identities, generation checks, `Idempotency-Key` rules, launch-mode split, error statuses, and owner/operator requirements apply unchanged to `domain_kind=protein_in_silico`. Protein code cannot define a second router contract, storage path, launch-context authority, or idempotency scope.

Protein-specific constraints are additive:

- `capability_id` must resolve to the Protein capability inventory and derive one registered protein workflow family, adapter, launch mode, allowed model/mode set, parameter schema, result contracts, and viewers.
- Target attachment accepts only protein adapters whose exact receipt role is allowed by the current Protein Domain v2 revision.
- A plan draft must bind the selected capability's exact target receipts and Dataset revisions. It cannot change Domain target identity through scheduler parameters.
- `typed_launcher_handoff` is used only when the accepted canonical protein launcher must render/submit the bound preparation. Other protein adapters use `managed_materialization`. One attempt can use only one mode.
- Protein comparison creation accepts only a registered compatibility adapter whose closed contract supports every member receipt kind, role, digest, unit, model/configuration constraint, and missingness rule.
- Exact result reopening uses the shared `GET D/results/{receipt_id}/surface` wrapper and a registered protein result-surface builder. A generic viewer route cannot replace missing native authority.
- The Protein workspace and agents call the exact shared routes. Compatibility workspace routes call the same services.

The shared route prefix remains:

```text
D = /api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}
```

The DRT4 compatibility-classification route is final:

```text
POST D/cm-legacy-frustrampnn-import-authorities
```

It accepts exactly `target_receipt_id`, `cm_job_id`, and `expected_domain_revision_id`, plus one required `Idempotency-Key` header. A persisted Project owner is required. The server reads the immutable target receipt from the global store and the persisted CM request/snapshot/plan/candidates/landscapes from the core store, performs section 8.3.1 verification, and atomically persists one existing `domain_adapter_receipts` resource in the global store. Success returns the exact `bms.cm-legacy-frustrampnn-import-authority.v1` body, its resource ID, normalized-request SHA-256, canonical receipt SHA-256, and unchanged Domain generation. Same-key/same-request replay returns the same receipt. Same-key/different-request, stale Domain revision, foreign target/Job, wrong accession/sequence/source binding, source drift, or cardinality mismatch returns typed `409`; malformed or unknown fields return `422`; missing authority returns `404`. Receipt creation does not run the importer or write the core store.

### 12.3 Canonical launcher APIs

Existing typed launcher APIs remain the scientific submission authorities. Each participating launcher accepts one opaque `launch_context_id`, resolves `bms.launch-context.v2` server-side, and renders the exact requested settings from its bound preparation. Submission must match the preparation's model/mode, normalized effective settings, sources, Dataset revisions, expected cardinality, and validation receipt. The managed worker or launcher claim consumes the context only after the canonical Job/request commits and validates. It returns the canonical Project binding receipt and return URI.

The browser does not submit Project IDs, target digests, workflow IDs, preparation digests, effective settings, or result relationships as authority.

### 12.4 Result APIs

Project Manager uses `GET D/results/{receipt_id}/surface` for exact result reopening and the bounded Domain results page for indexes. Scientific detail stays on existing routes:

- Results Viewer for core structure/design results;
- RFD3 local/general result pane;
- CM Viewer;
- MD Results Pane;
- FrustraMPNN Workbench;
- governed artifact downloads.

Every Project result receipt has one exact source/read method and one canonical viewer route. The server derives both.

## 13. Frontend deliverables

### 13.1 Project Manager

Replace free-text capability and validation inputs with typed selectors from the capability API.

Target selection uses verified adapters and Datasets. The creation/edit dialog displays:

- experiment mode;
- scientific objective;
- exact target records and roles;
- registered planned capabilities;
- typed comparison groups;
- registered validation capabilities;
- acceptance criteria.

Project map and inspector show capability, target, plan, run, replica, attempt, result, Dataset, comparison, evidence, and reconciliation identity.

### 13.2 Protein In Silico domain workspace

Add one domain workspace within the existing shell with these required sections in this order:

```text
Overview
Targets
Datasets
Plans
Runs
Results
Structures and Ensembles
Comparisons
Molecular Dynamics
FrustraMPNN
Evidence and ELN
History
```

This workspace coordinates canonical tools. It does not reimplement model forms or viewers.

Actions include:

- attach exact targets and historical work;
- create/revise Dataset and Workflow Plan revisions;
- open a canonical typed launcher with one launch context;
- inspect preparation settings and validation;
- monitor runs, replicas, and attempts;
- open canonical result viewers and return;
- create compatible comparisons;
- attach evidence or save a review Dataset;
- append notes, observations, decisions, and conclusions.

### 13.3 Settings and launcher reuse

Project workflows use the same typed components or schema-driven controls as canonical launchers. A reduced Project-only settings form is forbidden.

When a plan opens a launcher:

- restore the exact saved requested settings;
- show Project, Global Experiment, Domain Experiment, plan revision, and target context;
- prevent context changes from overwriting explicit saved settings;
- preserve the validated return path;
- submit only through the canonical Job or specialized request endpoint.

### 13.4 Viewer behavior

Every viewer opened from Project Manager:

- validates its native result contract and digest;
- displays producer-native status and scientific acceptance separately;
- retains exact Project return context;
- withholds stale or contradictory data;
- keeps missing values explicit;
- supports governed downloads, saved reviews, captures, and exports where the native workbench supports them.

### 13.5 Query isolation

React Query placeholder data must not cross Project, Global Experiment, Domain Experiment, Workflow Plan revision, run, result, Dataset revision, or viewer context.

- Remove or scope `keepPreviousData` for authority-bearing context changes.
- Reset accumulated pages on any scope change.
- Disable mutations until the new authority validates.
- Use abort/generation guards for late responses.
- Clear structure, residue, candidate, replica, and comparison selection when the parent authority changes.

## 14. ELN-like behavior

Project and experiment records include:

- purpose, hypothesis, success criteria, and scientific objective;
- short notes and observations;
- decisions with exact source receipts;
- conclusions authored by an operator;
- superseding records that retain prior text;
- evidence, Dataset revision, plan, run, result, and comparison links;
- complete audit history.

Automated model output, ranking, comparison, and guidance remain evidence. They cannot create an operator conclusion.

## 15. Operational health and release evidence

The global operations surface must report:

- build commit and tree;
- database path, migration ledger, and schema attestation;
- writer identity and dispatcher lease owner;
- pending outbox count and oldest age;
- reconciliation lag;
- adapter/capability registry digest and closure status;
- failed receipt-verification count;
- canonical scheduler reachability;
- last successful backup receipt;
- last successful export verification time.

For each active run, Project Manager preserves canonical Job/run ID, process owner, GPU assignment, terminal state, and result finalization. Resource claims require real child process and GPU evidence during live acceptance.

## 16. Failure and security behavior

- Project writes require Project owner authority.
- Global operational actions require operator authority.
- Saved plans are declarative and reject commands, paths, scripts, imports, and executable references.
- Unknown capability, model, mode, adapter, result contract, or viewer route fails closed.
- Unsupported or hidden experimental selectors fail before Job creation.
- Missing or ambiguous target authority fails closed.
- A stale plan, Dataset, target, receipt, binding revision, normalized request, effective settings set, or validation receipt requires a successor immutable preparation.
- A retry creates a fresh attempt and binds it directly to one revalidated immutable preparation. It reuses the source attempt's exact preparation ID only when every bound authority and normalized value remains valid and unchanged. A required successor links to the prior preparation through `supersedes` before dispatch.
- A result contract or artifact digest mismatch blocks global attachment and viewer action.
- Comparison remains unavailable unless a registered adapter proves compatibility.
- Cancellation prevents automatic retries and downstream comparisons.
- Manual dispatch and reconcile remain disabled with HTTP `409`.
- No model or algorithm fallback is permitted.

Retry uses the exact invariant in section 8.1: an unchanged valid preparation can be reused, while stale or changed authority requires a `supersedes` successor preparation before dispatch.

## 17. Delivery phases

### Phase P0: Re-pin, classify capabilities, and freeze contracts

**Work:** Re-pin `test`; reconcile model, template, workflow, adapter, result-contract, launcher, and viewer inventories; classify every visible/installed protein lane; freeze capability IDs, source roles, settings schemas, result surfaces, and deep-link contracts.

**Gate:** Every exposed capability has one complete static route from typed request to materializer, result authority, and viewer. RFD3 and validator policy matches section 2.2.

### Phase P1: Shared authority reuse and target binding

**Work:** Verify and consume the exact companion-owned `bms.shared-global-package-acceptance.v1` receipt. Reverify every bound identity in that receipt, including direct attempt-to-preparation and launch-context v2 authority; canonical workflow/run routes; outer Domain v2 support; Project Dataset APIs/UI; artifact/log writers; query isolation; resource admission; pagination/response limits; health/backup/export fields; and payload manifest/scanner/audit. Harden target receipt selection and migrate existing CM ownership without changing IDs.

**Gate:** The complete shared-package receipt exists, matches the implementation source/tree and every retained artifact digest, and verifies without omission. Historical and fresh schemas attest; old migration checksums remain unchanged; target and Dataset revisions reopen exactly. Phase P2 cannot start while this gate is incomplete, stale, partial, or digest-divergent.

### Phase P2: Capability registry and Protein Domain workspace foundation

**Work:** Implement capability inventory/health, typed Protein Domain v2 controls, protein target/comparison UI, Protein In Silico workspace, protein Workflow Plan authoring, preparation/run-group controls, and protein projections over the shared Dataset and query-isolation services.

**Gate:** Free-text capabilities are gone. The workspace can create/revise intent, exact Dataset revisions, Workflow Plan revisions, preparations, and run groups without creating a Job. Every saved authority reopens exactly.

### Phase P3: Folding and structure-prediction closure

**Work:** Complete Boltz-2, ESMFold2, and Protenix V2 plan, prepare, launch, terminal receipt, result, viewer, and provenance paths.

**Gate:** Each accepted predictor has typed UI/API parity, exact artifacts, no fallback, and one current owner-path acceptance receipt.

### Phase P4: RFD3 De Novo Design, mutation/variant, and sequence-design closure

**Work:** Add specialized general RFD3 global adapters; place local redesign under De Novo Design; close the governed mutation/variant-set authority and `mutagenesis/batch_predict` path; close required sequence-design and validation handoffs; implement the sole LigandMPNN lane through Foundry with typed execution, immutable inputs, terminal result authority, adapter, and viewer.

**Gate:** General de novo, local redesign, and mutation/variant exploration can be planned, launched, reviewed, compared, and reopened with exact parent/variant/result lineage. Sequence design runs only when explicitly selected or required by the plan. LigandMPNN is executable through Foundry and is no longer a registry-only capability.

### Phase P5: CM, MD, and FrustraMPNN closure

**Work:** Move CM under Protein Domain ownership; add specialized MD preparation/materialization; wire Frustra analysis, comparisons, guidance, redesign handoff, and fresh outcome analysis into the Project graph. Correct the legacy CM importer/CLI contract and add the section 8.3.1 classification-authority adapter, immutable `domain_adapter_receipts` receipt, and DRT4 fail-closed labelling rule.

**Gate:** CM ensembles, MD replicas, Frustra landscapes, comparisons, and guidance preserve native authorities and canonical viewers. A caller-selected job ID cannot establish DRT4 identity; only a verified section 8.3.1 receipt can, and an unbound import remains `historical_cm_compatibility` after restart.

### Phase P6: Comparison, Dataset, statistics, and ELN closure

**Work:** Complete compatibility adapters, exact comparison Datasets, bounded cross-run projections, saved reviews, evidence attachment, and ELN records. Add protein producers and projections to the companion-owned global validation-artifact and bounded-attempt-log writers. Native scientific artifacts and full logs remain in their owning stores and attach through verified receipts.

**Gate:** Incompatible results remain visibly unavailable. Exact reference/target roles, digests, support, exclusion, and missingness survive restart.

### Phase P7: Experimental/historical inventory reconciliation

**Work:** Complete or hide every Fold-CP, shape/CAD, Protein Hunter, DISCO, La-Proteina, BoltzGen, RFdiffusion, RF3, AF2, and specialized legacy lane according to Phase P0 classification. AF2 cannot become a new Project predictor or validator. Shape Blueprint exposes its validator suite through typed UI/API authority. DISCO and La-Proteina gain typed terminal manifests and specialized result verification before any complete status.

**Gate:** No visible capability is registry-only, generic-fallback-only, or viewerless. Historical attachment remains exact.

### Phase P8: Current-tree verification and Development acceptance

**Work:** Run the approved focused suite, exact-tree review, Development deployment, service health checks, and bounded live/browser scenarios.

**Gate:** Definition of done passes at one exact commit/tree and deployed build identity.

## 18. Verification scope

Test and compute execution require separate approval. Once authorized, use the bounded evidence set below.

### 18.1 Backend contract checks

- Capability inventory completeness and exposure-state enforcement.
- Target stable identity, revision/digest, role, Project scope, and ambiguity rejection.
- Domain payload canonical capability and validation IDs.
- Outer/domain v1 byte-for-byte read compatibility and explicit operator-authored v1-to-v2 successor creation.
- Strict Protein v2 target, constraint, comparison, criterion, evidence, and exact Dataset-revision validation.
- Dataset canonical persisted membership, exact retrieval, digest binding, and head independence.
- Workflow Plan strict schema, settings parity, prepare-without-launch, and normalized digest.
- Static materializer coverage for every exposed adapter.
- Direct attempt-to-preparation authority on first launch and retry.
- Terminal receipt binding to plan, preparation, attempt, canonical Job/run, result contract, and artifacts.
- RFD3 general/local, mutation/variant-set, structure prediction, CM, MD, and Frustra specialized adapter contracts.
- CM-legacy import authority receipt creation and exact `domain_adapter_receipts` persistence; non-owner denial; wrong job/request/target/snapshot/candidate/digest/cardinality rejection; CLI rejection of `--job-id`; one-time DRT4 label assignment through `authority_receipt_id`; and restart reopening of unbound imports as `historical_cm_compatibility`.
- Mutation parent/digest/residue authority, deterministic variant identity, cardinality, predictor policy, child results, missingness, comparison, retry/replica distinction, and viewer reopening.
- Comparison compatibility, reference/target roles, missingness, and incompatible failure.
- Retry, resubmit, cancellation, restart, backup, and export.
- Retry with unchanged valid preparation, required successor preparation after changed authority, and direct attempt binding in both cases.
- Non-owner/non-operator denial, cross-hierarchy and foreign Dataset/receipt injection denial, strict unknown-field rejection, and caller-supplied path/digest/relationship rejection.
- Shared cursor/page/response-limit behavior and passing protein payload-ownership audit.

### 18.2 Mounted frontend checks

- Create and revise Protein In Silico intent with registry selectors.
- Attach an exact target and Dataset revision.
- Save, reopen, and prepare a plan without Job creation.
- Open each canonical launcher with restored settings and Project context.
- Return from ordinary Results, RFD3, CM, MD, and Frustra viewers.
- Create and reopen an exact mutation/variant plan, variant-set manifest, selected child predictor result, and parent/variant comparison.
- Distinguish scientific replicas from retries.
- Reopen exact historical Dataset, result, comparison, guidance, and ELN records.
- Switch authority contexts while deferred responses are pending.
- Render blocked, stale, failed, partial, incompatible, and unavailable states visibly.

### 18.3 Static and build checks

Use affected Python contract/migration checks, frontend type/build checks, exact capability-matrix validation, migration attestation, `git diff --check`, and focused security/static checks. Broad unrelated suites are outside this SOW.

### 18.4 Scientific qualification

Model qualification is separate from per-commit contract CI. Each accepted capability requires one retained, digest-bound owner-path receipt that proves:

- exact runtime/model/checkpoint/source identity;
- scheduler and GPU ownership;
- required native artifacts;
- terminal ingestion and persistence;
- canonical API and viewer rendering;
- the model-specific identity/cardinality claim.

An import smoke, registry listing, workflow parse, or synthetic fixture is insufficient.

## 19. Development live-acceptance scenarios

Use DRT4 `WP_031606642.1` as the standard protein fixture where scientifically suitable. Every compute run requires explicit approval and a frozen watched source tree.

### Scenario A: Folding and review

1. Prove Development commit/tree, listener owner, database paths, scheduler owner, and capability digest.
2. Create a Project, Global Experiment, and Protein In Silico Domain Experiment.
3. Attach the exact DRT4 source sequence/structure receipt.
4. Create an immutable target Dataset revision.
5. Save and prepare a structure-prediction plan without creating a Job.
6. Launch through an approved predictor using one launch context.
7. Observe canonical Job, run, attempt, GPU/runtime receipt, artifacts, and result contract.
8. Open the canonical structure result and return to the exact Project selection.
9. Run or attach FrustraMPNN analysis, create one compatible comparison when two exact sources exist, and record an observation.

### Scenario B: De novo and iterative lineage

1. Create a De Novo Design plan with RFD3 as the general/non-nanobody engine.
2. Launch one bounded candidate set.
3. Preserve native structures and request/result manifests.
4. Run an explicit sequence-design stage only when selected by the plan.
5. Validate candidates with an approved validator.
6. Open candidate and validation results in canonical viewers.
7. Attach a Frustra landscape or guidance record.
8. Create a successor redesign plan derived from exact prior authority.
9. Prove `derived_from`, `validated_by`, and retry/replica distinctions.

### Scenario C: Ensemble, simulation, and Project reopening

1. Run or use an approved current CM request with Project-owned source authority.
2. Review ensemble, residue maps, Frustra evidence, support, and missingness.
3. Run only the pinned `gmx_amber99sb_ildn_tip3p_smoke_v1` structure fixture: one GROMACS replica, 2 fs, 300 K, 1 bar, 0.15 M salt, 1.0 nm padding, and maxima of 50,000 minimization, 50,000 NVT, 50,000 NPT, and 5,000 production steps. The evidence is infrastructure-only. DRT4 is prohibited from this MD step. A DRT4 MD job is allowed only after the separately accepted DRT4 chemistry/topology and analysis receipt in section 7.8 exists.
4. Review actual replicas, trajectory/analysis authority, and canonical playback.
5. Save one exact result/comparison Dataset revision.
6. Record an evidence-linked decision and conclusion.
7. Restart API and frontend services.
8. Reopen the same Project, Domain revision, target Dataset, plans, runs, attempts, results, CM/MD/Frustra viewers, comparison, and ELN records.
9. Verify health, backup, export, and browser evidence against the exact deployed build.

### Scenario D: Mutation and variant exploration

1. Attach one exact parent sequence or structure receipt with chain/residue numbering authority.
2. Save one bounded library or manual mutation plan with explicit mutation policy, seed, expected cardinality, accepted predictor, and complete typed settings.
3. Prepare without a Job, then launch through the canonical mutagenesis surface and opaque Project context.
4. Verify the server-owned variant-set manifest, stable variant IDs, exact mutations/sequences, per-variant child results/failures, and aggregate cardinality.
5. Open one child structure result and one compatible parent/variant comparison, then return to the exact Project selection.
6. Retry one failed child without changing the scientific variant count. Reuse or supersede preparation authority according to section 8.1.
7. Restart and reopen the same parent, plan, variant set, child result, comparison, and lineage.

Retained historical records may prove attachment and old-result reopening. They do not replace current launch-context proof for an accepted capability.

## 20. Definition of done

The SOW is complete only when all statements are true:

- Protein Domain intent uses canonical capability IDs and verified target receipts.
- New Protein intent uses strict `bms.domain-experiment.v2` and `bms.protein-in-silico-experiment.v2`; historical v1 remains byte-for-byte readable and converts only through an explicit validated successor revision.
- RFD3 is the chief general/non-nanobody de novo engine in Project taxonomy.
- RFD3 local redesign appears as a De Novo Design child while historical routes and IDs remain readable.
- Mutation and variant exploration has exact parent, mutation-set, predictor, variant manifest, child result, comparison, lineage, and viewer authority. It cannot remain a browser-generated payload with generic batch routing.
- Boltz-2, ESMFold2, and Protenix V2 each support typed plan, prepare, launch, result, viewer, and provenance closure.
- Foundry is the sole LigandMPNN implementation.
- LigandMPNN has a real Foundry execution path, typed coordinate-bearing input authority, terminal result contract, Project adapter, and canonical viewer. An enabled registry row alone cannot satisfy this condition.
- Specialized antibody/nanobody workflows pass Phase P0 closure and retain their specialized semantics.
- CM, GROMACS MD, and FrustraMPNN use specialized global adapters where their authority exceeds a core Job.
- The five-candidate CM→FrustraMPNN compatibility result reopens through canonical persisted authority without uploads, generalized import claims, changed native IDs/provenance, or fresh-runtime claims. A DRT4 label is allowed only when the closed import-authority receipt in section 8.3.1 binds the exact result to `WP_031606642.1`; otherwise the result remains `historical_cm_compatibility`.
- MD acceptance stays inside the exact profile and numerical bounds in section 7.8. DRT4 MD remains unavailable until its separate chemistry/topology and analysis receipt passes.
- Every exposed protein capability has a materializer, terminal verifier, result contract, result adapter, and canonical viewer.
- Every accepted model passes all ten settings-parity gates. Shape Blueprint does not apply a hidden validator suite.
- Typed core and experimental generator result adapters verify the producer-native bytes required by their result contracts.
- Unqualified experimental or historical capabilities are explicitly classified and cannot enter an unrelated fallback.
- Preparation creates no work. Launch is explicit and idempotent.
- Every attempt binds directly to one immutable preparation. Retry may reuse an unchanged valid preparation; changed or stale authority requires an immutable `supersedes` successor before dispatch.
- Global Datasets persist exact ordered membership and reopen exact revisions.
- `derived_from` and `compared_with` follow the direction contract in this SOW.
- Scientific replicas remain distinct from retry attempts.
- Missing, stale, ambiguous, foreign, incompatible, or digest-divergent authority fails visibly.
- Placeholder data never crosses Project, Domain, plan, result, Dataset, or viewer contexts.
- GPU-capable plans honor typed workflow GPU selection. CPU and DRAM admission honor the 24-thread and 96-GiB aggregate limits.
- AF2 and AF3 cannot be launched as new Project predictors or validators. Historical exact receipts remain reopenable when native authority exists.
- Global-owned validation artifacts and bounded attempt logs have real writers and Project read projections. Native scientific payloads remain in their owning stores.
- Protein uses the companion-owned Dataset, artifact/log, query-isolation, pagination, health, backup, export, and payload-audit mechanisms without a parallel implementation.
- The retained shared payload-ownership audit proves that structures, trajectories, landscapes, metrics, manifests, and other canonical protein payloads have one active authority.
- Canonical viewers preserve exact Project return context and validate native result authority.
- ELN observations, decisions, and conclusions retain exact evidence links and immutable history.
- Operational health, backup, export, and restart recovery include the complete vertical.
- Approved focused current-tree checks pass after final edits.
- Independent exact-tree review finds no release-blocking specification or scientific-integrity defect.
- `test` is pushed, Development runs the exact accepted commit with one dispatcher/scheduler owner, and the approved live/browser scenarios pass.
- Production remains unchanged until separately authorized.

## 21. Likely implementation files

### Global backend

- `platform/api/model_registry.py`
- `platform/api/config/models/*.yaml` for classification and truthful exposure only
- `platform/api/experiment_models.py`
- `platform/api/experiment_migrations.py`
- `platform/api/experiment_services.py`
- `platform/api/experiment_operations.py`
- `platform/api/routers/projects.py`
- `platform/api/routers/project_manager.py`
- `platform/api/routers/experiment_workspaces.py`
- `platform/api/services/global_experiments/adapters.py`
- `platform/api/services/global_experiments/receipts.py`
- `platform/api/services/global_experiments/read_models.py`
- `platform/api/services/global_experiments/result_surfaces.py`
- `platform/api/services/global_experiments/launch_contexts.py`
- `platform/api/services/global_experiments/worker.py`
- `platform/api/services/result_contracts.py`
- `platform/api/main.py`

### Canonical protein authorities

- `platform/api/services/nextflow.py`
- `platform/api/routers/jobs.py`
- `platform/api/services/rfd3_local_redesign.py`
- general RFD3 request/result services and scripts identified in Phase P0
- `platform/api/services/conformational_mapping/`
- `platform/api/routers/conformational_mapping.py`
- `platform/api/services/md/`
- `platform/api/routers/molecular_dynamics.py`
- `platform/api/routers/md_results.py`
- `platform/api/services/frustrampnn/`
- `platform/api/routers/frustrampnn.py`
- `platform/api/services/result_ingester.py`
- participating workflow and module files only where adapter closure requires a real native contract

### Frontend

- `platform/frontend/src/lib/api.ts`
- `platform/frontend/src/pages/ProjectManager.tsx`
- `platform/frontend/src/components/project-manager/ManagerDialog.tsx`
- a new Protein In Silico domain workspace under `platform/frontend/src/components/`
- `platform/frontend/src/components/JobSubmission.tsx`
- `platform/frontend/src/components/ResultsViewer.tsx`
- `platform/frontend/src/components/RFD3LocalRedesignResultsPane.tsx`
- `platform/frontend/src/components/conformationalMapping/`
- `platform/frontend/src/components/MDResultsPane.tsx`
- `platform/frontend/src/components/frustrampnn/`
- `platform/frontend/src/components/project-manager/ProjectReturnBanner.tsx`

### Focused tests

- `platform/api/tests/test_project_manager_hierarchy.py`
- `platform/api/tests/test_project_manager_adapters.py`
- `platform/api/tests/test_project_manager_domain_adapters.py`
- `platform/api/tests/test_project_manager_read_models.py`
- `platform/api/tests/test_experiment_workspace_meta_layer.py`
- current RFD3, structure-prediction, result-contract, CM, MD, FrustraMPNN, capability, and migration test owners
- `platform/frontend/tests/vitest/projectManagerPage.test.tsx`
- `platform/frontend/tests/vitest/projectManagerApi.test.ts`
- mounted Protein In Silico workspace and launcher-return tests added to the established Vitest configuration

## 22. Release boundary

Completion produces a Development candidate on `test`. The release packet records commit, tree, capability inventory digest, migration ledgers, database paths, listener/process owners, dispatcher/scheduler identity, GPU/runtime receipts for approved runs, focused check output, backup/export receipts, and browser evidence.

Production promotion remains a separate SOW or explicit operator action.
