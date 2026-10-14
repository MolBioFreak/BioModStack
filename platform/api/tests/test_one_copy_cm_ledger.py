"""Real CM ledger/finalizer on tiny recorded output fixtures, never inference."""
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from tests.test_one_copy_cm_bindings import load_script
from tests.test_conformational_mapping_confornets import _copy_fixture, _fixture_snapshot


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def instrumented_output_fixture(tmp_path, monkeypatch):
    binder = load_script('bind_confornets_output_ledger', monkeypatch)
    from confornets_source_closure import required_source_paths
    root = _copy_fixture(tmp_path)
    native = root/'native'
    request = json.loads((root/'request.json').read_bytes())
    plan = json.loads((root/'cm_coordinate_plan_v1.json').read_bytes())
    identity = request['confornets']['backend_identity']
    selected = {key: identity[key] for key in ('backend_commit', 'runtime_identity', 'container_digest', 'feature_identity_sha256')}
    selected['checkpoint_sha256'] = request['confornets']['checkpoint']['sha256']
    # Explicit fixture evidence; these are not claimed to be executed model sources.
    sources = [dict(relative_path=name, bytes=17, sha256=hashlib.sha256(b'fixture boundary').hexdigest())
               for name in required_source_paths(request['confornets']['task'])]
    driver = {'diversity':'run_diversity_bounded.py', 'mse':'scripts/run_mse_training.py', 'transfer':'scripts/run_transfer.py'}[request['confornets']['task']]
    attestation = dict(schema_name='cm_confornets_runtime_attestation', schema_version=2,
        status='container_executed', request_sha256=request['request_sha256'],
        coordinate_plan_sha256=plan['coordinate_plan_sha256'], **selected, executed_sources=sources,
        commands=[['python', '/fixture/preprocess.py'], ['python', '/fixture/'+driver]])
    (native/'raw').mkdir(exist_ok=True)
    (native/'raw/cm_confornets_runtime_attestation_v1.json').write_text(json.dumps(attestation))
    samples_path = native/'samples.json'
    samples = json.loads(samples_path.read_bytes())
    entries = []
    for index, (sample, coordinate) in enumerate(zip(samples, plan['coordinates'], strict=True)):
        source_name = f'fixture_source_{index}.cif'
        source = native/'raw'/source_name
        os.link(native/sample['relative_path'], source)
        sample['source_relative_path'] = source_name
        entries.append(dict(coordinates=coordinate,
            runtime_coordinates={key: coordinate[key] for key in binder._RUNTIME_COORDINATE_FIELDS},
            source_relative_path=source_name, bytes=source.stat().st_size, sha256=digest(source),
            request_sha256=request['request_sha256'], coordinate_plan_sha256=plan['coordinate_plan_sha256'],
            runtime_identity=selected['runtime_identity'], container_digest=selected['container_digest'],
            checkpoint_sha256=selected['checkpoint_sha256']))
    samples_path.write_text(json.dumps(samples))
    (native/'raw/cm_upstream_coordinate_ledger_v1.jsonl').write_text('\n'.join(json.dumps(row) for row in entries))
    (native/'cm_output_coordinate_ledger_v1.json').unlink()
    snapshot = root/'cm_complex_snapshots_v1.json'
    snapshot.write_text(json.dumps([_fixture_snapshot()]))
    return root, binder


def test_separate_ledger_and_one_closure_without_native_attachment_copy(tmp_path, monkeypatch):
    root, binder = instrumented_output_fixture(tmp_path, monkeypatch)
    native = root/'native'
    original = {p.relative_to(native):p.read_bytes() for p in native.rglob('*') if p.is_file()}
    ledger = tmp_path/'ledger'
    real_copytree = shutil.copytree
    def no_attachment_copy(*args, **kwargs):
        pytest.fail('ledger attachment must not copy native bodies')
    monkeypatch.setattr(shutil, 'copytree', no_attachment_copy)
    binder.bind(root/'request.json', root/'cm_coordinate_plan_v1.json', native, ledger)
    assert {p.name for p in ledger.iterdir()} == {'execution_receipt.json', 'cm_output_coordinate_ledger_v1.json', 'cm_native_file_ledger_v1.json'}
    assert {p.relative_to(native):p.read_bytes() for p in native.rglob('*') if p.is_file()} == original
    monkeypatch.setattr(shutil, 'copytree', real_copytree)
    finalizer = load_script('finalize_confornets_conformational_mapping', monkeypatch)
    copies = []
    real_copy2 = shutil.copy2
    def count_copy(source, destination, *args, **kwargs):
        copies.append((Path(source).name, Path(source).stat().st_size))
        return real_copy2(source, destination, *args, **kwargs)
    def counted_tree(source, destination, *args, **kwargs):
        if not args:
            kwargs['copy_function'] = count_copy
        return real_copytree(source, destination, *args, **kwargs)
    monkeypatch.setattr(shutil, 'copytree', counted_tree)
    output = tmp_path/'final'
    finalizer.finalize(root/'request.json', native, output, root/'cm_complex_snapshots_v1.json', ledger)
    assert len(copies) == len(original)
    for relative, payload in original.items():
        assert (output/'native'/relative).read_bytes() == payload
    copied_bytes = sum(size for _,size in copies)
    assert copied_bytes == sum(map(len, original.values()))
    print(json.dumps(dict(native_fixture_bytes=copied_bytes, ledger_attachment_copy_bytes=0,
                         final_native_closure_copy_bytes=copied_bytes,
                         avoided_attachment_bytes=copied_bytes)))
    # Independently retained output survives the disposable native/ledger closure.
    shutil.rmtree(native); shutil.rmtree(ledger)
    assert (output/'native/conformers/candidate_000.cif').is_file()


