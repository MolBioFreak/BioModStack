
import sys
import subprocess
from pathlib import Path

def test_prep_unidock():
    script_path = Path("/home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/scripts/prep_unidock.py")
    input_pdb = Path("/home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform/benchmarkdata/1www_trka.pdb")
    out_dir = Path("test_prep_output")
    
    # Ensure input exists
    if not input_pdb.exists():
        print(f"Skipping test: Input PDB {input_pdb} not found")
        return

    # Command imitating the backend call with ONLY ligand_smiles (simulating dATP selection)
    cmd = [
        sys.executable,
        str(script_path),
        "--input_pdb", str(input_pdb),
        "--ligand_smiles", "Nc1ncnc2c1ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3", # dATP
        "--box_size", "25",
        "--out_dir", str(out_dir)
    ]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print("SUCCESS: prep_unidock.py ran successfully without ntp_type")
        print("Output files:")
        for f in out_dir.glob("*"):
            print(f"  - {f.name}")
    else:
        print("FAILURE: prep_unidock.py failed")
        print("Stderr:", result.stderr)
        sys.exit(1)

if __name__ == "__main__":
    test_prep_unidock()
