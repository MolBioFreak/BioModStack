"""Real adapter replay and publication fault injection, entirely offline."""
import importlib
from pathlib import Path
import shutil

import pytest

from component_runtime import canonical_bytes
from native_components import native_resource_policy
from test_frustrampnn_parent_workflow_fanout import _load_client
from test_remote_frustrampnn_self_contained import prepared


def test_resource_config_preserves_native_setup_and_static_authority(tmp_path):
    from scripts.lib.component_adapter import native_resource_config
    root = Path(__file__).resolve().parents[3]
    components = [{'authority': f'modules/{source}.nf:{name}',
                   'selection_json': {}, 'resources_json': __import__('json').loads(native_resource_policy({}))}
                  for source, name in [('rfd3', 'RunRFD3'), ('rf3', 'RunRF3'),
                                       ('rfdiffusion', 'RunRFDiffusion'),
                                       ('conformational_mapping_protenix', 'CanonicalProtenixEnsemble')]]
    plan = {'metadata': {'static_components': components,
                        'dynamic_templates': [{'selection_json': {'native_process': 'WAIT'}}]}}
    config = native_resource_config(plan, {'required': {'cpus': 8, 'memory_bytes': 4096}},
                                    str(tmp_path / 'compute.lock'), root)
    assert "executor.cpus = 8" in config and "executor.memory = '4096 B'" in config
    assert 'mkdir -p rfd3_results rfd3_trajectories' in config
    assert 'mkdir -p rf3_results' in config and 'mkdir -p outputs schedules .dgl' in config
    assert '--executing-image "${runtime_image}" >/dev/null || exit 1' in config
    assert config.count('flock -x 198') == 4
    assert 'WAIT' not in config