def test_real_nextflow_thin_ledger_finalize_and_resume(tmp_path, monkeypatch):
    import subprocess
    import sys
    from tests.test_one_copy_cm_bindings import ROOT
    root, _ = instrumented_output_fixture(tmp_path, monkeypatch)
    jar = Path(os.environ.get('BMS_TEST_NEXTFLOW_JAR') or
               Path.home() / '.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')
    if not jar.is_file() or not shutil.which('java'):
        pytest.skip('existing offline Nextflow JAR and Java required; no downloads')
    # Real task definitions and Python finalizers; no model process is included.
    workflow = tmp_path/'probe.nf'
    workflow.write_text(f'''nextflow.enable.dsl=2
include {{ BindCanonicalConforNetsOutputLedger; FinalizeCanonicalConforNets }} from '{ROOT}/modules/conformational_mapping_confornets.nf'
params.code_root = '{ROOT}'
workflow {{
    nativeResults = Channel.value(file('{root}/native'))
    docs = Channel.value(tuple(file('{root}/request.json'), file('{root}/cm_coordinate_plan_v1.json')))
    finalDocs = Channel.value(tuple(file('{root}/request.json'), file('{root}/cm_coordinate_plan_v1.json'), file('{root}/cm_complex_snapshots_v1.json')))
    BindCanonicalConforNetsOutputLedger(docs, nativeResults)
    FinalizeCanonicalConforNets(finalDocs, nativeResults, BindCanonicalConforNetsOutputLedger.out.results_dir)
    FinalizeCanonicalConforNets.out.canonical_dir.view {{ "FINAL_PATH=" + it }}
}}
''')
    config = tmp_path/'probe.config'
    config.write_text(f"params.code_root='{ROOT}'\nprocess.executor='local'\nprocess.cpus=1\nprocess.memory='256 MB'\n")
    env = dict(os.environ, NXF_HOME=str(tmp_path/'nxf'),
               NXF_VER='25.10.1', NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true',
               PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'])
    command = ['java', '--add-opens=java.base/java.lang=ALL-UNNAMED',
               '--add-opens=java.base/java.util=ALL-UNNAMED',
               '--add-opens=java.base/java.nio=ALL-UNNAMED', '-jar', str(jar),
               '-C', str(config), 'run', str(workflow), '-w', str(tmp_path/'work'), '-ansi-log', 'false']
    first = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    (tmp_path/'nextflow-first.log').write_text(first.stdout+first.stderr)
    assert first.returncode == 0, first.stdout+first.stderr
    tasks = list((tmp_path/'work').glob('*/*/.command.sh'))
    assert len(tasks) == 2
    for task in tasks:
        assert (task.parent/'native').is_symlink()
        assert not (task.parent/'registered').exists()
    before = {str(p): p.stat().st_mtime_ns for p in (tmp_path/'work').rglob('cm_ensemble_v1.json')}
    resumed = subprocess.run([*command, '-resume'], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    (tmp_path/'nextflow-resume.log').write_text(resumed.stdout+resumed.stderr)
    assert resumed.returncode == 0, resumed.stdout+resumed.stderr
    assert 'Cached process' in resumed.stdout
    assert {str(p): p.stat().st_mtime_ns for p in (tmp_path/'work').rglob('cm_ensemble_v1.json')} == before


def test_retained_result_link_keeps_parent_lifetime_and_mode(tmp_path, monkeypatch):
    post = load_script('postprocess_conformational_mapping_frustrampnn_v2', monkeypatch)
    source = tmp_path/'source'; source.write_bytes(b'immutable published fixture'); source.chmod(0o444)
    before = source.stat()
    child = tmp_path/'child'
    post._retain_result_file(source, child)
    assert child.stat().st_ino == before.st_ino
    assert source.stat().st_mode == before.st_mode
    source.unlink()
    assert child.read_bytes() == b'immutable published fixture'


def test_contained_leaf_alias_and_hardlink_native_readers(tmp_path, monkeypatch):
    finalizer = load_script('finalize_confornets_conformational_mapping', monkeypatch)
    root = _copy_fixture(tmp_path)
    native = root/'native'
    source = native/'conformers/candidate_000.cif'
    alias = native/'raw/alias.cif'; alias.parent.mkdir(exist_ok=True); alias.symlink_to(source)
    assert finalizer._contained_file(native, 'raw/alias.cif', label='fixture') == source
    os.link(source, native/'raw/hard.cif')
    assert 'raw/hard.cif' in finalizer._validate_native_tree(native)
    outside = tmp_path/'outside'; outside.write_bytes(b'escape')
    alias.unlink(); alias.symlink_to(outside)
    with pytest.raises(finalizer.FinalizationError, match='escape'):
        finalizer._validate_native_tree(native)
