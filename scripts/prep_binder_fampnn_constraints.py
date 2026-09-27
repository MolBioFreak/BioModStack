#!/usr/bin/env python3
"""Existing generic FA-MPNN masks plus explicitly selected anchor evidence."""
import argparse
import base64
import json
from pathlib import Path
from prep_fampnn_constraints_generic import write_constraints


def prepare(input_dir, prepared_dir, output_csv, request, anchors_path):
    anchors = json.loads(Path(anchors_path).read_text()).get('anchors', [])
    # Native anchors carry explicit source residue identity. Do not infer chain
    # roles or fabricate CDR positions for generic selected binders.
    anchor_positions = [f"{row['chain']}:{row['resnum']}" for row in anchors
                        if not row.get('movable_region_member')]
    request = dict(request)
    request['fixed_positions'] = ','.join(filter(None, [request.get('fixed_positions'), *anchor_positions]))
    return write_constraints(input_dir, output_csv, request, prepared_dir)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-dir', required=True)
    p.add_argument('--prepared-dir', required=True)
    p.add_argument('--out-csv', required=True)
    p.add_argument('--request-base64', required=True)
    p.add_argument('--anchors', required=True)
    a = p.parse_args()
    prepare(a.input_dir, a.prepared_dir, a.out_csv, json.loads(base64.b64decode(a.request_base64)), a.anchors)
