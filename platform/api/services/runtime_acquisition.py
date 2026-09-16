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
from lib.pinned_weight_layout import materialize_weights, validate_members, _verify_tree
from lib.shared_runtime_images import verify_image


def _layouts(plan):
    groups = {}
    for entry in plan["artifacts"]:
        key = (entry["dependency"]["kind"], entry["dependency"]["relative_path"])
        groups.setdefault(key, []).append(entry)
    for group in groups.values():
        if any(e.get("member_path") is not None for e in group):
            yield group[0]["dependency"], [
                {"member_path": e.get("member_path"), "manifest": e["manifest"]}
                for e in group]


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
    for dependency, members in _layouts(plan):
        try:
            validate_members(dependency, members)
        except (AcquisitionError, TypeError, ValueError) as exc:
            plan["blockers"].append({"code": "invalid_weight_layout", "detail": str(exc)})
    canonical = json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()
    return {**plan, 'plan_digest': hashlib.sha256(canonical).hexdigest(),
            'qualification': 'not_checked'}


def revalidate_model_receipt(model_id, store_root, *, weights_root, expected_plan_digest,
                             receipt, test_only=False):
    """Offline verification, never acquire/repair/accept/activate.

    Paths are reconstructed from current authority, not read from saved receipts.
    Saved observations must match fresh no-follow hashes AND filesystem identity.
    test_only is a library fixture seam, never a supported CLI/config option.
    """
    plan = preview_model_acquisition(model_id)
    if plan['plan_digest'] != expected_plan_digest or plan['blockers']:
        raise AcquisitionError('acquisition authority changed or blocked')
    if (not isinstance(receipt, dict) or receipt.get('model_id') != model_id
            or receipt.get('plan_digest') != expected_plan_digest
            or not isinstance(receipt.get('artifacts'), list)
            or not isinstance(receipt.get('layouts'), list)
            or len(receipt['artifacts']) != len(plan['artifacts'])):
        raise AcquisitionError('invalid provision receipt identity')
    roots = {'image': Path(store_root).absolute(), 'weights': Path(weights_root).absolute()}
    if test_only:
        roots = {key: root / 'test-fixtures-not-scientific-assets' for key, root in roots.items()}
    artifacts = []
    for entry, saved in zip(plan['artifacts'], receipt['artifacts']):
        artifact = Artifact(**entry['manifest'])
        path = roots[artifact.kind] / 'objects' / 'sha256' / artifact.sha256 / 'runtime.sif'
        observation = verify_image(path, artifact.sha256)
        if observation['size'] != artifact.size_bytes:
            raise AcquisitionError('artifact byte size mismatch')
        fresh = {'dependency': entry['dependency'], 'artifact_id': artifact.artifact_id,
                 'kind': artifact.kind, 'path': str(path), 'manifest_digest': artifact.manifest_digest,
                 'verification': observation, 'test_only': test_only}
        if not isinstance(saved, dict) or any(saved.get(k) != v for k, v in fresh.items()):
            raise AcquisitionError('provision artifact path/bytes/identity drift')
        artifacts.append({**fresh, 'qualification': 'not_checked'})
    groups = list(_layouts(plan))
    if len(groups) != len(receipt['layouts']):
        raise AcquisitionError('invalid provision layout count')
    layouts = []
    for (dependency, entries), saved in zip(groups, receipt['layouts']):
        validate_members(dependency, entries, test_only=test_only)
        entries = sorted(entries, key=lambda e: e['member_path'])
        digest = hashlib.sha256(json.dumps({'dependency': dependency, 'members': entries},
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        path = roots['weights'] / 'layouts' / dependency['relative_path'] / digest
        fresh = {'dependency': dependency, 'path': str(path), 'layout_digest': digest,
                 'members': _verify_tree(path, entries, frozen=True), 'test_only': test_only}
        if not isinstance(saved, dict) or any(saved.get(k) != v for k, v in fresh.items()):
            raise AcquisitionError('provision layout path/bytes/identity drift')
        layouts.append({**fresh, 'qualification': 'not_checked'})
    return {'model_id': model_id, 'plan_digest': expected_plan_digest,
            'artifacts': artifacts, 'layouts': layouts, 'qualification': 'not_checked'}


def acquire_model(model_id: str, store_root: Path, *, expected_plan_digest: str,
                  accepted_licenses=(), attempts=3, timeout=30, total_timeout=300,
                  weights_root: Path | None = None) -> dict:
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
                          timeout=timeout, total_timeout=total_timeout, weights_root=weights_root)
        receipts.append({'dependency': entry['dependency'], **receipt})
    layouts = [materialize_weights(dependency, members, store_root,
                accepted_licenses=licenses, attempts=attempts, timeout=timeout,
                total_timeout=total_timeout, weights_root=weights_root) for dependency, members in _layouts(plan)]
    return {'model_id': model_id, 'plan_digest': plan['plan_digest'],
            'artifacts': receipts, 'layouts': layouts, 'qualification': 'not_checked'}
