"""Local-only regression evidence for BE-01 / BE-02; do not commit."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import subprocess
import types
from pathlib import Path

import experiment_migrations
import pytest
from sqlalchemy import event, insert, select, text

from experiment_models import (ExperimentAggregateHead, ExperimentDomainAdapterReceipt,
    ExperimentResource, ExperimentWorkflowSetupContext)
from experiment_services import ValidationFailure, create_domain_experiment
from services.global_experiments.read_models import build_project_manager_read_model
from services.global_experiments.workflow_setups import create_workflow_setup, delete_workflow_setup
from tests.test_project_manager_read_models import (
    _hierarchy, _domain_payload, _add_source_receipt, read_model_store,
)
from tests.test_project_workflow_setups import _project


@pytest.mark.asyncio
async def test_tasks_use_exact_focus_ownership_and_exclude_deleted(read_model_store):
    async with read_model_store() as session:
        project = await _project(session)
        setups = []
        for index in range(3):
            setups.append(await create_workflow_setup(
                session, actor_id="test-owner", project_id=project.id, relationship_kind="primary",
                global_experiment_id=None, experiment_name=f"Target {index}", experiment_objective="Explore",
                domain_kind="protein_in_silico", capability_id="protein.structure_prediction.esmfold2",
                idempotency_key=f"summary-create-{index:04d}",
            ))
        rows = [await session.get(ExperimentWorkflowSetupContext, item["setup_context_id"]) for item in setups]
        default = await build_project_manager_read_model(session, project_id=project.id)
        assert len(default["tasks"]) == 1
        for row in rows:
            focused = await build_project_manager_read_model(session, project_id=project.id, focus_id=row.global_experiment_id)
            assert [item["setup_context_id"] for item in focused["tasks"]] == [row.setup_context_id]
            folder = await build_project_manager_read_model(session, project_id=project.id,
                focus_id=row.global_experiment_id, selected_node_key=f"virtual_folder:{row.domain_experiment_id}:plans")
            assert [item["setup_context_id"] for item in folder["tasks"]] == [row.setup_context_id]
        first = await build_project_manager_read_model(session, project_id=project.id, focus_id=project.id, task_limit=2)
        second = await build_project_manager_read_model(session, project_id=project.id, focus_id=project.id,
            task_limit=2, task_cursor=first["pagination"]["task_next_cursor"])
        assert len(first["tasks"]) == 2 and len(second["tasks"]) == 1
        assert {item["setup_context_id"] for item in first["tasks"] + second["tasks"]} == {row.setup_context_id for row in rows}
        await delete_workflow_setup(session, project_id=project.id, setup_context_id=rows[0].setup_context_id,
            idempotency_key="summary-delete-0001")
        # Malformed sibling ownership must not reject a valid task or grant it actions.
        workflow = await session.get(ExperimentAggregateHead, rows[1].workflow_id)
        workflow.parent_id = rows[2].domain_experiment_id
        await session.flush()
        result = await build_project_manager_read_model(session, project_id=project.id, focus_id=project.id)
        assert [item["setup_context_id"] for item in result["tasks"]] == [rows[2].setup_context_id]


@pytest.mark.asyncio
async def test_large_hierarchy_pages_do_not_scope_collections_or_reject(read_model_store):
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        # Stored legacy hierarchy remains readable without a scientific upgrade.
        resources, heads = [], []
        for index in range(150):
            identity = f"summary-domain-{index:04d}"
            resources.append(dict(id=identity, kind="domain_experiment", workspace_id=project.id,
                lifecycle_owner_id=global_experiment.id))
            heads.append(dict(aggregate_id=identity, aggregate_kind="domain_experiment", workspace_id=project.id,
                parent_id=global_experiment.id, display_name=identity, lifecycle_state="active", head_generation=1))
        await session.execute(insert(ExperimentResource), resources)
        await session.execute(insert(ExperimentAggregateHead), heads)
        await _add_source_receipt(session, project_id=project.id, global_experiment_id=global_experiment.id,
            domain_id=domain.id, receipt_id="outside-tree-page", digest="a" * 64, created_at="2026-08-01T00:00:00+00:00")
        first = await build_project_manager_read_model(session, project_id=project.id, focus_id=global_experiment.id, tree_limit=100)
        second = await build_project_manager_read_model(session, project_id=project.id, focus_id=global_experiment.id,
            tree_limit=100, tree_cursor=first["tree"]["next_cursor"])
        assert first["counts"]["domain_experiments"] == 151
        ids = [node["subject_id"] for page in (first, second) for node in page["tree"]["nodes"] if node["node_type"] == "domain_experiment"]
        assert len(ids) == len(set(ids)) == 151
        assert not second["tree"]["has_more"]
        assert first["counts"]["attached_entities"] == second["counts"]["attached_entities"] == 1
        assert first["source_receipt_ids"] == second["source_receipt_ids"] == ["outside-tree-page"]
        with pytest.raises(ValidationFailure):
            await build_project_manager_read_model(session, project_id=project.id, focus_id=project.id,
                tree_parent_node_key=f"project:{project.id}", tree_cursor=first["tree"]["next_cursor"])


@pytest.mark.asyncio
async def test_large_attachment_set_is_truthfully_incomplete_not_rejected(read_model_store):
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        for index in range(1001):
            await _add_source_receipt(session, project_id=project.id, global_experiment_id=global_experiment.id,
                domain_id=domain.id, receipt_id=f"receipt-large-{index:04d}", digest="a" * 64,
                created_at="2026-08-01T00:00:00+00:00")
        result = await build_project_manager_read_model(session, project_id=project.id, focus_id=global_experiment.id,
            map_limit=1, lineage_limit=1, result_limit=1)
        assert result["counts"]["attached_entities"] == 1001
        assert len(result["source_receipt_ids"]) == 1
        assert result["source_projection"] == {"scope": "displayed_receipts", "complete": False, "total": 1001}
        assert result["reconciliation"]["state"] == "pending"


@pytest.mark.asyncio
async def test_history_over_old_cap_and_malformed_unrelated_rows_do_not_gate(read_model_store):
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        await _add_source_receipt(session, project_id=project.id, global_experiment_id=global_experiment.id,
            domain_id=domain.id, receipt_id="quiet-source", digest="a" * 64,
            created_at="2026-08-01T00:00:00+00:00", reverified_at=datetime.now(timezone.utc) - timedelta(minutes=10))
        resources, receipts = [], []
        for index in range(10001):
            identity = f"history-{index:05d}"
            resources.append(dict(id=identity, kind="domain_adapter_receipt", workspace_id=project.id,
                lifecycle_owner_id=domain.id))
            receipts.append(dict(resource_id=identity, workspace_id=project.id, domain_experiment_id=domain.id,
                adapter_id="test.adapter.v1", adapter_version="1", operation_kind="reverify_source",
                normalized_request_sha256="b" * 64,
                receipt_json="{invalid" if index == 10000 else json.dumps({"source_receipt_id": "unrelated"})))
        await session.execute(insert(ExperimentResource), resources)
        await session.execute(insert(ExperimentDomainAdapterReceipt), receipts)
        result = await build_project_manager_read_model(session, project_id=project.id, focus_id=global_experiment.id)
        assert result["reconciliation"]["state"] == "current"
        assert result["source_receipt_ids"] == ["quiet-source"]


@pytest.mark.asyncio
async def test_exact_receipt_projection_uses_matching_expression_index(read_model_store):
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        await _add_source_receipt(session, project_id=project.id, global_experiment_id=global_experiment.id,
            domain_id=domain.id, receipt_id="indexed-source", digest="a" * 64,
            created_at="2026-08-01T00:00:00+00:00", reverified_at=datetime.now(timezone.utc))
        # Inspect the real query against the production model-owned index.
        statements = []
        engine = session.bind.sync_engine
        def capture(_connection, _cursor, statement, parameters, _context, _many):
            if statement.startswith("SELECT") and "FROM domain_adapter_receipts" in statement and "CASE WHEN" in statement:
                statements.append((statement, parameters))
        event.listen(engine, "before_cursor_execute", capture)
        try:
            result = await build_project_manager_read_model(session, project_id=project.id, focus_id=global_experiment.id)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert result["reconciliation"]["state"] == "current"
        assert len(statements) == 1
        connection = await session.connection()
        query, parameters = statements[0]
        plan = (await connection.exec_driver_sql("EXPLAIN QUERY PLAN " + query, parameters)).all()
        details = "\n".join(str(row[-1]) for row in plan)
        print("EXACT_RECEIPT_QUERY_PLAN:", details)
        assert "USING INDEX ix_experiment_domain_adapter_receipts_source_latest" in details
        assert "<expr>=?" in details
        assert "USE TEMP B-TREE FOR ORDER BY" not in details


@pytest.mark.parametrize("upgrade", [False, True])
def test_v22_clean_and_v21_upgrade_preserve_immutable_history(tmp_path, upgrade):
    database = tmp_path / "migration.db"
    if upgrade:
        root = Path(__file__).resolve().parents[3]
        source = subprocess.run(["git", "show", "4598063ea42565ba303c7f87efeefcf647198b69:platform/api/experiment_migrations.py"],
            cwd=root, check=True, capture_output=True, text=True).stdout
        issued = types.ModuleType("issued_v21_migrations")
        exec(compile(source, "issued_v21_migrations.py", "exec"), issued.__dict__)
        assert issued.LATEST_MIGRATION_VERSION == 21
        issued.run_all(database)
    else:
        experiment_migrations.run_all(database)
    connection = experiment_migrations._connect(database)
    try:
        for identity, kind, owner in [("p", "workspace", None), ("g", "experiment", "p"),
                                      ("d", "domain_experiment", "g"), ("r1", "domain_adapter_receipt", "d"),
                                      ("r2", "domain_adapter_receipt", "d")]:
            connection.execute("INSERT INTO resources(id,kind,workspace_id,lifecycle_owner_id,created_at) VALUES(?,?,?,?,?)",
                (identity, kind, None if kind == "workspace" else "p", owner, "2026-01-01T00:00:00Z"))
        for identity, body in [("r1", '{"source_receipt_id":"historic"}'), ("r2", "{malformed historical JSON")]:
            connection.execute("INSERT INTO domain_adapter_receipts VALUES(?,?,?,?,?,?,?,?,?)",
                (identity, "p", "d", "test.adapter.v1", "1", "reverify_source", "a" * 64, body, "2026-01-01T00:00:00Z"))
        connection.commit()
        before = connection.execute("SELECT * FROM domain_adapter_receipts ORDER BY resource_id").fetchall()
        triggers_before = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall()
        if upgrade:
            assert connection.execute("SELECT max(version) FROM experiment_schema_migrations").fetchone() == (21,)
    finally:
        connection.close()
    experiment_migrations.run_all(database)
    experiment_migrations.run_all(database)
    connection = experiment_migrations._connect(database)
    try:
        assert connection.execute("SELECT * FROM domain_adapter_receipts ORDER BY resource_id").fetchall() == before
        assert connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall() == triggers_before
        assert connection.execute("SELECT max(version) FROM experiment_schema_migrations").fetchone() == (22,)
        attestation = experiment_migrations.attest_schema(connection)
        assert attestation["ok"], attestation
        for statement in ["UPDATE domain_adapter_receipts SET receipt_json='{}' WHERE resource_id='r1'",
                          "DELETE FROM domain_adapter_receipts WHERE resource_id='r1'"]:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(statement)
            connection.rollback()
        assert connection.execute("SELECT * FROM domain_adapter_receipts ORDER BY resource_id").fetchall() == before
        connection.execute("DROP INDEX ix_experiment_domain_adapter_receipts_source_latest")
        assert experiment_migrations.attest_schema(connection)["ok"] is False
    finally:
        connection.close()
