#!/usr/bin/env python3
"""Pinned protein partial sampler adapter; no inference imports until main.

The selected pair is a representable derivative with explicit author mapping.
The native hotspot preprocessing bug is deliberately NOT repaired here pending
approval. Writer observation never changes native tensors, masks or PDB bytes.
"""
import argparse
import ast
import hashlib
import importlib.abc
import importlib.machinery
import json
from pathlib import Path
import re
import runpy
import sys

_CONTEXT = {}


def scalar(value):
    return value.tolist() if hasattr(value, 'tolist') else value


def prepare_pair(source, destination, binder, target, model=1):
    if len(binder) != 1 or len(target) != 1 or binder == target:
        raise ValueError('Native protein partial flow requires one distinct binder and target chain')
    raw = Path(source).read_bytes()
    lines, mapping, lookup = [], [], {}
    current = 1
    counters = {binder: 0, target: 0}
    for line in raw.decode().splitlines():
        if line.startswith('MODEL '):
            current = int(line[10:14])
        if not line.startswith(('ATOM  ', 'HETATM')) or current != model or line[21:22] not in counters:
            continue
        key = (line[21], int(line[22:26]), line[26].strip())
        if key not in lookup:
            chain = 'A' if key[0] == target else 'B'
            counters[key[0]] += 1
            lookup[key] = (chain, counters[key[0]])
            mapping.append({'source': {'chain_id': key[0], 'auth_seq_id': key[1], 'insertion_code': key[2], 'model': model},
                            'derivative': {'chain_id': chain, 'auth_seq_id': counters[key[0]], 'insertion_code': ''},
                            'role': 'target' if key[0] == target else 'binder'})
        chain, number = lookup[key]
        lines.append(line[:21] + chain + f'{number:4d} ' + line[27:])
    if not all(counters.values()):
        raise ValueError('Selected pair/model absent from source')
    Path(destination).write_text('\n'.join(lines) + '\nEND\n')
    return {'source_sha256': hashlib.sha256(raw).hexdigest(), 'model': model,
            'derivative_sha256': hashlib.sha256(Path(destination).read_bytes()).hexdigest(), 'residues': mapping}


def map_mask(spec, correspondence, role):
    """Exact author tokens A12B or A:12B and inclusive author-number ranges."""
    selected = []
    for token in (spec or '').split(','):
        if not token.strip():
            continue
        match = re.fullmatch(r'([A-Za-z0-9]):?(-?\d+)([A-Za-z]?)(?:-(-?\d+))?', token.strip())
        if not match:
            raise ValueError(f'Invalid author residue: {token!r}')
        chain, start, insertion, end = match.groups()
        numbers = range(int(start), int(end) + 1) if end else [int(start)]
        for number in numbers:
            rows = [r for r in correspondence['residues'] if r['role'] == role and
                    r['source']['chain_id'] == chain and r['source']['auth_seq_id'] == number and
                    r['source']['insertion_code'] == insertion]
            if len(rows) != 1:
                raise ValueError(f'Author residue is not in selected {role}: {token}')
            r = rows[0]['derivative']
            selected.append(f"{r['chain_id']}{r['auth_seq_id']}")
    return ','.join(dict.fromkeys(selected))


