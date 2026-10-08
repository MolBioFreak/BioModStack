from __future__ import annotations

from scripts import wait_for_children as wait_module  # type: ignore[import-not-found]


class _Response:
    ok = True
    status_code = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


def test_expected_child_ids_block_stale_completed_batch_alias(monkeypatch) -> None:
    responses = iter(
        [
            _Response(
                {
                    "total": 1,
                    "completed": 1,
                    "failed": 0,
                    "cancelled": 0,
                    "running": 0,
                    "pending": 0,
                    "all_done": True,
                    "success_rate": 100.0,
                    "child_ids": ["stale-child"],
                    "child_output_dirs": ["/stale"],
                }
            ),
            _Response(
                {
                    "total": 1,
                    "completed": 0,
                    "failed": 0,
                    "cancelled": 0,
                    "running": 1,
                    "pending": 0,
                    "all_done": False,
                    "child_ids": ["expected-child"],
                }
            ),
            _Response(
                {
                    "total": 1,
                    "completed": 1,
                    "failed": 0,
                    "cancelled": 0,
                    "running": 0,
                    "pending": 0,
                    "all_done": True,
                    "success_rate": 100.0,
                    "child_ids": ["expected-child"],
                    "child_output_dirs": ["/expected"],
                }
            ),
        ]
    )
    monkeypatch.setattr(wait_module.requests, "get", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(wait_module.requests, "post", lambda *args, **kwargs: _Response({}))
    monkeypatch.setattr(wait_module.time, "sleep", lambda _: None)

    result = wait_module.wait_for_children(
        parent_job_id="parent",
        stage="md_replica",
        poll_interval=0,
        expected_child_ids={"expected-child"},
    )

    assert result["child_ids"] == ["expected-child"]
    assert result["child_output_dirs"] == ["/expected"]


def test_expected_child_ids_reject_mixed_lineage(monkeypatch) -> None:
    response = _Response(
        {
            "total": 2,
            "completed": 2,
            "failed": 0,
            "cancelled": 0,
            "running": 0,
            "pending": 0,
            "all_done": True,
            "success_rate": 100.0,
            "child_ids": ["expected-child", "foreign-child"],
            "child_output_dirs": ["/expected", "/foreign"],
        }
    )
    monkeypatch.setattr(wait_module.requests, "get", lambda *args, **kwargs: response)

    result = wait_module.wait_for_children(
        parent_job_id="parent",
        stage="md_replica",
        poll_interval=0,
        expected_child_ids={"expected-child"},
    )

    assert result["status"] == "lineage_mismatch"
    assert result["child_output_dirs"] == []


def test_runtime_wait_uses_attempt_ledger_without_http(tmp_path, monkeypatch):
    import hashlib
    import json
    from component_runtime import ComponentRuntime, ComponentRequest, ResultReference

    context = dict(ledger_path=str(tmp_path / "components.sqlite"), artifact_root=str(tmp_path),
                   attempt_id="attempt", root_job_id="parent", target_id="worker", lease_id="lease")
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context))
    monkeypatch.setenv("BMS_COMPONENT_CONTEXT", str(context_path))
    runtime = ComponentRuntime(tmp_path / "components.sqlite", attempt_id="attempt",
        root_job_id="parent", target_id="worker", lease_id="lease", artifact_root=tmp_path)
    payload = dict(name="child", model_id="boltzgen_child", mode="protein_binder",
                   params={"job_index": 0}, batch_name="batch")
    child_id = runtime.submit(ComponentRequest.capture(parent_job_id="parent", stage="boltzgen",
        child_key="0", payload=payload, required=False))
    runtime.claim(child_id, owner_id="owner", boot_id="boot")
    artifact = tmp_path / "native.json"
    artifact.write_bytes(b"{}")
    runtime.complete(child_id, owner_id="owner", boot_id="boot", result={"output_dir": str(tmp_path)},
        references=[ResultReference(child_id, "native.json", hashlib.sha256(b"{}").hexdigest(), 2, "native")])
    available_id = runtime.submit(ComponentRequest.capture(parent_job_id="parent", stage="boltzgen",
        child_key="1", payload={**payload, "name": "unvalidated"}, required=True))
    runtime.claim(available_id, owner_id="owner", boot_id="boot")
    runtime.execution_finished(available_id, owner_id="owner", boot_id="boot",
        output_dir=str(tmp_path / "available"), exit_code=0)
    def forbidden(*args, **kwargs):
        raise AssertionError("runtime wait must not contact host HTTP")
    monkeypatch.setattr(wait_module.requests, "get", forbidden)
    monkeypatch.setattr(wait_module.requests, "post", forbidden)
    result = wait_module.wait_for_children("parent", "boltzgen", poll_interval=0,
        batch_name="batch", expected_child_ids=[available_id, child_id])
    assert result["status"] == "outputs_available"
    assert result["completed"] == 1
    assert result["execution_finished"] == 1
    assert result["child_ids"] == [available_id, child_id]
    assert result["child_output_dirs"] == [str(tmp_path / "available"), str(tmp_path)]
    assert runtime.child_status(available_id)["references"] == []
    assert runtime.child_status(available_id)["status"] == "execution_finished"
