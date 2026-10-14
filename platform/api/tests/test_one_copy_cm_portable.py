"""Existing compiler/bundle placement owners with no checkpoint input upload."""
import hashlib
import json
from pathlib import Path

from scripts.lib import portable_inputs
from services import nextflow
from services.conformational_mapping.request_builder import materialize_trusted_internal_request
from tests.test_remote_bundle_path_gaps import roots, _place_closure
from tests.test_conformational_mapping_routing import _request_params
from tests.test_one_copy_cm_bindings import load_script


def test_real_compiler_portable_placement_and_canonical_reprep(roots, tmp_path, monkeypatch):
    root = roots['results']/'native-request'; root.mkdir()
    installed = roots['weights']/'openfold3/of3-p2-155k.pt'
    installed.parent.mkdir()
    installed.write_bytes(b'cold-host-installed fixture')
    params = _request_params('confornets')
    params['confornets']['checkpoint'] = dict(path='registered/old-source-id.pt',
        sha256=hashlib.sha256(installed.read_bytes()).hexdigest())
    materialized = materialize_trusted_internal_request(params, output_dir=root,
        request_id='9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='fixture')
    (root/'cm_runtime_registry_v1.json').write_text(json.dumps(params['confornets']['backend_identity']))
    original = materialized.request_path.read_bytes()
    monkeypatch.setattr(nextflow,'get_work_dir',lambda:tmp_path/'work')
    monkeypatch.setattr(nextflow,'resolve_nextflow_executable',lambda:'nextflow')
    invocations = []
    nextflow.build_nextflow_command('conformational_mapping','map',
        dict(materialized.launch_params,gpu_id=0,run_frustrampnn=True),str(roots['results']/'job'),
        job_id='fixture',materialize_inputs=False,native_invocations=invocations)
    invocations[0].materialize_inputs(roots['results']/'job')
    worker_checkpoint = tmp_path/'worker-attempt/materialized/runtime/weights/selected.pt'
    worker_checkpoint.parent.mkdir(parents=True)
    worker_checkpoint.write_bytes(installed.read_bytes())  # one necessary cold worker installation
    runtime = {str(installed):dict(sha256=hashlib.sha256(installed.read_bytes()).hexdigest(),
        size_bytes=installed.stat().st_size,format='pt',path=str(worker_checkpoint))}
    # The shared fixture predates selected file dependencies and supplies an
    # empty runtime_paths set. Supply the same selected roots as the bundle's
    # real caller; retain its actual enumeration/record/envelope implementation.
    from services.remote_execution import bundle
    input_assets = bundle._input_assets
    def selected_input_assets(*args, **kwargs):
        kwargs['runtime_paths'] = {installed}
        return input_assets(*args, **kwargs)
    monkeypatch.setattr(bundle, '_input_assets', selected_input_assets)
    remote, refs, transfer = _place_closure(roots,tmp_path,monkeypatch,invocations[0],runtime_references=runtime)
    runtime_refs = [ref for ref in refs if ref['role']=='runtime']
    assert len(runtime_refs)==1 and runtime_refs[0]['source_path']==str(installed)
    assert not list((remote/'bundle/inputs').rglob('*.pt'))
    mapped = portable_inputs.resolve_input_path(materialized.request_path)
    assert mapped.read_bytes()==original
    assert portable_inputs.resolve_input_path('registered/old-source-id.pt',owner=mapped)==worker_checkpoint
    prep = load_script('prep_canonical_confornets_request',monkeypatch)
    monkeypatch.setattr(prep,'_instrumented_confornets_runtime_available',lambda:True)
    for index in range(2):
        output = tmp_path/f'native-{index}.json'
        prep.prepare(mapped,mapped.parent/'cm_coordinate_plan_v1.json',tmp_path/f'assets-{index}',output)
        assert json.loads(output.read_bytes())['params']['checkpoint_path']==str(worker_checkpoint)
    assert mapped.read_bytes()==original
    assert worker_checkpoint.stat().st_size==installed.stat().st_size
    print(json.dumps(dict(checkpoint_bytes=installed.stat().st_size,checkpoint_input_upload_bytes=0,
                          worker_checkpoint_bodies=1,canonical_prep_count=2)))
