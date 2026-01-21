# OpenMM Integration Implementation Plan (Merged)

## Goal Description

Integrate **OpenMM** and **OpenMM-ML** into BioModStack as a **domain-aware** refinement layer for de novo protein design workflows. This enables physics-based relaxation and MM-GBSA binding affinity scoring with specialized support for antibody/nanobody CDR optimization and mutagenesis validation.

**Key Objectives:**
1. Add OpenMM as **optional** post-generation refinement with **workflow-specific defaults**
2. Support **CDR-only relaxation** with framework restraints for antibody/nanobody workflows
3. Implement **compute tiers** (fast/standard/full) to balance accuracy vs GPU time
4. Enable **mutagenesis ΔΔG** calculations for variant validation
5. Expose comprehensive controls in Advanced Settings with sensible defaults

---

## Decisions (Final)

**Container Strategy:** Create a **separate `openmm.sif`** container with script binding (`${projectDir}/scripts:/scripts`) for maintainability.
**Default Compute Tiers by Workflow:**
- Antibody/Nanobody: `standard` (minimization + short equilibration)
- BoltzGen/BindCraft scaffolds: `fast` (minimization only)
- Mutagenesis validation: `standard` + ΔΔG calculation

---

## Documentation References

### Primary Documentation
| Resource | URL |
|----------|-----|
| OpenMM User Guide | https://docs.openmm.org/latest/userguide/ |
| OpenMM Python API | https://docs.openmm.org/latest/api-python/ |
| OpenMM-ML GitHub | https://github.com/openmm/openmm-ml |
| MACE-OFF Models | https://github.com/ACEsuit/mace-off |
| MACE Installation | https://github.com/ACEsuit/mace |
| ANI-2x (TorchANI) | https://github.com/aiqm/torchani |
| PDBFixer | https://github.com/openmm/pdbfixer |
| ANARCI (CDR mapping) | https://github.com/oxpig/ANARCI |

### Installation (UV Policy Compliant)

```bash
# Container build - UV installation at build time
uv pip install --system openmm openmm-ml mace-torch torchani pdbfixer mdtraj anarci
```

---

## Proposed Changes

### Phase 1: Foundation & Container

---

#### [NEW] [openmm.def](file:///home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/apptainer/openmm.def)

Separate container with script binding support:

```singularity
Bootstrap: docker
From: nvidia/cuda:12.1.1-devel-ubuntu22.04

%post
    apt-get update && apt-get install -y python3-pip python3-dev
    pip install uv
    uv pip install --system openmm openmm-ml mace-torch torchani pdbfixer mdtraj numpy anarci

%environment
    export OPENMM_CUDA_COMPILER=/usr/local/cuda/bin/nvcc

%labels
    Author BioModStack
    Version 1.0
    Description OpenMM + OpenMM-ML (MACE-OFF, ANI-2x) physics refinement

%runscript
    python3 "$@"
```

**Note:** Do not hardcode `OPENMM_DEFAULT_PLATFORM` in the container; choose CPU/GPU via the `--platform` argument in `relax_openmm.py` and `score_mmgbsa.py`.

**Nextflow label binding** (add to `nextflow.config`):
```groovy
withLabel: 'OpenMM' {
    container = "${params.container_dir}/openmm.sif"
    containerOptions = """--nv --env CUDA_DEVICE_ORDER=PCI_BUS_ID --env CUDA_VISIBLE_DEVICES=${params.gpu_id} \
        --bind ${projectDir}/scripts:/scripts \
        --bind ${projectDir}"""
}
```

---

#### [NEW] [relax_openmm.py](file:///home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/scripts/relax_openmm.py)

