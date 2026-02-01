# RFantibody+ PPIFlow Implementation Plan

> **Version**: 3.0 (Finalized end-to-end plan)  
> **Last Updated**: 2026-01-25  
> **Status**: Ready for implementation

---

## Executive Summary

Integrate **PPIFlow backbone maturation** into the RFantibody+ pipeline immediately **after FilterFAMPNN** and **before Boltz‑2** to reduce downstream compute and improve interface quality. The implementation explicitly emphasizes the **two PPIFlow in‑silico maturation mechanisms**: **interface rotamer enrichment** (anchor fixing) and **partial flow refinement** (backbone perturb + regeneration). The plan must reuse existing BioModStack primitives (ANARCII CDR annotation, FAMPNN constraints, API queueing, orchestrator GPU pinning) and avoid duplicated logic.

**Acceptance Criteria (explicit)**:
- Median interface improvement ≥ **2.0 REU** vs baseline (no maturation).
- ≥ **80%** of matured designs improve interface score.
- False positives (pass maturation, fail Boltz-2) ≤ **15%**.

---

## Pipeline Architecture

```
RFdiffusion → FAMPNN → FilterFAMPNN → [PPIFlow Backbone Maturation] → Boltz-2 → OpenMM
```

Optional stages (ThermoMPNN / AF2 Backprop) remain **after maturation**, before Boltz-2.

**Maturation Mechanics (from PPIFlow preprint)**:
- **Interface Rotamer Enrichment**: identify energetic interface anchors (< −5 REU), fix rotamers, and enforce packing constraints.
- **Partial Flow Refinement**: perturb backbone to an intermediate flow state (e.g., t≈0.6) and regenerate unconstrained regions around fixed anchors.

---

## Core Design Decisions (Locked)

- **Insertion point**: After FilterFAMPNN, before Boltz‑2.
- **Stage naming**: “Backbone Maturation” for PPIFlow; “Affinity Maturation” reserved for IgGM at Step 6.
- **Scoring**: Rosetta‑only interface score (ipTM unavailable pre‑Boltz‑2).
- **Execution model**: Spawn child jobs that enter the queue; orchestrator handles launch timing and GPU assignment.
- **Constraints**: Reuse existing antibody constraint generator; do not create duplicate constraint scripts.
- **CDR detection**: Use existing ANARCII module/tooling; no new detection scripts.

---

## Parameters (New / Updated)

**Workflow / Nextflow**
- `run_maturation` (bool): enable PPIFlow stage
- `ppiflow_checkpoint` (string): `nanobody` / `antibody` / `binder` (mapped to ckpt)
- `ppiflow_weights` (path): shared weights directory
- `partial_flow_start_t` (float): start time for partial flow
- `maturation_redesign_temp` (float): FAMPNN temperature for redesign
- `maturation_anchor_threshold` (float): anchor energy threshold
- `maturation_min_improvement` (float): improvement threshold
- `maturation_filter_percentile` (float): optional percentile filter
- `antigen_chain`, `heavy_chain`, `light_chain` (string): chain mapping

**Schema / API**
- Add above params to `nextflow_schema.json` and API model configs.
- Ensure defaults match current RFantibody+ mode conventions.

---

## Execution Flow (Per Design)

1. **Interface Rotamer Enrichment (Anchor Selection)**  
   Use PyRosetta InterfaceAnalyzer with explicit antibody/antigen partner chains to identify energetic anchors (< −5 REU). Emit:
   - `anchors.json`
   - `interface_score.json`

2. **Detect CDRs (ANARCII)**  
   Reuse existing ANARCII module to produce comma‑separated CDR ranges for heavy/light chains.

3. **Partial Flow Refinement (PPIFlow)**  
   Use `sample_antibody_partial_flow.py` with:
   - `--fixed_positions` from anchor ranges
   - `--cdr_position` from ANARCII output
   - correct chain mapping and config

4. **Redesign Non‑Anchors (FAMPNN)**  
   Extend `prep_antibody_constraints.py` to accept `--extra_fixed_positions` and generate constraints CSV.

5. **Score Improvement**  
   Rosetta interface scoring + QC (RMSD, clashes, seq identity).

6. **Filter**  
   Threshold or percentile mode; percentile requires batch aggregation.

7. **Fallback**  
   If no anchors found, pass design through to Boltz‑2 unchanged.

---

## Implementation Plan (5 Phases)

### Phase 1 — Preflight + Canonical Dependencies
- Confirm container hosting for `ppiflow.sif` under `params.container_dir`.
- Decide baseline vs pinned CUDA/PyTorch stack; document only if deviation is required.
- Define shared weights path (e.g., `BMS_WEIGHTS/ppiflow`) and bind via `task.ext.containerOptions`.
- Add validation checks for missing weights.

### Phase 2 — Core Maturation Wiring
- Add CDR detection using existing ANARCII module (`modules/utils/anarci.nf`) and emit `cdr_positions_file`.
- Fix `RunPartialFlow` inputs to include **anchors + cdr_positions + complex_pdb**.
- Add zero-anchor fallback (passthrough to Boltz-2 when anchors list is empty).
- Ensure queue semantics: spawn submits jobs, orchestrator controls launch timing.
- Ensure **partial flow refinement** is explicitly invoked as the maturation step.

