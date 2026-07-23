from __future__ import annotations

import asyncio
import hashlib
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

from molbio_database import (  # noqa: E402
    create_molbio_engine,
    get_molbio_session,
    init_molbio_db,
    make_molbio_session_factory,
)
from molbio_models import NucleotideSequence  # noqa: E402
from routers.molbio_ops import router as molbio_router  # noqa: E402
from services.assembly.pydna_gibson import design_gibson  # noqa: E402
from services.assembly.types import AssemblyError, AssemblyFragment  # noqa: E402


def _dna(seed: int, length: int = 180) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(length))


def _fragment(index: int) -> AssemblyFragment:
    return AssemblyFragment(
        id=f"fragment-{index}",
        name=f"Fragment {index}",
        sequence=_dna(index),
        source_sequence_id=f"source-{index}",
        source_name=f"Source {index}",
        source_revision=index,
        source_start=0,
        source_end=180,
    )


def _request_fragment(
    index: int,
    *,
    preparation: str = "pcr",
    inline: bool = False,
) -> dict[str, object]:
    fragment = _fragment(index)
    return {
        "id": fragment.id,
        "name": fragment.name,
        "sequence": fragment.sequence,
        "preparation": preparation,
        "source_sequence_id": None if inline else fragment.source_sequence_id,
        "source_name": None if inline else fragment.source_name,
        "source_revision": None if inline else fragment.source_revision,
        "source_start": fragment.source_start,
        "source_end": fragment.source_end,
    }


def _design_payload(*, inline: bool = False) -> dict[str, object]:
    return {
        "fragments": [
            _request_fragment(1, inline=inline),
            _request_fragment(2, inline=inline),
        ],
        "circular": False,
        "overlap": 24,
        "target_tm": 55.0,
        "min_anneal": 15,
    }


def test_three_pcr_fragments_design_exact_circular_candidate() -> None:
    result = design_gibson(
        [_fragment(1), _fragment(2), _fragment(3)],
        preparations=["pcr", "pcr", "pcr"],
        circular=True,
        overlap=24,
        target_tm=55.0,
        min_anneal=15,
    )

    assert result.engine == "pydna"
    assert result.engine_version == "5.5.16"
    assert len(result.primers) == 6
    assert len(result.designed_fragments) == 3
    assert len(result.candidates) <= 10
    assert result.selected_candidate_checksum is not None

    selected = next(
        candidate
        for candidate in result.candidates
        if candidate.checksum == result.selected_candidate_checksum
    )
    assert selected.circular is True
    assert selected.exact_match is True
    assert len(selected.product.junctions) == 3
    assert all(
        primer.full_sequence.endswith(primer.annealing_sequence)
        for primer in result.primers
    )
    assert all(
        primer.full_sequence == primer.tail_sequence + primer.annealing_sequence
        for primer in result.primers
    )
    assert [item["source_revision"] for item in result.source_provenance] == [1, 2, 3]


def test_two_fragments_design_exact_linear_candidate_with_ready_linear_input() -> None:
    fragments = [_fragment(1), _fragment(2)]

    result = design_gibson(
        fragments,
        preparations=["ready_linear", "pcr"],
        circular=False,
        overlap=24,
        target_tm=55.0,
        min_anneal=15,
    )

    assert len(result.primers) == 2
    assert result.designed_fragments[0].primer_ids == []
    assert result.designed_fragments[0].sequence == fragments[0].sequence
    selected = next(item for item in result.candidates if item.exact_match)
    expected = fragments[0].sequence + fragments[1].sequence
    assert selected.sequence == expected
    assert selected.checksum == hashlib.sha256(expected.encode("ascii")).hexdigest()
    assert selected.circular is False
    assert len(selected.junctions) == 1


def test_unusable_and_invalid_design_requests_are_actionable() -> None:
    with pytest.raises(AssemblyError, match="2 to 20"):
        design_gibson(
            [_fragment(1)],
            preparations=["pcr"],
            circular=False,
        )

    with pytest.raises(AssemblyError, match="adjacent ready_linear"):
        design_gibson(
            [_fragment(1), _fragment(2)],
            preparations=["ready_linear", "ready_linear"],
            circular=False,
            overlap=24,
        )

    app = FastAPI()
    app.include_router(molbio_router)
    with TestClient(app) as client:
        response = client.post(
            "/api/molbio/assembly/gibson/design",
            json={
                "fragments": [
                    {
                        "id": "only-fragment",
                        "name": "Only fragment",
                        "sequence": _dna(9),
                        "preparation": "pcr",
                    }
                ],
                "circular": False,
            },
        )
    assert response.status_code == 400
    assert "2 to 20" in response.json()["detail"]

    with pytest.raises(AssemblyError, match="2 to 20"):
        design_gibson(
            [_fragment(index) for index in range(1, 22)],
            preparations=["pcr"] * 21,
            circular=False,
        )


def test_design_route_serializes_engine_primers_fragments_and_checksum() -> None:
    app = FastAPI()
    app.include_router(molbio_router)

    with TestClient(app) as client:
        response = client.post(
            "/api/molbio/assembly/gibson/design",
            json=_design_payload(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["engine"] == "pydna"
    assert payload["engine_version"] == "5.5.16"
    assert len(payload["primers"]) == 4
    assert {
        "full_sequence",
        "annealing_sequence",
        "tail_sequence",
        "tm",
    } <= payload["primers"][0].keys()
    assert len(payload["designed_fragments"]) == 2
    assert len(payload["designed_fragments"][0]["checksum"]) == 64
    assert payload["selected_candidate_checksum"] in {
        candidate["checksum"] for candidate in payload["candidates"]
    }


def test_design_save_rejects_forged_checksum_and_persists_server_candidate(
    tmp_path: Path,
) -> None:
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'pydna-save.db'}")
    asyncio.run(init_molbio_db(engine=engine))
    sessions = make_molbio_session_factory(engine)

    async def override_session():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(molbio_router)
    app.dependency_overrides[get_molbio_session] = override_session
    payload = _design_payload(inline=True)

    try:
        with TestClient(app) as client:
            design_response = client.post(
                "/api/molbio/assembly/gibson/design",
                json=payload,
            )
            checksum = design_response.json()["selected_candidate_checksum"]

            forged_response = client.post(
                "/api/molbio/assembly/gibson/design/save",
                json={
                    **payload,
                    "selected_candidate_checksum": "0" * 64,
                    "new_name": "Forged product",
                },
            )
            valid_response = client.post(
                "/api/molbio/assembly/gibson/design/save",
                json={
                    **payload,
                    "selected_candidate_checksum": checksum,
                    "new_name": "Designed product",
                },
            )

        assert forged_response.status_code == 400
        assert "checksum" in forged_response.json()["detail"].lower()
        assert valid_response.status_code == 200
        response_payload = valid_response.json()
        saved_payload = response_payload["saved_sequence"]
        assert saved_payload["sequence"] == response_payload["selected_product"]["sequence"]
        assert saved_payload["operation_params"]["engine"] == "pydna"
        assert saved_payload["operation_params"]["candidate_checksum"] == checksum
        assert len(saved_payload["operation_params"]["primers"]) == 4

        async def load_saved() -> NucleotideSequence | None:
            async with sessions() as session:
                return await session.scalar(
                    select(NucleotideSequence).where(
                        NucleotideSequence.name == "Designed product"
                    )
                )

        saved = asyncio.run(load_saved())
        assert saved is not None
        assert saved.operation_params["candidate_checksum"] == checksum
    finally:
        asyncio.run(engine.dispose())
