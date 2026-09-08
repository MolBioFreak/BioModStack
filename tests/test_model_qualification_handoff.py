"""TEST ONLY: tiny bytes and fabricated execution evidence, NEVER science.

Actual shell/manager, image/layout revalidators and Protenix attestation builder
and validator run. No backend, job, service or production registry is invoked.
"""
import hashlib
import json
from pathlib import Path

import pytest

from test_provision_cli import cli, identity, ROOT


@pytest.fixture
def materialized(cli):
    run, handler, root, env, fixture = cli
    args = identity(run)
    code, report = run('provision', *args, '--accept-license', 'TEST-LICENSE')
    assert code == 0, report
    return args, report


def _test_only_attestation(root, row, monkeypatch):
    for p in (ROOT, ROOT / 'platform/api', ROOT / 'scripts'):
        monkeypatch.syspath_prepend(str(p))
    from prepare_runtime_image_attestation import create_verified_image_reference
    from prepare_protenix_execution_snapshot import prepare_execution_snapshot
    from attest_protenix_runtime import build_runtime_attestation
    from services.conformational_mapping.protenix import _validate_runtime_attestation

    evidence = root / 'TEST-ONLY-NOT-SCIENTIFIC-EVIDENCE'
    evidence.mkdir()
    image = next(a for a in row['receipt']['artifacts'] if a['kind'] == 'image')
    weights = row['receipt']['layouts'][0]
    image_path = Path(image['path'])
    image_receipt = evidence / 'image-receipt.json'
    create_verified_image_reference(image=image_path, expected_sha256=image['verification']['sha256'],
        store_root=image_path.parents[3], reference=evidence / 'reference.json', receipt=image_receipt)
    # This is an isolated builder INPUT, not an admitted model registry.
    registry = {'schema_name': 'cm_runtime_registry', 'schema_version': 1,
        'backend_version': 'TEST-ONLY', 'backend_commit': 'b' * 40,
        'runtime_identity': 'TEST-ONLY', 'container_digest': 'sha256:' + image['verification']['sha256'],
        'checkpoint_sha256': weights['members']['checkpoint/protenix-v2.pt']['sha256'],
        'checkpoint_relative_path': 'checkpoint/protenix-v2.pt', 'model_id': 'protenix-v2'}
    registry_path = evidence / 'builder-input-NOT-REGISTERED.json'
    registry_path.write_text(json.dumps(registry))
    wrapper = evidence / 'test-only-wrapper.py'
    wrapper.write_text('# TEST ONLY; not executed\n')
    execution_root = evidence / 'snapshot'
    execution_receipt = evidence / 'execution-receipt.json'
    prepare_execution_snapshot(registry_path=registry_path, weights_root=Path(weights['path']),
        wrapper=wrapper, runtime_root=execution_root, receipt_path=execution_receipt)
    source = evidence / 'test-only-source'
    source.mkdir()
    (source / '__init__.py').write_text('# TEST ONLY; not executed\n')
    roles = ('runtime_input', 'feature_policy', 'log', 'runtime_config', 'composition_audit',
             'coordinate_ledger', 'coordinate_context', 'preprocessing_record', 'msa_record',
             'template_record', 'runtime_attestation', 'runtime_image_receipt', 'execution_snapshot_receipt')
    runtime = build_runtime_attestation(registry=registry, image_receipt_path=image_receipt,
        runtime_image=image_path, checkpoint=execution_root / 'checkpoint/protenix-v2.pt',
        source_roots=[source], direct_url={'vcs_info': {'vcs': 'git', 'commit_id': 'b' * 40}},
        distribution_version='TEST-ONLY', wrapper=execution_root / 'bms-wrapper/run_protenix_inference.py',
        execution_receipt_path=execution_receipt, command=['TEST-ONLY-NOT-EXECUTED'],
        global_artifacts=[{'semantic_role': r, 'relative_path': f'runtime/{r}.json'} for r in roles])
    _validate_runtime_attestation(runtime)  # real authority; no monkeypatched verdict
    path = evidence / 'attestation.json'
    path.write_text(json.dumps(runtime))
    return path


