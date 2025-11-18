# ProteinDJ Container Build System

This directory contains Apptainer definition files (`.def`) and build scripts for all ProteinDJ containers.

## Quick Start

**For Workstation Users:**
```bash
# From project root directory
./build_containers.sh
```

This builds all 5 containers locally in ~30-60 minutes, avoiding slow downloads from the Australian server.

---

## Container Definitions

| Container | File | Purpose | Base Image |
|-----------|------|---------|------------|
| `rfdiffusion.sif` | `rfdiffusion.def` | Protein backbone generation with RFdiffusion | NVIDIA CUDA 11.8 |
| `fampnn.sif` | `fampnn.def` | Full-Atom MPNN sequence design | NVIDIA CUDA 12.8 |
| `dl_binder_design.sif` | `dl_binder_design.def` | ProteinMPNN + AlphaFold2 + PyRosetta | NVIDIA CUDA 11.8 |
| `boltz2.sif` | `boltz2.def` | Boltz-2 structure prediction | NVIDIA CUDA 12.8 |
| `pyrosetta_tools.sif` | `pyrosetta_tools.def` | PyRosetta analysis and filtering | Python 3.10 |

---

## Build Scripts

### 1. Workstation Build Script (Recommended)

**File:** `build_containers_workstation.sh`

**Features:**
- Pre-build validation (Apptainer, disk space, dependencies)
- Parallel building with resource management
- Post-build container testing
- Clear progress reporting and logging
- Automatic error handling

**Usage:**
```bash
# Build all containers (default: 3 parallel)
./build_containers_workstation.sh

# Build sequentially
./build_containers_workstation.sh --sequential

# Build specific container
./build_containers_workstation.sh --container rfdiffusion

# Adjust parallelism
./build_containers_workstation.sh --parallel 2

# Skip validation tests
./build_containers_workstation.sh --skip-test

# Show help
./build_containers_workstation.sh --help
```

**Output:**
- Containers: `../containers/*.sif`
- Logs: `/tmp/${USER}/apptainer_build/*_build.log`
- Cache: `${HOME}/.apptainer/cache/`

### 2. HPC Build Script (SLURM)

**File:** `build_containers.sh`

For HPC clusters with SLURM scheduler. Submits batch jobs for parallel container builds.

---

## Build Process

Each container build follows this workflow:

1. **Bootstrap:** Pull base Docker/OCI image from registry
2. **Post-install:** Install system dependencies, Python packages
3. **Clone repos:** Download specific versions of tools (RFdiffusion, FAMPNN, etc.)
4. **Install:** Install Python packages and tools
5. **Environment:** Set environment variables
6. **Runscript:** Define default container entry point

---

## Prerequisites

### Required Software

- **Apptainer** (v1.1.0+)
  ```bash
  sudo apt install apptainer
  ```

- **Git** (for cloning repositories during build)
  ```bash
  sudo apt install git
  ```

- **Fakeroot** (for rootless builds)
  ```bash
  # Usually included with Apptainer
  # If not: sudo apt install uidmap
  ```

### System Requirements

- **Disk Space:** 30GB+ free (for building and caching)
- **Memory:** 8GB+ recommended
- **Internet:** Fast connection (downloads base images)

---

## Manual Building

To build containers manually without scripts:

```bash
cd apptainer
mkdir -p ../containers

# Build with fakeroot (no sudo needed)
apptainer build --fakeroot ../containers/rfdiffusion.sif rfdiffusion.def

# Or with sudo
sudo apptainer build ../containers/rfdiffusion.sif rfdiffusion.def

# Repeat for each container:
apptainer build --fakeroot ../containers/fampnn.sif fampnn.def
apptainer build --fakeroot ../containers/dl_binder_design.sif dl_binder_design.def
apptainer build --fakeroot ../containers/boltz2.sif boltz2.def
apptainer build --fakeroot ../containers/pyrosetta_tools.sif pyrosetta_tools.def
```

---

## Testing Containers

### Quick Test
```bash
# Check container exists and is valid
apptainer inspect containers/rfdiffusion.sif

# Test GPU access
apptainer exec --nv containers/rfdiffusion.sif nvidia-smi

# Test container runscript
apptainer run containers/rfdiffusion.sif --help
```

### Container-Specific Tests

**RFdiffusion:**
```bash
apptainer exec containers/rfdiffusion.sif python3.10 -c "import rfdiffusion; print('OK')"
```

**FAMPNN:**
```bash
apptainer exec containers/fampnn.sif python -c "import torch; print('PyTorch:', torch.__version__)"
```

**Boltz-2:**
```bash
apptainer exec containers/boltz2.sif bash -c ". /opt/venv/bin/activate && boltz --help"
```

