"""Export loop clone names from the settings owner and native fallback aliases.

Run from any cwd; stdout replaces src/lib/resequenceValidatorNames.json.
No model defaults or native scientific data are exported.
"""
from pathlib import Path
import json
import re
import runpy

root = Path(__file__).resolve().parents[3]
validators = runpy.run_path(str(root / 'platform/api/services/resequence_settings.py'))['VALIDATORS']
names = {engine: {name: rename.get(name, name) for name in allowed}
         for engine, (_, _, allowed, rename) in validators.items()}
# The wrapper owns Boltz's legacy sample-count precedence. Export only aliases
# for settings explicitly accepted by the loop owner, not arbitrary runtime keys.
native = (root / 'modules/structure_prediction.nf').read_text()
fallbacks = re.findall(r'params\.(\w+) != null \? params\.\1 : \(params\.(\w+) != null', native)
aliases = {name: [alias] for name, alias in fallbacks if name in names['boltz2']}
print(json.dumps({'names': names, 'aliases': {'boltz2': aliases}}, indent=2))
