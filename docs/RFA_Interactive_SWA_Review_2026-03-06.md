# RFA Interactive SWA Review

Date: 2026-03-06

## Scope

This note reviews the current BioModStack (`BMS`) implementation of the RFA-centered antibody/nanobody workflow, the model/documentation surface actually wired into it, the API/job-control surfaces relevant to orchestration, and the lowest-risk design for turning the current auto-progressing SWA pattern into a real user-driven iteration loop for de novo nanobody design.

The workflow under review is `workflows/antibody_denovo.nf`, with `framework_type='nanobody'` as the target operating mode.

## Current Nanobody Workflow

The current parent workflow is linear even when it uses child jobs. SWA is implemented as `spawn -> wait -> collect -> continue`, all inside a single Nextflow run.

1. Target resolution
   - Use `target_pdb` if supplied, otherwise predict a target complex upstream via `PredictTargetComplex`.
2. Step 1 backbone generation
   - RFantibody in standard mode, or RFantibody child jobs via orchestrator SWA, or skip with preloaded backbone PDBs.
3. Step 2 sequence design
   - FAMPNN is the default nanobody path from the frontend.
   - AntiFold and ProteinMPNN are optional sibling branches.
   - Optional PPIFlow maturation runs only on the FAMPNN branch.
4. Step 2.5 and 2.6 optional refinement
   - ThermoMPNN pre-Boltz scoring/filtering.
   - AF2 backprop refinement.
5. Step 3 structure validation
   - Generate one representative MSA.
   - Run Boltz either in exploration mode through child jobs, or in-process through `BoltzFromSequenceWithMSA`.
6. Post-validation scoring/refinement
   - Optional OpenMM.
   - Optional AntiBERTy scoring/filtering.
   - Optional IgGM affinity maturation.
   - Optional FrustraMPNN and ANARCII post-annotation.

Key source files:

- `workflows/antibody_denovo.nf`
- `modules/rfantibody.nf`
- `modules/fampnn.nf`
- `modules/ppiflow.nf`
- `modules/structure_prediction.nf`
- `platform/api/routers/jobs.py`
- `platform/api/routers/queue.py`
- `platform/api/services/nextflow.py`

## Model Audit

The table below distinguishes between:

- `Core`: used in the default nanobody path as currently submitted by the frontend
- `Optional`: available behind toggles or alternate branches
- `Local docs`: what is present in this repo today
- `Official code/docs`: GitHub, package, or project docs
- `Publication / preprint`: paper or preprint reviewed when one was identifiable

