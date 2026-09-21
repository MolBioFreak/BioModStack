"""BMS-DEV-57: real provider projection/validation, no provider IO or launch."""
from copy import deepcopy

import pytest

from services import msa_preparation as preparation
from services import msa_provider_setup as setup
import biomodstack_msa_api as api


COLABFOLD = {
    'colabfold_use_env': True,
    'colabfold_use_filter': True,
    'colabfold_use_templates': False,
    'colabfold_pairing_mode': 'unpaired',
    'colabfold_pairing_strategy': 'greedy',
}
NEUROSNAP = {
    'msa_neurosnap_coverage_percent': 35,
    'msa_neurosnap_identity_percent': 50,
    'msa_neurosnap_max_sequences': 1000,
    'msa_neurosnap_force_uppercase': False,
    'msa_neurosnap_pad_sequences': False,
}
TRANSPORT = {
    'colabfold_api_host': 'https://api.colabfold.com',
    'colabfold_api_min_interval': 1.0,
    'colabfold_api_poll_interval': 5.0,
}
LAUNCHER = {
    **COLABFOLD, **NEUROSNAP, **TRANSPORT,
    'colabfold_model_type': 'auto',
    'colabfold_num_recycle': 3,
    'colabfold_cache_dir': '/not-a-provider-setting',
    'colabfold_api_token': 'SECRET-TRANSPORT-VALUE',
    'colabfold_future_transport_option': 'SECRET-FUTURE-VALUE',
    'msa_cache_dir': '/not-authoritative',
    'credential_file': '/not-a-credential',
    'bcp_num_gpus': 4,
}
EXPECTED = {
    'colabfold_api': {key.removeprefix('colabfold_'): value for key, value in COLABFOLD.items()},
    'neurosnap_api': NEUROSNAP,
}


@pytest.mark.parametrize('provider', setup.PROVIDERS)
@pytest.mark.parametrize('argv_strings', [False, True])
def test_launcher_projection_stays_in_each_providers_closed_schema(provider, argv_strings):
    params = deepcopy(dict(LAUNCHER, msa_provider=provider))
    if argv_strings:
        params = {key: str(value).lower() if isinstance(value, bool)
                  else str(value) if isinstance(value, (int, float)) else value
                  for key, value in params.items()}
    before = deepcopy(params)
    settings = setup.provider_settings(params)
    assert params == before
    assert settings == EXPECTED[provider]
    effective = api.validate_settings(provider, settings)
    # Ask the existing validator for its expanded schema; do not add defaults.
    assert set(effective) == set(api.validate_settings(provider, {}))
    assert effective == api.validate_settings(provider, EXPECTED[provider])
    assert not set(settings).intersection(TRANSPORT)
    assert 'SECRET' not in repr(settings)


@pytest.mark.parametrize('key', list(TRANSPORT) + [
    'colabfold_model_type', 'colabfold_num_recycle', 'colabfold_api_token',
    'colabfold_cache_dir', 'colabfold_future_transport_option',
])
def test_non_scientific_colabfold_keys_do_not_enter_the_projection(key):
    params = dict(COLABFOLD, msa_provider='colabfold_api')
    params[key] = 'SECRET-UNVALIDATED-NONSCIENTIFIC-VALUE'
    settings = setup.provider_settings(params)
    assert settings == EXPECTED['colabfold_api']
    api.validate_settings('colabfold_api', settings)


def test_exact_reported_colabfold_regression_and_neurosnap_control():
    params = {**COLABFOLD, **TRANSPORT, **{key: value for key, value in NEUROSNAP.items()
              if key not in {'msa_neurosnap_force_uppercase', 'msa_neurosnap_pad_sequences'}}}
    for provider in setup.PROVIDERS:
        settings = setup.provider_settings(dict(params, msa_provider=provider))
        expected = (EXPECTED[provider] if provider == 'colabfold_api' else
                    {key: params[key] for key in setup.NEUROSNAP_FIELDS if key in params})
        assert settings == expected
        assert api.validate_settings(provider, settings) == api.validate_settings(provider, EXPECTED[provider])


