from __future__ import annotations

import inspect
from typing import Any, cast

from routers import bioxp


# R5 reviewed current surface: v2 receipts, retained evidence and basic camera
# are supported; retired generic commands/proxy dispatch must stay absent.
EXPECTED = {
    ('DELETE', '/profile'),
    ('GET', '/camera/frame/latest'),
    ('GET', '/camera/mjpeg'),
    ('GET', '/camera/status'),
    ('GET', '/camera/stream/state'),
    ('GET', '/jobs'),
    ('GET', '/jobs/{job_id}'),
    ('GET', '/oem-full-lifecycle/contract'),
    ('GET', '/oem-full-lifecycle/runs/{run_id}'),
    ('GET', '/oem-full-lifecycle/runs/{run_id}/ledger'),
    ('GET', '/operator-controls/audit-health'),
    ('GET', '/operator-controls/catalog'),
    ('GET', '/operator-controls/dashboard'),
    ('GET', '/operator-controls/history'),
    ('GET', '/operator-controls/pipettes/application/status'),
    ('GET', '/operator-controls/pipettes/requests'),
    ('GET', '/operator-controls/receipts/{command_id}'),
    ('GET', '/operator-controls/reports/commands'),
    ('GET', '/operator-controls/reports/commands/{command_id}'),
    ('GET', '/operator-controls/reports/commands/{command_id}/evidence'),
    ('GET', '/operator-controls/reports/commands/{command_id}/transitions'),
    ('GET', '/operator-controls/reports/events'),
    ('GET', '/operator-controls/reports/events/{event_id}'),
    ('GET', '/operator-controls/reports/exports'),
    ('GET', '/operator-controls/reports/exports/{export_id}'),
    ('GET', '/operator-controls/reports/exports/{export_id}/download'),
    ('GET', '/operator-controls/reports/pipette'),
    ('GET', '/operator-controls/reports/pipette/{pipette_operation_id}'),
    ('GET', '/operator-controls/reports/pipette/{pipette_operation_id}/channels'),
    ('GET', '/operator-controls/reports/pipette/{pipette_operation_id}/exchanges'),
    ('GET', '/operator-controls/reports/pressure-streams'),
    ('GET', '/operator-controls/reports/pressure-streams/{stream_session_id}'),
    ('GET', '/operator-controls/reports/pressure-streams/{stream_session_id}/samples'),
    ('GET', '/operator-controls/reports/summary'),
    ('GET', '/operator-controls/v2/catalog'),
    ('GET', '/operator-controls/v2/commands/{command_id}'),
    ('GET', '/operator-controls/v2/dashboard'),
    ('GET', '/operator-controls/v2/history'),
    ('GET', '/operator-controls/v2/methods/{method_id}'),
    ('GET', '/operator-controls/v2/receipts/{command_id}'),
    ('GET', '/profile'),
    ('GET', '/status'),
    ('POST', '/camera/snapshot'),
    ('POST', '/camera/stream/start'),
    ('POST', '/camera/stream/stop'),
    ('POST', '/connection/connect'),
    ('POST', '/connection/disconnect'),
    ('POST', '/connection/probe'),
    ('POST', '/oem-full-lifecycle/runs'),
    ('POST', '/oem-full-lifecycle/runs/{run_id}/cancel'),
    ('POST', '/operator-controls/actions/{action_id}'),
    ('POST', '/operator-controls/actions/{action_id}/admission'),
    ('POST', '/operator-controls/pipettes/application/plan'),
    ('POST', '/operator-controls/pipettes/readback'),
    ('POST', '/operator-controls/receipts/{command_id}/assessment'),
    ('POST', '/operator-controls/reports/exports'),
    ('POST', '/operator-controls/v2/actions/{action_id}'),
    ('POST', '/operator-controls/v2/interrupts/{action_id}'),
    ('POST', '/operator-controls/v2/methods'),
    ('POST', '/protocols/compile'),
    ('POST', '/protocols/submit'),
    ('PUT', '/profile'),
    ('PUT', '/settings/freshness'),
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
    assert len(_inventory()) == 63


def test_every_non_read_route_carries_the_global_containment_dependency() -> None:
    for route in _effective_routes():
        if set(route.methods or ()) <= {"GET", "HEAD", "OPTIONS"}:
            continue
        if route.path in {
            "/operator-controls/pipettes/readback",  # query-only, typed/idempotent
            "/operator-controls/pipettes/application/plan",  # no-motion planner
            "/operator-controls/reports/exports",  # retained evidence artifact
        }:
            continue
        calls = {dependency.dependency for dependency in route.dependencies}
        assert bioxp.require_bioxp_mutation_access in calls, route.path


def test_only_bounded_resource_identifiers_are_dynamic() -> None:
    dynamic = {path for _, path in _inventory() if "{" in path}
    assert dynamic == {path for _, path in EXPECTED if "{" in path}
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
        "/commands",
        "/commands/{command_id}",
        "/emergency-stop",
        "/protocol/execute",
        "/oem/runtime/commands/{command_name}",
    }
    assert forbidden.isdisjoint(paths)


def test_snapshot_completion_refreshes_status_and_lifecycle_reasons_are_operator_first() -> None:
    from routers.bioxp import connection

    # The generic command router is retired. The supported public probe remains
    # status-only; collecting hardware must not be smuggled into connection UI.
    probe_source = inspect.getsource(connection.probe)
    assert "await runtime.connection.probe_status_only()" in probe_source
    assert "await runtime.connection.probe()" not in probe_source
