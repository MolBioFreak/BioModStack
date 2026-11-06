"""5' phosphate state follows textbook product chemistry (label only; nothing here gates a join).

Expected values come from the chemistry, not from the code under test:
  restriction cut ends ........ 5'-P          PCR / synthetic oligos ...... 5'-OH (unmodified primers)
  fill-in / 3'->5' exo ........ 5' unchanged  mung bean / S1 on 5' overhang .. 5'-P
  T4 PNK / phosphatase ........ P / OH        uncut or pasted DNA .......... unknown
"""
import asyncio
import hashlib
import random

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import molbio_database
from molbio_models import MolBioBase, MolecularRevision
from routers import molbio_end_operations, molbio_ops
from services.assembly.end_operations import apply_end_operation
from services.assembly.ligation import simulate_ligation
from services.assembly.physical_ends import read_physical_ends, record_to_end
from services.assembly.types import AssemblyFragment, FragmentEnd
from services.restriction_catalog import catalog_authority
from services.restriction_digest import simulate_digest
from services.assembly.physical_ends import digest_end_to_fragment_end


def sticky(strand, phosphate="unknown"):
    return FragmentEnd(type="sticky_5", overhang="GATC", protruding_strand=strand, five_prime_phosphate=phosphate)


def phosphates(result):
    return result.left_end.five_prime_phosphate, result.right_end.five_prime_phosphate


# ── end operations ──────────────────────────────────────────────────────────────
# fragment with a 5' overhang on both ends, phosphate state stated per end
SEQ = "GATCAAAACCCCGGGGTTTT"  # top strand: left 5' overhang GATC (top), right 5' overhang GATC (bottom)


def fragment(left, right):
    return SEQ + "", left, right


@pytest.mark.parametrize("state", ["phosphorylated", "unphosphorylated", "unknown"])
def test_fill_in_leaves_the_5_prime_terminus_alone(state):
    done = apply_end_operation(SEQ, sticky("top", state), sticky("bottom", state), operation="fill_in", end="both")
    assert phosphates(done) == (state, state)


@pytest.mark.parametrize("state", ["phosphorylated", "unphosphorylated", "unknown"])
def test_three_prime_exonuclease_leaves_the_5_prime_terminus_alone(state):
    left = FragmentEnd(type="sticky_3", overhang="GATC", protruding_strand="bottom", five_prime_phosphate=state)
    right = FragmentEnd(type="sticky_3", overhang="GATC", protruding_strand="top", five_prime_phosphate=state)
    done = apply_end_operation(SEQ, left, right, operation="remove_3_overhang", end="both")
    assert phosphates(done) == (state, state)


@pytest.mark.parametrize("state", ["phosphorylated", "unphosphorylated", "unknown"])
def test_mung_bean_on_a_5_prime_overhang_leaves_a_phosphorylated_terminus(state):
    done = apply_end_operation(SEQ, sticky("top", state), sticky("bottom", state),
                               operation="remove_5_overhang_nuclease", end="both")
    assert phosphates(done) == ("phosphorylated", "phosphorylated")


def test_nuclease_on_an_end_it_does_not_describe_changes_nothing():
    blunt = FragmentEnd(type="blunt", five_prime_phosphate="unphosphorylated")
    done = apply_end_operation(SEQ, blunt, blunt, operation="remove_5_overhang_nuclease", end="both")
    assert phosphates(done) == ("unphosphorylated", "unphosphorylated")


def test_kinase_and_phosphatase_set_only_the_selected_end():
    left, right = sticky("top", "unphosphorylated"), sticky("bottom", "unphosphorylated")
    done = apply_end_operation(SEQ, left, right, operation="phosphorylate", end="left")
    assert phosphates(done) == ("phosphorylated", "unphosphorylated")
    again = apply_end_operation(SEQ, done.left_end, done.right_end, operation="dephosphorylate", end="left")
    assert phosphates(again) == ("unphosphorylated", "unphosphorylated")


# ── restriction digest ends ─────────────────────────────────────────────────────
def digest(sequence, enzyme, topology="linear"):
    catalog = catalog_authority.require()
    receipt = {k: v for k, v in catalog_authority.readiness().items() if k not in {"required", "ready", "status"}}
    receipt["digest_enabled"] = True
    return simulate_digest(
        sequence=sequence, topology=topology, catalog=catalog, records=(catalog.by_id[enzyme],),
        selected_enzyme_ids=(enzyme,),
        source_receipt={"kind": "inline_dna", "name": "x", "sequence_id": None, "revision_id": None,
                        "revision_number": None, "content_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
                        "content_length": len(sequence), "topology": topology},
        catalog_receipt=receipt)


def test_cut_ends_are_phosphorylated_and_the_uncut_ends_stay_unknown():
    first, middle, last = digest("TTTTTTTTGAATTCAAAAAAAAAAGAATTCCCCCCCC", "EcoRI").fragments
    ends = lambda f: tuple(digest_end_to_fragment_end(e).five_prime_phosphate for e in (f.left_end, f.right_end))
    assert ends(first) == ("unknown", "phosphorylated")
    assert ends(middle) == ("phosphorylated", "phosphorylated")
    assert ends(last) == ("phosphorylated", "unknown")


