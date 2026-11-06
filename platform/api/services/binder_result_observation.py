"""Observe retained binder outputs through model publication owners.

This is deliberately not a terminal finalizer. Native verdicts and Job lifecycle
remain unchanged; only producer-qualified outputs may become Designs under the
existing owner. Unranked/rejected documents remain registered observations.
"""
from pathlib import Path


async def observe_retained_binder_results(session, job, output_path, *, commit=False) -> dict:
    from services.binder_retained_publication import ROOTS
    from services.result_state_integrity import _sequence_native_publication_owner
    from services.binder_publication_roots import publication_root
    from services.rfd4_proteina_results import (
        PUBLICATIONS, publication_model, has_native_publication, publication_spec,
        publish_native_generation, read_published_generation_page, PUBLICATION_KEY,
    )
    sequence_owner = _sequence_native_publication_owner(job)
    params = job.params if isinstance(job.params, dict) else {}
    rfd3_owner = (job.model_id == 'protein_modification_experimental' and job.mode == 'de_novo_design'
                 and (params.get('generator_model_id') or params.get('generator') or 'rfd3') == 'rfd3')
    result = {'job_id': job.id, 'model_id': job.model_id, 'available': False,
              'publication_ref': None, 'document_count': 0, 'native_record_count': 0}
    if (job.model_id != 'bindcraft2' and job.model_id not in ROOTS and sequence_owner is None
            and publication_model(job) not in PUBLICATIONS and not rfd3_owner):
        return result
    root = publication_root(job, output_path)
    if not root.is_dir():
        return result
    if has_native_publication(job, root):
        prefix, name, _ = publication_spec(job)
        if not (root / prefix / name).is_file():
            return result  # Pre-runtime failures may have only logs, not a zero-yield receipt.
        await publish_native_generation(session, job, root)
        page = await read_published_generation_page(job, session, limit=1)
        result.update(available=True, publication_ref=PUBLICATION_KEY,
                      native_record_count=page['total'], retained_file_count=len(page['artifacts']))
        if commit:
            await session.commit()
        return result
    if rfd3_owner:
        manifest = root / 'collected/rfd3_generation/rfd3_generation_result_manifest.json'
        if not manifest.is_file():
            return result
        # RFD3 already owns paired Design rows plus retained unpaired artifacts.
        # Import through that owner only; never call the success finalizer here.
        from services.result_ingester import _ingest_rfd3_generation_manifest
        count = await _ingest_rfd3_generation_manifest(job, root, session, commit=False)
        result.update(available=True, publication_ref='rfd3_generation_result_manifest_sha256',
                      native_record_count=count, document_count=count)
        if commit:
            await session.commit()
        return result
    if job.model_id != 'bindcraft2' and job.model_id not in ROOTS and sequence_owner is None:
        return result
    if sequence_owner is not None:
        if not (root / sequence_owner.DIRECTORY).is_dir():
            return result
        await sequence_owner.publish_native_results(job, root, session)
        native = await sequence_owner.read_published_native_results(job, session)
        result.update(available=True, publication_ref=sequence_owner.DIRECTORY,
                      native_record_count=len(native['records']) if isinstance(native.get('records'), list) else None,
                      native_document=native)
        if commit:
            await session.commit()
        return result
    if job.model_id == 'bindcraft2':
        from services.bindcraft2_publication import native_campaign_root, publish_native_results, _inventory
        from services.bindcraft2_native_results import read_native_publication
        campaign = native_campaign_root(job, root)
        native = read_native_publication(campaign)
        if not any(a.trajectories or a.draws or a.retained or a.documents or a.attempts
                   or a.claimed_attempts is not None for a in native.arms) and not _inventory(campaign, native):
            return result
        await publish_native_results(job, root, session, commit=False, observations_only=True)
        key = 'bindcraft2_native_publication'
        result.update(document_count=sum(len(a.documents) for a in native.arms),
                      native_record_count=sum(len(a.trajectories) + len(a.draws) + len(a.retained)
                                              for a in native.arms),
                      arms=[{'arm': a.name, **a.accounting} for a in native.arms])
    else:
        from services.binder_retained_publication import publish_retained_documents, PUBLICATION_KEY
        publication = await publish_retained_documents(session, job, root)
        if publication is None:
            return result
        key = PUBLICATION_KEY
        result.update(document_count=sum(Path(name).suffix.lower() in {'.pdb', '.cif', '.mmcif'}
                                         for name in publication['files']),
                      retained_file_count=len(publication['files']), native_record_count=None)
    result.update(available=True, publication_ref=key)
    from sqlalchemy import select
    from database import JobArtifact
    prefix = 'bindcraft2/native/' if job.model_id == 'bindcraft2' else 'binder/retained/'
    artifacts = list((await session.scalars(select(JobArtifact).where(
        JobArtifact.owner_job_id == job.id, JobArtifact.attempt == (job.retry_count or 0),
        JobArtifact.logical_path.startswith(prefix)).order_by(JobArtifact.logical_path))).all())
    result['retained_file_count'] = len(artifacts)
    result['outputs'] = [{'kind': 'native_document', 'job_id': job.id, 'artifact_id': artifact.id}
                         for artifact in artifacts
                         if artifact.media_type in {'chemical/x-pdb', 'chemical/x-mmcif'}]
    if commit:
        await session.commit()
    else:
        await session.flush()
    return result
