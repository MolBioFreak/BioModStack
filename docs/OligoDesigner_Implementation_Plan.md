# Oligo Designer — Complete Implementation Plan

**Status:** APPROVED  
**Version:** 1.0  
**Date:** 2026-01-21  
**Priority:** HIGH  

---

## Executive Summary

**Oligo Designer** is a first-class BioModStack workflow for de novo design of nucleic acids and nucleoprotein complexes. It combines **RFDpoly** (diffusion-based multi-polymer generation) with **Boltz-2** validation and optional **OpenMM** physics refinement.

### Why This Matters
- **RNA therapeutics** are a $10B+ market (mRNA vaccines, siRNA drugs)
- **DNA aptamers** for biosensors and diagnostics
- **Protein-nucleic acid complexes** for CRISPR engineering
- **Synthetic biology** circuits using RNA regulatory elements

---

## Supported Design Modes

### Mode 1: RNA De Novo Design
Design RNA molecules with specific 3D structures:
- **Aptamers** — Small RNA molecules that bind specific targets
- **Riboswitches** — RNA elements that change conformation upon ligand binding  
- **Hairpin loops** — Stem-loop structures for regulatory elements
- **Pseudoknots** — Complex RNA folds for catalytic ribozymes
- **siRNA/shRNA scaffolds** — Optimized silencing structures

### Mode 2: DNA De Novo Design  
Design DNA with controlled topology:
- **DNA aptamers** — Therapeutic and diagnostic binding molecules
- **G-quadruplexes** — Four-stranded DNA structures
- **Cruciform structures** — Branched DNA junctions
- **Origami building blocks** — Modular DNA nanostructure components

### Mode 3: Protein-DNA Complexes
Design proteins bound to their cognate DNA:
- **Transcription factors** — Custom DNA-binding proteins
- **Zinc finger proteins** — Modular DNA recognition
- **Nucleosome-like assemblies** — Histone-DNA wrapping
- **CRISPR guide optimization** — sgRNA + Cas protein complexes

### Mode 4: Protein-RNA Complexes (Ribonucleoproteins)
Design proteins bound to RNA:
- **RNA-binding proteins** — Custom RBPs for synthetic biology
- **tRNA synthetase-like proteins** — Charging machinery
- **Spliceosome components** — snRNP-like assemblies
- **Ribosome subunit mimics** — Synthetic translation elements

### Mode 5: Multi-Component Assemblies
Complex systems with multiple polymer types:
- **DNA + RNA + Protein** — Three-way complexes
- **Multi-chain homodimers** — Symmetric nucleic acid assemblies
- **Hetero-oligomers** — Mixed DNA/RNA strands

---

## Complete Parameter Reference

### Core Generation Parameters

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `rfdpoly_enabled` | bool | `true` | - | Master enable |
| `rfdpoly_num_designs` | int | `4` | 1-64 | Designs per run |
| `rfdpoly_diffusion_steps` | int | `50` | 10-200 | Denoising steps (quality) |
| `rfdpoly_noise_scale` | float | `1.0` | 0.1-2.0 | Noise injection scale |

### Model Selection

| Parameter | Type | Default | Options | Description |
|-----------|------|---------|---------|-------------|
| `rfdpoly_checkpoint` | enum | `generalized` | `generalized`, `rna_optimized` | Model weights |
| `rfdpoly_weights_path` | path | auto | - | Custom checkpoint path |

### Chain Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `rfdpoly_polymer_chains` | list | `['protein']` | Chain types: `dna`, `rna`, `protein` |
| `rfdpoly_contigs` | string | `'100'` | Space-separated lengths per chain |
| `rfdpoly_chain_order` | string | auto | Explicit chain ordering |

### Length Specifications

| Format | Example | Meaning |
|--------|---------|---------|
| Fixed | `50` | Exactly 50 residues/bases |
| Range | `40-60` | Random length in range |
| Multi-chain | `"33 33 75"` | 33bp DNA, 33nt RNA, 75aa protein |

### Motif Scaffolding (Conditional Generation)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `rfdpoly_input_pdb` | path | - | Template structure for scaffolding |
| `rfdpoly_motif_residues` | string | - | Residues to preserve from template |
| `rfdpoly_scaffold_around` | bool | `false` | Design around fixed motif |

