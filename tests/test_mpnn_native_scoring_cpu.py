"""Native CPU helper differential; no trained model, checkpoint, or GPU."""
import json
import os
from pathlib import Path
import subprocess
import sys


def native_probe(scratch):
    import random
    import numpy as np
    import torch
    sys.path.insert(0, '/dl_binder_design/mpnn_fr')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import util_protein_mpnn as util
    from proteinmpnn_native_binding import install_native_scoring

    pdb = scratch/'fixture.pdb'
    lines = []
    for number in range(1, 4):
        for atom in ['N','CA','C','O']:
            lines.append(f'ATOM  {len(lines)+1:5d} {atom:^4} ALA A{number:4d}    {number:8.3f}{0:8.3f}{0:8.3f}{1:6.2f}{20:6.2f}           C\n')
    pdb.write_text(''.join(lines))
    features = util.generate_seqopt_features(str(pdb), ['A'])
    original = util.generate_sequences

    class InertModel:
        def __init__(self): self.calls=[]; self.expected=[]
        def __call__(self, X, S, mask, chain, idx, enc, randn, **kw):
            # Simulate an RNG-consuming forward without learned parameters.
            torch.randn_like(X)
            self.calls.append((S.clone(), kw))
            logits = torch.zeros((*S.shape,21))
            logits.scatter_(-1, S[...,None], 3.)
            return torch.log_softmax(logits, -1)
        def sample(self, X, randn, S, chain, enc, idx, **kw):
            draw = torch.randint(1,20,S.shape)
            sample = torch.where(chain*kw['chain_M_pos']*kw['mask'] > 0, draw, S)
            order = torch.argsort(randn,dim=-1)
            self.expected.append((sample.clone(), order.clone()))
            return {'S':sample,'decoding_order':order}

    def run(fn):
        random.seed(73); np.random.seed(73); torch.manual_seed(73)
        model = InertModel()
        output = fn(model, 'cpu', features, util.set_default_args(2, []), ['A'], [], np.zeros(21), {features['name']:{'A':[2]}})
        return model, output, torch.get_rng_state()

    before, old_output, old_rng = run(original)
    install_native_scoring(util)
    after, output, rng = run(util.generate_sequences)
    assert [row[0] for row in output] == [row[0] for row in old_output]
    assert torch.equal(old_rng, rng)
    assert len(after.calls) == len(before.calls) == 3
    assert torch.equal(after.calls[0][0], before.calls[0][0])
    for (sample, order), (scored, kwargs), (_, score) in zip(after.expected, after.calls[1:], output):
        assert torch.equal(sample, scored)
        assert kwargs['use_input_decoding_order'] is True
        assert torch.equal(kwargs['decoding_order'], order)
        # Independent native standard CLI contract: sampled identities in both
        # the model conditioning call and native NLL reduction, same order.
        expected_nll = -torch.log_softmax(torch.tensor([3.] + [0.]*20), -1)[0].item()
        assert abs(score-expected_nll) < 1e-6
    assert [x[1] for x in output] != [x[1] for x in old_output]
    report = {'native_forward_count':len(after.calls), 'sample_count':len(output),
              'sample_sequences_equal':True, 'torch_rng_state_equal':True,
              'native_sample_order_used':True,
              'original_scores':[float(x[1]) for x in old_output],
              'standard_native_scores':[float(x[1]) for x in output]}
    (scratch/'scoring-result.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


def test_actual_native_score_contract_and_rng(tmp_path):
    import pytest
    image = os.environ.get('BMS_TEST_PROTEINMPNN_SIF')
    if not image: pytest.skip('set BMS_TEST_PROTEINMPNN_SIF for native CPU helper')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['apptainer','exec','--cleanenv','--no-home',
        '--bind',f'{root}:{root}:ro','--bind',f'{tmp_path}:{tmp_path}',image,
        '/opt/conda/envs/mpnn/bin/python',str(Path(__file__).resolve()),'--native-test',str(tmp_path)],
        cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout+result.stderr
    print(result.stdout)


if __name__ == '__main__' and sys.argv[1] == '--native-test':
    native_probe(Path(sys.argv[2]))
