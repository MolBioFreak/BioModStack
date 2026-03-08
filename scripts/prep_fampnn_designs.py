import pyrosetta
import argparse
from pathlib import Path


def read_original_chain_ids(pdb_path):
    """
    Read the ordered list of unique chain IDs from a PDB file,
    preserving the order they first appear in the ATOM records.

    Returns a list like ['H', 'L', 'T'] for an RFdiffusion HLT output.
    """
    chains = []
    seen = set()
    with open(pdb_path, 'r') as f:
        for line in f:
            if line.startswith('ATOM') and len(line) >= 22:
                chain_id = line[21]
                if chain_id not in seen:
                    chains.append(chain_id)
                    seen.add(chain_id)
    return chains


def restore_chain_ids(pose, original_chains):
    """
    Re-apply original chain IDs to a PyRosetta pose.

    PyRosetta's pose_from_pdb() + dump_pdb() silently relabels chains
    to A, B, C, ... which breaks downstream tools (FAMPNN constraints,
    ANARCI) that reference the original chain IDs (e.g., H, L, T).
    """
    if not original_chains:
        return

    info = pose.pdb_info()
    # Build mapping: PyRosetta chain number (1-indexed) → original chain ID
    # PyRosetta assigns chains as 1, 2, 3, ... in order of appearance
    chain_map = {}
    for i in range(1, pose.total_residue() + 1):
        pyrosetta_chain_num = pose.chain(i)
        if pyrosetta_chain_num not in chain_map:
            chain_idx = len(chain_map)
            if chain_idx < len(original_chains):
                chain_map[pyrosetta_chain_num] = original_chains[chain_idx]
            else:
                # More chains in pose than in original PDB — keep PyRosetta's
                chain_map[pyrosetta_chain_num] = info.chain(i)

    # Apply the mapping
    for i in range(1, pose.total_residue() + 1):
        pyrosetta_chain_num = pose.chain(i)
        if pyrosetta_chain_num in chain_map:
            info.chain(i, chain_map[pyrosetta_chain_num])

    pose.pdb_info(info)


def main():
    parser = argparse.ArgumentParser(description='Restores side-chains to PDB files after RFdiffusion processing')
    parser.add_argument('--input_dir', required=True, help='Input directory containing PDB files')
    parser.add_argument('--out_dir', default='./outputpdbs', help='Output directory for updated PDB files')
    args = parser.parse_args()
    
    pyrosetta.init("-out:levels all:error -ignore_unrecognized_res 1")
        
    # Make list of input PDBs
    input_dir = Path(args.input_dir)
    pdb_files = list(input_dir.glob("*.pdb"))

    # Create the output directory
    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)

    for pdb_file in pdb_files:
        # Read original chain IDs BEFORE PyRosetta loads (and relabels) the PDB
        original_chains = read_original_chain_ids(str(pdb_file))

        # Import design. PyRosetta will automatically restore missing side-chains
        pose_design = pyrosetta.pose_from_pdb(str(pdb_file))

        # Restore original chain IDs (H/L/T) that PyRosetta replaced with A/B/C
        restore_chain_ids(pose_design, original_chains)

        # Output designs with preserved chain labels
        output_path = out_dir / pdb_file.name
        print(f"Outputting PDB file: {output_path} (chains: {original_chains})")
        pose_design.dump_pdb(str(output_path))


if __name__ == "__main__":
    main()
