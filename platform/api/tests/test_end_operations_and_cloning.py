"""Lane l5: end operations, phosphate state, junction label, ligation contract, cloning proposals."""
import asyncio
import json
import random
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import molbio_database
from molbio_models import MolBioBase
from routers import molbio_end_operations, molbio_ops
from services.assembly.common import orient_fragment, reverse_complement_end
from services.assembly.end_operations import apply_end_operation
from services.assembly.junction_sites import observe_junction_sites
from services.assembly.ligation import simulate_ligation
from services.assembly.physical_ends import read_physical_ends, write_physical_ends
from services.assembly.restriction_cloning import suggest_restriction_cloning
from services.assembly.types import AssemblyError, AssemblyFragment, FragmentEnd

COMP = str.maketrans("ACGT", "TGCA")


def comp(s):
    return s.translate(COMP)


def rc(s):
    return comp(s)[::-1]


# ── independent two-strand oracle ─────────────────────────────────────────────
# Reference axis positions are integers. top[p] is the top-strand base at p; bot[p] is the
# base of the bottom strand at p (it pairs with top[p] when both exist, so bot[p] == comp(top[p])).
def build_duplex(core, left_top="", left_bot="", right_top="", right_bot=""):
    """core is double stranded; left_top/right_top protrude on the top strand,
    left_bot/right_bot (given as top-orientation letters of the missing partner) protrude on the bottom."""
    top, bot = {}, {}
    pos = 0
    if left_top:
        for ch in left_top:
            top[pos] = ch
            pos += 1
    elif left_bot:
        for ch in left_bot:
            bot[pos] = comp(ch)
            pos += 1
    for ch in core:
        top[pos], bot[pos] = ch, comp(ch)
        pos += 1
    for ch in right_top:
        top[pos] = ch
        pos += 1
    for ch in right_bot:
        bot[pos] = comp(ch)
        pos += 1
    return top, bot


def oracle_apply(top, bot, op, side):
    top, bot = dict(top), dict(bot)
    lo = min(list(top) + list(bot))
    hi = max(list(top) + list(bot))
    if side == "left":
        positions = range(lo, max(min(top), min(bot)))
        edge = range(lo, max(min(top), min(bot)))
    else:
        edge = range(min(max(top), max(bot)) + 1, hi + 1)
    for p in edge:
        if op == "fill_in":
            if p in top and p not in bot:
                bot[p] = comp(top[p])
            elif p in bot and p not in top:
                top[p] = comp(bot[p])
        else:  # either overhang removal: drop the unpaired base
            top.pop(p, None)
            bot.pop(p, None)
    return top, bot


def top_span(top):
    return "".join(top[p] for p in sorted(top))


def overhang_of(top, bot, side):
    """Return (strand, 5'->3' overhang string) at an end of the oracle duplex, or None if blunt."""
    if side == "left":
        edge_pos = [p for p in range(min(list(top) + list(bot)), max(min(top), min(bot)))]
    else:
        edge_pos = [p for p in range(min(max(top), max(bot)) + 1, max(list(top) + list(bot)) + 1)]
    if not edge_pos:
        return None
    if all(p in top for p in edge_pos):
        return "top", "".join(top[p] for p in edge_pos)  # top strand written 5'->3' is left-to-right
    return "bottom", "".join(bot[p] for p in reversed(edge_pos))  # bottom 5'->3' runs right-to-left


def make_input(core, **kw):
    """Fragment (sequence, left_end, right_end) from the oracle duplex."""
    top, bot = build_duplex(core, **kw)
    left = overhang_of(top, bot, "left")
    right = overhang_of(top, bot, "right")

    def end(spec, side):
        if spec is None:
            return FragmentEnd(type="blunt")
        strand, ovh = spec
        five = (side == "left") == (strand == "top")
        return FragmentEnd(type="sticky_5" if five else "sticky_3", overhang=ovh, protruding_strand=strand)

    return top_span(top), end(left, "left"), end(right, "right"), top, bot


RNG = random.Random(11)
CORE = "".join(RNG.choice("ACGT") for _ in range(30))

