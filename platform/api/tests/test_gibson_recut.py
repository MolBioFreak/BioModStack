"""Gibson/HiFi: an overlap set on synthetic fragments is applied by re-cutting their ends.

Fragments BMS synthesised carry a marker; those (and only those) may have an end moved so the junction
has the length the user set. Supplied fragments are never rewritten.
"""
from __future__ import annotations

import asyncio
import hashlib
import random
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from molbio_database import create_molbio_engine, get_molbio_session, init_molbio_db, make_molbio_session_factory  # noqa: E402
from routers.molbio_gibson_suggest import router as suggest_router  # noqa: E402
from routers.molbio_ops import router as molbio_router  # noqa: E402
from services.assembly.pydna_gibson import design_gibson  # noqa: E402
from services.assembly.types import AssemblyFragment, JunctionSpec  # noqa: E402

FLAG = "synthetic_fragment"


def dna(seed: int, n: int) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(n))


def same_circle(a: str, b: str) -> bool:
    return len(a) == len(b) and a in b + b


def plan(flags=(True, True, True), cuts=(330, 630), overlap=26, n=900, limit=None):
    """A circular target cut into three synthetic fragments that share `overlap` nt with each neighbour."""
    target = dna(7, n)
    c1, c2 = cuts
    pieces = [target[:c1 + overlap], target[c1:c2 + overlap], target[c2:] + target[:overlap]]
    fragments = []
    for index, (sequence, flagged) in enumerate(zip(pieces, flags), start=1):
        metadata = {FLAG: True, **({"max_synthesis_length": limit} if limit else {})} if flagged else {}
        fragments.append(AssemblyFragment(id=f"f{index}", name=f"Synthetic {index}", sequence=sequence, metadata=metadata))
    return target, fragments


def design(fragments, *choices):
    return design_gibson(fragments, preparations=["ready_linear"] * len(fragments), circular=True, overlap=26,
                         junctions=[JunctionSpec(fid, overlap, None) for fid, overlap in choices] or None)


def lengths(result):
    return {f.id: len(f.sequence) for f in result.designed_fragments}


def overlaps(result):
    return [j.overlap_length for j in result.candidates[0].product.junctions]


def notes(result):
    return [n for j in result.candidates[0].product.junctions for n in j.notes]


# --- engine -----------------------------------------------------------------------------------

def test_longer_overlap_between_two_synthetic_fragments_is_applied_by_recutting_both() -> None:
    target, fragments = plan()
    result = design(fragments, ("f1", 40))
    assert overlaps(result) == [40, 26, 26]
    assert same_circle(result.candidates[0].product.sequence, target)
    before = {f.id: len(f.sequence) for f in fragments}
    after = lengths(result)
    assert after["f3"] == before["f3"]
    assert (after["f1"] - before["f1"]) + (after["f2"] - before["f2"]) == 14
    assert after["f1"] > before["f1"] and after["f2"] > before["f2"]
    assert not any("not applied" in n for n in notes(result))
    assert any("re-cut" in n and "40 nt" in n for n in result.candidates[0].product.junctions[0].notes)
    assert any("re-cut" in w for w in result.warnings)


def test_shorter_overlap_is_applied_too() -> None:
    target, fragments = plan()
    result = design(fragments, ("f2", 18))
    assert overlaps(result) == [26, 18, 26]
    assert same_circle(result.candidates[0].product.sequence, target)
    before = {f.id: len(f.sequence) for f in fragments}
    after = lengths(result)
    assert (after["f2"] - before["f2"]) + (after["f3"] - before["f3"]) == -8


def test_closing_junction_is_addressed_by_the_last_fragment() -> None:
    target, fragments = plan()
    result = design(fragments, ("f3", 33))
    assert overlaps(result) == [26, 26, 33]
    assert same_circle(result.candidates[0].product.sequence, target)


