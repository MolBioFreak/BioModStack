"""Installed CPU CSV/parser qualification; no model import or inference.

Run with the installed FAMPNN image's Python and BMS_G09_FAMPNN_SOURCE.
AST bodies and the positional CSV reader are executed unchanged.
"""
import ast
import __future__
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prep_fampnn_constraints_from_spec import write_constraints


class NativeLocalMasks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pandas as pd
        import torch
        cls.pd, cls.torch = pd, torch
        native = Path(os.environ.get('BMS_G09_FAMPNN_SOURCE', '/app/fampnn'))
        tree = ast.parse((native / 'fampnn/sampling_utils.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and
                 n.name in {'parse_fixed_positions', 'get_override_masks'}]
        assert len(nodes) == 2
        cls.scope = dict(torch=torch, pd=pd, re=re)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<installed-mask-functions>', 'exec',
                     flags=__future__.annotations.compiler_flag), cls.scope)
        tree = ast.parse((native / 'fampnn/inference/seq_design.py').read_text())
        cls.reader = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and
                          ast.unparse(n.test) == 'cfg.fixed_pos_csv is not None')

    def native_masks(self, csv_path, names, numbers):
        scope = dict(cfg=SimpleNamespace(fixed_pos_csv=str(csv_path)), pd=self.pd)
        exec(compile(ast.Module(body=[self.reader], type_ignores=[]), '<installed-csv-reader>', 'exec'), scope)
        df = scope['fixed_pos_df']
        self.assertEqual(df.index.tolist(), names)  # no unused header/custody columns
        return self.scope['get_override_masks'](
            dict(residue_index=self.torch.tensor(numbers), chain_index=self.torch.tensor([[0, 0, 1]] * len(names))),
            names, [{'A': 0, 'B': 1}] * len(names), df, 'seq_design')

    def test_actual_native_keep_change_neither_and_independent_sidechains(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, numbers in [('first', [10, 12, 21]), ('second', [110, 112, 221])]:
                source = [f'A:{numbers[0]}:', f'A:{numbers[1]}:', f'B:{numbers[2]}:']
                # Explicit preparation-object mapping, never first-input reuse.
                prepared = [f'A:{numbers[0]+1}:', f'A:{numbers[1]+1}:', f'B:{numbers[2]+1}:']
                (work / (name + '.pdb')).write_text(''.join(
                    f'ATOM  {i:5d}  CA  ALA {c}{int(n):4d}    {float(i):8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00           C\n'
                    for i, (c, n, _) in enumerate((v.split(':') for v in prepared), 1)))
                (work / (name + '.fampnn_prep.json')).write_text(json.dumps(dict(
                    source_domain=source, pairs=list(zip(source, prepared)))))
            for selection in ('keep', 'change', 'neither'):
                for fix_sidechains in (False, True):
                    with self.subTest(selection=selection, fix_sidechains=fix_sidechains):
                        csv_path = work / 'fixed.csv'
                        write_constraints(work, csv_path, design_chains='A',
                            fixed_positions='A10,A110' if selection == 'keep' else '',
                            designable_positions='A12,A112' if selection == 'change' else '',
                            fix_sidechains=fix_sidechains)
                        seq, sc = self.native_masks(csv_path, ['first', 'second'], [[11,13,22], [111,113,222]])
                        expected = [[1,0,1]]*2 if selection != 'neither' else [[0,0,1]]*2
                        self.assertEqual(seq.tolist(), expected)
                        self.assertEqual(sc.tolist(), expected if fix_sidechains else [[0,0,0]]*2)

    def test_old_manifest_and_absent_selector(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'old.pdb').write_text(''.join(
                f'ATOM  {i:5d}  CA  ALA {c}{n:4d}    {float(i):8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00           C\n'
                for i, (c,n) in enumerate([('A',10),('A',12),('B',21)], 1)))
            csv_path = work / 'fixed.csv'
            write_constraints(work, csv_path, manifest={'sequence_fixed_positions_spec':'A10,B21'}, fix_sidechains=True)
            masks = self.native_masks(csv_path, ['old'], [[10,12,21]])
            self.assertEqual([m.tolist() for m in masks], [[[1,0,1]], [[1,0,1]]])
            write_constraints(work, csv_path, design_chains='A', designable_positions='A999')
            masks = self.native_masks(csv_path, ['old'], [[10,12,21]])
            self.assertEqual([m.tolist() for m in masks], [[[1,1,1]], [[0,0,0]]])

    def test_installed_insertion_selector_limitation_is_not_silently_renumbered(self):
        with self.assertRaisesRegex(ValueError, 'Invalid position format'):
            self.scope['parse_fixed_positions']('A10A', {'A':0}, self.torch.tensor([10,11]), self.torch.tensor([0,0]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
