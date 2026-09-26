"""Model-owned sequence controls through actual Jobs normalization/compiler."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_registry import get_registry
from routers.jobs import normalize_job_request
from schemas import JobCreate
from services.nextflow import compile_job_nextflow_invocation


@pytest.mark.parametrize('model,mode', [('proteinmpnn','design'),('fampnn','design'),('fampnn','fixed_backbone'),('fampnn','binder_design')])
def test_model_owned_settings_survive_normalization_compiler_clone(model, mode, tmp_path):
    source=tmp_path/'complex.pdb'
    source.write_text('ATOM      1  CA  ALA T   7       0.000   0.000   0.000  1.00 20.00           C\n'
                      'ATOM      2  CA  ALA a  10       1.000   0.000   0.000  1.00 20.00           C\n'
                      'ATOM      3  CA  ALA a  12       2.000   0.000   0.000  1.00 20.00           C\nEND\n')
    definition=get_registry().get_model(model)
    mode_fields=set(next(m for m in definition.modes if m.id==mode).params)
    settings={p.name:deepcopy(p.default) for p in definition.params if p.name in mode_fields and p.default is not None}
    settings.update(input_pdb=str(source),design_chain='a',target_chain='T',fixed_positions='a:10',seqs_per_design=3)
    if model=='proteinmpnn':
        settings.update(mpnn_omitAAs='',mpnn_backbone_noise=0,mpnn_relax_max_cycles=0,mpnn_relax_output=False,
                        mpnn_relax_seqs_per_cycle=3,mpnn_relax_convergence_rmsd=0,mpnn_relax_convergence_score=0,
                        mpnn_relax_convergence_max_cycles=0,mpnn_checkpoint_type='vanilla',mpnn_checkpoint_model='v_48_010',
                        mpnn_output_intermediates=False,mpnn_num_connections=48)
    else:
        settings.update(fampnn_checkpoint='fampnn_0_3_cath.pt',fampnn_seed=42,fampnn_repack_last=False,
                        fampnn_exclude_cys=False,fampnn_fix_target_sidechains=False,fampnn_psce_threshold=0,
                        fampnn_mutation_top_n=0,fampnn_mutation_min_log_odds_delta=0,fampnn_presort_by_length=False,
                        fampnn_timestep_mode='cosine',fampnn_scn_num_steps=20,fampnn_scn_s_churn=0,fampnn_scn_s_noise=0)
    assert set(settings)<=mode_fields
    request=JobCreate(name='sequence contract fixture',model_id=model,mode=mode,params=settings)
    normalized=normalize_job_request(request)
    replay=normalize_job_request(JobCreate.model_validate(normalized.model_dump(mode='json')))
    assert replay.model_dump()==normalized.model_dump()
    job=SimpleNamespace(id='sequence-contract-fixture',model_id=model,mode=mode,params=normalized.params,provenance={})
    invocation=compile_job_nextflow_invocation(job,deepcopy(normalized.params),str(tmp_path/'out'))
    document=json.loads(next(g.payload for g in invocation.generated_inputs if g.relative_path=='.sequence-design-settings.json'))
    for key,value in settings.items():
        assert normalized.params[key]==value,key
        assert invocation.native_parameters[key]==value,key
        if key!='input_pdb': assert document[key]==value,key
    assert invocation.entrypoint=='workflows/protein_sequence_design.nf'
    assert document['sequence_design_engine']==model
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('model', ['proteinmpnn','fampnn'])
def test_all_consumed_scientific_settings_are_available_in_each_mode(model):
    definition=get_registry().get_model(model)
    declared={p.name for p in definition.params}
    # Antibody constraint mode remains a historical, non-generic wrapper control.
    applicable=declared-({'fampnn_constraint_mode'} if model=='fampnn' else set())
    for mode in definition.modes:
        assert set(mode.params)==applicable
    defaults={p.name:p.default for p in definition.params}
    assert defaults['seqs_per_design']==8
    if model=='proteinmpnn':
        assert defaults['mpnn_relax_output'] is False
        assert defaults['mpnn_relax_max_cycles']==0
    else:
        assert defaults['fampnn_checkpoint']=='fampnn_0_0.pt'
        assert defaults['fampnn_seed']==0


def test_fampnn_native_null_threshold_survives_compiler(tmp_path):
    source=tmp_path/'input.pdb'
    source.write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n')
    request=JobCreate(name='native null threshold',model_id='fampnn',mode='design',
                      params=dict(input_pdb=str(source),fampnn_psce_threshold=None))
    normalized=normalize_job_request(request)
    job=SimpleNamespace(id='null-threshold',model_id='fampnn',mode='design',params=normalized.params,provenance={})
    invocation=compile_job_nextflow_invocation(job,deepcopy(normalized.params),str(tmp_path/'out'))
    document=json.loads(next(g.payload for g in invocation.generated_inputs if g.relative_path=='.sequence-design-settings.json'))
    assert document['fampnn_psce_threshold'] is None
