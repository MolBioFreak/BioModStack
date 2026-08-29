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
                "filter": {},
                "filter_sha256": "0" * 64,
                "snapshot": SNAPSHOT,
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
    assert export.status_code == 200
    assert download.status_code == 200
    assert download.content == b'{"ok":true}\n'
    assert download.headers["x-content-sha256"] == "2" * 64
    assert created.status_code == 200
    assert [call["route_name"] for call in connection.calls] == [
        "operator_report_command_detail",
        "operator_report_export_detail",
        "operator_report_export_download",
        "operator_report_export_create",
    ]
    assert all(call["expected_generation"] == 9 for call in connection.calls)
