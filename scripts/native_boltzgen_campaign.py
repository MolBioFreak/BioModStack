#!/usr/bin/env python3
"""Placement-neutral BoltzGen campaign expansion and native artifact join."""
from __future__ import annotations
import argparse
import base64
import json
from pathlib import Path
import sys
import hashlib
import shutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'platform/api'))
from component_runtime import canonical_bytes, digest, durable_write, plan_boltzgen


def bundle_input(yaml_path: Path, output: Path):
    """Materialize only typed entity.path references, never rewrite arbitrary text.

    Called in PrepBoltzGenInput's copy-staged task. The native YAML bytes remain
    unchanged; relative paths retain names and must already be task-contained.
    External references must be delivered by the compiler before this step.
    """
    import yaml
    root = yaml_path.parent.resolve()
    output.mkdir()
    seen = set()
    def copy(path):
        path = Path(path)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('BoltzGen entity paths require task-contained relative input bindings')
        source = root/path
        if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError('BoltzGen referenced input is not a staged regular file')
        if path.as_posix() in seen:
            return
        seen.add(path.as_posix())
        destination = output/path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if path.suffix.lower() in {'.yaml', '.yml'}:
            doc = yaml.safe_load(source.read_bytes())
            if not isinstance(doc, dict):
                raise ValueError('BoltzGen YAML must be an object')
            # The current wrapper accepts entities with a typed file/protein/etc
            # path. Scalar or repeated scaffold path forms are both native.
            entities = doc.get('entities', [])
            # A generated scaffold spec is the native file entity payload itself.
            if 'path' in doc:
                refs = doc['path'] if isinstance(doc['path'], list) else [doc['path']]
                for ref in refs:
                    if not isinstance(ref, str):
                        raise ValueError('invalid BoltzGen scaffold path reference')
                    copy(Path(ref))
            for entity in entities:
                for kind in ('file', 'protein', 'ligand', 'dna', 'rna'):
                    value = entity.get(kind)
                    if isinstance(value, dict) and 'path' in value:
                        refs = value['path'] if isinstance(value['path'], list) else [value['path']]
                        for ref in refs:
                            if not isinstance(ref, str):
                                raise ValueError('invalid BoltzGen path reference')
                            copy(Path(ref))
    copy(Path(yaml_path.name))
    durable_write(output/'input_bindings.json', canonical_bytes({name: hashlib.sha256((output/name).read_bytes()).hexdigest() for name in sorted(seen)}))


def validate_input_bundle(root: Path):
    bindings = json.loads((root/'input_bindings.json').read_bytes())
    if not isinstance(bindings, dict) or 'boltzgen_input.yaml' not in bindings:
        raise ValueError('BoltzGen native root input binding is missing')
    files = list(root.rglob('*'))
    if any(path.is_symlink() for path in files):
        raise ValueError('BoltzGen native input closure contains symlinks')
    if {p.relative_to(root).as_posix() for p in files if p.is_file()} != set(bindings) | {'input_bindings.json'}:
        raise ValueError('BoltzGen input binding exact closure conflicts')
    for name, expected in bindings.items():
        relative = Path(name)
        path = root/relative
        if relative.is_absolute() or '..' in relative.parts or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('BoltzGen input binding changed before campaign expansion')
    return bindings


def collect(plan: dict, records: list[tuple[dict, Path]], output: Path):
    children = plan['children']
    expected = {row['index']: row for row in children}
    if len(expected) != len(children) or not expected:
        raise ValueError('campaign plan identity is invalid')
    observed = {}
    for meta, directory in records:
        index = meta.get('index')
        if index in observed or expected.get(index) != meta:
            raise ValueError('duplicate or foreign BoltzGen component')
        if not directory.is_dir():
            raise ValueError('required BoltzGen component output missing')
        observed[index] = directory
    if observed.keys() != expected.keys():
        raise ValueError('required BoltzGen exact join is incomplete')
    output.mkdir()
    native = output/'native'
    selected = output/'selected'
    selected.mkdir()
    artifacts, pdbs, terminal = [], [], []
    for index in sorted(expected):
        root = observed[index]
        execution = json.loads((root/'component_execution.json').read_bytes())
        if (set(execution) != {'stage', 'exit_code'} or execution['stage'] not in {'inference', 'filter'}
                or type(execution['exit_code']) is not int or execution['exit_code'] < 0):
            raise ValueError('invalid BoltzGen native execution receipt')
        completed = execution['stage'] == 'filter' and execution['exit_code'] == 0
        if execution['stage'] == 'inference' and execution['exit_code'] == 0:
            raise ValueError('required BoltzGen filtering has not completed')
        terminal.append({'index': index, 'status': 'completed' if completed else 'failed', **execution})
        # A zero-selected filter is legitimate scientific output, but absence of
        # the native filter report is not evidence of successful filtering.
        if completed and not (root/'filter_summary.json').is_file():
            raise ValueError('required native BoltzGen filter report missing')
        summary = json.loads((root/'filter_summary.json').read_bytes()) if completed else None
        if completed and not isinstance(summary, dict):
            raise ValueError('invalid native BoltzGen filter report')
        for path in sorted(root.rglob('*')):
            if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                raise ValueError('unsafe BoltzGen native artifact')
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            destination = native/f'job{index}'/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            artifacts.append({'component_index': index, 'relative_path': destination.relative_to(output).as_posix(),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size_bytes': path.stat().st_size})
            if completed and path.suffix.lower() == '.pdb' and len(relative.parts) == 1:
                name = f'job{index}_{path.name}'
                shutil.copyfile(path, selected/name)
                pdbs.append(name)
    receipt = {'schema_name': 'bms.boltzgen.native-campaign.v1', 'schema_version': 1,
        'status': 'complete', 'plan': plan, 'plan_sha256': digest(plan),
        'selected_pdbs': pdbs, 'artifacts': artifacts, 'children': terminal,
        'partial_failure_policy': 'at_least_one_completed_child', 'ingestion_triggered': False}
    if not any(row['status'] == 'completed' for row in terminal):
        receipt['status'] = 'failed'
    durable_write(output/'collection_manifest.json', canonical_bytes(receipt))
    if receipt['status'] == 'failed':
        raise ValueError('all BoltzGen children failed; no usable completed outputs')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    p = sub.add_parser('bundle')
    p.add_argument('--yaml', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('plan')
    p.add_argument('--total', type=int, required=True)
    p.add_argument('--size', type=int, required=True)
    p.add_argument('--settings-base64', required=True)
    p.add_argument('--parent', required=True)
    p.add_argument('--input-bundle', type=Path, required=True)
    p = sub.add_parser('collect')
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--records-base64', required=True)
    args = parser.parse_args()
    if args.operation == 'bundle':
        bundle_input(args.yaml, args.output)
    elif args.operation == 'plan':
        settings = json.loads(base64.b64decode(args.settings_base64, validate=True))
        children = plan_boltzgen(args.total, args.size, settings)
        bindings = validate_input_bundle(args.input_bundle)
        durable_write(Path('campaign_plan.json'), canonical_bytes({'schema_name': 'bms.boltzgen.campaign-plan.v1',
            'parent_job_id': args.parent, 'input_bindings_sha256': digest(bindings),
            'total_designs': args.total, 'designs_per_job': args.size, 'children': children}))
    else:
        records = json.loads(base64.b64decode(args.records_base64, validate=True))
        collect(json.loads(args.plan.read_bytes()), [(meta, Path(path)) for meta, path in records], Path('campaign'))

if __name__ == '__main__':
    main()
