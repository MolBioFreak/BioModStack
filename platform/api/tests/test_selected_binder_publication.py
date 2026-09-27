"""CPU-only publication fixtures, not native refinement/Caliby inference evidence."""
import importlib.util
import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job
from routers import jobs
from services.binder_continuation import resolve_selection, snapshot_selection
from services.result_state_integrity import finalize_successful_job


PDB = ''.join(
    f'ATOM  {serial:5d}  {atom:<3} ALA A   1    {float(serial):8.3f}{2.0:8.3f}{3.0:8.3f}  1.00 90.00          {element:>2}  \n'
    for serial, (atom, element) in enumerate([('N', 'N'), ('CA', 'C'), ('C', 'C'), ('O', 'O')], 1)
) + 'TER\nEND\n'
REPO = Path(__file__).resolve().parents[3]


def script(name):
    spec = importlib.util.spec_from_file_location(name, REPO / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'get_inputs_dir', lambda: tmp_path / 'inputs')
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: tmp_path / 'results')
    monkeypatch.setattr(jobs, '_resolve_design_structure_path', lambda path: Path(path))
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "publication.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def owner(identity, model='binder_refinement', **kw):
    return Job(id=identity, name=identity, model_id=model,
               mode='design' if model == 'caliby_binder' else 'refine',
               status='running', queue_status='running', awaiting_input=False, **kw)


def publish_fixture(root, model, producer, sources):
    """Run actual CPU publication serializers on explicitly synthetic structures."""
    expected = {}
    for index, source in enumerate(sources):
        # Same output basename in different state documents must not collapse.
        terminal = root / 'collected' / (
            'binder_refinement' if model == 'binder_refinement' else 'binder_generation/caliby'
        ) / f'state-{index}'
        # Caliby publishes a single flat batch; refinement also permits nested
        # published/ paths and same-basename documents in separate state roots.
        if model == 'caliby_binder':
            terminal = terminal.parent
        terminal.mkdir(parents=True, exist_ok=True)
        native = root / 'work' / str(index) / 'same.pdb'
        native.parent.mkdir(parents=True)
        native.write_text(PDB.replace('   2.000', '   4.000', 1))
        meta = source['source_meta']
        if model == 'binder_refinement':
            terminal = terminal / 'published'  # Nextflow publishes path('published/*').
            script('publish_binder_refinement').publish(native, {
                'id': 'same', 'source_staged_name': source['staged_name'],
                'source_meta': meta, 'source_document_id': meta['id'],
                'source_structure_state': meta['structure_state'],
                **({'terminal_producer': producer, 'validation_status': 'unvalidated'}
                   if producer != 'selected_input' else {}),
                'sample_index': index,
            }, terminal)
            path = terminal / 'same.pdb'
        else:
            if index != len(sources) - 1:
                continue
            manifest = script('caliby_runtime').normalize_sampling_results(
                results={'example_id': [Path(item['staged_name']).stem for item in sources],
                         'out_pdb': [str(root / 'work' / str(i) / 'same.pdb') for i in range(len(sources))],
                         'seq': ['A'] * len(sources), 'input_seq': ['A'] * len(sources),
                         'U': [-3.25] * len(sources)},
                output_pdb_dir=terminal, output_meta_dir=terminal,
                prefix='caliby', source='caliby', stage_mode='sequence_design',
                extra_metadata={'caliby_model': 'soluble_caliby_v1',
                                'validation_status': 'unvalidated', 'terminal_producer': 'caliby'},
            )
            for item, selected_source in zip(manifest, sources):
                path = Path(item['structure_path'])
                metadata = Path(item['metadata_path'])
                record = json.loads(metadata.read_text())
                # Exact source attachment done by run_caliby_sequence_design.py.
                source_meta = selected_source['source_meta']
                record.update(selected_source=selected_source, source_document_id=source_meta['id'],
                              source_meta=source_meta)
                metadata.write_text(json.dumps(record))
                expected[str(path)] = source_meta['design_id']
            continue
        expected[str(path)] = meta['design_id']
    # Plausible distractors at generic scanner/CSV locations, including a final
    # root unpaired PDB: none is a generator-published terminal document.
    for directory in ('pdb_files', 'collected/maturation', 'results/best_designs',
                      'collected/binder_generation/caliby/cleaned_pdbs'):
        path = root / directory / 'intermediate.pdb'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PDB)
    (root / 'results/all_designs.csv').write_text('description,pr_plddt\nintermediate,99\n')
    return expected