@pytest.mark.parametrize('cli', ['protenix'], indirect=True)
def test_verify_runs_existing_validator_missing_evidence(cli, materialized):
    run, handler, root, env, fixture = cli
    args, provisioned = materialized
    journal = Path(provisioned['journal_path'])
    before, requests = journal.read_bytes(), list(handler.requests)
    code, report = run('verify', *args)
    assert code == 3 and report['status'] == 'validator-blocked', report
    row = report['models'][0]
    assert row['bytes_materialized'] and row['validator']['status'] == 'blocked'
    assert 'complete observed runtime attestation' in str(row)
    assert row['required_evidence'] and not row['scientifically_qualified'] and not row['registered']
    assert not report['ready']
    assert journal.read_bytes() == before and handler.requests == requests


@pytest.mark.parametrize('cli', ['protenix'], indirect=True)
def test_validator_pass_is_not_scientific_qualification_or_registration(cli, materialized, monkeypatch):
    run, handler, root, env, fixture = cli
    args, provisioned = materialized
    evidence = _test_only_attestation(root, provisioned['models'][0], monkeypatch)
    journal = Path(provisioned['journal_path'])
    before, requests = journal.read_bytes(), list(handler.requests)
    code, report = run('verify', *args, '--runtime-attestation', str(evidence))
    assert code == 3 and report['status'] == 'validator-blocked', report
    row = report['models'][0]
    assert row['validator']['status'] == 'passed', row['blockers']
    assert row['validator']['provision_binding'] == 'matched'
    assert row['bytes_materialized'] and row['qualification'] == 'not-qualified'
    assert not row['scientifically_qualified'] and not row['registered'] and not report['ready']
    assert {b['code'] for b in row['blockers']} == {'scientific_qualification_evidence_required', 'registration_not_authorized'}
    assert journal.read_bytes() == before and handler.requests == requests


@pytest.mark.parametrize('cli', ['protenix'], indirect=True)
@pytest.mark.parametrize('drift', ['image-bytes', 'member-bytes', 'image-inode', 'member-inode',
                                  'image-path', 'layout-path', 'bindings', 'missing', 'symlink'])
def test_verify_invalidates_provision_drift(cli, materialized, drift):
    run, handler, root, env, fixture = cli
    args, provisioned = materialized
    journal = Path(provisioned['journal_path'])
    data = json.loads(journal.read_text())
    row = data['models'][0]
    image = Path(row['receipt']['artifacts'][0]['path'])
    member = Path(row['receipt']['layouts'][0]['path']) / 'checkpoint/protenix-v2.pt'
    if drift in {'image-bytes', 'member-bytes'}:
        path = image if drift.startswith('image') else member
        path.chmod(0o600)
        path.write_bytes(b'X' * len(handler.payload))  # equal-size mutation
        path.chmod(0o400)
    elif drift in {'image-inode', 'member-inode'}:
        path = image if drift.startswith('image') else member
        path.parent.chmod(0o700)
        replacement = path.with_name('replacement')
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o400)
        replacement.replace(path)
        path.parent.chmod(0o500)
    elif drift == 'missing':
        member.parent.chmod(0o700)
        member.unlink()
        member.parent.chmod(0o500)
    elif drift == 'symlink':
        image.parent.chmod(0o700)
        copy = root / 'copy.sif'
        copy.write_bytes(image.read_bytes())
        image.unlink()
        image.symlink_to(copy)
        image.parent.chmod(0o500)
    else:
        if drift == 'image-path':
            row['receipt']['artifacts'][0]['path'] = str(root / 'elsewhere.sif')
        elif drift == 'layout-path':
            row['receipt']['layouts'][0]['path'] = str(root / 'elsewhere')
        else:
            row['bindings'][0]['path'] = str(root / 'elsewhere.sif')
        journal.write_text(json.dumps(data))
    requests, before = list(handler.requests), journal.read_bytes()
    code, report = run('verify', *args)
    assert code == 3 and report['status'] == 'blocked', report
    assert not report['models'][0]['bytes_materialized']
    assert report['models'][0]['blockers'][0]['code'] == 'provision_revalidation_failed'
    assert 'validator' not in report['models'][0]
    assert handler.requests == requests and journal.read_bytes() == before


