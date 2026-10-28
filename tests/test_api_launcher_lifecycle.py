"""Real launcher/uv/Python/uvicorn ownership, without live units or BMS stores.

Only prerequisite/config discovery and the ASGI application are fixture-local.
Stops signal the launch PID only, like KillMode=process, including escalation.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("lifecycle condition did not become true")


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.parametrize("managed", [False, True], ids=["uv", "managed"])
@pytest.mark.parametrize("phase", ["prelistener", "ready", "hung_shutdown"])
def test_api_main_pid_stop_preserves_independent_runs(tmp_path, managed, phase):
    uv = shutil.which("uv")
    assert uv, "real uv is required for launcher lifecycle acceptance"
    import click
    import h11
    import uvicorn

    root = tmp_path / "project with spaces"
    scripts = root / "scripts"
    api = root / "platform/api"
    scripts.mkdir(parents=True)
    api.mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    for name in ("run_biomodstack_api.sh", "python_runtime_guard.sh", "configuration_read_guard.sh"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    (root / "biomodstack_configuration.py").write_text(
        "def configuration_identity(): return None\ndef assert_configuration_readable(): pass\n")
    (root / "biomodstack_python_prerequisites.py").write_text(
        "import os\ndef publish_installed_support(root): pass\n"
        "def resolve_python_environment(root):\n"
        "    python = os.environ.get('TEST_MANAGED_PYTHON')\n"
        "    return {'python': python, 'root': str(root)} if python else None\n")
    # uv really resolves a locked isolated project; Python packages are supplied
    # by this test interpreter, not downloaded or mocked executable shims.
    (api / "pyproject.toml").write_text(
        '[project]\nname="launcher-lifecycle-test"\nversion="0.0.0"\nrequires-python=">=3.11"\n')
    (api / "run_migrations.py").write_text(
        "from pathlib import Path\nPath('migration-done').write_text('done')\n")
    (api / "main.py").write_text('''
import asyncio, json, os, subprocess, sys
from pathlib import Path
assert Path('migration-done').exists()
# Both a same-session local child and a detached owner must remain untouched.
runs = [subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    start_new_session=detached) for detached in (False, True)]
Path('owner.json').write_text(json.dumps({'api': os.getpid(), 'runs': [p.pid for p in runs],
    'python': sys.executable, 'virtual_env': os.environ.get('VIRTUAL_ENV')}))
async def app(scope, receive, send):
    if scope['type'] == 'lifespan':
        await receive()
        if os.environ['TEST_PHASE'] == 'prelistener':
            while True: await asyncio.sleep(.05)
        await send({'type': 'lifespan.startup.complete'})
        await receive()
        if os.environ['TEST_PHASE'] == 'hung_shutdown':
            Path('shutdown-entered').touch()
            while True: await asyncio.sleep(.05)
        Path('shutdown-completed').touch()
        await send({'type': 'lifespan.shutdown.complete'})
    else:
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'isolated-ready'})
''')
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BMS_", "UV_", "PYTHON", "XDG_", "VIRTUAL_ENV"))}
    env.update(HOME=str(home), BMS_HOME=str(root), BMS_RUNTIME_MODE="dev",
               BMS_API_MODE="dev", BMS_API_RELOAD="0", BMS_CPU_POWER_STRICT="0",
               BMS_API_BIND_PORT=str(port), TEST_PHASE=phase,
               UV_PYTHON=sys.executable, UV_CACHE_DIR=str(tmp_path / "uv-cache"),
               UV_PROJECT_ENVIRONMENT=str(tmp_path / "external environment"),
               PYTHONPATH=os.pathsep.join(sorted({str(Path(m.__file__).parent.parent)
                                                 for m in (click, h11, uvicorn) if m.__file__})))
    subprocess.run([uv, "lock", "--offline"], cwd=api, env=env, check=True,
                   capture_output=True, timeout=30)
    if managed:
        env["TEST_MANAGED_PYTHON"] = sys.executable
    owner = None
    process = None
    # An independently owned sibling also survives; it is never in the API tree.
    independent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                   start_new_session=True)
    def ready():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2) as connection:
                connection.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
                return b"200 OK" in connection.recv(4096)
        except OSError:
            return False
    try:
        with (tmp_path / "api.log").open("w+") as log:
            process = subprocess.Popen(["bash", str(scripts / "run_biomodstack_api.sh")],
                                       env=env, stdout=log, stderr=log, start_new_session=True)
            wait_until(lambda: (api / "owner.json").exists() or process.poll() is not None)
            log.flush()
            assert process.poll() is None, (tmp_path / "api.log").read_text()
            owner = json.loads((api / "owner.json").read_text())
            assert owner["api"] == process.pid, "API must be the service MainPID, not a uv child"
            if managed:
                assert owner["python"] == sys.executable
            else:
                assert Path(owner["python"]).parent.parent == Path(env["UV_PROJECT_ENVIRONMENT"])
                assert owner["virtual_env"] == env["UV_PROJECT_ENVIRONMENT"]
            if phase == "prelistener":
                assert not ready()
            else:
                wait_until(ready)
            process.send_signal(signal.SIGTERM)
            if phase == "hung_shutdown":
                wait_until(lambda: (api / "shutdown-entered").exists())
            try:
                process.wait(timeout=1 if phase != "ready" else 10)
            except subprocess.TimeoutExpired:
                assert phase != "ready", "ready API did not stop gracefully"
                process.kill()  # TimeoutStopSec escalation, directed only at MainPID.
                process.wait(timeout=5)
            assert not alive(owner["api"])
            assert not ready()
            assert all(alive(pid) for pid in owner["runs"])
            assert independent.poll() is None
            if phase == "ready":
                assert (api / "shutdown-completed").exists()
                # Uvicorn re-raises the captured signal after graceful shutdown.
                assert process.returncode in (0, -signal.SIGTERM)
            else:
                assert process.returncode == -signal.SIGKILL
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if owner is None and (api / "owner.json").exists():
            owner = json.loads((api / "owner.json").read_text())
        if owner is not None and process is not None and owner["api"] != process.pid:
            # Also clean up the orphan when running this regression on old code.
            if alive(owner["api"]):
                os.kill(owner["api"], signal.SIGKILL)
        for pid in (owner or {}).get("runs", []):
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
        independent.terminate()
        independent.wait(timeout=5)
