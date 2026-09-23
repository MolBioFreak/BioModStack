"""Selected model-owner transport: typed request, immutable snapshots, raw readback."""
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.ligandmpnn_interface_selection import (
    InterfaceContextSelection, compile_selected_manifest, read_selected_attachment,
)
from test_ligandmpnn_interface_leaf import fixture, runner


def selection(candidate_ids):
    return InterfaceContextSelection.model_validate({
        'action': 'ligandmpnn_interface_context', 'source_job_id': 'source-job',
        'round_id': 'child-round', 'candidate_ids': candidate_ids,
        'settings': {'binder_chain': 'B', 'target_chain': 'A', 'target_patch': ['A3', 'A4'],
                     'seed': 7, 'samples': 1, 'temperature': 0.1},
    })


def test_exact_route_is_not_advertised_as_a_legacy_mode():
    from services.nextflow import resolve_nextflow_entrypoint
    import yaml
    root = Path(__file__).resolve().parents[3]
    assert resolve_nextflow_entrypoint(effective_profile='ligandmpnn', model_id='ligandmpnn',
                                       mode='interface_context') == 'workflows/ligandmpnn_interface_context.nf'
    model = yaml.safe_load((root / 'platform/api/config/models/ligandmpnn.yaml').read_text())
    assert 'interface_context' not in {mode['id'] for mode in model['modes']}


@pytest.mark.parametrize('change', [
    {'action': 'redesign'}, {'candidate_ids': []}, {'candidate_ids': ['x', 'x']},
    {'settings': {'binder_chain': 'B', 'target_chain': 'B', 'target_patch': ['B1'],
                  'seed': 0, 'samples': 1, 'temperature': 0.1}},
    {'settings': {'binder_chain': 'B', 'target_chain': 'A', 'target_patch': ['A1', 'A1'],
                  'seed': 0, 'samples': 1, 'temperature': 0.1}},
    {'settings': {'binder_chain': 'B', 'target_chain': 'A', 'target_patch': ['A1'],
                  'seed': 0, 'samples': 17, 'temperature': 0.1}},
])
def test_selection_is_closed_and_typed(change):
    data = selection(['x']).model_dump()
    data.update(change)
    with pytest.raises(ValidationError):
        InterfaceContextSelection.model_validate(data)


def test_generated_manifest_binds_only_selected_source_bytes(tmp_path):
    source, _, native = fixture(tmp_path)
    selected = selection([native['candidate_id']])
    selected = selected.model_copy(update={'settings': selected.settings.model_copy(update={
        'target_patch': native['target_patch']})})
    original = source.read_bytes()
    generated, manifest_path = compile_selected_manifest(selected, {native['candidate_id']: original}, tmp_path / 'output')
    assert not Path(manifest_path).exists()  # pure preview; launch materializes once
    assert source.read_bytes() == original
    files = dict(generated)
    assert len(files) == 3
    record, = json.loads(files['inputs/ligandmpnn_interface_context/selected.json'])
    request = json.loads(files['inputs/ligandmpnn_interface_context/000/request.json'])
    assert record['source_path'] == request['structure_path']
    assert request['source_sha256'] == hashlib.sha256(original).hexdigest()
    assert (request['seed'], request['samples'], request['temperature'], request['target_patch']) == (
        7, 1, 0.1, native['target_patch'])
    assert files['inputs/ligandmpnn_interface_context/000/source.pdb'] == original
    with pytest.raises(ValueError, match='exactly'):
        compile_selected_manifest(selected, {}, tmp_path / 'output')
    with pytest.raises(ValueError, match='exactly'):
        compile_selected_manifest(selected, {native['candidate_id']: original, 'unselected': original}, tmp_path / 'output')


def test_attachment_preserves_raw_conditions_and_artifact_hashes(tmp_path):
    source, _, request = fixture(tmp_path)
    ref, complete, ablated = runner.prepare(source, request['target_patch'], 'B')
    result_dir = tmp_path / 'native'
    result_dir.mkdir()
    conditions = {}
    for label, filename, content in [('supplied_complex', 'masked_complex.pdb', complete),
                                     ('without_binder', 'masked_without_binder.pdb', ablated)]:
        staged = result_dir / filename
        staged.write_text(content)
        conditions[label] = {'masked_input_sha256': hashlib.sha256(staged.read_bytes()).hexdigest(),
                             'samples': [{'batch_idx': 0, 'design_idx': 0, 'sampled_patch': ref,
                                          'patch_residue_count': len(ref), 'patch_exact_matches': len(ref)}]}
    result = {'schema': 'bms.ligandmpnn.interface-context.experimental.v1',
              'status': 'completed_unclassified', 'qualification': 'unqualified',
              'model_type': 'ligand_mpnn', 'method': 'masked_whole_patch_sampling_with_binder_ablation',
              'checkpoint_sha256': runner.CHECKPOINT_SHA256, 'foundry_version': runner.VERSION,
              'candidate_id': request['candidate_id'], 'round_id': request['round_id'],
              'source_sha256': request['source_sha256'], 'target_patch': request['target_patch'],
              'reference_patch': ref, 'fixed_binder_chain': 'B', 'target_chain': 'A',
              'seed': 7, 'samples': 1, 'temperature': 0.1, 'conditions': conditions}
    (result_dir / 'result.json').write_text(json.dumps(result))
    attachment = read_selected_attachment(result_dir, candidate_id=request['candidate_id'],
                                          round_id=request['round_id'], source=source.read_bytes())
    assert attachment['conditions'] == conditions
    assert attachment['status'] == 'completed_unclassified'
    assert attachment['artifacts']['result.json']['sha256'] == hashlib.sha256((result_dir / 'result.json').read_bytes()).hexdigest()
    assert 'verdict' not in attachment
    with pytest.raises(ValueError, match='identity mismatch'):
        read_selected_attachment(result_dir, candidate_id='other', round_id=request['round_id'], source=source.read_bytes())
