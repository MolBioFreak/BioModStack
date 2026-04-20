from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
START_UI = REPO_ROOT / "start_ui.sh"


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
    assert 'Usage: $0 {start|stop|status|restart|restart-api} [--runtime dev|container]' in text
    assert 'exec python3 "$MANAGER" "$ACTION" "$@"' in text
