from __future__ import annotations

import sys
from pathlib import Path
import pytest


SCRIPTS_ROOT = Path(__file__).resolve().parent

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from collect_maturation_outputs import collect_files, is_final_ppiflow_pdb


def test_is_final_ppiflow_pdb_accepts_redesign_outputs() -> None:
    assert is_final_ppiflow_pdb(Path("demo_ppiflow_sample0.pdb"))
    assert is_final_ppiflow_pdb(Path("demo_ppiflow_seq_7_sample0.pdb"))
    assert not is_final_ppiflow_pdb(Path("demo_enriched_complex.pdb"))
    assert not is_final_ppiflow_pdb(Path("demo_other_model.pdb"))


def test_collect_files_picks_up_redesign_ppiflow_pdbs(tmp_path: Path, monkeypatch) -> None:
    child_dir = tmp_path / "child"
    results_dir = child_dir / "run" / "ppiflow" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "demo_enriched_complex.pdb").write_text("MODEL\nENDMDL\n", encoding="utf-8")
    (results_dir / "demo_ppiflow_seq_7_sample0.pdb").write_text("MODEL\nENDMDL\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    collected = collect_files(
        [str(child_dir)],
        patterns=["*ppiflow*.pdb"],
        subdirs=["run/ppiflow/results"],
        predicate=is_final_ppiflow_pdb,
    )

    assert collected == ["demo_ppiflow_seq_7_sample0.pdb"]
    assert (tmp_path / "demo_ppiflow_seq_7_sample0.pdb").exists()


