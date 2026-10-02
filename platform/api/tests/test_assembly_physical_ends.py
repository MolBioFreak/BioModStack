"""Coordinate oracles for physical duplex rotation, not RC(top) assertions."""
from dataclasses import asdict, replace
import hashlib

import pytest
from Bio.Seq import Seq
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import molbio_ops
from services.assembly.common import orient_fragment, overhangs_compatible
from services.assembly.ligation import simulate_ligation
from services.assembly.types import AssemblyFragment, FragmentEnd
from services.restriction_catalog import catalog_authority
from services.restriction_digest import simulate_digest


def rc(sequence):
    return str(Seq(sequence).reverse_complement())


def digest(sequence, topology, enzyme):
    catalog = catalog_authority.require()
    receipt = {k: v for k, v in catalog_authority.readiness().items()
               if k not in {"required", "ready", "status"}}
    receipt["digest_enabled"] = True
    return simulate_digest(
        sequence=sequence, topology=topology, catalog=catalog,
        records=(catalog.by_id[enzyme],), selected_enzyme_ids=(enzyme,),
        source_receipt=dict(kind="inline_dna", name="coordinate fixture", sequence_id=None,
                            revision_id=None, revision_number=None,
                            content_sha256=hashlib.sha256(sequence.encode()).hexdigest(),
                            content_length=len(sequence), topology=topology),
        catalog_receipt=receipt,
    )


def fragment_input(fragment, orientation="forward"):
    def end(e):
        return FragmentEnd(
            type={"five_prime_overhang": "sticky_5", "three_prime_overhang": "sticky_3"}.get(e.kind, "blunt"),
            overhang=e.overhang_sequence_5to3 or "", protruding_strand=e.protruding_strand,
        )
    return AssemblyFragment(id=str(fragment.fragment_index), name="digest part",
                            sequence=fragment.top_strand_sequence, orientation=orientation,
                            left_end=end(fragment.left_end), right_end=end(fragment.right_end))


def span(sequence, start, stop):
    return "".join(sequence[i % len(sequence)] for i in range(start, stop))


