"""Observe retained binder outputs through model publication owners.

This is deliberately not a terminal finalizer. Native verdicts and Job lifecycle
remain unchanged; only producer-qualified outputs may become Designs under the
existing owner. Unranked/rejected documents remain registered observations.
"""
from pathlib import Path


async def observe_retained_binder_results(session, job, output_path, *, commit=False) -> dict:
    root = Path(output_path).absolute()
    if not job.output_dir or Path(job.output_dir).absolute() != root:
        raise ValueError('Retained binder output root differs from Job owner')
    result = {'job_id': job.id, 'model_id': job.model_id, 'available': False,
              'publication_ref': None, 'document_count': 0, 'native_record_count': 0}
    if not root.is_dir():
        return result
    from services.result_state_integrity import _sequence_native_publication_owner
    sequence_owner = _sequence_native_publication_owner(job)
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
