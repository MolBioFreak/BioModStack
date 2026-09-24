"""Narrow adapter for FilterBoltzGen's published, revision-1 report.

Callers: boltzgen / boltzgen_child; modules/boltzgen.nf publishes the exact
filtered directory. No fallback to raw outputs, CSV rows or requested counts.
"""
import json
import uuid
from pathlib import Path

from sqlalchemy import select, delete
from database import Design, Job
from services.core_protein_result_contract import (
    CandidateIntegrityError, _artifact, _ids, _structure_confidence,
    validate_candidate_accounting, validate_persisted_publication,
    revalidate_prepared_publication,
)


def prepare(job, output):
    root = Path(output) / 'collected' / 'boltzgen_filtered'
    manifest, raw = _artifact(root, 'filter_summary.json')
    try:
        report = json.loads(raw)
        if type(report['core_protein_scientific_contract']) is not int or report['core_protein_scientific_contract'] != 1:
            raise ValueError('revision')
        records = report['dispositions']
        ids = [r['candidate_id'] for r in records]
        _ids(ids, 'filter input inventory')
        if type(report['input_count']) is not int or report['input_count'] != len(ids):
            raise ValueError('input count')
        dispositions = []
        for record in records:
            selected = record['selected']
            if type(selected) is not bool:
                raise ValueError('selection must be boolean')
            state = record['disposition']
            disposition = {'candidate_id': record['candidate_id']}
            if selected:
                if state != 'passed':
                    raise ValueError('selected nonpassing candidate')
                disposition['disposition'] = 'selected'
            elif state == 'passed':
                disposition.update(disposition='rejected', **record['selection_rejection'])
            elif state in {'rejected_threshold', 'invalid_evidence', 'unevaluable_missing'}:
                failed = next(c for c in record['criteria'] if c['disposition'] == state)
                disposition.update(disposition={'rejected_threshold': 'rejected', 'invalid_evidence': 'failed',
                                                'unevaluable_missing': 'unevaluable'}[state],
                                   criterion=failed['criterion'], reason_code=failed['evidence']['reason_code'] or state)
            else:
                raise ValueError('unknown disposition')
            dispositions.append(disposition)
        publication = report['publication']
        if not isinstance(publication, dict):
            raise ValueError('publication')
        if type(report['final_count']) is not int or report['final_count'] != len(publication):
            raise ValueError('final count')
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        raise CandidateIntegrityError('invalid_filter_publication', f'invalid BoltzGen publication: {exc}') from exc
    # Validate the complete identity set before touching any ORM object.
    summary = validate_candidate_accounting(stage_id='boltzgen', requested_count=None,
        generated_ids=ids, dispositions=dispositions, expected_publication_ids=list(publication),
        persisted_ids=list(publication))
    prepared, paths = {}, []
    by_id = {r['candidate_id']: r for r in records}
    for candidate, declaration in publication.items():
        if not isinstance(declaration, dict) or set(declaration) not in ({'structure', 'metrics'}, {'structure', 'metrics', 'native'}):
            raise CandidateIntegrityError('candidate_artifact_missing', 'structure and metrics must be declared')
        artifacts, contents = {}, {}
        for role, evidence in declaration.items():
            if not isinstance(evidence, dict) or set(evidence) != {'path', 'sha256'}:
                raise CandidateIntegrityError('candidate_artifact_missing', 'invalid artifact declaration')
            actual, content = _artifact(root, evidence['path'])
            if actual['sha256'] != evidence['sha256']:
                raise CandidateIntegrityError('candidate_replay_changed', 'published artifact bytes changed')
            artifacts[role], contents[role] = actual, content
            paths.append(actual['path'])
        _structure_confidence(contents['structure'], artifacts['structure']['path'])  # syntax only; never pLDDT authority
        try:
            payload = json.loads(contents['metrics'])
            if payload['design_id'] != candidate or payload['source_sha256'] != by_id[candidate]['source_sha256']:
                raise ValueError('candidate/source identity')
            if artifacts['structure']['sha256'] != by_id[candidate]['structure_sha256']:
                raise ValueError('structure identity')
        except (ValueError, KeyError, TypeError) as exc:
            raise CandidateIntegrityError('foreign_candidate_id', 'published metrics do not match filter identity') from exc
        native = payload.get('native_scalar_source')
        if ('native' in artifacts) != (native is not None):
            raise CandidateIntegrityError('candidate_artifact_missing', 'native declaration mismatch')
        if native is not None:
            if (not isinstance(native, dict) or set(native) not in (
                    {'candidate_id', 'native_id', 'dialect', 'artifact', 'producer_identity'},
                    {'candidate_id', 'native_id', 'dialect', 'artifact', 'producer_identity', 'filter_from_inverse_folded'})
                    or native['candidate_id'] != candidate or not isinstance(native['native_id'], str)
                    or not native['native_id'] or native['dialect'] not in {'csv', 'npz'}
                    or native['artifact'] != declaration['native']):
                raise CandidateIntegrityError('foreign_candidate_id', 'native source binding mismatch')
        if native is not None:
            from services import aligned_error_utils  # registers shared scripts root
            from lib.filtering.native_gate import canonical_evidence
            canonical = canonical_evidence(payload, contents['native'], candidate)
            for criterion in by_id[candidate]['criteria']:
                name = criterion['criterion']
                if name in canonical and (canonical[name]['state'] != 'ok'
                        or criterion['evidence'] != canonical[name]):
                    raise CandidateIntegrityError('invalid_filter_publication', 'decisive native scalar differs from filter evidence')
        prepared[candidate] = {'payload': payload, 'artifacts': artifacts, 'native_bytes': contents.get('native')}
    _ids(paths, 'published artifact paths')
    observed = {str(p.resolve()) for p in root.iterdir() if p.suffix in {'.pdb', '.cif', '.mmcif', '.json', '.npz', '.csv'} and p.name != 'filter_summary.json'}
    if observed != set(paths):
        raise CandidateIntegrityError('candidate_publication_mismatch', 'extra or missing published artifacts')
    receipt = {'summary': summary, 'manifest': manifest, 'dispositions': dispositions,
               'candidates': {i: p['artifacts'] for i, p in prepared.items()}}
    prior = (job.provenance or {}).get('core_protein_candidate_publication')
    if prior is not None and prior != receipt:
        raise CandidateIntegrityError('candidate_replay_changed', 'candidate replay evidence changed')
    return root, prepared, receipt


