from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_module():
    module_path = REPO_ROOT / "biomodstack_panel_compat.py"
    spec = importlib.util.spec_from_file_location("biomodstack_panel_compat", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeActionRow:
    def __init__(self):
        self.title = None
        self.subtitle = None
        self.suffixes = []
        self.activatable_widget = None

    def set_title(self, value):
        self.title = value

    def set_subtitle(self, value):
        self.subtitle = value

    def add_suffix(self, widget):
        self.suffixes.append(widget)

    def set_activatable_widget(self, widget):
        self.activatable_widget = widget


class FakeSwitchRow(FakeActionRow):
    def __init__(self):
        super().__init__()
        self.active = None
        self.connections = []

    def set_active(self, value):
        self.active = value

    def get_active(self):
        return self.active

    def connect(self, signal_name, handler):
        self.connections.append((signal_name, handler))


class FakeSwitch:
    def __init__(self):
        self.active = None
        self.connections = []
        self.valign = None

    def set_active(self, value):
        self.active = value

    def get_active(self):
        return self.active

    def connect(self, signal_name, handler):
        self.connections.append((signal_name, handler))

    def set_valign(self, value):
        self.valign = value


class FakeAdwWithSwitchRow:
    ActionRow = FakeActionRow
    SwitchRow = FakeSwitchRow


class FakeAdwWithoutSwitchRow:
    ActionRow = FakeActionRow


class FakeGtk:
    Switch = FakeSwitch

    class Align:
        CENTER = "center"


def test_build_toggle_row_prefers_switch_row_when_available() -> None:
    module = load_module()

    row, control = module.build_toggle_row(
        FakeAdwWithSwitchRow,
        FakeGtk,
        title="Autostart on Login",
        subtitle="Launch control panel when you log in",
        active=True,
        handler=lambda *_args: None,
    )

    assert isinstance(row, FakeSwitchRow)
    assert control is row
    assert row.title == "Autostart on Login"
    assert row.subtitle == "Launch control panel when you log in"
    assert row.active is True
    assert row.connections[0][0] == "notify::active"


def test_build_toggle_row_falls_back_to_action_row_plus_switch_when_switch_row_is_missing() -> None:
    module = load_module()

    row, control = module.build_toggle_row(
        FakeAdwWithoutSwitchRow,
        FakeGtk,
        title="Desktop Notifications",
        subtitle="Show notifications for service events",
        active=False,
        handler=lambda *_args: None,
    )

    assert isinstance(row, FakeActionRow)
    assert isinstance(control, FakeSwitch)
    assert row.title == "Desktop Notifications"
    assert row.subtitle == "Show notifications for service events"
    assert row.suffixes == [control]
    assert row.activatable_widget is control
    assert control.active is False
    assert control.valign == FakeGtk.Align.CENTER
    assert control.connections[0][0] == "notify::active"
