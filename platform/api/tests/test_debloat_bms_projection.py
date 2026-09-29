"""Consumer boundary removal: no diagnostic startup copy or redundant dump."""
import asyncio
import json
import pytest
from services.bioxp.models import BioXpProfile
from services.bioxp.protocol_models import ProtocolJob
from test_bioxp_connection import _service
from test_bioxp_operator_controls import make_client
from test_bioxp_protocol_relay import bundle, JOB


def test_retired_full_lifecycle_facade_has_no_binding_or_mounted_route():
    from routers.bioxp import router
    from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES
    assert not any("/oem-full-lifecycle/" in route.path for route in router.routes)
    assert not any("/oem/runtime/movement-runs" in path for _, path, _ in DEFAULT_ROBOT_ROUTES.values())


def test_status_never_copies_unconsumed_startup(tmp_path):
    class Unconsumed(dict):
        def __deepcopy__(self, memo):
            raise AssertionError("unused startup must not be copied")
    async def scenario():
        clients = []
        service = _service(tmp_path, clients, probe_result={
            "status": "ok", "runtime_ready": True, "hardware_connected": True,
            "startup": Unconsumed(stages={"constructor": {"evidence": "x" * 190489}}),
        })
        await service.save_profile(BioXpProfile(api_url="http://robot:8123"))
        await service.connect()
        assert "startup_lifecycle" not in service.snapshot().model_dump()
        assert not hasattr(service, "_startup_lifecycle")
        await service.close()
    asyncio.run(scenario())

@pytest.mark.parametrize("status", ["completed", "failed", "ambiguous"])
def test_typed_protocol_response_preserves_bytes_without_explicit_dump(monkeypatch, status):
    client, runtime = make_client(monkeypatch)
    payload = bundle(status)
    payload["execution"]["runtime_state"]["action_results"] = [
        {"source_noop": True, "delivery_attempted": False},
        {"partial": True, "error": "child failure", "controller_completed": False},
    ]
    expected = ProtocolJob.model_validate(payload).model_dump(mode="json", exclude_unset=True)
    runtime.connection.client.responses["protocol_job"] = payload
    def unused_dump(*args, **kwargs):
        raise AssertionError("relay must return validated model without dump/re-encode")
    monkeypatch.setattr(ProtocolJob, "model_dump", unused_dump)
    runtime.connection.snapshot = lambda: (_ for _ in ()).throw(AssertionError("generation-only query built snapshot"))
    response = client.get(f"/api/bioxp/protocols/jobs/{JOB}")
    assert response.status_code == 200, response.text
    assert response.content == json.dumps(expected, separators=(",", ":"), ensure_ascii=False).encode()

@pytest.mark.parametrize("path", [
    "/operator-controls/catalog", "/operator-controls/dashboard",
    "/operator-controls/v2/catalog", "/operator-controls/v2/dashboard",
])
def test_operator_reads_never_reconstruct_connection_snapshot(monkeypatch, path):
    client, runtime = make_client(monkeypatch)
    runtime.connection.snapshot = lambda: (_ for _ in ()).throw(AssertionError("unused snapshot"))
    response = client.get("/api/bioxp" + path)
    assert response.status_code == 200, response.text


def test_camera_frames_do_not_reconstruct_profile_or_observations(monkeypatch):
    from test_bioxp_camera import make_client as camera_client
    client, connection = camera_client(monkeypatch)
    connection.snapshot = lambda: (_ for _ in ()).throw(AssertionError("unused snapshot"))
    response = client.get("/api/bioxp/camera/status?expected_generation=77")
    assert response.status_code == 200, response.text


def test_passive_status_keeps_current_malformed_profile_reporting(tmp_path):
    service = _service(tmp_path, [])
    async def scenario():
        await service.save_profile(BioXpProfile(api_url="http://robot:8123"))
        first = service.snapshot()
        service.profile_store.path.write_text("{broken", encoding="utf-8")
        invalid = service.snapshot()
        assert invalid.configured and "malformed" in invalid.last_error
        assert invalid.generation == first.generation
        service.profile_store.path.write_text(BioXpProfile(display_name="Restored", api_url="http://robot:8123").model_dump_json())
        restored = service.snapshot()
        assert restored.display_name == "Restored" and restored.last_error is None
        await service.close()
    asyncio.run(scenario())
