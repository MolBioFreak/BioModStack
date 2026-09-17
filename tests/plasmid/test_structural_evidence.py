import pytest
from plasmid_evidence import StructuralEvidenceUnavailable, read_summary, validate_structural_rows
from build_construct_topology_evidence import derive_topology_evidence


def call(**kw):
    return {"call_status": "split_supported", "call_confidence": "high",
            "primary_position_mod_ref": "500", "boundary_window_bp": "50", **kw}


def test_canonical_names_detect_non_boundary_split():
    screen = validate_structural_rows([call()], [{"aligned_dimer_reads": "10", "non_boundary_split_reads": "1"}], 1000)
    assert screen["contradictory_breakpoint_evidence"] is True
    assert screen["secondary_anomaly_fraction"] == .1


def test_origin_is_not_structural_contradiction():
    screen = validate_structural_rows([call(primary_position_mod_ref="0")], [{"aligned_dimer_reads": "10", "non_boundary_split_reads": "0"}], 1000)
    assert screen["contradictory_breakpoint_evidence"] is False


@pytest.mark.parametrize("rows", [[], [{"aligned_dimer_reads": "0", "non_boundary_split_reads": "0"}]])
def test_absent_or_no_eligible_screen_is_not_zero(rows):
    with pytest.raises(StructuralEvidenceUnavailable):
        validate_structural_rows([call()], rows, 1000)


@pytest.mark.parametrize("value", ["", "nan", "1.0", "-1", "11"])
def test_invalid_anomaly_counts(value):
    with pytest.raises(ValueError):
        validate_structural_rows([call()], [{"aligned_dimer_reads": "10", "non_boundary_split_reads": value}], 1000)


def test_header_only_summary_is_unavailable(tmp_path):
    path=tmp_path/'summary.tsv'
    path.write_text('aligned_dimer_reads\tnon_boundary_split_reads\n')
    with pytest.raises(StructuralEvidenceUnavailable):
        read_summary(path)


def test_duplicate_sample_summary_is_invalid(tmp_path):
    path=tmp_path/'summary.tsv'
    path.write_text('aligned_dimer_reads\tnon_boundary_split_reads\n10\t0\n10\t0\n')
    with pytest.raises(ValueError):
        read_summary(path)


def test_origin_evidence_does_not_turn_missing_screen_clean():
    result=derive_topology_evidence(reference_length=1000,
        sam_rows=['r1\t0\tp\t1\t60\t1000M\t*\t0\t0\tA\tI'],
        breakpoint_rows=[],secondary_rows=[],edge_window_bp=50)
    assert result['state']=='unavailable'
    assert result['reason']=='STRUCTURAL_SCREEN_NOT_EVALUATED'
    assert 'secondary_anomaly_fraction' not in result


def test_conflicting_aliases_are_invalid():
    with pytest.raises(ValueError, match='conflicting'):
        validate_structural_rows([call(breakpoint_status='no_split')],
            [{'aligned_dimer_reads':'10','non_boundary_split_reads':'0'}], 1000)
