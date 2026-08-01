from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def load_module():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    script_path = REPO_ROOT / "scripts" / "manage_desktop_services.py"
    spec = importlib.util.spec_from_file_location("manage_desktop_services_under_test", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_start_target_action_invokes_dev_prod_both_service_abstraction(monkeypatch) -> None:
    module = load_module()
    started: list[str] = []

    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "start-target", "--target", "both"])
    monkeypatch.setattr(module, "start_runtime_target", lambda target=None: started.append(target or "missing"))
    monkeypatch.setattr(
        module,
        "select_tailnet_environment",
        lambda environment=None: (_ for _ in ()).throw(
            AssertionError("both must not change the selected Tailnet environment")
        ),
    )
    monkeypatch.setattr(module, "status_lines", lambda runtime_mode=None: [f"status:{runtime_mode}"])

    assert module.main() == 0
    assert started == ["both"]


def test_dev_start_target_refreshes_selected_development_environment(monkeypatch) -> None:
    module = load_module()
    actions: list[tuple[str, str]] = []

    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "start-target", "--target", "dev"])
    monkeypatch.setattr(
        module,
        "start_runtime_target",
        lambda target=None: actions.append(("start", target or "missing")),
    )
    monkeypatch.setattr(
        module,
        "select_tailnet_environment",
        lambda environment=None: actions.append(("select", environment or "missing")),
    )

    assert module.main() == 0
    assert actions == [("start", "dev"), ("select", "development")]
