import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Design, Job
from services.result_ingester import ingest_esmfold2_results
from services.result_state_integrity import finalize_successful_job
from services import core_protein_result_contract as contract


async def setup(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "test.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


def artifacts(tmp_path, ids=('a', 'b')):
    root = tmp_path / 'esmfold2_results'
    root.mkdir(exist_ok=True)
    samples = []
    for candidate in ids:
        (root / f'{candidate}.pdb').write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n')
        (root / f'{candidate}.metrics.json').write_text(json.dumps({'sample_id': candidate, 'cif': f'{candidate}.pdb', 'iptm': 0.8}))
        samples.append({'sample_id': candidate, 'cif': f'{candidate}.pdb', 'metrics': f'{candidate}.metrics.json'})
    (root / 'manifest.json').write_text(json.dumps({'workflow': 'esmfold2', 'sample_count': len(ids), 'samples': samples}))
    return root


def job(tmp_path, marked=True):
    return Job(id='job', name='job', model_id='esmfold2', mode='predict', params={},
               status='running', queue_status='running', awaiting_input=False,
               output_dir=str(tmp_path), provenance={'core_protein_scientific_contract': 1} if marked else {})


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'foreign'])
async def test_declared_set_prevalidated_before_any_row(tmp_path, damage):
    root = artifacts(tmp_path)
    if damage == 'missing':
        (root / 'b.pdb').unlink()
    elif damage == 'duplicate':
        manifest = json.loads((root / 'manifest.json').read_text())
        manifest['samples'][1] = manifest['samples'][0]
        (root / 'manifest.json').write_text(json.dumps(manifest))
    else:
        (root / 'b.metrics.json').write_text(json.dumps({'sample_id': 'foreign', 'cif': 'b.pdb'}))
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current)
            await session.commit()
            with pytest.raises(RuntimeError):
                await ingest_esmfold2_results('job', tmp_path, session, current, commit=False)
            await session.commit()
            assert list((await session.execute(select(Design))).scalars()) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_replay_is_identical_or_rejected(tmp_path):
    root = artifacts(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current)
            await session.commit()
            assert await ingest_esmfold2_results('job', tmp_path, session, current) == 2
            rows = list((await session.execute(select(Design))).scalars())
            original = [(d.id, d.confidence_metrics) for d in rows]
            assert await ingest_esmfold2_results('job', tmp_path, session, current) == 0
            assert [(d.id, d.confidence_metrics) for d in rows] == original
            (root / 'b.metrics.json').write_text(json.dumps({'sample_id': 'b', 'cif': 'b.pdb', 'iptm': 0.3}))
            with pytest.raises(RuntimeError, match='replay'):
                await ingest_esmfold2_results('job', tmp_path, session, current)
    finally:
        await engine.dispose()


def test_intentional_rejection_accounts_without_requested_count_equality():
    validate = getattr(contract, 'validate_candidate_accounting', None)
    assert callable(validate), 'candidate accounting validator is missing'
    summary = validate(stage_id='filter', requested_count=8, generated_ids=['a', 'b'],
                       dispositions=[{'candidate_id': 'a', 'disposition': 'selected'},
                                     {'candidate_id': 'b', 'disposition': 'rejected', 'criterion': 'iptm >= 0.7', 'reason_code': 'below_threshold'}],
                       expected_publication_ids=['a'], persisted_ids=['a'])
    assert summary == {'stage_id': 'filter', 'requested_count': 8, 'generated_count': 2, 'selected_count': 1, 'published_count': 1,
                       'expected_publication_count': 1, 'rejected_count': 1, 'failed_count': 0, 'unevaluable_count': 0}


