"""Source-sealed, export-only maturation instrumentation (no model policy).

Only the sealed installed dialect is supported. Identity is captured inside each
parser's accepted-residue loop and follows native selections; export hooks see
actual Protein arrays, masks and paths. No sequence alignment or guessed IDs.
"""
import contextvars
import dataclasses
import hashlib
import importlib.abc
import importlib.util
import json
from pathlib import Path
import sys

from maturation_correspondence import publish_native_export

SOURCE_SHA256 = {
    'ppiflow': {
        'data/parsers.py': '9a143cce4397a7049600a2c96b8b850f8f8b980c60d98b805a6b6406a9c6a732',
        'data/protein.py': '238c1430a81593235ca84c202115859a3940bf9910ddbbc1c7e5abf763e33014',
        'data/utils.py': '06a4c23b9a0fc1b2caa077a69b9d935643e1dc977ec890a16a0ff9651f8a17f3',
        'data/datasets_antibody.py': 'f5a929dba64c56a2116008a64e23407f0b88d61b5c90e1819e82c88e07cc8c8e',
        'sample_antibody_nanobody_partial.py': 'c38343f08d5f0546da0f1c8df8c21fe816f878a3535ed80ea2fa8ba416865fcc',
        'models/flow_module_antibody_partial.py': '2aff7cc34a0d7dc7013a298392f3a7c447181b79e9b653f08d40c4a0f3e03ad9',
        'analysis/utils.py': '04d085d66f2c164312ba41591577c02484a4e403a7d00d8a184772f25dc588f2',
    },
    'fampnn': {
        'fampnn/data/protein.py': '67059e09dbb90f12e04a60018b67e272e5a45e1bc286b01881e36dca10a7bc93',
        'fampnn/data/data.py': '830a51971c30d65e17d44fc85abbb5a9896eb63d7e455a3c1a55c334bb733f29',
        'fampnn/data/pdb_utils.py': 'f57019158d469b6905ee887fbf2e36bd8e73194ba6709f92685f665ec72979eb',
        'fampnn/inference/seq_design.py': '9d790bf2009ed9ec7b863d36e19e035cb06e6287ce86e0e4474a2009d3e2e9b5',
        'fampnn/model/sd_model.py': 'c11837a76e25b956e1f57f28b7d95950177e6d762ce82c915ac4685b836b5b72',
    },
}
_AUTHORITY = contextvars.ContextVar('maturation_authority')
_FAMPNN = contextvars.ContextVar('maturation_fampnn_export')
_PENDING_PARTIAL = contextvars.ContextVar('maturation_partial_export', default=None)


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('source identity: instrumentation anchor changed')
    return text.replace(old, new)


