"""Optional isolated CPU execution at the existing stateless preparation boundary.

The configured interpreter is installation-owned, never supplied by a request.
No persistence, scheduler, resolver or scientific implementation lives here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


def execute(operation, request, local):
    python = os.environ.get('BMS_MOLECULAR_PREPARATION_PYTHON')
    if not python:
        return local(request)
    from services.molecular_construction import ConstructionUnavailable
    from services.molecular_geometry import GeometryUnavailable
    unavailable = ConstructionUnavailable if operation == 'graph' else GeometryUnavailable
    root = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            [python, '-I', '-B', str(root / 'scripts/molecular_preparation_worker.py'), operation],
            input=request.model_dump_json(), text=True, capture_output=True,
            cwd=root, check=True,
        )
        reply = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise unavailable('isolated molecular preparation runtime unavailable') from exc
    if 'error' in reply:
        errors = {'ConstructionUnavailable': ConstructionUnavailable,
                  'GeometryUnavailable': GeometryUnavailable,
                  'ValueError': ValueError, 'RuntimeError': RuntimeError}
        if reply['error'] not in errors:
            raise unavailable('isolated molecular preparation runtime unavailable')
        raise errors[reply['error']](reply['detail'])
    return reply['result']
