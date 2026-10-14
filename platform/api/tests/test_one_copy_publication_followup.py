"""WF-10/WF-16/PUB-08 inert custody qualification; no native inference."""
from pathlib import Path
import errno
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pyarrow as pa
import pytest

from services.frustrampnn import manifests, runtime
from services.scientific_artifacts import writer
from scripts import stage_frustrampnn_parent_candidate as candidate
from scripts import run_frustrampnn_parent_fanout as fanout
from scripts.lib.local_msa import cache
from test_one_copy_publication_frustra import bundle
from test_remote_close_confornets import staged, package_fixture, upstream_namespace, adapter, compiled as confor_compiled



def test_cm_delivered_retainer_real_shared_manifest_receiver(tmp_path, monkeypatch, record_property):
    from test_frustrampnn_manifests import _v2_bundle
    from scripts import postprocess_conformational_mapping_frustrampnn_v2 as cm
    assert Path(cm.__file__).resolve() == ROOT / 'scripts/postprocess_conformational_mapping_frustrampnn_v2.py'
    assert cm.validate_result_manifest is manifests.validate_result_manifest
    native = tmp_path / 'native'
    manifest, _ = _v2_bundle(native, monkeypatch)
    expected = manifests.validate_result_manifest(native, manifest)
    staged = tmp_path / 'staged'; staged.symlink_to(native, target_is_directory=True)
    # CM resolves the incoming bundle root, then invokes these real shared readers.
    _, _, loaded = cm.load_result_manifest_bytes_and_document(staged.resolve(strict=True))
    assert cm.validate_result_manifest(staged.resolve(strict=True), loaded) == expected
    retained = tmp_path / 'cm_retained'
    shutil.copytree(staged, retained, copy_function=cm._retain_result_file)
    for name in expected:
        assert (native / name).samefile(retained / name)
    shutil.rmtree(native); staged.unlink()
    assert cm.validate_result_manifest(retained, loaded) == expected
    bad = retained / 'raw_frustrampnn.csv'
    bad.chmod(0o644); bad.write_bytes(bad.read_bytes() + b'tamper')
    with pytest.raises(manifests.ManifestValidationError):
        cm.validate_result_manifest(retained, loaded)
    record_property('cm_tested_source', str(Path(cm.__file__).resolve()))
    record_property('shared_manifest_receiver_mocked', False)




def test_component_cli_consumes_real_nextflow_leaf_aliases(tmp_path, monkeypatch):
    from scripts import run_frustrampnn_component as component
    from services.frustrampnn.contracts import canonical_json_bytes
    from test_frustrampnn_component_phase3 import _v2_inputs, _mock_v2_runtime
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    request, normalized, structure_map, _ = _v2_inputs(inputs, residues=[('A', 1)], selected=[('A', 0)], request_generation=3)
    request_path = inputs / 'request.json'; request_path.write_bytes(canonical_json_bytes(request))
    task = tmp_path / 'task'; task.mkdir()
    for name, source in [('request.json', request_path), ('input.pdb', normalized), ('map.json', structure_map)]:
        (task / name).symlink_to(source)
    _mock_v2_runtime(component, monkeypatch, inputs)
    output = task / 'bundle'
    assert component.main(['--request', str(task / 'request.json'), '--structure', str(task / 'input.pdb'),
        '--structure-map', str(task / 'map.json'), '--output-dir', str(output), '--container', str(inputs / 'mock.sif'),
        '--physical-gpu-id', '3']) == 0
    loaded = manifests.load_result_manifest(output)
    expected = manifests.validate_result_manifest(output, loaded)
    shutil.rmtree(inputs)
    assert manifests.validate_result_manifest(output, loaded) == expected


