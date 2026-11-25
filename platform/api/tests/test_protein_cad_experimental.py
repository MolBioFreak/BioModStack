from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from model_registry import ModelRegistry
from services.nextflow import build_nextflow_command
from template_registry import TemplateRegistry


def test_standalone_protein_cad_experimental_model_and_template_are_removed() -> None:
    assert ModelRegistry().get_model("protein_cad_experimental") is None
    assert TemplateRegistry(API_ROOT / "config" / "templates").get_template("protein_cad_experimental") is None


def test_build_nextflow_command_maps_denovo_generation_params() -> None:
    cmd = build_nextflow_command(
        "protein_modification_experimental",
        "de_novo_design",
        {
            "generator": "disco",
            "backend": "disco",
            "design_task": "ligand_conditioned",
            "num_designs": 24,
            "target_lengths": "150,200,250",
            "disco_experiment": "diverse",
            "disco_effort": "max",
            "disco_ligand_sdf": "/tmp/heme_b.sdf",
            "disco_ligand_name": "heme_b",
        },
        "/tmp/out",
        job_id="job-cad-1",
    )

    joined = " ".join(cmd)

    assert cmd[1:4] == ["run", "workflows/denovo_generation.nf", "-profile"]
    assert cmd[4] == "workstation_ryzen7960x"
    assert "--pcad_backend disco" in joined
    assert "--pcad_task ligand_conditioned" in joined
    assert "--pcad_num_designs 24" in joined
    assert "--pcad_target_lengths 150,200,250" in joined
    assert "--pcad_disco_experiment diverse" in joined
    assert "--pcad_disco_effort max" in joined
    assert "--pcad_disco_ligand_sdf /tmp/heme_b.sdf" in joined
    assert "--pcad_disco_ligand_name heme_b" in joined
    assert "--rfd_num_designs 24" in joined
    assert "--backend disco" not in joined
    assert "--design_task ligand_conditioned" not in joined


def test_prep_denovo_generation_request_compiles_disco_dna_job(tmp_path: Path) -> None:
    output_path = tmp_path / "protein_cad_request.json"
    input_dir = tmp_path / "inputs"

    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "prep_denovo_generation_request.py"),
            "--job-id",
            "job-cad-2",
            "--job-name",
            "disco_dna",
            "--backend",
            "disco",
            "--task",
            "dna_conditioned",
            "--num-designs",
            "4",
            "--target-lengths",
            "50,60",
            "--disco-na-sequence",
            "TTTGCACCAA",
            "--output",
            str(output_path),
            "--input-dir",
            str(input_dir),
        ],
        check=True,
    )

    request = json.loads(output_path.read_text(encoding="utf-8"))
    compiled_input = Path(request["disco"]["compiled_input_json"])
    payload = json.loads(compiled_input.read_text(encoding="utf-8"))

    assert request["backend"] == "disco"
    assert request["disco"]["effort"] == "fast"
    assert len(payload) == 2
    assert payload[0]["name"] == "length_50_dna"
    sequences = payload[0]["sequences"]
    assert sequences[0]["proteinChain"]["sequence"] == "-" * 50
    assert sequences[1]["dnaSequence"]["sequence"] == "TTTGCACCAA"
    assert sequences[2]["dnaSequence"]["sequence"] == "TTGGTGCAAA"
