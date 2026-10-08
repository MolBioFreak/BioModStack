#!/usr/bin/env python3
"""
BioModStack Control Panel

A GTK4/Libadwaita control panel for managing BioModStack services.
Provides a native GNOME-style interface for:
- Service status monitoring
- Log viewing
- Database management
- Service control
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

import os
import sys
import subprocess
import threading
import sqlite3
import shutil
import webbrowser
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from gi.repository import Gtk, Adw, GLib, Gio, Pango

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

API_ROOT = Path(__file__).parent / "platform" / "api"
sys.path.insert(0, str(API_ROOT))
from paths import get_code_root, get_db_path, get_results_dir  # noqa: E402

PROJECT_ROOT = get_code_root()
API_PORT = 8000
FRONTEND_PORT = 5173
API_URL = f"http://localhost:{API_PORT}"
FRONTEND_URL = f"http://localhost:{FRONTEND_PORT}/bms/"

# Paths
DB_PATH = get_db_path()
RESULTS_DIR = get_results_dir()
API_LOG = Path("/tmp/biomodstack_api.log")
FRONTEND_LOG = Path("/tmp/biomodstack_frontend.log")
ICON_PATH = PROJECT_ROOT / "platform" / "assets" / "icons" / "biomodstack_tray.png"
CONFIG_PATH = Path.home() / ".config" / "biomodstack" / "panel_config.json"
AUTOSTART_PATH = Path.home() / ".config" / "autostart" / "biomodstack-panel.desktop"

# Scripts
START_SCRIPT = PROJECT_ROOT / "start_ui.sh"
RESTART_API_SCRIPT = PROJECT_ROOT / "restart_api.sh"  
STOP_SCRIPT = PROJECT_ROOT / "stop_services.sh"

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"autostart": True, "notifications": True, "log_lines": 30}

def save_config(config: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

# ═══════════════════════════════════════════════════════════════════════════════
# SERVICE STATUS
# ═══════════════════════════════════════════════════════════════════════════════

def check_api_status() -> bool:
    try:
        import urllib.request
        req = urllib.request.Request(f"{API_URL}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        try:
            result = subprocess.run(["pgrep", "-f", f"uvicorn.*:{API_PORT}"],
                                    capture_output=True, timeout=2)
            return result.returncode == 0
        except Exception:
            return False

def check_frontend_status() -> bool:
    """Check if Frontend dev server is running."""
    # First try HTTP check
    try:
        import urllib.request
        req = urllib.request.Request(f"{FRONTEND_URL}", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        pass
    
    # Fallback: check for vite process (without port - vite doesn't show port in process name)
    try:
        result = subprocess.run(["pgrep", "-f", "vite"],
                                capture_output=True, timeout=2)
        return result.returncode == 0
    except Exception:
        return False

def get_job_counts() -> tuple:
    try:
        if not DB_PATH.exists():
            return (0, 0, 0)
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        cur = conn.cursor()
        cur.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
        counts = dict(cur.fetchall())
        conn.close()
        return (counts.get("running", 0), counts.get("queued", 0), sum(counts.values()))
    except Exception:
        return (0, 0, 0)

def get_db_info() -> dict:
    try:
        if not DB_PATH.exists():
            return {"error": "Not found"}
        size_mb = DB_PATH.stat().st_size / (1024 * 1024)
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM jobs")
        jobs = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM designs")
        designs = cur.fetchone()[0]
        journal_mode = cur.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = cur.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        return {
            "size_mb": round(size_mb, 2),
            "jobs": jobs,
            "designs": designs,
            "journal_mode": journal_mode,
            "busy_timeout": busy_timeout,
        }
    except Exception as e:
        return {"error": str(e)}

def read_log_tail(log_path: Path, lines: int = 30) -> str:
    try:
        if not log_path.exists():
            return f"Log file not found: {log_path.name}"
        result = subprocess.run(["tail", "-n", str(lines), str(log_path)],
                                capture_output=True, text=True, timeout=5)
        return result.stdout or "(empty)"
    except Exception as e:
        return f"Error reading log: {e}"

# ═══════════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def show_notification(title: str, message: str):
    config = load_config()
    if not config.get("notifications", True):
        return
    try:
        subprocess.run([
            "notify-send", "-i", str(ICON_PATH) if ICON_PATH.exists() else "dialog-information",
            f"BioModStack: {title}", message
        ], timeout=5)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

class BioModStackPanel(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.biomodstack.panel",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None
        self.config = load_config()
        self.current_log = "api"
        
    def do_activate(self):
        if self.window:
            self.window.present()
            return
        
        self.window = Adw.ApplicationWindow(application=self)
        self.window.set_title("BioModStack Control Panel")
        self.window.set_default_size(500, 650)
        self.window.set_icon_name("biomodstack")
        
        # Try to load custom icon
        if ICON_PATH.exists():
            try:
                from gi.repository import GdkPixbuf
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(ICON_PATH))
                # GTK4 uses paintables, but we can set window icon via file
            except Exception:
                pass
        
        # Main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.set_content(main_box)
        
        # Header bar
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="BioModStack Control Panel"))
        main_box.append(header)
        
        # Scrollable content
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        main_box.append(scroll)
        
        # Content box with margins
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        scroll.set_child(content)
        
        # Build UI sections
        content.append(self._build_status_section())
        content.append(self._build_actions_section())
        content.append(self._build_logs_section())
        content.append(self._build_database_section())
        content.append(self._build_settings_section())
        
        # Start status refresh timer
        GLib.timeout_add_seconds(5, self._refresh_status)
        
        self.window.present()
    
    def _build_status_section(self) -> Gtk.Widget:
        """Status indicators section."""
        group = Adw.PreferencesGroup()
        group.set_title("Status")
        
        # Status row
        self.status_row = Adw.ActionRow()
        self.status_row.set_title("Services")
        self._update_status_row()
        group.add(self.status_row)
        
        # Jobs row
        self.jobs_row = Adw.ActionRow()
        self.jobs_row.set_title("Jobs")
        self._update_jobs_row()
        group.add(self.jobs_row)
        
        return group
    
    def _update_status_row(self):
        api_ok = check_api_status()
        frontend_ok = check_frontend_status()
        
        api_icon = "✓" if api_ok else "✗"
        frontend_icon = "✓" if frontend_ok else "✗"
        api_color = "green" if api_ok else "red"
        frontend_color = "green" if frontend_ok else "red"
        
        self.status_row.set_subtitle(
            f"<span foreground='{api_color}'>API {api_icon}</span>  |  "
            f"<span foreground='{frontend_color}'>Frontend {frontend_icon}</span>"
        )
        self.status_row.set_subtitle_lines(1)
        # Enable markup - GTK4 way
        subtitle_label = self.status_row.get_last_child()
        while subtitle_label:
            if isinstance(subtitle_label, Gtk.Label):
                subtitle_label.set_use_markup(True)
            subtitle_label = subtitle_label.get_prev_sibling()
    
    def _update_jobs_row(self):
        running, queued, total = get_job_counts()
        self.jobs_row.set_subtitle(f"{running} running  |  {queued} queued  |  {total} total")
    
    def _build_actions_section(self) -> Gtk.Widget:
        """Quick action buttons."""
        group = Adw.PreferencesGroup()
        group.set_title("Quick Actions")
        
        # Button box
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(8)
        button_box.set_margin_bottom(8)
        
        # Open UI button
        btn_ui = Gtk.Button(label="🌐 Open UI")
        btn_ui.add_css_class("suggested-action")
        btn_ui.connect("clicked", lambda _: webbrowser.open(FRONTEND_URL))
        button_box.append(btn_ui)
        
        # Results folder
        btn_results = Gtk.Button(label="📂 Results")
        btn_results.connect("clicked", lambda _: subprocess.Popen(["xdg-open", str(RESULTS_DIR)]))
        button_box.append(btn_results)
        
        # Start services (new!)
        btn_start = Gtk.Button(label="▶ Start")
        btn_start.connect("clicked", self._on_start_all)
        button_box.append(btn_start)
        
        # Restart all
        btn_restart = Gtk.Button(label="🔄 Restart")
        btn_restart.connect("clicked", self._on_restart_all)
        button_box.append(btn_restart)
        
        # Stop all
        btn_stop = Gtk.Button(label="⏹ Stop")
        btn_stop.add_css_class("destructive-action")
        btn_stop.connect("clicked", self._on_stop_all)
        button_box.append(btn_stop)
        
        # Wrap in a simple row
        row = Adw.ActionRow()
        row.set_child(button_box)
        group.add(row)
        
        return group
    
    def _on_start_all(self, button):
        show_notification("Starting", "Starting all services...")
        subprocess.Popen(["bash", str(START_SCRIPT)])
        GLib.timeout_add_seconds(5, self._refresh_status)
    
    def _on_restart_all(self, button):
        show_notification("Restarting", "Restarting all services...")
        subprocess.Popen(["bash", str(START_SCRIPT), "restart"])
        GLib.timeout_add_seconds(3, self._refresh_status)
    
    def _on_stop_all(self, button):
        show_notification("Stopping", "Stopping all services...")
        subprocess.Popen(["bash", str(STOP_SCRIPT)])
        GLib.timeout_add_seconds(2, self._refresh_status)
    
    def _build_logs_section(self) -> Gtk.Widget:
        """Log viewer section."""
        group = Adw.PreferencesGroup()
        group.set_title("Logs")
        
        # Log selector buttons
        selector_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        selector_box.set_halign(Gtk.Align.START)
        selector_box.set_margin_bottom(8)
        
        self.btn_api_log = Gtk.ToggleButton(label="API Log")
        self.btn_api_log.set_active(True)
        self.btn_api_log.connect("toggled", self._on_log_toggle, "api")
        selector_box.append(self.btn_api_log)
        
        self.btn_frontend_log = Gtk.ToggleButton(label="Frontend Log")
        self.btn_frontend_log.connect("toggled", self._on_log_toggle, "frontend")
        selector_box.append(self.btn_frontend_log)
        
        btn_refresh = Gtk.Button(label="↻")
        btn_refresh.set_tooltip_text("Refresh log")
        btn_refresh.connect("clicked", lambda _: self._refresh_log())
        selector_box.append(btn_refresh)
        
        btn_external = Gtk.Button(label="📄 Open Full")
        btn_external.connect("clicked", self._open_log_external)
        selector_box.append(btn_external)
        
        row_selector = Adw.ActionRow()
        row_selector.set_child(selector_box)
        group.add(row_selector)
        
        # Log view
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_view.add_css_class("log-view")
        
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(200)
        log_scroll.set_max_content_height(250)
        log_scroll.set_child(self.log_view)
        
        # Frame for log
        frame = Gtk.Frame()
        frame.set_child(log_scroll)
        
        row_log = Adw.ActionRow()
        row_log.set_child(frame)
        group.add(row_log)
        
        self._refresh_log()
        
        return group
    
    def _on_log_toggle(self, button, log_type):
        if button.get_active():
            self.current_log = log_type
            # Deselect other button
            if log_type == "api":
                self.btn_frontend_log.set_active(False)
            else:
                self.btn_api_log.set_active(False)
            self._refresh_log()
    
    def _refresh_log(self):
        log_path = API_LOG if self.current_log == "api" else FRONTEND_LOG
        content = read_log_tail(log_path, self.config.get("log_lines", 30))
        buffer = self.log_view.get_buffer()
        buffer.set_text(content)
        # Scroll to bottom
        GLib.idle_add(self._scroll_log_to_bottom)
    
    def _scroll_log_to_bottom(self):
        buffer = self.log_view.get_buffer()
        end_iter = buffer.get_end_iter()
        self.log_view.scroll_to_iter(end_iter, 0, False, 0, 0)
        return False
    
    def _open_log_external(self, button):
        log_path = API_LOG if self.current_log == "api" else FRONTEND_LOG
        if log_path.exists():
            subprocess.Popen(["xdg-open", str(log_path)])
        else:
            show_notification("Error", f"Log file not found: {log_path.name}")
    
    def _build_database_section(self) -> Gtk.Widget:
        """Database info and actions."""
        group = Adw.PreferencesGroup()
        group.set_title("Database")
        
        # Info row
        self.db_info_row = Adw.ActionRow()
        self.db_info_row.set_title("Statistics")
        self._update_db_info()
        group.add(self.db_info_row)
        
        # Action buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_box.set_halign(Gtk.Align.START)
        button_box.set_margin_top(4)
        
        btn_backup = Gtk.Button(label="💾 Backup Now")
        btn_backup.connect("clicked", self._on_backup_db)
        button_box.append(btn_backup)
        
        btn_location = Gtk.Button(label="📂 Open Location")
        btn_location.connect("clicked", lambda _: subprocess.Popen(["xdg-open", str(DB_PATH.parent)]))
        button_box.append(btn_location)
        
        row_buttons = Adw.ActionRow()
        row_buttons.set_child(button_box)
        group.add(row_buttons)
        
        return group
    
    def _update_db_info(self):
        info = get_db_info()
        if "error" in info:
            self.db_info_row.set_subtitle(f"Error: {info['error']}")
        else:
            journal = info.get("journal_mode", "?")
            busy = info.get("busy_timeout", "?")
            self.db_info_row.set_subtitle(
                f"Jobs: {info['jobs']:,}  |  Designs: {info['designs']:,}  |  Size: {info['size_mb']} MB  |  {journal.upper()}  |  busy {busy}ms"
            )
    
    def _on_backup_db(self, button):
        if not DB_PATH.exists():
            show_notification("Error", "Database not found")
            return
        
        backup_dir = PROJECT_ROOT / "backups"
        backup_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"biomodstack_backup_{timestamp}.db"
        
        try:
            shutil.copy2(DB_PATH, backup_path)
            size_mb = backup_path.stat().st_size / (1024 * 1024)
            show_notification("Backup Complete", f"Saved: {backup_path.name} ({size_mb:.1f} MB)")
        except Exception as e:
            show_notification("Backup Failed", str(e))
    
    def _build_settings_section(self) -> Gtk.Widget:
        """Settings toggles."""
        group = Adw.PreferencesGroup()
        group.set_title("Settings")
        
        # Autostart toggle
        self.autostart_row = Adw.SwitchRow()
        self.autostart_row.set_title("Autostart on Login")
        self.autostart_row.set_subtitle("Launch control panel when you log in")
        self.autostart_row.set_active(AUTOSTART_PATH.exists())
        self.autostart_row.connect("notify::active", self._on_autostart_toggle)
        group.add(self.autostart_row)
        
        # Notifications toggle
        self.notif_row = Adw.SwitchRow()
        self.notif_row.set_title("Desktop Notifications")
        self.notif_row.set_subtitle("Show notifications for service events")
        self.notif_row.set_active(self.config.get("notifications", True))
        self.notif_row.connect("notify::active", self._on_notifications_toggle)
        group.add(self.notif_row)
        
        return group
    
    def _on_autostart_toggle(self, row, param):
        if row.get_active():
            AUTOSTART_PATH.parent.mkdir(parents=True, exist_ok=True)
            desktop_entry = f"""[Desktop Entry]
