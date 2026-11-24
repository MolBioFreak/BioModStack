"""Bounded native replay of PL931 proposal observations, with no store mutations.

The retained browser design fixture supplies the actual DNA/annotations. Search,
preparation, restriction digest and pydna computations are real, not mocked.
"""
from copy import deepcopy

import pytest

from services.assembly import gibson_suggest as gibson
from services.assembly import golden_gate_suggest as gg
from services.assembly.golden_gate import resolve_golden_gate_enzyme
from services.assembly.golden_gate_design import _scan, design_golden_gate, preparation_geometry
from services.assembly.golden_gate_design_types import Source
from services.assembly.golden_gate_workflow import _split_core, material_for, run_workflow
from services.assembly.golden_gate_workflow_types import SplitTask
from services.assembly.golden_gate_workflow_wire import expand_workflow
from services.restriction_catalog import catalog_authority
from test_golden_gate_append_only_save import PL931_BROWSER_REQUEST


def pl931():
    return expand_workflow(deepcopy(PL931_BROWSER_REQUEST), "request")


@pytest.mark.parametrize("name,starts,fragment_counts", [
    ("SapI", [433, 643, 4811], [5, 4]),
    ("BtgZI", [363, 556, 707, 1464, 1855, 3494, 3896], [8, 5]),
])
def test_pl931_internal_cuts_explain_failed_physical_candidates(name, starts, fragment_counts):
    body = pl931()
    original = deepcopy(body)
    catalog = catalog_authority.require()
    enzyme = resolve_golden_gate_enzyme(enzyme_id=name, catalog_id=catalog.catalog_id,
                                       expected_catalog_sha256=catalog.content_sha256)
    geometry = preparation_geometry(enzyme)
    material = material_for(Source.model_validate(body["target"]), None)
    scan = _scan(material.sequence, material.topology, enzyme)
    assert [o.site_start for o in scan.occurrences] == starts
    assert all(e.status == "complete" for o in scan.occurrences for e in o.double_strand_events)
    body["enzyme"]["enzyme_id"] = name
    body["preparation"]["spacer"] = "ACGTACGTACGT"[:geometry.spacer_length]
    body["fidelity"]["dataset_id"] = gg.DATASET_BY_ENZYME.get(name)
    body["search"]["ranking_mode"] = "empirical" if name in gg.DATASET_BY_ENZYME else "lexicographic"
    task = SplitTask.model_validate(body)
    native = run_workflow(task)
    search = native.search_result
    assert len(search["solutions"]) == 3
    assert search["search_scope"]["attempted"] == 10000
    assert search["search_scope"]["complete"] is False
    assert native.solutions == []
    # Inspect actual digested material, not merely a no-proposal string.
    core = _split_core(task, material, search["solutions"][0]["cuts"], geometry.overhang_length)
    physical = design_golden_gate(core)
    assert physical.solutions == []
    assert [len(d.fragments) for d in physical.digests] == fragment_counts
    assert all(d.retained_fragment_index is None for d in physical.digests)
    assert all(p.retained_material_id is None for p in physical.preparations)
    assert all("intact intended payload" in reason for reason in physical.diagnostics)
    result = gg.suggest_golden_gate(gg.SuggestRequest(target=original["target"], enzymes=[name]))
    assert result.proposals == []
    status = result.enzymes[0].status
    assert result.enzymes[0].internal_sites == len(starts)
    assert "none of 3 searched junction set(s) yielded a physical design" in status
    assert f"target contains {len(starts)} internal recognition site(s)" in status
    assert "digest does not contain one intact intended payload" in status
    assert "neither optimality nor infeasibility is proven" in status
    assert "in this search" in result.diagnostics[0]
    assert PL931_BROWSER_REQUEST["sequences"][0] == material.sequence
    assert pl931() == original