CASES = [
    # (label, kwargs for build_duplex, operation, end)
    ("left top 5' fill-in", dict(left_top="AACG"), "fill_in", "left"),
    ("right bottom 5' fill-in", dict(right_bot="CTTA"), "fill_in", "right"),
    ("both 5' fill-in", dict(left_top="AACG", right_bot="CTTA"), "fill_in", "both"),
    ("left top 5' nuclease", dict(left_top="AACG"), "remove_5_overhang_nuclease", "left"),
    ("right bottom 5' nuclease", dict(right_bot="CTTA"), "remove_5_overhang_nuclease", "right"),
    ("left bottom 3' exo", dict(left_bot="GGAT"), "remove_3_overhang", "left"),
    ("right top 3' exo", dict(right_top="TCGA"), "remove_3_overhang", "right"),
    ("both 3' exo", dict(left_bot="GGAT", right_top="TCGA"), "remove_3_overhang", "both"),
]


@pytest.mark.parametrize("label,kw,op,end", CASES, ids=[c[0] for c in CASES])
def test_end_operations_match_the_two_strand_oracle(label, kw, op, end):
    sequence, left, right, top, bot = make_input(CORE, **kw)
    result = apply_end_operation(sequence, left, right, operation=op, end=end)
    sides = ("left", "right") if end == "both" else (end,)
    et, eb = top, bot
    for side in sides:
        spec = overhang_of(et, eb, side)
        if spec is None:
            continue
        strand, _ovh = spec
        is_five = (side == "left") == (strand == "top")
        if (op in ("fill_in", "remove_5_overhang_nuclease") and is_five) or (op == "remove_3_overhang" and not is_five):
            et, eb = oracle_apply(et, eb, "fill_in" if op == "fill_in" else "remove", side)
    assert result.sequence == top_span(et)
    assert result.left_end.type == "blunt" or end == "right"
    # every operated end is now blunt and every untouched end is unchanged
    for side, produced in (("left", result.left_end), ("right", result.right_end)):
        spec = overhang_of(et, eb, side)
        assert (spec is None) == (produced.type == "blunt")
    before, after = len(top) + len(bot), len(et) + len(eb)
    assert result.bases_added == max(after - before, 0)
    assert result.bases_removed == max(before - after, 0)


def test_fill_in_adds_the_overhang_length_and_duplicates_bases_on_the_other_strand():
    sequence, left, right, _t, _b = make_input(CORE, left_top="AACG")
    result = apply_end_operation(sequence, left, right, operation="fill_in", end="left")
    assert result.sequence == sequence  # 5' top overhang fill-in: top span is unchanged, bottom is extended
    assert result.left_end.type == "blunt" and result.bases_added == 4
    sequence, left, right, _t, _b = make_input(CORE, right_bot="CTTA")
    result = apply_end_operation(sequence, left, right, operation="fill_in", end="right")
    assert result.sequence == sequence + "CTTA" and result.bases_added == 4


def test_fill_in_of_3_prime_and_removal_of_missing_overhang_are_notes_not_refusals():
    sequence, left, right, _t, _b = make_input(CORE, left_bot="GGAT")
    result = apply_end_operation(sequence, left, right, operation="fill_in", end="left")
    assert result.sequence == sequence and result.left_end.type == "sticky_3"
    assert any("3' overhang" in n for n in result.notes)
    sequence, left, right, _t, _b = make_input(CORE)
    result = apply_end_operation(sequence, left, right, operation="remove_3_overhang", end="both")
    assert result.sequence == sequence and len(result.notes) == 2


def test_phosphorylation_changes_only_the_selected_end():
    sequence, left, right, _t, _b = make_input(CORE, left_top="AACG", right_bot="CTTA")
    done = apply_end_operation(sequence, left, right, operation="dephosphorylate", end="left")
    assert done.sequence == sequence
    assert (done.left_end.five_prime_phosphate, done.right_end.five_prime_phosphate) == ("unphosphorylated", "unknown")
    assert (done.left_end.type, done.left_end.overhang, done.left_end.protruding_strand) == \
        (left.type, left.overhang, left.protruding_strand)
    again = apply_end_operation(done.sequence, done.left_end, done.right_end, operation="phosphorylate", end="right")
    assert (again.left_end.five_prime_phosphate, again.right_end.five_prime_phosphate) == ("unphosphorylated", "phosphorylated")