### Advanced Inference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `rfdpoly_temperature` | float | `1.0` | Sampling temperature |
| `rfdpoly_seed` | int | random | Random seed for reproducibility |
| `rfdpoly_symmetry` | string | - | Symmetry constraints (C2, C3, etc.) |

### Checkpoint Mapping (Addendum Fix #1)

| UI Label | Filename | Use Case |
|----------|----------|----------|
| `generalized` | `train_session2024-07-08_1720455712_BFF_3.00.pt` | All polymer types |
| `rna_optimized` | `train_session2024-06-27_1719522052_BFF_7.00.pt` | RNA-only design |

### RFDpoly CLI Mapping (Addendum Fix #4)

The Nextflow module must translate BioModStack params to RFDpoly Hydra keys:

| BioModStack Param | RFDpoly Hydra Key | Example |
|-------------------|-------------------|---------|
| `rfdpoly_diffusion_steps` | `diffuser.T` | `diffuser.T=50` |
| `rfdpoly_num_designs` | `inference.num_designs` | `inference.num_designs=4` |
| `rfdpoly_contigs` | `contigmap.contigs` | `contigmap.contigs=['33 75']` |
| `rfdpoly_polymer_chains` | `contigmap.polymer_chains` | `contigmap.polymer_chains=['dna','protein']` |
| `rfdpoly_weights_path` | `inference.ckpt_path` | `inference.ckpt_path=/path/to/weights.pt` |
| `rfdpoly_input_pdb` | `inference.input_pdb` | `inference.input_pdb=/path/to/template.pdb` |
| (output prefix) | `inference.output_prefix` | `inference.output_prefix=./design_001` |