def test_every_junction_can_be_set_at_once() -> None:
    target, fragments = plan()
    result = design(fragments, ("f1", 40), ("f2", 22), ("f3", 50))
    assert overlaps(result) == [40, 22, 50]
    assert same_circle(result.candidates[0].product.sequence, target)


def test_supplied_fragments_are_never_rewritten() -> None:
    target, fragments = plan(flags=(False, False, False))
    result = design(fragments, ("f1", 40))
    assert overlaps(result) == [26, 26, 26]
    assert {f.id: f.sequence for f in result.designed_fragments} == {f.id: f.sequence for f in fragments}
    assert any("already share 26 nt" in n and "not applied" in n for n in notes(result))


def test_a_setting_equal_to_the_existing_overlap_changes_nothing_and_says_nothing() -> None:
    _, fragments = plan()
    result = design(fragments, ("f1", 26))
    assert overlaps(result) == [26, 26, 26]
    assert {f.id: f.sequence for f in result.designed_fragments} == {f.id: f.sequence for f in fragments}
    assert not any("not applied" in n or "re-cut" in n for n in notes(result))
    assert not any("re-cut" in w for w in result.warnings)


def test_only_the_synthetic_side_moves_next_to_a_supplied_fragment() -> None:
    target, fragments = plan(flags=(False, True, False))
    result = design(fragments, ("f1", 40), ("f2", 35))
    assert overlaps(result) == [40, 35, 26]
    assert same_circle(result.candidates[0].product.sequence, target)
    designed = {f.id: f.sequence for f in result.designed_fragments}
    assert designed["f1"] == fragments[0].sequence and designed["f3"] == fragments[2].sequence
    assert designed["f2"] != fragments[1].sequence
    assert len(designed["f2"]) == len(fragments[1].sequence) + 14 + 9


def test_a_recut_that_cannot_fit_keeps_the_existing_overlap_and_says_why() -> None:
    target, fragments = plan(flags=(True, False, True), cuts=(60, 100))
    result = design(fragments, ("f2", 80))
    assert overlaps(result) == [26, 26, 26]
    assert same_circle(result.candidates[0].product.sequence, target)
    assert any("could not be re-cut" in n and "80 nt" in n for n in notes(result))
    assert {f.id: f.sequence for f in result.designed_fragments} == {f.id: f.sequence for f in fragments}


def test_growing_past_the_synthesis_limit_is_an_observation_not_a_refusal() -> None:
    target, fragments = plan(limit=360)
    result = design(fragments, ("f1", 80))
    assert overlaps(result) == [80, 26, 26]
    assert same_circle(result.candidates[0].product.sequence, target)
    assert any("360 nt" in n and "synthesis limit" in n for n in notes(result))


def test_the_synthesis_limit_is_respected_when_the_overlap_fits_under_it() -> None:
    target, fragments = plan(limit=360)
    result = design(fragments, ("f1", 60))
    assert overlaps(result) == [60, 26, 26]
    assert same_circle(result.candidates[0].product.sequence, target)
    assert max(lengths(result).values()) <= 360
    assert not any("synthesis limit" in n for n in notes(result))


def test_provenance_records_the_fragments_as_supplied() -> None:
    _, fragments = plan()
    result = design(fragments, ("f1", 40))
    shas = {item["fragment_id"]: item["sequence_sha256"] for item in result.source_provenance}
    assert shas["f1"] == hashlib.sha256(fragments[0].sequence.encode()).hexdigest()
    assert result.candidates[0].product.fragments[0].sequence == fragments[0].sequence


def test_a_marker_without_a_user_overlap_changes_nothing() -> None:
    target, fragments = plan()
    result = design(fragments)
    assert overlaps(result) == [26, 26, 26]
    assert {f.id: f.sequence for f in result.designed_fragments} == {f.id: f.sequence for f in fragments}


