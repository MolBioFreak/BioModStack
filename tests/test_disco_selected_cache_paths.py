"""Offline real compiler/bundle regression; scratch bytes, not worker proof."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'platform/api'))
sys.path.insert(0, str(ROOT / 'platform/api/tests'))
from test_denovo_remote_dependencies import assets, leaf, MODEL
from services.nextflow import compile_nextflow_invocation
from services.remote_execution import bundle


@pytest.mark.parametrize('custom_root', [False, True])
@pytest.mark.parametrize('custom_checkpoint', [False, True])
def test_selected_checkpoint_retains_managed_hf_cache(assets, tmp_path, custom_root, custom_checkpoint):
    weights = assets['data'] / 'operator-weights' if custom_root else assets['weights']
    checkpoint = leaf(weights / 'disco/DISCO.pt')
    hf = weights / 'disco/huggingface'
    members = ['hub/models--chenglab--CCD/snapshots/pinned/components.cif',
               'hub/models--airkingbd--dplm_650m/snapshots/pinned/model.safetensors']
    for member in members:
        leaf(hf / member)
    leaf(assets['containers'] / 'disco.sif')
    if custom_checkpoint:
        checkpoint = leaf(assets['data'] / 'operator-checkpoint/custom.pt')
    params = dict(generator='disco', backend='disco', design_task='unconditional',
                  target_lengths='100', num_designs=1, weights_root=str(weights))
    if custom_checkpoint:
        params['disco_checkpoint_path'] = str(checkpoint)
    invocation = compile_nextflow_invocation(MODEL, 'de_novo_design', params,
                                             str(tmp_path / 'output'), job_id='offline')
    selected = bundle._runtime_assets(MODEL, 'de_novo_design', invocation.native_parameters,
        native_invocation=invocation, only_kinds=frozenset({'image', 'weights', 'runtime_data'}))
    records = [r for source, name in selected
               for r in bundle._records_for_source(source, name, 'runtime')]
    names = {r.relative_path for r in records}
    assert {'weights/disco/huggingface/' + member for member in members} <= names
    assert checkpoint in {source for source, _ in selected} or checkpoint.parent in {source for source, _ in selected}


def test_prepared_request_portable_relocation_without_originals(assets, tmp_path, monkeypatch):
    import hashlib
    import importlib.util
    import json
    import shutil
    sys.path.insert(0, str(ROOT / 'scripts'))
    from lib import portable_inputs as portable
    def load(name):
        spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    prep = load('prep_protein_cad_request')
    wrapper = load('run_disco_inference')
    original = assets['data'] / 'original'
    checkpoint = leaf(original / 'custom/model.pt')
    hf = original / 'selected-weights/disco/huggingface'
    cache_member = leaf(hf / 'hub/models--chenglab--CCD/snapshots/pinned/components.cif')
    cutlass = original / 'cutlass'
    leaf(cutlass / 'include/cutlass.h')
    prepared = original / 'prepared'
    prepared.mkdir()
    request = prepared / 'request.json'
    monkeypatch.setattr(sys, 'argv', ['prep', '--backend', 'disco', '--task', 'unconditional',
        '--job-id', 'offline', '--job-name', 'offline', '--target-lengths', '100', '--num-designs', '1',
        '--disco-checkpoint-path', str(checkpoint), '--disco-hf-cache-path', str(hf),
        '--disco-cutlass-path', str(cutlass), '--output', str(request),
        '--input-dir', str(prepared / 'inputs')])
    prep.main()
    worker = tmp_path / 'worker'
    runtime_refs = {}
    for source, relative in [(checkpoint, 'weights/disco/DISCO.pt'),
                             (hf, 'weights/disco/huggingface'), (cutlass, 'data/cutlass')]:
        destination = worker / 'runtime' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        records = bundle._records_for_source(source, relative, 'runtime')
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        runtime_refs[str(source)] = dict(path=str(destination),
            format='runtime-directory' if source.is_dir() else 'binary',
            sha256=hashlib.sha256(bundle._canonical_bytes([rec.model_dump(mode='json') for rec in records])).hexdigest()
                if source.is_dir() else records[0].sha256,
            size_bytes=sum(rec.size_bytes for rec in records))
    refs = portable.discover_native_input_references(MODEL, 'de_novo_design',
        {'protein_cad_request': str(request)}, [], output_dir=prepared,
        allowed_roots=[original], runtime_references=runtime_refs)
    assert any(ref['selector'] == ['disco', 'hf_cache_path'] for ref in refs)
    target = worker / 'bundle/inputs/prepared'
    records = bundle._records_for_source(prepared, 'inputs/prepared', 'input')
    transfer = bundle.TransferPlan(prepared, str(target))
    binding, _ = bundle._write_portable_bindings(staging_root=assets['data'],
        remote_attempt=str(worker), references=refs, input_transfers=[transfer],
        input_records=records, remote_runtime=str(worker / 'runtime'),
        remote_results=str(worker / 'results'))
    shutil.copytree(prepared, target)
    Path(binding.remote_destination).parent.mkdir(parents=True)
    shutil.copy2(binding.source, binding.remote_destination)
    shutil.rmtree(original)
    monkeypatch.setenv(portable.ENV, binding.remote_destination)
    monkeypatch.setattr(sys, 'argv', ['wrapper', '--request', str(request),
        '--input-dir', str(target / 'inputs'), '--output-dir', str(worker / 'results')])
    calls = []
    def inert_native(cmd, **kwargs):
        calls.append(cmd)
        assert 'load_checkpoint_path=' + runtime_refs[str(checkpoint)]['path'] in cmd
        env = kwargs['env']
        assert env['HF_HOME'] == runtime_refs[str(hf)]['path']
        assert env['HF_HUB_CACHE'] == runtime_refs[str(hf)]['path'] + '/hub'
        assert env['CUTLASS_PATH'] == runtime_refs[str(cutlass)]['path']
        relocated = Path(env['HF_HOME']) / cache_member.relative_to(hf)
        assert relocated.read_bytes() == b'offline asset fixture'
    monkeypatch.setattr(wrapper.subprocess, 'run', inert_native)
    wrapper.main()
    assert len(calls) == 1
    assert not original.exists()
