"""Selected image CPU composition and real native parsers; no inference."""
import os
from pathlib import Path
import pytest
from scripts.lib.complexa_contract import Request, Analysis
from scripts.bms_complexa.config import compose_request

MODEL_MAP = {'ca': {'{model}': {'threshold': 0.75, 'op': '<'}}}
VALUES = {
    **{k: MODEL_MAP for k in ('allatom_codesignability_thresholds', 'ca_codesignability_thresholds',
       'designability_thresholds', 'codesignability_thresholds',
       'motif_region_designability_thresholds', 'motif_region_codesignability_thresholds')},
    'motif_rmsd_thresholds': {'ca': 0.5},
    'motif_seq_rec_threshold': {'threshold': 0.9, 'op': '>='},
    'success_thresholds': {'i_pAE': [7, '<=', 31, 'complex']},
    'motif_binder_success_thresholds': {'binder': {'i_pAE': {'value': 7, 'operator': '<=', 'scale': 31}},
       'motif': [{'column': '{seq_type}_motif_rmsd_pred_all', 'threshold': 1.5, 'op': '<'}]},
    'motif_success_criteria': [{'column': '_res_scRMSD_ca_{model}', 'threshold': 0.75, 'op': '<'}],
    'sequence_types': ['self'], 'folding_model': 'esmfold', 'mmseqs_coverage': 0.4,
    'mmseqs_min_seq_id': 0.3, 'compute_residue_type_distribution': False,
    'compute_timing': False, 'require_all_thresholds': False,
}

@pytest.fixture
def upstream():
    root = os.environ.get('COMPLEXA_TEST_SOURCE')
    if not root:
        pytest.skip('selected installed Complexa required')
    return Path(root)

@pytest.mark.parametrize('field', VALUES)
def test_each_analysis_field_sparse_native_composition(tmp_path, upstream, field):
    import ast
    tree = ast.parse((upstream/'src/proteinfoundation/analyze.py').read_text())
    consumers = {n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call) and n.args
                 and isinstance(n.args[0], ast.Constant)
                 and ((isinstance(n.func, ast.Name) and n.func.id == '_cfg_or_default')
                      or (isinstance(n.func, ast.Attribute) and n.func.attr == 'get'
                          and isinstance(n.func.value, ast.Name) and n.func.value.id == 'cfg_aggregation'))}
    assert field in consumers
    from omegaconf import OmegaConf
    request = Request(task='protein_binder', task_name='analysis', analysis={field: VALUES[field]})
    cfg = compose_request(request, upstream, tmp_path/'explicit', tmp_path/'weights')
    dumped = OmegaConf.to_container(cfg.aggregation, resolve=True)
    assert dumped[field] == VALUES[field]
    baseline = compose_request(Request(task='protein_binder', task_name='analysis'), upstream,
                               tmp_path/'baseline', tmp_path/'weights')
    assert {k:v for k,v in dumped.items() if k != field} == {
        k:v for k,v in OmegaConf.to_container(baseline.aggregation, resolve=True).items() if k != field}
    assert Analysis().model_dump(exclude_unset=True) == {}
    cleared = compose_request(Request(task='protein_binder', task_name='analysis', analysis={field: None}),
                              upstream, tmp_path/'clear', tmp_path/'weights')
    assert cleared.aggregation[field] is None


def test_native_analysis_parsers(upstream):
    from proteinfoundation.result_analysis.analysis_utils import parse_threshold_spec, evaluate_threshold
    from proteinfoundation.result_analysis.motif_analysis_utils import resolve_thresholds, resolve_success_criteria, normalize_motif_rmsd_thresholds
    from proteinfoundation.result_analysis.motif_binder_analysis_utils import parse_motif_binder_success, check_redesign_passes_binder_and_motif
    value = Analysis(**VALUES).model_dump(mode='json', exclude_unset=True)
    for key in ('allatom_codesignability_thresholds', 'ca_codesignability_thresholds', 'designability_thresholds',
                'codesignability_thresholds', 'motif_region_designability_thresholds', 'motif_region_codesignability_thresholds'):
        resolved = resolve_thresholds(value[key], model=value['folding_model'])
        spec = parse_threshold_spec(resolved['ca']['esmfold'])
        assert evaluate_threshold(0.5, spec['threshold'], spec['op'], spec['scale'])
        assert not evaluate_threshold(1.0, spec['threshold'], spec['op'], spec['scale'])
    assert normalize_motif_rmsd_thresholds(value['motif_rmsd_thresholds'])['ca']['threshold'] == 0.5
    assert parse_threshold_spec(value['motif_seq_rec_threshold'])['op'] == '>='
    assert parse_threshold_spec(value['success_thresholds']['i_pAE'])['scale'] == 31
    assert resolve_success_criteria(value['motif_success_criteria'], 'esmfold')[0]['column'] == '_res_scRMSD_ca_esmfold'
    binder, motif = parse_motif_binder_success(value['motif_binder_success_thresholds'], 'self')
    assert check_redesign_passes_binder_and_motif({'i_pAE': 0.1}, {'self_motif_rmsd_pred_all': 1.0}, binder, motif)
    assert not check_redesign_passes_binder_and_motif({'i_pAE': 0.9}, {'self_motif_rmsd_pred_all': 1.0}, binder, motif)

@pytest.mark.parametrize('tokens,mask', [(['A1'], [True,False]), (['A2'], [False,True]),
                                       ([1], [False,False]), ([1,'A2'], [False,True]), ([], [False,False])])
def test_hotspots_native_parser_and_both_config_owners(tmp_path, upstream, tokens, mask):
    from proteinfoundation.utils.pdb_utils import load_target_from_pdb
    pdb = tmp_path/'target.pdb'
    pdb.write_text(''.join(
        f'ATOM  {((r-1)*5+i):5d} {atom:^4s} ALA A{r:4d}    {(r*4+dx):8.3f}{dy:8.3f}{0:8.3f}  1.00 20.00          {atom[0]:>2s}  \n'
        for r in (1,2) for i,atom,dx,dy in [(1,'N',-1.2,0.),(2,'CA',0.,0.),
          (3,'C',1.2,0.),(4,'O',1.8,1.),(5,'CB',0.,1.5)]) + 'END\n')
    request = Request(task='protein_binder', task_name='hotspots', min_length=5, max_length=5,
                      source={'document_id':'target','input_spec':'A1-2','target_hotspots':tokens})
    assert request.model_dump(mode='json')['source']['target_hotspots'] == tokens
    cfg = compose_request(request, upstream, tmp_path/'compose', tmp_path/'weights',
                          prepared_source={'native_path':str(pdb)}, evaluation_directory=tmp_path/'eval')
    feature = cfg.generation.dataloader.dataset.conditional_features[0]
    assert list(feature.target_hotspots) == tokens
    assert list(cfg.dataset.target_dict_cfg.hotspots.hotspot_residues) == tokens
    actual = load_target_from_pdb(feature.input_spec, feature.pdb_path, feature.target_hotspots)[3]
    assert actual.tolist() == mask
