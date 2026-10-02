"""Offline fidelity qualification; synthetic matrices are explicitly nonempirical."""
from dataclasses import replace
from fractions import Fraction
import gzip
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import socket
import sys
from typing import Any

import openpyxl
import pytest

from services.assembly import golden_gate_fidelity as gg

S2 = "pryor2020-s002"
S5 = "pryor2020-s005"
FIG4 = "GGAG TGAC TCCC TACT CCAT AATG AGCC TTCG GCTT GGTA CGCT".split()
EXTRA = "ACCT CCGC ACAA AACA GAAA CAAG GCAC TAGA AAAT".split()


def synthetic(entries):
    labels = tuple("".join(x) for x in itertools.product("ACGT", repeat=3))
    return gg.ObservationMatrix({"id": "synthetic-not-empirical", "end_length": 3}, labels, labels,
                                tuple(tuple(entries.get((a, b), 0) for b in labels) for a in labels))


@pytest.mark.parametrize("dataset_id", [f"pryor2020-s{i:03d}" for i in range(1, 6)])
def test_original_bytes_independent_openpyxl_axes_every_cell(dataset_id):
    matrix = gg.load_dataset(dataset_id)
    item = matrix.manifest
    assert item["license"] == "CC-BY-4.0"
    original = gg.ASSET_ROOT / item["file_name"]
    assert hashlib.sha256(original.read_bytes()).hexdigest() == item["sha256"]
    book = openpyxl.load_workbook(original, read_only=True, data_only=True)
    try:
        sheet = book[item["worksheet"]]
        rows = list(sheet.values)
    finally:
        book.close()
    n = 4 ** item["end_length"]
    assert len(rows) == n + 1
    assert tuple(rows[0][1:]) == matrix.column_labels
    assert tuple(x[0] for x in rows[1:]) == matrix.row_labels
    assert matrix.row_labels != matrix.column_labels
    assert matrix.row_labels[0] == "T" * item["end_length"]
    assert matrix.column_labels[0] == "A" * item["end_length"]
    assert tuple(tuple(x[1:]) for x in rows[1:]) == matrix.observations
    assert all(matrix.count(a, b) == matrix.count(b, a) for a in matrix.row_labels for b in matrix.column_labels)
    assert any(matrix.count(a, a) > 0 for a in matrix.row_labels if a != gg.reverse_complement(a))


def test_manifest_conditions_primary_cc_by_and_conversion():
    items = gg.discover_datasets()
    assert len(items) == 5
    assert items[1]["thermal_profile"] == "42C/16C"
    assert items[3]["buffer"] == "CutSmart + 10mM DTT + 1mM ATP"
    assert [x["end_length"] for x in items] == [4, 4, 4, 4, 3]
    for i, item in enumerate(items):
        assert (item["cycles"], item["minutes_per_step"], item["substrate_nM"], item["reaction_volume_uL"]) == (30, 5, 100, 20)
        assert item["ligase_U"] == (None if i < 2 else 500)
        assert item["restriction_enzyme_U"] == (None if i < 2 else 15)
        assert item["enzyme_mix_uL"] == (2 if i < 2 else None)
    primary = (gg.ASSET_ROOT / "primary-license-methods.xml").read_text()
    assert "creativecommons.org/licenses/by/4.0/" in primary
    assert "100 nM" in primary and "500 U" in primary
    spec = importlib.util.spec_from_file_location("pryor_converter", gg.ASSET_ROOT / "convert_pryor.py")
    assert spec is not None and spec.loader is not None
    converter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(converter)
    for item in items:
        converted = converter.convert(gg.ASSET_ROOT / item["file_name"], item["end_length"])
        bundled = json.loads(gzip.decompress((gg.ASSET_ROOT / item["parsed_file"]).read_bytes()))
        assert converted == bundled


@pytest.mark.parametrize("junctions,expected", [(FIG4, 0.8092502583687903), (FIG4 + EXTRA, 0.8047788615874139)])
def test_published_fig4_pair_pooled_not_oriented_or_event_fraction(junctions, expected):
    result = gg.evaluate_overhangs(junctions, S2)
    assert result["f_set"] == pytest.approx(expected, abs=1e-15)
    matrix = gg.load_dataset(S2)
    universe = sorted(set(junctions + [gg.reverse_complement(x) for x in junctions]))
    exact = Fraction(1)
    for a in junctions:
        b = gg.reverse_complement(a)
        exact *= Fraction(matrix.count(a, b) + matrix.count(b, a), sum(matrix.count(a, x) + matrix.count(b, x) for x in universe))
    assert result["f_set"] == pytest.approx(float(exact), abs=1e-15)
    event = sum(matrix.count(a, gg.reverse_complement(a)) for a in universe) / sum(matrix.count(a, b) for a in universe for b in universe)
    assert abs(event - result["f_set"]) > 0.1
    reversed_result = gg.evaluate_overhangs([gg.reverse_complement(x) for x in reversed(junctions)], S2)
    assert reversed_result["f_set"] == result["f_set"]
    assert result["pair_observations"] == []  # no automatic diagnostic matrix on wire


