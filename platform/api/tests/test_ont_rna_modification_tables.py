"""RNA basecalling can make per-site modification tables through the shared modkit stage."""
from __future__ import annotations

from pathlib import Path

import pytest

from routers import jobs, ont_runs
from services import job_readback
from services.ont_ngs_contract import CANONICAL_ONT_WORKFLOWS, normalize_ont_launch_params

REPO = Path(__file__).resolve().parents[3]


def test_rna_workflow_runs_the_shared_modkit_stage_on_its_aligned_reads() -> None:
    text = (REPO / "workflows/ngs/ont_basecall_rna.nf").read_text(encoding="utf-8")
    assert "include { ModkitAnalysis } from '../../modules/ngs/modkit_analysis.nf'" in text
    assert "ModkitAnalysis(DoradoAlign.out.aligned, Channel.value(reference_file))" in text
    assert "params.run_modkit == true && (params.modified_bases ?: 'none')" in text
    spec = CANONICAL_ONT_WORKFLOWS["ont_basecall_rna"]
    assert ("run_modkit=true", ("modkit_summary", "methylation_bed")) in spec.artifact_conditions
    assert {"modkit_summary", "methylation_bed"} <= set(spec.artifact_kinds)


def test_rna_tables_stay_off_unless_asked_for_and_need_a_reference() -> None:
    assert normalize_ont_launch_params("ont_basecall_rna", {})["run_modkit"] is False
    spec = CANONICAL_ONT_WORKFLOWS["ont_basecall_rna"]
    params = {"pod5_dir": "/data/pod5", "run_modkit": True, "modified_bases": "m6A_DRACH"}
    with pytest.raises(ValueError, match="RNA modification tables require reference_fasta"):
        ont_runs._validate_ont_input_contract("ont_basecall_rna", params)
    assert ont_runs._validate_ont_input_contract("ont_basecall_rna", {**params, "reference_fasta": "/data/ref.fa"})[0] == "pod5"
    assert ont_runs._validate_ont_input_contract("ont_basecall_rna", {"pod5_dir": "/data/pod5"})[0] == "pod5"
    # No modification model, nothing to tabulate: a plain basecall goes ahead as before.
    assert ont_runs._validate_ont_input_contract("ont_basecall_rna", {"pod5_dir": "/data/pod5", "run_modkit": True})[0] == "pod5"
    assert spec.input_modes == ("pod5",)


def test_rna_stage_list_names_the_tables_only_when_they_were_asked_for() -> None:
    base = {"pod5_dir": "/data/pod5", "reference_fasta": "/data/ref.fa"}
    assert job_readback._planned_nanopore_stages(base, mode="basecall_rna") == ["dorado_basecall", "dorado_align"]
    assert job_readback._planned_nanopore_stages({**base, "run_modkit": True}, mode="basecall_rna") == ["dorado_basecall", "dorado_align"]
    assert job_readback._planned_nanopore_stages({**base, "run_modkit": True, "modified_bases": "m6A_DRACH"}, mode="basecall_rna") == [
        "dorado_basecall", "dorado_align", "modkit"]
