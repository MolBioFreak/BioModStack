#!/usr/bin/env python3
"""Transport explicit selected-document and native sample identity for PPIFlow children.

These sidecars are evidence, not admission criteria. Unknown historical associations
remain unknown; a basename is only a lookup key within an explicit emitted sidecar.
"""
import argparse
import json
import shutil
from pathlib import Path


def stage(manifest_path, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = json.loads(manifest_path.read_text())
    staged = []
    used = set()
    for index, row in enumerate(rows):
        source = Path(row['path'])
        # Deterministic unique staging names prevent same-name documents colliding.
        name = f"source_{index:06d}.pdb"
        if name in used:
            raise ValueError(f'duplicate staged name: {name}')
        used.add(name)
        shutil.copy2(source, output_dir / name)
        staged.append({'staged_name': name, 'source_path': str(source),
                       'source_meta': row['meta']})
    (output_dir / 'source_identity.json').write_text(json.dumps(staged, indent=2))


def sample(source_manifest, meta_json, pdb_path, output_path):
    source_rows = json.loads(source_manifest.read_text()) if source_manifest.is_file() else []
    source_by_name = {row['staged_name']: row for row in source_rows}
    meta = json.loads(meta_json)
    parent = source_by_name.get(meta.get('source_staged_name'))
    output_path.write_text(json.dumps({
        'pdb_name': pdb_path.name,
        'sample_meta': meta,
        'source': parent,
        'validation_status': 'unvalidated',
    }, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    stage_parser = sub.add_parser('stage')
    stage_parser.add_argument('manifest', type=Path)
    stage_parser.add_argument('output_dir', type=Path)
    sample_parser = sub.add_parser('sample')
    sample_parser.add_argument('source_manifest', type=Path)
    sample_parser.add_argument('meta_json')
    sample_parser.add_argument('pdb', type=Path)
    sample_parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.command == 'stage':
        stage(args.manifest, args.output_dir)
    else:
        sample(args.source_manifest, args.meta_json, args.pdb, args.output)


if __name__ == '__main__':
    main()
