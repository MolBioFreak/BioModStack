"""Inert CPU fixtures only: custody/parser tests, never scientific acceptance."""
import hashlib
import io
from pathlib import Path

import pytest
from Bio.PDB import MMCIFIO
from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from sqlalchemy import select
from database import Design, Job, JobArtifact
from services.binder_result_observation import observe_retained_binder_results
from services.binder_native_selection import resolve_native_source
from services.bindcraft2_native_results import read_native_publication, native_result_page
from services.bindcraft2_publication import _native_document_identity, native_workbench_page, read_published_native_results
from test_binder_shared_publication import store
from test_binder_source_materialization import cif
from test_bindcraft2_publication import campaign


def inert_cif(*, design=None, trajectory=False, chain='a', models=(3,)):
    columns = MMCIF2Dict(io.StringIO(cif(chain=chain, models=models)))
    columns.pop('_atom_site.occupancy')
    columns['_audit.fixture'] = 'inert CPU parser fixture; not a scientific result'
    if design:
        columns['_bindcraft.design'] = design
    if trajectory:
        columns['_bindcraft.stage'] = 'trajectory'
    columns['_bindcraft.binder_chains'] = chain
    writer = MMCIFIO(); writer.set_dict(columns)
    out = io.StringIO(); writer.save(out)
    return out.getvalue().encode()


@pytest.mark.parametrize('output_format,model', [('native', None), ('pdb', None), ('native', 7), ('pdb', 7)])
def test_missing_occupancy_real_parser_and_pdb_writer(tmp_path, monkeypatch, output_format, model):
    from services import binder_source_materialization as materializer
    monkeypatch.setattr(materializer, 'to_allowed_relative', str)
    raw = inert_cif(models=(3, 7) if model else (3,))
    result = materializer.materialize_source_bytes(raw, '.cif', tmp_path,
        output_format=output_format, model_number=model)
    assert result['inspection_error'] is None
    assert result['author_residues'] == [{'model_number': model or 3, 'auth_asym_id': 'a',
        'auth_seq_id': 42, 'insertion_code': 'B', 'residue_name': 'ALA'}]
    assert Path(result['native_path']).read_bytes() == raw
    assert result['original_sha256'] == hashlib.sha256(raw).hexdigest()
    if output_format == 'pdb':
        assert Path(result['path']).read_bytes().startswith(b'ATOM') or b'ATOM' in Path(result['path']).read_bytes()


def test_exact_producer_association_not_suffix_normalization(tmp_path):
    campaign(tmp_path)
    documents = {
        '1_Trajectories/t/t_trajectory.cif': inert_cif(trajectory=True),
        '1_Trajectories/wrong/t_trajectory.cif': inert_cif(trajectory=True),
        '2_Refolded/BinderMonomer/unrelated_filename.cif': inert_cif(design='t_candidate2'),
        '2_Refolded/BinderMonomer/t_candidate1_monomer.cif': inert_cif(design='t_candidate1'),
        '2_Refolded/BinderMonomer/t_candidate1_alternate.cif': inert_cif(),
    }
    for name, raw in documents.items():
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    publication = read_native_publication(tmp_path)
    arm = publication.arms[0]
    docs = {d.path: _native_document_identity(arm, d) for d in arm.documents}
    assert docs['1_Trajectories/t/t_trajectory.cif']['native_rows'][0]['design'] == 't'
    assert docs['1_Trajectories/t/t_trajectory.cif']['binder_chains'] == 'a'
    assert not docs['1_Trajectories/wrong/t_trajectory.cif']['native_rows']
    assert docs['2_Refolded/BinderMonomer/unrelated_filename.cif']['native_rows'][0]['design'] == 't_candidate2'
    assert docs['2_Refolded/BinderMonomer/t_candidate1_monomer.cif']['native_rows'][0]['outcome'] == 'rejected'
    assert not docs['2_Refolded/BinderMonomer/t_candidate1_alternate.cif']['native_rows']


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['failed', 'cancelled'])
async def test_bc2_partial_observation_full_population_replay_no_designs(tmp_path, store, status):
    root = tmp_path / 'results'; campaign(root)
    for i in range(37):
        path = root / '2_Refolded' / f'candidate-{i}.cif'
        path.write_bytes(inert_cif(design='t_candidate1'))
    async with store() as session:
        job = Job(id='partial', name='inert fixture', model_id='bindcraft2', mode='campaign',
                  status=status, queue_status=status, error_message='original failure', params={}, output_dir=str(root))
        session.add(job); await session.commit()
        before = (job.status, job.queue_status, job.error_message, job.completed_at)
        first = await observe_retained_binder_results(session, job, root, commit=True)
        assert first['document_count'] == 39  # all 37 observations plus two emitted ranked fixture documents
        assert first['native_record_count'] == 4
        assert not list((await session.scalars(select(Design))).all())
        publication, receipt = await read_published_native_results(job, session)
        assert receipt['observations_only'] and not receipt['candidates']
        page = native_workbench_page(native_result_page(publication, stage='draw'), receipt)
        assert len(page['rows'][0]['structures']) == 37
        ids = {a.id for a in (await session.scalars(select(JobArtifact))).all()}
        assert await observe_retained_binder_results(session, job, root, commit=True) == first
        assert {a.id for a in (await session.scalars(select(JobArtifact))).all()} == ids
        assert (job.status, job.queue_status, job.error_message, job.completed_at) == before
        selected = page['rows'][0]['structures'][0]
        path, identity = await resolve_native_source(session, job.id, selected['artifact_id'])
        assert identity['producer_document']['native_rows'][0]['outcome'] == 'rejected'
        # Offline unrelated native accounting does not break exact saved source identity.
        (root / '1_Trajectories/!_Trajectories.csv').unlink()
        assert (await resolve_native_source(session, job.id, selected['artifact_id']))[0] == path


