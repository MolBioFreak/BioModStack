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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from gi.repository import Gtk, Adw, GLib, Gio, Pango

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

API_ROOT = Path(__file__).parent / "platform" / "api"
sys.path.insert(0, str(API_ROOT))
from paths import get_code_root, get_db_path, get_results_dir  # noqa: E402
from biomodstack_panel_compat import build_toggle_row  # noqa: E402
from biomodstack_services import (  # noqa: E402
    API_LOG as API_LOG_PATH,
    API_SERVICE,
    CORE_RUNTIME_LOG as CORE_RUNTIME_LOG_PATH,
    DEV_RUNTIME_MODE,
    FRONTEND_LOG as FRONTEND_LOG_PATH,
    FRONTEND_SERVICE,
    WORKFLOW_ADAPTER_LOG as WORKFLOW_ADAPTER_LOG_PATH,
    build_launch_ui_command,
    operator_frontend_url,
    operator_runtime_mode,
    runtime_port_settings,
    save_runtime_port_settings,
    service_is_active,
)

PROJECT_ROOT = get_code_root()
API_PORT = 8000
FRONTEND_PORT = 5173
API_URL = f"http://localhost:{API_PORT}"

# Paths
DB_PATH = get_db_path()
RESULTS_DIR = get_results_dir()
API_LOG = API_LOG_PATH
FRONTEND_LOG = FRONTEND_LOG_PATH
WORKFLOW_ADAPTER_LOG = WORKFLOW_ADAPTER_LOG_PATH
CORE_RUNTIME_LOG = CORE_RUNTIME_LOG_PATH
LOG_PATHS = {
    "api": API_LOG,
    "frontend": FRONTEND_LOG,
    "workflow-adapter": WORKFLOW_ADAPTER_LOG,
    "core-runtime": CORE_RUNTIME_LOG,
}
LOG_LABELS = {
    "api": "API Backend Log",
    "frontend": "Frontend/Web Log",
    "workflow-adapter": "Workflow Adapter Log",
    "core-runtime": "Container Runtime Log",
}
LOG_HELP_TEXT = {
    "api": "FastAPI/backend app output. In stable container mode this tails docker logs for biomodstack-api; in dev mode it falls back to api.log.",
    "frontend": "Web UI output. In stable container mode this tails docker logs for biomodstack-web; in dev mode it falls back to frontend.log/Vite output.",
    "workflow-adapter": "Advanced: host-side bridge for workflow/GPU/Nextflow operations that containers should not own directly.",
    "core-runtime": "Advanced: docker compose lifecycle log for starting/stopping the stable container stack, not the API request log.",
}
CONTAINER_LOG_SOURCES = {
    "api": ("biomodstack-api", API_LOG),
    "frontend": ("biomodstack-web", FRONTEND_LOG),
}
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
            return service_is_active(API_SERVICE)
        except Exception:
            pass
        try:
            result = subprocess.run(["pgrep", "-f", f"uvicorn.*:{API_PORT}"],
                                    capture_output=True, timeout=2)
            return result.returncode == 0
        except Exception:
            return False

def check_frontend_status() -> bool:
    """Check if the frontend is responding on the active runtime hosted-web URL."""
    frontend_url = operator_frontend_url(project_root=PROJECT_ROOT)
    try:
        import urllib.request
        req = urllib.request.Request(frontend_url, method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        runtime_mode = operator_runtime_mode(project_root=PROJECT_ROOT)
        if runtime_mode == DEV_RUNTIME_MODE:
            try:
                return service_is_active(FRONTEND_SERVICE)
            except Exception:
                pass

            try:
                result = subprocess.run(["pgrep", "-f", f"vite.*{FRONTEND_PORT}"],
                                        capture_output=True, timeout=2)
                return result.returncode == 0
            except Exception:
                return False
        return False

STATUS_DB_TIMEOUT_SECONDS = 0.25

def _connect_status_db() -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=STATUS_DB_TIMEOUT_SECONDS)
    except Exception:
        conn = sqlite3.connect(str(DB_PATH), timeout=STATUS_DB_TIMEOUT_SECONDS)
    conn.execute("PRAGMA busy_timeout = 250")
    return conn

def get_job_counts() -> tuple:
    try:
        if not DB_PATH.exists():
            return (0, 0, 0)
        conn = _connect_status_db()
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
        conn = _connect_status_db()
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


