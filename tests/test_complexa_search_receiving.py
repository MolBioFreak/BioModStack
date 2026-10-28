"""Installed native search factories/replica accounting; synthetic generator, not inference."""
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.lib.complexa_contract import Request
from scripts.bms_complexa.config import compose_request

pytestmark = pytest.mark.skipif(not os.environ.get('COMPLEXA_TEST_SOURCE'), reason='selected installed Complexa required')


@pytest.mark.parametrize('algorithm,klass', [('single-pass', 'SinglePassGeneration'), ('best-of-n', 'BestOfNSearch'),
    ('beam-search', 'BeamSearch'), ('fk-steering', 'FKSteering'), ('mcts', 'MCTSSearch')])
def test_real_factory_receives_each_explicit_algorithm(tmp_path, algorithm, klass):
    from proteinfoundation.search.search_factory import instantiate_search
    request = Request(task='AME', task_name='factories', nsamples=2, min_length=12, max_length=12,
                      search={'algorithm': algorithm, 'step_checkpoints': [0, 200, 400]}, reward=None)
    cfg = compose_request(request, Path(os.environ['COMPLEXA_TEST_SOURCE']), tmp_path/'compose', tmp_path/'weights')
    result = instantiate_search(SimpleNamespace(), cfg.generation, algorithm)
    assert type(result).__name__ == klass
    assert list(result.inf_cfg.search.step_checkpoints) == [0, 200, 400]


def test_best_of_n_returns_all_replicas_with_native_chunk_order(tmp_path):
    import torch
    from proteinfoundation.search.search_factory import instantiate_search
    cfg = compose_request(Request(task='AME', task_name='replicas',
        search={'algorithm': 'best-of-n', 'best_of_n': {'replicas': 3}, 'max_batch_size': 2}, reward=None),
        Path(os.environ['COMPLEXA_TEST_SOURCE']), tmp_path/'compose', tmp_path/'weights')
    native = instantiate_search(SimpleNamespace(), cfg.generation, 'best-of-n')
    chunks = []
    def synthetic_single_pass(batch):
        chunks.append(batch['mask'].shape[0])
        return {'lookahead': None, 'final': {'mask': batch['mask'], 'source_identity': batch['source_identity']}}
    # Only the NN generation boundary is synthetic. Original replication,
    # chunking, metadata identity and final concatenation run unchanged on CPU.
    native.single_pass.search = synthetic_single_pass
    result = native.search({'mask': torch.ones((2, 4), dtype=torch.bool), 'source_identity': torch.tensor([11, 22])})
    assert chunks == [2, 2, 2]
    assert result['lookahead'] is None
    assert result['final']['mask'].shape == (6, 4)
    assert result['final']['source_identity'].tolist() == [11, 11, 11, 22, 22, 22]
    assert result['final']['metadata_tag'] == [f'bon_orig{s}_r{r}' for s in range(2) for r in range(3)]
