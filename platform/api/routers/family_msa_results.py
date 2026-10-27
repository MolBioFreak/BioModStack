"""Exact native and Project-retained family alignment result readers."""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import Job, JobArtifact, get_session
from experiment_database import get_experiment_session
from experiment_models import ExperimentArtifact, ExperimentExternalEntityReceipt, ExperimentResource, ExperimentAggregateHead, ExperimentRevision
from experiment_services import ExperimentServiceError, ValidationFailure, create_dataset, save_dataset_revision, sha256_text
from molbio_database import get_molbio_session
from routers.experiment_workspaces import _error, _operator_principal, _require_mutation_owner
from services.family_msa.sources import browse_dataset_resources, freeze_dataset_resources, resolve_molecular_member, resolve_design_member
from services.family_msa.publication import CustodyError, retained_path
from services.family_msa.read_model import alignment_document
from services.family_msa.shared import SharedState
from services.global_experiments.adapters import AdapterError, _read_verified_file, registry
from services.global_experiments.result_surfaces import _acknowledgement, _validate_persisted_authority
from services.job_result_roots import resolve_persisted_job_result_root

router = APIRouter(prefix='/api/family-msa', tags=['family-msa-results'])


@router.get('/projects/{project_id}/receipts')
async def list_project_alignments(request: Request, project_id: str, after: str = '',
                                  session: AsyncSession = Depends(get_experiment_session)):
    """Page existing Project receipts, without creating computation records."""
    await _require_mutation_owner(request, session, resource_id=project_id)
    rows = (await session.scalars(select(ExperimentExternalEntityReceipt).where(
        ExperimentExternalEntityReceipt.workspace_id == project_id,
        ExperimentExternalEntityReceipt.entity_kind.in_(('family_msa_job_result', 'family_msa_import')),
        ExperimentExternalEntityReceipt.id > after,
    ).order_by(ExperimentExternalEntityReceipt.id).limit(101))).all()
    page = rows[:100]
    return {'items': [{'receipt_id': r.id, 'entity_kind': r.entity_kind,
                       'availability': r.availability} for r in page],
            'next_cursor': page[-1].id if len(rows) > 100 else None}


@router.get('/settings-schema')
async def settings_schema():
    """The request owner's schema is the shared settings inventory."""
    from bms_alignment import AlignmentRequest
    return AlignmentRequest.model_json_schema()


class SourceReceipt(BaseModel):
    receipt_id: str
    entity_kind: str
    entity_id: str
    adapter_id: str
    revision: str | None
    content_digest: str | None
    availability: str


class SourceOccurrence(BaseModel):
    ordinal: int
    role: str
    member_identity: str
    member_sha256: str
    media_type: str | None
    value: Any
    receipt: SourceReceipt | None


class ResourcePage(BaseModel):
    schema_name: str = Field(alias='schema')
    project_id: str
    dataset_id: str
    revision_id: str
    revision_sha256: str
    items: list[SourceOccurrence]
    offset: int
    limit: int
    total: int
    next_offset: int | None


class ResourceSelection(BaseModel):
    schema_name: str = Field(alias='schema')
    project_id: str
    dataset_id: str
    revision_id: str
    revision_sha256: str
    members: list[SourceOccurrence]


class FreezeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ordinals: list[StrictInt] | None = None


class ResolvedMember(BaseModel):
    schema_name: str = Field(alias='schema')
    project_id: str
    dataset_id: str
    revision_id: str
    revision_sha256: str
    ordinal: int
    member_identity: str
    member_sha256: str
    status: str
    reason: str | None = None
    source_kind: str | None = None
    source_identity: str | None = None
    source_digest: str | None = None
    molecule: str | None = None
    label: str | None = None
    start: int | None = None
    end: int | None = None
    sequence: str | None = None
    source_positions: list[int] | None = None
    structure_residue_map: dict[str, Any] | None = None
    chain: str | None = None
    chains: list[dict[str, Any]] | None = None


SOURCE_PATH = '/projects/{project_id}/datasets/{dataset_id}/revisions/{revision_id}'


