"""Real GTK widget/async smoke under Xvfb; all nine read-only CLIs run.

Mutation Cancel/Continue routing is intercepted, NOT installer acceptance.
Set BMS_PANEL_SMOKE_ROOT to inspect a deployed checkout; output is evidence JSON.
"""
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(os.environ.get('BMS_PANEL_SMOKE_ROOT', Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(ROOT))
import biomodstack_panel as module
from gi.repository import Gtk, GLib

Gtk.init()
panel = module.BioModStackPanel()
panel.window = Gtk.Window()
panel.window.set_child(panel._build_setup_section())
panel.window.present()
module.show_notification = lambda *args: None
panel._refresh_status_once = lambda: None
panel._update_dev_updates_control = lambda: None
context = GLib.MainContext.default()

def text():
    buffer = panel.setup_output.get_buffer()
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

def pump():
    deadline = time.monotonic() + 120
    while getattr(panel, '_service_action_active', False) and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(.01)
    assert not panel._service_action_active, 'CLI did not complete'
    assert text().strip()
    assert panel.setup_run_button.get_sensitive()

assert 'No action has been run' in text()
assert panel.setup_details_row.get_expanded()
results = []
for action in module.SETUP_ACTIONS:
    panel.setup_action_combo.set_active_id(action)
    assert panel.setup_help.get_text() == module.SETUP_HELP[action]
    assert 'No action has been run' in text()
    expected = module.SETUP_INPUTS.get(action, ())
    assert {key for key, row in panel.setup_input_rows.items() if row.get_visible()} == set(expected)
    assert panel.setup_options.get_visible() == bool(expected)
    if action in module.SETUP_MUTATIONS:
        continue
    # A missing file is intentional: exercise actual safe CLI validation, not a
    # pretend host configuration. Valid configuration acceptance belongs in a sandbox.
    panel.setup_entries['document'].set_text('/nonexistent-bms-smoke/settings.json')
    panel.setup_entries['models'].set_text('protenix')
    panel.setup_run_button.emit('clicked')
    assert 'Working' in text()
    assert not panel.setup_run_button.get_sensitive()
    pump()
    output = text()
    report = json.loads(output)
    results.append({'action': action, 'scope': 'actual read-only CLI through GTK',
                    'status': panel.setup_status_row.get_subtitle(), 'report': report})

calls = []
runner = panel._run_service_action
panel._run_service_action = lambda *args: calls.append(args)
for action in module.SETUP_MUTATIONS:
    panel.setup_action_combo.set_active_id(action)
    panel.setup_entries['document'].set_text('/tmp/reviewed install.json')
    panel.setup_entries['operation'].set_text('reviewed-operation')
    for response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.ACCEPT):
        before = len(calls)
        panel.setup_run_button.emit('clicked')
        assert 'Nothing changed yet' in panel.setup_status_row.get_subtitle()
        dialogs = [w for w in Gtk.Window.list_toplevels() if isinstance(w, Gtk.MessageDialog)]
        assert len(dialogs) == 1
        dialogs[0].response(response)
        assert len(calls) == before + (response == Gtk.ResponseType.ACCEPT)
        if response == Gtk.ResponseType.CANCEL:
            assert 'was not run' in text()
        else:
            assert calls[-1][1] == module.build_setup_command(action,
                document='/tmp/reviewed install.json', operation='reviewed-operation')
    results.append({'action': action, 'scope': 'GTK confirmation and intercepted routing only', 'status': 'passed'})

for action, key in [('configure', 'document'), ('recover', 'operation'), ('verify', 'models')]:
    panel.setup_action_combo.set_active_id(action)
    panel.setup_entries[key].set_text('')
    before = len(calls)
    panel.setup_run_button.emit('clicked')
    assert len(calls) == before
    assert 'Not started' in panel.setup_status_row.get_subtitle()
    assert text().strip() and panel.setup_details_row.get_expanded()

panel._run_service_action = runner
for result, error in [(SimpleNamespace(returncode=0, stdout='', stderr=''), None),
                      (None, OSError('smoke launch failure')),
                      (SimpleNamespace(returncode=3, stdout='failure diagnostic', stderr=''), None)]:
    panel._finish_service_action('Setup: smoke', result, error)
    assert text().strip() and panel.setup_details_row.get_expanded()
    assert panel.setup_run_button.get_sensitive()
assert len(results) == len(module.SETUP_ACTIONS) == 13
panel.window.destroy()
print(json.dumps({'gtk': 'passed', 'actions': results, 'validation': 'passed',
                  'empty_output_and_launch_failure': 'passed', 'initial_details': 'passed'}, indent=2))
