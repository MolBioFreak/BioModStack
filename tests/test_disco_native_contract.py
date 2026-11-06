"""Pinned DISCO writer grammar/transport tests; no model/GPU execution."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from lib.disco_contract import build_native_jobs, native_overrides, parse_native_records

spec = importlib.util.spec_from_file_location("run_disco", SCRIPTS / "run_disco_inference.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_actual_plain_writer_has_no_newline_between_records():
    # runner/inference.py:549 and runner/utils.py:111-112 at 82b594f:
    # f.write(header); f.writelines(aa_seq) -- NOT ordinary FASTA wrapping.
    text = ">cogen_seq 0\nACDE>cogen_seq 1\nFGHI"
    records = parse_native_records(text)
    assert [(r["sample_id"], r["protein_sequence"]) for r in records] == [(0, "ACDE"), (1, "FGHI")]
    assert "".join(r["raw_text"] for r in records) == text
    assert parse_native_records("ACDE")[0]["protein_sequence"] == "ACDE"


def test_actual_biomolecular_grammar_does_not_contaminate_protein():
    text = (">cogen_seq 0\nCHAIN A type=protein mode=fixed seq=ACDE\n"
            "CHAIN B type=dna mode=fixed seq=AGTC\nCHAIN C type=rna mode=fixed seq=AGUC\n"
            "\nligand_smiles CCD_ATP\nligand_smiles FILE_ligand.sdf\n")
    record, = parse_native_records(text)
    assert record["protein_sequence"] == "ACDE"
    assert [c["chain_id"] for c in record["chains"]] == ["A", "B", "C"]
    assert record["ligand_descriptors"] == ["CCD_ATP", "FILE_ligand.sdf"]
    assert record["raw_text"] == text
    ligand, = parse_native_records(">cogen_seq 0\nACDE\nligand_smiles CCO\n")
    assert ligand["protein_sequence"] == "ACDE"
    assert ligand["ligand_descriptors"] == ["CCO"]
    unknown, = parse_native_records(">cogen_seq 0\nnot a protein 123\n")
    assert unknown["protein_sequence"] is None
    assert unknown["unparsed_lines"] == ["not a protein 123"]


def test_native_settings_preserve_explicit_fast_false_zero_and_precedence():
    settings = dict(experiment="designable", effort="fast", seeds=0,
                    num_inference_seeds=8, use_deepspeed_evo_attention=False,
                    N_step=120, N_cycle=3, noisy_guidance={"enabled": False, "omega_seq": 0.0},
                    sequence_sampling={"logits_temp": 0.0, "power": 1.5}, output_format=None)
    actual = native_overrides(settings)
    assert actual[:3] == ["experiment=designable", "effort=fast", "use_deepspeed_evo_attention=false"]
    assert "seeds=0" in actual and "num_inference_seeds=null" in actual
    assert "++sample_diffusion.N_step=120" in actual
    assert "++model.N_cycle=3" in actual
    assert "++sample_diffusion.noisy_guidance.omega_seq=0.0" in actual
    assert "++sequence_sampling_strategy.sequence_noise_scheduler.power=1.5" in actual
    assert "++output_format=null" in actual
    assert "num_inference_seeds=0" in native_overrides({"num_inference_seeds": 0})
    assert "num_inference_seeds=8" in native_overrides({"seeds": []})
    assert "seeds=[0]" in native_overrides({"seeds": [0]})
    assert "effort=max" in native_overrides({"effort": "max"})


def test_typed_entities_preserve_mask_strands_copies_chemistry_bond_endpoints():
    jobs = [{"name": "authored", "sequences": [
        {"proteinChain": {"sequence": "AC--EF", "count": 1}},
        {"dnaSequence": {"sequence": "AGTC", "count": 2}},
        {"rnaSequence": {"sequence": "AGUC", "count": 1}},
        {"ligand": {"ligand": "CCD_ATP", "count": 2}},
        {"ligand": {"ligand": "CCO", "count": 1}},
        {"ligand": {"ligand": "FILE_lig.sdf", "count": 1}},
        {"ion": {"ion": "MG", "count": 2}}],
        "covalent_bonds": [{"left_entity": 1, "left_copy": 1, "left_position": 2,
                            "left_atom": "SG", "right_entity": 4, "right_copy": 2,
                            "right_position": 1, "right_atom": "C1"}]}]
    actual = build_native_jobs(jobs)
    assert actual == jobs and actual is not jobs
    assert len(actual[0]["sequences"]) == 7  # no implicit complementary strand


def test_publication_exact_sample_missingness_and_partial_native_failure(tmp_path):
    native = tmp_path / "native/input_0/seed_0"
    native.mkdir(parents=True)
    p1, p10 = native / "name_sample_1.pdb", native / "name_sample_10.pdb"
    p1.write_text("native sample 1 bytes")
    p10.write_text("native sample 10 bytes")
    seq = native / "name_sample_0.txt"
    seq.write_text(">cogen_seq 10\nACDE\nligand_smiles CCO\n")
    confidence = native / "name_summary_confidence_sample_10.json"
    confidence.write_text(json.dumps({"ptm": 0.4, "design_id": "must-not-overwrite"}))
    event = dict(input_index=0, input_name="name", seed=0, sequence_path=str(seq),
                 structures=[dict(sample_id=i, path=str(p)) for i, p in [(1, p1), (10, p10)]],
                 confidence=[dict(sample_id=10, path=str(confidence), role="summary_confidence")])
    (tmp_path / "native_events.jsonl").write_text(json.dumps(event) + "\n")
    err = tmp_path / "disco_output/ERR/name.txt"
    err.parent.mkdir(parents=True)
    err.write_text("native partial failure")
    manifest = runner.publish_outputs(tmp_path, {"task": "ligand_conditioned"}, 7)
    assert len(manifest) == 2
    missing, present = manifest
    assert missing["sequence"] is None
    assert missing["missing"] == ["sequence_record", "summary_confidence"]
    assert present["sequence"] == "ACDE"
    metadata = json.loads((tmp_path / present["metadata_path"]).read_text())
    assert metadata["ptm"] == 0.4
    assert metadata["design_id"] != "must-not-overwrite"
    assert metadata["native_record"]["ligand_descriptors"] == ["CCO"]
    assert (tmp_path / missing["structure_path"]).read_text() == p1.read_text()
    publication = json.loads((tmp_path / "disco_publication.json").read_text())
    assert publication["native_returncode"] == 7 and publication["execution_failed"]
    assert publication["native_errors_present"]


def test_sequence_only_records_do_not_inherit_first_structure(tmp_path):
    seq = tmp_path / "seq.txt"
    seq.write_text(">cogen_seq 0\nACDE>cogen_seq 1\nFGHI")
    pdb = tmp_path / "native.pdb"
    pdb.write_text("first structure only")
    event = dict(input_index=3, input_name="prefix_sample_10", seed=10, sequence_path=str(seq),
                 structures=[dict(sample_id=0, path=str(pdb))], confidence=[])
    (tmp_path / "native_events.jsonl").write_text(json.dumps(event) + "\n")
    manifest = runner.publish_outputs(tmp_path, {"task": "unconditional"}, 0)
    assert [m["sequence"] for m in manifest] == ["ACDE", "FGHI"]
    assert manifest[1]["structure_path"] is None
    assert manifest[1]["native_identity"] == dict(input_index=3, input_name="prefix_sample_10", seed=10, sample_id=1)


def test_wrapper_publishes_before_propagating_failed_native_exit(tmp_path, monkeypatch):
    source = tmp_path / "input.json"
    source.write_text(json.dumps([{"name": "example", "sequences": [
        {"proteinChain": {"sequence": "AC--", "count": 1}}]}]))
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"task": "unconditional", "job_name": "fixture",
        "disco": {"input_json_path": str(source), "seeds": [0], "effort": "fast"}}))
    output = tmp_path / "out"

    def failed_native(cmd, **kwargs):
        assert "seeds=[0]" in cmd and "effort=fast" in cmd
        assert kwargs["check"] is False
        sequence = output / "sequence.txt"
        sequence.write_text(">cogen_seq 0\nACDE")
        event = dict(input_index=0, input_name="example", seed=0,
                     sequence_path=str(sequence), structures=[], confidence=[])
        (output / "native_events.jsonl").write_text(json.dumps(event) + "\n")
        (output / "disco_effective_config.json").write_text(json.dumps({"seeds": [0, 10]}))
        return runner.subprocess.CompletedProcess(cmd, 7)

    monkeypatch.setattr(runner.subprocess, "run", failed_native)
    monkeypatch.setattr(sys, "argv", ["runner", "--request", str(request),
                        "--input-dir", str(tmp_path), "--output-dir", str(output)])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 7
    manifest = json.loads((output / "design_manifest.json").read_text())
    assert manifest[0]["sequence"] == "ACDE"
    assert manifest[0]["structure_path"] is None
    publication = json.loads((output / "disco_publication.json").read_text())
    assert publication["requested_input_seed_count"] == 2
    assert [r["emitted_samples"] for r in publication["input_seed_accounting"]] == [1, 0]
    assert publication["native_returncode"] == 7
