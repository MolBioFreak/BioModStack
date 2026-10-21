"""Execute actual parent Groovy projections without child/model execution."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def project(tmp_path, params, kind, analysis=None):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('Existing pinned offline Groovy/Nextflow jar not selected')
    source = (ROOT / 'workflows/antibody_denovo.nf').read_text()
    if kind == 'fampnn':
        start = source.index('    def modelParams = ', source.index('process SpawnFAMPNNJobs'))
        end = source.index('\n    def params_json_base64', start)
        expression = source[start:end]
        output = 'params_json'
    else:
        start = source.index('                def child_params = ', source.index('msa_for_spawn ='))
        end = source.index('\n                ])', start) + len('\n                ])')
        expression = source[start:end]
        output = 'child_params'
    program = ('def params = new groovy.json.JsonSlurper().parseText(' + json.dumps(json.dumps(params)) + ')\n' +
               'def analysisParams = new groovy.json.JsonSlurper().parseText(' + json.dumps(json.dumps(analysis or {})) + ')\n' +
               'def structure_validator=' + json.dumps(kind) + '\n' + expression + '\nprintln(' + output + ')\n')
    script = tmp_path / 'actual-parent.groovy'
    script.write_text(program)
    result = subprocess.run(['java', '-cp', jar, 'groovy.ui.GroovyMain', str(script)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_fampnn_complete_effective_controls_and_falsey_values(tmp_path):
    params = dict(fampnn_temperature=0.0, fampnn_psce_threshold=None, fampnn_num_steps=37,
                  fampnn_seed=0, fampnn_presort_by_length=False, fampnn_seq_only=False,
                  fampnn_repack_last=True, fampnn_exclude_cys=False, fampnn_batch_size=3,
                  fampnn_scn_num_steps=7, fampnn_extra_config='',
                  fampnn_analysis_declaration={'scope': 'old'})
    contract = {'core_protein_scientific_contract': 1,
                'fampnn_analysis_declaration': {'scope': 'producer-owned'}}
    child = project(tmp_path, params, 'fampnn', contract)
    assert all(child[k] == v for k, v in {**params, **contract}.items())


def test_fampnn_omitted_keys_keep_existing_stage_defaults(tmp_path):
    child = project(tmp_path, {}, 'fampnn')
    assert {k: child[k] for k in ('fampnn_temperature', 'fampnn_psce_threshold', 'fampnn_num_steps')} == {
        'fampnn_temperature': 0.0001, 'fampnn_psce_threshold': 0.15, 'fampnn_num_steps': 500}


@pytest.mark.parametrize('kind,controls', [
    ('boltz2', {'boltz_seed': 0, 'boltz_use_msa': True, 'boltz_use_potentials': False,
                'boltz_num_samples': 3, 'boltz_recycling_steps': 0, 'boltz_step_scale': None}),
    ('esmfold2', {'esmf_seed': 0, 'esmf_use_msa': True, 'esmf_model_variant': 'full',
                  'esmfold2_validation_num_diffusion_samples': 3,
                  'esmfold2_validation_num_sampling_steps': 17}),
    ('protenix', {'protenix_seeds': '0,17', 'protenix_use_msa': True,
                  'protenix_use_template': False, 'protenix_n_sample': 3,
                  'protenix_enable_cache': False}),
])
def test_validation_children_retain_model_controls_without_override(tmp_path, kind, controls):
    params = {**controls, 'epitope_residues': 'X42A', 'structure_validator': kind}
    child = project(tmp_path, params, kind)
    assert all(child[k] == value for k, value in params.items())
