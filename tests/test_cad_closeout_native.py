"""Run in the selected image: real pinned CPU parsers, never inference."""
import os
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]


def atom(serial, name, residue=10, insertion='A'):
    return f'ATOM  {serial:5d} {name:>4} ALA X{residue:4d}{insertion}   {serial:8.3f}{2.:8.3f}{3.:8.3f}{1.:6.2f}{20.:6.2f}           C  \n'


@pytest.mark.parametrize('task',['monomer_motif_indexed','monomer_motif_unindexed'])
def test_complexa_native_evaluation_mask_and_coordinates(tmp_path, task):
    import torch
    from scripts.lib.complexa_contract import Request
    from scripts.bms_complexa.inputs import prepare_source
    from scripts.bms_complexa.config import compose_request
    from proteinfoundation.evaluation.motif_eval import load_motif_info
    from proteinfoundation.utils.motif_utils import extract_motif_from_pdb
    source=tmp_path/'source.pdb'; source.write_text(atom(1,'CA')+atom(2,'CB'))
    request=Request(task=task,task_name='custom',source={'document_id':'source',
        'atom_selection_mode':'all_atom','motif_atoms':[{'chain':'X','residue':10,'insertion_code':'A','atoms':['CA','CB']}]})
    mapping=prepare_source(request.source,source,tmp_path/'prepared',ame=False)
    cfg=compose_request(request,Path('/opt/complexa'),tmp_path/'cfg',tmp_path/'weights',prepared_source=mapping)
    consumed=load_motif_info(cfg.dataset.motif_dict_cfg.custom,'custom')
    mask,coordinates,residue=extract_motif_from_pdb(mapping['native_path'],motif_atom_spec=mapping['motif_atom_spec'],coors_to_nm=False,center_motif=True)
    assert int(mask.sum()) == 2
    assert torch.equal(consumed.motif_mask,mask)
    assert torch.equal(consumed.residue_type,residue)
    assert torch.equal(consumed.x_motif[mask],coordinates[mask])
    assert consumed.contig_string == 'B1'


def test_la_native_atom_spec_needs_no_contig(tmp_path):
    from proteinfoundation.utils.motif_utils import parse_motif
    source=tmp_path/'source.pdb'; source.write_text(atom(1,'CA',insertion=' ')+atom(2,'CB',insertion=' '))
    lengths, masks, coordinates, residues, contigs=parse_motif(str(source),motif_atom_spec='X10: [CA, CB]')
    assert lengths == [1] and contigs == [None]
    assert int(masks[0].sum()) == 2
    assert coordinates[0].shape[-1] == 3


def test_la_native_evaluator_opens_retained_motif_after_source_removal(tmp_path, monkeypatch):
    import shutil
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf
    from lib.laproteina_motif_custody import retain_motif_references
    from run_laproteina_inference import evaluate_native
    monkeypatch.setenv('DATA_PATH',str(tmp_path/'unused-data'))
    with initialize_config_dir(config_dir='/opt/laproteina/configs',version_base='1.3'):
        cfg=compose(config_name='inference_motif_idx_aa')
    original=tmp_path/'original'; original.mkdir()
    motif=original/'motif.pdb'
    motif.write_text(''.join(atom(i,name,insertion=' ') for i,name in enumerate(['N','CA','C','O','CB'],1)))
    cfg.generation.dataset.motif_task_name='custom'
    OmegaConf.update(cfg,'generation.dataset.motif_dict_cfg.custom',{
        'contig_string':'X10','motif_pdb_path':str(motif),'motif_only':False},force_add=True)
    config=original/'native.yaml'; OmegaConf.save(cfg,config)
    retained=tmp_path/'moved'; retained.mkdir()
    target=retained/'native.yaml'; shutil.copyfile(config,target)
    retain_motif_references(config,target)
    shutil.rmtree(original)
    samples=retained/'samples'; samples.mkdir()
    (samples/'custom_0_motif_info.csv').write_text('native_header\n')
    result=evaluate_native({'source_directory':str(samples),'config_path':str(target),
        'job_id':0,'ncpus':1,'metrics':{'compute_designability':False,'compute_codesignability':False,
        'compute_co_sequence_recovery':False,'compute_motif_rmsd':True,
        'designability_motif_eval':False,'codesignability_motif_eval':False}},tmp_path/'run')
    assert result['execution_status'] == 'completed' and result['sample_count'] == 0
    motif_path=Path(result['effective_config']['generation']['dataset']['motif_dict_cfg']['custom']['motif_pdb_path'])
    assert motif_path.is_file() and not motif.is_file()


def test_la_empty_native_designability_binds_mpnn_without_loading_weights(tmp_path, monkeypatch):
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf
    from run_laproteina_inference import evaluate_native
    monkeypatch.setenv('DATA_PATH', str(tmp_path/'unused-data'))
    monkeypatch.setenv('LAPROTEINA_PROTEINMPNN_WEIGHTS', str(tmp_path/'selected-vanilla-weights'))
    with initialize_config_dir(config_dir='/opt/laproteina/configs', version_base='1.3'):
        cfg=compose(config_name='inference_ucond_notri')
    source=tmp_path/'samples'; source.mkdir()
    config=tmp_path/'original.yaml'; OmegaConf.save(cfg,config)
    cwd=Path.cwd()
    result=evaluate_native({'source_directory':str(source),'config_path':str(config),
        'job_id':0,'ncpus':1,'metrics':{'compute_designability':True}},tmp_path/'run')
    assert result['sample_count'] == 0 and result['execution_status'] == 'completed'
    assert Path.cwd() == cwd
    attempts=list((tmp_path/'run').glob('evaluation_*'))
    script=attempts[0]/'ProteinMPNN/protein_mpnn_run.py'
    assert script.read_bytes() == Path('/opt/laproteina/ProteinMPNN/protein_mpnn_run.py').read_bytes()
    assert result['proteinmpnn_weights'] == str(tmp_path/'selected-vanilla-weights')
    assert not (script.parent/'vanilla_model_weights').is_symlink()
