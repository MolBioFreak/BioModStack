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
from molbio_models import (  # noqa: E402
    MolecularDocument,
    MolecularOperationInput,
    MolecularOperationOutput,
)
from routers import molbio_ops, nucleotide_sequences  # noqa: E402
from routers.molbio_ops import DnaWeaverPlanRequest, router as molbio_router  # noqa: E402
from routers.nucleotide_sequences import NucleotideSequenceUpdate  # noqa: E402
from services.molbio_persistence import current_molecular_revision  # noqa: E402


@pytest.fixture(autouse=True)
def _exact_build_revision(monkeypatch) -> None:
    monkeypatch.setenv("BMS_BUILD_SHA", "a" * 40)


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
            source_response = client.post(
                "/api/sequences",
                json={
                    "name": "Immutable order target",
                    "sequence": _target(),
                    "sequence_type": "dna",
                    "is_circular": False,
                },
            )
            assert source_response.status_code in {200, 201}, source_response.text
            source_id = source_response.json()["id"]
            payload = {**payload, "target_sequence_id": source_id}
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
            assert plan["receipt_schema_version"] == "dnaweaver-gibson-plan-v4"
            assert plan["planner_implementation_revision"]
            assert len(plan["selected_product_checksum"]) == 64
            assert plan["target_attestation"] == {
                "sequence_id": source_id,
                "revision_id": plan["target_attestation"]["revision_id"],
                "revision_number": 1,
                "revision_sha256": plan["target_checksum"],
            }
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
            inline_save_response = client.post(
                "/api/molbio/assembly/gibson/dnaweaver/save",
                json={
                    **_payload(),
                    "selected_plan_checksum": checksum,
                    "new_name": "Inline target must fail",
                },
            )
            inline_plan_response = client.post(
                "/api/molbio/assembly/gibson/dnaweaver/plan",
                json=_payload(),
            )
            workups_response = client.get("/api/sequences/assembly-workups")

        assert forged_response.status_code == 400
        assert "checksum" in forged_response.json()["detail"].lower()
        assert stale_response.status_code == 400
        assert "selected target changed" in stale_response.json()["detail"].lower()
        assert valid_response.status_code == 200, valid_response.text
        assert inline_save_response.status_code == 400
        assert "persisted target" in inline_save_response.json()["detail"].lower()
        assert inline_plan_response.status_code == 400
        assert (
            "persisted immutable target"
            in inline_plan_response.json()["detail"].lower()
        )
        saved = valid_response.json()["saved_sequence"]
        with TestClient(app) as client:
            reloaded_response = client.get(f"/api/sequences/{saved['id']}")
        assert reloaded_response.status_code == 200, reloaded_response.text
        reloaded = reloaded_response.json()
        params = saved["operation_params"]

        async def saved_input_roles() -> list[str]:
            async with sessions() as session:
                revision_id = (
                    await session.execute(
                        select(MolecularDocument.current_revision_id).where(
                            MolecularDocument.id == saved["id"]
                        )
                    )
                ).scalar_one()
                operation_id = (
                    await session.execute(
                        select(MolecularOperationOutput.operation_id).where(
                            MolecularOperationOutput.revision_id == revision_id
                        )
                    )
                ).scalar_one()
                return list(
                    (
                        await session.execute(
                            select(MolecularOperationInput.role)
                            .where(MolecularOperationInput.operation_id == operation_id)
                            .order_by(MolecularOperationInput.position)
                        )
                    ).scalars()
                )

        assert asyncio.run(saved_input_roles())[0] == "target"
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
        assert (
            reloaded["operation_params"]["ordered_fragments"]
            == params["ordered_fragments"]
        )
        assert [item["sequence"] for item in params["ordered_fragments"]] == [
            item["sequence"] for item in plan["ordered_fragments"]
        ]
        assert workups_response.status_code == 200
        workups = workups_response.json()
        saved_listing = next(item for item in workups if item["id"] == saved["id"])
        assert saved_listing["engine"] == "dnaweaver"
        assert saved_listing["fragment_count"] == len(params["ordered_fragments"])
    finally:
        asyncio.run(engine.dispose())


def test_dnaweaver_save_revalidates_target_under_writer_lock(
    tmp_path: Path, monkeypatch
) -> None:
    engine = create_molbio_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'dnaweaver-target-race.db'}"
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

    original_persist = molbio_ops.persist_assembly_product
    source_id = ""

    async def mutate_source_then_persist(session, **kwargs):
        async with sessions() as competing_session:
            before = await current_molecular_revision(competing_session, source_id)
            assert before is not None
            changed_sequence = ("C" if _target()[0] != "C" else "A") + _target()[1:]
            updated = await nucleotide_sequences.update_sequence(
                source_id,
                NucleotideSequenceUpdate(sequence=changed_sequence),
                competing_session,
            )
            after = await current_molecular_revision(competing_session, source_id)
            assert after is not None
            assert after.id != before.id
            assert after.revision_number == before.revision_number + 1
            assert (
                after.content_sha256
                == hashlib.sha256(changed_sequence.encode("ascii")).hexdigest()
            )
            assert updated.sequence == changed_sequence
        return await original_persist(session, **kwargs)

    try:
        with TestClient(app) as client:
            source_response = client.post(
                "/api/sequences",
                json={
                    "name": "Immutable DNA Weaver target",
                    "sequence": _target(),
                    "sequence_type": "dna",
                    "is_circular": False,
                },
            )
            assert source_response.status_code in {200, 201}, source_response.text
            source_id = source_response.json()["id"]
            payload = {**_payload(), "target_sequence_id": source_id}
            plan_response = client.post(
                "/api/molbio/assembly/gibson/dnaweaver/plan", json=payload
            )
            assert plan_response.status_code == 200, plan_response.text
            plan = plan_response.json()
            assert plan["target_attestation"]["sequence_id"] == source_id
            assert plan["target_attestation"]["revision_id"]

            monkeypatch.setattr(
                molbio_ops, "persist_assembly_product", mutate_source_then_persist
            )
            save_response = client.post(
                "/api/molbio/assembly/gibson/dnaweaver/save",
                json={
                    **payload,
                    "selected_plan_checksum": plan["plan_checksum"],
                    "new_name": "Must not save stale target",
                },
            )

        assert save_response.status_code == 409, save_response.text
        assert "target" in save_response.json()["detail"].lower()
        assert "revision" in save_response.json()["detail"].lower()
    finally:
        asyncio.run(engine.dispose())
