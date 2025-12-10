#!/bin/bash
set -e

# Verification script for BoltzGen and DiffDock integration
# Usage: ./verify_new_tools.sh

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Starting Verification of New Tools${NC}"

# Check for containers
CONTAINER_DIR="../containers"
# If local containers dir not standard, check nextflow config logic (usually ./containers or params.container_dir)
# Assuming user built them in ./apptainer or moved them. 
# We'll rely on Nextflow finding them via config params or defaults.

# 1. Test BoltzGen Standalone
echo -e "\n${GREEN}[TEST 1] BoltzGen Standalone Mode${NC}"
echo "Generating 1 design for dATP binder..."

rm -rf test_boltzgen_verify

nextflow run main.nf \
    -profile workstation_ryzen7960x,test,monomer_denovo \
    --diffusion_method boltzgen \
    --run_boltzgen_only true \
    --boltzgen_ntp_type dATP \
    --boltzgen_num_designs 1 \
    --out_dir test_boltzgen_verify \
    --container_dir ./containers \
    -resume

# Check output
if ls test_boltzgen_verify/results/*.pdb 1> /dev/null 2>&1; then
    echo -e "${GREEN}✓ BoltzGen output found.${NC}"
else
    echo -e "${RED}✗ BoltzGen output NOT found.${NC}"
fi

# 2. Test DiffDock Integration
echo -e "\n${GREEN}[TEST 2] DiffDock Integration (Stage 4)${NC}"
echo "Docking Ethanol (CCO) to benchmark PDB..."

rm -rf test_diffdock_verify

# Use a specific input PDB from benchmarkdata if available
INPUT_PDB="benchmarkdata/5o45_pd-l1.pdb"
if [ ! -f "$INPUT_PDB" ]; then
    echo "Warning: $INPUT_PDB not found, trying minimal run without specific input (might fail if no structural inputs)"
fi

nextflow run main.nf \
    -profile workstation_ryzen7960x,test,monomer_denovo \
    --run_docking true \
    --params.run_rfd_only false \
    --skip_rfd_seq_pred true \
    --analysis_input_pdbs "$INPUT_PDB" \
    --diffdock_ligand_smiles "CCO" \
    --diffdock_num_poses 1 \
    --out_dir test_diffdock_verify \
    --container_dir ./containers \
    -resume

# Check output
if ls test_diffdock_verify/run/diffdock/results/*/*.pdb 1> /dev/null 2>&1; then
    echo -e "${GREEN}✓ DiffDock output found.${NC}"
else
    echo -e "${RED}✗ DiffDock output NOT found (check logs in test_diffdock_verify/run/diffdock/).${NC}"
fi

echo -e "\n${GREEN}Verification Complete${NC}"
