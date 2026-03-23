# PPIFlow Steering And Loop Scoring Spec

## Purpose

This spec covers the remaining PPIFlow gaps after the dual-stage workflow split and recent viewer fixes.

## Upstream Reference Point

Upstream PPIFlow should be treated as more than a local refinement script. The public project positions it as a flow-matching framework and integrated binder-design workflow covering protein binders, antibodies, nanobodies, motif scaffolding, and partial-flow redesign:

- GitHub: <https://github.com/Mingchenchen/PPIFlow>
- bioRxiv PDF: <https://www.biorxiv.org/content/10.64898/2026.01.19.700484v1.full.pdf> (doi: `10.64898/2026.01.19.700484`)
- local PDF snapshot: [2026.01.19.700484v1.full.pdf](./2026.01.19.700484v1.full.pdf)

The January 22, 2026 preprint frames the upstream method as a combined system:

- `PPIFlow` for backbone/interface generation with flow matching
- interface rotamer enrichment for energetic packing
- partial-flow refinement for local redesign of an existing complex
- `AF3Score` for candidate prioritization

The upstream README also explicitly documents antibody/nanobody generation and antibody partial flow, including fixed-position residues, CDR positions, hotspot residues, and `start_t` as the control that trades off preservation versus redesign.

## BioModStack Interpretation

This spec is not proposing to clone the entire upstream PPIFlow workflow into BioModStack.

In BioModStack, PPIFlow is being used as a stage inside an `RFantibody`-centered antibody/nanobody workflow:

1. `RFantibody` generates hotspot-conditioned starting backbones.
2. Optional `post_rfantibody` PPIFlow refines local loop pose and interface geometry before sequence design.
3. `FAMPNN` generates sequence-conditioned designs on the selected backbones.
4. Optional `post_fampnn` PPIFlow performs later-stage maturation / cleanup on concrete sequence-conditioned complexes.
5. Validation and any later repair stages happen after those explicit insertion points.

That stage placement matters because the experimental question in BMS is narrower than the full upstream PPIFlow paper: we are mostly using PPIFlow to improve or compare a specific existing antibody pose, first after `RFantibody` backbone generation and then, if desired, after `FAMPNN`.

This is why the next gap is not basic execution, but steering and measurement. Once PPIFlow is inserted between `RFantibody` and `FAMPNN`, or after `FAMPNN`, the platform needs loop-local and hotspot-local scoring instead of only whole-interface energy deltas.

The current stack can:

- run `post_rfantibody` or `post_fampnn` PPIFlow
- restrict movable region to selected CDRs, all CDRs, framework-only, or whole antibody
- pass antigen hotspot residues into PPIFlow
- persist refined structures and coarse post-scoring
- display parent/child lineage and basic PPIFlow metrics

The current stack still cannot do the most important experimental task well:

- measure whether the modified loop actually moved toward the intended epitope residues
- compare pre-vs-post loop geometry and contacts in a loop-specific way
- rank PPIFlow outputs by loop-local objective instead of only whole-interface Rosetta energy

This spec bridges that gap.

## Current State

Movable-region control is already real in:

- [modules/ppiflow.nf](../modules/ppiflow.nf)
- [scripts/identify_anchors.py](../scripts/identify_anchors.py)
- [platform/frontend/src/components/QualitySettingsPanel.tsx](../platform/frontend/src/components/QualitySettingsPanel.tsx)

Current scoring is still coarse:

- `interface_score_original`
- `interface_score_matured`
- `delta_interface_score`
- `rmsd_backbone`
- `sequence_identity`
- `clash_count_ca`

That score is computed in [scripts/score_maturation.py](../scripts/score_maturation.py).

Current chart support is richer than before, but still mostly whole-structure / whole-interface. The charts do not yet expose loop-local deltas or hotspot-local deltas.

## Problem Statement

PPIFlow is being used as a local structural refinement tool, but the stack still evaluates it mostly as a global interface-energy perturbation.

That is not enough when the experimental question is:

- did `H1` move closer to the target patch?
- did `H2/H3` gain epitope contacts while the framework stayed stable?
- did a refined loop improve hotspot engagement without making the rest of the pose worse?

The system needs a second scoring layer:

1. structure-local
2. loop-aware
3. hotspot-aware
4. pre/post comparable
5. persisted and chartable

## Design Goals

