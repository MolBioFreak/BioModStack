from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from molbio_database import create_molbio_engine, get_molbio_session, init_molbio_db, make_molbio_session_factory  # noqa: E402
from molbio_models import MolecularOperationOutput, MolecularRevision, NucleotideSequence  # noqa: E402
from routers.molbio_ops import router as molbio_router  # noqa: E402
from services.assembly.gibson import simulate_gibson  # noqa: E402
from services.assembly.pydna_gibson import design_gibson  # noqa: E402
from services.assembly.types import AssemblyFragment, FragmentEnd  # noqa: E402
from services.molbio_ops import reverse_complement  # noqa: E402


def dna(seed: int, n: int) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(n))


A, M, B = dna(1, 900), dna(2, 600), dna(3, 500)
CORE = dna(4, 800)
CASSETTE = A[-30:] + CORE + B[:30]
BACKBONE = B + A
EXPECTED = A + CORE + B  # circular


def _same_circle(left: str, right: str) -> bool:
    return len(left) == len(right) and left in right + right


def test_a1b_pcr_backbone_plus_preoverlapped_cassette_exact_product() -> None:
    result = design_gibson(
        [AssemblyFragment(id="bb", name="backbone", sequence=BACKBONE),
         AssemblyFragment(id="cas", name="cassette", sequence=CASSETTE)],
        preparations=["pcr", "ready_linear"], circular=True, overlap=30, min_anneal=13)
    assert _same_circle(result.candidates[0].sequence, EXPECTED)
    assert all(p.tail_sequence == "" for p in result.primers)  # no second copy of homology
    assert next(d for d in result.designed_fragments if d.id == "cas").sequence == CASSETTE


def test_a1b_reversed_order_and_untailed_junction_gets_tail_only_where_missing() -> None:
    # Cassette overlaps backbone on its left only; the right junction lacks homology.
    cassette = A[-30:] + CORE
    backbone = B + A
    result = design_gibson(
        [AssemblyFragment(id="cas", name="cassette", sequence=cassette),
         AssemblyFragment(id="bb", name="backbone", sequence=backbone)],
        preparations=["ready_linear", "pcr"], circular=True, overlap=30, min_anneal=13)
    expected = A + CORE + B
    assert _same_circle(result.candidates[0].sequence, expected)
    tails = {p.id: p.tail_sequence for p in result.primers}
    # cassette->backbone lacks homology (forward tail); backbone->cassette has it.
    assert tails["bb:forward"] != "" and tails["bb:reverse"] == ""


def test_a1d_single_fragment_closure_circularizes() -> None:
    body = dna(5, 400)
    linear = body + dna(6, 30)[:0] + body[:30]  # ends carry 30 nt homology
    product = simulate_gibson([AssemblyFragment(id="f", name="f", sequence=linear)],
                              circular=True, minimum_overlap=20)
    assert product.sequence == body and product.circular
    result = design_gibson([AssemblyFragment(id="f", name="f", sequence=linear)],
                           preparations=["pcr"], circular=True, overlap=30, min_anneal=13)
    assert _same_circle(result.candidates[0].sequence, body)
    assert result.primers[0].tail_sequence == "" and result.primers[1].tail_sequence == ""


def test_a1d_single_fragment_closure_design_tails_reproduce_opposite_end() -> None:
    body = dna(7, 500)
    result = design_gibson([AssemblyFragment(id="f", name="f", sequence=body)],
                           preparations=["pcr"], circular=True, overlap=30, min_anneal=13)
    forward, reverse = result.primers
    assert forward.tail_sequence == body[-15:]
    assert reverse.tail_sequence == reverse_complement(body[:15])
    assert _same_circle(result.candidates[0].sequence, body)


def test_a1d_short_insert_is_built() -> None:
    insert = dna(8, 25)
    result = design_gibson(
        [AssemblyFragment(id="a", name="a", sequence=dna(9, 300)),
         AssemblyFragment(id="ins", name="insert", sequence=insert),
         AssemblyFragment(id="b", name="b", sequence=dna(10, 300))],
        preparations=["pcr", "ready_linear", "pcr"], circular=False, overlap=24, min_anneal=13)
    assert result.candidates[0].sequence == dna(9, 300) + insert + dna(10, 300)


def test_digest_linearized_vector_uses_five_prime_protrusion_not_three_prime_flap() -> None:
    # EcoRI-like 5' AATT on the bottom strand at the right end: counted as homology.
    insert_head = "GAATTC" + dna(11, 60)
    vector_top = dna(12, 200)
    protrusion = "AATT"
    vector = AssemblyFragment(
        id="v", name="vector", sequence=vector_top,
        right_end=FragmentEnd(type="sticky_5", overhang=protrusion, protruding_strand="bottom"))
    # Homology (>= 12 nt) must be the vector's single-stranded end + the insert head.
    ss = vector_top[-8:] + reverse_complement(protrusion)
    insert = AssemblyFragment(id="i", name="insert", sequence=ss + dna(13, 200))
    product = simulate_gibson([vector, insert], circular=False, minimum_overlap=12)
    assert product.sequence == vector_top + reverse_complement(protrusion) + insert.sequence[len(ss):]
    assert "counted as homology" in " ".join(product.warnings)
    # A 3' protrusion on the top strand is a flap: trimmed, not counted.
    flap = AssemblyFragment(id="v", name="vector", sequence=vector_top + "GGCC",
                            right_end=FragmentEnd(type="sticky_3", overhang="GGCC", protruding_strand="top"))
    other = AssemblyFragment(id="i", name="insert", sequence=vector_top[-20:] + dna(14, 100))
    flap_product = simulate_gibson([flap, other], circular=False, minimum_overlap=12)
    assert flap_product.sequence == vector_top + other.sequence[20:]
    assert "unpaired flap" in " ".join(flap_product.warnings)