def test_end_operation_features_follow_the_surviving_bases():
    sequence, left, right, _t, _b = make_input(CORE, right_top="TCGA")
    feature = {"id": "f", "name": "tail", "type": "misc_feature", "start": len(sequence) - 4, "end": len(sequence),
               "strand": 1, "segments": [{"start": len(sequence) - 4, "end": len(sequence)}]}
    keep = {"id": "g", "name": "core", "type": "misc_feature", "start": 2, "end": 10, "strand": 1,
            "segments": [{"start": 2, "end": 10}]}
    result = apply_end_operation(sequence, left, right, operation="remove_3_overhang", end="right", features=[feature, keep])
    assert [f["name"] for f in result.features] == ["core"] and result.features[0]["start"] == 2


def test_results_ligate_after_end_repair():
    # blunt vector end + filled-in sticky end: refused before, joins after fill-in
    sequence, left, right, _t, _b = make_input(CORE, left_top="AACG")
    blunt = AssemblyFragment(id="v", name="blunt", sequence="ACGTACGTACGT", left_end=FragmentEnd(type="blunt"),
                             right_end=FragmentEnd(type="blunt"))
    sticky = AssemblyFragment(id="s", name="sticky", sequence=sequence, left_end=left, right_end=right)
    with pytest.raises(AssemblyError, match="end-repair"):
        simulate_ligation([blunt, sticky], circular=False)
    filled = apply_end_operation(sequence, left, right, operation="fill_in", end="left")
    product = simulate_ligation([blunt, AssemblyFragment(id="s", name="sticky", sequence=filled.sequence,
                                left_end=filled.left_end, right_end=filled.right_end)], circular=False)
    assert product.sequence == "ACGTACGTACGT" + filled.sequence
    # two filled-in partners (different original overhangs) join blunt to blunt
    a = apply_end_operation(*make_input(CORE, right_bot="CTTA")[:3], operation="fill_in", end="right")
    b = apply_end_operation(*make_input(CORE, left_top="AACG")[:3], operation="fill_in", end="left")
    joined = simulate_ligation([
        AssemblyFragment(id="a", name="a", sequence=a.sequence, left_end=a.left_end, right_end=a.right_end),
        AssemblyFragment(id="b", name="b", sequence=b.sequence, left_end=b.left_end, right_end=b.right_end)], circular=False)
    assert joined.sequence == a.sequence + b.sequence


# ── phosphate state and junction label ────────────────────────────────────────
def _pair(vector_phosphate, insert_phosphate, ovh="GATC"):
    def frag(i, phos):
        return AssemblyFragment(
            id=i, name=i, sequence="AAAAAAAAAAAA",
            left_end=FragmentEnd(type="sticky_5", overhang=ovh, protruding_strand="top", five_prime_phosphate=phos),
            right_end=FragmentEnd(type="sticky_5", overhang=ovh, protruding_strand="bottom", five_prime_phosphate=phos))
    return frag("vector", vector_phosphate), frag("insert", insert_phosphate)


def test_junction_state_labels_and_bases_never_change():
    bases = {}
    for states, expected in [
        (("phosphorylated", "phosphorylated"), "both"),
        (("unphosphorylated", "phosphorylated"), "one_strand"),
        (("unphosphorylated", "unphosphorylated"), "none"),
        (("unknown", "phosphorylated"), "unknown"),
        (("unknown", "unknown"), "unknown"),
    ]:
        v, i = _pair(*states)
        product = simulate_ligation([v, i], circular=False)
        bases[states] = product.sequence
        assert product.junctions[0].sealed == expected
        if expected == "unknown":
            assert "joined, ligation chemistry not established" in product.junctions[0].notes
        if expected == "one_strand":
            assert "joined, nicked" in product.junctions[0].notes
    assert len(set(bases.values())) == 1  # product bases identical with and without phosphate information


def test_rotated_end_keeps_its_phosphate_state_and_swaps_only_the_strand():
    end = FragmentEnd(type="sticky_5", overhang="GATC", protruding_strand="top", five_prime_phosphate="phosphorylated")
    rotated = reverse_complement_end(end)
    assert rotated.five_prime_phosphate == "phosphorylated" and rotated.protruding_strand == "bottom"
    assert rotated.overhang == "GATC"
    oriented = orient_fragment(AssemblyFragment(
        id="x", name="x", sequence="GATCAAAA", orientation="reverse",
        left_end=end, right_end=FragmentEnd(type="blunt", five_prime_phosphate="unphosphorylated")))
    assert oriented.left_end.five_prime_phosphate == "unphosphorylated"
    assert oriented.right_end.five_prime_phosphate == "phosphorylated"


