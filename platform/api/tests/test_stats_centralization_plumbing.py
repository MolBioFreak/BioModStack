"""Step 2 BMS plumbing tests for Stats Toolkit centralization (R04-R10 / C05-C12)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import AnalysisRun, Base, Design, Job
from routers.analyses import (
    AnalysisRunRequest,
    get_analysis_run,
    get_job_analysis,
    trigger_job_analysis,
)
from services import analysis_autorun as autorun
from services import analysis_subprocess as worker
from services.analysis_registry import (
    JOB_CORRELATION_MATRIX_ANALYSIS,
    JOB_METRIC_SUMMARY_ANALYSIS,
    SELECTION_BATCH_SUMMARY_ANALYSIS,
    SELECTION_METRIC_SUMMARY_ANALYSIS,
    get_analysis_definition,
)
from services.analysis_runs import (
    get_matching_job_analysis_run,
    request_selection_analysis,
)
from services.stats_toolkit_client import (
    DEFAULT_BMS_WORKSPACE_NAME,
    STATS_TOOLKIT_UNAVAILABLE_REASON,
    StatsToolkitClient,
    StatsToolkitUnavailableError,
    clear_workspace_cache,
)


async def _setup_db(tmp_path: Path):
    db_path = tmp_path / "test_step2.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory, engine


def _bind_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, factory) -> None:
    monkeypatch.setattr(worker, "async_session", factory)
    monkeypatch.setattr("paths.get_analysis_cache_dir", lambda: tmp_path / "analysis_cache")
    monkeypatch.setattr("services.analysis_runs.get_analysis_cache_dir", lambda: tmp_path / "analysis_cache")


class _FakeToolkitServer:
    """In-memory Stats Toolkit HTTP mock supporting workspaces, durable runs, and readback."""

    def __init__(self, *, existing_workspace: bool = False, poll_before_complete: int = 0) -> None:
        self.workspaces: list[dict[str, Any]] = []
        if existing_workspace:
            self.workspaces.append({
                "workspace_id": "ws_existing_bms",
                "name": DEFAULT_BMS_WORKSPACE_NAME,
                "status": "active",
            })
        self.runs: dict[str, dict[str, Any]] = {}
        self.pending_polls: dict[str, int] = {}
        self.poll_before_complete = poll_before_complete
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str]]] = []
        self._seq = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        headers = {k.lower(): v for k, v in request.headers.items()}
        self.calls.append((request.method, request.url.path, body, headers))

        if request.method == "GET" and request.url.path == "/api/v1/workspaces":
            return httpx.Response(200, json={"offset": 0, "limit": 100, "workspaces": list(self.workspaces)})

        if request.method == "POST" and request.url.path == "/api/v1/workspaces":
            self._seq += 1
            ws = {
                "workspace_id": f"ws_created_{self._seq}",
                "name": body["name"],
                "description": body.get("description", ""),
                "status": "active",
            }
            self.workspaces.append(ws)
            return httpx.Response(201, json=ws)

        if request.method == "POST" and request.url.path == "/api/v1/runs":
            self._seq += 1
            run_id = f"run_tk_{self._seq}"
            tool_id = body["tool_id"]
            inputs = body["inputs"]
            if tool_id == "descriptive.summary":
                import math
                cols_out = {}
                for col_name, raw_vals in inputs["columns"].items():
                    missing_count = 0
                    non_finite_count = 0
                    finite: list[float] = []
                    for v in raw_vals:
                        if v is None:
                            missing_count += 1
                        else:
                            fv = float(v)
                            if math.isfinite(fv):
                                finite.append(fv)
                            else:
                                non_finite_count += 1
                    finite.sort()
                    n = len(finite)
                    if n == 0:
                        cols_out[col_name] = {
                            "count": 0,
                            "missing_count": missing_count,
                            "non_finite_count": non_finite_count,
                            "mean": None,
                            "median": None,
                            "std": None,
                            "min": None,
                            "max": None,
                            "q1": None,
                            "q3": None,
                            "iqr": None,
                            "histogram": {
                                "bins": inputs.get("histogram_bins", 10),
                                "edges": [],
                                "counts": [],
                            },
                        }
                    else:
                        mean = sum(finite) / n
                        sd = (
                            math.sqrt(sum((x - mean) ** 2 for x in finite) / (n - 1))
                            if n >= 2
                            else None
                        )
                        def _q7(p: float) -> float:
                            if n == 1:
                                return finite[0]
                            h = (n - 1) * p
                            lo = math.floor(h)
                            hi = math.ceil(h)
                            return finite[lo] + (h - lo) * (finite[hi] - finite[lo])
                        q1 = _q7(0.25)
                        med = _q7(0.5)
                        q3 = _q7(0.75)
                        cols_out[col_name] = {
                            "count": n,
                            "missing_count": missing_count,
                            "non_finite_count": non_finite_count,
                            "mean": mean,
                            "median": med,
                            "std": sd,
                            "min": finite[0],
                            "max": finite[-1],
                            "q1": q1,
                            "q3": q3,
                            "iqr": q3 - q1,
                            "histogram": {
                                "bins": inputs.get("histogram_bins", 10),
                                "edges": [finite[0], finite[-1]],
                                "counts": [n],
                            },
                        }
                result = {
                    "columns": cols_out,
                    "histogram_bins": inputs.get("histogram_bins", 10),
                    "method_version": "descriptive.summary.v1",
                }
            elif tool_id == "inference.correlation_matrix":
                import math
                cols = inputs["columns"]
                variables = list(cols.keys())
                m = len(variables)
                n = len(next(iter(cols.values()))) if variables else 0
                method = inputs.get("method", "pearson")
                p_adjust = inputs.get("p_adjust_method", "fdr_bh")
                def _ranks(xs):
                    indexed = sorted(enumerate(xs), key=lambda t: t[1])
                    ranks = [0.0] * len(xs)
                    i = 0
                    while i < len(xs):
                        j = i
                        while j + 1 < len(xs) and indexed[j + 1][1] == indexed[i][1]:
                            j += 1
                        avg = (i + j) / 2.0 + 1.0
                        for k in range(i, j + 1):
                            ranks[indexed[k][0]] = avg
                        i = j + 1
                    return ranks
                def _corr(xs, ys):
                    a = _ranks(xs) if method == "spearman" else xs
                    b = _ranks(ys) if method == "spearman" else ys
                    ma = sum(a) / len(a)
                    mb = sum(b) / len(b)
                    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
                    da = math.sqrt(sum((x - ma) ** 2 for x in a))
                    db = math.sqrt(sum((y - mb) ** 2 for y in b))
                    if da == 0.0 or db == 0.0:
                        return None
                    return num / (da * db)
                matrix = [[1.0 if i == j else None for j in range(m)] for i in range(m)]
                pvals = [[0.0 if i == j else None for j in range(m)] for i in range(m)]
                pairs = []
                for i in range(m):
                    for j in range(i + 1, m):
                        r = _corr(cols[variables[i]], cols[variables[j]]) if n >= 2 else None
                        matrix[i][j] = r
                        matrix[j][i] = r
                        p_v = 0.01 if r is not None else None
                        pvals[i][j] = p_v
                        pvals[j][i] = p_v
                        pairs.append({
                            "var_a": variables[i],
                            "var_b": variables[j],
                            "statistic": r,
                            "p_value": p_v,
                            "adjusted_p_value": p_v,
                        })
                result = {
                    "variables": variables,
                    "correlation_matrix": matrix,
                    "p_value_matrix": pvals,
                    "adjusted_p_value_matrix": pvals,
                    "pairs": pairs,
                    "n": n,
                    "method": method,
                    "p_adjust_method": p_adjust,
                    "method_version": "inference.correlation_matrix.v1",
                }
            else:
                result = {
                    "echo": inputs,
                    "method_version": f"{tool_id}.v1",
                }

            envelope = {
                "schema_version": "1.0",
                "run_id": run_id,
                "workspace_id": body["workspace_id"],
                "tool_id": tool_id,
                "tool_version": "1.0.0",
                "status": "completed",
                "result": result,
            }
            self.runs[run_id] = envelope
            if self.poll_before_complete > 0:
                self.pending_polls[run_id] = self.poll_before_complete
                return httpx.Response(200, json={
                    "run_id": run_id,
                    "workspace_id": body["workspace_id"],
                    "tool_id": tool_id,
                    "tool_version": "1.0.0",
                    "status": "running",
                })
            return httpx.Response(200, json=envelope)

        if request.method == "GET" and request.url.path.startswith("/api/v1/runs/"):
            run_id = request.url.path.rsplit("/", 1)[-1]
            if run_id not in self.runs:
                return httpx.Response(404, json={"detail": "run does not exist"})
            rem = self.pending_polls.get(run_id, 0)
            if rem > 0:
                self.pending_polls[run_id] = rem - 1
                return httpx.Response(200, json={"run_id": run_id, "status": "running"})
            return httpx.Response(200, json=self.runs[run_id])

        return httpx.Response(404, json={"detail": f"unhandled {request.method} {request.url.path}"})

    def client(self, base_url: str = "http://stats.test") -> StatsToolkitClient:
        return StatsToolkitClient(base_url=base_url, transport=httpx.MockTransport(self.handler))


# --- C05: Client run_tool + read_run_outputs + socket guard ---

def test_c05_run_tool_mock_transport_and_socket_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_workspace_cache()
    server = _FakeToolkitServer(existing_workspace=True, poll_before_complete=1)
    client = server.client()

    run_ref, outputs = client.run_tool(
        "ws_existing_bms",
        "descriptive.summary",
        {"columns": {"ptm": [0.5, 0.7, 0.9]}, "histogram_bins": 4},
        idempotency_key="idem-c05",
        poll_interval=0.01,
    )
    assert run_ref == {
        "run_id": "run_tk_1",
        "workspace_id": "ws_existing_bms",
        "tool_id": "descriptive.summary",
        "tool_version": "1.0.0",
        "method_version": "descriptive.summary.v1",
    }
    assert outputs["columns"]["ptm"]["count"] == 3
    post_calls = [c for c in server.calls if c[0] == "POST" and c[1] == "/api/v1/runs"]
    assert post_calls[0][3].get("idempotency-key") == "idem-c05"

    readback = client.read_run_outputs("run_tk_1")
    assert readback == outputs

    # Under the conftest socket guard, a real HTTP attempt to 18181 raises StatsToolkitUnavailableError.
    # Under --noconftest (live pass where sockets are open and 18181 is up), probe an unreachable port.
    import socket as _socket
    monkeypatch.delenv("BMS_STATS_TOOLKIT_URL", raising=False)
    socket_guard_active = getattr(_socket.socket, "__name__", "") != "socket"
    probe_url = "http://127.0.0.1:18181" if socket_guard_active else "http://127.0.0.1:1"
    real_client = StatsToolkitClient(base_url=probe_url)
    with pytest.raises(StatsToolkitUnavailableError):
        real_client.run_tool("ws_existing_bms", "descriptive.summary", {"columns": {"x": [1.0, 2.0]}})


# --- C06: Workspace resolution cached once per process ---

def test_c06_workspace_resolution_cached_across_analyses(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_workspace_cache()
    monkeypatch.delenv("BMS_STATS_TOOLKIT_WORKSPACE_ID", raising=False)
    server = _FakeToolkitServer(existing_workspace=False)

    # Two separate client instances for two analyses resolve once and reuse the workspace ID.
    c1 = server.client("http://stats-c06.test")
    c2 = server.client("http://stats-c06.test")
    ws1 = c1.resolve_workspace_id()
    ws2 = c2.resolve_workspace_id()
    assert ws1 == ws2 == "ws_created_1"
    ws_gets = [c for c in server.calls if c[0] == "GET" and c[1] == "/api/v1/workspaces"]
    ws_posts = [c for c in server.calls if c[0] == "POST" and c[1] == "/api/v1/workspaces"]
    assert len(ws_gets) == 1
    assert len(ws_posts) == 1

    # Env override bypasses lookup altogether.
    monkeypatch.setenv("BMS_STATS_TOOLKIT_WORKSPACE_ID", "ws_env_override")
    assert c1.resolve_workspace_id() == "ws_env_override"
    assert len([c for c in server.calls if c[1] == "/api/v1/workspaces"]) == 2
    clear_workspace_cache()


# --- C07 + C08: Storage reference (empty result_inline_json) + Stale fallback ---

@pytest.mark.asyncio
async def test_c07_c08_storage_reference_and_stale_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_workspace_cache()
    monkeypatch.delenv("BMS_STATS_TOOLKIT_WORKSPACE_ID", raising=False)
    server = _FakeToolkitServer(existing_workspace=True)
    monkeypatch.setattr("services.stats_toolkit_client.get_stats_toolkit_client", lambda **_: server.client("http://stats-c07.test"))

    factory, engine = await _setup_db(tmp_path)
    _bind_cache(monkeypatch, tmp_path, factory)
    try:
        async with factory() as session:
            session.add(Job(id="job-c07", name="denovo", model_id="boltzgen", mode="denovo", params={}, status="completed"))
            for i in range(3):
                session.add(Design(
                    id=f"d-{i}", job_id="job-c07", name=f"d-{i}",
                    pdb_path=f"/tmp/d-{i}.pdb", review_profile_id="de_novo_generation_v1",
                    plddt_overall=80.0 + i, ptm=0.7 + i * 0.05,
                ))
            await session.commit()
            queued = await trigger_job_analysis("job-c07", JOB_METRIC_SUMMARY_ANALYSIS, AnalysisRunRequest(), session)

        assert await worker._async_main(queued.run_id) == 0

        # C07: stored row has summary_json.stats_run.run_id, empty result_inline_json, and no result.json on disk.
        async with factory() as session:
            row = await session.get(AnalysisRun, queued.run_id)
            assert row is not None and row.status == "completed"
            assert row.result_inline_json is None
            assert row.summary_json["stats_run"]["run_id"] == "run_tk_1"
            assert row.summary_json["stats_run"]["tool_id"] == "descriptive.summary"
            result_file = tmp_path / "analysis_cache" / "job" / "job-c07" / JOB_METRIC_SUMMARY_ANALYSIS / row.cache_key / row.id / "result.json"
            assert not result_file.exists()

            # GET reads outputs from Toolkit and returns stats_run + stale=False.
            resp = await get_job_analysis("job-c07", JOB_METRIC_SUMMARY_ANALYSIS, session=session)
            assert resp.status == "completed"
            assert resp.stale is False
            assert resp.stats_run["run_id"] == "run_tk_1"
            assert resp.result["columns"]["plddt_overall"]["count"] == 3

            # C08: Add one design -> exact cache misses, GET returns stored run with stale=True.
            session.add(Design(
                id="d-3", job_id="job-c07", name="d-3",
                pdb_path="/tmp/d-3.pdb", review_profile_id="de_novo_generation_v1",
                plddt_overall=95.0, ptm=0.95,
            ))
            await session.commit()
            job = await session.get(Job, "job-c07")
            exact_match, *_ = await get_matching_job_analysis_run(session, job, JOB_METRIC_SUMMARY_ANALYSIS, {})
            assert exact_match is None

            stale_resp = await get_job_analysis("job-c07", JOB_METRIC_SUMMARY_ANALYSIS, session=session)
            assert stale_resp.status == "completed"
            assert stale_resp.run_id == queued.run_id
            assert stale_resp.stale is True
            assert stale_resp.stats_run["run_id"] == "run_tk_1"
            assert stale_resp.result["columns"]["plddt_overall"]["count"] == 3
    finally:
        await engine.dispose()
        clear_workspace_cache()


# --- C09: Recompute with options supersedes prior run; prior remains readable via /analyses/{run_id} ---

@pytest.mark.asyncio
async def test_c09_recompute_with_options_supersedes_and_preserves_prior(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_workspace_cache()
    monkeypatch.delenv("BMS_STATS_TOOLKIT_WORKSPACE_ID", raising=False)
    server = _FakeToolkitServer(existing_workspace=True)
    monkeypatch.setattr("services.stats_toolkit_client.get_stats_toolkit_client", lambda **_: server.client("http://stats-c09.test"))

    factory, engine = await _setup_db(tmp_path)
    _bind_cache(monkeypatch, tmp_path, factory)
    try:
        async with factory() as session:
            session.add(Job(id="job-c09", name="binder", model_id="binder_design", mode="binder", params={}, status="completed"))
            for i in range(6):
                session.add(Design(
                    id=f"b-{i}", job_id="job-c09", name=f"b-{i}",
                    pdb_path=f"/tmp/b-{i}.pdb", review_profile_id="binder_design_v1",
                    plddt_overall=75.0 + i * 3.0, ptm=0.6 + i * 0.05,
                ))
            await session.commit()

            # 1. Default (Pearson) correlation run
            r1 = await trigger_job_analysis("job-c09", JOB_CORRELATION_MATRIX_ANALYSIS, AnalysisRunRequest(), session)
            assert r1.params["method"] == "pearson"
            assert r1.supersedes_run_id is None
        # Mark r1 completed (mocking Pearson output)
        async with factory() as session:
            row1 = await session.get(AnalysisRun, r1.run_id)
            row1.status = "completed"
            row1.completed_at = datetime.utcnow()
            row1.result_inline_json = {"job_id": "job-c09", "method": "pearson", "metrics": ["plddt_overall", "ptm"], "matrix": [[1.0, 0.99], [0.99, 1.0]], "sample_sizes": [[6, 6], [6, 6]]}
            await session.commit()

            # 2. Recompute with method=spearman -> supersedes r1
            r2 = await trigger_job_analysis(
                "job-c09",
                JOB_CORRELATION_MATRIX_ANALYSIS,
                AnalysisRunRequest(params={"method": "spearman"}),
                session,
            )
            assert r2.run_id != r1.run_id
            assert r2.params["method"] == "spearman"
            assert r2.supersedes_run_id == r1.run_id
            row2 = await session.get(AnalysisRun, r2.run_id)
            row2.status = "completed"
            row2.completed_at = datetime.utcnow()
            row2.result_inline_json = {"job_id": "job-c09", "method": "spearman", "metrics": ["plddt_overall", "ptm"], "matrix": [[1.0, 1.0], [1.0, 1.0]], "sample_sizes": [[6, 6], [6, 6]]}
            await session.commit()

            # 3. GET without explicit method returns latest superseding (Spearman) run
            latest = await get_job_analysis("job-c09", JOB_CORRELATION_MATRIX_ANALYSIS, session=session)
            assert latest.run_id == r2.run_id
            assert latest.params["method"] == "spearman"
            assert latest.supersedes_run_id == r1.run_id

            # 4. Prior Pearson run remains readable via GET /api/analyses/{run_id}
            prior = await get_analysis_run(r1.run_id, session=session)
            assert prior.run_id == r1.run_id
            assert prior.params["method"] == "pearson"
            assert prior.result["method"] == "pearson"

            # 5. Also verify durable job_metric_summary recompute with histogram_bins=20
            s1 = await trigger_job_analysis("job-c09", JOB_METRIC_SUMMARY_ANALYSIS, AnalysisRunRequest(), session)
        assert await worker._async_main(s1.run_id) == 0
        async with factory() as session:
            s2 = await trigger_job_analysis("job-c09", JOB_METRIC_SUMMARY_ANALYSIS, AnalysisRunRequest(params={"histogram_bins": 20}), session)
            assert s2.supersedes_run_id == s1.run_id
        assert await worker._async_main(s2.run_id) == 0
        async with factory() as session:
            latest_s = await get_job_analysis("job-c09", JOB_METRIC_SUMMARY_ANALYSIS, session=session)
            assert latest_s.run_id == s2.run_id
            assert latest_s.result["histogram_bins"] == 20
            old_s = await get_analysis_run(s1.run_id, session=session)
            assert old_s.run_id == s1.run_id
            assert old_s.result["histogram_bins"] == 10
    finally:
        await engine.dispose()
        clear_workspace_cache()


# --- C10 + C11: Autorun queueing above 500-design cap & error boundary isolation ---

@pytest.mark.asyncio
async def test_c10_autorun_queues_job_stats_above_per_design_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory, engine = await _setup_db(tmp_path)
    _bind_cache(monkeypatch, tmp_path, factory)
    monkeypatch.setenv("BMS_ANALYSIS_AUTORUN_MAX_DESIGNS", "2")

    @asynccontextmanager
    async def store():
        async with factory() as s:
            yield s

    monkeypatch.setattr(autorun, "async_session", store)
    try:
        async with factory() as session:
            session.add(Job(id="job-c10", name="big-denovo", model_id="boltzgen", mode="denovo", params={}, status="completed"))
            for i in range(5):
                session.add(Design(
                    id=f"dn-{i}", job_id="job-c10", name=f"dn-{i}",
                    pdb_path=f"/tmp/dn-{i}.pdb", review_profile_id="de_novo_generation_v1",
                    plddt_overall=80.0 + i, ptm=0.7 + i * 0.02,
                ))
            await session.commit()

        res1 = await autorun.ensure_viewer_minimum_analyses_for_job("job-c10")
        # Per-design analyses skipped by the cap (5 > 2)
        assert res1 == {"designs": 5, "queued": 0, "reused": 0, "skipped": 5}
        # Re-running autorun must not duplicate queued rows
        await autorun.ensure_viewer_minimum_analyses_for_job("job-c10")

        async with factory() as session:
            rows = list((await session.execute(
                select(AnalysisRun).where(AnalysisRun.subject_kind == "job", AnalysisRun.subject_id == "job-c10")
            )).scalars().all())
            by_type = {r.analysis_type: r for r in rows}
            assert len(rows) == 2
            assert set(by_type) == {JOB_METRIC_SUMMARY_ANALYSIS, JOB_CORRELATION_MATRIX_ANALYSIS}
            assert all(r.status == "queued" for r in rows)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_c11_job_stats_queue_failure_preserves_job_and_per_design_autorun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory, engine = await _setup_db(tmp_path)
    _bind_cache(monkeypatch, tmp_path, factory)

    @asynccontextmanager
    async def store():
        async with factory() as s:
            yield s

    async def failing_job_stats(_session, _job):
        raise RuntimeError("simulated job stats queue failure")

    per_design_calls: list[tuple[str, str]] = []

    async def fake_design_req(_session, row, analysis_type, **kwargs):
        per_design_calls.append((row.id, analysis_type))
        return object(), False

    monkeypatch.setattr(autorun, "async_session", store)
    monkeypatch.setattr(autorun, "_ensure_job_stats_analyses", failing_job_stats)
    monkeypatch.setattr(autorun, "request_design_analysis", fake_design_req)
    try:
        async with factory() as session:
            session.add(Job(id="job-c11", name="ok-denovo", model_id="boltzgen", mode="denovo", params={}, status="completed"))
            session.add(Design(
                id="d-c11", job_id="job-c11", name="d-c11",
                pdb_path="/tmp/d-c11.pdb", review_profile_id="de_novo_generation_v1",
                plddt_overall=88.0,
            ))
            await session.commit()

        res = await autorun.ensure_viewer_minimum_analyses_for_job("job-c11")
        assert res["designs"] == 1
        assert res["queued"] == 2
        assert len(per_design_calls) == 2

        async with factory() as session:
            job = await session.get(Job, "job-c11")
            assert job is not None and job.status == "completed" and job.error_message is None
    finally:
        await engine.dispose()



@pytest.mark.asyncio
@pytest.mark.parametrize("count,queue_fails", [(501, False), (1, True)])
async def test_terminal_publication_schedules_isolated_stats(tmp_path, monkeypatch, count, queue_fails):
    """Exercise the post-commit hook, not just autorun on an already-completed job."""
    import asyncio
    from runtime_policy import DEPLOYMENT_ADMISSION_LOCK_ENV
    from services import gpu_orchestrator, result_state_integrity

    factory, engine = await _setup_db(tmp_path)
    _bind_cache(monkeypatch, tmp_path, factory)
    monkeypatch.setattr(autorun, "async_session", factory)
    monkeypatch.setenv(DEPLOYMENT_ADMISSION_LOCK_ENV, str(tmp_path / "admission.lock"))
    monkeypatch.delenv("BMS_ANALYSIS_AUTORUN_MAX_DESIGNS", raising=False)
    job_id = f"terminal-stats-{count}"
    pdb = tmp_path / "structure.pdb"
    pdb.write_text("END\n")  # Inert result-publication fixture; no inference.

    async def ingest(job_id, output_dir, session, **kwargs):
        session.add_all([Design(id=f"d-{i}", job_id=job_id, name=f"candidate-{i}",
            pdb_path=str(pdb), review_profile_id="de_novo_generation_v1",
            plddt_overall=80.0, ptm=0.8) for i in range(count)])
        await session.flush()
        return count

    finalize = result_state_integrity.finalize_successful_job
    async def finalize_fixture(job, directory, session):
        return await finalize(job, directory, session, ingest_fn=ingest)
    monkeypatch.setattr(result_state_integrity, "finalize_successful_job", finalize_fixture)
    if queue_fails:
        async def fail_job_queue(session, job):
            assert job.status == "completed"
            raise RuntimeError("injected post-publication queue failure")
        monkeypatch.setattr(autorun, "_ensure_job_stats_analyses", fail_job_queue)

    ensure = autorun.ensure_viewer_minimum_analyses_for_job
    finished = asyncio.get_running_loop().create_future()
    async def observed(job_id):
        try:
            result = await ensure(job_id)
        except Exception as exc:
            finished.set_exception(exc)
            raise
        finished.set_result(result)
        return result
    monkeypatch.setattr(autorun, "ensure_viewer_minimum_analyses_for_job", observed)
    try:
        async with factory() as s:
            job = Job(id=job_id, name=job_id, model_id="boltzgen", mode="denovo", params={},
                      status="running", queue_status="running", output_dir=str(tmp_path))
            s.add(job)
            await s.commit()
            assert await gpu_orchestrator._finalize_recovered_success(job, s, str(tmp_path),
                snapshot_history=False, gate_present=False, age_seconds=0, stale_fail_after_seconds=60)
        await asyncio.wait_for(finished, timeout=20)
        async with factory() as s:
            job = await s.get(Job, job_id)
            assert job.status == "completed" and job.error_message is None
            rows = list((await s.execute(select(AnalysisRun))).scalars())
            assert len(rows) == 2 and all(r.status == "queued" for r in rows)
            if queue_fails:
                assert {r.subject_kind for r in rows} == {"design"}
                assert len({r.analysis_type for r in rows}) == 2
            else:
                assert {r.subject_kind for r in rows} == {"job"}
                assert {r.analysis_type for r in rows} == {JOB_METRIC_SUMMARY_ANALYSIS, JOB_CORRELATION_MATRIX_ANALYSIS}
    finally:
        await engine.dispose()

# --- C12: Unavailable state when BMS_STATS_TOOLKIT_URL=off ---

@pytest.mark.asyncio
@pytest.mark.parametrize("analysis_type", [JOB_METRIC_SUMMARY_ANALYSIS, JOB_CORRELATION_MATRIX_ANALYSIS])
async def test_c12_unavailable_toolkit_marks_failed_and_job_still_opens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, analysis_type: str) -> None:
    clear_workspace_cache()
    monkeypatch.setenv("BMS_STATS_TOOLKIT_URL", "off")
    monkeypatch.delenv("BMS_STATS_TOOLKIT_WORKSPACE_ID", raising=False)

    factory, engine = await _setup_db(tmp_path)
    _bind_cache(monkeypatch, tmp_path, factory)
    try:
        async with factory() as session:
            session.add(Job(id="job-c12", name="denovo-off", model_id="boltzgen", mode="denovo", params={}, status="completed"))
            # Enough eligible data to execute both tools; one row is not a correlation.
            for i in range(6):
                session.add(Design(
                    id=f"d-c12-{i}", job_id="job-c12", name=f"d-c12-{i}",
                    pdb_path="/tmp/d-c12.pdb", review_profile_id="de_novo_generation_v1",
                    plddt_overall=85.0 + i, ptm=0.70 + i / 100,
                ))
            await session.commit()
            queued = await trigger_job_analysis("job-c12", analysis_type, AnalysisRunRequest(), session)

        # Worker records failed row with stats_toolkit_unavailable reason
        assert await worker._async_main(queued.run_id) == 1
        async with factory() as session:
            row = await session.get(AnalysisRun, queued.run_id)
            assert row is not None
            assert row.status == "failed"
            assert row.error_message == STATS_TOOLKIT_UNAVAILABLE_REASON
            assert row.summary_json["reason"] == STATS_TOOLKIT_UNAVAILABLE_REASON

            # GET analysis reports unavailable
            resp = await get_job_analysis("job-c12", analysis_type, session=session)
            assert resp.status == "unavailable"
            assert resp.reason == STATS_TOOLKIT_UNAVAILABLE_REASON
            assert resp.result is None

            # Job itself and its designs remain readable
            job = await session.get(Job, "job-c12")
            designs = list((await session.execute(select(Design).where(Design.job_id == "job-c12"))).scalars().all())
            assert job is not None and job.status == "completed" and len(designs) == 6
    finally:
        await engine.dispose()
        clear_workspace_cache()


# --- Selection subject_kind plumbing (R08 / R13 prep) ---

@pytest.mark.asyncio
async def test_selection_subject_reuse_and_read_by_run_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_workspace_cache()
    server = _FakeToolkitServer(existing_workspace=True)
    monkeypatch.setattr("services.stats_toolkit_client.get_stats_toolkit_client", lambda **_: server.client("http://stats-sel.test"))

    factory, engine = await _setup_db(tmp_path)
    _bind_cache(monkeypatch, tmp_path, factory)
    try:
        assert get_analysis_definition(SELECTION_METRIC_SUMMARY_ANALYSIS).subject_kind == "selection"
        assert get_analysis_definition(SELECTION_BATCH_SUMMARY_ANALYSIS).subject_kind == "selection"
        sig = "a" * 64
        async with factory() as session:
            run1, hit1 = await request_selection_analysis(
                session,
                sig,
                SELECTION_BATCH_SUMMARY_ANALYSIS,
                raw_params={"job_ids": ["j2", "j1"]},
            )
            assert hit1 is False
            assert run1.subject_kind == "selection"
            assert run1.subject_id == sig

            # Identical selection reuses the same run
            run2, hit2 = await request_selection_analysis(
                session,
                sig,
                SELECTION_BATCH_SUMMARY_ANALYSIS,
                raw_params={"job_ids": ["j1", "j2"]},
            )
            assert hit2 is True
            assert run2.id == run1.id

            # GET /api/analyses/{run_id} handles subject_kind="selection"
            resp = await get_analysis_run(run1.id, session=session)
            assert resp.run_id == run1.id
            assert resp.subject_kind == "selection"
            assert resp.subject_id == sig
    finally:
        await engine.dispose()
        clear_workspace_cache()