# --- through the routes ------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'recut.db'}")
    asyncio.run(init_molbio_db(engine=engine))
    sessions = make_molbio_session_factory(engine)

    async def override():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(molbio_router)
    app.include_router(suggest_router)
    app.dependency_overrides[get_molbio_session] = override
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(engine.dispose())


def suggest(client, **body):
    response = client.post("/api/molbio/assembly/gibson/suggest", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def design_route(client, request):
    response = client.post("/api/molbio/assembly/gibson/design", json=request)
    assert response.status_code == 200, response.text
    return response.json()


def test_suggest_marks_only_the_synthetic_fragments(client) -> None:
    target = dna(40, 2400)
    top = suggest(client, target={"sequence": target, "circular": True}, sources=[{"id": "x", "sequence": dna(41, 400)}],
                  fragment_count=3)["proposals"][0]
    fragments = top["design_request"]["fragments"]
    assert [f["preparation"] for f in fragments] == ["ready_linear"] * 3
    assert all((f.get("metadata") or {}).get(FLAG) is True for f in fragments)


def test_overlap_edit_on_an_all_synthetic_suggestion_is_applied_through_the_route(client) -> None:
    target = dna(40, 2400)
    top = suggest(client, target={"sequence": target, "circular": True}, sources=[{"id": "x", "sequence": dna(41, 400)}],
                  fragment_count=3)["proposals"][0]
    request = dict(top["design_request"])
    suggested = [j["overlap_length"] for j in top["junctions"]]
    request["junctions"] = [{"after_fragment_id": "f1", "overlap": 40}]
    edited = design_route(client, request)
    product = edited["selected_product"]
    assert same_circle(product["sequence"], target)
    got = [j["overlap_length"] for j in product["junctions"]]
    assert got[0] == 40 and got[1:] == suggested[1:]
    assert not any("not applied" in n for j in product["junctions"] for n in j["notes"])
    assert any("re-cut" in w for w in edited["warnings"])
    original = {f["id"]: f["sequence"] for f in request["fragments"]}
    changed = {f["id"] for f in edited["designed_fragments"] if f["sequence"] != original[f["id"]]}
    assert changed == {"f1", "f2"}


def test_unedited_suggestion_designs_exactly_as_suggested(client) -> None:
    target = dna(40, 2400)
    top = suggest(client, target={"sequence": target, "circular": True}, sources=[{"id": "x", "sequence": dna(41, 400)}],
                  fragment_count=3)["proposals"][0]
    plain = design_route(client, top["design_request"])
    assert [j["overlap_length"] for j in plain["selected_product"]["junctions"]] == [j["overlap_length"] for j in top["junctions"]]
    assert {f["id"]: f["sequence"] for f in plain["designed_fragments"]} == {f["id"]: f["sequence"] for f in top["design_request"]["fragments"]}


def test_edit_next_to_a_supplied_fragment_only_moves_the_synthetic_one(client) -> None:
    supplied = dna(52, 700)
    target = supplied + dna(53, 500)
    proposals = suggest(client, target={"sequence": target, "circular": True},
                        sources=[{"id": "s", "name": "S", "sequence": supplied, "circular": False}])["proposals"]
    top = next(p for p in proposals if p["counts"]["existing"] == 1 and p["counts"]["synthesis"] == 1)
    request = dict(top["design_request"])
    ids = [f["id"] for f in request["fragments"]]
    existing = next(f for f in request["fragments"] if f["preparation"] == "ready_linear" and not (f.get("metadata") or {}).get(FLAG))
    request["junctions"] = [{"after_fragment_id": existing["id"], "overlap": 44}]
    edited = design_route(client, request)
    assert same_circle(edited["selected_product"]["sequence"], target)
    designed = {f["id"]: f["sequence"] for f in edited["designed_fragments"]}
    assert designed[existing["id"]] == existing["sequence"]
    assert {j["overlap_length"] for j in edited["selected_product"]["junctions"]} >= {44}
    assert len(ids) == 2
