from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.bioxp import operator_controls
from routers.bioxp.dependencies import get_bioxp_runtime
from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES


SNAPSHOT = {
    "database_identity": "robot_authoritative_sqlite",
    "schema_version": 2,
    "database_path_exposed": False,
    "identity_version": 2,
}
FILTERS = {
    "start": None,
    "end": None,
    "status": "completed",
    "operation": None,
    "action": None,
    "channel": None,
    "event_kind": None,
    "entrypoint": None, "caller_class": None, "control_class": None,
    "protocol_job_id": None, "protocol_action_id": None,
    "lifecycle_stage_id": None, "lifecycle_attempt_id": None,
    "outcome": None, "event_source": None, "pressure_stream_id": None,
    "delivery_verified": None, "controller_acknowledged": None,
    "completion_verified": None, "hardware_postcondition_verified": None,
    "physical_effect_verified": None, "evidence_state": None,
    "command_id": None, "pipette_operation_id": None,
    "connection_generation": None, "ownership_generation": None,
    "limit": 25,
}
COMMAND = {
    "sequence": 1,
    "command_id": "cmd-1",
    "idempotency_key": "idem-1",
    "operation": "operator_action",
    "command_kind": "physical_capable",
    "entrypoint_id": "operator.action",
    "caller_class": "bms",
    "control_class": "pipette",
    "action_id": "pipette.aspirate",
    "status": "completed",
    "outcome": "completed",
    "failure_code": None,
    "ownership_generation": 7,
    "connection_generation": 9,
    "started_at": 1.0,
    "admitted_at": 1.1,
    "dispatched_at": 1.2,
    "finished_at": 1.3,
    "duration_ms": 300,
    "delivery_verified": True,
    "controller_acknowledged": True,
    "completion_verified": True,
    "hardware_precondition_verified": False,
    "hardware_postcondition_verified": False,
    "physical_effect_verified": False,
    "evidence_state": "compact",
}


# Offline fixture for operator_reports.py export publication/metadata contract.
# This is deliberately unverified release identity, never deployed evidence.
RELEASE = {
    "schema": "bioxp.runtime.release_identity.v1", "status": "unverified",
    "verified": False, "reason_code": "canonical_release_packet_absent", "release_id": None,
    "source": dict(commit=None, tree=None, mode=None, manifest_sha256=None, aggregate_sha256=None),
    "image": dict(id=None, inspection_receipt_sha256=None),
    "deployment": dict(receipt_id=None, installed_at=None, receipt_sha256=None),
    "binding": dict(service_unit=None, unit_sha256=None, launcher_sha256=None,
        configuration_sha256=None, oem_lock_sha256=None, udocker_sha256=None,
        udocker_tree_sha256=None, declared_listener=None, observed_listener=None),
}
EXPORT_RECEIPT = {
    "receipt_schema": "bioxp.operator_report_export_receipt.v1",
    "publisher_identity": "bioxp.operator_reports", "export_id": "export-1",
    "evidence_artifact_id": "report-export:export-1", "created_at": 1.0,
    "retention_deadline": 1000.0, "normalized_filters": FILTERS,
    "filter_sha256": "0" * 64, "row_count": 1,
    "database_incarnation_id": "offline-test-database",
    "release_identity": RELEASE,
    "schema_identity": {"database_identity": "robot_authoritative_sqlite",
        "schema_version": 5, "identity_version": 2, "release_identity": RELEASE},
    "source_high_waters": dict(operator_commands=1, operator_transitions=0,
        pipette_operations=0, pipette_channel_observations=0, pipette_transport_exchanges=0,
        runtime_events=0, pipette_pressure_streams=0, pipette_pressure_chunks=0,
        runtime_evidence_objects=1, runtime_evidence_links=1, runtime_evidence_events=1,
        operator_plane_command_versions=0, operator_plane_pipette_versions=0,
        operator_plane_pressure_stream_versions=0, operator_plane_evidence_versions=0),
    "artifact": {"format": "json", "sha256": "1" * 64, "byte_count": 10, "relpath": None},
    "evidence_state": "active", "legal_hold": False, "evidence_available": True,
    "public_download_available": True,
}


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def snapshot(self):
        return SimpleNamespace(generation=9)

    async def request_active_query(self, route_name, *, expected_generation, require_fresh, params=None, path_params=None, json_data=None):
        self.calls.append({
            "route_name": route_name,
            "expected_generation": expected_generation,
            "require_fresh": require_fresh,
            "params": params,
            "path_params": path_params,
            "json_data": json_data,
        })
        if route_name == "operator_report_commands":
            return {
                "filters": {**FILTERS, "limit": int((params or {}).get("limit", 25))},
                "snapshot": {**SNAPSHOT, "high_water_sequence": 1},
                "returned_count": 1,
                "filtered_total": 1,
                "has_more": False,
                "next_cursor": None,
                "commands": [COMMAND],
            }
        if route_name == "operator_report_command_detail":
            return {**COMMAND, "requested_inputs": {}, "effective_inputs": {}, "source_identity": {}, "transitions": [], "evidence": [], "pipette": None}
        if route_name == "operator_report_export_detail":
            return {
                "export_id": "export-1",
                "format": "json",
                "filter": FILTERS,
                "filter_sha256": "0" * 64,
                "snapshot": EXPORT_RECEIPT,
                "receipt": EXPORT_RECEIPT,
                "release_identity": RELEASE,
                "publication_state": "published",
                "evidence_state": "active", "legal_hold": False, "evidence_available": True,
                "row_count": 1,
                "sha256": "1" * 64,
                "byte_count": 10,
                "status": "completed",
                "created_at": 1.0,
                "completed_at": 1.1,
                "download": "/operator/reports/exports/export-1/download",
            }
        raise AssertionError(route_name)

    async def request_active(self, route_name, *, expected_generation, require_fresh, params=None, path_params=None, json_data=None):
        self.calls.append({
            "route_name": route_name,
            "expected_generation": expected_generation,
            "require_fresh": require_fresh,
            "params": params,
            "path_params": path_params,
            "json_data": json_data,
        })
        assert route_name == "operator_report_export_create"
        return {
            "export_id": "export-1",
            "evidence_artifact_id": "report-export:export-1",
            "release_identity": RELEASE,
            "status": "completed",
            "format": "json",
            "row_count": 1,
            "sha256": "1" * 64,
            "byte_count": 10,
            "download": "/operator/reports/exports/export-1/download",
        }

    async def request_active_bytes(self, route_name, *, expected_generation, require_fresh, params=None, path_params=None, json_data=None, max_bytes=0):
        self.calls.append({
            "route_name": route_name,
            "expected_generation": expected_generation,
            "require_fresh": require_fresh,
            "params": params,
            "path_params": path_params,
            "json_data": json_data,
            "max_bytes": max_bytes,
        })
        return SimpleNamespace(content=b'{"ok":true}\n', content_type="application/json", sha256="2" * 64)


