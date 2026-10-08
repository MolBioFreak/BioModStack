#!/usr/bin/env python3
"""
BioModStack System Tray Indicator

A comprehensive system tray application for managing BioModStack services:
- Service status monitoring with dynamic icons
- Log viewing for API and Frontend
- Database info and backup tools
- Service control (restart/stop)
- Autostart configuration
"""

import os
import sys
import subprocess
import threading
import time
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
import webbrowser
import json

# Third-party imports
try:
    import pystray
    from pystray import MenuItem as Item, Menu
    from PIL import Image, ImageDraw
except ImportError:
    print("Missing dependencies. Install with: uv pip install pystray pillow")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

API_ROOT = Path(__file__).parent / "platform" / "api"
sys.path.insert(0, str(API_ROOT))
from paths import get_code_root, get_db_path, get_results_dir  # noqa: E402
from biomodstack_services import (  # noqa: E402
    API_LOG as API_LOG_PATH,
    API_SERVICE,
    FRONTEND_LOG as FRONTEND_LOG_PATH,
    FRONTEND_SERVICE,
    build_launch_ui_command,
    service_is_active,
)

PROJECT_ROOT = get_code_root()
API_PORT = 8000
FRONTEND_PORT = 5173
API_URL = f"http://localhost:{API_PORT}"
FRONTEND_URL = f"http://localhost:{FRONTEND_PORT}/bms/"

# Paths
DB_PATH = get_db_path()
RESULTS_DIR = get_results_dir()
API_LOG = API_LOG_PATH
FRONTEND_LOG = FRONTEND_LOG_PATH
ICON_PATH = PROJECT_ROOT / "platform" / "assets" / "icons" / "biomodstack_256.png"
TRAY_ICON_PATH = PROJECT_ROOT / "platform" / "assets" / "icons" / "biomodstack_tray.png"  # No text version
CONFIG_PATH = Path.home() / ".config" / "biomodstack" / "tray_config.json"
AUTOSTART_PATH = Path.home() / ".config" / "autostart" / "biomodstack-tray.desktop"

# Scripts
START_SCRIPT = PROJECT_ROOT / "start_ui.sh"
RESTART_API_SCRIPT = PROJECT_ROOT / "restart_api.sh"
STOP_SCRIPT = PROJECT_ROOT / "stop_services.sh"

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    """Load configuration from JSON file."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"autostart": True, "notifications": True}

def save_config(config: dict):
    """Save configuration to JSON file."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

# ═══════════════════════════════════════════════════════════════════════════════
# SERVICE STATUS CHECKING
# ═══════════════════════════════════════════════════════════════════════════════

def check_api_status() -> bool:
    """Check if API is responding."""
    try:
        import urllib.request
        req = urllib.request.Request(f"{API_URL}/api/health", method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        try:
            return service_is_active(API_SERVICE)
        except Exception:
            pass
        # Fallback: check if process is running
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"uvicorn.*:{API_PORT}"],
                capture_output=True, timeout=2
            )
            return result.returncode == 0
        except Exception:
            return False

def check_frontend_status() -> bool:
    """Check if Frontend dev server is running."""
    try:
        return service_is_active(FRONTEND_SERVICE)
    except Exception:
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"vite.*{FRONTEND_PORT}"],
                capture_output=True, timeout=2
            )
            return result.returncode == 0
        except Exception:
            return False

STATUS_DB_TIMEOUT_SECONDS = 0.25

def _connect_status_db() -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=STATUS_DB_TIMEOUT_SECONDS)
    except Exception:
        conn = sqlite3.connect(str(DB_PATH), timeout=STATUS_DB_TIMEOUT_SECONDS)
    conn.execute("PRAGMA busy_timeout = 250")
    return conn

def get_job_counts() -> Tuple[int, int, int]:
    """Get job counts from database. Returns (running, queued, total)."""
    try:
        if not DB_PATH.exists():
            return (0, 0, 0)
        conn = _connect_status_db()
        cur = conn.cursor()
        cur.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
        counts = dict(cur.fetchall())
        conn.close()
        running = counts.get("running", 0)
        queued = counts.get("queued", 0)
        total = sum(counts.values())
        return (running, queued, total)
    except Exception:
        return (0, 0, 0)

