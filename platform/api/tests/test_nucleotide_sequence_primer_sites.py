import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from molbio_database import create_molbio_engine, init_molbio_db
from routers import nucleotide_sequences


@pytest.fixture
def sequence_client(tmp_path: Path) -> Iterator[TestClient]:
    database = tmp_path / "primer-sites.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{database}")
    asyncio.run(init_molbio_db(engine=engine))
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_molbio_session():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[nucleotide_sequences.get_molbio_session] = (
        override_get_molbio_session
    )
    app.include_router(nucleotide_sequences.router)
    with TestClient(app) as client:
        yield client
    asyncio.run(engine.dispose())


def _primer(**overrides):
    primer = {
        "id": "primer-1",
        "name": "Primer 1",
        "sequence": "ACGT",
        "sequence_type": "dna",
        "start": 0,
        "end": 4,
        "strand": 1,
        "sites": [{"start": 0, "end": 4, "strand": 1, "tm": 60.0}],
    }
    primer.update(overrides)
    return primer


@pytest.mark.parametrize(
    "primer",
    [
        _primer(strand=7),
        _primer(sites=[{"start": -1, "end": 4, "strand": 1}]),
        _primer(sites=[{"start": 0, "end": 99, "strand": 1}]),
        _primer(sites=[{"start": 4, "end": 2, "strand": 1}]),
        _primer(sites=[{"start": 0, "end": 4, "strand": 0}]),
        _primer(start=7, end=2, sites=[{"start": 0, "end": 4, "strand": 1}]),
    ],
)
def test_post_rejects_invalid_primer_placement(
    sequence_client: TestClient,
    primer: dict,
) -> None:
    response = sequence_client.post(
        "/api/sequences/",
        json={
            "name": "Linear construct",
            "sequence": "ACGTACGT",
            "sequence_type": "dna",
            "is_circular": False,
            "primers": [primer],
        },
    )

    assert response.status_code == 400, response.text
    assert "primer" in response.json()["detail"].lower()


def test_put_rejects_invalid_primer_sites(sequence_client: TestClient) -> None:
    created = sequence_client.post(
        "/api/sequences/",
        json={
            "name": "Editable construct",
            "sequence": "ACGTACGT",
            "sequence_type": "dna",
            "is_circular": False,
        },
    )
    assert created.status_code == 200, created.text

    response = sequence_client.put(
        f"/api/sequences/{created.json()['id']}",
        json={
            "primers": [
                _primer(
                    start=0,
                    end=4,
                    sites=[{"start": 0, "end": 4, "strand": -1}],
                )
            ],
        },
    )

    assert response.status_code == 400, response.text
    assert "primer" in response.json()["detail"].lower()


def test_circular_primer_sites_round_trip_through_post_get_and_put(
    sequence_client: TestClient,
) -> None:
    circular_primer = _primer(
        name="Reverse origin wrap",
        sequence="ACGTACGT",
        start=12,
        end=4,
        strand=-1,
        sites=[
            {"start": 12, "end": 16, "strand": -1, "tm": 61.5},
            {"start": 0, "end": 4, "strand": -1, "tm": 61.5},
        ],
    )
    created = sequence_client.post(
        "/api/sequences/",
        json={
            "name": "Circular construct",
            "sequence": "ACGTACGTACGTACGT",
            "sequence_type": "dna",
            "is_circular": True,
            "primers": [circular_primer],
        },
    )
    assert created.status_code == 200, created.text
    sequence_id = created.json()["id"]
    expected_sites = circular_primer["sites"]
    assert created.json()["primers"][0]["sites"] == expected_sites

    fetched = sequence_client.get(f"/api/sequences/{sequence_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["primers"][0]["sites"] == expected_sites

    updated_primer = {**circular_primer, "name": "Updated reverse origin wrap"}
    updated = sequence_client.put(
        f"/api/sequences/{sequence_id}",
        json={"primers": [updated_primer]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["primers"][0]["name"] == "Updated reverse origin wrap"
    assert updated.json()["primers"][0]["sites"] == expected_sites


def test_put_rejects_sequence_shrink_that_orphans_existing_primer_site(
    sequence_client: TestClient,
) -> None:
    created = sequence_client.post(
        "/api/sequences/",
        json={
            "name": "Long linear construct",
            "sequence": "ACGTACGT",
            "sequence_type": "dna",
            "is_circular": False,
            "primers": [
                _primer(
                    sequence="ACGT",
                    start=4,
                    end=8,
                    sites=[{"start": 4, "end": 8, "strand": 1}],
                )
            ],
        },
    )
    assert created.status_code == 200, created.text

    response = sequence_client.put(
        f"/api/sequences/{created.json()['id']}",
        json={"sequence": "ACGT"},
    )

    assert response.status_code == 400, response.text
    assert "primer" in response.json()["detail"].lower()


def test_put_rejects_linearization_of_existing_origin_wrapping_primer(
    sequence_client: TestClient,
) -> None:
    circular_primer = _primer(
        sequence="ACGTACGT",
        start=12,
        end=4,
        strand=-1,
        sites=[
            {"start": 12, "end": 16, "strand": -1},
            {"start": 0, "end": 4, "strand": -1},
        ],
    )
    created = sequence_client.post(
        "/api/sequences/",
        json={
            "name": "Circular construct",
            "sequence": "ACGTACGTACGTACGT",
            "sequence_type": "dna",
            "is_circular": True,
            "primers": [circular_primer],
        },
    )
    assert created.status_code == 200, created.text

    response = sequence_client.put(
        f"/api/sequences/{created.json()['id']}",
        json={"is_circular": False},
    )

    assert response.status_code == 400, response.text
    assert "primer" in response.json()["detail"].lower()
