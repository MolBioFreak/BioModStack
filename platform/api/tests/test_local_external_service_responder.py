"""A local attempt answers declared MSA requests through the shared provider-agnostic code."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import component_adapter as ca  # noqa: E402
import services.model_msa_handoff as handoff  # noqa: E402


class FakeRuntime:
    def __init__(self, tmp, target="local"):
        self.target_id = target
        self.artifact_root = tmp
        self.requests = [{"request_id": "r1", "service": {"logical_id": "protenix:msa"}, "native_input": []}]
        self.failed, self.accepted = [], []

    def pending_external_services(self):
        return tuple(self.requests) if not (self.failed or self.accepted) else ()

    def fail_external_service(self, request_id):
        self.failed.append(request_id)


def _drain(responder):
    responder.poll()
    responder.thread.join(10)


def test_local_request_is_prepared_and_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, "generated_msa_service_supported", lambda s: True)
    def prepare(request, destination):
        destination.mkdir(parents=True); (destination / "msa-inputs.json").write_text("{}")
        return "a" * 64
    monkeypatch.setattr(handoff, "prepare_generated_msa", prepare)
    rt = FakeRuntime(tmp_path)
    monkeypatch.setattr(handoff, "accept_generated_msa", lambda r, i, sha: rt.accepted.append((i, sha)))
    _drain(ca.LocalExternalServiceResponder(rt))
    assert rt.accepted == [("r1", "a" * 64)] and not rt.failed
    assert (tmp_path / "external-services" / "r1" / ("a" * 64) / "msa-inputs.json").is_file()


def test_provider_failure_is_recorded_as_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, "generated_msa_service_supported", lambda s: True)
    def boom(request, destination): raise RuntimeError("provider down")
    monkeypatch.setattr(handoff, "prepare_generated_msa", boom)
    rt = FakeRuntime(tmp_path)
    _drain(ca.LocalExternalServiceResponder(rt))
    assert rt.failed == ["r1"]


def test_pending_provider_is_retried_later_not_failed(tmp_path, monkeypatch):
    from biomodstack_msa_api import PendingMSA
    monkeypatch.setattr(handoff, "generated_msa_service_supported", lambda s: True)
    def pending(request, destination): raise PendingMSA("wait", operation={"provider": "colabfold_api", "retry_after_seconds": 30})
    monkeypatch.setattr(handoff, "prepare_generated_msa", pending)
    rt = FakeRuntime(tmp_path)
    r = ca.LocalExternalServiceResponder(rt)
    _drain(r)
    assert not rt.failed and r.retry_at > time.monotonic()


def test_remote_targets_are_not_served_here(tmp_path):
    rt = FakeRuntime(tmp_path, target="remote-1")
    r = ca.LocalExternalServiceResponder(rt)
    r.poll()
    assert r.thread is None


def test_task_config_whitelists_the_attempt_context_for_containers():
    plan = {"metadata": {"static_components": []}}
    resources = {"required": {"cpus": 1, "memory_bytes": 1024}}
    text = ca.native_resource_config(plan, resources, "/tmp/x.lock", ROOT)
    assert "apptainer.envWhitelist" in text and "BMS_COMPONENT_CONTEXT" in text
