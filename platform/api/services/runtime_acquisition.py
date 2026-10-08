"""Setup-facing acquisition API. Does not configure/activate/qualify models."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from model_registry import model_acquisition_plan

_SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib.pinned_acquisition import AcquisitionError, Artifact, acquire


def preview_model_acquisition(model_id: str) -> dict:
    """Read the existing registry only; validate all manifests before any IO."""
    plan = model_acquisition_plan(model_id)
    for entry in plan['artifacts']:
        try:
            Artifact(**entry['manifest']).validate()
        except (AcquisitionError, TypeError, ValueError) as exc:
            plan['blockers'].append({'code': 'invalid_acquisition_metadata',
                                     'artifact_id': entry['manifest']['artifact_id'],
                                     'detail': str(exc)})
    canonical = json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()
    return {**plan, 'plan_digest': hashlib.sha256(canonical).hexdigest(),
            'qualification': 'not_checked'}


def acquire_model(model_id: str, store_root: Path, *, expected_plan_digest: str,
                  accepted_licenses=(), attempts=3, timeout=30, total_timeout=300) -> dict:
    """Re-resolve trusted metadata and bind to preview; never accept URLs from UI.

    Return durable per-artifact receipts. A partial model failure is not success;
    rerunning rehashes/reuses completed artifacts. Exceptions leave resumable
    checkpoints, never configuration writes or a scientific-ready verdict.
    """
    plan = preview_model_acquisition(model_id)
    if plan['plan_digest'] != expected_plan_digest:
        raise AcquisitionError('acquisition plan changed since preview')
    if plan['blockers']:
        raise AcquisitionError(json.dumps(plan['blockers'], sort_keys=True))
    licenses = frozenset(accepted_licenses)
    for entry in plan['artifacts']:
        item = Artifact(**entry['manifest'])
        if item.kind == 'weights' and item.license_id not in licenses:
            raise AcquisitionError(f'license acceptance required: {item.license_id}')
    receipts = []
    for entry in plan['artifacts']:
        receipt = acquire(Artifact(**entry['manifest']), store_root,
                          accepted_licenses=licenses, attempts=attempts,
                          timeout=timeout, total_timeout=total_timeout)
        receipts.append({'dependency': entry['dependency'], **receipt})
    return {'model_id': model_id, 'plan_digest': plan['plan_digest'],
            'artifacts': receipts, 'qualification': 'not_checked'}