def scalar_block(item, design_id):
    from services import aligned_error_utils  # registers existing shared scripts root
    from lib.boltzgen_native import metric_records, unavailable_identity
    from services.core_protein_scientific_contract import validate_metric
    source = item['payload'].get('native_scalar_source')
    if source is None:
        # Older marked publications have no native source; never bless their floats.
        source = {'dialect': 'unavailable', 'producer_identity': unavailable_identity()}
    raw = item['native_bytes'] or b''
    metrics = metric_records(source, raw, candidate_id=design_id)
    for record in metrics.values():
        if item['native_bytes'] is None:
            record['source']['artifact_sha256'] = item['artifacts']['metrics']['sha256']
        validate_metric(record)
    return {'schema_version': 1, 'producer': 'boltzgen', 'design_id': design_id,
            'metrics': metrics}


async def verified_boltzgen_design(session, design):
    """Read through the current persisted Job and full selected set, without writes.

    No caller flags are enabled here. Legacy unmarked owners remain untouched.
    Filesystem hashing/parsing runs off the event loop.
    """
    import asyncio
    import copy
    from paths import get_data_root, resolve_runtime_data_path
    from services.core_protein_scientific_contract import revision_for_job
    from sqlalchemy import inspect
    state = inspect(design, raiseerr=False)
    required = {'id', 'job_id', 'name', 'pdb_path', 'json_path', 'confidence_metrics'}
    # Analytics deliberately selects compact columns. Load only deferred fields,
    # without replacing already supplied values before the identity comparison.
    if state is not None and state.session is session.sync_session:
        unloaded = required & state.unloaded
        if unloaded:
            with session.no_autoflush:
                await session.refresh(design, attribute_names=sorted(unloaded))
    supplied = (design.id, design.job_id, design.name, design.pdb_path, design.json_path,
                copy.deepcopy(design.confidence_metrics))
    with session.no_autoflush:
        job = await session.scalar(select(Job).where(Job.id == design.job_id).execution_options(populate_existing=True))
        if job is None or revision_for_job(job) != 1 or job.model_id not in {'boltzgen', 'boltzgen_child'}:
            raise CandidateIntegrityError('missing_candidate_declaration', 'no marked BoltzGen owner')
        if not isinstance(job.output_dir, str) or not job.output_dir:
            raise CandidateIntegrityError('candidate_artifact_missing', 'no authorized output directory')
        output = Path(job.output_dir)
        output = resolve_runtime_data_path(output) if output.is_absolute() else get_data_root() / output
        if not isinstance((job.provenance or {}).get('core_protein_candidate_publication'), dict):
            raise CandidateIntegrityError('missing_candidate_declaration', 'no persisted publication authority')
        root, prepared, receipt = await asyncio.to_thread(prepare, job, output)
        rows = list((await session.execute(select(Design).where(Design.job_id == job.id,
            Design.source_stage.is_(None)).execution_options(populate_existing=True))).scalars())
        await asyncio.to_thread(validate_persisted_publication, job, rows, root)
        selected = None
        for row in rows:
            item = prepared[row.name]
            block = await asyncio.to_thread(scalar_block, item, row.id)
            if (row.confidence_metrics or {}).get('core_protein_scientific') != block:
                raise CandidateIntegrityError('candidate_replay_changed', 'persisted scalar block differs from native authority')
            if row.id == supplied[0]:
                if supplied != (row.id, row.job_id, row.name, row.pdb_path, row.json_path, row.confidence_metrics):
                    raise CandidateIntegrityError('foreign_candidate_id', 'supplied candidate differs from persisted owner')
                selected = {'block': block, 'artifacts': item['artifacts']}
        if selected is None:
            raise CandidateIntegrityError('foreign_candidate_id', 'candidate is not in selected publication')
        await asyncio.to_thread(revalidate_prepared_publication, root, receipt)
        return selected


