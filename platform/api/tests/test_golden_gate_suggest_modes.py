"""Imposed standard overhangs and supplied parts: expected products are built by hand, not by the owner."""
import pytest
from Bio.Seq import Seq

from services.assembly.golden_gate_suggest import SuggestRequest, suggest_golden_gate
from services.assembly.golden_gate_workflow import freeze_selection, run_workflow
from services.assembly.golden_gate_workflow_types import REQUEST_ADAPTER, SaveDesignRequest
from test_golden_gate_design_core import A, B, ENZYMES
from test_golden_gate_workflow_receiving import client_store

C = "GATTACAGGCTTAACCGTATCGGATCCAGTCATGCAACGTTGACCATGGTCAA"
SITES = {name: (site, spacer, width) for name, site, spacer, width in ENZYMES}


def rc(s):
    return str(Seq(s).reverse_complement())


def inline(id, seq, topology="linear"):
    return dict(id=id, source=dict(kind="inline", sequence=seq, topology=topology))


def digested(proposal):
    return {r["part_id"]: r for r in proposal.prepared_fragments}


@pytest.mark.parametrize("enzyme", ["BsaI", "PaqCI"])
def test_imposed_overhangs_digest_to_standard_ends_and_assemble_with_scars(enzyme):
    width = SITES[enzyme][2]
    standard = ["AATG", "GCTT", "CGAA"] if width == 4 else ["AAT", "GCT", "CGA"]
    r = suggest_golden_gate(SuggestRequest.model_validate(dict(
        target=inline("t", A + B + C, "circular"), enzymes=[enzyme], fragment_count=3,
        standard_overhangs=standard, min_fragment_length=10)))
    p = r.proposals[0]
    assert p.junction_mode == "imposed" and p.junctions == standard
    rows = digested(p)
    # prepared fragments digest (real digest, inside the design) to pieces whose ends are the standard overhangs
    for i in range(3):
        row = rows[f"part-{i + 1}"]
        assert row["left_overhang"] == standard[i] and row["right_overhang"] == rc(standard[(i + 1) % 3])
    # independent expected product: overhang + payload for each piece, payloads are the three target slices
    payloads = [row["digested_sequence"][width:] for row in (rows[f"part-{i + 1}"] for i in range(3))]
    assert "".join(payloads) == A + B + C
    assert p.final_sequence == "".join(s + x for s, x in zip(standard, payloads))
    assert p.candidate.design.solutions[0].exact_target_match is True
    assert p.f_set is None or p.empirical.startswith("pryor") or p.ranking_mode == "lexicographic"


def test_imposed_with_linker_bases_in_prepared_fragments_and_product():
    r = suggest_golden_gate(SuggestRequest.model_validate(dict(
        target=inline("t", A + B, "circular"), enzymes=["BsaI"], fragment_count=2,
        standard_overhangs=["AATG", "GCTT"], linkers=["GGCG", ""], min_fragment_length=10)))
    p = r.proposals[0]
    first, second = digested(p)["part-1"], digested(p)["part-2"]
    assert first["digested_sequence"].startswith("AATG" + "GGCG")
    assert second["left_overhang"] == "GCTT"
    assert p.final_sequence.startswith("AATG" + "GGCG")
    assert len(p.final_sequence) == len(A + B) + 4 + 4 + 4  # two 4-nt overhangs + one 4-nt linker


def test_imposed_scored_by_existing_fidelity_owner_and_labelled_when_no_dataset():
    req = dict(target=inline("t", A + B + C, "circular"), fragment_count=3, min_fragment_length=10,
               standard_overhangs=["AATG", "GCTT", "CGAA"])
    bsai = suggest_golden_gate(SuggestRequest.model_validate({**req, "enzymes": ["BsaI"]})).proposals[0]
    paqci = suggest_golden_gate(SuggestRequest.model_validate({**req, "enzymes": ["PaqCI"]})).proposals[0]
    from services.assembly import golden_gate_fidelity as fid
    direct = fid.evaluate_overhangs(["AATG", "GCTT", "CGAA"], "pryor2020-s001")
    assert bsai.f_set == direct["f_set"] and bsai.f_set is not None
    assert paqci.empirical == "no empirical data" and paqci.f_set is None and paqci.ranking_mode == "lexicographic"


