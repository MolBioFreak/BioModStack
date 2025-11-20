# AlphaFold2 Initial-Guess Implementation for RTX 5090

## Date: November 19, 2025

## Changes Made

### 1. Updated `apptainer/af2.def` for RTX 5090 Compatibility

**Base Image:**
- **FROM:** `nvcr.io/nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04`
- **TO:** `nvcr.io/nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04`

**Python Version:**
- **FROM:** Python 3.10
- **TO:** Python 3.11 (as specified by nrbennet/dl_binder_design)

**JAX Stack (CRITICAL for AF2):**
- **FROM:** JAX 0.3.25 with CUDA 11
- **TO:** JAX 0.4.20 with CUDA 12 support (`jax[cuda12_pip]==0.4.20`)

**Dependencies:**
- ✅ **KEPT:** dm-haiku, dm-tree, ml-collections (REQUIRED for AF2 Initial-Guess)
- ✅ **KEPT:** biopython, ml_dtypes, mock
- ✅ **KEPT:** PyRosetta (REQUIRED for AF2 Initial-Guess)
- ❌ **REMOVED:** TensorFlow 2.13.0 (testing if AF2 needs it)
- ❌ **REMOVED:** dllogger (not needed for inference)

**Repository:**
- Uses PapenfussLab/dl_binder_design fork (contains AF2 Initial-Guess modifications)

### 2. Updated `apptainer/build_containers_workstation.sh`

Added af2 to container definitions:
```bash
CONTAINERS=(
    ["rfdiffusion"]="rfdiffusion.def"
    ["boltz2"]="boltz2.def"
    ["fampnn"]="fampnn.def"
    ["dl_binder_design"]="dl_binder_design.def"
    ["pyrosetta_tools"]="pyrosetta_tools.def"
    ["af2"]="af2.def"  # NEW
)

BUILD_ORDER=("rfdiffusion" "fampnn" "dl_binder_design" "af2" "boltz2" "pyrosetta_tools")
```

---

## What is AF2 Initial-Guess?

### Traditional AlphaFold2:
```
Sequence → MSA Search (2.5TB databases) → Neural Network → Structure
```

### AF2 Initial-Guess (No MSA needed!):
```
RFdiffusion Backbone + ProteinMPNN Sequence → AF2 Neural Network → Refined Structure
                                            └─ No database searches!
```

**Key Insight:** AF2 Initial-Guess uses AlphaFold2's neural network as a **structure refinement tool** rather than a *de novo* predictor. By providing:
1. A reasonable starting structure (from RFdiffusion)
2. The designed sequence (from ProteinMPNN)

The AF2 network can validate and refine without needing evolutionary information.

**Model Size:** Only ~1.1GB (neural network weights) vs 2.5TB+ for full AF2

---

## Technical Details

### Why JAX Instead of PyTorch?

**AF2 Initial-Guess requires JAX** because:
- AlphaFold2 was originally written in JAX/Haiku by DeepMind
- The dl_binder_design fork maintains this JAX implementation
- dm-haiku is the neural network library used by AF2

**This is different from RFdiffusion:**
- RFdiffusion: Pure PyTorch (no JAX/TensorFlow needed)
- AF2 Initial-Guess: JAX + Haiku (no PyTorch needed)
- They run in separate containers, so no conflicts

### TensorFlow Status

**Removed for testing.** If AF2 build or runtime fails with import errors, uncomment this line in af2.def:
```bash
pip install -q --no-cache-dir tensorflow==2.13.0
```

### Python 3.11 Requirement

nrbennet/dl_binder_design specifies Python 3.11 for CUDA 12 compatibility. Added via deadsnakes PPA.

---

## Build Instructions

### Option 1: Build All Containers (Recommended)

```bash
cd /home/user/Protein-De-Novo-Modification-and-Design-Platform
./build_containers.sh
```

This will build all 6 containers in parallel (if using the workstation build script).

### Option 2: Build Only AF2 Container

```bash
cd apptainer
apptainer build --fakeroot ../containers/af2.sif af2.def
```

**Expected build time:** 10-20 minutes
**Expected size:** ~3-5GB

---

## Testing Instructions

### Test 1: Container Build Verification

```bash
# Check container exists
ls -lh containers/af2.sif

# Test JAX GPU detection
apptainer exec --nv containers/af2.sif python3.11 -c "import jax; print('JAX devices:', jax.devices())"
```