async def ingest(job, output, session, *, commit=True):
    root, prepared, receipt = prepare(job, output)
    with session.no_autoflush:
        rows = list((await session.execute(select(Design).where(Design.job_id == job.id,
                                                               Design.source_stage.is_(None)))).scalars())
    prior = (job.provenance or {}).get('core_protein_candidate_publication')
    if rows or prior is not None:
        validate_persisted_publication(job, rows, root)
        if (job.provenance or {}).get('boltzgen_generation_publication') is not None:
            await read_published_generation_results(job, session)
        return 0
    revalidate_prepared_publication(root, receipt)
    # Entire set has been checked. Only now may review cleanup and writes occur.
    await session.execute(delete(Design).where(Design.job_id == job.id, Design.source_stage.is_not(None)))
    for candidate, item in prepared.items():
        payload = item['payload']
        design_id = str(uuid.uuid4())
        session.add(Design(id=design_id, job_id=job.id, name=candidate, producer_model_id='boltzgen',
            pdb_path=item['artifacts']['structure']['path'], json_path=item['artifacts']['metrics']['path'],
            confidence_metrics={**payload, 'core_protein_scientific_contract': 1,
                                'core_protein_scientific': scalar_block(item, design_id),
                                'core_protein_candidate_artifacts': item['artifacts']}))
    job.provenance = {**(job.provenance or {}), 'core_protein_candidate_publication': receipt}
    await session.flush()
    if job.model_id == 'boltzgen':
        await _generation_publication(job, output, session, publish=True)
    if commit:
        await session.commit()
    return len(prepared)


def generation_workbench(result, publication, *, offset=0, limit=100):
    """Exact native rows with persisted Design/document and governed file handles."""
    from services.bindcraft2_publication import native_workbench_page
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Invalid native result page')
    page = native_workbench_page({'arm': '', 'stage': 'generation',
                                 'rows': result['records'][offset:offset + limit]}, publication)
    return {**result, 'records': page['rows'], 'total': len(result['records']),
            'offset': offset, 'limit': limit, 'publication': publication, 'artifacts': page['artifacts']}


