# Workstation Testing Issues and Workarounds

## Session Date: November 17-18, 2025

## System Configuration
- **Hardware:** AMD Ryzen 7960x, 128GB RAM
- **GPUs:**
  - GPU 0: RTX 3090 (24GB, compute 8.6)
  - GPU 1: RTX 3090 (24GB, compute 8.6)
  - GPU 2: RTX 5090 (32GB, compute 12.0)
  - GPU 3: RTX 5060 Ti (16GB, compute 12.0) - excluded from pipeline
- **OS:** Pop!_OS (Ubuntu-based)
- **Nextflow:** 25.10.0
- **Apptainer:** 1.3.0

---

## Issue 1: Directory Name with Space

**Problem:**
```
FATAL: failed to retrieve path for fork/Protein-De-Novo-Modification-and-Design-Platform
```

**Root Cause:**
Project directory named `ProteinDJ fork` (with space) caused Apptainer bind mount failures. Path was split at the space character.

**Workaround:**
```bash
cd ~
mv "ProteinDJ fork" ProteinDJ_fork
```

**Solution:** Renamed directory to remove space.

---

## Issue 2: Models Directory Not Found

**Problem:**
```
FATAL: mount source .../models/rfd doesn't exist
```

**Root Cause:**
Model weights (~15GB) were never downloaded. Only containers were built.

**Workaround:**
```bash
bash scripts/download_models.sh
```

**Timeline:**
- RFdiffusion models: ~3.7GB
- AlphaFold2 models: ~5.2GB
- Boltz-2 models: ~6GB
- Total: ~15GB download, ~7GB final size
- Estimated time with fast internet: 10-20 minutes

---

## Issue 3: RTX 5090 Not Supported (CRITICAL)

**Problem:**
```
RuntimeError: CUDA error: no kernel image is available for execution on the device
```

**Root Cause:**
Original container definitions used **PyTorch 2.0.1 + CUDA 11.8** (compiled 2023), which has NO support for:
- RTX 5090 (compute capability 12.0 - Blackwell architecture)
- RTX 5060 Ti (compute capability 12.0)

**Workaround Attempts:**

### Attempt 1: PyTorch 2.5.1 + CUDA 12.4
**Failed:** DGL (Deep Graph Library) doesn't have wheels for PyTorch 2.5.1
```
ERROR: Could not find a version that satisfies the requirement dgl
```

### Attempt 2: PyTorch 2.4.1 + CUDA 12.1 with DGL from wheel server
**Failed:** DGL wheel server returned 403 Forbidden
```
ERROR: HTTP error 403 while getting https://data.dgl.ai/wheels/torch-2.4/cu121/dgl-2.5.0...
```

### Attempt 3: PyTorch 2.4.1 + CUDA 12.1 with DGL from PyPI
**Success:** DGL 2.1.0 installed successfully from PyPI

**Final Solution:**
- **PyTorch:** 2.4.1 (supports compute capability 12.0)
- **CUDA:** 12.1
- **DGL:** 2.1.0 from PyPI (not wheel server)
- **Base Image:** `nvcr.io/nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04`

**Applied to ALL 5 containers:**
1. rfdiffusion.sif
2. fampnn.sif
3. dl_binder_design.sif
4. boltz2.sif
5. pyrosetta_tools.sif

---

## Issue 4: Docker Base Image Pull Failures

**Problem:**
```
FATAL: conveyor failed to get: no descriptor found for reference
```

**Root Cause:**
Base images from Docker Hub (`nvidia/cuda:12.8.0-base-ubuntu24.04`) either don't exist or had corrupted cache entries.

**Workarounds:**

### Step 1: Clear Apptainer cache
```bash
apptainer cache clean -f
```

### Step 2: Use NVIDIA Container Registry instead of Docker Hub
**Changed from:**
```
From: nvidia/cuda:12.8.0-base-ubuntu24.04
```

**Changed to:**
```
From: nvcr.io/nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04
```

**Why:** NVIDIA's official registry (`nvcr.io`) is more reliable than Docker Hub for CUDA images.

---

## Issue 5: DL Binder Design Shell Syntax Error

**Problem:**
```
/.post.script: 81: eval: Syntax error: "(" unexpected (expecting "}")
```