def get_db_info() -> dict:
    """Get database information."""
    try:
        if not DB_PATH.exists():
            return {"error": "Database not found"}
        
        size_mb = DB_PATH.stat().st_size / (1024 * 1024)
        conn = _connect_status_db()
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) FROM jobs")
        job_count = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM designs")
        design_count = cur.fetchone()[0]
        
        journal_mode = cur.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = cur.execute("PRAGMA busy_timeout").fetchone()[0]
        
        conn.close()
        
        return {
            "size_mb": round(size_mb, 2),
            "jobs": job_count,
            "designs": design_count,
            "path": str(DB_PATH),
            "journal_mode": journal_mode,
            "busy_timeout": busy_timeout,
        }
    except Exception as e:
        return {"error": str(e)}

# ═══════════════════════════════════════════════════════════════════════════════
# ICON GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def create_status_icon(status: str = "unknown") -> Image.Image:
    """Create a status-colored icon.
    
    status: 'healthy' (green), 'degraded' (yellow), 'down' (red), 'unknown' (gray)
    """
    colors = {
        "healthy": "#22c55e",   # Green
        "degraded": "#eab308",  # Yellow
        "down": "#ef4444",      # Red
        "unknown": "#6b7280",   # Gray
    }
    color = colors.get(status, colors["unknown"])
    
    # Try to load custom icon, fall back to generated
    if TRAY_ICON_PATH.exists():
        try:
            img = Image.open(TRAY_ICON_PATH).convert("RGBA")
            img = img.resize((64, 64), Image.Resampling.LANCZOS)
            
            # Add status dot in corner
            draw = ImageDraw.Draw(img)
            draw.ellipse([48, 48, 62, 62], fill=color, outline="#ffffff")
            return img
        except Exception:
            pass
    
    # Fallback: generate simple icon
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Draw stacked blocks (simplified logo)
    draw.rectangle([16, 40, 48, 52], fill="#166534")  # Dark green
    draw.rectangle([14, 26, 50, 38], fill="#22c55e")  # Mid green
    draw.rectangle([18, 12, 46, 24], fill="#86efac")  # Light green
    
    # Status dot
    draw.ellipse([48, 48, 62, 62], fill=color, outline="#ffffff")
    
    return img

# ═══════════════════════════════════════════════════════════════════════════════
# LOG VIEWER (Uses system text editor or terminal)
# ═══════════════════════════════════════════════════════════════════════════════

def open_log_file(log_path: Path):
    """Open log file in system viewer."""
    if not log_path.exists():
        show_notification("Log Not Found", f"{log_path.name} doesn't exist yet")
        return
    
    # Try various editors in order of preference
    editors = ["xdg-open", "gedit", "kate", "mousepad", "leafpad", "nano"]
    for editor in editors:
        try:
            if editor == "nano":
                # Terminal-based, need terminal emulator
                subprocess.Popen(["gnome-terminal", "--", "nano", str(log_path)])
            else:
                subprocess.Popen([editor, str(log_path)])
            return
        except FileNotFoundError:
            continue
    
    show_notification("Error", "No text editor found")

def open_log_tail(log_path: Path):
    """Open live tail of log file in terminal."""
    if not log_path.exists():
        show_notification("Log Not Found", f"{log_path.name} doesn't exist yet")
        return
    
    terminals = [
        ["gnome-terminal", "--", "tail", "-f", "-n", "100", str(log_path)],
        ["konsole", "-e", "tail", "-f", "-n", "100", str(log_path)],
        ["xfce4-terminal", "-e", f"tail -f -n 100 {log_path}"],
        ["xterm", "-e", "tail", "-f", "-n", "100", str(log_path)],
    ]
    
    for cmd in terminals:
        try:
            subprocess.Popen(cmd)
            return
        except FileNotFoundError:
            continue
    
    show_notification("Error", "No terminal emulator found")

# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def backup_database():
    """Create a timestamped backup of the database."""
    if not DB_PATH.exists():
        show_notification("Backup Failed", "Database not found")
        return
    
    backup_dir = PROJECT_ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"biomodstack_backup_{timestamp}.db"
    
    try:
        shutil.copy2(DB_PATH, backup_path)
        size_mb = backup_path.stat().st_size / (1024 * 1024)
        show_notification(
            "Backup Complete",
            f"Saved to backups/\n{backup_path.name}\n({size_mb:.1f} MB)"
        )
    except Exception as e:
        show_notification("Backup Failed", str(e))

def open_db_location():
    """Open database directory in file manager."""
    db_dir = DB_PATH.parent
    subprocess.Popen(["xdg-open", str(db_dir)])