def instrument_source(producer, relative_path, data):
    expected = SOURCE_SHA256.get(producer, {}).get(relative_path)
    if expected is None or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('unsupported native source identity')
    text = data.decode('utf-8')
    if (producer, relative_path) == ('ppiflow', 'data/parsers.py'):
        text = _replace(text, '    chain_ids = []', '    chain_ids = []\n    bms_identity = []')
        text = _replace(text, '        residue_index.append(res.id[1])',
                        '        bms_identity.append([ord(chain.id), int(res.id[1]), ord(res.id[2])])\n        residue_index.append(res.id[1])')
        text = _replace(text, '    return Protein(', '    return _bms.identity_protein(Protein, bms_identity=np.array(bms_identity),')
    elif (producer, relative_path) == ('ppiflow', 'data/utils.py'):
        text = _replace(text, '    ca_idx = residue_constants.atom_order[\'CA\']',
                        '    if "bms_identity" in chain_feats and scale_factor != 1.:\n        raise ValueError("unsupported native coordinate scaling")\n    ca_idx = residue_constants.atom_order[\'CA\']')
        text = _replace(text, "    scaled_pos = centered_pos / scale_factor", '    if "bms_identity" in chain_feats:\n        offset = chain_feats.get("bms_offset", np.zeros((len(centered_pos), 3)))\n        chain_feats["bms_offset"] = offset + (bb_center if normalize_positions else 0)\n    scaled_pos = centered_pos / scale_factor')
    elif (producer, relative_path) == ('ppiflow', 'data/datasets_antibody.py'):
        prefix, text = text.split('class AntibodyPartialDataset(Dataset):', 1)
        text = _replace(text, '            "res_plddt": res_plddt,', '            "bms_identity": torch.tensor(processed_feats["bms_identity"]),\n            "bms_offset": torch.tensor(processed_feats["bms_offset"]),\n            "res_plddt": res_plddt,')
        text = _replace(text, '        trans_1 -= motif_com[None, :]', '        feats["bms_offset"] = feats["bms_offset"] + motif_com[None, :]\n        trans_1 -= motif_com[None, :]')
        text = _replace(text, '            "diffuse_mask": diffuse_mask,', '            "bms_identity": feats["bms_identity"],\n            "bms_offset": feats["bms_offset"],\n            "diffuse_mask": diffuse_mask,')
        text = prefix + 'class AntibodyPartialDataset(Dataset):' + text
    elif (producer, relative_path) == ('ppiflow', 'models/flow_module_antibody_partial.py'):
        text = _replace(text, 'final_pos, pdb_path, no_indexing=True,', 'final_pos, pdb_path, bms_features={**{k: origin_batch[k][i] for k in ("bms_identity", "bms_offset")}, "bms_defer": True}, no_indexing=True,')
        text = _replace(text, '                print(f"Attempt {attempt}: Break={total_breaks}, Clash={clash}")', '                print(f"Attempt {attempt}: Break={total_breaks}, Clash={clash}")\n        _bms.flush_partial(pdb_path)')
    elif (producer, relative_path) == ('ppiflow', 'analysis/utils.py'):
        text = _replace(text, '    binder=False,\n):', '    binder=False,\n    bms_features=None,\n):')
        text = _replace(text, '    if overwrite:', '    if bms_features is not None and (binder or prot_pos.ndim != 3):\n        raise ValueError("unsupported native export transform")\n    if overwrite:')
        text = _replace(text, '    return save_path', '    if bms_features is not None:\n        _bms.publish_partial(save_path, prot, bms_features, protein.PDB_CHAIN_IDS)\n    return save_path')
    elif (producer, relative_path) == ('fampnn', 'fampnn/data/protein.py'):
        text = _replace(text, '    insertion_code_offsets = []', '    insertion_code_offsets = []\n    bms_identity = []')
        text = _replace(text, '            residue_index.append(res.id[1] + insertion_code_offset)', '            bms_identity.append([ord(chain.id), int(res.id[1]), ord(res.id[2])])\n            residue_index.append(res.id[1] + insertion_code_offset)')
        text = _replace(text, '    return Protein(\n        atom_positions=np.array(atom_positions),', '    return _bms.identity_protein(Protein, bms_identity=np.array(bms_identity),\n        atom_positions=np.array(atom_positions),')
    elif (producer, relative_path) == ('fampnn', 'fampnn/inference/seq_design.py'):
        text = _replace(text, '        batch_list = []', '        batch_list = []\n        bms_inputs = []')
        text = _replace(text, '            single = process_single_pdb(data)', '            bms_inputs.append(_bms.fampnn_input(pdb_file, data))\n            single = process_single_pdb(data)')
        text = _replace(text, '        SeqDenoiser.save_samples_to_pdb(samples, pdbs)', '        _bms.save_fampnn(SeqDenoiser.save_samples_to_pdb, samples, pdbs, bms_inputs)')
    elif (producer, relative_path) == ('fampnn', 'fampnn/data/pdb_utils.py'):
        text = _replace(text, '        f.write(protein.to_pdb(prot, conect=conect))', '        f.write(protein.to_pdb(prot, conect=conect))\n    _bms.publish_fampnn(filename, prot, protein.PDB_CHAIN_IDS)')
    # Insert after future imports, without importing any physical engine here.
    import ast
    tree = ast.parse(text)
    insertion = 0
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            insertion = node.end_lineno
        elif isinstance(node, ast.ImportFrom) and node.module == '__future__':
            insertion = node.end_lineno
        else:
            break
    lines = text.splitlines(keepends=True)
    lines.insert(insertion, 'import maturation_native_adapter as _bms\n')
    return ''.join(lines)