| Model / tool | Role in BMS nanobody path | Status | Local docs | Official code/docs | Publication / preprint | Notes |
|---|---|---|---|---|---|---|
| RFantibody | Backbone generation against target hotspots | Core | `workflows/antibody_denovo.nf`, `modules/rfantibody.nf`, `docs/RFA_Workflow_Fix_Plan_2026-02-10.md` | https://github.com/RosettaCommons/RFantibody | https://www.nature.com/articles/s41586-025-09383-z | BMS uses RFantibody as the first-stage generator and already added container/runtime hardening around it. The paper is the RFdiffusion-antibody work underlying the repo. |
| FAMPNN | Default sequence design and pre-Boltz quality filter source | Core | `modules/fampnn.nf`, `nextflow.config` | https://github.com/richardshuai/fampnn | https://doi.org/10.1101/2025.02.13.637498 | Official repo matches BMS use of sidechain-aware sequence design and recommended `fampnn_0_3.pt` weights. |
| AntiFold | Optional inverse-folding sequence branch | Optional | `modules/antifold.nf`, workflow comments in `workflows/antibody_denovo.nf` | https://github.com/oxpig/AntiFold | https://doi.org/10.1101/2023.09.20.558827 | Official docs confirm IMGT-numbered inputs, nanobody mode, FASTA output, and sequence sampling. |
| ProteinMPNN | Optional alternate sequence branch | Optional | `modules/proteinmpnn.nf` | https://github.com/dauparas/ProteinMPNN | https://www.science.org/doi/10.1126/science.add2187 | BMS uses it as a fixed-backbone sequence designer. |
| ThermoMPNN | Optional pre-Boltz ddG-based stability heuristic | Optional | `modules/thermompnn.nf`, `nextflow.config` | https://github.com/Kuhlman-Lab/ThermoMPNN | https://doi.org/10.1101/2023.03.08.531600 | Official ThermoMPNN is a mutation stability predictor; BMS is using it as a coarse pre-Boltz filter, which is useful but should be treated as heuristic rather than absolute stability truth. |
| GenerateLocalMSA / MMseqs2 cache | Shared MSA generation for Boltz validation | Core when validation enabled | `modules/structure_prediction.nf` | BMS-local implementation only | Not applicable | Not a learned model, but central to the validation path and to any pause/resume design around cached MSAs. |
| Boltz-2 | Structural validation, confidence, optional affinity | Core when validation enabled | `modules/structure_prediction.nf`, `modules/boltz.nf`, `nextflow.config` | https://github.com/jwohlwend/boltz | https://doi.org/10.1101/2025.06.14.659047 | Official docs align with BMS use of YAML-driven prediction, optional affinity heads, and MSA-assisted inference. |
| PPIFlow | Optional post-FAMPNN maturation / partial-flow redesign | Optional | `modules/ppiflow.nf`, `docs/2026.01.19.700484v1.full.pdf` | https://github.com/Mingchenchen/PPIFlow | local paper snapshot `docs/2026.01.19.700484v1.full.pdf` (bioRxiv doi: `10.64898/2026.01.19.700484`) | Official docs explicitly support antibody and nanobody design plus partial-flow maturation. |
| AF2 backprop / AfDesign | Optional CDR refinement before Boltz | Optional | `modules/af2_backprop.nf`, `nextflow.config` | https://github.com/sokrypton/ColabDesign | https://doi.org/10.5281/zenodo.8147634 | BMS implementation matches the AfDesign-style idea of optimizing sequences against AlphaFold confidence objectives. I did not confirm a separate canonical peer-reviewed AfDesign paper in this pass, so I am treating the software DOI as the primary citable source. |
| OpenMM | Optional physics refinement and MM-GBSA | Optional | `modules/openmm.nf`, `nextflow.config` | https://github.com/openmm/openmm and https://openmm.org/ | https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1005659 | BMS uses OpenMM as a post-modeling refinement layer, not as a generator. |
| AntiBERTy | Optional immunogenicity / naturalness scoring via PLL | Optional | `modules/antiberty.nf` | https://pypi.org/project/antiberty/ | https://arxiv.org/abs/2112.07782 | Official package docs match BMS use of pseudo-log-likelihood scoring. |
| IgGM | Optional affinity maturation / antibody foundation-model stage | Optional | `modules/iggm.nf` | https://github.com/TencentAI4S/IgGM | https://doi.org/10.1101/2025.01.19.633870 | Official repo now frames IgGM as a broader antibody foundation model supporting de novo design, maturation, inverse design, structure prediction, and humanization. |
| ANARCII / ANARCI | IMGT numbering and CDR extraction | Conditional | `modules/utils/anarci.nf` | https://pypi.org/project/anarcii/ and https://github.com/oxpig/ANARCI | https://doi.org/10.1101/2025.10.29.681165 | The local module imports `ANARCII`, while older repo references still point at legacy `ANARCI`; BMS should treat this as a migration boundary, not a settled naming/documentation surface. |
| FrustraMPNN | Optional post-hoc QC / frustration annotation | Optional | `modules/frustrampnn.nf`, `docs/frustraMPNN.pdf` | No canonical official repo confirmed in this pass | local paper snapshot `docs/frustraMPNN.pdf` (bioRxiv doi: `10.64898/2026.01.22.701012`) | The local module is the current source of truth for how BMS uses it. |
| PredictTargetComplex | Upstream target-structure resolver using Boltz | Optional | `modules/predict_target_complex.nf` | Covered indirectly by Boltz docs | Covered indirectly by the Boltz-2 preprint | Utility wrapper rather than a separate model family. |

### Publication Notes

The paper/preprint layer is now explicit, but there are still a few documentation caveats:

