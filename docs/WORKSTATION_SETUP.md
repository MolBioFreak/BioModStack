# BioModStack Workstation Setup Guide

## Hardware Configuration

**Current Configuration:**
- CPU: AMD Ryzen Threadripper 9960X (24 cores / 48 threads)
- RAM: 128GB DDR5
- Storage: 4TB NVMe (primary) + expansion drives
- GPUs: 4 GPUs in pipeline
  - GPU 0: RTX 5090 (32GB VRAM) - Primary compute
  - GPU 1: RTX 5060 Ti (16GB VRAM) - Secondary
  - GPU 2: RTX 3090 (24GB VRAM) - Batch processing
  - GPU 3: RTX 3090 (24GB VRAM) - Batch processing

**Total GPU resources:** 96GB VRAM, 4 parallel GPU tasks

---

## Prerequisites

### 1. Install Apptainer

**On Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install -y apptainer
```

**On other Linux distributions or for latest version:**
```bash
# Download latest release
wget https://github.com/apptainer/apptainer/releases/download/v1.3.0/apptainer_1.3.0_amd64.deb

# Install
sudo dpkg -i apptainer_1.3.0_amd64.deb

# If dependencies missing:
sudo apt-get install -f
```

**Verify installation:**
```bash
apptainer --version
# Should show: apptainer version 1.3.0 or higher
```

### 2. Verify GPU Access

```bash
# Test Apptainer can see GPUs
apptainer exec --nv docker://nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi

# Should show all 4 GPUs (pipeline will use only 0,1,2)
```

### 3. Install Nextflow

```bash
# Install Java (required by Nextflow)
sudo apt install -y default-jdk

# Install Nextflow
curl -s https://get.nextflow.io | bash

# Move to PATH
sudo mv nextflow /usr/local/bin/

# Verify
nextflow -version
# Should show: nextflow version 24.04.0 or higher
```

---

## Repository Setup

### 1. Clone Test Branch

```bash
# Clone repository
git clone https://github.com/MolBioFreak/Protein-De-Novo-Modification-and-Design-Platform.git
cd Protein-De-Novo-Modification-and-Design-Platform

# Checkout test branch
git fetch origin test/workstation-setup
git checkout test/workstation-setup
```

### 2. Download Model Weights

**Option A: Automatic (Recommended for first test)**

Models will auto-download on first run to their respective cache locations:
- RFdiffusion models: Download automatically to `models/rfd/`
- AlphaFold2 models: Download automatically to `models/af2/`
- Boltz models: Download automatically on first Boltz run to `models/boltz/`

Total: ~15GB, happens during first pipeline run.

**Option B: Manual Pre-download**

```bash
# Download model weights manually (faster for testing)
bash scripts/download_models.sh

# This downloads:
# - RFdiffusion models (~1.5 GB) → models/rfd/
# - AlphaFold2 models (~3.5 GB) → models/af2/
# - Boltz models (~10 GB) → models/boltz/ (on first Boltz run)
```

### 3. Container Strategy

**RECOMMENDED: Build Containers Locally (Much Faster)**

Building containers locally avoids slow downloads from the Australian server and uses your fast internet to pull base images from global CDNs.

**Quick Build (Recommended):**
```bash
# From project root directory
./build_containers.sh

# This will:
# - Download base images from NVIDIA/Docker Hub (fast global CDNs)
# - Build all 5 containers in parallel (3 at a time)
# - Validate each container after build
# - Save to containers/ directory
# - Time: 30-60 minutes one-time
```

**Advanced Build Options:**
```bash
# Build sequentially (slower but uses less resources)
./build_containers.sh --sequential

# Build specific container only
./build_containers.sh --container rfdiffusion

# Build with 2 parallel processes (less resource intensive)
./build_containers.sh --parallel 2

# See all options
./build_containers.sh --help
```

**Alternative: Auto-Download from Server (Not Recommended)**

If you skip local building, containers will auto-download from Australian server on first run:
```bash
# Containers cached to: ~/.apptainer/cache/
# Total: ~15GB
# Download time: 1-2 hours from slow server

# Containers:
# - rfdiffusion.sif (~2.5 GB)
# - dl_binder_design.sif (~5 GB) - includes ProteinMPNN, PyRosetta, AF2
# - fampnn.sif (~1.5 GB)
# - boltz2.sif (~3 GB)
# - pyrosetta_tools.sif (~4 GB)
```

**Container Build Details:**

The build script (`apptainer/build_containers_workstation.sh`) provides:
- Pre-build validation (Apptainer, disk space, etc.)
- Parallel building with resource management
- Post-build container testing
- Clear progress reporting
- Automatic error handling

Containers are built from definition files (`.def`) in `apptainer/` directory that:
- Pull base images from NVIDIA container registry (fast)
- Clone repositories and install dependencies
- Are fully compatible with Nextflow/BioModStack

---

## First Test Run

### 1. Small Test (Recommended)

```bash
# Run BioModStack's built-in test
# 4 designs × 2 sequences = 8 total predictions
# Should take ~25-35 minutes