@pytest.mark.asyncio
async def test_prior_rows_plus_loss_fails_with_retained_partial(tmp_path):
    root = artifacts(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current)
            await session.commit()
            await ingest_esmfold2_results('job', tmp_path, session, current)
            (root / 'b.pdb').unlink()
            async def ingest(jid, output, db, **kwargs):
                return await ingest_esmfold2_results(jid, Path(output), db, await db.get(Job, jid), commit=False)
            result = await finalize_successful_job(current, str(tmp_path), session, ingest_fn=ingest)
            await session.refresh(current)
            assert not result.completed
            assert current.status == 'failed'
            assert current.provenance['result_integrity']['partial'] is True
            assert current.provenance['result_integrity']['reason']['code'] == 'candidate_artifact_missing'
    finally:
        await engine.dispose()



@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['substitute', 'duplicate', 'extra', 'all_missing', 'receipt_missing'])
async def test_generic_finalizer_revalidates_exact_persisted_set(tmp_path, damage):
    root = artifacts(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current)
            await session.commit()
            await ingest_esmfold2_results('job', tmp_path, session, current)
            rows = list((await session.execute(select(Design))).scalars())
            if damage == 'substitute':
                rows[1].name = 'foreign'
            elif damage == 'duplicate':
                rows[1].name = rows[0].name
            elif damage == 'extra':
                session.add(Design(id='extra', job_id='job', name='extra', pdb_path=rows[0].pdb_path))
            elif damage == 'receipt_missing':
                current.provenance = {'core_protein_scientific_contract': 1}
            else:
                (root / 'a.pdb').unlink()
                (root / 'b.pdb').unlink()
            await session.commit()
            async def noop(*args, **kwargs):
                return 0
            result = await finalize_successful_job(current, str(tmp_path), session, ingest_fn=noop)
            assert not result.completed
            await session.refresh(current)
            assert current.status == 'failed'
            assert current.provenance['result_integrity']['state'] != 'validated'
            if damage == 'all_missing':
                assert current.provenance['result_integrity']['partial'] is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', ['cancelled', 'awaiting_input'])
async def test_two_session_authority_wins_after_ingest_commit(tmp_path, terminal):
    from sqlalchemy import update
    artifacts(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current)
            await session.commit()
            async def competing_ingest(jid, output, db, **kwargs):
                count = await ingest_esmfold2_results(jid, Path(output), db, await db.get(Job, jid))
                async with factory() as competing:
                    await competing.execute(update(Job).where(Job.id == jid).values(
                        status=terminal, queue_status='cancelled' if terminal == 'cancelled' else 'completed',
                        awaiting_input=terminal == 'awaiting_input', error_message='operator authority'))
                    await competing.commit()
                return count
            result = await finalize_successful_job(current, str(tmp_path), session, ingest_fn=competing_ingest)
            assert not result.completed
            await session.refresh(current)
            assert current.status == terminal
            assert current.error_message == 'operator authority'
            assert 'result_integrity' not in current.provenance
    finally:
        await engine.dispose()


@pytest.mark.parametrize('persisted', [['foreign'], ['a', 'a'], ['a', 'extra']])
def test_equal_count_substitution_duplicates_and_extras(persisted):
    with pytest.raises(RuntimeError):
        contract.validate_candidate_accounting(stage_id='filter', requested_count=None, generated_ids=['a'],
            dispositions=[{'candidate_id': 'a', 'disposition': 'selected'}],
            expected_publication_ids=['a'], persisted_ids=persisted)


def test_zero_survivors_are_valid_accounting_not_invented_candidates():
    summary = contract.validate_candidate_accounting(stage_id='filter', requested_count=5, generated_ids=['a'],
        dispositions=[{'candidate_id': 'a', 'disposition': 'unevaluable', 'reason_code': 'missing_metric'}],
        expected_publication_ids=[], persisted_ids=[])
    assert summary['published_count'] == 0
    assert summary['generated_count'] == 1



