"""BMS-012: real BMS routes/leases -> robot routes/admission/SQLite/dispatcher.

Run with BIOXP_TEST_SOURCE pointing at a robot source checkout (src/bioxp).
No robot app lifespan or hardware owner starts. Native action and Stop delivery
are inert leaves; admission, controls, review, executor and SQL are production.
The default BMS network namespace/socket guard remains enabled.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from routers.bioxp.dependencies import require_bioxp_mutation_access
from routers.bioxp.protocols import router
from services.bioxp.errors import RobotResponseError, RobotTimeoutError
from services.bioxp.models import BioXpProfile
from test_bioxp_connection import _service


@pytest.fixture
def robot(tmp_path, monkeypatch):
    source = os.environ.get("BIOXP_TEST_SOURCE")
    if not source:
        pytest.skip("connected qualification requires BIOXP_TEST_SOURCE robot checkout")
    source = Path(source).resolve()
    assert (source / "src/bioxp/operator_command_plane.py").is_file()
    monkeypatch.syspath_prepend(str(source / "src"))
    root = tmp_path / "robot-store"
    monkeypatch.setenv("BIOXP_OEM_RUNTIME_STATE_ROOT", str(root))
    monkeypatch.setenv("BIOXP_REFERENCE_STATE_PATH", str(root / "reference.db"))
    monkeypatch.setenv("BIOXP_PROTOCOL_JOBS_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.delenv("BIOXP_OEM_RUNTIME_ROOT", raising=False)
    from bioxp import runtime_audit_store
    monkeypatch.setattr(runtime_audit_store, "CANONICAL_RUNTIME_ROOT", root)
    from bioxp import api
    from bioxp.oem_runtime_store import OEMRuntimeStore
    from bioxp.operator_command_plane import OperatorCommandStore
    from bioxp.protocols.models import ProtocolActionKind
    from bioxp.services.protocol_service import ProtocolBindings
    # Guard even accidental future native fallback; no real USB discovery.
    def no_hardware(*args, **kwargs):
        raise AssertionError("physical transport is forbidden in connected qualification")
    try:
        import usb.core
    except ImportError:
        pass  # BMS locked venv has no USB package: physical entry cannot import.
    else:
        monkeypatch.setattr(usb.core, "find", no_hardware)
        from bioxp.usb_driver import BioXpTester
        monkeypatch.setattr(BioXpTester, "__init__", no_hardware)
    seed = OEMRuntimeStore(root)
    seed.close()
    store = OperatorCommandStore(root)
    trace, errors = [], []
    leaf_entered, leaf_release = threading.Event(), threading.Event()
    outcome = {"failure": False}

    def native(action, state):
        from bioxp.runtime_audit_store import workflow_claim_context
        trace.append({"action_id": action.action_id, "binding": workflow_claim_context()})
        if action.action_id == "second":
            leaf_entered.set()
            assert leaf_release.wait(15), "test did not release native leaf"
        return {"ok": not outcome["failure"], "fixture_only": True,
                "physical_effect_verified": False,
                **({"error": "injected_native_failure"} if outcome["failure"] else {})}

    monkeypatch.setattr(api, "_protocol_command_store", lambda: store)
    monkeypatch.setattr(api, "_protocol_authority", lambda document: (7, {}))
    monkeypatch.setattr(api, "_protocol_bindings", lambda bundle, **kw: ProtocolBindings(
        {ProtocolActionKind.THERMAL_DOOR: native}, {}, {}))
    app = FastAPI()
    for path, endpoint in [
        ("/protocol/execute", api.protocol_execute),
        ("/protocol/jobs/{job_id}/control", api.protocol_job_control),
        ("/protocol/jobs/{job_id}/review", api.protocol_job_review),
    ]:
        app.add_api_route(path, endpoint, methods=["POST"])
    app.add_api_route("/protocol/jobs/{job_id}", api.protocol_job_detail, methods=["GET"])
    threads = []
    def dispatch():
        claimed = store.claim_next()
        assert claimed is not None
        def run():
            try:
                store._workflow_dispatcher(claimed)
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        threads.append(thread)
        thread.start()
        return claimed
    yield SimpleNamespace(app=app, store=store, root=root, trace=trace,
                          leaf_entered=leaf_entered, leaf_release=leaf_release,
                          dispatch=dispatch, outcome=outcome, errors=errors)
    leaf_release.set()
    for row in store.list_workflows():
        if not row["command"]["terminal"]:
            try:
                store.control_workflow(row["job_id"], request={"action": "abort",
                    "command_id": row["job_id"], "expected_ownership_generation": 7,
                    "idempotency_key": "test-teardown"})
            except ValueError:
                pass
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive(), "stranded production protocol dispatcher"
    store.stop()
    assert not errors


def submission(key, generation):
    return {"expected_connection_generation": generation, "source_type": "native",
            "dry_run": False, "idempotency_key": key,
            "live_execution": {"operator_id": "offline-test", "live_execution_ack": True},
            "document": {"protocol_id": "concurrency", "stages": [{"stage_id": "one", "actions": [
                {"action_id": "first", "kind": "thermal_door", "review_required": True},
                {"action_id": "second", "kind": "thermal_door"},
                {"action_id": "third", "kind": "thermal_door"}]}]}}


async def eventually(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(.005)
    await asyncio.wait_for(poll(), 8)


@pytest.mark.parametrize("ending", ["completed", "addressed_stop", "native_failure", "reply_timeout"])
def test_connected_admission_order_controls_stop_and_generation(robot, tmp_path, ending):
    async def scenario():
        clients = []
        service = _service(tmp_path, clients)
        await service.save_profile(BioXpProfile(api_url="http://robot:8123"))
        generation = (await service.connect()).generation
        original = clients[0]
        calls, responses, stop_wire = [], [], []
        admitted, reply_release = asyncio.Event(), asyncio.Event()
        v1_entered, v1_release = asyncio.Event(), asyncio.Event()
        app = FastAPI()
        app.state.bioxp_runtime = SimpleNamespace(connection=service)
        app.dependency_overrides[require_bioxp_mutation_access] = lambda: None
        app.include_router(router, prefix="/api/bioxp")
        base = "/api/bioxp/protocols"
        first_body = submission("original-" + ending, generation)
        first_job = None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(robot.app), base_url="http://inert-robot") as remote:
            async def transport(route, **kwargs):
                nonlocal first_job
                calls.append((route, copy.deepcopy(kwargs)))
                if route == "invoke_operator_action":
                    v1_entered.set()
                    await v1_release.wait()
                    return {}
                if route == "interrupt_operator_action_v1":
                    action = kwargs["path_params"]["action_id"]
                    request = kwargs["json_data"]
                    receipt = robot.store.begin_interrupt(action, state={"ownership_generation": 7}, request=request)
                    stop_wire.append((action, copy.deepcopy(request)))
                    result = robot.store.finalize_interrupt(idempotency_key=request["idempotency_key"],
                        receipt=receipt, attempted=True, acknowledged=True,
                        response={"ok": True, "fixture_only": True, "source_return_ok": True,
                                  "controller_stop_acknowledged": True})
                    robot.store._notify_workflow_interrupt()
                    return result
                path = {"protocol_execute": "/protocol/execute",
                        "protocol_control": "/protocol/jobs/{job_id}/control",
                        "protocol_review": "/protocol/jobs/{job_id}/review",
                        "protocol_job": "/protocol/jobs/{job_id}"}[route].format(**kwargs.get("path_params", {}))
                response = await remote.request("GET" if route == "protocol_job" else "POST", path,
                                                json=kwargs.get("json_data"))
                payload = response.json()
                responses.append({"route": route, "status": response.status_code, "body": payload})
                if response.is_error:
                    raise RobotResponseError(response.status_code, payload)
                if route == "protocol_execute" and first_job is None:
                    first_job = payload["job_id"]
                    admitted.set()
                    await reply_release.wait()
                    if ending == "reply_timeout":
                        raise RobotTimeoutError("reply lost after real durable admission", dispatched=True)
                return payload
            original.request = transport
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://bms") as http:
                v1 = asyncio.create_task(service.request_active("invoke_operator_action", expected_generation=generation))
                await asyncio.wait_for(v1_entered.wait(), 2)
                # Existing live direct resource holder delays activation, not admission.
                scope = robot.store.normal_mutation_scope(resources=("axis:x",))
                direct = scope.__enter__()
                execute = asyncio.create_task(http.post(base + "/submit", json=first_body))
                disconnect = None
                try:
                    await asyncio.wait_for(admitted.wait(), 5)
                    assert not execute.done() and not v1.done()
                    assert robot.store.claim_next() is None
                    assert robot.trace == []
                    assert robot.store.get_workflow(first_job)["command"]["status"] == "queued"
                    # A distinct overlapping request reaches actual SQL admission and
                    # retains the robot's existing 400 workflow_busy (not an invented 409).
                    busy = await asyncio.wait_for(http.post(base + "/submit", json=submission("overlap", generation)), 3)
                    assert busy.status_code == 400 and busy.json()["detail"] == "workflow_busy", busy.text
                    replay = await http.post(base + "/submit", json=first_body)
                    assert replay.status_code == 202 and replay.json()["job_id"] == first_job, replay.text
                    changed = copy.deepcopy(first_body)
                    changed["document"]["protocol_id"] = "changed-intent"
                    conflict = await http.post(base + "/submit", json=changed)
                    assert conflict.status_code == 409, conflict.text
                    assert "idempotency" in conflict.text
                    scope.__exit__(None, None, None)
                    scope = None
                    claimed = robot.dispatch()
                    assert claimed["command_id"] == first_job
                    await eventually(lambda: robot.store.get_workflow(first_job)["execution"]["runtime_state"]["workflow"]["gate"] == "review")
                    assert [row["action_id"] for row in robot.trace] == ["first"]
                    identity = {"expected_connection_generation": generation, "command_id": first_job,
                                "expected_ownership_generation": 7}
                    stale = await http.post(base + f"/jobs/{first_job}/control", json={**identity,
                        "expected_ownership_generation": 6, "idempotency_key": "stale-owner", "action": "abort"})
                    assert stale.status_code == 409, stale.text
                    bad_review = await http.post(base + f"/jobs/{first_job}/review", json={**identity,
                        "idempotency_key": "wrong-review", "stage_id": "one", "action_id": "other", "reviewer": "offline"})
                    assert bad_review.status_code == 409, bad_review.text
                    review_body = {**identity, "idempotency_key": "original-review", "stage_id": "one",
                                   "action_id": "first", "reviewer": "offline"}
                    reviewed = await asyncio.wait_for(http.post(base + f"/jobs/{first_job}/review", json=review_body), 3)
                    assert reviewed.status_code == 200 and reviewed.json()["job_id"] == first_job, reviewed.text
                    await eventually(robot.leaf_entered.is_set)
                    repeated_review = await http.post(base + f"/jobs/{first_job}/review", json=review_body)
                    assert repeated_review.status_code == 200 and repeated_review.json()["job_id"] == first_job
                    assert not execute.done() and not v1.done()
                    assert robot.store.claim_next() is None
                    assert [row["action_id"] for row in robot.trace] == ["first", "second"]
                    queued = []
                    if ending == "completed":
                        # Real canonical FIFO entries, not a test-side queue. The
                        # live workflow owns dispatch despite later HTTP admission.
                        for key in ("fifo-first", "fifo-second"):
                            queued.append(robot.store.admit_command({"action_id": "oem.x.prepare",
                                "inputs": {}, "expected_ownership_generation": 7,
                                "idempotency_key": key}, state={}))
                        assert robot.store.claim_next() is None
                        assert all(robot.store.get_command(row["command_id"])["status"] == "queued" for row in queued)
                    if ending == "addressed_stop":
                        abort_body = {**identity, "idempotency_key": "original-abort", "action": "abort"}
                        abort = await asyncio.wait_for(http.post(base + f"/jobs/{first_job}/control", json=abort_body), 3)
                        assert abort.status_code == 200 and abort.json()["accepted"], abort.text
                        assert stop_wire == []  # cooperative Abort never sends physical Stop
                        stop_request = {"idempotency_key": "original-stop"}
                        stopped = await asyncio.wait_for(service.request_active_safety_interrupt(
                            "interrupt_operator_action_v1", expected_generation=generation,
                            path_params={"action_id": "oem.x.stop"}, json_data=stop_request), 3)
                        assert stopped["scope"] == "x" and stopped["persistence_state"] == "committed"
                        assert stopped["physical_effect_verified"] is False
                        assert stop_wire == [("oem.x.stop", stop_request)]
                        assert not execute.done() and not v1.done()
                    robot.outcome["failure"] = ending == "native_failure"
                    robot.leaf_release.set()
                    await eventually(lambda: robot.store.get_workflow(first_job)["command"]["terminal"])
                    final = robot.store.get_workflow(first_job)
                    expected = {"addressed_stop": "interrupted", "native_failure": "failed"}.get(ending, "completed")
                    assert final["command"]["status"] == expected, final
                    readback = await http.get(base + f"/jobs/{first_job}")
                    assert readback.status_code == 200 and readback.json() == final, readback.text
                    order = [row["action_id"] for row in robot.trace]
                    assert order == (["first", "second"] if ending in ("addressed_stop", "native_failure") else ["first", "second", "third"])
                    assert all(row["binding"]["parent_command_id"] == first_job for row in robot.trace)
                    assert [row["binding"]["source_occurrence_id"] for row in robot.trace] == order
                    assert final["command"]["idempotency_key"] == first_body["idempotency_key"]
                    assert final["command"]["ownership_generation"] == 7
                    fifo = []
                    for row in queued:
                        next_command = robot.store.claim_next()
                        assert next_command["command_id"] == row["command_id"]
                        # The second same-axis entry cannot overtake or overlap.
                        assert robot.store.claim_next() is None
                        robot.store.finish(next_command["command_id"], status="completed",
                            payload={"fixture_only": True, "physical_effect_verified": False}, claimed=next_command)
                        fifo.append(next_command["command_id"])
                    assert fifo == [row["command_id"] for row in queued]
                    assert robot.store.connection.execute("SELECT status FROM operator_commands WHERE command_id=?", (direct,)).fetchone()[0] == "completed"
                    # Disconnect drains old leases; replacement receives no old dispatch.
                    disconnect = asyncio.create_task(service.disconnect())
                    await asyncio.sleep(0)
                    assert not original.closed and not disconnect.done()
                    replacement = await service.connect()
                    assert replacement.generation != generation
                    count = len(calls)
                    stale_connection = await http.post(base + "/submit", json=first_body)
                    assert stale_connection.status_code == 409 and len(calls) == count
                    assert not clients[1].request_calls
                    revision = service._v2_query_revision
                finally:
                    if scope is not None:
                        scope.__exit__(None, None, None)
                    robot.leaf_release.set()
                    reply_release.set()
                    v1_release.set()
                    result = await execute
                    await v1
                    if disconnect is not None:
                        await disconnect
                assert result.status_code == (504 if ending == "reply_timeout" else 202), result.text
                if ending == "reply_timeout":
                    assert result.json()["detail"]["dispatch_state"] == "outcome_ambiguous"
                else:
                    assert result.json()["job_id"] == first_job
                assert original.closed and service.snapshot().generation == replacement.generation
                assert service._v2_query_revision > revision
                assert all(item.lease_count == 0 for item in service._generation_leases.values())
                forwarded = next(kw["json_data"] for name, kw in calls if name == "protocol_execute")
                assert forwarded == {k: v for k, v in first_body.items() if k != "expected_connection_generation"}
                assert len(robot.store.list_workflows()) == 1
                assert robot.store.connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                export = os.environ.get("BMS_ROBOT_CONCURRENCY_EXPORT")
                if export:
                    Path(export).mkdir(parents=True, exist_ok=True)
                    (Path(export) / (ending + ".json")).write_text(json.dumps({
                        "ending": ending, "robot_root": str(robot.root), "calls": calls,
                        "robot_responses": responses, "native_trace": robot.trace, "stop_wire": stop_wire,
                        "fifo_claim_order": fifo, "queued_commands": queued,
                        "final": final, "bms_result": result.json(), "bms_status": result.status_code,
                        "original_generation": generation, "replacement_generation": replacement.generation,
                        "physical_acceptance": False}, indent=2))
        await service.close()
    asyncio.run(scenario())
