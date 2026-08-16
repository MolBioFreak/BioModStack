from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request
from fastapi.routing import APIRoute
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from experiment_database import ExperimentBase, create_experiment_engine, create_experiment_session_factory
from experiment_migrations import run_all as run_experiment_migrations
from migrations.sqlite_sha256 import register_sqlite_sha256
from experiment_models import (
    ExperimentAuditEvent,
    ExperimentDispatchOutbox,
    ExperimentLineageEdge,
    ExperimentResource,
    ExperimentRevision,
    ExperimentValidation,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
)
from experiment_services import (
    DispatchFailure,
    ExistingJobMaterializer,
    IdempotencyConflict,
    RevisionConflict,
    ValidationFailure,
    add_audit_event,
    canonical_json,
    create_dataset,
    create_domain_experiment,
    create_experiment_workspace,
    create_global_experiment,
    create_workflow,
    create_run_group,
    clone_workflow,
    prepare_workflow,
    save_dataset_revision,
    save_workflow_draft,
    save_workflow_revision,
)
from routers import experiment_workspaces as workspace_router
from routers.experiment_workspaces import router


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest_asyncio.fixture
async def experiment_store(tmp_path: Path):
    db_path = tmp_path / "experiments.db"
    run_experiment_migrations(db_path)
    engine = create_experiment_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = create_experiment_session_factory(engine)
    try:
        yield db_path, engine, factory
    finally:
        await engine.dispose()


def _workflow_payload() -> dict:
    return {
        "schema": "bms.workflow.generic.v1",
        "workflow_family": "generic_test",
        "contract_version": "1",
        "adapter_id": "generic.test.adapter.v1",
        "nodes": [{"id": "main", "kind": "scheduler_job", "required": True}],
        "edges": [],
        "parameters": {"seed": 101},
        "scheduler": {
            "name": "generic-test",
            "model_id": "generic_test",
            "mode": "predict",
            "params": {"seed": 101},
        },
    }


async def _domain_parent(session: AsyncSession, workspace_id: str, label: str):
    global_experiment = await create_global_experiment(
        session,
        workspace_id,
        {
            "schema": "bms.global-experiment.v1",
            "name": f"{label} global",
            "objective": "Test global hierarchy",
            "scientific_question": "Does the hierarchy remain authoritative?",
            "description": "Hierarchy fixture",
            "status": "draft",
            "priority": "normal",
            "tags": [],
            "shared_source_receipt_ids": [],
            "shared_dataset_ids": [],
            "success_criteria": ["verified"],
            "created_by": "test",
            "change_summary": "created",
        },
    )
    return await create_domain_experiment(
        session,
        workspace_id,
        global_experiment.id,
        {
            "schema": "bms.domain-experiment.v1",
            "domain_kind": "protein_in_silico",
            "domain_contract_version": "1",
            "name": f"{label} domain",
            "objective": "Test domain hierarchy",
            "status": "draft",
            "tags": [],
            "source_receipt_ids": [],
            "dataset_ids": [],
            "created_by": "test",
            "change_summary": "created",
            "domain_payload": {
                "schema": "bms.protein-in-silico-experiment.v1",
                "experiment_mode": "design",
                "targets": [{
                    "target_id": "target-1",
                    "label": "Target 1",
                    "entity_receipt_ids": [],
                    "role": "target",
                }],
                "scientific_objective": "Test domain hierarchy",
                "design_constraints": [],
                "planned_capabilities": ["rfd3_local_redesign"],
                "comparison_groups": [],
                "validation_strategy": ["boltz2"],
            },
        },
    )


def _mutation_request(
    *,
    principal: str | None = None,
    roles: list[str] | None = None,
    proxy_secret: str | None = None,
) -> Request:
    headers = []
    if proxy_secret is not None:
        headers.append((b"x-bms-cm-proxy-secret", proxy_secret.encode("ascii")))
    request = Request({
        "type": "http", "method": "POST", "scheme": "http", "path": "/api/experiment-workspaces",
        "query_string": b"", "headers": headers, "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8000),
    })
    if principal is not None:
        request.state.authenticated_principal = {
            "subject": principal,
            "roles": roles or ["scientist"],
        }
    return request


