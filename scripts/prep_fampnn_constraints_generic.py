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
        writer.writerow(['pdb', 'fixed_seq_positions', 'fixed_sidechains'] +
                        (['bms_source'] if request is not None else []))
        import hashlib
        # The native reader selects named mask columns. Carry the original
        # bytes' identity in this already-staged per-input document, before
        # Rosetta-restored coordinates become the native input.
        for source, row in zip(files, rows):
            evidence = None
            if request is not None:
                prepared = Path(prepared_dir) / source.name
                evidence = dict(source_structure_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                                prepared_sha256=hashlib.sha256(prepared.read_bytes()).hexdigest())
            writer.writerow([*row, json.dumps(evidence)] if request is not None else row)
    return rows


def annotate_outputs(directory, request, source_csv=None):
    """Attach sequence identity from the actual native output, never input_seq."""
    from Bio.SeqUtils import seq1
    for path in sorted(Path(directory).glob('*.pdb')):
        # Preserve native author IDs in emitted order, with no sequence/filename
        # inference about which source candidate produced this structure.
        residues = {}
        for line in path.read_text().splitlines():
            if line.startswith('ATOM  '):
                residues.setdefault((line[21], int(line[22:26]), line[26].strip()), line[17:20])
        sequences = {}
        mapping = []
        for (chain, number, insertion), name in residues.items():
            sequences[chain] = sequences.get(chain, '') + seq1(name)
            mapping.append(dict(chain_id=chain, author_number=number, insertion_code=insertion))
        metadata_path = path.with_suffix('.json')
        record = json.loads(metadata_path.read_text())
        design = [c.strip() for c in (request.get('design_chain') or '').split(',') if c.strip()]
        targets = [c.strip() for c in (request.get('target_chain') or '').split(',') if c.strip()]
        record.update(chain_sequences=sequences,
                      designed_chain_sequences={c: sequences[c] for c in design if c in sequences},
                      binder_chains=design, target_chains=targets,
                      output_structure_name=path.name, residue_mapping=mapping)
        record.update(fampnn_correspondence(path, source_csv))
        record.update(input_binder_chains=design, input_target_chains=targets)
        join = record.get('source_residue_mapping')
        if join is not None:
            def project(chains):
                return list(dict.fromkeys(r['output']['chain_id'] for r in join
                                          if r['source']['chain_id'] in chains))
            output_design, output_targets = project(design), project(targets)
            record.update(binder_chains=output_design, target_chains=output_targets,
                          chain_roles_namespace='output',
                          designed_chain_sequences={c: sequences[c] for c in output_design if c in sequences})
        metadata_path.write_text(json.dumps(record) + '\n')


def fampnn_correspondence(path, source_csv):
    """Compose verified preparation identity with the native batch/writer join.

    The shell copies the writer sidecar with the exact PDB it renames. Neither
    sample suffix parsing nor sequence matching establishes input ownership.
    Missing optional evidence does not change execution or scientific success.
    """
    import hashlib
    unavailable = dict(source_structure_sha256=None, source_residue_mapping=None)
    if not source_csv:
        return unavailable
    try:
        receipt = json.loads(Path(str(path) + '.fa_binding').read_text())
        with Path(source_csv).open() as handle:
            sources = {r['pdb']: json.loads(r.get('bms_source') or 'null')
                       for r in csv.DictReader(handle)}
        source = sources.get(receipt['input_id'])
        if (not source or source['prepared_sha256'] != receipt['source_pdb_sha256']
                or hashlib.sha256(path.read_bytes()).hexdigest() != receipt['candidate_pdb_sha256']):
            return unavailable
        def identity(value):
            chain, number, insertion = value.split(':')
            return dict(chain_id=chain, auth_seq_id=int(number), insertion_code=insertion)
        return dict(source_structure_sha256=source['source_structure_sha256'],
                    source_residue_mapping=[dict(source=identity(r['source']), output=identity(r['candidate']))
                                            for r in receipt['records'] if r['candidate'] is not None])
    except (OSError, ValueError, KeyError, TypeError):
        return unavailable


def run_native(native_script, argv):
    """Reuse the installed parser/writer taps, observationally for standalone.

    Unsupported installed source retains its normal execution without mapping.
    Existing strict analysis callers keep their original launcher and gates.
    """
    import runpy
    import sys
    import warnings
    try:
        import fampnn_native_binding as binding
        root = Path(native_script).resolve().parents[2]
        sources = {p: binding.instrument_source(p, (root/p).read_bytes())
                   for p in binding.SOURCE_SHA256['fampnn']}
    except (ImportError, OSError, ValueError) as exc:
        warnings.warn(f'FA-MPNN correspondence unavailable: {exc}')
        sys.argv = [native_script, *argv]
        return runpy.run_path(native_script, run_name='__main__')
    def optional(function, fallback):
        def capture(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except (OSError, ValueError, KeyError, TypeError, LookupError) as exc:
                warnings.warn(f'FA-MPNN correspondence unavailable: {exc}')
                return fallback
        return capture
    binding.capture_input = optional(binding.capture_input, {})
    binding.capture_candidate = optional(binding.capture_candidate, None)
    binding.capture_sample = optional(binding.capture_sample, None)
    sys.meta_path.insert(0, binding._SourceLoader('fampnn', root, sources))
    sys.path.insert(0, str(root))
    sys.argv = [native_script, *argv]
    exec(compile(sources['fampnn/inference/seq_design.py'], native_script, 'exec'),
         {'__name__': '__main__', '__file__': native_script})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-script')
    parser.add_argument('--source_csv')
    parser.add_argument('native_args', nargs=argparse.REMAINDER)
    parser.add_argument('--input_dir')
    parser.add_argument('--out_csv')
    parser.add_argument('--annotate_dir')
    parser.add_argument('--request_base64')
    parser.add_argument('--prepared_dir')
    args = parser.parse_args()
    if args.native_script:
        argv = args.native_args[1:] if args.native_args[:1] == ['--'] else args.native_args
        return run_native(args.native_script, argv)
    request = json.loads(base64.b64decode(args.request_base64, validate=True)) if args.request_base64 is not None else None
    if args.annotate_dir:
        if request is None:
            parser.error('--annotate_dir requires --request_base64')
        annotate_outputs(args.annotate_dir, request, args.source_csv)
        return
    if not args.input_dir or not args.out_csv:
        parser.error('--input_dir and --out_csv are required for constraints')
    rows = write_constraints(args.input_dir, args.out_csv, request, args.prepared_dir)
    print(f'Wrote generic FAMPNN constraints for {len(rows)} PDBs')


if __name__ == '__main__':
    main()