**Core relaxation script with CDR-aware and mutagenesis support:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--pdb` | path | required | Input PDB file |
| `--output` | path | required | Output relaxed PDB |
| `--output_json` | path | auto | Output metrics JSON |
| **Force Field Selection** ||||
| `--force_field` | choice | `amber14sb` | Primary: `amber14sb`, `mace-off`, `ani2x` |
| `--force_field_priority` | str | `amber14sb,mace-off,ani2x` | Fallback chain |
| `--mace_model_size` | choice | `medium` | MACE: `small`, `medium`, `large` |
| **Compute Tier** ||||
| `--compute_tier` | choice | `standard` | `fast`, `standard`, `full` |
| `--num_steps` | int | 100 | Minimization steps (fast) |
| `--equilibration_steps` | int | 2500 | Equilibration steps (standard) |
| **Antibody/Nanobody Mode** ||||
| `--cdr_only` | bool | false | Relax only CDR regions |
| `--restraint_mode` | choice | `none` | `none`, `framework`, `backbone` |
| `--restraint_strength` | float | 5.0 | kcal/mol/Å² restraint force |
| `--binder_chains` | str | `H,L` | Binder chain IDs |
| `--nanobody_mode` | bool | false | Single-chain mode (chain H only) |
| `--cdr_definition` | str | `auto` | `auto`, `anarci`, or `file:/path/to/cdr.json` |
| **General** ||||
| `--add_hydrogens` | bool | true | PDBFixer hydrogen addition |
| `--resolve_clashes` | bool | true | Pre-minimization clash resolution |
| `--platform` | choice | `auto` | `auto`, `cuda`, `cpu` |

**CDR-only requirements:** When `--cdr_only` is enabled, CDRs must be identified via ANARCI or an explicit `--cdr_definition` file. If CDR mapping fails, the script must fall back to whole-structure relaxation and emit a warning.

**Output JSON fields:**
```json
{
  "potential_energy_initial": -12345.6,
  "potential_energy_final": -12567.8,
  "energy_delta": -222.2,
  "energy_units": "kcal/mol",
  "clash_count_initial": 15,
  "clash_count_final": 0,
  "rmsd_backbone": 0.45,
  "rmsd_cdr": 1.23,
  "rmsd_framework": 0.12,
  "force_field_used": "amber14sb",
  "compute_tier": "standard",
  "cdr_only": true,
  "restraint_mode": "framework",
  "cdr_definition": "anarci"
}
```

---

#### [NEW] [score_mmgbsa.py](file:///home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/scripts/score_mmgbsa.py)

**MM-GBSA scoring with mode support:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--complex_pdb` | path | required | Complex PDB |
| `--output` | path | required | Output JSON |
| **Chain Configuration** ||||
| `--binder_chains` | str | `H,L` | Binder chain IDs |
| `--target_chains` | str | `A` | Target chain IDs |
| `--nanobody_mode` | bool | false | Single H chain |
| **Scoring Mode** ||||
| `--mmgbsa_mode` | choice | `interface` | `off`, `interface`, `stability`, `both` |
| `--solvent_model` | choice | `GBn2` | `GBn2`, `HCT`, `OBC2` |
| `--md_steps` | int | 5000 | Equilibration (2fs/step = 10ps) |
| **Mutagenesis** ||||
| `--wt_pdb` | path | optional | Wild-type reference for ΔΔG |
| `--validation_mode` | choice | `refine` | `refine`, `mutagenesis`, `both` |

**Output JSON fields:**
```json
{
  "mmgbsa_mode": "interface",
  "dg_bind": -45.6,
  "dg_complex": -1234.5,
  "dg_binder": -567.8,
  "dg_target": -621.1,
  "energy_units": "kcal/mol",
  "electrostatic": -89.2,
  "vdw": -34.5,
  "polar_solvation": 78.1,
  "sasa": -12.3,
  "ddg_mutation": null,
  "delta_interface_energy": null
}
```

---

#### [NEW] [select_top_n.py](file:///home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/scripts/select_top_n.py)

Helper script to select top-N designs for MM-GBSA scoring based on upstream metrics (e.g., iPTM, confidence).

**Exposed Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--pdbs` | path(s) | required | Candidate PDBs |
| `--scores` | path(s) | required | Metrics JSON/CSV from upstream stage |
| `--rank_by` | str | `iptm` | Metric to rank by |
| `--top_n` | int | 10 | Number of designs to keep |
| `--out_dir` | path | required | Output directory |

---

### Phase 2: Database Schema Extension

---

#### [MODIFY] [database.py](file:///home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/platform/api/database.py)

Add columns to `Design` class (after line 193):

```python
# ═══════════════════════════════════════════════════════════════════════════
# MOLECULAR MECHANICS / PHYSICS REFINEMENT (OpenMM)
# ═══════════════════════════════════════════════════════════════════════════
# Energy metrics
openmm_energy_initial = Column(Float, nullable=True)   # kcal/mol before
openmm_energy_final = Column(Float, nullable=True)     # kcal/mol after
openmm_energy_delta = Column(Float, nullable=True)     # improvement
openmm_clash_count = Column(Integer, nullable=True)    # remaining clashes
openmm_energy_units = Column(String(10), nullable=True) # kcal/mol