def test_sapi_independent_three_base_fixture():
    matrix = gg.load_dataset(S5)
    ends = ["AAA", "AAC", "AGC"]
    assert len(matrix.row_labels) == 64
    result = gg.evaluate_overhangs(ends, S5, include_pair_observations=True)
    assert result["f_set"] == pytest.approx(0.9899613288578235, abs=1e-15)
    assert len(result["pair_observations"]) == 36
    assert gg.evaluate_overhangs(ends, S2)["status"] == "unavailable"
    assert gg.evaluate_overhangs(["AAAA"], S5)["status"] == "unavailable"


def test_log_objective_distinguishes_underflow_from_observed_zero():
    labels = ["".join(x) for x in itertools.product("ACGT", repeat=3)]
    entries = {(a, b): 1 if b == gg.reverse_complement(a) else 10 ** 20 for a in labels for b in labels}
    matrix = synthetic(entries)
    ends = sorted({min(a, gg.reverse_complement(a)) for a in labels})
    result = gg.score_matrix(matrix, ends)
    assert result["status"] == "available" and result["f_set"] == 0.0
    assert result["log_f_set"] < -745
    assert not result["exact_zero_observed_correct"]
    json.dumps(result, allow_nan=False)


def test_reference_proxy_selection_explicit_and_inventory_background_unweighted():
    plain = gg.evaluate_overhangs(["AATG"], S2)
    result = gg.evaluate_overhangs(["AATG"], S2, condition_use="explicit_proxy", inventory=[
        gg.EndInstance("dropout-1", "AATG", "dropout"),
        gg.EndInstance("removed", "ATAT", "donor", removed=True)])
    assert result["f_set"] == plain["f_set"]
    assert result["unmodeled_inventory_instance_ids"] == ["dropout-1"]
    assert result["condition_use"] == "explicit_proxy"
    search = gg.optimize_overhangs(["AATG"], 1, S2, end_length=4, condition_use="explicit_proxy")
    assert search["condition_use"] == "explicit_proxy"
    with pytest.raises(ValueError):
        gg.evaluate_overhangs(["AATG"], S2, condition_use="auto_best")


def test_synthetic_same_string_terms_and_pair_pooling():
    matrix = synthetic({("AAA", "TTT"): 8, ("TTT", "AAA"): 2,
                        ("AAA", "AAA"): 3, ("TTT", "TTT"): 7})
    result = gg.score_matrix(matrix, ["AAA"], include_pair_observations=True)
    assert result["f_set"] == pytest.approx(0.5)
    assert result["per_junction"][0]["correct_observations"] == 10
    assert result["per_junction"][0]["total_observations"] == 20
    assert result["joining_bias"][0]["relative_to_dataset_max_pooled_wc"] == 1
    assert result["joining_bias"][0]["relative_to_dataset_max_pooled_wc"] != result["f_set"]
    assert len(result["pair_observations"]) == 4


def test_zero_numerator_denominator_empty_and_structural_ambiguity():
    zero = gg.score_matrix(synthetic({}), ["AAA"])
    assert zero["f_set"] is None and zero["reasons"] == ["zero_denominator"]
    observed = gg.score_matrix(synthetic({("AAA", "AAA"): 3}), ["AAA"])
    assert observed["f_set"] == 0 and observed["exact_zero_observed_correct"]
    assert gg.evaluate_overhangs([], S2)["f_set"] is None
    for ends, reason in [(["AATG", "CATT"], "repeated_junction_classes"), (["ATAT"], "palindromic_junctions")]:
        result = gg.evaluate_overhangs(ends, S2, include_pair_observations=True)
        assert result["status"] == "unavailable" and result["f_set"] is None
        assert reason in result["reasons"] and result["pair_observations"]
        assert result["junctions"] == ends


@pytest.mark.parametrize("dataset_id", [None, "unknown"])
def test_unselected_unknown_unavailable_not_denial(dataset_id):
    inventory = [gg.EndInstance("vector-left", "AATG", "vector", "j1", "five_prime"),
                 gg.EndInstance("dropout", None, "dropout", removed=False)]
    result = gg.evaluate_overhangs(["AATG"], dataset_id, inventory=inventory)
    assert result["status"] == "unavailable" and result["f_set"] is None
    assert result["inventory"][1]["sequence"] is None
    assert not result["inventory_complete"]
    assert "allowed" not in result and "can_save" not in result