@router.get(SOURCE_PATH + '/resources', response_model=ResourcePage)
async def source_resources(request: Request, project_id: str, dataset_id: str, revision_id: str,
                           offset: int = 0, limit: int = 50,
                           session: AsyncSession = Depends(get_experiment_session)):
    await _require_mutation_owner(request, session, resource_id=project_id)
    try:
        return await browse_dataset_resources(session, project_id=project_id, dataset_id=dataset_id,
                                              revision_id=revision_id, offset=offset, limit=limit)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.post(SOURCE_PATH + '/resources/freeze', response_model=ResourceSelection)
async def source_freeze(request: Request, project_id: str, dataset_id: str, revision_id: str,
                        payload: FreezeRequest, session: AsyncSession = Depends(get_experiment_session)):
    """Return an immutable selection value; the Project draft owner persists it."""
    await _require_mutation_owner(request, session, resource_id=project_id)
    try:
        return await freeze_dataset_resources(session, project_id=project_id, dataset_id=dataset_id,
                                              revision_id=revision_id, ordinals=payload.ordinals)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(SOURCE_PATH + '/resources/{ordinal}/molecular', response_model=ResolvedMember)
async def source_molecular(request: Request, project_id: str, dataset_id: str, revision_id: str,
                           ordinal: int, start: int | None = None, end: int | None = None,
                           session: AsyncSession = Depends(get_experiment_session),
                           core: AsyncSession = Depends(get_session),
                           molbio: AsyncSession = Depends(get_molbio_session)):
    await _require_mutation_owner(request, session, resource_id=project_id)
    try:
        return await resolve_molecular_member(session, core, molbio, project_id=project_id,
            dataset_id=dataset_id, revision_id=revision_id, ordinal=ordinal, start=start, end=end)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(SOURCE_PATH + '/resources/{ordinal}/design', response_model=ResolvedMember)
async def source_design(request: Request, project_id: str, dataset_id: str, revision_id: str,
                        ordinal: int, chain: str | None = None, start: int | None = None, end: int | None = None,
                        session: AsyncSession = Depends(get_experiment_session), core: AsyncSession = Depends(get_session)):
    await _require_mutation_owner(request, session, resource_id=project_id)
    try:
        return await resolve_design_member(session, core, project_id=project_id, dataset_id=dataset_id, revision_id=revision_id, ordinal=ordinal, chain=chain, start=start, end=end)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


def unavailable(exc):
    return HTTPException(409, detail='Alignment result unavailable or corrupt')


async def job_result(request, session, job_id):
    # Jobs have no per-user ownership model. Restrict this new scientific reader
    # to the established application operator boundary instead of inventing one.
    _operator_principal(request)
    job = await session.get(Job, job_id)
    if not job or job.model_id not in ('family_msa_protein', 'family_msa_nucleic') or job.mode != 'family_msa':
        raise HTTPException(404, detail='Result not found')
    try:
        receipt = await registry.get(f'bms.family-msa.{job.model_id}.adapter.v1').verify(session, job_id)
    except AdapterError as exc:
        raise unavailable(exc) from exc
    meta = receipt['metadata']
    entries = [{'id': meta['manifest_artifact_id'], 'role': 'result_manifest'}] + meta['artifacts']
    async def read(ident):
        if ident not in {x['id'] for x in entries}:
            raise HTTPException(404, detail='Artifact not found')
        artifact = await session.get(JobArtifact, ident)
        if artifact is None or artifact.owner_job_id != job_id:
            raise HTTPException(404, detail='Artifact not found')
        try:
            _, data = _read_verified_file(artifact.storage_path, artifact.sha256, artifact.bytes, canonical_root=resolve_persisted_job_result_root(job))
        except AdapterError as exc:
            raise unavailable(exc) from exc
        return data
    return entries, read