def test_real_nextflow_remote_pack_and_group(tmp_path, record_property):
    from services.frustrampnn.contracts import canonical_json_bytes
    from test_frustrampnn_component_phase3 import _v2_inputs
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    request, normalized, structure_map, _ = _v2_inputs(inputs, residues=[('A', 1)], selected=[('A', 0)], request_generation=3)
    request_path = inputs / 'request.json'; request_path.write_bytes(canonical_json_bytes(request))
    normalized.chmod(0o444); structure_map.chmod(0o444)
    pipeline = tmp_path / 'groups.nf'
    pipeline.write_text(f"""nextflow.enable.dsl=2
params.api_python = '{sys.executable}'
params.code_root = '{ROOT}'
params.out_dir = '{tmp_path}/results'
include {{ PackRemoteFrustraMPNNRequest; PlanRemoteFrustraMPNNGroups }} from '{ROOT}/modules/frustrampnn_remote.nf'
workflow {{
  PackRemoteFrustraMPNNRequest(Channel.of(tuple('{request['candidate_id']}', file('{request_path}'), file('{normalized}'), file('{structure_map}'))))
  PlanRemoteFrustraMPNNGroups(PackRemoteFrustraMPNNRequest.out.candidate.collect())
}}
""")
    config = tmp_path / 'config'; config.write_text('process.executor = "local"\n')
    command = ['java', '-jar', str(JAR), '-log', str(tmp_path / 'nextflow.log'), 'run', str(pipeline),
               '-c', str(config), '-work-dir', str(tmp_path / 'work')]
    for extra in (['-preview'], []):
        result = subprocess.run(command + extra, cwd=tmp_path, env=dict(os.environ, BMS_REMOTE_ATTEMPT_ID="publication-fixture"), capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
    retained = list((tmp_path / 'work').glob('**/groups/group_*/*/canonical_source.pdb'))
    assert len(retained) == 1 and retained[0].samefile(normalized)
    assert (retained[0].parent / 'frustrampnn_structure_map_v1.json').samefile(structure_map)
    from scripts.remote_frustrampnn_batch import read_candidate, materialize_batch
    actual_request, payloads = read_candidate(retained[0].parent)
    assert actual_request == request
    authority = tmp_path / 'child_authority'
    _, batch = materialize_batch([retained[0].parent], authority)
    retained_child = authority / batch['records'][0]['source_relative_path']
    assert retained_child.samefile(retained[0])
    shutil.rmtree(inputs); shutil.rmtree(tmp_path / 'work')
    assert retained_child.read_bytes() == payloads[1]
    record_property('nextflow_actual_processes', 'PackRemoteFrustraMPNNRequest,PlanRemoteFrustraMPNNGroups')




@pytest.mark.parametrize('module,process', [('confornets_experimental', 'RunConforNets'),
                                           ('conformational_mapping_confornets', 'RunCanonicalConforNets')])
def test_existing_confornets_groovy_with_cached_jar(confor_compiled, tmp_path, monkeypatch, module, process):
    from test_remote_close_confornets import test_native_groovy_caller_uses_managed_source_and_service
    monkeypatch.setenv('BMS_TEST_NEXTFLOW_JAR', str(JAR))
    test_native_groovy_caller_uses_managed_source_and_service(confor_compiled, tmp_path, module, process, monkeypatch)


def test_existing_nextflow_stub_with_cached_distribution(tmp_path, monkeypatch):
    from test_frustrampnn_component_phase3 import test_nextflow_stub_smoke_preserves_candidate_identity
    launcher = tmp_path / 'nextflow'
    launcher.write_text('#!/bin/sh\nexec java -jar ' + str(JAR) + ' "$@"\n')
    launcher.chmod(0o755)
    monkeypatch.setenv('BMS_NEXTFLOW_BIN', str(launcher))
    monkeypatch.setenv('BMS_NEXTFLOW_TEST_HOME', str(tmp_path / 'nxfhome'))
    test_nextflow_stub_smoke_preserves_candidate_identity(tmp_path)


ROOT = Path(__file__).resolve().parents[3]
JAR = Path('/home/dalab/.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')


@pytest.mark.parametrize('kind', ['root', 'leaf', 'hardlink'])
def test_actual_bundle_incoming_aliases(tmp_path, kind):
    source, manifest = bundle(tmp_path)
    incoming = tmp_path / 'incoming'
    if kind == 'root':
        incoming.symlink_to(source, target_is_directory=True)
    else:
        incoming.mkdir()
        for leaf in source.iterdir():
            if kind == 'leaf':
                (incoming / leaf.name).symlink_to(leaf)
            else:
                os.link(leaf, incoming / leaf.name)
    expected = manifests.validate_result_manifest(source, manifest)
    loaded = manifests.load_result_manifest(incoming, input_root=tmp_path)
    assert loaded == manifest
    assert manifests.validate_result_manifest(incoming, loaded, input_root=tmp_path) == expected
    if kind == 'hardlink':
        shutil.rmtree(source)
        assert manifests.validate_result_manifest(incoming, loaded) == expected
    else:
        with pytest.raises(manifests.ManifestValidationError):
            manifests.validate_result_manifest(incoming, loaded, immutable_input=False, input_root=tmp_path)
        with pytest.raises(manifests.ManifestValidationError):
            manifests.validate_result_manifest(incoming, loaded, input_root=tmp_path / 'incoming' if kind == 'leaf' else tmp_path / 'forbidden')




def test_manifest_root_alias_retarget_during_real_read(tmp_path, monkeypatch):
    source, manifest = bundle(tmp_path)
    replacement = tmp_path / 'replacement'; shutil.copytree(source, replacement)
    alias = tmp_path / 'incoming'; alias.symlink_to(source, target_is_directory=True)
    real = manifests._read_regular
    changed = False
    def retarget(*args, **kwargs):
        nonlocal changed
        payload = real(*args, **kwargs)
        if not changed:
            changed = True
            alias.unlink(); alias.symlink_to(replacement, target_is_directory=True)
        return payload
    monkeypatch.setattr(manifests, '_read_regular', retarget)
    with pytest.raises(manifests.ManifestValidationError, match='root alias changed'):
        manifests.validate_result_manifest(alias, manifest, input_root=tmp_path)


def test_alias_tamper_and_outside_caller_root(tmp_path):
    source, manifest = bundle(tmp_path)
    alias = tmp_path / 'alias'
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(manifests.ManifestValidationError):
        manifests.load_result_manifest(alias, input_root=tmp_path / 'missing')
    leaf = source / 'normalized_input.pdb'
    leaf.chmod(0o644)
    leaf.write_bytes(leaf.read_bytes().replace(b'ATOM', b'XXXX', 1))
    with pytest.raises(manifests.ManifestValidationError):
        manifests.validate_result_manifest(alias, manifest, input_root=tmp_path)


def test_runtime_resolved_bind_containment_preserves_output_guards(tmp_path):
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    physical = inputs / 'normalized.pdb'; physical.write_bytes(b'ATOM\n'); physical.chmod(0o444)
    alias = tmp_path / 'input.pdb'; alias.symlink_to(physical)
    output = tmp_path / 'output'; output.mkdir()
    args = dict(apptainer='apptainer', container='/proc/self/fd/42', normalized=alias,
                raw=output / 'raw.csv', output_root=output, physical_gpu_id=0)
    command = runtime.build_frustrampnn_command(**args, input_root=inputs)
    assert f'{physical}:/bms/input/normalized.pdb:ro' in command.argv
    with pytest.raises(runtime.RuntimeValidationError):
        runtime.build_frustrampnn_command(**args)
    with pytest.raises(runtime.RuntimeValidationError):
        runtime.build_frustrampnn_command(**args, input_root=output)
    bad_output = tmp_path / 'output_alias'; bad_output.symlink_to(output, target_is_directory=True)
    with pytest.raises(runtime.RuntimeValidationError):
        runtime.build_frustrampnn_command(**{**args, 'output_root': bad_output}, input_root=inputs)
    with pytest.raises(runtime.RuntimeValidationError):
        runtime.open_verified_container(alias, hashlib.sha256(physical.read_bytes()).hexdigest())


@pytest.mark.parametrize('mutable,exdev', [(False, False), (True, False), (False, True)])
def test_candidate_lifetime_and_mutable_exception(tmp_path, monkeypatch, mutable, exdev):
    source = tmp_path / 'native.pdb'; source.write_bytes(b'ATOM\n')
    if not mutable: source.chmod(0o444)
    alias = tmp_path / 'staged.pdb'; alias.symlink_to(source)
    if exdev:
        def fail(*args, **kwargs): raise OSError(errno.EXDEV, 'fixture cross-device')
        monkeypatch.setattr(candidate.os, 'link', fail)
    retained = tmp_path / 'retained.pdb'
    candidate.stage_source(alias, retained)
    assert source.samefile(retained) == (not mutable and not exdev)
    if mutable:
        source.write_bytes(b'changed\n')
    source.unlink(); alias.unlink()
    assert retained.read_bytes() == b'ATOM\n'
    assert not retained.stat().st_mode & 0o222


def test_confornets_unchanged_assets_private_rewrites_and_reader_lifetime(tmp_path):
    request = staged(tmp_path)
    assets = tmp_path / 'assets'; bench = assets / request['benchmark']
    donor = bench / 'native_payload.json'; donor.write_text(json.dumps({'native': list(range(100))}))
    donor.chmod(0o444)
    old_query = bench / 'query.json'; old_query.write_text('{}'); original = old_query.read_bytes(); old_query.chmod(0o444)
    mutable = bench / 'mutable.txt'; mutable.write_text('legacy')
    batch = bench / 'batch'; batch.mkdir(exist_ok=True)
    (batch / 'native.pt').write_bytes(b'old batch'); (batch / 'native.pt').chmod(0o444)
    source, sha = package_fixture(tmp_path, request)
    output = adapter.prepare(request, assets, tmp_path / 'prepared', source=source, sha256=sha)
    assert donor.samefile(output.parent / donor.name)
    assert not old_query.samefile(output.parent / 'query.json')
    assert old_query.read_bytes() == original
    assert not mutable.samefile(output.parent / mutable.name)
    assert not (batch / 'native.pt').samefile(output.parent / 'batch/native.pt')
    shutil.rmtree(assets); shutil.rmtree(source)
    ns = upstream_namespace()
    chain = ns['InferenceQuerySet'].from_json(output).queries[request['query_id']].chains[0]
    assert ''.join(ns['parse_a3m'](chain.main_msa_file_paths[0].read_text()).msa[0]) == 'ACDE'
    assert json.loads((output.parent / donor.name).read_text())['native'] == list(range(100))


def test_cache_migration_keeps_deepest_generation_and_freshness(tmp_path):
    digest = cache.compute_sequence_hash('ACDE')
    deepest = cache.get_cache_path(str(tmp_path), digest, 'maximum')
    deepest.parent.mkdir(parents=True)
    with gzip.open(deepest, 'wt') as handle: handle.write('>query\nACDE\n>hit\nACDE\n')
    inode = deepest.stat().st_ino
    shallow = cache.get_cache_path(str(tmp_path), digest, 'fast')
    with gzip.open(shallow, 'wt') as handle: handle.write('>query\nACDE\n')
    path, profile = cache.check_cache(str(tmp_path), digest, 30)
    assert profile == 'single' and path.stat().st_ino == inode
    assert not deepest.exists() and not shallow.exists()
    assert cache.load_from_cache(path, str(tmp_path / 'read.a3m')) == '>query\nACDE\n>hit\nACDE\n'
    assert cache.check_cache(str(tmp_path), digest, 30) == (path, 'single')




def test_artifact_digest_link_churn_then_same_mtime_tamper(tmp_path, monkeypatch):
    rows = [{'residue': 1, 'energy': 0.125}, {'residue': 2, 'energy': None}]
    schema = pa.schema([('residue', pa.int64()), ('energy', pa.float64())])
    artifact = writer.install_parquet_rows(root=tmp_path / 'artifacts', owner_kind='job', owner_id='one',
        role='landscape', schema_id='native.v1', schema_version=1, source_sha256='a'*64,
        rows=rows, schema=schema)
    native = artifact.storage_path
    real = os.pread
    called = False
    def churn(fd, count, offset):
        nonlocal called
        if not called:
            called = True
            os.link(native, tmp_path / 'sibling.parquet')
        return real(fd, count, offset)
    with monkeypatch.context() as context:
        context.setattr(writer.os, 'pread', churn)
        assert writer.read_rows(artifact, root=tmp_path / 'artifacts') == rows
    before = native.stat()
    native.chmod(0o644)
    payload = native.read_bytes(); native.write_bytes(b'XXXX' + payload[4:]); native.chmod(0o444)
    os.utime(native, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(writer.ScientificArtifactError):
        writer.read_rows(artifact, root=tmp_path / 'artifacts')


def test_real_nextflow_parent_stage_preview_and_shell(tmp_path, record_property):
    assert JAR.is_file(), 'the assigned cached Nextflow distribution must be available'
    source = tmp_path / 'source.pdb'; source.write_bytes(b'ATOM\n'); source.chmod(0o444)
    pipeline = tmp_path / 'probe.nf'
    pipeline.write_text(f"""nextflow.enable.dsl=2
params.api_python = '{sys.executable}'
params.code_root = '{ROOT}'
include {{ StageFrustraMPNNParentCandidate }} from '{ROOT}/modules/frustrampnn_parent_fanout.nf'
workflow {{
  StageFrustraMPNNParentCandidate(Channel.of(tuple([candidate_id:'c1', parent_job_id:'p1', parent_workflow_id:'structure_prediction', producer_stage:'native', producer_candidate_key:'c1', requiredness:'required'], file('{source}'))))
}}
""")
    config = tmp_path / 'probe.config'; config.write_text('process.executor = "local"\n')
    command = ['java', '-jar', str(JAR), '-log', str(tmp_path / 'nextflow.log'), 'run', str(pipeline),
               '-c', str(config), '-work-dir', str(tmp_path / 'work')]
    for extra in (['-preview'], []):
        result = subprocess.run(command + extra, cwd=tmp_path, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
    outputs = list((tmp_path / 'work').rglob('candidate_c1/source.pdb'))
    assert len(outputs) == 1 and outputs[0].samefile(source)
    scripts = list((tmp_path / 'work').rglob('.command.run'))
    assert len(scripts) == 1 and 'ln -s' in scripts[0].read_text()
    metadata, selected = fanout._candidate_authority(outputs[0].parent, 'p1', 'structure_prediction')
    source.unlink()
    assert metadata['candidate_id'] == 'c1' and selected.read_bytes() == b'ATOM\n'
    record_property('nextflow_version', '25.10.1')
    record_property('actual_stage_shell', str(scripts[0]))
