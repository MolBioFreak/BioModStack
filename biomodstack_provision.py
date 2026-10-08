"""Supported setup byte provisioning. Never activates or qualifies a runtime.

The configuration lock stabilizes root selection for the whole operation. The
journal binds selection, registry plans, roots and explicit license acceptance.
Resume re-resolves authority and rehashes publications; saved receipts are not
trusted as a substitute for verification. Release consumers use receipt bindings,
not opaque weight member objects.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

import biomodstack_runtime_profile as profiles
from biomodstack_configuration import (
    configuration_lock, assert_configuration_readable, _write,
)


class ProvisionBlocked(RuntimeError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _authority(project_root):
    api = str(project_root / 'platform' / 'api')
    if api not in sys.path:
        sys.path.insert(0, api)
    from services import runtime_acquisition
    return runtime_acquisition


def _plan(project_root, models):
    assert_configuration_readable()
    profile_path = profiles.get_install_profile_path()
    profile = {}
    if profile_path.exists():
        raw = json.loads(profile_path.read_text())
        profiles.validate_install_profile_raw(raw)
        profile = profiles.normalize_install_profile(raw)
    paths = profiles.resolve_runtime_paths(project_root=project_root, profile=profile)
    roots = {key: str(paths[key]) for key in ('container_dir', 'weights_root')}
    image_root, weight_root = map(Path, roots.values())
    if image_root.is_relative_to(weight_root) or weight_root.is_relative_to(image_root):
        raise ProvisionBlocked('overlapping_artifact_stores: image and licensed weight stores must be disjoint')
    for value in roots.values():
        if Path(value).is_relative_to(project_root.resolve()):
            raise ProvisionBlocked('storage_in_source: configure external stores first')
    authority = _authority(project_root)
    plans = [authority.preview_model_acquisition(model) for model in sorted(set(models))]
    identities = {}
    for plan in plans:
        groups = {}
        for entry in plan['artifacts']:
            manifest = entry['manifest']
            key = (entry['dependency']['kind'], entry['dependency']['relative_path'])
            groups.setdefault(key, []).append(entry)
            identity = (manifest['kind'], manifest['artifact_id'])
            digest = _digest(manifest)
            if identity in identities and identities[identity] != digest:
                raise ProvisionBlocked('conflicting_artifact_identity: selected models disagree')
            identities[identity] = digest
        for (kind, name), entries in groups.items():
            if kind == 'weights' and any(not e.get('member_path') for e in entries):
                plan['blockers'].append({'code': 'weight_member_layout_required', 'relative_path': name})
            if kind == 'image' and (len(entries) != 1 or entries[0].get('member_path')):
                plan['blockers'].append({'code': 'ambiguous_image_binding', 'relative_path': name})
    plan = {'schema_version': 'bms.provision-plan.v1', 'source': str(project_root.resolve()),
            'selected_models': sorted(set(models)), 'store_roots': roots, 'models': plans}
    return {**plan, 'plan_digest': _digest(plan)}


def receipt_bindings(receipt):
    """Binding contract for a later release transaction; NOT qualification.

    Weight bindings must point at the fully verified directory, never runtime.sif
    objects containing individual member bytes. Preserve full receipts separately.
    """
    bindings = [{'dependency': item['dependency'], 'path': item['path']}
                for item in receipt['artifacts'] if item['kind'] == 'image']
    bindings.extend({'dependency': item['dependency'], 'path': item['path']}
                    for item in receipt['layouts'])
    return bindings


def _rows(plan):
    return [{'model_id': p['model_id'], 'status': 'blocked' if p['blockers'] else 'planned',
             'qualification': 'not-qualified', 'blockers': list(p['blockers'])}
            for p in plan['models']]


def provision_report(action, *, project_root, models=(), expected_plan_digest=None,
                     operation_id=None, accepted_licenses=()):
    report = {'schema_version': 'bms.provision.v1', 'action': action, 'status': 'blocked',
              'ready': False, 'qualification': 'not-qualified', 'registered': False,
              'models': [], 'blockers': []}
    try:
        if action not in {'provision-plan', 'provision', 'resume'}:
            raise ProvisionBlocked('unsupported provision action')
        if not models:
            raise ProvisionBlocked('model_selection_required: repeat --model MODEL')
        if action == 'provision-plan':
            plan = _plan(project_root, models)
            report.update(plan=plan, plan_digest=plan['plan_digest'], models=_rows(plan),
                          read_only=True, status='blocked' if any(p['blockers'] for p in plan['models']) else 'planned')
            return report
        if not expected_plan_digest or not re.fullmatch('[0-9a-f]{64}', expected_plan_digest):
            raise ProvisionBlocked('expected_plan_required: use --expect-plan-sha256 from provision-plan')
        if not operation_id or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', operation_id):
            raise ProvisionBlocked('operation_identity_required: use --operation-id (1-80 safe characters)')
        report['operation_id'] = operation_id
        with configuration_lock():
            plan = _plan(project_root, models)
            report.update(plan_digest=plan['plan_digest'], models=_rows(plan))
            if expected_plan_digest != plan['plan_digest']:
                raise ProvisionBlocked('stale_plan: selection, registry authority or configured stores changed')
            authority = _authority(project_root)
            from lib.shared_runtime_images import _file
            journal_path = profiles.get_biomodstack_config_dir() / 'provision-v1' / operation_id / 'journal.json'
            if action == 'resume':
                try:
                    with _file(journal_path) as (fd, _, info):
                        if info.st_nlink != 1:
                            raise ProvisionBlocked('unsafe provision journal')
                        with os.fdopen(os.dup(fd)) as stream:
                            journal = json.load(stream)
                except FileNotFoundError:
                    raise ProvisionBlocked('operation_not_found: resume requires an existing journal') from None
                if (journal.get('schema_version') != 'bms.provision-journal.v1'
                        or journal.get('operation_id') != operation_id
                        or journal.get('plan') != plan):
                    raise ProvisionBlocked('journal_plan_mismatch: explicit reconciliation required')
                acceptance = journal.get('license_acceptance', {})
                licenses = acceptance.get('licenses')
                if (not isinstance(licenses, list) or not all(isinstance(x, str) for x in licenses)
                        or acceptance.get('plan_digest') != plan['plan_digest']
                        or not acceptance.get('recorded_at')):
                    raise ProvisionBlocked('invalid_license_acceptance_record')
                if accepted_licenses and sorted(set(accepted_licenses)) != licenses:
                    raise ProvisionBlocked('license_acceptance_changed: start a new operation')
            else:
                if os.path.lexists(journal_path):
                    raise ProvisionBlocked('operation_exists: use resume, never overwrite acceptance')
                licenses = sorted(set(accepted_licenses))
                journal = {'schema_version': 'bms.provision-journal.v1', 'operation_id': operation_id,
                           'plan': plan, 'license_acceptance': {'licenses': licenses,
                           'plan_digest': plan['plan_digest'],
                           'recorded_at': datetime.now(timezone.utc).isoformat()},
                           'models': report['models'], 'events': []}
                _write(journal_path, json.dumps(journal, sort_keys=True))
            # Rebuild results on EVERY resume. Never return prior success without rehashing.
            for row, model_plan in zip(report['models'], plan['models']):
                missing = sorted({e['manifest']['license_id'] for e in model_plan['artifacts']
                                  if e['manifest']['kind'] == 'weights'} - set(licenses))
                row['blockers'].extend({'code': 'license_acceptance_required', 'license_id': x} for x in missing)
                if row['blockers']:
                    row['status'] = 'blocked'
                else:
                    row['status'] = 'materializing'
                    journal['models'] = report['models']
                    journal['events'].append({'model_id': row['model_id'], 'status': 'materializing'})
                    _write(journal_path, json.dumps(journal, sort_keys=True), replace=True)
                    try:
                        receipt = authority.acquire_model(row['model_id'], Path(plan['store_roots']['container_dir']),
                            weights_root=Path(plan['store_roots']['weights_root']),
                            expected_plan_digest=model_plan['plan_digest'], accepted_licenses=licenses)
                        row.update(status='bytes-materialized', receipt=receipt, bindings=receipt_bindings(receipt))
                    except (RuntimeError, OSError, ValueError, TypeError) as exc:
                        row.update(status='blocked', blockers=[{'code': 'materialization_failed', 'detail': str(exc)}])
                journal['models'] = report['models']
                journal['events'].append({'model_id': row['model_id'], 'status': row['status']})
                _write(journal_path, json.dumps(journal, sort_keys=True), replace=True)
            report['journal_path'] = str(journal_path)
            report['status'] = 'bytes-materialized' if all(r['status'] == 'bytes-materialized' for r in report['models']) else 'blocked'
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        report['blockers'].append({'code': 'provision_blocked', 'detail': str(exc)})
        for row in report['models']:
            row['status'] = 'blocked'
    return report
