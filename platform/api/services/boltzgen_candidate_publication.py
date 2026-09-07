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
        return 0
    revalidate_prepared_publication(root, receipt)
    # Entire set has been checked. Only now may review cleanup and writes occur.
    await session.execute(delete(Design).where(Design.job_id == job.id, Design.source_stage.is_not(None)))
    for candidate, item in prepared.items():
        payload = item['payload']
        design_id = str(uuid.uuid4())
        session.add(Design(id=design_id, job_id=job.id, name=candidate,
            pdb_path=item['artifacts']['structure']['path'], json_path=item['artifacts']['metrics']['path'],
            confidence_metrics={**payload, 'core_protein_scientific_contract': 1,
                                'core_protein_scientific': scalar_block(item, design_id),
                                'core_protein_candidate_artifacts': item['artifacts']}))
    job.provenance = {**(job.provenance or {}), 'core_protein_candidate_publication': receipt}
    if commit:
        await session.commit()
    return len(prepared)