**Root Cause:**
Apptainer `%post` section defaulted to `/bin/sh` (not bash). Micromamba's `eval "$(micromamba shell hook --shell bash)"` requires bash-specific features.

**Solution:**
```diff
-%post
+%post -c /bin/bash
```

Explicitly use bash for the post-install section.

---

## Final Container Configuration

All 5 containers now use:
- **PyTorch:** 2.4.1
- **CUDA:** 12.1
- **Base Image:** `nvcr.io/nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04`
- **DGL:** 2.1.0 from PyPI (rfdiffusion only)

### Build Times (Parallel)
- Wave 1: rfdiffusion + fampnn: ~17 minutes
- Wave 2: boltz2 + pyrosetta_tools: ~12 minutes
- Wave 3: dl_binder_design: ~6 minutes
- **Total:** ~35 minutes

### Container Sizes
- rfdiffusion.sif: 7.8GB
- fampnn.sif: 11GB
- dl_binder_design.sif: 2.4GB
- boltz2.sif: 9.3GB
- pyrosetta_tools.sif: 9.0GB
- **Total:** ~39GB

---

## GPU Support Matrix

| GPU | Compute Cap | PyTorch 2.0.1 | PyTorch 2.4.1 | Status |
|-----|-------------|---------------|---------------|--------|
| RTX 3090 | 8.6 | ✅ Supported | ✅ Supported | Working |
| RTX 3090 | 8.6 | ✅ Supported | ✅ Supported | Working |
| RTX 5090 | 12.0 | ❌ Not Supported | ✅ Supported | **NOW WORKING** |
| RTX 5060 Ti | 12.0 | ❌ Not Supported | ✅ Supported | Excluded (reserved) |

---

## Build Scripts Created

### 1. `build_containers_workstation.sh`
Full-featured parallel container build system with:
- Pre-build validation
- Parallel building (3 at a time)
- Post-build testing
- Progress reporting
- Error handling with detailed logs

### 2. `build_containers.sh`
Simple wrapper script for easy access.

### 3. `rebuild_all_containers.sh`
Complete rebuild script with automatic backup of old containers.

---

## Files Modified

### Container Definitions
- `apptainer/rfdiffusion.def`
- `apptainer/fampnn.def`
- `apptainer/dl_binder_design.def`
- `apptainer/boltz2.def`
- `apptainer/pyrosetta_tools.def`

### Documentation
- `RTX_5090_SUPPORT.md` (new)
- `WORKSTATION_QUICKSTART.md`
- `docs/WORKSTATION_SETUP.md`
- `apptainer/README.md` (new)

### Build Scripts
- `build_containers.sh` (new)
- `apptainer/build_containers_workstation.sh` (new)
- `rebuild_all_containers.sh` (new)

---

## Key Takeaways

1. **Always check GPU compute capability vs PyTorch version compatibility**
   - RTX 5090 (compute 12.0) requires PyTorch 2.4.0+
   - PyTorch 2.0.1 only supports up to compute capability 8.9

2. **Use NVIDIA Container Registry for CUDA base images**
   - More reliable than Docker Hub
   - Better maintained for HPC/scientific computing

3. **DGL dependency chain is fragile**
   - Wheel servers may block downloads
   - PyPI version works but may have fewer optimizations

4. **Apptainer defaults matter**
   - `%post` uses `/bin/sh` not bash
   - Use `%post -c /bin/bash` for bash-specific features

5. **Directory names with spaces break bind mounts**
   - Always use underscores or hyphens in project paths

6. **Local container builds are faster than remote downloads**
   - Remote: 1-2+ hours from slow Australian server
   - Local: 30-60 minutes using fast CDNs for base images

---

## Next Steps

1. ✅ All containers rebuilt with RTX 5090 support
2. ⏳ Download model weights (~15GB)
3. ⏳ Test pipeline with all 3 GPUs:
   ```bash
   nextflow run main.nf \
       -profile test,workstation_ryzen7960x,monomer_denovo \
       --out_dir test_results
   ```
4. ⏳ Verify GPU utilization in nvidia-smi during run

---

## References

- PyTorch 2.4.1 release notes: https://pytorch.org/
- CUDA 12.1 documentation: https://docs.nvidia.com/cuda/
- RTX 5090 specifications: Compute capability 12.0 (Blackwell)
- DGL PyPI: https://pypi.org/project/dgl/
