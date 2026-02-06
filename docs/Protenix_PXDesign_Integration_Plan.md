# Protenix & PXDesign Integration Plan

> **Author**: BioModStack AI Assistant  
> **Date**: 2026-02-06  
> **Status**: Planning

## Executive Summary

Integration of ByteDance's Protenix ecosystem into BioModStack for cross-comparison with existing prediction and design workflows.

| Component | Role | Current Equivalent | Phase |
|-----------|------|-------------------|-------|
| **Protenix** | Structure prediction | Boltz-2, RF3 | 1 |
| **PXDesign-d** | Backbone diffusion | RFantibody, BoltzGen | 2 |
| **AF2-IG** | Validation (Initial Guess) | Boltz-2 validation | 2 |

---

## Phase 1: Protenix Structure Predictor

**Goal**: Add Protenix as 3rd predictor option alongside Boltz-2 and RF3

### 1.1 Model Variants

All three sizes will be supported:

| UI Label | Model Name | Params | VRAM | N_cycle | Diffusion Steps |
|----------|------------|--------|------|---------|-----------------|
| Base | `protenix_base_default_v1.0.0` | 368M | ~24GB | 10 | 200 |
| Mini | `protenix_mini_default_v0.5.0` | 134M | ~12GB | 4 | 5 |
| Tiny | `protenix_tiny_default_v0.5.0` | 110M | ~8GB | 4 | 5 |

**v1.0.0 Features**: RNA MSA support, Template support, improved training dynamics

### 1.2 Container Definition

**File**: `apptainer/protenix.def`

```def
Bootstrap: docker
From: nvcr.io/nvidia/cuda:12.8.0-cudnn-devel-ubuntu22.04

%post
    apt-get -q update
    DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \
        git wget curl python3.10 python3.10-dev python3-venv python3-pip build-essential
    
    # UV package manager (fast)
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="/root/.local/bin:$PATH"
    
    python3.10 -m venv /opt/venv
    . /opt/venv/bin/activate
    
    # PyTorch nightly for RTX 5090 (sm_120 Blackwell support)
    uv pip install --compile torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/nightly/cu128
    
    # Protenix
    uv pip install --compile protenix
    
    apt-get autoremove -y && apt-get clean
    echo '. /opt/venv/bin/activate' >> $APPTAINER_ENVIRONMENT

%runscript
    exec protenix "$@"

%test
    . /opt/venv/bin/activate
    protenix pred --help
    echo "Protenix container build successful"

%help
    Protenix structure prediction container with CUDA 12.8 runtime
    Models: base (368M), mini (134M), tiny (110M)
```

### 1.3 Nextflow Module

**File**: `modules/protenix.nf`

```nextflow
// Protenix structure prediction process
process ProtenixFromSequence {
    label 'Protenix'
    label 'gpu'
    publishDir "${params.out_dir}/run/protenix", mode: 'copy', pattern: "*.log"
    tag "${name}"
    
    input:
    tuple val(sequence), val(name), path(msa_file)
    
    output:
    path "predictions/*.cif", emit: cifs
    path "predictions/*.json", emit: jsons
    path "*.log", emit: logs
    
    script:
    def model = params.protenix_model ?: 'protenix_base_default_v1.0.0'
    def dtype = params.protenix_dtype ?: 'bf16'
    def useMsa = msa_file.name != 'NO_MSA'
    """
    mkdir -p tmp predictions
    export NUMBA_CACHE_DIR=tmp
    export XDG_CONFIG_HOME=tmp
    export TRITON_CACHE_DIR=tmp
    export HOME=tmp
    
    # Create input JSON
    cat > input.json << EOF
{
    "sequences": [
        {
            "protein": {
                "id": "A",
                "sequence": "${sequence}"
            }
        }
    ]${useMsa ? ',\n    "msa_dir": "' + msa_file + '"' : ''}
}
EOF
    
    protenix pred \\
        -i input.json \\
        -o output/ \\
        -n ${model} \\
        --dtype ${dtype} \\
        --use_fast_ln True \\
        --use_deepspeed_evo_attention True \\
        2>&1 | tee protenix_${name}.log
    
    # Move outputs to standard location
    find output/ -name "*.cif" -exec mv {} predictions/ \\;
    find output/ -name "*.json" -exec mv {} predictions/ \\;
    """
}

// Protenix from PDB complex (mirrors BoltzFromComplex)
process ProtenixFromComplex {
    label 'Protenix'
    label 'gpu'
    publishDir "${params.out_dir}/run/protenix", mode: 'copy'
    tag "${name}"
    
    input:
    tuple path(input_yaml), val(name)
    
    output:
    path "predictions/*.cif", emit: cifs
    path "predictions/*.json", emit: jsons
    path "*.log", emit: logs
    
    script:
    def model = params.protenix_model ?: 'protenix_base_default_v1.0.0'
    def dtype = params.protenix_dtype ?: 'bf16'
    """
    mkdir -p tmp predictions
    export NUMBA_CACHE_DIR=tmp
    export XDG_CONFIG_HOME=tmp
    export TRITON_CACHE_DIR=tmp
    
    protenix pred \\
        -i ${input_yaml} \\
        -o output/ \\
        -n ${model} \\
        --dtype ${dtype} \\
        --use_fast_ln True \\
        --use_deepspeed_evo_attention True \\
        2>&1 | tee protenix_${name}.log
    
    find output/ -name "*.cif" -exec mv {} predictions/ \\;
    find output/ -name "*.json" -exec mv {} predictions/ \\;
    """
}
```

