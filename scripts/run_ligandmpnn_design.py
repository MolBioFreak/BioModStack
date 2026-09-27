#!/usr/bin/env python3
"""Thin ordinary-design boundary for installed rc-foundry LigandMPNN.

Consume prepared JSON plus explicit coordinate/output paths. Native result
objects own both identity and serialization; no glob/rank/ordinal joins.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path

CONTRACT = 'ligandmpnn_design.v1'
CHECKPOINT = '/foundry/checkpoints/ligandmpnn_v_32_010_25.pt'
MODES = {'ligand_aware', 'ntp_aware', 'metal_aware', 'dna_aware'}


def json_values(value):
    # Native recovery can be NaN for an empty interface. It is missing evidence,
    # not a zero score and not a reason to drop the generated sequence.
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: json_values(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_values(v) for v in value]
    return value


def execute(request: dict, source: Path, output: Path, *, engine_class=None):
    if request.get('contract') != CONTRACT or request.get('mode') not in MODES or request.get('model_type') != 'ligand_mpnn':
        raise ValueError('Expected prepared ordinary LigandMPNN request')
    options = dict(request['options'])
    if {'structure_path', 'name'} & options.keys():
        raise ValueError('Source path and producer name are transport-owned')
    source = source.resolve()
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    # Keep the installed model's legacy checkpoint interpretation explicit.
    if engine_class is None:
        from mpnn.inference_engines.mpnn import MPNNInferenceEngine
        engine_class = MPNNInferenceEngine
    options.update(structure_path=str(source), name='design')
    engine = engine_class(model_type='ligand_mpnn', checkpoint_path=CHECKPOINT,
                          is_legacy_weights=True, write_fasta=False,
                          write_structures=False, out_directory=str(output))
    results = engine.run(input_dicts=[options], atom_arrays=None)
    records = []
    seen = set()
    for result in results:
        native = result.output_dict
        producer = {'name': result.input_dict['name'],
                    'batch_idx': native['batch_idx'], 'design_idx': native['design_idx']}
        identity = (producer['name'], producer['batch_idx'], producer['design_idx'])
        if identity in seen or producer['name'] != 'design' or native['model_type'] != 'ligand_mpnn':
            raise ValueError('Unexpected/duplicate native producer identity')
        seen.add(identity)
        for key in ('batch_idx', 'design_idx'):
            if type(producer[key]) is not int or producer[key] < 0:
                raise ValueError('Invalid native producer sample key')
        base = output / f"design_b{producer['batch_idx']}_d{producer['design_idx']}"
        artifacts = []
        for enabled, writer, extension, kind in (
            (request['write_structures'], result.write_structure, '.cif', 'structure'),
            (request['write_fasta'], result.write_fasta, '.fa', 'sequence'),
        ):
            if enabled:
                writer(base_path=base)
                path = base.with_suffix(extension)
                artifacts.append({'path': path.name, 'kind': kind,
                                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        records.append({'producer': producer, 'source_sha256': source_digest,
                        'native_input': json_values(result.input_dict),
                        'native_output': json_values(native), 'artifacts': artifacts})
    manifest = {'contract': CONTRACT, 'model_type': 'ligand_mpnn',
                'mode': request['mode'], 'request': request,
                'foundry_version': importlib.metadata.version('rc-foundry'),
                'checkpoint_path': CHECKPOINT, 'is_legacy_weights': True,
                'source_sha256': source_digest, 'records': records}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    execute(json.loads(args.request.read_text()), args.input, args.output)


if __name__ == '__main__':
    main()