@pytest.mark.parametrize('key,value', [
    ('colabfold_use_env', False), ('colabfold_use_filter', False),
    ('colabfold_use_templates', True), ('colabfold_pairing_mode', 'unpaired_paired'),
    ('colabfold_pairing_strategy', 'complete'),
])
def test_all_five_scientific_values_survive_without_silent_defaulting(key, value):
    params = dict(COLABFOLD, msa_provider='colabfold_api', **TRANSPORT)
    params[key] = value
    settings = setup.provider_settings(params)
    assert settings == {**EXPECTED['colabfold_api'], key.removeprefix('colabfold_'): value}
    if key == 'colabfold_use_templates':
        # Preserve the validator's existing unsupported-template refusal.
        with pytest.raises(api.MSAAPIError, match='template retrieval is not supported'):
            api.validate_settings('colabfold_api', settings)
    else:
        assert api.validate_settings('colabfold_api', settings)[key.removeprefix('colabfold_')] == value


@pytest.mark.parametrize('key', ['colabfold_use_env', 'colabfold_use_filter', 'colabfold_use_templates'])
def test_invalid_scientific_booleans_are_still_refused(key):
    with pytest.raises(ValueError, match='Invalid boolean MSA setting'):
        setup.provider_settings(dict(LAUNCHER, msa_provider='colabfold_api', **{key: 'maybe'}))


@pytest.mark.parametrize('provider', setup.PROVIDERS)
def test_transport_does_not_change_provider_request_identity(tmp_path, provider):
    client = api.MSAClient(config=api.ClientConfig(controller_root=tmp_path / 'authority'))
    params = dict(COLABFOLD, **NEUROSNAP, msa_provider=provider)
    clean = setup.provider_settings(params)
    transported = setup.provider_settings(dict(params, **TRANSPORT))
    sequences = ['ACDEFGHIKLMNPQRSTVWYA']
    assert client._identity(sequences, provider, clean) == client._identity(sequences, provider, transported)
    assert not (tmp_path / 'authority').exists()


@pytest.mark.parametrize('provider', setup.PROVIDERS)
def test_preparation_forwards_only_valid_science_with_launcher_transport(tmp_path, monkeypatch, provider):
    captured = []
    def cache_lookup(**kwargs):
        captured.append(kwargs)
        assert kwargs['provider'] == provider
        assert kwargs['settings'] == EXPECTED[provider]
        api.validate_settings(provider, kwargs['settings'])
        assert kwargs['cache_only'] is True
        return {'offline_fixture': True}
    monkeypatch.setattr(api, 'prepare_msa', cache_lookup)
    monkeypatch.setattr(setup, 'cache_root', lambda: tmp_path / 'not-created')
    monkeypatch.setattr(setup, 'credential_file', lambda: None)
    result = preparation.prepare_model_msa(sequences=['ACDEFGHIKLMNPQRSTVWYA'],
        params=dict(LAUNCHER, msa_provider=provider, msa_cache_only=True))
    assert result == {'offline_fixture': True}
    assert len(captured) == 1 and not (tmp_path / 'not-created').exists()


@pytest.mark.parametrize('provider', setup.PROVIDERS)
def test_closed_validator_names_offending_keys_but_not_values(provider):
    with pytest.raises(api.MSAAPIError) as caught:
        api.validate_settings(provider, {
            'colabfold_api_poll_interval': 'SECRET-POLL',
            'colabfold_api_host': 'https://private.invalid/?token=SECRET-HOST',
            'colabfold_api_min_interval': 'SECRET-MIN',
        })
    assert str(caught.value) == ('unsupported scientific setting keys: colabfold_api_host, '
        'colabfold_api_min_interval, colabfold_api_poll_interval')
    assert 'SECRET' not in str(caught.value) and 'private.invalid' not in str(caught.value)


@pytest.mark.parametrize('key', ['bad\nkey', 'https://private.invalid/?token=SECRET', 'x' * 1000, 42])
def test_unsupported_key_labels_are_bounded_and_not_raw_ui_text(key):
    with pytest.raises(api.MSAAPIError) as caught:
        api.validate_settings('colabfold_api', {key: 'SECRET-VALUE'})
    assert str(caught.value) == 'unsupported scientific setting keys: <invalid setting key>'


def test_many_unsupported_keys_have_bounded_sorted_diagnostics():
    settings = {f'unknown_{i:03d}': 'SECRET-VALUE' for i in reversed(range(100))}
    with pytest.raises(api.MSAAPIError) as caught:
        api.validate_settings('colabfold_api', settings)
    assert str(caught.value) == ('unsupported scientific setting keys: '
        + ', '.join(f'unknown_{i:03d}' for i in range(16)) + ' (+84 more)')
