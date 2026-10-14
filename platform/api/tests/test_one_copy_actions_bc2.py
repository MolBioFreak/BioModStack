"""Pinned CPU action writers; no image, model, provider or inference execution."""
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from services import bindcraft2_launch as launch, bindcraft2_runtime as runtime
from test_bindcraft2_lifecycle import setup, compiler

PIN = 'd5bae16e9fee95f4c97fc16bc05dcbde4ccb885f'


@pytest.fixture
def native(monkeypatch):
    root = os.environ.get('BC2_ACTION_SOURCE')
    if not root:
        pytest.skip('set BC2_ACTION_SOURCE to exact pinned native source')
    root = Path(root)
    assert subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() == PIN
    package = types.ModuleType('bindcraft')
    package.__path__ = [str(root / 'bindcraft')]
    monkeypatch.setitem(sys.modules, 'bindcraft', package)
    modules = {}
    # Execute complete pinned action functions. Remove only heavyweight protein
    # imports; the scoring boundary is stubbed, NOT the action/table/ZIP writers.
    for name in ('campaign_output', 'score', 'rank', 'campaign_filter'):
        source = root / 'bindcraft' / (name + '.py')
        tree = ast.parse(source.read_text())
        tree.body = [node for node in tree.body if not (
            isinstance(node, ast.ImportFrom) and node.module in
            ('bindcraft.campaign_log', 'bindcraft.protein', 'bindcraft.filters',
             'bindcraft.loss', 'bindcraft.protein_preparation'))]
        tree.body.insert(0, ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0))
        ast.fix_missing_locations(tree)
        module = types.ModuleType('bindcraft.' + name)
        module.__dict__.update(ALWAYS_SUPPRESSED_METRICS=(), VERSION='pinned-test',
                               recorded_number=lambda value: value, REGISTERED_FILTER_METRICS={})
        monkeypatch.setitem(sys.modules, module.__name__, module)
        exec(compile(tree, str(source), 'exec'), module.__dict__)
        modules[name] = module
    score = modules['score']
    def scored(path, *args, **kwargs):
        assert Path(path).read_text() == 'structure\n'
        return {'design': Path(path).stem, 'i_pDAE': 0.75, 'Binder_Sequence': 'AAAA'}
    score.scored_structure_row = scored
    score.score_design = lambda path, *a, **k: scored(path)
    score.design_model_scores = lambda _: {}
    return types.SimpleNamespace(**modules)


