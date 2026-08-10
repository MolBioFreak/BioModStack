from __future__ import annotations

import sys
from pathlib import Path

import pytest
from Bio.Seq import Seq
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import DatabaseError

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from molbio_database import (  # noqa: E402
    create_molbio_engine,
    init_molbio_db,
    make_molbio_session_factory,
)
from molbio_models import (  # noqa: E402
    MolecularOperationInput,
    MolecularOperationOutput,
    NucleotideSequence,
    PCRExperimentRevision,
)
from routers.molbio_ops import (  # noqa: E402
    PCRRequest,
    PCRReviewStateRequest,
    get_pcr_experiment,
    list_pcr_experiments,
    pcr,
    update_pcr_experiment_review_state,
)
from services.molbio_persistence import record_sequence_revision  # noqa: E402


async def _seed_template(session) -> NucleotideSequence:
    sequence = "ATGCGTACGTTAGCTAGCTAGGCTAACCGGTTACGATCGATCGTACGTTAGC"
    template = NucleotideSequence(
        id="template-1",
        name="PCR template",
        sequence=sequence,
        sequence_type="dna",
        molecule_strandedness="double",
        molecule_orientation="both",
        is_circular=False,
        length=len(sequence),
        features=[],
        primers=[],
        analysis_tracks=[],
        version=1,
    )
    session.add(template)
    await record_sequence_revision(
        session,
        template,
        change_kind="create",
        provenance={"source": "test"},
    )
    await session.commit()
    return template


@pytest.mark.asyncio
async def test_pcr_experiment_persists_separately_and_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "molbio.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)

    async with sessions() as session:
        template = await _seed_template(session)
        primer_fwd = template.sequence[:12]
        primer_rev = str(Seq(template.sequence[-12:]).reverse_complement())
        request = PCRRequest(
            sequence_id=template.id,
            primer_fwd=primer_fwd,
            primer_rev=primer_rev,
            save=False,
            persist_experiment=True,
            idempotency_key="pcr-test-1",
            reaction_settings={"reaction_volume_uL": 25.0},
            cycling_assumptions={"cycles": 30, "extension_seconds": 30},
            notes="scientific audit test",
            review_state="draft",
        )
        first = await pcr(request, session)
        second = await pcr(request, session)

        assert first.experiment_id
        assert first.experiment_revision_id
        assert first.operation_id
        assert first.reused is False
        assert first.sequence is None
        assert second.experiment_id == first.experiment_id
        assert second.experiment_revision_id == first.experiment_revision_id
        assert second.reused is True

        sequence_count = (
            await session.execute(select(func.count()).select_from(NucleotideSequence))
        ).scalar_one()
        pcr_count = (
            await session.execute(select(func.count()).select_from(PCRExperimentRevision))
        ).scalar_one()
        revision = await session.get(PCRExperimentRevision, first.experiment_revision_id)
        assert sequence_count == 1
        assert pcr_count == 1
        assert revision is not None
        assert revision.template_revision_id is not None
        assert revision.template_sha256
        assert revision.forward_primer_snapshot["sequence"] == primer_fwd
        assert revision.reverse_primer_snapshot["sequence"] == primer_rev
        assert revision.tm_model_revision_id is not None
        assert revision.reaction_settings == {"reaction_volume_uL": 25.0}
        assert revision.cycling_assumptions == {"cycles": 30, "extension_seconds": 30}
        assert revision.product_document_id is None
        assert revision.notes == "scientific audit test"

        review = await update_pcr_experiment_review_state(
            first.experiment_id,
            PCRReviewStateRequest(
                review_state="in_review",
                notes="ready for independent review",
            ),
            session,
        )
        assert review["revision_number"] == 2
        assert review["review_state"] == "in_review"
        assert review["created_by"] == "system:molbio-api"
        detail = await get_pcr_experiment(first.experiment_id, session)
        listing = await list_pcr_experiments(100, session)
        assert detail["current_revision_id"] == review["id"]
        assert [item["revision_number"] for item in detail["revisions"]] == [2, 1]
        assert listing["count"] == 1
        assert listing["items"][0]["review_state"] == "in_review"

        with pytest.raises(DatabaseError, match="immutable"):
            await session.execute(
                text("UPDATE pcr_experiment_revisions SET notes='tampered' WHERE id=:id"),
                {"id": first.experiment_revision_id},
            )
            await session.commit()
        await session.rollback()

    await engine.dispose()


@pytest.mark.asyncio
async def test_pcr_saved_product_has_immutable_revision_and_lineage(tmp_path: Path):
    db_path = tmp_path / "molbio.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)

    async with sessions() as session:
        template = await _seed_template(session)
        request = PCRRequest(
            sequence_id=template.id,
            primer_fwd=template.sequence[:12],
            primer_rev=str(Seq(template.sequence[-12:]).reverse_complement()),
            save=True,
            new_name="PCR product",
            persist_experiment=True,
            idempotency_key="pcr-test-save-1",
        )
        response = await pcr(request, session)
        assert response.sequence is not None
        assert response.experiment_revision_id is not None

        experiment = await session.get(PCRExperimentRevision, response.experiment_revision_id)
        assert experiment is not None
        assert experiment.product_document_id == response.sequence.id
        assert experiment.product_revision_id is not None
        input_count = (
            await session.execute(
                select(func.count())
                .select_from(MolecularOperationInput)
                .where(MolecularOperationInput.operation_id == response.operation_id)
            )
        ).scalar_one()
        output_count = (
            await session.execute(
                select(func.count())
                .select_from(MolecularOperationOutput)
                .where(MolecularOperationOutput.operation_id == response.operation_id)
            )
        ).scalar_one()
        assert input_count == 1
        assert output_count == 1

        with pytest.raises(DatabaseError, match="immutable"):
            await session.execute(
                text("UPDATE molecular_revisions SET change_kind='tampered' WHERE id=:id"),
                {"id": experiment.product_revision_id},
            )
            await session.commit()
        await session.rollback()

    await engine.dispose()


@pytest.mark.asyncio
async def test_pcr_unknown_polymerase_revision_fails_without_partial_rows(tmp_path: Path):
    db_path = tmp_path / "molbio.db"
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)

    async with sessions() as session:
        template = await _seed_template(session)
        request = PCRRequest(
            sequence_id=template.id,
            primer_fwd=template.sequence[:12],
            primer_rev=str(Seq(template.sequence[-12:]).reverse_complement()),
            save=True,
            persist_experiment=True,
            idempotency_key="pcr-test-invalid-polymerase",
            polymerase_preset_revision_id="not-a-real-revision",
        )
        with pytest.raises(HTTPException) as exc_info:
            await pcr(request, session)
        assert exc_info.value.status_code == 400
        assert "Unknown polymerase preset revision" in str(exc_info.value.detail)

        sequence_count = (
            await session.execute(select(func.count()).select_from(NucleotideSequence))
        ).scalar_one()
        pcr_count = (
            await session.execute(select(func.count()).select_from(PCRExperimentRevision))
        ).scalar_one()
        assert sequence_count == 1
        assert pcr_count == 0

    await engine.dispose()
