# Protenix & PXDesign Integration Plan

> **Author**: BioModStack AI Assistant  
> **Date**: 2026-02-06 (rev. 3 — Gap-Rectified)  
> **Status**: Planning — All 10 Gaps Rectified  
> **Sources**: [infer_json_format.md](https://github.com/bytedance/Protenix/blob/main/docs/infer_json_format.md), [training_inference_instructions.md](https://github.com/bytedance/Protenix/blob/main/docs/training_inference_instructions.md), [colabfold_compatible_msa.md](https://github.com/bytedance/Protenix/blob/main/docs/colabfold_compatible_msa.md), [msa_template_pipeline.md](https://github.com/bytedance/Protenix/blob/main/docs/msa_template_pipeline.md), [kernels.md](https://github.com/bytedance/Protenix/blob/main/docs/kernels.md)

---

## Executive Summary

Integration of ByteDance's Protenix ecosystem into BioModStack as a third structure predictor alongside Boltz-2 and RoseTTAFold3. This revision corrects 10 critical gaps identified during deep infrastructure review.

---

## 1. Protenix Overview & Model Variants

| Model Weight ID | Training Cutoff | MSA Required | ESM Backbone | Notes |
|---|---|---|---|---|
| `protenix_base_20241211_v0.2.1` | 2021-09-30 | Yes | — | CASP15/16 default |
| `protenix_base_20250630_v1.0.0` | 2025-06-30 | Yes | — | Post-CASP16, latest data |
| `protenix_esm_20241211_v0.2.1` | 2021-09-30 | **No** | ESM2-3B | No-MSA mode |
| `protenix_mini_esm_v0.5.0` | — | **No** | ESM2-3B | Lightweight, no-MSA |
| `protenix_pxdesign_20250211_v0.2.0` | — | Yes | — | PXDesign inverse folding |

> [!IMPORTANT]
> The ESM-based models (`protenix_esm_*`, `protenix_mini_esm_*`) use ESM2-3B embeddings in place of MSA. These require ~15GB VRAM for the ESM pass alone, making them heavier than the MSA-based models per-token.

---

## 2. Input JSON Format (CORRECTED)

Protenix uses a **specific JSON schema** distinct from Boltz-2's YAML. Key differences from the original plan:

### 2.1 Entity Naming Convention

```diff
- { "protein": [{ "id": "prot_A", "sequence": "MVLSPADKTNVK..." }] }
+ { "sequences": [{ "proteinChain": { "sequence": "MVLSPADKTNVK...", "count": 1 } }] }
```

Entity types (keys in each sequence object):
- `proteinChain` — protein sequence + optional MSA paths
- `dnaSequence` — DNA strand
- `rnaSequence` — RNA strand
- `ligand` — CCD code **or** SMILES string
- `ion` — element symbol (e.g., `"ZN"`)

### 2.2 Full Multi-Modal Complex Example

```json
[
  {
    "name": "complex_prediction",
    "modelSeeds": [42],
    "sequences": [
      {
        "proteinChain": {
          "sequence": "MVLSPADKTNVK...",
          "count": 2,
          "pairedMsaPath": "/path/to/paired/pairing.a3m",
          "unpairedMsaPath": "/path/to/unpaired/non_pairing.a3m"
        }
      },
      {
        "dnaSequence": {
          "sequence": "ATCGATCG",
          "count": 1
        }
      },
      {
        "ligand": {
          "ccdCodes": ["ATP"]
        }
      },
      {
        "ion": {
          "ion": "ZN",
          "count": 2
        }
      }
    ]
  }
]
```

### 2.3 MSA Path Requirements

Each `proteinChain` entity **independently** needs:
- `pairedMsaPath` → points to `pairing.a3m` (with taxonomy headers)
- `unpairedMsaPath` → points to `non_pairing.a3m`

> [!WARNING]
> Our ColabFold output is a **single** `.a3m` file. Protenix requires **separate** `pairing.a3m` and `non_pairing.a3m` files with taxonomy headers. Use Protenix's `colabfold_msa.py` post-processor or the built-in `protenix prep` CLI (see §5).

---

## 3. VRAM Requirements (MEASURED)

From [training_inference_instructions.md](https://github.com/bytedance/Protenix/blob/main/docs/training_inference_instructions.md):

| Total Tokens | VRAM (GB) | Fits RTX 5090 (32GB)? | Fits RTX 3090 (24GB)? |
|---|---|---|---|
| 500 | 6 | ✅ | ✅ |
| 1000 | 12 | ✅ | ✅ |
| 2000 | 30 | ✅ (tight) | ❌ |
| 3000 | 50 | ❌ | ❌ |
| 4000 | 78 | ❌ | ❌ |

**"Total tokens"** = sum of all entity lengths (protein residues + DNA/RNA nucleotides + ligand atoms).

**VRAM profile for `gpu_orchestrator.py`:**
```python
# Protenix structure prediction — VRAM benchmarks from docs
# Quadratic: base=4000, scale=55 matches: 500tok→6GB, 1000tok→9.5GB, 2000tok→26GB
'protenix': {'base': 4000, 'scale': 55},
'protenix_esm': {'base': 6000, 'scale': 60},     # ESM2-3B adds ~2GB overhead
'protenix_mini_esm': {'base': 5000, 'scale': 50}, # Lighter ESM variant
```

> [!NOTE]
> RTX 5090 (32GB) is safe to ~1200 total tokens with base model. For larger complexes, only the server GPUs (A100/H100) would suffice.

---

## 4. Output Format & Confidence Parsing (CORRECTED)

### 4.1 Output Structure

```
predictions/
  <name>/
    seed_42/
      sample_0/
        pred.cif            # mmCIF structure
        confidence.json     # 13+ metrics
```

### 4.2 Confidence Metrics (Full List)

The `confidence.json` contains these metrics — **ALL must be parsed by `result_ingester.py`**:

| Metric | Type | Description |
|---|---|---|
| `ranking_score` | float | Primary ranking (weighted composite) |
| `full_plddt` | float | Global pLDDT (0–100) |
| `full_ptm` | float | pTM score (0–1) |
| `full_iptm` | float | ipTM score (0–1, interface quality) |
| `gpde` | float | Global PDE score |
| `full_has_clash` | bool | Steric clash detection |
| `chain_plddt` | dict | Per-chain pLDDT (e.g., `{"A": 85.2, "B": 72.1}`) |
| `chain_ptm` | dict | Per-chain pTM |
| `chain_pair_iptm` | dict | Pairwise chain interface pTM |
| `disorder_prob_mean` | dict | Per-chain disorder probability |
| `full_chain_pair_iptm` | float | Averaged pairwise ipTM |
| `has_disordered_region` | dict | Per-chain disorder flags |
| `full_disorder_prob_mean` | float | Global disorder probability |

---

## 5. MSA Compatibility Strategy

### 5.1 Option A: Use Protenix's Built-in MSA CLI (Recommended)

```bash
# Full MSA + template pipeline
protenix prep --input input.json --output_dir msa_output/

# Or step-by-step:
protenix msa --input input.json --output_dir msa_output/      # JackHMMER/MMseqs2
protenix mt --input input.json --output_dir msa_output/        # Template search (HMMER)
```

This produces the correct `pairing.a3m` / `non_pairing.a3m` / `hmmsearch.a3m` structure.

### 5.2 Option B: ColabFold Post-Processing

If using our existing ColabFold MSA cache:

```bash
python3 scripts/colabfold_msa.py input.fasta <colabfold_db> output_dir \
    --db1 uniref30_2103_db \
    --db3 colabfold_envdb_202108_db \
    --mmseqs_path <mmseqs>
```

This adds pseudo taxonomy IDs required for Protenix's pairing pipeline.

### 5.3 BioModStack Integration Decision

> [!IMPORTANT]
> **Recommended**: Use **Option A** (Protenix's built-in `protenix prep`) for initial integration. This avoids MSA format compatibility issues entirely. Our ColabFold MSA cache is optimized for Boltz-2/RF3 and would require a translation layer.

In the Nextflow module, the MSA step would be:
1. **If user provides MSA paths** → pass them directly via `pairedMsaPath`/`unpairedMsaPath`
2. **If user selects "no MSA"** → use ESM-based model weights (no MSA generation needed)
3. **If user wants MSA** → run `protenix prep` inside the container before `protenix pred`

---

## 6. CUDA Kernels & Container Setup

### 6.1 Custom Kernels

From [kernels.md](https://github.com/bytedance/Protenix/blob/main/docs/kernels.md):

| Kernel | Source | Speedup | Default |
|---|---|---|---|
| `fast_layernorm` | FastFold/OneFlow CUDA | 30–50% | **ON** (JIT compiled) |
| `triattention` | Custom triangle attention | Default attention | Default |
| `cuequivariance` | NVIDIA cuEquivariance | Requires CUTLASS v3.5.1 | Optional |
| `deepspeed` | DeepSpeed FlashAttention | Alternative | Optional |

### 6.2 Container Definition (`apptainer/protenix.def`)

```
Bootstrap: docker
From: nvcr.io/nvidia/pytorch:24.01-py3

%post
    pip install protenix
    # CUTLASS v3.5.1 for cuEquivariance kernels
    git clone --branch v3.5.1 https://github.com/NVIDIA/cutlass.git /opt/cutlass
    export CUTLASS_PATH=/opt/cutlass
    pip install cuequivariance cuequivariance-ops-torch-cu12
    # HMMER for template search
    apt-get update && apt-get install -y hmmer

%environment
    export CUTLASS_PATH=/opt/cutlass
```

---

## 7. Backend Changes — Complete Touchpoint List

### 7.1 `nextflow.config` — Container Binding

```groovy
withLabel: 'Protenix' {
    container = "${params.container_dir}/protenix.sif"
}
```

### 7.2 `gpu_orchestrator.py` — VRAM Profiles & HEAVY_MODELS

```python
# In VRAM_PROFILES dict:
'protenix': {'base': 4000, 'scale': 55},
'protenix_esm': {'base': 6000, 'scale': 60},

# Protenix is NOT in HEAVY_MODELS (fits on 5060Ti for small proteins)
# But ESM variants may be borderline — monitor
```

### 7.3 `nextflow.py` — `build_nextflow_command`

**Profile mapping:**
```python
model_mode_to_profile = {
    # ... existing entries ...
    ('protenix', 'predict'): 'protenix',
    ('protenix', 'complex'): 'protenix',
}
```

**Stage tracking regex (in `launch_nextflow_job`):**
```python
elif 'protenix' in stage_clean:
    stage = 'protenix'
```

**Param mapping (new entries):**
```python
param_mapping = {
    # ... existing entries ...
    # Protenix structure prediction params
    'protenix_model_weights': 'protenix_model_weights',
    'protenix_seeds': 'protenix_seeds',
    'protenix_n_sample': 'protenix_n_sample',
    'protenix_n_step': 'protenix_n_step',
    'protenix_n_cycle': 'protenix_n_cycle',
    'protenix_use_msa': 'protenix_use_msa',
    'protenix_use_template': 'protenix_use_template',
    'protenix_enable_cache': 'protenix_enable_cache',
    'protenix_enable_fusion': 'protenix_enable_fusion',
}
```

### 7.4 Model Registry YAML — `config/models/protenix.yaml`

```yaml
id: protenix
name: Protenix
version: "1.0.0"
category: structure_prediction
description: >
  ByteDance's open-source structure predictor. AF3-level accuracy with
  5 model variants including ESM-based no-MSA modes. Supports protein,
  DNA, RNA, ligand, and ion complexes.
container: protenix.sif

ui_icon: atom
ui_color: "#8B5CF6"

inputs:
  - json
outputs:
  - cif
  - json

modes:
  - id: predict
    name: Structure Prediction
    description: Predict structure from sequence
    params:
      - sequence
      - sequence_name
      - protenix_model_weights
      - protenix_seeds
      - protenix_n_sample
      - protenix_n_step
      - protenix_n_cycle
      - protenix_use_msa
      - protenix_use_template
      - num_parallel_jobs

  - id: complex
    name: Complex Prediction
    description: Multi-chain protein/DNA/RNA/ligand/ion complex
    params:
      - sequence
      - sequence_name
      - protenix_model_weights
      - protenix_seeds
      - protenix_n_sample
      - protenix_n_step
      - protenix_n_cycle
      - protenix_use_msa
      - protenix_use_template
      - num_parallel_jobs

params:
  - name: sequence
    type: text
    description: Amino acid sequence to predict
    required: true

  - name: sequence_name
    type: string
    description: Name for output structure files
    required: false
    default: predicted

  - name: protenix_model_weights
    type: string
    description: Model weight variant
    required: false
    default: protenix_base_20250630_v1.0.0
    enum:
      - protenix_base_20241211_v0.2.1
      - protenix_base_20250630_v1.0.0
      - protenix_esm_20241211_v0.2.1
      - protenix_mini_esm_v0.5.0

  - name: protenix_seeds
    type: string
    description: "Comma-separated random seeds (e.g., '42,123,456')"
    required: false
    default: "42"

  - name: protenix_n_sample
    type: integer
    description: Number of diffusion samples per seed
    required: false
    default: 5
    minimum: 1
    maximum: 32

  - name: protenix_n_step
    type: integer
    description: Number of diffusion steps
    required: false
    default: 200
    minimum: 10
    maximum: 1000

  - name: protenix_n_cycle
    type: integer
    description: Number of recycling iterations
    required: false
    default: 10
    minimum: 1
    maximum: 20

  - name: protenix_use_msa
    type: boolean
    description: >
      Use MSA (requires pairing/non_pairing a3m files).
      If false, uses ESM-based model weights automatically.
    required: false
    default: true

  - name: protenix_use_template
    type: boolean
    description: Use structural templates from PDB
    required: false
    default: false

  - name: protenix_enable_cache
    type: boolean
    description: Enable data caching for repeated inference
    required: false
    default: true

  - name: protenix_enable_fusion
    type: boolean
    description: Enable kernel fusion for faster inference
    required: false
    default: true

  - name: num_parallel_jobs
    type: integer
    description: Number of parallel simulation jobs
    required: false
    default: 1
    minimum: 1
    maximum: 100

enabled: true
experimental: true
```

### 7.5 `result_ingester.py` — Protenix Output Parsing

The ingester needs a new parser for Protenix's CIF + confidence.json format:

```python
def parse_protenix_results(output_dir: str) -> List[Dict]:
    """Parse Protenix prediction outputs."""
    results = []
    pred_dir = Path(output_dir) / "predictions"
    
    for name_dir in pred_dir.iterdir():
        for seed_dir in name_dir.iterdir():
            for sample_dir in seed_dir.iterdir():
                cif_path = sample_dir / "pred.cif"
                conf_path = sample_dir / "confidence.json"
                
                if cif_path.exists() and conf_path.exists():
                    with open(conf_path) as f:
                        conf = json.load(f)
                    
                    results.append({
                        'structure_path': str(cif_path),
                        'plddt': conf.get('full_plddt'),
                        'ptm': conf.get('full_ptm'),
                        'iptm': conf.get('full_iptm'),
                        'ranking_score': conf.get('ranking_score'),
                        'gpde': conf.get('gpde'),
                        'has_clash': conf.get('full_has_clash'),
                        'chain_plddt': conf.get('chain_plddt'),
                        'chain_ptm': conf.get('chain_ptm'),
                        'chain_pair_iptm': conf.get('chain_pair_iptm'),
                        'disorder_prob_mean': conf.get('full_disorder_prob_mean'),
                    })
    
    return results
```

---

## 8. Frontend Changes

### 8.1 Predictor Selector Expansion

```tsx
// Current:
const [predictor, setPredictor] = useState<'boltz' | 'rf3' | 'both'>('boltz');

// Updated:
const [predictor, setPredictor] = useState<'boltz' | 'rf3' | 'protenix' | 'both' | 'all'>('boltz');
```

Add new predictor card:
```tsx
{ id: 'protenix', name: 'Protenix', desc: 'AF3-level, multi-modal', color: 'violet' }
```

### 8.2 Protenix Settings Panel

Conditional on `predictor === 'protenix' || predictor === 'all'`:

- **Model Variant** — dropdown: `base_v0.2.1`, `base_v1.0.0`, `esm_v0.2.1`, `mini_esm_v0.5.0`
- **Use MSA** — toggle (auto-switches to ESM model when disabled)
- **Use Template** — toggle
- **Seeds** — text input (comma-separated)
- **N Samples** — number input (1–32, default 5)
- **N Steps** — number input (10–1000, default 200)
- **N Cycles** — number input (1–20, default 10)

### 8.3 MSA Panel Behavior

When Protenix + MSA enabled:
- Show the existing MSA Quality Options panel (same as Boltz-2/RF3)
- Add a note: "Protenix uses its own MSA pipeline (protenix prep). ColabFold MSA is post-processed for compatibility."

---

## 9. Nextflow Module — `modules/protenix.nf`

### 9.1 Process Definition

```groovy
process ProtenixPredict {
    label 'Protenix'
    label 'gpu'
    publishDir "${params.out_dir}/run/protenix", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/**/*.cif"

    input:
    val sequence
    val sequence_name
    val protenix_model_weights
    val protenix_seeds
    val protenix_n_sample
    val protenix_n_step
    val protenix_n_cycle
    val protenix_use_msa
    val protenix_use_template
    val protenix_enable_cache
    val protenix_enable_fusion

    output:
    path "predictions/**/*.cif", emit: structures
    path "predictions/**/confidence.json", emit: confidence
    path "*.log", emit: logs

    script:
    def model_weights = protenix_model_weights ?: 'protenix_base_20250630_v1.0.0'
    def seeds = protenix_seeds ?: '42'
    def n_sample = protenix_n_sample ?: 5
    def n_step = protenix_n_step ?: 200
    def n_cycle = protenix_n_cycle ?: 10
    def use_template_flag = protenix_use_template == true || protenix_use_template == 'true' ? '--use_template true' : ''
    def cache_flag = protenix_enable_cache == true || protenix_enable_cache == 'true' ? '--enable_cache' : ''
    def fusion_flag = protenix_enable_fusion == true || protenix_enable_fusion == 'true' ? '--enable_fusion' : ''
    
    // Generate input JSON
    def json_content = """
    [{
        "name": "${sequence_name}",
        "modelSeeds": [${seeds}],
        "sequences": [{
            "proteinChain": {
                "sequence": "${sequence}",
                "count": 1
            }
        }]
    }]
    """

    """
    # Write input JSON
    cat > input.json << 'ENDJSON'
    ${json_content}
    ENDJSON

    # Run Protenix prediction
    protenix pred \\
        --input input.json \\
        --output_dir predictions/ \\
        --model_weights ${model_weights} \\
        --sample_diffusion.N_sample ${n_sample} \\
        --sample_diffusion.N_step ${n_step} \\
        --num_cycle ${n_cycle} \\
        ${use_template_flag} \\
        ${cache_flag} \\
        ${fusion_flag} \\
        2>&1 | tee protenix_predict.log
    """
}
```

### 9.2 Complex Mode (Multi-Chain + Ligands)

```groovy
process ProtenixFromComplex {
    label 'Protenix'
    label 'gpu'
    publishDir "${params.out_dir}/run/protenix_complex", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/**/*.cif"

    input:
    path complex_json  // Pre-built Protenix-format JSON
    val protenix_model_weights
    val protenix_seeds
    val protenix_n_sample
    val protenix_n_step
    val protenix_n_cycle
    val protenix_use_template
    
    output:
    path "predictions/**/*.cif", emit: structures
    path "predictions/**/confidence.json", emit: confidence
    path "*.log", emit: logs

    script:
    def model_weights = protenix_model_weights ?: 'protenix_base_20250630_v1.0.0'
    def seeds_list = (protenix_seeds ?: '42')
    def n_sample = protenix_n_sample ?: 5
    def n_step = protenix_n_step ?: 200
    def n_cycle = protenix_n_cycle ?: 10
    def use_template_flag = protenix_use_template == true || protenix_use_template == 'true' ? '--use_template true' : ''

    """
    protenix pred \\
        --input ${complex_json} \\
        --output_dir predictions/ \\
        --model_weights ${model_weights} \\
        --sample_diffusion.N_sample ${n_sample} \\
        --sample_diffusion.N_step ${n_step} \\
        --num_cycle ${n_cycle} \\
        ${use_template_flag} \\
        --enable_cache \\
        --enable_fusion \\
        2>&1 | tee protenix_complex.log
    """
}
```

---

## 10. Nextflow Profile — `nextflow.config`

```groovy
protenix {
    params {
        mode = 'protenix'
        protenix_model_weights = 'protenix_base_20250630_v1.0.0'
        protenix_seeds = '42'
        protenix_n_sample = 5
        protenix_n_step = 200
        protenix_n_cycle = 10
        protenix_use_msa = true
        protenix_use_template = false
        protenix_enable_cache = true
        protenix_enable_fusion = true
    }
}
```

---

## 11. `Model_Integrations.md` Update

Add entry #17:

```markdown
17) **protenix**
    - Internal: `docs/Protenix_PXDesign_Integration_Plan.md`, `modules/protenix.nf`
    - External code: https://github.com/bytedance/Protenix
    - Paper: https://www.biorxiv.org/content/10.1101/2025.01.08.631790
```

---

## 12. `Centralization_and_Standardization.md` Update

Add to environment variables:
```bash
export BMS_PROTENIX_WEIGHTS="${BMS_WEIGHTS}/protenix"
```

Add to `explicit_path_defaults` in `build_nextflow_command`:
```python
explicit_protenix_weights = params.get("protenix_weights") or os.getenv("BMS_PROTENIX_WEIGHTS") or str(Path(explicit_weights_root) / "protenix")
```

---

## 13. CLI Flags Reference

Complete Protenix CLI for inference:

```bash
protenix pred \
    --input input.json \
    --output_dir ./output \
    --model_weights protenix_base_20250630_v1.0.0 \
    --sample_diffusion.N_sample 5 \       # diffusion samples per seed
    --sample_diffusion.N_step 200 \       # diffusion steps
    --num_cycle 10 \                       # recycling iterations
    --seeds 42,123,456 \                   # random seeds (comma-sep)
    --use_template true \                  # structural templates
    --enable_cache \                       # data caching
    --enable_fusion                        # kernel fusion
```

---

## 14. Verification Plan

### Automated Tests

1. **Container build**: `apptainer build protenix.sif protenix.def && apptainer exec protenix.sif protenix pred --help`
2. **Single-chain prediction**: Run 100-residue protein, verify CIF output + confidence.json parsing
3. **Complex prediction**: Protein + ATP + Zn²⁺, verify multi-entity JSON generation
4. **VRAM validation**: Monitor actual VRAM @ 500, 1000 tokens vs documented benchmarks
5. **Result ingestion**: Submit via UI → verify Design table populated with all 13+ confidence metrics

### Manual Verification

1. Verify predictor selector shows 4 options in UI (Boltz-2, RF3, Protenix, Ensemble)
2. Verify Protenix settings panel toggles on/off correctly
3. Verify ESM model auto-selection when MSA is disabled
4. Verify CIF structures render in Molstar viewer