def test_resource_slot_serializes_writers_and_keeps_targets_independent(tmp_path, monkeypatch):
    import ast
    import re
    import subprocess
    from scripts.lib.component_adapter import native_resource_config
    from native_components import PROCESS_CONTRACTS
    monkeypatch.setitem(PROCESS_CONTRACTS, 'science.nf:SCIENCE', ((), (), (), (), ()))
    plan = {'metadata': {'static_components': [{'authority': 'science.nf:SCIENCE',
        'selection_json': {'native_process': 'SCIENCE'},
        'resources_json': __import__('json').loads(native_resource_policy({}))}]}}
    def setup(target):
        config = native_resource_config(plan, {'required': {'cpus': 1, 'memory_bytes': 1024}},
                                        str(tmp_path / target), tmp_path)
        return ast.literal_eval(re.search(r'return (.+?) \+ \(nativeSetup', config)[1])
    # A harmless shell holds the generated descriptor while awaiting stdin.
    first = subprocess.Popen(['bash', '-c', setup('first') + 'printf ready; read -r release'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    second = independent = None
    try:
        assert first.stdout.read(5) == b'ready'
        second = subprocess.Popen(['bash', '-c', setup('first') + 'printf second'], stdout=subprocess.PIPE)
        independent = subprocess.run(['bash', '-c', setup('other') + 'printf independent'],
                                     capture_output=True, timeout=5, check=True)
        assert independent.stdout == b'independent'
        assert second.poll() is None
        first.communicate(b'release\n', timeout=5)
        assert second.communicate(timeout=5)[0] == b'second'
        assert first.returncode == second.returncode == 0
    finally:
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()


@pytest.mark.parametrize("interrupt", [False, True])
def test_planner_exact_replay_and_interrupted_file_publication(tmp_path, monkeypatch, interrupt):
    planner = importlib.import_module("scripts.plan_frustrampnn_groups")
    inputs = [tmp_path / f"input-{i}" for i in range(3)]
    for i, directory in enumerate(inputs):
        prepared(directory, i)
    output = tmp_path / "groups"
    original = planner.durable_write
    writes = 0
    def interrupted_write(path, payload):
        nonlocal writes
        if path.parent.name == ".staging":
            writes += 1
            if writes == 2:
                # Simulate a process dying with an incomplete unpublished file.
                path.write_bytes(payload[:1])
                raise OSError("interrupted copy")
        original(path, payload)
    if interrupt:
        monkeypatch.setattr(planner, "durable_write", interrupted_write)
        with pytest.raises(OSError, match="interrupted copy"):
            planner.materialize_groups(inputs, output, attempt_id="attempt")
        monkeypatch.setattr(planner, "durable_write", original)
    plan = planner.materialize_groups(inputs, output, attempt_id="attempt")
    assert planner.materialize_groups(list(reversed(inputs)), output, attempt_id="attempt") == plan
    # Physical relocation of input directories must not change authority.
    relocated = tmp_path / "relocated"
    shutil.copytree(inputs[0], relocated)
    assert planner.materialize_groups([relocated, *inputs[1:]], output, attempt_id="attempt") == plan
    for group in output.glob("group_*"):
        for member in group.iterdir():
            request = (member / planner.REQUEST).read_bytes()
            source = next(d for d in inputs if (d / planner.REQUEST).read_bytes() == request)
            assert planner.tree_authority(member) == planner.tree_authority(source)


@pytest.mark.parametrize("tamper", ["published", "source", "extra", "link", "plan"])
def test_planner_replay_rejects_altered_materialization(tmp_path, tamper):
    planner = importlib.import_module("scripts.plan_frustrampnn_groups")
    source = tmp_path / "input"
    prepared(source, 0)
    output = tmp_path / "groups"
    planner.materialize_groups([source], output, attempt_id="attempt")
    destination = output / "group_000000/candidate_000000"
    if tamper == "source":
        (source / "unexpected.txt").write_bytes(b"changed")
    elif tamper == "published":
        (destination / planner.REQUEST).write_bytes(b"truncated")
    elif tamper == "extra":
        (destination / "unexpected.txt").write_bytes(b"changed")
    elif tamper == "link":
        (destination / planner.REQUEST).unlink()
        (destination / planner.REQUEST).symlink_to(source / planner.REQUEST)
    else:
        (output / "grouping_plan_v1.json").write_bytes(b"changed")
    with pytest.raises(ValueError, match="conflict|unsafe"):
        planner.materialize_groups([source], output, attempt_id="attempt")


@pytest.mark.parametrize("change", ["origin", "type", "filename", "bytes", "settings", "placement"])
def test_local_request_replay_fences_semantics_before_post(tmp_path, monkeypatch, change):
    client = _load_client()
    directory = tmp_path / "candidate"
    directory.mkdir()
    (directory / "metadata.json").write_bytes(canonical_bytes(dict(
        candidate_id="candidate-0", parent_job_id="parent", parent_workflow_id="protein_design",
        producer_stage="protein_design:terminal", producer_candidate_key="terminal/a.pdb",
        requiredness="required")))
    (directory / "source.pdb").write_bytes(b"ATOM\nEND\n")
    args = dict(parent_job_id="parent", parent_workflow_id="protein_design",
                settings_json=canonical_bytes(dict(batching_enabled=True, structures_per_job=2)).decode(),
                candidate_dirs=[directory], output_receipt=tmp_path / "terminal.json",
                output_bundles=tmp_path / "bundles", capability="test-only")
    calls = []
    def post(*_args, **kwargs):
        calls.append(kwargs)
        raise OSError("recorded POST; no network")
    monkeypatch.setattr(client.requests, "post", post)
    with pytest.raises(OSError, match="recorded POST"):
        client.execute_parent_fanout(**args)
    if change == "origin":
        args["settings_value_origin"] = "operator_request"
    elif change in {"type", "filename"}:
        (directory / "source.pdb").rename(directory / ("source.cif" if change == "type" else "source.PDB"))
    elif change == "bytes":
        (directory / "source.pdb").write_bytes(b"changed")
    elif change == "settings":
        args["settings_json"] = canonical_bytes(dict(batching_enabled=True, structures_per_job=3)).decode()
    else:
        relocated = tmp_path / "relocated"
        shutil.copytree(directory, relocated)
        args.update(candidate_dirs=[relocated], capability="rotated-test-only", api_url="http://relocated-api")
    if change == "placement":
        with pytest.raises(OSError, match="recorded POST"):
            client.execute_parent_fanout(**args)
        assert len(calls) == 2
        assert calls[0]["data"] == calls[1]["data"]
        assert calls[0]["files"] == calls[1]["files"]
    else:
        with pytest.raises(ValueError, match="immutable grouping plan conflicts"):
            client.execute_parent_fanout(**args)
        assert len(calls) == 1


@pytest.mark.parametrize("checkpoint", [False, True])
def test_shared_pump_real_processes_replay_and_native_collection(tmp_path, monkeypatch, checkpoint):
    """Harmless Python recorder processes; no model/image/provider execution."""
    import json
    import sys
    import textwrap
    from component_runtime import NativeInvocation
    from scripts.lib import component_adapter as adapter
    import services.nextflow as compiler

    root_script = textwrap.dedent('''
        import hashlib, json, time
        from pathlib import Path
        from scripts.lib.component_adapter import runtime_from_environment
        from component_runtime import ComponentRequest, ResultReference
        runtime = runtime_from_environment()
        ids = [runtime.submit(ComponentRequest.capture(parent_job_id='root', stage='record', child_key=str(i),
            payload=dict(model_id='recorder', mode='record', params=dict(seed=i)))) for i in range(2)]
        for identity in ids:
            for attempt in range(100):
                row = runtime.child_status(identity)
                if row['status'] == 'execution_finished':
                    break
                if row['status'] == 'failed':
                    raise RuntimeError('recorder process failed')
                time.sleep(.05)
            else:
                raise RuntimeError('recorder timed out')
            # The native collector, not process exit, validates fixture content.
            value = json.loads((Path(row['output_dir']) / 'record.json').read_text())
            assert value == row['params']
            artifact = Path(row['output_dir']) / 'record.json'
            raw = artifact.read_bytes()
            ref = ResultReference(identity, artifact.relative_to(runtime.artifact_root).as_posix(),
                                  hashlib.sha256(raw).hexdigest(), len(raw), 'fixture.record.v1')
            runtime.complete_validated_child(identity, result={'fixture_json_validated': True}, references=[ref])
        if CHECKPOINT:
            artifact = runtime.artifact_root / 'review.json'
            artifact.write_bytes(b'{"review":"fixture"}')
            ref = ResultReference('root', artifact.name, hashlib.sha256(artifact.read_bytes()).hexdigest(),
                                  artifact.stat().st_size, 'review.fixture.v1')
            runtime.checkpoint('explicit-review', component_ids=['root'], artifacts=[ref])
    ''').replace('CHECKPOINT', repr(checkpoint))
    context = tmp_path / "context.json"
    context.write_text(json.dumps(dict(ledger_path=str(tmp_path / 'ledger.sqlite'), artifact_root=str(tmp_path),
        attempt_id='pump-attempt', root_job_id='root', target_id='worker', lease_id='lease',
        parent=dict(id='root', model_id='recorder', mode='record', params={}, execution_target_id='worker'),
        root_command=[sys.executable, '-c', root_script], working_directory=str(tmp_path))))
    compiled = []
    def compile_recorder(request, context):
        compiled.append(request.child_key)
        script = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])"
        return NativeInvocation.capture(model_id='recorder', mode='record',
            command=[sys.executable, '-c', script, str(Path(context['child_output_dir']) / 'record.json'),
                     json.dumps(request.payload['params'])],
            requested=request.payload, effective=request.payload, native_parameters={'out_dir': context['child_output_dir']}, entrypoint='fixture.nf')
    monkeypatch.setattr(compiler, 'compile_component_nextflow_invocation', compile_recorder, raising=False)
    # This process recorder is not Nextflow; real generated resource directives
    # are exercised separately rather than passed to Python as fake NF flags.
    monkeypatch.setattr(adapter, 'resource_bound_command', lambda command, *_args: list(command))
    monkeypatch.setenv('BMS_COMPONENT_CONTEXT', str(context))
    monkeypatch.setenv('APPTAINERENV_BMS_COMPONENT_CONTEXT', str(context))
    assert adapter.run_component_workflow(context) == (75 if checkpoint else 0)
    assert compiled == ['0', '1']
    state = json.loads(context.with_suffix('.state.json').read_text())
    assert state['state'] == ('paused' if checkpoint else 'completed')
    assert state['quiescent'] is True
    assert adapter.run_component_workflow(context) == (75 if checkpoint else 0)
    assert compiled == ['0', '1']  # No duplicate root or child execution on replay.
    runtime = adapter.runtime_from_environment()
    assert all(row['status'] == 'completed' and len(row['references']) == 1 for row in runtime.children('root'))
    if checkpoint:
        assert runtime.pending_checkpoints()[0]['decision'] is None