def read_container_log_tail(container_name: str, fallback_path: Path, lines: int = 30) -> str:
    """Tail a stable-runtime docker container log, falling back to the dev log file."""
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(lines), container_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        result = None
        docker_error = "docker command not available; showing file log fallback."
    except Exception as exc:
        result = None
        docker_error = f"docker log read failed: {exc}; showing file log fallback."
    else:
        if result.returncode == 0:
            output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
            return output or f"({container_name} log empty)"
        docker_error = (result.stderr or result.stdout or f"docker logs exited {result.returncode}").strip()

    fallback = read_log_tail(fallback_path, lines)
    return f"[{container_name}] {docker_error}\n\n--- fallback: {fallback_path} ---\n{fallback}"


def read_named_log_tail(log_type: str, lines: int = 30) -> str:
    if log_type in CONTAINER_LOG_SOURCES:
        container_name, fallback_path = CONTAINER_LOG_SOURCES[log_type]
        return read_container_log_tail(container_name, fallback_path, lines)
    log_path = LOG_PATHS.get(log_type, CORE_RUNTIME_LOG)
    return read_log_tail(log_path, lines)


def call_local_api_json(method: str, path: str, payload: Optional[dict] = None, timeout: float = 8.0) -> dict:
    """Call the local BMS API from the native control panel."""
    import urllib.error
    import urllib.request

    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API_URL}{path}", data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
        except Exception:
            detail = body
        raise RuntimeError(f"{method.upper()} {path} failed ({exc.code}): {detail}") from exc


def get_bioxp_interlink_state(probe: bool = False, timeout: float = 3.0) -> dict:
    query = "?probe=true" if probe else ""
    return call_local_api_json("GET", f"/api/bioxp/interlink/state{query}", timeout=timeout)


def _bioxp_probe_is_fresh(last_probe_at: object, window_seconds: int = 60) -> bool:
    if not last_probe_at:
        return False
    try:
        parsed = datetime.fromisoformat(str(last_probe_at).replace("Z", "+00:00"))
    except Exception:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    return 0 <= age_seconds <= window_seconds


