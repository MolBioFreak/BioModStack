import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "prepare_protein_local_validator_input.py"
spec = importlib.util.spec_from_file_location("prepare_protein_local_validator_input", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def atom_line(serial: int, residue: str, chain: str, residue_number: int) -> str:
    return (
        f"ATOM  {serial:5d}  CA  {residue:>3s} {chain:1s}{residue_number:4d}    "
        "   0.000   0.000   0.000  1.00 20.00           C  \n"
    )


def test_builds_multichain_protenix_contract_with_source_identity(tmp_path: Path):
    pdb_path = tmp_path / "candidate one.pdb"
    pdb_path.write_text(
        atom_line(1, "ALA", "A", 1)
        + atom_line(2, "GLY", "A", 2)
        + atom_line(3, "SER", "B", 1)
        + "END\n",
        encoding="utf-8",
    )

    contract = module.build_contract(pdb_path, model_seeds=[7, 11])

    assert contract["candidate_id"] == "candidate_one"
    assert len(contract["source_sha256"]) == 64
    assert contract["components"] == [
        {"chain_id": "A", "molecule_type": "protein", "sequence": "AG"},
        {"chain_id": "B", "molecule_type": "protein", "sequence": "S"},
    ]
    payload = contract["protenix_input"]
    assert payload[0]["name"] == "candidate_one"
    assert payload[0]["modelSeeds"] == [7, 11]
    assert [entry["proteinChain"]["sequence"] for entry in payload[0]["sequences"]] == ["AG", "S"]


def test_rejects_pdb_without_supported_polymer_atoms(tmp_path: Path):
    pdb_path = tmp_path / "empty.pdb"
    pdb_path.write_text("END\n", encoding="utf-8")

    try:
        module.build_contract(pdb_path, model_seeds=[42])
    except ValueError as exc:
        assert "protein residues" in str(exc)
    else:
        raise AssertionError("empty PDB must fail closed")
