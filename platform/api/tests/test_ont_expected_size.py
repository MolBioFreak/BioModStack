"""Requested Auto and effective execution sizes share the ONT reference parse."""
import hashlib
import json
from pathlib import Path

import pytest

from services.ont_ngs_contract import (
    effective_expected_plasmid_size,
    normalize_ont_launch_params,
    normalized_fasta_sequence_identity,
    normalized_fasta_sequence_sha256,
    resolve_expected_plasmid_size,
)


@pytest.mark.parametrize("workflow", ["ont_fastq_qc", "ont_plasmid_qc", "ont_construct_screening", "wf_clone_validation"])
@pytest.mark.parametrize("requested,reference_length,expected", [(None, 3000, 3000), (None, 12000, 12000), (7000, 12000, 7000)])
def test_requested_effective_save_and_replay(workflow, requested, reference_length, expected, tmp_path, monkeypatch):
    path = tmp_path / "reference.fasta"
    sequence = "acgt" * (reference_length // 4)
    path.write_text(">record\n" + sequence + "\n")
    opens = []
    original_open = Path.open
    def counted_open(self, *args, **kwargs):
        if self == path:
            opens.append(self)
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", counted_open)
    params = normalize_ont_launch_params(workflow, {"expected_plasmid_size": requested})
    digest, length = normalized_fasta_sequence_identity(path)
    params["reference_sequence_sha256"] = digest
    resolve_expected_plasmid_size(params, length)
    assert opens == [path]  # identity and size share the same parse/hash pass
    assert digest == hashlib.sha256(sequence.upper().encode()).hexdigest()
    assert params["expected_plasmid_size"] == expected
    assert params["requested_expected_plasmid_size"] == requested
    saved = json.loads(json.dumps(params))
    replay = normalize_ont_launch_params(workflow, saved)
    assert replay["expected_plasmid_size"] == expected
    assert replay["requested_expected_plasmid_size"] == requested
    # A different reference must not silently replace a retained execution size.
    assert effective_expected_plasmid_size(replay["expected_plasmid_size"], 4500) == expected


def test_new_omission_is_auto_and_does_not_mutate_caller():
    source = {}
    assert normalize_ont_launch_params("ont_fastq_qc", source)["expected_plasmid_size"] is None
    assert source == {}


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "7000", 100000001])
def test_invalid_size_retains_bounded_integer_contract(value):
    with pytest.raises(ValueError, match="expected_plasmid_size"):
        normalize_ont_launch_params("ont_fastq_qc", {"expected_plasmid_size": value})


@pytest.mark.parametrize("value", [1, 100000000, 7000])
def test_positive_override_boundaries(value):
    assert effective_expected_plasmid_size(value, 3000) == value


def test_existing_fasta_contract_and_digest_wrapper(tmp_path):
    path = tmp_path / "reference.fasta"
    path.write_text(">test\nacgt\nNN\n")
    digest, length = normalized_fasta_sequence_identity(path)
    assert length == 6
    assert normalized_fasta_sequence_sha256(path) == digest
    path.write_text(">one\nACGT\n>two\nACGT\n")
    with pytest.raises(ValueError, match="exactly one"):
        normalized_fasta_sequence_identity(path)


def test_model_and_public_schema_auto_defaults():
    from model_registry import get_registry
    model = get_registry().get_model("nanopore")
    assert model is not None
    field = next(p for p in model.params if p.name == "expected_plasmid_size")
    assert field.default is None and field.minimum == 1 and field.maximum == 100000000
    for mode in model.modes:
        if mode.id in {"fastq_qc", "plasmid_qc", "construct_screening", "clone_validation"}:
            assert "expected_plasmid_size" in mode.params
    root = Path(__file__).resolve().parents[3]
    for mode in ("fastq_qc", "plasmid_qc", "construct_screening", "clone_validation"):
        schema = json.loads((root / f"schemas/ngs_molbio/ngs-ont-{mode}-v1.schema.json").read_text())
        setting = schema["properties"]["expected_plasmid_size"]
        assert setting["default"] is None
        assert set(setting["type"]) == {"integer", "null"}
