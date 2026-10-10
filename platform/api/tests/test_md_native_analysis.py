from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.bms_md.analysis import write_analysis_report
from scripts.bms_md.collect_analysis import collect_analysis, run_native_wham
from services.md.results import analysis_report, artifact_inventory, completion_barrier, apply_completion_barrier, MDResultError
from test_md_analysis_results import _md_job_tree, _record
from test_md_results_trim import store, _tree, _seed
from database import Job, MdRun, JobArtifact, MdAttemptSegment
from sqlalchemy import select
from services.md.completion import validate_and_finalize_md_job
from routers.md_results import get_md_analysis, get_md_summary


@pytest.mark.asyncio
async def test_optional_failed_analysis_finalizes_in_scratch_store_and_reopens_routes(store, tmp_path, monkeypatch):
    import shutil
    _engine, maker = store
    root, spec = _tree(tmp_path, monkeypatch)
    spec['analysis'] = {}
    manifest_path = root / 'replicas/replica_0/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['config'] = spec
    manifest_path.write_text(json.dumps(manifest))
    shutil.rmtree(root / 'analysis')
    await _seed(maker, root, spec)
    async with maker() as session:
        child = await session.get(Job, 'analysis-child-0')
        child.status = child.queue_status = 'failed'
        child.error_message = 'native analysis process exited 1'
        await session.commit()
        parent = await session.get(Job, 'md-job-1')
        snapshot = await validate_and_finalize_md_job(parent, session)
        assert snapshot['dynamics_state'] == 'completed'
        assert snapshot['analysis_state'] == 'failed'
        assert snapshot['analysis_error']['message'] == child.error_message
        await session.commit()
    async with maker() as reader:
        assert (await reader.get(Job, 'md-job-1')).status == 'completed'
        assert (await reader.get(MdRun, 'md-job-1')).phase == 'completed'
        assert list((await reader.scalars(select(JobArtifact))).all())
        summary = await get_md_summary('md-job-1', reader)
        report = await get_md_analysis('md-job-1', reader)
        assert summary['dynamics_state'] == 'completed'
        assert summary['analysis_state'] == 'failed'
        assert report['status'] == 'absent'
        assert report['execution'] == [{'replica': 0, 'job_id': 'analysis-child-0', 'status': 'failed', 'error': 'native analysis process exited 1'}]


@pytest.mark.asyncio
async def test_completion_does_not_rewrite_failed_dynamics_segment(store, tmp_path, monkeypatch):
    _engine, maker = store
    root, spec = _tree(tmp_path, monkeypatch)
    _replica_id, segment_id = await _seed(maker, root, spec)
    async with maker() as session:
        segment = await session.get(MdAttemptSegment, segment_id)
        segment.state = 'failed'
        await session.commit()
        parent = await session.get(Job, 'md-job-1')
        with pytest.raises(MDResultError, match='failed segment'):
            await validate_and_finalize_md_job(parent, session)
        await session.rollback()
    async with maker() as reader:
        assert (await reader.get(MdAttemptSegment, segment_id)).state == 'failed'


def _optional_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path))
    job, manifest, trajectory = _md_job_tree(tmp_path)
    job.params["md_job_spec"]["analysis"] = {}
    document = json.loads(manifest.read_text())
    document["config"] = job.params["md_job_spec"]
    manifest.write_text(json.dumps(document))
    aggregate_path = tmp_path / "manifest.json"
    aggregate = json.loads(aggregate_path.read_text())
    aggregate["lineage"] = dict(completed_children=1, failed_children=0, cancelled_children=0, child_ids=["dynamics"])
    aggregate_path.write_text(json.dumps(aggregate))
    (tmp_path / "md_completion_barrier.json").write_text(json.dumps({
        "schema": "bms.md.completion-barrier.v1", "status": "completed", "job_id": job.id,
        "aggregate_manifest_sha256": hashlib.sha256(aggregate_path.read_bytes()).hexdigest(),
    }))
    return job, manifest, trajectory


def test_optional_analysis_absent_or_corrupt_does_not_hide_completed_dynamics(tmp_path, monkeypatch):
    job, manifest, trajectory = _optional_tree(tmp_path, monkeypatch)
    snapshot = completion_barrier(job)
    assert snapshot["dynamics_state"] == "completed"
    assert snapshot["analysis_state"] == "absent"
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    (analysis / "md_analysis_replica_0.artifacts.json").write_text("{}")
    snapshot = completion_barrier(job)
    assert snapshot["state"] == "completed"
    assert snapshot["analysis_state"] == "failed"
    assert snapshot["analysis_error"]["code"] == "MD_ANALYSIS_ARTIFACT_MANIFEST_INVALID"
    assert artifact_inventory(job)["artifacts"]
    job.status = "cancelled"
    with pytest.raises(MDResultError, match="cancellation"):
        apply_completion_barrier(job, _snapshot=snapshot)
    aggregate = json.loads((tmp_path / "manifest.json").read_text())
    aggregate["status"] = "failed"
    (tmp_path / "manifest.json").write_text(json.dumps(aggregate))
    with pytest.raises(MDResultError):
        completion_barrier(job)


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_optional_analysis_never_promotes_unsuccessful_dynamics(tmp_path, monkeypatch, status):
    job, path, _ = _optional_tree(tmp_path, monkeypatch)
    manifest = json.loads(path.read_text())
    manifest['status'] = status
    path.write_text(json.dumps(manifest))
    with pytest.raises(MDResultError, match='dynamics did not complete'):
        completion_barrier(job)


