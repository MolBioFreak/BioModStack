"""Exact reported two-provider reproduction; run in the locked, isolated API env."""
import importlib.util
import json
from pathlib import Path
import sys
root = Path.cwd().parents[1]
sys.path[:0] = [str(root), str(root/'platform/api')]
from services import msa_provider_setup as setup
from biomodstack_msa_api import validate_settings, MSAAPIError
params = {
    'colabfold_use_env': True, 'colabfold_use_filter': True, 'colabfold_use_templates': False,
    'colabfold_pairing_mode': 'unpaired', 'colabfold_pairing_strategy': 'greedy',
    'colabfold_api_host': 'https://api.colabfold.com',
    'colabfold_api_min_interval': 1.0, 'colabfold_api_poll_interval': 5.0,
    'msa_neurosnap_coverage_percent': 35, 'msa_neurosnap_identity_percent': 50,
    'msa_neurosnap_max_sequences': 1000,
}
report = {}
for provider in setup.PROVIDERS:
    settings = setup.provider_settings(dict(params, msa_provider=provider))
    try:
        result = validate_settings(provider, settings)
        outcome = 'accepted'
    except MSAAPIError as exc:
        result = None
        outcome = str(exc)
    report[provider] = {'settings': settings, 'validation': result, 'outcome': outcome}
try:
    validate_settings('colabfold_api', {'colabfold_api_host': 'SECRET-NOT-FOR-DISPLAY',
        'colabfold_api_min_interval': 1.0, 'colabfold_api_poll_interval': 5.0})
except MSAAPIError as exc:
    report['refusal'] = str(exc)
Path(sys.argv[1]).write_text(json.dumps(report, indent=2)+'\n')
