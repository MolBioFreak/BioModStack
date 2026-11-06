"""The Resequence workflow publishes no RFD3 filter stage, so its results must not be refused for lacking one."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

PLR_JOBS = [("protein_modification_experimental", "region_redesign"), ("protein_local_redesign", "local_redesign")]


@pytest.mark.parametrize("model_id,mode", PLR_JOBS)
def test_resequence_result_needs_no_filter_stage_record(model_id, mode):
    from services.rf_filter_stage_accounting import prepare_filter_stages
    # A remote run never writes the controller-side roster, and the workflow writes no run/filter_stages files.
    job = SimpleNamespace(id="job", model_id=model_id, mode=mode, params={"plr_seq_method": "mpnn"},
                          provenance={"core_protein_scientific_contract": 1})
    with tempfile.TemporaryDirectory() as root:
        assert prepare_filter_stages(job, Path(root)) is None


def test_protein_design_still_requires_its_roster_and_stage_records():
    from services.rf_filter_stage_accounting import prepare_filter_stages
    job = SimpleNamespace(id="job", model_id="protein_modification_experimental", mode="de_novo_design", params={},
                          provenance={"core_protein_scientific_contract": 1})
    with tempfile.TemporaryDirectory() as root:
        with pytest.raises(ValueError, match="roster missing or incomplete"):
            prepare_filter_stages(job, Path(root))