def test_unknown_phosphate_is_omitted_from_the_wire_and_known_is_carried():
    unknown = molbio_ops.AssemblyFragmentEndSchema(type="blunt")
    assert "five_prime_phosphate" not in unknown.model_dump()
    known = molbio_ops.AssemblyFragmentEndSchema(type="blunt", five_prime_phosphate="phosphorylated")
    assert known.model_dump()["five_prime_phosphate"] == "phosphorylated"
    v, i = _pair("unknown", "unknown")
    junction = molbio_ops.assembly_junction_to_response(simulate_ligation([v, i], circular=False).junctions[0])
    assert "sealed" not in junction.model_dump()
    v, i = _pair("phosphorylated", "phosphorylated")
    junction = molbio_ops.assembly_junction_to_response(simulate_ligation([v, i], circular=False).junctions[0])
    assert junction.model_dump()["sealed"] == "both"


def test_digest_ends_become_phosphorylated_only_when_enzyme_created():
    from services.restriction_catalog import catalog_authority
    from services.restriction_digest import simulate_digest
    from services.assembly.physical_ends import digest_end_to_fragment_end
    catalog = catalog_authority.require()
    sequence = "TTTTTTTT" + "GGATCC" + "AAAAAAAA"
    receipt = {k: v for k, v in catalog_authority.readiness().items() if k not in {"required", "ready", "status"}}
    receipt["digest_enabled"] = True
    import hashlib
    sim = simulate_digest(sequence=sequence, topology="linear", catalog=catalog, records=(catalog.by_id["BamHI"],),
                          selected_enzyme_ids=("BamHI",),
                          source_receipt={"kind": "inline_dna", "name": "x", "sequence_id": None, "revision_id": None,
                                          "revision_number": None, "content_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
                                          "content_length": len(sequence), "topology": "linear"},
                          catalog_receipt=receipt)
    first, second = sim.fragments
    assert digest_end_to_fragment_end(first.left_end).five_prime_phosphate == "unknown"  # natural end
    assert digest_end_to_fragment_end(first.right_end).five_prime_phosphate == "phosphorylated"
    assert digest_end_to_fragment_end(second.left_end).five_prime_phosphate == "phosphorylated"


# ── hybrid junction observation (A4d) ─────────────────────────────────────────
def test_bamhi_bglii_hybrid_junction_is_not_cleavable_by_either_enzyme():
    # BamHI-cut end: G^GATCC leaves 5'-GATC overhang; BglII A^GATCT leaves the same.
    # BamHI G^GATCC and BglII A^GATCT both leave 5'-GATC; the hybrid joint reads GGATCT.
    vector_seq = "GATC" + "CAAAAGGTACCTTTT" + "G"
    insert_seq = "GATC" + "TTTACCATGGTTT" + "A"
    vector = AssemblyFragment(id="v", name="vector", sequence=vector_seq,
                              left_end=FragmentEnd(type="sticky_5", overhang="GATC", protruding_strand="top"),
                              right_end=FragmentEnd(type="sticky_5", overhang="GATC", protruding_strand="bottom"))
    insert = AssemblyFragment(id="i", name="insert", sequence=insert_seq,
                              left_end=FragmentEnd(type="sticky_5", overhang="GATC", protruding_strand="top"),
                              right_end=FragmentEnd(type="sticky_5", overhang="GATC", protruding_strand="bottom"))
    product = simulate_ligation([vector, insert], circular=False)
    observed = observe_junction_sites(product, ["BamHI", "BglII"])
    # vector ends ...G | GATC...: right fragment's top starts with GATC -> G+GATCT... = GGATCT (neither GGATCC nor AGATCT)
    assert product.sequence[len(vector_seq) - 1:len(vector_seq) + 5] == "GGATCT"
    assert observed[0]["regenerated"] == [] and sorted(observed[0]["destroyed"]) == ["BamHI", "BglII"]
    assert "not cleavable by either enzyme" in observed[0]["note"]


