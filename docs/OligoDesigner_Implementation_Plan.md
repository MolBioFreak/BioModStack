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
| `rfdpoly_output_format` | enum | `pdb` | `pdb`, `cif` |

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
| `models/rfdpoly/*.pt` | NEW | Model checkpoints |
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
