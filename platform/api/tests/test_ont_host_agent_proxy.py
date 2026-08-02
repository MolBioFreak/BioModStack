from __future__ import annotations

import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
SCRIPTS_ROOT = REPO_ROOT / "scripts"

for path in (API_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from services import host_agent_client, ont_device_control  # noqa: E402


def test_host_agent_client_exposes_ont_status_and_positions(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, path: str, payload=None, *, query=None):
        calls.append((method, path))
        return {"path": path, "fake_or_demo_devices": False}

    monkeypatch.setattr(host_agent_client, "request_host_agent", fake_request)

    assert host_agent_client.get_ont_status()["path"] == "/ont/status"
    assert host_agent_client.get_ont_positions()["path"] == "/ont/positions"
    assert host_agent_client.get_ont_position("X1")["path"] == "/ont/positions/X1"
    assert calls == [("GET", "/ont/status"), ("GET", "/ont/positions"), ("GET", "/ont/positions/X1")]


def test_device_status_delegates_to_host_agent_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("BMS_ONT_MINKNOW_ENABLED", "1")
    monkeypatch.setenv("BMS_HOST_AGENT_URL", "http://127.0.0.1:8798")
    monkeypatch.setattr(
        ont_device_control,
        "get_ont_status",
        lambda: {
            "implementation_status": "configured",
            "minknow": {"host": "localhost", "manager_port": 9502},
            "live_devices": [{"position": "X1", "device_type": "mk1d"}],
            "fake_or_demo_devices": False,
        },
    )
    monkeypatch.setattr(
        ont_device_control,
        "discover_minknow_devices",
        lambda: (_ for _ in ()).throw(AssertionError("API container direct MinKNOW fallback should not run when host-agent is configured")),
    )

    payload = ont_device_control.get_device_control_status()

    assert payload["owner"] == "bms_service_api"
    assert payload["analysis_owner"] == "nextflow_analysis"
    assert payload["implementation_status"] == "configured"
    assert payload["live_devices"] == [{
        "position": "X1",
        "device_type": "mk1d",
        "state": None,
        "running": False,
        "available_for_run": False,
        "flow_cell": {"present": False},
        "fake_or_demo_device": False,
    }]
    assert payload["fake_or_demo_devices"] is False


def test_device_status_reports_host_agent_unavailable_without_fake_devices(monkeypatch) -> None:
    monkeypatch.setenv("BMS_ONT_MINKNOW_ENABLED", "1")
    monkeypatch.setenv("BMS_HOST_AGENT_URL", "http://127.0.0.1:8798")
    monkeypatch.setattr(
        ont_device_control,
        "get_ont_status",
        lambda: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )

    payload = ont_device_control.get_device_control_status()

    assert payload["implementation_status"] == "host_agent_unavailable"
    assert payload["live_devices"] == []
    assert payload["fake_or_demo_devices"] is False
    assert payload["message"] == "Mk1D discovery is unavailable."
    assert "connection refused" not in str(payload)


def test_host_agent_ont_run_route_delegates_to_read_only_run_observation(monkeypatch) -> None:
    import bms_host_agent  # noqa: PLC0415

    monkeypatch.setattr(
        bms_host_agent,
        "observe_ont_run",
        lambda run_id: {"status": "completed", "minknow_run_id": run_id, "output_files": {"fastq": [], "pod5": [], "bam": []}, "fake_or_demo_devices": False},
    )

    assert bms_host_agent.ont_route_payload("/ont/runs/MNK-123") == {
        "status": "completed",
        "minknow_run_id": "MNK-123",
        "output_files": {"fastq": [], "pod5": [], "bam": []},
        "fake_or_demo_devices": False,
    }


def test_host_agent_public_device_requires_literal_booleans() -> None:
    import bms_host_agent  # noqa: PLC0415

    projected = bms_host_agent._public_ont_device(
        {
            "position": "X1",
            "device_type": "mk1d",
            "running": "false",
            "available_for_run": 1,
            "flow_cell": {"present": "true"},
        }
    )

    assert projected is not None
    assert projected["running"] is False
    assert projected["available_for_run"] is False
    assert projected["flow_cell"]["present"] is False


def test_host_agent_ont_routes_return_discovery_payload(monkeypatch) -> None:
    import bms_host_agent  # noqa: PLC0415

    monkeypatch.setattr(
        bms_host_agent,
        "discover_ont_status",
        lambda: {
            "implementation_status": "configured",
            "live_devices": [
                {"position": "X1", "device_type": "mk1d"},
                {"position": "X2", "device_type": "mk1d"},
            ],
            "fake_or_demo_devices": False,
        },
    )

    assert bms_host_agent.ont_route_payload("/ont/status") == {
        "implementation_status": "configured",
        "live_devices": [
            {
                "position": "X1",
                "device_type": "mk1d",
                "state": None,
                "running": False,
                "available_for_run": False,
                "flow_cell": {"present": False},
                "fake_or_demo_device": False,
            },
            {
                "position": "X2",
                "device_type": "mk1d",
                "state": None,
                "running": False,
                "available_for_run": False,
                "flow_cell": {"present": False},
                "fake_or_demo_device": False,
            },
        ],
        "fake_or_demo_devices": False,
        "message": "Mk1D discovery is available.",
    }
    assert bms_host_agent.ont_route_payload("/ont/positions") == {
        "positions": [
            {
                "position": "X1",
                "device_type": "mk1d",
                "state": None,
                "running": False,
                "available_for_run": False,
                "flow_cell": {"present": False},
                "fake_or_demo_device": False,
            },
            {
                "position": "X2",
                "device_type": "mk1d",
                "state": None,
                "running": False,
                "available_for_run": False,
                "flow_cell": {"present": False},
                "fake_or_demo_device": False,
            },
        ],
        "implementation_status": "configured",
        "fake_or_demo_devices": False,
    }
    assert bms_host_agent.ont_route_payload("/ont/positions/X2") == {
        "position": {
            "position": "X2",
            "device_type": "mk1d",
            "state": None,
            "running": False,
            "available_for_run": False,
            "flow_cell": {"present": False},
            "fake_or_demo_device": False,
        },
        "implementation_status": "configured",
        "fake_or_demo_devices": False,
    }
    assert bms_host_agent.ont_route_payload("/ont/positions/missing") is None


def test_host_agent_protocol_options_route_returns_truthful_preflight(monkeypatch) -> None:
    import bms_host_agent  # noqa: PLC0415

    monkeypatch.setattr(
        bms_host_agent,
        "discover_ont_status",
        lambda: {
            "implementation_status": "configured",
            "live_devices": [
                {
                    "position": "X1",
                    "running": False,
                    "flow_cell": {"present": True, "product_code": "FLO-MIN114"},
                }
            ],
            "fake_or_demo_devices": False,
        },
    )
    monkeypatch.setattr(
        bms_host_agent,
        "discover_ont_protocol_options",
        lambda position, kit=None, basecalling_enabled=True, status=None: {
            "position": position,
            "kit": kit,
            "can_start": True,
            "blockers": [],
            "basecalling_enabled": basecalling_enabled,
            "fake_or_demo_devices": False,
        },
    )

    payload = bms_host_agent.ont_route_payload("/ont/positions/X1/protocol-options?kit=SQK-LSK114")

    assert payload == {
        "position": "X1",
        "kit": "SQK-LSK114",
        "can_start": True,
        "blockers": [],
        "basecalling_enabled": True,
        "fake_or_demo_devices": False,
    }



def test_host_agent_position_refresh_and_restart_routes_are_explicit(monkeypatch) -> None:
    import bms_host_agent  # noqa: PLC0415

    monkeypatch.setattr(
        bms_host_agent,
        "refresh_ont_position",
        lambda position: (200, {"action": "refresh", "position": {"position": position}, "fake_or_demo_devices": False}),
    )
    monkeypatch.setattr(
        bms_host_agent,
        "restart_ont_position",
        lambda position, payload: (
            501,
            {
                "detail": "BMS does not yet perform a MinKNOW/Mk1D instrument restart",
                "position": position,
                "fake_or_demo_devices": False,
            },
        ),
    )

    assert bms_host_agent.ont_post_route_payload("/ont/positions/MD-105428/refresh", {"confirm_refresh": True}) == (
        200,
        {
            "action": "refresh",
            "position": {"position": "MD-105428"},
            "fake_or_demo_devices": False,
        },
    )
    assert bms_host_agent.ont_post_route_payload("/ont/positions/MD-105428/restart", {"confirm_restart": True}) == (
        501,
        {
            "detail": "BMS does not yet perform a MinKNOW/Mk1D instrument restart",
            "position": "MD-105428",
            "fake_or_demo_devices": False,
        },
    )


def test_host_agent_retired_raw_position_start_route_is_unreachable(monkeypatch) -> None:
    import bms_host_agent  # noqa: PLC0415

    monkeypatch.setattr(
        bms_host_agent,
        "start_ont_protocol",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retired host route must not reach MinKNOW start")),
        raising=False,
    )

    assert bms_host_agent.ont_post_route_payload("/ont/positions/MD-105428/start", {"confirm_start": True}) is None



def test_host_agent_hardware_check_route_fails_closed() -> None:
    import bms_host_agent  # noqa: PLC0415

    status, payload = bms_host_agent.ont_post_route_payload(
        "/ont/positions/MD-105428/hardware-check",
        {"confirm_hardware_check": True},
    )

    assert status == 501
    assert payload == {
        "detail": "Mk1D hardware-check activation is disabled pending separately authorized supervised commissioning.",
        "position": "MD-105428",
        "fake_or_demo_devices": False,
    }


def test_begin_hardware_check_never_discovers_or_constructs_minknow_manager(monkeypatch) -> None:
    from lib import ont_minknow_host  # noqa: PLC0415

    monkeypatch.setattr(
        ont_minknow_host,
        "discover_status",
        lambda: (_ for _ in ()).throw(AssertionError("hardware-check tombstone must not discover live state")),
    )
    monkeypatch.setattr(
        ont_minknow_host,
        "build_manager",
        lambda _config: (_ for _ in ()).throw(AssertionError("hardware-check tombstone must not construct a manager")),
    )

    for payload in ({}, {"confirm_hardware_check": True}):
        status, response = ont_minknow_host.begin_hardware_check("MD-105428", payload)
        assert status == 501
        assert response["position"] == "MD-105428"
        assert response["fake_or_demo_devices"] is False
        assert "supervised commissioning" in response["detail"]



def test_normalize_minknow_run_id_histories_and_current_hardware_check() -> None:
    from lib import ont_minknow_host  # noqa: PLC0415

    class History:
        run_ids = ["protocol-1", "protocol-2"]

    class Current:
        run_id = "hardware-run"
        protocol_id = "checks/hardware_validation/hardware_check:CTC-MIN001"
        state = "0"
        phase = "0"

    assert ont_minknow_host.normalize_protocol_runs(History()) == [
        {"run_id": "protocol-1"},
        {"run_id": "protocol-2"},
    ]
    current = ont_minknow_host.normalize_current_protocol(Current())
    assert current["run_id"] == "hardware-run"
    assert current["hardware_check_like"] is True


def test_observe_run_never_treats_pending_as_completed(monkeypatch) -> None:
    from lib import ont_minknow_host  # noqa: PLC0415

    monkeypatch.setattr(
        ont_minknow_host,
        "discover_status",
        lambda: {
            "implementation_status": ont_minknow_host.MINKNOW_STATUS_CONFIGURED,
            "live_devices": [
                {
                    "running": False,
                    "current_protocol": None,
                    "protocol_runs": [{"run_id": "run-pending", "state": "pending"}],
                    "acquisition_runs": [],
                }
            ],
        },
    )

    assert ont_minknow_host.observe_run("run-pending")["status"] == "unknown"


def test_protocol_run_state_uses_protobuf_enum_name() -> None:
    from lib import ont_minknow_host  # noqa: PLC0415

    class EnumValue:
        name = "PROTOCOL_STATE_RUNNING"

    class EnumType:
        values_by_number = {2: EnumValue()}

    class Field:
        enum_type = EnumType()

    class Descriptor:
        fields_by_name = {"state": Field()}

    class Run:
        DESCRIPTOR = Descriptor()
        run_id = "run-enum"
        state = 2

    normalized = ont_minknow_host.normalize_protocol_run(Run())

    assert normalized["state"] == "PROTOCOL_STATE_RUNNING"


@pytest.mark.parametrize(
    ("raw_state", "expected"),
    [
        ("PROTOCOL_STATE_RUNNING", "active"),
        ("PROTOCOL_STATE_COMPLETED", "completed"),
        ("error_recovery_pending", "unknown"),
    ],
)
def test_observe_run_uses_only_finite_exact_state_mappings(monkeypatch, raw_state: str, expected: str) -> None:
    from lib import ont_minknow_host  # noqa: PLC0415

    monkeypatch.setattr(
        ont_minknow_host,
        "discover_status",
        lambda: {
            "implementation_status": ont_minknow_host.MINKNOW_STATUS_CONFIGURED,
            "live_devices": [
                {
                    "running": False,
                    "current_protocol": {},
                    "protocol_runs": [{"run_id": "run-exact", "state": raw_state}],
                    "acquisition_runs": [],
                }
            ],
        },
    )

    assert ont_minknow_host.observe_run("run-exact")["status"] == expected


@pytest.mark.parametrize(
    ("raw_state", "expected"),
    [
        ("running", "active"),
        ("pending", "unknown"),
        ("error_recovery_pending", "unknown"),
        ("finished", "completed"),
    ],
)
def test_current_protocol_uses_only_finite_exact_state_mappings(
    monkeypatch, raw_state: str, expected: str
) -> None:
    from lib import ont_minknow_host  # noqa: PLC0415

    monkeypatch.setattr(
        ont_minknow_host,
        "discover_status",
        lambda: {
            "implementation_status": ont_minknow_host.MINKNOW_STATUS_CONFIGURED,
            "live_devices": [
                {
                    "running": False,
                    "current_protocol": {"run_id": "run-current", "state": raw_state},
                    "protocol_runs": [],
                    "acquisition_runs": [],
                }
            ],
        },
    )

    assert ont_minknow_host.observe_run("run-current")["status"] == expected


@pytest.mark.parametrize("invalid_confirmation", [False, "false", "true", 0, 1, None])
def test_host_stop_requires_literal_true(invalid_confirmation) -> None:
    from lib import ont_minknow_host  # noqa: PLC0415

    status, _payload = ont_minknow_host.stop_protocol(
        "run-exact", {"confirm_stop": invalid_confirmation}
    )

    assert status == 400
