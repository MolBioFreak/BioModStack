"""Tiny native helper/module-shell qualification; never imports or runs inference."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_blocks)


def fixture(tmp_path):
    weights = tmp_path / 'installed'
    checkpoint = weights / 'checkpoint/protenix-v2.pt'
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b'tiny selected checkpoint\x00\x01')
    checkpoint.chmod(0o444)
    for member in ('common/ccd.cif', 'mmcif/template.cif', 'search_database/index'):
        target = weights / member
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(member.encode())
        target.chmod(0o444)
    wrapper = tmp_path / 'run_protenix_inference.py'
    wrapper.write_text('raise AssertionError("inference must never run")\n')
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'checkpoint_relative_path': 'checkpoint/protenix-v2.pt',
                                    'checkpoint_sha256': sha(checkpoint)}))
    return weights, checkpoint, wrapper, registry


def prepare(tmp_path, weights, wrapper, registry):
    module = load('prepare_protenix_execution_snapshot')
    runtime = tmp_path / 'execution-snapshot'
    receipt = tmp_path / 'receipt.json'
    result = module.prepare_execution_snapshot(registry_path=registry, weights_root=weights,
        wrapper=wrapper, runtime_root=runtime, receipt_path=receipt)
    return runtime, receipt, result


@pytest.mark.parametrize('alias', ['none', 'root', 'directory', 'leaf', 'hardlink'])
def test_aliases_use_one_selected_referent_and_existing_receipt(tmp_path, alias):
    weights, checkpoint, wrapper, registry = fixture(tmp_path)
    source = checkpoint
    if alias == 'root':
        weights = tmp_path / 'root-alias'
        weights.symlink_to(checkpoint.parents[1], target_is_directory=True)
    elif alias == 'directory':
        actual = checkpoint.parent.with_name('actual-checkpoint')
        checkpoint.parent.rename(actual)
        checkpoint.parent.symlink_to(actual, target_is_directory=True)
        source = actual / checkpoint.name
    elif alias == 'leaf':
        source = checkpoint.with_name('payload.pt')
        checkpoint.rename(source)
        checkpoint.symlink_to(source)
    elif alias == 'hardlink':
        os.link(checkpoint, checkpoint.with_name('another-name.pt'))
    before = source.stat()
    runtime, receipt, result = prepare(tmp_path / 'task', weights, wrapper, registry)
    assert list(runtime.rglob('*.pt')) == []
    assert result['checkpoint']['observed_source']['inode'] == before.st_ino
    assert result['checkpoint']['verified_snapshot'] == {
        'sha256': sha(source), 'bytes': before.st_size, 'mode': 0o444}
    assert source.stat() == before
    assert (runtime / 'bms-wrapper/run_protenix_inference.py').read_bytes() == wrapper.read_bytes()
    assert (runtime / 'bms-wrapper/run_protenix_inference.py').stat().st_ino != wrapper.stat().st_ino
    schema = json.loads((ROOT / 'schemas/conformational_mapping/cm_protenix_execution_snapshot_v1.schema.json').read_text())
    Draft202012Validator(schema).validate(result)
    receiver = load('attest_protenix_runtime')
    received = receiver._validate_execution_receipt(receipt, weights / 'checkpoint/protenix-v2.pt',
                                                   runtime / 'bms-wrapper/run_protenix_inference.py')
    assert received['receipt'] == result


def test_checkpoint_alias_cannot_escape_selected_root(tmp_path):
    weights, checkpoint, wrapper, registry = fixture(tmp_path)
    outside = tmp_path / 'outside.pt'
    checkpoint.rename(outside)
    checkpoint.symlink_to(outside)
    module = load('prepare_protenix_execution_snapshot')
    with pytest.raises(ValueError, match='escapes'):
        prepare(tmp_path / 'task', weights, wrapper, registry)


def test_selected_concrete_member_is_not_resolved_twice(tmp_path, monkeypatch):
    weights, checkpoint, wrapper, registry = fixture(tmp_path)
    module = load('prepare_protenix_execution_snapshot')
    original_source = module._checkpoint_source
    outside = tmp_path / 'outside.pt'
    outside.write_bytes(checkpoint.read_bytes())
    def swapped_source(*args):
        result = original_source(*args)
        checkpoint.unlink()
        checkpoint.symlink_to(outside)
        return result
    monkeypatch.setattr(module, '_checkpoint_source', swapped_source)
    with pytest.raises(module.ProtenixExecutionSnapshotError, match='cannot open'):
        module.prepare_execution_snapshot(registry_path=registry, weights_root=weights, wrapper=wrapper,
            runtime_root=tmp_path / 'task', receipt_path=tmp_path / 'receipt.json')
    assert not (tmp_path / 'receipt.json').exists()


@pytest.mark.parametrize('old', [False, True])
def test_old_and_new_receipts_readback_and_hash_negatives(tmp_path, old):
    weights, checkpoint, wrapper, registry = fixture(tmp_path)
    runtime, receipt, result = prepare(tmp_path / 'task', weights, wrapper, registry)
    consumed = checkpoint
    if old:
        # The historical v1 representation carried a physical checkpoint snapshot.
        consumed = runtime / 'checkpoint/protenix-v2.pt'
        consumed.parent.mkdir()
        shutil.copyfile(checkpoint, consumed)
        consumed.chmod(0o444)
    receiver = load('attest_protenix_runtime')
    wrapper_snapshot = runtime / 'bms-wrapper/run_protenix_inference.py'
    assert receiver._validate_execution_receipt(receipt, consumed, wrapper_snapshot)['receipt'] == result
    consumed.chmod(0o644)
    consumed.write_bytes(b'changed checkpoint')
    with pytest.raises(receiver.ProtenixRuntimeAttestationError, match='checkpoint bytes differ'):
        receiver._validate_execution_receipt(receipt, consumed, wrapper_snapshot)
    consumed.write_bytes(b'tiny selected checkpoint\x00\x01')
    wrapper_snapshot.chmod(0o644)
    wrapper_snapshot.write_text('different wrapper')
    with pytest.raises(receiver.ProtenixRuntimeAttestationError, match='wrapper bytes differ'):
        receiver._validate_execution_receipt(receipt, consumed, wrapper_snapshot)


def test_producer_wrong_registered_digest_emits_no_receipt(tmp_path):
    weights, checkpoint, wrapper, registry = fixture(tmp_path)
    payload = json.loads(registry.read_text())
    payload['checkpoint_sha256'] = '0' * 64
    registry.write_text(json.dumps(payload))
    module = load('prepare_protenix_execution_snapshot')
    with pytest.raises(ValueError, match='digest mismatch'):
        prepare(tmp_path / 'task', weights, wrapper, registry)
    assert not (tmp_path / 'task/receipt.json').exists()
    assert not list((tmp_path / 'task').rglob('*.pt'))


@pytest.mark.parametrize('reader', ['producer', 'receiver'])
@pytest.mark.parametrize('change', ['hardlink', 'replace', 'mutate'])
def test_pinned_measurement_keeps_byte_and_path_checks(tmp_path, monkeypatch, reader, change):
    weights, checkpoint, wrapper, registry = fixture(tmp_path)
    module = load('prepare_protenix_execution_snapshot' if reader == 'producer' else 'attest_protenix_runtime')
    original_read = os.read
    changed = False
    before = checkpoint.stat()
    expected = sha(checkpoint)

    def read(fd, size):
        nonlocal changed
        result = original_read(fd, size)
        if not changed and os.fstat(fd).st_ino == before.st_ino:
            changed = True
            if change == 'hardlink':
                os.link(checkpoint, weights / 'concurrent-reference.pt')
            elif change == 'replace':
                replacement = weights / 'replacement.pt'
                replacement.write_bytes(checkpoint.read_bytes())
                replacement.chmod(0o444)
                os.replace(replacement, checkpoint)
            else:
                checkpoint.chmod(0o644)
                checkpoint.write_bytes(b'X' * before.st_size)
                checkpoint.chmod(0o444)
        return result

    monkeypatch.setattr(module.os, 'read', read)
    def measure():
        if reader == 'producer':
            return module.snapshot_opened_file(source=checkpoint, destination=None, expected_sha256=expected)
        return module._sha256_file(checkpoint, immutable=True)
    if change == 'hardlink':
        measure()
        assert checkpoint.stat().st_ino == before.st_ino
    else:
        with pytest.raises((ValueError, RuntimeError), match='changed'):
            measure()
    assert changed


@pytest.mark.parametrize('reader', ['producer', 'receiver'])
def test_alias_retarget_during_read_measures_pinned_concrete_referent(tmp_path, monkeypatch, reader):
    weights, checkpoint, wrapper, registry = fixture(tmp_path)
    alias = weights / 'alias.pt'
    alias.symlink_to(checkpoint)
    other = weights / 'other.pt'
    other.write_bytes(b'not selected')
    module = load('prepare_protenix_execution_snapshot' if reader == 'producer' else 'attest_protenix_runtime')
    expected = sha(checkpoint)
    original_read = os.read
    changed = False
    def read(fd, size):
        nonlocal changed
        data = original_read(fd, size)
        if not changed:
            alias.unlink()
            alias.symlink_to(other)
            changed = True
        return data
    monkeypatch.setattr(module.os, 'read', read)
    if reader == 'producer':
        result = module.snapshot_opened_file(source=alias, destination=None, expected_sha256=expected)
        assert result['observed_source']['sha256'] == expected
    else:
        assert module._sha256_file(alias, immutable=True)[0] == expected
    assert alias.resolve() == other


def test_measurement_allocates_no_payload_even_without_clone_support(tmp_path, monkeypatch):
    _, checkpoint, _, _ = fixture(tmp_path)
    module = load('prepare_protenix_execution_snapshot')
    expected = sha(checkpoint)
    def forbidden(*args, **kwargs):
        raise AssertionError('checkpoint measurement must never create/write a payload')
    monkeypatch.setattr(module.tempfile, 'mkstemp', forbidden)
    monkeypatch.setattr(module.os, 'write', forbidden)
    monkeypatch.setattr(module.os, 'link', forbidden)
    result = module.snapshot_opened_file(source=checkpoint, destination=None, expected_sha256=expected)
    assert result['verified_snapshot']['sha256'] == expected


def test_mutable_wrapper_retains_ctime_change_protection(tmp_path, monkeypatch):
    _, _, wrapper, _ = fixture(tmp_path)
    module = load('prepare_protenix_execution_snapshot')
    original_read = os.read
    changed = False
    def read(fd, size):
        nonlocal changed
        data = original_read(fd, size)
        if not changed:
            os.link(wrapper, tmp_path / 'new-wrapper-link')
            changed = True
        return data
    monkeypatch.setattr(module.os, 'read', read)
    with pytest.raises(module.ProtenixExecutionSnapshotError, match='changed'):
        module.snapshot_opened_file(source=wrapper, destination=tmp_path / 'snapshot.py', expected_sha256=None)
    assert not (tmp_path / 'snapshot.py').exists()


@pytest.mark.parametrize('reader', ['prepare_protenix_execution_snapshot', 'attest_protenix_runtime'])
def test_staged_json_alias_read_checks_same_concrete_path(tmp_path, reader):
    target = tmp_path / 'record.json'
    target.write_bytes(b'{"selected": true}')
    alias = tmp_path / 'staged.json'
    alias.symlink_to(target)
    assert load(reader)._read_stable_bytes(alias) == target.read_bytes()


def render(script, **values):
    for key, value in values.items():
        script = script.replace('${' + key + '}', str(value))
    return script.replace('\\$', '$')


def test_extracted_paired_shell_concurrent_no_checkpoint_copy(tmp_path):
    weights, checkpoint, wrapper, registry = fixture(tmp_path)
    before = {str(p.relative_to(weights)): (identity(p), sha(p)) for p in weights.rglob('*') if p.is_file()}
    text = (ROOT / 'modules/conformational_mapping_protenix.nf').read_text()
    start = '    ${params.api_python} ${params.code_root}/scripts/prepare_protenix_execution_snapshot.py'
    producer = start + text.split(start, 1)[1].split('    """', 1)[0]
    consumer = '    CHECKPOINT_REL=' + text.split('    CHECKPOINT_REL=', 1)[1].split('    EXTRA=()', 1)[0]
    # Only the container mount is projected to a tiny host fixture, no fake helper outputs.
    consumer = consumer.replace('/protenix_weights', str(weights))
    producer = producer.replace('${params.code_root}/scripts/run_protenix_inference.py', str(wrapper))
    values = {'params.api_python': sys.executable, 'params.code_root': ROOT, 'params.protenix_weights': weights}

    def attempt(index):
        work = tmp_path / f'prepare-{index}'
        work.mkdir()
        env = os.environ | {'REGISTRY': str(registry)}
        subprocess.run(['bash', '-euc', render(producer, **values)], cwd=work, env=env,
                       check=True, capture_output=True, text=True)
        staged = tmp_path / f'execute-{index}' / 'preflight'
        shutil.copytree(work / 'protenix_preflight', staged)
        env |= {'SNAPSHOT_ROOT': str(staged / 'execution-snapshot'),
                'EXECUTION_RECEIPT': str(staged / 'execution-snapshot-receipt.json')}
        # Shell's mandatory checkpoint test + actual receiver measurement run before inference.
        probe = '\nprintf "%s\\n" "$PROTENIX_ROOT_DIR" "$CHECKPOINT"\ntest -f "$PROTENIX_ROOT_DIR/common/ccd.cif"\ntest -f "$PROTENIX_ROOT_DIR/mmcif/template.cif"\n'
        result = subprocess.run(['bash', '-euc', render(consumer, **values) + probe], cwd=staged.parent,
                                env=env, check=True, capture_output=True, text=True)
        assert result.stdout.splitlines() == [str(weights), str(checkpoint)]
        assert not list(work.rglob('*.pt')) and not list(staged.parent.rglob('*.pt'))
        return staged, env

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(attempt, range(2)))
    after = {str(p.relative_to(weights)): (identity(p), sha(p)) for p in weights.rglob('*') if p.is_file()}
    assert after == before
    payloads = [p for p in tmp_path.rglob('*.pt') if p.is_file()]
    assert payloads == [checkpoint]
    print(json.dumps({'checkpoint_bytes': checkpoint.stat().st_size, 'checkpoint_blocks': checkpoint.stat().st_blocks,
                      'payload_count': len(payloads), 'task_checkpoint_bytes': 0, 'concurrent_attempts': len(attempts)}))
    checkpoint.chmod(0o644)
    checkpoint.write_bytes(b'tampered before execution')
    staged, env = attempts[0]
    failure = subprocess.run(['bash', '-euc', render(consumer, **values)], cwd=staged.parent,
                             env=env, capture_output=True, text=True)
    assert failure.returncode != 0 and 'checkpoint bytes differ' in failure.stderr
    checkpoint.unlink()
    missing = subprocess.run(['bash', '-euc', render(consumer, **values)], cwd=staged.parent,
                             env=env, capture_output=True, text=True)
    assert missing.returncode != 0