def test_saved_digest_geometry_reads_back_with_the_cut_end_rule():
    geometry = {"geometry": {
        "left_end": {"kind": "five_prime_overhang", "overhang_sequence_5to3": "AATT", "protruding_strand": "top",
                     "enzyme_created": True},
        "right_end": {"kind": "natural", "overhang_sequence_5to3": None, "protruding_strand": None,
                      "enzyme_created": False}}}
    left, right = read_physical_ends(geometry)
    assert (left.five_prime_phosphate, right.five_prime_phosphate) == ("phosphorylated", "unknown")
    explicit = {"geometry": {"left_end": {**geometry["geometry"]["left_end"], "five_prime_phosphate": "unphosphorylated"},
                             "right_end": geometry["geometry"]["right_end"]}}
    assert read_physical_ends(explicit)[0].five_prime_phosphate == "unphosphorylated"


# ── a junction's seal follows from the two product rules, and never gates the join ──
def test_cut_vector_with_a_pcr_insert_reads_nicked_and_joins_all_the_same():
    vector = AssemblyFragment(id="v", name="vector", sequence="GATC" + "A" * 20, left_end=sticky("top", "phosphorylated"),
                              right_end=FragmentEnd(type="blunt", five_prime_phosphate="phosphorylated"))
    insert = AssemblyFragment(id="i", name="PCR insert", sequence="C" * 20,
                              left_end=FragmentEnd(type="blunt", five_prime_phosphate="unphosphorylated"),
                              right_end=FragmentEnd(type="blunt", five_prime_phosphate="unphosphorylated"))
    product = simulate_ligation([vector, insert], circular=False)
    assert product.junctions[0].sealed == "one_strand" and "joined, nicked" in product.junctions[0].notes
    assert product.sequence == vector.sequence + insert.sequence


# ── routes: PCR product, Gibson amplicon, saved children ───────────────────────
@pytest.fixture
def client_and_sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/molbio.db")

    async def init():
        async with engine.begin() as connection:
            await connection.run_sync(MolBioBase.metadata.create_all)

    asyncio.run(init())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(molbio_end_operations.router)
    app.include_router(molbio_ops.router)

    async def override():
        async with maker() as session:
            yield session

    app.dependency_overrides[molbio_database.get_molbio_session] = override
    app.dependency_overrides[molbio_ops.get_molbio_session] = override
    with TestClient(app) as client:
        yield client, maker
    asyncio.run(engine.dispose())


def revisions(maker, document_id):
    async def read():
        async with maker() as session:
            return list((await session.execute(select(MolecularRevision).where(
                MolecularRevision.document_id == document_id))).scalars().all())
    return asyncio.run(read())


_rng = random.Random(15)
TEMPLATE = "".join(_rng.choice("ACGT") for _ in range(240))  # no repeats long enough to make primers ambiguous
COMP = str.maketrans("ACGT", "TGCA")


def test_pcr_product_is_blunt_with_5_prime_hydroxyl_ends(client_and_sessions):
    client, maker = client_and_sessions
    forward = TEMPLATE[50:72]
    reverse = TEMPLATE[150:172].translate(COMP)[::-1]
    response = client.post("/api/molbio/pcr", json=dict(sequence=TEMPLATE, sequence_type="dna", primer_fwd=forward,
                                                       primer_rev=reverse, save=True, new_name="amplicon"))
    assert response.status_code == 200, response.text
    for revision in revisions(maker, response.json()["sequence"]["id"]):
        left, right = read_physical_ends(revision.provenance)
        assert (left.type, left.five_prime_phosphate) == ("blunt", "unphosphorylated")
        assert (right.type, right.five_prime_phosphate) == ("blunt", "unphosphorylated")


def test_pcr_save_with_a_persisted_experiment_carries_the_same_ends(client_and_sessions):
    client, maker = client_and_sessions
    forward = TEMPLATE[50:72]
    reverse = TEMPLATE[150:172].translate(COMP)[::-1]
    response = client.post("/api/molbio/pcr", json=dict(sequence=TEMPLATE, sequence_type="dna", primer_fwd=forward,
                                                       primer_rev=reverse, save=True, persist_experiment=True))
    assert response.status_code == 200, response.text
    revision = revisions(maker, response.json()["sequence"]["id"])[0]
    assert read_physical_ends(revision.provenance)[0].five_prime_phosphate == "unphosphorylated"


