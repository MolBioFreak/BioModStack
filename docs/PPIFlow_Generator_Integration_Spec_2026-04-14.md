# PPIFlow Generator Integration Spec

Date: 2026-04-14

## Goal

Add `PPIFlow` as a third generator in the **De Novo Nanobody Toolkit** under the same high-level architecture now used for RFantibody and BoltzGen:

1. generator batch
2. interactive review / shortlist
3. reopen selected outputs into the shared `Antibody Refinement` loop

The existing **PPIFlow refinement role** must remain intact. This work is about adding a generator entry, not replacing the current refinement-stage PPIFlow behavior.

## Key Constraint

PPIFlow is not target-only in the way RFantibody or BoltzGen are.

Based on the live implementation:

- [modules/ppiflow.nf](/home/dalab/biomodstack/biomodstack/modules/ppiflow.nf) expects a **complex PDB** with antibody and antigen chains already present.
- [scripts/prepare_ppiflow_maturation.py](/home/dalab/biomodstack/biomodstack/scripts/prepare_ppiflow_maturation.py) identifies anchors and movable regions from an existing antibody-antigen interface.
- [workflows/maturation_child_core.nf](/home/dalab/biomodstack/biomodstack/workflows/maturation_child_core.nf) runs `IdentifyAnchorResidues -> RunPartialFlow` on an input complex list.

So the safe interpretation is:

- **RFantibody** = target-first generator
- **BoltzGen** = target-first generator
- **PPIFlow** = **seeded generator**

PPIFlow generator mode therefore needs a **seed complex intake**, not just `target_pdb + epitope`.

## Product Shape

Add a third generator called:

- `PPIFlow Seeded`

This should appear beside `RFantibody Stack` and `BoltzGen Nanobody` in [AntibodyDenovoTemplate.tsx](/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/AntibodyDenovoTemplate.tsx).

The semantics:

- user provides one or more **seed antibody-target complex PDBs**
- toolkit runs PPIFlow partial-flow in **generator mode**
- toolkit pauses at a new review gate
- user saves / filters / clusters / selects a cohort
- selected outputs reopen in `Antibody Refinement`

This preserves the current generator/refiner split.

## Non-Goals

Do not do these in the first pass:

- do not make PPIFlow a target-only generator from just antigen structure
- do not remove or rename current PPIFlow refinement modes inside `Antibody Refinement`
- do not overload `post_rfantibody` or `post_fampnn` to fake PPIFlow generator review
- do not create a second refinement contract for PPIFlow outputs

## Chosen Artifact Semantics

Use explicit generator-stage metadata instead of pretending the new path is the same as refinement-stage PPIFlow.

Recommended stage identity for generator outputs:

- `stage_family = "ppiflow"`
- `stage_mode = "generator_backbone_refine"`

Recommended review gate:

- `awaiting_stage = "post_ppiflow_generator"`

Artifact class:

- generator outputs should be treated as `backbone_complex`

Reason:

- these outputs should feed sequence design and downstream refinement like RFantibody backbones
- using a distinct `generator_backbone_refine` mode avoids lineage confusion with existing refinement-stage `backbone_refine` / `post_ppiflow`

## Existing Code Reuse

This is not a net-new science stack. Most of the runtime already exists.

Reusable pieces:

- [modules/ppiflow.nf](/home/dalab/biomodstack/biomodstack/modules/ppiflow.nf)
- [workflows/maturation_child_core.nf](/home/dalab/biomodstack/biomodstack/workflows/maturation_child_core.nf)
- [workflows/maturation_child.nf](/home/dalab/biomodstack/biomodstack/workflows/maturation_child.nf)
- [scripts/prepare_ppiflow_maturation.py](/home/dalab/biomodstack/biomodstack/scripts/prepare_ppiflow_maturation.py)
- [modules/utils/anarci.nf](/home/dalab/biomodstack/biomodstack/modules/utils/anarci.nf)
- the existing review / shortlist / reopen machinery already used for BoltzGen and RFantibody

What is missing is mostly:

- a generator-specific workflow wrapper
- a seed-complex input contract
- a new review gate identity
- a UI surface for the third generator

## Required Input Contract