### 1.4 Workflow Wiring

**File**: `modules/structure_prediction.nf`

Extend `pred_method` parameter:

```nextflow
// Current options: boltz, rf3, both
// New options: protenix, all

def pred_method = params.pred_method ?: 'boltz'
def protenix_use_msa = params.protenix_use_msa ?: true

// Update need_msa calculation
def need_msa = 
    (pred_method in ['boltz', 'both', 'all'] && boltz_use_msa) ||
    (pred_method in ['rf3', 'both', 'all'] && rf3_use_msa) ||
    (pred_method in ['protenix', 'all'] && protenix_use_msa)

// Add Protenix execution branch
if (pred_method == 'protenix' || pred_method == 'all') {
    ProtenixFromSequence(inputs_with_msa)
    structures = structures.mix(ProtenixFromSequence.out.cifs)
}
```

### 1.5 Frontend Changes

**File**: `platform/frontend/src/components/StructurePredictionTemplate.tsx`

Add predictor selector:

```tsx
// State
const [predMethod, setPredMethod] = useState<'boltz' | 'protenix' | 'rf3' | 'all'>('boltz');
const [protenixModel, setProtenixModel] = useState<'base' | 'mini' | 'tiny'>('base');

// UI - Predictor Selection
<div className="space-y-2">
    <label>Structure Predictor</label>
    <div className="grid grid-cols-4 gap-2">
        {['boltz', 'protenix', 'rf3', 'all'].map(method => (
            <button
                key={method}
                onClick={() => setPredMethod(method)}
                className={predMethod === method ? 'active' : ''}
            >
                {method === 'boltz' ? 'Boltz-2' : 
                 method === 'protenix' ? 'Protenix' :
                 method === 'rf3' ? 'RF3' : 'All (Compare)'}
            </button>
        ))}
    </div>
</div>

// Protenix Model Size (if selected)
{(predMethod === 'protenix' || predMethod === 'all') && (
    <div className="space-y-2">
        <label>Protenix Model</label>
        <select value={protenixModel} onChange={e => setProtenixModel(e.target.value)}>
            <option value="base">Base (368M) - Best accuracy</option>
            <option value="mini">Mini (134M) - Fast</option>
            <option value="tiny">Tiny (110M) - Screening</option>
        </select>
    </div>
)}
```

### 1.6 Parameters Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pred_method` | string | `boltz` | `boltz`, `protenix`, `rf3`, `all` |
| `protenix_model` | string | `protenix_base_default_v1.0.0` | Model variant |
| `protenix_dtype` | string | `bf16` | `bf16` or `fp32` |
| `protenix_use_msa` | boolean | `true` | Enable MSA input |
| `protenix_use_template` | boolean | `false` | Enable template (v1.0.0+) |

### 1.7 Testing Checkpoint

Before proceeding to Phase 2:

- [ ] Build container successfully
- [ ] Basic inference test (single sequence)
- [ ] MSA integration test (ColabFold format)
- [ ] Compare against Boltz-2 on benchmark set
- [ ] Validate output parsing (CIF, confidence JSON)
- [ ] Frontend toggle functional
- [ ] Results Viewer displays Protenix metrics

---

## Phase 2: PXDesign Binder Workflow

**Gated on**: Phase 1 testing success

**Goal**: Add PXDesign as alternative binder design workflow

### 2.1 Architecture Comparison

| Feature | PXDesign-d | RFantibody | BoltzGen |
|---------|-----------|------------|----------|
| Architecture | DiT (AF3-based) | SE(3)-equiv | Diffusion |
| Output | Backbone atoms | Backbone frames | All-atom |
| Targets | Protein/DNA/RNA/small mol | Antibody-specific | Proteins |
| Speed | Fast (no triangle updates) | Moderate | Fast |
| Conditioning | `[xpb]` token | CDR masking | Noise injection |

**Use Case Split**:
- General binders → PXDesign-d
- Antibodies/CDRs → RFantibody  
- All-atom with Boltz → BoltzGen

### 2.2 Key Modification: FAMPNN over ProteinMPNN

PXDesign uses ProteinMPNN for sequence design. We will swap in **FAMPNN** (Full Atom MPNN) for higher quality sequences:

```
PXDesign-d (backbone) → FAMPNN (our container) → Protenix validation
```

### 2.3 Container Definition