nextflow run main.nf \
    -profile test,workstation_ryzen7960x,monomer_denovo \
    --out_dir test_results
```

**What happens:**
1. Downloads containers to cache (first run only, ~15GB)
2. RFdiffusion generates 4 monomer backbones (uses 3 GPUs)
3. FAMPNN designs 2 sequences per backbone = 8 sequences (uses 3 GPUs)
4. Boltz-2 predicts structures for all 8 sequences (uses 3 GPUs)
5. PyRosetta analyzes results (CPU)
6. Generates `test_results/` directory with:
   - `results/best_designs/` - PDB files that passed filters
   - `results/all_designs.csv` - Metadata for all designs
   - `results/best_designs.csv` - Metadata for filtered designs

### 2. Monitor During Run

**In another terminal:**
```bash
# Watch GPU usage
watch -n 1 nvidia-smi

# Should see:
# - GPUs 0, 1, 2 active (GPU 3 idle - excluded from pipeline)
# - Memory usage ~8-12GB per GPU during tasks
# - Utilization cycling as tasks complete
```

**Check progress:**
```bash
# View Nextflow log
tail -f .nextflow.log

# Look for:
# - Task completions
# - GPU assignments
# - Any errors
```

### 3. Verify Results

```bash
# Check output files
ls -lh test_results/results/best_designs/

# View metadata
cat test_results/results/all_designs.csv

# Check execution report
# Nextflow generates HTML report with resource usage
ls -lh .nextflow.log
```

---

## Understanding the Pipeline Flow

**Stage 1: RFdiffusion (Backbone Generation)**
- Generates 4 protein backbones
- Uses 3 GPUs in parallel
- Output: `fold_0.pdb` through `fold_3.pdb`

**Stage 2: Sequence Design (FAMPNN)**
- Designs 2 sequences for each backbone
- Uses 3 GPUs in parallel
- Output: `fold_0_seq_0.pdb`, `fold_0_seq_1.pdb`, etc.

**Stage 3: Structure Prediction (Boltz-2)**
- Predicts 3D structure for each sequence
- Uses 3 GPUs in parallel
- Output: `fold_X_seq_X_boltzpred.pdb` with confidence metrics

**Stage 4: Filtering**
- Each stage filters designs based on quality metrics
- Only best designs proceed to next stage
- Some designs may be filtered out (normal)

**Stage 5: Analysis**
- PyRosetta calculates additional metrics
- Generates CSV files with all metadata
- CPU-only stage

---

## Expected Performance

**With 3 GPUs (RTX 3090 × 2, RTX 5090 × 1):**

| Stage | Tasks | Parallel | Time per Task | Total Time |
|-------|-------|----------|---------------|------------|
| RFdiffusion | 4 designs | 3 GPUs | ~2-3 min | ~4-6 min |
| FAMPNN | 8 sequences | 3 GPUs | ~1-2 min | ~3-6 min |
| Boltz-2 | 8 predictions | 3 GPUs | ~5-10 min | ~15-30 min |
| Analysis | All designs | CPU | ~1-2 min | ~1-2 min |

**Total for test run: 25-35 minutes**

---

## Troubleshooting

### Issue: Apptainer Not Found

**Solution:**
```bash
# Check if installed
which apptainer

# If not found, install:
sudo apt install apptainer

# Or build from source
wget https://github.com/apptainer/apptainer/releases/download/v1.3.0/apptainer_1.3.0_amd64.deb
sudo dpkg -i apptainer_1.3.0_amd64.deb
```

### Issue: GPU Not Detected

**Solution:**
```bash
# Check NVIDIA driver
nvidia-smi

# If not working, install drivers:
sudo apt install nvidia-driver-550

# Reboot
sudo reboot
```

### Issue: Container Download Slow

**Solution 1: Build Locally (RECOMMENDED)**
```bash
# Build containers locally instead of downloading from slow server
./build_containers.sh

# This uses fast CDNs for base images and builds everything locally
# Time: 30-60 minutes (much faster than slow download)
```

**Solution 2: Parallel Download (Still Slow)**
```bash
# If you prefer to download pre-built containers, use parallel downloads
sudo apt install aria2

mkdir -p containers
cd containers

