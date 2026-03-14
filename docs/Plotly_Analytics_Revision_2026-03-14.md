# Plotly Analytics Revision

Date: 2026-03-14
Status: implemented frontend revision, backend follow-on still needed

## Context

This revision updates the active `Charts` tab to use the flattened Plotly metric surface as the source of truth instead of a fixed Boltz-centric metric list.

It is aligned with the workflow and control-plane guidance in:

- `docs/RFA_Interactive_SWA_Review_2026-03-06.md`
- `docs/RFA_Protenix_Validator_Toggle_Implementation_Spec_2026-03-07.md`

## What Changed

1. The active dashboard now receives `jobId` and fetches `/api/designs/by-job/{job_id}/plotly-metrics`.
2. The dashboard keeps the top-of-tab structural views:
   - chain-resolved per-residue pLDDT
   - PAE matrix
3. Plotly sections are now grouped by major output family:
   - `Validation Loop`
   - `RFantibody Backbone Gate`
   - `FAMPNN Sequence Design`
   - `PPIFlow Maturation`
   - `FrustraMPNN QC`
   - `Protenix Validator Detail`
4. The chart surface now applies an analysis lens:
   - auto-detected from the active job context when possible
   - overridable in the dashboard header
   - used to prioritize the focused analysis block, seed custom Plotly defaults, and bias the selected-design dropdown toward the active family
5. The custom Plotly lab now works against all available flattened metric keys, so new numeric fields render without another dashboard rewrite.

## What This Solves

- The visible `Charts` tab is no longer hard-wired to the Boltz2 validation loop.
- Existing persisted RFA screening metrics now have first-class chart coverage.
- Existing persisted FAMPNN, PPIFlow, FrustraMPNN, and Protenix metrics now have dedicated sections.
- Validator-loop metrics such as RMSD and ipSAE-like keys can surface automatically if they are persisted into `confidence_metrics`.

## Remaining Persistence Gaps

The dashboard is now prepared for a broader metric surface, but the ingest layer still needs more data from the custom antibody pipeline:

1. RFantibody orientation and pose metrics
   - Examples: hotspot alignment, tilt/rotation, target-facing orientation, pose error
2. Validation-loop agreement metrics
   - Explicit aliases for ipSAE and validator-specific RMSD values
3. FAMPNN deeper residue summaries
   - Max residue PSCE and residue-level summary stats
4. PPIFlow richer maturation outputs
   - Residue or interface-local deltas beyond the current three scalar fields
5. Validator provenance
   - A cleaner persisted marker for Boltz2 vs Protenix when both appear across related runs

## Recommended Next Backend Slice

1. Add one namespaced per-design JSON field and one typed per-stage summary field rather than adding many more single-purpose columns.
   - `Design.model_metrics`
   - `Job.stage_summaries` or `Job.stage_artifacts`
2. Persist RFA orientation metrics alongside the existing coarse screening CSV fields.
3. Normalize validator agreement aliases in `_build_plotly_metrics()`.
4. Capture richer FAMPNN, PPIFlow, and FrustraMPNN summaries into namespaced JSON payloads.
5. Extend stage review materialization for `post_structure_validation` so the same Plotly surface works at the validator gate, not only after final ingestion.
6. Keep the dashboard model-agnostic by only expanding ingestion and flattening, not by adding more frontend schema branches.
