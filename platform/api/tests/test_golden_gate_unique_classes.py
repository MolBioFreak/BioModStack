"""Golden Gate never allows a reused overhang class: not in the search, not in the request, not in design."""
import pytest
from pydantic import ValidationError

import services.assembly.golden_gate_fidelity as gg
from services.assembly.golden_gate_workflow_types import REQUEST_ADAPTER, SearchControls


def test_search_settings_default_is_unique_and_cannot_be_turned_off():
    assert gg.SearchSettings().unique_classes is True
    with pytest.raises(ValueError, match="Duplicate overhangs are not allowed"):
        gg.SearchSettings(unique_classes=False)


def test_request_search_controls_refuse_reuse():
    assert SearchControls().unique_classes is True
    with pytest.raises(ValidationError):
        SearchControls(unique_classes=False)


def test_optimize_request_with_reuse_is_rejected_on_the_wire():
    with pytest.raises(ValidationError):
        REQUEST_ADAPTER.validate_python(dict(
            task="optimize_overhangs", candidate_domain=["AATG", "GCTT"], junction_count=3, end_length=4,
            search=dict(unique_classes=False)))


def test_search_never_returns_a_reused_class_even_when_it_runs_out_of_overhangs():
    settings = gg.SearchSettings(ranking_mode="lexicographic")
    result = gg.optimize_overhangs(["AATG", "CATT", "GCTT"], 3, None, end_length=4, settings=settings)
    assert not result["solutions"]
    one = gg.optimize_overhangs(["AATG", "CATT", "GCTT"], 2, None, end_length=4, settings=settings)
    for solution in one["solutions"]:
        classes = {min(j, gg.reverse_complement(j)) for j in solution["junctions"]}
        assert len(classes) == len(solution["junctions"])


def test_palindromes_can_still_be_allowed_without_allowing_reuse():
    settings = gg.SearchSettings(exclude_palindromes=False, ranking_mode="lexicographic")
    result = gg.optimize_overhangs(["AATG", "ATAT"], 2, None, end_length=4, settings=settings)
    assert [x["junctions"] for x in result["solutions"]] == [["AATG", "ATAT"]]