# CDR-specific metrics (antibody/nanobody)
openmm_energy_cdr = Column(Float, nullable=True)       # CDR region energy
openmm_energy_framework = Column(Float, nullable=True) # Framework energy
openmm_cdr_rmsd = Column(Float, nullable=True)         # CDR RMSD from input
openmm_framework_rmsd = Column(Float, nullable=True)   # Framework RMSD

# Configuration
openmm_force_field = Column(String(50), nullable=True) # amber14sb, mace-off, ani2x
openmm_compute_tier = Column(String(20), nullable=True) # fast, standard, full
openmm_cdr_only = Column(Boolean, nullable=True)       # CDR-only relaxation
openmm_restraint_mode = Column(String(20), nullable=True) # framework, backbone, none
openmm_relaxed_pdb = Column(String(500), nullable=True) # Path to relaxed structure

# MM-GBSA binding affinity
mmgbsa_mode = Column(String(20), nullable=True)        # interface, stability, both
mmgbsa_dg_bind = Column(Float, nullable=True)          # ΔG binding (kcal/mol)
mmgbsa_electrostatic = Column(Float, nullable=True)    # Electrostatic component
mmgbsa_vdw = Column(Float, nullable=True)              # Van der Waals component
mmgbsa_decomposition = Column(JSON, nullable=True)     # Full decomposition
mmgbsa_energy_units = Column(String(10), nullable=True) # kcal/mol

# Mutagenesis validation
openmm_ddg_mutation = Column(Float, nullable=True)     # ΔΔG vs wild-type
openmm_delta_interface = Column(Float, nullable=True)  # Interface energy delta
openmm_wt_reference = Column(String(500), nullable=True) # WT PDB path
```

---

#### [NEW] [add_openmm_fields.py](file:///home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/platform/api/migrations/add_openmm_fields.py)

```python
"""Add OpenMM/MM-GBSA fields to designs table."""
import sqlite3
import sys

def migrate(db_path: str = "./biomodstack.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    columns = [
        # Energy metrics
        ("openmm_energy_initial", "REAL"),
        ("openmm_energy_final", "REAL"),
        ("openmm_energy_delta", "REAL"),
        ("openmm_clash_count", "INTEGER"),
        ("openmm_energy_units", "TEXT"),
        # CDR-specific
        ("openmm_energy_cdr", "REAL"),
        ("openmm_energy_framework", "REAL"),
        ("openmm_cdr_rmsd", "REAL"),
        ("openmm_framework_rmsd", "REAL"),
        # Configuration
        ("openmm_force_field", "TEXT"),
        ("openmm_compute_tier", "TEXT"),
        ("openmm_cdr_only", "INTEGER"),  # Boolean as int
        ("openmm_restraint_mode", "TEXT"),
        ("openmm_relaxed_pdb", "TEXT"),
        # MM-GBSA
        ("mmgbsa_mode", "TEXT"),
        ("mmgbsa_dg_bind", "REAL"),
        ("mmgbsa_electrostatic", "REAL"),
        ("mmgbsa_vdw", "REAL"),
        ("mmgbsa_decomposition", "TEXT"),  # JSON as TEXT
        ("mmgbsa_energy_units", "TEXT"),
        # Mutagenesis
        ("openmm_ddg_mutation", "REAL"),
        ("openmm_delta_interface", "REAL"),
        ("openmm_wt_reference", "TEXT"),
    ]
    
    added = 0
    for col_name, col_type in columns:
        try:
            cursor.execute(f"ALTER TABLE designs ADD COLUMN {col_name} {col_type}")
            added += 1
        except sqlite3.OperationalError:
            pass  # Column exists
    
    conn.commit()
    conn.close()
    print(f"Migration complete. Added {added} new columns.")

if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "./biomodstack.db"
    migrate(db)
```

---

### Phase 3: Nextflow Module

---

#### [NEW] [openmm.nf](file:///home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/modules/openmm.nf)

```nextflow
/*
 * OpenMM Physics Refinement Module
 * 
 * Provides domain-aware relaxation and MM-GBSA scoring with:
 * - CDR-only mode for antibody/nanobody workflows
 * - Compute tiers (fast/standard/full)
 * - Mutagenesis ΔΔG calculation
 */

