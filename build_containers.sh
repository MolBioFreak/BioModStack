#!/bin/bash

################################################################################
# ProteinDJ Container Build - Quick Start Wrapper
#
# This script builds all required Apptainer containers locally for workstation
# use, avoiding slow downloads from the Australian server.
#
# Requirements:
#   - Apptainer (sudo apt install apptainer)
#   - Git (sudo apt install git)
#   - 30GB+ free disk space
#   - Fast internet connection
#
# Estimated time: 30-60 minutes (one-time setup)
#
# Usage:
#   ./build_containers.sh              # Build all containers (3 parallel)
#   ./build_containers.sh --sequential # Build one at a time
#   ./build_containers.sh --help       # Show all options
#
################################################################################

set -euo pipefail

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                                                                       ║${NC}"
echo -e "${BLUE}║  ProteinDJ Local Container Build                                     ║${NC}"
echo -e "${BLUE}║                                                                       ║${NC}"
echo -e "${BLUE}║  This will build 5 containers locally instead of downloading from    ║${NC}"
echo -e "${BLUE}║  the slow Australian server.                                         ║${NC}"
echo -e "${BLUE}║                                                                       ║${NC}"
echo -e "${BLUE}║  Estimated time: 30-60 minutes                                       ║${NC}"
echo -e "${BLUE}║  Disk space needed: ~15GB                                            ║${NC}"
echo -e "${BLUE}║                                                                       ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Verify nextflow.config points to local containers directory
if grep -q 'container_dir.*=.*"\${projectDir}/containers"' "$SCRIPT_DIR/nextflow.config"; then
    echo -e "${GREEN}✓${NC} Nextflow config verified: will use local containers"
else
    echo -e "${YELLOW}!${NC} Warning: Check nextflow.config workstation profile"
    echo "  Expected: container_dir = \"\${projectDir}/containers\""
fi

echo ""
echo "Containers to build:"
echo "  1. rfdiffusion       - Protein backbone generation"
echo "  2. fampnn            - Full-Atom MPNN sequence design"
echo "  3. dl_binder_design  - ProteinMPNN + AlphaFold2 + PyRosetta"
echo "  4. boltz2            - Boltz-2 structure prediction"
echo "  5. pyrosetta_tools   - Analysis and filtering"
echo ""

# Confirm before proceeding
read -p "Proceed with container build? [y/N] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Build cancelled."
    exit 0
fi

echo ""
echo "Starting build..."
echo ""

# Run the main build script
exec "$SCRIPT_DIR/apptainer/build_containers_workstation.sh" "$@"