def test_global_cm_mutation_principal_requires_authentication_and_preserves_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", "trusted-secret")
    with pytest.raises(HTTPException) as missing:
        workspace_router._mutation_principal(_mutation_request())
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as wrong:
        workspace_router._mutation_principal(_mutation_request(proxy_secret="wrong"))
    assert wrong.value.status_code == 401
    assert workspace_router._mutation_principal(
        _mutation_request(proxy_secret="trusted-secret")
    ) == "local-application-operator"
    assert workspace_router._mutation_principal(_mutation_request(principal="alice")) == "alice"


def test_global_cm_operator_principal_rejects_scientists_and_accepts_operator_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", "trusted-secret")
    with pytest.raises(HTTPException) as missing:
        workspace_router._operator_principal(_mutation_request())
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as scientist:
        workspace_router._operator_principal(_mutation_request(principal="alice"))
    assert scientist.value.status_code == 403
    assert workspace_router._operator_principal(
        _mutation_request(principal="olivia", roles=["operator"])
    ) == "olivia"
    assert workspace_router._operator_principal(
        _mutation_request(principal="ada", roles=["admin"])
    ) == "ada"
    assert workspace_router._operator_principal(
        _mutation_request(proxy_secret="trusted-secret")
    ) == "local-application-operator"


@pytest.mark.asyncio
async def test_global_cm_mutation_owner_fails_closed_for_ownerless_and_foreign_resources(
    experiment_store,
) -> None:
    _db_path, _engine, factory = experiment_store
    async with factory() as session:
        owned_workspace = await create_experiment_workspace(session, "owned")
        ownerless_workspace = await create_experiment_workspace(session, "ownerless")
        foreign_workspace = await create_experiment_workspace(session, "foreign")
        owned_domain = await _domain_parent(session, owned_workspace.id, "owned")
        ownerless_domain = await _domain_parent(session, ownerless_workspace.id, "ownerless")
        foreign_domain = await _domain_parent(session, foreign_workspace.id, "foreign")
        owned_workflow = await create_workflow(
            session, owned_workspace.id, "owned workflow", "generic_test",
            experiment_id=owned_domain.id,
        )
        ownerless_workflow = await create_workflow(
            session, ownerless_workspace.id, "ownerless workflow", "generic_test",
            experiment_id=ownerless_domain.id,
        )
        foreign_workflow = await create_workflow(
            session, foreign_workspace.id, "foreign workflow", "generic_test",
            experiment_id=foreign_domain.id,
        )
        for workspace, principal_id in (
            (owned_workspace, "alice"),
            (foreign_workspace, "bob"),
        ):
            add_audit_event(
                session,
                workspace_id=workspace.id,
                resource_id=workspace.id,
                event_type="workspace_owner_bound",
                generation=0,
                payload={"principal_id": principal_id},
            )
        await session.commit()

        request = _mutation_request(principal="alice")
        for resource_id in (ownerless_workspace.id, ownerless_workflow.id, foreign_workflow.id):
            with pytest.raises(HTTPException) as denied:
                await workspace_router._require_mutation_owner(
                    request, session, resource_id=resource_id,
                )
            assert denied.value.status_code == 404
        assert await workspace_router._require_mutation_owner(
            request, session, resource_id=owned_workflow.id,
        ) == "alice"
        assert await workspace_router._require_mutation_owner(
            request, session, resource_id=owned_workspace.id,
        ) == "alice"


_MUTATING_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
_GLOBAL_OPERATOR_PATHS = {
    "/api/experiment-workspaces/ops/backup",
    "/api/experiment-workspaces/dispatch/once",
    "/api/experiment-workspaces/{workspace_id}/run-groups/{run_group_id}/reconcile",
}


def _mutating_routes() -> list[APIRoute]:
    return [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.methods & _MUTATING_METHODS
    ]


