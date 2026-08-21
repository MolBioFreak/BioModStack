from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.bioxp import operator_controls
from routers.bioxp.dependencies import get_bioxp_runtime
from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.payload = {
            "schema_version": "bioxp.operator_report.v1",
            "resource": "commands",
            "rows": [],
            "summary": {"count": 0},
            "next_cursor": None,
        }

    def snapshot(self):
        return SimpleNamespace(generation=9)

    async def request_active_query(self, route_name, *, expected_generation, require_fresh, params=None, path_params=None):
        self.calls.append({
            "route_name": route_name,
            "expected_generation": expected_generation,
            "require_fresh": require_fresh,
            "params": params,
            "path_params": path_params,
        })
        return self.payload


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
    connection = FakeConnection()
    client = TestClient(app_for(connection))

    response = client.get(
        "/operator-controls/reports/commands",
        params={"status": "completed", "limit": 25, "cursor": "cursor-1"},
    )

    assert response.status_code == 200
    assert response.json()["resource"] == "commands"
    assert connection.calls == [{
        "route_name": "operator_report_commands",
        "expected_generation": 9,
        "require_fresh": True,
        "params": {"status": "completed", "limit": 25, "cursor": "cursor-1"},
        "path_params": None,
    }]


def test_report_detail_and_export_metadata_use_robot_report_contract() -> None:
    connection = FakeConnection()
    client = TestClient(app_for(connection))

    detail = client.get("/operator-controls/reports/commands/cmd-1")
    export = client.get("/operator-controls/reports/exports/export-1")

    assert detail.status_code == 200
    assert export.status_code == 200
    assert [call["route_name"] for call in connection.calls] == [
        "operator_report_command_detail",
        "operator_report_export_detail",
    ]
    assert all(call["expected_generation"] == 9 for call in connection.calls)