### MVP

PPIFlow generator mode should require one of:

1. a single uploaded seed complex PDB
2. a directory of seed complex PDBs
3. a selected saved dataset / reviewed cohort from an older run

Each seed must be an antibody-antigen complex, not just a target structure.

Required metadata:

- `selected_input_dir` or `ppiflow_seed_input_dir`
- `selected_input_manifest` when present
- `framework_type`
- `antibody_chains`
- `antigen_chains`
- `cdr_positions_by_loop`
- `manual_cdr_definitions`

### Why not `target_pdb + framework_pdb` only?

There is no current safe target-bound seed assembly step for PPIFlow. Concatenating target and framework PDBs is not enough because anchor detection depends on a real interface.

[scripts/merge_complex.py](/home/dalab/biomodstack/biomodstack/scripts/merge_complex.py) can merge structures into one file, but that does not solve placement/interface realism. So target-only PPIFlow generation is a separate later project.

## Workflow Design

Add a new standalone workflow:

- `workflows/ppiflow_generator_design.nf`

This workflow should:

1. resolve seed complex inputs
2. normalize / enrich chain + loop metadata
3. run `MATURATION_CHILD_CORE` with:
   - `ppiflow_mode = backbone_refine`
   - `maturation_redesign_enabled = false`
4. publish stable raw candidate outputs
5. open `post_ppiflow_generator` when interactive
6. ingest completed generator outputs as `ppiflow` / `generator_backbone_refine`

### Why a wrapper workflow instead of hijacking `antibody_denovo.nf`?

Because the existing `antibody_denovo.nf` PPIFlow path is refinement-stage logic hanging off RFantibody/FAMPNN. A standalone wrapper keeps:

- generator mode separate
- refinement mode untouched
- runtime reuse high
- lineage semantics clear

### Runtime settings

Default generator-mode settings:

- `ppiflow_mode = backbone_refine`
- `maturation_redesign_enabled = false`
- `ppiflow_require_anchors = true`
- `ppiflow_rotamer_enrichment_enabled = true`
- `ppiflow_region_mode = selected_cdrs`
- `ppiflow_selected_loops = H1,H2,H3` for nanobody mode unless explicitly overridden

Expose generator-specific knobs:

- checkpoint
- config path
- `ppiflow_start_t`
- `ppiflow_samples_per_target`
- `ppiflow_retry_limit`
- region mode
- selected loops
- strict anchor requirement
- rotamer enrichment

Do not expose refinement-only maturation redesign settings in initial generator mode by default.

## Backend / Contract Changes

### 1. Artifact inference

Update [platform/api/antibody_pipeline_contract.py](/home/dalab/biomodstack/biomodstack/platform/api/antibody_pipeline_contract.py):

- map `("ppiflow", "generator_backbone_refine") -> backbone_complex`

Optional:

- also accept `("ppiflow", "generator_maturation") -> sequence_designed_complex` for a later phase, but do not expose this in UI initially

### 2. Review-stage recognition

Update [platform/api/services/stage_review.py](/home/dalab/biomodstack/biomodstack/platform/api/services/stage_review.py):

- add `post_ppiflow_generator` to `REVIEWABLE_STAGES`
- map `post_ppiflow_generator -> ("ppiflow", "generator_backbone_refine")`
- materialize review rows from stable published raw/filtered PPIFlow generator dirs

### 3. Resume / reopen semantics

Update [platform/api/routers/jobs.py](/home/dalab/biomodstack/biomodstack/platform/api/routers/jobs.py):

- accept `post_ppiflow_generator` as an interactive gate stage
- add a `_should_spawn_antibody_refinement_on_resume()` branch for paused PPIFlow generator review
- spawn `ui_refinement` with:
  - `selected_input_artifact_class = backbone_complex`
  - `selected_input_stage_family = ppiflow`
  - `selected_input_stage_mode = generator_backbone_refine`

### 4. Saved shortlist persistence

No new schema should be required.

Existing fields already cover this:

- `jobs.saved_selection_sets`
- `jobs.selected_input_artifact_class`
- `designs.artifact_class`
- `designs.stage_family`
- `designs.stage_mode`

