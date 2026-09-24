"""Scratch SQLite publication fixtures, not model/scientific execution."""
import copy
import hashlib
import json

import pytest
from sqlalchemy import select
from database import Design, Job, JobArtifact
from test_project_manager_adapters import stores, _project_payload, _global_payload, _domain_payload
from services import ppiflow_generation as pp
from services import boltzgen_candidate_publication as bg
from services.global_experiments.adapters import NativeBinderJobResultAdapter


def pp_output(root, count=2):
    output = root / 'ppiflow_generation'
    output.mkdir(parents=True)
    receipt = {'schema_version': pp.SCHEMA_VERSION, 'model': 'ppiflow', 'mode': 'protein_binder',
               'operation': 'initial_generation', 'status': 'completed', 'emitted_samples': count,
               'requested_samples': 4, 'unemitted_samples': 4 - count,
               'native_effective_config': None, 'source_identity': {'unresolved': True}}
    records = []
    for i in range(count):
        path = f'candidates/source-{i}/sample-0.pdb'
        target = output / path
        target.parent.mkdir(parents=True)
        target.write_text(f'ATOM      1  CA  ALA A   1       {i + 1}.000   2.000   3.000  1.00 80.00           C\nEND\n')
        record = {'schema_version': pp.SCHEMA_VERSION, 'producer': 'ppiflow', 'mode': 'protein_binder',
                  'operation': 'initial_generation', 'candidate_key': f'source:{i}/sample:0',
                  'source_row_index': i, 'sample_index': 0, 'native_input_id': 'repeated-target',
                  'native_path': 'same-overwritten.pdb', 'path': path,
                  'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                  'source': {'source_row_index': i}, 'source_identity': {'unresolved': True},
                  'metrics': {'native_score': 0, 'unknown': None}}
        records.append(record)
        (output / (path + '.sample.json')).write_text(json.dumps(record))
    (output / 'generation_receipt.json').write_text(json.dumps(receipt))
    if records:
        (output / 'samples.jsonl').write_text('\n'.join(json.dumps(r) for r in records) + '\n')
    return records


@pytest.mark.asyncio
@pytest.mark.parametrize('model,zero', [('ppiflow', False), ('ppiflow', True), ('boltzgen', False), ('boltzgen', True)])
async def test_generation_publication_replay_project_reopen(stores, model, zero):
    from experiment_services import create_project, create_global_experiment, create_domain_experiment
    from services.global_experiments.receipts import attach_verified_entity
    from services.global_experiments.result_surfaces import result_surface_for_receipt
    root, experiments, core = stores
    output = root / 'results' / 'generation'
    output.mkdir(parents=True)
    if model == 'ppiflow':
        native = pp_output(output, 0 if zero else 2)
        publish, read = pp.publish_generation_results, pp.read_published_generation_results
    else:
        from test_boltzgen_candidate_accounting import published
        if zero:
            from types import SimpleNamespace
            from filter_boltzgen import run_strict_filter
            target = output / 'collected/boltzgen_filtered'
            target.mkdir(parents=True)
            run_strict_filter(SimpleNamespace(pdbs=[], jsons=[], out_dir=str(target), filter_biased='false',
                metrics_override=None, additional_filters=None, size_buckets=None,
                boltzgen_min_plddt=None, boltzgen_min_conf_score=None, boltzgen_max_rmsd=None, budget=1, alpha=0))
        else:
            published(output)
        publish, read = bg.ingest, bg.read_published_generation_results
    adapter = NativeBinderJobResultAdapter(model)
    async with core() as db:
        job = Job(id='generation', name='generation', model_id=model, mode='protein_binder',
                  output_dir=str(output), params={}, provenance={'core_protein_scientific_contract': 1}, status='completed')
        db.add(job)
        await db.flush()
        await publish(job, output, db, commit=False)
        await db.commit()
        first = await read(job, db)
        designs = list((await db.scalars(select(Design))).all())
        assert len(designs) == (0 if zero else (2 if model == 'ppiflow' else 1))
        for design in designs:
            assert design.provenance['validation_state'] == 'unvalidated'
            assert design.plddt_overall is None
        ids = [d.id for d in designs]
        await publish(job, output, db, commit=False)
        await db.commit()
        assert (await read(job, db)) == first
        assert [d.id for d in (await db.scalars(select(Design))).all()] == ids
        if model == 'ppiflow' and not zero:
            assert [r['candidate_key'] for r in first['records']] == [r['candidate_key'] for r in native]
            assert first['records'][0]['metrics'] == {'native_score': 0, 'unknown': None}
            assert first['records'][0]['structures'][0]['artifact_id'] != first['records'][1]['structures'][0]['artifact_id']
        await adapter.verify(db, job.id)
    async with experiments() as db:
        project = await create_project(db, _project_payload())
        experiment = await create_global_experiment(db, project.id, _global_payload())
        domain = await create_domain_experiment(db, project.id, experiment.id, _domain_payload('protein_in_silico'))
        keys = project.id, experiment.id, domain.id, project.head_generation
        await db.commit()
    async with experiments() as db, core() as cdb:
        args = dict(project_id=keys[0], global_experiment_id=keys[1], domain_experiment_id=keys[2],
                    adapter_id=adapter.adapter_id, entity_id='generation', operation='link_output',
                    role='produced', note=None, expected_head_generation=keys[3])
        attached = await attach_verified_entity(db, cdb, **args)
        assert (await attach_verified_entity(db, cdb, **args))['attachment_receipt_id'] == attached['attachment_receipt_id']
        await db.commit()
        surface = await result_surface_for_receipt(db, project_id=keys[0], receipt_id=attached['source_receipt_id'])
        assert surface['route']['path'] == '/designs/generation'


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['bytes', 'sidecar', 'receipt', 'design', 'artifact', 'attempt', 'root'])
async def test_ppiflow_immutable_readback(stores, damage):
    root, _, core = stores
    output = root / 'results' / 'generation'
    records = pp_output(output)
    async with core() as db:
        job = Job(id='pp', name='pp', model_id='ppiflow', mode='protein_binder', params={}, output_dir=str(output))
        db.add(job)
        await db.flush()
        await pp.publish_generation_results(job, output, db, commit=True)
    async with core() as db:
        job = await db.get(Job, 'pp')
        if damage in {'bytes', 'sidecar', 'receipt'}:
            name = {'bytes': records[0]['path'], 'sidecar': records[0]['path'] + '.sample.json', 'receipt': 'generation_receipt.json'}[damage]
            path = output / 'ppiflow_generation' / name
            path.write_bytes(path.read_bytes() + b' ')
        elif damage == 'design':
            design = await db.scalar(select(Design))
            design.name = 'different'
        elif damage == 'artifact':
            artifact = await db.scalar(select(JobArtifact))
            artifact.sha256 = '0' * 64
        elif damage == 'attempt':
            job.retry_count = 9
        else:
            job.output_dir = str(root / 'other')
        await db.commit()
        with pytest.raises((ValueError, OSError)):
            await pp.read_published_generation_results(job, db)