async def _generation_publication(job, output, session, *, publish=False, offset=0, limit=100):
    """Wrap existing native accounting authority; do not re-rank or invent joins."""
    import asyncio
    from database import JobArtifact
    if job.model_id != 'boltzgen' or not job.output_dir or Path(job.output_dir).absolute() != Path(output).absolute():
        raise ValueError('BoltzGen generation publication root or owner differs')
    root, prepared, core = await asyncio.to_thread(prepare, job, output)
    if (job.provenance or {}).get('core_protein_candidate_publication') != core:
        raise ValueError('BoltzGen native candidate publication missing')
    with session.no_autoflush:
        designs = list((await session.scalars(select(Design).where(Design.job_id == job.id, Design.source_stage.is_(None)))).all())
        artifacts = list((await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))).all())
    await asyncio.to_thread(validate_persisted_publication, job, designs, root)
    for design in designs:
        block = await asyncio.to_thread(scalar_block, prepared[design.name], design.id)
        if (design.confidence_metrics or {}).get('core_protein_scientific') != block:
            raise ValueError('BoltzGen persisted native scalars changed')
    previous = (job.provenance or {}).get('boltzgen_generation_publication')
    if previous is None and not publish:
        # Historical marked publications already have their own immutable authority.
        # Reopen without mutating them or pretending a JobArtifact was registered.
        report = json.loads((root / 'filter_summary.json').read_bytes())
        records = report['dispositions']
        return {'receipt': report, 'records': records[offset:offset + limit],
                'publication': core, 'artifacts': [], 'total': len(records),
                'offset': offset, 'limit': limit}
    files = {}
    for entry in [core['manifest'], *(a for item in prepared.values() for a in item['artifacts'].values())]:
        path = Path(entry['path'])
        files[path.relative_to(root).as_posix()] = {'sha256': entry['sha256'], 'bytes': path.stat().st_size}
    publication = {'schema': 'boltzgen.generation-publication.v1', 'job_id': job.id,
                   'root': str(Path(output).absolute()), 'campaign_root': 'collected/boltzgen_filtered',
                   'attempt': job.retry_count or 0, 'remote_attempt_id': job.remote_attempt_id, 'files': files}
    owned = {a.logical_path.removeprefix('boltzgen/native/'): a for a in artifacts if a.logical_path.startswith('boltzgen/native/')}
    if previous is not None and ({k: v for k, v in previous.items() if k != 'candidates'} != publication
                                 or set(owned) != set(files)):
        raise ValueError('BoltzGen native publication replay changed')
    if len(owned) != sum(a.logical_path.startswith('boltzgen/native/') for a in artifacts):
        raise ValueError('BoltzGen registered artifacts span multiple attempts')
    if previous is None and owned:
        raise ValueError('BoltzGen artifacts exist without publication')
    for name, info in files.items():
        row = owned.get(name)
        if row is None:
            row = JobArtifact(id=str(uuid.uuid4()), owner_job_id=job.id, attempt=publication['attempt'],
                              logical_path='boltzgen/native/' + name, storage_path=str(root / name),
                              sha256=info['sha256'], bytes=info['bytes'],
                              media_type='chemical/x-pdb' if name.endswith('.pdb') else 'application/octet-stream',
                              provenance={'model_id': 'boltzgen'})
            session.add(row)
            owned[name] = row
        elif (row.storage_path, row.sha256, row.bytes, row.attempt) != (str(root / name), info['sha256'], info['bytes'], publication['attempt']):
            raise ValueError('BoltzGen registered artifact changed')
    publication['candidates'] = []
    for design in sorted(designs, key=lambda d: d.name):
        path = Path(prepared[design.name]['artifacts']['structure']['path']).relative_to(root).as_posix()
        artifact = owned[path]
        publication['candidates'].append({'design_id': design.id, 'candidate_key': design.name,
            'structures': [{'artifact_id': artifact.id, 'logical_path': artifact.logical_path,
                            'path': path, 'sha256': artifact.sha256, 'primary': True, 'target_state': None}]})
        fields = {'lineage_root_job_id': job.lineage_root_job_id or job.id, 'origin_job_id': job.id,
                  'stage_family': 'boltzgen', 'stage_mode': job.mode,
                  'provenance': {'schema': 'boltzgen.candidate-lineage.v1', 'candidate_key': design.name,
                                 'primary_artifact_id': artifact.id, 'validation_state': 'unvalidated'}}
        if previous is None:
            for key, value in fields.items():
                setattr(design, key, value)
        elif any(getattr(design, key) != value for key, value in fields.items()):
            raise ValueError('BoltzGen persisted candidate lineage changed')
    if previous is not None and publication != previous:
        raise ValueError('BoltzGen candidate document bindings changed')
    if previous is None:
        job.provenance = {**(job.provenance or {}), 'boltzgen_generation_publication': publication}
        await session.flush()
    report = json.loads((root / 'filter_summary.json').read_bytes())
    by_key = {d.name: d for d in designs}
    records = [{**record, **({'native_metrics': scalar_block(prepared[record['candidate_id']], by_key[record['candidate_id']].id)}
                if record['candidate_id'] in prepared else {})} for record in report['dispositions']]
    return generation_workbench({'receipt': report, 'records': records}, publication, offset=offset, limit=limit)


async def read_published_generation_results(job, session, *, offset=0, limit=100):
    return await _generation_publication(job, job.output_dir, session, offset=offset, limit=limit)