def show_db_info():
    """Show database info in notification."""
    info = get_db_info()
    if "error" in info:
        show_notification("Database Error", info["error"])
    else:
        show_notification(
            "Database Info",
            f"Size: {info['size_mb']} MB\n"
            f"Jobs: {info['jobs']}\n"
            f"Designs: {info['designs']}\n"
            f"Journal: {info.get('journal_mode', '?')}\n"
            f"Busy Timeout: {info.get('busy_timeout', '?')} ms"
        )

# ═══════════════════════════════════════════════════════════════════════════════
# SERVICE CONTROL
# ═══════════════════════════════════════════════════════════════════════════════

def restart_all_services():
    """Restart all BioModStack services."""
    show_notification("Restarting", "Restarting all services...")
    subprocess.Popen([str(START_SCRIPT), "restart"])

def restart_api_only():
    """Restart only the API service."""
    show_notification("Restarting API", "Restarting API service...")
    subprocess.Popen([str(RESTART_API_SCRIPT)])

def stop_all_services():
    """Stop all BioModStack services."""
    show_notification("Stopping", "Stopping all services...")
    subprocess.Popen([str(STOP_SCRIPT)])

def open_ui():
    """Open BioModStack UI in the Electron shell."""
    show_notification("Opening UI", "Launching the BioModStack shell...")
    subprocess.Popen(build_launch_ui_command(project_root=PROJECT_ROOT))


def open_browser_ui():
    """Open BioModStack UI in the hosted browser surface."""
    webbrowser.open(FRONTEND_URL)


def open_results_folder():
    """Open results folder in file manager."""
    RESULTS_DIR.mkdir(exist_ok=True)
    subprocess.Popen(["xdg-open", str(RESULTS_DIR)])

# ═══════════════════════════════════════════════════════════════════════════════
# AUTOSTART MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def is_autostart_enabled() -> bool:
    """Check if autostart is enabled."""
    return AUTOSTART_PATH.exists()