async def project_result(request, session, project_id, receipt_id):
    await _require_mutation_owner(request, session, resource_id=project_id)
    receipt = await session.get(ExperimentExternalEntityReceipt, receipt_id)
    if not receipt or receipt.workspace_id != project_id or receipt.entity_kind not in ('family_msa_job_result', 'family_msa_import'):
        raise HTTPException(404, detail='Result not found')
    if receipt.entity_kind == 'family_msa_import':
        try:
            metadata = json.loads(receipt.acknowledgement_json)
            entries = metadata['artifacts']
            if metadata['schema'] != 'bms.family-msa.import.v1' or not isinstance(entries, list):
                raise ValueError('invalid import receipt')
        except (ValueError, KeyError, TypeError) as exc:
            raise unavailable(exc) from exc
        async def read_import(ident):
            entry = next ( (x for x in entries if x['id'] == ident), None)
            if entry is None:
                raise HTTPException(404, detail='Artifact not found')
            resource = await session.get(ExperimentResource, ident)
            artifact = await session.get(ExperimentArtifact, ident)
            if not resource or resource.workspace_id != project_id or resource.lifecycle_owner_id != receipt.id or not artifact or artifact.blob_sha256 != entry['sha256'] or artifact.logical_role != entry['role']:
                raise unavailable(None)
            try:
                return (await retained_path(session, artifact)).read_bytes()
            except (CustodyError, OSError) as exc:
                raise unavailable(exc) from exc
        return entries, read_import
    try:
        ack = _acknowledgement(receipt)
        _validate_persisted_authority(receipt, ack)
        metadata = ack['metadata']
        entries = metadata['retained_artifacts']
        if not isinstance(entries, list) or not entries or len({x['resource_id'] for x in entries}) != len(entries):
            raise ValueError('invalid retained inventory')
        source = {x['id']: x for x in metadata['artifacts']}
        if set(x['source_artifact_id'] for x in entries) != set(source) | {metadata['manifest_artifact_id']}:
            raise ValueError('incomplete retained inventory')
        for entry in entries:
            expected = 'result_manifest' if entry['source_artifact_id'] == metadata['manifest_artifact_id'] else source[entry['source_artifact_id']]['role']
            if entry['role'] != expected:
                raise ValueError('role mismatch')
    except (ValidationFailure, KeyError, TypeError, ValueError) as exc:
        # Do not disclose malformed stored authority to callers.
        raise unavailable(exc) from exc
    async def read(ident):
        entry = next((x for x in entries if x['resource_id'] == ident), None)
        if not entry:
            raise HTTPException(404, detail='Artifact not found')
        resource = await session.get(ExperimentResource, ident)
        artifact = await session.get(ExperimentArtifact, ident)
        if not resource or resource.workspace_id != project_id or not artifact or artifact.logical_role != entry['role']:
            raise HTTPException(404, detail='Artifact not found')
        expected = receipt.content_digest if entry['role'] == 'result_manifest' else source[entry['source_artifact_id']]['sha256']
        if artifact.blob_sha256 != expected:
            raise unavailable(None)
        try:
            path = await retained_path(session, artifact)
            data = path.read_bytes()
            import hashlib
            if hashlib.sha256(data).hexdigest() != expected:
                raise CustodyError('changed during read')
            return data
        except (CustodyError, OSError) as exc:
            raise unavailable(exc) from exc
    return [{'id': x['resource_id'], 'role': x['role'], 'logical_path': source.get(x['source_artifact_id'], {}).get('logical_path')} for x in entries], read


def listing(entries):
    return {'artifacts': [{'id': x['id'], 'role': x['role'], 'logical_path': x.get('logical_path')} for x in entries]}


async def document(entries, read, artifact_id):
    selected = next((x for x in entries if x['id'] == artifact_id and x['role'] == 'alignment'), None)
    receipts = [x for x in entries if x['role'] == 'receipt']
    if not selected or len(receipts) != 1:
        raise HTTPException(404, detail='Alignment document not found')
    try:
        receipt = json.loads(await read(receipts[0]['id']))
        roster = receipt['requested']['roster']['rows']
        fasta = (await read(artifact_id)).decode('utf-8')
        from pathlib import PurePosixPath
        parent = PurePosixPath(selected.get('logical_path') or '').parent
        maps = [x for x in entries if x['role'] == 'row_map' and PurePosixPath(x.get('logical_path') or '').parent == parent]
        if len(maps) > 1:
            raise ValueError('ambiguous row map')
        row_map = json.loads(await read(maps[0]['id'])) if maps else None
        return alignment_document(result_id=artifact_id, fasta=fasta, roster=roster, row_map=row_map).model_dump()
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise unavailable(exc) from exc


