"""RFD3 fixed ingestion profile: preserve intent, execution and replay identity."""
from copy import deepcopy

import pytest

from services import rfd3_local_redesign as service


@pytest.mark.parametrize("requested", [True, False, None, 0, "", "false"])
def test_requested_output_value_is_not_silently_replaced(tmp_path, monkeypatch, requested):
    source = tmp_path / "source.pdb"
    source.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
        "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\nEND\n"
    )
    monkeypatch.setattr(service, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(service, "_resolve_local_redesign_source", lambda value: source)
    params = dict(input_structure=str(source), design_chains=["A"], redesign_ranges="A1",
                  write_full_json=requested)
    before = deepcopy(params)
    normalized, request, digest = service.normalize_local_redesign_params(params, job_name="profile")
    assert normalized["write_full_json"] == requested
    assert type(normalized["write_full_json"]) is type(requested)
    receipt = normalized["rfd3_output_profile"]["write_full_json"]
    assert receipt["requested_present"] is True
    assert receipt["requested"] == requested
    assert receipt["authority"] == "profile" and receipt["reason"]
    assert receipt["effective"] is True
    assert request["execution"]["write_full_json"] is True
    assert normalized["plr_write_full_json"] is True
    # Same effective immutable request/digest as the old normalizer, without
    # rewriting what the operator supplied or exposing a newly reachable gate.
    historical = {**params, "write_full_json": True}
    assert service.normalize_local_redesign_params(historical, job_name="profile")[1:] == (request, digest)
    replay = {key: value for key, value in normalized.items() if key in service.RFD3_SCIENTIFIC_PARAM_KEYS}
    assert service.normalize_local_redesign_params(replay, job_name="profile")[1:] == (request, digest)
    scheduler = service.prepare_local_redesign_scheduler_params(
        {**params, "workflow_adapter": service.RFD3_WORKFLOW_ADAPTER_ID}, job_name="profile")
    assert scheduler["write_full_json"] == requested
    assert scheduler["rfd3_request"]["execution"]["write_full_json"] is True
    assert params == before


def test_omitted_output_value_reports_profile_default(tmp_path, monkeypatch):
    source = tmp_path / "source.pdb"
    source.write_text("ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n")
    monkeypatch.setattr(service, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(service, "_resolve_local_redesign_source", lambda value: source)
    normalized, request, _ = service.normalize_local_redesign_params(
        dict(input_structure=str(source), design_chains=["A"], redesign_ranges="A1"), job_name="profile")
    assert normalized["write_full_json"] is True
    assert normalized["rfd3_output_profile"]["write_full_json"] == {
        "authority": "profile", "reason": service.EXECUTION_FIELDS["write_full_json"]["reason"],
        "requested_present": False, "requested": None, "effective": True}
    assert request["execution"]["write_full_json"] is True