def test_completed_native_child_directory_cannot_be_skipped(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr("collect_maturation_outputs.component_runtime_enabled", lambda: True)
    with pytest.raises(FileNotFoundError, match="Completed child"):
        collect_files([str(tmp_path / "missing")], ["*.pdb"], [""])


def _collection_runtime(tmp_path, monkeypatch, *, failed=False, required=True):
    import json
    import uuid
    from lib.component_adapter import runtime_from_environment
    from component_runtime import ComponentRequest
    context = dict(ledger_path=str(tmp_path / 'ledger.sqlite'), artifact_root=str(tmp_path),
                   attempt_id=str(uuid.uuid4()), root_job_id='root', target_id='target',
                   lease_id='lease', output_root=str(tmp_path / 'root'))
    (tmp_path / 'root').mkdir()
    path = tmp_path / 'context.json'
    path.write_text(json.dumps(context))
    monkeypatch.setenv('BMS_COMPONENT_CONTEXT', str(path))
    runtime = runtime_from_environment()
    identities, dirs = [], []
    for index in range(2):
        child = runtime.submit(ComponentRequest.capture(parent_job_id='root', stage='maturation',
            child_key=str(index), payload=dict(model_id='fixture', mode='fixture', params={}), required=required))
        runtime.claim(child, owner_id='owner', boot_id='boot')
        out = tmp_path / child
        out.mkdir()
        runtime.bind_native_parent(child, {"id": child, "output_dir": str(out)},
                                   owner_id='owner', boot_id='boot')
        runtime.execution_finished(child, owner_id='owner', boot_id='boot',
                                   output_dir=str(out), exit_code=1 if failed and index else 0)
        identities.append(child)
        if not (failed and index):
            dirs.append(str(out))
    return runtime, dict(child_ids=identities, child_output_dirs=dirs)


def test_native_maturation_exact_collection_replay_and_tamper(tmp_path, monkeypatch):
    import json
    import pytest
    from collect_maturation_outputs import main
    runtime, data = _collection_runtime(tmp_path, monkeypatch)
    for directory in data['child_output_dirs']:
        native = Path(directory) / 'run/ppiflow/results'
        native.mkdir(parents=True)
        (native / 'same_ppiflow.pdb').write_text('native fixture bytes')
        (native / 'same_ppiflow_enriched_complex.pdb').write_text('excluded fixture')
    receipt = tmp_path / 'wait.json'
    receipt.write_text(json.dumps(data))
    for index in range(2):
        work = tmp_path / f'work{index}'
        work.mkdir()
        monkeypatch.chdir(work)
        monkeypatch.setattr(sys, 'argv', ['collect', '--child_outputs_json', str(receipt)])
        main()
        manifest = json.loads(Path('collection_manifest.json').read_text())
        assert manifest['count_pdbs'] == 1  # Existing cross-child basename dedupe.
        assert manifest['component_collection']['exact_join'] is True
    rows = runtime.join_children(data['child_ids'])
    assert all(row['result']['native_collection']['scientific_validation'] is False for row in rows)
    assert [len(row['result']['native_collection']['files']) for row in rows] == [1, 0]
    first = Path(data['child_output_dirs'][0]) / 'run/ppiflow/results/same_ppiflow.pdb'
    first.write_text('tampered')
    with pytest.raises(ValueError):
        runtime.join_children(data['child_ids'])


def test_inline_antibody_collectors_use_actual_native_sets(tmp_path, monkeypatch):
    import json
    import textwrap
    from child_job_utils import complete_native_collection
    source = (SCRIPTS_ROOT.parent / 'workflows/antibody_denovo.nf').read_text()
    for name in ('CollectChildOutputs', 'CollectFAMPNNOutputs'):
        case = tmp_path / name
        case.mkdir()
        runtime, data = _collection_runtime(case, monkeypatch)
        for directory in data['child_output_dirs']:
            native = Path(directory) / 'pdb_files'
            native.mkdir()
            (native / 'sample.pdb').write_text('native fixture')
            (native / 'sample.json').write_text('{}')
            (native / 'sample.trb').write_text('native metadata fixture')
        wait = case / 'wait.json'
        wait.write_text(json.dumps(data))
        block = source.split('process ' + name + ' {', 1)[1].split('\nprocess ', 1)[0]
        code = textwrap.dedent(block.split('    """', 1)[1].rsplit('    """', 1)[0])
        code = code.replace('${child_outputs_json}', str(wait)).replace('${stage_name}', name)
        code = code.replace('${params.code_root}', str(SCRIPTS_ROOT.parent))
        work = case / 'work'
        work.mkdir()
        monkeypatch.chdir(work)
        exec(compile(code, name, 'exec'), {})
        manifest = json.loads(Path('collection_manifest.json').read_text())
        assert manifest['component_collection']['exact_join']
        rows = runtime.join_children(data['child_ids'])
        suffixes = [{Path(f['path']).suffix for f in row['result']['native_collection']['files']} for row in rows]
        assert suffixes == ([{'.pdb', '.trb'}] * 2 if name == 'CollectChildOutputs' else [{'.pdb', '.json'}] * 2)


def test_native_validation_shell_collection_keeps_sidecars_and_dedupe(tmp_path, monkeypatch):
    import json
    import subprocess
    runtime, data = _collection_runtime(tmp_path, monkeypatch)
    for directory in data['child_output_dirs']:
        native = Path(directory) / 'pdb_files'
        (native / 'aligned_error').mkdir(parents=True)
        for filename in ('sample.pdb', 'sample.cif', 'sample.json', 'sample.npz', 'aligned_error/full.json'):
            (native / filename).write_text('native fixture')
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'wait_result.json').write_text(json.dumps(data))
    source = (SCRIPTS_ROOT.parent / 'workflows/antibody_denovo.nf').read_text()
    block = source.split('process WaitAndAggregateChildResults {', 1)[1].split('\nworkflow ', 1)[0]
    code = block.split('    """', 1)[1].rsplit('    """', 1)[0]
    # Only replace the wait invocation: exercise the actual shell collection.
    start = code.index('    # Wait for all children')
    end = code.index('    # Parse wait result')
    code = code[:start] + code[end:]
    for key, value in {'params.code_root': str(SCRIPTS_ROOT.parent), 'params.out_dir': str(tmp_path / 'root'),
                       'expected_child_count': '2', 'parent_job_id': 'root', 'batch_name': 'fixture'}.items():
        code = code.replace('${' + key + '}', value)
    code = code.replace("${'$'}", '$').replace('\\$', '$').replace('\\\\', '\\')
    result = subprocess.run(['bash', '-c', code], cwd=work, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((work / 'aggregation_report.json').read_text())
    assert report['component_collection']['exact_join']
    rows = runtime.join_children(data['child_ids'])
    assert [len(row['result']['native_collection']['files']) for row in rows] == [5, 0]


@pytest.mark.parametrize('required', [False, True])
def test_native_partial_collection_preserves_requiredness(tmp_path, monkeypatch, required):
    from child_job_utils import complete_native_collection
    runtime, data = _collection_runtime(tmp_path, monkeypatch, failed=True, required=required)
    if required:
        with pytest.raises(RuntimeError, match='required'):
            complete_native_collection(data, {d: [] for d in data['child_output_dirs']}, authority='fixture')
    else:
        report = complete_native_collection(data, {d: [] for d in data['child_output_dirs']}, authority='fixture')
        assert report['exact_join'] is True
    assert runtime.child_status(data['child_ids'][1])['status'] == 'failed'


def test_stage_reporter_local_and_worker_current_identity_no_http(tmp_path, monkeypatch):
    import json
    import pytest
    import stage_reporter
    runtime, data = _collection_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(stage_reporter.requests, 'post', lambda *a, **k: pytest.fail('HTTP callback'))
    for remote in ('0', '1'):
        monkeypatch.setenv('BMS_REMOTE_EXECUTION', remote)
        monkeypatch.setenv('BMS_REMOTE_JOB_ID', 'root')
        for identity, directory in [('root', str(tmp_path)),
                                     (data['child_ids'][0], data['child_output_dirs'][0])]:
            monkeypatch.setenv('BMS_COMPONENT_JOB_ID', identity)
            monkeypatch.setenv('BMS_COMPONENT_OUTPUT_DIR', directory)
            monkeypatch.setattr(sys, 'argv', ['report', identity, 'maturation', 'complete'])
            stage_reporter.main()
            saved = json.loads((Path(directory) / '.bms-stage-receipts/maturation.terminal.json').read_text())
            assert saved['job_id'] == identity and saved['attempt_id'] == runtime.attempt_id
    monkeypatch.setenv('BMS_COMPONENT_OUTPUT_DIR', str(tmp_path / 'root'))
    with pytest.raises(SystemExit):
        stage_reporter.main()
    monkeypatch.setenv('BMS_COMPONENT_JOB_ID', 'foreign')
    monkeypatch.setattr(sys, 'argv', ['report', 'foreign', 'maturation', 'complete'])
    with pytest.raises(SystemExit):
        stage_reporter.main()