# Download with multiple connections
aria2c -x 16 -s 16 https://object-store.rc.nectar.org.au/v1/AUTH_b6f9bdf15faf4320a7587fd42f62e530/ContainerHub/rfdiffusion.sif &
aria2c -x 16 -s 16 https://object-store.rc.nectar.org.au/v1/AUTH_b6f9bdf15faf4320a7587fd42f62e530/ContainerHub/boltz2.sif &
aria2c -x 16 -s 16 https://object-store.rc.nectar.org.au/v1/AUTH_b6f9bdf15faf4320a7587fd42f62e530/ContainerHub/fampnn.sif &
aria2c -x 16 -s 16 https://object-store.rc.nectar.org.au/v1/AUTH_b6f9bdf15faf4320a7587fd42f62e530/ContainerHub/dl_binder_design.sif &
aria2c -x 16 -s 16 https://object-store.rc.nectar.org.au/v1/AUTH_b6f9bdf15faf4320a7587fd42f62e530/ContainerHub/pyrosetta_tools.sif &
wait

cd ..
```

### Issue: Container Build Fails

**Problem: Apptainer fakeroot not available**
```bash
# Check if fakeroot is available
apptainer build --help | grep fakeroot

# If not available, install:
sudo apt install uidmap

# Or build without fakeroot (requires sudo):
cd apptainer
sudo apptainer build ../containers/rfdiffusion.sif rfdiffusion.def
# Repeat for other containers
```

**Problem: Out of disk space during build**
```bash
# Check available space
df -h .

# Clean up Apptainer cache if needed:
apptainer cache clean --all

# Or use different temp directory with more space:
export BUILD_TEMP_DIR=/path/to/large/disk/tmp
./build_containers.sh
```

**Problem: Build fails during git clone or pip install**
```bash
# Check build log:
cat /tmp/${USER}/apptainer_build/<container>_build.log

# Common issues:
# - Network timeout: Retry the build
# - Git clone failed: Check internet connection
# - Pip install failed: May need to update base image in .def file
```

**Problem: Container test warnings**
```bash
# Some import tests may fail without GPU - this is usually OK
# Container will work when run with --nv flag (GPU access)

# To verify container works:
apptainer exec --nv containers/rfdiffusion.sif nvidia-smi
```

### Issue: Out of Memory

**Solution:**
```bash
# If tasks fail with OOM, reduce parallel tasks:
# Edit nextflow.config workstation profile:

process {
    withLabel: 'gpu' {
        maxForks = 2  // Reduce from 3 to 2
    }
}
```

### Issue: Task Hangs

**Solution:**
```bash
# Kill and resume
Ctrl+C  # Kill Nextflow

# Resume from last completed task
nextflow run main.nf \
    -profile test,workstation_ryzen7960x,monomer_denovo \
    --out_dir test_results \
    -resume
```

---

## Next Steps

After successful test run:

1. **Run larger design campaigns:**
   ```bash
   nextflow run main.nf \
       -profile workstation_ryzen7960x,binder_denovo \
       --rfd_input_pdb your_target.pdb \
       --rfd_contigs '[A1-150/0 60-100]' \
       --rfd_hotspots '[A50,A75,A100]' \
       --rfd_num_designs 100 \
       --seqs_per_design 4 \
       --out_dir results_binder
   ```

2. **Explore other design modes:**
   - `monomer_foldcond` - Design with scaffold templates
   - `monomer_motifscaff` - Scaffold functional motifs
   - `binder_foldcond` - Binder design with templates
   - See `docs/modes.md` for details

3. **Add new models** (Phase 2):
   - LigandMPNN for ligand binding
   - Chai-1 for improved prediction
   - ColabFold for fast predictions

---

## Resource Usage Summary

**Disk Space:**
- Containers: ~15GB (one-time, cached)
- Models: ~15GB (one-time, downloaded)
- Results: Variable (depends on campaign size)
- **Total initial:** ~30GB + results

**Memory:**
- GPU tasks: 32GB each × 3 parallel = up to 96GB
- CPU tasks: 32GB
- **Peak usage:** ~100GB (well within 128GB available)

**GPU VRAM:**
- RFdiffusion: ~8-10GB per GPU
- FAMPNN: ~6-8GB per GPU
- Boltz-2: ~10-12GB per GPU
- **All fit comfortably in 24GB+ VRAM**

---

## GPU 3 (RTX 5060 Ti) - Reserved

GPU 3 is **intentionally excluded** from the pipeline via `CUDA_VISIBLE_DEVICES=0,1,2`.

**Why:** Structure prediction tasks (Boltz, AF2) can use 16-20GB VRAM for large proteins/complexes. The 5060 Ti's 16GB might bottleneck or fail on large tasks while other GPUs succeed.

**GPU 3 still available for:**
- Manual testing
- Non-pipeline work
- Other applications

**To use GPU 3 in future** (after testing Phase 1):
- Can selectively enable for low-VRAM stages (RFdiffusion, ProteinMPNN)
- Keep excluded from prediction stages
- Requires custom process configuration (advanced)

---

## Support

- Documentation: `docs/` directory
- BioModStack GitHub: https://github.com/PapenfussLab/biomodstack
- Issues: Report in project repository
