"""Exercise the installed evaluator with zero samples: no folding or generation."""
import json
import os
from pathlib import Path
import sys
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]


def test_discovery_has_only_implemented_native_metrics():
    from lib.laproteina_contract import evaluation_schema
    fields = evaluation_schema()['properties']['metrics']['properties']
    assert 'compute_fid' not in fields
    assert fields['codesignability_folding_models']['items']['enum'] == ['esmfold']
    assert 'compute_co_sequence_recovery' in fields


def test_native_empty_evaluation_and_sparse_controls(tmp_path):
    if not Path('/opt/laproteina/proteinfoundation/evaluate.py').exists():
        pytest.skip('Installed La runtime required')
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf
    from run_laproteina_inference import evaluate_native
    with initialize_config_dir(config_dir='/opt/laproteina/configs', version_base='1.3'):
        cfg = compose(config_name='inference_ucond_notri')
    # Native parse_cfg_for_table resolves environment expressions even though no
    # sample is evaluated; use an explicit private non-existent data location.
    os.environ['DATA_PATH'] = str(tmp_path/'unused-data')
    source = tmp_path/'native'
    source.mkdir()
    config = tmp_path/'retained.yaml'
    OmegaConf.save(cfg, config)
    request = {'source_directory': str(source), 'config_path': str(config), 'job_id': 0, 'ncpus': 1,
               'metrics': {'compute_designability': False, 'keep_folding_outputs': False}}
    prepared = evaluate_native(request, tmp_path/'prepared', prepare_only=True)
    assert prepared['effective_config']['generation']['metric']['compute_codesignability'] is True
    assert prepared['effective_config']['generation']['metric']['keep_folding_outputs'] is False
    done = evaluate_native(request, tmp_path/'run')
    assert done['sample_count'] == 0
    assert done['execution_status'] == 'completed'
    assert 'pdb_path' in Path(done['result_path']).read_text().splitlines()[0]
    assert list(source.iterdir()) == []
    assert OmegaConf.load(config).generation.metric.compute_designability is True
