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
    def chainTimeout = params.anarcii_chain_timeout ?: 120
    """
    (python3 << 'EOF'
import sys
import json
import subprocess
import warnings
from pathlib import Path
from Bio import PDB
from Bio import SeqUtils
from Bio.PDB import PDBIO
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
chain_timeout = int("${chainTimeout}")

# Suppress PDB warnings
warnings.simplefilter("ignore", PDB.PDBExceptions.PDBConstructionWarning)

ANARCII_SUBPROCESS = r"""
import json
import sys

try:
    from anarcii import Anarcii
except ImportError:
    from ANARCII import Anarcii

seq = sys.argv[1]
runner = Anarcii(seq_type='unknown')
runner.number([seq])
numbering, alignments, hit_tables = runner.to_legacy()
print(json.dumps({
    "numbering": numbering,
    "alignments": alignments,
    "hit_tables": hit_tables,
}))
"""

def get_seq_from_chain(chain):
    seq = ""
    residues = []
    for residue in chain:
        if PDB.is_aa(residue):
            # Use SeqUtils.seq1 for modern Biopython compatibility
            seq += SeqUtils.seq1(residue.get_resname())
            residues.append(residue)
    return seq, residues

def run_anarcii(seq, chain_id):
    try:
        result = subprocess.run(
            [sys.executable, "-c", ANARCII_SUBPROCESS, seq],
            capture_output=True,
            text=True,
            timeout=chain_timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"ANARCII timed out for chain {chain_id} after {chain_timeout}s")
        return None
    except Exception as exc:
        print(f"ANARCII subprocess failed for chain {chain_id}: {exc}")
        return None

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        print(f"ANARCII failed for chain {chain_id}: {stderr[:500]}")
        return None

    stdout = (result.stdout or "").strip()
    if not stdout:
        print(f"ANARCII produced no output for chain {chain_id}")
        return None

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        print(f"ANARCII returned invalid JSON for chain {chain_id}: {exc}")
        return None

    return (
        payload.get("numbering") or [],
        payload.get("alignments") or [],
        payload.get("hit_tables") or [],
    )

parser = PDB.PDBParser()
structure = parser.get_structure("input", pdb_file)

renumbered_structure = PDB.Structure.Structure("imgt")
renumbered_model = PDB.Model.Model(0)
renumbered_structure.add(renumbered_model)

cdr_data = {"cdrs": {}, "numbering": {}, "humanness_score": 0.0}
cdr_positions = {"H1": [], "H2": [], "H3": [], "L1": [], "L2": [], "L3": []}
processed_chains = []
chain_failures = []

# Process each chain
for model in structure:
    for chain in model:
        seq, residues = get_seq_from_chain(chain)
        if not seq: continue
        
        # Run ANARCII
        try:
            results = run_anarcii(seq, chain.id)
            if results is None:
                chain_failures.append(chain.id)
                continue
            numbering, alignments, hit_tables = results
        except Exception as e:
            print(f"ANARCII failed for chain {chain.id}: {e}")
            chain_failures.append(chain.id)
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
if processed_chains:
    io.set_structure(renumbered_structure)
    io.save(output_pdb)
else:
    Path(output_pdb).write_text(Path(pdb_file).read_text())
    print("No chains were renumbered; copied input PDB to fallback output.")

with open(output_json, 'w') as f:
    json.dump(cdr_data, f, indent=2)

# Normalize and write loop positions
for key in cdr_positions:
    cdr_positions[key] = sorted(set(cdr_positions[key]))
with open(output_positions_json, 'w') as f:
    json.dump(cdr_positions, f, indent=2)

print(f"Renumbered chains: {processed_chains}")
if chain_failures:
    print(f"Chains skipped after ANARCII failure/timeout: {chain_failures}")
EOF
    ) > anarci.log 2>&1
    """
}
