# FrustraMPNN Integration into BioModStack

> **Version**: 3.1 (Mutagenesis Refinement Loop)  
> **Last Updated**: 2026-01-25  
> **Status**: ✅ Ready for implementation

## Overview

**FrustraMPNN** provides per-residue **local energetic frustration** analysis. Useful for understanding designs, not gatekeeping them.

| Aspect | Value |
|--------|-------|
| **Speed** | ~30s for 300-500 AA complex |
| **Use Case** | Post-pipeline QC / scientist annotation |
| **License** | MIT ✅ |

> [!IMPORTANT]
> FrustraMPNN is **FIO (For Information Only)** — annotates final candidates, does not filter.

---

## Integration Position

```
RFdiffusion → FAMPNN → FilterFAMPNN → Boltz-2 → OpenMM → [FrustraMPNN QC]
                                                              ↑
                                              POST-PIPELINE ANNOTATION
```

**Runs on**: Final validated candidates only (top N after all filters)

**NOT used for**: Mid-pipeline filtering

---

## Use Cases

| Use Case | When | Input |
|:---------|:-----|:------|
| **Design QC** | After Boltz-2 validation | Final candidates |
| **Epitope Analysis** | Before design (optional) | Target antigen PDB |

---

## Implementation

### Container

**File**: `apptainer/frustrampnn.def`

```singularity
Bootstrap: docker
From: nvcr.io/nvidia/cuda:12.8.0-cudnn-devel-ubuntu22.04

%post
    apt-get -q update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3.10 python3-venv python3-pip curl
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="/root/.local/bin:$PATH"
    python3.10 -m venv /opt/venv && . /opt/venv/bin/activate
    uv pip install torch --index-url https://download.pytorch.org/whl/nightly/cu128
    uv pip install frustrampnn[all]
    python -c "from frustrampnn import FrustraMPNN; FrustraMPNN.from_pretrained('default')"
    echo '. /opt/venv/bin/activate' >> $APPTAINER_ENVIRONMENT

%runscript
    exec frustrampnn "$@"
```

---

### Nextflow Module

**File**: `modules/frustrampnn.nf`

```groovy
process FrustrampnnQC {
    label 'process_gpu'
    container "${params.container_dir}/frustrampnn.sif"
    containerOptions { "--nv" }
    
    publishDir "${params.out_dir}/frustration", mode: 'copy'
    
    input:
    tuple val(meta), path(pdb)
    
    output:
    tuple val(meta), path("${meta.id}_frustration.csv"), emit: frustration
    tuple val(meta), path("${meta.id}_summary.json"), emit: summary
    
    script:
    """
    frustrampnn predict --pdb ${pdb} --output ${meta.id}_frustration.csv
    python3 -c "
import pandas as pd, json
df = pd.read_csv('${meta.id}_frustration.csv')
pos = df.groupby(['position','chain'])['frustration_pred'].mean()
json.dump({
    'pdb': '${meta.id}',
    'n_high_frust': int((pos <= -1.0).sum()),
    'n_min_frust': int((pos >= 0.58).sum()),
    'total': len(pos),
    'pct_high_frust': round((pos <= -1.0).sum() / len(pos) * 100, 1)
}, open('${meta.id}_summary.json','w'))
"
    """
}

process AggregateFrustrationReports {
    publishDir "${params.out_dir}/frustration", mode: 'copy'
    
    input:
    path summaries
    
    output:
    path "batch_frustration_report.json"
    
    script:
    """
    python3 -c "
import json
from pathlib import Path
data = [json.load(open(f)) for f in Path('.').glob('*_summary.json')]
json.dump({
    'total_designs': len(data),
    'zero_high_frust': sum(1 for d in data if d['n_high_frust']==0),
    'avg_pct_high_frust': round(sum(d['pct_high_frust'] for d in data)/len(data), 1) if data else 0,
    'designs': data
}, open('batch_frustration_report.json','w'), indent=2)
"
    """
}
```

---

### Workflow Integration

**File**: `workflows/antibody_denovo.nf` — Add after final filtering:

```groovy
// =============================================================================
// Step 4.x: FrustraMPNN QC (Post-Pipeline Annotation)
// FIO only — does not filter, just annotates for Results Viewer
// =============================================================================

if (params.run_frustrampnn == true) {
    log.info("Step 4.x: Running FrustraMPNN QC on final candidates...")
    
    include { FrustrampnnQC; AggregateFrustrationReports } from '../modules/frustrampnn'
    
    // Run on final validated structures
    frustrampnn_input = final_candidates.flatMap { meta, pdbs ->
        def pdb_list = pdbs instanceof List ? pdbs : [pdbs]
        pdb_list.collect { pdb -> [[id: pdb.baseName], pdb] }
    }
    
    FrustrampnnQC(frustrampnn_input)
    AggregateFrustrationReports(FrustrampnnQC.out.summary.collect())
}
```

---

### Parameters

**File**: `nextflow.config`

