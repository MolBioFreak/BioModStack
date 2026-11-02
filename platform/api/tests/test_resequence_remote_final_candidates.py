"""A remote Resequence continuation names its reviewed selection as the final candidate directory."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

SELECTION = ".bms-review/selections/637bd834741f3cbf0b7f8a2fe29914726ba3abaad3f1921746f035d7bb1b4bf3"


def _job(model_id="protein_modification_experimental", mode="region_redesign", checkpoint="job:post_structure_validation"):
    return SimpleNamespace(model_id=model_id, mode=mode, params={},
                           provenance={"remote_checkpoint_operation": {"binding": {"checkpoint_id": checkpoint}}})


@pytest.mark.parametrize("model_id,mode", [("protein_modification_experimental", "region_redesign"),
                                           ("protein_local_redesign", "local_redesign")])
def test_final_gate_continuation_publishes_the_reviewed_selection(tmp_path, model_id, mode):
    from services.remote_execution.executor import _resequence_final_candidate_dir
    local_output = tmp_path / SELECTION / "continuation"
    found = _resequence_final_candidate_dir(_job(model_id, mode), PurePosixPath(SELECTION + "/continuation"), local_output)
    assert found == tmp_path / SELECTION


@pytest.mark.parametrize("job,relative", [
    (_job(checkpoint="job:post_fampnn"), SELECTION + "/continuation"),                    # earlier gate: finals come later
    (_job("boltz2", "predict"), SELECTION + "/continuation"),                            # not a Resequence job
    (_job(), "."),                                                                       # no continuation (first generation)
    (_job(), "components/child"),                                                        # some other native output
    (SimpleNamespace(model_id="protein_modification_experimental", mode="region_redesign", params={}, provenance={}),
     SELECTION + "/continuation"),                                                       # no checkpoint operation
])
def test_other_results_name_no_final_candidate_dir(tmp_path, job, relative):
    from services.remote_execution.executor import _resequence_final_candidate_dir
    assert _resequence_final_candidate_dir(job, PurePosixPath(relative), Path(tmp_path / relative)) is None
