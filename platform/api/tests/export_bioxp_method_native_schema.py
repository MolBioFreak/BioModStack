"""Run with pinned robot root/src on PYTHONPATH; emits schemas, not robot code."""
import json
import sys
from pathlib import Path
import tests.z_stop_offline_guard  # noqa: F401
from bioxp.pipette.cavro_application import ApplicationRequest, capability_catalog
from bioxp.pipette.cavro_liquid import Recipe

Path(sys.argv[1]).write_text(json.dumps({
    'source_commit': sys.argv[2],
    'application': ApplicationRequest.model_json_schema(),
    'recipe': Recipe.model_json_schema(),
    'capabilities': capability_catalog(),
}, indent=2) + '\n')
