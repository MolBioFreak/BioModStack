#!/usr/bin/env python3
"""Export-only PPIFlow sample identity at the native test_step producer.

The installed producer writes pdb_path for batch_idx after its retry loop.
Capture those exact variables, not a directory ordering. This wrapper changes
neither model settings, tensors, retry decisions nor native output bytes.
"""
import ast
import importlib.abc
import importlib.machinery
import json
from pathlib import Path
import runpy
import sys


def publish_sample(path, sample_index):
    path = Path(path)
    if path.is_file():
        Path(str(path) + '.sample.json').write_text(json.dumps({
            'producer': 'ppiflow', 'sample_index': int(sample_index),
            'pdb_path': str(path.resolve()),
        }))


def instrument(source, filename):
    tree = ast.parse(source, filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'FlowModule':
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name == 'test_step':
                    method.body.extend(ast.parse(
                        "import ppiflow_sample_identity\n"
                        "ppiflow_sample_identity.publish_sample(pdb_path, batch_idx)\n"
                    ).body)
    return compile(ast.fix_missing_locations(tree), filename, 'exec')


class SampleLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self):
        self.filename = None

    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'models.flow_module_antibody_partial':
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None:
            self.filename = spec.origin
            spec.loader = self
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        exec(instrument(Path(self.filename).read_text(), self.filename), module.__dict__)


def collect(root, destination, prefix, manifest_path, comparison=False,
            requested_count=None, accounting_path=None):
    """Copy producer-associated samples; zero samples is a real empty result."""
    import shutil
    root, destination = Path(root), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for sidecar in sorted(root.rglob('*.sample.json')):
        sample = json.loads(sidecar.read_text())
        pdb = Path(sample['pdb_path'])
        # The sidecar is written alongside that exact producer output. Using its
        # sibling makes the association portable after task-directory relocation.
        pdb = sidecar.with_name(sidecar.name.removesuffix('.sample.json'))
        index = sample['sample_index']
        name = f'{prefix}_ppiflow_sample{index}.pdb'
        output = destination / name
        shutil.copy2(pdb, output)
        comparison_path = None
        if comparison:
            comparison_path = str(Path(str(output) + '.comparison.json').resolve())
            shutil.copy2(str(pdb) + '.comparison.json', comparison_path)
        records.append(dict(sample_index=index, name=name, path=str(output.resolve()),
                            comparison_path=comparison_path, producer=sample['producer']))
    Path(manifest_path).write_text(json.dumps(records, indent=2))
    if accounting_path is not None:
        # This is transport accounting, never a retention or success threshold.
        # Native partial preprocessing emits one row per requested sample and
        # test_step records its batch_idx. Unassociated PDBs are not candidates.
        emitted = {row['sample_index'] for row in records}
        missing = (sorted(set(range(int(requested_count))) - emitted)
                   if requested_count is not None else None)
        Path(accounting_path).write_text(json.dumps({
            'producer': 'ppiflow', 'source_id': prefix,
            'requested_count': requested_count, 'emitted_count': len(records),
            'emitted_sample_indices': sorted(emitted),
            'missing_count': len(missing) if missing is not None else None,
            'missing_sample_indices': missing,
        }, indent=2))
    return records


if __name__ == '__main__':
    # Strict correspondence instrumentation already loads its own native module;
    # its existing export callback also calls publish_sample directly.
    sys.meta_path.insert(0, SampleLoader())
    script, *args = sys.argv[1:]
    sys.argv = [script, *args]
    sys.path.insert(0, str(Path(script).resolve().parent))
    runpy.run_path(script, run_name='__main__')
