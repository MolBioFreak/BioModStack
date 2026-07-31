from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import biomodstack_tailnet as tailnet  # noqa: E402


def test_global_tailnet_policy_installs_governed_mobile_update_routes() -> None:
    assert tailnet.GLOBAL_SERVE_HANDLERS["/api/mobile-apk"] == "http://127.0.0.1:8000/api/mobile-apk"
    assert tailnet.GLOBAL_SERVE_HANDLERS["/api/mobile-ui"] == "http://127.0.0.1:8000/api/mobile-ui"


def test_global_tailnet_policy_adds_managed_routes_removes_exact_dead_routes_and_preserves_others(monkeypatch) -> None:
    handlers: dict[str, dict[str, str]] = {
        "/": {"Proxy": "http://127.0.0.1:5173"},
        "/am": {"Proxy": "http://127.0.0.1:5174/am"},
        "/vlm": {"Proxy": "http://127.0.0.1:8010"},
        "/other": {"Proxy": "http://127.0.0.1:9000"},
    }

    def snapshot() -> tailnet.ServeSnapshot:
        return tailnet.ServeSnapshot(
            origin="https://node.example.ts.net",
            root_proxy=handlers["/"]["Proxy"],
            handlers={path: dict(handler) for path, handler in handlers.items()},
            raw={"handlers": {path: dict(handler) for path, handler in handlers.items()}},
        )

    set_calls: list[tuple[str, str]] = []
    clear_calls: list[str] = []

    def set_path(path: str, target: str) -> None:
        set_calls.append((path, target))
        handlers[path] = {"Proxy": target}

    def clear_path(path: str) -> None:
        clear_calls.append(path)
        handlers.pop(path, None)

    monkeypatch.setattr(tailnet, "_read_serve_snapshot", snapshot)
    monkeypatch.setattr(tailnet, "_set_serve_path", set_path)
    monkeypatch.setattr(tailnet, "_clear_serve_path", clear_path)

    installed = tailnet.ensure_global_tailnet_routes()

    assert installed.root_proxy == "http://127.0.0.1:5173"
    assert installed.handlers["/other"] == {"Proxy": "http://127.0.0.1:9000"}
    assert "/am" not in installed.handlers
    assert "/vlm" not in installed.handlers
    assert set_calls == list(tailnet.GLOBAL_SERVE_HANDLERS.items())
    assert clear_calls == ["/am", "/vlm"]
    for path, target in tailnet.GLOBAL_SERVE_HANDLERS.items():
        assert installed.handlers[path] == {"Proxy": target}


def test_global_tailnet_policy_migrates_exact_legacy_stats_embed_target(monkeypatch) -> None:
    legacy_target = "http://127.0.0.1:18180"
    expected_target = tailnet.GLOBAL_SERVE_HANDLERS["/stats/embed"]
    handlers: dict[str, dict[str, str]] = {
        "/": {"Proxy": "http://127.0.0.1:5173"},
        "/stats/embed": {"Proxy": legacy_target},
        **{
            path: {"Proxy": target}
            for path, target in tailnet.GLOBAL_SERVE_HANDLERS.items()
            if path != "/stats/embed"
        },
    }

    def snapshot() -> tailnet.ServeSnapshot:
        return tailnet.ServeSnapshot(
            origin="https://node.example.ts.net",
            root_proxy=handlers["/"]["Proxy"],
            handlers={path: dict(handler) for path, handler in handlers.items()},
            raw={"handlers": {path: dict(handler) for path, handler in handlers.items()}},
        )

    set_calls: list[tuple[str, str]] = []

    def set_path(path: str, target: str) -> None:
        set_calls.append((path, target))
        handlers[path] = {"Proxy": target}

    monkeypatch.setattr(tailnet, "_read_serve_snapshot", snapshot)
    monkeypatch.setattr(tailnet, "_set_serve_path", set_path)

    installed = tailnet.ensure_global_tailnet_routes()

    assert set_calls == [("/stats/embed", expected_target)]
    assert installed.handlers["/stats/embed"] == {"Proxy": expected_target}


def test_global_tailnet_policy_preserves_reassigned_deprecated_path(monkeypatch) -> None:
    handlers = {
        "/": {"Proxy": "http://127.0.0.1:5173"},
        "/am": {"Proxy": "http://127.0.0.1:9001"},
        **{path: {"Proxy": target} for path, target in tailnet.GLOBAL_SERVE_HANDLERS.items()},
    }

    def snapshot() -> tailnet.ServeSnapshot:
        return tailnet.ServeSnapshot(
            origin="https://node.example.ts.net",
            root_proxy=handlers["/"]["Proxy"],
            handlers={path: dict(handler) for path, handler in handlers.items()},
            raw={"handlers": {path: dict(handler) for path, handler in handlers.items()}},
        )

    monkeypatch.setattr(tailnet, "_read_serve_snapshot", snapshot)
    monkeypatch.setattr(
        tailnet,
        "_clear_serve_path",
        lambda *args: (_ for _ in ()).throw(AssertionError("must preserve reassigned route")),
    )

    installed = tailnet.ensure_global_tailnet_routes()
    assert installed.handlers["/am"] == {"Proxy": "http://127.0.0.1:9001"}


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


def _mock_control_policy_prerequisites(monkeypatch, *, matches: bool) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(tailnet, "_validate_canonical_environment_root", lambda root, environment: None)
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: "a" * 40)
    monkeypatch.setattr(
        tailnet,
        "_install_adapter_control_policy",
        lambda root, revision: "operator@example.test",
    )
    monkeypatch.setattr(
        tailnet,
        "daemon_reload",
        lambda project_root=None: calls.append(("daemon-reload", str(project_root))),
    )
    monkeypatch.setattr(tailnet, "service_is_active", lambda service, project_root=None: True)
    monkeypatch.setattr(
        tailnet,
        "_adapter_identity_policy_matches",
        lambda root, login, runtime_revision: matches,
    )
    monkeypatch.setattr(
        tailnet,
        "run_systemctl",
        lambda action, service, project_root=None: calls.append((action, service)),
    )
    monkeypatch.setattr(tailnet, "_wait_for_adapter_policy", lambda *args, **kwargs: True)
    return calls


def test_control_policy_installer_is_idempotent_for_matching_adapter(monkeypatch, tmp_path) -> None:
    calls = _mock_control_policy_prerequisites(monkeypatch, matches=True)

    report = tailnet.ensure_tailnet_control_policy(tmp_path)

    assert report["adapter_restarted"] is False
    assert [call for call in calls if call[0] == "restart"] == []


def test_control_policy_installer_restarts_stale_active_adapter(monkeypatch, tmp_path) -> None:
    calls = _mock_control_policy_prerequisites(monkeypatch, matches=False)

    report = tailnet.ensure_tailnet_control_policy(tmp_path)

    assert report["adapter_restarted"] is True
    assert ("restart", tailnet.WORKFLOW_ADAPTER_SERVICE) in calls