1. `AF2 backprop / AfDesign`
   - I confirmed the official ColabDesign codebase and software DOI.
   - I did not confirm a single canonical peer-reviewed paper for the exact BMS AF2-backprop implementation during this pass.

2. `ANARCII / ANARCI`
   - The modern package and the legacy repo are both in circulation.
   - BMS code is already on the modern package side, but the repo inventory still references the legacy surface.

3. `FrustraMPNN`
   - The repo already contains the preprint PDF.
   - I did not confirm a canonical public code repo during this pass, so the local PDF and local module remain the source of truth.

## Local Documentation Gaps

`docs/ai_guidance/Model_Integrations.md` is useful as an inventory, but several internal references in it are stale or missing from the repo.

Missing internal documents referenced by the inventory:

- `docs/installation.md`
- `docs/parameters.md`
- `docs/modes.md`
- `docs/FAMPNN_CONSTRAINTS_UPDATE.md`
- `docs/RFA_PPIFlow_Implementation_Plan_Final.md`
- `docs/OpenMM_Integration_Plan.md`
- `docs/FrustraMPNN_Integration_Plan.md`
- `docs/WORKSTATION_SETUP.md`

Implication:

- The current source of truth is the code plus a small number of surviving docs, not the model inventory file by itself.
- Any implementation work on interactive gating should assume code-first truth and update docs only after behavior is corrected.

## Current Integration Risks

## Implemented Changes (2026-03-07)

The current code now includes the following workflow/control-plane changes:

1. Post-FAMPNN interactive gate is implemented
   - Antibody template submissions now set `interactive_swa=true` with `interactive_gate_stage=post_fampnn`.
   - The workflow materializes the FAMPNN candidate set, opens a stage gate through the API, and exits cleanly.
   - The backend finalizes those jobs as `status='awaiting_input'`, not `completed`.
   - Resume/continue from that gate reuses the collected FAMPNN directory through `fampnn_collected_pdbs` and sets `interactive_gate_continue=true`.

2. Post-Boltz PPIFlow repair stage is implemented
   - The workflow now supports a second PPIFlow pass on validated Boltz structures through `run_post_boltz_maturation`.
   - The current antibody template maps this to the same user-facing maturation toggle, so enabling PPIFlow now covers both pre-Boltz maturation and post-Boltz repair.

3. AntiFold no longer corrupts the PDB-only downstream path
   - AntiFold FASTA outputs are now kept as sequence-only candidates.
   - They can enter serial Boltz refinement, but are excluded from exploration-mode child validation, ThermoMPNN, AF2 backprop, and other PDB-only stages.

4. AntiBERTy filtering is now structure-safe
   - The workflow now uses a PDB-aware AntiBERTy filter instead of feeding PDBs into the FASTA filter contract.
   - Downstream stages keep working on `(meta, pdb)` tuples instead of accidentally switching to FASTA.

5. PPIFlow child output handling is corrected
   - `maturation_child.nf` now emits the real `filter_reports` output.
   - `FilterByMaturation` now accepts both `*_maturation_score.json` and `*_partial_flow_score.json`.

6. Nanobody chain defaults are now safer
   - The main workflow forces `antibody_chains='H'` when `framework_type=nanobody` and no explicit override was provided.
   - PPIFlow now validates heavy/light chain presence against the input PDB and drops a missing light chain instead of silently assuming paired-chain behavior.

7. ThermoMPNN naming drift is corrected in the active path
   - API/job normalization now canonicalizes to `run_thermompnn` while still mirroring the legacy `run_stability_scoring` flag for backward compatibility.
   - Stage display logic now keys off the canonical ThermoMPNN flag.

8. RFantibody loop-length priors are now first-class controls
   - The antibody launcher now distinguishes between:
     - manual CDR residue spans for downstream FAMPNN constraints
     - RFantibody-native loop-length priors for initial backbone exploration
   - This prevents the previous overloading of one parameter for two incompatible meanings.

9. Viewer-launched CDR indel rounds now exist
   - The Results Viewer can now launch a `cdr_indel_round` from selected antibody designs.
   - This creates explicit insertion/deletion variant libraries on chosen CDR loops and submits them as new top-level validation jobs while preserving full complex context per variant.
   - The current implementation is chain-specific by construction: selected loops must all belong to the same chain family (`H*` or `L*`).

