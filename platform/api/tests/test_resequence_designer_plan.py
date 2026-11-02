"""A Resequence plan declares the sequence designer that actually runs, with its image, helpers and weights.

The plan used to branch only on ProteinMPNN versus FAMPNN, so a LigandMPNN, Caliby or ProtonPottsMPNN loop
shipped to a worker without its container image and failed at the first task.
"""
import json

import pytest

from test_denovo_remote_dependencies import plan  # noqa: F401  (also provides the assets fixture)


def _declared(selected):
    metadata = selected.metadata
    stages = {component.authority.rsplit(':', 1)[-1] for component in (*metadata.static_components, *metadata.dynamic_templates)}
    images = {d.relative_path for d in metadata.dependencies if d.kind == 'image'}
    weights = {d.relative_path for d in metadata.dependencies if d.kind == 'weights'}
    tools = {d.relative_path for d in metadata.dependencies if d.kind == 'support_tool'}
    return stages, images, weights, tools


def _plan(designer, tmp_path, **extra):
    template = tmp_path / 'designer.json'
    template.write_text(json.dumps(extra.pop('template', {})))
    params = {'plr_seq_method': designer, 'plr_structure_validators': ['boltz2'], 'plr_designer_template': str(template)}
    return plan(params, mode='region_redesign', entrypoint='protein_local_redesign')


@pytest.mark.parametrize('designer,stage,image', [
    ('ligandmpnn', 'RunLigandMPNNResequence', 'foundry.sif'),
    ('caliby', 'RunCalibyResequence', 'caliby.sif'),
    ('protonpottsmpnn', 'RunProtonPottsResequence', 'protonpottsmpnn.sif'),
])
def test_the_chosen_designer_stage_and_its_image_are_declared(designer, stage, image, tmp_path):
    stages, images, _, tools = _declared(_plan(designer, tmp_path))
    assert stage in stages and image in images
    assert 'scripts/run_resequence_designer.py' in tools
    # No other designer's stage rides along.
    others = {'RunLigandMPNNResequence', 'RunCalibyResequence', 'RunProtonPottsResequence'} - {stage}
    assert not others & stages
    assert not {'RunFAMPNN', 'RunMPNN'} & stages


def test_caliby_declares_the_checkpoint_the_template_names(tmp_path):
    _, _, weights, _ = _declared(_plan('caliby', tmp_path, template={'model_name': 'soluble_caliby_v1'}))
    assert 'caliby/model_params/caliby/soluble_caliby_v1.ckpt' in weights or any(w.endswith('soluble_caliby_v1.ckpt') for w in weights)


@pytest.mark.parametrize('designer,stage,image', [('mpnn', 'RunMPNN', 'dl_binder_design.sif'), ('fampnn', 'RunFAMPNN', 'fampnn.sif')])
def test_proteinmpnn_and_fampnn_plans_are_unchanged(designer, stage, image, tmp_path):
    stages, images, _, _ = _declared(_plan(designer, tmp_path))
    assert stage in stages and image in images
    assert not {'RunLigandMPNNResequence', 'RunCalibyResequence', 'RunProtonPottsResequence'} & stages