@router.get('/jobs/{job_id}')
async def get_job(request: Request, job_id: str, session: AsyncSession = Depends(get_session)):
    entries, _ = await job_result(request, session, job_id)
    return listing(entries)


@router.get('/jobs/{job_id}/artifacts/{artifact_id}')
async def get_job_artifact(request: Request, job_id: str, artifact_id: str, session: AsyncSession = Depends(get_session)):
    _, read = await job_result(request, session, job_id)
    return Response(await read(artifact_id), media_type='application/octet-stream', headers={'Content-Disposition': 'attachment'})


@router.get('/jobs/{job_id}/documents/{artifact_id}')
async def get_job_document(request: Request, job_id: str, artifact_id: str, session: AsyncSession = Depends(get_session)):
    entries, read = await job_result(request, session, job_id)
    return await document(entries, read, artifact_id)


@router.get('/projects/{project_id}/receipts/{receipt_id}')
async def get_project(request: Request, project_id: str, receipt_id: str, session: AsyncSession = Depends(get_experiment_session)):
    entries, _ = await project_result(request, session, project_id, receipt_id)
    result = listing(entries)
    receipt = await session.get(ExperimentExternalEntityReceipt, receipt_id)
    if receipt.entity_kind == 'family_msa_import':
        metadata = json.loads(receipt.acknowledgement_json)
        result['import_provenance'] = {key: metadata[key] for key in ('source_project_id', 'source_receipt_id', 'dependencies', 'shared_state')}
        result['import_provenance']['shared_states'] = metadata.get('shared_states', [])
        result['import_provenance']['artifact_id_map'] = metadata.get('artifact_id_map', {})
    return result


@router.get('/projects/{project_id}/receipts/{receipt_id}/artifacts/{artifact_id}')
async def get_project_artifact(request: Request, project_id: str, receipt_id: str, artifact_id: str, session: AsyncSession = Depends(get_experiment_session)):
    _, read = await project_result(request, session, project_id, receipt_id)
    return Response(await read(artifact_id), media_type='application/octet-stream', headers={'Content-Disposition': 'attachment'})


@router.get('/projects/{project_id}/receipts/{receipt_id}/documents/{artifact_id}')
async def get_project_document(request: Request, project_id: str, receipt_id: str, artifact_id: str, session: AsyncSession = Depends(get_experiment_session)):
    entries, read = await project_result(request, session, project_id, receipt_id)
    return await document(entries, read, artifact_id)


class ComparisonInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    left_receipt_id: str = Field(min_length=1)
    left_artifact_id: str = Field(min_length=1)
    right_receipt_id: str = Field(min_length=1)
    right_artifact_id: str = Field(min_length=1)


async def comparison_roster(entries, read, artifact_id):
    from bms_alignment.models import Row, SequenceSet
    doc = await document(entries, read, artifact_id)
    receipt_entry = next(x for x in entries if x['role'] == 'receipt')
    receipt = json.loads(await read(receipt_entry['id']))
    roster = receipt['requested']['roster']
    original = {row['row_id']: row for row in roster['rows']}
    rows = []
    for aligned in doc['rows']:
        source = original[aligned['row_id']]
        # Case changes do not change residue positions; U/T and all other
        # native transformations remain unknown unless explicitly mapped.
        source_residues = ''.join(c for c in source['sequence'] if c not in '-.')
        aligned_residues = ''.join(c for c in aligned['aligned'] if c not in '-.')
        same = source_residues.upper() == aligned_residues.upper()
        positions = source.get('source_positions')
        if same and positions is not None and len(positions) == len(source['sequence']):
            residues = iter([p for c, p in zip(source['sequence'], positions) if c not in '-.'])
            positions = [None if c in '-.' else next(residues) for c in aligned['aligned']]
        else:
            positions = None
        rows.append(Row(row_id=aligned['row_id'], header=aligned['label'], sequence=aligned['aligned'],
                        source_member_id=source.get('source_member_id'), occurrence_id=source.get('occurrence_id'),
                        source_offset=source.get('source_offset') if same and source.get('source_positions') is None else None,
                        source_positions=positions, transformation_provenance=source.get('transformation_provenance')))
    return SequenceSet(molecule=roster['molecule'], rows=rows)