def put(root, relative, body='before\n'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('operation,options', [
    ('score', {'structure_relative_path': 'flat/design.cif'}),
    ('rank', {'campaign_relative_path': 'flat', 'list': True}),
    ('rank', {'campaign_relative_path': 'flat', 'rescore': True, 'on': ['i_pDAE']}),
    ('filter', {'campaign_relative_path': 'flat', 'list': True}),
    ('filter', {'campaign_relative_path': 'flat', 'where': [{'metric': 'i_pDAE', 'comparison': '>', 'value': 0.9}], 'write_rejected': True}),
])
def test_real_action_writers_both_owners(tmp_path, monkeypatch, native, operation, options):
    campaign, _ = setup(tmp_path, monkeypatch)
    put(campaign, 'flat/design.cif', 'structure\n')
    put(campaign, 'flat/scored.csv.partial')
    before = snapshot(campaign)
    handoff = launch.materialize_native_action(tmp_path / 'parent', tmp_path / 'child',
        operation=operation, options=options, source_job_id='parent', compiler=compiler,
        source_campaign_immutable=True)
    prepared = Path(handoff['bc2_campaign_dir'])
    compiled = json.loads((prepared / 'compilation.json').read_text())
    worker = tmp_path / 'worker'
    rebound = runtime.materialize_runtime_compilation(compiled, prepared, worker)
    child = worker / 'campaign'
    assert (child / 'flat/design.cif').stat().st_ino == (campaign / 'flat/design.cif').stat().st_ino
    assert (child / 'flat/scored.csv.partial').stat().st_ino != (campaign / 'flat/scored.csv.partial').stat().st_ino
    command = runtime.native_command(rebound, child, worker / 'native_settings.json')
    if operation == 'score':
        native.score.main(command[2:])
        put(child, 'native_score.txt', 'score output')
    else:
        getattr(native, 'campaign_filter' if operation == 'filter' else 'rank').main(command[2:])
        assert (child / 'flat/scored.csv').is_file()  # --list also scores
    assert snapshot(campaign) == before
    # Existing receipt means no source access and zero allocations on replay.
    (worker / 'compilation.json').write_text(json.dumps(rebound))
    inode = {p: p.stat().st_ino for p in worker.rglob('*') if p.is_file()}
    monkeypatch.setattr(runtime, '_private_leaf', lambda *a: pytest.fail('replay copied payload'))
    assert runtime.materialize_runtime_compilation(compiled, prepared, worker) == rebound
    assert launch.materialize_native_action(tmp_path / 'parent', tmp_path / 'child', operation=operation,
        options=options, source_job_id='parent', compiler=compiler, source_campaign_immutable=True) == handoff
    assert all(p.stat().st_ino == value for p, value in inode.items())
    assert compiled['source_compilation_sha256'] == hashlib.sha256((campaign.parent / 'compilation.json').read_bytes()).hexdigest()
    shutil.rmtree(campaign.parent)
    assert (child / 'flat/design.cif').read_text() == 'structure\n'


@pytest.mark.parametrize('scope', ['.', '3_Ranked/!_Ranked.csv'])
def test_recorded_table_rescore_keeps_native_choice(tmp_path, native, scope):
    source = tmp_path / 'source'
    put(source, 'campaign/3_Ranked/!_Ranked.csv', 'design,i_pDAE\nrecorded,0.7\n')
    put(source, 'campaign/recorded.cif', 'structure\n')
    before = snapshot(source)
    destination = tmp_path / 'child'
    runtime.materialize_action_tree(source, destination, {'operation': 'rank'})
    native.score.scored_structure_row = lambda *a, **k: pytest.fail('native --rescore must not replace a recorded stage table')
    native.rank.main([str(destination / 'campaign' / scope), '--rescore', '--on', 'i_pDAE'])
    assert list(destination.rglob('ranked_by_i_pDAE.csv'))
    assert not list(destination.rglob('scored.csv'))
    assert snapshot(source) == before


@pytest.mark.parametrize('selected', ['.', 'armA'])
def test_multiarm_campaign_output(tmp_path, native, selected):
    source = tmp_path / 'source'
    for arm in ('armA', 'armB'):
        put(source, f'campaign/{arm}/1_Trajectories/!_Trajectories.csv', 'design,i_pDAE\ndesign,0.6\n')
        put(source, f'campaign/{arm}/3_Ranked/!_Ranked.csv', 'design,i_pDAE\ndesign,0.7\n')
        put(source, f'campaign/{arm}/3_Ranked/design.cif', 'structure\n')
        put(source, f'campaign/{arm}/summary.csv.partial')
    before = snapshot(source)
    destination = tmp_path / 'destination'
    runtime.materialize_action_tree(source, destination, {'operation': 'campaign_output', 'options': {'campaign_relative_path': selected}})
    native.campaign_output.main([str(destination / 'campaign' / selected)])
    assert (destination / 'campaign/armA/summary.csv').exists()
    assert (destination / 'campaign/armB/summary.csv').exists() == (selected == '.')
    assert (destination / 'campaign/summary.csv').exists() == (selected == '.')
    assert snapshot(source) == before


def test_real_archive_unarchive_collision(tmp_path, native):
    source = tmp_path / 'source'
    leaf = put(source, 'campaign/1_Trajectories/design/design.cif', 'structure\n')
    put(source, 'campaign/1_Trajectories/design.zip.partial', 'old partial')
    archived = tmp_path / 'archived'
    runtime.materialize_action_tree(source, archived, {'operation': 'archive'})
    alias = archived / leaf.relative_to(source)
    assert alias.stat().st_ino == leaf.stat().st_ino
    native.campaign_output.archive_campaign_trajectories(str(archived / 'campaign'))
    assert leaf.read_text() == 'structure\n' and not alias.exists()
    collision = put(archived, leaf.relative_to(source), 'collision\n')
    put(archived, 'campaign/1_Trajectories/untouched.cif', 'retained\n')
    restored = tmp_path / 'restored'
    before = snapshot(archived)
    runtime.materialize_action_tree(archived, restored, {'operation': 'unarchive'})
    extracted = restored / leaf.relative_to(source)
    assert extracted.stat().st_ino != collision.stat().st_ino
    native.campaign_output.restore_campaign_trajectories(str(restored / 'campaign'))
    assert extracted.read_text() == 'structure\n'
    assert snapshot(archived) == before
    assert (restored / 'campaign/1_Trajectories/untouched.cif').stat().st_ino == (archived / 'campaign/1_Trajectories/untouched.cif').stat().st_ino


def test_resume_completed_hash_bodies_and_native_state_writers(tmp_path, native):
    source = tmp_path / 'source'
    name, identity = 'target_l20_0123456789abcdef', '0123456789abcdef'
    put(source, 'sources/target.pdb')
    put(source, 'campaign/1_Trajectories/!_Trajectories.csv',
        f'design,hash,trajectory\n{name},{identity},1\n')
    body = put(source, f'campaign/1_Trajectories/{name}/{name}_trajectory.cif', 'structure\n')
    accepted = put(source, f'campaign/3_Ranked/{name}_seq0.cif', 'structure\n')
    put(source, 'campaign/.campaign_state.json', json.dumps({'trajectories': 1, 'accepted': 0, 'attempted': [identity]}))
    put(source, 'campaign/.redesigned_sequences.txt', 'AAAA\n')
    destination = tmp_path / 'child'
    before = snapshot(source)
    runtime.materialize_action_tree(source, destination, {'operation': 'resume'})
    for path in (body, accepted):
        assert path.stat().st_ino == (destination / path.relative_to(source)).stat().st_ino
    progress = native.campaign_output.CampaignProgress(str(destination / 'campaign'), 5, 5)
    assert progress.claim_trajectory() == (2, 0)
    assert progress.claim_recipe(identity) is False  # before native opens the shared names
    assert native.campaign_output.redesigned_sequence(str(destination / 'campaign'), 'BBBB') is False
    native.campaign_output.append_campaign_metrics(str(destination / 'campaign/1_Trajectories/!_Trajectories.csv'), {'design': 'new', 'hash': 'newhash'})
    assert snapshot(source) == before
    # Explicit native state which does not remember the hash is not proof of an
    # immutable continuation body. Keep it independent, without a new refusal.
    put(source, 'campaign/.campaign_state.json', json.dumps({'trajectories': 1, 'accepted': 0, 'attempted': []}))
    other = tmp_path / 'other'
    runtime.materialize_action_tree(source, other, {'operation': 'resume'})
    assert body.stat().st_ino != (other / body.relative_to(source)).stat().st_ino


def test_non_cow_readonly_zero_copy_and_cross_filesystem_custody(tmp_path, monkeypatch):
    import errno
    import fcntl
    source = tmp_path / 'source'
    body = put(source, 'campaign/design.cif', 'structure\n')
    put(source, 'campaign/scored.csv', 'design,i_pDAE\ndesign,0.7\n')
    put(source, 'sources/target.pdb', 'target\n')
    monkeypatch.setattr(fcntl, 'ioctl', lambda *a: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP, 'no CoW')))
    copies = []
    private = runtime._private_leaf
    def counted(src, dest):
        copies.append(src.relative_to(source).as_posix())
        return private(src, dest)
    monkeypatch.setattr(runtime, '_private_leaf', counted)
    destination = tmp_path / 'samefs'
    runtime.materialize_action_tree(source, destination, {'operation': 'rank'})
    assert copies == ['campaign/scored.csv']
    assert (destination / 'campaign/design.cif').stat().st_ino == body.stat().st_ino
    # Required independent custody when linking is impossible is not a claimed
    # dedup win. Exercise the real copy fallback with FICLONE unsupported.
    monkeypatch.setattr(os, 'link', lambda *a: (_ for _ in ()).throw(OSError(errno.EXDEV, 'cross filesystem')))
    copies.clear()
    other = tmp_path / 'otherfs'
    runtime.materialize_action_tree(source, other, {'operation': 'rank'})
    assert set(copies) == {'campaign/design.cif', 'campaign/scored.csv', 'sources/target.pdb'}
    assert snapshot(other) == snapshot(source)
    assert (other / 'campaign/design.cif').stat().st_ino != body.stat().st_ino


def test_resume_mutables_and_active_donor(tmp_path):
    source = tmp_path / 'source'
    immutable = put(source, 'sources/target.pdb')
    for relative in ('.redesigned_sequences.txt', 'arm/.campaign_state.json', 'arm/design.cif', 'arm/metrics.csv', '.sweep_state.json'):
        put(source, 'campaign/' + relative)
    for operation, immutable_campaign in [('resume', True), ('rank', False)]:
        destination = tmp_path / operation
        runtime.materialize_action_tree(source, destination, {'operation': operation}, campaign_immutable=immutable_campaign)
        assert (destination / 'sources/target.pdb').stat().st_ino == immutable.stat().st_ino
        for path in (source / 'campaign').rglob('*'):
            if path.is_file():
                target = destination / path.relative_to(source)
                assert target.stat().st_ino != path.stat().st_ino
                with target.open('a') as stream: stream.write('append')
                target.write_text('truncate')
                replacement = target.with_name(target.name + '.replace')
                replacement.write_text('replace')
                replacement.replace(target)
                target.unlink()
                assert path.read_text() == 'before\n'
