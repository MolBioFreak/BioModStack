process RFANTIBODY {
    tag "${meta.id}"
    label 'process_gpu'
    container 'apptainer/rfantibody.sif'

    input:
    tuple val(meta), path(target_pdb), val(antigen_chains)

    output:
    tuple val(meta), path("output/*.pdb"), emit: designs
    path "rfantibody.log"

    script:
    """
    python3 -c '
import sys
import warnings
from Bio import PDB
from Bio.PDB import PDBIO

pdb_file = "${target_pdb}"
output_file = "input.pdb"
target_chain_id = "${antigen_chains}"
# Handle potential quotes or empty
if not target_chain_id or target_chain_id == "null": target_chain_id = "A"

warnings.simplefilter("ignore", PDB.PDBExceptions.PDBConstructionWarning)

parser = PDB.PDBParser()
structure = parser.get_structure("input", pdb_file)
model = structure[0]

chains = list(model.get_chains())
if len(chains) < 1:
    print("Error: Input PDB is empty")
    sys.exit(1)

final_structure = PDB.Structure.Structure("HLT")
final_model = PDB.Model.Model(0)
final_structure.add(final_model)

h_chain = None
l_chain = None
target_chains = []

# Heuristic: 
# Look for chains with IDs H and L.
# Any other chains are target.

for c in chains:
    if c.id == "H": h_chain = c
    elif c.id == "L": l_chain = c
    else: target_chains.append(c)

# If no explicit H/L, and multiple chains, try to assign by length or order?
# If we have < 2 chains and no H/L, we verify if it is De Novo (only Antigen).

if not h_chain and not l_chain:
    # No H/L found.
    # If 1 chain, assume it is Antigen (De Novo mode).
    if len(chains) == 1:
        target_chains = [chains[0]]
    # If 2 chains, assume H and L?
    elif len(chains) == 2:
        h_chain = chains[0]
        l_chain = chains[1]
        target_chains = []
    # If > 2 chains, assume first 2 are H/L? Or all are Antigen?
    # This is ambiguous.
    # For safe default: If > 2 chains, assume first 2 are H/L.
    elif len(chains) > 2:
        h_chain = chains[0]
        l_chain = chains[1]
        target_chains = chains[2:]

# Build final model
if h_chain:
    new_h = h_chain.copy()
    new_h.id = "H"
    final_model.add(new_h)

if l_chain:
    new_l = l_chain.copy()
    new_l.id = "L"
    final_model.add(new_l)

# Add targets
# Rename first target to user-specified ID (default A), others B, C... 
for i, t in enumerate(target_chains):
    new_t = t.copy()
    if i == 0:
        new_t.id = target_chain_id
        final_model.add(new_t)
    else:
        # warn or adding as B?
        new_t.id = chr(ord('A') + i + 1) # B, C...
        if new_t.id in ['H', 'L']: new_t.id = "X" # fallback
        final_model.add(new_t)

io = PDBIO()
io.set_structure(final_structure)
io.save(output_file)
    '

    # Run RFantibody
    # Assuming standard hydra logic
    # We pass the reformatted PDB
    
    mkdir -p output
    
    # Try multiple common entry points
    if [ -f "/app/RFantibody/run_inference.py" ]; then
        python3 /app/RFantibody/run_inference.py \
            inference.input_pdb=input.pdb \
            inference.output_prefix=output/${meta.id} \
            inference.num_designs=1
    elif [ -f "/app/RFantibody/rfantibody/inference.py" ]; then
         python3 /app/RFantibody/rfantibody/inference.py \
            input_pdb=input.pdb \
            output_prefix=output/${meta.id}
    else
        echo "RFantibody entry point not found"
        # Fallback to module execution if installed
        python3 -m rfantibody.inference \
            input_pdb=input.pdb \
            output_prefix=output/${meta.id} || true
    fi
    """
}
