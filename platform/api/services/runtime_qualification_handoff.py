"""Read-only bridge to existing Protenix evidence validation, NOT registration.

An observed execution attestation is a prerequisite to native-result acceptance,
not a scientific qualification record. Never promote its status to ready, or
write a runtime registry from an operator-supplied document.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from lib.shared_runtime_images import _file, _check_file


PROTENIX_REQUIRED_EVIDENCE = [
    {'authority': 'schemas/conformational_mapping/cm_protenix_runtime_attestation_v1.schema.json',
     'required': 'Observed image host receipt, checkpoint/executed-wrapper snapshot receipt, backend source manifest/commit/version, command, times and global artifact roles; bound to these provisioned identities.'},
    {'authority': 'services.conformational_mapping.protenix.finalize_protenix',
     'required': 'Actual canonical request and ordered snapshots, native output files and coordinate ledger; validated composition, modifications/bonds, confidence, full seed/sample coverage and global artifacts.'},
    {'authority': 'services.conformational_mapping.contracts.validate_contract_bundle',
     'required': 'Consistent native-artifacts/ensemble/analysis/handoff bundle with exact request, runtime attestation and artifact hashes.'},
    {'authority': 'services.conformational_mapping.persistence',
     'required': 'Existing managed job/result ingestion and persisted native/ensemble evidence; setup does not execute or bypass this authority.'},
    {'authority': 'docs/Model_Configuration_Operator_Control_and_Agent_Parity.md',
     'required': 'Release-owner scientific acceptance for exact released bytes, supported modes, full effective settings, UI/API parity, persistence and global results; an attestation alone does not satisfy these gates.'},
]


def _read_evidence(path):
    with _file(Path(path)) as (fd, parent, before):
        if before.st_nlink != 1 or before.st_size > 4 * 1024 * 1024:
            raise ValueError('attestation must be a single-link JSON file at most 4 MiB')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            payload = stream.read(4 * 1024 * 1024 + 1)
        _check_file(Path(path), fd, parent, before)
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError('attestation exceeds 4 MiB')
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError('attestation must be an object')
    return value, hashlib.sha256(payload).hexdigest()


def check_evidence(model_id, receipt, *, attestation_path=None):
    result = {'status': 'validator-blocked', 'qualification': 'not-qualified',
              'scientifically_qualified': False, 'registered': False, 'ready': False,
              'validator': {'status': 'not-run'}, 'blockers': [],
              'registration': {'status': 'not-performed',
                               'reason': 'No installer registration authority; existing managed scientific admission/result authorities remain required.'}}
    if model_id != 'protenix':
        result['blockers'].append({'code': 'qualification_handoff_unsupported', 'model_id': model_id})
        return result
    result['required_evidence'] = PROTENIX_REQUIRED_EVIDENCE
    result['validator']['authority'] = 'services.conformational_mapping.protenix._validate_runtime_attestation'
    try:
        from services.conformational_mapping.protenix import _validate_runtime_attestation
        runtime = {}
        if attestation_path is not None:
            runtime, digest = _read_evidence(attestation_path)
            result['validator']['evidence'] = {'path': str(attestation_path), 'sha256': digest}
        # This is the same validator invoked by the native-output finalizer.
        # Missing evidence also traverses it; never substitute a hash-only pass.
        _validate_runtime_attestation(runtime)
        result['validator']['status'] = 'passed'
        images = [a for a in receipt['artifacts'] if a['kind'] == 'image']
        layouts = [a for a in receipt['layouts'] if a['dependency']['relative_path'] == 'protenix']
        if len(images) != 1 or len(layouts) != 1 or runtime['model_id'] != 'protenix-v2':
            raise ValueError('attestation model/dependency identity mismatch')
        image, weights = images[0], layouts[0]
        measured = image['verification']
        snapshot = runtime['runtime_image']['host_observed_source']
        if (runtime['runtime_image']['sha256'] != measured['sha256']
                or runtime['runtime_image']['bytes'] != measured['size']
                or snapshot.get('path') != image['path']
                or any(snapshot.get(key) != measured[key] for key in ('device', 'inode'))):
            raise ValueError('attestation image path/bytes/identity mismatch')
        checkpoint = runtime['checkpoint']
        member = weights['members'].get(checkpoint['relative_path'])
        source = runtime['execution_snapshot']['receipt']['checkpoint']
        if (not member or source['source_path'] != checkpoint['relative_path']
                or checkpoint['sha256'] != member['sha256'] or checkpoint['bytes'] != member['size']
                or any(source['observed_source'].get(key) != member[key] for key in ('sha256', 'device', 'inode'))
                or source['observed_source'].get('bytes') != member['size']):
            raise ValueError('attestation checkpoint member/path/bytes/identity mismatch')
        result['validator']['provision_binding'] = 'matched'
        result['blockers'].append({'code': 'scientific_qualification_evidence_required',
                                  'detail': 'Attestation validation passed, not native scientific-result acceptance or model qualification.'})
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        code = 'attestation_binding_failed' if result['validator']['status'] == 'passed' else 'attestation_validation_failed'
        result['validator']['status'] = 'blocked'
        result['blockers'].append({'code': code, 'detail': str(exc)})
    result['blockers'].append({'code': 'registration_not_authorized',
                              'detail': result['registration']['reason']})
    return result