@pytest.mark.asyncio
@pytest.mark.parametrize('model,producer', [
    ('binder_refinement', 'pyrosetta_repack'), ('binder_refinement', 'ppiflow'),
    ('binder_refinement', 'fampnn'), ('binder_refinement', 'selected_input'),
    ('caliby_binder', 'caliby'),
])
async def test_finalizer_terminal_identity_metrics_replay_and_later_selection(tmp_path, store, model, producer):
    root = tmp_path / 'publication'
    async with store() as session:
        origin = owner('origin', params={})
        source_job = owner('source', params={'lineage_root_job_id': 'origin'}, lineage_root_job_id='origin')
        session.add_all([origin, source_job])
        for identity, job_id, state in [('source-a', 'origin', 'open'), ('source-b', 'source', 'closed')]:
            path = tmp_path / identity / 'same.pdb'
            path.parent.mkdir()
            path.write_text(PDB)
            session.add(Design(id=identity, job_id=job_id, name='same', pdb_path=str(path),
                               lineage_root_job_id='origin', origin_design_id='source-a',
                               origin_job_id='origin', provenance={'structure_state': state},
                               plddt_overall=97, iptm=.91, passed_screen=True))
        await session.commit()
        source, ancestor, designs = await resolve_selection(session, source_job.id, ['source-b', 'source-a'])
        selection = snapshot_selection(source, ancestor, designs)
        sources = json.loads((selection / 'source_identity.json').read_text())
        expected = publish_fixture(root, model, producer, sources)
        child = owner('child', model, output_dir=str(root), lineage_root_job_id='origin', params={
            'lineage_root_job_id': 'origin', 'selected_input_manifest': str(selection / 'selection_manifest.json'),
            'source_identity_json': str(selection / 'source_identity.json'),
            'selected_input_source_job_id': 'source', 'selection_source_job_id': 'source',
        })
        session.add(child)
        await session.commit()
        result = await finalize_successful_job(child, str(root), session)
        assert result.completed, child.error_message
        assert result.design_count == 2
    async with store() as session:
        child = await session.get(Job, 'child')
        assert child.status == child.queue_status == 'completed'
        rows = list((await session.scalars(select(Design).where(Design.job_id == 'child'))).all())
        assert {row.pdb_path: row.parent_design_id for row in rows} == expected
        before = {row.pdb_path: row.id for row in rows}
        for row in rows:
            assert row.producer_model_id == producer
            assert row.origin_design_id == 'source-a' and row.origin_job_id == 'origin'
            assert row.source_stage_job_id == ('source' if row.parent_design_id == 'source-b' else 'origin')
            assert row.provenance['structure_state'] == ('closed' if row.parent_design_id == 'source-b' else 'open')
            assert row.plddt_overall is None and row.residue_plddt is None
            assert row.iptm is None and row.passed_screen is None
            assert row.artifact_class != 'validated_binder_complex'
            if model == 'caliby_binder':
                assert row.confidence_metrics['caliby']['caliby_potts_energy'] == -3.25
                assert row.confidence_metrics['caliby']['sequence'] == 'A'
                assert row.review_profile_id == 'sequence_design_v1'
                assert row.review_contract_source == 'producer'
                assert row.artifact_class == 'sequence_designed_complex'
        child.status = child.queue_status = 'running'
        await session.commit()
        assert (await finalize_successful_job(child, str(root), session)).completed
    async with store() as session:
        rows = list((await session.scalars(select(Design).where(Design.job_id == 'child'))).all())
        assert {row.pdb_path: row.id for row in rows} == before
        source, ancestor, selected = await resolve_selection(session, 'child', [rows[0].id])
        selection = snapshot_selection(source, ancestor, selected)
        sources = json.loads((selection / 'source_identity.json').read_text())
        assert sources[0]['source_meta']['design_id'] == rows[0].id
        assert sources[0]['source_meta']['source_job_id'] == 'child'
        assert sources[0]['source_meta']['structure_state'] == rows[0].provenance['structure_state']
        later_root = tmp_path / 'later'
        later_expected = publish_fixture(later_root, model, producer, sources)
        later = owner('later', model, output_dir=str(later_root), lineage_root_job_id='origin', params={
            'lineage_root_job_id': 'origin', 'selected_input_manifest': str(selection / 'selection_manifest.json'),
            'selected_input_source_job_id': 'child',
        })
        session.add(later)
        await session.commit()
        assert (await finalize_successful_job(later, str(later_root), session)).completed
    async with store() as session:
        later_rows = list((await session.scalars(select(Design).where(Design.job_id == 'later'))).all())
        assert {row.pdb_path: row.parent_design_id for row in later_rows} == later_expected
        assert later_rows[0].source_stage_job_id == 'child'
        assert later_rows[0].origin_design_id == 'source-a' and later_rows[0].origin_job_id == 'origin'
        assert later_rows[0].plddt_overall is None and later_rows[0].passed_screen is None