process OpenMMRelaxation {
    label 'OpenMM'
    label 'gpu'
    
    publishDir "${params.out_dir}/run/openmm", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "relaxed/*.pdb"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "relaxed/*.json"
    
    input:
    path pdbs
    
    output:
    path "relaxed/*.pdb", emit: pdbs
    path "relaxed/*.json", emit: metrics
    path "openmm_relax.log"
    
    script:
    // Force field with priority fallback
    def ffPriority = params.openmm_force_field_priority ?: 'amber14sb,mace-off,ani2x'
    def maceSize = params.openmm_mace_model_size ?: 'medium'
    
    // Compute tier
    def computeTier = params.openmm_compute_tier ?: 'standard'
    def numSteps = params.openmm_num_steps ?: 100
    def eqSteps = params.openmm_equilibration_steps ?: 2500
    
    // Antibody/nanobody mode
    def cdrOnly = params.openmm_cdr_only ?: false
    def restraintMode = params.openmm_restraint_mode ?: 'none'
    def restraintStrength = params.openmm_restraint_strength ?: 5.0
    def binderChains = params.openmm_binder_chains ?: 'H,L'
    def nanobodyMode = params.openmm_nanobody_mode ?: false
    def cdrDefinition = params.openmm_cdr_definition ?: 'auto'
    def addHydrogens = params.openmm_add_hydrogens == null ? true : params.openmm_add_hydrogens
    def resolveClashes = params.openmm_resolve_clashes == null ? true : params.openmm_resolve_clashes
    def platform = params.openmm_platform ?: 'auto'
    
    """
    mkdir -p relaxed
    
    for pdb in ${pdbs}; do
        base=\$(basename \$pdb .pdb)
        python3 /scripts/relax_openmm.py \\
            --pdb \$pdb \\
            --output relaxed/\${base}_relaxed.pdb \\
            --output_json relaxed/\${base}_relaxed.json \\
            --force_field_priority '${ffPriority}' \\
            --mace_model_size ${maceSize} \\
            --compute_tier ${computeTier} \\
            --num_steps ${numSteps} \\
            --equilibration_steps ${eqSteps} \\
            ${cdrOnly ? '--cdr_only' : ''} \\
            ${restraintMode != 'none' ? "--restraint_mode ${restraintMode}" : ''} \\
            ${restraintMode != 'none' ? "--restraint_strength ${restraintStrength}" : ''} \\
            --binder_chains '${binderChains}' \\
            ${nanobodyMode ? '--nanobody_mode' : ''} \\
            --cdr_definition '${cdrDefinition}' \\
            ${addHydrogens ? '--add_hydrogens' : ''} \\
            ${resolveClashes ? '--resolve_clashes' : ''} \\
            --platform ${platform} \\
            2>&1 | tee -a openmm_relax.log
    done
    """
}

process SelectTopNOpenMM {
    label 'cpu'
    
    input:
    path pdbs
    path rank_jsons
    
    output:
    path "topn/*.pdb", emit: pdbs
    
    script:
    def topN = params.openmm_mmgbsa_top_n ?: 10
    def rankBy = params.openmm_mmgbsa_rank_by ?: 'iptm'
    """
    mkdir -p topn
    python3 /scripts/select_top_n.py \\
        --pdbs ${pdbs} \\
        --scores ${rank_jsons} \\
        --top_n ${topN} \\
        --rank_by ${rankBy} \\
        --out_dir topn
    """
}

**MM-GBSA invocation rule:** Only run `OpenMMScore` when `openmm_compute_tier == 'full'` and `openmm_mmgbsa_mode != 'off'`.

process OpenMMScore {
    label 'OpenMM'
    label 'gpu'
    
    publishDir "${params.out_dir}/run/openmm", mode: 'copy', pattern: "*.json"
    publishDir "${params.out_dir}/run/openmm", mode: 'copy', pattern: "*.log"
    
    input:
    path complex_pdbs
    path wt_pdb, optional: true  // Optional: for mutagenesis ΔΔG
    
    output:
    path "scores/*.json", emit: scores
    path "mmgbsa.log"
    
    script:
    def mmgbsaMode = params.openmm_mmgbsa_mode ?: 'interface'
    def binderChains = params.openmm_binder_chains ?: 'H,L'
    def targetChains = params.openmm_target_chains ?: 'A'
    def solventModel = params.openmm_solvent_model ?: 'GBn2'
    def mdSteps = params.openmm_md_steps ?: 5000
    def validationMode = params.openmm_validation_mode ?: 'refine'
    def nanobodyMode = params.openmm_nanobody_mode ?: false
    def wtArg = wt_pdb ? "--wt_pdb ${wt_pdb}" : ''
    
    """
    mkdir -p scores
    
    for pdb in ${complex_pdbs}; do
        base=\$(basename \$pdb .pdb)
        python3 /scripts/score_mmgbsa.py \\
            --complex_pdb \$pdb \\
            --output scores/\${base}_mmgbsa.json \\
            --binder_chains '${binderChains}' \\
            --target_chains '${targetChains}' \\
            ${nanobodyMode ? '--nanobody_mode' : ''} \\
            --mmgbsa_mode ${mmgbsaMode} \\
            --solvent_model ${solventModel} \\
            --md_steps ${mdSteps} \\
            --validation_mode ${validationMode} \\
            ${wtArg} \\
            2>&1 | tee -a mmgbsa.log
    done
    """
}