**File**: `apptainer/pxdesign.def`

```def
Bootstrap: docker
From: nvcr.io/nvidia/cuda:12.8.0-cudnn-devel-ubuntu22.04

%post
    # Similar to Protenix container
    apt-get update && apt-get install -y git wget curl python3.10 python3.10-dev python3-venv
    
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="/root/.local/bin:$PATH"
    
    python3.10 -m venv /opt/venv
    . /opt/venv/bin/activate
    
    uv pip install torch --index-url https://download.pytorch.org/whl/nightly/cu128
    
    # PXDesign
    git clone https://github.com/bytedance/PXDesign.git /pxdesign
    cd /pxdesign && pip install -e .
    
    echo '. /opt/venv/bin/activate' >> $APPTAINER_ENVIRONMENT

%runscript
    exec pxdesign "$@"
```

### 2.4 Workflow Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                     1) GENERATION                            │
├─────────────────────────────────────────────────────────────┤
│  PXDesign-d (Diffusion)                                     │
│  └── Target-conditioned binder backbones                    │
│                         ↓                                   │
│  FAMPNN (our container)                                     │
│  └── Full-atom sequence design (replacing ProteinMPNN)      │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                 2) DUAL VALIDATION                           │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐     ┌─────────────────┐               │
│  │    Protenix     │     │     AF2-IG      │               │
│  │  (from Phase 1) │     │  (Initial Guess)│               │
│  └────────┬────────┘     └────────┬────────┘               │
│           ↓                       ↓                         │
│      ipTM, pLDDT             ipTM, pLDDT                    │
│           └───────────┬───────────┘                         │
│                       ↓                                     │
│              Combined filter pass/fail                      │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│               3) SELECTION FOR WET-LAB                       │
├─────────────────────────────────────────────────────────────┤
│  Clustering (optional) → Rank by ipTM → Top candidates      │
└─────────────────────────────────────────────────────────────┘
```

### 2.5 Frontend Template

**File**: `platform/frontend/src/components/PXDesignTemplate.tsx`

Key Controls:
- Target antigen selector (reuse existing component)
- Hotspot/epitope picker (reuse existing)
- Binder length control (slider: 50-150 aa)
- Sample count (N_sample: 10-1000)
- Preset selection (extended/preview/infer)
- Model selector (PXDesign-d variants)

### 2.6 Input YAML Generation

Convert frontend selections to PXDesign YAML format:

```yaml
target:
  file: "${target_pdb_path}"
  chains:
    A:
      crop: ["${crop_range}"]
      hotspots: [${hotspot_residues}]
  msa: "${msa_path}"  # From existing ColabFold pipeline
binder_length: ${binder_length}
```

### 2.7 AF2-IG Validation

AF2-IG = AlphaFold2 with designed structure as initial guess (template).

Uses existing AF2 container with template mode:

```nextflow
process AF2InitialGuess {
    label 'AlphaFold2'
    label 'gpu'
    
    input:
    tuple path(designed_pdb), val(name)
    
    output:
    path "*.pdb", emit: validated_pdbs
    path "*.json", emit: metrics
    
    script:
    """
    # Run AF2 with designed structure as template
    python run_alphafold.py \\
        --fasta_paths ${name}.fasta \\
        --template_pdb ${designed_pdb} \\
        --use_template_for_initial_guess \\
        --output_dir output/
    """
}
```

---

## Dependencies

### Existing Infrastructure (Reused)
- ColabFold MSA databases (MMseqs2 GPU)
- FAMPNN container
- AF2 container
- Results Viewer

### New Requirements
- Protenix model checkpoints (~5-10GB)
- PXDesign-d checkpoint (~TBD)
- CUDA 12.8+ (RTX 5090 compatible)

### Storage Location
```
/mnt/BioModStack/models/
├── protenix/
│   ├── protenix_base_default_v1.0.0/
│   ├── protenix_mini_default_v0.5.0/
│   └── protenix_tiny_default_v0.5.0/
└── pxdesign/
    └── pxdesign_d_checkpoint/
```

---

## Timeline

| Phase | Scope | Est. Effort |
|-------|-------|-------------|
| 1.1 | Container build | 2-4 hours |
| 1.2 | Nextflow module | 2-4 hours |
| 1.3 | Workflow wiring | 2-3 hours |
| 1.4 | Frontend toggle | 2-3 hours |
| 1.5 | Testing/validation | 4-8 hours |
| **Phase 1 Total** | | **12-22 hours** |
| 2.x | PXDesign (gated) | 20-30 hours |

---

## References

- [Protenix GitHub](https://github.com/bytedance/Protenix)
- [PXDesign GitHub](https://github.com/bytedance/PXDesign)
- [Protenix Technical Report](https://github.com/bytedance/Protenix/blob/main/docs/PTX_V1_Technical_Report_202602042356.pdf)
- [PXDesign Project Page](https://protenix.github.io/pxdesign/)