**Expected output:**
```
JAX devices: [cuda(id=0), cuda(id=1), cuda(id=2)]
```

### Test 2: AF2 Import Test

```bash
apptainer exec --nv containers/af2.sif python3.11 -c "
import jax
import haiku as hk
import pyrosetta
print('JAX version:', jax.__version__)
print('Haiku imported successfully')
print('PyRosetta imported successfully')
"
```

### Test 3: Full Pipeline Test with AF2

Run a binder design test using AF2 instead of Boltz:

```bash
nextflow run main.nf \
    -profile test,workstation_ryzen7960x,binder_denovo \
    --pred_method af2 \
    --out_dir test_af2_results
```

**This will test:**
1. RFdiffusion backbone generation
2. ProteinMPNN sequence design
3. **AF2 Initial-Guess structure prediction** ← NEW
4. PyRosetta analysis

---

## Troubleshooting

### Issue: JAX not detecting GPUs

**Check CUDA libraries:**
```bash
apptainer exec --nv containers/af2.sif nvidia-smi
```

**Verify CUDA 12.1 visible:**
```bash
apptainer exec --nv containers/af2.sif nvcc --version
```

### Issue: TensorFlow import errors

If you see `ModuleNotFoundError: No module named 'tensorflow'`, uncomment the TensorFlow line in af2.def:

```bash
# Line 51 in af2.def
pip install -q --no-cache-dir tensorflow==2.13.0
```

Then rebuild the container.

### Issue: PyRosetta license errors

PyRosetta requires a free academic license. If you see license errors:
1. Visit: https://www.pyrosetta.org/downloads
2. Register for an academic license
3. Configure `~/.condarc` with credentials from the Gray Lab repository

### Issue: Out of memory during build

AF2 container build requires ~8GB RAM. If build fails:
```bash
export APPTAINER_TMPDIR=/path/to/large/tmp
apptainer build --fakeroot containers/af2.sif apptainer/af2.def
```

---

## Integration with ProteinDJ Pipeline

### Container Usage in Nextflow

The AF2 container is automatically used when `pred_method = 'af2'`:

```groovy
// nextflow.config
params {
    pred_method = 'af2'  // or 'boltz'
    af2_initial_guess = true  // Use initial guess (default)
}
```

### Model Weights Location

AF2 models are mounted at runtime:
```bash
${params.af2_models} → /dl_binder_design/af2_initial_guess/model_weights/params
```

Make sure you've downloaded AF2 models:
```bash
mkdir -p models/af2 && cd models/af2
wget https://storage.googleapis.com/alphafold/alphafold_params_2022-12-06.tar
tar -xf alphafold_params_2022-12-06.tar params_model_1_ptm.npz
```

### GPU Configuration

AF2 uses the same GPU settings as other containers:
- maxForks = 3 (parallel tasks)
- CUDA_VISIBLE_DEVICES=0,1,2 (excludes GPU 3)

---

## Next Steps

1. ✅ **af2.def updated** for RTX 5090 (CUDA 12.1, JAX 0.4.20, Python 3.11)
2. ✅ **build_containers_workstation.sh** updated to include af2
3. ⏳ **Build af2.sif** container (requires apptainer on workstation)
4. ⏳ **Test AF2 container** with GPU detection
5. ⏳ **Run pipeline test** with pred_method='af2'
6. ⏳ **Compare results** between AF2 and Boltz-2

---

## Files Modified

- `apptainer/af2.def` - Complete rewrite for RTX 5090 support
- `apptainer/build_containers_workstation.sh` - Added af2 to build list

## Files Not Modified (but relevant)

- `nextflow.config` - Already has AF2 configuration
- `modules/af2.nf` - Already has AF2 process definitions
- `scripts/filter_af2.py` - Already has AF2 filtering logic

---

## References

- **nrbennet/dl_binder_design:** https://github.com/nrbennet/dl_binder_design
- **PapenfussLab fork:** https://github.com/PapenfussLab/dl_binder_design
- **Paper:** "Improving de novo Protein Binder Design with Deep Learning" (Nature 2023)
- **JAX Documentation:** https://jax.readthedocs.io/
- **AF2 weights:** https://storage.googleapis.com/alphafold/

---

**Status:** Ready for building and testing on workstation with apptainer installed.
