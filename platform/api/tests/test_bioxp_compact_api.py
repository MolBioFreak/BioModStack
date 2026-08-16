from __future__ import annotations

import inspect
from typing import Any, cast

from routers import bioxp


EXPECTED = {
    ("GET", "/profile"),
    ("PUT", "/profile"),
    ("DELETE", "/profile"),
    ("GET", "/status"),
    ("GET", "/camera/status"),
    ("GET", "/camera/frame/latest"),
    ("POST", "/camera/snapshot"),
    ("POST", "/connection/connect"),
    ("POST", "/connection/disconnect"),
    ("POST", "/connection/probe"),
    ("GET", "/logs"),
    ("POST", "/protocols/compile"),
    ("POST", "/protocols/submit"),
    ("GET", "/jobs"),
    ("GET", "/jobs/{job_id}"),
    ("POST", "/commands"),
    ("GET", "/commands"),
    ("GET", "/commands/{command_id}"),
    ("POST", "/emergency-stop"),
    ("GET", "/oem-full-lifecycle/contract"),
    ("POST", "/oem-full-lifecycle/runs"),
    ("GET", "/oem-full-lifecycle/runs/{run_id}"),
    ("GET", "/oem-full-lifecycle/runs/{run_id}/ledger"),
    ("POST", "/oem-full-lifecycle/runs/{run_id}/cancel"),
    ("GET", "/operator-controls/catalog"),
    ("GET", "/operator-controls/dashboard"),
    ("GET", "/operator-controls/history"),
    ("GET", "/operator-controls/receipts/{command_id}"),
    ("POST", "/operator-controls/actions/{action_id}/admission"),
    ("POST", "/operator-controls/actions/{action_id}"),
    ("POST", "/operator-controls/receipts/{command_id}/assessment"),
}


def _effective_routes() -> tuple[Any, ...]:
    rows: list[Any] = []
    for included_route in bioxp.router.routes:
        candidates = getattr(included_route, "effective_candidates", None)
        if callable(candidates):
            rows.extend(cast(tuple[Any, ...], candidates()))
        else:
            rows.append(cast(Any, included_route))
    return tuple(rows)


def _inventory() -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = set()
    for route in _effective_routes():
        for method in route.methods or ():
            if method not in {"HEAD", "OPTIONS"}:
                rows.add((method, route.path))
    return rows


def test_compact_api_inventory_is_exact_and_bounded() -> None:
    assert _inventory() == EXPECTED
    assert len(_inventory()) == 31


def test_every_non_read_route_carries_the_global_containment_dependency() -> None:
    for route in _effective_routes():
        if set(route.methods or ()) <= {"GET", "HEAD", "OPTIONS"}:
            continue
        calls = {dependency.dependency for dependency in route.dependencies}
        assert bioxp.require_bioxp_mutation_access in calls, route.path


def test_only_bounded_resource_identifiers_are_dynamic() -> None:
    dynamic = {path for _, path in _inventory() if "{" in path}
    assert dynamic == {
        "/jobs/{job_id}",
        "/commands/{command_id}",
        "/oem-full-lifecycle/runs/{run_id}",
        "/oem-full-lifecycle/runs/{run_id}/ledger",
        "/oem-full-lifecycle/runs/{run_id}/cancel",
        "/operator-controls/receipts/{command_id}",
        "/operator-controls/actions/{action_id}",
        "/operator-controls/actions/{action_id}/admission",
        "/operator-controls/receipts/{command_id}/assessment",
    }
    assert all("{path" not in path and "{command_name" not in path for _, path in _inventory())


def test_retired_proxy_lifecycle_and_commissioning_routes_are_absent() -> None:
    paths = {path for _, path in _inventory()}
    forbidden = {
        "/proxy",
        "/linkage",
        "/daemon/start",
        "/interlink/runtime-reset",
        "/interlink/robot-reboot",
        "/diagnostics/usb-sniff/start",
        "/motion/axis/relative",
        "/liquid/aspirate",
        "/thermal/set_temp",
        "/camera/mjpeg",
        "/protocol/execute",
        "/oem/runtime/commands/{command_name}",
    }
    assert forbidden.isdisjoint(paths)


def test_snapshot_completion_refreshes_status_and_lifecycle_reasons_are_operator_first() -> None:
    from routers.bioxp import commands, connection

    command_source = inspect.getsource(commands.execute_command)
    assert "result.remote_acknowledged" in command_source
    assert 'request.command != "stop_axis_diagnostic"' in command_source
    assert "await runtime.connection.probe_status_only()" in command_source
    assert "await runtime.connection.probe()" not in command_source

    status_source = inspect.getsource(connection.get_status)
    assert status_source.index("lifecycle_stage_reasons") < status_source.index("requires_hardware_ready")
    assert status_source.index("lifecycle_stage_reasons") < status_source.index("required_capability")

    probe_source = inspect.getsource(connection.probe)
    assert "await runtime.connection.probe_status_only()" in probe_source
    assert "await runtime.connection.probe()" not in probe_source