1. Make PPIFlow steerable toward a loop-local interface objective.
2. Make every refined output comparable to its source backbone.
3. Make the Charts tab show loop-local pre/post deltas, not only coarse global metrics.
4. Keep raw PPIFlow samples visible even when automatic filtering is enabled.
5. Preserve full provenance from source backbone to refined output.

## Required Revisions

### 1. Add Loop-Local PPIFlow Metrics

Add a new analysis/scoring pass for every PPIFlow output:

- `ppiflow_loop_metrics`
- `ppiflow_hotspot_metrics`
- `ppiflow_compare_metrics`

This should be a stage-native persisted payload, not an on-demand viewer-only computation.

Implement a new scorer, for example:

- `scripts/score_ppiflow_loop_refinement.py`

Inputs:

- source complex PDB
- refined complex PDB
- antibody chains
- antigen chains
- selected loop scope
- manual CDR definitions if present
- selected hotspot residues if present

Outputs:

- per-loop contact counts before and after
- per-loop nearest epitope distance before and after
- per-loop nearest any-target distance before and after
- per-loop centroid-to-epitope distance before and after
- per-loop centroid-to-target distance before and after
- per-loop backbone RMSD
- per-loop CA displacement magnitude
- per-loop hotspot-contact count before and after
- per-loop hotspot minimum distance before and after
- per-loop improvement deltas for all of the above

Persist these as namespaced JSON plus selected scalar columns for filtering and sorting.

### 2. Add Loop-Local Database Fields

Keep the full JSON, but also add scalar columns for the most important sortable/filterable metrics.

Recommended columns on `Design`:

- `ppiflow_modified_loop_scope`
- `ppiflow_primary_loop`
- `ppiflow_loop_rmsd`
- `ppiflow_loop_contact_delta`
- `ppiflow_loop_epitope_distance_delta`
- `ppiflow_loop_target_distance_delta`
- `ppiflow_hotspot_contact_delta`
- `ppiflow_hotspot_min_distance_delta`
- `ppiflow_source_epitope_contact_count`
- `ppiflow_refined_epitope_contact_count`
- `ppiflow_source_target_contact_count`
- `ppiflow_refined_target_contact_count`

If multiple loops are modified, `ppiflow_primary_loop` should be:

- the only loop if one was selected
- otherwise a normalized label like `H2,H3`

The full JSON should carry all loop-by-loop values.

## 3. Distinguish Raw And Filtered PPIFlow Sets

PPIFlow should behave like RF review:

- `raw` set: all generated samples
- `filtered` set: samples that passed post-scoring gates

Current problem:

- filtering hides useful exploratory structures
- the user cannot inspect why a source backbone yielded eight samples but only two were retained

Required behavior:

- publish and ingest all raw PPIFlow samples
- persist `passed_filter` and `filter_reason`
- expose `raw` vs `filtered` as selectable review sets in the viewer

This needs updates in:

- [modules/ppiflow.nf](../modules/ppiflow.nf)
- [workflows/maturation_child_core.nf](../workflows/maturation_child_core.nf)
- [platform/api/services/result_ingester.py](../platform/api/services/result_ingester.py)
- [platform/frontend/src/components/ResultsViewer.tsx](../platform/frontend/src/components/ResultsViewer.tsx)

## 4. Add Better PPIFlow Steering Controls

The current controls are necessary but not sufficient.

Keep:

- `region_mode`
- `selected_loops`
- `start_t`
- `samples_per_target`
- hotspot residues
- anchor threshold
- anchor distance cutoff

Add:

- `ppiflow_objective_mode`
  - `global_interface`
  - `loop_epitope`
  - `loop_hotspot`
  - `balanced`
- `ppiflow_max_loop_rmsd`
- `ppiflow_min_hotspot_contact_delta`
- `ppiflow_max_hotspot_distance`
- `ppiflow_min_epitope_contact_delta`
- `ppiflow_require_zero_ca_clash`
- `ppiflow_rank_by`
  - `delta_interface`
  - `loop_contact_delta`
  - `hotspot_contact_delta`
  - `loop_distance_delta`
  - `hybrid`

Interpretation:

- `global_interface` preserves current behavior
- `loop_epitope` ranks by improvement of the modified loop against selected epitope residues
- `loop_hotspot` ranks by improvement against explicitly selected hotspot residues
- `balanced` combines whole-interface and loop-local terms

## 5. Add Pre/Post Compare As A First-Class View

The viewer currently supports direct source-structure compare, but it is not yet a true PPIFlow compare workflow.

Required additions:

