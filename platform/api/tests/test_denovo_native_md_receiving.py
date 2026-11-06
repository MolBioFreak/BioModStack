"""MD shared receiving with actual Project/core stores and native compiler."""
import asyncio
import json
import pytest
from database import Job
from tests.test_remote_rectify_admission import remote_project_store
from tests.test_md_typed_launch import project_context_preview_store
from model_registry import selected_execution_metadata


@pytest.fixture
def current_project_store(monkeypatch, tmp_path, request):
    # Reuse the real two-store fixture; its caller predates the added pytest request argument.
    from types import SimpleNamespace
    from tests import test_md_typed_launch as typed
    original = typed.project_context_preview_store.__wrapped__
    monkeypatch.setattr(typed, 'project_context_preview_store', SimpleNamespace(
        __wrapped__=lambda patcher, root: original(patcher, root, request)))
    yield from remote_project_store.__wrapped__(monkeypatch, tmp_path)



@pytest.mark.parametrize('navigation', [None, '/designs/source?view=selected'])
@pytest.mark.parametrize('exact_candidate', [False, True, 'tamper'])
def test_optional_navigation_is_not_scientific_authority(current_project_store, monkeypatch, navigation, exact_candidate):
    from tests import test_md_typed_launch as typed
    from services.md import launch_contract
    from routers import jobs
    store = current_project_store
    monkeypatch.setenv('BMS_FEATURE_MOLECULAR_DYNAMICS', '1')
    monkeypatch.setattr(jobs, '_raise_if_workflow_launches_disabled', lambda *_: None)
    monkeypatch.setattr(launch_contract, 'get_chemistry_catalog', lambda: typed._Catalog())
    source_ref = {'kind': 'design', 'id': store['expected_design_id']}
    if exact_candidate:
        from services.md.starting_structures import StartingStructureSourceRef
        from database import Design
        async def bind_exact_source():
            import hashlib
            from experiment_models import (ExperimentRevision, ExperimentWorkflowPreparation,
                ExperimentValidation, ExperimentLaunchContext, ExperimentRunGroup)
            canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
            sha = lambda value: hashlib.sha256(value.encode()).hexdigest()
            async with store['core_sessions']() as core:
                design = await core.get(Design, store['expected_design_id'])
                exact = StartingStructureSourceRef.model_validate({'kind': 'candidate', 'id': design.job_id,
                    'candidate_ref': {'kind': 'design', 'job_id': design.job_id, 'design_id': design.id},
                    'model_number': 1}).model_dump(mode='json')
            async with store['experiment_sessions']() as session:
                ids = store['ids']
                revision = await session.get(ExperimentRevision, ids['workflow_revision'])
                preparation = await session.get(ExperimentWorkflowPreparation, ids['preparation'])
                validation = await session.get(ExperimentValidation, ids['validation'])
                context = await session.get(ExperimentLaunchContext, ids['context'])
                group = await session.get(ExperimentRunGroup, ids['run_group'])
                workflow = json.loads(revision.canonical_payload)
                workflow['scheduler']['params']['source_ref'] = exact
                revision.canonical_payload = canonical(workflow)
                revision.payload_sha256 = sha(revision.canonical_payload)
                normalized = json.loads(preparation.normalized_request_json)
                normalized['workflow'] = workflow
                preparation.normalized_request_json = canonical(normalized)
                preparation.normalized_request_sha256 = sha(preparation.normalized_request_json)
                preparation.scheduler_payload_json = canonical(workflow['scheduler'])
                receipt = json.loads(validation.receipt_json)
                receipt['normalized_request_sha256'] = preparation.normalized_request_sha256
                preparation.validation_receipt_json = validation.receipt_json = canonical(receipt)
                validation.receipt_sha256 = sha(validation.receipt_json)
                context.normalized_request_sha256 = group.request_sha256 = preparation.normalized_request_sha256
                context.validation_receipt_sha256 = validation.receipt_sha256
                await session.commit()
            return exact
        source_ref = asyncio.run(bind_exact_source())
    intent = {**typed._intent_payload(), 'name': 'Typed-MD-launch',
        'launch_context_id': store['ids']['context'],
        'source_ref': source_ref, 'return_to': navigation}
    if exact_candidate == 'tamper':
        intent['source_ref']['model_number'] = 2
    headers = {'x-bms-launch-context-id': store['ids']['context']}
    preview = store['client'].post('/api/molecular-dynamics/launch-preview', headers=headers,
        json={'schema_version': 'bms.md.launch-preview-request.v1', 'intent': intent})
    if exact_candidate == 'tamper':
        assert preview.status_code == 409, preview.text
        assert preview.json()['detail']['code'] == 'launch_context_source_mismatch'
        return
    assert preview.status_code == 200, preview.text
    result = store['client'].post('/api/molecular-dynamics/launch', headers=headers,
        json={'schema_version': 'bms.md.launch-request.v1', 'intent': intent,
              'preview_digest': preview.json()['preview_digest']})
    assert result.status_code == 201, result.text
    async def readback():
        async with store['core_sessions']() as session:
            job = await session.get(Job, result.json()['id'])
            assert job.params['intent']['return_to'] == navigation
            assert job.execution_target_id is None and job.parent_job_id is None
    asyncio.run(readback())


@pytest.mark.parametrize('profile,has_structure,amber', [
    ('amber_ff19sb_opc_protein_v1', True, True),
    ('gmx_amber99sb_ildn_tip3p_smoke_v1', True, False),
    ('amber_ff19sb_opc_protein_v1', False, False)])
def test_recipe_owned_preparation_closure(profile, has_structure, amber):
    config = {'schema': 'bms.md.job.v2', 'engine': 'gromacs',
              'input': {'structure': 'source.pdb'} if has_structure else {},
              'chemistry': {'profile_id': profile}}
    selected = selected_execution_metadata('molecular_dynamics', 'simulate', {'md_config': config},
        'workflows/experimental/molecular_dynamics/orchestrator.nf')
    paths = {item.relative_path for item in selected.dependencies}
    assert ('md-preparation-v1.sif' in paths) == amber
    assert ('md-preparation/env-v1-explicit.txt' in paths) == amber
    assert 'gromacs-md-2025.3.sif' in paths
    assert any(c.authority.endswith(':MD_PREPARE_CONFIG') for c in selected.static_components)