// Convenience workflow for combined relaxation + scoring
workflow OPENMM_REFINEMENT {
    take:
    pdbs
    wt_pdb  // Optional
    rank_jsons  // Optional: upstream metrics for top-N selection
    
    main:
    OpenMMRelaxation(pdbs)
    
    def score_pdbs = OpenMMRelaxation.out.pdbs
    if (params.openmm_mmgbsa_mode != 'off' && params.openmm_compute_tier == 'full') {
        if (params.openmm_mmgbsa_top_n > 0 && rank_jsons) {
            SelectTopNOpenMM(OpenMMRelaxation.out.pdbs, rank_jsons)
            score_pdbs = SelectTopNOpenMM.out.pdbs
        }
        OpenMMScore(score_pdbs, wt_pdb)
        scores = OpenMMScore.out.scores
    } else {
        scores = Channel.empty()
    }
    
    emit:
    pdbs = OpenMMRelaxation.out.pdbs
    metrics = OpenMMRelaxation.out.metrics
    scores = scores
}
```

---

#### [MODIFY] [nextflow.config](file:///home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/nextflow.config)

Add comprehensive OpenMM parameters:

```groovy
// ═══════════════════════════════════════════════════════════════════════════
// OPENMM: DOMAIN-AWARE PHYSICS REFINEMENT
// ═══════════════════════════════════════════════════════════════════════════

// Master toggle
openmm_enabled = false

// Force field configuration (priority fallback)
openmm_force_field_priority = 'amber14sb,mace-off,ani2x'
openmm_mace_model_size = 'medium'  // small | medium | large

// Compute tiers: fast (minimize), standard (+equilibrate), full (+MM-GBSA)
openmm_compute_tier = 'standard'
openmm_num_steps = 100             // Minimization steps
openmm_equilibration_steps = 2500  // Equilibration (2fs/step = 5ps)
openmm_platform = 'auto'           // auto | cuda | cpu

// Antibody/Nanobody specialization
openmm_cdr_only = false            // Relax only CDR regions
openmm_restraint_mode = 'none'     // none | framework | backbone
openmm_restraint_strength = 5.0   // kcal/mol/Å²
openmm_binder_chains = 'H,L'       // Default for Fab
openmm_nanobody_mode = false       // Single H chain (VHH)
openmm_cdr_definition = 'auto'     // auto | anarci | file:/path/to/cdr.json

// MM-GBSA scoring modes
openmm_mmgbsa_mode = 'off'         // off | interface | stability | both
openmm_mmgbsa_top_n = 10           // Only score top N designs
openmm_mmgbsa_rank_by = 'iptm'     // Ranking metric for top-N gating
openmm_target_chains = 'A'         // Antigen/target chains
openmm_solvent_model = 'GBn2'      // GBn2 | HCT | OBC2
openmm_md_steps = 5000             // 10ps equilibration

// Mutagenesis validation
openmm_validation_mode = 'refine'  // refine | mutagenesis | both
openmm_wt_pdb = ''                 // Path to wild-type for ΔΔG

// Preprocessing
openmm_add_hydrogens = true
openmm_resolve_clashes = true
```

---

### Phase 4: Workflow Integration

---

#### [MODIFY] [antibody_denovo.nf](file:///home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/workflows/antibody_denovo.nf)

Add OpenMM with **antibody-specific defaults**:

```nextflow
include { OpenMMRelaxation ; SelectTopNOpenMM ; OpenMMScore } from '../modules/openmm.nf'