@router.post('/projects/{project_id}/compare')
async def compare_project_results(request: Request, project_id: str, payload: ComparisonInput,
                                  session: AsyncSession = Depends(get_experiment_session)):
    from bms_alignment import compare
    left_entries, left_read = await project_result(request, session, project_id, payload.left_receipt_id)
    right_entries, right_read = await project_result(request, session, project_id, payload.right_receipt_id)
    left = await comparison_roster(left_entries, left_read, payload.left_artifact_id)
    right = await comparison_roster(right_entries, right_read, payload.right_artifact_id)
    return {'method': 'explicit-source-occurrence.v1', 'report': compare(left, right)}


SHARED_KIND = 'family_msa.shared_state.v1'


class SharedCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=255)
    domain_experiment_id: str = Field(min_length=1, max_length=128)
    state: SharedState


class SharedSave(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_head_generation: StrictInt = Field(ge=0)
    state: SharedState


def shared_revision(head, revision):
    return {'id': head.aggregate_id, 'project_id': head.workspace_id,
            'head_generation': head.head_generation, 'revision_id': revision.resource_id,
            'revision_number': revision.revision_number, 'payload_sha256': revision.payload_sha256,
            'state': json.loads(revision.canonical_payload)}


async def shared_head(session, project_id, dataset_id):
    head = await session.get(ExperimentAggregateHead, dataset_id)
    if not head or head.workspace_id != project_id or head.aggregate_kind != 'dataset' or head.dataset_kind != SHARED_KIND:
        raise HTTPException(404, detail='Shared alignment state not found')
    return head


@router.post('/projects/{project_id}/shared-state', status_code=201)
async def create_shared_state(request: Request, project_id: str, payload: SharedCreate,
                              session: AsyncSession = Depends(get_experiment_session)):
    await _require_mutation_owner(request, session, resource_id=project_id)
    try:
        head = await create_dataset(session, project_id, payload.name, SHARED_KIND,
                                    experiment_id=payload.domain_experiment_id)
        revision = await save_dataset_revision(session, head.aggregate_id, payload.state.model_dump(by_alias=True),
                                               expected_head_generation=head.head_generation)
        await session.commit()
        return shared_revision(head, revision)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.put('/projects/{project_id}/shared-state/{dataset_id}')
async def save_shared_state(request: Request, project_id: str, dataset_id: str, payload: SharedSave,
                            session: AsyncSession = Depends(get_experiment_session)):
    await _require_mutation_owner(request, session, resource_id=project_id)
    head = await shared_head(session, project_id, dataset_id)
    try:
        revision = await save_dataset_revision(session, dataset_id, payload.state.model_dump(by_alias=True),
                                               expected_head_generation=payload.expected_head_generation)
        await session.commit()
        await session.refresh(head)
        return shared_revision(head, revision)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get('/projects/{project_id}/shared-state/{dataset_id}')
async def get_shared_state(request: Request, project_id: str, dataset_id: str, revision_id: str | None = None,
                           session: AsyncSession = Depends(get_experiment_session)):
    await _require_mutation_owner(request, session, resource_id=project_id)
    head = await shared_head(session, project_id, dataset_id)
    revision = await session.get(ExperimentRevision, revision_id or head.current_revision_id)
    if not revision or revision.subject_id != dataset_id:
        raise HTTPException(404, detail='Shared alignment revision not found')
    if sha256_text(revision.canonical_payload) != revision.payload_sha256:
        raise HTTPException(409, detail='Shared alignment revision corrupt')
    return shared_revision(head, revision)


class ImportBundle(BaseModel):
    model_config = ConfigDict(extra='forbid')
    replay_key: str = Field(min_length=1, max_length=255)
    manifest: dict[str, Any]
    files: dict[str, str]  # base64, keyed only by portable paths
    domain_experiment_id: str | None = None


class ImportReceipt(BaseModel):
    receipt_id: str
    replayed: bool
    shared_state: str
    shared_states: list[dict[str, Any]] = Field(default_factory=list)


@router.post('/projects/{project_id}/imports/alignment', response_model=ImportReceipt)
async def import_alignment(request: Request, project_id: str, format: str, replay_key: str, molecule: str,
                           session: AsyncSession = Depends(get_experiment_session)):
    """Retain an uploaded source and a clearly separate display projection."""
    import tempfile
    from pathlib import Path
    from bms_alignment.bundle import make_bundle
    from bms_alignment.formats import parse, project_a3m
    await _require_mutation_owner(request, session, resource_id=project_id)
    try:
        if format not in ('fasta', 'stockholm', 'clustal', 'a3m') or not replay_key or len(replay_key) > 255:
            raise ValueError('invalid import parameters')
        raw = await request.body()
        roster = parse(raw.decode('utf-8'), format, molecule)
        projected = {row['row_id']: row['match_columns'] for row in project_a3m(roster)} if format == 'a3m' else {}
        rows = [{'row_id': row.row_id, 'header': row.header,
                 'sequence': projected.get(row.row_id, row.sequence)} for row in roster.rows]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'; root.mkdir()
            (root / 'original').write_bytes(raw)
            (root / 'display.fasta').write_text(''.join(f">{row['row_id']}\n{row['sequence']}\n" for row in rows), encoding='utf-8')
            (root / 'receipt.json').write_text(json.dumps({'requested': {'roster': {'molecule': molecule, 'rows': rows}},
                'import': {'source_format': format, 'display': 'match columns only' if format == 'a3m' else 'row projection',
                           'molecule': molecule}}), encoding='utf-8')
            entries = [{'id': 'original', 'role': 'source', 'logical_path': f'original.{format}', 'path': 'original'},
                       {'id': 'alignment', 'role': 'alignment', 'logical_path': 'display.fasta', 'path': 'display.fasta'},
                       {'id': 'receipt', 'role': 'receipt', 'logical_path': 'receipt.json', 'path': 'receipt.json'}]
            (root / 'project-export.json').write_text(json.dumps({'schema': 'bms.family-msa.project-export.v1',
                'project_id': project_id, 'receipt_id': 'file-import', 'artifacts': entries, 'shared_states': []}), encoding='utf-8')
            bundle = Path(directory) / 'bundle'
            manifest = make_bundle(root, bundle, ['project-export.json', 'original', 'display.fasta', 'receipt.json'])
            return await _import_directory(project_id, replay_key, manifest, bundle, session)
    except HTTPException:
        await session.rollback()
        raise
    except (ValueError, KeyError, TypeError, OSError, CustodyError) as exc:
        await session.rollback()
        raise HTTPException(400, detail='Unable to import alignment file') from exc


async def _import_directory(project_id, replay_key, manifest, root, session, domain_experiment_id=None):
    import hashlib
    from bms_alignment.bundle import inspect_project_bundle
    from bms_alignment.project_remap import remap_state
    from pydantic import ValidationError
    from experiment_services import _resource, canonical_json
    from services.family_msa.publication import retain_artifact

    inspection = inspect_project_bundle(root)
    inventory = inspection['inventory']
    raw_entries = inventory['artifacts']
    if not raw_entries or any(not isinstance(x.get('id'), str) or not isinstance(x.get('role'), str) or not x['role'] for x in raw_entries):
        raise ValueError('invalid artifact inventory')
    if len({x['id'] for x in raw_entries}) != len(raw_entries):
        raise ValueError('duplicate artifact identity')
    hashes = {item['path']: item['sha256'] for item in manifest['files']}
    digest_input = {'manifest': manifest, 'files': hashes}
    if domain_experiment_id is not None:
        digest_input['domain_experiment_id'] = domain_experiment_id
    digest = hashlib.sha256(canonical_json(digest_input).encode()).hexdigest()
    receipt_id = 'msa-import-' + hashlib.sha256(canonical_json([project_id, replay_key]).encode()).hexdigest()
    existing = await session.get(ExperimentExternalEntityReceipt, receipt_id)
    if existing:
        if existing.workspace_id != project_id or existing.entity_kind != 'family_msa_import' or existing.content_digest != digest:
            raise HTTPException(409, detail='Import replay key conflicts with different content')
        ack = json.loads(existing.acknowledgement_json)
        return {'receipt_id': receipt_id, 'replayed': True, 'shared_state': ack['shared_state'], 'shared_states': ack.get('shared_states', [])}
    await _resource(session, kind='external_entity_receipt', workspace_id=project_id, lifecycle_owner_id=project_id, resource_id=receipt_id)
    entries = []
    groups = [(x, x['role']) for x in raw_entries] + [(x, 'shared_state_snapshot') for x in inventory['shared_states']]
    descriptors = {x['path']: x for x in manifest['files']}
    for index, (entry, role) in enumerate(groups):
        artifact_id = receipt_id + '-' + str(index)
        descriptor = descriptors[entry['path']]
        with (root / entry['path']).open('rb') as source:
            artifact = await retain_artifact(session, source=source, workspace_id=project_id,
                lifecycle_owner_id=receipt_id, resource_id=artifact_id, logical_role=role,
                logical_key=entry.get('logical_path') or entry['path'], schema_name='bms.family-msa.import',
                schema_version='1', media_type='application/octet-stream', expected_sha256=descriptor['sha256'],
                expected_size=descriptor['size'], provenance={'producer': 'portable_import', 'source_id': entry['id']})
        entries.append({'id': artifact_id, 'role': role, 'logical_path': entry.get('logical_path'), 'sha256': artifact.blob_sha256})
    artifact_ids = {entry['id']: entries[index]['id'] for index, entry in enumerate(raw_entries)}
    reports = []
    for entry in inventory['shared_states']:
        report = {'source_id': entry['id'], 'status': 'retained_unmapped', 'dataset_id': None, 'unresolved': []}
        try:
            snapshot = json.loads((root / entry['path']).read_bytes())
            state = SharedState.model_validate(snapshot['state'])
            mapped = remap_state(state.model_dump(by_alias=True), artifact_ids)
            report['unresolved'] = [item.model_dump() for item in mapped.unresolved]
        except (ValueError, KeyError, TypeError, ValidationError):
            report['reason'] = 'unrecognized shared state snapshot'
        else:
            if domain_experiment_id:
                head = await create_dataset(session, project_id, 'Imported alignment state', SHARED_KIND,
                                            experiment_id=domain_experiment_id)
                await save_dataset_revision(session, head.aggregate_id, mapped.state,
                                            expected_head_generation=head.head_generation)
                report.update(status='imported', dataset_id=head.aggregate_id)
            else:
                report['reason'] = 'domain_experiment_id not supplied'
        reports.append(report)
    if not reports:
        state_status = 'retained_unmapped' if any(e['role'] == 'shared_state_snapshot' for e in raw_entries) else 'none'
    elif not all(r['status'] == 'imported' for r in reports):
        state_status = 'retained_unmapped'
    else:
        state_status = 'imported_with_unresolved' if any(r['unresolved'] for r in reports) else 'imported'
    ack = {'schema': 'bms.family-msa.import.v1', 'artifacts': entries,
           'source_project_id': inventory.get('project_id'), 'source_receipt_id': inventory.get('receipt_id'),
           'dependencies': inspection['dependencies'], 'shared_state': state_status, 'shared_states': reports, 'artifact_id_map': artifact_ids}
    session.add(ExperimentExternalEntityReceipt(id=receipt_id, workspace_id=project_id, resource_id=receipt_id,
        store_id='experiment', entity_kind='family_msa_import', entity_id=digest,
        generation_or_revision='1', content_digest=digest, availability='available',
        verification_authority='bms.family-msa.import.v1', acknowledgement_json=canonical_json(ack)))
    await session.commit()
    return {'receipt_id': receipt_id, 'replayed': False, 'shared_state': state_status, 'shared_states': reports}


@router.post('/projects/{project_id}/imports', response_model=ImportReceipt)
async def import_bundle(request: Request, project_id: str, payload: ImportBundle,
                        session: AsyncSession = Depends(get_experiment_session)):
    """Legacy JSON import; retained for existing clients."""
    import base64
    import tempfile
    from pathlib import Path
    from bms_alignment.bundle import _safe_name
    await _require_mutation_owner(request, session, resource_id=project_id)
    try:
        decoded = {name: base64.b64decode(value, validate=True) for name, value in payload.files.items()}
        if any(not _safe_name(name) for name in decoded):
            raise ValueError('unsafe bundle path')
        if set(decoded) != set(payload.manifest['dependencies']['included']):
            raise ValueError('uploaded files differ from manifest')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'manifest.json').write_text(json.dumps(payload.manifest), encoding='utf-8')
            for name, data in decoded.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            return await _import_directory(project_id, payload.replay_key, payload.manifest, root, session, payload.domain_experiment_id)
    except HTTPException:
        await session.rollback()
        raise
    except (ValueError, KeyError, TypeError, OSError, CustodyError) as exc:
        await session.rollback()
        raise HTTPException(400, detail='Invalid or unavailable import bundle') from exc


