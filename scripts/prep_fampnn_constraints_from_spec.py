#!/usr/bin/env python3
"""Materialize local keep/change intent through each preparation correspondence."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


def pdb_identities(path):
    return list(dict.fromkeys(
        f'{line[21]}:{int(line[22:26])}:{line[26].strip()}'
        for line in Path(path).read_text().splitlines() if line.startswith('ATOM  ')))


def select_positions(spec, domain):
    """Author selections, not ordinals or alignment positions.

    As in the existing source-mask adapter, absent identities match nothing;
    a range selects only identities actually present in this particular input.
    """
    selected = set()
    for token in (spec or '').split(','):
        if not token.strip():
            continue
        match = re.fullmatch(r'([A-Za-z0-9]):?(-?\d+)([A-Za-z]?)(?:-(-?\d+))?', token.strip())
        if not match:
            raise ValueError(f'Invalid source fixed position: {token}')
        chain, first, insertion, last = match.groups()
        for identity in domain:
            c, n, ins = identity.split(':')
            if c == chain and int(first) <= int(n) <= int(last or first) and (
                    not insertion if last else ins == insertion):
                selected.add(identity)
    return selected


def sequence_scope(domain, design_chains='', fixed_positions='', designable_positions=''):
    """One source's authorized sequence region; sidechains are independent."""
    chains = {c.strip() for c in (design_chains or '').split(',') if c.strip()}
    authorized = {v for v in domain if not chains or v.split(':')[0] in chains}
    if designable_positions:
        authorized &= select_positions(designable_positions, domain)
    authorized -= select_positions(fixed_positions, domain)
    return [v for v in domain if v in authorized]


def write_constraints(input_dir, out_csv, *, manifest=None, fixed_positions='',
                      designable_positions='', design_chains='', fix_sidechains=False):
    manifest = manifest or {}
    fixed_spec = fixed_positions or str(manifest.get('sequence_fixed_positions_spec',
                                                    manifest.get('fixed_positions_spec', '')) or '')
    rows = []
    for pdb_path in sorted(Path(input_dir).glob('*.pdb')):
        receipt_path = pdb_path.with_suffix('.fampnn_prep.json')
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text())
            domain = receipt['source_domain']
            mapping = dict(receipt['pairs'])
        else:
            domain = pdb_identities(pdb_path)
            mapping = {v: v for v in domain}
        authorized = set(sequence_scope(domain, design_chains, fixed_spec, designable_positions))
        fixed = [mapping[v] for v in domain if v not in authorized]
        # Native positional CSV accepts compact author-number selectors. Keep
        # insertion suffixes explicit (native support is a separate limitation),
        # never silently turn an insertion identity into its uninserted sibling.
        encoded = ','.join(c + n + ins for c, n, ins in (v.split(':') for v in fixed))
        rows.append((pdb_path.stem, encoded, encoded if fix_sidechains else ''))
    with Path(out_csv).open('w', newline='', encoding='utf-8') as handle:
        csv.writer(handle).writerows(rows)  # native reader is headerless/positional
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input_dir', required=True)
    parser.add_argument('--manifest', default='')
    parser.add_argument('--fixed_positions', default='')
    parser.add_argument('--designable_positions', default='')
    parser.add_argument('--design_chains', default='')
    parser.add_argument('--out_csv', required=True)
    parser.add_argument('--fix_sidechains', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text()) if args.manifest else {}
    rows = write_constraints(args.input_dir, args.out_csv, manifest=manifest,
        fixed_positions=args.fixed_positions, designable_positions=args.designable_positions,
        design_chains=args.design_chains, fix_sidechains=args.fix_sidechains)
    print(f'Wrote constraints for {len(rows)} PDBs to {args.out_csv}')


if __name__ == '__main__':
    main()
