#!/usr/bin/env python3
"""Publish selected refinement outputs and source evidence, without scoring."""
import argparse
import base64
import json
from pathlib import Path
import shutil


def publish(pdb, meta, output_dir):
    pdb, output_dir = Path(pdb), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdb, output_dir / pdb.name)
    record = dict(meta)
    record.update(pdb_file=pdb.name, source=meta.get('terminal_producer', 'selected_input'))
    # All metrics remain with their actual producer. No parent confidence is
    # promoted to a changed descendant and no synthetic score is supplied.
    (output_dir / f'generator_{pdb.stem}.json').write_text(json.dumps(record, indent=2))
    (output_dir / f'{pdb.stem}_sample_identity.json').write_text(json.dumps({
        'pdb_name': pdb.name, 'sample_meta': meta,
        'source': {'staged_name': meta.get('source_staged_name'), 'source_meta': meta.get('source_meta')},
        'validation_status': meta.get('validation_status'),
    }, indent=2))
    return record


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pdb', required=True)
    p.add_argument('--meta-base64', required=True)
    p.add_argument('--output-dir', required=True)
    a = p.parse_args()
    publish(a.pdb, json.loads(base64.b64decode(a.meta_base64)), a.output_dir)
