process ANARCI {
    tag "${meta.id}"
    label 'process_low'
    container 'apptainer/antibody_tools.sif'

    input:
    tuple val(meta), path(pdb)

    output:
    tuple val(meta), path("*_imgt.pdb"), emit: pdb_imgt
    tuple val(meta), path("*_cdrs.json"), emit: cdrs
    path "anarci.log"

    script:
    """
    python3 << 'EOF'
import sys
import json
import warnings
from Bio import PDB
from Bio.PDB import PDBIO, Select
from anarci import anarci

pdb_file = "${pdb}"
output_pdb = "${meta.id}_imgt.pdb"
output_json = "${meta.id}_cdrs.json"

# Suppress PDB warnings
warnings.simplefilter("ignore", PDB.PDBExceptions.PDBConstructionWarning)

def get_seq_from_chain(chain):
    seq = ""
    residues = []
    for residue in chain:
        if PDB.is_aa(residue):
            seq += PDB.Polypeptide.three_to_one(residue.get_resname())
            residues.append(residue)
    return seq, residues

parser = PDB.PDBParser()
structure = parser.get_structure("input", pdb_file)

renumbered_structure = PDB.Structure.Structure("imgt")
renumbered_model = PDB.Model.Model(0)
renumbered_structure.add(renumbered_model)

cdr_data = {"cdrs": {}, "numbering": {}, "humanness_score": 0.0}
processed_chains = []

# Process each chain
for model in structure:
    for chain in model:
        seq, residues = get_seq_from_chain(chain)
        if not seq: continue
        
        # Run ANARCI
        try:
            results = anarci([("seq", seq)], scheme="imgt", output=False)
            numbering, alignments, hit_tables = results
        except Exception as e:
            print(f"ANARCI failed for chain {chain.id}: {e}")
            continue
            
        if not numbering or not numbering[0]:
            print(f"No ANARCI hit for chain {chain.id}")
            continue
            
        domains = numbering[0]
        if not domains:
            continue
            
        # Take the best domain
        domain = domains[0]
        (start, end), (e_value, score), mapping = domain
        
        # Mapping is list of tuples: ((imgt_num, insert_code), aa)
        
        # Start index in python is 0-based index in `seq` where the domain starts.
        domain_residues = residues[start : end+1]
        
        # Filter mapping to remove gaps
        real_mapping = [m for m in mapping if m[1] != '-']
        
        if len(real_mapping) != len(domain_residues):
            print(f"Warning: Length mismatch for chain {chain.id}. PDB: {len(domain_residues)}, ANARCI: {len(real_mapping)}")
            continue

        # Determine chain type (H or L)
        chain_type = alignments[0][0]['chain_type'] 
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
            elif 56 <= imgt_num <= 65:
                cdrs["2"] += aa
            elif 105 <= imgt_num <= 117:
                cdrs["3"] += aa
                
        renumbered_model.add(new_chain)
        processed_chains.append(new_chain_id)
        
        suffix = "H" if chain_type == 'H' else "L"
        cdr_data["cdrs"][f"{suffix}1"] = cdrs["1"]
        cdr_data["cdrs"][f"{suffix}2"] = cdrs["2"]
        cdr_data["cdrs"][f"{suffix}3"] = cdrs["3"]

io = PDBIO()
io.set_structure(renumbered_structure)
io.save(output_pdb)

with open(output_json, 'w') as f:
    json.dump(cdr_data, f, indent=2)

print(f"Renumbered chains: {processed_chains}")
EOF
    """
}
