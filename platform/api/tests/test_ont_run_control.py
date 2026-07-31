from __future__ import annotations

import asyncio
import copy
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import database  # noqa: E402
from database import Base, OntInstrumentRun, OntInstrumentRunEvent, OntProtocolOptionReceipt  # noqa: E402
from migrations.add_ont_instrument_run_ledger import migrate as migrate_ont_ledger  # noqa: E402
from migrations.add_ont_protocol_preflight import migrate as migrate_ont_preflight  # noqa: E402
from migrations.add_ont_terminal_artifact_manifests import migrate as migrate_ont_terminal_manifests  # noqa: E402
from migrations.sqlite_sha256 import register_sqlite_sha256  # noqa: E402
from routers import ont_runs  # noqa: E402
from services import ont_run_control  # noqa: E402
from services.ont_run_control import (  # noqa: E402
    build_start_preflight,
    stop_instrument_run,
    build_plasmid_qc_handoff,
)


async def _seed_server_observed_run(
    *,
    state: str = "running",
    minknow_run_id: str = "PRIVATE-MINKNOW-RUN",
    output_files: dict[str, list[str]] | None = None,
) -> str:
    """Seed a host-observed row only for server-internal lifecycle tests.

    Physical starts are retired in this phase, so tests that exercise retained
    refresh/stop/handoff internals must not recreate a browser-reachable start.
    """
    run_id = "ont-run-server-observed"
    now = ont_run_control._utc_now()
    files = output_files or {"fastq": [], "pod5": [], "bam": []}
    async with ont_run_control.async_session() as session:
        session.add(
            OntInstrumentRun(
                id=run_id,
                position_id="X1",
                minknow_run_id=minknow_run_id,
                state=state,
                observed_at=now,
                observed_generation=1,
                sample_id="sample-safe",
                experiment_group="group-safe",
                kit="PRIVATE-KIT",
                output_directories={"reads": "/private/output"},
                output_files=files,
                handoff_ready=bool(files.get("fastq") or files.get("bam")),
                last_minknow_payload={"protocol_id": "PRIVATE-PROTOCOL", "rpc": {"target": "PRIVATE-RPC"}},
            )
        )
        session.add(
            OntInstrumentRunEvent(
                id="ont-run-event-server-observed",
                run_id=run_id,
                event_type="status_observed",
                state=state,
                observed_at=now,
                observed_generation=1,
                minknow_payload={"flow_cell_id": "PRIVATE-FLOWCELL", "history": ["PRIVATE-HISTORY"]},
                output_files=files,
            )
        )
        await session.commit()
    return run_id