def test_native_final_structure_without_requested_trajectory_remains_readable(tmp_path, monkeypatch):
    job, path, _ = _optional_tree(tmp_path, monkeypatch)
    manifest = json.loads(path.read_text())
    del manifest['artifacts']['trajectory']
    del manifest['artifacts']['representative_structure']['source_trajectory_sha256']
    path.write_text(json.dumps(manifest))
    assert completion_barrier(job)['dynamics_state'] == 'completed'
    assert artifact_inventory(job)['artifacts']


def test_native_pull_producer_collection_receiver_preserves_failure_and_coordinate_identity(tmp_path, monkeypatch):
    job, manifest_path, _ = _optional_tree(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text())
    coordinates = [dict(coordinate=1, column=1, label="height", unit="nm", groups=["surface", "DNA"], window="w0"),
                   dict(coordinate=2, column=3, label="angle", unit="degree", groups=["A", "B", "C"], window="w0")]
    manifest["config"]["analysis"]["pull_coordinates"] = coordinates
    job.params["md_job_spec"] = manifest["config"]
    pull = manifest_path.parent / "pullx.xvg"
    pull.write_text('@ xaxis label "Time (ps)"\n# native-style inert fixture\n0 1 99 10\n2 2 98 20\n4 3 97 30\n')
    manifest["artifacts"]["pullx"] = _record(pull, manifest_path.parent, semantic_role="pull_coordinates")
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setitem(sys.modules, "MDAnalysis", None)
    child = tmp_path / "child"
    output, ok = write_analysis_report(manifest_path, child / "md_analysis_replica_0.json", runtime_sha256="f" * 64, max_points=2)
    assert not ok  # Missing structural backend remains an actual analysis failure.
    report = json.loads(output.read_text())
    assert report["failure"]["code"] == "MD_ANALYSIS_RUNTIME_UNAVAILABLE"
    assert report["pull_coordinates"][1]["points"] == [{"time_ps": 0.0, "value": 10.0}, {"time_ps": 4.0, "value": 30.0}]
    assert report["pull_coordinates"][0]["histogram"]["sample_count"] == 3
    assert sum(report["pull_coordinates"][0]["histogram"]["counts"]) == 3
    status = tmp_path / "status.json"
    status.write_text(json.dumps(dict(child_output_dirs=[str(child)], failed=1, cancelled=0, child_ids=["analysis"])))
    collected = collect_analysis(status, tmp_path / "manifest.json", tmp_path)
    assert collected["status"] == "partial_failure"
    assert collected["completed_analysis_children"] == 0
    received = analysis_report(job)
    assert received["status"] == "failed"
    assert received["reports"][0]["pull_coordinates"] == report["pull_coordinates"]
    assert received["replica_states"][0]["failure"] == report["failure"]
    snapshot = completion_barrier(job)
    assert snapshot["dynamics_state"] == "completed"
    assert snapshot["analysis_state"] == "failed"


def test_wham_invokes_native_estimator_and_reports_native_failure(tmp_path, monkeypatch):
    job, manifest_path, _ = _optional_tree(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text())
    for filename, role in (("production.tpr", "production_tpr"), ("pullx.xvg", "pull_coordinates")):
        path = manifest_path.parent / filename
        path.write_bytes(b"inert transport fixture, not scientific output")
        manifest["artifacts"][filename] = _record(path, manifest_path.parent, semantic_role=role)
    manifest_path.write_text(json.dumps(manifest))
    calls = []
    def native_failure(command, **kwargs):
        calls.append(command)
        selection = Path(command[command.index("-is") + 1]).read_text()
        assert selection == "0 1\n"
        return SimpleNamespace(returncode=1, stdout="native GROMACS rejected test TPR")
    monkeypatch.setattr("subprocess.run", native_failure)
    request = dict(unit="nm", temperature_k=300, begin_ps=10, end_ps=20,
                   windows=[dict(replica=0, window="w0", coordinate=2, coordinate_count=2)])
    result = run_native_wham({0: manifest_path}, request, tmp_path / "wham")
    assert len(calls) == 1
    assert calls[0][:2] == ["gmx", "wham"]
    assert result["status"] == "failed"
    assert result["returncode"] == 1
    assert result["dimension"] == 1
    assert "points" not in result
    assert result["request"] == request
    assert result["inputs"][0]["window"] == "w0"