def test_pl931_bsai_retains_original_search_and_exact_dna():
    body = pl931()
    result = gg.suggest_golden_gate(gg.SuggestRequest(target=body["target"], enzymes=["BsaI"]))
    assert [p.cuts for p in result.proposals] == [[1, 2094], [1, 2096], [1, 2097]]
    assert result.enzymes[0].status == "3 proposal(s)"
    assert result.enzymes[0].internal_sites == 0
    for proposal in result.proposals:
        product = proposal.candidate.design.solutions[0]
        assert product.exact_target_match
        assert product.sequence == body["target"]["source"]["sequence"]


def test_no_junction_set_reports_search_owner_evidence():
    result = gg.suggest_golden_gate(gg.SuggestRequest(
        target=pl931()["target"], enzymes=["BsaI"],
        protected_regions=[dict(start=0, end=5584)]))
    assert result.proposals == []
    assert "no junction set returned by this search" in result.enzymes[0].status
    assert "no feasible split" not in result.enzymes[0].status


def gibson_request():
    source = pl931()["target"]["source"]
    return gibson.SuggestRequest.model_validate(dict(
        target=dict(name="PL931", sequence=source["sequence"], circular=True, features=source["features"]),
        sources=[dict(id="src1", name="PL931", sequence=source["sequence"], circular=True,
                      features=source["features"])],
        min_match=30, overlap=30, target_tm=60, min_anneal=13))


def test_pl931_gibson_report_is_two_roles_not_duplicate_annotation():
    req = gibson_request()
    before = req.model_dump(mode="json")
    # Independently check identity, strand, intervals and the actual annotated bases.
    source = req.sources[0]
    assert source.sequence == req.target.sequence and len(source.sequence) == 5584
    assert source.features == req.target.features and len(source.features) == 9
    source_spans = gibson._feature_spans(source.features, source.sequence, True)
    target_spans = gibson._feature_spans(req.target.features, req.target.sequence, True)
    assert source_spans == target_spans
    assert len({key for key, _, _ in source_spans}) == 9
    for (_, _, source_positions), (_, _, target_positions) in zip(source_spans, target_spans):
        assert source_positions == target_positions
        assert "".join(source.sequence[p] for p in source_positions) == "".join(req.target.sequence[p] for p in target_positions)
    result = gibson.suggest_gibson(req)
    proposal, = result.proposals
    assert proposal.counts == dict(pcr=1, synthesis=0, existing=0)
    assert len(proposal.features.kept) == 18
    assert proposal.features.removed == proposal.features.clipped == []
    for key, name, _ in source_spans:
        assert proposal.features.kept.count(f"PL931: {name} ({key})") == 1
        assert proposal.features.kept.count(f"target: {name} ({key})") == 1
    assert any("source coverage and target match coverage separately" in note for note in proposal.notes)
    actual = gibson._evaluate(proposal.design_request, req.target.sequence, True)
    assert actual.candidates[0].product.sequence == req.target.sequence
    assert len(actual.primers) == 2
    assert req.model_dump(mode="json") == before


@pytest.mark.parametrize("difference", ["identity", "interval", "strand"])
def test_same_label_annotations_are_not_merged(difference):
    req = gibson_request()
    # Same displayed name does not establish biological equivalence.
    source_feature = deepcopy(req.sources[0].features[0])
    source_feature["name"] = "shared label"
    target_feature = deepcopy(source_feature)
    if difference == "identity":
        target_feature["id"] = "different-id"
    elif difference == "interval":
        target_feature["segments"][0]["end"] -= 5
    else:
        target_feature["strand"] *= -1
    req.sources[0].features = [source_feature]
    req.target.features = [target_feature]
    result = gibson.suggest_gibson(req)
    assert result.proposals[0].features.kept == [
        f"PL931: shared label ({source_feature['id']})",
        f"target: shared label ({target_feature['id']})"]
    assert req.sources[0].features == [source_feature]
    assert req.target.features == [target_feature]
