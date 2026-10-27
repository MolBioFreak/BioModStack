"""Project setup descriptors for the two native family-alignment editors.

Scientific settings remain owned by the native editor and Job request, not by
Project Manager. Setup availability does not assert execution availability.
"""
import copy

_MODELS = {
    'family_msa_protein': ('protein_in_silico', 'Protein family alignment'),
    'family_msa_nucleic': ('ngs_molbio', 'Nucleic acid family alignment'),
}


def family_msa_capability(capability_id: str):
    entry = _MODELS.get(capability_id)
    if entry is None:
        return None
    domain, label = entry
    destination = f'/alignments?model={capability_id}&mode=family_msa'
    adapter = f'bms.core-job.{capability_id}.adapter.v1'
    capability = {
        'capability_id': capability_id, 'label': label, 'domain_kind': domain,
        'project_setup_state': 'ready', 'project_setup_adapter_id': adapter,
        'project_native_owner_id': f'{capability_id}:family_msa',
        'safe_setup_destination': destination, 'launch_mode': 'typed_launcher_handoff',
        'product_taxonomy': {'family': 'family_alignment'},
        'result_adapter_ids': [f'bms.family-msa.{capability_id}.adapter.v1'],
    }
    schema = {
        '$id': f'bms.workflow-parameters.{capability_id}.v1', 'type': 'object',
        'properties': {
            'native_job_request': {'type': 'object'},
            'editor_state': {'type': 'object', 'default': {'model_id': capability_id, 'mode': 'family_msa'}},
        },
        'required': ['native_job_request'], 'additionalProperties': False,
        'x-bms-native-editor-draft': True,
    }
    return copy.deepcopy((capability, schema))


def family_msa_capability_inventory():
    capabilities = []
    for model in _MODELS:
        record = family_msa_capability(model)
        assert record is not None
        capabilities.append(record[0])
    return {'schema': 'bms.family-msa-project-workflow-picker.v1', 'capabilities': capabilities}
