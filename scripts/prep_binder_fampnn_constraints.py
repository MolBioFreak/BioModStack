#!/usr/bin/env python3
"""Existing generic FA-MPNN masks plus explicitly selected anchor evidence."""
import argparse
import base64
import json
from pathlib import Path
from prep_fampnn_constraints_generic import write_constraints


def remap_positions(spec, rows, unrepresented=None):
    """Join author masks to actual emitted atoms, including insertion codes."""
    import re
    selected = []
    for token in (spec or '').split(','):
        if not token.strip():
            continue
        m = re.fullmatch(r'([A-Za-z0-9]):?(-?\d+)([A-Za-z]?)(?:-(-?\d+))?', token.strip())
        if not m:
            raise ValueError(f'Invalid source fixed position: {token}')
        chain, start, insertion, end = m.groups()
        matches = [r for r in rows if r['source']['chain_id'] == chain and
                   int(start) <= r['source']['auth_seq_id'] <= int(end or start) and
                   (not insertion if end else r['source'].get('insertion_code', '') == insertion)]
        if not matches and unrepresented is not None:
            unrepresented.append(token.strip())
        for row in matches:
            out = row['output']
            selected.append(f"{out['chain_id']}:{out['auth_seq_id']}")
    return ','.join(dict.fromkeys(selected))


def prepare(input_dir, prepared_dir, output_csv, request, anchors_path, metadata=None):
    anchors = json.loads(Path(anchors_path).read_text()).get('anchors', [])
    # Native anchors carry explicit source residue identity. Do not infer chain
    # roles or fabricate CDR positions for generic selected binders.
    anchor_positions = [f"{row['chain']}:{row['resnum']}{row.get('icode', row.get('insertion_code', ''))}" for row in anchors
                        if not row.get('movable_region_member')]
    request = dict(request)
    request['fixed_positions'] = ','.join(filter(None, [request.get('fixed_positions'), *anchor_positions]))
    if metadata and metadata.get('residue_mapping'):
        request['source_fixed_positions'] = request['fixed_positions']
        unrepresented = []
        request['fixed_positions'] = remap_positions(request['fixed_positions'], metadata['residue_mapping'], unrepresented)
        request['unrepresented_source_fixed_positions'] = unrepresented
    Path('transport').mkdir(exist_ok=True)
    Path('transport/request.json').write_text(json.dumps(request))
    # Pinned seq_design.py reads exactly three headerless positional columns.
    # Keep hash custody separate rather than shifting its native CSV columns.
    rows = write_constraints(input_dir, 'transport/source.csv', request, prepared_dir)
    import csv
    with Path(output_csv).open('w', newline='') as handle:
        csv.writer(handle).writerows(rows)
    return rows


def annotate_inline(directory, transport_dir):
    """Preserve actual native child identity; never inherit flow sample identity."""
    import hashlib
    from prep_fampnn_constraints_generic import annotate_outputs
    transport = Path(transport_dir)
    request = json.loads((transport / 'request.json').read_text())
    annotate_outputs(directory, request, transport / 'source.csv')
    for path in Path(directory).glob('*.pdb'):
        document = path.with_suffix('.json')
        record = json.loads(document.read_text())
        record.update(producer='fampnn', artifact_class='sequence_designed_complex',
                      sequence_semantics='native_fampnn_output',
                      sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size,
                      sequence_design_request=request)
        binding = Path(str(path) + '.fa_binding')
        if binding.is_file() and record.get('source_residue_mapping') is not None:
            receipt = json.loads(binding.read_text())
            record.update(native_input_id=receipt['input_id'], native_output_id=receipt['candidate_id'])
        document.write_text(json.dumps(record))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-dir', required=True)
    p.add_argument('--prepared-dir', required=True)
    p.add_argument('--out-csv', required=True)
    p.add_argument('--request-base64', required=True)
    p.add_argument('--anchors', required=True)
    p.add_argument('--meta-base64')
    a = p.parse_args()
    prepare(a.input_dir, a.prepared_dir, a.out_csv, json.loads(base64.b64decode(a.request_base64)), a.anchors,
            json.loads(base64.b64decode(a.meta_base64)) if a.meta_base64 else None)