@pytest.mark.parametrize('cli', ['protenix'], indirect=True)
@pytest.mark.parametrize('change', ['malformed', 'array', 'unknown-key', 'digest', 'image-path', 'checkpoint-path'])
def test_evidence_is_not_an_approval_document(cli, materialized, monkeypatch, change):
    run, handler, root, env, fixture = cli
    args, provisioned = materialized
    path = _test_only_attestation(root, provisioned['models'][0], monkeypatch)
    data = json.loads(path.read_text())
    if change == 'malformed':
        path.write_text('{')
    elif change == 'array':
        path.write_text('[]')
    else:
        if change == 'unknown-key':
            data['approved'] = True
        elif change == 'digest':
            data['attestation_sha256'] = '0' * 64
        elif change == 'image-path':
            data['runtime_image']['host_observed_source']['path'] = str(root / 'other.sif')
        else:
            data['checkpoint']['relative_path'] = 'other-checkpoint.pt'
        if change != 'digest':
            data['attestation_sha256'] = hashlib.sha256(json.dumps(
                {k: v for k, v in data.items() if k != 'attestation_sha256'},
                sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        path.write_text(json.dumps(data))
    code, report = run('verify', *args, '--runtime-attestation', str(path))
    assert code == 3 and report['models'][0]['validator']['status'] == 'blocked', report
    assert not report['ready'] and not report['registered']


def test_unsupported_model_does_not_gain_qualification(cli, materialized):
    args, _ = materialized
    code, report = cli[0]('verify', *args)
    assert code == 3 and report['models'][0]['bytes_materialized'], report
    assert report['models'][0]['blockers'][0]['code'] == 'qualification_handoff_unsupported'


@pytest.mark.parametrize('cli', ['protenix'], indirect=True)
@pytest.mark.parametrize('shape', ['missing-receipt', 'list-receipt', 'missing-models', 'duplicate-models', 'forged-ready'])
def test_saved_verdict_is_not_authority(cli, materialized, shape):
    args, provisioned = materialized
    journal = Path(provisioned['journal_path'])
    data = json.loads(journal.read_text())
    if shape == 'missing-receipt':
        data['models'][0].pop('receipt')
    elif shape == 'list-receipt':
        data['models'][0]['receipt'] = []
    elif shape == 'missing-models':
        data.pop('models')
    elif shape == 'duplicate-models':
        data['models'] *= 2
    else:
        data['models'][0].update(qualification='scientifically-qualified',
                                scientifically_qualified=True, registered=True, ready=True)
    journal.write_text(json.dumps(data))
    before = journal.read_bytes()
    code, report = cli[0]('verify', *args)
    assert code == 3 and not report['ready'] and not report['registered']
    assert all(not row['scientifically_qualified'] and not row['registered'] for row in report['models'])
    if shape == 'forged-ready':
        assert report['models'][0]['validator']['status'] == 'blocked'
    assert journal.read_bytes() == before


@pytest.mark.parametrize('cli', ['protenix'], indirect=True)
@pytest.mark.parametrize('change', ['roots', 'selection', 'metadata'])
def test_verify_stale_plan_does_not_repair_or_fetch(cli, materialized, change):
    run, handler, root, env, fixture = cli
    args, provisioned = materialized
    if change == 'roots':
        env['BMS_WEIGHTS'] = str(root / 'new-weights')
    elif change == 'selection':
        args += ['--model', 'unknown']
    else:
        data = json.loads(fixture.read_text())
        data['entries'][0]['approval_ref'] = 'CHANGED-TEST-ONLY'
        fixture.write_text(json.dumps(data))
    before = list(handler.requests)
    code, report = run('verify', *args)
    assert code == 3 and 'stale_plan' in str(report)
    assert handler.requests == before


@pytest.mark.parametrize('cli', ['protenix'], indirect=True)
def test_verify_real_registry_remains_blocked(cli):
    run = cli[0]
    code, plan = run('provision-plan', production=True)
    assert code == 3
    code, report = run('verify', '--expect-plan-sha256', plan['plan_digest'],
                       '--operation-id', 'not-provisioned', production=True)
    assert code == 3 and not report['ready'] and not report['registered']
    assert 'approved_acquisition_metadata_missing' in str(report)
    assert not cli[1].requests
