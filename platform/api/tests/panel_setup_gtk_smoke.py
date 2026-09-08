"""Real GTK4/Adw smoke: xvfb-run -a /usr/bin/python3 <this file>.

No service changes or installs: only python-plan is executed. Mutation confirmation
is exercised with Cancel and with an intercepted runner on Continue.
"""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import biomodstack_panel as panel_module
from gi.repository import Gtk, GLib

Gtk.init()
panel = panel_module.BioModStackPanel()
panel.window = Gtk.Window()
section = panel._build_setup_section()
panel.window.set_child(section)
# No present on the user's display; this test is intended for isolated Xvfb.
texts = []

def walk(widget):
    for getter in ("get_title", "get_label", "get_subtitle", "get_description"):
        method = getattr(widget, getter, None)
        if method:
            value = method()
            if isinstance(value, str):
                texts.append(value)
    assert not isinstance(widget, Gtk.PasswordEntry)
    child = widget.get_first_child()
    while child:
        walk(child)
        child = child.get_next_sibling()

walk(section)
assert "Setup" in texts
assert "Run" in texts
assert "Details" in texts
model = panel.setup_action_combo.get_model()
assert len(model) == len(panel_module.SETUP_ACTIONS)
assert panel.setup_action_combo.get_active_id() == "discover"
assert panel.setup_action_combo.get_active_text() == "Check system"
assert not section.get_description()
for text in texts + list(panel_module.SETUP_ACTIONS.values()) + list(panel_module.SETUP_MUTATIONS.values()):
    assert not any(term in text.lower() for term in
        ("admin password", "scientific provisioning", "artifact", "installer-owned", "external root", "authority"))
assert not hasattr(panel, "cached_sudo_password")

panel_module.show_notification = lambda *args: None
panel._refresh_status_once = lambda: None
panel._update_dev_updates_control = lambda: None
panel.setup_action_combo.set_active_id("python-plan")
panel._on_setup_action(None)
context = GLib.MainContext.default()
deadline = time.monotonic() + 30
while getattr(panel, "_service_action_active", False) and time.monotonic() < deadline:
    context.iteration(False)
    time.sleep(0.02)
assert not panel._service_action_active, "Read-only setup action did not complete"
buffer = panel.setup_output.get_buffer()
report = json.loads(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False))
assert report["action"] == "python-plan"
assert "exit " in panel.setup_status_row.get_subtitle()

calls = []
panel._run_service_action = lambda *args: calls.append(args)
for action in panel_module.SETUP_MUTATIONS:
    panel.setup_action_combo.set_active_id(action)
    panel.setup_entries["document"].set_text("/tmp/reviewed install.json")
    panel.setup_entries["operation"].set_text("reviewed-operation")
    for response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.ACCEPT):
        before = len(calls)
        panel._on_setup_action(None)
        dialogs = [window for window in Gtk.Window.list_toplevels() if isinstance(window, Gtk.MessageDialog)]
        assert len(dialogs) == 1
        dialogs[0].response(response)
        assert len(calls) == before + (response == Gtk.ResponseType.ACCEPT)
        if response == Gtk.ResponseType.ACCEPT:
            assert calls[-1][0] == "Setup: " + panel_module.SETUP_ACTIONS[action]
            assert calls[-1][1] == panel_module.build_setup_command(action,
                document="/tmp/reviewed install.json", operation="reviewed-operation")
panel.window.destroy()
print(json.dumps({"gtk": "passed", "setup_actions": len(model),
    "password_widgets": 0, "mutation_confirmations": len(calls),
    "real_read_only_action": report["action"], "read_only_status": report["status"],
    "visible_labels": sorted(set(texts))}, indent=2))
