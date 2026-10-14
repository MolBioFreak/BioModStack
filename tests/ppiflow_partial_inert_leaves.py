"""Test-only inert scientific leaves; actual sampler/parser/writer and NF remain.

Loaded solely by test_ppiflow_protein_partial's private python launcher. Never
packaged into the scientific adapter or used for native acceptance.
"""
import argparse
import ast
import io
import json
import os
from pathlib import Path
import runpy
import sys
import time
import types

import numpy as np
import pandas as pd
import yaml
from Bio import PDB


def run_adapter(script, args, native_root):
    sys.path.insert(0, str(Path(script).parent))
    import run_ppiflow_protein_partial as partial
    from test_ppiflow_target_producer_mapping import native_io, tensor, definitions, runner
    ns = native_io.__wrapped__(Path(native_root))
    sys.modules['run_ppiflow_generation'] = runner
    real_prepare = partial.prepare
    partial.prepare = lambda settings, pdb, output: real_prepare(settings, pdb, output, native_root)

    def native_run(path, run_name=None):
        root = Path(native_root)
        source = ast.parse((root / 'models/flow_module_binder_partial.py').read_text())
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'FlowModule')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'test_step')
        code = ast.unparse(ast.Module(body=[method], type_ignores=[]))
        producer = {'Any': object, 'os': os, '_bms_partial': partial, 'au': types.SimpleNamespace(write_prot_to_pdb=ns['write_prot_to_pdb'])}
        exec(partial.instrument(code, 'installed-test-step'), producer)

        class InertExperiment:
            def __init__(self, cfg):
                self.cfg = cfg

            def test(self):
                # Inputs come from the native CLI/parser; no scientific model.
                cli = parser().parse_args(sys.argv[1:])
                structure = PDB.PDBParser(QUIET=True).get_structure('pair', cli.input_pdb)
                atoms, types_, numbers, chains = [], [], [], []
                for chain in structure[0]:
                    native = ns['process_chain'](chain, 0 if chain.id == 'A' else 1)
                    atoms.extend(native.atom_positions)
                    types_.extend(native.aatype)
                    numbers.extend(native.residue_index)
                    chains.extend(native.chain_index)
                count = len(numbers)
                coordinates = tensor(np.asarray(atoms))
                # Inert sampler returns input coordinates; never claim inference.
                class Interpolant:
                    def set_device(self, device): pass
                    def corrupt_batch(self, batch):
                        return dict(batch, so3_t=tensor([cli.start_t]), trans_t=None, rotmats_t=None)
                    def sample(self, *args, **kw):
                        return [tensor([coordinates])], {'pred_aatype': None}
                module = types.SimpleNamespace(
                    task='binder_motif_partial', interpolant=Interpolant(), model=None,
                    _interpolant_cfg=types.SimpleNamespace(starting_at_hotspot_center=False),
                    _model_cfg=types.SimpleNamespace(seq_decoder=types.SimpleNamespace(use=False)),
                    _data_cfg=types.SimpleNamespace(task='binder_motif_partial'),
                    _exp_cfg=types.SimpleNamespace(testing_model=types.SimpleNamespace(save_dir=cli.output_dir)))
                for sample in range(cli.samples_per_target):
                    batch = {'res_mask': tensor(np.ones((1, count))), 'diffuse_mask': tensor([[int(c == 1) for c in chains]]),
                             'sample_ids': tensor([sample]), 'pdb_name': [cli.name], 'aatype': tensor([types_]),
                             'chain_idx': tensor([chains]), 'original_res_idx': tensor([numbers]),
                             'trans_1': tensor(np.zeros((1, count, 3))), 'rotmats_1': tensor(np.tile(np.eye(3), (1, count, 1, 1))),
                             'hotspot_mask': tensor(np.zeros((1, count))), 'target_interface_mask': tensor(np.zeros((1, count))),
                             'binder_motif_mask': tensor(np.zeros((1, count)))}
                    from test_ppiflow_protein_partial import actual_dataset_row
                    features = {key: value[0] for key, value in batch.items() if key not in {'pdb_name', 'sample_ids'}}
                    fixed = {int(x[1:]) for x in (cli.fixed_positions or '').split(',') if x}
                    features['binder_motif_mask'] = tensor([int(c == 1 and n in fixed) for c,n in zip(chains,numbers)])
                    row = actual_dataset_row(root, features, sample, motif=bool(fixed))
                    batch = {key: [value] if isinstance(value, str) else tensor([value]) for key, value in row.items()}
                    producer['test_step'](module, batch, sample)
                    if os.environ.get('BMS_INERT_FAIL_AFTER_ONE') == '1':
                        raise RuntimeError('Explicit inert scientific failure after first native write')

        def process_file(row, write_dir):
            # Explicit inert preprocessing leaf; pipeline/CSV/config transport real.
            return {'pdb_name': row['PDBID'], 'processed_path': 'INERT_NO_PICKLE'}
        env = {'argparse': argparse, 'os': os, 'shutil': __import__('shutil'), 'pd': pd, 'time': time, 'yaml': yaml,
               'Dict': dict, 'Any': object, 'OmegaConf': types.SimpleNamespace(create=lambda x: x),
               'Experiment': InertExperiment, 'process_file': process_file,
               'get_residue_pairs_within_distance': lambda *a, **kw: ([], [1], [1])}
        definitions(Path(path), {'ConfigManager', 'get_parser', 'get_motif_residues', 'preprocess_csv_and_pkl', 'run_pipeline'}, env)
        parser = env['get_parser']
        parsed = parser().parse_args(sys.argv[1:])
        env['run_pipeline'](parsed)
    runpy.run_path = native_run
    sys.argv = [script, *args]
    partial.main()