def test_vector_closure_ends_are_in_the_scored_junction_set():
    # destination vector cut by BsaI leaves ends GGAG (left of insert) and TACT (right) -> both fixed junctions
    vector = "TT" + "GGTCTC" + "A" + "GGAG" + "ACGTACGTACGTAC" + "TACT" + "A" + "GAGACC" + "AA" + A
    r = suggest_golden_gate(SuggestRequest.model_validate(dict(
        vector=inline("v", vector, "circular"), enzymes=["BsaI"], preparation_kind="synthesis",
        parts=[dict(source=inline("p1", B[:30]), name="P1"), dict(source=inline("p2", B[30:]), name="P2")],
        min_fragment_length=1)))
    p = r.proposals[0]
    assert p.junction_mode == "chosen"
    origins = {s["index"]: s["origin"] for s in p.junction_slots}
    assert origins[0] == "vector" and origins[2] == "vector" and origins[1] == "optimizer"
    assert p.junctions[0] != p.junctions[2] and {p.junctions[0], p.junctions[2]} == {"GGAG", "TACT"}
    assert p.f_set is not None and p.final_sequence is not None


@pytest.mark.parametrize("enzyme", ["BsaI", "BtgZI"])
def test_parts_mode_fixed_overhang_respected_and_product_is_hand_built(enzyme):
    width = SITES[enzyme][2]
    fixed = "AATG"
    r = suggest_golden_gate(SuggestRequest.model_validate(dict(
        enzymes=[enzyme], circular=True, preparation_kind="pcr",
        parts=[dict(source=inline("a", A), name="A", left_overhang=fixed),
               dict(source=inline("b", B), name="B"),
               dict(source=inline("c", C), name="C")])))
    p = r.proposals[0]
    assert p.junctions[0] == fixed and p.junction_slots[0]["origin"] == "user"
    order = [row for row in p.prepared_fragments]
    assert all(row["tailed_length"] > row["payload_length"] for row in order)
    # hand-built product: each junction label followed by its payload (labels from the proposal)
    assert p.final_sequence == p.junctions[0] + A + p.junctions[1] + B + p.junctions[2] + C
    assert len(set(p.junctions)) == 3
    assert order[0]["primers"][0].startswith("TT" + SITES[enzyme][0])


@pytest.mark.asyncio
async def test_parts_plan_save_reopen_restores_moved_boundary(tmp_path):
    async with client_store(tmp_path) as (client, _):
        body = dict(enzymes=["BsaI"], circular=True, preparation_kind="synthesis",
                    parts=[dict(source=inline("a", A), name="A", left_overhang="AATG"),
                           dict(source=inline("b", B), name="B", left_overhang="GGAG"),
                           dict(source=inline("c", C), name="C")])
        resp = await client.post("/api/molbio/assembly/golden-gate/suggest", json=body)
        assert resp.status_code == 200, resp.text
        top = resp.json()["proposals"][0]
        task = top["assemble_request"]
        # move the B/C junction: user edits the label in the posted design request
        moved = "TACT"
        for part, label in (("part-2", moved),):
            task["parts"][1]["preparation"]["right"]["fusion"] = label
        task["parts"][2]["preparation"]["left"]["fusion"] = moved
        task["target"].pop("exact_sequence", None)
        result = run_workflow(REQUEST_ADAPTER.validate_python(task))
        save = SaveDesignRequest(selection=freeze_selection(result, result.selected_solution_id), name="Parts plan", idempotency_key="parts1")
        saved = await client.post("/api/molbio/assembly/golden-gate/design/save", json=save.model_dump(mode="json"))
        assert saved.status_code == 200, saved.text
        reopened = await client.get("/api/molbio/assembly/golden-gate/design/" + saved.json()["operation_id"])
        got = reopened.json()["result"]["solutions"][0]["design"]["solutions"][0]["sequence"]
        assert got == "AATG" + A + "GGAG" + B + moved + C
