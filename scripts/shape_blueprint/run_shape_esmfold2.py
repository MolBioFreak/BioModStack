#!/usr/bin/env python3
"""Shape's immutable request owner -> existing ESMFold2 native compiler."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.portable_inputs import resolve_input_path
from run_esmfold2_inference import compile_workflow_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--sequence', required=True)
    parser.add_argument('--sequence-name', required=True)
    args = parser.parse_args()
    owner = args.request.resolve()
    shape = json.loads(owner.read_bytes())
    # Historical v2 requests predate native settings; retain their Shape defaults.
    settings = dict(shape.get('validator_settings', {}).get('esmfold2', {
        'model_variant': 'fast', 'num_loops': 3, 'num_sampling_steps': 50,
        'num_diffusion_samples': 1, 'seed': shape.get('seed', 0), 'esmf_use_msa': False,
    }))
    settings.update(core_protein_scientific_contract=1, esmf_sequence=args.sequence,
                    esmf_sequence_name=args.sequence_name)
    sources = {settings[key]: resolve_input_path(settings[key], owner=owner)
               for key in ('msa_path', 'esmf_msa_path') if settings.get(key)}
    argv, receipt = compile_workflow_request(settings, sources)
    output = Path('esmfold2_results')
    output.mkdir(exist_ok=True)
    effective = output / 'effective_settings.json'
    effective.write_text(json.dumps(receipt, allow_nan=False, sort_keys=True))
    scripts = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(scripts / 'bms_gpu_run_telemetry.py'),
        '--label', 'ESMFold2Predict', '--output-json', str(output / 'runtime.telemetry.json'), '--',
        sys.executable, str(scripts / 'run_esmfold2_inference.py'), *argv], check=True,
        env=dict(os.environ, BMS_ESMFOLD2_EFFECTIVE_SETTINGS=str(effective.resolve())))


if __name__ == '__main__':
    main()
