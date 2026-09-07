"""API import adapter for the shared runtime/browser MSA policy."""
import importlib.util
from pathlib import Path

_source = Path(__file__).resolve().parents[3] / 'biomodstack_msa_policy.py'
_spec = importlib.util.spec_from_file_location('bms_shared_msa_policy', _source)
assert _spec is not None and _spec.loader is not None
_policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_policy)

POLICY = _policy.POLICY
LOCAL_DISABLED = _policy.LOCAL_DISABLED
DISCLOSURE = _policy.DISCLOSURE
requires_msa_search = _policy.requires_msa_search
apply_msa_policy = _policy.apply_msa_policy
resolve_search_backend = _policy.resolve_search_backend
reject_local_search = _policy.reject_local_search
