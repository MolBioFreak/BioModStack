"""PM projection of producer-owned family MSA JobArtifact manifests."""
import json

from sqlalchemy import select
from database import Job, JobArtifact
from services.job_result_roots import resolve_persisted_job_result_root


class FamilyMsaResultAdapter:
    adapter_version = 1
    store_id = 'core'
    entity_kind = 'family_msa_job_result'

    def __init__(self, model_id, domain_kind):
        self.model_id = model_id
        self.domain_kind = domain_kind
        self.adapter_id = f'bms.family-msa.{model_id}.adapter.v1'
        self.display_name = f'Family alignment result: {model_id}'

    async def search(self, core_session, *, query, limit):
        from services.global_experiments.adapters import EntityProjection, _bounded_label, _search_inputs
        q = _search_inputs(query, limit)
        statement = select(Job).where(Job.model_id == self.model_id, Job.mode == 'family_msa')
        if q:
            statement = statement.where(Job.name.ilike(f'%{q}%') | Job.id.ilike(f'%{q}%'))
        jobs = (await core_session.scalars(statement.order_by(Job.created_at.desc()).limit(limit))).all()
        return [EntityProjection(j.id, self.entity_kind, _bounded_label(j.name, j.id), str(j.status),
                                 {'model_id': j.model_id, 'mode': j.mode}) for j in jobs]

    async def verify(self, core_session, entity_id):
        from services.global_experiments.adapters import AdapterError, _canonical_json_sha256, _read_verified_file, _receipt
        job = await core_session.get(Job, entity_id)
        if job is None or job.model_id != self.model_id or job.mode != 'family_msa':
            raise AdapterError('entity_not_found', 'Family alignment Job not found')
        rows = (await core_session.scalars(select(JobArtifact).where(
            JobArtifact.owner_job_id == job.id, JobArtifact.attempt == (job.retry_count or 0)
        ).order_by(JobArtifact.created_at.desc(), JobArtifact.id.desc()))).all()
        manifests = [a for a in rows if (a.provenance or {}).get('role') == 'result_manifest']
        if not manifests:
            raise AdapterError('source_contract_unavailable', 'Family alignment manifest unavailable')
        seal = manifests[0]
        root = resolve_persisted_job_result_root(job)
        _, data = _read_verified_file(seal.storage_path, seal.sha256, seal.bytes, canonical_root=root)
        try:
            manifest = json.loads(data)
            if (manifest['schema'] != 'bms.family-msa.result-manifest.v1' or
                manifest['job_id'] != job.id or manifest['attempt'] != seal.attempt or
                manifest['run_identity'] != (seal.provenance or {}).get('run_identity') or
                not isinstance(manifest['publication_id'], str) or not manifest['publication_id'] or
                not isinstance(manifest['artifacts'], list)):
                raise ValueError('invalid manifest identity')
            by_id = {a.id: a for a in rows}
            seen = set()
            for entry in manifest['artifacts']:
                a = by_id[entry['id']]
                if a.id in seen or a.id == seal.id:
                    raise ValueError('duplicate artifact')
                seen.add(a.id)
                if (entry != {'id': a.id, 'role': (a.provenance or {}).get('role'),
                    'logical_path': a.logical_path, 'sha256': a.sha256, 'bytes': a.bytes,
                    'media_type': a.media_type} or
                    (a.provenance or {}).get('run_identity') != manifest['run_identity']):
                    raise ValueError('artifact identity changed')
                _read_verified_file(a.storage_path, a.sha256, a.bytes, canonical_root=root)
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterError('source_contract_invalid', 'Family alignment manifest is invalid') from exc
        return _receipt(self, entity_id=job.id, entity_revision_id=seal.id,
            content_digest=seal.sha256, contract_digest=_canonical_json_sha256({'model_id': job.model_id, 'mode': job.mode}),
            reopen_uri=f'/alignments?job_id={job.id}', metadata={
                'model_id': job.model_id, 'mode': job.mode, 'job_status': str(job.status),
                'canonical_state': str(job.status), 'manifest_artifact_id': seal.id,
                'publication_id': manifest['publication_id'], 'attempt': seal.attempt,
                'artifacts': manifest['artifacts'], 'record_count': 1})