**Required config flag (Addendum Fix #2):** Always pass `--config-name=multi_polymer`

**Default input PDB (Addendum Fix #3):** Provide `tools/RFDpoly/rf_diffusion/test_data/DBP035.pdb` as fallback

---

## Validation Pipeline

### Boltz-2 Validation
All designs are validated with Boltz-2 structure prediction:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `oligo_validate_boltz` | bool | `true` | Enable Boltz-2 validation |
| `oligo_min_plddt` | float | `70.0` | Minimum pLDDT threshold |
| `oligo_min_ptm` | float | `0.5` | Minimum pTM threshold |
| `oligo_max_pae` | float | `15.0` | Maximum PAE threshold |

### OpenMM Physics Refinement (Optional)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `oligo_refine_openmm` | bool | `false` | Enable physics relaxation |
| `oligo_openmm_tier` | enum | `standard` | `fast`, `standard`, `full` |
| `oligo_implicit_solvent` | bool | `true` | Use implicit solvent model |

### Chain Type Serialization (Addendum Fix #7)

UI must serialize chain types exactly as RFDpoly expects:
- Allowed values: `dna`, `rna`, `protein` (lowercase only)
- Format: `['dna','protein']` (single quotes, comma-separated, bracketed)
- Nextflow module enforces this format before CLI construction

### Downstream Compatibility (Addendum Fix #8)

**RFDpoly → Boltz-2 Conversion:**
- RFDpoly outputs PDB with chain IDs
- `prep_boltz_oligo.py` detects polymer type per chain:
  - DNA: residues DA/DT/DG/DC
  - RNA: residues A/U/G/C (or RA/RU/RG/RC)
  - Protein: standard amino acids
- Generates Boltz-2 YAML with correct `dna`/`rna`/`protein` blocks

**RFDpoly → OpenMM Compatibility:**
- OpenMM supports nucleic acids via AMBER force fields (`ff14SB` for NA)
- Implicit solvent (`OBC2`) works for RNA/DNA
- MM-GBSA scoring NOT validated for NA — disable for oligo runs

### NA-Aware Metrics (Addendum Fix #6)

Protein-centric metrics (SS count, RoG) are invalid for nucleic acids. Use:

| Metric | Protein | RNA/DNA | Notes |
|--------|---------|---------|-------|
| pLDDT | ✅ | ✅ | Per-residue confidence (Boltz-2) |
| pTM | ✅ | ✅ | Global fold quality |
| PAE | ✅ | ✅ | Pairwise alignment error |
| Secondary Structure | ✅ | ❌ | Skip for NA-only |
| Radius of Gyration | ⚠️ | ⚠️ | Valid but interpret differently |
| Clash Score | ✅ | ✅ | OpenMM steric check |
| Base Pairing | ❌ | ✅ | Future: RNA/DNA-specific |

---

## Workflow Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        OLIGO DESIGNER WORKFLOW                             │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │                         STAGE 1: INPUT                               │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │ │
│  │  │ Design Mode │  │ Chain Config│  │   Motif     │                  │ │
│  │  │  Selector   │  │  (lengths)  │  │ (optional)  │                  │ │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                  │ │
│  └─────────┼────────────────┼────────────────┼──────────────────────────┘ │
│            └────────────────┴────────────────┘                             │
│                             │                                              │
│                             ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │                      STAGE 2: GENERATION                             │ │
│  │                                                                      │ │
│  │   ┌─────────────────────────────────────────────────────────────┐   │ │
│  │   │                      RFDpoly                                │   │ │
│  │   │   • Multi-polymer diffusion model                          │   │ │
│  │   │   • Generates N backbone structures                         │   │ │
│  │   │   • Outputs: PDB coordinates for all chains                │   │ │
│  │   └─────────────────────────────────────────────────────────────┘   │ │
│  │                                                                      │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│                             │                                              │
│                             ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │                      STAGE 3: VALIDATION                             │ │
│  │                                                                      │ │
│  │   ┌─────────────────────────────────────────────────────────────┐   │ │
│  │   │                      Boltz-2                                │   │ │
│  │   │   • All-atom structure prediction                          │   │ │
│  │   │   • Nucleic acid + protein complex support                 │   │ │
│  │   │   • Outputs: pLDDT, pTM, PAE metrics                       │   │ │
│  │   └─────────────────────────────────────────────────────────────┘   │ │
│  │                                                                      │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│                             │                                              │
│                             ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │                      STAGE 4: FILTER                                 │ │
│  │                                                                      │ │
│  │   • Filter by pLDDT ≥ threshold                                     │ │
│  │   • Filter by pTM ≥ threshold                                       │ │
│  │   • Filter by PAE ≤ threshold                                       │ │
│  │                                                                      │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│                             │                                              │
│                    ┌────────┴────────┐                                     │
│                    ▼                 ▼                                     │
│  ┌──────────────────────┐  ┌──────────────────────┐                       │
│  │ STAGE 5a: REFINEMENT │  │ STAGE 5b: SKIP       │                       │
│  │ (if openmm_enabled)  │  │ (direct to results)  │                       │
│  │                      │  │                      │                       │
│  │  ┌────────────────┐  │  │                      │                       │
│  │  │    OpenMM      │  │  │                      │                       │
│  │  │  Minimization  │  │  │                      │                       │
│  │  └────────────────┘  │  │                      │                       │
│  └──────────┬───────────┘  └──────────┬───────────┘                       │
│             └──────────────┬──────────┘                                    │
│                            ▼                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │                      STAGE 6: RESULTS                                │ │
│  │                                                                      │ │
│  │   • all_designs.csv — All generated designs with metrics            │ │
│  │   • best_designs.csv — Filtered high-confidence designs             │ │
│  │   • PDB files — Structural coordinates                              │ │
│  │   • Validation metrics — pLDDT, pTM, PAE per design                 │ │
│  │                                                                      │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## Frontend UI Specification

### Template: OligoDesignerTemplate.tsx

#### Section 1: Design Mode Selector
```
┌─────────────────────────────────────────────────────────────┐
│  DESIGN MODE                                                │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │  🧬 RNA     │ │  🔷 DNA     │ │  🔗 Complex │           │
│  │  Aptamer    │ │  Aptamer    │ │  Design     │           │
│  └─────────────┘ └─────────────┘ └─────────────┘           │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │  📎 Protein │ │  🧪 RNP     │ │  ⚙️ Custom  │           │
│  │  -DNA       │ │  Complex    │ │  Multi-Poly │           │
│  └─────────────┘ └─────────────┘ └─────────────┘           │
└─────────────────────────────────────────────────────────────┘
```

#### Section 2: Chain Configuration
```
┌─────────────────────────────────────────────────────────────┐
│  CHAIN CONFIGURATION                                        │
│                                                             │
│  Chain 1:  [ DNA      ▼]  Length: [  33  ] bases           │
│  Chain 2:  [ Protein  ▼]  Length: [  75  ] residues        │
│                                                             │
│  [+ Add Chain]                                              │
│                                                             │
│  ☐ Use length range: Min [__] Max [__]                     │
└─────────────────────────────────────────────────────────────┘
```

#### Section 3: Generation Settings
```
┌─────────────────────────────────────────────────────────────┐
│  GENERATION SETTINGS                                        │
│                                                             │
│  Number of Designs:     [  4  ]  (1-64)                    │
│                                                             │
│  Quality Preset:   [○ Fast  ● Standard  ○ High Quality]    │
│                         25       50           100  steps    │
│                                                             │
│  Model Checkpoint: [Generalized (all polymers)    ▼]       │
│                    - Generalized (all polymers)             │
│                    - RNA-optimized                          │
│                    - Custom checkpoint...                   │
│                                                             │
│  ▸ Advanced Options                                         │
│    Temperature: [1.0]  Noise Scale: [1.0]  Seed: [auto]    │
└─────────────────────────────────────────────────────────────┘
```

#### Section 4: Motif Scaffolding (Collapsible)
```
┌─────────────────────────────────────────────────────────────┐
│  ▸ MOTIF SCAFFOLDING (Optional)                             │
│                                                             │
│  ☐ Scaffold around existing structure                       │
│                                                             │
│  Template PDB:   [Browse...]  filename.pdb                  │
│  Fixed Residues: [A:10-25, B:1-15]  (chain:range)          │
└─────────────────────────────────────────────────────────────┘
```

#### Section 5: Validation Settings
```
┌─────────────────────────────────────────────────────────────┐
│  VALIDATION                                                 │
│                                                             │
│  ☑ Validate with Boltz-2                                   │
│    Min pLDDT: [══════●══════] 70                           │
│    Min pTM:   [═════●═══════] 0.5                          │
│    Max PAE:   [═══════●═════] 15                           │
│                                                             │
│  ☐ Physics Refinement (OpenMM)                             │
│    Compute Tier: [Standard ▼]                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: Foundation (Day 1)
- [ ] Download RFDpoly container (SE3nv.sif)
- [ ] Download model weights (generalized + RNA-optimized)
- [ ] Clone RFDpoly repository to `tools/`
- [ ] Test container execution manually

### Phase 2: Nextflow Module (Day 1-2)
- [ ] Create `modules/rfdpoly.nf` with RFDPolyDesign process
- [ ] Create `scripts/prep_boltz_oligo.py` for chain type detection
- [ ] Add container config to `nextflow.config`
- [ ] Add all parameters to `nextflow.config`

### Phase 3: Workflow Integration (Day 2)
- [ ] Create `workflows/oligo_design.nf`
- [ ] Integrate with Boltz-2 validation
- [ ] Integrate with OpenMM refinement (optional)
- [ ] Add routing in `main.nf`

### Phase 4: Frontend (Day 2-3)
- [ ] Create `OligoDesignerTemplate.tsx`
- [ ] Add to workflow catalog (`inputs.yaml`)
- [ ] Wire to job submission endpoint
- [ ] Add preset design modes (RNA aptamer, DNA aptamer, etc.)

### Phase 5: Testing (Day 3)
- [ ] Unit test: Container runs
- [ ] Integration test: RNA aptamer generation
- [ ] Integration test: Protein-DNA complex
- [ ] UI test: Job submission and tracking

---

## File Manifest

| File | Status | Description |
|------|--------|-------------|
| `containers/rfdpoly.sif` | NEW | Pre-built container from IPD |
| `tools/RFDpoly/` | NEW | Cloned repository |
| `$BMS_WEIGHTS/rfdpoly/*.pt` | NEW | Model checkpoints |
| `modules/rfdpoly.nf` | NEW | Nextflow module |
| `workflows/oligo_design.nf` | NEW | Workflow definition |
| `scripts/prep_boltz_oligo.py` | NEW | Chain type detection |
| `platform/frontend/.../OligoDesignerTemplate.tsx` | NEW | UI template |
| `platform/api/config/models/oligo_design.yaml` | NEW | API config |
| `nextflow.config` | MODIFY | Add RFDpoly params |
| `main.nf` | MODIFY | Add workflow routing |
| `platform/api/config/inputs.yaml` | MODIFY | Catalog entry |

---

## Estimated Effort

| Phase | Hours |
|-------|-------|
| Phase 1: Foundation | 1 |
| Phase 2: Nextflow Module | 3 |
| Phase 3: Workflow Integration | 2 |
| Phase 4: Frontend | 4 |
| Phase 5: Testing | 2 |
| **Total** | **12 hours** |

---

## Future Extensions

### Sequence Optimization Layer
After structural generation, optimize nucleic acid sequences:
- **gRNAde** — RNA sequence design from structure
- **RNAinverse** (ViennaRNA) — Inverse folding for RNA
- **Thermodynamic optimization** — Tm, GC%, hairpin stability

### Experimental Export
- Primer design for DNA synthesis
- Oligo ordering format (IDT, Twist)
- SHAPE-seq accessibility prediction in silico

### Advanced Topologies
- Symmetric oligomers (homodimers, trimers)
- Circular RNA/DNA
- Knotted topologies

---

## References

- [RFDpoly GitHub](https://github.com/RosettaCommons/RFDpoly)
- [RFDpoly Documentation](https://rosettacommons.github.io/RFDpoly/)
- [Preprint: De novo design of RNA and nucleoprotein complexes](https://www.biorxiv.org/content/10.1101/2025.10.01.679929v1)
- [Boltz-2 Nucleic Acid Support](https://github.com/jwohlwend/boltz)
- [OpenMM Nucleic Acid Force Fields](https://docs.openmm.org/)

---

## Addendum: Critical Alignment With RFDpoly Docs ✅ RESOLVED

This addendum lists concerns found while cross‑checking the plan against the RFDpoly README and official documentation. **All issues have been addressed inline above.**

### 1) **Checkpoint naming is not aligned to actual weights**
**Concern:** The plan uses abstract labels (`generalized`, `rna_optimized`) without mapping to the actual checkpoint filenames that RFDpoly expects via `inference.ckpt_path`.  
**Why it matters:** Users may select a checkpoint in UI that does not exist on disk, causing runtime failures.  
**Recommendation:** Add a mapping table from UI labels to concrete filenames, and store those paths in `nextflow.config`.  
**Docs:** RFDpoly README lists specific checkpoints and uses `inference.ckpt_path` for selection.  
- https://github.com/RosettaCommons/RFDpoly
- https://rosettacommons.github.io/RFDpoly/

### 2) **Missing `--config-name=multi_polymer` requirement**
**Concern:** The plan doesn’t specify the required config selection shown in the README examples.  
**Why it matters:** RFDpoly uses Hydra configs; wrong config means wrong defaults and possible schema errors.  
**Recommendation:** Hardcode `--config-name=multi_polymer` in the Nextflow module unless a user overrides it explicitly.  
**Docs:** README demo uses `--config-name=multi_polymer`.  
- https://github.com/RosettaCommons/RFDpoly

### 3) **Unconditional runs may still require `inference.input_pdb`**
**Concern:** README notes failures can occur unless a real input PDB is provided, even for “unconditional” runs.  
**Why it matters:** The pipeline could appear broken for users with no input PDB.  
**Recommendation:** Provide a default PDB (e.g., repo test_data) and document it in config, with a clear override option.  
**Docs:** README troubleshooting note recommends setting `inference.input_pdb`.  
- https://github.com/RosettaCommons/RFDpoly

### 4) **Parameter names don’t map to RFDpoly’s actual CLI**
**Concern:** Plan parameters (e.g., `rfdpoly_diffusion_steps`, `rfdpoly_polymer_chains`) are not mapped to the actual RFDpoly Hydra keys (e.g., `diffuser.T`, `contigmap.contigs`, `contigmap.polymer_chains`).  
**Why it matters:** Without explicit mapping, parameters will be ignored or misrouted.  
**Recommendation:** Add a CLI mapping section and implement exact key translation in the Nextflow module.  
**Docs:** README example uses `diffuser.T`, `contigmap.contigs`, `contigmap.polymer_chains`, `inference.output_prefix`.  
- https://github.com/RosettaCommons/RFDpoly

### 5) **Output format assumptions are unverified**
**Concern:** The plan adds `rfdpoly_output_format` (pdb/cif) but RFDpoly docs do not confirm a switchable output format.  
**Why it matters:** UI options could mislead users or fail silently.  
**Recommendation:** Verify supported outputs in the repo/docs; only expose UI toggles that are real.  
**Docs:** README shows output prefix and PDB examples but no explicit format toggle.  
- https://github.com/RosettaCommons/RFDpoly

### 6) **Backbone metrics are protein-centric**
**Concern:** Current SS/RoG filters in BioModStack are peptide‑backbone‑centric and may be invalid for DNA/RNA.  
**Why it matters:** Filtering could remove valid nucleic acid designs or produce nonsensical metrics.  
**Recommendation:** Add NA‑aware metrics (or disable protein‑centric filters for NA‑only runs).  
**Docs:** RFDpoly focuses on multi‑polymer outputs (DNA/RNA/protein), so metrics must match the polymer type.  
- https://github.com/RosettaCommons/RFDpoly

### 7) **Chain type serialization must match RFDpoly expectations**
**Concern:** The plan’s UI needs to serialize chain types exactly as `['dna','rna','protein']` for `contigmap.polymer_chains`.  
**Why it matters:** Mismatched strings or formatting will break RFDpoly input parsing.  
**Recommendation:** Enforce exact allowed values and output formatting in the UI and in the Nextflow module.  
**Docs:** README example uses `contigmap.polymer_chains=['dna','protein']` syntax.  
- https://github.com/RosettaCommons/RFDpoly

### 8) **Plan assumes downstream validation compatibility without explicit checks**
**Concern:** The plan assumes Boltz‑2 and OpenMM can handle multi‑polymer outputs with no extra conversion or flags.  
**Why it matters:** Mixed polymer topologies can require format conversion or specific templates.  
**Recommendation:** Add a compatibility checkpoint: “RFDpoly output → Boltz‑2/OpenMM input” with explicit conversion rules and tested examples.  
**Docs:** RFDpoly README provides output expectations; Boltz/OpenMM docs should be checked for NA compatibility.  
- https://github.com/RosettaCommons/RFDpoly
- https://github.com/jwohlwend/boltz
- https://docs.openmm.org/

---

## Addendum 2: Implementation Notes (Agent Feedback)

Additional technical concerns identified during integration of Addendum 1:

### 9) **IGSO3 Cache Cold Start**
**Concern:** RFDpoly README notes first run takes extra time to "precompute the IGSO3 cache."  
**Impact:** Initial test runs may timeout or appear hung; users may report false failures.  
**Recommendation:** Document expected first-run delay (~2-3 min extra). Consider pre-warming cache during container setup.

### 10) **RNA vs DNA Residue Detection Ambiguity**
**Concern:** `prep_boltz_oligo.py` must detect polymer type from residue names. RNA residues vary by convention:
- Standard: `A`, `U`, `G`, `C`
- Explicit: `RA`, `RU`, `RG`, `RC`  
- DNA is more reliable: `DA`, `DT`, `DG`, `DC`  
**Impact:** Misclassification → wrong Boltz-2 YAML blocks → failed validation.  
**Recommendation:** Implement robust detection with fallback heuristics (phosphate backbone check, C2' hydroxyl).

### 11) **OpenMM Nucleic Acid Force Field Selection**
**Concern:** Plan stated `ff14SB` for NA, but this is protein-only.  
**Correct options:**
- RNA/DNA: `ff99bsc0` (older) or `OL15` (recommended for DNA), `OL3` (RNA)
- AMBER14SB is for proteins only  
**Impact:** Using wrong force field → nonsensical energies or crashes.  
**Recommendation:** Add `oligo_force_field` param with NA-aware defaults; disable MM-GBSA for pure NA runs until validated.

### 12) **Symmetry Support Unverified**
**Concern:** Plan includes `rfdpoly_symmetry` param, but RFDpoly docs don't explicitly confirm symmetry support.  
**Impact:** May be non-functional or require undocumented Hydra keys.  
**Recommendation:** Mark as "experimental" in UI; test before exposing. May require checking RFDpoly source code.

### 13) **Output Format Toggle (Deferred from #5)**
**Concern:** RFDpoly likely supports output format via Hydra config (inherited from RFdiffusion) but isn't documented.  
**Recommendation:** Leave disabled until confirmed; low priority.