Type=Application
Name=BioModStack Panel
Comment=BioModStack Control Panel
Exec=python3 {Path(__file__).resolve()}
Icon={ICON_PATH}
Terminal=false
Categories=Science;System;
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""
            AUTOSTART_PATH.write_text(desktop_entry)
        else:
            if AUTOSTART_PATH.exists():
                AUTOSTART_PATH.unlink()
    
    def _on_notifications_toggle(self, row, param):
        self.config["notifications"] = row.get_active()
        save_config(self.config)
    
    def _refresh_status(self):
        """Refresh all status displays."""
        self._update_status_row()
        self._update_jobs_row()
        self._update_db_info()
        return True  # Keep timer running

# ═══════════════════════════════════════════════════════════════════════════════
# CSS STYLING
# ═══════════════════════════════════════════════════════════════════════════════

CSS = """
.log-view {
    font-family: monospace;
    font-size: 11px;
    background-color: #1e1e2e;
    color: #cdd6f4;
    padding: 8px;
}
"""

def load_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS.encode())
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Import Gdk for CSS
    from gi.repository import Gdk
    
    app = BioModStackPanel()
    
    # Load custom CSS after app creation
    def on_activate(app):
        load_css()
    app.connect("activate", on_activate)
    
    app.run(sys.argv)
