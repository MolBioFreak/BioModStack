"""Lane F: isolated authority regressions and work counters; no live stores."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import event, insert, update, delete
from jsonschema.exceptions import _WrappedReferencingError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from services import ngs_molbio_capabilities as caps
from services import ngs_molbio_n5 as n5
from services import ngs_molbio_connector as connector
from services import ngs_molbio_release_acceptance as release
from services import ngs_molbio_run_control as control


def test_metadata_biological_labels_and_closed_constraints():
    value = {"display_label": "Sequence alignment and restriction digest", "tags": ["reads", "structure", "signal"]}
    assert n5._metadata(value) == value
    for invalid in ({"sequence": "ACGT"}, {"tags": [{}]}, {"tags": ["x", "x"]},
                    {"display_label": "x" * 256}, {"group_label": {}}, {"tags": ["x" * 2049]}):
        with pytest.raises(n5.ValidationFailure):
            n5._metadata(invalid)


def test_warm_schema_authority_reuses_checks_and_defensive_copies(monkeypatch, tmp_path):
    # Exercise the installed registry bytes, not a test-repaired schema manifest.
    schema_registry, _ = caps._read(caps._CONFIG_ROOT / caps._REGISTRY_FILES["schema"])
    (tmp_path / caps._REGISTRY_FILES["schema"]).write_text(json.dumps(schema_registry))
    (tmp_path / caps._REGISTRY_FILES["dataset"]).write_bytes((caps._CONFIG_ROOT / caps._REGISTRY_FILES["dataset"]).read_bytes())
    monkeypatch.setattr(caps, "_CONFIG_ROOT", tmp_path)
    for name in ("_schema_context_version", "_contract_document_version", "_verified_schema_version", "_compiled_validator", "_checked_schema"):
        getattr(caps, name).cache_clear()
    checks = []
    original = caps.Draft202012Validator.check_schema
    monkeypatch.setattr(caps.Draft202012Validator, "check_schema", lambda schema: (checks.append(schema), original(schema))[1])
    first = caps.contract_registry("dataset")
    cold = len(checks)
    assert cold > 0
    first["entries"].clear()
    for _ in range(20):
        assert caps.contract_registry("dataset")["entries"]
    assert len(checks) == cold
    print(f"F schema counter: cold meta-checks={cold}; 20 warm requests additional=0")


def test_connector_validator_is_cached_and_still_checks_each_payload(monkeypatch):
    caps._compiled_validator.cache_clear()
    caps._checked_schema.cache_clear()
    schema_id = "bms.molbio-ngs.binding-acknowledged.v1"
    body = {"schema": schema_id, "binding_revision_id": "binding-1", "binding_revision_number": 1, "binding_receipt_sha256": "a" * 64}
    connector._validate(schema_id, body)
    misses = caps._compiled_validator.cache_info().misses
    for _ in range(20):
        connector._validate(schema_id, body)
    assert caps._compiled_validator.cache_info().misses == misses
    with pytest.raises(connector.ConnectorConflict):
        connector._validate(schema_id, {**body, "binding_revision_number": -1})
    print("F connector counter: 20 warm payloads, zero additional validator compilations")


def test_selected_schema_dependency_changes_are_not_hidden(tmp_path, monkeypatch):
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:lane:f", "type": "string"}
    path = tmp_path / "selected.json"
    raw = json.dumps(schema).encode()
    path.write_bytes(raw)
    monkeypatch.setattr(caps, "_REPO_ROOT", tmp_path)
    rows = [{"schema_id": schema["$id"], "path": path.name, "schema_sha256": caps._raw_digest(raw), "schema_canonical_sha256": caps._raw_digest(caps.rfc8785.dumps(schema))}]
    scoped, _ = caps._schema_closure(rows)
    assert scoped[schema["$id"]] == schema
    path.write_bytes(raw + b" ")
    with pytest.raises(caps.NgsMolBioCapabilityError, match="byte digest"):
        scoped[schema["$id"]]


def test_cached_validator_rechecks_referenced_schema_bytes(tmp_path, monkeypatch):
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:lane:f:child", "type": "integer"}
    path = tmp_path / "child.json"
    raw = json.dumps(schema).encode()
    path.write_bytes(raw)
    monkeypatch.setattr(caps, "_REPO_ROOT", tmp_path)
    rows = [{"schema_id": schema["$id"], "path": path.name, "schema_sha256": caps._raw_digest(raw), "schema_canonical_sha256": caps._raw_digest(caps.rfc8785.dumps(schema))}]
    _, registry = caps._schema_closure(rows)
    validator = caps._compiled_validator(json.dumps({"$ref": schema["$id"]}), registry)
    assert validator.is_valid(1)
    path.write_bytes(raw + b" ")
    # referencing wraps the typed retrieval error; it must never accept stale bytes.
    with pytest.raises(_WrappedReferencingError):
        validator.is_valid(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "revision", "archive", "restore"])
async def test_successful_dataset_replay_precedes_mutable_catalog(monkeypatch, operation):
    monkeypatch.setattr(n5, "require_domain_hierarchy", AsyncMock())
    monkeypatch.setattr(n5, "require_dataset_read", AsyncMock(return_value=SimpleNamespace(dataset_kind="retired-kind")))
    catalog = lambda *args: pytest.fail("replay consulted current catalog")
    monkeypatch.setattr(n5, "_enabled_dataset_kind", catalog)
    common = dict(project_id="p", experiment_id="e", domain_id="d", actor="actor", idempotency_key="key", change_summary="change")
    normalized = dict(project_id="p", experiment_id="e", domain_id="d", change_summary="change")
    if operation == "create":
        normalized.update(operation="dataset_create", name="name", dataset_kind="kind")
        function, kwargs = n5.create_project_dataset, dict(name="name", dataset_kind="kind")
    elif operation == "revision":
        normalized.update(operation="dataset_revision_create", dataset_id="dataset", expected_head_generation=1, members=[])
        function, kwargs = n5.revise_project_dataset, dict(core_session=None, dataset_id="dataset", expected_head_generation=1, members=[])
    else:
        normalized.update(operation="dataset_" + operation, dataset_id="dataset", expected_head_generation=1)
        function, kwargs = n5.set_project_dataset_lifecycle, dict(dataset_id="dataset", expected_head_generation=1, operation=operation)
    claim = SimpleNamespace(request_sha256=n5.sha256_text(n5.canonical_json(normalized)), response_json='{"retained":true}')
    session = SimpleNamespace(get=AsyncMock(return_value=claim))
    assert await function(session, **common, **kwargs) == {"retained": True}
    claim.request_sha256 = "different"
    with pytest.raises(n5.IdempotencyConflict):
        await function(session, **common, **kwargs)
    assert n5.require_domain_hierarchy.await_count == 2
    session.get.return_value = None
    current_catalog = Mock(side_effect=n5.ValidationFailure("catalog disabled"))
    monkeypatch.setattr(n5, "_enabled_dataset_kind", current_catalog)
    with pytest.raises(n5.ValidationFailure, match="catalog disabled"):
        await function(session, **common, **kwargs)
    current_catalog.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["foreground", "worker"])
@pytest.mark.parametrize("failure", [None, "retry", "conflict", "unexpected"])
async def test_leased_processing_is_shared_without_reclaim(monkeypatch, entrypoint, failure):
    command = SimpleNamespace(command_id="c", lease_token="token", lease_owner="worker", status="leased", request_json='{"reason":"stop"}')
    claim = AsyncMock(return_value=command)
    monkeypatch.setattr(control, "_claim_command", claim)
    monkeypatch.setattr(control, "_decode_command", lambda command: ({}, {}))
    monkeypatch.setattr(control, "_flatten_targets", lambda snapshot: ["target"])
    process = AsyncMock()
    if failure == "retry":
        process.side_effect = control._Retryable("retry", "retry")
    elif failure == "conflict":
        process.side_effect = control._Conflict("conflict", "conflict")
    elif failure == "unexpected":
        process.side_effect = RuntimeError("unexpected")
    monkeypatch.setattr(control, "_process_target", process)
    finalize, retry, conflict = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(control, "_finalize", finalize)
    monkeypatch.setattr(control, "_set_retryable", retry)
    monkeypatch.setattr(control, "_set_conflicted", conflict)
    session = SimpleNamespace(rollback=AsyncMock(), get=AsyncMock(return_value=command))
    core = SimpleNamespace(rollback=AsyncMock())
    if entrypoint == "foreground":
        assert await control.process_run_control_command(session, core, command_id="c", worker_id="worker") is command
    else:
        assert await control.process_run_control_command_once(session, core, worker_id="worker") == 1
    assert claim.await_count == process.await_count == 1
    assert finalize.await_count == (failure is None)
    assert session.rollback.await_count == core.rollback.await_count == (failure is not None)
    transition = conflict if failure == "conflict" else retry
    if failure is not None:
        assert transition.call_args.kwargs["lease_token"] == "token"


def test_closed_evidence_validator_rejects_instead_of_nameerror():
    with pytest.raises(release.SharedPackageAcceptanceError, match="closed evidence schema"):
        release._validate_closed_package_evidence({}, receipt_id="receipt", expected_runtime_implementation_sha256="a" * 64)


async def _scratch_session(tmp_path, models):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "scratch.sqlite"))
    async with engine.begin() as connection:
        for model in models:
            await connection.run_sync(lambda sync, model=model: model.__table__.create(sync))
    return engine, AsyncSession(engine, expire_on_commit=False)


def _fixture_row(model, index, **overrides):
    row = {}
    for column in model.__table__.columns:
        if column.nullable or column.default is not None or column.server_default is not None:
            continue
        kind = column.type.python_type
        row[column.name] = index if kind is int else f"{column.name}-{index}"
    for field in ("created_at", "updated_at"):
        if field in model.__table__.columns:
            row[field] = "2026-01-01T00:00:00+00:00"
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_health_uses_scalar_aggregates_without_payload_hydration(tmp_path):
    models = [connector.ExperimentDomainConnectorCommand, connector.MolBioNGSOutboxEvent,
              connector.ExperimentDomainConnectorInbox, connector.ExperimentDomainConnectorConflict]
    engine, session = await _scratch_session(tmp_path, models)
    try:
        async with session:
            for model in models[:2]:
                await session.execute(insert(model), [_fixture_row(model, i, status="pending" if i < 3 else "applied", created_at="2026-01-01T00:00:00+00:00") for i in range(1003)])
            await session.commit()
            statements = []
            event.listen(engine.sync_engine, "before_cursor_execute", lambda conn, cursor, statement, *args: statements.append(statement))
            health = await connector.connector_health(session, session)
            assert health["command_pending_count"] == health["outbox_pending_count"] == 3
            assert health["command_conflict_count"] == health["outbox_conflict_count"] == 0
            assert health["oldest_command_age_seconds"] is not None
            assert not session.identity_map
            assert len(statements) == 5
            assert sum("GROUP BY" in sql for sql in statements) == 2
            assert all("payload_json" not in sql and "command_json" not in sql for sql in statements)
            print("F health: 2006 retained rows; 5 scalar queries; zero ORM payload identities")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_convergence_covers_more_than_old_ceiling_and_late_divergence(tmp_path):
    local, inbox = release.MolBioNGSOutboxEvent, release.ExperimentDomainConnectorInbox
    command, ack = release.ExperimentDomainConnectorCommand, release.MolBioNGSConnectorAcknowledgement
    engine, session = await _scratch_session(tmp_path, [local, inbox, command, ack])
    try:
        async with session:
            shared = dict(binding_revision_id="binding", state_revision_id=None, event_type="event", event_stream="stream", source_generation=1, payload_sha256="a" * 64, acknowledgement_sha256="b" * 64)
            total = 10005
            await session.execute(insert(local), [_fixture_row(local, i, stream_generation=i + 1, id=f"event-{i:06}", status="acknowledged", global_domain_experiment_id="domain", **shared) for i in range(total)])
            await session.execute(insert(inbox), [_fixture_row(inbox, i, stream_generation=i + 1, event_id=f"event-{i:06}", disposition="applied", source_store_id=release._LOCAL_SOURCE_STORE_ID, domain_experiment_id="domain", **shared) for i in range(total)])
            await session.commit()
            statements = []
            event.listen(engine.sync_engine, "before_cursor_execute", lambda conn, cursor, statement, *args: statements.append(statement))
            assert await release._connector_convergence_count(session, session) == 0
            assert not session.identity_map
            assert all("payload_json" not in sql and "acknowledgement_json" not in sql for sql in statements)
            assert all("LIMIT" in sql for sql in statements)
            await session.execute(update(local).where(local.id == "event-010004").values(payload_sha256="c" * 64))
            await session.execute(delete(inbox).where(inbox.event_id == "event-010003"))
            assert await release._connector_convergence_count(session, session) == 2
            command_shared = dict(command_id="command-1", acknowledgement_id="ack-1", binding_revision_id="binding", acknowledgement_sha256="d" * 64)
            await session.execute(insert(command), [_fixture_row(command, 1, status="applied", **command_shared)])
            await session.execute(insert(ack), [_fixture_row(ack, 1, disposition="applied", **command_shared)])
            assert await release._connector_convergence_count(session, session) == 2
            await session.execute(update(ack).values(acknowledgement_sha256="e" * 64))
            assert await release._connector_convergence_count(session, session) == 3
            await session.execute(delete(command))
            assert await release._connector_convergence_count(session, session) == 3
            print(f"F convergence: {total} settled pairs; 512-row scalar batches; late mismatch and missing event/command peer detected")
    finally:
        await engine.dispose()



def test_payload_scan_uses_declared_source_and_real_sqlite_not_whole_source_record(tmp_path):
    import sqlite3
    from services import payload_ownership_audit as owner
    contract = owner.load_installed_payload_ownership_contract()
    database = tmp_path / "payloads.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, params TEXT)")
        connection.execute("INSERT INTO jobs VALUES ('completed', 'completed', '{}')")
        connection.execute("CREATE TABLE payloads (id TEXT PRIMARY KEY, payload TEXT)")
    targets = [owner.SQLiteColumnTarget("jobs", "scratch", database, "core", "jobs", ("id",), ("id",),
        "params", "*", "json", "projection")]
    for index, row in enumerate(contract.manifest["classes"]):
        targets.append(owner.SQLiteColumnTarget(f"owner_{index}", "scratch", database,
            row["active_authority"], "payloads", ("id",), ("id",), "payload", row["payload_class"], "text", "owner"))
    plan = owner.PayloadOwnershipScanPlan(owner.ReleaseSourceIdentity("a" * 40, "b" * 40), tuple(targets),
        (owner.SQLiteActiveJobCheck("active", "scratch", database, "jobs", "id", "status", ("queued", "running")),))
    result = owner.run_payload_ownership_scan(plan)
    assert result.audit["source_commit"] == "a" * 40
    assert result.audit["source_tree"] == "b" * 40
    assert result.audit["no_active_jobs"] is True
    assert result.scan_sources[0]["source_kind"] == "sqlite_snapshot"
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO jobs VALUES ('running', 'running', '{}')")
    with pytest.raises(owner.ActiveJobsPresent):
        owner.run_payload_ownership_scan(plan)
