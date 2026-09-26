"""Shared resolution of existing Job/Design/document provenance.

No model settings, numerical interpretation or continuation policy lives here.
"""
from pathlib import Path
import hashlib
import json
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from database import Design, Job, JobArtifact


class CandidateDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifact_id: str | None = None
    target_state: str | None = None


def root_id(job):
    return getattr(job, 'lineage_root_job_id', None) or (job.params or {}).get('lineage_root_job_id') or (job.params or {}).get('iteration_source_root_job_id') or job.id


async def _ppiflow_feature_targets(job, prepared, session):
    """Consume the native pre-sampling JSON export; never deserialize features."""
    if session is None:
        return []
    hashes = {row['role']: row['sha256'] for row in prepared['source_bindings']}
    sources = {row['source_row_index']: row for row in prepared['source_rows']}
    targets = {}
    for design in await session.scalars(select(Design).where(Design.job_id == job.id).order_by(Design.id)):
        provenance = design.provenance or {}
        export = provenance.get('independent_target') or {}
        document = export.get('document') or {}
        index = (provenance.get('source') or {}).get('source_row_index')
        source = sources.get(index)
        if (source is None or document.get('schema_version') != 'ppiflow-independent-target/1'
                or document.get('source_feature_sha256') != hashes.get(f'csv_row:{index}')):
            continue
        raw = (json.dumps(document, sort_keys=True, separators=(',', ':')) + '\n').encode()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != export.get('sha256'):
            continue
        components = []
        for chain in document.get('chains', []):
            if not isinstance(chain.get('chain_id'), str) or not isinstance(chain.get('sequence'), str) or not chain['sequence']:
                components = []
                break
            # Native feature indices and unknown insertion codes are preserved
            # in the bound document, never recast as structural author evidence.
            components.append({'id': chain['chain_id'], 'type': 'protein', 'sequence': chain['sequence']})
        if not components or len({row['id'] for row in components}) != len(components):
            continue
        key = (index, digest)
        target = targets.setdefault(key, {
            'owner_job_id': job.id, 'name': source['native_target_name'],
            'source_row_index': index, 'sha256': digest, 'independent_target': export,
            'chains': [row['id'] for row in components], 'components': components,
            'source_design_ids': [],
        })
        target['source_design_ids'].append(design.id)
    return list(targets.values())


async def declared_targets(source, session):
    """Nearest independently declared inputs, following persisted ancestry only."""
    pending, seen = [source], set()
    while pending:
        job = pending.pop(0)
        if job is None or job.id in seen:
            continue
        seen.add(job.id)
        params = job.params or {}
        if job.model_id == 'ppiflow' and params.get('ppiflow_generation_request'):
            from paths import resolve_runtime_data_path
            from services.ppiflow_generation import read_prepared_ppiflow_generation_request
            directory = resolve_runtime_data_path(params['ppiflow_generation_request'])
            prepared = read_prepared_ppiflow_generation_request(job.mode, params, directory)
            transport = prepared['transport_settings']
            if transport.get('target_pdb'):
                return [dict(owner_job_id=job.id, name=row['native_target_name'],
                             source_row_index=row['source_row_index'],
                             target_path=str(directory / transport['target_pdb']),
                             chains=transport.get('target_chain') or transport.get('antigen_chain'))
                        for row in prepared['source_rows']]
            return await _ppiflow_feature_targets(job, prepared, session)
        targets = (params.get('bindcraft2_settings') or {}).get('targets')
        if isinstance(targets, list) and targets:
            return [dict(owner_job_id=job.id, name=t.get('name'), target_path=t.get('target_path'),
                         chains=t.get('chains'))
                    for t in targets if isinstance(t, dict)]
        if params.get('boltzgen_target_pdb_path'):
            return [dict(owner_job_id=job.id, name=None, target_path=params['boltzgen_target_pdb_path'],
                         chains=params.get('target_chains'))]
        if params.get('target_pdb'):
            saved = (params.get('blind_pose_selected') or {}).get('target_context') or {}
            return [dict(owner_job_id=saved.get('owner_job_id', job.id),
                         name=saved.get('name'), target_path=params['target_pdb'])]
        ancestors = [params.get('selection_source_job_id'), getattr(job, 'parent_job_id', None),
                     params.get('iteration_source_job_id'), params.get('parent_job_id'), root_id(job)]
        parents = [await session.get(Job, identity)
                   for identity in dict.fromkeys(i for i in ancestors if i and i not in seen)]
        pending[0:0] = parents
    return []


def documents(source, design):
    key = {'ppiflow': 'ppiflow_generation_publication',
           'boltzgen': 'boltzgen_generation_publication'}.get(
               getattr(source, 'model_id', None), 'bindcraft2_native_publication')
    publication = (getattr(source, 'provenance', None) or {}).get(key) or {}
    rows = next((row.get('structures', []) for row in publication.get('candidates', [])
                 if row.get('design_id') == design.id), [])
    # Use the existing governed file transport, not viewer-local artifact IDs.
    from paths import to_allowed_relative
    from urllib.parse import quote
    result = []
    for doc in rows:
        item = dict(doc)
        relative = doc.get('path')
        if relative is None and str(doc.get('logical_path', '')).startswith('bindcraft2/native/'):
            relative = doc['logical_path'].removeprefix('bindcraft2/native/')
        if publication.get('root') and relative:
            path = Path(publication['root']) / publication.get('campaign_root', '.') / relative
            try:
                item['download_url'] = '/api/files/download/' + quote(to_allowed_relative(path), safe='/')
            except ValueError:
                pass  # Inspection availability is not a selection prerequisite.
        result.append(item)
    return result


async def selected_document(source, design, selector, session):
    provenance = getattr(design, 'provenance', None) or {}
    identity = {'design_id': design.id, 'owner_job_id': source.id,
                'lineage_root_job_id': root_id(source),
                'origin_job_id': getattr(design, 'origin_job_id', None),
                'parent_design_id': getattr(design, 'parent_design_id', None),
                'design_provenance': provenance}
    path = design.pdb_path
    identity.update({key: provenance[key] for key in ('primary_artifact_id', 'primary_target_state') if key in provenance})
    if selector and (selector.artifact_id is not None or selector.target_state is not None):
        matches = [doc for doc in documents(source, design)
                   if (selector.artifact_id is None or doc.get('artifact_id') == selector.artifact_id)
                   and (selector.target_state is None or doc.get('target_state') == selector.target_state)]
        if len(matches) != 1:
            raise ValueError('Select one producer-bound document for the requested Design')
        doc = matches[0]
        artifact = await session.get(JobArtifact, doc['artifact_id'])
        if artifact is None or artifact.owner_job_id != source.id or artifact.sha256 != doc['sha256']:
            raise ValueError('Selected document differs from its owned artifact')
        path = artifact.storage_path
        identity.update(artifact_id=artifact.id, target_state=doc.get('target_state'),
                        logical_path=artifact.logical_path, artifact_sha256=artifact.sha256,
                        producer_document={key: value for key, value in doc.items() if key != 'download_url'})
    else:
        identity.update(artifact_id=provenance.get('primary_artifact_id'),
                        target_state=provenance.get('primary_target_state'))
    return Path(path), identity