def test_missing_corrupt_assets_remain_unavailable(tmp_path, monkeypatch):
    items = gg.discover_datasets()
    (tmp_path / "manifest.json").write_text(json.dumps(items))
    monkeypatch.setattr(gg, "ASSET_ROOT", tmp_path)
    assert gg.evaluate_overhangs(FIG4, S2)["status"] == "unavailable"
    item = items[1]
    (tmp_path / item["file_name"]).write_bytes(b"corrupt original")
    (tmp_path / item["parsed_file"]).write_bytes(b"corrupt parsed")
    result = gg.evaluate_overhangs(FIG4, S2)
    assert result["status"] == "unavailable" and "checksum" in result["reasons"][0]


def test_fixed_required_excluded_exact_global_objective():
    domain = ["AAAA", "AAAC", "AATG", "CCGT", "TGAC", "ATAT"]
    result = gg.optimize_overhangs(domain, 3, S2, end_length=4, fixed=["CATT"], required=["AAAC"], excluded=["ACGG"])
    assert result["search_scope"]["complete"] and result["search_scope"]["optimality_proven"]
    for solution in result["solutions"]:
        ends = solution["junctions"]
        assert ends[:2] == ["CATT", "AAAC"]
        assert "CCGT" not in ends and "ATAT" not in ends
        assert solution["f_set"] == gg.evaluate_overhangs(ends, S2)["f_set"]
    expected = max(gg.evaluate_overhangs(["CATT", "AAAC", x], S2)["f_set"] for x in ["AAAA", "TGAC"])
    assert result["solutions"][0]["f_set"] == expected
    assert result["search_scope"]["domain_size"] == 2


def test_seed_budget_restart_and_no_silent_nonempirical_fallback():
    settings = gg.SearchSettings(seed=71, exact_limit=0, evaluation_budget=23, restarts=4, alternatives=3)
    domain = ["AAAA", "AAAC", "AATG", "CCGT", "TGAC", "GCTT"]
    args: dict[str, Any] = dict(candidate_domain=domain, junction_count=3, dataset_id=S2, end_length=4, settings=settings)
    a = gg.optimize_overhangs(**args)
    assert a == gg.optimize_overhangs(**args)
    assert a["search_scope"]["attempted"] == 23
    assert not a["search_scope"]["complete"] and not a["search_scope"]["optimality_proven"]
    assert len(a["solutions"]) <= 3
    args["settings"] = replace(settings, evaluation_budget=0)
    stopped = gg.optimize_overhangs(**args)
    assert stopped["status"] == "no_solution_found_within_budget"
    assert not stopped["search_scope"]["complete"]
    args["dataset_id"] = None
    assert gg.optimize_overhangs(**args)["status"] == "unavailable_objective"
    args["settings"] = replace(settings, ranking_mode="lexicographic")
    unscored = gg.optimize_overhangs(**args)
    assert unscored["solutions"] and all(x["f_set"] is None for x in unscored["solutions"])


def test_conflicts_and_mismatched_dataset_are_outcomes():
    result = gg.optimize_overhangs(["AATG"], 2, S2, end_length=4, fixed=["AATG"], excluded=["CATT"])
    assert not result["solutions"] and result["search_scope"]["complete"]
    assert gg.evaluate_overhangs(["AATG"], None)["status"] == "unavailable"
    mismatched = gg.optimize_overhangs(["AAA", "AAC"], 1, S2, end_length=3)
    assert not mismatched["solutions"] and mismatched["status"] == "unavailable_objective"
    with pytest.raises(ValueError):
        gg.optimize_overhangs([" aaac"], 1, S2, end_length=4)


def test_editable_automatic_preferences_do_not_become_manual_restrictions():
    settings = gg.SearchSettings(unique_classes=False, exclude_palindromes=False, ranking_mode="lexicographic")
    result = gg.optimize_overhangs(["AATG", "ATAT"], 2, S2, end_length=4, settings=settings)
    assert len(result["solutions"]) == 3
    assert any(x["junctions"] == ["ATAT", "ATAT"] for x in result["solutions"])
    assert all(x["f_set"] is None for x in result["solutions"])


def test_real_window_domains_exact_target_protection_and_frame():
    sequence = "AATGACCTGCTTAACCGGAGT"
    domains = gg.sequence_window_domains(sequence, [(2, 9)], end_length=4,
                                        protected_regions=[(6, 7)], frame_constraints=[(0, len(sequence), 0, 2)])
    assert domains == [[{"position": 2, "sequence": "TGAC"}, {"position": 8, "sequence": "GCTT"}]]
    for entry in domains[0]:
        assert entry["sequence"] == sequence[entry["position"]:entry["position"] + 4]


