"""Consumer boundary removal: no diagnostic startup copy or redundant dump."""
import asyncio
import json
import pytest
from services.bioxp.models import BioXpProfile
from services.bioxp.protocol_models import ProtocolJob
from test_bioxp_connection import _service
from test_bioxp_operator_controls import make_client
from test_bioxp_protocol_relay import bundle, JOB


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