So this should be a **no-migration** integration unless new PPIFlow-specific metrics are added later.

## Results / Dashboard Changes

### Results Viewer

[ResultsViewer.tsx](/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/ResultsViewer.tsx) and [designOutputSource.ts](/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/designOutputSource.ts) already understand the `ppiflow` source bucket.

Needed additions:

- recognize `post_ppiflow_generator` as a paused review stage
- show saved shortlists for completed PPIFlow generator jobs
- allow `Open Antibody Refinement` from PPIFlow generator cohorts

### Dashboard

Update [Dashboard.tsx](/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/Dashboard.tsx):

- label `post_ppiflow_generator` as `PPIFlow Review`
- map it to resume stage `ppiflow`

## Frontend Changes

### 1. Add third generator

Update [AntibodyDenovoTemplate.tsx](/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/AntibodyDenovoTemplate.tsx):

- extend `type DeNovoGenerator = 'rfantibody' | 'boltzgen' | 'ppiflow'`
- add a third generator tile
- keep the same shared shell and generator/refiner framing

### 2. Generator-specific form

Add a PPIFlow generator panel with:

- seed-complex upload / directory selection / saved dataset selection
- chain-role controls
- loop-scope controls
- core PPIFlow generator knobs
- interactive review toggle

### 3. Preserve the generator-first behavior

Like BoltzGen, PPIFlow generator mode should default to **core-generator only**.

The downstream stage tiles should remain hidden or disabled in initial generator mode, because the intended path is:

- generator batch first
- review / shortlist
- then `Antibody Refinement`

### 4. Refinement compatibility

The existing refinement logic already knows how to treat `ppiflow`-origin inputs specially in [AntibodyDenovoTemplate.tsx](/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/AntibodyDenovoTemplate.tsx), specifically the `refinementSourceIsPpiFlow` / immediate-backbone-refine guard.

Keep that logic. It is correct.

## Stable Output Layout

Generator-mode PPIFlow should publish stable directories similar to the BoltzGen and RFantibody generator flows.

Recommended:

- `collected/ppiflow_generator_raw`
- `collected/ppiflow_generator_filtered`

Review payload should point at:

- `candidate_dir = filtered if present else raw`
- `raw_dir = collected/ppiflow_generator_raw`
- `filtered_dir = collected/ppiflow_generator_filtered`

## Acceptance Criteria

PPIFlow generator integration is done when all of these are true:

1. `PPIFlow Seeded` appears as a third generator in the de novo toolkit.
2. A user can submit a seed-complex batch without entering refinement mode.
3. Interactive mode pauses at `post_ppiflow_generator`.
4. Results Viewer shows the cohort as `PPIFlow`, not RFantibody or validation.
5. Saved shortlists persist on completed generator runs.
6. `Open Antibody Refinement` works from paused or completed PPIFlow generator cohorts.
7. Refinement receives those inputs as `backbone_complex`.
8. Existing refinement-stage PPIFlow still behaves exactly as it does today.

## Recommended Commit Split

### Commit 1

`ppiflow: add generator-stage contract and review identity`

- `antibody_pipeline_contract.py`
- `stage_review.py`
- `jobs.py`
- related tests

### Commit 2

`ppiflow: add standalone seeded generator workflow`

- `workflows/ppiflow_generator_design.nf`
- reuse `maturation_child_core.nf`
- stable publish dirs
- runtime defaults
- workflow tests / stub run

### Commit 3

`frontend: add PPIFlow Seeded generator to de novo toolkit`

- `AntibodyDenovoTemplate.tsx`
- API types / launch payloads
- generator-specific panel

### Commit 4

`results: add post_ppiflow_generator review and refinement reopen parity`

- `Dashboard.tsx`
- `ResultsViewer.tsx`
- `designOutputSource.ts`
- saved shortlist / reopen handling

## Recommended Scope Decision

Ship **seeded PPIFlow generator** first.

Do not promise a target-only PPIFlow generator until there is a real seed-placement/interface-construction stage. Right now the current PPIFlow stack is strong enough to be a generator in the toolkit, but only if the initial input is honestly treated as a **seed complex batch**, not a raw target-only de novo job.