@pytest.mark.asyncio
async def test_unmarked_ingress_retains_missing_sample_legacy_behavior(tmp_path):
    root = artifacts(tmp_path)
    (root / 'b.pdb').unlink()
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path, marked=False)
            session.add(current)
            await session.commit()
            assert await ingest_esmfold2_results('job', tmp_path, session, current) == 1
            row = (await session.execute(select(Design))).scalar_one()
            assert 'core_protein_scientific_contract' not in row.confidence_metrics
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_native_zero_survivor_finalizer_keeps_no_candidates(tmp_path):
    from services.result_ingester import ShapeNoCandidates
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = 'protein_modification_experimental'
            current.mode = 'shape_blueprint'
            session.add(current)
            await session.commit()
            async def no_survivors(*args, **kwargs):
                raise ShapeNoCandidates({'code': 'no_candidates', 'message': 'none survived'})
            result = await finalize_successful_job(current, str(tmp_path), session, ingest_fn=no_survivors)
            assert result.integrity_state == 'no_candidates'
            await session.refresh(current)
            assert current.provenance['result_integrity']['partial'] is False
            assert current.provenance['result_integrity']['state'] == 'no_candidates'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_invalid_revision_finalizes_failure_not_exception(tmp_path):
    artifacts(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.provenance = {'core_protein_scientific_contract': True}
            session.add(current)
            await session.commit()
            async def ingress(jid, output, db, **kwargs):
                return await ingest_esmfold2_results(jid, Path(output), db, await db.get(Job, jid), commit=False)
            result = await finalize_successful_job(current, str(tmp_path), session, ingest_fn=ingress)
            assert not result.completed
            await session.refresh(current)
            assert current.status == 'failed'
    finally:
        await engine.dispose()



# Explicit valid toy coordinates for parser/transaction tests, not model output.
TOY_CIF = '''data_toy
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_asym_id
_atom_site.pdbx_PDB_model_num
ATOM 1 C CA . ALA A 1 1 ? 1.000 2.000 3.000 1.00 80.00 1 A 1
#
'''


def replace_last_structure(root, suffix, content):
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest['samples'][-1]['cif'] = f'b.{suffix}'
    (root / 'manifest.json').write_text(json.dumps(manifest))
    (root / f'b.{suffix}').write_text(content)
    (root / 'b.metrics.json').write_text(json.dumps({'sample_id': 'b', 'cif': f'b.{suffix}', 'iptm': 0.8}))


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix,content', [
    ('pdb', 'not a structure\n'), ('cif', 'not a structure\n'),
    ('pdb', 'HEADER    atomless toy\nEND\n'), ('cif', 'data_atomless\n#\n'),
    ('pdb', 'ATOM      1  CA  ALA A   1         NaN   2.000   3.000  1.00 80.00           C\nEND\n'),
    ('pdb', 'ATOM      1  CA  ALA A   1         inf   2.000   3.000  1.00 80.00           C\nEND\n'),
    ('pdb', 'ATOM      1  CA  ALA A   1         bad   2.000   3.000  1.00 80.00           C\nEND\n'),
    ('cif', TOY_CIF.replace('1.000', 'nan')),
    ('cif', TOY_CIF.replace('1.000', 'inf')),
    ('cif', TOY_CIF.replace('1.000', 'bad')),
])
async def test_direct_dispatch_invalid_last_structure_preserves_transaction(tmp_path, suffix, content):
    from services.result_ingester import ingest_job_results
    from sqlalchemy import event
    root = artifacts(tmp_path)
    replace_last_structure(root, suffix, content)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            review = Design(id='review', job_id='job', name='review', source_stage='review', pdb_path=str(root / 'a.pdb'))
            session.add_all([current, review])
            await session.commit()
            # Caller-owned pending work must not be autoflushed or rolled back.
            review.name = 'caller edit'
            await session.connection()
            transaction = session.get_transaction()
            writes = []
            def track(conn, cursor, statement, parameters, context, executemany):
                if statement.lstrip().upper().startswith(('DELETE', 'UPDATE', 'INSERT')):
                    writes.append(statement)
            event.listen(engine.sync_engine, 'before_cursor_execute', track)
            with pytest.raises(contract.CandidateIntegrityError) as error:
                await ingest_job_results('job', str(tmp_path), session, commit=False)
            assert error.value.reason['code'] == 'candidate_structure_invalid'
            assert writes == []
            assert session.get_transaction() is transaction
            assert review in session.dirty and not session.new and not session.deleted
            assert current.provenance == {'core_protein_scientific_contract': 1}
            await session.commit()  # Catch-and-commit must not lose existing rows.
        async with factory() as reader:
            rows = list((await reader.execute(select(Design))).scalars())
            assert [(r.id, r.name) for r in rows] == [('review', 'caller edit')]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix', ['pdb', 'cif', 'mmcif'])
