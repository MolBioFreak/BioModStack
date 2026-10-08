import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("prepare_protenix_constraints.py")
SCRIPT_DIR = MODULE_PATH.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("prepare_protenix_constraints_module", MODULE_PATH)
prepare_protenix_constraints = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(prepare_protenix_constraints)


def test_auto_pocket_inference_injects_constraint(tmp_path: Path) -> None:
    input_payload = [
        {
            "name": "task1",
            "sequences": [
                {"proteinChain": {"id": ["E"], "sequence": "BINDERSEQ", "count": 1}},
                {"proteinChain": {"id": ["A"], "sequence": "TARGETSEQ", "count": 1}},
                {"ion": {"id": ["B"], "ion": "ZN", "count": 3}},
            ],
        }
    ]
    input_json = tmp_path / "input.json"
    input_json.write_text(json.dumps(input_payload), encoding="utf-8")
    output_json = tmp_path / "output.json"

    target_pdb = tmp_path / "target.pdb"
    target_pdb.write_text(
        "\n".join(
            [
                "ATOM      1  CA  ALA A  10       0.000   0.000   0.000  1.00 10.00           C",
                "ATOM      2  CB  ALA A  10       0.600   0.000   0.000  1.00 10.00           C",
                "ATOM      3  CA  ALA A  20      20.000   0.000   0.000  1.00 10.00           C",
                "ATOM      4  CB  ALA A  20      20.600   0.000   0.000  1.00 10.00           C",
                "HETATM    5 ZN    ZN A 301       1.800   0.000   0.000  1.00 10.00          ZN",
                "END",
                "",
            ]
        ),
        encoding="utf-8",
    )

    argv = sys.argv
    try:
        sys.argv = [
            str(MODULE_PATH),
            "--input_json",
            str(input_json),
            "--output_json",
            str(output_json),
            "--binder_chains",
            "E",
            "--predicted_target_chains",
            "A",
            "--target_pdb",
            str(target_pdb),
            "--source_target_chains",
            "A",
            "--auto-pocket-if-missing",
            "--auto-pocket-max-residues",
            "4",
            "--pocket-max-distance",
            "7.5",
        ]
        prepare_protenix_constraints.main()
    finally:
        sys.argv = argv

    output_payload = json.loads(output_json.read_text(encoding="utf-8"))
    constraint = output_payload[0]["constraint"]["pocket"]

    assert constraint["binder_chain"] == {"entity": 1, "copy": 1}
    assert constraint["max_distance"] == 7.5
    assert constraint["contact_residues"]
    assert constraint["contact_residues"][0]["entity"] == 2
    assert constraint["contact_residues"][0]["position"] == 1
    assert max(item["position"] for item in constraint["contact_residues"]) <= 2
