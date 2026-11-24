"""Lane C: Gibson with restriction-cut (sticky-ended) vectors counts the cohesive end once."""
from __future__ import annotations

import pytest

from services.assembly.gibson import simulate_gibson
from services.assembly.types import AssemblyFragment, FragmentEnd
from test_assembly_product_content import client_with_store, dna, rc

BODY = dna(41, 398)
INSERT = dna(42, 500)


def ecori_vector():
    # EcoRI-cut: top span AATT...ATG, 5' top AATT at left, 5' bottom AATT at right.
    top = "AATT" + BODY + "ATGC"
    assert len(top) == 406
    left = {"type": "sticky_5", "overhang": "AATT", "protruding_strand": "top"}
    right = {"type": "sticky_5", "overhang": "AATT", "protruding_strand": "bottom"}
    return top, left, right


def payload(overlap, vec_top, left, right, prep="ready_linear"):
    return {"fragments": [
        {"id": "v", "name": "vector", "sequence": vec_top, "preparation": prep,
         "left_end": left, "right_end": right},
        {"id": "i", "name": "insert", "sequence": INSERT, "preparation": "pcr"}],
        "circular": True, "overlap": overlap, "target_tm": 60.0, "min_anneal": 13}


def rotation(a, b):
    return len(a) == len(b) and a in b + b


@pytest.mark.asyncio
@pytest.mark.parametrize("overlap", [15, 20, 30, 40])
async def test_ecori_vector_design_counts_cohesive_end_once(tmp_path, overlap):
    top, left, right = ecori_vector()
    async with client_with_store(tmp_path) as (client, _):
        response = await client.post("/api/molbio/assembly/gibson/design", json=payload(overlap, top, left, right))
        assert response.status_code == 200, response.text
        body = response.json()
        product = body["selected_product"]["sequence"]
        assert len(product) == 906
        assert rotation(product, top + INSERT)
        primers = {p["direction"]: p for p in body["primers"]}
        assert primers["forward"]["tail_sequence"] == top[-overlap:] and top[-overlap:].endswith("ATGC")
        assert primers["reverse"]["tail_sequence"] == rc(top[:overlap]) and rc(top[:overlap]).endswith("AATT")
        # the cohesive end appears once in the vector region (no doubled AATT at the junction)
        assert (top + INSERT).count("AATT") == product.count("AATT") or rotation(product, top + INSERT)
        assert "AATTAATT" not in product + product[:8]
        assert not any("counted as homology" in w for w in body.get("warnings", []))


@pytest.mark.asyncio
async def test_ecori_vector_design_save_and_cold_reopen(tmp_path):
    top, left, right = ecori_vector()
    async with client_with_store(tmp_path) as (client, _):
        pl = payload(30, top, left, right)
        design = (await client.post("/api/molbio/assembly/gibson/design", json=pl)).json()
        saved = await client.post("/api/molbio/assembly/gibson/design/save", json={
            **pl, "computation_id": design["computation_id"],
            "selected_candidate_checksum": design["selected_candidate_checksum"], "new_name": "sticky"})
        assert saved.status_code == 200, saved.text
        sid = saved.json()["saved_sequence"]["id"]
        reopened = await client.get(f"/api/sequences/{sid}")
        assert reopened.status_code == 200
        assert len(reopened.json()["sequence"]) == 906 and rotation(reopened.json()["sequence"], top + INSERT)


@pytest.mark.parametrize("overlap", [15, 30])
def test_5prime_bamhi_vector_function_level(overlap):
    top = "GATC" + dna(43, 396) + "AGGC"
    left = FragmentEnd("sticky_5", "GATC", protruding_strand="top")
    right = FragmentEnd("sticky_5", "GATC", protruding_strand="bottom")
    tail_fwd, tail_rev = top[-overlap:], rc(top[:overlap])
    insert = tail_fwd + dna(44, 460) + rc(tail_rev)
    product = simulate_gibson(
        [AssemblyFragment(id="v", name="v", sequence=top, left_end=left, right_end=right),
         AssemblyFragment(id="i", name="i", sequence=insert)],
        circular=True, minimum_overlap=overlap, preferred_overlap=overlap)
    expected = top + insert[overlap:-overlap]
    assert len(product.sequence) == len(expected) and rotation(product.sequence, expected)
    assert not any("below preferred" in w for w in product.warnings)


def test_left_bottom_strand_case_is_unchanged_in_span():
    # left end bottom-strand 5' protrusion is outside the top span; nothing appended on the left
    top = dna(45, 400)
    left = FragmentEnd("sticky_5", "AATT", protruding_strand="bottom")
    insert = top[-20:] + dna(46, 300) + top[:20]
    product = simulate_gibson(
        [AssemblyFragment(id="v", name="v", sequence=top, left_end=left), AssemblyFragment(id="i", name="i", sequence=insert)],
        circular=True, minimum_overlap=15)
    assert rotation(product.sequence, top + insert[20:-20])


def test_3prime_kpni_style_flap_still_trimmed():
    # KpnI GGTAC^C: right top 3' overhang GTAC sits in the span and is an unpaired flap
    stem = dna(47, 396)
    top = stem + "GGTAC"
    right = FragmentEnd("sticky_3", "GTAC", protruding_strand="top")
    left = FragmentEnd("sticky_3", "GTAC", protruding_strand="bottom")
    kept = top[:-4]
    insert = kept[-20:] + dna(48, 300) + kept[:20]
    product = simulate_gibson(
        [AssemblyFragment(id="v", name="v", sequence=top, left_end=left, right_end=right),
         AssemblyFragment(id="i", name="i", sequence=insert)],
        circular=True, minimum_overlap=15)
    assert rotation(product.sequence, kept + insert[20:-20])


def test_preferred_warning_follows_requested_overlap_through_simulate_route_defaults():
    from routers.molbio_ops import GibsonAssemblyRequest, _requested_preferred_overlap
    frag = [{"id": "a", "name": "a", "sequence": "ACGT" * 10}]
    assert _requested_preferred_overlap(GibsonAssemblyRequest(fragments=frag, minimum_overlap=20)) == 20
    assert _requested_preferred_overlap(GibsonAssemblyRequest(fragments=frag, minimum_overlap=20, preferred_overlap=35)) == 35
    assert _requested_preferred_overlap(GibsonAssemblyRequest(fragments=frag)) == 28


@pytest.mark.asyncio
@pytest.mark.parametrize("overlap", [15, 20])
async def test_design_does_not_warn_below_preferred_for_ready_fragments_at_requested_overlap(tmp_path, overlap):
    a, b, c = dna(1, 400), dna(2, 300), dna(3, 400)
    parts = [a + b[:25], b + c[:25], c + a[:25]]
    pl = {"fragments": [{"id": str(i), "name": f"f{i}", "sequence": s, "preparation": "ready_linear"}
                        for i, s in enumerate(parts)],
          "circular": True, "overlap": overlap, "target_tm": 60.0, "min_anneal": 13}
    async with client_with_store(tmp_path) as (client, _):
        body = (await client.post("/api/molbio/assembly/gibson/design", json=pl)).json()
        assert not any("below preferred" in w for w in body.get("warnings", []))
