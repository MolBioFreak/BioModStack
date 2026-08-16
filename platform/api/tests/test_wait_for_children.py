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