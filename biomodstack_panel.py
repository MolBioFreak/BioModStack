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
from biomodstack_panel_compat import build_toggle_row  # noqa: E402
from biomodstack_services import (  # noqa: E402
    API_LOG as API_LOG_PATH,
    CORE_RUNTIME_LOG as CORE_RUNTIME_LOG_PATH,
    CONTAINER_RUNTIME_MODE,
    DEV_RUNTIME_MODE,
    FRONTEND_LOG as FRONTEND_LOG_PATH,
    WORKFLOW_ADAPTER_LOG as WORKFLOW_ADAPTER_LOG_PATH,
    build_launch_ui_command,
    operator_frontend_url,
    operator_runtime_mode,
    runtime_api_url,
    runtime_api_health_url,
    runtime_descriptor,
    runtime_port_settings,
    save_runtime_port_settings,
)

# Service-control scripts must belong to the checkout that supplied this panel.
# An inherited BMS_HOME may describe a deployed data/runtime profile, but it must
# not silently redirect control actions into a different source tree.
PROJECT_ROOT = Path(__file__).resolve().parent
API_PORT = 8000
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

def _api_health_ready(runtime_mode: str) -> bool:
    """Require a fresh semantic-ready response from the selected API lane."""
    try:
        import urllib.request

        req = urllib.request.Request(
            runtime_api_health_url(runtime_mode=runtime_mode, project_root=PROJECT_ROOT),
            headers={"Accept": "application/json", "Cache-Control": "no-cache"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status != 200:
                return False
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False

    return (
        isinstance(payload, dict)
        and payload.get("status") == "healthy"
        and isinstance(payload.get("liveness"), dict)
        and payload["liveness"].get("alive") is True
        and isinstance(payload.get("readiness"), dict)
        and payload["readiness"].get("ready") is True
    )


def _component_ready(descriptor: dict, component_id: str) -> bool:
    components = descriptor.get("components")
    if not isinstance(components, dict):
        return False
    component = components.get(component_id)
    return (
        isinstance(component, dict)
        and component.get("active") is True
        and component.get("ready") is True
        and component.get("owner_verified") is True
    )


def runtime_status_snapshot(runtime_mode: str) -> dict:
    """Return a fail-closed operator status for one explicit runtime lane."""
    try:
        descriptor = runtime_descriptor(
            project_root=PROJECT_ROOT,
            runtime_mode=runtime_mode,
        )
    except Exception:
        descriptor = {}

    api_health_ready = _api_health_ready(runtime_mode)
    required_components = []
    components = descriptor.get("components")
    if isinstance(components, dict):
        required_components = [
            component_id
            for component_id, component in components.items()
            if isinstance(component, dict) and component.get("required") is True
        ]
    required_ready = bool(required_components) and all(
        _component_ready(descriptor, component_id)
        for component_id in required_components
    )
    runtime_ready = (
        descriptor.get("runtime_active") is True
        and descriptor.get("runtime_ready") is True
        and required_ready
        and api_health_ready
    )

    # Component checks are gated by aggregate runtime readiness. A partial lane
    # must never render as an all-green operator surface.
    return {
        "runtime_mode": runtime_mode,
        "runtime_ready": runtime_ready,
        "api_ready": runtime_ready and _component_ready(descriptor, "api"),
        "frontend_ready": runtime_ready and _component_ready(descriptor, "frontend"),
        "adapter_ready": (
            runtime_ready and _component_ready(descriptor, "workflow-adapter")
            if runtime_mode == "container"
            else None
        ),
    }


def check_api_status(runtime_mode: str | None = None) -> bool:
    selected_runtime = runtime_mode or operator_runtime_mode(project_root=PROJECT_ROOT)
    return runtime_status_snapshot(selected_runtime)["api_ready"]


def check_frontend_status(runtime_mode: str | None = None) -> bool:
    selected_runtime = runtime_mode or operator_runtime_mode(project_root=PROJECT_ROOT)
    return runtime_status_snapshot(selected_runtime)["frontend_ready"]

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
    selected_runtime = operator_runtime_mode(project_root=PROJECT_ROOT)
    api_url = runtime_api_url(
        selected_runtime,
        project_root=PROJECT_ROOT,
    ).rstrip("/")
    req = urllib.request.Request(f"{api_url}{path}", data=data, headers=headers, method=method.upper())
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


def get_bioxp_status(timeout: float = 3.0) -> dict:
    return call_local_api_json("GET", "/api/bioxp/status", timeout=timeout)


def summarize_bioxp_status(payload: dict) -> str:
    if not payload:
        return "BMS API unavailable"
    nested = payload.get("connection")
    state: dict = nested if isinstance(nested, dict) else payload
    configured = bool(state.get("configured"))
    active = bool(state.get("active"))
    fresh = state.get("fresh")
    reachable = state.get("reachable")
    runtime_ready = state.get("runtime_ready")
    hardware = state.get("hardware_ready")
    if not configured:
        label = "NOT CONFIGURED"
    elif not active:
        label = "SAVED / DISCONNECTED"
    elif fresh is False:
        label = "STALE"
    elif reachable is False:
        label = "UNREACHABLE"
    elif fresh is not True or reachable is not True:
        label = "UNKNOWN"
    elif runtime_ready is False:
        label = "API REACHABLE / RUNTIME NOT READY"
    elif runtime_ready is not True:
        label = "API REACHABLE / RUNTIME UNKNOWN"
    elif hardware is False:
        label = "API REACHABLE / HARDWARE NOT READY"
    elif hardware is not True:
        label = "API REACHABLE / HARDWARE UNKNOWN"
    else:
        label = "READY"
    parts = [label, f"generation={int(state.get('generation') or 0)}"]
    target = state.get("target_url")
    if target:
        parts.append(str(target))
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
        """Runtime-scoped status indicators section."""
        group = Adw.PreferencesGroup()
        group.set_title("Status")

        self.dev_status_row = Adw.ActionRow()
        self.dev_status_row.set_title("Development")
        group.add(self.dev_status_row)

        self.prod_status_row = Adw.ActionRow()
        self.prod_status_row.set_title("Production")
        group.add(self.prod_status_row)

        self._update_status_rows()

        self.jobs_row = Adw.ActionRow()
        self.jobs_row.set_title("Production database rows")
        self._update_jobs_row()
        group.add(self.jobs_row)

        return group

    @staticmethod
    def _status_part(label: str, ready: bool) -> str:
        icon = "✓" if ready else "✗"
        color = "green" if ready else "red"
        return f"<span foreground='{color}'>{label} {icon}</span>"

    def _update_runtime_status_row(self, row, runtime_mode: str):
        status = runtime_status_snapshot(runtime_mode)
        parts = [
            self._status_part("Runtime", status["runtime_ready"]),
            self._status_part("API", status["api_ready"]),
            self._status_part("Frontend", status["frontend_ready"]),
        ]
        if status["adapter_ready"] is not None:
            parts.append(self._status_part("Adapter", status["adapter_ready"]))
        row.set_subtitle("  |  ".join(parts))
        row.set_subtitle_lines(1)
        subtitle_label = row.get_last_child()
        while subtitle_label:
            if isinstance(subtitle_label, Gtk.Label):
                subtitle_label.set_use_markup(True)
            subtitle_label = subtitle_label.get_prev_sibling()

    def _update_status_rows(self):
        self._update_runtime_status_row(self.dev_status_row, DEV_RUNTIME_MODE)
        self._update_runtime_status_row(self.prod_status_row, CONTAINER_RUNTIME_MODE)
    
    def _update_jobs_row(self):
        running, queued, total = get_job_counts()
        self.jobs_row.set_subtitle(
            f"{running} running  |  {queued} queued  |  {total:,} raw production DB rows"
        )

    def _load_runtime_port_settings(self) -> dict:
        try:
            return runtime_port_settings(project_root=PROJECT_ROOT)
        except Exception:
            return {
                "dev_web_host_port": 18082,
                "prod_web_host_port": 18080,
                "dev_url": "http://127.0.0.1:18082/",
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
        """Read-only BioXP status; configuration and controls live in the web cockpit."""
        group = Adw.PreferencesGroup()
        group.set_title("BioXP")

        self.bioxp_row = Adw.ActionRow()
        self.bioxp_row.set_title("Control-plane status")
        self._update_bioxp_row()
        group.add(self.bioxp_row)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_box.set_halign(Gtk.Align.START)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", self._on_bioxp_refresh)
        button_box.append(refresh)

        action_row = Adw.ActionRow()
        action_row.set_title("Status only")
        action_row.set_subtitle("Use the BioXP web cockpit for profile and connection actions. This panel does not control the robot host or collect remote logs.")
        action_row.set_child(button_box)
        group.add(action_row)
        return group

    def _update_bioxp_row(self):
        if not hasattr(self, "bioxp_row"):
            return
        try:
            self.bioxp_row.set_subtitle(summarize_bioxp_status(get_bioxp_status(timeout=2.0)))
        except Exception as exc:
            self.bioxp_row.set_subtitle(f"BMS API/BioXP status unavailable: {exc}")

    def _on_bioxp_refresh(self, button):
        self._update_bioxp_row()

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

        btn_dev_browser = Gtk.Button(label="🌐 Dev")
        btn_dev_browser.set_tooltip_text("Open development UI backed by the isolated development database")
        btn_dev_browser.connect("clicked", self._on_open_browser, DEV_RUNTIME_MODE)
        button_box.append(btn_dev_browser)

        btn_prod_browser = Gtk.Button(label="🌐 Production")
        btn_prod_browser.set_tooltip_text("Open production UI backed by the main production database")
        btn_prod_browser.connect("clicked", self._on_open_browser, CONTAINER_RUNTIME_MODE)
        button_box.append(btn_prod_browser)

        # Results folder
        btn_results = Gtk.Button(label="📂 Results")
        btn_results.connect("clicked", lambda _: subprocess.Popen(["xdg-open", str(RESULTS_DIR)]))
        button_box.append(btn_results)
        
        # Start services (new!)
        self.btn_start = Gtk.Button(label="▶ Start")
        self.btn_start.connect("clicked", self._on_start_all)
        button_box.append(self.btn_start)
        
        # Restart all
        self.btn_restart = Gtk.Button(label="🔄 Restart")
        self.btn_restart.connect("clicked", self._on_restart_all)
        button_box.append(self.btn_restart)
        
        # Stop all
        self.btn_stop = Gtk.Button(label="⏹ Stop")
        self.btn_stop.add_css_class("destructive-action")
        self.btn_stop.connect("clicked", self._on_stop_all)
        button_box.append(self.btn_stop)
        
        # Wrap in a simple row
        row = Adw.ActionRow()
        row.set_child(button_box)
        group.add(row)

        self.action_status_row = Adw.ActionRow()
        self.action_status_row.set_title("Last action")
        self.action_status_row.set_subtitle("Ready")
        group.add(self.action_status_row)
        
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

    def _on_open_browser(self, button, runtime_mode: str):
        settings = runtime_port_settings(project_root=PROJECT_ROOT)
        key = "dev_url" if runtime_mode == DEV_RUNTIME_MODE else "prod_url"
        webbrowser.open(str(settings[key]))

    def _selected_runtime_target(self) -> str:
        combo = getattr(self, "runtime_target_combo", None)
        if combo is not None:
            active_id = combo.get_active_id()
            if active_id in {"prod", "dev", "both"}:
                return active_id
        return "prod"
    
    def _on_start_all(self, button):
        target = self._selected_runtime_target()
        self._run_service_action(
            f"Start {target}",
            ["bash", str(START_SCRIPT), "start-target", "--target", target],
        )
    
    def _on_restart_all(self, button):
        runtime_mode = operator_runtime_mode(project_root=PROJECT_ROOT)
        self._run_service_action(
            "Restart",
            ["bash", str(START_SCRIPT), "restart", "--runtime", runtime_mode],
        )
    
    def _on_stop_all(self, button):
        runtime_mode = operator_runtime_mode(project_root=PROJECT_ROOT)
        self._run_service_action(
            "Stop",
            ["bash", str(STOP_SCRIPT), "--runtime", runtime_mode],
        )

    def _run_service_action(self, label: str, command: list[str]) -> None:
        if getattr(self, "_service_action_active", False):
            show_notification("Action In Progress", "Wait for the current BioModStack service action to finish.")
            return

        self._service_action_active = True
        if hasattr(self, "action_status_row"):
            self.action_status_row.set_subtitle(f"{label} in progress…")
        show_notification(label, "BioModStack service action started.")

        def worker() -> None:
            result = None
            error = None
            try:
                result = subprocess.run(
                    command,
                    env=self._script_env(),
                    capture_output=True,
                    text=True,
                    timeout=360,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                error = exc
            GLib.idle_add(self._finish_service_action, label, result, error)

        threading.Thread(target=worker, name="biomodstack-service-action", daemon=True).start()

    def _finish_service_action(self, label: str, result, error) -> bool:
        self._service_action_active = False
        if error is not None:
            detail = str(error).strip() or "service action could not be executed"
            subtitle = f"{label} failed: {detail}"
            notification_title = f"{label} Failed"
        else:
            returncode = int(getattr(result, "returncode", 1))
            raw_output = getattr(result, "stderr", "") if returncode else getattr(result, "stdout", "")
            lines = [line.strip() for line in str(raw_output or "").splitlines() if line.strip()]
            detail = lines[-1] if lines else ("completed" if returncode == 0 else "no diagnostic output")
            detail = detail.removeprefix("ERROR:").strip()
            if returncode == 0:
                subtitle = f"{label} completed: {detail}"
                notification_title = f"{label} Complete"
            else:
                subtitle = f"{label} failed (exit {returncode}): {detail}"
                notification_title = f"{label} Failed"

        if hasattr(self, "action_status_row"):
            self.action_status_row.set_subtitle(subtitle)
        show_notification(notification_title, detail)
        self._refresh_status_once()
        return False

    def _script_env(self) -> dict:
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("BMS_") or key == "COMPOSE_PROJECT_NAME":
                env.pop(key, None)
        if self.cached_sudo_password:
            env["BMS_SUDO_PASSWORD"] = self.cached_sudo_password
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
        self._update_status_rows()
        self._update_jobs_row()
        self._update_db_info()
        self._update_bioxp_row()
        return True  # Keep timer running

    def _refresh_status_once(self):
        """Refresh once after an action without creating another poller."""
        self._refresh_status()
        return False

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
