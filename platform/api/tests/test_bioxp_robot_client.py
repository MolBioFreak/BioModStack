from __future__ import annotations

import asyncio
from ipaddress import ip_address

import httpx

from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import ValidatedBioXpTarget


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"runtime_ready": True}, request=request)

    async def aclose(self) -> None:
        self.closed = True


class SnapshotRefreshTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, stale: bool, age_s: float = 0.0) -> None:
        self.requests: list[httpx.Request] = []
        self.stale = stale
        self.age_s = age_s

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"ok": True}, request=request)
        if self.stale and len(self.requests) == 1:
            return httpx.Response(
                200,
                json={
                    "available": False,
                    "cache_state": "stale",
                    "freshness": {"state": "stale", "age_s": 31.0},
                    "capabilities": ["collect_hardware_snapshot"],
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "available": True,
                "cache_state": "fresh",
                "freshness": {"state": "fresh", "age_s": self.age_s, "fresh_for_s": 30.0},
                "runtime_available": True,
                "hardware_connected": True,
                "capabilities": ["collect_hardware_snapshot"],
            },
            request=request,
        )


def test_robot_transport_connects_to_validated_address_without_second_dns_lookup() -> None:
    target = ValidatedBioXpTarget(
        api_url="https://robot:8123",
        scheme="https",
        hostname="robot",
        port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),),
    )
    transport = RecordingTransport()
    client = BioXpRobotClient(target, transport=transport)

    assert asyncio.run(client.probe()) == {"runtime_ready": True}
    request = transport.requests[0]
    assert request.url.host == "100.64.0.10"
    assert request.headers["host"] == "robot:8123"
    assert request.extensions["sni_hostname"] == "robot"

    asyncio.run(client.close())
    assert transport.closed is True


def test_robot_transport_rejects_unresolved_targets() -> None:
    target = ValidatedBioXpTarget(
        api_url="http://robot:8123",
        scheme="http",
        hostname="robot",
        port=8123,
    )

    try:
        BioXpRobotClient(target, transport=RecordingTransport())
    except ValueError as exc:
        assert "validated address" in str(exc)
    else:  # pragma: no cover - regression assertion
        raise AssertionError("unresolved target must fail closed")


def test_robot_client_routes_only_current_compact_commissioning_contracts() -> None:
    target = ValidatedBioXpTarget(
        api_url="http://robot:8123",
        scheme="http",
        hostname="robot",
        port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),),
    )
    client = BioXpRobotClient(target, transport=RecordingTransport())

    assert client.routes["collect_hardware_snapshot"][:2] == ("POST", "/hardware/snapshot/collect")
    assert client.routes["construct_pipettes"][:2] == ("POST", "/oem/startup/constructor_pipettes")
    assert client.routes["initialize_without_motion"][:2] == ("POST", "/oem/startup/initialize_without_motion")
    assert client.routes["run_initial_check"][:2] == ("POST", "/oem/initial_check")
    assert client.routes["collect_hardware_snapshot"][2] >= 195.0

    asyncio.run(client.close())


def test_probe_renews_advertised_stale_hardware_evidence_through_query_only_collector() -> None:
    target = ValidatedBioXpTarget(
        api_url="http://robot:8123",
        scheme="http",
        hostname="robot",
        port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),),
    )
    transport = SnapshotRefreshTransport(stale=True)
    client = BioXpRobotClient(target, transport=transport)

    payload = asyncio.run(client.probe())

    assert payload["cache_state"] == "fresh"
    assert [(request.method, request.url.path) for request in transport.requests] == [
        ("GET", "/status"),
        ("POST", "/hardware/snapshot/collect"),
        ("GET", "/status"),
    ]
    asyncio.run(client.close())


def test_probe_does_not_collect_when_advertised_hardware_evidence_is_fresh() -> None:
    target = ValidatedBioXpTarget(
        api_url="http://robot:8123",
        scheme="http",
        hostname="robot",
        port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),),
    )
    transport = SnapshotRefreshTransport(stale=False)
    client = BioXpRobotClient(target, transport=transport)

    payload = asyncio.run(client.probe())

    assert payload["cache_state"] == "fresh"
    assert [(request.method, request.url.path) for request in transport.requests] == [("GET", "/status")]
    asyncio.run(client.close())


def test_probe_renews_fresh_hardware_evidence_before_half_life_expires() -> None:
    target = ValidatedBioXpTarget(
        api_url="http://robot:8123",
        scheme="http",
        hostname="robot",
        port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),),
    )
    transport = SnapshotRefreshTransport(stale=False, age_s=15.0)
    client = BioXpRobotClient(target, transport=transport)

    asyncio.run(client.probe())

    assert [(request.method, request.url.path) for request in transport.requests] == [
        ("GET", "/status"),
        ("POST", "/hardware/snapshot/collect"),
        ("GET", "/status"),
    ]
    asyncio.run(client.close())
