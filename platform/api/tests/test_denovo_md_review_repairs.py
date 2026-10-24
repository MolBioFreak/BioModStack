"""Review regressions: real source/preview and scratch approval persistence, NOT MD inference."""
from copy import deepcopy
import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from database import Design, Job, JobArtifact
from schemas import ExecutionPolicy
from services import binder_round as rounds, binder_selected_round as selected_round
from services.md import starting_structures as sources
from test_md_typed_launch import project_context_preview_store


@pytest.mark.parametrize('project_context_preview_store', ['guided'], indirect=True)
@pytest.mark.parametrize('selection', ['primary', 'stale_digest', 'model', 'alternate', 'alternate_identical'])
def test_selected_md_preview_uses_exact_candidate_authority(project_context_preview_store, monkeypatch, selection):
    store = project_context_preview_store
    monkeypatch.setenv('BMS_FEATURE_MOLECULAR_DYNAMICS', '1')
    intent = deepcopy(store['native_intent'])
    original_settings = deepcopy({key: intent[key] for key in ('stages', 'analysis', 'windows')})

    async def exercise():
        async with store['core_sessions']() as session:
            root = await session.scalar(select(Job))
            design = await session.get(Design, store['expected_design_id'])
            from pathlib import Path
            primary = Path(design.pdb_path)
            original = primary.read_bytes()
            ref = {'kind': 'design', 'job_id': root.id, 'design_id': design.id}
            if selection in {'model', 'alternate', 'alternate_identical'}:
                selected_path = primary.with_name('selected-document.pdb')
                raw = original
                if selection == 'model':
                    body = b''.join(line for line in original.splitlines(keepends=True)
                                    if not line.startswith((b'END', b'MODEL')))
                    # Distinct selected MODEL records over the existing native fixture;
                    # this is a coordinate custody fixture, not generated MD output.
                    raw = b'MODEL        1\n' + body + b'ENDMDL\nMODEL        2\n' + body + b'ENDMDL\nEND\n'
                    primary.write_bytes(raw)
                    intent['input']['source_ref']['model_number'] = 1
                    intent['input']['expected_source_sha256'] = hashlib.sha256(
                        sources.select_structure_model(raw, 'pdb', 1)).hexdigest()
                elif selection == 'alternate':
                    raw = b'REMARK exact alternate document\n' + original
                selected_path.write_bytes(raw)
                sha = hashlib.sha256(raw).hexdigest()
                artifact_id = '33333333-3333-4333-8333-333333333333'
                root.model_id, root.mode = 'bindcraft2', 'design'
                root.provenance = {'bindcraft2_native_publication': {'candidates': [{
                    'design_id': design.id,
                    'structures': [{'artifact_id': artifact_id, 'sha256': sha, 'target_state': 'bound'}],
                }]}}
                session.add(JobArtifact(id=artifact_id, owner_job_id=root.id,
                                        storage_path=str(selected_path), logical_path=selected_path.name,
                                        sha256=sha, bytes=len(raw), media_type='chemical/x-pdb'))
                ref['document'] = {'artifact_id': artifact_id, 'target_state': 'bound'}
                if selection == 'model':
                    ref['document']['model'] = 2
                await session.commit()
            if selection == 'stale_digest':
                intent['input']['expected_source_sha256'] = 'b' * 64
            selected_source = sources.StartingStructureSourceRef(kind='candidate', id=root.id, candidate_ref=ref)
            resolved = await sources.resolve_source(selected_source, session)
            try:
                selected_sha = sources.read_resolved_structure(resolved).sha256
            finally:
                resolved.close()
            request = SimpleNamespace(launch_context_id=None, execution_policy=ExecutionPolicy())
            child, binding = await selected_round.md_request(
                session, root, {'owner_job_id': root.id, 'ref': ref}, {'intent': intent}, None, request, 'exact-md')
            actual = child.params['intent']['input']
            assert actual['expected_source_sha256'] == selected_sha
            assert binding['md_preview']['source']['sha256'] == selected_sha
            assert binding['md_launch_request']['preview_digest'] == binding['md_preview']['preview_digest']
            assert {key: child.params['intent'][key] for key in original_settings} == original_settings
            if selection == 'primary':
                # Preserve the already prepared Project wire for unchanged authority.
                assert actual['source_ref'] == intent['input']['source_ref']
            else:
                assert actual['source_ref'] == selected_source.model_dump(mode='json')
                assert binding['md_preview']['source']['source_ref'] == actual['source_ref']
            if selection == 'model':
                assert actual['source_ref']['candidate_ref']['document']['model'] == 2
                assert child.params['md_source_provenance']['model_number'] == 2
                assert selected_sha != intent['input']['expected_source_sha256']
            assert not list(await session.scalars(select(Job).where(Job.model_id == 'molecular_dynamics')))
    asyncio.run(exercise())


