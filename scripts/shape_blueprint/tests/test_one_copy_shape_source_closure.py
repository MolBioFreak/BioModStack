"""Private selected-source and writer-custody fixtures; no installed writes/science."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_one_copy_shape_handoffs import ROOT, SCRIPTS, inputs, attach
from run_shape_sequence import run_sequence_lane


def test_fampnn_reader_alias_is_closed_only_after_native_exit(tmp_path):
    backbone = tmp_path / 'backbone.pdb'
    original = b'ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 90.00           C\n'
    backbone.write_bytes(original)
    runner = tmp_path / 'inert_native_emission.py'
    runner.write_text('''from pathlib import Path
import sys
args = dict(item.split('=', 1) for item in sys.argv[1:])
source, = Path(args['pdb_dir']).glob('*.pdb')
assert source.is_symlink(), 'native reader must see the original staging contract'
out = Path(args['out_dir']) / 'fastas'
out.mkdir()
(out / (source.stem + '_sample0.fasta')).write_text('>' + source.stem + '_sample0\\nA\\n')
''')
    settings = dict(fampnn_batch_size=1, fampnn_seq_only=True, fampnn_repack_last=False,
        fampnn_temperature=0.23, fampnn_num_steps=7, fampnn_exclude_cys=False, fampnn_psce_threshold=None)
    request = dict(sequence_engine='fampnn', sequence_policy='auto', sequences_per_backbone=1, seed=7,
        sequence_settings=settings, requested_sequence_settings=settings,
        sequence_settings_identity={'engine': 'fampnn'})
    request['request_sha256'] = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    outputs = []
    for index in range(2):
        out = tmp_path / f'output{index}'
        records = run_sequence_lane(engine='fampnn', backbone_path=backbone, output_dir=out,
            receipt_path=out / 'runtime_receipt.json', count=1, seed=7, candidate_id='a' * 64,
            request=request, runner=str(runner))
        assert records[0]['sequence_settings'] == settings
        assert records[0]['sequence_name'] == 'a' * 64 + '__fampnn__000'
        for path in (out / 'input/backbone.pdb', out / 'source_backbone.pdb'):
            assert not path.is_symlink() and path.samefile(backbone)
        outputs.append(out)
    assert not (outputs[0] / 'runtime_receipt.json').samefile(outputs[1] / 'runtime_receipt.json')
    failed_runner = tmp_path / 'inert_native_failure.py'
    failed_runner.write_text('raise RuntimeError("explicit inert native failure")\n')
    failed = tmp_path / 'failed'
    with pytest.raises(subprocess.CalledProcessError):
        run_sequence_lane(engine='fampnn', backbone_path=backbone, output_dir=failed,
            receipt_path=failed / 'runtime_receipt.json', count=1, seed=7, candidate_id='a' * 64,
            request=request, runner=str(failed_runner))
    assert json.loads((failed / 'runtime_receipt.json').read_text())['status'] == 'failed'
    assert backbone.read_bytes() == original
    shutil.rmtree(failed)
    (outputs[0] / 'runtime_receipt.json').write_text('private metadata truncation')
    shutil.rmtree(outputs[0])
    backbone.unlink()
    assert (outputs[1] / 'input/backbone.pdb').read_bytes() == original
    assert (outputs[1] / 'source_backbone.pdb').read_bytes() == original
    assert json.loads((outputs[1] / 'runtime_receipt.json').read_text())['effective_settings'] == settings


def test_reattachment_keeps_existing_gzip_transform_private(tmp_path, monkeypatch):
    import attach_shape_post_refold as owner
    candidate, evidence, sequences = inputs(tmp_path)
    metadata_path = candidate / 'candidate_bundle.json'
    metadata = json.loads(metadata_path.read_text())
    metadata['status'] = 'accepted'
    metadata_path.write_text(json.dumps(metadata))
    old_transform = candidate / 'candidate.post_refold.cif.gz'
    old_transform.write_bytes(b'old task-specific gzip transform')
    # Coordinate science isn't the subject: exercise the actual later gzip
    # writer and metadata writers while labelling the evaluator as inert.
    def inert_evaluator(**kw):
        payload = {'status': 'accepted', 'schema': 'inert'}
        kw['output_path'].write_text(json.dumps(payload))
        return payload
    monkeypatch.setattr(owner, 'evaluate_post_refold', inert_evaluator)
    sibling = tmp_path / 'sibling.gz'
    os.link(old_transform, sibling)
    before = {p.name: p.read_bytes() for p in candidate.iterdir()}
    output = tmp_path / 'attached'
    attach(tmp_path, output, candidate, evidence, sequences)
    assert not (output / old_transform.name).samefile(old_transform)
    assert (output / old_transform.name).read_bytes() != before[old_transform.name]
    assert {p.name: p.read_bytes() for p in candidate.iterdir()} == before
    assert sibling.read_bytes() == before[old_transform.name]


@pytest.mark.parametrize('engine', ['skip', 'proteinmpnn', 'fampnn', 'caliby_experimental'])
def test_selected_shape_plan_retains_helper_dependency(engine):
    from model_registry import selected_execution_metadata
    from component_runtime import SelectedExecutionPlan, SourceIdentity, canonical_bytes
    request = {'sequence_policy': 'skip' if engine == 'skip' else 'auto',
               'sequence_engine': engine if engine != 'skip' else None,
               'sequences_per_backbone': 0 if engine == 'skip' else 1,
               'validator_suite': [] if engine == 'skip' else ['esmfold2', 'boltz2', 'protenix_v2']}
    params = {'shape_request': request}
    entry = 'workflows/shape_blueprint_design.nf'
    metadata = selected_execution_metadata('protein_modification_experimental', 'shape_blueprint', params, entry)
    selected = SelectedExecutionPlan(SourceIdentity('a' * 40, 'b' * 40), 'shape_blueprint_design',
        'protein_modification_experimental', 'shape_blueprint', entry, b'{}', b'{}', canonical_bytes(params), metadata)
    paths = {d.relative_path for d in selected.dependencies if d.kind == 'support_tool'}
    # BuildShapeResult is unconditionally selected, so the shared stdlib helper
    # is already in both skip and sequence selected-source unions. No new registry.
    assert 'scripts/shape_blueprint/build_shape_result.py' in paths
    assert any(d.kind == 'source_release' for d in selected.dependencies)


def test_actual_archive_relocated_cli_imports_without_checkout(tmp_path):
    from services.remote_execution.bundle import _staged_source_archive
    repo = tmp_path / 'source-fixture'
    scripts = repo / 'scripts/shape_blueprint'
    scripts.mkdir(parents=True)
    # Tiny source-only repo, all real Shape Python owners, no images or assets.
    for path in SCRIPTS.glob('*.py'):
        shutil.copyfile(path, scripts / path.name)
    lib = repo / 'scripts/lib'
    lib.mkdir()
    shutil.copyfile(ROOT / 'scripts/lib/native_diagnostics.py', lib / 'native_diagnostics.py')
    def git(*args):
        return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    git('init', '-q')
    git('add', 'scripts')
    git('-c', 'user.name=Shape fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'inert source closure')
    revision = git('rev-parse', 'HEAD')
    relocated = tmp_path / 'relocated'
    _staged_source_archive(repo, tmp_path / 'private-data', revision, relocated)
    helper = relocated / 'scripts/shape_blueprint/build_shape_result.py'
    assert helper.read_bytes() == (SCRIPTS / helper.name).read_bytes()
    shutil.rmtree(repo)
    environment = {**os.environ, 'PYTHONPATH': str(relocated)}
    # EvaluateShapeCandidate's full CLI requires torch in its selected native
    # image (not present in this locked API interpreter). Qualify its exact
    # retain_file import below without substituting a fake torch runtime.
    for name in ('build_shape_skip_bundle.py', 'run_shape_sequence.py',
                 'run_shape_validator_suite.py', 'attach_shape_post_refold.py', 'build_shape_result.py'):
        result = subprocess.run([sys.executable, str(relocated / 'scripts/shape_blueprint' / name), '--help'],
            cwd=tmp_path, env=environment, text=True, capture_output=True)
        assert result.returncode == 0, (name, result.stdout, result.stderr)
    isolated_import = '''import ast, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
tree = ast.parse((root / 'evaluate_shape_candidate.py').read_text())
node, = [n for n in tree.body if isinstance(n, ast.Try) and any(isinstance(c, ast.ImportFrom) and c.module == 'build_shape_result' for c in n.body)]
namespace = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), 'retained-source-import', 'exec'), namespace)
assert Path(sys.modules[namespace['retain_file'].__module__].__file__).resolve() == root / 'build_shape_result.py'
'''
    result = subprocess.run([sys.executable, '-c', isolated_import, str(helper.parent)],
        cwd=tmp_path, env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    # Also exercise package-style fallback, rather than only sys.path[0] imports.
    result = subprocess.run([sys.executable, '-c',
        'from scripts.shape_blueprint.build_shape_skip_bundle import retain_file; '
        'from scripts.shape_blueprint.run_shape_sequence import retain_file; '
        'assert retain_file.__module__ == "scripts.shape_blueprint.build_shape_result"'],
        cwd=tmp_path, env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