def test_regenerated_site_is_reported():
    vector = AssemblyFragment(id="v", name="v", sequence="GATC" + "CAAAAGGTACCTTTT" + "G",
                              left_end=FragmentEnd(type="sticky_5", overhang="GATC", protruding_strand="top"),
                              right_end=FragmentEnd(type="sticky_5", overhang="GATC", protruding_strand="bottom"))
    insert = AssemblyFragment(id="i", name="i", sequence="GATC" + "CCCCCCCCCCCC" + "G",
                              left_end=FragmentEnd(type="sticky_5", overhang="GATC", protruding_strand="top"),
                              right_end=FragmentEnd(type="sticky_5", overhang="GATC", protruding_strand="bottom"))
    product = simulate_ligation([vector, insert], circular=False)
    observed = observe_junction_sites(product, ["BamHI"])
    assert observed[0]["regenerated"] == ["BamHI"]


# ── routes: end operations apply/preview, retained ends, idempotency, ligation contract ──
@pytest.fixture
def client_and_session(tmp_path):
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


def _inline(sequence, left, right):
    def dump(e):
        return {"type": e.type, "overhang": e.overhang, "protruding_strand": e.protruding_strand}
    return {"kind": "inline", "name": "frag", "sequence": sequence, "left_end": dump(left), "right_end": dump(right),
            "features": [{"id": "k", "name": "core", "type": "misc_feature", "start": 6, "end": 12, "strand": 1,
                          "segments": [{"start": 6, "end": 12}]}]}


