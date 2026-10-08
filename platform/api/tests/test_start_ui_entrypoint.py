from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
START_UI = REPO_ROOT / "start_ui.sh"
START_UI_ELECTRON = REPO_ROOT / "start_ui_electron.sh"
START_UI_GUI = REPO_ROOT / "start_ui_gui.sh"


def test_start_ui_sh_still_forwards_directly_to_manage_desktop_services(tmp_path: Path) -> None:
    fake_home = tmp_path / "fake-home"
    manager = fake_home / "scripts" / "manage_desktop_services.py"
    manager.parent.mkdir(parents=True, exist_ok=True)
    manager.write_text(
        "import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}))\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["BMS_HOME"] = str(fake_home)

    result = subprocess.run(
        ["bash", str(START_UI), "start", "--runtime", "container"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"argv": ["start", "--runtime", "container"]}


def test_start_ui_sh_usage_contract_remains_service_control_only() -> None:
    text = START_UI.read_text(encoding="utf-8")

    assert "launch_biomodstack_ui.py" not in text
    assert "start-api" in text
    assert "stop-api" in text
    assert 'exec python3 "$MANAGER" "$ACTION" "$@"' in text


def test_start_ui_electron_sh_launches_ui_script_on_the_electron_surface(tmp_path: Path) -> None:
    fake_home = tmp_path / "fake-home"
    launcher = fake_home / "scripts" / "launch_biomodstack_ui.py"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        "import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}))\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["BMS_HOME"] = str(fake_home)

    result = subprocess.run(
        ["bash", str(START_UI_ELECTRON), "--runtime", "container"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"argv": ["--surface", "electron", "--runtime", "container"]}


def test_start_ui_gui_sh_restarts_requested_runtime_and_opens_matching_browser_url(tmp_path: Path) -> None:
    fake_home = tmp_path / "fake-home"
    start_ui = fake_home / "start_ui.sh"
    start_ui.parent.mkdir(parents=True, exist_ok=True)
    start_ui.write_text(
        "#!/bin/bash\nprintf '%s\\n' \"$@\" > \"$BMS_CAPTURE_ARGS_PATH\"\n",
        encoding="utf-8",
    )
    start_ui.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name, content in {
        "notify-send": "#!/bin/bash\nexit 0\n",
        "sleep": "#!/bin/bash\nexit 0\n",
        "xdg-open": "#!/bin/bash\nprintf '%s\\n' \"$1\" > \"$BMS_CAPTURE_URL_PATH\"\n",
    }.items():
        path = fake_bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    captured_args = tmp_path / "captured-args.txt"
    captured_url = tmp_path / "captured-url.txt"
    env = os.environ.copy()
    env["BMS_HOME"] = str(fake_home)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["BMS_CAPTURE_ARGS_PATH"] = str(captured_args)
    env["BMS_CAPTURE_URL_PATH"] = str(captured_url)

    result = subprocess.run(
        ["bash", str(START_UI_GUI), "--runtime", "dev"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert captured_args.read_text(encoding="utf-8").splitlines() == ["restart", "--runtime", "dev"]
    assert captured_url.read_text(encoding="utf-8").strip() == "http://localhost:5173/"
