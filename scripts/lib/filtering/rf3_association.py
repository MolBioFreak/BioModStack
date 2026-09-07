"""RF3 native export boundary: bind pairs BEFORE workflow flattening/staging.

Naming authority: RF3Output.dump and dump_top_ranked_outputs in Foundry
b02eed6a6bdf8f44d14a80cc36e3da13c9f2291c/models/rf3/src/rf3/inference_engines/rf3.py.
Both write <identity>_model.cif[.gz] and <identity>_summary_confidences.json
from the SAME output object. This adapter preserves that producer declaration;
it must run in RunRF3, never reconstruct authority from filter-stage neighbors.
No assertion about installed model/checkpoint qualification is made here.
"""
import gzip
import hashlib
import json
from pathlib import Path

SCHEMA = 'bms.rf3-native-pair.v1'


def binding_path(structure):
    return structure.with_name(structure.name.removesuffix('.gz').removesuffix('.cif').removesuffix('.pdb') + '_binding.json')


def descriptor(name, raw):
    return {'name': name, 'sha256': hashlib.sha256(raw).hexdigest()}


def validate(binding, structure_name, structure_raw, summary_name, summary_raw):
    if (set(binding) != {'schema', 'structure', 'summary'} or binding['schema'] != SCHEMA
            or binding['structure'] != descriptor(structure_name, structure_raw)
            or binding['summary'] != descriptor(summary_name, summary_raw)):
        raise ValueError('RF3 producer structure-summary binding mismatch')


def export_pairs(native_root, output):
    """Export native sibling pairs atomically with respect to inventory validation."""
    native_root, output = Path(native_root), Path(output)
    pending, names, summaries = [], set(), set()
    structures = sorted([*native_root.rglob('*_model.cif'), *native_root.rglob('*_model.cif.gz')])
    if not structures:
        raise ValueError('RF3 native structure inventory empty')
    for structure in structures:
        stem = structure.name.removesuffix('.gz').removesuffix('.cif')
        summary = structure.with_name(stem.removesuffix('_model') + '_summary_confidences.json')
        if not summary.is_file() or summary.is_symlink() or structure.is_symlink():
            raise ValueError('RF3 native pair missing or symlinked')
        target = stem + '.cif.gz'
        if target in names:
            raise ValueError('RF3 duplicate flattened candidate identity')
        names.add(target); summaries.add(summary)
        raw = structure.read_bytes()
        compressed = raw if structure.suffix == '.gz' else gzip.compress(raw, mtime=0)
        summary_raw = summary.read_bytes()
        binding = {'schema': SCHEMA, 'structure': descriptor(target, compressed),
                   'summary': descriptor(summary.name, summary_raw)}
        pending.append((target, compressed, summary.name, summary_raw, binding))
    if summaries != set(native_root.rglob('*_summary_confidences.json')):
        raise ValueError('RF3 orphan summary inventory')
    output.mkdir(parents=True, exist_ok=False)
    for name, raw, summary_name, summary_raw, binding in pending:
        (output / name).write_bytes(raw)
        (output / summary_name).write_bytes(summary_raw)
        binding_path(output / name).write_text(json.dumps(binding, sort_keys=True))
    for summary in summaries:
        full = summary.with_name(summary.name.replace('_summary_confidences.json', '_confidences.json'))
        if full.is_file():
            (output / full.name).write_bytes(full.read_bytes())


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--native-root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    export_pairs(args.native_root, args.output)
