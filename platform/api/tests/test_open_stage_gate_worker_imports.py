"""The stage-gate script runs inside worker containers whose Python has no `requests`.

Worker-side runs open the gate through the attempt journal; only the controller-local path posts over
HTTP. A module-level `import requests` made every remote loop job fail at its final gate step, after
all the science had already run.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_gate_script_imports_without_requests():
    code = (
        "import sys; sys.modules['requests'] = None; "
        f"sys.path.insert(0, {str(ROOT)!r}); "
        "import importlib; m = importlib.import_module('scripts.open_stage_gate'); "
        "assert callable(m.main) and callable(m.component_checkpoint_projection); print('ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr[-400:]
