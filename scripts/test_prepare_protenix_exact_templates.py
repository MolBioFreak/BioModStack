import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("prepare_protenix_exact_templates.py")
SPEC = importlib.util.spec_from_file_location("prepare_protenix_exact_templates_module", MODULE_PATH)
prepare_protenix_exact_templates = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(prepare_protenix_exact_templates)


def test_rewrites_target_chain_to_exact_template(tmp_path: Path) -> None:
    input_payload = [
        {
            "name": "task1",
            "sequences": [
                {"proteinChain": {"sequence": "TARGETSEQ", "count": 1}},
                {"proteinChain": {"sequence": "BINDERSEQ", "count": 1}},
            ],
        }
    ]
    input_json = tmp_path / "input.json"
    input_json.write_text(json.dumps(input_payload), encoding="utf-8")
    output_json = tmp_path / "output.json"

    prepare_protenix_exact_templates.main = prepare_protenix_exact_templates.main
    # Directly exercise the helpers via a minimal argv patch.
    import sys

    argv = sys.argv
    try:
        sys.argv = [
            str(MODULE_PATH),
            "--input_json",
            str(input_json),
            "--output_json",
            str(output_json),
            "--target_sequence",
            "TARGETSEQ",
            "--template_pdb_id",
            "2lgv",
            "--template_chains",
            "A",
            "--out_dir",
            str(tmp_path / "exact"),
        ]
        prepare_protenix_exact_templates.main()
    finally:
        sys.argv = argv

    output_payload = json.loads(output_json.read_text(encoding="utf-8"))
    target_chain = output_payload[0]["sequences"][0]["proteinChain"]
    binder_chain = output_payload[0]["sequences"][1]["proteinChain"]

    exact_path = Path(target_chain["templatesPath"])
    assert exact_path.exists()
    assert (
        exact_path.read_text(encoding="utf-8")
        == ">2lgv_A/1-9 mol:protein length:9 exact_target_template\nTARGETSEQ\n"
    )
    assert "templatesPath" not in binder_chain
