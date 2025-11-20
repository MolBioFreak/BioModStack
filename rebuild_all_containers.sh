#!/bin/bash

################################################################################
# Rebuild ALL Containers with RTX 5090 Support
#
# This script rebuilds all 5 containers with:
# - PyTorch 2.5.1
# - CUDA 12.4
# - Support for compute capability 12.0 (RTX 5090 + RTX 5060 Ti)
#
# Estimated time: 30-60 minutes
################################################################################

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINERS_DIR="${SCRIPT_DIR}/containers"
BACKUP_DIR="${SCRIPT_DIR}/containers_backup_$(date +%Y%m%d_%H%M%S)"
BUILD_TEMP_DIR="${BUILD_TEMP_DIR:-/tmp/${USER}/apptainer_rebuild}"

echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Rebuild ALL Containers with RTX 5090 Support                 ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}[INFO]${NC} This will rebuild all 5 containers:"
echo "  1. rfdiffusion.sif"
echo "  2. fampnn.sif"
echo "  3. dl_binder_design.sif"
echo "  4. boltz2.sif"
echo "  5. pyrosetta_tools.sif"
echo ""
echo -e "${BLUE}[INFO]${NC} All will support:"
echo "  - RTX 5090 (compute capability 12.0)"
echo "  - RTX 5060 Ti (compute capability 12.0)"
echo "  - RTX 3090 (compute capability 8.6)"
echo ""
echo -e "${YELLOW}[WARNING]${NC} Estimated time: 30-60 minutes"
echo ""

read -p "Proceed with rebuild? [y/N] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Rebuild cancelled."
    exit 0
fi

# Setup
mkdir -p "$BUILD_TEMP_DIR"
export APPTAINER_TMPDIR="$BUILD_TEMP_DIR"

# Backup existing containers
if [ -d "$CONTAINERS_DIR" ]; then
    echo -e "${BLUE}[INFO]${NC} Backing up existing containers to: $BACKUP_DIR"
    mkdir -p "$BACKUP_DIR"
    cp -r "$CONTAINERS_DIR"/*.sif "$BACKUP_DIR/" 2>/dev/null || true
    echo -e "${GREEN}[SUCCESS]${NC} Backup complete"
    echo ""
fi

cd "${SCRIPT_DIR}/apptainer"

# Container list
declare -A CONTAINERS=(
    ["rfdiffusion"]="rfdiffusion.def"
    ["fampnn"]="fampnn.def"
    ["dl_binder_design"]="dl_binder_design.def"
    ["boltz2"]="boltz2.def"
    ["pyrosetta_tools"]="pyrosetta_tools.def"
)

BUILD_ORDER=("rfdiffusion" "fampnn" "dl_binder_design" "boltz2" "pyrosetta_tools")

# Function to build a container
build_container() {
    local name=$1
    local def_file="${CONTAINERS[$name]}"
    local output_file="${CONTAINERS_DIR}/${name}.sif"
    local log_file="${BUILD_TEMP_DIR}/${name}_rebuild.log"

    echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}  Building: ${name}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"

    local start_time=$(date +%s)

    # Remove old container
    rm -f "$output_file"

    # Build
    echo -e "${BLUE}[INFO]${NC} Building with RTX 5090 support..."
    echo -e "${BLUE}[INFO]${NC} Log: $log_file"

    if apptainer build --fakeroot "$output_file" "$def_file" > "$log_file" 2>&1; then
        local end_time=$(date +%s)
        local duration=$((end_time - start_time))
        local size=$(du -h "$output_file" | cut -f1)

        echo -e "${GREEN}[SUCCESS]${NC} Built in $((duration / 60))m $((duration % 60))s ($size)"
        echo ""
        return 0
    else
        local end_time=$(date +%s)
        local duration=$((end_time - start_time))

        echo -e "${RED}[ERROR]${NC} Build failed after $((duration / 60))m $((duration % 60))s"
        echo -e "${RED}[ERROR]${NC} Check log: $log_file"
        echo ""
        echo "Last 50 lines of log:"
        tail -50 "$log_file"
        return 1
    fi
}

# Build containers in waves (2 parallel at a time)
echo -e "${BLUE}[INFO]${NC} Building containers in parallel waves..."
echo ""

# Wave 1: RFdiffusion and FAMPNN
echo -e "${CYAN}=== Wave 1/3: RFdiffusion + FAMPNN ===${NC}"
build_container "rfdiffusion" &
PID1=$!
build_container "fampnn" &
PID2=$!
wait $PID1 $PID2

# Wave 2: DL Binder Design and Boltz2
echo -e "${CYAN}=== Wave 2/3: DL Binder Design + Boltz2 ===${NC}"
build_container "dl_binder_design" &
PID3=$!
build_container "boltz2" &
PID4=$!
wait $PID3 $PID4

# Wave 3: PyRosetta Tools
echo -e "${CYAN}=== Wave 3/3: PyRosetta Tools ===${NC}"
build_container "pyrosetta_tools"

# Verify all containers
echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Verification                                                  ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

FAILED=0
for name in "${BUILD_ORDER[@]}"; do
    sif_file="${CONTAINERS_DIR}/${name}.sif"
    if [ -f "$sif_file" ]; then
        size=$(du -h "$sif_file" | cut -f1)
        echo -e "${GREEN}✓${NC} $name.sif ($size)"
    else
        echo -e "${RED}✗${NC} $name.sif (MISSING)"
        FAILED=1
    fi
done

echo ""
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}[SUCCESS]${NC} All containers rebuilt successfully!"
    echo ""
    echo -e "${BLUE}[INFO]${NC} Total container size:"
    du -sh "$CONTAINERS_DIR"
    echo ""
    echo -e "${BLUE}[INFO]${NC} Old containers backed up to:"
    echo "  $BACKUP_DIR"
    echo ""
    echo -e "${GREEN}Next step:${NC}"
    echo "  Run the pipeline: nextflow run main.nf -profile test,workstation_ryzen7960x,monomer_denovo --out_dir test_results"
    echo ""
else
    echo -e "${RED}[ERROR]${NC} Some containers failed to build"
    echo "Check logs in: $BUILD_TEMP_DIR"
    exit 1
fi