@pytest.mark.asyncio
async def test_ppiflow_ingester_dispatch_uses_only_producer_snapshots(stores):
    from services.result_ingester import ingest_job_results
    root, _, core = stores
    output = root / 'results' / 'generation'
    pp_output(output, 0)
    (output / 'unrelated.pdb').write_text('not a candidate')
    async with core() as db:
        job = Job(id='dispatch', name='dispatch', model_id='ppiflow', mode='protein_binder', params={}, output_dir=str(output))
        db.add(job)
        await db.commit()
        assert await ingest_job_results(job.id, str(output), db) == 0
        assert not list((await db.scalars(select(Design))).all())
        assert pp.PUBLICATION_KEY in job.provenance


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['artifact', 'design', 'manifest', 'missing', 'extra_attempt'])
async def test_boltzgen_generation_custody_is_immutable(stores, damage):
    from test_boltzgen_candidate_accounting import published
    root, _, core = stores
    output = root / 'results' / 'generation'
    output.mkdir(parents=True)
    published(output)
    async with core() as db:
        job = Job(id='bg', name='bg', model_id='boltzgen', mode='protein_binder', params={},
                  output_dir=str(output), provenance={'core_protein_scientific_contract': 1})
        db.add(job)
        await db.flush()
        await bg.ingest(job, output, db, commit=True)
    async with core() as db:
        job = await db.get(Job, 'bg')
        artifact = await db.scalar(select(JobArtifact))
        if damage == 'artifact':
            artifact.sha256 = '0' * 64
        elif damage == 'missing':
            await db.delete(artifact)
        elif damage == 'extra_attempt':
            db.add(JobArtifact(id='other-attempt', owner_job_id=job.id, attempt=8,
                               logical_path=artifact.logical_path, storage_path=artifact.storage_path,
                               bytes=artifact.bytes, sha256=artifact.sha256, media_type=artifact.media_type))
        elif damage == 'design':
            row = await db.scalar(select(Design))
            row.origin_job_id = 'foreign'
        else:
            path = output / 'collected/boltzgen_filtered/filter_summary.json'
            path.write_bytes(path.read_bytes() + b' ')
        await db.commit()
        with pytest.raises((ValueError, RuntimeError)):
            await bg.read_published_generation_results(job, db)


@pytest.mark.asyncio
async def test_overwritten_native_document_is_not_an_alternate_candidate(stores):
    from services.binder_diagnostic_selection import selected_document, CandidateDocument
    root, _, core = stores
    output = root / 'results' / 'generation'
    records = pp_output(output)
    overwritten = output / 'ppiflow_generation' / 'same-overwritten.pdb'
    overwritten.write_bytes((output / 'ppiflow_generation' / records[1]['path']).read_bytes())
    async with core() as db:
        parent = Job(id='parent', name='parent', model_id='ppiflow', mode='protein_binder', params={})
        job = Job(id='child', name='child', model_id='ppiflow', mode='protein_binder', params={},
                  lineage_root_job_id='parent', output_dir=str(output))
        db.add_all([parent, job])
        await db.flush()
        await pp.publish_generation_results(job, output, db, commit=True)
        page = await pp.read_published_generation_results(job, db, offset=1, limit=1)
        assert page['total'] == 2 and len(page['records']) == 1
        assert page['records'][0]['candidate_key'] == records[1]['candidate_key']
        assert all('same-overwritten.pdb' != a['path'] for a in page['artifacts'])
        design = await db.scalar(select(Design).where(Design.name == records[0]['candidate_key']))
        before = (design.pdb_path, copy.deepcopy(design.provenance))
        primary, identity = await selected_document(job, design, None, db)
        assert str(primary) == before[0] and identity['artifact_id'] == design.provenance['primary_artifact_id']
        with pytest.raises(ValueError):
            await selected_document(job, design, CandidateDocument(artifact_id='unjoined-native-file'), db)
        assert (design.pdb_path, design.provenance) == before
        assert design.lineage_root_job_id == 'parent' and design.origin_job_id == 'child'
        assert design.parent_design_id is None and design.plddt_overall is None
        assert design.provenance['validation_state'] == 'unvalidated'
