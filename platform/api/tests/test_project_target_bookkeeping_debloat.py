"""The target's immutable receipt is authority, not hand-entered map bookkeeping."""
import copy
import pytest

from routers.projects import _complete_domain_v4_attestations
from services.global_experiments.workflow_setups import _domain_payload
from services.ngs_molbio_capabilities import NgsMolBioCapabilityError, validate_domain_experiment


def target_domain():
    payload = _domain_payload('Explore', 'Determine feasibility', 'exploration', 'test-owner')
    payload['source_receipt_ids'] = ['receipt-1']
    payload['domain_payload']['targets'] = [{
        'target_id': 'target-1', 'label': 'Verified source', 'role': 'target',
        'source_receipt_ids': ['receipt-1'], 'dataset_member_refs': [],
        'expected_content_sha256': 'a' * 64,
    }]
    return _complete_domain_v4_attestations(payload)


def test_target_needs_no_unproduced_entity_map_bookkeeping():
    payload = target_domain()
    assert validate_domain_experiment(payload) == payload
    assert 'entity_map_reference' not in payload['domain_payload']['targets'][0]


@pytest.mark.parametrize('field,value', [('expected_content_sha256', 'not-a-digest'), ('source_receipt_ids', [])])
def test_target_keeps_required_exact_source_identity(field, value):
    payload = target_domain()
    payload['domain_payload']['targets'][0][field] = value
    with pytest.raises(NgsMolBioCapabilityError):
        validate_domain_experiment(_complete_domain_v4_attestations(payload))


def test_historical_map_is_preserved_and_validated_when_supplied():
    payload = target_domain()
    metadata = {'schema': 'bms.protein-entity-map-reference.v1', 'authority_kind': 'native_receipt',
        'receipt_id': 'map-receipt-1', 'receipt_sha256': 'b' * 64, 'content_sha256': 'c' * 64,
        'canonical_size_bytes': 128, 'entity_count': 1, 'residue_mapping_count': 0, 'display_entities': []}
    payload['domain_payload']['targets'][0]['entity_map_reference'] = copy.deepcopy(metadata)
    payload = _complete_domain_v4_attestations(payload)
    result = validate_domain_experiment(payload)
    assert result['domain_payload']['targets'][0]['entity_map_reference'] == metadata
    payload['domain_payload']['targets'][0]['entity_map_reference']['receipt_sha256'] = 'invalid'
    with pytest.raises(NgsMolBioCapabilityError):
        validate_domain_experiment(_complete_domain_v4_attestations(payload))
