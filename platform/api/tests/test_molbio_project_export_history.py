import pytest
import pytest_asyncio

from molbio_database import create_molbio_engine, make_molbio_session_factory
from molbio_models import MolBioBase, NucleotideSequence
from services.molbio_persistence import record_sequence_revision, create_operation, add_operation_edges
from services.molbio_project_history import project_export_history


@pytest_asyncio.fixture
async def history_store(tmp_path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(MolBioBase.metadata.create_all)
    factory = make_molbio_session_factory(engine)
    async with factory() as session:
        sequence = NucleotideSequence(id='sequence', name='Public QA plasmid', sequence='AACCGGTT', sequence_type='dna',
            molecule_strandedness='double', molecule_orientation='forward', is_circular=True, length=8,
            features=[], primers=[], analysis_tracks=[], version=1, gc_content=50.0)
        session.add(sequence)
        first = await record_sequence_revision(session, sequence, change_kind='create')
        operation = await create_operation(session, operation_kind='alignment', implementation='test', parameters={}, provenance={}, idempotency_key='history-test', request_fingerprint='a' * 64)
        await add_operation_edges(session, operation, input_revisions=[(first, 'reference', None)])
        sequence.name = 'Annotated QA plasmid'
        sequence.version = 2
        second = await record_sequence_revision(session, sequence, change_kind='update')
        await session.commit()
        yield session, first.id, second.id, operation.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_history_includes_every_saved_revision_and_related_operation_without_sequence_payload(history_store):
    session, first, second, operation = history_store
    result = await project_export_history(session, 'sequence', second)
    assert result['complete'] is True
    assert result['selected_revision_id'] == second
    assert {entry['id'] for entry in result['entries']} == {first, second, operation}
    assert len(result['manifest_sha256']) == 64
    assert all('sequence' not in entry and 'snapshot' not in entry for entry in result['entries'])
    assert all(entry['reopen_href'].startswith('/designer?') for entry in result['entries'])


@pytest.mark.asyncio
async def test_exact_history_boundary_does_not_advance_to_newer_revision(history_store):
    session, first, second, operation = history_store
    result = await project_export_history(session, 'sequence', first)
    assert {entry['id'] for entry in result['entries']} == {first, operation}
    assert second not in {entry['id'] for entry in result['entries']}


@pytest.mark.asyncio
async def test_history_rejects_a_revision_from_a_different_sequence(history_store):
    session, first, _, _ = history_store
    with pytest.raises(LookupError):
        await project_export_history(session, 'other-sequence', first)


@pytest.mark.asyncio
async def test_history_limit_fails_instead_of_returning_an_incomplete_history(history_store, monkeypatch):
    import services.molbio_project_history as history
    session, _, second, _ = history_store
    monkeypatch.setattr(history, 'MAX_HISTORY_ITEMS', 1)
    with pytest.raises(OverflowError, match='nothing was truncated'):
        await history.project_export_history(session, 'sequence', second)


@pytest.mark.asyncio
async def test_saved_operation_receipt_uses_the_pm_lineage_digest_contract(history_store):
    from services.global_experiments.adapters import MolBioOperationAdapter
    session, _, _, operation = history_store
    receipt = await MolBioOperationAdapter(molbio_session_factory=lambda: session).verify(session, operation)
    lineage = receipt['metadata']['native_lineage']
    assert lineage
    assert all(item['receipt_content_digest'] == item['content_digest'] for item in lineage)
    assert all(len(item['receipt_content_digest']) == 64 for item in lineage)
