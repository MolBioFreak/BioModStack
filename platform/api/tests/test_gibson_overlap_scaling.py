"""Gibson/HiFi Suggest: with no overlap set, the overlap follows NEB's guidance for the number of fragments.

NEBuilder HiFi protocol (E2621/E5520): 15-20 nt overlaps for 2-3 fragments, 20-30 nt for 4-6 fragments.
"""
import asyncio
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


def dna(seed, n):
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(n))


def same_circle(a, b):
    return len(a) == len(b) and a in b + b


@pytest.fixture
def client(tmp_path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'scale.db'}")
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


SOURCE = [{"id": "x", "sequence": dna(41, 400)}]


def top_plan(client, size, **extra):
    target = dna(40, size)
    response = client.post("/api/molbio/assembly/gibson/suggest",
                           json={"target": {"sequence": target, "circular": True}, "sources": SOURCE, **extra})
    assert response.status_code == 200, response.text
    return target, response.json()["proposals"][0]


def overlaps(plan):
    return [j["overlap_length"] for j in plan["junctions"]]


def rebuilds(client, plan, target):
    response = client.post("/api/molbio/assembly/gibson/design", json=plan["design_request"])
    assert response.status_code == 200, response.text
    return same_circle(response.json()["selected_product"]["sequence"], target)


@pytest.mark.parametrize("count,expected", [(1, 20), (2, 20), (3, 20), (4, 30), (5, 30), (6, 30), (12, 30)])
def test_the_rule_by_fragment_count(count, expected):
    from services.assembly.gibson_suggest import scaled_overlap
    assert scaled_overlap(count) == expected


def test_two_fragments_use_20_nt(client):
    target, plan = top_plan(client, 5000)
    assert len(plan["fragments"]) == 2
    assert overlaps(plan) == [20, 20]
    assert plan["design_request"]["overlap"] == 20
    assert rebuilds(client, plan, target)


def test_three_fragments_use_20_nt(client):
    target, plan = top_plan(client, 5000, fragment_count=3, max_synthesis_length=2000)
    assert len(plan["fragments"]) == 3
    assert overlaps(plan) == [20, 20, 20]
    assert rebuilds(client, plan, target)


def test_four_fragments_use_30_nt(client):
    target, plan = top_plan(client, 5000, fragment_count=4, max_synthesis_length=2000)
    assert len(plan["fragments"]) == 4
    assert overlaps(plan) == [30, 30, 30, 30]
    assert plan["design_request"]["overlap"] == 30
    assert rebuilds(client, plan, target)


def test_the_count_the_plan_ends_with_decides_not_the_count_it_started_with(client):
    # 10.5 kb with the default 3000 nt limit cannot be cut into fewer than four pieces.
    target, plan = top_plan(client, 10488)
    assert len(plan["fragments"]) == 4
    assert set(overlaps(plan)) == {30}
    assert rebuilds(client, plan, target)


def test_more_than_six_fragments_is_noted_not_refused(client):
    target, plan = top_plan(client, 5000, fragment_count=7, max_synthesis_length=1500)
    assert len(plan["fragments"]) == 7
    assert set(overlaps(plan)) == {30}
    assert any("more than the 2-6" in note for note in plan["notes"]), plan["notes"]
    assert rebuilds(client, plan, target)


def test_six_fragments_carry_no_beyond_guidance_note(client):
    _, plan = top_plan(client, 5000, fragment_count=6, max_synthesis_length=1500)
    assert len(plan["fragments"]) == 6
    assert not any("more than the 2-6" in note for note in plan["notes"])


def test_an_overlap_the_user_sets_is_used_instead_of_the_rule(client):
    target, plan = top_plan(client, 5000, fragment_count=4, max_synthesis_length=2000, overlap=24)
    assert overlaps(plan) == [24, 24, 24, 24]
    assert plan["design_request"]["overlap"] == 24
    assert not any("NEB" in note and "guidance" in note for note in plan["notes"])
    assert rebuilds(client, plan, target)


def test_the_overlap_that_follows_the_rule_is_explained(client):
    _, plan = top_plan(client, 5000, fragment_count=4, max_synthesis_length=2000)
    assert any("30 nt" in note and "NEB" in note for note in plan["notes"]), plan["notes"]
