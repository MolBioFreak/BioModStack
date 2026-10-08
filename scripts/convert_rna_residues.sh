#!/bin/bash
# convert_rna_residues.sh
# Pre-rebuild: per-chain residue name conversion for correct sugar chemistry
# NA-MPNN always outputs DG/DC/DT/DA (DNA naming). For RNA chains we convert:
#   DG -> G, DC -> C, DT -> U, DA -> A (ribose, with 2'-OH)
# For DNA chains: keep DG/DC/DT/DA as-is (deoxyribose, no 2'-OH)
# For protein chains: no conversion needed.

# Usage: convert_rna_residues.sh <polymer_chains_csv> <exclude_filename>
# Example: convert_rna_residues.sh "dna,rna,protein" "rebuild_script.py"

POLYMER_CHAINS="${1}"
EXCLUDE_FILE="${2:-}"

CHAIN_LETTERS=(A B C D E F G H I J K L M N O P Q R S T U V W X Y Z)
IFS=',' read -ra POLYMER_TYPES <<< "${POLYMER_CHAINS}"

for pdb in *.pdb; do
    if [ -f "$pdb" ] && [ "$pdb" != "$EXCLUDE_FILE" ]; then
        for i in "${!POLYMER_TYPES[@]}"; do
            PTYPE="$(echo ${POLYMER_TYPES[$i]} | tr '[:upper:]' '[:lower:]' | xargs)"
            CHAIN="${CHAIN_LETTERS[$i]}"
            if [ "$PTYPE" = "rna" ]; then
                echo "Chain $CHAIN is RNA — converting DNA residue names to RNA"
                # Only convert lines matching this chain ID (PDB column 22, 1-indexed)
                sed -i "/^\(ATOM\|HETATM\)/ {
                    /^.\{21\}$CHAIN/ {
                        s/ DG / G  /g
                        s/ DC / C  /g
                        s/ DA / A  /g
                        s/ DT / U  /g
                    }
                }" "$pdb"
            else
                echo "Chain $CHAIN is $PTYPE — keeping residue names as-is"
            fi
        done
    fi
done