def write_sample(writer, batch, index, *args, **kwargs):
    # Reuse the existing actual atom-emission observer (including native swaps).
    import run_ppiflow_generation as observation
    previous = observation._ACTIVE_WRITE
    observed = {'residues': {}}
    observation._ACTIVE_WRITE = observed
    try:
        path = writer(*args, **kwargs)
    finally:
        observation._ACTIVE_WRITE = previous
    path = Path(path)
    identity = json.loads(batch['bms_partial_residues'][index])
    chains = identity['chains']
    numbers = identity['numbers']
    diffuse = scalar(batch['diffuse_mask'][index])
    lookup = {(r['derivative']['chain_id'], r['derivative']['auth_seq_id']): r
              for r in _CONTEXT['correspondence']['residues']}
    joins = []
    for i, output in observed['residues'].items():
        key = ('A' if int(chains[i]) == 0 else 'B', int(numbers[i]))
        if key in lookup:
            joins.append({**lookup[key], 'output': output, 'diffuse_mask': int(diffuse[i])})
    sample = int(scalar(batch['sample_ids'][index]))
    meta = {'producer': 'ppiflow', 'ppiflow_task': 'protein_binder_partial',
            'native_input_id': str(batch['pdb_name'][index]), 'sample_index': sample,
            'native_output_id': f"{batch['pdb_name'][index]}:sample:{sample}",
            'artifact_class': 'backbone_complex', 'sequence_semantics': 'movable_positions_masked_alanine',
            'b_factor_semantics': 'native_hotspot_interface_motif_masks_not_confidence',
            'pdb_path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'bytes': path.stat().st_size, 'source_sha256': _CONTEXT['correspondence']['source_sha256'],
            'residue_mapping': joins, 'effective_settings': _CONTEXT['settings'],
            'binder_chains': list(dict.fromkeys(r['output']['chain_id'] for r in joins if r['role'] == 'binder')),
            'target_chains': list(dict.fromkeys(r['output']['chain_id'] for r in joins if r['role'] == 'target'))}
    meta['source_id'] = _CONTEXT.get('prefix', 'selected_pair')
    meta['native_output_id'] = f"{meta['source_id']}:sample:{sample}"
    Path(str(path) + '.sample.json').write_text(json.dumps(meta, indent=2))
    if _CONTEXT.get('publication_dir'):
        # Publish each closed native writer output through the existing owner,
        # including retained samples before a later scientific leaf fails.
        import shutil
        from publish_binder_refinement import publish
        name = f"{meta['source_id']}_ppiflow_sample{sample}.pdb"
        staged = path.parent / name
        shutil.copy2(path, staged)
        source_meta = _CONTEXT.get('source_meta', {})
        publish(staged, {**source_meta, **meta, 'id': staged.stem,
                        'parent_id': source_meta.get('id'), 'terminal_producer': 'ppiflow',
                        'validation_status': 'unvalidated'}, _CONTEXT['publication_dir'])
    return str(path)


def capture_dataset(values):
    """Keep native target-then-binder indices before Dataset drops them."""
    target, binder = values['target_feats'], values['binder_feats']
    values['output_feats']['bms_partial_residues'] = json.dumps({
        'chains': list(scalar(target['chain_idx'])) + list(scalar(binder['chain_idx'])),
        'numbers': list(scalar(target['original_res_idx'])) + list(scalar(binder['original_res_idx']))})


def instrument(source, filename, dataset=False):
    class Observe(ast.NodeTransformer):
        active = False

        def visit_ClassDef(self, node):
            previous = self.active
            self.active = node.name == 'PpiTestPartialDataset'
            self.generic_visit(node)
            self.active = previous
            return node

        def visit_Return(self, node):
            if dataset and self.active and isinstance(node.value, ast.Name) and node.value.id == 'output_feats':
                return [*ast.parse('_bms_partial.capture_dataset(locals())').body, node]
            return node

        def visit_Call(self, node):
            self.generic_visit(node)
            if ast.unparse(node.func) == 'au.write_prot_to_pdb':
                node.args = [node.func, ast.Name(id='batch', ctx=ast.Load()), ast.Name(id='i', ctx=ast.Load()), *node.args]
                node.func = ast.parse('_bms_partial.write_sample', mode='eval').body
            return node
    return compile(ast.fix_missing_locations(Observe().visit(ast.parse(source, filename))), filename, 'exec')


class Loader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self):
        self.files = {}

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in {'models.flow_module_binder_partial', 'data.datasets_binder_partial'}:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec:
            self.files[fullname] = spec.origin
            spec.loader = self
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        filename = self.files[module.__name__]
        module.__dict__['_bms_partial'] = sys.modules[__name__]
        exec(instrument(Path(filename).read_text(), filename,
                        dataset=module.__name__ == 'data.datasets_binder_partial'), module.__dict__)


