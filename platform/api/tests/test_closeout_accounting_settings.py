"""CA/PT adversarial fixtures: synthetic publications, no model execution."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.test_core_protein_candidates import artifacts, job
from services.core_protein_result_contract import CandidateIntegrityError, prepare_esmfold2_publication
from services.core_protein_execution_settings import prepare_receipt

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('closeout_runner', ROOT / 'scripts/run_esmfold2_inference.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def publication(tmp_path):
    root = artifacts(tmp_path, ids=('a',))
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest['schema_version'] = 2
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return root


@pytest.mark.parametrize('version', [None, 1, True, '2', 999])
def test_manifest_dialect_rejected(tmp_path, version):
    root = publication(tmp_path)
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest['schema_version'] = version
    (root / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(CandidateIntegrityError):
        prepare_esmfold2_publication(job(tmp_path), root, [])


@pytest.mark.parametrize('extra', ['extra.pdb', 'extra.cif', 'extra.mmcif', 'extra.metrics.json'])
def test_exact_scientific_inventory(tmp_path, extra):
    root = publication(tmp_path)
    (root / extra).write_text('undeclared')
    with pytest.raises(CandidateIntegrityError):
        prepare_esmfold2_publication(job(tmp_path), root, [])


def test_ancillary_and_public_count(tmp_path):
    root = publication(tmp_path)
    (root / 'capture.json').write_text('{}')
    (root / 'summary.tsv').write_text('fixture')
    current = job(tmp_path)
    current.params = {'num_diffusion_samples': 3}
    assert prepare_esmfold2_publication(current, root, [])[1]['summary']['requested_count'] == 3
    current.params['esmf_num_diffusion_samples'] = 2
    with pytest.raises(CandidateIntegrityError):
        prepare_esmfold2_publication(current, root, [])


def test_missing_candidate_has_typed_identity(tmp_path):
    root = publication(tmp_path)
    (root / 'a.pdb').unlink()
    with pytest.raises(CandidateIntegrityError) as exc:
        prepare_esmfold2_publication(job(tmp_path), root, [])
    assert exc.value.reason['candidate_id'] == 'a'

@pytest.mark.parametrize('params, expected', [({}, None), ({'num_diffusion_samples': 3}, 3),
    ({'esmf_num_diffusion_samples': 3}, 3), ({'num_diffusion_samples': 3, 'esmf_num_diffusion_samples': 3}, 3)])
def test_count_alias_authority(tmp_path, params, expected):
    current = job(tmp_path)
    current.params = params
    assert prepare_esmfold2_publication(current, publication(tmp_path), [])[1]['summary']['requested_count'] == expected


@pytest.mark.parametrize('params', [{'num_diffusion_samples': True}, {'num_diffusion_samples': '3'},
    {'num_diffusion_samples': 1, 'esmf_num_diffusion_samples': True}])
def test_count_types_not_coerced(tmp_path, params):
    current = job(tmp_path)
    current.params = params
    with pytest.raises(CandidateIntegrityError):
        prepare_esmfold2_publication(current, publication(tmp_path), [])


def test_two_component_scopes_shared_source_and_contradiction(tmp_path):
    source = tmp_path / 'shared.a3m'
    source.write_text('>q\nAC\n')
    original = {'complex_components': [
        {'id': cid, 'type': 'protein', 'sequence': 'AC', 'msa_path': str(source)} for cid in ['B', 'C']]}
    _, receipt = runner.compile_workflow_request({'core_protein_scientific_contract': 1, **original}, {str(source): str(source)})
    owner = SimpleNamespace(provenance={'core_protein_requested_params': original})
    path = tmp_path / 'effective_settings.json'
    path.write_text(json.dumps(receipt))
    assert len(prepare_receipt(owner, tmp_path, path)['receipt']['sources']) == 2
    receipt['sources'][1]['sha256'] = '0' * 64
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='contradictory staged byte'):
        prepare_receipt(owner, tmp_path, path)


def test_no_msa_retains_empty_inventory_and_defaults(tmp_path):
    original = {'sequence': 'AC', 'seed': 0}
    _, receipt = runner.compile_workflow_request({'core_protein_scientific_contract': 1, **original}, {})
    path = tmp_path / 'effective_settings.json'
    path.write_text(json.dumps(receipt))
    result = prepare_receipt(SimpleNamespace(provenance={'core_protein_requested_params': original}), tmp_path, path)
    assert result['receipt']['sources'] == []
    settings = {s['key']: s for s in result['receipt']['settings']}
    assert settings['seed']['effective'] == 0
    assert settings['msa_remove_insertions']['effective'] is True
    assert settings['msa_remove_insertions']['origin'] == 'workflow_default'


def test_inventory_revalidated_after_preparation(tmp_path):
    from services.core_protein_result_contract import revalidate_prepared_publication
    root = publication(tmp_path)
    _, receipt = prepare_esmfold2_publication(job(tmp_path), root, [])
    (root / 'extra.pdb').write_bytes((root / 'a.pdb').read_bytes())
    with pytest.raises(CandidateIntegrityError):
        revalidate_prepared_publication(tmp_path, receipt)



@pytest.mark.parametrize('component', [False, True])
@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'foreign_scope', 'requested_path', 'used_path', 'drop_argv', 'drop_request'])
def test_source_inventory_first_ingestion(tmp_path, component, damage):
    source = tmp_path / 'source.a3m'
    source.write_text('>q\nAC\n')
    msa = {'msa_path': str(source), 'msa_remove_insertions': False}
    original = {'complex_components': [{'id': 'B', 'type': 'protein', 'sequence': 'AC', **msa}]} if component else {'sequence': 'AC', **msa}
    original['seed'] = 0
    argv, receipt = runner.compile_workflow_request({'core_protein_scientific_contract': 1, **original}, {str(source): str(source)})
    owner = SimpleNamespace(provenance={'core_protein_requested_params': original})
    path = tmp_path / 'effective_settings.json'
    path.write_text(json.dumps(receipt))
    assert len(prepare_receipt(owner, tmp_path, path)['receipt']['sources']) == 1
    if damage == 'missing': receipt['sources'] = []
    elif damage == 'duplicate': receipt['sources'] *= 2
    elif damage == 'foreign_scope': receipt['sources'][0]['scope'] = 'foreign'
    elif damage in ('requested_path', 'used_path'): receipt['sources'][0][damage] = 'foreign'
    elif damage == 'drop_request':
        (original['complex_components'][0] if component else original).pop('msa_path')
    elif component:
        idx = argv.index('--complex-components-json') + 1
        components = json.loads(argv[idx]); components[0].pop('msa_path')
        argv[idx] = json.dumps(components)
    else:
        argv[argv.index('--msa-path') + 1] = ''
    receipt['argv'] = argv  # Mutate the actual command, not an unbound local copy.
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        prepare_receipt(owner, tmp_path, path)
