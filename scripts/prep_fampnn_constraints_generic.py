#!/usr/bin/env python3
"""Translate public chain/author-residue selections to native FAMPNN masks.

Unmarked legacy callers retain empty masks. Public requests validate the exact
source and prepared domains; the pinned parser cannot represent insertion codes,
negative residue numbers, non-letter chain IDs, or multiple coordinate models.
No chain-order or residue-offset correspondence is inferred.
"""
import argparse
import base64
import csv
import json
import re
from pathlib import Path


def pdb_domain(path):
    """Ordered exact ATOM identities, rejecting parser-ambiguous structures."""
    residues = {}
    atoms = set()
    models = 0
    previous = None
    for line in Path(path).read_text().splitlines():
        if line.startswith('MODEL '):
            models += 1
            if models > 1:
                raise ValueError('multiple PDB models are ambiguous')
        if line.startswith('HETATM'):
            raise ValueError('generic protein design cannot preserve unrecognized HETATM identity')
        if not line.startswith('ATOM  '):
            continue
        if len(line) < 54 or line[16].strip():
            raise ValueError('malformed or alternate-location PDB atom')
        chain, number, insertion = line[21], int(line[22:26]), line[26].strip()
        if not re.fullmatch('[A-Za-z]', chain) or insertion or number < 0:
            raise ValueError('native parser cannot represent this chain/residue identity')
        identity = (chain, number)
        atom = (identity, line[12:16].strip())
        if atom in atoms or (identity != previous and identity in residues):
            raise ValueError('duplicate or discontiguous PDB residue identity')
        atoms.add(atom)
        name = line[17:20]
        if identity in residues and residues[identity] != name:
            raise ValueError('ambiguous residue identity')
        residues[identity] = name
        previous = identity
    if not residues:
        raise ValueError('input PDB has no protein ATOM residues')
    return residues


def selected_chains(value, domain, field):
    if value is None or value == '':
        return set()
    if not isinstance(value, str):
        raise ValueError(f'{field} must be a comma-separated chain string')
    parts = [v.strip() for v in value.split(',')]
    if any(not re.fullmatch('[A-Za-z]', v) for v in parts):
        raise ValueError(f'{field}: malformed chains')
    result = set(parts)
    if not result <= {c for c, _ in domain}:
        raise ValueError(f'{field}: absent chain selection')
    return result


def fixed_residues(value, domain):
    if value is None or value == '':
        return set()
    if not isinstance(value, str):
        raise ValueError('fixed_positions must be an author-residue selection string')
    result = set()
    for token in value.split(','):
        match = re.fullmatch(r'([A-Za-z]):(\d+)(?:-(\d+))?', token.strip())
        if not match:
            raise ValueError('malformed fixed_positions; expected A:1 or A:1-10')
        chain, start, end = match.groups()
        start, end = int(start), int(end if end is not None else start)
        if start > end:
            raise ValueError('invalid or absent fixed_positions range')
        # Match the existing typed-admission interval semantics: select actual
        # author residues within the interval, without inventing missing loop
        # residues or iterating an arbitrarily large integer range.
        selected = {(c, n) for c, n in domain if c == chain and start <= n <= end}
        if not selected:
            raise ValueError('fixed_positions contains absent residues')
        result |= selected
    return result


def constraints(domain, request):
    mode = request['sequence_design_mode']
    if mode not in {'design', 'fixed_backbone', 'binder_design'}:
        raise ValueError('unsupported generic FAMPNN mode')
    design = selected_chains(request.get('design_chain'), domain, 'design_chain')
    target = selected_chains(request.get('target_chain'), domain, 'target_chain')
    if design & target:
        raise ValueError('design_chain and target_chain overlap')
    if not design:
        raise ValueError('design_chain must select at least one present chain')
    fixed = fixed_residues(request.get('fixed_positions'), domain)
    fix_sc = request.get('fampnn_fix_target_sidechains')
    if fix_sc is None:
        fix_sc = False
    if not isinstance(fix_sc, bool):
        raise ValueError('fampnn_fix_target_sidechains must be boolean')
    # Undeclared chains are preserved, not silently redesigned. Explicit fixed
    # positions constrain sequence only; target sidechain control is independent.
    untouched = {r for r in domain if design and r[0] not in design | target}
    target_res = {r for r in domain if r[0] in target}
    seq = fixed | untouched | target_res
    sc = untouched | (target_res if fix_sc else set())
    return seq, sc


def write_constraints(input_dir, out_csv, request=None, prepared_dir=None):
    files = sorted(Path(input_dir).glob('*.pdb'))
    if request is not None and not files:
        raise ValueError('no input PDB files')
    rows = []
    for source in files:
        seq, sc = set(), set()
        if request is not None:
            domain = pdb_domain(source)
            if prepared_dir is None:
                raise ValueError('public constraints require prepared identity verification')
            prepared = pdb_domain(Path(prepared_dir) / source.name)
            if domain != prepared or list(domain) != list(prepared):
                raise ValueError('preparation changed source residue identity/order')
            seq, sc = constraints(domain, request)
        encode = lambda values: ','.join(f'{c}{n}' for c, n in sorted(values))
        rows.append((source.stem, encode(seq), encode(sc)))
    with Path(out_csv).open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['pdb', 'fixed_seq_positions', 'fixed_sidechains'])
        writer.writerows(rows)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input_dir', required=True)
    parser.add_argument('--out_csv', required=True)
    parser.add_argument('--request_base64')
    parser.add_argument('--prepared_dir')
    args = parser.parse_args()
    request = json.loads(base64.b64decode(args.request_base64, validate=True)) if args.request_base64 is not None else None
    rows = write_constraints(args.input_dir, args.out_csv, request, args.prepared_dir)
    print(f'Wrote generic FAMPNN constraints for {len(rows)} PDBs')


if __name__ == '__main__':
    main()