@pytest.mark.parametrize('project_context_preview_store', ['guided'], indirect=True)
@pytest.mark.parametrize('initial', [True, False])
@pytest.mark.parametrize('matching', [True, False])
def test_existing_md_approval_route_retains_exact_initial_or_selected_digest(
    project_context_preview_store, monkeypatch, initial, matching,
):
    """Real HTTP receiver and cold SQLite readback; reconciliation is inert, never remote execution."""
    import main
    from experiment_database import get_experiment_session
    store = project_context_preview_store
    root_id = '11111111-1111-4111-8111-111111111111'
    round_id = 'initial:' + root_id if initial else 'selected-md-review'
    step_id = 'md-review-step'
    expected_digest = 'a' * 64
    retained = {'round_id': round_id, 'state': 'review_required', 'errors': {}, 'steps': {step_id: {
        'state': 'review_required', 'job_id': None, 'request': {},
        'metadata': {'route': 'md_native', 'md_launch_request': {'preview_digest': expected_digest}},
        'review': {'code': 'md_native_preview_approval_required', 'preview_digest': expected_digest},
    }}}
    progress = retained if initial else {'state': 'running', 'steps': {}, 'errors': {}, 'selected_rounds': [retained]}

    async def seed():
        async with store['core_sessions']() as session:
            root = await session.get(Job, root_id)
            rounds.save(root, progress)
            await session.commit()
    asyncio.run(seed())
    calls = []

    async def reconcile_initial(session, experiments, received_root):
        calls.append(('initial', received_root))
        return await rounds.read_round(session, received_root)

    async def reconcile_selected(session, experiments, received_root, received_round):
        calls.append(('selected', received_root, received_round))
        root = await session.get(Job, received_root)
        return next(row for row in rounds.progress_of(root)['selected_rounds'] if row['round_id'] == received_round)

    async def experiment_session():
        async with store['experiment_sessions']() as session:
            yield session

    monkeypatch.setattr(rounds, 'reconcile_round', reconcile_initial)
    monkeypatch.setattr(rounds, 'reconcile_selected', reconcile_selected)
    old_override = main.app.dependency_overrides.get(get_experiment_session)
    main.app.dependency_overrides[get_experiment_session] = experiment_session
    try:
        url = f'/api/binder-continuation/{root_id}/round/{round_id}/steps/{step_id}/md-approval'
        response = store['client'].post(url, json={'preview_digest': expected_digest if matching else 'b' * 64})
        assert response.status_code == (200 if matching else 409), response.text
        if matching:
            # Replay records the same approval and cannot create an external Job.
            again = store['client'].post(url, json={'preview_digest': expected_digest})
            assert again.status_code == 200, again.text
            assert calls == ([('initial', root_id)] * 2 if initial else [('selected', root_id, round_id)] * 2)
        else:
            assert not calls
    finally:
        if old_override is None:
            main.app.dependency_overrides.pop(get_experiment_session, None)
        else:
            main.app.dependency_overrides[get_experiment_session] = old_override

    async def cold_read():
        async with store['core_sessions']() as session:
            root = await session.get(Job, root_id)
            readback = rounds.progress_of(root)
            saved = readback if initial else readback['selected_rounds'][0]
            step = saved['steps'][step_id]
            if matching:
                assert step['metadata']['approved_md_preview_digest'] == expected_digest
                assert step['state'] == 'prepared' and 'review' not in step
            else:
                assert 'approved_md_preview_digest' not in step['metadata']
                assert step['state'] == 'review_required' and step['review']['preview_digest'] == expected_digest
            assert not list(await session.scalars(select(Job).where(Job.model_id == 'molecular_dynamics')))
    asyncio.run(cold_read())
