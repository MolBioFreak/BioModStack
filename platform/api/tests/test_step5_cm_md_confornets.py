"""Step 5 completion-case tests: C21 (CM), C22 (MD), C23 (ConforNets), C28 (no fallback/flags)."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import re
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import AnalysisRun, Base, Job
from routers.md_results import get_md_analysis
from services.conformational_mapping import analysis as cm_analysis
from services.conformational_mapping.confornets_mds import (
    CONFORNETS_MDS_ARTIFACT_FILENAME,
    compute_confornets_mds_via_toolkit,
    ingest_confornets_mds_for_job,
)
from services.md.results import _compute_replica_convergence_diagnostics
from services.stats_toolkit_client import StatsToolkitClient, clear_workspace_cache


def _make_cm_fixture():
    policy = {
        "sign_zero_epsilon": 1e-6,
        "clash_detector_id": "bms_sidechain_clash_v1",
        "clash_detector_version": "1",
        "outer_support_minimum": 0.8,
        "inner_support_minimum": 0.6,
        "sign_consistency_minimum": 0.8,
        "clash_free_minimum": 0.9,
        "rank_stability_minimum": 0.6,
        "minimum_common_ranked_universe_size": 3,
    }
    candidates = []
    landscapes = {}
    for seed in (1, 2, 3):
        cid = f"cand_{seed}_0"
        candidates.append({
            "candidate_id": cid,
            "backend_coordinates": {
                "backend": "protenix_v2_ensemble",
                "target_id": "t1",
                "ordered_seed": seed,
                "sample_index": 0,
            },
        })
        residues = []
        for pos in (1, 2, 3):
            residues.append({
                "entity_instance_id": "e1",
                "auth_asym_id": "A",
                "auth_seq_id": pos,
                "insertion_code": "",
                "sequence_index": pos,
                "wt": "A",
                "slots": [
                    {"mutation_aa": "A", "score": 0.0, "status": "ok"},
                    {"mutation_aa": "V", "score": float(pos * 1.5), "status": "ok"},
                ],
            })
        landscapes[cid] = {
            "candidate_id": cid,
            "target_id": "t1",
            "clash_detector_sha256": cm_analysis.CLASH_DETECTOR_SHA256,
            "residues": residues,
        }
    ensemble = {"candidates": candidates}
    return ensemble, landscapes, policy


def test_c21_cm_unavailable_when_toolkit_off(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_workspace_cache()
    monkeypatch.setenv("BMS_STATS_TOOLKIT_URL", "off")
    ensemble, landscapes, policy = _make_cm_fixture()
    result = cm_analysis.analyze_landscapes(ensemble, landscapes, policy=policy)
    assert result["stats_status"] == "unavailable"
    assert result["stats_unavailable_reason"] == "stats_toolkit_unavailable"
    assert result["stats_runs"] == []
    assert all(row["status"] == "insufficient_support" for row in result["results"])
    assert all(row["hierarchical_mean"] is None for row in result["results"])
    assert all(row["components"].get("rank_stability") is None for row in result["results"])
    for deleted in ("_sample_variance", "_hierarchical", "_hierarchical_fraction", "_average_ranks", "_spearman"):
        assert not hasattr(cm_analysis, deleted)


@pytest.mark.asyncio
async def test_short_md_trace_opens_without_creating_an_unusable_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from test_md_analysis_results import _completed_md_report_tree

    record, _, _, _ = _completed_md_report_tree(tmp_path, monkeypatch, point_count=11)
    def unexpected_client():
        pytest.fail("a trace shorter than the Toolkit minimum must not create a run")
    monkeypatch.setattr("services.stats_toolkit_client.get_stats_toolkit_client", unexpected_client)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(Job(id=record.id, name="md", model_id="molecular_dynamics", mode="simulate",
                            status="completed", params=record.params, output_dir=str(tmp_path)))
            await session.commit()
            for _ in range(2):
                report = await get_md_analysis(record.id, session=session)
                assert len(report["reports"][0]["points"]) == 11
                assert report["convergence_diagnostics"]["reason"] == "insufficient_frames"
                assert report["convergence_diagnostics"]["status"] == "unavailable"
                assert report["convergence_diagnostics"]["replicas"] == []
    finally:
        await engine.dispose()


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.asyncio
async def test_c22_md_convergence_persists_reuses_and_recomputes(optional: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clear_workspace_cache()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'md.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    post_runs = 0
    get_runs = 0
    stored_outputs: dict[str, dict[str, Any]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_runs, get_runs
        if request.url.path == "/api/v1/workspaces":
            return httpx.Response(200, json={"workspaces": [{"workspace_id": "ws-md", "name": "BioModStack analyses"}]})
        if request.method == "POST" and request.url.path == "/api/v1/runs":
            post_runs += 1
            run_id = f"run-md-{post_runs}"
            out = {
                "n": 12,
                "tau_int": 1.5,
                "statistical_inefficiency": 4.0,
                "effective_sample_size": 3.0,
                "block_sem": 0.025 * post_runs,
                "naive_sem": 0.012,
                "geweke_z": 0.25,
                "geweke_p_value": 0.80,
                "changepoint_time": 20.0,
                "equilibrated_after_index": 2,
                "drift_slope": 0.0001,
                "drift_p_value": 0.90,
                "converged": True,
                "method_version": "1.0.0",
            }
            stored_outputs[run_id] = out
            return httpx.Response(200, json={
                "run_id": run_id,
                "workspace_id": "ws-md",
                "tool_id": "timeseries.convergence_diagnostics",
                "status": "completed",
                "result": out,
            })
        if request.method == "GET" and request.url.path.startswith("/api/v1/runs/"):
            get_runs += 1
            run_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={
                "run_id": run_id,
                "workspace_id": "ws-md",
                "tool_id": "timeseries.convergence_diagnostics",
                "status": "completed",
                "result": stored_outputs[run_id],
            })
        return httpx.Response(404)

    monkeypatch.setattr(
        "services.stats_toolkit_client.get_stats_toolkit_client",
        lambda **_: StatsToolkitClient(base_url="http://stats.test", transport=httpx.MockTransport(handler)),
    )
    from test_md_analysis_results import _completed_md_report_tree

    record, _, _, _ = _completed_md_report_tree(tmp_path, monkeypatch, point_count=12)
    if optional:
        record.params["md_job_spec"]["analysis"] = {}
    async with Session() as session:
        job = Job(id=record.id, name="md", model_id="molecular_dynamics", mode="simulate", status="completed", params=record.params, output_dir=str(tmp_path))
        session.add(job)
        await session.commit()

        first = await get_md_analysis(record.id, recompute=False, session=session)
        assert post_runs == 1 and get_runs == 0
        assert first["convergence_diagnostics"]["stats_run"]["run_id"] == "run-md-1"
        assert first["convergence_diagnostics"]["replicas"][0]["block_sem"] == pytest.approx(0.025)

        # Second GET reuses persisted AnalysisRun via GET /api/v1/runs/{id}
        second = await get_md_analysis(record.id, recompute=False, session=session)
        assert post_runs == 1 and get_runs == 1
        assert second["convergence_diagnostics"]["stats_run"]["run_id"] == "run-md-1"
        assert second["convergence_diagnostics"]["analysis_run_id"] == first["convergence_diagnostics"]["analysis_run_id"]

        # Recompute creates superseding run
        third = await get_md_analysis(record.id, recompute=True, session=session)
        assert post_runs == 2
        assert third["convergence_diagnostics"]["stats_run"]["run_id"] == "run-md-2"
        rows = (await session.execute(select(AnalysisRun).where(AnalysisRun.analysis_type == "md_convergence_diagnostics").order_by(AnalysisRun.queued_at.asc()))).scalars().all()
        assert len(rows) == 2
        assert rows[0].result_inline_json is None
        assert rows[1].supersedes_run_id == rows[0].id
    await engine.dispose()


@pytest.mark.asyncio
async def test_c23_confornets_controller_mds_ingestion_and_no_worker_mds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clear_workspace_cache()
    # Verify worker script has no MDS computation or _stats_toolkit_rpc
    repo_root = Path(__file__).resolve().parents[3]
    worker_src = (repo_root / "scripts" / "run_confornets_inference.py").read_text(encoding="utf-8")
    assert "_mds_coordinates" not in worker_src
    assert "_stats_toolkit_rpc" not in worker_src
    assert not (repo_root / "scripts" / "_stats_toolkit_rpc.py").exists()

    # Prepare worker pairwise_rmsd_matrix.csv
    result_root = tmp_path / "canonical_confornets"
    eval_dir = result_root / "native" / "evaluation"
    eval_dir.mkdir(parents=True)
    csv_path = eval_dir / "pairwise_rmsd_matrix.csv"
    sample_ids = ["s0", "s1", "s2"]
    dm = [[0.0, 3.0, 4.0], [3.0, 0.0, 5.0], [4.0, 5.0, 0.0]]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", *sample_ids])
        for sid, row in zip(sample_ids, dm):
            w.writerow([sid, *row])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/workspaces":
            return httpx.Response(200, json={"workspaces": [{"workspace_id": "ws-cf", "name": "BioModStack analyses"}]})
        if request.url.path == "/api/v1/runs":
            return httpx.Response(200, json={
                "run_id": "run-cf-mds-1",
                "workspace_id": "ws-cf",
                "tool_id": "multivariate.distance_clustering",
                "status": "completed",
                "result": {"mds_coordinates": [[0.0, 0.0], [3.0, 0.0], [0.0, 4.0]], "method_version": "1.0.0"},
            })
        return httpx.Response(404)

    monkeypatch.setattr(
        "services.conformational_mapping.confornets_mds.get_stats_toolkit_client",
        lambda: StatsToolkitClient(base_url="http://stats.test", transport=httpx.MockTransport(handler)),
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cf.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        job = Job(id="job-cf-1", name="cf", model_id="conformational_mapping", mode="confornets", status="completed", params={}, output_dir=str(tmp_path))
        session.add(job)
        await session.commit()
        payload = await ingest_confornets_mds_for_job(session, job, result_root=result_root)
        await session.commit()
        assert payload is not None
        assert payload["status"] == "available"
        assert payload["stats_run"]["run_id"] == "run-cf-mds-1"
        assert len(payload["coordinates"]) == 3
        assert (result_root / CONFORNETS_MDS_ARTIFACT_FILENAME).is_file()
        run_row = (await session.execute(select(AnalysisRun).where(AnalysisRun.analysis_type == "confornets_mds"))).scalar_one()
        assert run_row.summary_json["stats_run"]["run_id"] == "run-cf-mds-1"
        assert run_row.result_inline_json is None
    await engine.dispose()

    # Toolkit off -> unavailable without local eigendecomposition fallback
    monkeypatch.setenv("BMS_STATS_TOOLKIT_URL", "off")
    monkeypatch.undo()
    monkeypatch.setenv("BMS_STATS_TOOLKIT_URL", "off")
    off_res = compute_confornets_mds_via_toolkit(dm)
    assert off_res["status"] == "unavailable"
    assert off_res["coordinates"] == []


def test_c28_no_forbidden_fallback_symbols_or_flags() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    pattern = re.compile(r"stats_toolkit_required|BMS_STATS_TOOLKIT_REQUIRED|statistics_distribution|_pearson_r|calculate_distribution")
    hits: list[str] = []
    for sub in ("platform/api/services", "platform/api/routers", "scripts"):
        base = repo_root / sub
        for py_file in base.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            if pattern.search(text):
                hits.append(str(py_file.relative_to(repo_root)))
    assert hits == [], f"Forbidden fallback symbols found in: {hits}"