@pytest.fixture(autouse=True)
def durable_test_ledger(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "ont-ledger.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        poolclass=NullPool,
        json_serializer=database._canonical_sqlite_json,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def register_manifest_hashing(dbapi_connection, _connection_record) -> None:
        register_sqlite_sha256(dbapi_connection)
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async def create_schema() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_schema())
    migrate_ont_ledger(str(db_path))
    migrate_ont_preflight(str(db_path))
    migrate_ont_terminal_manifests(str(db_path))
    factory = sessionmaker(engine, class_=ont_run_control.AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(ont_run_control, "async_session", factory)
    yield
    asyncio.run(engine.dispose())


def test_start_preflight_blocks_missing_flowcell() -> None:
    payload = build_start_preflight(
        position={
            "position": "X1",
            "running": False,
            "flow_cell": {"present": False},
        },
        kit="SQK-LSK114",
        basecalling_enabled=True,
        output_directories={"reads": "/data/minknow"},
    )

    assert payload["can_start"] is False
    assert "flowcell_absent" in payload["blockers"]
    assert payload["position"] == "X1"
    assert payload["fake_or_demo_devices"] is False


def test_start_preflight_blocks_running_position_and_missing_output_directory() -> None:
    payload = build_start_preflight(
        position={
            "position": "X1",
            "running": True,
            "flow_cell": {"present": True, "product_code": "FLO-MIN114"},
        },
        kit="SQK-LSK114",
        basecalling_enabled=False,
        output_directories={},
    )

    assert payload["can_start"] is False
    assert payload["blockers"] == ["position_already_running", "output_directory_missing"]


def test_start_preflight_allows_ready_position_with_requested_kit_and_output_dir() -> None:
    payload = build_start_preflight(
        position={
            "position": "X1",
            "running": False,
            "flow_cell": {"present": True, "product_code": "FLO-MIN114"},
        },
        kit="SQK-LSK114",
        basecalling_enabled=True,
        basecalling_options={"simplex_models": ["dna_r10.4.1_e8.2_400bps_sup"]},
        output_directories={"reads": "/data/minknow"},
    )

    assert payload["can_start"] is True
    assert payload["blockers"] == []
    assert payload["protocol_id"] is None
    assert payload["basecalling_options"]["simplex_models"] == ["dna_r10.4.1_e8.2_400bps_sup"]



def test_start_preflight_exposes_flowcell_truth_and_output_directories_for_config_cell() -> None:
    payload = build_start_preflight(
        position={
            "position": "MD-105428",
            "running": True,
            "flow_cell": {
                "present": False,
                "is_ctc": False,
                "channel_count": 0,
                "sample_rate": 10000,
            },
            "output_directories": {"reads": "/var/lib/minknow/data/"},
        },
        kit="SQK-LSK114",
        basecalling_enabled=True,
        output_directories={"reads": "/var/lib/minknow/data/"},
    )

    assert payload["can_start"] is False
    assert payload["flow_cell"] == {
        "present": False,
        "is_ctc": False,
        "channel_count": 0,
        "sample_rate": 10000,
    }
    assert payload["output_directories"] == {"reads": "/var/lib/minknow/data/"}
    assert "flowcell_absent" in payload["blockers"]
    assert "position_already_running" in payload["blockers"]
    assert "output_directory_missing" not in payload["blockers"]


def test_refresh_position_state_proxies_host_agent_without_claiming_restart(monkeypatch) -> None:
    monkeypatch.setattr(
        ont_run_control,
        "request_host_agent",
        lambda method, path, payload=None, *, query=None: {
            "action": "refresh",
            "detail": "Reopened the MinKNOW position connection and reread device/flow-cell state; this does not power-cycle the instrument.",
            "position": {"position": "MD-105428", "device_type": "mk1d"},
            "fake_or_demo_devices": False,
        },
    )

    payload = ont_run_control.refresh_position_state("MD-105428")

    assert payload["action"] == "refresh"
    assert payload["detail"] == "Re-read the Mk1D position state without a power cycle."
    assert payload["position"]["position"] == "MD-105428"


def test_restart_position_requires_confirmation_but_remains_host_agent_contract(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method, path, payload=None, *, query=None):
        calls.append((method, path, payload))
        return {
            "detail": "BMS does not yet perform a MinKNOW/Mk1D instrument restart",
            "position": "MD-105428",
            "fake_or_demo_devices": False,
        }

    monkeypatch.setattr(ont_run_control, "request_host_agent", fake_request)

    try:
        ont_run_control.restart_position("MD-105428", {"confirm_restart": False})
    except ValueError as exc:
        assert "confirm_restart" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("restart should require explicit confirmation")

    payload = ont_run_control.restart_position("MD-105428", {"confirm_restart": True})

    assert "does not yet perform" in payload["detail"]
    assert calls == [("POST", "/ont/positions/MD-105428/restart", {"confirm_restart": True})]

def test_protocol_options_endpoint_uses_host_agent_payload(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    client = TestClient(app)

    monkeypatch.setattr(
        ont_run_control,
        "get_position_protocol_options",
        lambda position, kit=None, basecalling_enabled=True: {
            "position": position,
            "device_type": "mk1d",
            "can_start": True,
            "blockers": [],
            "protocol_id": "sequencing/sequencing_MIN114_DNA_e8_2_400K",
            "basecalling_options": {"simplex_models": ["sup"]},
            "output_directories": {"reads": "/data/minknow"},
            "flow_cell": {"present": True},
            "fake_or_demo_devices": False,
        },
    )

    response = client.get("/api/ont/positions/X1/protocol-options?kit=SQK-LSK114")

    assert response.status_code == 200
    payload = response.json()
    assert payload["position"] == "X1"
    assert payload["can_start"] is True
    assert payload["fake_or_demo_devices"] is False


def test_protocol_catalog_issues_opaque_receipts_without_exposing_protocol_paths_or_models(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    client = TestClient(app)
    monkeypatch.setattr(
        ont_run_control,
        "get_position_protocol_options",
        lambda position, kit=None, basecalling_enabled=True: {
            "position": position,
            "device_type": "mk1d",
            "can_start": True,
            "blockers": [],
            "protocol_id": "sequencing/sequencing_MIN114_DNA_e8_2_400K",
            "kit": "SQK-LSK114",
            "basecalling_enabled": True,
            "basecalling_options": {"simplex_models": ["dna_r10.4.1_e8.2_400bps_sup"]},
            "output_directories": {"reads": "/var/lib/minknow/data"},
            "flow_cell": {"present": True, "flow_cell_id": "FC-001", "product_code": "FLO-MIN114"},
            "fake_or_demo_devices": False,
        },
    )

    response = client.get("/api/ont/positions/X1/protocol-options")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["position"] == "X1"
    assert payload["can_start"] is True
    assert payload["options"] and len(payload["options"]) == 1
    option = payload["options"][0]
    assert option["option_id"].startswith("ont-option-")
    assert option["option_receipt_id"].startswith("ont-preflight-")
    assert option["output_policy_id"].startswith("ont-output-policy-")
    assert option["protocol_label"]
    assert "protocol_id" not in option
    assert "basecalling_options" not in option
    assert "output_directories" not in payload
    assert "/var/lib/minknow/data" not in response.text
    assert "dna_r10.4.1_e8.2_400bps_sup" not in response.text


def test_protocol_catalog_returns_graceful_no_flowcell_blocker_without_option_receipt(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    monkeypatch.setattr(
        ont_run_control,
        "get_position_protocol_options",
        lambda *_args, **_kwargs: {
            "position": "X1",
            "device_type": "mk1d",
            "can_start": False,
            "blockers": ["flowcell_absent"],
            "flow_cell": {"present": False},
            "output_directories": {"reads": "/var/lib/minknow/data"},
            "fake_or_demo_devices": False,
        },
    )

    response = TestClient(app).get("/api/ont/positions/X1/protocol-options")

    assert response.status_code == 200, response.text
    assert response.json()["can_start"] is False
    assert response.json()["blockers"] == ["flowcell_absent", "protocol_unavailable"]
    assert response.json()["options"] == []
    assert "/var/lib/minknow/data" not in response.text


@pytest.mark.asyncio
async def test_run_intent_binds_opaque_option_to_durable_ledger_and_rejects_raw_protocol_inputs(monkeypatch) -> None:
    host_preflight = {
        "position": "X1",
        "device_type": "mk1d",
        "can_start": True,
        "blockers": [],
        "protocol_id": "sequencing/sequencing_MIN114_DNA_e8_2_400K",
        "kit": "SQK-LSK114",
        "basecalling_enabled": True,
        "basecalling_options": {"simplex_models": ["sup"]},
        "output_directories": {"reads": "/var/lib/minknow/data"},
        "flow_cell": {"present": True, "flow_cell_id": "FC-001", "product_code": "FLO-MIN114"},
        "fake_or_demo_devices": False,
    }
    monkeypatch.setattr(ont_run_control, "get_position_protocol_options", lambda *_args, **_kwargs: host_preflight)

    catalog = await ont_run_control.issue_position_protocol_catalog("X1")
    option = catalog["options"][0]
    intent = await ont_run_control.create_run_intent(
        "X1",
        {
            "option_id": option["option_id"],
            "option_receipt_id": option["option_receipt_id"],
            "sample_id": "plasmid-A12",
            "experiment_group": "verification",
        },
    )

    assert intent["id"].startswith("ont-run-")
    assert intent["state"] == "armed"
    assert intent["selected_option_id"] == option["option_id"]
    assert intent["flow_cell_identity"] != "FC-001"
    assert "minknow_run_id" not in intent
    assert [event["event_type"] for event in intent["events"]] == ["preflight_armed"]
    with pytest.raises(ValueError, match="opaque option"):
        await ont_run_control.create_run_intent(
            "X1",
            {
                "option_id": option["option_id"],
                "option_receipt_id": option["option_receipt_id"],
                "protocol": {"arbitrary": "json"},
                "output_directory": "/caller/chosen/path",
            },
        )


@pytest.mark.asyncio
async def test_run_intent_rejects_expired_and_cross_position_opaque_receipts(monkeypatch) -> None:
    def host_preflight(position: str, *_args, **_kwargs):
        return {
            "position": position,
            "device_type": "mk1d",
            "can_start": True,
            "blockers": [],
            "protocol_id": "PRIVATE-PROTOCOL",
            "kit": "PRIVATE-KIT",
            "basecalling_enabled": False,
            "basecalling_options": {},
            "output_directories": {"reads": "/private/output"},
            "flow_cell": {"present": True, "flow_cell_id": f"FC-{position}"},
        }

    monkeypatch.setattr(ont_run_control, "get_position_protocol_options", host_preflight)
    option = (await ont_run_control.issue_position_protocol_catalog("X1"))["options"][0]

    with pytest.raises(ValueError, match="unknown, expired, or already consumed"):
        await ont_run_control.create_run_intent(
            "X2",
            {"option_id": option["option_id"], "option_receipt_id": option["option_receipt_id"]},
        )

    async with ont_run_control.async_session() as session:
        receipt = await session.get(OntProtocolOptionReceipt, option["option_receipt_id"])
        assert receipt is not None
        receipt.expires_at = ont_run_control._utc_now() - timedelta(seconds=1)
        await session.commit()

    with pytest.raises(ValueError, match="expired"):
        await ont_run_control.create_run_intent(
            "X1",
            {"option_id": option["option_id"], "option_receipt_id": option["option_receipt_id"]},
        )


@pytest.mark.asyncio
async def test_public_start_501_is_canonical_and_does_not_reflect_backend_exception(monkeypatch) -> None:
    async def disabled_start(_run_id: str, _payload: dict) -> dict:
        raise NotImplementedError("PRIVATE-PROTOCOL /private/path grpc://private-host")

    monkeypatch.setattr(ont_run_control, "validate_armed_intent_start", disabled_start)
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    response = TestClient(app).post(
        "/api/ont/runs/ont-run-safe/start",
        json={"confirm_start": True, "intent_generation": 1},
    )

    assert response.status_code == 501
    assert response.json() == {
        "detail": "MinKNOW protocol start remains disabled pending separately authorized supervised commissioning."
    }
    assert "PRIVATE" not in response.text
    assert "grpc://" not in response.text
    assert "/private/path" not in response.text


@pytest.mark.asyncio
async def test_intent_start_rereads_position_and_invalidates_changed_flowcell_without_host_start(monkeypatch) -> None:
    host_preflight = {
        "position": "X1",
        "device_type": "mk1d",
        "can_start": True,
        "blockers": [],
        "protocol_id": "sequencing/min114",
        "kit": "SQK-LSK114",
        "basecalling_enabled": True,
        "basecalling_options": {"simplex_models": ["sup"]},
        "output_directories": {"reads": "/var/lib/minknow/data"},
        "flow_cell": {"present": True, "flow_cell_id": "FC-001", "product_code": "FLO-MIN114"},
    }
    monkeypatch.setattr(ont_run_control, "get_position_protocol_options", lambda *_args, **_kwargs: host_preflight)
    option = (await ont_run_control.issue_position_protocol_catalog("X1"))["options"][0]
    intent = await ont_run_control.create_run_intent(
        "X1", {"option_id": option["option_id"], "option_receipt_id": option["option_receipt_id"]}
    )
    monkeypatch.setattr(
        ont_run_control,
        "get_position_protocol_options",
        lambda _position: {
            "position": "X1",
            "device_type": "mk1d",
            "running": False,
            "flow_cell": {"present": True, "flow_cell_id": "FC-CHANGED", "product_code": "FLO-MIN114"},
        },
    )
    monkeypatch.setattr(
        ont_run_control,
        "request_host_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("start must not call host-agent")),
    )

    with pytest.raises(ValueError, match="flowcell_mismatch"):
        await ont_run_control.validate_armed_intent_start(
            intent["id"], {"confirm_start": True, "intent_generation": intent["observed_generation"]}
        )

    persisted = await ont_run_control.get_instrument_run(intent["id"])
    assert persisted is not None
    assert persisted["state"] == "armed"
    assert persisted["preflight"]["invalidation_reason"] == "flowcell_mismatch"
    assert [event["event_type"] for event in persisted["events"]] == ["preflight_armed", "preflight_invalidated"]


@pytest.mark.asyncio
async def test_intent_start_invalidates_changed_protocol_capability_digest_without_host_start(monkeypatch) -> None:
    initial = {
        "position": "X1",
        "device_type": "mk1d",
        "can_start": True,
        "blockers": [],
        "protocol_id": "sequencing/initial-protocol",
        "kit": "SQK-LSK114",
        "basecalling_enabled": True,
        "basecalling_options": {"simplex_models": ["initial-model"]},
        "output_directories": {"reads": "/private/initial-output"},
        "flow_cell": {"present": True, "flow_cell_id": "FC-001", "product_code": "FLO-MIN114"},
    }
    monkeypatch.setattr(ont_run_control, "get_position_protocol_options", lambda *_args, **_kwargs: initial)
    option = (await ont_run_control.issue_position_protocol_catalog("X1"))["options"][0]
    intent = await ont_run_control.create_run_intent(
        "X1", {"option_id": option["option_id"], "option_receipt_id": option["option_receipt_id"]}
    )
    changed = {**initial, "basecalling_options": {"simplex_models": ["replacement-model"]}}
    monkeypatch.setattr(ont_run_control, "get_position_protocol_options", lambda *_args, **_kwargs: changed)
    monkeypatch.setattr(ont_run_control, "get_ont_position", lambda _position: initial)
    monkeypatch.setattr(
        ont_run_control,
        "request_host_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("intent start must not contact a raw host start route")),
    )

    with pytest.raises(ValueError, match="capability_mismatch"):
        await ont_run_control.validate_armed_intent_start(
            intent["id"], {"confirm_start": True, "intent_generation": intent["observed_generation"]}
        )


@pytest.mark.asyncio
async def test_public_intent_projection_omits_raw_protocol_flowcell_paths_and_event_payloads(monkeypatch) -> None:
    monkeypatch.setattr(
        ont_run_control,
        "get_position_protocol_options",
        lambda *_args, **_kwargs: {
            "position": "X1",
            "device_type": "mk1d",
            "can_start": True,
            "blockers": [],
            "protocol_id": "protocol/PRIVATE-PROTOCOL",
            "kit": "PRIVATE-KIT",
            "basecalling_enabled": True,
            "basecalling_options": {"simplex_models": ["PRIVATE-MODEL"]},
            "output_directories": {"reads": "/private/output"},
            "flow_cell": {"present": True, "flow_cell_id": "PRIVATE-FLOWCELL", "product_code": "PRIVATE-PRODUCT"},
        },
    )
    option = (await ont_run_control.issue_position_protocol_catalog("X1"))["options"][0]
    intent = await ont_run_control.create_run_intent(
        "X1", {"option_id": option["option_id"], "option_receipt_id": option["option_receipt_id"]}
    )

    rendered = str(intent)
    for secret in ("PRIVATE-PROTOCOL", "PRIVATE-KIT", "PRIVATE-MODEL", "/private/output", "PRIVATE-FLOWCELL", "PRIVATE-PRODUCT"):
        assert secret not in rendered
    assert "minknow_payload" not in rendered
    assert "output_files" not in rendered


@pytest.mark.asyncio
async def test_armed_intent_revalidates_without_any_raw_host_start_call(monkeypatch) -> None:
    host_preflight = {
        "position": "X1",
        "device_type": "mk1d",
        "can_start": True,
        "blockers": [],
        "protocol_id": "PRIVATE-PROTOCOL",
        "kit": "PRIVATE-KIT",
        "basecalling_enabled": True,
        "basecalling_options": {"simplex_models": ["PRIVATE-MODEL"]},
        "output_directories": {"reads": "/private/output"},
        "flow_cell": {"present": True, "flow_cell_id": "PRIVATE-FLOWCELL"},
    }
    monkeypatch.setattr(ont_run_control, "get_position_protocol_options", lambda *_args, **_kwargs: host_preflight)
    option = (await ont_run_control.issue_position_protocol_catalog("X1"))["options"][0]
    intent = await ont_run_control.create_run_intent(
        "X1", {"option_id": option["option_id"], "option_receipt_id": option["option_receipt_id"]}
    )
    monkeypatch.setattr(ont_run_control, "get_ont_position", lambda _position: host_preflight)
    monkeypatch.setattr(
        ont_run_control,
        "request_host_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("intent validation must not call a host start route")),
    )

    with pytest.raises(NotImplementedError, match="start remains disabled"):
        await ont_run_control.validate_armed_intent_start(
            intent["id"], {"confirm_start": True, "intent_generation": intent["observed_generation"]}
        )

    persisted = await ont_run_control.get_instrument_run(intent["id"])
    assert persisted is not None
    assert persisted["state"] == "armed"
    assert [event["event_type"] for event in persisted["events"]] == ["preflight_armed", "preflight_revalidated"]


@pytest.mark.asyncio
async def test_retained_server_handoff_keeps_paths_internal(monkeypatch, tmp_path: Path) -> None:
    fastq = tmp_path / "reads.fastq.gz"
    reference = tmp_path / "reference.fasta"
    fastq.write_text("@r1\nACGT\n+\n!!!!\n")
    reference.write_text(">plasmid\nACGT\n")
    run_id = await _seed_server_observed_run(state="starting", output_files={"fastq": [str(fastq)], "pod5": [], "bam": []})
    monkeypatch.setattr(
        ont_run_control,
        "request_host_agent",
        lambda *_args, **_kwargs: {"status": "completed", "minknow_run_id": "PRIVATE-MINKNOW-RUN", "output_files": {"fastq": [str(fastq)], "pod5": [], "bam": []}},
    )
    await ont_run_control.reconcile_instrument_run(run_id)

    handoff = await build_plasmid_qc_handoff(run_id, {"reference_fasta": str(reference)})

    assert handoff["params"]["fastq_path"] == str(fastq)
    assert handoff["params"]["reference_fasta"] == str(reference)
    assert handoff["params"]["source_instrument_run_id"] == run_id


@pytest.mark.asyncio
async def test_stop_retained_server_operation_returns_only_safe_projection(monkeypatch) -> None:
    run_id = await _seed_server_observed_run()
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method, path, payload=None, *, query=None):
        calls.append((method, path, payload))
        return {"status": "stopped", "output_directories": {"reads": "/private/stop-output"}}

    monkeypatch.setattr(ont_run_control, "request_host_agent", fake_request)
    with pytest.raises(ValueError, match="confirm_stop"):
        await stop_instrument_run(run_id, {"confirm_stop": False})
    stopped = await stop_instrument_run(run_id, {"confirm_stop": True})

    assert stopped["status"] == "stopped"
    assert stopped["output_summary"] == {"fastq": 0, "pod5": 0, "bam": 0}
    assert calls == [("POST", "/ont/runs/PRIVATE-MINKNOW-RUN/stop", {"confirm_stop": True})]
    assert "PRIVATE-MINKNOW-RUN" not in str(stopped)
    assert "/private/stop-output" not in str(stopped)


def test_raw_start_route_rejects_browser_protocol_payload_without_contacting_host_agent(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    monkeypatch.setattr(
        ont_run_control,
        "request_host_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retired route must not contact host-agent")),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/ont/positions/MD-105428/start",
            json={"kit": "SQK-LSK114", "protocol": {"caller": "json"}, "confirm_start": True},
        )

    assert response.status_code == 410
    assert "opaque protocol receipt" in response.text


def test_intent_start_schema_rejects_raw_protocol_model_and_path_fields_before_service_call(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    monkeypatch.setattr(
        ont_run_control,
        "validate_armed_intent_start",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("schema rejection must precede intent validation")),
    )

    response = TestClient(app).post(
        "/api/ont/runs/ont-run-safe/start",
        json={
            "confirm_start": True,
            "intent_generation": 1,
            "protocol_id": "PRIVATE-PROTOCOL",
            "model_id": "PRIVATE-MODEL",
            "output_directory": "/private/output",
            "flow_cell_id": "PRIVATE-FLOWCELL",
        },
    )

    assert response.status_code == 422
    for secret in ("PRIVATE-PROTOCOL", "PRIVATE-MODEL", "/private/output", "PRIVATE-FLOWCELL"):
        assert secret not in response.text


def test_raw_handoff_descriptor_route_is_retired_without_serializing_paths(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    monkeypatch.setattr(
        ont_run_control,
        "build_plasmid_qc_handoff",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retired handoff route must not build a descriptor")),
    )

    response = TestClient(app).post(
        "/api/ont/runs/ont-run-safe/handoff/plasmid-qc",
        json={"reference_fasta": "/private/reference.fasta", "output_directory": "/private/output"},
    )

    assert response.status_code == 410
    assert "server-only" in response.text
    assert "/private/reference.fasta" not in response.text
    assert "/private/output" not in response.text


def test_public_terminal_run_projection_survives_unknown_host_status_without_raw_disclosure(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    run_id = asyncio.run(
        _seed_server_observed_run(
            state="completed",
            output_files={"fastq": ["/private/output/reads.fastq.gz"], "pod5": [], "bam": []},
        )
    )
    monkeypatch.setattr(
        ont_run_control,
        "request_host_agent",
        lambda *_args, **_kwargs: {"status": "unknown", "history": ["PRIVATE-HISTORY"], "rpc_payload": "PRIVATE-RPC"},
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/api/ont/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["output_summary"] == {"fastq": 1, "pod5": 0, "bam": 0}
    rendered = response.text
    for secret in (
        "PRIVATE-MINKNOW-RUN",
        "PRIVATE-KIT",
        "PRIVATE-PROTOCOL",
        "PRIVATE-FLOWCELL",
        "PRIVATE-HISTORY",
        "PRIVATE-RPC",
        "/private/output/reads.fastq.gz",
        "minknow_payload",
        "output_files",
    ):
        assert secret not in rendered


def test_hardware_check_public_route_is_a_fail_closed_tombstone(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def forbidden_request(method, path, payload=None, *, query=None):
        calls.append((method, path, payload))
        raise AssertionError("hardware-check tombstone must not contact the host agent")

    monkeypatch.setattr(ont_run_control, "request_host_agent", forbidden_request)

    with pytest.raises(NotImplementedError, match="supervised commissioning"):
        ont_run_control.begin_position_hardware_check("MD-105428", {"confirm_hardware_check": True})

    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    response = TestClient(app).post("/api/ont/positions/MD-105428/hardware-check", json={"confirm_hardware_check": True})

    assert response.status_code == 501
    assert response.json() == {
        "detail": "Mk1D hardware-check activation is disabled pending separately authorized supervised commissioning."
    }
    assert calls == []


@pytest.mark.asyncio
async def test_reconcile_observes_active_then_completed_once_and_materializes_terminal_artifact_manifest(
    monkeypatch, tmp_path: Path
) -> None:
    """A bounded host snapshot advances durable state once per semantic observation."""
    fastq = tmp_path / "reads.fastq"
    fastq.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    run_id = await _seed_server_observed_run(state="starting")
    snapshots = iter(
        [
            {"status": "active", "minknow_run_id": "PRIVATE-MINKNOW-RUN", "output_files": {"fastq": [], "pod5": [], "bam": []}},
            {"status": "completed", "minknow_run_id": "PRIVATE-MINKNOW-RUN", "output_files": {"fastq": [str(fastq)], "pod5": [], "bam": []}},
        ]
    )
    monkeypatch.setattr(ont_run_control, "request_host_agent", lambda *_args, **_kwargs: next(snapshots))

    active = await ont_run_control.reconcile_instrument_run(run_id)
    completed = await ont_run_control.reconcile_instrument_run(run_id)
    repeat = await ont_run_control.reconcile_instrument_run(run_id)

    assert active["state"] == "running"
    assert completed["state"] == "completed"
    assert completed["handoff_ready"] is True
    assert completed["terminal_artifact_manifest"]["sha256"]
    assert completed["terminal_artifact_manifest"]["artifact_counts"] == {"fastq": 1, "pod5": 0, "bam": 0}
    assert repeat["observed_generation"] == completed["observed_generation"]
    assert [event["event_type"] for event in repeat["events"]] == [
        "status_observed",
        "active_observed",
        "completed_observed",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["completed", "failed", "stopped"])
async def test_reconcile_records_terminal_state_without_fabricating_empty_artifact_evidence(monkeypatch, state: str) -> None:
    run_id = await _seed_server_observed_run(state="running")
    monkeypatch.setattr(
        ont_run_control,
        "request_host_agent",
        lambda *_args, **_kwargs: {"status": state, "minknow_run_id": "PRIVATE-MINKNOW-RUN", "output_files": {"fastq": [], "pod5": [], "bam": []}},
    )

    observed = await ont_run_control.reconcile_instrument_run(run_id)

    assert observed["state"] == state
    assert observed["events"][-1]["event_type"] == f"{state}_observed"
    assert observed["terminal_artifact_manifest"] is None
    assert observed["handoff_ready"] is False


@pytest.mark.asyncio
async def test_reconcile_preserves_terminal_projection_when_host_is_unavailable(monkeypatch) -> None:
    run_id = await _seed_server_observed_run(state="completed")
    monkeypatch.setattr(
        ont_run_control,
        "request_host_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("host unavailable")),
    )

    reconciled = await ont_run_control.reconcile_instrument_run(run_id)

    assert reconciled["state"] == "completed"
    assert reconciled["observed_generation"] == 1
    assert [event["event_type"] for event in reconciled["events"]] == ["status_observed"]


@pytest.mark.asyncio
async def test_terminal_manifest_freezes_first_observation_and_canonicalizes_reordered_artifacts(monkeypatch, tmp_path: Path) -> None:
    first_fastq = tmp_path / "first.fastq"
    second_fastq = tmp_path / "second.fastq"
    late_fastq = tmp_path / "late.fastq"
    for path in (first_fastq, second_fastq, late_fastq):
        path.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    run_id = await _seed_server_observed_run(state="starting")
    snapshots = iter(
        [
            {
                "status": "completed",
                "minknow_run_id": "PRIVATE-MINKNOW-RUN",
                "output_files": {"fastq": [str(second_fastq), str(first_fastq)], "pod5": [], "bam": []},
            },
            {
                "status": "completed",
                "minknow_run_id": "PRIVATE-MINKNOW-RUN",
                "output_files": {"fastq": [str(first_fastq), str(second_fastq)], "pod5": [], "bam": []},
            },
            {
                "status": "completed",
                "minknow_run_id": "PRIVATE-MINKNOW-RUN",
                "output_files": {"fastq": [str(late_fastq)], "pod5": [], "bam": []},
            },
        ]
    )
    monkeypatch.setattr(ont_run_control, "request_host_agent", lambda *_args, **_kwargs: next(snapshots))

    first = await ont_run_control.reconcile_instrument_run(run_id)
    reordered = await ont_run_control.reconcile_instrument_run(run_id)
    changed = await ont_run_control.reconcile_instrument_run(run_id)

    assert reordered["observed_generation"] == first["observed_generation"]
    assert changed["terminal_artifact_manifest"] == first["terminal_artifact_manifest"]
    assert changed["handoff_ready"] is True


def test_terminal_manifest_validation_rejects_digest_consistent_noncanonical_bindings() -> None:
    """A matching digest alone cannot make an empty or cross-run manifest authoritative."""
    manifest = {
        "schema": "bms.ont.instrument-terminal-artifacts.v1",
        "schema_version": 1,
        "run_id": "ont-run-bound",
        "minknow_run_id_sha256": ont_run_control.hashlib.sha256(b"MNK-bound").hexdigest(),
        "terminal_state": "completed",
        "observed_generation": 7,
        "artifacts": [
            {"kind": "fastq", "path": "/trusted/reads.fastq", "bytes": 12, "sha256": "a" * 64},
            {"kind": "pod5", "path": "/trusted/reads.pod5", "bytes": 24, "sha256": "c" * 64},
        ],
    }
    record = OntInstrumentRun(
        id="ont-run-bound",
        position_id="X1",
        minknow_run_id="MNK-bound",
        state="completed",
        observed_at=ont_run_control._utc_now(),
        observed_generation=7,
        output_directories={},
        output_files={},
        terminal_artifact_manifest=manifest,
        terminal_artifact_manifest_sha256=ont_run_control._canonical_digest(manifest),
    )
    assert ont_run_control._valid_terminal_manifest(record) is not None

    mutations = {
        "empty_artifacts": lambda value: value.update(artifacts=[]),
        "wrong_schema": lambda value: value.update(schema="bms.ont.instrument-terminal-artifacts.v2"),
        "wrong_schema_version": lambda value: value.update(schema_version=2),
        "wrong_run": lambda value: value.update(run_id="another-run"),
        "wrong_terminal_state": lambda value: value.update(terminal_state="failed"),
        "wrong_observation_generation": lambda value: value.update(observed_generation=8),
        "wrong_minknow_binding": lambda value: value.update(minknow_run_id_sha256="b" * 64),
        "unordered_artifacts": lambda value: value.update(artifacts=list(reversed(value["artifacts"]))),
    }
    for label, mutate in mutations.items():
        candidate = copy.deepcopy(manifest)
        mutate(candidate)
        record.terminal_artifact_manifest = candidate
        record.terminal_artifact_manifest_sha256 = ont_run_control._canonical_digest(candidate)
        assert ont_run_control._valid_terminal_manifest(record) is None, label


@pytest.mark.asyncio
async def test_reserved_handoff_attack_does_not_burn_a_durable_server_issued_molbio_receipt(monkeypatch, tmp_path: Path) -> None:
    """Route validation must reject browser provenance before receipt consumption or job creation."""
    from database import Job, MolBioNgsReceipt
    from schemas import JobResponse, JobStatus
    from services import molbio_ngs_receipts

    monkeypatch.setattr(molbio_ngs_receipts, "get_inputs_dir", lambda: tmp_path / "inputs")
    revision = SimpleNamespace(
        id="revision-safe",
        content_sha256=molbio_ngs_receipts.sha256_text("ACGT"),
        snapshot={"sequence": "ACGT", "sequence_type": "dna"},
    )

    async with ont_run_control.async_session() as session:
        receipt = await molbio_ngs_receipts.issue_molbio_ngs_receipt(session, sequence_id="sequence-safe", revision=revision)
        receipt_id = receipt.id
        await session.commit()

    async def durable_session_override():
        async with ont_run_control.async_session() as session:
            yield session

    created_jobs: list[str] = []

    async def fake_handoff(_run_id: str, payload: dict) -> dict:
        assert payload["reference_fasta"].endswith("expected_reference.fasta")
        return {"params": {"fastq_path": "/trusted/reads.fastq", "reference_fasta": payload["reference_fasta"]}}

    def fake_job_create(*_args, **_kwargs):
        return SimpleNamespace(params={})

    async def fake_create_pipeline_job(_job, _background_tasks, session, *_args, **_kwargs) -> JobResponse:
        created_jobs.append("job-after-rejected-attack")
        session.add(Job(
            id=created_jobs[-1], name="ONT plasmid QC", status="queued",
            model_id="nanopore", mode="plasmid_qc", params={},
            output_dir="/tmp/job-after-rejected-attack",
        ))
        await session.flush()
        return JobResponse(
            id=created_jobs[-1],
            name="ONT plasmid QC",
            status=JobStatus.QUEUED,
            model_id="nanopore",
            mode="plasmid_qc",
            params={},
            created_at=ont_run_control._utc_now(),
            output_dir="/tmp/job-after-rejected-attack",
            design_count=0,
        )

    monkeypatch.setattr(ont_runs.ont_run_control, "build_plasmid_qc_handoff", fake_handoff)
    monkeypatch.setattr(ont_runs, "_job_create_for_ont_submit", fake_job_create)
    monkeypatch.setattr(ont_runs, "_create_pipeline_job", fake_create_pipeline_job)
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    app.dependency_overrides[ont_runs.get_session] = durable_session_override

    with TestClient(app) as client:
        attack = client.post(
            "/api/ont/runs/ont-run-safe/handoff/plasmid-qc/submit",
            json={"molbio_ngs_receipt_id": receipt_id, "params": {"output_dir": "/browser/chosen/output"}},
        )
        assert attack.status_code == 422
        assert created_jobs == []
        assert "/browser/chosen/output" not in attack.text

        async with ont_run_control.async_session() as session:
            persisted = await session.get(MolBioNgsReceipt, receipt_id)
            assert persisted is not None
            assert persisted.consumed_at is None
            assert persisted.consumed_job_id is None

        usable_after_attack = client.post(
            "/api/ont/runs/ont-run-safe/handoff/plasmid-qc/submit",
            json={"molbio_ngs_receipt_id": receipt_id, "params": {"igv_report_max_sites": 7}},
        )

    assert usable_after_attack.status_code == 201, usable_after_attack.text
    assert created_jobs == ["job-after-rejected-attack"]
    async with ont_run_control.async_session() as session:
        persisted = await session.get(MolBioNgsReceipt, receipt_id)
        assert persisted is not None
        assert persisted.consumed_at is not None
        assert persisted.consumed_job_id == "job-after-rejected-attack"


@pytest.mark.asyncio
@pytest.mark.parametrize("host_payload", [
    {"status": "PRIVATE-UNKNOWN-STATUS", "output_files": {"fastq": ["/private/malformed.fastq"], "pod5": [], "bam": []}},
    {"status": "unknown", "error": "PRIVATE-HOST-ERROR /private/output", "output_files": {"fastq": ["/private/unknown.fastq"], "pod5": [], "bam": []}},
])
async def test_terminal_reconcile_malformed_or_unknown_host_status_returns_safe_durable_projection_without_mutation(
    monkeypatch, tmp_path: Path, host_payload: dict
) -> None:
    fastq = tmp_path / "reads.fastq"
    fastq.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    run_id = await _seed_server_observed_run(state="starting")
    monkeypatch.setattr(
        ont_run_control,
        "request_host_agent",
        lambda *_args, **_kwargs: {"status": "completed", "minknow_run_id": "PRIVATE-MINKNOW-RUN", "output_files": {"fastq": [str(fastq)], "pod5": [], "bam": []}},
    )
    terminal = await ont_run_control.reconcile_instrument_run(run_id)
    monkeypatch.setattr(ont_run_control, "request_host_agent", lambda *_args, **_kwargs: host_payload)

    safe = await ont_run_control.reconcile_instrument_run(run_id)

    assert safe["state"] == "completed"
    assert safe["observed_generation"] == terminal["observed_generation"]
    assert safe["terminal_artifact_manifest"] == terminal["terminal_artifact_manifest"]
    rendered = str(safe)
    for secret in ("PRIVATE-UNKNOWN-STATUS", "PRIVATE-HOST-ERROR", "/private/malformed.fastq", "/private/unknown.fastq", "/private/output"):
        assert secret not in rendered


@pytest.mark.asyncio
async def test_handoff_ready_ignores_prehashed_output_files_without_an_immutable_manifest(tmp_path: Path) -> None:
    fastq = tmp_path / "unverified.fastq"
    fastq.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    run_id = await _seed_server_observed_run(
        state="completed", output_files={"fastq": [str(fastq)], "pod5": [], "bam": []}
    )

    projection = await ont_run_control.get_instrument_run(run_id)

    assert projection is not None
    assert projection["terminal_artifact_manifest"] is None
    assert projection["handoff_ready"] is False
