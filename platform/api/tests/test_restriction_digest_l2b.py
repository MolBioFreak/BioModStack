"""Digest lane l2: per-site exclusion, duplex-aware redigestion, child features, migration 0010."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from molbio_database import create_molbio_engine, init_molbio_db, get_applied_molbio_migrations
from molbio_models import MolecularRevision
from services.assembly.physical_ends import record_to_end
from services.restriction_analysis import reverse_complement
from services.restriction_catalog import catalog_authority
from services.restriction_digest import simulate_digest
from services.restriction_digest_substrate import substrate_from_ends

from test_restriction_digest_persistence import (
    CATALOG_ID, CATALOG_SHA, _client, _preview_request, _simulate, _store,
)

FIXTURE = Path(__file__).parent / "fixtures/restriction_digest/pre0010_saved_digest.json"
TWO_SITES = "TTGAATTCAAGGATCCTTGAATTCAAA"  # EcoRI sites at 2 and 18 (cut after 3 and 19), BamHI at 10 (cut after 11)


def _ends(fragment):
    return (
        record_to_end(fragment.left_end.model_dump(mode="json")),
        record_to_end(fragment.right_end.model_dump(mode="json")),
    )


def _redigest(fragment, enzymes, topology="linear"):
    view = catalog_authority.require()
    sequence = fragment.top_strand_sequence
    receipt = {k: v for k, v in catalog_authority.readiness().items() if k not in {"required", "ready", "status"}}
    receipt["digest_enabled"] = True
    left, right = _ends(fragment)
    return simulate_digest(
        persisted_identity=True, sequence=sequence, topology=topology, catalog=view,
        records=tuple(view.by_id[e] for e in enzymes), selected_enzyme_ids=enzymes,
        source_receipt={"kind": "inline_dna", "name": "f", "sequence_id": None, "revision_id": None,
                        "revision_number": None, "content_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
                        "content_length": len(sequence), "topology": topology},
        catalog_receipt=receipt, substrate=substrate_from_ends(sequence, left, right),
    )


def _group_ids(sequence, enzymes, topology="linear"):
    return [c.contributor_group_id for c in _simulate(sequence, enzymes, topology).cleavages]


def _simulate_excluding(sequence, enzymes, excluded, topology="linear"):
    view = catalog_authority.require()
    receipt = {k: v for k, v in catalog_authority.readiness().items() if k not in {"required", "ready", "status"}}
    receipt["digest_enabled"] = True
    return simulate_digest(
        persisted_identity=True, sequence=sequence, topology=topology, catalog=view,
        records=tuple(view.by_id[e] for e in enzymes), selected_enzyme_ids=enzymes,
        source_receipt={"kind": "inline_dna", "name": "fixture", "sequence_id": None, "revision_id": None,
                        "revision_number": None, "content_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
                        "content_length": len(sequence), "topology": topology},
        catalog_receipt=receipt, excluded_cleavage_group_ids=tuple(excluded),
    )


# ---- A4a: per-site selection -------------------------------------------------

def test_excluding_one_cleavage_event_gives_expected_partial_digest() -> None:
    groups = _group_ids(TWO_SITES, ("EcoRI", "BamHI"))
    assert len(groups) == 3
    # Independent oracle: EcoRI cuts after G (top index 3 / 19), BamHI after G (index 11).
    cut_top = {0: 3, 1: 11, 2: 19}
    for off in range(3):
        kept = sorted(v for k, v in cut_top.items() if k != off)
        edges = [0, *kept, len(TWO_SITES)]
        expected = [TWO_SITES[a:b] for a, b in zip(edges, edges[1:])]
        result = _simulate_excluding(TWO_SITES, ("EcoRI", "BamHI"), [groups[off]])
        assert [f.top_strand_sequence for f in result.fragments] == expected
        assert result.excluded_cleavage_group_ids == (groups[off],)
        assert result.cleavage_state == "fragmented"


def test_excluding_every_event_leaves_molecule_uncut_and_one_event_linearizes_a_circle() -> None:
    groups = _group_ids(TWO_SITES, ("EcoRI",))
    none = _simulate_excluding(TWO_SITES, ("EcoRI",), groups)
    assert none.cleavage_state == "uncut"
    assert [f.top_strand_sequence for f in none.fragments] == [TWO_SITES]
    circ_groups = _group_ids(TWO_SITES, ("EcoRI",), "circular")
    one = _simulate_excluding(TWO_SITES, ("EcoRI",), circ_groups[:1], "circular")
    assert one.cleavage_state == "linearized"
    (fragment,) = one.fragments
    assert fragment.topology == "linear" and len(fragment.top_strand_sequence) == len(TWO_SITES)
    # Linearized at the kept cut: the top strand is the circle rotated to start after that cut.
    kept_cut = {c.contributor_group_id: c.top_boundary for c in _simulate(TWO_SITES, ("EcoRI",), "circular").cleavages}[circ_groups[1]]
    assert fragment.top_strand_sequence == TWO_SITES[kept_cut:] + TWO_SITES[:kept_cut]


def test_empty_exclusion_is_byte_identical_and_unknown_ids_are_ignored() -> None:
    plain = _simulate(TWO_SITES, ("EcoRI", "BamHI"))
    empty = _simulate_excluding(TWO_SITES, ("EcoRI", "BamHI"), [])
    assert empty.canonical_unsigned_bytes() == plain.canonical_unsigned_bytes()
    assert "excluded_cleavage_group_ids" not in json.loads(plain.canonical_unsigned_bytes())
    assert "not_cuttable" not in json.loads(plain.canonical_unsigned_bytes())
    # A group id that matches no event removes nothing.
    stray = _simulate_excluding(TWO_SITES, ("EcoRI", "BamHI"), ["sha256:nothing"])
    assert [f.top_strand_sequence for f in stray.fragments] == [f.top_strand_sequence for f in plain.fragments]


# ---- A4b: redigestion of retained sticky fragments ----------------------------

def test_redigesting_a_retained_sticky_fragment_matches_one_pass_digest() -> None:
    first = _simulate(TWO_SITES, ("EcoRI",))
    middle = first.fragments[1]
    assert middle.top_strand_sequence == "AATTCAAGGATCCTTG"
    again = _redigest(middle, ("BamHI",))
    assert again.cleavage_state == "fragmented" and not again.not_cuttable
    one_pass = _simulate(TWO_SITES, ("EcoRI", "BamHI"))
    expected = one_pass.fragments[1:3]
    for got, want in zip(again.fragments, expected):
        assert got.top_strand_sequence == want.top_strand_sequence
        for side in ("left_end", "right_end"):
            g, w = getattr(got, side), getattr(want, side)
            assert (g.kind, g.protruding_strand, g.overhang_sequence_5to3, g.length_nt) == (
                w.kind, w.protruding_strand, w.overhang_sequence_5to3, w.length_nt)
    # Hand-written oracle: AATT stays on the left, GATC (new, bottom-strand) joins the two pieces.
    assert [f.top_strand_sequence for f in again.fragments] == ["AATTCAAG", "GATCCTTG"]
    a, b = again.fragments
    assert (a.left_end.overhang_sequence_5to3, a.left_end.protruding_strand) == ("AATT", "top")
    assert (a.right_end.overhang_sequence_5to3, a.right_end.protruding_strand) == ("GATC", "bottom")
    assert (b.left_end.overhang_sequence_5to3, b.left_end.protruding_strand) == ("GATC", "top")
    assert (b.right_end.overhang_sequence_5to3, b.right_end.protruding_strand) == ("AATT", "bottom")
    # Bottom strands, read from the parent by coordinates, are what the pieces' duplex partners are.
    for fragment in again.fragments:
        assert fragment.bottom_end_boundary - fragment.bottom_start_boundary >= 0


def test_redigesting_the_reverse_orientation_gives_the_reverse_complement_duplex() -> None:
    forward = _simulate(TWO_SITES, ("EcoRI",)).fragments[1]
    fwd_pieces = _redigest(forward, ("BamHI",)).fragments
    parent = TWO_SITES
    one_pass = _simulate(parent, ("EcoRI", "BamHI")).fragments[1:3]
    # Physical bottom strand of each forward piece, read 5'->3' from the parent by coordinates.
    bottoms = {reverse_complement(parent[f.bottom_start_boundary:f.bottom_end_boundary]) for f in one_pass}
    reverse_parent = reverse_complement(parent)
    rev_middle = _simulate(reverse_parent, ("EcoRI",)).fragments
    rev_middle = next(f for f in rev_middle if "GGATCC" in f.top_strand_sequence)
    rev_pieces = _redigest(rev_middle, ("BamHI",)).fragments
    assert {f.top_strand_sequence for f in rev_pieces} == bottoms
    assert len(fwd_pieces) == len(rev_pieces) == 2


def test_site_overlapping_an_overhang_is_listed_not_cuttable_and_not_cut() -> None:
    piece = _redigest(_simulate(TWO_SITES, ("EcoRI",)).fragments[1], ("BamHI",)).fragments[1]
    assert piece.top_strand_sequence == "GATCCTTG"
    result = _redigest(piece, ("MboI",))  # GATC is the unpaired 5' overhang
    assert result.cleavage_state == "uncut"
    assert [(r.enzyme_id, r.site_start, r.reason) for r in result.not_cuttable] == [
        ("MboI", 0, "recognition_site_overlaps_unpaired_overhang")]
    assert [f.top_strand_sequence for f in result.fragments] == ["GATCCTTG"]
    # The inherited ends survive an uncut redigest.
    assert result.fragments[0].left_end.overhang_sequence_5to3 == "GATC"
    assert result.fragments[0].right_end.overhang_sequence_5to3 == "AATT"


def test_circular_parents_are_unaffected_by_the_substrate_argument() -> None:
    plain = _simulate(TWO_SITES, ("EcoRI",), "circular")
    view = catalog_authority.require()
    assert plain.not_cuttable == ()
    assert [f.topology for f in plain.fragments] == ["linear", "linear"]
    assert plain.cleavage_state == "fragmented" and view is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("strandedness", ["single", "unknown"])
async def test_single_and_unknown_strand_substrates_still_422(tmp_path, strandedness) -> None:
    snapshot = {"sequence_type": "dna", "sequence": "TTGAATTCAA", "is_circular": False,
                "molecule_strandedness": strandedness}
    engine, sessions, digest = await _store(tmp_path, source_snapshot=snapshot)
    try:
        async with await _client(sessions) as client:
            response = await client.post("/api/molbio/restriction/digests/simulate", json=_preview_request(digest))
            assert response.status_code == 422
            assert response.json()["detail"]["code"] == "unsupported_digest_substrate"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_http_exclusion_and_sticky_revision_digest_through_the_route(tmp_path) -> None:
    sequence = TWO_SITES
    engine, sessions, digest = await _store(tmp_path, sequence=sequence)
    try:
        async with await _client(sessions) as client:
            request = _preview_request(digest)
            full = await client.post("/api/molbio/restriction/digests/simulate", json=request)
            assert full.status_code == 200, full.text
            groups = [c["contributor_group_id"] for c in full.json()["cleavages"]]
            assert len(groups) == 2
            request["excluded_cleavage_group_ids"] = [groups[0]]
            part = await client.post("/api/molbio/restriction/digests/simulate", json=request)
            assert part.status_code == 200, part.text
            assert [f["top_strand_sequence"] for f in part.json()["fragments"]] == [sequence[:19], sequence[19:]]
            assert part.json()["excluded_cleavage_group_ids"] == [groups[0]]
            assert "excluded_cleavage_group_ids" not in full.json()
    finally:
        await engine.dispose()


# ---- A3a: children carry mapped features --------------------------------------

def _covered(feature):
    return {i for s in feature["segments"] for i in range(s["start"], s["end"])}


@pytest.mark.asyncio
async def test_saved_digest_children_carry_mapped_features_including_wrapped_fragment(tmp_path) -> None:
    sequence = "GGAATTCTTTTTGGATCCAAAAACCCGGGTTTTTGAATTCAAAAA"  # circular, EcoRI at 1 and 34
    features = [
        {"id": "f1", "name": "inside", "type": "gene", "start": 12, "end": 22, "strand": 1,
         "segments": [{"start": 12, "end": 22}], "qualifiers": {"note": ["x"]}},
        {"id": "f2", "name": "over-origin", "type": "misc_feature", "start": 40, "end": 5, "strand": 1,
         "segments": [{"start": 40, "end": 45}, {"start": 0, "end": 5}], "qualifiers": {}},
    ]
    snapshot = {"sequence_type": "dna", "sequence": sequence, "is_circular": True,
                "molecule_strandedness": "double", "features": features}
    engine, sessions, digest = await _store(tmp_path, source_snapshot=snapshot, sequence=sequence)
    try:
        async with await _client(sessions) as client:
            request = _preview_request(digest)
            preview = await client.post("/api/molbio/restriction/digests/simulate", json=request)
            assert preview.status_code == 200, preview.text
            fragments = preview.json()["fragments"]
            save = {**request, "schema": "bms.molbio.restriction-digest-save-request.v1",
                    "simulation_sha256": preview.json()["simulation_sha256"], "idempotency_key": "feat-1",
                    "persistence_mode": "operation_and_fragments", "fragment_name_prefix": "feat"}
            saved = await client.post("/api/molbio/restriction/digests", json=save)
            assert saved.status_code == 200, saved.text
            again = await client.get(f"/api/molbio/restriction/digests/{saved.json()['operation_id']}")
            assert again.status_code == 200 and again.content == saved.content
        async with sessions() as session:
            children = {}
            for out in saved.json()["outputs"]:
                row = await session.get(MolecularRevision, out["revision_id"])
                children[out["fragment_index"]] = row.snapshot
        for index, frag in enumerate(fragments):
            positions = [p for a, b in frag["source_segments"] for p in range(a, b)]
            snap = children[index]
            assert snap["sequence"] == "".join(sequence[p] for p in positions)
            by_name = {f["name"]: f for f in snap.get("features", [])}
            for feature in features:
                source_bases = {p for s in feature["segments"] for p in range(s["start"], s["end"])}
                expected = {i for i, p in enumerate(positions) if p in source_bases}  # independent oracle
                if expected:
                    assert _covered(by_name[feature["name"]]) == expected, (index, feature["name"])
                else:
                    assert feature["name"] not in by_name
        wrapped = [i for i, f in enumerate(fragments) if f["wraps_origin"]]
        assert wrapped, "fixture must contain a wrapped fragment"
        assert "over-origin" in {f["name"] for f in children[wrapped[0]]["features"]}
        assert "inside" in {f["name"] for f in children[0]["features"]}
    finally:
        await engine.dispose()


# ---- migration 0010 and pre-change saved digest --------------------------------

def _load_fixture(path: Path, *, with_validators: bool = True) -> None:
    from molbio_migrations import restriction_digest_json_equal, validate_restriction_digest_result
    from services.restriction_digest_save_receipt import validate_persisted_save_request_receipt

    data = json.loads(FIXTURE.read_text())
    with sqlite3.connect(path) as db:
        db.create_function("bms_restriction_digest_result_valid", 7, validate_restriction_digest_result, deterministic=True)
        db.create_function("bms_restriction_digest_json_equal", 2, restriction_digest_json_equal, deterministic=True)
        db.create_function("bms_restriction_digest_save_receipt_valid", 3, validate_persisted_save_request_receipt, deterministic=True)
        db.execute("PRAGMA foreign_keys=OFF")
        for table in ("molecular_documents", "molecular_revisions", "molecular_operations",
                      "molecular_operation_inputs", "molecular_operation_outputs", "restriction_digest_results"):
            for row in data[table]:
                cols = list(row)
                db.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                           [row[c] for c in cols])
        db.commit()


@pytest.mark.asyncio
async def test_pre_change_saved_digest_validates_through_the_0010_trigger(tmp_path) -> None:
    from molbio_migrations import restriction_digest_physical_schema_issues
    database = tmp_path / "new.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    try:
        applied = await init_molbio_db(engine=engine)
        assert applied[-1] == "0010_digest_child_features"
        async with engine.connect() as connection:
            issues = await connection.run_sync(lambda c: restriction_digest_physical_schema_issues(c))
        assert issues == []
    finally:
        await engine.dispose()
    _load_fixture(database)  # raises sqlite3.IntegrityError if the trigger rejects it
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM restriction_digest_results").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_pre_change_digest_is_rewritten_identically_by_current_code() -> None:
    data = json.loads(FIXTURE.read_text())
    stored = json.loads(data["restriction_digest_results"][0]["result"])
    source = data["source_sequence"]
    receipt = stored["simulation"]["source"]
    now = _simulate(source, ("EcoRI",), "circular", source_receipt=receipt)
    assert now.simulation_sha256 == stored["simulation"]["simulation_sha256"] == stored["result_sha256"]
    assert json.loads(now.model_dump_json(by_alias=True)) == stored["simulation"]


@pytest.mark.asyncio
async def test_store_at_0008_upgrades_once_to_0010_and_rerun_is_a_noop(tmp_path) -> None:
    import molbio_migrations as mm
    database = tmp_path / "old.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    try:
        await init_molbio_db(engine=engine)
    finally:
        await engine.dispose()
    # Turn the fresh store back into a 0008 store: the 0007 trigger and no 0010 ledger row.
    with sqlite3.connect(database) as db:
        db.execute("DROP TRIGGER molbio_restriction_digest_results_integrity_insert")
        db.execute(mm._RESTRICTION_DIGEST_INTEGRITY_TRIGGER_SQL)
        db.execute("DELETE FROM molbio_schema_migrations WHERE version='0010_digest_child_features'")
        db.commit()
    _load_fixture(database)  # saved under the old (0007) trigger
    for attempt in range(2):
        engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
        try:
            applied = await init_molbio_db(engine=engine)
            assert applied[-1] == "0010_digest_child_features"
        finally:
            await engine.dispose()
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT count(*) FROM molbio_schema_migrations WHERE version='0010_digest_child_features'").fetchone()[0] == 1
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name='molbio_restriction_digest_results_integrity_insert'").fetchone()[0]
            assert "NOT IN (5, 6)" in sql
            assert db.execute("SELECT count(*) FROM restriction_digest_results").fetchone()[0] == 1