@pytest.mark.parametrize("enzyme,source", [
    ("BsaI", "TTTTGGTCTCAAATGCCCCCCCC"),
    ("PstI", "AAAAGCTGCAGTCCGTAAAA"),
    ("EcoRV", "AAAAGGATATCTCCGTAAAA"),
    ("BsaI", "TTTTGGTCTCAAATGCCCCCCCCGGTCTCAGGAGTTTTTT"),
])
@pytest.mark.parametrize("topology,shift", [("linear", 0), ("circular", 0), ("circular", 8), ("circular", 13)])
@pytest.mark.parametrize("orientation", ["forward", "reverse"])
def test_real_digest_coordinates_http_roundtrip(enzyme, source, topology, shift, orientation):
    source = source[shift:] + source[:shift]
    products = digest(source, topology, enzyme).fragments
    inputs = [fragment_input(f, orientation) for f in products]
    # Independent bottom-strand coordinate oracle uses original source bases,
    # not assembly end conversion or its rotation helper.
    for physical, request in zip(products, inputs):
        expected = physical.top_strand_sequence if orientation == "forward" else rc(span(
            source, physical.bottom_start_boundary, physical.bottom_end_boundary))
        assert orient_fragment(request).sequence == expected
    if orientation == "reverse":
        inputs.reverse()
    expected = "".join(orient_fragment(f).sequence for f in inputs)
    whole = source if orientation == "forward" else rc(source)
    assert expected == whole if topology == "linear" else expected in whole + whole
    app = FastAPI()
    app.include_router(molbio_ops.router)
    payload = {"fragments": [asdict(f) for f in inputs], "circular": topology == "circular"}
    with TestClient(app) as client:
        response = client.post("/api/molbio/assembly/ligation/simulate", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["product"]["sequence"] == expected
        assert client.post("/api/molbio/assembly/ligation/simulate", json=payload).json() == response.json()
    assert len(expected) == len(source)


@pytest.mark.parametrize("left_delta,right_delta", [(4, 4), (-4, -4), (4, -4), (-4, 4), (0, 0), (0, 4), (-4, 0)])
def test_independent_asymmetric_linear_duplex_and_double_rotation(left_delta, right_delta):
    source = "ACGTAATGCCCCCGGAGTACCTGCA"
    top_start, top_end = 5, 18
    bottom_start, bottom_end = top_start + left_delta, top_end + right_delta
    def end(top, bottom, left):
        if top == bottom:
            return FragmentEnd("blunt")
        protrudes_top = (top < bottom) == left
        sequence = source[min(top, bottom):max(top, bottom)]
        return FragmentEnd("sticky_5" if top < bottom else "sticky_3",
                           sequence if protrudes_top else rc(sequence),
                           protruding_strand="top" if protrudes_top else "bottom")
    fragment = AssemblyFragment("x", "x", source[top_start:top_end], orientation="reverse",
                                left_end=end(top_start, bottom_start, True),
                                right_end=end(top_end, bottom_end, False))
    rotated = orient_fragment(fragment)
    assert rotated.sequence == rc(source[bottom_start:bottom_end])
    twice = orient_fragment(replace(fragment, sequence=rotated.sequence,
                                    left_end=rotated.left_end, right_end=rotated.right_end))
    assert twice.sequence == fragment.sequence


@pytest.mark.asyncio
@pytest.mark.parametrize("physical", [True, False])
@pytest.mark.parametrize("mode", ["ligation", "golden-gate"])
async def test_real_http_save_fresh_session_and_replay(tmp_path, physical, mode):
    import httpx
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from molbio_models import MolBioBase, NucleotideSequence
    from molbio_database import get_molbio_session

    source = "TTTTGGTCTCAAATGCCCCCCCC"
    fragments = [fragment_input(f, "reverse") for f in digest(source, "linear", "BsaI").fragments][::-1]
    if mode == "golden-gate":
        fragments = [AssemblyFragment(str(i), "prepared insert", sequence, orientation="reverse",
                     left_end=FragmentEnd("sticky_5", left, protruding_strand="top"),
                     right_end=FragmentEnd("sticky_5", right, protruding_strand="bottom"))
                     for i, (sequence, left, right) in enumerate([
                         ("GGAGTTTTT", "GGAG", "CATT"), ("AATGCCCCC", "AATG", "CTCC")])]
    if not physical:
        for fragment in fragments:
            fragment.left_end.protruding_strand = None
            fragment.right_end.protruding_strand = None
    payload: dict = dict(fragments=[asdict(f) for f in fragments], circular=False)
    if mode == "golden-gate":
        catalog = catalog_authority.require()
        payload.update(enzyme_id="BsaI", catalog_id=catalog.catalog_id,
                       expected_catalog_sha256=catalog.content_sha256)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'assembly.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(MolBioBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def session_dependency():
        async with sessions() as session:
            yield session
    app = FastAPI()
    app.include_router(molbio_ops.router)
    app.dependency_overrides[get_molbio_session] = session_dependency
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # Real persisted top-span sources also have to survive rotation;
            # the source-slice checker must use the same duplex owner.
            for fragment in payload["fragments"]:
                seed = await client.post("/api/molbio/assembly/ligation/save", json={
                    "fragments": [{**fragment, "orientation": "forward"}], "circular": False})
                assert seed.status_code == 200, seed.text
                fragment["source_sequence_id"] = seed.json()["saved_sequence"]["id"]
            preview = await client.post(f"/api/molbio/assembly/{mode}/simulate", json=payload)
            assert preview.status_code == 200, preview.text
            if physical:
                assert preview.json()["product"]["sequence"] == (rc(source) if mode == "ligation" else "CATTAAAAACTCCGGGGG")
            saved = await client.post(f"/api/molbio/assembly/{mode}/save", json=payload)
            assert saved.status_code == 200, saved.text
            async with sessions() as session:
                row = await session.get(NucleotideSequence, saved.json()["saved_sequence"]["id"])
                assert row.sequence == preview.json()["product"]["sequence"]
                request = row.operation_params["assembly_request"]
                assert request["fragments"] == molbio_ops.LigationAssemblyRequest.model_validate(payload).model_dump(mode="json")["fragments"]
                assert row.operation_params["fragments"][0]["right_end"]["protruding_strand"] == ("bottom" if physical else None)
            replay = await client.post(f"/api/molbio/assembly/{mode}/simulate", json=request)
            assert replay.status_code == 200, replay.text
            assert replay.json()["product"] == preview.json()["product"]
    finally:
        await engine.dispose()


def test_physical_vs_unknown_notation_and_legacy_reverse_replay():
    bottom = FragmentEnd("sticky_5", "CATT", protruding_strand="bottom")
    top = FragmentEnd("sticky_5", "AATG", protruding_strand="top")
    assert overhangs_compatible(bottom, top) == (True, [])
    assert not overhangs_compatible(bottom, replace(top, overhang="CATT"))[0]
    for overhang in ["CATT", "AATG"]:
        assert overhangs_compatible(replace(bottom, protruding_strand=None), FragmentEnd("sticky_5", overhang))[0]
    legacy = AssemblyFragment("old", "old", "CCCCCCCC", orientation="reverse",
                              left_end=FragmentEnd("sticky_5", "AATG"),
                              right_end=FragmentEnd("sticky_5", "AATG"))
    assert simulate_ligation([legacy], circular=True).sequence == "GGGGGGGG"