@router.post('/projects/{project_id}/imports/stream', response_model=ImportReceipt)
async def import_bundle_stream(request: Request, project_id: str,
                               session: AsyncSession = Depends(get_experiment_session)):
    """Length-prefixed JSON metadata followed by manifest-ordered raw file bytes."""
    import tempfile
    from pathlib import Path
    from bms_alignment.bundle import _safe_name
    await _require_mutation_owner(request, session, resource_id=project_id)
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            iterator = request.stream().__aiter__()
            # Retain a view instead of repeatedly copying a potentially large ASGI chunk.
            pending = memoryview(b'')
            async def take(n):
                nonlocal pending
                parts = []
                while n:
                    if not pending:
                        try:
                            pending = memoryview(await iterator.__anext__())
                        except StopAsyncIteration:
                            raise ValueError('truncated upload')
                        if not pending:
                            raise ValueError('truncated upload')
                    piece, pending = pending[:n], pending[n:]
                    parts.append(piece)
                    n -= len(piece)
                return b''.join(parts)
            header_size = int.from_bytes(await take(8), 'big')
            # Spool the declared header before parsing: an incomplete upload must not
            # accumulate its entire received prefix in memory.  json.load still
            # materializes complete metadata, as required by the existing model.
            with tempfile.TemporaryFile(mode='w+b', dir=directory) as header:
                remaining = header_size
                while remaining:
                    chunk = await take(min(remaining, 1024 * 1024))
                    header.write(chunk)
                    remaining -= len(chunk)
                header.seek(0)
                metadata = json.load(header)
            payload = ImportBundle.model_validate({**metadata, 'files': {}})
            manifest = payload.manifest
            descriptors = manifest['files']
            if not isinstance(descriptors, list):
                raise ValueError('invalid file descriptors')
            seen = set()
            for item in descriptors:
                name, size = item['path'], item['size']
                if not _safe_name(name) or name in seen or type(size) is not int or size < 0:
                    raise ValueError('invalid file descriptor')
                seen.add(name)
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('wb') as dest:
                    remaining = size
                    while remaining:
                        chunk = await take(min(remaining, 1024 * 1024))
                        dest.write(chunk)
                        remaining -= len(chunk)
            if pending:
                raise ValueError('trailing upload bytes')
            async for chunk in iterator:
                if chunk:
                    raise ValueError('trailing upload bytes')
            (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            return await _import_directory(project_id, payload.replay_key, manifest, root, session, payload.domain_experiment_id)
    except HTTPException:
        await session.rollback()
        raise
    except (ValueError, KeyError, TypeError, OSError, CustodyError) as exc:
        await session.rollback()
        raise HTTPException(400, detail='Invalid or unavailable import bundle') from exc