### Phase 3 — Constraints + Anchor Semantics
- Extend `scripts/prep_antibody_constraints.py` to accept `--extra_fixed_positions`.
- Implement anchor range grouping in `anchors_to_ppiflow_positions.py`.
- Ensure interface scoring uses explicit antibody/antigen partners (InterfaceAnalyzer setup).
- Treat **anchor selection** as the **interface rotamer enrichment** stage (the first of the two PPIFlow mechanisms).

### Phase 4 — Orchestration + Filtering
- Align spawn/wait/collect with `/api/jobs` (existing parent/child pattern).
- Add percentile aggregation if `maturation_filter_percentile` is enabled.
- Validate join keys and tuple shapes in the maturation DAG.

### Phase 5 — UI / Schema / Validation
- Wire new params through `nextflow.config`, schemas, API, and UI.
- Add **GitHub link in model settings section** (standard for all models).
- Update stage reporting for maturation steps.
- Run real smoke test (not `-dry-run`) + CLI sanity checks inside container.

---

## Core Wiring Rules (BioModStack)

- **Do not** redefine GPU containerOptions in module code; use labels and `task.ext.containerOptions`.
- **Do not** add new CDR detection scripts; reuse ANARCII tooling.
- **Do not** duplicate constraints logic; extend `prep_antibody_constraints.py`.
- **Do not** assume immediate child job start; respect queue→orchestrator handoff.

---

## Module / Workflow Wiring (Explicit)

**`modules/ppiflow.nf`** (new)
- `IdentifyAnchorResidues` (label `pyrosetta_tools`)
- `DetectCDRs` (reuse ANARCII module or wrapper)
- `RunPartialFlow` (label `gpu`)
- `RedesignNonAnchors` (label `FAMPNN`)
- `ScoreMaturationImprovement` (label `pyrosetta_tools`)
- `FilterByMaturation` (CPU)
- Spawn / wait / collect processes aligned with API queue semantics

**`workflows/antibody_denovo.nf`** (modify)
- Insert “Backbone Maturation” step after FilterFAMPNN.
- Join anchors + CDR outputs before calling `RunPartialFlow`.
- Add fallback branch when anchors list is empty.

---

## Required Changes (Consolidated)

**New**
- `modules/ppiflow.nf`
- `scripts/identify_anchors.py`
- `scripts/anchors_to_ppiflow_positions.py`
- `scripts/score_maturation.py`
- `scripts/filter_maturation.py`
- `scripts/spawn_maturation_children.py`
- `scripts/collect_maturation_outputs.py`
- `apptainer/ppiflow.def` (documented, hosted via `container_dir`)

**Modify**
- `scripts/prep_antibody_constraints.py` (add `--extra_fixed_positions`)
- `workflows/antibody_denovo.nf` (insert maturation stage)
- `nextflow.config` / schemas / API model configs (new params)
- UI settings panel (add GitHub link + params)

---

## Known Gaps (Resolved by This Plan)

1. **CLI mismatch** → Use `sample_antibody_partial_flow.py` with required args.  
2. **IgGM overlap** → Rename to "Backbone Maturation" to avoid confusion.  
3. **Missing weights** → Shared weights + bind + validation.  
4. **Anchor format** → Range grouping utility.  
5. **CDR detection** → Use ANARCII module.  
6. **Score QC** → Add RMSD/clash/identity metrics.  
7. **Bind mounts** → Use `task.ext.containerOptions`.  
8. **Missing module** → Create `modules/ppiflow.nf`.  
9. **API endpoint** → Use `/api/jobs`.  
10. **Percentile filtering** → Add aggregation step.  
11. **Queue semantics** → Respect orchestrator scheduling.  
12. **Duplicate constraints** → Extend existing script.  
13. **Input mismatch** → Join anchors + CDRs before `RunPartialFlow`.  
14. **Undefined variables** → Pass anchor positions explicitly.  
15. **Range grouping** → Implement in helper.  
16. **Acceptance criteria vague** → Explicit thresholds (above).  
17. **Zero-anchor fallback** → Passthrough.

---

## Validation & Testing

**Container sanity**:
```bash
apptainer exec --nv ppiflow.sif python3 -c "import torch; print(torch.cuda.is_available())"
apptainer exec --nv ppiflow.sif python3 /opt/ppiflow/sample_antibody_partial_flow.py --help
```

**Pipeline smoke** (real run, no dry-run):
```bash
nextflow run workflows/antibody_denovo.nf -profile test,gpu \
  --target_pdb tests/fixtures/6m0j_RBD.pdb \
  --rfantibody_num_designs 10 \
  --seqs_per_design 5 \
  --run_maturation true
```

**Acceptance metrics**:
- Aggregate interface improvements for matured vs baseline.
- Compute percent improved and false‑positive rate vs Boltz‑2 results.

---

## Model References

- **PPIFlow Preprint**: https://www.biorxiv.org/content/10.64898/2026.01.19.700484
- **PPIFlow GitHub**: https://github.com/Mingchenchen/PPIFlow
