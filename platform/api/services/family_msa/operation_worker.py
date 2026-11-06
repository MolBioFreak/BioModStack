"""Post-processing child owned (and killed) by the transient workflow unit."""
import asyncio
import hashlib
import json
import sys
import zipfile
from pathlib import Path

# Invoked as a script by the same interpreter as the owned workflow runner.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def compute(work, job_id, session_factory):
    from services.family_msa.operations import ManagedOperation
    from services.family_msa.publication import retain_artifact
    from routers.family_msa_results import project_result_data, comparison_roster
    from experiment_models import ExperimentAggregateHead, ExperimentRevision
    from bms_alignment import compare, ensemble_support
    from bms_alignment.bundle import make_bundle
    operation = ManagedOperation.model_validate(json.loads((work / 'operation.json').read_text()))
    request = operation.request
    inputs = request.inputs
    async with session_factory() as session:
        head = await session.get(ExperimentAggregateHead, operation.request_dataset_id)
        revision = await session.get(ExperimentRevision, operation.request_revision_id) if operation.request_revision_id else None
        expected = {'schema': 'bms.family-msa.operation.v1', 'job_id': job_id, **operation.model_dump(mode='json', exclude={'request_revision_id'})}
        if (not head or head.workspace_id != operation.project_id or not revision or revision.subject_id != head.aggregate_id
                or json.loads(revision.canonical_payload) != expected
                or hashlib.sha256(revision.canonical_payload.encode()).hexdigest() != revision.payload_sha256):
            raise ValueError('Managed operation Project request binding changed')
        async def source(receipt_id):
            return await project_result_data(session, operation.project_id, receipt_id)
        if request.operation == 'compare':
            left = await comparison_roster(*(await source(inputs['left_receipt_id'])), inputs['left_artifact_id'])
            right = await comparison_roster(*(await source(inputs['right_receipt_id'])), inputs['right_artifact_id'])
            result = {'method': 'explicit-source-occurrence.v1', 'report': compare(left, right)}
        elif request.operation == 'ensemble-support':
            entries, read = await source(inputs['receipt_id'])
            reference = await comparison_roster(entries, read, inputs['reference_artifact_id'])
            members = [await comparison_roster(entries, read, ident) for ident in inputs['member_artifact_ids']]
            report = ensemble_support(reference, members)
            by_id = {entry['id']: entry for entry in entries if entry['role'] == 'alignment'}
            for sample, ident in zip(report['samples'], inputs['member_artifact_ids']):
                sample.update(receipt_id=inputs['receipt_id'], artifact_id=ident, logical_path=by_id[ident].get('logical_path'))
            result = {'receipt_id': inputs['receipt_id'], 'reference_artifact_id': inputs['reference_artifact_id'],
                      'reference_logical_path': by_id[inputs['reference_artifact_id']].get('logical_path'), 'report': report}
        else:
            entries, read = await source(inputs['receipt_id'])
            root = work / 'source'; root.mkdir()
            inventory, states, files = [], [], []
            for index, entry in enumerate(entries):
                path = f'artifacts/{index:06d}'
                target = root / path; target.parent.mkdir(exist_ok=True)
                target.write_bytes(await read(entry['id']))
                files.append(path)
                inventory.append({'id': entry['id'], 'role': entry['role'], 'logical_path': entry.get('logical_path'), 'path': path})
            # Revision IDs were frozen at admission, not read from a moving head.
            snapshots = operation.shared_states
            for index, state in enumerate(snapshots):
                path = f'shared-state/{index:06d}.json'
                target = root / path; target.parent.mkdir(exist_ok=True)
                target.write_text(json.dumps(state, ensure_ascii=False) + '\n')
                files.append(path); states.append({'id': state['id'], 'path': path})
            (root / 'project-export.json').write_text(json.dumps({'schema': 'bms.family-msa.project-export.v1',
                'project_id': operation.project_id, 'receipt_id': inputs['receipt_id'], 'artifacts': inventory, 'shared_states': states}))
            bundle = work / 'bundle'
            make_bundle(root, bundle, ['project-export.json', *files], excluded=[
                'Project shared states not explicitly selected', 'External dependencies not retained in the Project receipt'])
            output = work / 'operation-result.zip'
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in ['manifest.json', 'project-export.json', *files]:
                    archive.write(bundle / path, path)
        if request.operation != 'export':
            output = work / 'operation-result.json'
            output.write_text(json.dumps(result, ensure_ascii=False) + '\n')
        media_type = 'application/zip' if request.operation == 'export' else 'application/json'
        with output.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        resource_id = 'msa-operation-' + job_id + '-' + work.name
        with output.open('rb') as stream:
            await retain_artifact(session, source=stream, workspace_id=operation.project_id,
                lifecycle_owner_id=operation.request_dataset_id, resource_id=resource_id,
                logical_role='operation_result', logical_key=output.name,
                schema_name='bms.family-msa.operation-result', schema_version='1', media_type=media_type,
                expected_sha256=digest, expected_size=output.stat().st_size,
                provenance={'job_id': job_id, 'operation': request.operation})
        await session.commit()
        (work / 'operation-publication.json').write_text(json.dumps({'retained_resource_id': resource_id,
            'media_type': media_type, 'sha256': digest, 'result_path': output.name}))


if __name__ == '__main__':
    from experiment_database import experiment_session_factory
    asyncio.run(compute(Path(sys.argv[1]), sys.argv[2], experiment_session_factory))