def test_tm_model_and_overlap_tm_are_labelled() -> None:
    result = design_gibson(
        [AssemblyFragment(id="bb", name="backbone", sequence=BACKBONE),
         AssemblyFragment(id="cas", name="cassette", sequence=CASSETTE)],
        preparations=["pcr", "ready_linear"], circular=True, overlap=30, min_anneal=13)
    for primer in result.primers:
        assert "Tm_NN" in primer.tm_model and primer.tm_assumptions["Mg_mM"] == 1.5
    for junction in result.candidates[0].junctions:
        assert junction.overlap_tm is not None and junction.overlap_tm_2_4 is not None
        assert junction.overlap_tm_2_4 == sum(4 if b in "GC" else 2 for b in junction.overlap_sequence)


def test_primer_sites_on_template_and_product() -> None:
    result = design_gibson(
        [AssemblyFragment(id="bb", name="backbone", sequence=BACKBONE),
         AssemblyFragment(id="cas", name="cassette", sequence=CASSETTE)],
        preparations=["pcr", "ready_linear"], circular=True, overlap=30, min_anneal=13)
    product = result.candidates[0].sequence
    for primer in result.primers:
        site = primer.product_site
        assert site is not None and primer.template_site is not None
        piece = product[site["start"]:site["end"]]
        assert piece == (primer.annealing_sequence if site["strand"] == 1 else reverse_complement(primer.annealing_sequence))


@pytest.fixture
def app_and_sessions(tmp_path: Path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'l3.db'}")
    asyncio.run(init_molbio_db(engine=engine))
    sessions = make_molbio_session_factory(engine)

    async def override():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(molbio_router)
    from routers.nucleotide_sequences import router as sequence_router
    app.include_router(sequence_router)
    app.dependency_overrides[get_molbio_session] = override
    try:
        yield app, sessions
    finally:
        asyncio.run(engine.dispose())


def test_save_maps_primer_sites_stores_children_and_restore_data(app_and_sessions) -> None:
    app, sessions = app_and_sessions
    payload = {
        "fragments": [
            {"id": "bb", "name": "backbone", "sequence": BACKBONE, "preparation": "pcr"},
            {"id": "cas", "name": "cassette", "sequence": CASSETTE, "preparation": "ready_linear"},
        ],
        "circular": True, "overlap": 30, "target_tm": 60.0, "min_anneal": 13,
    }
    with TestClient(app) as client:
        design = client.post("/api/molbio/assembly/gibson/design", json=payload)
        assert design.status_code == 200, design.text
        body = design.json()
        assert body["primers"][0]["tm_model"] and body["primers"][0]["product_site"]
        saved = client.post("/api/molbio/assembly/gibson/design/save", json={
            **payload, "computation_id": body["computation_id"],
            "selected_candidate_checksum": body["selected_candidate_checksum"], "new_name": "Swap"})
        assert saved.status_code == 200, saved.text
        sequence = saved.json()["saved_sequence"]
        primers = sequence["primers"]
        assert all(p["end"] > p["start"] for p in primers)
        params = sequence["operation_params"]
        assert params["design_request"]["fragments"][0]["preparation"] == "pcr"
        assert params["design_request"]["overlap"] == 30

    async def read():
        async with sessions() as session:
            outs = (await session.execute(select(MolecularOperationOutput).where(
                MolecularOperationOutput.role == "designed_fragment"))).scalars().all()
            revs = [await session.get(MolecularRevision, o.revision_id) for o in outs]
            shelf = [await session.get(NucleotideSequence, o.snapshot["document_id"]) for o in outs]
            return outs, revs, shelf

    outs, revs, shelf = asyncio.run(read())
    assert len(outs) == 1  # only the PCR backbone is a designed amplicon
    assert revs[0].snapshot["sequence"] == next(
        d for d in design.json()["designed_fragments"] if d["id"] == "bb")["sequence"]
    assert revs[0].provenance["physical_ends"]["left_end"]["kind"] == "blunt"
    assert shelf == [None]


def test_a1f_more_than_ten_pieces_still_saves(app_and_sessions) -> None:
    app, _ = app_and_sessions
    payload = {"target_sequence": dna(23, 3000), "circular": False, "min_fragment_length": 100,
               "max_fragment_length": 200, "overlap_length": 30, "vendor_name": "Generic",
               "price_per_bp": 0.1, "lead_time_days": 5}
    with TestClient(app) as client:
        plan = client.post("/api/molbio/assembly/gibson/dnaweaver/plan", json=payload)
        assert plan.status_code == 200, plan.text
        body = plan.json()
        assert len(body["ordered_fragments"]) > 10
        assert body["order_ready"] is False
        assert any(c["status"] == "blocker" for c in body["quality_checks"])
        saved = client.post("/api/molbio/assembly/gibson/dnaweaver/save", json={
            **payload, "computation_id": body["computation_id"],
            "selected_plan_checksum": body["plan_checksum"], "new_name": "Many pieces"})
        assert saved.status_code == 200, saved.text
        assert saved.json()["saved_sequence"]["sequence"] == payload["target_sequence"]
