#!/usr/bin/env python3
"""Publish every native Boltz sample with exact input-record correspondence.

model_N is the native confidence-ranked output index, not the RNG draw index.
Confidence numbers are not changed. Original native files remain intact.
"""
import argparse
import json
from pathlib import Path
import re
import shutil


def publish_samples(native_root, output, inputs):
    native_root, output, inputs = map(Path, (native_root, output, inputs))
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    # The input YAML roster owns source identity, never substring matching.
    for config in sorted(inputs.glob('*.yaml')):
        source = config.stem
        directory = native_root / source
        pattern = re.compile(re.escape(source) + r'_model_(\d+)\.pdb')
        for structure in sorted(directory.glob('*.pdb')):
            match = pattern.fullmatch(structure.name)
            if not match:
                continue
            index = int(match[1])
            stem = f'{source}_model_{index}_boltzpred'
            identity = {'source_id': source, 'native_model_index': index,
                        'native_structure': structure.name, 'input_yaml': config.name}
            shutil.copyfile(structure, output / f'{stem}.pdb')
            confidence = directory / f'confidence_{source}_model_{index}.json'
            if confidence.is_file():
                values = json.loads(confidence.read_text())
                values['bms_native_source'] = identity
                (output / f'{stem}.json').write_text(json.dumps(values, indent=2) + '\n')
            pae = directory / f'pae_{source}_model_{index}.npz'
            if pae.is_file():
                shutil.copyfile(pae, output / f'{stem}.pae.npz')
            rows.append({**identity, 'published_stem': stem,
                         'confidence_present': confidence.is_file(), 'pae_present': pae.is_file()})
        for affinity in directory.glob('affinity_*.json'):
            shutil.copyfile(affinity, output / affinity.name)
    (output / 'boltz_samples.manifest').write_text(json.dumps({'schema': 'bms.boltz-samples.v1', 'samples': rows}, indent=2) + '\n')
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--inputs', required=True)
    args = parser.parse_args()
    print(json.dumps(publish_samples(args.native_root, args.output, args.inputs), indent=2))
