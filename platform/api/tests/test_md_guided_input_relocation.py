"""Retained worker wires through CLI validation and preparation consumption.

The checked-in wires are the two failed guided-v3 attempts. Unit tests replace
only placement and source bytes with explicitly inert data; native qualification
uses the retained original bytes separately. No chemistry is simulated here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path

from typing import Any

import pytest

from scripts.bms_md import cli
from scripts.bms_md.chemistry.prepare import build_preparation_bundle
from scripts.lib import portable_inputs

FIXTURE = Path(__file__).with_name('fixtures') / 'md_guided_worker_inputs.json'


def stage_worker(tmp_path, case):
    retained = json.loads(FIXTURE.read_text())[case]
    config = retained['config']
    bindings = retained['bindings']
    controller = tmp_path / 'controller'
    worker = tmp_path / 'worker'
    controller.mkdir()
    worker.mkdir()
    original_root = str(Path(config['input']['structure']).parent.parent)
    worker_root = bindings['roots'][0].split('/bundle/inputs')[0]
    # Relocate the captured placement, never substitute paths in production.
    def relocate(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(original_root, str(controller)).replace(worker_root, str(worker))
        if isinstance(value, dict):
            return {key: relocate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [relocate(item) for item in value]
        return value
    config, bindings = relocate(config), relocate(bindings)
    source = Path(config['input']['structure'])
    source.parent.mkdir()
    source.write_bytes(b'REMARK inert relocation fixture, not a molecule\nEND\n')
    contents = source.read_bytes()
    config['input']['structure_sha256'] = hashlib.sha256(contents).hexdigest()
    config['input']['structure_bytes'] = len(contents)
    for binding in bindings['bindings']:
        target = Path(binding['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = contents if binding['reference']['role'] == 'structure' else json.dumps(config).encode()
        target.write_bytes(payload)
        binding['reference']['sha256'] = hashlib.sha256(payload).hexdigest()
        binding['reference']['size_bytes'] = len(payload)
    path = worker / 'bindings.json'
    path.write_text(json.dumps(bindings))
    archived = Path(bindings['bindings'][0]['path'])
    shutil.rmtree(controller)
    assert not source.exists()
    return config, bindings, path, archived, contents


def validate(monkeypatch, archived, binding, output):
    monkeypatch.setenv('BMS_FEATURE_MOLECULAR_DYNAMICS', '1')
    monkeypatch.setenv(portable_inputs.ENV, str(binding))
    monkeypatch.setattr(sys, 'argv', ['bms-md', 'validate', '--config', str(archived),
                                    '--output', str(output), '--base-dir', str(archived.parent)])
    cli.main()
    return json.loads(output.read_text())


@pytest.mark.parametrize('case', ['protein', 'protein_dna'])
def test_guided_worker_validation_reaches_real_preparation_consumer(tmp_path, monkeypatch, case):
    original, bindings, binding, archived, contents = stage_worker(tmp_path, case)
    archived_bytes = archived.read_bytes()
    output = tmp_path / 'normalized_config.json'
    config = validate(monkeypatch, archived, binding, output)
    expected = copy.deepcopy(original)
    expected['input']['structure'] = bindings['bindings'][1]['path']
    assert archived.read_bytes() == archived_bytes
    lock = tmp_path / 'inert.lock'
    lock.write_text('inert test lock\n')
    consumed = []

    class ReachedChemistryWorker(Exception):
        pass

    def inert_worker(command, **kwargs):
        root = Path(kwargs['cwd'])
        consumed.append((root / 'source.pdb').read_bytes())
        request = json.loads((root / 'request.json').read_text())
        assert request['profile']['id'] == original['chemistry']['profile_id']
        for key in ('padding_nm', 'salt_molar', 'neutralize'):
            assert request[key] == original['preparation'][key]
        raise ReachedChemistryWorker

    # The actual consumer hashes and copies the relocated bytes before the
    # explicitly inert chemistry leaf stops; no fabricated native outputs.
    with pytest.raises(ReachedChemistryWorker):
        build_preparation_bundle(
            source_structure=Path(config['input']['structure']), destination=tmp_path / 'prepared',
            profile_id=config['chemistry']['profile_id'],
            profile_sha256=config['chemistry']['profile_sha256'], runtime_lock=lock,
            **{key: config['preparation'][key] for key in ('padding_nm', 'salt_molar', 'neutralize')},
            runner=inert_worker,
        )
    assert config == expected  # Includes all native stages, seeds and chemistry.
    assert consumed == [contents]
    assert not (tmp_path / 'prepared').exists()


@pytest.mark.parametrize('damage, message', [
    ('digest', 'digest/size mismatch'), ('outside', 'outside approved roots'),
    ('symlink', 'symlink'), ('ambiguous', 'Ambiguous portable input binding'),
])
def test_guided_validation_keeps_portable_checks(tmp_path, monkeypatch, damage, message):
    _, bindings, binding, archived, _ = stage_worker(tmp_path, 'protein')
    entry = bindings['bindings'][1]
    target = Path(entry['path'])
    if damage == 'digest':
        target.write_bytes(b'corrupt')
    elif damage == 'outside':
        outside = tmp_path / 'outside.pdb'
        shutil.copyfile(target, outside)
        entry['path'] = str(outside)
    elif damage == 'symlink':
        other = target.with_name('other.pdb')
        target.rename(other)
        target.symlink_to(other)
    else:
        duplicate = copy.deepcopy(entry)
        duplicate['reference']['sha256'] = '0' * 64
        bindings['bindings'].append(duplicate)
    binding.write_text(json.dumps(bindings))
    output = tmp_path / 'normalized_config.json'
    with pytest.raises(ValueError, match=message):
        validate(monkeypatch, archived, binding, output)
    assert not output.exists()


@pytest.mark.parametrize('mode', ['legacy', 'guided-v2', 'prepared', 'compiled'])
def test_cli_preserves_other_input_contracts(tmp_path, monkeypatch, mode):
    from scripts.bms_md.contract import normalize_job_config, prepare_verified_worker_inputs

    controller = tmp_path / 'controller'
    worker = tmp_path / 'worker'
    controller.mkdir()
    worker.mkdir()
    config = json.loads(FIXTURE.read_text())['protein']['config']
    if mode in {'legacy', 'guided-v2'}:
        config['schema'] = 'bms.md.job.v1' if mode == 'legacy' else 'bms.md.job.v2'
        config.pop('stages')
        config.pop('preparation')
        fields = ['structure']
    elif mode == 'prepared':
        fields = ['coordinates', 'topology', 'index', 'restraint_reference']
    else:
        fields = ['tpr', 'checkpoint']
        config['stages'] = []
    config['input'] = {}
    expected_bytes = {}
    for field in fields:
        path = controller / (field + '.dat')
        path.write_bytes(('inert ' + field + '\n').encode())
        expected_bytes[field] = path.read_bytes()
        config['input'].update({field: str(path), field + '_bytes': path.stat().st_size,
                               field + '_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    if mode == 'prepared':
        config['input']['topology_closure'] = {'root': str(controller), 'files': []}
    config = normalize_job_config(config)
    source = controller / 'config.json'
    source.write_text(json.dumps(config))
    refs = portable_inputs.discover_native_input_references(
        'molecular_dynamics', 'simulate', {'md_job_config': str(source)}, (),
        output_dir=controller, allowed_roots=[controller])
    entries = []
    expected = copy.deepcopy(config)
    for ref in refs:
        target = worker / Path(ref['source_path']).name
        shutil.copyfile(ref['source_path'], target)
        entries.append({'reference': ref, 'path': str(target)})
        if ref['role'] in fields:
            expected['input'][ref['role']] = str(target)
    binding = worker / 'bindings.json'
    binding.write_text(json.dumps({'schema': portable_inputs.SCHEMA,
                                  'roots': [str(worker)], 'bindings': entries}))
    shutil.rmtree(controller)
    output = worker / 'normalized.json'
    assert validate(monkeypatch, worker / 'config.json', binding, output) == expected
    received = prepare_verified_worker_inputs(output, worker / 'private')
    for field in fields:
        assert Path(received['input'][field]).read_bytes() == expected_bytes[field]


@pytest.mark.parametrize('base_dir', [False, True])
def test_local_cli_retains_relative_input_semantics(tmp_path, monkeypatch, base_dir):
    config = json.loads(FIXTURE.read_text())['protein']['config']
    config['input']['structure'] = 'inputs/structure.pdb'
    archived = tmp_path / 'config.json'
    archived.write_text(json.dumps(config))
    output = tmp_path / 'normalized.json'
    monkeypatch.delenv(portable_inputs.ENV, raising=False)
    monkeypatch.setenv('BMS_FEATURE_MOLECULAR_DYNAMICS', '1')
    args = ['bms-md', 'validate', '--config', str(archived), '--output', str(output)]
    if base_dir:
        args += ['--base-dir', str(tmp_path)]
        config['input']['structure'] = str(tmp_path / 'inputs/structure.pdb')
    monkeypatch.setattr(sys, 'argv', args)
    cli.main()
    assert json.loads(output.read_text()) == config
