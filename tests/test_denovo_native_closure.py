"""Inert selected-image input/composition acceptance; no model inference."""
import os
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def test_installed_source_free_input_and_sparse_profile_overlay(tmp_path):
    source = os.environ.get('BMS_TEST_RFD4_SOURCE')
    if not source:
        pytest.skip('Selected installed RFD4 source required')
    from bms_rfd4.inputs import prepare_inputs
    from bms_rfd4.run import compose_native
    from atomworks.io import parse
    from rfproteina.utils.parse_presets import ANNOTATED_CIF
    from atomworks.ml.conditions import C_SEQ, C_CRD
    from atomworks.io.utils.standard_annotations import S_SEGMIN, S_SEGMAX
    from omegaconf import OmegaConf
    import numpy as np
    request = {'input': {'kind': 'unconditional', 'name': 'native', 'chains': [
        {'chain_id': 'A', 'min_length': 12, 'max_length': 19},
        {'chain_id': 'B', 'min_length': 8, 'max_length': 8}]},
        'groups': {'generation': 'inverse_folding', 'recycle_strategy': 'thinking_low'},
        'settings': {'seed': 0, 'generation': {'n_recycle_schedule': None,
             'model': {'local_latents': {'simulation_step_params': {'sc_scale_noise': 0}}}}}}
    inputs, _ = prepare_inputs(request, {}, tmp_path / 'prepared',
        sentinel=Path(source) / 'data/benchmarks/monomer/monomer-short.cif')
    atoms = parse(inputs / 'native.cif', config=ANNOTATED_CIF)['asym_unit'][0]
    assert S_SEGMIN.annotation(atoms).tolist() == [12, 8]
    assert S_SEGMAX.annotation(atoms).tolist() == [19, 8]
    assert not C_SEQ.mask(atoms).any() and not C_CRD.mask(atoms).any()
    assert np.isnan(atoms.coord).all()
    cfg = compose_native(request, Path(source) / 'configs', inputs=inputs,
                         output=tmp_path / 'output', bundle=tmp_path / 'not-loaded-weights')
    assert cfg.seed == 0
    assert cfg.generation.args.inverse_folding is True
    assert cfg.generation.n_recycle_schedule is None
    assert cfg.generation.model.local_latents.simulation_step_params.sc_scale_noise == 0
    assert 'bb_ca' not in request['settings']['generation']['model']
    (tmp_path / 'composed.yaml').write_text(OmegaConf.to_yaml(cfg, resolve=False))