def identity_protein(cls, *, bms_identity, **kwargs):
    """Add a numeric identity axis without changing any native model feature."""
    derived = dataclasses.make_dataclass('MaturationProtein', [('bms_identity', object, dataclasses.field(default=None))], bases=(cls,), frozen=cls.__dataclass_params__.frozen)
    return derived(bms_identity=bms_identity, **kwargs)


def _array(value):
    if hasattr(value, 'detach'):
        value = value.detach().cpu()
    return value.tolist()


def _identity(encoded):
    chain, number, insertion = map(int, encoded)
    return [chr(chain), number, chr(insertion).strip()]


def configure(reference, roles, domains):
    """Request-owned authority, supplied before invoking a native producer."""
    data = Path(reference).read_bytes()
    _AUTHORITY.set(dict(reference_pdb=data.decode('ascii'), reference_sha256=hashlib.sha256(data).hexdigest(), roles=roles, domains=domains))


def _publish(path, records, producer, authority):
    reference = authority['reference_pdb'].encode('ascii')
    if hashlib.sha256(reference).hexdigest() != authority['reference_sha256']:
        raise ValueError('changed reference authority')
    result = publish_native_export(reference, path, records=records, roles=authority['roles'], domains=authority['domains'], source_evidence={'producer': producer, 'source_files': SOURCE_SHA256.get(producer, {})})
    # Transport the original request separately from the candidate correspondence.
    result['transport_authority'] = authority
    Path(str(path) + '.comparison.json').write_text(json.dumps(result, sort_keys=True, allow_nan=False))
    return result


def publish_partial(path, prot, features, chain_ids):
    identities, offsets = _array(features['bms_identity']), _array(features['bms_offset'])
    if len(identities) != len(prot.residue_index) or len(offsets) != len(identities):
        raise ValueError('native identity axis mismatch')
    records = [dict(source=_identity(identities[i]), exported=[chain_ids[int(prot.chain_index[i])], int(prot.residue_index[i]), ''], offset=[float(v) for v in offsets[i]]) for i in range(len(identities))]
    if features.get('bms_defer'):
        pending = dict(_PENDING_PARTIAL.get() or {})
        pending[str(Path(path).resolve())] = (hashlib.sha256(Path(path).read_bytes()).hexdigest(), records, _AUTHORITY.get())
        _PENDING_PARTIAL.set(pending)
        return None
    return _publish(path, records, 'ppiflow', _AUTHORITY.get())


def flush_partial(path):
    """Restore only after native retry/clash checks have finished unchanged."""
    pending = dict(_PENDING_PARTIAL.get() or {})
    digest, records, authority = pending.pop(str(Path(path).resolve()))
    _PENDING_PARTIAL.set(pending)
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
        raise ValueError('native output changed after writer capture')
    return _publish(path, records, 'ppiflow', authority)


def read_transport(path):
    path = Path(path)
    request = json.loads(Path(str(path) + '.comparison.json').read_text())
    if request['candidate_sha256'] != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError('changed producer output')
    return request


def fampnn_input(path, data):
    request = read_transport(path)
    identities = _array(data['bms_identity'])
    chains, residues = _array(data['chain_index']), _array(data['residue_index'])
    mapping = {}
    owned = {tuple(r['source']) for r in request['native_export']['records']}
    for i, identity in enumerate(identities):
        key = (int(chains[i]), int(residues[i]))
        source = _identity(identity)
        if key in mapping or tuple(source) not in owned:
            raise ValueError('unowned/ambiguous native parser identity')
        mapping[key] = source
    return dict(mapping=mapping, authority=request['transport_authority'])


def save_fampnn(writer, samples, paths, inputs):
    if len(paths) != len(inputs) or len(set(map(str, paths))) != len(paths):
        raise ValueError('ambiguous native output ownership')
    token = _FAMPNN.set({str(Path(p).resolve()): inputs[i] for i, p in enumerate(paths)})
    try:
        return writer(samples, paths)
    finally:
        _FAMPNN.reset(token)


