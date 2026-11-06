"""Native RFD3 v1/v2 receiving; incremental population, immutable candidates."""
import uuid
from scripts.rfd3_generation.manifest_records import iter_manifest_records
from urllib.parse import quote
from fastapi import HTTPException
from sqlalchemy import select, func
from database import Design
from services.rfd3_generation import GenerationContractError, RESULT_SCHEMA_V2


def load_incremental_manifest(path, *, request, output_root, job_id):
    """Scan scalar accounting, then validate/yield one native pair at a time.

    The second pass runs inside the import transaction: a bad later record rolls
    back earlier inserts through the existing ingest owner. No population-sized
    JSON string, candidate list or duplicate coordinate read is retained.
    """
    from services.rfd3_generation import RESULT_SCHEMA, validate_result_manifest, _finite_number
    header, seen, count, accepted = {}, set(), 0, 0
    stats = {key: [None, 0.0, None] for key in ('residue_count', 'radius_of_gyration')}
    for key, value in iter_manifest_records(path):
        if key != 'candidate':
            header[key] = value
            continue
        identity = value.get('candidate_id')
        if not isinstance(identity, str) or not identity or identity in seen:
            raise GenerationContractError('RFD3 generation candidate identity is invalid')
        seen.add(identity); count += 1
        if not isinstance(value.get('accepted'), bool):
            raise GenerationContractError('RFD3 generation candidate accepted flag must be boolean')
        accepted += int(value['accepted'])
        for metric, summary in stats.items():
            number = _finite_number(value.get('metrics', {}).get(metric), metric, minimum=1 if metric == 'residue_count' else 0)
            summary[0] = number if summary[0] is None else min(summary[0], number)
            summary[1] += number
            summary[2] = number if summary[2] is None else max(summary[2], number)
    schema = header.get('schema')
    if schema not in {RESULT_SCHEMA, RESULT_SCHEMA_V2}:
        raise GenerationContractError('unsupported RFD3 generation result manifest schema')
    requested = request.get('generation', {}).get('num_designs')
    if isinstance(requested, bool) or not isinstance(requested, int):
        raise GenerationContractError('RFD3 generation candidate cardinality mismatch')
    if schema == RESULT_SCHEMA and (not count or count != requested):
        raise GenerationContractError('RFD3 generation candidate cardinality mismatch')
    aggregate = {'requested': requested, 'generated': count, 'accepted': accepted}
    for metric, label in [('residue_count', 'length'), ('radius_of_gyration', 'radius_of_gyration')]:
        low, total, high = stats[metric]
        aggregate[label] = {'min': low, 'mean': round(total / count, 6), 'max': high} if count else None
    if header.get('aggregate') != aggregate:
        raise GenerationContractError('RFD3 generation aggregate statistics mismatch')
    population = header.get('population')
    if schema == RESULT_SCHEMA_V2:
        for key, expected in {'requested': requested, 'generated': count, 'missing': max(0, requested-count), 'unexpected': max(0, count-requested)}.items():
            if not isinstance(population, dict) or isinstance(population.get(key), bool) or population.get(key) != expected:
                raise GenerationContractError('population accounting mismatch')
    fields = {'schema', 'job_id', 'request_id', 'request_sha256', 'aggregate'} | ({'execution_status', 'population'} if schema == RESULT_SCHEMA_V2 else set())
    if set(header) != fields:
        raise GenerationContractError('RFD3 generation result manifest must contain exact fields')
    # Reuse the existing authority for header, request and unpaired-byte checks.
    probe = {**header, 'schema': RESULT_SCHEMA_V2, 'candidates': [],
        'aggregate': {'requested': requested, 'generated': 0, 'accepted': 0, 'length': None, 'radius_of_gyration': None},
        'execution_status': header.get('execution_status', 'unknown'),
        'population': {**(population or {}), 'requested': requested, 'generated': 0, 'missing': max(0, requested), 'unexpected': max(0, -requested),
                       **({} if population is not None else {'failed': None, 'unpaired_artifacts': []})}}
    validate_result_manifest(probe, request=request, output_root=output_root, job_id=job_id)

    def candidates():
        for key, candidate in iter_manifest_records(path):
            if key != 'candidate': continue
            metrics = candidate['metrics']
            single = {**probe, 'candidates': [candidate],
                'aggregate': {'requested': requested, 'generated': 1, 'accepted': int(candidate['accepted']),
                    'length': {'min': metrics['residue_count'], 'mean': round(float(metrics['residue_count']), 6), 'max': metrics['residue_count']},
                    'radius_of_gyration': {'min': metrics['radius_of_gyration'], 'mean': round(float(metrics['radius_of_gyration']), 6), 'max': metrics['radius_of_gyration']}},
                'population': {'requested': requested, 'generated': 1, 'missing': max(0, requested-1), 'unexpected': max(0, 1-requested), 'failed': None, 'unpaired_artifacts': []}}
            yield validate_result_manifest(single, request=request, output_root=output_root, job_id=job_id)['candidates'][0]
    return header, candidates(), seen


async def candidate_batches(session, job_id, candidates):
    from itertools import islice
    position = 0
    while batch := list(islice(candidates, 256)):
        existing = {row.name: row for row in await session.scalars(select(Design).where(
            Design.job_id == job_id, Design.name.in_([row['candidate_id'] for row in batch])))}
        for candidate in batch:
            position += 1
            yield position, candidate, existing.get(candidate['candidate_id'])