async def test_valid_toy_structures_use_only_hashed_snapshots(tmp_path, monkeypatch, suffix):
    import hashlib
    from services import result_ingester as ingester
    root = artifacts(tmp_path)
    if suffix != 'pdb':
        replace_last_structure(root, suffix, TOY_CIF)
    source = (root / f'b.{suffix}').read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('strict publication must not reread legacy payload/structure paths')
    for name in ('_load_json_payload', '_esmfold2_sample_entries', 'extract_plddt_from_pdb'):
        monkeypatch.setattr(ingester, name, forbidden)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add_all([current, Design(id='review', job_id='job', name='review', source_stage='review', pdb_path=str(root / 'a.pdb'))])
            await session.commit()
            assert await ingester.ingest_job_results('job', str(tmp_path), session) == 2
            rows = list((await session.execute(select(Design).order_by(Design.name))).scalars())
            assert [r.name for r in rows] == ['a', 'b']
            # Unversioned metrics/CIF cannot establish native token-mean authority.
            assert rows[1].plddt_overall is None and rows[1].residue_plddt == [80.0]
            assert rows[1].iptm is None
            receipt = current.provenance['core_protein_candidate_publication']
            assert set(receipt['candidates']) == {r.name for r in rows}
            assert receipt['candidates']['b']['structure'] == {'path': str(root / f'b.{suffix}'), 'sha256': hashlib.sha256(source).hexdigest()}
            assert (root / f'b.{suffix}').read_bytes() == source
            assert await ingester.ingest_job_results('job', str(tmp_path), session) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepared_manifest_is_sole_iteration_authority(tmp_path, monkeypatch):
    from services.result_ingester import ingest_job_results
    root = artifacts(tmp_path)
    real_prepare = contract.prepare_esmfold2_publication
    def replace_before_prepare(*args, **kwargs):
        # Replacement between ingress discovery and preparation: only this new
        # declaration may determine both rows and receipt, not an earlier read.
        manifest = json.loads((root / 'manifest.json').read_text())
        manifest['samples'] = manifest['samples'][1:]
        manifest['sample_count'] = 1
        (root / 'manifest.json').write_text(json.dumps(manifest))
        return real_prepare(*args, **kwargs)
    monkeypatch.setattr(contract, 'prepare_esmfold2_publication', replace_before_prepare)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current)
            await session.commit()
            assert await ingest_job_results('job', str(tmp_path), session) == 1
            rows = list((await session.execute(select(Design))).scalars())
            assert {r.name for r in rows} == set(current.provenance['core_protein_candidate_publication']['candidates']) == {'b'}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('replacement', ['manifest.json', 'b.metrics.json', 'b.pdb'])
