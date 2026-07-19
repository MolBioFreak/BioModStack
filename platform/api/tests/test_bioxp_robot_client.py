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

    asyncio.run(client.close())
