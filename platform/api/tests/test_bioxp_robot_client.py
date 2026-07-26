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

    assert client.routes["activate_usb_for_service"][:2] == ("POST", "/oem/runtime/activate_service")
    assert client.routes["activate_usb_for_service"][2] >= 90.0
    assert client.routes["collect_hardware_snapshot"][:2] == ("POST", "/hardware/snapshot/collect")
    assert client.routes["initialize_oem_environment"][:2] == ("POST", "/oem/startup/initialize_environment")
    assert client.routes["initialize_oem_environment"][2] >= 460.0
    assert client.routes["collect_hardware_snapshot"][2] >= 195.0

    asyncio.run(client.close())


def test_dynamic_full_lifecycle_run_path_is_percent_encoded_and_template_bound() -> None:
    target = ValidatedBioXpTarget(
        api_url="http://robot:8123",
        scheme="http",
        hostname="robot",
        port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),),
    )
    transport = RecordingTransport()
    client = BioXpRobotClient(target, transport=transport)

    asyncio.run(client.request("get_oem_full_lifecycle_run", path_params={"run_id": "run/../../status"}))
    assert transport.requests[0].url.path == "/oem/runtime/movement-runs/run/../../status"
    assert transport.requests[0].url.raw_path == b"/oem/runtime/movement-runs/run%2F..%2F..%2Fstatus"

    try:
        asyncio.run(client.request("get_oem_full_lifecycle_run"))
    except Exception as exc:
        assert "route parameters" in str(exc)
    else:
        raise AssertionError("missing route parameter must fail closed")
    asyncio.run(client.close())


def test_lifecycle_mutation_routes_use_server_side_token_file_and_gets_do_not(tmp_path) -> None:
    target = ValidatedBioXpTarget(
        api_url="http://robot:8123",
        scheme="http",
        hostname="robot",
        port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),),
    )
    token_path = tmp_path / "oem-runtime.token"
    token_path.write_text("test-only-bms-oem-token-0000000000000000", encoding="utf-8")
    token_path.chmod(0o600)
    transport = RecordingTransport()
    client = BioXpRobotClient(target, transport=transport, oem_lifecycle_token_file=token_path)

    asyncio.run(client.request("oem_full_lifecycle_contract"))
    asyncio.run(client.request("plan_oem_full_lifecycle", json_data={"mode": "dry_run"}))
    asyncio.run(client.request("cancel_oem_full_lifecycle_run", path_params={"run_id": "run-12345678"}))

    assert "X-BioXP-OEM-Token" not in transport.requests[0].headers
    assert transport.requests[1].headers["X-BioXP-OEM-Token"] == "test-only-bms-oem-token-0000000000000000"
    assert transport.requests[2].headers["X-BioXP-OEM-Token"] == "test-only-bms-oem-token-0000000000000000"
    asyncio.run(client.close())


def test_lifecycle_mutation_route_fails_before_http_without_private_token_file(monkeypatch) -> None:
    monkeypatch.delenv("BMS_BIOXP_OEM_RUNTIME_TOKEN_FILE", raising=False)
    target = ValidatedBioXpTarget(
        api_url="http://robot:8123",
        scheme="http",
        hostname="robot",
        port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),),
    )
    transport = RecordingTransport()
    client = BioXpRobotClient(target, transport=transport)

    try:
        asyncio.run(client.request("plan_oem_full_lifecycle", json_data={"mode": "dry_run"}))
    except Exception as exc:
        assert "token file is not configured" in str(exc)
    else:
        raise AssertionError("missing lifecycle token file must fail closed")
    assert transport.requests == []
    asyncio.run(client.close())


def test_probe_never_posts_hidden_snapshot_when_hardware_evidence_is_stale() -> None:
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

    assert payload["cache_state"] == "stale"
    assert [(request.method, request.url.path) for request in transport.requests] == [
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


def test_probe_never_posts_hidden_snapshot_at_freshness_half_life() -> None:
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
    ]
    asyncio.run(client.close())