async def test_replacement_after_prepare_fails_before_dispatch_delete(tmp_path, monkeypatch, replacement):
    from services.result_ingester import ingest_job_results
    root = artifacts(tmp_path)
    real_prepare = contract.prepare_esmfold2_publication
    def replace_after_prepare(*args, **kwargs):
        snapshot = real_prepare(*args, **kwargs)
        (root / replacement).write_text('replacement bytes')
        return snapshot
    monkeypatch.setattr(contract, 'prepare_esmfold2_publication', replace_after_prepare)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add_all([current, Design(id='review', job_id='job', name='review', source_stage='review', pdb_path=str(root / 'a.pdb'))])
            await session.commit()
            with pytest.raises(contract.CandidateIntegrityError) as error:
                await ingest_job_results('job', str(tmp_path), session, commit=False)
            assert error.value.reason['code'] == 'candidate_replay_changed'
            assert not session.dirty and not session.new and not session.deleted
            await session.commit()
            assert [r.id for r in (await session.execute(select(Design))).scalars()] == ['review']
            assert 'core_protein_candidate_publication' not in current.provenance
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', ['broken JSON', '[]', '{"sample_id": "foreign", "cif": "b.pdb"}'])
async def test_direct_dispatch_invalid_last_metrics_preserves_review_rows(tmp_path, payload):
    from services.result_ingester import ingest_job_results
    root = artifacts(tmp_path)
    (root / 'b.metrics.json').write_text(payload)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add_all([current, Design(id='review', job_id='job', name='review', source_stage='review', pdb_path=str(root / 'a.pdb'))])
            await session.commit()
            with pytest.raises(contract.CandidateIntegrityError):
                await ingest_job_results('job', str(tmp_path), session, commit=False)
            assert not session.dirty and not session.new and not session.deleted
            await session.commit()
            assert [r.id for r in (await session.execute(select(Design))).scalars()] == ['review']
            assert 'core_protein_candidate_publication' not in current.provenance
    finally:
        await engine.dispose()


def test_structure_validation_consumes_exact_hashed_bytes(tmp_path, monkeypatch):
    root = artifacts(tmp_path)
    valid = (root / 'b.pdb').read_bytes()
    (root / 'b.pdb').write_text('malformed bytes that are hashed')
    real_artifact = contract._artifact
    def replace_after_read(root, raw):
        evidence, content = real_artifact(root, raw)
        if raw == 'b.pdb':
            (root / raw).write_bytes(valid)
        return evidence, content
    monkeypatch.setattr(contract, '_artifact', replace_after_read)
    with pytest.raises(contract.CandidateIntegrityError) as error:
        contract.prepare_esmfold2_publication(job(tmp_path), root, [])
    assert error.value.reason['code'] == 'candidate_structure_invalid'
    assert (root / 'b.pdb').read_bytes() == valid


def test_current_producer_empty_declaration_is_discovered(tmp_path):
    from services.result_ingester import _resolve_esmfold2_final_root
    root = artifacts(tmp_path, ids=())
    assert _resolve_esmfold2_final_root(tmp_path) == root


@pytest.mark.asyncio
async def test_actual_dispatch_commits_valid_publication_atomically(tmp_path):
    artifacts(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current)
            await session.commit()
            result = await finalize_successful_job(current, str(tmp_path), session)
            assert result.completed
        async with factory() as reader:
            persisted = await reader.get(Job, 'job')
            rows = list((await reader.execute(select(Design))).scalars())
            assert persisted.status == 'completed'
            assert persisted.provenance['result_integrity']['partial'] is False
            assert len(rows) == 2
            assert all(d.confidence_metrics['core_protein_scientific_contract'] == 1 for d in rows)
            summary = persisted.provenance['core_protein_candidate_publication']['summary']
            assert summary['requested_count'] is None
            assert summary['generated_count'] == summary['selected_count'] == summary['published_count'] == 2
    finally:
        await engine.dispose()



@pytest.mark.asyncio
async def test_replay_cannot_silently_recreate_lost_publication(tmp_path):
    from sqlalchemy import delete
    artifacts(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current)
            await session.commit()
            await ingest_esmfold2_results('job', tmp_path, session, current)
            await session.execute(delete(Design).where(Design.job_id == 'job'))
            await session.commit()
            with pytest.raises(RuntimeError):
                await ingest_esmfold2_results('job', tmp_path, session, current)
    finally:
        await engine.dispose()