def check_incremental_candidates(existing, manifest, digest):
    candidates = {candidate['candidate_id']: candidate for candidate in manifest['candidates']}
    names = {row.name for row in existing}
    if len(names) != len(existing) or not names <= set(candidates):
        raise GenerationContractError('persisted RFD3 generation candidates conflict with the producer manifest')
    for row in existing:
        candidate = candidates[row.name]
        provenance = row.provenance or {}
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:rfd3-generation:{manifest['job_id']}:{row.name}"))
        if (row.id != identity or any(provenance.get(key) != manifest[key] for key in ('request_id', 'request_sha256'))
                or any(provenance.get(key) != candidate[key] for key in ('accepted', 'metrics', 'artifact_manifest_sha256', 'artifacts'))):
            raise GenerationContractError('persisted RFD3 generation candidates conflict with the producer manifest')
        if manifest['schema'] != RESULT_SCHEMA_V2 and provenance.get('result_manifest_sha256') != digest:
            raise GenerationContractError('persisted RFD3 generation candidates conflict with the producer manifest')
    return names


async def retain_unpaired_artifacts(session, job, root, manifest):
    """Retain verified native files without manufacturing candidate Designs."""
    from database import JobArtifact
    for descriptor in (manifest.get('population') or {}).get('unpaired_artifacts', []):
        relative = descriptor['relative_path']
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f'bms:rfd3-unpaired:{job.id}:{job.retry_count or 0}:{relative}'))
        existing = await session.get(JobArtifact, identity)
        provenance = {'model_id': 'rfd3', 'request_sha256': manifest['request_sha256'],
                      'native_role': descriptor['role']}
        expected = (str(root / relative), descriptor['sha256'], descriptor['bytes'], provenance)
        if existing is not None:
            if (existing.storage_path, existing.sha256, existing.bytes, existing.provenance) != expected:
                raise GenerationContractError('persisted RFD3 unpaired artifact conflicts with native bytes')
        else:
            session.add(JobArtifact(id=identity, owner_job_id=job.id, attempt=job.retry_count or 0,
                logical_path=relative, storage_path=expected[0], sha256=expected[1], bytes=expected[2],
                media_type=descriptor['media_type'], provenance=provenance))


async def read_generation_results(job, session, *, offset=0, limit=None):
    """Read persisted native scalars, including a valid zero-yield v2 result.

    Omitted limit preserves the historical API. Explicit pages avoid loading
    coordinates and retain full producer totals rather than page-sized totals.
    """
    from routers.jobs import _rfd3_public_json
    if offset < 0 or (limit is not None and not 1 <= limit <= 1000):
        raise HTTPException(422, 'Invalid native page')
    params = job.params if isinstance(job.params, dict) else {}
    request = params.get('rfd3_generation_request')
    digest = params.get('rfd3_generation_result_manifest_sha256')
    aggregate = params.get('rfd3_generation_aggregate')
    if not isinstance(request, dict) or not isinstance(digest, str):
        raise HTTPException(404, 'RFD3 generation typed result is not available')
    if not isinstance(aggregate, dict):
        raise HTTPException(409, 'RFD3 generation aggregate contract is unavailable')
    total = await session.scalar(select(func.count()).select_from(Design).where(Design.job_id == job.id))
    if total != aggregate['generated']:
        raise HTTPException(409, 'RFD3 generation population does not match persisted candidates')
    aggregates = {}
    for label, metric in [('length', 'residue_count'), ('radius', 'radius_of_gyration'), ('helix', 'helix_count'), ('strand', 'strand_count')]:
        column = Design.provenance['metrics'][metric].as_float()
        minimum, mean, maximum = (await session.execute(select(func.min(column), func.avg(column), func.max(column)).where(Design.job_id == job.id))).one()
        aggregates[label] = {'min': minimum, 'mean': None if mean is None else round(mean, 6), 'max': maximum}
    for label, key in [('length', 'length'), ('radius', 'radius_of_gyration')]:
        expected = aggregate[key] or {'min': None, 'mean': None, 'max': None}
        if aggregates[label] != expected:
            raise HTTPException(409, 'RFD3 generation aggregate contract does not match persisted candidates')
    query = select(Design).where(Design.job_id == job.id).order_by(Design.name).offset(offset)
    if limit is not None: query = query.limit(limit)
    designs = list((await session.scalars(query)).all())
    candidates = []
    for design in designs:
        provenance = design.provenance or {}
        metrics = provenance.get('metrics')
        if not isinstance(metrics, dict):
            raise HTTPException(409, 'RFD3 generation candidate metrics are unavailable')
        candidates.append({'candidate_id': design.name, 'design_id': design.id,
            'source_structure': {'job_id': job.id, 'design_id': design.id, 'output_format': 'native'},
            'status': 'accepted' if provenance['accepted'] else 'generated',
            'length': metrics['residue_count'], 'radius': metrics['radius_of_gyration'],
            'helix_count': metrics['helix_count'], 'strand_count': metrics['strand_count'],
            'structure_url': f'/api/jobs/{quote(job.id, safe="")}/rfd3-generation/artifacts/{quote(design.name, safe="")}/candidate_structure'})
    return {'schema': 'bms.rfd3.generation.read-model.v1', 'job_id': job.id,
        'request': _rfd3_public_json(request), 'result_manifest_sha256': digest,
        'result_schema': params.get('rfd3_generation_result_schema'),
        'execution_status': params.get('rfd3_generation_execution_status', job.status),
        'population': params.get('rfd3_generation_population'),
        'counts': {key: aggregate[key] for key in ('requested', 'generated', 'accepted')},
        'aggregates': aggregates,
        'offset': offset, 'limit': limit, 'total': total,
        'candidates': candidates}
