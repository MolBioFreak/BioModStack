# Global MPNN-family scientific capability layer implementation plan

> **For Hermes:** implement this plan in the listed vertical slices. Do not expand it into a generic plugin system. Every production claim requires the exact focused contract check and live owner-path evidence declared here; broad repository test campaigns are explicitly excluded.

**Status:** implementation-ready specification for all remaining work  
**Source branch:** `feat/global-model-analysis-config-20260802`  
**Source pin:** `18b7e4c282a1aba0f9c6816e0eb716e3bd0b64a3`  
**Base pin:** `origin/test` at `d7fbd82e009bab00ee860364471f0a35a4893a66`  
**Open implementation PR:** [#52](https://github.com/MolBioFreak/BioModStack/pull/52)  
**Target branch:** `test`; `main` and production remain owner-controlled  
**Canonical Development checkout:** `/home/dalab/biomodstack/dev-test-canonical`

**Goal:** finish a small, globally configured capability layer for FrustraMPNN and the MPNN family, then integrate each model through its scientifically correct workflow surfaces with scheduler-owned execution, immutable artifacts, exact lineage, native result contracts, and concise enabled-only UI context.

**Architecture:** extend the existing model registry with one narrow integration/presentation contract and one safe exposure flag. Workflow cards consume those global defaults and descriptions, while each model retains a model-specific request, adapter, manifest, and result schema. Existing scheduler, Nextflow, result ingestion, artifact routes, and viewers are reused; there is no generic model runner, plugin SDK, dynamic Python import, second scheduler, or browser-selected GPU.

**Technology:** FastAPI/Pydantic registry, existing SQLAlchemy job state, existing Nextflow scheduler adapter, Apptainer model images, React/TypeScript/TanStack Query, existing governed artifact APIs and Mol*.

---

## 1. Product decisions and non-negotiable semantics

1. The operator label is **Frustration analysis**, not “FrustraMPNN QC”.
2. FrustraMPNN is a structure-conditioned scientific analysis capability. Its valid uses include residue-level frustration mapping, interpretation of predicted structures, substitution-landscape inspection, and scientifically guided mutagenesis. It is a QC input only when a parent workflow explicitly defines such a policy.
3. Stable model identity, scientific description, runtime/checkpoint labels, safe exposure, resource-profile reference, and workflow defaults are globally owned.
4. Workflow cards own whether a capability is offered, whether the operator requested it for a job, workflow-specific context, and the exact request parameter compiled into the parent workflow.
5. Scientific numerical meaning is backend-owned. Frontends display typed scores/classes and policy metadata; they do not recreate FrustraMPNN thresholds, FA-MPNN pSCE formulas, ThermoMPNN ΔΔG interpretations, or model ranking policy.
6. Only the scheduler assigns a physical GPU. A registry or browser may reference a resource profile but may never select `CUDA_VISIBLE_DEVICES` or a physical GPU ID.
7. Every enabled model invocation is fail closed. Disabled stages persist an explicit canonical `not_requested`/empty-output pair where the parent contract requires one.
8. A globally declared model is not automatically a directly launchable model. `public_launch: false` is required for embedded components and for any model whose standalone request→runtime→artifact contract is incomplete.
9. The shared layer is metadata and workflow-integration infrastructure only. Runtime adapters and result payloads remain model-specific.
10. No model may manufacture placeholder success artifacts, write `N/A`/`ERROR` rows while exiting successfully, infer residue authority from a filename, or treat loose recursive file discovery as result authority.

## 2. Explicit scope

### 2.1 Required in this specification

- Finish and land PR #52’s global integration schema, safe-exposure behavior, FrustraMPNN registry entry, shared frontend control, and neutral wording.
- Reconcile the earlier FrustraMPNN universal-component implementation with the global-config-first product architecture.
- Migrate every currently reachable FrustraMPNN operator surface to central configuration and enabled-only information.
- Complete one genuine governed FrustraMPNN owner path through card → API → scheduler → Nextflow → GPU → manifest → ingestion → API → Mol*.
- Apply the accepted pattern to ProteinMPNN, LigandMPNN, FA-MPNN, and ThermoMPNN.
- Correct false registry/runtime claims before exposing those models.
- Add native model contracts and minimal review surfaces for each family member.
- Retire direct/fail-open/placeholder paths only after the replacement path is live.

### 2.2 Explicitly excluded

- A generalized plugin marketplace, model SDK, dynamic entrypoint loader, universal parameter DSL, or new orchestration service.
- A launcher card for every internal stage.
- Automatic model chaining based only on category or capability tags.
- Browser-selected physical GPU identity.
- Cross-product ranking formulas or a universal “quality score”.
- New biological efficacy claims from small qualification fixtures.
- Full-repository, full-browser-matrix, load, compliance, or stochastic regression campaigns.
- Promotion to `main` or production.

## 3. Current source truth

### 3.1 Strong mechanisms already present

- FrustraMPNN already has a scheduler-owned canonical Nextflow component in `modules/frustrampnn.nf`, a typed service package under `platform/api/services/frustrampnn/`, immutable request/result/receipt/manifests, exact author-residue mapping, child jobs, governed artifact routes, and `FrustraMpnnResultsViewer.tsx`.
- Exact disabled-state ingestion hardening is already merged into `test`.
- PR #52 adds `public_launch`, global `ModelIntegrationConfig`, a safe `/api/models/{model_id}/integration` endpoint, `frustrampnn.yaml`, and `ModelIntegrationControl.tsx`.
- ProteinMPNN, LigandMPNN, and FA-MPNN already have registry YAML files.
- ProteinMPNN, FA-MPNN, and ThermoMPNN have existing Nextflow modules.
- ProteinMPNN and FA-MPNN are already nested in antibody/protein/Shape workflows.
- Result ingestion, job lineage, model registry, scheduler profiles, governed artifact routes, and generic viewer infrastructure already exist and must be reused.

### 3.2 Gaps that bind implementation

- PR #52 is open and deliberately untested/unmerged.
- Only Structure Prediction currently consumes the global integration control/default.
- Antibody de novo has duplicated FrustraMPNN toggles and locally hard-coded wording.
- Other FrustraMPNN actions/results still need to consume global presentation metadata consistently.
- `result_contracts.py` incorrectly groups FrustraMPNN into `sequence_design_v1`; FrustraMPNN does not design a sequence.
- ProteinMPNN/LigandMPNN/FA-MPNN registry declaration does not itself prove standalone launch closure.
- `resolve_nextflow_entrypoint()` has no explicit direct model-mode route for ProteinMPNN, LigandMPNN, or FA-MPNN; unknown entries fall back to `workflows/protein_design.nf`.
- No `modules/ligandmpnn.nf` exists in the inspected tree.
- LigandMPNN’s registry advertises `ntp_type`, `metal_type`, `dna_sequence`, and SMILES-like context, but state-of-the-art ligand-aware sequence design requires explicit coordinate-bearing structural context. These declarations must not imply that names/SMILES alone atomically materialize a valid model context.
- ThermoMPNN has no global model registry entry and `modules/thermompnn.nf` creates placeholder CSVs on missing output or errors. That behavior is scientifically invalid and must be removed before capability claims.
- Existing MPNN result handling is too generic: native FASTA, designed/fixed masks, checkpoint identity, model-native scores, sidechain confidence, ligand context, and mutation ΔΔG are not uniformly manifest-authoritative.
- Existing residue constraints may traverse PDB remarks/B-factors or ad hoc strings; final model input must prove the exact designed/fixed residue set using author identity including insertion code.

## 4. Minimal shared global contract

Modify `platform/api/model_registry.py` only; do not create a second registry.

### 4.1 `ModelDefinition`

Retain PR #52 fields:

```python
public_launch: bool = True
integration: Optional[ModelIntegrationConfig] = None
```

Add only one further field needed by all five models:

```python
runtime_profile: Optional[str] = None
```

The human-readable checkpoint label remains in `ModelIntegrationConfig`; do not duplicate it at top level. Do not expose host paths, image paths, checkpoint paths, digests, physical GPU IDs, or command-line arguments in the public registry response. Exact runtime identity belongs in backend readiness and execution receipts.

### 4.2 `ModelIntegrationConfig`

Final contract:

```python
class WorkflowModelIntegration(BaseModel):
    default_enabled: bool = False
    enabled_summary: str

class ModelIntegrationConfig(BaseModel):
    stage_parameter: str
    operator_label: str
    model_summary: str
    checkpoint_label: Optional[str] = None
    semantic_roles: list[str]
    workflows: dict[str, WorkflowModelIntegration]
```

Binding rules:

- `stage_parameter` is a canonical backend request key, not an arbitrary browser field.
- Every workflow key must correspond to a known workflow/product ID.
- Registry load fails when an integration has an empty label/summary, duplicate semantic role, unknown workflow, or a stage parameter not allowlisted for that model.
- Workflow defaults are product defaults only. Saved templates or an explicit boolean in a job request override them.
- Loading configuration must never mutate a saved job or re-evaluate a historical request.

### 4.3 Safe public API

Files:

- Modify: `platform/api/routers/models.py`
- Modify: `platform/frontend/src/lib/api.ts`

Requirements:

- `GET /api/models` and `GET /api/models/{id}` return only `enabled && public_launch` models.
- `GET /api/models/{id}/integration` returns the bounded presentation/integration projection for enabled internal or public models.
- Direct launch validation uses the same `get_model()` filter and rejects internal components.
- No private runtime path or hash leaks through the integration endpoint.
- Unknown integration IDs return 404; malformed registry configuration makes readiness fail rather than disappearing silently.

## 5. Final global model records

Files:

- Modify: `platform/api/config/models/frustrampnn.yaml`
- Modify: `platform/api/config/models/proteinmpnn.yaml`
- Modify: `platform/api/config/models/ligandmpnn.yaml`
- Modify: `platform/api/config/models/fampnn.yaml`
- Create: `platform/api/config/models/thermompnn.yaml`

### 5.1 FrustraMPNN

- Category: `scientific_analysis`.
- `public_launch: false`; scheduler-backed analyze/reanalyze remains a governed artifact action.
- Label: `Frustration analysis`.
- Summary: maps residue-level energetic frustration and substitution landscapes for structure interpretation and mutation planning.
- Roles: `structure_interpretation`, `mutagenesis_guidance`, `workflow_specific_quality_control`.
- Checkpoint label: `MegaScale-trained checkpoint`.
- Stage parameter: `run_frustrampnn`.
- Enabled path is required/fail closed.
- No sequence-design result contract.

### 5.2 ProteinMPNN

- Category: `sequence_design`.
- Initially `public_launch: false` until the standalone vertical slice is accepted.
- Label: `Protein sequence design`.
- Roles: `fixed_backbone_sequence_design`, `binder_sequence_design`.
- Inputs: immutable coordinate structure plus typed design/fixed mask and chain/context roles.
- Native outputs: FASTA, sequence scores/log probabilities, typed designed/fixed mask, seed/temperature/design index, optional model-native designed structure.
- No ligand-awareness claim.

### 5.3 LigandMPNN

- Category: `sequence_design`.
- `public_launch: false` until its dedicated adapter and coordinate-context contract are accepted.
- Label: `Context-aware sequence design`.
- Roles: `ligand_aware_sequence_design`, `cofactor_aware_sequence_design`, `nucleic_acid_context_sequence_design`, `metal_context_sequence_design` only when explicit coordinates and atom/context roles exist.
- A ligand name, SMILES, nucleotide enum, metal enum, or DNA sequence is metadata; it never substitutes for required coordinates.
- Remove or hide any registry mode that cannot materialize and validate the required atomic context before scheduling.

### 5.4 FA-MPNN

- Canonical display: `FA-MPNN`; canonical ID remains `fampnn` for compatibility.
- Initially `public_launch: false` until standalone closure is accepted.
- Label: `Full-atom sequence design`.
- Roles: `full_atom_sequence_design`, `sidechain_aware_design`, `binder_sequence_design`.
- Native outputs: designed structures, FASTA, sequence scores, per-residue pSCE with explicit units/definition, exact mask and lineage.
- pSCE interpretation/filter policy is backend/parent-owned and versioned; no duplicate frontend threshold.

### 5.5 ThermoMPNN

- Category: `scientific_analysis`, not sequence design.
- `public_launch: false` initially.
- Label: `Mutation stability scoring`.
- Roles: `mutation_ddg_scoring`, `stability_prioritization`.
- Inputs: immutable structure and an explicit bounded mutation set.
- Native outputs: one typed row per requested mutation with exact author residue identity, WT, mutant, finite predicted ΔΔG, model/checkpoint identity, units/sign convention, status and missingness.
- It is a computational estimate, not an experimental stability measurement and not a universal acceptance gate.

## 6. Shared frontend behavior

Files:

- Modify: `platform/frontend/src/components/ModelIntegrationControl.tsx`
- Modify: `platform/frontend/src/components/StructurePredictionTemplate.tsx`
- Modify: `platform/frontend/src/components/AntibodyDenovoTemplate.tsx`
- Modify: `platform/frontend/src/components/ResultsViewer.tsx`
- Modify only where reachable: relevant Protein Design/Complex Prediction controls in `JobSubmission.tsx`
- Modify: `platform/frontend/src/components/modelDocumentationRegistry.ts`
- Modify: `platform/frontend/src/components/workflowModelInventory.ts`

Requirements:

1. One shared query/hook caches the global integration record.
2. A control receives model ID, workflow ID, checked state, and change callback.
3. Disabled: show the concise operator label only. No model/checkpoint/scientific block.
4. Enabled: show a compact highlighted block containing canonical model name, checkpoint label when configured, and the workflow-specific enabled summary.
5. Loading/error: keep the stable fallback label and never alter an explicit saved/user selection. Absence of presentation metadata must not alter backend execution semantics.
6. Apply a configured default once only when no saved/template/request boolean exists. A delayed query must never overwrite an operator click.
7. Do not expose physical GPU selection.
8. No frontend numerical classification thresholds.
9. FrustraMPNN results use the dedicated viewer and exact `(auth_asym_id, auth_seq_id, insertion_code)` mapping.
10. Remove remaining user-visible “FrustraMPNN QC” wording. Context-specific quality-control prose may describe a parent policy but cannot rename the model capability.

Endpoint migration order:

1. Structure Prediction — finish PR #52 behavior.
2. Antibody de novo/refinement — replace both duplicated toggles with one shared control in the scientifically correct panel; preserve preset/lock behavior and stale-post-maturation rejection.
3. Results Viewer rerun/action surface — consume global label/context and preserve immutable selected artifact lineage.
4. Protein Design and Complex Prediction — expose the shared control only where the backend parent already has a canonical FrustraMPNN route.
5. Conformational Mapping — keep its dedicated scientific UI; consume central model/checkpoint display metadata without replacing CM-owned interpretation/ranking.

## 7. Model-specific runtime contract pattern

Reuse only these conventions:

- stable parent/job/candidate/artifact identity;
- scheduler-owned execution and physical GPU assignment;
- immutable request, receipt, native artifacts, result and manifest;
- exact model/checkpoint/image/executable identity in the receipt;
- explicit requested/required/not-requested/failed/succeeded states;
- manifest-first ingestion;
- parent-owned scientific policy.

Do not share FrustraMPNN’s landscape parser, thresholds, or residue summaries with designers/scorers.

Each model adapter must have:

```text
platform/api/services/<model>/contracts.py
platform/api/services/<model>/runtime.py
platform/api/services/<model>/manifests.py
scripts/run_<model>_component.py
modules/<model>.nf
```

Existing files are modified in place. Create only missing packages/files needed by an accepted model slice; do not pre-create empty abstractions for all models.

Canonical component output:

```text
final/<model>/<invocation_id>/
├── workflow_component_request_v1.json
├── model_native_outputs...
├── <model>_execution_receipt_v1.json
├── workflow_component_result_v1.json
└── <model>_result_manifest_v1.json
```

## 8. Sequence-design request and result contracts

ProteinMPNN, LigandMPNN and FA-MPNN share envelope conventions but have model-specific payload schemas.

### 8.1 Request invariants

Required:

- parent job/workflow/candidate/invocation IDs;
- source structure relative path, SHA-256, media type and producer stage;
- exact structure identity authority;
- explicit design chains/context chains;
- typed residue mask rows keyed by `(auth_asym_id, auth_seq_id, insertion_code)` and state `designable` or `fixed`;
- model-native seed, sampling temperature and requested design count;
- model/checkpoint ID;
- requiredness.

The adapter translates the typed mask to model-native files only at the final runtime boundary, then records and reopens the translated file. PDB B-factors, remarks, basename conventions, or ambiguous one-based strings are not authority.

### 8.2 Result invariants

Successful result requires:

- exact requested and observed sequence cardinality;
- native FASTA retained;
- one typed result row per generated sequence;
- seed, temperature, batch/design index;
- sequence and model-native score/log probability;
- exact designed/fixed mask hash;
- source structure hash;
- model/checkpoint/image/executable identity;
- optional designed PDB identity when the engine emits one;
- no silent truncation, partial-success promotion or recursive loose-file authority.

### 8.3 LigandMPNN additions

- Exact coordinate-bearing non-protein/nucleic-acid context inventory.
- Context atom/residue identities and source hashes.
- Model-native context/ligand mask.
- Explicit rejection before scheduling for missing, ambiguous or fabricated context.
- Mode claims must match the actual adapter/runtime invocation atomically.

### 8.4 FA-MPNN additions

- Designed structure hash and sequence correspondence.
- Per-residue pSCE rows mapped to author identity.
- Exact definition/units/version of pSCE.
- Missing sidechain-score reasons are explicit; glycine/other unsupported rows are not zero-filled.

## 9. ThermoMPNN contract and required rewrite

Modify `modules/thermompnn.nf` in place.

Remove all behavior that:

- creates a header-only or `N/A` CSV when no model output exists;
- creates an `ERROR` CSV and allows the pipeline to continue as success;
- discovers output by a broad unscoped glob;
- suppresses nonzero exit status.

Implement:

- explicit typed request with a bounded mutation set;
- exact command construction in `scripts/run_thermompnn_component.py`;
- nonzero model exit propagation;
- strict native output selection under one isolated work root;
- cardinality and identity validation against requested mutations;
- finite numerical ΔΔG only, with explicit units and sign convention from the model adapter;
- immutable receipt/result/manifest;
- optional parent policy outside the adapter.

If the installed runtime cannot produce a trustworthy per-mutation contract, keep `public_launch: false` and hide workflow invocation rather than fabricating capability.

## 10. Result-contract corrections

Modify `platform/api/services/result_contracts.py`.

Required definitions:

- Remove `frustrampnn` from `sequence_design_v1`.
- Add `frustration_analysis_v1` with required canonical manifest, structure map, landscape, summary and receipt; viewer capabilities include exact residue landscape and Mol* mapping.
- Split sequence-design capability so ProteinMPNN/FA-MPNN/LigandMPNN require FASTA, typed design result, source/mask lineage and receipt instead of structure-only success.
- Add `mutation_stability_scoring_v1` for ThermoMPNN.
- Historical rows remain readable but cannot be upgraded into exact modern contracts by inference or filename matching.

Update `platform/api/services/result_ingester.py` only through manifest-first handlers. Each handler validates path containment, hashes, schema IDs, cardinality, parent identity and idempotency before a transaction commits.

## 11. Scheduler and workflow wiring

Files likely modified per model:

- `platform/api/services/nextflow.py`
- `platform/api/services/workflow_adapter.py`
- `platform/api/services/gpu_config.py`
- `nextflow.config`
- `conf/gpu.config`
- `workflows/protein_design.nf`
- `workflows/antibody_denovo.nf`
- `workflows/complex_prediction.nf`
- `workflows/structure_prediction.nf`
- `workflows/shape_blueprint_design.nf`
- model-specific child workflow only when a persisted artifact action needs one

Rules:

- Every advertised `(model_id, mode)` maps to a literal known workflow entrypoint and model adapter. No fallback to `workflows/protein_design.nf` may masquerade as standalone support.
- Internal-only workflow stages are not direct-launch models.
- One explicit scheduler resource profile per runtime family; aliases may share a profile only when the inspected image/CLI/checkpoint contract is identical.
- Scheduler writes assigned physical GPU into launch parameters/receipts; Nextflow task receives only its assigned device namespace.
- Disabled stages schedule zero model tasks and persist exact not-requested state where required.
- Enabled required failures prevent parent terminal success.
- Parent workflows preserve exact producer/candidate identity and do not deduplicate by basename.

## 12. Implementation slices and commit gates

### Slice 0 — source reconciliation and PR #52 completion

**Objective:** land the smallest valid global configuration foundation.

Files:

- `platform/api/model_registry.py`
- `platform/api/routers/models.py`
- `platform/api/config/models/frustrampnn.yaml`
- `platform/frontend/src/lib/api.ts`
- `platform/frontend/src/components/ModelIntegrationControl.tsx`
- `platform/frontend/src/components/StructurePredictionTemplate.tsx`

Steps:

1. Rebase PR #52 onto current `origin/test`; re-read every conflicted file.
2. Add registry validation/allowlists and prevent late default resolution from overriding a user click.
3. Add `runtime_profile` only if needed by the final YAML; keep the checkpoint label solely inside `ModelIntegrationConfig`.
4. Run the minimal contract checks in §13.
5. Commit and obtain exact-tree review.
6. Merge to `test`; deploy canonical Development only after the gate passes.

Gate: safe public exposure, central defaults/presentation, neutral wording, and unchanged scheduler ownership.

### Slice 1 — FrustraMPNN product convergence

**Objective:** finish global-config consumption and one real scientific owner path.

Files:

- endpoint files in §6;
- `platform/api/services/result_contracts.py`;
- existing FrustraMPNN service/module/ingester only for defects encountered;
- `scripts/accept_frustrampnn_phase6.py` reduced to the declared live cases rather than a ten-case mandatory campaign.

Steps:

1. Correct result-contract classification.
2. Migrate reachable UI controls to central metadata.
3. Preserve exact disabled state and fail-closed enabled behavior.
4. Submit one Structure Prediction job with Frustration analysis enabled.
5. Verify scheduler GPU receipt, canonical manifests, exact 1UBQ cardinality when using that fixture, ingestion, API and Mol* mapping.
6. Retain one intentional failure receipt only if existing evidence is stale for the accepted build.

Gate: one exact Development build produces a visible genuine frustration map through the owner path.

### Slice 2 — ProteinMPNN first-class component

**Objective:** establish the sequence-design request/result contract with the simplest mature MPNN runtime.

Files:

- modify `modules/proteinmpnn.nf`;
- create only missing `platform/api/services/proteinmpnn/*` files;
- create `scripts/run_proteinmpnn_component.py`;
- modify registry, result contracts, ingester and one existing parent workflow.

Gate: one immutable PDB plus typed mask produces the exact requested number of FASTA designs with complete lineage; fixed residues are unchanged.

### Slice 3 — FA-MPNN component

**Objective:** adapt the sequence-design contract to full-atom outputs without universalizing ProteinMPNN assumptions.

Files:

- modify `modules/fampnn.nf` and existing FA-MPNN analyzers;
- create only missing `platform/api/services/fampnn/*` files;
- create `scripts/run_fampnn_component.py` if no canonical adapter exists;
- update one existing antibody or Shape parent.

Gate: one real output preserves sequence/structure pairing, exact mask and valid per-residue pSCE/missingness.

### Slice 4 — LigandMPNN truthful capability

**Objective:** implement coordinate-context-aware design or hide unsupported claims.

Files:

- create `modules/ligandmpnn.nf` only after inspected runtime CLI closure;
- create `platform/api/services/ligandmpnn/*` and `scripts/run_ligandmpnn_component.py`;
- correct `ligandmpnn.yaml` modes/parameters;
- add an explicit model-mode route only when accepted.

Gate: one real coordinate-bearing protein/context complex produces designs while preserving the context and exact fixed/design mask. If runtime closure fails, the accepted implementation is truthful non-exposure, not a placeholder.

### Slice 5 — ThermoMPNN fail-closed scoring

**Objective:** replace placeholder stability outputs with a typed mutation-scoring capability.

Files:

- modify `modules/thermompnn.nf`;
- create `platform/api/services/thermompnn/*`;
- create `scripts/run_thermompnn_component.py`;
- create `thermompnn.yaml`;
- update antibody Results Viewer only after typed ingestion exists.

Gate: exact requested mutation set equals exact finite result set; model failure publishes no success manifest and parent state is truthful.

### Slice 6 — retirement and consolidation

**Objective:** remove contradicted paths after all accepted replacements exist.

Remove/retire:

- fallback direct model routing that implies unsupported standalone MPNN support;
- placeholder ThermoMPNN output behavior;
- duplicate FrustraMPNN wording/defaults and frontend thresholds;
- loose-glob native result authority;
- duplicate runtime/checkpoint declarations;
- model-specific direct Apptainer subprocesses outside canonical adapters/probes.

Gate: repository negative scans find no forbidden production path; historical reads remain intact.

## 13. Minimal proportional verification

The user explicitly requested minimal tests. This section is the complete required denominator; implementers must not silently expand it.

### 13.1 Per-commit contract checks

Use five focused suite owners, adding parameterized cases rather than many files:

1. `platform/api/tests/test_model_registry.py`
   - safe `public_launch` filtering;
   - integration endpoint projection;
   - invalid workflow/stage-parameter configuration rejection.
2. `platform/api/tests/test_mpnn_component_contracts.py`
   - request/result/manifest closure;
   - model-specific cardinality and finite-value checks;
   - explicit malformed-case table.
3. `platform/api/tests/test_mpnn_routing_and_parent_state.py`
   - exact entrypoint/adapter route;
   - zero scheduling when disabled;
   - fail-closed enabled state;
   - exact not-requested pair where applicable.
4. `platform/api/tests/test_mpnn_result_ingestion.py`
   - one success bundle/model;
   - hash/path/cardinality rejection table;
   - idempotent replay.
5. `platform/frontend/tests/modelIntegrationControl.test.tsx` plus the existing static Structure Prediction contract
   - disabled hides details;
   - enabled shows model/checkpoint/context;
   - delayed config does not overwrite explicit choice;
   - no QC label regression.

Run only changed-owner tests plus syntax/type compilation needed for changed files. Do not run the broad backend or frontend repository suites unless one of these focused checks reveals a cross-cutting defect.

### 13.2 Live owner-path acceptance

One real deployed owner path per capability, not a combinatorial matrix:

- FrustraMPNN: Structure Prediction → exact map/landscape/Mol*.
- ProteinMPNN: one existing sequence-design parent → FASTA/mask/results.
- FA-MPNN: one antibody or Shape parent → designed structure/pSCE.
- LigandMPNN: one coordinate-bearing ligand/cofactor complex → context-preserving designs.
- ThermoMPNN: one bounded mutation set → typed ΔΔG table.

Each packet records exact build SHA, request/input hashes, image/executable/checkpoint identity, scheduler/GPU receipt, command argv, native output hashes/cardinality, manifest, ingestion projection, and one browser screenshot or governed API receipt.

### 13.3 Scientific qualification, not CI

- FrustraMPNN 1UBQ: exactly `76 × 20 = 1,520` unique finite slots and one native slot per residue.
- Sequence designer: one fixture with fixed and designable residues, insertion code or controlled exact-map equivalent; fixed residues remain identical.
- LigandMPNN: one genuine coordinate context; no efficacy claim.
- FA-MPNN: pSCE values map to correct author residues and missingness remains explicit.
- ThermoMPNN: requested mutation count equals finite result count and sign/units are reported correctly.

No repeated stochastic campaign is required for ordinary source edits.

## 14. Acceptance definition

The complete specification is implemented only when:

- the global registry is the single source of stable model/integration metadata;
- public launch exposure is truthful and fail closed;
- every reachable FrustraMPNN control uses neutral “Frustration analysis” language and shows model context only when enabled;
- FrustraMPNN has one genuine governed Development owner-path result with exact Mol* author-residue mapping;
- ProteinMPNN, LigandMPNN, FA-MPNN and ThermoMPNN each have either an accepted first-class typed runtime path or are explicitly non-exposed without false capability claims;
- sequence-design masks, ligand/context identities, pSCE rows and ΔΔG rows preserve exact scientific identity;
- all model execution is scheduler-owned and manifest-first;
- placeholder success, direct request-thread inference, loose-file authority and physical-GPU browser selection are absent;
- minimal focused tests and one live path per accepted capability pass on the exact code pushed to `origin/test`;
- `main` and production remain untouched until Christian promotes.

## 15. Completion accounting

Report separately:

- **Specification/contract:** complete when this document is reconciled with the current source and approved.
- **Global mechanism:** PR #52 landed and Development-deployed.
- **FrustraMPNN integration:** all reachable controls plus one live owner path.
- **ProteinMPNN:** typed component and one live path.
- **FA-MPNN:** typed component and one live path.
- **LigandMPNN:** truthful coordinate-aware path or explicit non-exposure.
- **ThermoMPNN:** fail-closed typed scorer and one live path.
- **Optional/deferred:** broader scientific campaigns, extra endpoint convenience controls, production promotion.

Documentation completion is never counted as runtime implementation. Explicitly deferred optional work is not counted against required completion.