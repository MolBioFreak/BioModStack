"""RF resume output custody without importing or running inference."""
import ast
import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[3] / 'scripts/rfantibody_inference_wrapper.py'


def helper():
    tree = ast.parse(WRAPPER.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'seed_resume_output')
    scope = {'os': os}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(WRAPPER), 'exec'), scope)
    return scope['seed_resume_output']


def put(root, name, text='parent'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.mark.parametrize('immutable', [False, True])
@pytest.mark.parametrize('indices', [[0, 1], [0, 3], [0, 1, 2, 3], []])
def test_seed_no_clobber_sparse_collisions(tmp_path, monkeypatch, immutable, indices):
    seed = helper()
    source, child = tmp_path / 'source', tmp_path / 'child'
    source.mkdir(); child.mkdir()
    for index in indices:
        put(source, f'design_{index}.pdb')
        put(source, f'design_{index}.trb')
        put(source, f'traj/design_{index}_Xt-1_traj.pdb')
        put(source, f'traj/design_{index}_pX0_traj.pdb')
    put(source, 'design_2.trb', 'orphan or collision')
    put(source, 'cache.dat')
    put(source, 'nested/evidence.txt')
    put(child, 'cache.dat', 'destination wins')
    seed(source, child, prefix='design', num_designs=4, immutable=immutable)
    assert (child / 'cache.dat').read_text() == 'destination wins'
    assert len(list(child.glob('design_*.pdb'))) == len(indices)
    for path in source.rglob('*'):
        if not path.is_file() or path.name == 'cache.dat': continue
        target = child / path.relative_to(source)
        relative = path.relative_to(source).as_posix()
        collides = any(relative in (f'design_{i}.pdb', f'design_{i}.trb', f'traj/design_{i}_Xt-1_traj.pdb', f'traj/design_{i}_pX0_traj.pdb') for i in range(len(indices), 4))
        if not immutable or collides or relative in ('nested/evidence.txt',) or path.name == 'design_2.trb' and 2 not in indices:
            assert target.stat().st_ino != path.stat().st_ino
            before = path.read_bytes()
            target.write_text('truncate')
            with target.open('a') as stream: stream.write('append')
            replacement = target.with_suffix('.replacement')
            replacement.write_text('replace'); replacement.replace(target)
            assert path.read_bytes() == before
        else:
            assert target.stat().st_ino == path.stat().st_ino
    before = {p: (p.stat().st_ino, p.read_bytes()) for p in child.rglob('*') if p.is_file()}
    monkeypatch.setattr(os, 'link', lambda *a: pytest.fail('replay allocated'))
    seed(source, child, prefix='design', num_designs=4, immutable=immutable)
    seed(child, child, prefix='design', num_designs=4, immutable=immutable)
    assert all((p.stat().st_ino, p.read_bytes()) == value for p, value in before.items())


def test_pinned_native_pdb_trb_writer_collisions(tmp_path):
    import pickle
    native_root = Path(os.environ.get('RF_ACTION_SOURCE', '/mnt/BioModStack/weights/rfantibody/rfantibody_repo'))
    if not native_root.exists():
        pytest.skip('set RF_ACTION_SOURCE to pinned native source')
    assert subprocess.check_output(['git', '-C', str(native_root), 'rev-parse', 'HEAD'], text=True).strip() == '8d9d402a99fd63052ad538b3d670e467b800123c'
    script = native_root / 'scripts/rfdiffusion_inference.py'
    tree = ast.parse(script.read_text())
    blocks = [node for node in ast.walk(tree) if isinstance(node, ast.With)
              and any(isinstance(item.context_expr, ast.Call)
                      and isinstance(item.context_expr.func, ast.Name)
                      and item.context_expr.func.id == 'open'
                      and ast.unparse(item.context_expr.args[0]) in ("f'{out_prefix}.trb'", 'out')
                      for item in node.items)]
    assert len(blocks) == 2
    source, child = tmp_path / 'source', tmp_path / 'child'
    # Sparse count=2 attempts index 2, despite already retaining index 2.
    for index in (0, 2):
        put(source, f'design_{index}.pdb')
        put(source, f'design_{index}.trb')
    helper()(source, child, prefix='design', num_designs=3, immutable=True)
    scope = {'out_prefix': str(child / 'design_2'), 'out': str(child / 'design_2.pdb'),
             'pdblines': ['native replacement'], 'trb': {'native': True}, 'pickle': pickle}
    exec(compile(ast.Module(body=blocks, type_ignores=[]), str(script), 'exec'), scope)
    assert (child / 'design_2.pdb').read_text() == 'native replacement'
    assert pickle.loads((child / 'design_2.trb').read_bytes()) == {'native': True}
    assert (source / 'design_2.pdb').read_text() == 'parent'
    assert (source / 'design_2.trb').read_text() == 'parent'


def test_actual_cli_raw_source_and_non_cow(tmp_path, monkeypatch):
    import fcntl
    source, child = tmp_path / 'source', tmp_path / 'child'
    put(source, 'design_0.pdb')
    put(source, 'traj/design_0_Xt-1_traj.pdb')
    subprocess.run([sys.executable, str(WRAPPER), '--bms-seed-output', str(source), str(child), 'design', '2'], check=True)
    assert (source / 'design_0.pdb').stat().st_ino != (child / 'design_0.pdb').stat().st_ino
    monkeypatch.setattr(fcntl, 'ioctl', lambda *a: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP, 'no CoW')))
    destination = tmp_path / 'fallback'
    helper()(source, destination, prefix='design', num_designs=2)
    assert (destination / 'design_0.pdb').read_text() == 'parent'
    assert (destination / 'design_0.pdb').stat().st_ino != (source / 'design_0.pdb').stat().st_ino
