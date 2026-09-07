"""Pinned FA export identity captured at the parser, PDB and PKL writers.

The native batch's explicit input/path association is the authority, not a
post-hoc basename match. Hashes only preserve bytes captured at those seams.
No model features, selections, coordinates or inference settings are changed.
"""
import contextvars
import hashlib
import json
from pathlib import Path
import sys

from maturation_native_adapter import (
    SOURCE_SHA256, _SourceLoader, _replace, _array, _identity,
    identity_protein, instrument_source as identity_source,
)

VERSION = 'fampnn-native-export-v1'
_CONTEXT = contextvars.ContextVar('fampnn_probability_export')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def receipt_path(candidate):
    return Path(str(candidate) + '.fa_binding.json')


def read_receipt(candidate):
    try:
        receipt = json.loads(receipt_path(candidate).read_text())
    except (OSError, ValueError) as exc:
        raise ValueError('native binding: missing/invalid writer receipt') from exc
    fields = {'version', 'source_files', 'candidate_id', 'input_id', 'source_pdb_sha256',
              'candidate_pdb_sha256', 'sample_pkl_sha256', 'records'}
    if (not isinstance(receipt, dict) or set(receipt) != fields
            or receipt.get('version') != VERSION or receipt.get('source_files') != SOURCE_SHA256['fampnn']
            or receipt.get('candidate_id') != Path(candidate).stem):
        raise ValueError('native binding: unsupported or foreign writer identity')
    import re
    if (not isinstance(receipt['input_id'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', receipt['input_id'])):
        raise ValueError('native binding: invalid input identity')
    for key in ('source_pdb_sha256', 'candidate_pdb_sha256', 'sample_pkl_sha256'):
        if key == 'sample_pkl_sha256' and receipt[key] is None:
            continue
        if not isinstance(receipt[key], str) or not re.fullmatch('[0-9a-f]{64}', receipt[key]):
            raise ValueError('native binding: invalid producer digest')
    if not isinstance(receipt['records'], list) or any(
        not isinstance(r, dict) or set(r) != {'chain_index', 'residue_index', 'source', 'candidate'}
        or type(r['chain_index']) is not int or type(r['residue_index']) is not int
        or not isinstance(r['source'], str)
        or (r['candidate'] is not None and not isinstance(r['candidate'], str)) for r in receipt['records']):
        raise ValueError('native binding: invalid export records')
    return receipt


def capture_input(path, data):
    mapping = {}
    identities, chains, residues = (_array(data[k]) for k in ('bms_identity', 'chain_index', 'residue_index'))
    if not len(identities) == len(chains) == len(residues):
        raise ValueError('native binding: parser identity axis mismatch')
    for encoded, chain, number in zip(identities, chains, residues):
        key = (int(chain), int(number))
        if key in mapping:
            raise ValueError('native binding: duplicate parser identity')
        mapping[key] = ':'.join(map(str, _identity(encoded)))
    return dict(input_id=Path(path).stem, source_pdb_sha256=sha(Path(path).read_bytes()), mapping=mapping)


def save_samples(writer, samples, paths, inputs):
    if len(paths) != len(inputs) or len(set(map(str, paths))) != len(paths):
        raise ValueError('native binding: ambiguous batch ownership')
    token = _CONTEXT.set({str(Path(p).resolve()): dict(inputs[i], present=_array(samples['seq_mask'][i]))
                          for i, p in enumerate(paths)})
    try:
        return writer(samples, paths)
    finally:
        _CONTEXT.reset(token)


def capture_candidate(path, prot, chain_ids):
    context = _CONTEXT.get()[str(Path(path).resolve())]
    records = []
    if len(context['present']) != len(prot.residue_index):
        raise ValueError('native binding: writer presence axis mismatch')
    for i in range(len(prot.residue_index)):
        if not context['present'][i]:
            if any(float(v) >= .5 for v in prot.atom_mask[i]):
                raise ValueError('native binding: absent row emitted atoms')
            continue
        key = (int(prot.chain_index[i]), int(prot.residue_index[i]))
        emitted = any(float(v) >= .5 for v in prot.atom_mask[i])
        if key not in context['mapping']:
            if emitted:
                raise ValueError('native binding: unowned exported residue')
            continue  # native padding has no emitted atoms
        records.append(dict(chain_index=key[0], residue_index=key[1],
            source=context['mapping'][key], candidate=f'{chain_ids[key[0]]}:{key[1]}:' if emitted else None))
    receipt = dict(version=VERSION, source_files=SOURCE_SHA256['fampnn'],
        input_id=context['input_id'], candidate_id=Path(path).stem,
        source_pdb_sha256=context['source_pdb_sha256'], candidate_pdb_sha256=sha(Path(path).read_bytes()),
        sample_pkl_sha256=None, records=records)
    target = receipt_path(path)
    if target.exists():
        raise ValueError('native binding: duplicate candidate export')
    target.write_text(json.dumps(receipt, sort_keys=True) + '\n')


def capture_sample(path, candidate, context):
    # Called AFTER the native pickle stream is closed, using the same native j
    # that selected samples[j], pdbs[j], and pdb_batch_files[j].
    receipt = read_receipt(candidate)
    if (receipt['input_id'] != context['input_id']
            or receipt['source_pdb_sha256'] != context['source_pdb_sha256']
            or receipt['sample_pkl_sha256'] is not None
            or sha(Path(candidate).read_bytes()) != receipt['candidate_pdb_sha256']):
        raise ValueError('native binding: changed/duplicate export')
    receipt['sample_pkl_sha256'] = sha(Path(path).read_bytes())
    receipt_path(candidate).write_text(json.dumps(receipt, sort_keys=True) + '\n')


def validate_binding(candidate, source_bytes, candidate_bytes, *, input_id, sample_bytes=None):
    receipt = read_receipt(candidate)
    if (receipt['input_id'] != input_id
            or receipt['source_pdb_sha256'] != sha(source_bytes)
            or receipt['candidate_pdb_sha256'] != sha(candidate_bytes)
            or (sample_bytes is not None and receipt['sample_pkl_sha256'] != sha(sample_bytes))):
        raise ValueError('native binding: artifact differs from producer capture')
    return receipt


def instrument_source(relative, data):
    if sha(data) != SOURCE_SHA256['fampnn'].get(relative):
        raise ValueError('unsupported native source identity')
    if relative == 'fampnn/data/protein.py':
        return identity_source('fampnn', relative, data).replace('import maturation_native_adapter as _bms', 'import fampnn_native_binding as _bms')
    text = data.decode()
    if relative == 'fampnn/inference/seq_design.py':
        text = _replace(text, '        batch_list = []', '        batch_list = []\n        bms_inputs = []')
        text = _replace(text, '            single = process_single_pdb(data)', '            bms_inputs.append(_bms.capture_input(pdb_file, data))\n            single = process_single_pdb(data)')
        text = _replace(text, '        SeqDenoiser.save_samples_to_pdb(samples, pdbs)', '        _bms.save_samples(SeqDenoiser.save_samples_to_pdb, samples, pdbs, bms_inputs)')
        text = _replace(text, '                pickle.dump(sample_j, f)', '                pickle.dump(sample_j, f)\n            _bms.capture_sample(f.name, pdbs[j], bms_inputs[j])')
    elif relative == 'fampnn/data/pdb_utils.py':
        text = _replace(text, '        f.write(protein.to_pdb(prot, conect=conect))', '        f.write(protein.to_pdb(prot, conect=conect))\n    _bms.capture_candidate(filename, prot, protein.PDB_CHAIN_IDS)')
    return 'import fampnn_native_binding as _bms\n' + text


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('native_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.native_args
    if argv and argv[0] == '--':
        argv = argv[1:]
    root = Path(args.root).resolve()
    entry = 'fampnn/inference/seq_design.py'
    if not argv or Path(argv[0]).resolve() != root/entry:
        raise ValueError('unsupported native entrypoint identity')
    sources = {p: instrument_source(p, (root/p).read_bytes()) for p in SOURCE_SHA256['fampnn']}
    if any(p[:-3].replace('/', '.') in sys.modules for p in sources):
        raise ValueError('native source imported before identity instrumentation')
    sys.meta_path.insert(0, _SourceLoader('fampnn', root, sources))
    sys.path.insert(0, str(root))
    sys.argv = argv
    exec(compile(sources[entry], argv[0], 'exec'), {'__name__': '__main__', '__file__': argv[0]})


if __name__ == '__main__':
    sys.modules['fampnn_native_binding'] = sys.modules[__name__]
    main()
