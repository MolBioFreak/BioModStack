"""Newly reconciled native constructor leaves, installed inert composition."""
import os
from pathlib import Path
import pytest
from scripts.lib.complexa_contract import Request
from scripts.bms_complexa.config import compose_request


@pytest.mark.parametrize('task,key,inherited,explicit', [
    ('monomer_motif_indexed','padding',True,False),
    ('monomer_motif_unindexed','padding',False,True),
    ('protein_binder','binder_gen_only',True,False)])
def test_constructor_leaf_profile_and_override(tmp_path, task, key, inherited, explicit):
    root = os.environ.get('COMPLEXA_TEST_SOURCE')
    if not root:
        pytest.skip('Installed selected Complexa required')
    values = {'task':task,'task_name':'exact','source':{'document_id':'input','contig_string':'A1-2','input_spec':'A1-2'}}
    prepared = {'native_path':str(tmp_path/'retained.pdb')}
    cfg = compose_request(Request(**values), Path(root), tmp_path/'inherited', tmp_path/'weights', prepared_source=prepared)
    assert cfg.generation.dataloader.dataset.conditional_features[0][key] == inherited
    values['source'][key] = explicit
    cfg = compose_request(Request(**values), Path(root), tmp_path/'explicit', tmp_path/'weights', prepared_source=prepared)
    assert cfg.generation.dataloader.dataset.conditional_features[0][key] == explicit


def test_ligand_identity_and_rf3_verbose(tmp_path):
    root = os.environ.get('COMPLEXA_TEST_SOURCE')
    if not root:
        pytest.skip('Installed selected Complexa required')
    r = Request(task='ligand_binder', task_name='identity',
                source={'document_id':'exact', 'pdb_id':'original-id'},
                reward={'rf3folding': {'verbose':True}})
    cfg = compose_request(r, Path(root), tmp_path/'compose', tmp_path/'weights',
                          prepared_source={'native_path':str(tmp_path/'original.pdb')})
    assert cfg.generation.dataloader.dataset.conditional_features[0].pdb_id == 'original-id'
    assert cfg.generation.reward_model.reward_models.rf3folding.verbose is True


def test_retained_motif_csv_reaches_native_constructor(tmp_path):
    root = os.environ.get('COMPLEXA_TEST_SOURCE')
    if not root:
        pytest.skip('Installed selected Complexa required')
    from scripts.bms_complexa.run import run
    structure = tmp_path/'source.pdb'
    structure.write_text('REMARK inert custody fixture; no structure parsed\nEND\n')
    table = tmp_path/'table.csv'
    table.write_text('contig\nA1-2\n')
    request = Request(task='monomer_motif_indexed', task_name='csv',
                      source={'document_id':'structure','motif_csv_slot':'table'})
    receipt = run(request, upstream=Path(root), weights=tmp_path/'weights', output=tmp_path/'out',
                  source_path=structure, motif_csv_path=table, prepare_only=True)
    features = receipt['effective_config']['generation']['dataloader']['dataset']['conditional_features']
    path = Path(features[0]['motif_csv_path'])
    assert path.read_bytes() == table.read_bytes()
    assert path.is_relative_to(tmp_path/'out')
    assert receipt['execution'] == 'not_started'