10. RFantibody backbone screening and a first review gate now exist
   - The workflow can now pause at `post_rfantibody` before FAMPNN starts.
   - An optional coarse RFantibody screen is also available before sequence design:
     - minimum epitope contact count
     - maximum minimum epitope distance
     - minimum loose whole-target contact count
     - maximum antibody-to-epitope centroid distance
   - This screen is intentionally conservative and is meant to remove obviously detached or malformed backbones, not to replace manual review or downstream structural validation.
   - The screen now also corrects an older chain-detection failure mode where chain `A` could be misinterpreted as the antibody fallback even when `A` was actually the antigen chain.

## Current Integration Risks

Several code-path mismatches matter before introducing user-facing gating:

1. IgGM affinity maturation is still not looped back through validation
   - The workflow now preserves the PDB type into the IgGM stage.
   - It still warns instead of re-running a full post-IgGM Boltz/OpenMM/AntiBERTy cycle.

2. FrustraMPNN aggregator is glob-driven
   - The aggregator ignores its formal input channel and globs local files instead, which is fragile for checkpointed or resumed execution.
   - The current workflow now at least runs FrustraMPNN per structure instead of collapsing selected designs into one synthetic batch, and the Results Viewer exposes frustration-aware table columns, overview stats, and structure coloring.

3. The first gate is implemented, but the full multi-gate loop is not
   - `post_fampnn` now exists as a first-class pause point.
   - `post_rfantibody` now also exists as an earlier review point for backbone triage.
   - A separate decision endpoint and explicit `continue` endpoint are still not implemented; continuation currently reuses the resume path.
   - RFantibody screening is still deliberately coarse; more opinionated automatic filters should only be added after empirical review of false-positive/false-negative behavior on real runs.

## Relevant BMS API / Scheduler Surfaces

The existing control plane is strong enough to support an interactive loop, but it is missing one layer of state and schema.

What already exists:

- `Job` database model includes:
  - `queue_status`, `paused`
  - `parent_job_id`, `child_stage`, `aggregated_by_parent`
  - `current_stage`, `completed_stages`, `stage_outputs`
  - `awaiting_input`, `awaiting_stage`, `awaiting_payload`, `decision_history`
- Job creation schema already supports:
  - `parent_job_id`
  - `child_stage`
  - `batch_id`
  - `batch_name`
- Queue endpoints already support:
  - pause
  - resume
  - cancel
  - pin to GPU
  - priority changes
- Stage tracking already supports:
  - `POST /api/jobs/{job_id}/stage-start`
  - `POST /api/jobs/{job_id}/stage-complete`
  - `GET /api/jobs/{job_id}/stages`
  - `POST /api/jobs/{job_id}/stage-gates/{stage}/open`
  - `GET /api/jobs/{job_id}/stage-gates`
- Child aggregation already supports:
  - `GET /api/jobs/{parent_id}/children/status`
  - `POST /api/jobs/{parent_id}/children/mark-aggregated`
- Resume already exists as a new-job workflow:
  - `POST /api/jobs/{job_id}/resume`

What does not yet exist:

- a dedicated decision endpoint separate from generic resume
- a dedicated `continue` endpoint separate from generic cache-driven resume
- a second implemented gate after Boltz/repair scoring

## Why the Current SWA Pattern Is Not Enough

The current SWA abstraction solves parallel GPU distribution, not human-in-the-loop iteration.

Today:

- parent job spawns children
- parent job blocks in `wait_for_children.py`
- parent job collects outputs
- parent job immediately feeds those outputs into the next stage

For an iterative nanobody workflow, the parent job instead needs to:

- stop after a meaningful artifact set exists
- expose those artifacts and summary metrics in the UI/API
- wait for a structured decision from the user
- continue with either:
  - different thresholds
  - different subset selection
  - different ranking/scoring weights
  - different downstream branch choice
  - different data path overrides

That is not a scheduler pause. It is a workflow stage gate.

## Recommended Interactive Design

### Design Principle

Do not keep a Nextflow process alive while waiting on the user.

