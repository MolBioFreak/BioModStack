"""Native lifecycle copies state, reopens without source access, and uses pinned CPU CLI."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from services import bindcraft2_launch as launch
from services.bindcraft2_native import _canonical, compile_for_native
from services.bindcraft2_runtime import native_command, validate_action_options
from services.bindcraft2_typed import schema


def compiler(request, campaign, *, resume=False):
    result = compile_for_native(request, campaign, lambda x: x, resume=resume)
    return {**result, 'requested_settings': request, 'request_sha256': hashlib.sha256(_canonical(request)).hexdigest()}


def setup(tmp_path, monkeypatch, compile_fn=compiler):
    monkeypatch.setattr(launch, 'get_results_dir', lambda: tmp_path)
    monkeypatch.setattr(launch, 'get_allowed_roots', lambda: {'results': tmp_path})
    target = tmp_path / 'target.fasta'
    target.write_text('>fixture\nAAAAAAAAAAAAAAAAAAAA\n')
    request = {'max_trajectories': 3, 'targets': [{'name': 'fixture', 'target_path': str(target)}]}
    preview = launch.preview_campaign(request, compiler=compile_fn)
    launch.materialize_campaign(request, tmp_path / 'parent', preview_digest=preview['preview_digest'], compiler=compile_fn)
    campaign = tmp_path / 'parent/bindcraft2/campaign'
    campaign.mkdir()
    (campaign / '.campaign_state.json').write_text('{"accepted":0,"trajectories":1,"attempted":["fixture"]}')
    (campaign / '.redesigned_sequences.txt').write_text('AAAA\n')
    (campaign / 'arm_1').mkdir()
    (campaign / 'arm_1/.campaign_state.json').write_text('{"accepted":0,"trajectories":1}')
    return campaign, request


def test_resume_snapshots_state_and_reopens_without_original_sources(tmp_path, monkeypatch):
    campaign, request = setup(tmp_path, monkeypatch)
    options = dict(operation='resume', source_job_id='parent', compiler=compiler)
    child = launch.materialize_native_action(tmp_path / 'parent', tmp_path / 'child', **options)
    receipt = json.loads(Path(child['bc2_compilation']).read_text())
    assert receipt['schema_version'] == 1
    assert receipt['native_request']['resume'] is True
    assert receipt['requested_settings'] == request
    copied = Path(child['bc2_campaign_dir']) / 'campaign'
    assert (copied / 'arm_1/.campaign_state.json').read_bytes() == (campaign / 'arm_1/.campaign_state.json').read_bytes()
    assert (copied / '.redesigned_sequences.txt').read_text() == 'AAAA\n'
    (copied / '.campaign_state.json').write_text('{"trajectories":2}')
    (tmp_path / 'target.fasta').unlink()
    # Simulate a controller restart: reconstruct from disk, do not recopy old state.
    assert launch.materialize_native_action(tmp_path / 'parent', tmp_path / 'child', **options) == child
    assert (copied / '.campaign_state.json').read_text() == '{"trajectories":2}'
    assert json.loads((campaign / '.campaign_state.json').read_text())['trajectories'] == 1
    assert launch.read_campaign_receipt(tmp_path / 'child')['native_action']['operation'] == 'resume'
    with pytest.raises(ValueError, match='source campaign'):
        launch.materialize_native_action(tmp_path / 'parent', tmp_path / 'parent', **options)


def test_interrupted_preparation_retries_atomic_files(tmp_path, monkeypatch):
    from services.bindcraft2_runtime import prepare_campaign
    destination = tmp_path / 'job'
    request = {'max_trajectories': 1, 'targets': [{'name': 'fixture', 'target_path': str(tmp_path / 'fixture.fasta')}]}
    compiled = compile_for_native(request, destination / 'campaign', lambda value: value)
    write = Path.write_bytes
    interrupted = False

    def interrupt(path, payload):
        nonlocal interrupted
        if path.name == 'native_settings.json.partial' and not interrupted:
            interrupted = True
            write(path, payload[:8])
            raise OSError('fixture interruption')
        return write(path, payload)

    monkeypatch.setattr(Path, 'write_bytes', interrupt)
    read = lambda path: json.loads(path.read_text())
    with pytest.raises(OSError, match='fixture interruption'):
        prepare_campaign(compiled, destination, lambda value: value, lambda _: (), read)
    assert not (destination / 'native_settings.json').exists()
    prepared = prepare_campaign(compiled, destination, lambda value: value, lambda _: (), read)
    assert json.loads(Path(prepared['settings_path']).read_text()) == compiled['native_request']
    assert not (destination / 'native_settings.json.partial').exists()


def test_native_action_options_and_nested_discovery():
    data = schema()
    assert set(data['native_actions']) == {'resume', 'rank', 'filter', 'campaign_output', 'archive', 'unarchive', 'score'}
    nested = data['nested_control_schemas']
    assert nested['targets']['items']['properties']['target_path']['control'] == 'source'
    assert nested['losses']['properties']['interface_contacts']['properties']['params']['properties']['contact_residue_count']['native_default_encoding'] == '+Infinity'
    assert 'default' not in nested['filters']['properties']['i_pTM']['properties']['threshold']
    for operation, options in [('rank', {'on': [1]}), ('filter', {'where': [{'metric': 'i_pTM'}]}), ('resume', {'force': True})]:
        with pytest.raises(ValueError):
            validate_action_options(operation, options)
    action = {'native_action': {'operation': 'filter', 'options': {'where': [{'metric': 'i_pTM', 'comparison': '>=', 'value': 0.7}], 'write_rejected': True}}}
    assert native_command(action, Path('/job/campaign'), Path('/job/settings.json')) == [
        'bindcraft', 'filter', '/job/campaign', '--where', 'i_pTM>=0.7', '--rejected', '/job/campaign/rejected.csv']


def image_run(root, scratch, *args):
    return subprocess.run(['apptainer', 'exec', '--no-home', '--env', 'JAX_PLATFORMS=cpu',
        '--bind', f'{root}:{root}', '--bind', f'{scratch}:{scratch}', str(launch.IMAGE),
        'python3', *map(str, args)], check=True, capture_output=True, text=True, timeout=120)


def test_installed_cpu_transport_relocation_return_and_restart(tmp_path, monkeypatch):
    import shutil
    if not launch.IMAGE.is_file():
        pytest.skip('installed pinned BC2 image unavailable')
    root = Path(__file__).resolve().parents[3]
    setup(tmp_path, monkeypatch, launch._native_compile)
    source = tmp_path / 'parent/bindcraft2'
    original = json.loads((source / 'compilation.json').read_text())
    transported = tmp_path / 'worker/inputs/bindcraft2'
    shutil.copytree(source, transported)
    untouched = (transported / 'compilation.json').read_bytes()
    destination = tmp_path / 'worker/results/bindcraft2'
    args = [root / 'scripts/run_bindcraft2_campaign.py', transported / 'compilation.json', destination,
            '--native-source', '/opt/bindcraft']
    # CPU-only preparation: no design, local worker-layout harness, not remote GPU evidence.
    image_run(root, tmp_path, *args)
    relocated = json.loads((destination / 'compilation.json').read_text())
    assert relocated['requested_settings'] == original['requested_settings']
    assert relocated['request_sha256'] == original['request_sha256']
    assert relocated['effective_sha256'] != original['effective_sha256']
    assert relocated['placement']['original_effective_sha256'] == original['effective_sha256']
    assert relocated['native_request']['project_folder'] == str(destination / 'campaign')
    assert Path(relocated['native_request']['targets'][0]['target_path']).read_bytes() == (source / 'sources/target_0.fasta').read_bytes()
    (destination / 'campaign/.campaign_state.json').write_text('{"trajectories":2}')
    image_run(root, tmp_path, *args)
    assert (destination / 'campaign/.campaign_state.json').read_text() == '{"trajectories":2}'
    assert (transported / 'compilation.json').read_bytes() == untouched
    shutil.copytree(destination, source, dirs_exist_ok=True)  # existing bridge output return layout
    shutil.rmtree(tmp_path / 'worker')
    assert launch.read_campaign_receipt(tmp_path / 'parent')['effective_sha256'] == relocated['effective_sha256']
    child = launch.materialize_native_action(tmp_path / 'parent', tmp_path / 'after-return', operation='resume', source_job_id='parent')
    assert json.loads(Path(child['bc2_compilation']).read_text())['native_request']['resume'] is True
    assert (tmp_path / 'after-return/bindcraft2/campaign/.campaign_state.json').read_text() == '{"trajectories":2}'


def test_installed_cpu_lifecycle_and_native_progress_recovery(tmp_path, monkeypatch):
    if not launch.IMAGE.is_file():
        pytest.skip('installed pinned BC2 image unavailable')
    root = Path(__file__).resolve().parents[3]
    campaign, request = setup(tmp_path, monkeypatch, launch._native_compile)
    image_run(root, tmp_path, '-c', "import shutil,sys; shutil.copyfile('/opt/bindcraft/settings/target/structures/hIL2R_beta_gamma.pdb',sys.argv[1])", campaign / 'coordinate_fixture.pdb')
    # Explicit fixture tables, not simulated native inference or acceptance evidence.
    trajectories = campaign / '1_Trajectories'
    trajectories.mkdir()
    design = trajectories / 'fixture'
    design.mkdir()
    (design / 'fixture_losses.csv').write_text('round,loss\n1,1\n')
    (trajectories / '!_Trajectories.csv').write_text('design,hash,terminated\nfixture,fixture,completed\n')
    refolded = campaign / '2_Refolded'
    refolded.mkdir()
    (refolded / '!_Refolded.csv').write_text('design,i_pTM,i_pDAE,Binder_Sequence\na,0.8,0.8,AAAA\nb,0.2,0.2,CCCC\n')
    for operation, options in [
        ('resume', {}), ('rank', {'table': 'candidates', 'on': ['i_pTM']}),
        ('filter', {'where': [{'metric': 'i_pTM', 'comparison': '>=', 'value': 0.7}], 'write_rejected': True}),
        ('campaign_output', {}), ('archive', {}),
        ('score', {'structure_relative_path': 'coordinate_fixture.pdb', 'binder': 'B', 'target': 'A'}),
    ]:
        output = tmp_path / operation
        handoff = launch.materialize_native_action(tmp_path / 'parent', output, operation=operation, options=options, source_job_id='parent')
        args = [root / 'scripts/run_bindcraft2_campaign.py', handoff['bc2_compilation'], handoff['bc2_campaign_dir'], '--native-source', '/opt/bindcraft']
        # Resume preparation only: NEVER execute GPU design in this test.
        completed = image_run(root, tmp_path, *args, *([] if operation == 'resume' else ['--execute']))
        prepared = json.loads(completed.stdout.splitlines()[0])
        assert prepared['command'][1] == ('design' if operation == 'resume' else operation)
        assert launch.read_campaign_receipt(output)['requested_settings'] == request
        assert launch.materialize_native_action(tmp_path / 'parent', output, operation=operation, options=options, source_job_id='parent') == handoff
    ranked = list((tmp_path / 'rank/bindcraft2/campaign').rglob('ranked_by_i_pTM.csv'))
    assert len(ranked) == 1 and 'a' in ranked[0].read_text()
    assert (tmp_path / 'filter/bindcraft2/campaign/rejected.csv').is_file()
    assert (tmp_path / 'campaign_output/bindcraft2/campaign/summary.csv').is_file()
    assert 'Interface_Residues' in (tmp_path / 'score/bindcraft2/campaign/native_score.txt').read_text()
    archive = tmp_path / 'archive/bindcraft2/campaign/1_Trajectories/fixture.zip'
    assert archive.is_file()
    restored = launch.materialize_native_action(tmp_path / 'archive', tmp_path / 'unarchive', operation='unarchive', source_job_id='archive')
    image_run(root, tmp_path, root / 'scripts/run_bindcraft2_campaign.py', restored['bc2_compilation'], restored['bc2_campaign_dir'], '--native-source', '/opt/bindcraft', '--execute')
    assert (tmp_path / 'unarchive/bindcraft2/campaign/1_Trajectories/fixture/fixture_losses.csv').is_file()
    assert archive.is_file()  # immutable parent was not unpacked
    # Native state owner, in separate processes, demonstrates persisted progress and dedup.
    code = "from bindcraft.campaign_output import CampaignProgress,redesigned_sequence; import sys,json; p=CampaignProgress(sys.argv[1],10,3); print(json.dumps([p.campaign_status(),p.claim_recipe('fixture'),redesigned_sequence(sys.argv[1],'AAAA')]))"
    results = [image_run(root, tmp_path, '-c', code, tmp_path / 'resume/bindcraft2/campaign').stdout for _ in range(2)]
    assert results[0] == results[1]
    assert json.loads(results[0]) == [[0, 1], False, True]
