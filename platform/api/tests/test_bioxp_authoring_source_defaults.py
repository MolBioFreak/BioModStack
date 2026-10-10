"""Published authoring schema follows the unchanged explicit converter."""
from copy import deepcopy
import pytest
from jsonschema import Draft202012Validator
from bioxp_workflow_authoring import discovery, native_intent
from bioxp_method_model import method_catalog


def test_source_defaults_are_documentary_not_silent_physical_choices():
    found = discovery()
    mapping = found['native_request']['properties']['steps']['items']['discriminator']['mapping']
    selected = [name for name in mapping if name in ('source_fluid_offset', 'source_mix', 'source_purge')]
    assert selected
    catalog = {a['action']: a for a in method_catalog()['actions']}
    for name in selected:
        key = mapping[name].rsplit('/', 1)[-1]
        source = found['native_request']['$defs'][key]
        authored = catalog[name]['inputs']
        assert set(authored['required']) == set(authored['properties'])
        for field, schema in source['properties'].items():
            projected = authored['properties'][field]
            assert 'default' not in projected
            if 'default' in schema:
                assert projected['source_default'] == schema['default']
        omitted = {'operation': name}
        assert not Draft202012Validator(authored).is_valid(omitted)
        with pytest.raises(ValueError, match='missing'):
            native_intent(omitted)
        assert omitted == {'operation': name}


def test_structural_projection_never_mutates_source_schema_or_raw_choices():
    from bioxp_workflow_authoring import explicit_authoring_schema
    source = {'type': 'object', 'properties': {'prefill': {'type': 'boolean', 'default': True},
        'values': {'type': 'array', 'default': []}}, 'required': []}
    original = deepcopy(source)
    projected = explicit_authoring_schema(source)
    assert source == original
    assert projected['required'] == ['prefill', 'values']
    assert projected['properties']['values']['source_default'] == []
    assert Draft202012Validator(projected).is_valid({'prefill': False, 'values': []})