// After Boltz2 validation, with antibody defaults
if (params.openmm_enabled) {
    // Apply defaults for antibody workflow (only if not explicitly set)
    params.openmm_cdr_only = params.openmm_cdr_only ?: true
    params.openmm_restraint_mode = params.openmm_restraint_mode ?: 'framework'
    params.openmm_compute_tier = params.openmm_compute_tier ?: 'standard'
    params.openmm_mmgbsa_mode = params.openmm_mmgbsa_mode ?: 'interface'
    
    OpenMMRelaxation(BOLTZ2_VALIDATION.out.pdbs)
    
    if (params.openmm_mmgbsa_mode != 'off' && params.openmm_compute_tier == 'full') {
        def rank_jsons = BOLTZ2_VALIDATION.out.jsons
        if (params.openmm_mmgbsa_top_n > 0 && rank_jsons) {
            SelectTopNOpenMM(OpenMMRelaxation.out.pdbs, rank_jsons)
            OpenMMScore(SelectTopNOpenMM.out.pdbs)
        } else {
            OpenMMScore(OpenMMRelaxation.out.pdbs)
        }
    }
    
    OpenMMRelaxation.out.pdbs.set { validated_pdbs }
} else {
    BOLTZ2_VALIDATION.out.pdbs.set { validated_pdbs }
}
```

---

#### [MODIFY] [mutagenesis.nf](file:///home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/modules/mutagenesis.nf) *(if exists)*

Add **ΔΔG calculation** for mutagenesis validation:

```nextflow
if (params.openmm_enabled && params.openmm_validation_mode in ['mutagenesis', 'both']) {
    // Collect WT reference from input
    def wt_pdb = params.openmm_wt_pdb ?: ORIGINAL_STRUCTURE.out.pdb
    
    OpenMMScore(
        MUTANT_STRUCTURES.out.pdbs,
        wt_pdb
    )
    
    // Output includes ddg_mutation and delta_interface_energy
}
```

---

#### [MODIFY] [boltzgen.nf](file:///home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/modules/boltzgen.nf)

Add OpenMM with **scaffold defaults**:

```nextflow
// After FilterBoltzGen, with scaffold defaults
if (params.openmm_enabled) {
    def scaffoldParams = [
        openmm_cdr_only: false,  // Full structure for scaffolds
        openmm_compute_tier: params.openmm_compute_tier ?: 'fast',
        openmm_mmgbsa_mode: params.openmm_mmgbsa_mode ?: 'off'
    ]
    
    OpenMMRelaxation(FilterBoltzGen.out.pdbs)
    OpenMMRelaxation.out.pdbs.set { refined_pdbs }
} else {
    FilterBoltzGen.out.pdbs.set { refined_pdbs }
}
```

---

### Phase 5: Frontend Integration

---

#### [MODIFY] UI Templates (BoltzGenTemplate.tsx, BindCraftTemplate.tsx, AntibodyDeNovoTemplate.tsx)

Add **collapsible "Physics Refinement (OpenMM)" section** in Advanced Settings:

| Control | Type | Parameter | Default | Options |
|---------|------|-----------|---------|---------|
| Enable Physics Refinement | Toggle | `openmm_enabled` | false | - |
| **Compute Settings** |||||
| Compute Tier | Dropdown | `openmm_compute_tier` | `standard` | Fast, Standard, Full |
| Force Field Priority | Text | `openmm_force_field_priority` | `amber14sb,mace-off,ani2x` | - |
| MACE Model Size | Dropdown | `openmm_mace_model_size` | `medium` | Small, Medium, Large |
| Minimization Steps | Slider | `openmm_num_steps` | 100 | 10-1000 |
| **Antibody/Nanobody Mode** |||||
| CDR-Only Relaxation | Toggle | `openmm_cdr_only` | true (antibody) | - |
| Restraint Mode | Dropdown | `openmm_restraint_mode` | `framework` | None, Framework, Backbone |
| Restraint Strength | Slider | `openmm_restraint_strength` | 5.0 | 0.1-50.0 |
| Binder Chains | Text | `openmm_binder_chains` | `H,L` | - |
| Nanobody Mode | Toggle | `openmm_nanobody_mode` | false | - |
| **MM-GBSA Scoring** |||||
| MM-GBSA Mode | Dropdown | `openmm_mmgbsa_mode` | `off` | Off, Interface, Stability, Both |
| Score Top N Only | Number | `openmm_mmgbsa_top_n` | 10 | 1-100 |
| Target Chains | Text | `openmm_target_chains` | `A` | - |
| Solvent Model | Dropdown | `openmm_solvent_model` | `GBn2` | GBn2, HCT, OBC2 |
| **Mutagenesis** (conditional) |||||
| Validation Mode | Dropdown | `openmm_validation_mode` | `refine` | Refine, Mutagenesis, Both |
| Wild-Type Reference | File | `openmm_wt_pdb` | - | PDB upload |

**Conditional visibility:**
- "Antibody/Nanobody Mode" section visible when workflow is `antibody_denovo` or `bindcraft` with VHH
- "Mutagenesis" section visible when workflow is `mutagenesis`

---

#### [MODIFY] Results Viewer

Add new columns and chart support:

**Table columns:**
- `OpenMM ΔE` (energy delta, sortable)
- `Clashes` (clash count, sortable)
- `CDR RMSD` (for antibody workflows)
- `MM-GBSA ΔG` (binding affinity, sortable)
- `ΔΔG` (mutagenesis workflows only)

**Charts tab:**
- "Energy vs iPTM" scatter plot
- "MM-GBSA ΔG Distribution" histogram
- "CDR RMSD vs Framework RMSD" (antibody workflows)

---

## Verification Plan

### Automated Tests

#### 1. Container Build
```bash
cd /home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/apptainer
sudo apptainer build openmm.sif openmm.def