```groovy
params {
    run_frustrampnn = false  // Optional post-pipeline QC
    container_dir = "${BMS_HOME}/containers"
}
```

---

## Files Summary

| File | Action |
|------|--------|
| `apptainer/frustrampnn.def` | NEW |
| `modules/frustrampnn.nf` | NEW (2 processes) |
| `workflows/antibody_denovo.nf` | MODIFY (add post-Boltz block) |
| `nextflow.config` | MODIFY (add param) |

**Not needed**:
- ~~Spawn/wait/collect scripts~~ (inline parallelism sufficient for top N)
- ~~Filtering logic~~ (FIO only)
- ~~Master slider integration~~ (no thresholds to tune)

---

## Timing Expectations

| Final Candidates | Estimated Time |
|:-----------------|:---------------|
| 10 | ~5 minutes |
| 50 | ~25 minutes |
| 100 | ~50 minutes |

Acceptable for post-pipeline QC on final selections.

---

## Use Case 2: Mutagenesis (Default Post-Run + Optional Pre-Run)

### Why Pre-Filter Matters for Single-AA Substitutions

**The Problem with AI/ML Folding for Mutations**:

AI folding models (AlphaFold2, ESMFold, Boltz-2) learn from **Multiple Sequence Alignments (MSAs)** which encode coevolutionary patterns — correlated mutations that appear together across species.

When you introduce a **single-point mutation**:

| Issue | Consequence |
|:------|:------------|
| **No MSA signal** | The specific variant hasn't been seen in evolution |
| **Broken covariance** | Natural mutations come in *packages* — single AA changes break this pattern |
| **Confidence collapse** | pLDDT/ipTM become unreliable — model is extrapolating |
| **Structural identity** | AF2/Boltz often predict mutant ≈ wild-type, missing the impact |

> [!CAUTION]
> For a 15 AA stretch with 500 generated mutants, **most single-AA changes will show no structural effect** in Boltz-2 predictions — not because they're neutral, but because the model can't detect the perturbation.

**Solution**: Default to **post-run FrustraMPNN** on Boltz-2 mutant structures, with an **optional pre-run** only when an input PDB exists to reduce the mutant pool.

### Input Constraints
FrustraMPNN requires a structure. For **sequence-only** inputs, pre-run is unavailable; post-run uses **Boltz-2 predicted structures**.

### RFA → Mutagenesis Refinement Loop (Primary Target Use Case)
Use FrustraMPNN on **final RFA binders** (post-Boltz-2) to select CDR positions for mutagenesis. This avoids running FrustraMPNN on large mutant pools and keeps the expensive modeling step focused.

**Loop**:
1. RFA workflow → final Boltz-2 candidates
2. FrustraMPNN on each final candidate (skip if already computed)
3. Select CDR sites for mutation (exclude minimally frustrated)
4. Generate mutant pool (substitution + optional indel rules)
5. Boltz-2 on mutant pool
6. Optional FrustraMPNN annotation for reporting
7. Iterate on top performers

**Skip logic**: If a candidate already has `*_frustration.csv` + `*_summary.json` for the same structure hash, reuse it.

### Integration with MutagenesisTemplate.tsx

The existing UI has:
- Base sequence input with PDB import
- Library generator (regions, strategy, variants)
- Manual editor (interactive residue selection)
- Predictor settings (Boltz-2, RoseTTAFold3)
- Physics refinement (OpenMM ΔΔG)

**Add FrustraMPNN pre-filter panel between region selection and preview** (only if pre-run is enabled):

```tsx
// New state for optional FrustraMPNN pre-filter
const [runFrustrampnnPre, setRunFrustrampnnPre] = useState(false);
const [frustrationData, setFrustrationData] = useState<FrustrationData | null>(null);
const [hiddenPositions, setHiddenPositions] = useState<Set<number>>(new Set());
const hasInputPdb = inputMode === 'pdb' || Boolean(uploadedPdb);

// UI Panel (insert after region input, before preview)
{mode === 'library' && runFrustrampnnPre && frustrationData && (
    <div className="bg-amber-950/20 border border-amber-700/30 rounded-lg p-4">
        <div className="flex justify-between items-center mb-3">
            <h4 className="text-sm font-semibold text-amber-400 flex items-center gap-2">
                ⚡ Frustration Pre-Filter
            </h4>
            <span className="text-xs text-amber-300">
                {hiddenPositions.size} positions excluded
            </span>
        </div>
        
        <div className="text-xs text-slate-400 mb-3">
            Minimally frustrated positions (likely destabilizing if mutated) are auto-excluded:
        </div>
        
        <div className="flex flex-wrap gap-1">
            {frustrationData.positions.map(pos => {
                const isMinFrust = pos.class === 'minimally_frustrated';
                return (
                    <button
                        key={pos.position}
                        onClick={() => togglePosition(pos.position)}
                        className={`px-2 py-1 text-xs rounded ${
                            isMinFrust 
                                ? 'bg-red-600/20 text-red-400 line-through' 
                                : 'bg-emerald-600/20 text-emerald-400'
                        }`}
                        title={`${pos.class}: ${pos.mean_frustration.toFixed(2)}`}
                    >
                        {pos.position}
                    </button>
                );
            })}
        </div>
    </div>
)}
```

