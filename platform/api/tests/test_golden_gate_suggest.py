"""A2b: target + sources, no hand-authored cuts -> proposals that digest/assemble to the target."""
import pytest
from services.assembly.golden_gate_suggest import SuggestRequest, suggest_golden_gate
from services.assembly.golden_gate_workflow import freeze_selection
from services.assembly.golden_gate_workflow_types import SaveDesignRequest
from test_golden_gate_design_core import A, B, binding
from test_golden_gate_workflow_receiving import client_store

TARGET = "AATG" + A + "GGAG" + B


def req(**kw):
    base = dict(target=dict(id="target", source=dict(kind="inline", sequence=TARGET, topology="circular")),
                enzymes=["BsaI", "BbsI", "SapI", "PaqCI", "BtgZI", "Esp3I"], fragment_count=3, min_fragment_length=10)
    base.update(kw)
    return SuggestRequest.model_validate(base)


def test_proposals_assemble_to_target_and_label_enzymes():
    r = suggest_golden_gate(req())
    assert 1 <= len(r.proposals) <= 3
    for p in r.proposals:
        sol = p.candidate.design.solutions[0]
        assert sol.sequence == TARGET and sol.exact_target_match
        assert p.junction_mode == "scarless" and len(p.junctions) == 3
        digest_lengths = [len(f.top_strand_sequence) for d in p.candidate.design.digests for f in d.fragments]
        assert digest_lengths  # prepared fragments digest to pieces
    names = {e.name: e for e in r.enzymes}
    assert names["PaqCI"].empirical == "no empirical data"
    assert names["BsaI"].empirical.startswith("pryor2020")


def test_fixed_boundary_respected():
    r = suggest_golden_gate(req(fixed_boundaries=[30], enzymes=["BsaI"]))
    assert r.proposals and all(30 in p.cuts for p in r.proposals)


def test_donor_default_is_overlap_not_index_zero():
    from services.assembly.golden_gate_suggest import default_retained_fragment_index
    from services.assembly.golden_gate import resolve_golden_gate_enzyme
    from services.assembly.feature_types import Material
    b = binding("BsaI")
    enz = resolve_golden_gate_enzyme(enzyme_id="BsaI", catalog_id=b["catalog_id"], expected_catalog_sha256=b["catalog_sha256"])
    donor = "TT" + "GGTCTC" + "A" + "AATG" + A + "GGAG" + "T" + "GAGACC" + "AA" + B
    m = Material(sequence=donor, topology="circular")
    inside = default_retained_fragment_index(m, enz, (15, 40))
    outside = default_retained_fragment_index(m, enz, (len(donor) - 20, len(donor) - 5))
    assert inside is not None and outside is not None and inside != outside


def test_pcr_reports_tailed_and_payload_lengths():
    r = suggest_golden_gate(req(preparation_kind="pcr", enzymes=["BsaI"]))
    pcr = r.proposals[0].pcr_products
    assert pcr and all(x["tailed_length"] > x["payload_length"] for x in pcr)


@pytest.mark.asyncio
async def test_route_and_save_reopen(tmp_path):
    async with client_store(tmp_path) as (client, _):
        resp = await client.post("/api/molbio/assembly/golden-gate/suggest", json=req(enzymes=["BsaI"]).model_dump(mode="json"))
        assert resp.status_code == 200, resp.text
        top = resp.json()["proposals"][0]
        from services.assembly.golden_gate_workflow_types import WorkflowResult, WorkflowRequest, REQUEST_ADAPTER
        from services.assembly.golden_gate_workflow import run_workflow
        result = run_workflow(REQUEST_ADAPTER.validate_python(top["split_request"]))
        save = SaveDesignRequest(selection=freeze_selection(result, result.selected_solution_id), name="Suggested", idempotency_key="sg")
        saved = await client.post("/api/molbio/assembly/golden-gate/design/save", json=save.model_dump(mode="json"))
        assert saved.status_code == 200, saved.text
        reopened = await client.get("/api/molbio/assembly/golden-gate/design/" + saved.json()["operation_id"])
        assert reopened.json()["result"]["solutions"][0]["design"]["solutions"][0]["sequence"] == TARGET


@pytest.mark.asyncio
async def test_a5f_intermediate_and_final_product_reusable_as_golden_gate_sources(tmp_path):
    """Saved GG intermediate revision and final product revision resolve as sources in a new GG request."""
    from sqlalchemy import select
    from molbio_models import MolecularOperation, MolecularRevision
    from services.assembly.golden_gate_design import design_golden_gate
    from services.assembly.golden_gate_workflow_persistence import resolve_sources
    from services.assembly.golden_gate_design_types import GoldenGateDesignRequest
    from test_golden_gate_design_core import request
    async with client_store(tmp_path) as (client, sessions):
        from services.assembly.golden_gate_workflow import run_workflow
        from services.assembly.golden_gate_workflow_types import REQUEST_ADAPTER
        result = run_workflow(REQUEST_ADAPTER.validate_python(request().model_dump(mode="json")))
        save = SaveDesignRequest(selection=freeze_selection(result, "fixed"), name="GG", idempotency_key="a5f")
        saved = await client.post("/api/molbio/assembly/golden-gate/design/save", json=save.model_dump(mode="json"))
        assert saved.status_code == 200, saved.text
        async with sessions() as session:
            revisions = (await session.scalars(select(MolecularRevision))).all()
            by_len = {r.id: r.snapshot["sequence"] for r in revisions}
            final = saved.json()["product_revision_id"]
            inter = next(r.id for r in revisions if r.id != final and r.snapshot["sequence"] != by_len[final]
                         and not r.snapshot.get("is_circular"))
            for rid in (inter, final):
                src = dict(id="s", source=dict(kind="molecular_revision", revision_id=rid))
                resolved, _ = await resolve_sources(session, [GoldenGateDesignRequest.model_validate(dict(
                    sources=[src], parts=[], target=dict(topology="linear"),
                    enzyme=binding("BsaI"))).sources[0]])
                assert resolved[rid].sequence == by_len[rid]
        # the final product revision (no shelf row) is the PCR template of a new design through the real route
        again = request().model_dump(mode="json")
        again["sources"][1] = dict(id="insert", source=dict(kind="molecular_revision", revision_id=final))
        again["parts"][1]["preparation"]["region"] = dict(start=4, end=4 + len(B))
        again["target"].pop("exact_sequence", None)
        resp = await client.post("/api/molbio/assembly/golden-gate/design", json=again)
        assert resp.status_code == 200, resp.text