- a `Source vs Refined` compare mode in `3D Structure`
- loop-scope overlay highlighting moved residues
- source/refined metrics shown side-by-side
- per-loop delta cards

The selected design card for PPIFlow should show:

- `Source Backbone`
- `Sample`
- `Modified Loop Scope`
- `Source Interface Score`
- `Refined Interface Score`
- `ΔIface`
- `Loop RMSD`
- `Loop Contact Δ`
- `Hotspot Contact Δ`
- `Epitope Dist Δ`
- `Target Dist Δ`
- `CA Clash`

## 6. Expand Charts Tab

The Charts tab should become the main experimental analysis surface for PPIFlow.

Required new charts:

### Core PPIFlow

- `Source vs Refined Interface Score`
- `ΔIface vs Whole Backbone RMSD`
- `Sample Index vs ΔIface`
- `CA Clash vs ΔIface`

### Loop-Local

- `Loop RMSD by Modified Loop Scope`
- `Loop Contact Δ by Modified Loop Scope`
- `Loop Epitope Distance Δ by Modified Loop Scope`
- `Loop Target Distance Δ by Modified Loop Scope`
- `Hotspot Contact Δ by Modified Loop Scope`

### Source/Target Contrast

- `Source vs Refined Epitope Contacts`
- `Source vs Refined Any-Target Contacts`
- `Source vs Refined Hotspot Contacts`
- `Source vs Refined Loop Centroid Distance`

### Family / Backbone Contrast

- `Backbone ID vs Best Loop Contact Δ`
- `Backbone ID vs Best Hotspot Δ`
- `Backbone ID vs Best ΔIface`

## 7. Filtering / Sorting Additions

The table and saved review datasets should support:

- sort by `Iface Score`
- sort by `Loop RMSD`
- sort by `Loop Contact Δ`
- sort by `Hotspot Contact Δ`
- sort by `Epitope Dist Δ`

And filters for:

- modified loop scope
- raw vs filtered PPIFlow set
- loop RMSD max
- hotspot contact delta min
- epitope distance delta max
- zero-clash only

## 8. Provenance Requirements

Every refined output must keep these links:

- `source_design_id`
- `source_design_name`
- `source_backbone_id`
- `source_job_id`
- `selected_loop_scope`
- `region_mode`
- `hotspot_spec`
- `start_t`
- `sample_index`
- `filter_passed`
- `filter_reason`

The provenance payload should also carry:

- full source metrics snapshot
- full refined metrics snapshot
- full loop-local delta snapshot

This makes downstream FA-MPNN or validator stages explainable.

## 9. Recommended Default Behavior

### Post-RFantibody PPIFlow

Default for exploration:

- `region_mode=selected_cdrs`
- loop explicitly chosen
- `start_t=0.45-0.60`
- `samples_per_target=6-8`
- automatic rejection disabled by default
- raw and filtered sets both visible
- ranking default = `loop_hotspot` if hotspots are selected, otherwise `loop_epitope`

### Post-FA-MPNN PPIFlow

Default for cleanup:

- `region_mode=selected_cdrs` or `all_cdrs`
- `start_t=0.70-0.85`
- `samples_per_target=3-5`
- keep filtering on if desired
- ranking default = `balanced`

## Implementation Order

### Phase 1

- add loop-local scorer script
- add persisted JSON payloads
- add scalar DB columns
- ingest and expose metrics in API

### Phase 2

- raw vs filtered PPIFlow review sets
- viewer source selector support
- table sort/filter support for new metrics

### Phase 3

- dedicated pre/post compare UI
- Charts tab expansion
- objective-mode driven ranking presets

## Acceptance Criteria

1. A user can run `post_rfantibody` PPIFlow on `H1` and see whether `H1` moved closer to the selected epitope residues.
2. A user can compare source vs refined loop metrics in both cards and charts.
3. Raw PPIFlow samples are always inspectable, even when filter gates are enabled.
4. Saved datasets preserve raw/filtered set choice and new loop-local filters.
5. Downstream stages can always identify exactly which source backbone and which loop scope produced a refined structure.

## Priority

- `[P1][Revision]` add loop-local and hotspot-local PPIFlow metrics
- `[P1][Revision]` expose raw vs filtered PPIFlow sets
- `[P1][Revision]` add source-vs-refined compare metrics and charts
- `[P2][Revision]` add objective-mode ranking presets
- `[P2][Revision]` add hybrid loop/global ranking
