from __future__ import annotations

from fastapi.routing import APIRoute

from routers import bioxp


EXPECTED = {
    ("GET", "/profile"),
    ("PUT", "/profile"),
    ("DELETE", "/profile"),
    ("GET", "/status"),
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
}


def _inventory() -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = set()
    for route in bioxp.router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or ():
            if method not in {"HEAD", "OPTIONS"}:
                rows.add((method, route.path))
    return rows


def test_compact_api_inventory_is_exact_and_bounded() -> None:
    assert _inventory() == EXPECTED
    assert len(_inventory()) <= 18


def test_every_non_read_route_carries_the_global_containment_dependency() -> None:
    for route in bioxp.router.routes:
        if not isinstance(route, APIRoute):
            continue
        if set(route.methods or ()) <= {"GET", "HEAD", "OPTIONS"}:
            continue
        calls = {dependency.call for dependency in route.dependant.dependencies}
        assert bioxp.require_bioxp_mutation_access in calls, route.path


def test_only_bounded_resource_identifiers_are_dynamic() -> None:
    dynamic = {path for _, path in _inventory() if "{" in path}
    assert dynamic == {"/jobs/{job_id}", "/commands/{command_id}"}
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
