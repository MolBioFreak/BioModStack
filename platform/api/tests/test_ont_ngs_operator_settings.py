import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers.ont_runs import OntNgsSubmitRequest, _job_create_for_ont_submit  # noqa: E402
from services.ont_ngs_contract import normalize_ont_launch_params  # noqa: E402


def test_construct_screening_persists_visible_fastq_stage_settings_at_submit_boundary(monkeypatch):
    import routers.ont_runs as ont_runs

    monkeypatch.setattr(ont_runs, "_confine_submitted_path", lambda value, _label, **_kwargs: str(value))
    job = _job_create_for_ont_submit(
        "ont_construct_screening",
        OntNgsSubmitRequest(
            name="construct-visible-settings",
            params={
                "fastq_path": "/inputs/reads.fastq",
                "reference_fasta": "/inputs/reference.fasta",
                "run_fastq_qc": False,
                "run_assembly": True,
            },
        ),
    )

    assert job.mode == "construct_screening"
    assert job.params["run_fastq_qc"] is False
    assert job.params["run_assembly"] is True
    assert "run_multimer_qc" not in job.params


def test_construct_screening_preserves_operator_selected_stage_flags():
    disabled = normalize_ont_launch_params(
        "ont_construct_screening",
        {"run_assembly": False, "run_fastq_qc": False},
    )
    assert disabled["run_assembly"] is False
    assert disabled["run_fastq_qc"] is False
    assert "wf_clone_assembly_tool" not in disabled

    enabled = normalize_ont_launch_params(
        "ont_construct_screening",
        {"run_assembly": True, "run_fastq_qc": True},
    )
    assert enabled["run_assembly"] is True
    assert enabled["run_fastq_qc"] is True
    assert enabled["wf_clone_assembly_tool"] == "flye"


def test_construct_screening_rejects_non_boolean_assembly_and_clone_keeps_required_assembly():
    with pytest.raises(ValueError, match="run_assembly must be boolean"):
        normalize_ont_launch_params("ont_construct_screening", {"run_assembly": "false"})

    clone = normalize_ont_launch_params("wf_clone_validation", {"run_assembly": False})
    assert clone["run_assembly"] is True


def test_construct_screening_assembly_uses_clone_validation_normalization_contract():
    clone_controls = {
        "wf_clone_assembly_tool": "canu",
        "wf_clone_basecaller_model": "dna_r10.4.1_e8.2_400bps_hac@v5.0.0",
        "wf_clone_large_construct": True,
        "wf_clone_approx_size": 12_000,
        "wf_clone_assm_coverage": 75,
        "wf_clone_trim_length": 25,
        "wf_clone_min_quality": 11,
        "wf_clone_flye_quality": "nano-corr",
        "wf_clone_non_uniform_coverage": False,
        "wf_clone_canu_fast": True,
        "wf_clone_cutsite_mismatch": 3,
        "wf_clone_primer_mismatch": 4,
        "wf_clone_expected_coverage": 92.5,
        "wf_clone_expected_identity": 98.25,
    }

    clone = normalize_ont_launch_params("wf_clone_validation", clone_controls)
    construct = normalize_ont_launch_params(
        "ont_construct_screening",
        {"run_assembly": True, **clone_controls},
    )

    default_clone = normalize_ont_launch_params("wf_clone_validation", {})
    default_construct = normalize_ont_launch_params(
        "ont_construct_screening",
        {"run_assembly": True},
    )
    visible_clone_keys = {
        "wf_clone_assembly_tool",
        "wf_clone_basecaller_model",
        "wf_clone_large_construct",
        "wf_clone_approx_size",
        "wf_clone_assm_coverage",
        "wf_clone_trim_length",
        "wf_clone_min_quality",
        "wf_clone_flye_quality",
        "wf_clone_non_uniform_coverage",
        "wf_clone_canu_fast",
        "wf_clone_cutsite_mismatch",
        "wf_clone_primer_mismatch",
        "wf_clone_expected_coverage",
        "wf_clone_expected_identity",
    }

    assert {key: default_construct[key] for key in visible_clone_keys} == {
        key: default_clone[key] for key in visible_clone_keys
    }

    assert {key: construct[key] for key in clone_controls} == {
        key: clone[key] for key in clone_controls
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("wf_clone_large_construct", "true"),
        ("wf_clone_approx_size", 0),
        ("wf_clone_assm_coverage", 0),
        ("wf_clone_trim_length", -1),
        ("wf_clone_min_quality", 61),
        ("wf_clone_assembly_tool", "spades"),
        ("wf_clone_flye_quality", "unsupported"),
        ("wf_clone_non_uniform_coverage", 1),
        ("wf_clone_canu_fast", "false"),
        ("wf_clone_cutsite_mismatch", 11),
        ("wf_clone_primer_mismatch", -1),
        ("wf_clone_expected_coverage", 100.1),
        ("wf_clone_expected_identity", -0.1),
    ],
)
def test_construct_screening_assembly_rejects_invalid_clone_controls(key, value):
    with pytest.raises(ValueError, match=key):
        normalize_ont_launch_params(
            "ont_construct_screening",
            {"run_assembly": True, key: value},
        )


@pytest.mark.parametrize(
    "params",
    [
        {"wf_clone_insert_reference": "/inputs/insert.fa"},
        {"wf_clone_regions_bedfile": "/inputs/regions.bed"},
    ],
)
def test_construct_screening_assembly_enforces_clone_input_dependencies(params):
    with pytest.raises(ValueError, match="requires"):
        normalize_ont_launch_params(
            "ont_construct_screening",
            {"run_assembly": True, **params},
        )
