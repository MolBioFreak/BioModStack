"""Exact native and Project-retained family alignment result readers."""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, JobArtifact, get_session
from experiment_database import get_experiment_session
from experiment_models import ExperimentArtifact, ExperimentExternalEntityReceipt, ExperimentResource
from experiment_services import ExperimentServiceError, ValidationFailure
from molbio_database import get_molbio_session
from routers.experiment_workspaces import _error, _operator_principal, _require_mutation_owner
from services.family_msa.sources import browse_dataset_resources, freeze_dataset_resources, resolve_molecular_member
from services.family_msa.publication import CustodyError, retained_path
from services.family_msa.read_model import alignment_document
from services.global_experiments.adapters import AdapterError, _read_verified_file, registry
from services.global_experiments.result_surfaces import _acknowledgement, _validate_persisted_authority
from services.job_result_roots import resolve_persisted_job_result_root

router = APIRouter(prefix='/api/family-msa', tags=['family-msa-results'])


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
    if not receipt or receipt.workspace_id != project_id or receipt.entity_kind != 'family_msa_job_result':
        raise HTTPException(404, detail='Result not found')
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
    return {'artifacts': [{'id': x['id'], 'role': x['role']} for x in entries]}


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
    return listing(entries)


@router.get('/projects/{project_id}/receipts/{receipt_id}/artifacts/{artifact_id}')
async def get_project_artifact(request: Request, project_id: str, receipt_id: str, artifact_id: str, session: AsyncSession = Depends(get_experiment_session)):
    _, read = await project_result(request, session, project_id, receipt_id)
    return Response(await read(artifact_id), media_type='application/octet-stream', headers={'Content-Disposition': 'attachment'})


@router.get('/projects/{project_id}/receipts/{receipt_id}/documents/{artifact_id}')
async def get_project_document(request: Request, project_id: str, receipt_id: str, artifact_id: str, session: AsyncSession = Depends(get_experiment_session)):
    entries, read = await project_result(request, session, project_id, receipt_id)
    return await document(entries, read, artifact_id)
