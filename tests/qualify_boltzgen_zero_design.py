"""Run inside the pinned BoltzGen image; CPU control flow, NEVER inference.

Executes complete installed method ASTs without heavyweight module imports or
constructors. Synthetic feature tensors are branch fixtures, not model outputs.
Pipeline construction stubs device discovery and artifact path resolution only.
"""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch
import torch.nn.functional as F
from boltzgen.data import const
from boltzgen.cli import boltzgen as cli

ROOT = Path(cli.__file__).resolve().parents[1]


def installed_method(relative, name):
    path = ROOT / relative
    tree = ast.parse(path.read_text())
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name
                 and (name != 'sample' or 'feats' in [arg.arg for arg in node.args.args])]
    assert len(functions) == 1
    function = functions[0]
    namespace = {'torch': torch, 'F': F, 'const': const}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name], {'path': str(path), 'line': function.lineno,
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    args = cli.build_parser().parse_args(['run', 'synthetic-zero-design.yaml', '--output', '/unused',
                                         '--protocol', 'protein-small_molecule', '--num_designs', '1',
                                         '--devices', '1', '--use_kernels', 'false'])
    with patch.object(torch.cuda, 'get_device_capability', return_value=(0, 0)), \
         patch.object(cli, 'get_artifact_path', side_effect=lambda *a, **k: Path('/not-loaded.ckpt')):
        pipeline = cli.BinderDesignPipeline(args, Path('/not-loaded-molecules'))
    steps = [step.name for step in pipeline.steps]
    assert all(step in steps for step in ['design', 'inverse_folding', 'folding', 'design_folding', 'analysis', 'filtering']), steps

    sample, inverse_source = installed_method('model/modules/inverse_fold.py', 'sample')
    sequence = F.one_hot(torch.tensor([[const.token_ids['ALA'], const.token_ids['GLY']]]), const.num_tokens)
    features = {'design_mask': torch.zeros((1, 2), dtype=torch.bool),
                'res_type_clone': sequence, 'coords': torch.zeros((1, 8, 3))}
    # No neural layers exist on this self: touching a predictor would fail.
    decoder = SimpleNamespace(inverse_fold_restriction=[], num_res_type=const.num_tokens)
    result = sample(decoder, torch.zeros((2, 1)), torch.zeros((0, 1)),
                    torch.empty((2, 0), dtype=torch.long), torch.ones((1, 2), dtype=torch.bool), features)
    assert torch.equal(result['res_type'].long(), sequence)

    metrics, analysis_source = installed_method('task/analyze/analyze.py', 'compute_metrics')
    feat = {'path': 'synthetic-zero-design.cif', 'exception': False,
            'res_type': sequence[0], 'design_mask': torch.zeros(2, dtype=torch.bool),
            'token_pad_mask': torch.ones(2, dtype=torch.bool), 'asym_id': torch.zeros(2, dtype=torch.long)}
    analyzer = SimpleNamespace(data=SimpleNamespace(predict_set=SimpleNamespace(get_sample=lambda **kwargs: feat)))
    try:
        metrics(analyzer, sample_id='synthetic', design_dir=Path('/unused'))
    except IndexError as exc:
        failure = str(exc)
    else:
        raise AssertionError('Expected installed analysis to index an empty design selection')
    print(json.dumps({'protocols': sorted(cli.protocol_configs), 'steps': steps,
                      'inverse_fold_zero_design': 'unchanged sequence; decoder loop empty',
                      'analysis_zero_design': {'error': 'IndexError', 'message': failure},
                      'sources': [inverse_source, analysis_source],
                      'scope': 'CPU branch fixtures; no weights, inference, GPU or successful full pipeline claimed'}, indent=2))


if __name__ == '__main__':
    main()