Instead:

1. let each compute segment run to a stable checkpoint
2. persist artifacts and stage metadata
3. mark the parent job as awaiting a decision
4. let the UI submit a structured decision payload
5. create a resumed job or continuation job from that checkpoint

This keeps orchestration deterministic and works with the existing BMS queue model.

### Recommended New Job / Stage State

Add explicit gate fields to the job record, for example:

- `awaiting_input` boolean
- `awaiting_stage` string
- `awaiting_payload` JSON
- `awaiting_artifacts` JSON
- `decision_history` JSON
- `continuation_of_job_id` string

Minimum semantics:

- `status='running'` should no longer be overloaded to mean "done computing but waiting on a human"
- `queue_status='paused'` should remain scheduler-level
- `awaiting_input=true` should mean "this workflow segment completed and the next segment cannot start until a decision is submitted"

### Recommended New API Endpoints

1. `POST /api/jobs/{job_id}/stage-gates/{stage}/open`
   - Called by the workflow or a helper script after artifact collection.
   - Stores summaries, artifact paths, and allowed decisions.

2. `GET /api/jobs/{job_id}/stage-gates`
   - Returns current open gate and gate history.

3. `POST /api/jobs/{job_id}/stage-gates/{stage}/decision`
   - Accepts user choices:
     - filter thresholds
     - selected design IDs / paths
     - ranking method
     - scoring weights
     - whether to continue, branch, or stop

4. `POST /api/jobs/{job_id}/continue`
   - Creates a continuation job with the decision payload applied.
   - Equivalent to a structured resume, not a raw scheduler resume.

### Recommended Gate Payload Shape

Every gate payload should include:

- `stage`
- `summary_metrics`
- `artifact_paths`
- `preview_tables`
- `candidate_ids`
- `default_decision`
- `allowed_decisions`
- `provenance`

For the antibody workflow, artifact payloads should be relative API-safe paths whenever possible, matching `stage_reporter.py` path normalization.

## Recommended Gate Locations

Start small. The cleanest first gate is after FAMPNN collection/filtering and before Boltz.

### Phase 1: First Gate

Gate A: `post_fampnn`

Open after:

- FAMPNN child jobs complete
- optional FAMPNN PSCE filter completes

Actual implementation note:

- The implemented gate is now before downstream maturation/validation, not after them.
- This is intentional: it lets the user retune filtering, decide whether to run PPIFlow repair, and choose how to continue before additional GPU-heavy steps.

Expose:

- collected PDBs
- per-design FAMPNN JSON
- PSCE summaries
- chain and loop metadata

Allow decisions on:

- keep/drop designs
- PSCE thresholds
- maturation percentile / redesign toggle
- path overrides for pre-collected PDB reuse
- whether to continue into Boltz exploration or refinement mode

Why first:

- this is the first point where the search space can be collapsed cheaply
- it avoids wasting Boltz/OpenMM time on clearly bad candidates
- it aligns with the user’s desired loop around filtering, data viewing, and ranking

### Phase 2: Second Gate

Gate B: `post_boltz`

Open after:

- Boltz child jobs aggregate, or serial Boltz finishes

Expose:

- pLDDT / pTM / ipTM / affinity outputs
- selected structures
- optional target-interface metrics
- optional zero-yield report

Allow decisions on:

- rank function
- acceptance thresholds
- whether to run OpenMM
- whether to run AntiBERTy
- whether to hand selected candidates into IgGM

### Phase 3: Upstream and Downstream Gates

Later expansion:

- `post_rfantibody`
- `post_openmm`
- `post_antiberty`
- `post_iggm`

`post_rfantibody` is useful only after the first two gates are stable, because it will generate large candidate sets and heavier preview requirements.

## Lowest-Risk Implementation Path

1. Add API and DB support for explicit stage gates.
2. Implement one gate only: `post_fampnn`.
3. Make the workflow stop after `CollectFAMPNNOutputs` or `FilterFAMPNN` and emit a gate payload instead of auto-continuing to Boltz.
4. Add a continuation endpoint that launches a new job with:
   - prior output directory as source
   - selected design paths
   - updated filter/ranking params
   - explicit stage hint
