process ANARCII {
    tag "${meta.id}"
    label 'process_low'
    container "${params.container_dir}/antibody_tools.sif"

    input:
    tuple val(meta), path(pdb)

    output:
    tuple val(meta), path("*_imgt.pdb"), emit: pdb_imgt
    tuple val(meta), path("*_cdrs.json"), emit: cdrs
    tuple val(meta), path("*_cdr_positions.json"), emit: cdr_positions
    path "anarci.log"

    script:
    """
    (python3 << 'EOF'
import sys
import json
import warnings
from Bio import PDB
from Bio import SeqUtils
from Bio.PDB import PDBIO, Select
try:
    from anarcii import Anarcii
except ImportError:
    try:
        from ANARCII import Anarcii
    except ImportError:
        print("Could not import ANARCII. Ensure it is installed.")
        sys.exit(1)

pdb_file = "${pdb}"
output_pdb = "${meta.id}_imgt.pdb"
output_json = "${meta.id}_cdrs.json"
output_positions_json = "${meta.id}_cdr_positions.json"

# Suppress PDB warnings
warnings.simplefilter("ignore", PDB.PDBExceptions.PDBConstructionWarning)

def get_seq_from_chain(chain):
    seq = ""
    residues = []
    for residue in chain:
        if PDB.is_aa(residue):
            # Use SeqUtils.seq1 for modern Biopython compatibility
            seq += SeqUtils.seq1(residue.get_resname())
            residues.append(residue)
    return seq, residues

parser = PDB.PDBParser()
structure = parser.get_structure("input", pdb_file)

renumbered_structure = PDB.Structure.Structure("imgt")
renumbered_model = PDB.Model.Model(0)
renumbered_structure.add(renumbered_model)

cdr_data = {"cdrs": {}, "numbering": {}, "humanness_score": 0.0}
cdr_positions = {"H1": [], "H2": [], "H3": [], "L1": [], "L2": [], "L3": []}
processed_chains = []

# Instantiate ANARCII runner (default scheme is IMGT)
runner = Anarcii()

# Process each chain
for model in structure:
    for chain in model:
        seq, residues = get_seq_from_chain(chain)
        if not seq: continue
        
        # Run ANARCII
        try:
            # ANARCII number() takes list of sequences
            runner.number([seq])
            # Convert to legacy format: (numbering, alignments, hit_tables)
            results = runner.to_legacy()
            numbering, alignments, hit_tables = results
        except Exception as e:
            print(f"ANARCII failed for chain {chain.id}: {e}")
            continue
            
        if not numbering or not numbering[0]:
            print(f"No ANARCII hit for chain {chain.id}")
            continue
            
        domains = numbering[0]
        if not domains:
            continue
            
        # Take the best domain
        domain = domains[0]
        
        # New ANARCII to_legacy structure is (mapping, start, end)
        # Old ANARCII: ((start, end), (e_value, score), mapping)
        try:
            mapping, start, end = domain
        except ValueError:
            # Fallback if structure changes again
            (start, end), (e_value, score), mapping = domain
            
        # Mapping is list of tuples: ((imgt_num, insert_code), aa)
        
        # Start index in python is 0-based index in `seq` where the domain starts.
        domain_residues = residues[start : end+1]
        
        # Filter mapping to remove gaps
        real_mapping = [m for m in mapping if m[1] != '-']
        
        if len(real_mapping) != len(domain_residues):
            print(f"Warning: Length mismatch for chain {chain.id}. PDB: {len(domain_residues)}, ANARCII: {len(real_mapping)}")
            continue

        # Determine chain type (H or L)
        chain_type = alignments[0][0]['chain_type'] 
        suffix = "H" if chain_type == 'H' else "L"
        new_chain_id = 'H' if chain_type == 'H' else 'L'
        
        if new_chain_id in [c.id for c in renumbered_model]:
             new_chain_id = chain.id 
        
        new_chain = PDB.Chain.Chain(new_chain_id)
        
        cdrs = {"1": "", "2": "", "3": ""}
        
        for i, ((imgt_num, insert_code), aa) in enumerate(real_mapping):
            original_res = domain_residues[i]
            icode = insert_code if insert_code != ' ' else ' '
            new_res_id = (' ', imgt_num, icode)
            
            new_res = PDB.Residue.Residue(new_res_id, original_res.get_resname(), original_res.get_segid())
            
            for atom in original_res:
                new_atom = atom.copy()
                new_res.add(new_atom)
            
            new_chain.add(new_res)
            
            if 27 <= imgt_num <= 38:
                cdrs["1"] += aa
                cdr_positions[f"{suffix}1"].append(original_res.get_id()[1])
            elif 56 <= imgt_num <= 65:
                cdrs["2"] += aa
                cdr_positions[f"{suffix}2"].append(original_res.get_id()[1])
            elif 105 <= imgt_num <= 117:
                cdrs["3"] += aa
                cdr_positions[f"{suffix}3"].append(original_res.get_id()[1])
                
        renumbered_model.add(new_chain)
        processed_chains.append(new_chain_id)
        
        cdr_data["cdrs"][f"{suffix}1"] = cdrs["1"]
        cdr_data["cdrs"][f"{suffix}2"] = cdrs["2"]
        cdr_data["cdrs"][f"{suffix}3"] = cdrs["3"]

io = PDBIO()
io.set_structure(renumbered_structure)
io.save(output_pdb)

with open(output_json, 'w') as f:
    json.dump(cdr_data, f, indent=2)

# Normalize and write loop positions
for key in cdr_positions:
    cdr_positions[key] = sorted(set(cdr_positions[key]))
with open(output_positions_json, 'w') as f:
    json.dump(cdr_positions, f, indent=2)

print(f"Renumbered chains: {processed_chains}")
EOF
    ) > anarci.log 2>&1
    """
}
