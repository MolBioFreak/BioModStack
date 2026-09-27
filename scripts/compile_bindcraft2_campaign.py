#!/usr/bin/env python3
"""Resolve a typed BC2 campaign with the exact installed native interpreter (CPU only)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

service_dir = Path(__file__).resolve().parents[1] / 'platform/api/services'
package = types.ModuleType('services')
package.__path__ = [str(service_dir)]
sys.modules['services'] = package
for name in ('bindcraft2_native', 'bindcraft2_typed'):
    spec = importlib.util.spec_from_file_location(f'services.{name}', service_dir / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from services.bindcraft2_native import PIN, receipt_json
from services.bindcraft2_typed import compile_typed


def main() -> None:
    root = Path('/opt/bindcraft')
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != PIN:
        raise ValueError(f'Installed BC2 source revision differs: {revision}')
    from bindcraft.settings import load_settings
    from bindcraft.parameter_sweep import parameter_sweep_arms
    request = json.load(sys.stdin)
    compiled = compile_typed(request, Path(sys.argv[1]), load_settings, parameter_sweep_arms,
                             resume='--resume' in sys.argv[2:])
    print(json.dumps(receipt_json(compiled), sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
