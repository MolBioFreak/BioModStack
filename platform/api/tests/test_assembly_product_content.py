"""Assembly products keep sources, features, primer sites and external ends (real routes, scratch SQLite)."""
from __future__ import annotations

import random
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, FeatureLocation
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from molbio_database import get_molbio_session
from molbio_models import MolBioBase, MolecularOperationInput, MolecularRevision, NucleotideSequence
from routers import molbio_ops, nucleotide_sequences
from services.assembly.physical_ends import read_physical_ends
from services.assembly.product_content import _primer_entry
from services.molbio_persistence import record_sequence_revision


def dna(seed: int, n: int) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(n))


@asynccontextmanager
async def client_with_store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'content.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(MolBioBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def dependency():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(molbio_ops.router)
    app.include_router(nucleotide_sequences.router)
    app.dependency_overrides[get_molbio_session] = dependency
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, sessions
    finally:
        await engine.dispose()


async def seed(sessions, sequence, *, circular=False, features=None, shelf=True, name="source"):
    """A saved document: a shelf row (shelf=True) or a retained child with revisions only."""
    row = NucleotideSequence(
        id=str(uuid.uuid4()), name=name, sequence=sequence, sequence_type="dna", is_circular=circular,
        length=len(sequence), features=features or [], primers=[], version=1)
    async with sessions.begin() as session:
        if shelf:
            session.add(row)
        await record_sequence_revision(session, row, change_kind="created")
    return row.id


def source_fragment(fid, source_id, sequence, start=None, end=None, wraps=False, orientation="forward", **extra):
    return dict(id=fid, name=fid, sequence=sequence, source_sequence_id=source_id, source_revision=1,
                source_start=start, source_end=end, source_wraps_origin=wraps, orientation=orientation,
                left_end={"type": "blunt"}, right_end={"type": "blunt"}, **extra)


def rc(sequence):
    return str(Seq(sequence).reverse_complement())


def extract(sequence, feature):
    """Independent Biopython oracle: bases of a stored feature in biological order."""
    strand = feature.get("strand", 1)
    parts = [FeatureLocation(s["start"], s["end"], strand=strand)
             for s in feature.get("segments") or [{"start": feature["start"], "end": feature["end"]}]]
    location = parts[0] if len(parts) == 1 else CompoundLocation(parts)
    return str(location.extract(Seq(sequence)))


def feature(name, start, end, strand=1, **extra):
    return {"id": name, "name": name, "type": "misc_feature", "start": start, "end": end, "strand": strand,
            "segments": [{"start": start, "end": end}], **extra}


def by_name(features, name):
    return next(f for f in features if f["name"] == name)


async def ligate(client, fragments, circular=False, route="ligation"):
    return await client.post(f"/api/molbio/assembly/{route}/save", json=dict(fragments=fragments, circular=circular))


# ── A3c: a retained child with no shelf row is a usable source ───────────────