def publish_fampnn(path, prot, chain_ids):
    context = _FAMPNN.get()[str(Path(path).resolve())]
    records = []
    for i in range(len(prot.residue_index)):
        key = (int(prot.chain_index[i]), int(prot.residue_index[i]))
        # Padding has no emitted atoms and no biological identity.
        if key not in context['mapping']:
            if any(float(v) >= .5 for v in prot.atom_mask[i]):
                raise ValueError('unowned native export identity')
            continue
        records.append(dict(source=context['mapping'][key], exported=[chain_ids[key[0]], key[1], ''], offset=[0., 0., 0.]))
    return _publish(path, records, 'fampnn', context['authority'])


class _SourceLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, producer, root, sources):
        self.producer, self.root, self.sources = producer, Path(root), sources

    def find_spec(self, fullname, path=None, target=None):
        relative = fullname.replace('.', '/') + '.py'
        if relative in self.sources:
            return importlib.util.spec_from_file_location(fullname, self.root / relative, loader=self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        relative = module.__name__.replace('.', '/') + '.py'
        exec(compile(self.sources[relative], str(self.root / relative), 'exec'), module.__dict__)


def install(producer, root):
    """Preflight all sealed bytes; load instrumented copies in memory only."""
    sources = {p: instrument_source(producer, p, (Path(root) / p).read_bytes()) for p in SOURCE_SHA256[producer]}
    for relative in sources:
        if relative[:-3].replace('/', '.') in sys.modules:
            raise ValueError('native source imported before identity instrumentation')
    loader = _SourceLoader(producer, root, sources)
    sys.meta_path.insert(0, loader)
    return loader


def request_domains(selected_path, loops_path, roles=None):
    from score_maturation import parse_exact_position_spec
    domains = {'selected': [list(k) for k in sorted(parse_exact_position_spec(Path(selected_path).read_text()))]}
    loops = json.loads(Path(loops_path).read_text())
    if not isinstance(loops, dict):
        raise ValueError('invalid loop domain authority')
    for name, values in loops.items():
        if not isinstance(values, list):
            raise ValueError('invalid loop domain authority')
        tokens = []
        for value in values:
            token = str(value)
            if not token or not token[0].isalpha():
                binders = (roles or {}).get('binder', [])
                idx = 0 if name.startswith('H') else 1 if name.startswith('L') else -1
                if idx < 0 or idx >= len(binders):
                    raise ValueError('missing loop chain authority')
                token = binders[idx] + token
            tokens.append(token)
        domains[name] = [list(k) for k in sorted(parse_exact_position_spec(','.join(tokens)))]
    return domains


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--producer', choices=tuple(SOURCE_SHA256), required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--reference')
    parser.add_argument('--binder')
    parser.add_argument('--target')
    parser.add_argument('--selected')
    parser.add_argument('--loops')
    parser.add_argument('native_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    native_args = args.native_args
    if native_args and native_args[0] == '--':
        native_args = native_args[1:]
    entry = 'sample_antibody_nanobody_partial.py' if args.producer == 'ppiflow' else 'fampnn/inference/seq_design.py'
    if not native_args or Path(native_args[0]).resolve() != (Path(args.root)/entry).resolve():
        raise ValueError('unsupported native entrypoint identity')
    if args.producer == 'ppiflow':
        roles = {'binder': [c for c in (args.binder or '').split(',') if c], 'target': [c for c in (args.target or '').split(',') if c]}
        if not roles['binder'] or not roles['target'] or set(roles['binder']) & set(roles['target']):
            raise ValueError('explicit disjoint native roles required')
        configure(args.reference, roles, request_domains(args.selected, args.loops, roles))
    loader = install(args.producer, args.root)
    sys.path.insert(0, str(Path(args.root).resolve()))
    sys.argv = native_args
    exec(compile(loader.sources[entry], native_args[0], 'exec'), {'__name__': '__main__', '__file__': native_args[0]})


if __name__ == '__main__':
    # Hooks import this same module; do not create a second authority ContextVar.
    sys.modules['maturation_native_adapter'] = sys.modules[__name__]
    main()
