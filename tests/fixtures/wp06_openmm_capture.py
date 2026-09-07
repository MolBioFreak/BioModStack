"""Capture only. Never load OpenMM or generate a scientific result."""
import json
from pathlib import Path
import sys

out = Path('relaxed')
out.mkdir(exist_ok=True)
(out / 'capture.json').write_text(json.dumps({'argv': sys.argv[1:]}))
(out / 'fixture.pdb').write_text('NON_MODEL_FIXTURE_ONLY\n')