def prepare(settings, pdb, output, native_root='/app/ppiflow'):
    import yaml
    root, output = Path(native_root), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    pair = output / 'selected_pair.pdb'
    mapping = prepare_pair(pdb, pair, settings['binder_chains'], settings['target_chains'], settings.get('ppiflow_input_model', 1))
    config = yaml.safe_load((root / 'configs/inference_binder_partial.yaml').read_text())
    contract = yaml.safe_load((Path(__file__).resolve().parents[1] / 'platform/api/config/models/binder_refinement.yaml').read_text())
    settings = dict(settings)
    for field in contract['params']:
        if field.get('protein_native_cli') or field.get('protein_native_path'):
            default = field.get('protein_default', field.get('default'))
            if default is not None:
                settings.setdefault(field['name'], default)
    settings['ppiflow_task'] = 'protein_binder_partial'
    settings.setdefault('ppiflow_checkpoint', 'binder')
    argv = [str(root / 'sample_binder_partial.py'), '--input_pdb', str(pair), '--target_chain', 'A', '--binder_chain', 'B',
            '--output_dir', str(output / 'native'), '--name', 'selected_pair',
            '--model_weights', settings.get('ppiflow_checkpoint_path') or '/opt/ppiflow/ckpt/binder.ckpt']
    for field in contract['params']:
        if not field.get('protein_native_cli') and not field.get('protein_native_path'):
            continue
        value = settings.get(field['name'], field.get('protein_default', field.get('default')))
        if value is None:
            continue
        if field['name'] == 'ppiflow_fixed_positions':
            value = map_mask(value, mapping, 'binder')
        elif field['name'] == 'epitope_residues':
            value = map_mask(value, mapping, 'target')
        if field.get('protein_native_cli') and value != '':
            argv.extend(['--' + field['protein_native_cli'], str(value)])
        if field.get('protein_native_path'):
            *parents, leaf = field['protein_native_path'].split('.')
            node = config
            for part in parents:
                node = node[part]
            node[leaf] = value
    compiled = output / 'native_config.yaml'
    compiled.write_text(yaml.safe_dump(config, sort_keys=False))
    argv.extend(['--config', str(compiled)])
    receipt = {'settings': settings, 'correspondence': mapping, 'argv': argv,
               'native_source_revision': '000ce45a4411e7b97c1523a22c0f1bece7ede5fc',
               'inference_qualification': 'not_established_by_adapter_tests',
               'explicit_hotspot_chain2_correction': 'not_applied_pending_approval'}
    (output / 'request_receipt.json').write_text(json.dumps(receipt, indent=2))
    return receipt


def collect(output, destination, prefix, manifest):
    import shutil
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for sidecar in sorted(Path(output).rglob('*.sample.json')):
        meta = json.loads(sidecar.read_text())
        source = sidecar.with_name(sidecar.name.removesuffix('.sample.json'))
        name = f"{prefix}_ppiflow_sample{meta['sample_index']}.pdb"
        dest = destination / name
        shutil.copy2(source, dest)
        records.append({**meta, 'name': name, 'path': str(dest.resolve())})
    Path(manifest).write_text(json.dumps(records, indent=2))
    return records


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pdb', required=True)
    p.add_argument('--settings', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--prefix', required=True)
    p.add_argument('--anchors')
    p.add_argument('--publication-dir')
    p.add_argument('--source-meta')
    args = p.parse_args()
    settings = json.loads(Path(args.settings).read_text())
    if args.anchors:
        anchors = json.loads(Path(args.anchors).read_text()).get('anchors', [])
        fixed = [settings.get('ppiflow_fixed_positions') or '']
        fixed.extend(str(r['pdb_position']) for r in anchors)
        settings['ppiflow_fixed_positions'] = ','.join(x for x in fixed if x)
    receipt = prepare(settings, args.pdb, args.output)
    _CONTEXT.update(receipt, prefix=args.prefix, publication_dir=args.publication_dir,
                    source_meta=json.loads(Path(args.source_meta).read_text()) if args.source_meta else {})
    import run_ppiflow_generation as observation
    # Only the existing writer observer is used; no initial-generation repair.
    observer = observation.NativeLoader(observation)
    original_find = observer.find_spec
    observer.find_spec = lambda name, path=None, target=None: original_find(name, path, target) if name in {'analysis.utils', 'data.protein'} else None
    sys.meta_path[:0] = [Loader(), observer]
    sys.path.insert(0, '/app/ppiflow')
    sys.argv = receipt['argv']
    try:
        runpy.run_path(sys.argv[0], run_name='__main__')
    finally:
        records = collect(args.output, 'ppiflow_backbones', args.prefix, 'ppiflow_backbones_manifest.json')
        Path(f'{args.prefix}_ppiflow_accounting.json').write_text(json.dumps({
            'requested_count': settings.get('ppiflow_samples_per_target', 100),
            'emitted_count': len(records), 'emitted_sample_indices': [r['sample_index'] for r in records]}, indent=2))


if __name__ == '__main__':
    main()
