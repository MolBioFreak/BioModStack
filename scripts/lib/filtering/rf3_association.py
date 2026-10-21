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
import shutil
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
    # Foundry writes a top-level best alias in addition to per-sample leaves.
    # Identify by its native directory/name contract, not coordinate equality:
    # distinct samples can legitimately have identical coordinates/confidence.
    aliases = []
    for structure in structures:
        identity = structure.name.removesuffix('.gz').removesuffix('.cif').removesuffix('_model')
        samples = list(structure.parent.glob(f'seed-*_sample-*/{identity}_seed-*_sample-*_model.cif*'))
        if samples:
            aliases.append(structure)
        stem = structure.name.removesuffix('.gz').removesuffix('.cif')
        summary = structure.with_name(stem.removesuffix('_model') + '_summary_confidences.json')
        if not summary.is_file() or summary.is_symlink() or structure.is_symlink():
            raise ValueError('RF3 native pair missing or symlinked')
        if structure in aliases:
            summaries.add(summary)
            continue
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
    # Preserve native aliases/rankings separately, not as candidate records.
    archive = output / 'native_artifacts'
    for source in [*aliases, *native_root.rglob('*_ranking_scores.csv')]:
        destination = archive / source.relative_to(native_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    for alias in aliases:
        identity = alias.name.removesuffix('.gz').removesuffix('.cif').removesuffix('_model')
        for source in alias.parent.glob(identity + '*confidences.json'):
            destination = archive / source.relative_to(native_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    alias_summaries = {p.with_name(p.name.removesuffix('.gz').removesuffix('.cif').removesuffix('_model') + '_summary_confidences.json') for p in aliases}
    for summary in summaries - alias_summaries:
        full = summary.with_name(summary.name.replace('_summary_confidences.json', '_confidences.json'))
        if full.is_file():
            (output / full.name).write_bytes(full.read_bytes())


def archive_top_aliases(native_root, output):
    """Legacy staging: remove only redundant native aliases, adding no gates."""
    native_root, output = Path(native_root), Path(output)
    structures = sorted([*native_root.rglob('*_model.cif'), *native_root.rglob('*_model.cif.gz')])
    sources = set(native_root.rglob('*_ranking_scores.csv'))
    for structure in structures:
        identity = structure.name.removesuffix('.gz').removesuffix('.cif').removesuffix('_model')
        if list(structure.parent.glob(f'seed-*_sample-*/{identity}_seed-*_sample-*_model.cif*')):
            sources.add(structure)
            for suffix in ('_summary_confidences.json', '_confidences.json'):
                sibling = structure.with_name(identity + suffix)
                if sibling.is_file():
                    sources.add(sibling)
    output.mkdir(parents=True, exist_ok=True)
    for source in sources:
        destination = output / source.relative_to(native_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--native-root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--archive-aliases-only', action='store_true')
    args = parser.parse_args()
    (archive_top_aliases if args.archive_aliases_only else export_pairs)(args.native_root, args.output)
