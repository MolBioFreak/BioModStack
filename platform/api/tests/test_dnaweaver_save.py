from __future__ import annotations

import asyncio
import hashlib
import random
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from molbio_database import (  # noqa: E402
    create_molbio_engine,
    get_molbio_session,
    init_molbio_db,
    make_molbio_session_factory,
)
from routers import nucleotide_sequences  # noqa: E402
from routers.molbio_ops import DnaWeaverPlanRequest, router as molbio_router  # noqa: E402


def test_dnaweaver_request_default_price_matches_generic_profile() -> None:
    request = DnaWeaverPlanRequest(target_sequence=_target())
    assert request.price_per_bp == 0.08


def _target(seed: int = 23, length: int = 900) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(length))


def _payload() -> dict[str, object]:
    return {
        "target_sequence": _target(),
        "circular": False,
        "min_fragment_length": 250,
        "max_fragment_length": 450,
        "overlap_length": 30,
        "vendor_name": "Generic synthesis profile",
        "price_per_bp": 0.15,
        "lead_time_days": 10,
    }


def test_dnaweaver_save_rejects_forged_plan_and_persists_regenerated_evidence(
    tmp_path: Path,
) -> None:
    engine = create_molbio_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'dnaweaver-save.db'}"
    )
    asyncio.run(init_molbio_db(engine=engine))
    sessions = make_molbio_session_factory(engine)

    async def override_session():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(molbio_router)
    app.include_router(nucleotide_sequences.router)
    app.dependency_overrides[get_molbio_session] = override_session
    app.dependency_overrides[nucleotide_sequences.get_molbio_session] = override_session
    payload = _payload()

    try:
        with TestClient(app) as client:
            plan_response = client.post(
                "/api/molbio/assembly/gibson/dnaweaver/plan",
                json=payload,
            )
            assert plan_response.status_code == 200, plan_response.text
            plan = plan_response.json()
            checksum = plan["plan_checksum"]
            assert len(checksum) == 64
            assert (
                plan["target_checksum"]
                == hashlib.sha256(_target().encode("ascii")).hexdigest()
            )
            assert plan["order_ready"] is True
            assert plan["quality_checks"]
            assert all(
                len(fragment["sequence_sha256"]) == 64
                for fragment in plan["ordered_fragments"]
            )

            forged_response = client.post(
                "/api/molbio/assembly/gibson/dnaweaver/save",
                json={
                    **payload,
                    "selected_plan_checksum": "0" * 64,
                    "new_name": "Forged plan",
                },
            )
            stale_response = client.post(
                "/api/molbio/assembly/gibson/dnaweaver/save",
                json={
                    **payload,
                    "target_sequence": _target(seed=24),
                    "selected_plan_checksum": checksum,
                    "new_name": "Stale plan",
                },
            )
            valid_response = client.post(
                "/api/molbio/assembly/gibson/dnaweaver/save",
                json={
                    **payload,
                    "selected_plan_checksum": checksum,
                    "new_name": "Saved plan",
                },
            )
            workups_response = client.get("/api/sequences/assembly-workups")

        assert forged_response.status_code == 400
        assert "checksum" in forged_response.json()["detail"].lower()
        assert stale_response.status_code == 400
        assert "checksum" in stale_response.json()["detail"].lower()
        assert valid_response.status_code == 200, valid_response.text
        saved = valid_response.json()["saved_sequence"]
        params = saved["operation_params"]
        assert saved["is_circular"] is False
        assert params["engine"] == "dnaweaver"
        assert params["validator_engine"] == "pydna"
        assert params["plan_checksum"] == checksum
        assert params["target_checksum"] == plan["target_checksum"]
        assert params["pydna_exact_candidate_count"] >= 1
        assert params["planning_parameters"]["overlap_length"] == 30
        assert params["ordered_fragments"]
        assert all(item["sequence"] for item in params["ordered_fragments"])
        assert all(
            len(item["sequence_sha256"]) == 64 for item in params["ordered_fragments"]
        )
        assert workups_response.status_code == 200
        workups = workups_response.json()
        saved_listing = next(item for item in workups if item["id"] == saved["id"])
        assert saved_listing["engine"] == "dnaweaver"
        assert saved_listing["fragment_count"] == len(params["ordered_fragments"])
    finally:
        asyncio.run(engine.dispose())
