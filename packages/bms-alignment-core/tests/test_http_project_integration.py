"""Exercise SDK import/view/annotation/export against real ASGI and scratch PM."""
import asyncio
import io
import json
from urllib.error import HTTPError

import pytest
pytest.importorskip('database')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database import Base, get_session
from model_registry import ModelRegistry
from routers import jobs
from pathlib import Path
from experiment_models import ExperimentBase
from experiment_database import get_experiment_session
from experiment_services import create_project, create_global_experiment, create_domain_experiment, add_audit_event
from routers.family_msa_results import router
from test_project_manager_adapters import _project_payload, _global_payload, _domain_payload
from bms_alignment.http import (AlignmentClient, AlignmentImport, SharedState, View, Mark, ArtifactColumns,
                                SharedCreate, SharedSave, BrowserExport, ComparisonInput, CompareOperation, ExportOperation, ExportInput, BmsHttpError)


def test_sdk_wire_fields_match_authoritative_request_owners():
    from bms_alignment import http
    from routers import family_msa_results as routes
    from services.family_msa import shared
    for name in ('ComparisonInput', 'EnsembleSupportInput', 'BrowserExport', 'FreezeRequest', 'SharedCreate', 'SharedSave'):
        assert getattr(http, name).model_json_schema()['properties'] == getattr(routes, name).model_json_schema()['properties'], name
    for name in ('ArtifactColumns', 'SourcePositions', 'Mark', 'View', 'Comparison', 'SharedState'):
        assert getattr(http, name).model_json_schema()['properties'] == getattr(shared, name).model_json_schema()['properties'], name


def test_http_scratch_project_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_CM_TRUSTED_PROXY_SECRET', 'test-secret')
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'artifacts'))
    monkeypatch.setenv('BMS_EXPERIMENT_ARTIFACT_ROOT', str(tmp_path / 'blobs'))
    monkeypatch.setenv('BMS_CORE_RUNTIME_MODE', '0')
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: tmp_path / 'results')
    registry = ModelRegistry(Path(__file__).resolve().parents[3] / 'platform/api/config/models')
    monkeypatch.setattr(jobs, 'get_registry', lambda: registry)
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "pm.sqlite"}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(ExperimentBase.metadata.create_all)
            await conn.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            project = await create_project(session, _project_payload())
            experiment = await create_global_experiment(session, project.id, _global_payload())
            domain = await create_domain_experiment(session, project.id, experiment.id, _domain_payload('ngs_molbio'))
            add_audit_event(session, workspace_id=project.id, resource_id=project.id, event_type='workspace_owner_bound', generation=project.head_generation, payload={'principal_id': 'local-application-operator'})
            await session.commit()
            return project.id, domain.id

    project, domain = asyncio.run(setup())
    async def dependency():
        async with sessions() as session:
            yield session
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_experiment_session] = dependency
    app.dependency_overrides[get_session] = dependency
    try:
        with TestClient(app) as transport:
            class Opener:
                def open(self, request, timeout):
                    response = transport.request(request.method, request.full_url, headers=dict(request.header_items()), content=request.data)
                    if response.is_error:
                        raise HTTPError(request.full_url, response.status_code, '', response.headers, io.BytesIO(response.content))
                    return io.BytesIO(response.content)
            sdk = AlignmentClient('http://testserver', headers={'X-BMS-CM-Proxy-Secret': 'test-secret'})
            sdk._opener = Opener()
            raw = b'>duplicate\nAC-D\n>duplicate\nACED\n'
            imported = sdk.import_alignment(project, raw, AlignmentImport(format='fasta', molecule='protein', replay_key='sdk-import'))
            assert sdk.import_alignment(project, raw, AlignmentImport(format='fasta', molecule='protein', replay_key='sdk-import')).replayed
            assert sdk.receipts(project).items[0].receipt_id == imported.receipt_id
            artifacts = sdk.result(project, imported.receipt_id).artifacts
            alignment = next(a.id for a in artifacts if a.role == 'alignment')
            original = next(a.id for a in artifacts if a.role == 'source')
            assert sdk.artifact(project, imported.receipt_id, original) == raw
            document = sdk.document(project, imported.receipt_id, alignment)
            assert len(document.rows) == 2 and document.rows[0].row_id != document.rows[1].row_id
            assert document.rows[0].source_member_id is None
            state = SharedState(views=[View(id='v', name='saved', artifact_id=alignment, selected_column=0)], marks=[Mark(id='m', label='gap', coordinate=ArtifactColumns(scope='artifact_columns', artifact_id=alignment, start=2, end=3))])
            shared = sdk.create_shared_state(project, SharedCreate(name='SDK view', domain_experiment_id=domain, state=state))
            assert sdk.shared_state(project, shared.id).state == state
            changed = state.model_copy(update={'views': []})
            saved = sdk.save_shared_state(project, shared.id, SharedSave(expected_head_generation=shared.head_generation, state=changed))
            assert sdk.shared_state(project, saved.id).state.views == []
            assert sdk.shared_state(project, shared.id, revision_id=shared.revision_id).state.views[0].selected_column == 0
            with pytest.raises(BmsHttpError) as stale:
                sdk.save_shared_state(project, shared.id, SharedSave(expected_head_generation=shared.head_generation, state=state))
            assert stale.value.status == 409
            report = sdk.compare(project, ComparisonInput(left_receipt_id=imported.receipt_id, right_receipt_id=imported.receipt_id, left_artifact_id=alignment, right_artifact_id=alignment))
            assert report.report.agreement.jaccard is None
            bundle = sdk.export_project_zip(project, imported.receipt_id, BrowserExport(shared_state_ids=[shared.id]))
            reimported = sdk.import_project_zip(project, bundle, replay_key='sdk-portable', domain_experiment_id=domain)
            reopened = sdk.result(project, reimported.receipt_id)
            restored_original = next(a.id for a in reopened.artifacts if a.role == 'source')
            assert sdk.artifact(project, reimported.receipt_id, restored_original) == raw
            assert reopened.import_provenance['shared_states']
            comparison = CompareOperation(molecule='protein', domain_experiment_id=domain, inputs=ComparisonInput(left_receipt_id=imported.receipt_id, right_receipt_id=imported.receipt_id, left_artifact_id=alignment, right_artifact_id=alignment))
            accepted = sdk.submit_operation(project, comparison)
            assert accepted.status == 'queued'
            assert sdk.operation(project, accepted.job_id).request_dataset_id == accepted.request_dataset_id
            with pytest.raises(BmsHttpError) as pending:
                sdk.operation_artifact(project, accepted.job_id)
            assert pending.value.status == 409
            exported = sdk.submit_operation(project, ExportOperation(molecule='protein', domain_experiment_id=domain, inputs=ExportInput(receipt_id=imported.receipt_id, shared_state_ids=[shared.id])))
            assert sdk.operation(project, exported.job_id).operation == 'export'
    finally:
        asyncio.run(engine.dispose())