@pytest.mark.asyncio
@pytest.mark.parametrize('model,relative', [
    ('boltzgen', 'collected/boltzgen_raw'), ('rfantibody', 'run/rfantibody/output'),
    ('antibody_denovo', 'run/rfantibody/output'),
    ('ppiflow', 'ppiflow_generation/candidates/source-0'),
])
async def test_native_partial_sources_all_documents_no_accepted_designs(tmp_path, store, model, relative):
    output = tmp_path / 'native'; directory = output / relative; directory.mkdir(parents=True)
    for i in range(35):
        (directory / f'inert-{i}.cif').write_bytes(inert_cif())
    # Inputs must never become retained output candidates.
    (output / 'input.cif').write_bytes(inert_cif())
    async with store() as session:
        job = Job(id=model, name='inert partial fixture', model_id=model, mode='protein_binder',
                  status='failed', queue_status='failed', params={}, output_dir=str(output))
        session.add(job); await session.commit()
        observed = await observe_retained_binder_results(session, job, output, commit=True)
        assert observed['document_count'] == 35 and observed['retained_file_count'] == 35
        assert observed['native_record_count'] is None  # no invented scored-row population
        artifacts = list((await session.scalars(select(JobArtifact))).all())
        assert len(artifacts) == 35 and not list((await session.scalars(select(Design))).all())
        artifact = artifacts[0]
        path, identity = await resolve_native_source(session, job.id, artifact.id)
        assert path == Path(artifact.storage_path)
        assert identity['producer_model_id'] == ('rfantibody' if relative.startswith('run/rfantibody/') else model)
        assert identity['producer_document']['native_output_id'] == path.relative_to(output).as_posix()
        assert await observe_retained_binder_results(session, job, output, commit=True) == observed
        assert job.status == job.queue_status == 'failed'
        path.write_bytes(path.read_bytes() + b'# changed\n')
        with pytest.raises(ValueError, match='replay changed'):
            await observe_retained_binder_results(session, job, output)


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['boltzgen', 'ppiflow'])
async def test_current_native_publication_source_is_selectable_offline(tmp_path, store, model):
    from test_generation_publication_integration import pp_output
    from test_boltzgen_candidate_accounting import published
    from services import ppiflow_generation, boltzgen_candidate_publication
    output = tmp_path / 'publication'; output.mkdir()
    if model == 'ppiflow':
        pp_output(output)
        publish = ppiflow_generation.publish_generation_results
        key = 'ppiflow_generation_publication'
    else:
        published(output)
        publish = boltzgen_candidate_publication.ingest
        key = 'boltzgen_generation_publication'
    async with store() as session:
        owner = Job(id=model, name='inert native writer fixture', model_id=model, mode='protein_binder',
                    status='completed', params={}, output_dir=str(output),
                    provenance={'core_protein_scientific_contract': 1})
        session.add(owner); await session.commit()
        await publish(owner, output, session, commit=False); await session.commit()
        binding = owner.provenance[key]['candidates'][0]['structures'][0]
        path, identity = await resolve_native_source(session, owner.id, binding['artifact_id'])
        assert identity['artifact_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert identity['producer_model_id'] == model
        # Remove unrelated producer summaries; selected-document custody remains independent.
        for name in owner.provenance[key]['files']:
            if name.endswith('.json') and 'sample' not in name:
                (Path(owner.provenance[key]['root']) / owner.provenance[key]['campaign_root'] / name).unlink()
        assert await resolve_native_source(session, owner.id, binding['artifact_id']) == (path, identity)


def test_native_document_bytes_are_read_once_per_publication(tmp_path, monkeypatch):
    from services.bindcraft2_publication import _inventory
    campaign(tmp_path)
    reads = []
    original = Path.read_bytes
    def read(path):
        if path.suffix == '.cif':
            reads.append(path)
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    observations = {}
    publication = read_native_publication(tmp_path, observations=observations)
    inventory = _inventory(tmp_path, publication, observations=observations)
    assert len(reads) == len(set(reads)) == 2
    assert all(inventory[name]['sha256'] == observed['sha256']
               and inventory[name]['bytes'] == observed['bytes']
               for name, observed in observations.items())