def toggle_autostart(icon, item):
    """Toggle autostart on/off."""
    if is_autostart_enabled():
        AUTOSTART_PATH.unlink()
        show_notification("Autostart", "Disabled - Won't start on login")
    else:
        AUTOSTART_PATH.parent.mkdir(parents=True, exist_ok=True)
        desktop_entry = f"""[Desktop Entry]
Type=Application
Name=BioModStack Tray
Comment=BioModStack System Tray Indicator
Exec=python3 {Path(__file__).resolve()}
Icon={ICON_PATH}
Terminal=false
Categories=Science;Development;
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""
        AUTOSTART_PATH.write_text(desktop_entry)
        show_notification("Autostart", "Enabled - Will start on login")

# ═══════════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def show_notification(title: str, message: str):
    """Show desktop notification."""
    config = load_config()
    if not config.get("notifications", True):
        return
    
    try:
        subprocess.run([
            "notify-send",
            "-i", str(ICON_PATH) if ICON_PATH.exists() else "dialog-information",
            f"BioModStack: {title}",
            message
        ], timeout=5)
    except Exception:
        pass

def toggle_notifications(icon, item):
    """Toggle notifications on/off."""
    config = load_config()
    config["notifications"] = not config.get("notifications", True)
    save_config(config)
    status = "Enabled" if config["notifications"] else "Disabled"
    # Force show this notification regardless of setting
    subprocess.run([
        "notify-send", "-i", "dialog-information",
        "BioModStack", f"Notifications {status}"
    ], timeout=5)

def notifications_enabled() -> bool:
    """Check if notifications are enabled."""
    return load_config().get("notifications", True)

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN TRAY APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

class BioModStackTray:
    def __init__(self):
        self.icon: Optional[pystray.Icon] = None
        self.running = True
        self.current_status = "unknown"
        
    def get_status(self) -> str:
        """Determine overall system status."""
        api_ok = check_api_status()
        frontend_ok = check_frontend_status()
        
        if api_ok and frontend_ok:
            return "healthy"
        elif api_ok or frontend_ok:
            return "degraded"
        else:
            return "down"
    
    def update_icon(self):
        """Update icon based on current status."""
        new_status = self.get_status()
        if new_status != self.current_status:
            self.current_status = new_status
            if self.icon:
                self.icon.icon = create_status_icon(new_status)
    
    def get_tooltip(self) -> str:
        """Generate tooltip text."""
        api_status = "✓" if check_api_status() else "✗"
        frontend_status = "✓" if check_frontend_status() else "✗"
        running, queued, total = get_job_counts()
        
        return (
            f"BioModStack\n"
            f"API: {api_status} | UI: {frontend_status}\n"
            f"Jobs: {running} running, {queued} queued"
        )
    
    def create_menu(self) -> Menu:
        """Create the tray menu."""
        running, queued, total = get_job_counts()
        db_info = get_db_info()
        
        return Menu(
            # Open UI
            Item("🖥 Open BioModStack Shell", lambda: open_ui(), default=True),
            Item("🌐 Open Hosted Web UI", lambda: open_browser_ui()),
            Item("📂 Open Results Folder", lambda: open_results_folder()),
            
            Menu.SEPARATOR,
            
            # Logs submenu
            Item("📋 Logs", Menu(
                Item("📄 View API Log", lambda: open_log_file(API_LOG)),
                Item("📄 View Frontend Log", lambda: open_log_file(FRONTEND_LOG)),
                Menu.SEPARATOR,
                Item("🔴 Live API Log (tail)", lambda: open_log_tail(API_LOG)),
                Item("🔴 Live Frontend Log (tail)", lambda: open_log_tail(FRONTEND_LOG)),
            )),
            
            # Database submenu
            Item("🗄️ Database", Menu(
                Item(
                    f"ℹ️ Info: {db_info.get('jobs', '?')} jobs, {db_info.get('designs', '?')} designs",
                    lambda: show_db_info()
                ),
                Item(f"📦 Size: {db_info.get('size_mb', '?')} MB", lambda: show_db_info()),
                Item(
                    f"🩺 Health: {db_info.get('journal_mode', '?')} | busy {db_info.get('busy_timeout', '?')}ms",
                    lambda: show_db_info()
                ),
                Menu.SEPARATOR,
                Item("💾 Backup Database Now", lambda: backup_database()),
                Item("📂 Open DB Location", lambda: open_db_location()),
            )),
            
            Menu.SEPARATOR,
            
            # Job status (informational)
            Item(f"📊 Jobs: {running} running, {queued} queued", lambda: open_ui()),
            
            Menu.SEPARATOR,
            
            # Service control
            Item("🔄 Restart All Services", lambda: restart_all_services()),
            Item("🔄 Restart API Only", lambda: restart_api_only()),
            Item("⏹️ Stop All Services", lambda: stop_all_services()),
            
            Menu.SEPARATOR,
            
            # Settings
            Item("⚙️ Settings", Menu(
                Item(
                    "☑️ Autostart on Login" if is_autostart_enabled() else "☐ Autostart on Login",
                    toggle_autostart
                ),
                Item(
                    "☑️ Desktop Notifications" if notifications_enabled() else "☐ Desktop Notifications",
                    toggle_notifications
                ),
            )),
            
            Menu.SEPARATOR,
            
            Item("❌ Quit", lambda: self.quit()),
        )
    
    def status_monitor(self):
        """Background thread to monitor status and update icon."""
        while self.running:
            try:
                self.update_icon()
                # Update menu to refresh job counts
                if self.icon:
                    self.icon.menu = self.create_menu()
            except Exception:
                pass
            time.sleep(10)  # Check every 10 seconds
    
    def quit(self):
        """Quit the tray application."""
        self.running = False
        if self.icon:
            self.icon.stop()
    
    def run(self):
        """Run the tray application."""
        # Initial status check
        self.current_status = self.get_status()
        
        # Create icon
        self.icon = pystray.Icon(
            "biomodstack",
            icon=create_status_icon(self.current_status),
            title=self.get_tooltip(),
            menu=self.create_menu()
        )
        
        # Start status monitor thread
        monitor_thread = threading.Thread(target=self.status_monitor, daemon=True)
        monitor_thread.start()
        
        # Run the icon (blocking)
        self.icon.run()

# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Ensure we're not already running
    try:
        result = subprocess.run(
            ["pgrep", "-f", "biomodstack_tray.py"],
            capture_output=True
        )
        pids = result.stdout.decode().strip().split("\n")
        # Filter out our own PID
        other_pids = [p for p in pids if p and int(p) != os.getpid()]
        if other_pids:
            print("BioModStack tray is already running")
            sys.exit(0)
    except Exception:
        pass
    
    print("Starting BioModStack System Tray...")
    tray = BioModStackTray()
    tray.run()