# Verify imports
apptainer exec --nv openmm.sif python3 -c "
import openmm
import openmmml
import mace
import torchani
print('All imports successful')
print(f'OpenMM version: {openmm.__version__}')
"
```

#### 2. Script Tests
```bash
# Test relax_openmm.py with CDR-only mode
apptainer exec --nv -B scripts:/scripts apptainer/openmm.sif \
    python3 /scripts/relax_openmm.py \
    --pdb data/test/nanobody.pdb \
    --output /tmp/relaxed.pdb \
    --compute_tier fast \
    --cdr_only \
    --restraint_mode framework \
    --cdr_definition anarci \
    --nanobody_mode \
    --platform cpu

# Validate output
python3 -c "import json; d=json.load(open('/tmp/relaxed.json')); print(f'CDR RMSD: {d[\"rmsd_cdr\"]:.2f}Å')"
```

#### 2b. Failure-Mode Tests
```bash
# CDR mapping fallback (no ANARCI)
apptainer exec --nv -B scripts:/scripts apptainer/openmm.sif \
    python3 /scripts/relax_openmm.py \
    --pdb data/test/nanobody.pdb \
    --output /tmp/relaxed_fallback.pdb \
    --compute_tier fast \
    --cdr_only \
    --cdr_definition file:/tmp/nonexistent.json \
    --platform cpu

# MM-GBSA without WT
apptainer exec --nv -B scripts:/scripts apptainer/openmm.sif \
    python3 /scripts/score_mmgbsa.py \
    --complex_pdb data/test/complex.pdb \
    --output /tmp/mmgbsa.json \
    --mmgbsa_mode interface \
    --md_steps 1000
```

#### 3. Database Migration
```bash
python3 platform/api/migrations/add_openmm_fields.py platform/biomodstack.db
sqlite3 platform/biomodstack.db ".schema designs" | grep -E "openmm|mmgbsa"
```

### Manual Verification

1. Submit BoltzGen nanobody job with:
   - `openmm_enabled=true`
   - `openmm_cdr_only=true`
   - `openmm_compute_tier=standard`
   
2. Monitor Nextflow for `OpenMMRelaxation` process

3. Verify Results Viewer shows new metrics

---

## Implementation Order

| Phase | Effort | Deliverables |
|-------|--------|--------------|
| 1. Container + Scripts | 3-4 hrs | `openmm.def`, `relax_openmm.py`, `score_mmgbsa.py` |
| 2. Database | 30 min | Migration script, model updates |
| 3. Nextflow | 2-3 hrs | `openmm.nf`, config params |
| 4. Workflow Integration | 2-3 hrs | Updates to 4 workflows |
| 5. Frontend | 3-4 hrs | UI components, Results Viewer |

**Total: 11-15 hours**

---

## Rollback Plan

1. Set `openmm_enabled = false` globally (immediate disable)
2. All changes are additive—no existing functionality modified
3. Database columns nullable—no data loss on rollback
4. Container is separate—can be deleted without affecting other containers
