"""Installed Caliby/AF2 CPU contracts; inert sentinels, no weights/inference.

Run using the image interpreter with this repository bound at /repo:
  apptainer exec --cleanenv --bind "$PWD:/repo:ro" --bind "$SCRATCH:/scratch" \
    --env CUDA_VISIBLE_DEVICES=,JAX_PLATFORMS=cpu,TMPDIR=/scratch,MPLCONFIGDIR=/scratch/mpl \
    candidate.sif /opt/caliby/.venv/bin/python /repo/tests/native_caliby_af2.py

Only weight acquisition, model construction, GPU cleanup, and inference boundaries
are intercepted. Imports, native config merge, dispatch and PDB serialization run.
"""
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from omegaconf import OmegaConf
import caliby.api as api
import caliby.weights as weights
import caliby.eval.eval_utils.folding_utils as folding
import caliby.eval.eval_utils.eval_metrics as metrics
import colabdesign
import colabdesign.af
import colabdesign.shared.utils

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from caliby_runtime import maybe_run_self_consistency

class CPUContracts(unittest.TestCase):
    def test_installed_imports_and_identity(self):
        folding._check_af2_installed()
        for mod in (api, weights, folding, metrics):
            self.assertTrue(mod.__file__.startswith('/opt/caliby/'), mod.__file__)
            print('INSTALLED', mod.__file__, hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest())
        print('AF2_IMPORT', colabdesign.__file__)
        self.assertEqual(metadata.version('dm-haiku'), '0.0.13')
        self.assertEqual(metadata.version('jax'), '0.4.34')
        self.assertEqual(metadata.version('jaxlib'), '0.4.34')
        self.assertEqual(metadata.version('torch'), '2.8.0+cu128')
        direct = json.loads(metadata.distribution('colabdesign').read_text('direct_url.json'))
        self.assertEqual(direct['vcs_info']['commit_id'], 'd024c4e846fea83c090afcbe89a313eeee8ec01e')

    def exercise(self, overrides, expected):
        holder = SimpleNamespace(device='cpu')
        holder.self_consistency_eval = lambda *a, **kw: api.CalibyModel.self_consistency_eval(holder, *a, **kw)
        returned = {'candidate_0': {'avg_ca_plddt': 0.0, 'sc_ca_rmsd': 1.25, 'tmalign_score': .75}}
        with patch.object(weights, 'ensure_af2_params', return_value='/sentinel/no-weights') as ensure, \
             patch.object(folding, 'clear_mem_torch'), \
             patch.object(colabdesign.shared.utils, 'clear_mem'), \
             patch.object(colabdesign.af, 'mk_af_model', return_value=object()) as constructor, \
             patch.object(metrics, 'run_self_consistency_eval', return_value=returned) as evaluate:
            if overrides is None:
                result = holder.self_consistency_eval(['candidate_0.pdb'], out_dir='/scratch/unused')
            else:
                result = maybe_run_self_consistency(model=holder, designed_paths=['candidate_0.pdb'],
                    output_dir=Path('/scratch/unused'), enabled=True, **overrides)
            self.assertIs(result, returned)
            ensure.assert_called_once_with(None)
            constructor.assert_called_once_with(data_dir='/sentinel/no-weights', use_multimer=expected['use_multimer'])
            args, kw = evaluate.call_args
            self.assertEqual(args[0], ['candidate_0.pdb'])
            self.assertEqual(kw, {'out_dir': '/scratch/unused'})
            cfg = OmegaConf.to_container(args[1]['cfg'], resolve=True)
            self.assertEqual(args[1]['device'], 'cpu')
            for key, value in expected.items():
                self.assertEqual(cfg['af2'][key], value)
            print('NATIVE_MERGED_AF2_CONFIG', json.dumps(cfg, sort_keys=True))

    def test_native_defaults(self):
        self.exercise(None, dict(num_models=5, num_recycles=3, sample_models=True, use_multimer=False, save_best=True))

    def test_wrapper_explicit_overrides_and_zero(self):
        self.exercise(dict(num_models=2, num_recycles=0, use_multimer=True),
            dict(num_models=2, num_recycles=0, sample_models=True, use_multimer=True, save_best=True))

    def test_wrapper_disabled_never_calls_native(self):
        with patch.object(api.CalibyModel, 'self_consistency_eval', side_effect=AssertionError('disabled')):
            result = maybe_run_self_consistency(model=SimpleNamespace(), designed_paths=['x.pdb'],
                output_dir=Path('/scratch/unused'), enabled=False, num_models=5, num_recycles=3, use_multimer=False)
            self.assertEqual(result, {})

    def test_native_af2_dispatch_and_writer(self):
        from colabdesign.af.alphafold.common import protein
        aux = {
            'plddt': np.array([[.1, .2], [.8, .9]]),
            'aatype': np.zeros((2, 2), dtype=int),
            'residue_index': np.tile(np.arange(2), (2, 1)),
            'atom_positions': np.arange(2*2*37*3, dtype=float).reshape(2,2,37,3)/10,
            'atom_mask': np.ones((2,2,37)),
        }
        from unittest.mock import Mock
        sentinel = Mock()
        sentinel._tmp = {'best': {'aux': {'all': aux}}}
        cfg = OmegaConf.create({'af2': dict(num_models=2, sample_models=False, num_recycles=0)})
        with tempfile.TemporaryDirectory() as d:
            outputs = folding.run_af2(['candidate_0.pdb'], {'af_model': sentinel, 'cfg': cfg}, d)
            self.assertEqual(outputs, [str(Path(d)/'af2_candidate_0.pdb')])
            sentinel.prep_inputs.assert_called_once_with('candidate_0.pdb')
            sentinel.restart.assert_called_once_with(mode='wt')
            sentinel.predict.assert_called_once_with(num_models=2, sample_models=False, num_recycles=0, verbose=False)
            sentinel._save_results.assert_called_once_with(save_best=True, best_metric='plddt', verbose=False)
            text = Path(outputs[0]).read_text()
            parsed = protein.from_pdb_string(text)
            np.testing.assert_allclose(parsed.b_factors[parsed.atom_mask.astype(bool)],
                (100 * aux['plddt'][1][:,None] * np.ones((2,37)))[parsed.atom_mask.astype(bool)])
            self.assertTrue(text.endswith('END\n'))
            print('NATIVE_AF2_DISPATCH_AND_BEST_PLDDT_WRITER_PASS', hashlib.sha256(text.encode()).hexdigest())

if __name__ == '__main__':
    unittest.main(verbosity=2)
