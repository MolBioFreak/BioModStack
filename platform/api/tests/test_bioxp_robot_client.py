from __future__ import annotations

import asyncio
from ipaddress import ip_address

import httpx

from services.bioxp.errors import RobotTimeoutError
from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES, BioXpRobotClient
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


class TimeoutTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("deadline expired", request=request)


class SnapshotRefreshTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        stale: bool,
        age_s: float = 0.0,
        snapshot_status: int = 200,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.stale = stale
        self.age_s = age_s
        self.snapshot_status = snapshot_status

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        if request.method == "POST":
            if self.snapshot_status >= 400:
                return httpx.Response(
                    self.snapshot_status,
                    json={"detail": "snapshot unavailable"},
                    request=request,
                )
            self.stale = False
            self.age_s = 0.0
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "published": True,
                    "snapshot": {"snapshot_id": "snapshot-refresh"},
                },
                request=request,
            )
        if self.stale:
            return httpx.Response(
                200,
                json={
                    "runtime_available": True,
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
                "admission_observation": {
                    "available": True, "cache_state": "fresh",
                    "freshness": {"state": "fresh", "age_s": self.age_s, "fresh_for_s": 30.0},
                },
                "deck_authority": {
                    "available": True,
                    "freshness": {"state": "fresh", "age_s": self.age_s, "fresh_for_s": 15.0},
                },
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


def test_robot_read_timeout_is_typed_as_dispatched_and_outcome_ambiguous() -> None:
    target = ValidatedBioXpTarget(
        api_url="http://robot:8123",
        scheme="http",
        hostname="robot",
        port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),),
    )
    client = BioXpRobotClient(target, transport=TimeoutTransport())

    try:
        asyncio.run(client.request("invoke_operator_action_v2", path_params={"action_id": "oem.y.move_steps"}, json_data={}))
    except RobotTimeoutError as exc:
        assert exc.dispatched is True
        assert exc.dispatch_state == "outcome_ambiguous"
        assert exc.caller_can_retry is False
    else:  # pragma: no cover - regression assertion
        raise AssertionError("robot request timeout must remain explicitly ambiguous")
    asyncio.run(client.close())


def test_v2_admission_timeout_covers_observed_activation_duration() -> None:
    _, _, timeout_seconds = DEFAULT_ROBOT_ROUTES["invoke_operator_action_v2"]

    assert timeout_seconds == 15.0
    assert timeout_seconds > 8.052384


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


def test_robot_client_routes_only_supported_compact_commissioning_contracts() -> None:
    target = ValidatedBioXpTarget(
        api_url="http://robot:8123",
        scheme="http",
        hostname="robot",
        port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),),
    )
    client = BioXpRobotClient(target, transport=RecordingTransport())

    for retired in (
        "initialize_oem_environment",
        "run_oem_motor_stage",
        "record_oem_motor_stage_observation",
        "recover_motion_non_homing",
    ):
        assert retired not in client.routes
    assert client.routes["collect_hardware_snapshot"][:2] == ("POST", "/hardware/snapshot/collect")
    assert client.routes["collect_hardware_snapshot"][2] >= 195.0
    for command in (
        "collect_axis_diagnostics",
        "run_axis_diagnostic",
        "stop_axis_diagnostic",
    ):
        assert command in client.routes

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


def test_lifecycle_routes_do_not_inject_authentication_headers() -> None:
    target = ValidatedBioXpTarget(
        api_url="http://robot:8123",
        scheme="http",
        hostname="robot",
        port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),),
    )
    transport = RecordingTransport()
    client = BioXpRobotClient(target, transport=transport)

    asyncio.run(client.request("oem_full_lifecycle_contract"))
    asyncio.run(client.request("plan_oem_full_lifecycle", json_data={"mode": "dry_run"}))
    asyncio.run(client.request("cancel_oem_full_lifecycle_run", path_params={"run_id": "run-12345678"}))

    assert all("X-BioXP-OEM-Token" not in request.headers for request in transport.requests)
    assert all("Authorization" not in request.headers for request in transport.requests)
    asyncio.run(client.close())


def test_passive_probe_never_collects_stale_or_half_expired_evidence() -> None:
    target = ValidatedBioXpTarget(api_url="http://robot:8123", scheme="http",
        hostname="robot", port=8123, resolved_addresses=(ip_address("100.64.0.10"),))
    async def scenario():
        for stale, age in [(True, 31.0), (False, 15.0), (False, 0.0)]:
            transport = SnapshotRefreshTransport(stale=stale, age_s=age)
            client = BioXpRobotClient(target, transport=transport)
            try:
                for _ in range(4):
                    row = await client.probe()
                    assert row["cache_state"] == ("stale" if stale else "fresh")
                    assert "automatic_snapshot_refresh" not in row
                assert [(r.method, r.url.path) for r in transport.requests] == [("GET", "/status")] * 4
            finally:
                await client.close()
    asyncio.run(scenario())


def test_explicit_full_collection_still_posts_and_status_reads_back_result() -> None:
    target = ValidatedBioXpTarget(api_url="http://robot:8123", scheme="http",
        hostname="robot", port=8123, resolved_addresses=(ip_address("100.64.0.10"),))
    transport = SnapshotRefreshTransport(stale=True)
    async def scenario():
        client = BioXpRobotClient(target, transport=transport)
        try:
            assert (await client.probe())["cache_state"] == "stale"
            result = await client.request("collect_hardware_snapshot", json_data={})
            assert result["published"] is True
            assert (await client.probe())["cache_state"] == "fresh"
            assert [(r.method, r.url.path) for r in transport.requests] == [
                ("GET", "/status"), ("POST", "/hardware/snapshot/collect"), ("GET", "/status")]
            assert transport.requests[1].content == b"{}"
            assert transport.requests[1].extensions["timeout"]["read"] == 210.0
        finally:
            await client.close()
    asyncio.run(scenario())
