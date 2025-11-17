# Workstation Quick Start Guide

## What Was Done

✅ **Test branch created:** `claude/workstation-setup-013vjdvYsxKRb6xKyBBmxBBA`
✅ **Workstation profile added:** `workstation_ryzen7960x` in `nextflow.config`
✅ **GPU configuration:** 3 GPUs active (GPUs 0,1,2), GPU 3 excluded
✅ **Documentation created:** Complete setup guide in `docs/WORKSTATION_SETUP.md`

---

## On Your Workstation: Setup Commands

### 1. Install Prerequisites

```bash
# Install Apptainer
sudo apt update
sudo apt install -y apptainer

# Install Java (for Nextflow)
sudo apt install -y default-jdk

# Install Nextflow
curl -s https://get.nextflow.io | bash
sudo mv nextflow /usr/local/bin/

# Verify installations
apptainer --version
nextflow -version
```

### 2. Clone Test Branch

```bash
# Navigate to your workspace
cd ~/projects  # or wherever you keep projects

# Clone repository
git clone https://github.com/MolBioFreak/Protein-De-Novo-Modification-and-Design-Platform.git
cd Protein-De-Novo-Modification-and-Design-Platform

# Checkout test branch
git fetch origin claude/workstation-setup-013vjdvYsxKRb6xKyBBmxBBA
git checkout claude/workstation-setup-013vjdvYsxKRb6xKyBBmxBBA
```

### 3. Verify GPU Access

```bash
# Should show all 4 GPUs
nvidia-smi

# Test Apptainer GPU access
apptainer exec --nv docker://nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
```

### 4. Run Test

```bash
# Run small test (4 designs × 2 sequences)
# First run downloads containers (~15GB) and models (~15GB)
# Takes ~30-60 minutes including downloads
# Subsequent runs: ~25-35 minutes

nextflow run main.nf \
    -profile test,workstation_ryzen7960x,monomer_denovo \
    --out_dir test_results
```

### 5. Monitor (in another terminal)

```bash
# Watch GPU usage
watch -n 1 nvidia-smi

# Watch pipeline progress
tail -f .nextflow.log
```

---

## What to Expect

**First Run:**
- Downloads containers to `~/.apptainer/cache/` (~15GB, one-time)
- Downloads models to `models/` (~15GB, one-time)
- Runs pipeline with 3 GPUs active
- GPU 3 (RTX 5060 Ti) will be idle (excluded by design)
- Total time: ~30-60 minutes

**Subsequent Runs:**
- Uses cached containers (instant)
- Uses cached models (instant)
- Total time: ~25-35 minutes

**GPU Usage:**
- GPUs 0, 1, 2 active (3090s and 5090)
- GPU 3 idle (5060 Ti excluded)
- VRAM usage: ~8-12GB per active GPU
- Up to 3 parallel GPU tasks

**Expected Results:**
```
test_results/
├── results/
│   ├── best_designs/        # PDB files that passed filters
│   ├── all_designs.csv      # All design metadata
│   └── best_designs.csv     # Filtered design metadata
├── run/                     # Intermediate files and logs
└── nextflow.log             # Execution log
```

---

## Success Criteria

✅ All 4 stages complete without errors
✅ GPUs 0, 1, 2 show activity in nvidia-smi
✅ GPU 3 remains idle (as designed)
✅ `test_results/results/all_designs.csv` contains data
✅ At least some designs in `test_results/results/best_designs/`

---

## If Issues Occur

**See full troubleshooting guide:**
```bash
cat docs/WORKSTATION_SETUP.md
```

**Common fixes:**

**Apptainer not found:**
```bash
sudo apt install apptainer
```

**GPU not accessible:**
```bash
nvidia-smi  # Check driver
sudo apt install nvidia-driver-550  # If needed
sudo reboot
```

**Resume after interruption:**
```bash
# Ctrl+C to stop, then:
nextflow run main.nf \
    -profile test,workstation_ryzen7960x,monomer_denovo \
    --out_dir test_results \
    -resume
```

---

## After Successful Test

Report back:
1. Total execution time
2. Any errors in log
3. Number of designs that passed filters
4. GPU usage patterns observed

Then we'll proceed with Phase 2: Adding new models (LigandMPNN, Chai-1, etc.)

---

## Configuration Details

**Profile:** `workstation_ryzen7960x`

**Resources:**
- 3 GPUs parallel (maxForks = 3)
- 10 CPU threads per GPU task
- 32GB RAM per GPU task
- 16 threads for CPU-only tasks

**GPU Restriction:**
```bash
CUDA_VISIBLE_DEVICES=0,1,2  # Only GPUs 0,1,2 visible to pipeline
```

**Container Source:**
- Auto-download from ProteinDJ cloud
- Cache location: `~/.apptainer/cache/`

**Model Paths:**
- RFdiffusion: `models/rfd/`
- AlphaFold2: `models/af2/`
- Boltz: `models/boltz/`
