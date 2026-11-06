"""Resequence designers must launch the way a rented (udocker) worker can run them.

Two defects only showed on the worker:
* Caliby put TMPDIR under the task directory. The worker's task directory is ~94 characters, so Python's
  multiprocessing listener socket (TMPDIR/pymp-*/listener-*) exceeded the 107-character AF_UNIX limit and the
  data workers died silently while the main process waited forever.
* ProtonPottsMPNN called `apptainer exec` directly. A udocker worker has no unprivileged user namespaces; the
  selected scientific launcher is BMS_CONTAINER_EXECUTABLE (unset keeps `apptainer`).
"""
import os
import re
import subprocess
from pathlib import Path

NF = Path(__file__).resolve().parents[3] / "workflows" / "protein_local_redesign.nf"
# Longest task directory the remote worker produces: /root/bms/attempts/<uuid>/work/xx/<30 hex>
WORKER_TASK_DIR = "/root/bms/attempts/5499d5f8-9674-4fc7-a590-329159417222/work/a4/0ccb1e2de1235604ff01bb392da017"


def _script(process):
    text = NF.read_text(encoding="utf-8")
    block = text[text.index(f"process {process} {{"):]
    body = block.split('script:', 1)[1]
    fenced = re.search(r'"""(.*?)"""', body, re.S)
    return body[:fenced.start()], fenced.group(1)


def test_caliby_temp_dir_keeps_multiprocessing_sockets_inside_the_unix_limit(tmp_path):
    _, script = _script("RunCalibyResequence")
    fragment = "\n".join(line for line in script.replace("\\$", "$").splitlines()
                         if "run_resequence_designer.py" not in line)
    # The real fragment, run in a task directory as deep as the worker's. Python 3.12 (the Caliby image) binds the
    # resource-sharer listener at <TMPDIR>/pymp-<8>/listener-<8>; sockaddr_un.sun_path holds 107 characters.
    task = tmp_path / WORKER_TASK_DIR.lstrip("/")
    task.mkdir(parents=True)
    run = subprocess.run(["bash", "-ue", "-c", fragment + '\nprintf "%s" "$TMPDIR"\n'], cwd=task, text=True,
                         capture_output=True, env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)})
    assert run.returncode == 0, run.stderr[-600:]
    tmpdir = run.stdout.strip()
    assert tmpdir, "the process must set TMPDIR"
    listener = f"{tmpdir}/pymp-abcdefgh/listener-abcdefgh"
    assert len(listener) <= 107, (len(listener), listener)
    # It does not leave a scratch directory behind per task.
    assert "trap" in fragment and not os.path.exists(tmpdir)


def test_protonpotts_resequence_uses_the_selected_scientific_launcher():
    header, script = _script("RunProtonPottsResequence")
    text = header + script
    assert "BMS_CONTAINER_EXECUTABLE" in header
    first_command = [line.strip() for line in script.splitlines() if line.strip()][0]
    assert not first_command.startswith("apptainer exec"), first_command
    assert "${launcher} exec" in first_command
    # Unset keeps the existing Apptainer executable.
    assert re.search(r"System\.getenv\('BMS_CONTAINER_EXECUTABLE'\)\s*\?:\s*'apptainer'", header), text