**DL Binder Design:**
```bash
apptainer exec containers/dl_binder_design.sif python -c "import pyrosetta; print('PyRosetta loaded')"
```

**PyRosetta Tools:**
```bash
apptainer exec containers/pyrosetta_tools.sif python -c "from Bio import PDB; print('BioPython OK')"
```

---

## Nextflow Integration

Containers are automatically used by Nextflow when you use the `workstation_ryzen7960x` profile:

**nextflow.config:**
```groovy
workstation_ryzen7960x {
    params {
        container_dir = "${projectDir}/containers"  // Points to local builds
    }
}

process {
    withLabel: 'RFDiffusion' {
        container = "${params.container_dir}/rfdiffusion.sif"
    }
    // ... other processes
}
```

---

## Troubleshooting

### Build Fails with Permission Error

**Problem:** Cannot write to build directory

**Solution:**
```bash
# Use temp directory you own
export APPTAINER_TMPDIR=/tmp/${USER}/apptainer_build
mkdir -p $APPTAINER_TMPDIR
./build_containers_workstation.sh
```

### Build Fails with "No space left on device"

**Problem:** Disk full

**Solution:**
```bash
# Check disk space
df -h

# Clean Apptainer cache
apptainer cache clean --all

# Use different temp directory with more space
export BUILD_TEMP_DIR=/path/to/large/disk/tmp
./build_containers_workstation.sh
```

### Build Hangs or Times Out

**Problem:** Network download timeout

**Solution:**
```bash
# Retry the build (Apptainer caches progress)
./build_containers_workstation.sh

# Or build specific failed container
./build_containers_workstation.sh --container <name>
```

### Container Test Fails

**Problem:** Import test fails during validation

**Solution:**
```bash
# Some tests require GPU access - this is normal
# Container will work when run with --nv flag

# Test with GPU:
apptainer exec --nv containers/rfdiffusion.sif nvidia-smi
```

### Fakeroot Not Available

**Problem:** `--fakeroot` flag not supported

**Solution:**
```bash
# Install uidmap
sudo apt install uidmap

# Or build with sudo
sudo apptainer build containers/rfdiffusion.sif rfdiffusion.def
```

---

## Build Times

Approximate build times per container (with fast internet):

| Container | Time | Notes |
|-----------|------|-------|
| rfdiffusion | 10-15 min | Large conda packages |
| fampnn | 8-12 min | PyTorch, model downloads |
| dl_binder_design | 15-20 min | Largest container, many dependencies |
| boltz2 | 12-18 min | Boltz package compilation |
| pyrosetta_tools | 10-15 min | BioPython, PyRosetta |

**Total (parallel, 3 at a time):** 30-60 minutes
**Total (sequential):** 60-90 minutes

---

## Advanced Topics

### Modifying Container Definitions

To customize containers, edit the `.def` files:

1. Edit definition file (e.g., `rfdiffusion.def`)
2. Rebuild: `apptainer build --fakeroot ../containers/rfdiffusion.sif rfdiffusion.def`
3. Test: `apptainer exec ../containers/rfdiffusion.sif <test command>`

### Using Remote Build

To offload building to a remote machine:

```bash
# On remote machine with Apptainer
ssh remote-server
cd /path/to/proteindj/apptainer
./build_containers_workstation.sh

# Copy back to local machine
scp remote-server:/path/to/proteindj/containers/*.sif ../containers/
```

### Converting Docker Images

If you have Docker images, convert to Apptainer:

```bash
# Pull from Docker Hub and convert
apptainer build rfdiffusion.sif docker://username/rfdiffusion:latest

# From local Docker daemon
apptainer build rfdiffusion.sif docker-daemon://rfdiffusion:latest
```

---

## Container Sizes

Expected final container sizes:

| Container | Size |
|-----------|------|
| rfdiffusion.sif | ~2.5 GB |
| fampnn.sif | ~1.5 GB |
| dl_binder_design.sif | ~5 GB |
| boltz2.sif | ~3 GB |
| pyrosetta_tools.sif | ~4 GB |
| **Total** | **~16 GB** |

---

## Support

For issues with:
- **Container builds:** Check logs in `/tmp/${USER}/apptainer_build/`
- **Apptainer:** https://apptainer.org/docs/
- **ProteinDJ:** https://github.com/PapenfussLab/proteindj

---

## References

- **Apptainer Documentation:** https://apptainer.org/docs/
- **Container Definition Files:** https://apptainer.org/docs/user/main/definition_files.html
- **RFdiffusion:** https://github.com/RosettaCommons/RFdiffusion
- **Full-Atom MPNN:** https://github.com/richardshuai/fampnn
- **Boltz-2:** https://github.com/jwohlwend/boltz
- **AlphaFold2:** https://github.com/deepmind/alphafold
- **PyRosetta:** https://www.pyrosetta.org/