5. Update the frontend to render:
   - open gate banner
   - artifact preview tables
   - design selection controls
   - continue/branch/stop actions
6. Only after this is stable, repeat the pattern for `post_boltz`.

## Implementation Notes for SWA Refactor

Do not modify child-job semantics first.

What should stay the same initially:

- child job creation through `/api/jobs`
- child polling through `/children/status`
- aggregation markers via `aggregated_by_parent`
- stage output reporting via `stage_reporter.py`

What should change:

- the parent Nextflow workflow should stop using the collected channel as an unconditional downstream input
- instead, it should end the compute segment after aggregation and open a gate

In practice, this likely means splitting `antibody_denovo.nf` into checkpointable segments or adding continuation modes such as:

- `antibody_denovo_stage2_from_backbones`
- `antibody_denovo_stage3_from_fampnn`
- `antibody_denovo_stage4_from_boltz`

This is less elegant than a single monolithic workflow, but much safer for long-lived human-in-the-loop execution.

## Recommended First Engineering Tasks

1. Fix the known interface mismatches before adding gating:
   - AntiFold FASTA/PDB mismatch
   - AntiBERTy filter input mismatch
   - PPIFlow nanobody-chain defaults and emit mismatch
   - `run_stability_scoring` vs `run_thermompnn` drift

2. Add explicit stage-gate schema and DB fields.

3. Implement `post_fampnn` as the first interactive checkpoint.

4. Add frontend support for:
   - awaiting-input job state
   - artifact preview
   - threshold editing
   - candidate selection
   - continuation submission

## Status Update 2026-03-08

The workflow has moved beyond the original review baseline.

Implemented since the original write-up:

- `post_fampnn` is live as a real interactive gate.
- `post_structure_validation` is now also implemented as a second gate.
- antibody validation is selectable between `boltz2` and `protenix`.
- the existing results/data viewer now supports selection-driven follow-on antibody actions instead of acting as a read-only surface.
- the antibody launcher now supports explicit `Static` vs `Interactive` execution mode and explicit pause-point selection.

Important integration fixes completed:

- RFantibody target inputs are normalized before backbone generation so raw multi-model RCSB inputs are no longer passed through unchanged.
- SAbDab framework handling now produces a true RFantibody-compatible H/L/T artifact instead of only appending remarks.
- saved antibody templates now route back into the antibody workflow page and restore richer workflow state instead of falling back to the generic launcher.
- stale antibody templates are normalized on access so old raw SAbDab framework paths do not keep breaking new launches.

Current Protenix status:

- antibody validation through `Protenix` is wired in the workflow, API, UI, and result-ingestion path.
- the current MSA bridge is no longer the original "let Protenix poll its own service and hope it completes" path.
- `scripts/prepare_protenix_msa.py` now resolves MSA up front and supports:
  - `colabfold_api` for small jobs
  - `local` for larger jobs
  - `auto` backend selection between those two
- MSA server GPU pinning now propagates into the actual local/batch MSA launch paths instead of staying a UI-only setting.

Still not complete:

- the no-compromise campaign / round / evaluation / lineage schema is still not implemented.
- repeated round history still collapses too much into the current job/design model.
- end-to-end proof of the new antibody `Protenix + external MSA-prep` path is still pending after the latest module-level fixes.

Latest concrete bug classes fixed during implementation:

- RFantibody child jobs missing normalized target/framework inputs
- zero-yield guard tuple-shape bug after FAMPNN filtering
- Protenix antibody-child FAMPNN-weight validation false positive
- MSA GPU pin not propagating to real launch paths
- Protenix module helper parse failure in Groovy before child execution

## Summary

The current BMS RFA workflow is already close to what an interactive nanobody design loop needs:

- SWA child orchestration exists
- stage checkpoint metadata exists
- queue pause/resume exists
- resume launching exists
- artifact paths can already be persisted

The missing piece is not compute orchestration. It is a first-class workflow gate model that lets the parent job stop at a scientifically meaningful checkpoint, expose artifacts and metrics, collect a structured decision, and continue as a controlled new segment.

That is the correct place to modify the SWA logic for iterative de novo nanobody design.