def test_the_pcr_request_hash_does_not_depend_on_the_ends_label(client_and_sessions):
    """The ends label sits on the product revision only, so replaying a request still dedupes."""
    client, _ = client_and_sessions
    body = dict(sequence=TEMPLATE, sequence_type="dna", primer_fwd=TEMPLATE[50:72],
                primer_rev=TEMPLATE[150:172].translate(COMP)[::-1], save=True, persist_experiment=True,
                idempotency_key="phos-replay")
    first = client.post("/api/molbio/pcr", json=body)
    second = client.post("/api/molbio/pcr", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["operation_id"] == second.json()["operation_id"]


def test_ends_from_a_cut_then_dephosphorylated_vector_survive_a_save_and_reopen(client_and_sessions):
    client, maker = client_and_sessions
    inline = {"kind": "inline", "name": "cut vector", "sequence": SEQ, "features": [],
              "left_end": {"type": "sticky_5", "overhang": "GATC", "protruding_strand": "top",
                           "five_prime_phosphate": "phosphorylated"},
              "right_end": {"type": "sticky_5", "overhang": "GATC", "protruding_strand": "bottom",
                            "five_prime_phosphate": "phosphorylated"}}
    cip = client.post("/api/molbio/end-operations/apply",
                      json={"source": inline, "operation": "dephosphorylate", "end": "both", "new_name": "CIP vector"})
    assert cip.status_code == 200, cip.text
    assert cip.json()["left_end"]["five_prime_phosphate"] == "unphosphorylated"
    saved = revisions(maker, cip.json()["document_id"])[0]
    left, right = read_physical_ends(saved.provenance)
    assert (left.five_prime_phosphate, right.five_prime_phosphate) == ("unphosphorylated", "unphosphorylated")


# ── Golden Gate retained intermediates and Gibson designed amplicons ───────────
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["pcr", "synthesis"])
async def test_golden_gate_preparations_are_5_prime_hydroxyl_and_digest_pieces_are_phosphorylated(tmp_path, kind):
    from test_golden_gate_workflow_receiving import client_store
    from test_golden_gate_design_core import request
    from services.assembly.golden_gate_workflow import freeze_selection, run_workflow
    from services.assembly.golden_gate_workflow_types import REQUEST_ADAPTER, SaveDesignRequest
    result = run_workflow(REQUEST_ADAPTER.validate_python(request(kind=kind).model_dump(mode="json")))
    async with client_store(tmp_path) as (client, sessions):
        saved = await client.post("/api/molbio/assembly/golden-gate/design/save", json=SaveDesignRequest(
            selection=freeze_selection(result, "fixed"), name="GG", idempotency_key="phos").model_dump(mode="json"))
        assert saved.status_code == 200, saved.text
        async with sessions() as session:
            rows = list((await session.scalars(select(MolecularRevision).where(
                MolecularRevision.operation_id == saved.json()["operation_id"]))).all())
    by = {}
    for row in rows:
        by.setdefault(row.provenance.get("transformation"), []).append(read_physical_ends(row.provenance))
    for left, right in by[kind]:
        assert (left.type, left.five_prime_phosphate, right.type, right.five_prime_phosphate) == \
            ("blunt", "unphosphorylated", "blunt", "unphosphorylated")
    for left, right in by["digest"]:
        assert left.five_prime_phosphate in ("phosphorylated", "unknown") and right.five_prime_phosphate in ("phosphorylated", "unknown")
    assert any("phosphorylated" in (left.five_prime_phosphate, right.five_prime_phosphate) for left, right in by["digest"])


def test_gibson_designed_pcr_amplicon_is_5_prime_hydroxyl(client_and_sessions):
    client, maker = client_and_sessions
    rng = random.Random(23)
    dna = lambda n: "".join(rng.choice("ACGT") for _ in range(n))
    backbone, cassette = dna(1200), dna(500)
    payload = {"fragments": [{"id": "bb", "name": "backbone", "sequence": backbone, "preparation": "pcr"},
                             {"id": "cas", "name": "cassette", "sequence": cassette, "preparation": "ready_linear"}],
               "circular": True, "overlap": 30, "target_tm": 60.0, "min_anneal": 13}
    design = client.post("/api/molbio/assembly/gibson/design", json=payload)
    assert design.status_code == 200, design.text
    saved = client.post("/api/molbio/assembly/gibson/design/save", json={
        **payload, "computation_id": design.json()["computation_id"],
        "selected_candidate_checksum": design.json()["selected_candidate_checksum"], "new_name": "Swap"})
    assert saved.status_code == 200, saved.text

    async def read():
        from molbio_models import MolecularOperationOutput
        async with maker() as session:
            outs = (await session.execute(select(MolecularOperationOutput).where(
                MolecularOperationOutput.role == "designed_fragment"))).scalars().all()
            return [await session.get(MolecularRevision, o.revision_id) for o in outs]

    revs = asyncio.run(read())
    assert len(revs) == 1  # only the PCR backbone is an amplicon; the synthetic cassette is an order sequence
    left, right = read_physical_ends(revs[0].provenance)
    assert (left.five_prime_phosphate, right.five_prime_phosphate) == ("unphosphorylated", "unphosphorylated")
    assert revs[0].provenance["physical_ends"]["source"] == "pcr_product"