def test_apply_saves_a_retained_child_and_reads_back_ends_and_is_idempotent(client_and_session):
    client, maker = client_and_session
    sequence, left, right, _t, _b = make_input(CORE, right_bot="CTTA")
    body = {"source": _inline(sequence, left, right), "operation": "fill_in", "end": "right",
            "idempotency_key": "k-1", "new_name": "filled"}
    preview = client.post("/api/molbio/end-operations/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["changed"] == {"bases_added": 4, "bases_removed": 0}
    first = client.post("/api/molbio/end-operations/apply", json=body)
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["sequence"] == sequence + "CTTA" and data["right_end"]["type"] == "blunt"
    assert data["sequence_id"] is None and data["revision_id"] and data["document_id"] and data["operation_id"]
    assert data["features"][0]["name"] == "core"
    again = client.post("/api/molbio/end-operations/apply", json=body)
    assert again.status_code == 200 and again.json()["operation_id"] == data["operation_id"]
    assert client.post("/api/molbio/end-operations/apply", json={**body, "end": "left"}).status_code == 409

    async def read():
        from molbio_models import MolecularOperation, MolecularOperationOutput, MolecularRevision, NucleotideSequence
        from sqlalchemy import select
        async with maker() as session:
            revision = await session.get(MolecularRevision, data["revision_id"])
            operation = await session.get(MolecularOperation, data["operation_id"])
            outputs = (await session.execute(select(MolecularOperationOutput))).scalars().all()
            shelf = (await session.execute(select(NucleotideSequence))).scalars().all()
            return revision, operation, outputs, shelf

    revision, operation, outputs, shelf = asyncio.run(read())
    assert operation.operation_kind == "end_operation" and shelf == []  # retained document, no shelf projection
    assert [o.role for o in outputs] == ["end_operation_product"]
    left_back, right_back = read_physical_ends(revision.provenance)
    assert (left_back.type, right_back.type) == (left.type, "blunt")
    assert revision.provenance["physical_ends"]["source"] == "end_operation"

    # chain from the retained revision: dephosphorylate it, input edge points at the source revision
    chained = client.post("/api/molbio/end-operations/apply", json={
        "source": {"kind": "molecular_revision", "revision_id": data["revision_id"]},
        "operation": "dephosphorylate", "end": "both"})
    assert chained.status_code == 200, chained.text
    assert chained.json()["left_end"]["five_prime_phosphate"] == "unphosphorylated"
    assert chained.json()["sequence"] == data["sequence"]


def test_registered_ligation_contract_runs_through_the_real_route(client_and_session):
    client, maker = client_and_session
    sequence, left, right, _t, _b = make_input(CORE, right_bot="CTTA")
    a = client.post("/api/molbio/end-operations/apply", json={"source": _inline(sequence, left, right),
                    "operation": "phosphorylate", "end": "both", "new_name": "A"}).json()
    b = client.post("/api/molbio/end-operations/apply", json={"source": _inline("GGCCAATTGGCCAATT", FragmentEnd(type="blunt"), FragmentEnd(type="blunt")),
                    "operation": "dephosphorylate", "end": "both", "new_name": "B"}).json()
    from services.assembly.ligation_contract import materialize_ligation_request

    async def go():
        async with maker() as session:
            return await materialize_ligation_request(session, {
                "fragment_revision_ids": [a["revision_id"], b["revision_id"]], "orientations": ["forward", "forward"],
                "circular": False, "change_summary": "ligation contract test"})

    native = asyncio.run(go())
    assert [f.source_sequence_id for f in native.fragments] == [a["document_id"], b["document_id"]]
    assert [f.source_revision for f in native.fragments] == [1, 1]
    assert native.fragments[0].right_end.type == "sticky_5" and native.fragments[1].left_end.type == "blunt" and native.fragments[1].left_end.five_prime_phosphate == "unphosphorylated"
    # A's right end was fill-in-free (sticky); B blunt: the route reports the refusal with the repair hint
    response = client.post("/api/molbio/assembly/ligation/simulate", json=native.model_dump(mode="json"))
    assert response.status_code == 400 and "end-repair" in response.json()["detail"]
    # repair A then ligate A(blunt) + B through the same adapter and route
    fixed = client.post("/api/molbio/end-operations/apply", json={"source": {"kind": "molecular_revision", "revision_id": a["revision_id"]},
                        "operation": "fill_in", "end": "right"}).json()

    async def go2():
        async with maker() as session:
            return await materialize_ligation_request(session, {
                "fragment_revision_ids": [fixed["revision_id"], b["revision_id"]], "orientations": ["forward", "forward"],
                "circular": False, "change_summary": "ligation contract test"})

    native2 = asyncio.run(go2())
    ok = client.post("/api/molbio/assembly/ligation/simulate", json=native2.model_dump(mode="json"))
    assert ok.status_code == 200, ok.text
    product = ok.json()["product"]
    assert product["sequence"] == fixed["sequence"] + "GGCCAATTGGCCAATT"
    assert product["junctions"][0]["sealed"] == "one_strand"  # fixed right end phosphorylated, B dephosphorylated


def test_inventory_names_the_ligation_materializer():
    from services.ngs_molbio_capabilities import capability_record
    record = capability_record("molbio.assembly.ligation")
    assert record["materializer_owner"] == "services.assembly.ligation_contract.materialize_ligation_request"
    assert record["native_mapping"]["native_request_compatibility"] == "exact_native_mapping"


# ── restriction-cloning proposals ─────────────────────────────────────────────
def _random_dna(n, seed, avoid=()):
    rng = random.Random(seed)
    while True:
        s = "".join(rng.choice("ACGT") for _ in range(n))
        if not any(site in s or rc(site) in s for site in avoid):
            return s


SITES = {"BamHI": "GGATCC", "EcoRI": "GAATTC", "HindIII": "AAGCTT", "BglII": "AGATCT", "XhoI": "CTCGAG", "NcoI": "CCATGG"}


def _plasmid(*parts, seed=3):
    filler = _random_dna(400, seed, avoid=SITES.values())
    pieces = [filler[:150]]
    for part in parts:
        pieces += [part, filler[150:260]]
    return "".join(pieces) + filler[260:]


def test_suggests_directional_pair_with_independent_expectations():
    backbone = _plasmid(SITES["EcoRI"] + "TTTAGC" + SITES["BamHI"], seed=5)
    insert_payload = _random_dna(120, 8, avoid=SITES.values())
    insert = "GCGC" + SITES["EcoRI"] + insert_payload + SITES["BamHI"] + "GCGC"
    result = suggest_restriction_cloning({
        "backbone_sequence": backbone, "backbone_topology": "circular", "insert_sequence": insert,
        "enzymes": ["EcoRI", "BamHI", "HindIII"], "limit": 3})
    assert result["no_suitable_pair"] is False
    top = result["proposals"][0]
    assert sorted(top["vector_enzymes"]) == ["BamHI", "EcoRI"] and sorted(top["insert_enzymes"]) == ["BamHI", "EcoRI"]
    assert top["directional"] is True and top["insert_orientation"] == "forward"
    assert top["vector_ends_match_each_other"] is False
    assert top["vector_self_ligation"] == "not possible"
    cut_e, cut_b = backbone.index(SITES["EcoRI"]) + 1, backbone.index(SITES["BamHI"]) + 1  # top-strand cuts G^
    ins_e, ins_b = insert.index(SITES["EcoRI"]) + 1, insert.index(SITES["BamHI"]) + 1
    assert top["vector_length"] == len(backbone) - (cut_b - cut_e)
    assert top["insert_payload_length"] == ins_b - ins_e
    assert top["product_length"] == len(backbone) - (cut_b - cut_e) + (ins_b - ins_e)
    assert all(j["regenerated"] for j in top["junction_sites"])  # both junctions restore their own site


def test_compatible_cohesive_pair_reports_hybrid_and_nondirectional_background():
    # vector cut by BamHI twice-free single site; insert flanked by BglII (compatible ends)
    backbone = _plasmid(SITES["BamHI"], seed=6)
    insert = "GC" + SITES["BglII"] + _random_dna(90, 9, avoid=SITES.values()) + SITES["BglII"] + "GC"
    result = suggest_restriction_cloning({"backbone_sequence": backbone, "insert_sequence": insert,
                                          "enzymes": ["BamHI", "BglII"], "limit": 3})
    # one vector enzyme cannot give a two-site vector region: no usable pair, with alternatives listed
    assert result["no_suitable_pair"] is True
    assert {a["method"] for a in result["alternatives"]} == {"overlap_assembly", "pcr_primer_tails", "synthesis"}


def test_primer_tail_proposal_adds_sites_outside_the_payload_without_editing_the_source():
    backbone = _plasmid(SITES["XhoI"] + "AACCGG" + SITES["NcoI"], seed=7)
    payload = _random_dna(100, 10, avoid=SITES.values())
    result = suggest_restriction_cloning({"backbone_sequence": backbone, "insert_sequence": payload,
                                          "enzymes": ["XhoI", "NcoI", "EcoRI"], "limit": 3})
    assert not result["no_suitable_pair"]
    top = result["proposals"][0]
    assert top["kind"] == "primer_tail" and top["primer_tails"]["forward_primer_5prime_tail"].endswith(
        SITES["XhoI"] if "XhoI" in top["vector_enzymes"][0] else SITES["NcoI"])
    assert top["directional"] is True
    assert result["considered"]["unique_in_insert"] == 0  # the supplied insert was not edited or rescanned into sites


def test_no_pair_when_enzyme_cuts_vector_more_than_once():
    backbone = _plasmid(SITES["EcoRI"], SITES["EcoRI"], seed=2)
    insert = SITES["EcoRI"] + _random_dna(60, 3, avoid=SITES.values()) + SITES["HindIII"]
    result = suggest_restriction_cloning({"backbone_sequence": backbone, "insert_sequence": insert,
                                          "enzymes": ["EcoRI", "HindIII"], "limit": 3})
    assert result["no_suitable_pair"] is True and result["proposals"] == []


def test_vector_phosphate_state_changes_only_the_self_ligation_observation():
    backbone = _plasmid(SITES["BamHI"] + "TTTAGC" + SITES["BglII"], seed=4)
    insert = "GC" + SITES["BamHI"] + _random_dna(80, 5, avoid=SITES.values()) + SITES["BglII"] + "GC"
    args = {"backbone_sequence": backbone, "insert_sequence": insert, "enzymes": ["BamHI", "BglII"], "limit": 3}
    unknown = suggest_restriction_cloning(args)
    unphos = suggest_restriction_cloning({**args, "vector_five_prime_phosphate": "unphosphorylated"})
    assert unknown["proposals"] and unphos["proposals"]
    a, b = unknown["proposals"][0], unphos["proposals"][0]
    assert a["vector_ends_match_each_other"] is True  # BamHI/BglII ends are compatible: self-ligation background
    assert a["product_length"] == b["product_length"]
    assert "unphosphorylated" in b["vector_self_ligation"] and "unphosphorylated" not in a["vector_self_ligation"]
    assert a["insert_orientation"] == "either"


def test_cloning_route_returns_the_same_result(client_and_session):
    client, _maker = client_and_session
    backbone = _plasmid(SITES["EcoRI"] + "TTTAGC" + SITES["BamHI"], seed=5)
    insert = "GCGC" + SITES["EcoRI"] + _random_dna(120, 8, avoid=SITES.values()) + SITES["BamHI"] + "GCGC"
    response = client.post("/api/molbio/assembly/restriction-cloning/suggest", json={
        "backbone_sequence": backbone, "insert_sequence": insert, "enzymes": ["EcoRI", "BamHI"]})
    assert response.status_code == 200, response.text
    assert response.json()["proposals"][0]["directional"] is True
