"""ProteinMPNN, FAMPNN and validator settings reach the Resequence workflow, checked by the model registry."""
import asyncio
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from services.resequence_settings import DESIGNERS, VALIDATORS, designer_params, validator_params

PDB = "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n"
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "DesignerSettingsForm.tsx"


def test_designer_settings_pass_through_unchanged():
    assert designer_params("mpnn", {"mpnn_temperature": 0.3, "seqs_per_design": 4}) == {"mpnn_temperature": 0.3, "seqs_per_design": 4}
    assert designer_params("fampnn", {"fampnn_temperature": 0.5, "fampnn_checkpoint": "fampnn_0_3.pt"}) == {
        "fampnn_temperature": 0.5, "fampnn_checkpoint": "fampnn_0_3.pt"}
    assert designer_params("mpnn", {}) == {}


@pytest.mark.parametrize("engine,settings,fragment", [
    ("mpnn", {"mpnn_temperature": 9}, "must be <= 2.0"),
    ("mpnn", {"mpnn_temperature": "hot"}, "typed number"),
    ("mpnn", {"mpnn_checkpoint_model": "nope"}, "must be one of"),
    ("fampnn", {"fampnn_num_steps": 5}, "must be >= 10"),
    ("mpnn", {"input_pdb": "x.pdb"}, "not a setting the loop passes"),
    ("mpnn", {"fixed_positions": "A1"}, "not a setting the loop passes"),
    ("fampnn", {"design_chain": "B"}, "not a setting the loop passes"),
])
def test_invalid_or_loop_owned_designer_settings_are_refused(engine, settings, fragment):
    with pytest.raises(ValueError, match=fragment):
        designer_params(engine, settings)


def test_validator_settings_use_the_workflows_parameter_names():
    got = validator_params(["protenix_v2", "boltz2", "esmfold2"], {
        "protenix_v2": {"protenix_n_sample": 3},
        "boltz2": {"boltz_sampling_steps": 100},
        "esmfold2": {"num_loops": 4, "model_variant": "full", "seed": 7},
    })
    assert got == {"protenix_n_sample": 3, "boltz_sampling_steps": 100,
                   "esmf_num_loops": 4, "esmf_model_variant": "full", "esmf_seed": 7}


def test_settings_of_an_unselected_validator_are_not_sent():
    assert validator_params(["boltz2"], {"esmfold2": {"num_loops": 4}}) == {}


@pytest.mark.parametrize("settings,fragment", [
    ({"protenix_v2": {"protenix_n_sample": 99}}, "must be <= 16"),
    ({"boltz2": {"boltz_recycling_steps": 99}}, "must be <= 10"),
    ({"esmfold2": {"num_loops": 99}}, "must be <= 12"),
    ({"boltz2": {"sequence": "AAA"}}, "not a setting the loop passes"),
    ({"protenix_v2": {"protenix_msa_backend": "none"}}, "not a setting the loop passes"),
    ({"nonsense": {"x": 1}}, "not a loop validator"),
])
def test_invalid_validator_settings_are_refused(settings, fragment):
    with pytest.raises(ValueError, match=fragment):
        validator_params(["protenix_v2", "boltz2", "esmfold2"], settings)


from test_denovo_api_final import api, stores  # noqa: F401


async def _launch(api, **fields):
    client, _, root = api
    (root / 'inputs/d.pdb').write_text(PDB)
    return await client.post('/api/jobs/resequence/from-structures/prepare', json=dict(
        sources=[{'path': 'inputs/d.pdb'}], design_chains='A', **fields))


@pytest.mark.asyncio
async def test_launch_forwards_designer_and_validator_settings(api):
    response = await _launch(api, seq_method='fampnn', designer_settings={'fampnn_temperature': 0.4},
        structure_validators=['protenix_v2', 'esmfold2'],
        validator_settings={'protenix_v2': {'protenix_n_sample': 2}, 'esmfold2': {'num_loops': 3}})
    assert response.status_code == 200, response.text
    params = response.json()['params']
    assert params['fampnn_temperature'] == 0.4
    assert params['protenix_n_sample'] == 2 and params['esmf_num_loops'] == 3
    assert 'designer_template' not in params


@pytest.mark.asyncio
async def test_launch_without_settings_sends_none(api):
    response = await _launch(api, seq_method='mpnn')
    assert response.status_code == 200, response.text
    params = response.json()['params']
    assert not any(key.startswith(('mpnn_', 'fampnn_', 'protenix_n', 'boltz_', 'esmf_')) for key in params)
    assert set(params) == {'backbone_input_pdbs', 'design_chains', 'seq_method', 'structure_validators',
                           'protenix_msa_backend', 'resequence_sources', 'resequence_preparation'}


@pytest.mark.asyncio
async def test_launch_refuses_a_bad_setting_and_removes_the_staged_selection(api):
    response = await _launch(api, seq_method='mpnn', designer_settings={'mpnn_temperature': 9})
    assert response.status_code == 422 and 'must be <= 2.0' in response.text
    assert not list((api[2] / 'inputs/design_selections/protein_local_redesign').glob('*'))


def test_every_name_the_form_shows_is_one_the_backend_accepts():
    """The browser lists the same names as the backend allowlist, so no shown control is refused at launch."""
    source = FRONTEND.read_text()
    block = re.search(r"export const LOOP_SETTINGS[^=]*=\s*(\{.*?\n\});", source, re.S)
    assert block, "LOOP_SETTINGS missing from DesignerSettingsForm.tsx"
    listed = {key: set(re.findall(r"'([A-Za-z0-9_]+)'", body)) for key, body in
              re.findall(r"\n\s+(\w+):\s*\[(.*?)\]", block.group(1), re.S)}
    expected = {engine: set(names) for engine, (_, _, names) in DESIGNERS.items()}
    expected.update({validator: set(names) for validator, (_, _, names, _) in VALIDATORS.items()})
    assert listed == expected