def test_every_global_cm_mutation_route_has_the_appropriate_complete_auth_gate() -> None:
    routes = _mutating_routes()
    assert routes
    for route in routes:
        source = inspect.getsource(route.endpoint)
        assert "request: Request" in source, f"{route.methods} {route.path} lacks request authority"
        if route.path == "/api/experiment-workspaces":
            assert "_mutation_principal(" in source
        elif route.path in _GLOBAL_OPERATOR_PATHS:
            assert "_operator_principal(" in source
            assert "_require_mutation_owner(" not in source
        else:
            assert "_require_mutation_owner(" in source, (
                f"{route.methods} {route.path} lacks persisted workspace-owner gate"
            )


_MUTATION_HTTP_CASES = {
    "create_workspace": ("/api/experiment-workspaces", {"name": "workspace"}, None),
    "archive_workspace_aggregate": (
        "/api/experiment-workspaces/workspace/aggregates/aggregate/archive", {}, None,
    ),
    "create_workspace_experiment": (
        "/api/experiment-workspaces/workspace/experiments", {"name": "experiment"}, None,
    ),
    "create_workspace_workflow": (
        "/api/experiment-workspaces/workspace/workflows",
        {
            "name": "workflow",
            "workflow_family": "generic_test",
            "domain_experiment_id": "domain",
        },
        None,
    ),
    "save_workflow_draft_route": (
        "/api/experiment-workspaces/workspace/workflows/workflow/draft",
        {"payload": {}, "expected_generation": 0},
        None,
    ),
    "save_workflow_revision_route": (
        "/api/experiment-workspaces/workspace/workflows/workflow/revisions",
        {"expected_head_generation": 0},
        None,
    ),
    "clone_workspace_workflow": (
        "/api/experiment-workspaces/workspace/workflows/workflow/clone", {}, None,
    ),
    "create_workspace_dataset": (
        "/api/experiment-workspaces/workspace/datasets",
        {
            "name": "dataset",
            "dataset_kind": "generic_inputs",
            "domain_experiment_id": "domain",
        },
        None,
    ),
    "save_dataset_revision_route": (
        "/api/experiment-workspaces/workspace/datasets/dataset/revisions",
        {"payload": {}, "expected_head_generation": 0},
        None,
    ),
    "prepare_workspace_workflow": (
        "/api/experiment-workspaces/workspace/preparations",
        {},
        {"workflow_revision_id": "revision"},
    ),
    "create_workspace_run_group": (
        "/api/experiment-workspaces/workspace/run-groups",
        {"preparation_ids": ["preparation"], "idempotency_key": "launch"},
        None,
    ),
    "reconcile_workspace_run_group": (
        "/api/experiment-workspaces/workspace/run-groups/group/reconcile", None, None,
    ),
    "retry_workspace_run_group": (
        "/api/experiment-workspaces/workspace/run-groups/group/retry",
        {"idempotency_key": "retry"},
        None,
    ),
    "resubmit_workspace_run_group": (
        "/api/experiment-workspaces/workspace/run-groups/group/resubmit",
        {"idempotency_key": "resubmit"},
        None,
    ),
    "create_experiment_backup": (
        "/api/experiment-workspaces/ops/backup", None, None,
    ),
    "export_workspace": (
        "/api/experiment-workspaces/workspace/exports", None, None,
    ),
    "register_stats_toolkit_handoff": (
        "/api/experiment-workspaces/workspace/analytics/stats-handoffs",
        {
            "stats_run_id": "stats-run",
            "toolkit_version": "1",
            "source_resource_ids": ["source"],
            "source_content_digests": ["0" * 64],
            "result_content_digest": "1" * 64,
            "result_generation_or_revision": "1",
        },
        None,
    ),
    "register_workspace_external_receipt": (
        "/api/experiment-workspaces/workspace/external-receipts",
        {
            "store_id": "store",
            "entity_kind": "entity",
            "entity_id": "external-id",
            "generation_or_revision": "1",
            "content_digest": "2" * 64,
        },
        None,
    ),
    "dispatch_one_experiment_outbox": (
        "/api/experiment-workspaces/dispatch/once", None, None,
    ),
}