@pytest.mark.asyncio
async def test_save_from_retained_child_without_shelf_row(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        source = dna(1, 80)
        child_id = await seed(sessions, source, shelf=False, name="digest child")
        async with sessions() as session:
            assert await session.get(NucleotideSequence, child_id) is None
        response = await ligate(client, [source_fragment("a", child_id, source[10:50], 10, 50)])
        assert response.status_code == 200, response.text
        saved = response.json()["saved_sequence"]
        assert saved["sequence"] == source[10:50]
        assert saved["parent_id"] is None
        async with sessions() as session:
            edges = (await session.execute(select(MolecularOperationInput).where(
                MolecularOperationInput.role == "fragment"))).scalars().all()
            child_revision = (await session.execute(select(MolecularRevision).where(
                MolecularRevision.document_id == child_id))).scalar_one()
            assert [edge.revision_id for edge in edges] == [child_revision.id]
        reopened = await client.get(f"/api/sequences/{saved['id']}")
        assert reopened.status_code == 200 and reopened.json()["sequence"] == source[10:50]


@pytest.mark.asyncio
async def test_single_shelf_parent_is_still_the_scalar_parent(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        source = dna(2, 60)
        shelf_id = await seed(sessions, source)
        response = await ligate(client, [source_fragment("a", shelf_id, source[:30], 0, 30)])
        assert response.status_code == 200, response.text
        assert response.json()["saved_sequence"]["parent_id"] == shelf_id


# ── A3d: single-cut circular linearization is a full-circle slice ────────────

@pytest.mark.asyncio
async def test_full_circle_slice_saves(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        source_id = await seed(sessions, "TTGAATTCAA", circular=True)
        fragment = source_fragment("a", source_id, "AATTCAATTG", 3, 3, wraps=True)
        preview = await client.post("/api/molbio/assembly/ligation/simulate", json=dict(fragments=[fragment], circular=False))
        assert preview.status_code == 200, preview.text
        response = await ligate(client, [fragment])
        assert response.status_code == 200, response.text
        assert response.json()["saved_sequence"]["sequence"] == "AATTCAATTG"


@pytest.mark.asyncio
async def test_invalid_wrapped_interval_is_still_rejected(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        source_id = await seed(sessions, "TTGAATTCAA", circular=True)
        # start < end cannot wrap the origin; its bases are not a wrapped slice.
        fragment = source_fragment("a", source_id, "GAATTC" + "TTGAA", 2, 5, wraps=True)
        response = await ligate(client, [fragment])
        assert response.status_code == 409
        assert "invalid wrapped source geometry" in response.text


# ── A6a-c: products carry mapped features at the right bases ─────────────────

@pytest.mark.asyncio
async def test_features_forward_and_reverse_fragments(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        first, second = dna(3, 90), dna(4, 90)
        a = await seed(sessions, first, features=[feature("fwd", 20, 35), feature("rev", 40, 55, -1)])
        b = await seed(sessions, second, features=[feature("tail", 60, 75)])
        fragments = [
            source_fragment("a", a, first[10:70], 10, 70),
            source_fragment("b", b, second[50:90], 50, 90, orientation="reverse"),
        ]
        response = await ligate(client, fragments)
        assert response.status_code == 200, response.text
        saved = response.json()["saved_sequence"]
        product, features = saved["sequence"], saved["features"]
        assert product == first[10:70] + rc(second[50:90])
        assert extract(product, by_name(features, "fwd")) == first[20:35]
        assert extract(product, by_name(features, "rev")) == extract(first, feature("rev", 40, 55, -1))
        tail = by_name(features, "tail")
        assert tail["strand"] == -1
        assert extract(product, tail) == second[60:75]


@pytest.mark.asyncio
async def test_origin_spanning_feature_is_one_run_on_a_wrapped_slice(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        plasmid = dna(5, 40)
        spanning = {"id": "span", "name": "span", "type": "misc_feature", "start": 0, "end": 40, "strand": 1,
                    "segments": [{"start": 36, "end": 40}, {"start": 0, "end": 3}]}
        source_id = await seed(sessions, plasmid, circular=True, features=[spanning])
        wrapped = plasmid[30:] + plasmid[:12]
        response = await ligate(client, [source_fragment("a", source_id, wrapped, 30, 12, wraps=True)])
        assert response.status_code == 200, response.text
        product = response.json()["saved_sequence"]
        mapped = by_name(product["features"], "span")
        assert extract(product["sequence"], mapped) == plasmid[36:] + plasmid[:3]
        assert mapped["segments"] == [{"start": 6, "end": 13}]


@pytest.mark.asyncio
async def test_clipped_cds_is_marked_truncated_without_stale_translation(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        source = dna(6, 90)
        cds = feature("orf", 10, 40, qualifiers={"codon_start": ["1"], "translation": ["MKLVAGHDTE"]})
        cds["type"] = "CDS"
        source_id = await seed(sessions, source, features=[cds])
        response = await ligate(client, [source_fragment("a", source_id, source[14:70], 14, 70)])
        assert response.status_code == 200, response.text
        mapped = by_name(response.json()["saved_sequence"]["features"], "orf")
        assert extract(response.json()["saved_sequence"]["sequence"], mapped) == source[14:40]
        assert mapped["provenance"]["mapping_status"] == "truncated"
        assert mapped["qualifiers"]["codon_start"] == ["3"]
        assert "translation" not in mapped["qualifiers"]


@pytest.mark.asyncio
async def test_inline_fragment_features_use_fragment_local_coordinates(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        left, right = dna(7, 40), dna(8, 40)
        fragments = [
            dict(id="a", name="a", sequence=left, features=[feature("mark", 5, 15)],
                 left_end={"type": "blunt"}, right_end={"type": "blunt"}),
            dict(id="b", name="b", sequence=rc(right), orientation="reverse", features=[feature("back", 20, 30)],
                 left_end={"type": "blunt"}, right_end={"type": "blunt"}),
        ]
        response = await ligate(client, fragments)
        assert response.status_code == 200, response.text
        saved = response.json()["saved_sequence"]
        # The reversed fragment's local top strand is rc(right); orienting it gives `right` back.
        assert saved["sequence"] == left + right
        assert extract(saved["sequence"], by_name(saved["features"], "mark")) == left[5:15]
        back = by_name(saved["features"], "back")
        assert extract(saved["sequence"], back) == extract(rc(right), feature("back", 20, 30))


@pytest.mark.asyncio
async def test_gibson_products_keep_features_across_the_removed_overlap(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        overlap = dna(9, 24)
        left, right = dna(10, 50) + overlap, overlap + dna(11, 50)
        a = await seed(sessions, left, features=[feature("one", 5, 20), feature("shared", 50, 74)])
        b = await seed(sessions, right, features=[feature("two", 40, 55)])
        fragments = [source_fragment("a", a, left), source_fragment("b", b, right)]
        response = await client.post("/api/molbio/assembly/gibson/save", json=dict(
            fragments=fragments, circular=False, minimum_overlap=20, preferred_overlap=24, maximum_overlap=24))
        assert response.status_code == 200, response.text
        saved = response.json()["saved_sequence"]
        product = saved["sequence"]
        assert product == left + right[24:]
        assert extract(product, by_name(saved["features"], "one")) == left[5:20]
        assert extract(product, by_name(saved["features"], "shared")) == overlap
        assert extract(product, by_name(saved["features"], "two")) == right[40:55]


# ── A6f: linear sticky ends survive Save and feed a second ligation ──────────

@pytest.mark.asyncio
async def test_linear_sticky_ends_survive_save_and_feed_a_second_ligation(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        def sticky(overhang):
            return {"type": "sticky_5", "overhang": overhang}
        body_a, body_b = dna(12, 20), dna(13, 20)
        first = [
            dict(id="a", name="a", sequence="GATC" + body_a, left_end=sticky("GATC"), right_end=sticky("AATT")),
            dict(id="b", name="b", sequence="AATT" + body_b, left_end=sticky("AATT"), right_end=sticky("GATC")),
        ]
        response = await ligate(client, first)
        assert response.status_code == 200, response.text
        saved = response.json()["saved_sequence"]
        async with sessions() as session:
            revision = (await session.execute(select(MolecularRevision).where(
                MolecularRevision.document_id == saved["id"]))).scalar_one()
        left, right = read_physical_ends(revision.provenance)
        assert revision.provenance["physical_ends"]["source"] == "assembly_product"
        assert (left.type, left.overhang, right.type, right.overhang) == ("sticky_5", "GATC", "sticky_5", "GATC")
        # The saved product recircularizes through its retained ends.
        second = [dict(id="p", name="p", sequence=saved["sequence"], source_sequence_id=saved["id"],
                       left_end={"type": left.type, "overhang": left.overhang},
                       right_end={"type": right.type, "overhang": right.overhang})]
        again = await client.post("/api/molbio/assembly/ligation/save", json=dict(fragments=second, circular=True))
        assert again.status_code == 200, again.text
        assert again.json()["saved_sequence"]["sequence"] == saved["sequence"]


@pytest.mark.asyncio
async def test_blunt_and_circular_products_write_no_physical_ends(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        response = await ligate(client, [dict(id="a", name="a", sequence=dna(14, 30),
                                              left_end={"type": "blunt"}, right_end={"type": "blunt"})])
        assert response.status_code == 200, response.text
        async with sessions() as session:
            revision = (await session.execute(select(MolecularRevision).where(
                MolecularRevision.document_id == response.json()["saved_sequence"]["id"]))).scalar_one()
        assert "physical_ends" not in revision.provenance


# ── PCR Save keeps template features and the two primer sites ────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("persist_experiment", [False, True])
async def test_pcr_save_carries_features_and_primer_sites(tmp_path, persist_experiment):
    async with client_with_store(tmp_path) as (client, sessions):
        template = dna(15, 240)
        template_id = await seed(sessions, template, features=[feature("insert", 70, 120), feature("outside", 5, 20)])
        forward = "GAATTC" + template[50:72]
        reverse = "GGATCC" + rc(template[150:172])
        response = await client.post("/api/molbio/pcr", json=dict(
            sequence_id=template_id, primer_fwd=forward, primer_rev=reverse, save=True,
            persist_experiment=persist_experiment, new_name="amplicon"))
        assert response.status_code == 200, response.text
        saved = response.json()["sequence"]
        amplicon = saved["sequence"]
        assert amplicon == forward + template[72:150] + rc(reverse)[:0] + rc(reverse)
        names = {f["name"] for f in saved["features"]}
        assert names == {"insert"}
        assert extract(amplicon, by_name(saved["features"], "insert")) == template[70:120]
        primers = {p["name"]: p for p in saved["primers"]}
        fwd, rev = primers["Forward primer"], primers["Reverse primer"]
        assert amplicon[fwd["start"]:fwd["end"]] == template[50:72] and fwd["strand"] == 1
        assert amplicon[rev["start"]:rev["end"]] == template[150:172] and rev["strand"] == -1
        assert fwd["sequence"] == forward and rev["sequence"] == reverse


def test_unplaced_primer_is_needs_review_not_zero_zero_alone():
    entry = _primer_entry("ACGTACGTACGT", key="p", name="p", full="TTTTGGGGCCCC", annealing="GGGGCCCC", tail="TTTT",
                          reverse=False, expected=4, tm=55.0, settings=None)
    assert entry["binding_status"] == "needs_review"
    assert (entry["start"], entry["end"], entry["sites"]) == (0, 0, [])