def test_sequence_window_joint_optimization_constraints_and_rerank():
    sequence = "AATGACCTGCTTAACCGGAGTTTGCAGCG"
    result = gg.optimize_sequence_windows(sequence, [(2, 7), (13, 19)], S2, end_length=4,
        fixed_positions=[2, None], fixed_overhangs=["AATG"], required=["TGAC"], excluded=["CCGG"],
        min_fragment_length=2, max_fragment_length=20, target_fragment_length=9)
    assert result["solutions"] and result["search_scope"]["complete"]
    independent = []
    for a, b in itertools.product(*result["candidate_domains"]):
        if b["position"] - a["position"] < 4:
            continue
        lengths = [a["position"], b["position"] - a["position"], len(sequence) - b["position"]]
        if min(lengths) < 2 or max(lengths) > 20:
            continue
        score = gg.evaluate_overhangs(["AATG", a["sequence"], b["sequence"]], S2)["f_set"]
        if score is not None:
            independent.append(score)
    assert result["solutions"][0]["f_set"] == max(independent)
    for solution in result["solutions"]:
        assert solution["cuts"][0] == {"position": 2, "sequence": "TGAC"}
        assert sum(solution["fragment_lengths"]) == len(sequence)
        assert solution["f_set"] == gg.evaluate_overhangs(solution["junctions"], S2)["f_set"]


def test_circular_sapi_origin_window_and_empty_domain_scope():
    sequence = "ACGTTGCAAT"
    domains = gg.sequence_window_domains(sequence, [(9, 10)], end_length=3, topology="circular")
    assert domains == [[{"position": 9, "sequence": "TAC"}]]
    result = gg.optimize_sequence_windows(sequence, [(2, 3), (9, 10)], S5, end_length=3, topology="circular")
    assert result["solutions"] and sum(result["solutions"][0]["fragment_lengths"]) == len(sequence)
    empty = gg.optimize_sequence_windows(sequence, [(2, 3)], S5, end_length=3, protected_regions=[(0, len(sequence))])
    assert not empty["solutions"] and empty["search_scope"]["complete"]
    zero = gg.optimize_sequence_windows(sequence, [], S5, end_length=3)
    assert not zero["solutions"]


def test_window_local_search_deterministic_and_incomplete():
    args: dict[str, Any] = dict(sequence="AATGACCTGCTTAACCGGAGTTTGCAGCG", windows=[(2, 7), (13, 19)], dataset_id=S2,
                end_length=4, settings=gg.SearchSettings(seed=5, exact_limit=0, evaluation_budget=17, restarts=3))
    result = gg.optimize_sequence_windows(**args)
    assert result == gg.optimize_sequence_windows(**args)
    assert result["search_scope"]["attempted"] == 17
    assert not result["search_scope"]["complete"]


def test_circular_wrapping_window_is_one_slot_and_cyclic_order_is_preserved():
    sequence = "ACGTTGCAAT"
    domains = gg.sequence_window_domains(sequence, [(9, 2)], end_length=3, topology="circular")
    assert len(domains) == 1
    assert [x["position"] for x in domains[0]] == [9, 0, 1]
    result = gg.optimize_sequence_windows(sequence, [(9, 2), (4, 5)], S5,
                                         end_length=3, topology="circular", fixed_positions=[9, 4])
    assert result["solutions"][0]["fragment_lengths"] == [5, 5]
    assert [x["position"] for x in result["solutions"][0]["cuts"]] == [9, 4]
    protected = gg.sequence_window_domains(sequence, [(9, 2)], end_length=3,
                                          topology="circular", protected_regions=[(9, 2)])
    assert protected == [[]]


def test_discovery_no_matrix_and_cold_import_no_io_or_network(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "empty-cache"))
    def denied(*args, **kwargs):
        raise AssertionError("Import/network attempted")
    with monkeypatch.context() as patch:
        patch.setattr(socket, "create_connection", denied)
        patch.setattr(socket, "getaddrinfo", denied)
        patch.setattr(Path, "read_bytes", denied)
        patch.setattr(Path, "read_text", denied)
        name = "cold_golden_gate_fidelity"
        spec = importlib.util.spec_from_file_location(name, gg.__file__)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            del sys.modules[name]
    assert module.evaluate_overhangs(FIG4, S2)["f_set"] == pytest.approx(0.8092502583687903)
    assert list(tmp_path.iterdir()) == []
    assert all("observations" not in item for item in gg.discover_datasets())
    json.dumps(module.evaluate_overhangs(FIG4, S2), allow_nan=False)
