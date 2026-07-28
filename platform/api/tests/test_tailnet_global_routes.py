from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import biomodstack_tailnet as tailnet  # noqa: E402


def test_global_tailnet_policy_adds_selector_and_stats_without_touching_root_or_unrelated_handlers(monkeypatch) -> None:
    handlers: dict[str, dict[str, str]] = {
        "/": {"Proxy": "http://127.0.0.1:5173"},
        "/am": {"Proxy": "http://127.0.0.1:5174/am"},
    }

    def snapshot() -> tailnet.ServeSnapshot:
        return tailnet.ServeSnapshot(
            origin="https://node.example.ts.net",
            root_proxy=handlers["/"]["Proxy"],
            handlers={path: dict(handler) for path, handler in handlers.items()},
            raw={"handlers": {path: dict(handler) for path, handler in handlers.items()}},
        )

    calls: list[tuple[str, str]] = []

    def set_path(path: str, target: str) -> None:
        calls.append((path, target))
        handlers[path] = {"Proxy": target}

    monkeypatch.setattr(tailnet, "_read_serve_snapshot", snapshot)
    monkeypatch.setattr(tailnet, "_set_serve_path", set_path)

    installed = tailnet.ensure_global_tailnet_routes()

    assert installed.root_proxy == "http://127.0.0.1:5173"
    assert installed.handlers["/am"] == {"Proxy": "http://127.0.0.1:5174/am"}
    assert calls == list(tailnet.GLOBAL_SERVE_HANDLERS.items())
    for path, target in tailnet.GLOBAL_SERVE_HANDLERS.items():
        assert installed.handlers[path] == {"Proxy": target}


def test_global_tailnet_policy_rejects_conflicting_route_owner_without_mutation(monkeypatch) -> None:
    snapshot = tailnet.ServeSnapshot(
        origin="https://node.example.ts.net",
        root_proxy="http://127.0.0.1:5173",
        handlers={
            "/": {"Proxy": "http://127.0.0.1:5173"},
            "/stats/embed": {"Proxy": "http://127.0.0.1:9999/foreign"},
        },
        raw={"sealed": "prior"},
    )
    monkeypatch.setattr(tailnet, "_read_serve_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        tailnet,
        "_set_serve_path",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not mutate")),
    )

    with pytest.raises(tailnet.TailnetEnvironmentError, match="unexpected target"):
        tailnet.ensure_global_tailnet_routes()