### Enable Toggle (add to Predictor Settings section)

```tsx
<div className="flex items-center gap-2 pt-6">
    <input
        type="checkbox"
        checked={runFrustrampnnPre}
        onChange={(e) => setRunFrustrampnnPre(e.target.checked)}
        disabled={!hasInputPdb}
        className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-amber-600"
    />
    <label
        className={hasInputPdb ? "text-slate-300" : "text-slate-500"}
        title={hasInputPdb
            ? "Pre-screen positions using FrustraMPNN to exclude likely-destabilizing mutations"
            : "Requires input PDB (pre-run disabled for sequence-only inputs)"}
    >
        🧬 Pre-Filter with FrustraMPNN (PDB only)
    </label>
</div>
```

### Workflow Integration
**Default (all inputs, including sequence-only):**
1. Generate mutant pool
2. Run Boltz-2 on all mutants
3. Run FrustraMPNN on Boltz-2 outputs (annotation only)

**Optional pre-run (only if input PDB exists and `runFrustrampnnPre` is enabled):**
1. Run FrustraMPNN on the input PDB **once** (~30s)
2. Return frustration profile for all positions
3. UI highlights minimally frustrated positions (excluded by default)
4. User can toggle positions back in if desired
5. Library generation only uses "safe" positions

**Backend Flows**:
```
Default:  Mutant Pool → Boltz-2 → FrustraMPNN (annotate mutants)
```
```
Optional: Input PDB → FrustraMPNN → Filter Positions → Generate Pool → Boltz-2
```

**Note**: Generating mutant PDBs pre-Boltz (e.g., side-chain repack) can be added later, but those structures are approximate and may distort frustration signals. Keep it exploratory, not default.

---

## Mutagenesis Workflow Upgrades (RFA-Compatible)

### 1) Inputs from RFA / Binder Design Workflows
Support ingesting **final candidate PDBs** as mutagenesis seeds:
- Accept a list of PDBs + metadata (design id, chain mapping, target id).
- Auto-populate base sequence and chain id from PDB.
- Optionally carry over previously computed FrustraMPNN results (skip compute).

### 2) FrustraMPNN-Guided Position Selection
Expose a simple rule set:
- **Exclude** minimally frustrated positions by default
- **Include** neutral + highly frustrated (flag highly frustrated as functionally sensitive)
- Manual override per position

### 3) Mutation Rule Extensions (Library Generator)
Add support for **discrete mutation sizes** and **indels**:
- **Exact N** substitutions (e.g., 1, 2, 3) instead of only range
- **N ± 1/2/3** for loop resize (insertions/deletions)
- **Random chance** vs explicit choice for substitutions/indels
- **Whitelist / blacklist** for:
  - positions (never mutate anchor residues)
  - amino acids (exclude Pro/Cys, etc.)
 - **MSA note**: mutagenesis variants regenerate MSAs per-variant (no shared reference MSA); indels still supported

### 4) CDR Loop Resize Constraints
Indels must be limited to **CDR regions only**:
- Define allowed insertion/deletion ranges by CDR
- Clamp total length change to ±3 by default
- Maintain consistent chain mapping for downstream PDB handling

### 5) Iteration Controls
Prevent runaway search:
- Max iterations (e.g., 2–3 rounds)
- Max pool size per round
- Stop if top-K scores plateau

### Filter Logic (Pre-Run Only)

| Frustration Class | Default Action | Rationale |
|:------------------|:---------------|:----------|
| Minimally frustrated (≥0.58) | **EXCLUDE** | Mutation likely destabilizing |
| Neutral (-1.0 to 0.58) | Include | Safe for substitution |
| Highly frustrated (≤-1.0) | Include (flagged) | May affect function — warn user |

### ROI Calculation (Pre-Run Only)

For a 15 AA region with ~50% minimally frustrated positions:
- Without filter: 500 variants × 10 min Boltz = **83 hours**
- With filter: 250 variants × 10 min Boltz = **42 hours** + 30s FrustraMPNN

**Savings**: 41 hours compute time, **plus** avoiding wasted analysis on doomed mutations.

---

## Summary: Three Use Cases

| Workflow | Position | Role | Timing |
|:---------|:---------|:-----|:-------|
| **RFA/BindCraft** | Post-pipeline | FIO annotation | Run on top N |
| **RFA → Mutagenesis** | Post-RFA | Position selection | Use final PDBs |
| **Mutagenesis** | Default post-run | Mutant annotation | Run after Boltz-2 |
| **Mutagenesis** | Optional pre-run | Position screening | PDB-only, run once |

---

*Document finalized: 2026-01-25 (v3.1 - Refinement Loop + Mutagenesis Upgrades)*