def summarize_bioxp_interlink_state(state: dict) -> str:
    if not state:
        return "BMS API unavailable"
    active = bool(state.get("active"))
    configured = bool(state.get("configured"))
    reachable = state.get("reachable")
    hardware = state.get("hardware_connected")
    probe_stale = bool(state.get("probe_stale"))
    if not probe_stale and active and state.get("last_probe_reachable") is True and reachable is not False:
        window = int(state.get("probe_fresh_window_seconds") or 60)
        probe_stale = not _bioxp_probe_is_fresh(state.get("last_probe_at"), window)
    if active:
        if reachable is False:
            label = "UNREACHABLE"
        elif probe_stale:
            label = "STALE"
        elif reachable is True:
            label = "LINKED"
        else:
            label = "UNVERIFIED"
    elif configured:
        label = "SAVED / inactive"
    else:
        label = "not configured"
    parts = [label]
    if reachable is not None:
        parts.append(f"reachable={'yes' if reachable else 'no'}")
    elif probe_stale:
        parts.append("reachable=stale")
    if hardware is not None:
        parts.append(f"hardware={'yes' if hardware else 'no'}")
    elif probe_stale:
        parts.append("hardware=unknown")
    url = state.get("robot_api_url") or state.get("recommended_url")
    if url:
        parts.append(str(url))
    return "  |  ".join(parts)

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
        self.cached_sudo_password = ""
        
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
        content.append(self._build_privilege_section())
        content.append(self._build_runtime_ports_section())
        content.append(self._build_bioxp_section())
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

    def _load_runtime_port_settings(self) -> dict:
        try:
            return runtime_port_settings(project_root=PROJECT_ROOT)
        except Exception:
            return {
                "dev_web_host_port": 5173,
                "prod_web_host_port": 18080,
                "dev_url": "http://127.0.0.1:5173/",
                "prod_url": "http://127.0.0.1:18080/bms/",
            }

    def _runtime_ports_subtitle(self, settings: dict) -> str:
        return f"Dev {settings['dev_url']}  |  Stable {settings['prod_url']}"

    def _update_runtime_ports_row(self, settings: dict):
        if hasattr(self, "dev_port_entry"):
            self.dev_port_entry.set_text(str(settings["dev_web_host_port"]))
        if hasattr(self, "prod_port_entry"):
            self.prod_port_entry.set_text(str(settings["prod_web_host_port"]))
        if hasattr(self, "runtime_ports_row"):
            self.runtime_ports_row.set_subtitle(self._runtime_ports_subtitle(settings))

    def _build_runtime_ports_section(self) -> Gtk.Widget:
        """Manual dev/prod frontend port configuration."""
        group = Adw.PreferencesGroup()
        group.set_title("Runtime Ports")

        settings = self._load_runtime_port_settings()
        self.runtime_ports_row = Adw.ActionRow()
        self.runtime_ports_row.set_title("Frontend ports")
        self.runtime_ports_row.set_subtitle(self._runtime_ports_subtitle(settings))

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_halign(Gtk.Align.END)

        dev_label = Gtk.Label(label="Dev")
        box.append(dev_label)
        self.dev_port_entry = Gtk.Entry()
        self.dev_port_entry.set_width_chars(6)
        self.dev_port_entry.set_text(str(settings["dev_web_host_port"]))
        box.append(self.dev_port_entry)

        prod_label = Gtk.Label(label="Stable")
        box.append(prod_label)
        self.prod_port_entry = Gtk.Entry()
        self.prod_port_entry.set_width_chars(6)
        self.prod_port_entry.set_text(str(settings["prod_web_host_port"]))
        box.append(self.prod_port_entry)

        apply_button = Gtk.Button(label="Apply")
        apply_button.connect("clicked", self._on_apply_runtime_ports)
        box.append(apply_button)

        self.runtime_ports_row.add_suffix(box)
        group.add(self.runtime_ports_row)
        return group

    def _parse_port_entry(self, entry, label: str) -> int:
        try:
            port = int(str(entry.get_text()).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be an integer TCP port") from exc
        if not 1 <= port <= 65535:
            raise ValueError(f"{label} must be between 1 and 65535")
        return port

    def _on_apply_runtime_ports(self, button):
        try:
            settings = save_runtime_port_settings(
                dev_web_host_port=self._parse_port_entry(self.dev_port_entry, "Dev port"),
                prod_web_host_port=self._parse_port_entry(self.prod_port_entry, "Stable port"),
                project_root=PROJECT_ROOT,
            )
        except Exception as exc:
            show_notification("Runtime Port Error", str(exc))
            return
        self._update_runtime_ports_row(settings)
        show_notification(
            "Runtime Ports Saved",
            "Restart the selected surfaces, then reload Electron/browser to apply the new ports.",
        )

    def _build_bioxp_section(self) -> Gtk.Widget:
        """BioXP robot interlink controls exposed in the native control panel."""
        group = Adw.PreferencesGroup()
        group.set_title("BioXP Robot Runtime")

        self.bioxp_row = Adw.ActionRow()
        self.bioxp_row.set_title("Interlink")
        self._update_bioxp_row(probe=False)
        group.add(self.bioxp_row)

        settings_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        settings_box.set_halign(Gtk.Align.START)
        self.bioxp_url_entry = Gtk.Entry()
        self.bioxp_url_entry.set_width_chars(32)
        self.bioxp_url_entry.set_placeholder_text("Robot API URL, e.g. http://100.124.140.56:8123")
        try:
            state = get_bioxp_interlink_state(probe=False, timeout=1.5)
            self.bioxp_url_entry.set_text(str(state.get("robot_api_url") or state.get("recommended_url") or ""))
        except Exception:
            self.bioxp_url_entry.set_text(os.environ.get("BIOXP_SERVER_URL", ""))
        settings_box.append(self.bioxp_url_entry)

        btn_bioxp_connect = Gtk.Button(label="Connect")
        btn_bioxp_connect.add_css_class("suggested-action")
        btn_bioxp_connect.connect("clicked", self._on_bioxp_connect)
        settings_box.append(btn_bioxp_connect)

        btn_bioxp_disconnect = Gtk.Button(label="Disconnect")
        btn_bioxp_disconnect.connect("clicked", self._on_bioxp_disconnect)
        settings_box.append(btn_bioxp_disconnect)

        settings_row = Adw.ActionRow()
        settings_row.set_title("Robot API endpoint")
        settings_row.set_subtitle("Uses BMS /api/bioxp/interlink/* proxy routes, not the raw container-internal FastAPI port.")
        settings_row.set_child(settings_box)
        group.add(settings_row)

        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        action_box.set_halign(Gtk.Align.START)

        btn_refresh = Gtk.Button(label="Refresh")
        btn_refresh.connect("clicked", self._on_bioxp_refresh)
        action_box.append(btn_refresh)

        btn_diagnostics = Gtk.Button(label="Diagnostics")
        btn_diagnostics.connect("clicked", self._on_bioxp_diagnostics)
        action_box.append(btn_diagnostics)

        btn_logs = Gtk.Button(label="Robot Logs")
        btn_logs.connect("clicked", self._on_bioxp_logs)
        action_box.append(btn_logs)

        btn_runtime_reset = Gtk.Button(label="Restart API Runtime")
        btn_runtime_reset.add_css_class("destructive-action")
        btn_runtime_reset.set_tooltip_text("Requires the BMS API/robot deployment to support /api/bioxp/interlink/runtime-reset.")
        btn_runtime_reset.connect("clicked", self._on_bioxp_runtime_reset)
        action_box.append(btn_runtime_reset)

        action_row = Adw.ActionRow()
        action_row.set_title("Runtime controls")
        action_row.set_subtitle("No homing, arming, USB motion recovery, or axis movement is performed by these controls.")
        action_row.set_child(action_box)
        group.add(action_row)
        return group

    def _bioxp_settings_payload(self) -> dict:
        url = str(self.bioxp_url_entry.get_text()).strip() if hasattr(self, "bioxp_url_entry") else ""
        return {
            "robot_api_url": url,
            "robot_ssh_host": "robot",
            "connection_mode": "direct_http",
            "display_name": "BioXP3200",
        }

    def _update_bioxp_row(self, probe: bool = False):
        if not hasattr(self, "bioxp_row"):
            return
        try:
            state = get_bioxp_interlink_state(probe=probe, timeout=4.0 if probe else 1.5)
            self.bioxp_row.set_subtitle(summarize_bioxp_interlink_state(state))
            if hasattr(self, "bioxp_url_entry") and not str(self.bioxp_url_entry.get_text()).strip():
                self.bioxp_url_entry.set_text(str(state.get("robot_api_url") or state.get("recommended_url") or ""))
        except Exception as exc:
            self.bioxp_row.set_subtitle(f"BMS API/BioXP interlink unavailable: {exc}")

    def _show_bioxp_result(self, title: str, result: dict):
        status = "ok" if result.get("ok", True) is not False and result.get("supported", True) is not False else "not complete"
        detail = result.get("runtime_note") or result.get("reason") or result.get("detail") or summarize_bioxp_interlink_state(result)
        show_notification(title, f"{status}: {detail}")
        if hasattr(self, "log_view"):
            buffer = self.log_view.get_buffer()
            buffer.set_text(json.dumps(result, indent=2, sort_keys=True))

    def _on_bioxp_refresh(self, button):
        self._update_bioxp_row(probe=True)

    def _on_bioxp_connect(self, button):
        payload = self._bioxp_settings_payload()
        if not payload["robot_api_url"]:
            show_notification("BioXP Connect", "Robot API URL is required.")
            return
        try:
            result = call_local_api_json("POST", "/api/bioxp/interlink/connect", payload, timeout=20.0)
        except Exception as exc:
            show_notification("BioXP Connect Failed", str(exc))
            return
        self._update_bioxp_row(probe=False)
        self._show_bioxp_result("BioXP Connected", result)

    def _on_bioxp_disconnect(self, button):
        try:
            result = call_local_api_json("POST", "/api/bioxp/interlink/disconnect", {}, timeout=8.0)
        except Exception as exc:
            show_notification("BioXP Disconnect Failed", str(exc))
            return
        self._update_bioxp_row(probe=False)
        self._show_bioxp_result("BioXP Disconnected", result)

    def _on_bioxp_diagnostics(self, button):
        try:
            result = call_local_api_json("POST", "/api/bioxp/interlink/diagnostics?probe=true", {}, timeout=20.0)
        except Exception as exc:
            show_notification("BioXP Diagnostics Failed", str(exc))
            return
        self._update_bioxp_row(probe=False)
        self._show_bioxp_result("BioXP Diagnostics", result)

    def _on_bioxp_logs(self, button):
        try:
            result = call_local_api_json("POST", "/api/bioxp/interlink/logs", {"tail": 120}, timeout=20.0)
        except Exception as exc:
            show_notification("BioXP Logs Failed", str(exc))
            return
        self._show_bioxp_result("BioXP Logs", result)

    def _on_bioxp_runtime_reset(self, button):
        try:
            result = call_local_api_json(
                "POST",
                "/api/bioxp/interlink/runtime-reset",
                {"operator_ack": "RESET BIOXP RUNTIME", "tail": 120},
                timeout=30.0,
            )
        except Exception as exc:
            show_notification("BioXP Runtime Restart Failed", str(exc))
            return
        self._update_bioxp_row(probe=False)
        self._show_bioxp_result("BioXP Runtime Restart", result)
    
    def _build_actions_section(self) -> Gtk.Widget:
        """Quick action buttons."""
        group = Adw.PreferencesGroup()
        group.set_title("Quick Actions")
        
        # Button box
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(8)
        button_box.set_margin_bottom(8)
        
        # Start-mode selector
        self.runtime_target_combo = Gtk.ComboBoxText()
        self.runtime_target_combo.append("prod", "Prod only")
        self.runtime_target_combo.append("dev", "Dev only")
        self.runtime_target_combo.append("both", "Both")
        self.runtime_target_combo.set_active_id("prod")
        button_box.append(self.runtime_target_combo)

        # Open UI button
        btn_ui = Gtk.Button(label="🖥 Open UI")
        btn_ui.add_css_class("suggested-action")
        btn_ui.connect("clicked", self._on_open_ui)
        button_box.append(btn_ui)

        btn_browser = Gtk.Button(label="🌐 Browser")
        btn_browser.connect("clicked", self._on_open_browser)
        button_box.append(btn_browser)

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

    def _build_privilege_section(self) -> Gtk.Widget:
        """Privileged-action credentials."""
        group = Adw.PreferencesGroup()
        group.set_title("Privileges")

        password_row = Adw.ActionRow()
        password_row.set_title("Admin Password")
        password_row.set_subtitle("Used for privileged API restart/port-clear actions. Cached in memory only.")

        password_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.password_entry = Gtk.PasswordEntry()
        self.password_entry.set_show_peek_icon(True)
        self.password_entry.set_width_chars(20)
        self.password_entry.set_hexpand(True)
        self.password_entry.connect("changed", self._on_password_changed)
        password_box.append(self.password_entry)

        btn_clear_password = Gtk.Button(label="Clear")
        btn_clear_password.connect("clicked", self._on_clear_password)
        password_box.append(btn_clear_password)

        password_row.add_suffix(password_box)
        password_row.set_activatable_widget(self.password_entry)
        group.add(password_row)

        return group

    def _on_open_ui(self, button):
        show_notification("Opening UI", "Launching the BioModStack shell...")
        runtime_mode = operator_runtime_mode(project_root=PROJECT_ROOT)
        subprocess.Popen(
            build_launch_ui_command(project_root=PROJECT_ROOT, runtime_mode=runtime_mode),
            env=self._script_env(),
        )

    def _on_open_browser(self, button):
        webbrowser.open(operator_frontend_url(project_root=PROJECT_ROOT))

    def _selected_runtime_target(self) -> str:
        combo = getattr(self, "runtime_target_combo", None)
        if combo is not None:
            active_id = combo.get_active_id()
            if active_id in {"prod", "dev", "both"}:
                return active_id
        return "prod"
    
    def _on_start_all(self, button):
        target = self._selected_runtime_target()
        show_notification("Starting", f"Starting BioModStack runtime target: {target}")
        subprocess.Popen(["bash", str(START_SCRIPT), "start-target", "--target", target], env=self._script_env())
        GLib.timeout_add_seconds(5, self._refresh_status)
    
    def _on_restart_all(self, button):
        show_notification("Restarting", "Restarting all services...")
        runtime_mode = operator_runtime_mode(project_root=PROJECT_ROOT)
        subprocess.Popen(["bash", str(START_SCRIPT), "restart", "--runtime", runtime_mode], env=self._script_env())
        GLib.timeout_add_seconds(3, self._refresh_status)
    
    def _on_stop_all(self, button):
        show_notification("Stopping", "Stopping all services...")
        runtime_mode = operator_runtime_mode(project_root=PROJECT_ROOT)
        subprocess.Popen(["bash", str(STOP_SCRIPT), "--runtime", runtime_mode], env=self._script_env())
        GLib.timeout_add_seconds(2, self._refresh_status)

    def _script_env(self) -> dict:
        env = os.environ.copy()
        if self.cached_sudo_password:
            env["BMS_SUDO_PASSWORD"] = self.cached_sudo_password
        else:
            env.pop("BMS_SUDO_PASSWORD", None)
        return env
    
    def _build_logs_section(self) -> Gtk.Widget:
        """Log viewer section."""
        group = Adw.PreferencesGroup()
        group.set_title("Logs")
        
        # Log selector buttons
        selector_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        selector_box.set_halign(Gtk.Align.START)
        selector_box.set_margin_bottom(8)
        
        self.btn_api_log = Gtk.ToggleButton(label=LOG_LABELS["api"])
        self.btn_api_log.set_tooltip_text(LOG_HELP_TEXT["api"])
        self.btn_api_log.set_active(True)
        self.btn_api_log.connect("toggled", self._on_log_toggle, "api")
        selector_box.append(self.btn_api_log)
        
        self.btn_frontend_log = Gtk.ToggleButton(label=LOG_LABELS["frontend"])
        self.btn_frontend_log.set_tooltip_text(LOG_HELP_TEXT["frontend"])
        self.btn_frontend_log.connect("toggled", self._on_log_toggle, "frontend")
        selector_box.append(self.btn_frontend_log)

        self.btn_workflow_adapter_log = Gtk.ToggleButton(label=LOG_LABELS["workflow-adapter"])
        self.btn_workflow_adapter_log.set_tooltip_text(LOG_HELP_TEXT["workflow-adapter"])
        self.btn_workflow_adapter_log.connect("toggled", self._on_log_toggle, "workflow-adapter")
        selector_box.append(self.btn_workflow_adapter_log)

        self.btn_core_runtime_log = Gtk.ToggleButton(label=LOG_LABELS["core-runtime"])
        self.btn_core_runtime_log.set_tooltip_text(LOG_HELP_TEXT["core-runtime"])
        self.btn_core_runtime_log.connect("toggled", self._on_log_toggle, "core-runtime")
        selector_box.append(self.btn_core_runtime_log)
        
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

        self.log_help_label = Gtk.Label(label=LOG_HELP_TEXT[self.current_log])
        self.log_help_label.set_wrap(True)
        self.log_help_label.set_xalign(0)
        self.log_help_label.add_css_class("dim-label")
        row_help = Adw.ActionRow()
        row_help.set_child(self.log_help_label)
        group.add(row_help)
        
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
            for candidate_type, candidate_button in (
                ("api", self.btn_api_log),
                ("frontend", self.btn_frontend_log),
                ("workflow-adapter", self.btn_workflow_adapter_log),
                ("core-runtime", self.btn_core_runtime_log),
            ):
                if candidate_type != log_type:
                    candidate_button.set_active(False)
            self._refresh_log()
    
    def _refresh_log(self):
        if hasattr(self, "log_help_label"):
            self.log_help_label.set_text(LOG_HELP_TEXT.get(self.current_log, ""))
        content = read_named_log_tail(self.current_log, self.config.get("log_lines", 30))
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
        log_path = LOG_PATHS.get(self.current_log, CORE_RUNTIME_LOG)
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

        autostart_row_widget, self.autostart_row = build_toggle_row(
            Adw,
            Gtk,
            title="Autostart on Login",
            subtitle="Launch control panel when you log in",
            active=AUTOSTART_PATH.exists(),
            handler=self._on_autostart_toggle,
        )
        group.add(autostart_row_widget)

        notif_row_widget, self.notif_row = build_toggle_row(
            Adw,
            Gtk,
            title="Desktop Notifications",
            subtitle="Show notifications for service events",
            active=self.config.get("notifications", True),
            handler=self._on_notifications_toggle,
        )
        group.add(notif_row_widget)

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

    def _on_password_changed(self, entry):
        self.cached_sudo_password = entry.get_text()

    def _on_clear_password(self, button):
        self.cached_sudo_password = ""
        self.password_entry.set_text("")
    
    def _refresh_status(self):
        """Refresh all status displays."""
        self._update_status_row()
        self._update_jobs_row()
        self._update_db_info()
        self._update_bioxp_row(probe=False)
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
