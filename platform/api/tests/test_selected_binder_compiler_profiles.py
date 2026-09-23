"""Exact native routes and profiles for independent selected operations."""
from pathlib import Path

from services import ligandmpnn_interface_publication as publication
from services.ligandmpnn_interface_selection import InterfaceContextSelection
from services.nextflow import compile_nextflow_invocation
from test_ligandmpnn_interface_leaf import fixture


def test_ligandmpnn_selected_compiler_passes_only_manifest_and_profile(tmp_path, monkeypatch):
    source, _, native = fixture(tmp_path)
    monkeypatch.setattr(publication, 'get_inputs_dir', lambda: tmp_path / 'managed-inputs')
    request = InterfaceContextSelection.model_validate({
        'action': 'ligandmpnn_interface_context', 'source_job_id': 'source',
        'round_id': 'source', 'candidate_ids': [native['candidate_id']],
        'settings': {'binder_chain': 'B', 'target_chain': 'A',
                     'target_patch': native['target_patch'], 'seed': 7,
                     'samples': 1, 'temperature': 0.1},
    })
    binding = publication.materialize(request, {native['candidate_id']: source.read_bytes()})
    invocation = compile_nextflow_invocation('ligandmpnn', 'interface_context', {
        publication.KEY: binding, 'interface_context_manifest': binding['manifest'],
        'selection_source_job_id': 'source', 'lineage_root_job_id': 'source',
    }, str(tmp_path / 'output'), job_id='interface-child')
    assert invocation.entrypoint == 'workflows/ligandmpnn_interface_context.nf'
    assert 'ligandmpnn_interface_context,workstation_ryzen7960x' in invocation.command
    assert invocation.native_parameters['interface_context_manifest'] == binding['manifest']
    assert '--interface_context_manifest' in invocation.command
    assert '--ligandmpnn_interface_selection' not in invocation.command
    assert '--selection_source_job_id' not in invocation.command
    assert '--lineage_root_job_id' not in invocation.command
    assert Path(binding['manifest']).is_file()