def app_for(connection: FakeConnection) -> FastAPI:
    app = FastAPI()
    app.include_router(operator_controls.router)
    app.dependency_overrides[get_bioxp_runtime] = lambda: SimpleNamespace(connection=connection)
    return app


def test_report_routes_are_generation_bound_read_only_relays() -> None:
    assert DEFAULT_ROBOT_ROUTES["operator_report_summary"] == (
        "GET",
        "/operator/reports/summary",
        10.0,
    )
    assert DEFAULT_ROBOT_ROUTES["operator_report_export_download"] == (
        "GET",
        "/operator/reports/exports/{export_id}/download",
        30.0,
    )
    connection = FakeConnection()
    client = TestClient(app_for(connection))

    response = client.get(
        "/operator-controls/reports/commands",
        params={"status": "completed", "limit": 25, "cursor": "cursor-1"},
    )

    assert response.status_code == 200
    assert response.json()["commands"][0]["command_id"] == "cmd-1"
    assert connection.calls == [{
        "route_name": "operator_report_commands",
        "expected_generation": 9,
        "require_fresh": True,
        "params": {"status": "completed", "limit": 25, "cursor": "cursor-1"},
        "path_params": None,
        "json_data": None,
    }]


def test_report_detail_export_metadata_and_download_use_robot_contract() -> None:
    connection = FakeConnection()
    client = TestClient(app_for(connection))

    detail = client.get("/operator-controls/reports/commands/cmd-1")
    export = client.get("/operator-controls/reports/exports/export-1")
    download = client.get("/operator-controls/reports/exports/export-1/download")
    created = client.post("/operator-controls/reports/exports", json={"format": "json", "limit": 10})

    assert detail.status_code == 200
    assert detail.json()["command_id"] == "cmd-1"
    assert export.status_code == 200, export.text
    assert export.json()["receipt"] == EXPORT_RECEIPT
    assert export.json()["release_identity"] == RELEASE
    assert export.json()["download"] == "/api/bioxp/operator-controls/reports/exports/export-1/download"
    assert download.status_code == 200
    assert download.content == b'{"ok":true}\n'
    assert download.headers["x-content-sha256"] == "2" * 64
    assert created.status_code == 200
    assert [call["route_name"] for call in connection.calls] == [
        "operator_report_command_detail",
        "operator_report_commands",  # legacy detail requires indexed snapshot context
        "operator_report_export_detail",
        "operator_report_export_download",
        "operator_report_export_create",
    ]
    assert all(call["expected_generation"] == 9 for call in connection.calls)


def test_export_metadata_rejects_missing_receipt_and_unknown_evidence_fields():
    class InvalidConnection(FakeConnection):
        async def request_active_query(self, *args, **kwargs):
            payload = await super().request_active_query(*args, **kwargs)
            if self.missing:
                del payload["receipt"]
            else:
                payload["receipt"] = {**payload["receipt"], "invented_authority": True}
            return payload
    for missing in (True, False):
        connection = InvalidConnection()
        connection.missing = missing
        response = TestClient(app_for(connection)).get("/operator-controls/reports/exports/export-1")
        assert response.status_code == 502