@pytest.mark.asyncio
async def test_every_global_cm_mutation_http_route_rejects_missing_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = _mutating_routes()
    assert {route.endpoint.__name__ for route in routes} == set(_MUTATION_HTTP_CASES)
    monkeypatch.setattr(workspace_router, "create_online_backup", lambda: {"backup_id": "blocked"})
    app = FastAPI()
    app.include_router(router)

    class StubSession(SimpleNamespace):
        async def rollback(self) -> None:
            pass

    async def override_session():
        yield StubSession()

    app.dependency_overrides[workspace_router.get_experiment_session] = override_session
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for route in routes:
            path, body, params = _MUTATION_HTTP_CASES[route.endpoint.__name__]
            method = next(iter(route.methods & _MUTATING_METHODS))
            response = await client.request(method, path, json=body, params=params)
            assert response.status_code == 401, (
                f"{method} {path} returned {response.status_code} without authentication"
            )


@pytest.mark.asyncio
async def test_global_backup_and_dispatch_http_routes_require_operator_or_admin_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_backup() -> dict[str, Any]:
        calls.append("backup")
        return {"backup_id": "operator-backup"}

    monkeypatch.setattr(workspace_router, "create_online_backup", fake_backup)
    app = FastAPI()

    @app.middleware("http")
    async def inject_test_principal(request: Request, call_next):
        role = request.headers.get("X-Test-Role")
        if role:
            request.state.authenticated_principal = {
                "subject": "role-test-user",
                "roles": [role],
            }
        return await call_next(request)

    app.include_router(router)

    class StubSession(SimpleNamespace):
        async def rollback(self) -> None:
            pass

    async def override_session():
        yield StubSession()

    app.dependency_overrides[workspace_router.get_experiment_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in _GLOBAL_OPERATOR_PATHS:
            scientist = await client.post(path, headers={"X-Test-Role": "scientist"})
            assert scientist.status_code == 403
        assert calls == []
        backup = await client.post(
            "/api/experiment-workspaces/ops/backup",
            headers={"X-Test-Role": "operator"},
        )
        dispatch = await client.post(
            "/api/experiment-workspaces/dispatch/once",
            headers={"X-Test-Role": "admin"},
        )
        assert backup.status_code == 201
        assert dispatch.status_code == 409
        assert calls == ["backup"]


@pytest.mark.asyncio
async def test_experiment_migration_enforces_pragmas_digest_and_revision_immutability(
    experiment_store,
):
    db_path, _engine, _factory = experiment_store
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        register_sqlite_sha256(connection)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        workspace_id = "workspace-1"
        connection.execute(
            "INSERT INTO resources (id, kind, workspace_id, lifecycle_owner_id, created_at) "
            "VALUES (?, 'workspace', NULL, NULL, '2026-01-01T00:00:00Z')",
            (workspace_id,),
        )
        revision_id = "revision-1"
        payload = '{"a":1}'
        connection.execute(
            "INSERT INTO resources (id, kind, workspace_id, lifecycle_owner_id, created_at) "
            "VALUES (?, 'revision', ?, ?, '2026-01-01T00:00:00Z')",
            (revision_id, workspace_id, workspace_id),
        )
        connection.execute(
            "INSERT INTO revisions (resource_id, subject_id, revision_number, schema_name, schema_version, "
            "canonical_payload, payload_sha256, dependency_graph_sha256, created_at) "
            "VALUES (?, ?, 1, 'test', '1', ?, ?, ?, '2026-01-01T00:00:00Z')",
            (revision_id, workspace_id, payload, _sha(payload), _sha("[]")),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="revision payload digest"):
            connection.execute(
                "INSERT INTO revisions (resource_id, subject_id, revision_number, schema_name, schema_version, "
                "canonical_payload, payload_sha256, dependency_graph_sha256, created_at) "
                "VALUES ('revision-2', ?, 2, 'test', '1', ?, ?, ?, '2026-01-01T00:00:00Z')",
                (workspace_id, payload, "0" * 64, _sha("[]")),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable revision"):
            connection.execute(
                "UPDATE revisions SET canonical_payload = ? WHERE resource_id = ?",
                ('{"a":2}', revision_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable revision"):
            connection.execute("DELETE FROM revisions WHERE resource_id = ?", (revision_id,))
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_workflow_and_dataset_revision_save_load_prepare_is_server_backed(experiment_store):
    _db_path, _engine, factory = experiment_store
    async with factory() as session:
        workspace = await create_experiment_workspace(session, "CM test workspace")
        domain = await _domain_parent(session, workspace.id, "CM test")
        workflow = await create_workflow(
            session, workspace.id, "Generic workflow", "generic_test", experiment_id=domain.id,
        )
        dataset = await create_dataset(
            session, workspace.id, "Input dataset", "generic_inputs", experiment_id=domain.id,
        )
        await save_workflow_draft(session, workflow.id, _workflow_payload(), expected_generation=0)
        workflow_revision = await save_workflow_revision(
            session, workflow.id, expected_head_generation=0
        )
        dataset_revision = await save_dataset_revision(
            session,
            dataset.id,
            {"members": [{"role": "sequence", "value": "ACDE"}]},
            expected_head_generation=0,
        )
        preparation = await prepare_workflow(
            session,
            workflow_revision.id,
            {"input_dataset_revision_ids": [dataset_revision.id]},
        )
        await session.commit()

        assert workflow_revision.payload_sha256 == _sha(workflow_revision.canonical_payload)
        assert dataset_revision.payload_sha256 == _sha(dataset_revision.canonical_payload)
        assert preparation.validation_status == "valid"
        assert preparation.scheduler_payload_json

        persisted_revision = await session.get(ExperimentRevision, workflow_revision.id)
        persisted_preparation = await session.get(ExperimentWorkflowPreparation, preparation.id)
        assert persisted_revision is not None
        assert persisted_preparation is not None
        assert persisted_preparation.validation_resource_id
        assert (await session.execute(select(ExperimentValidation))).scalars().all()
        assert (await session.execute(select(ExperimentLineageEdge))).scalars().all()
        assert (await session.execute(select(ExperimentAuditEvent))).scalars().all()
        assert json.loads(persisted_revision.canonical_payload)["workflow_family"] == "generic_test"

        clone = await clone_workflow(session, workflow.id, source_revision_id=workflow_revision.id)
        await session.commit()
        assert clone.id != workflow.id
        fork_edges = (
            await session.execute(
                select(ExperimentLineageEdge).where(
                    ExperimentLineageEdge.source_resource_id == clone.id,
                    ExperimentLineageEdge.edge_mode == "forked_from",
                )
            )
        ).scalars().all()
        assert len(fork_edges) == 1


@pytest.mark.asyncio
async def test_revision_generation_conflict_and_run_group_idempotency(experiment_store):
    _db_path, _engine, factory = experiment_store
    async with factory() as session:
        workspace = await create_experiment_workspace(session, "run-group workspace")
        domain = await _domain_parent(session, workspace.id, "run-group")
        workflow = await create_workflow(
            session, workspace.id, "workflow", "generic_test", experiment_id=domain.id,
        )
        dataset = await create_dataset(
            session, workspace.id, "dataset", "generic_inputs", experiment_id=domain.id,
        )
        await save_workflow_draft(session, workflow.id, _workflow_payload(), expected_generation=0)
        revision = await save_workflow_revision(session, workflow.id, expected_head_generation=0)
        dataset_revision = await save_dataset_revision(
            session, dataset.id, {"members": []}, expected_head_generation=0
        )
        preparation = await prepare_workflow(
            session, revision.id, {"input_dataset_revision_ids": [dataset_revision.id]}
        )
        await session.commit()

        with pytest.raises(RevisionConflict):
            await save_workflow_revision(session, workflow.id, expected_head_generation=0)

        group = await create_run_group(
            session,
            workspace.id,
            [preparation.id],
            idempotency_key="launch-1",
        )
        await session.commit()
        replay = await create_run_group(
            session,
            workspace.id,
            [preparation.id],
            idempotency_key="launch-1",
        )
        assert replay.id == group.id
        with pytest.raises(IdempotencyConflict):
            await create_run_group(
                session,
                workspace.id,
                [],
                idempotency_key="launch-1",
            )

        outbox_rows = (
            await session.execute(select(ExperimentDispatchOutbox))
        ).scalars().all()
        runs = (await session.execute(select(ExperimentWorkflowRun))).scalars().all()
        assert len(outbox_rows) == 1
        assert len(runs) == 1
        assert outbox_rows[0].status == "pending"
        with pytest.raises(IntegrityError, match="workflow preparation is immutable"):
            preparation.validation_receipt_json = canonical_json({"schema": "bms.experiment.validation.v1", "status": "valid", "tampered": True})
            await session.flush()
        await session.rollback()


def test_migration_v9_makes_preparations_and_dataset_members_immutable(tmp_path) -> None:
    from experiment_migrations import run_all

    db_path = tmp_path / "authority.db"
    run_all(db_path)
    connection = sqlite3.connect(db_path)
    try:
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert {
            "trg_experiment_preparation_immutable_update",
            "trg_experiment_preparation_immutable_delete",
            "trg_experiment_dataset_member_digest_insert",
            "trg_experiment_dataset_member_immutable_update",
            "trg_experiment_dataset_member_immutable_delete",
        } <= triggers
    finally:
        connection.close()


class _FakeMaterializer:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def materialize(self, attempt_id: str, payload: dict) -> dict:
        self.calls.append((attempt_id, payload))
        return {
            "store_id": "fake-core",
            "entity_kind": "job",
            "entity_id": attempt_id,
            "generation": 1,
            "content_digest": _sha(json.dumps(payload, sort_keys=True)),
        }


@pytest.mark.asyncio
async def test_outbox_dispatch_is_idempotent_and_acknowledges_external_receipt(experiment_store):
    _db_path, _engine, factory = experiment_store
    from experiment_services import dispatch_pending_outbox

    async with factory() as session:
        workspace = await create_experiment_workspace(session, "dispatch workspace")
        domain = await _domain_parent(session, workspace.id, "dispatch")
        workflow = await create_workflow(
            session, workspace.id, "workflow", "generic_test", experiment_id=domain.id,
        )
        dataset = await create_dataset(
            session, workspace.id, "dataset", "generic_inputs", experiment_id=domain.id,
        )
        await save_workflow_draft(session, workflow.id, _workflow_payload(), expected_generation=0)
        revision = await save_workflow_revision(session, workflow.id, expected_head_generation=0)
        dataset_revision = await save_dataset_revision(
            session, dataset.id, {"members": []}, expected_head_generation=0
        )
        preparation = await prepare_workflow(
            session, revision.id, {"input_dataset_revision_ids": [dataset_revision.id]}
        )
        await create_run_group(session, workspace.id, [preparation.id], idempotency_key="dispatch-1")
        await session.commit()

        materializer = _FakeMaterializer()
        first = await dispatch_pending_outbox(session, materializer)
        second = await dispatch_pending_outbox(session, materializer)

        assert first == 1
        assert second == 0
        assert len(materializer.calls) == 1
        outbox = (await session.execute(select(ExperimentDispatchOutbox))).scalar_one()
        assert outbox.status == "acknowledged"
        run = (await session.execute(select(ExperimentWorkflowRun))).scalar_one()
        assert run.state == "dispatched"

        await create_run_group(session, workspace.id, [preparation.id], idempotency_key="dispatch-2")
        await session.commit()
        stale = (
            await session.execute(
                select(ExperimentDispatchOutbox).where(ExperimentDispatchOutbox.status == "pending")
            )
        ).scalar_one()
        stale.status = "dispatching"
        stale.updated_at = "2000-01-01T00:00:00+00:00"
        await session.commit()
        recovered_materializer = _FakeMaterializer()
        assert await dispatch_pending_outbox(session, recovered_materializer) == 1
        assert len(recovered_materializer.calls) == 1


@pytest.mark.asyncio
async def test_workspace_router_exposes_server_backed_creation(
    experiment_store,
    monkeypatch: pytest.MonkeyPatch,
):
    _db_path, _engine, factory = experiment_store
    from experiment_database import get_experiment_session

    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", "workspace-test-secret")
    app = FastAPI()
    app.include_router(router)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_experiment_session] = override_session
    transport = httpx.ASGITransport(app=app)
    headers = {"X-BMS-CM-Proxy-Secret": "workspace-test-secret"}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers,
    ) as client:
        response = await client.post(
            "/api/experiment-workspaces",
            json={"name": "HTTP workspace", "description": "saved on server"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "HTTP workspace"
        rejected = await client.post(
            "/api/experiment-workspaces",
            json={"name": "invalid", "unexpected": True},
        )
        assert rejected.status_code == 422
        loaded = await client.get(f"/api/experiment-workspaces/{body['id']}")
        assert loaded.status_code == 200
        assert loaded.json()["id"] == body["id"]

    async with factory() as session:
        workspace = await session.get(ExperimentResource, body["id"])
        assert workspace is not None
        assert workspace.lifecycle_owner_id is None
        owner_events = (
            await session.execute(
                select(ExperimentAuditEvent).where(
                    ExperimentAuditEvent.workspace_id == body["id"],
                    ExperimentAuditEvent.event_type == "workspace_owner_bound",
                )
            )
        ).scalars().all()
        assert len(owner_events) == 1
        assert json.loads(owner_events[0].payload_json) == {
            "principal_id": "local-application-operator"
        }


@pytest.mark.asyncio
async def test_existing_job_materializer_preallocates_and_reuses_core_job_identity(tmp_path: Path):
    from database import Job

    core_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    try:
        async with core_engine.begin() as connection:
            await connection.run_sync(Job.__table__.create)
        core_factory = async_sessionmaker(core_engine, expire_on_commit=False, class_=AsyncSession)
        payload = {
            "run_group_id": "run-group-id",
            "scheduler_job_id": "12345678-1234-1234-1234-123456789012",
            "scheduler": {
                "name": "materialized-job",
                "model_id": "protenix",
                "mode": "predict",
                "params": {
                    "seed": 101,
                    "sequence": "ACDEFGHIKLMNPQRSTVWY",
                    "msa_provider": "colabfold_api",
                    "job_name": "materialized-job",
                    "pred_method": "protenix",
                    "structure_validator": "protenix",
                    "workflow_adapter": "bms.core-job.protenix.adapter.v1",
                },
            },
        }
        async with core_factory() as session:
            materializer = ExistingJobMaterializer(session)
            first = await materializer.materialize("12345678-1234-5234-9234-123456789012", payload)
            persisted = await session.get(Job, "12345678-1234-5234-9234-123456789012")
            assert persisted is not None
            assert dict(persisted.params or {}) == payload["scheduler"]["params"], (
                persisted.params,
                payload["scheduler"]["params"],
            )
            second = await materializer.materialize("12345678-1234-5234-9234-123456789012", payload)
            assert first == second
            assert first["external_job_id"] == "12345678-1234-5234-9234-123456789012"
            assert (await session.execute(select(Job))).scalars().all().__len__() == 1
            conflict = {
                **payload,
                "scheduler": {
                    **payload["scheduler"],
                    "params": {
                        **payload["scheduler"]["params"],
                        "seed": 202,
                    },
                },
            }
            with pytest.raises(DispatchFailure, match="conflicts"):
                await materializer.materialize("12345678-1234-5234-9234-123456789012", conflict)
    finally:
        await core_engine.dispose()
