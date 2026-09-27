"""Portable selected manifest/request transport through the real stage script."""
import hashlib
import json
from pathlib import Path

import pytest

from services import ligandmpnn_interface_publication as publication
from services.ligandmpnn_interface_selection import InterfaceContextSelection
from services.remote_execution.bundle import (_stage_ligandmpnn_selection, _rewrite,
                                              _input_records, RemoteBundleError)
from test_ligandmpnn_interface_leaf import fixture, stager


@pytest.mark.parametrize('original_format', [None, '.cif', '.pdb'])
def test_remote_selected_documents_and_command_are_relocated(tmp_path, monkeypatch, original_format):
    source, _, native = fixture(tmp_path)
    monkeypatch.setattr(publication, 'get_inputs_dir', lambda: tmp_path / 'inputs')
    chosen = InterfaceContextSelection.model_validate({
        'action': 'ligandmpnn_interface_context', 'source_job_id': 'source',
        'round_id': 'source', 'candidate_ids': [native['candidate_id']],
        'settings': {'binder_chain': 'B', 'target_chain': 'A',
                     'target_patch': native['target_patch'], 'seed': 7,
                     'samples': 1, 'temperature': 0.1}})
    candidate_id = native['candidate_id']
    original_bytes = b'data_original_transport_fixture\n#\n' if original_format == '.cif' else source.read_bytes()
    identity = {'owner_job_id': 'source', 'format': original_format,
                'sha256': hashlib.sha256(original_bytes).hexdigest(),
                'artifact_id': 'producer-artifact', 'target_state': 'on-target'}
    binding = publication.materialize(chosen, {candidate_id: source.read_bytes()},
        source_identities={candidate_id: identity} if original_format else None,
        original_sources={candidate_id: original_bytes} if original_format else None)
    original_manifest = Path(binding['manifest']).read_bytes()
    original_request = Path(binding['sources'][native['candidate_id']]['request']).read_bytes()
    remote = tmp_path / 'worker' / 'bundle' / 'inputs' / 'ligand-selection'
    staging = tmp_path / 'attempt'
    staging.mkdir()
    staged = _stage_ligandmpnn_selection(binding, staging, str(remote))
    # The worker path is simulated by transferring precisely the inventoried tree.
    from shutil import copytree
    copytree(staged, remote)
    if original_format:
        original = Path(binding['sources'][candidate_id]['original']['snapshot_path'])
        relative = original.relative_to(Path(binding['manifest']).parent)
        assert (remote / relative).read_bytes() == original_bytes
        assert original.read_bytes() == original_bytes
    roster, = json.loads((remote / 'selected.json').read_text())
    request = json.loads(Path(roster['request_path']).read_text())
    assert request['structure_path'] == roster['source_path']
    assert request['source_sha256'] == hashlib.sha256(Path(roster['source_path']).read_bytes()).hexdigest()
    effective = stager.stage(Path(roster['request_path']), Path(roster['source_path']), tmp_path / 'effective.json')
    assert effective['candidate_id'] == native['candidate_id']
    command = ['nextflow', 'run', 'workflows/ligandmpnn_interface_context.nf',
               '--interface_context_manifest', binding['manifest']]
    translated_command = [_rewrite(argument, {str(Path(binding['manifest']).parent): str(remote)})
                          for argument in command]
    assert translated_command[:4] == command[:4]
    translated = translated_command[4]
    assert translated == str(remote / 'selected.json')
    assert json.loads(Path(translated).read_text()) == [roster]
    assert original_manifest == Path(binding['manifest']).read_bytes()
    assert original_request == Path(binding['sources'][native['candidate_id']]['request']).read_bytes()
    publication.verify_binding(binding)
    with pytest.raises(ValueError, match='changed'):
        Path(binding['sources'][native['candidate_id']]['path']).write_text('tampered')
        _stage_ligandmpnn_selection(binding, tmp_path / 'retry', str(remote))
