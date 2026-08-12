from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def _write_manifest(
    directory: Path,
    *,
    prefix: str = "aligned",
    job_id: str | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reference.fasta").write_text(">ref\nACGTACGT\n", encoding="utf-8")
    (directory / "reference.fasta.fai").write_text("ref\t8\t5\t8\t9\n", encoding="utf-8")
    (directory / f"{prefix}.bam").write_bytes(b"bam")
    (directory / f"{prefix}.bam.bai").write_bytes(b"bai")
    payload = {
        "artifact_schema_version": 2,
        "schema": "sequence_qc.manifest.v1",
        "workflow_id": "ont_fastq_qc",
        "job_id": job_id or directory.parent.name,
        "input_mode": "fastq",
        "analysis_status": "completed",
        "alignment_session": {
            "mode": "dimer_candidates" if "dimer" in directory.name else "primary",
            "reference_sequence_sha256": hashlib.sha256(b"ACGTACGT").hexdigest(),
        },
        "artifacts": [
            {"kind": "reference", "path": "reference.fasta", "required": True, "state": "present"},
            {"kind": "reference_index", "path": "reference.fasta.fai", "required": False, "state": "present"},
            {"kind": "alignment_bam", "path": f"{prefix}.bam", "required": False, "state": "present"},
            {"kind": "alignment_bai", "path": f"{prefix}.bam.bai", "required": False, "state": "present"},
        ],
    }
    for artifact in payload["artifacts"]:
        path = directory / artifact["path"]
        artifact["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        artifact["size_bytes"] = path.stat().st_size
    (directory / "qc_manifest.json").write_text(json.dumps(payload), encoding="utf-8")


def test_manifest_schema_and_job_binding_are_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    manifest_dir = tmp_path / "job-a" / "fastq_qc"
    _write_manifest(manifest_dir)
    manifest_path = manifest_dir / "qc_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["job_id"] = "job-b"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))

    sessions = service.build_alignment_sessions("job-a", results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")
    assert primary["ready"] is False
    assert "manifest job_id does not match requested job" in primary["unavailable_reason"]


def test_duplicate_artifact_role_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    manifest_dir = tmp_path / "job-a" / "fastq_qc"
    _write_manifest(manifest_dir)
    manifest_path = manifest_dir / "qc_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["artifacts"].append(dict(payload["artifacts"][2]))
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))

    sessions = service.build_alignment_sessions("job-a", results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")
    assert primary["ready"] is False
    assert "duplicate artifact role: alignment" in primary["unavailable_reason"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "untrusted.manifest.v1"),
        ("artifact_schema_version", 1),
    ],
)
def test_manifest_schema_version_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    from services import ngs_alignment_sessions as service

    manifest_dir = tmp_path / "job-a" / "fastq_qc"
    _write_manifest(manifest_dir)
    manifest_path = manifest_dir / "qc_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload[field] = value
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))

    sessions = service.build_alignment_sessions("job-a", results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")
    assert primary["ready"] is False
    assert "manifest schema" in primary["unavailable_reason"]


def test_generic_artifact_resolution_rejects_unready_manifest_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    manifest_dir = tmp_path / "job-a" / "fastq_qc"
    _write_manifest(manifest_dir)
    manifest_path = manifest_dir / "qc_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    alignment = next(item for item in payload["artifacts"] if item["kind"] == "alignment_bam")
    alignment["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))

    sessions = service.build_alignment_sessions("job-a", results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")
    artifact_id = primary["artifacts"]["alignment"]["artifact_id"]
    assert primary["ready"] is False

    with pytest.raises(service.AlignmentSessionError, match="not found"):
        service.resolve_alignment_artifact("job-a", artifact_id, results_dir=tmp_path)


def test_primary_session_is_opaque_job_scoped_and_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services import ngs_alignment_sessions as service

    _write_manifest(tmp_path / "job-a" / "fastq_qc")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))

    sessions = service.build_alignment_sessions("job-a", results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")
    assert primary["ready"] is True
    assert primary["reference_contig"] == "ref"
    assert primary["unavailable_reason"] is None
    assert set(primary["artifacts"]) >= {"alignment", "alignment_index", "reference", "reference_index"}
    assert all("path" not in artifact for artifact in primary["artifacts"].values())
    assert primary["artifacts"]["alignment"]["url"].startswith(
        "/api/jobs/job-a/alignment-artifacts/"
    )
    assert primary["artifacts"]["alignment"]["sha256"]


def test_dimer_kind_cannot_enter_primary_or_contradict_declared_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    manifest_dir = tmp_path / "job-a" / "generic"
    _write_manifest(manifest_dir, prefix="generic")
    manifest_path = manifest_dir / "qc_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["alignment_session"]["mode"] = "primary"
    payload["artifacts"][2]["kind"] = "dimer_alignment_bam"
    payload["artifacts"][3]["kind"] = "dimer_alignment_bai"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))

    sessions = service.build_alignment_sessions("job-a", results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")
    assert "alignment" not in primary["artifacts"]
    assert primary["ready"] is False
    assert "contradictory primary session mode" in primary["unavailable_reason"]
    assert all(item["mode"] != "dimer_candidates" for item in sessions)


def test_explicit_primary_mode_rejects_dimer_path_heuristic_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    manifest_dir = tmp_path / "job-a" / "fastq_dimer_qc"
    _write_manifest(manifest_dir, prefix="generic")
    manifest_path = manifest_dir / "qc_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["alignment_session"]["mode"] = "primary"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))

    sessions = service.build_alignment_sessions("job-a", results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")

    assert primary["ready"] is False
    assert "alignment" not in primary["artifacts"]
    assert "contradictory primary session mode" in primary["unavailable_reason"]


def test_persisted_production_output_directory_resolves_sessions_and_stays_confined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import ngs_alignment_sessions as service

    results = tmp_path / "results"
    output_dir = results / "submitted-name_20260719_040000"
    _write_manifest(output_dir / "fastq_qc", job_id="opaque-job-uuid")
    monkeypatch.setattr(service, "get_results_dir", lambda: results)
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))

    sessions = service.build_alignment_sessions(
        "opaque-job-uuid",
        results_dir=results,
        job_output_dir=output_dir,
    )
    assert next(item for item in sessions if item["mode"] == "primary")["ready"] is True

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(
        child_output_dir=None,
        output_dir=str(output_dir),
    )
    response = TestClient(app).get("/api/jobs/opaque-job-uuid/alignment-sessions")
    assert response.status_code == 200
    assert next(item for item in response.json()["sessions"] if item["mode"] == "primary")["ready"] is True

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(service.AlignmentSessionError, match="unsafe job root"):
        service.build_alignment_sessions(
            "opaque-job-uuid",
            results_dir=results,
            job_output_dir=outside,
        )


def test_alignment_capability_enforces_two_principal_cross_job_denial() -> None:
    from routers import ngs_alignment_sessions as routes
    from services import alignment_access

    token_a, digest_a = alignment_access.issue_alignment_access_token()
    _token_b, digest_b = alignment_access.issue_alignment_access_token()

    class Result:
        def __init__(self, digest: str):
            self.job = SimpleNamespace(
                id="job-a",
                output_dir="/tmp/results/job-a-run",
                provenance={alignment_access.PROVENANCE_DIGEST_KEY: digest},
            )

        def scalar_one_or_none(self):
            return self.job

    class Session:
        def __init__(self, digest: str):
            self.digest = digest

        async def execute(self, _query):
            return Result(self.digest)

    request_a = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/jobs/job-a/alignment-sessions",
            "headers": [(b"authorization", f"Bearer {token_a}".encode())],
        }
    )
    authorized = asyncio.run(routes.require_alignment_job("job-a", request_a, Session(digest_a)))
    assert authorized.output_dir == "/tmp/results/job-a-run"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(routes.require_alignment_job("job-b", request_a, Session(digest_b)))
    assert exc_info.value.status_code == 403


def test_manifest_assigns_distinct_opaque_roles_without_treating_generic_coverage_as_bedgraph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    manifest_dir = tmp_path / "job-a" / "fastq_qc"
    _write_manifest(manifest_dir)
    payload = json.loads((manifest_dir / "qc_manifest.json").read_text(encoding="utf-8"))
    artifact_files = [
        ("coverage", "fastq_coverage.tsv"),
        ("igv_coverage_depth", "igv_coverage_depth.bedgraph"),
        ("igv_gc_content", "igv_gc_content.bedgraph"),
        ("igv_position_gradient", "igv_position_gradient.bedgraph"),
        ("igv_gc_zscore", "igv_gc_zscore.bedgraph"),
        ("igv_split_read_density", "igv_split_read_density.bedgraph"),
        ("igv_softclip_density", "igv_softclip_density.bedgraph"),
        ("igv_junction_hotspots", "igv_junction_hotspots.bed"),
        ("igv_report", "igv_report.html"),
        ("igv_track_config", "igv_track_config.json"),
    ]
    for kind, name in artifact_files:
        path = manifest_dir / name
        path.write_text(f"authoritative {kind}\n", encoding="utf-8")
        payload["artifacts"].append(
            {
                "kind": kind,
                "path": name,
                "required": False,
                "state": "present",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
        )
    (manifest_dir / "qc_manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))

    primary = service.build_alignment_sessions("job-a", results_dir=tmp_path)[0]

    expected_roles = {
        "coverage_depth": "igv_coverage_depth.bedgraph",
        "gc_content": "igv_gc_content.bedgraph",
        "position_gradient": "igv_position_gradient.bedgraph",
        "gc_zscore": "igv_gc_zscore.bedgraph",
        "split_read_density": "igv_split_read_density.bedgraph",
        "soft_clip_density": "igv_softclip_density.bedgraph",
        "junction_hotspots": "igv_junction_hotspots.bed",
        "report": "igv_report.html",
        "track_config": "igv_track_config.json",
    }
    assert "coverage" not in primary["artifacts"]
    assert set(expected_roles).issubset(primary["artifacts"])
    for role, expected_name in expected_roles.items():
        descriptor = primary["artifacts"][role]
        assert descriptor["url"].startswith("/api/jobs/job-a/alignment-artifacts/")
        assert "path" not in descriptor
        assert service.resolve_alignment_artifact(
            "job-a", descriptor["artifact_id"], results_dir=tmp_path
        ).name == expected_name


def test_primary_never_mixes_dimer_sidecars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services import ngs_alignment_sessions as service

    _write_manifest(tmp_path / "job-a" / "fastq_qc", prefix="aligned")
    _write_manifest(tmp_path / "job-a" / "dimer_qc", prefix="dimer_candidates")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))

    sessions = service.build_alignment_sessions("job-a", results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")
    dimer = next(item for item in sessions if item["mode"] == "dimer_candidates")
    assert primary["session_id"] != dimer["session_id"]
    primary_id = primary["artifacts"]["alignment"]["artifact_id"]
    dimer_id = dimer["artifacts"]["alignment"]["artifact_id"]
    assert service.resolve_alignment_artifact("job-a", primary_id, results_dir=tmp_path).name == "aligned.bam"
    assert service.resolve_alignment_artifact("job-a", dimer_id, results_dir=tmp_path).name == "dimer_candidates.bam"


def test_artifact_id_cannot_cross_jobs_or_accept_path_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services import ngs_alignment_sessions as service

    _write_manifest(tmp_path / "job-a" / "fastq_qc")
    _write_manifest(tmp_path / "job-b" / "fastq_qc")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))
    artifact_id = service.build_alignment_sessions("job-a", results_dir=tmp_path)[0]["artifacts"]["alignment"][
        "artifact_id"
    ]

    with pytest.raises(service.AlignmentSessionError, match="not found"):
        service.resolve_alignment_artifact("job-b", artifact_id, results_dir=tmp_path)
    with pytest.raises(service.AlignmentSessionError, match="unsafe"):
        service.build_alignment_sessions("../job-a", results_dir=tmp_path)


def test_symlink_or_special_file_never_becomes_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services import ngs_alignment_sessions as service

    target = tmp_path / "outside.bam"
    target.write_bytes(b"outside")
    manifest_dir = tmp_path / "job-a" / "fastq_qc"
    _write_manifest(manifest_dir)
    (manifest_dir / "aligned.bam").unlink()
    (manifest_dir / "aligned.bam").symlink_to(target)
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))

    primary = service.build_alignment_sessions("job-a", results_dir=tmp_path)[0]
    assert primary["ready"] is False
    assert "unsafe" in primary["unavailable_reason"].lower()

    inside = tmp_path / "job-c" / "real"
    inside.mkdir(parents=True)
    artifact = inside / "aligned.bam"
    artifact.write_bytes(b"bam")
    linked = tmp_path / "job-c" / "linked"
    linked.symlink_to(inside, target_is_directory=True)
    safe_path, reason = service._regular_file_inside(linked / "aligned.bam", tmp_path / "job-c")
    assert safe_path is None
    assert reason == "unsafe artifact: symlink component"

    real_job = tmp_path / "real-job"
    real_job.mkdir()
    (tmp_path / "job-symlink").symlink_to(real_job, target_is_directory=True)
    with pytest.raises(service.AlignmentSessionError, match="symlink job root"):
        service.build_alignment_sessions("job-symlink", results_dir=tmp_path)


def test_semantic_role_resolver_requires_ready_exact_mode_role_and_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    _write_manifest(tmp_path / "job-a" / "fastq_qc")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))
    primary = service.build_alignment_sessions("job-a", results_dir=tmp_path)[0]
    alignment = primary["artifacts"]["alignment"]

    path, metadata = service.resolve_alignment_artifact_by_role(
        "job-a",
        "primary",
        "alignment",
        alignment["sha256"],
        results_dir=tmp_path,
    )

    assert path.name == "aligned.bam"
    assert metadata["sha256"] == alignment["sha256"]
    for mode, role, digest in (
        ("dimer_candidates", "alignment", alignment["sha256"]),
        ("primary", "unknown", alignment["sha256"]),
        ("primary", "alignment", "0" * 64),
    ):
        with pytest.raises(service.AlignmentSessionError, match="not found"):
            service.resolve_alignment_artifact_by_role(
                "job-a", mode, role, digest, results_dir=tmp_path
            )


def test_generic_alignment_routes_offload_blocking_service_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import ngs_alignment_sessions as service

    monkeypatch.setattr(
        service,
        "build_alignment_sessions",
        lambda *_args, **_kwargs: [{"session_id": "session-a"}],
    )
    monkeypatch.setattr(
        service,
        "resolve_alignment_session",
        lambda *_args, **_kwargs: {"session_id": "session-a"},
    )
    monkeypatch.setattr(
        service,
        "_resolve_internal_artifact",
        lambda *_args, **_kwargs: (Path("/tmp/artifact"), {"artifact_id": "artifact-a"}),
    )
    monkeypatch.setattr(service, "resolve_session_bam", lambda *_args, **_kwargs: Path("/tmp/aligned.bam"))
    monkeypatch.setattr(
        service,
        "read_bam_page",
        lambda *_args, **_kwargs: {"reads": [], "next_cursor": None},
    )
    monkeypatch.setattr(
        service,
        "read_bam_exact",
        lambda *_args, **_kwargs: {
            "read": {"read_id": "read-a"},
            "scan_truncated": False,
        },
    )

    async def fake_serve_artifact(*_args, **_kwargs):
        return JSONResponse({"served": True})

    monkeypatch.setattr(routes, "_serve_artifact", fake_serve_artifact)
    threadpool_calls = []
    real_run_in_threadpool = routes.run_in_threadpool

    async def tracked_run_in_threadpool(func, *args, **kwargs):
        threadpool_calls.append(func)
        return await real_run_in_threadpool(func, *args, **kwargs)

    monkeypatch.setattr(routes, "run_in_threadpool", tracked_run_in_threadpool)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(
        child_output_dir=None,
        output_dir="/tmp/job-a-run",
    )
    client = TestClient(app)

    assert client.get("/api/jobs/job-a/alignment-sessions").status_code == 200
    assert client.get("/api/jobs/job-a/alignment-sessions/session-a").status_code == 200
    assert client.get("/api/jobs/job-a/alignment-artifacts/artifact-a").status_code == 200
    assert client.get("/api/jobs/job-a/reads?session_id=session-a").status_code == 200
    assert client.get("/api/jobs/job-a/reads/read-a?session_id=session-a").status_code == 200

    assert service.build_alignment_sessions in threadpool_calls
    assert service.resolve_alignment_session in threadpool_calls
    assert service._resolve_internal_artifact in threadpool_calls
    assert service.resolve_session_bam in threadpool_calls
    assert service.read_bam_page in threadpool_calls
    assert service.read_bam_exact in threadpool_calls


def test_semantic_role_route_is_capability_scoped_and_range_capable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import ngs_alignment_sessions as service

    artifact = tmp_path / "aligned.bam"
    artifact.write_bytes(b"abcdefghij")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    monkeypatch.setattr(
        service,
        "resolve_alignment_artifact_by_role",
        lambda *_args, **_kwargs: (
            artifact,
            {"sha256": digest, "mime_type": "application/octet-stream", "size_bytes": 10},
        ),
        raising=False,
    )
    threadpool_calls = []
    real_run_in_threadpool = routes.run_in_threadpool

    async def tracked_run_in_threadpool(func, *args, **kwargs):
        threadpool_calls.append(func)
        return await real_run_in_threadpool(func, *args, **kwargs)

    monkeypatch.setattr(routes, "run_in_threadpool", tracked_run_in_threadpool)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(
        child_output_dir=None,
        output_dir="/tmp/job-a-run",
    )
    client = TestClient(app)

    response = client.get(
        f"/api/jobs/job-a/alignment-session-artifacts/primary/alignment/{digest}",
        headers={"Range": "bytes=3-6"},
    )

    assert response.status_code == 206
    assert response.content == b"defg"
    assert response.headers["content-range"] == "bytes 3-6/10"
    assert response.headers["etag"] == f'"{digest}"'
    assert threadpool_calls[0] is service.resolve_alignment_artifact_by_role
    assert service.open_verified_artifact_snapshot in threadpool_calls


def test_semantic_role_route_rejects_resolver_to_descriptor_open_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import ngs_alignment_sessions as service

    original = b"verified"
    mutated = b"tampered"
    artifact = tmp_path / "aligned.bam"
    artifact.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    _isolate_snapshot_state(service, monkeypatch, tmp_path, limit=1024)
    real_os_open = service.os.open
    mutation_count = 0

    def racing_os_open(path, flags, *args, **kwargs):
        nonlocal mutation_count
        if path == artifact.name and not flags & os.O_DIRECTORY:
            artifact.write_bytes(mutated)
            mutation_count += 1
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(service.os, "open", racing_os_open)
    monkeypatch.setattr(
        service,
        "resolve_alignment_artifact_by_role",
        lambda *_args, **_kwargs: (
            artifact,
            {
                "sha256": digest,
                "mime_type": "application/octet-stream",
                "size_bytes": len(original),
            },
        ),
    )
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(
        child_output_dir=None,
        output_dir="/tmp/job-a-run",
    )

    response = TestClient(app).get(
        f"/api/jobs/job-a/alignment-session-artifacts/primary/alignment/{digest}",
        headers={"Range": "bytes=0-7"},
    )

    assert mutation_count == 1
    assert response.status_code == 400
    assert response.json() == {"detail": "artifact integrity digest mismatch"}


def test_paginated_bam_reads_are_bounded_and_sequences_are_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import ngs_alignment_sessions as service

    sam_lines = [
        "r1\t0\tref\t2\t60\t4M\t*\t0\t0\tACGT\tIIII",
        "r2\t16\tref\t3\t40\t4M\t*\t0\t0\tTGCA\t####",
        "r3\t4\t*\t0\t0\t*\t*\t0\t0\tNNNN\t!!!!",
    ]
    monkeypatch.setattr(service, "_iter_sam_lines", lambda *_args, **_kwargs: iter(sam_lines))
    page = service.read_bam_page(Path("unused.bam"), cursor="1", limit=1, include_sequence=False)
    assert [row["read_id"] for row in page["reads"]] == ["r2"]
    assert page["next_cursor"] == "2"
    assert "sequence" not in page["reads"][0]
    detailed = service.read_bam_page(Path("unused.bam"), q="r1", limit=10, include_sequence=True)
    assert detailed["reads"][0]["sequence"] == "ACGT"
    assert detailed["reads"][0]["mean_quality"] == pytest.approx(40.0)


def test_job_scoped_artifact_route_supports_ranges_and_etags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import ngs_alignment_sessions as service

    artifact = tmp_path / "aligned.bam"
    artifact.write_bytes(b"abcdefghij")
    digest = service._sha256_file(artifact)
    monkeypatch.setattr(
        service,
        "_resolve_internal_artifact",
        lambda *_args, **_kwargs: (
            artifact,
            {"sha256": digest, "mime_type": "application/octet-stream", "size_bytes": 10},
        ),
    )
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(output_dir="/tmp/job-a-run")
    client = TestClient(app)

    ranged = client.get(
        "/api/jobs/job-a/alignment-artifacts/" + "a" * 64,
        headers={"Range": "bytes=2-5"},
    )
    assert ranged.status_code == 206
    assert ranged.content == b"cdef"
    assert ranged.headers["content-range"] == "bytes 2-5/10"
    assert ranged.headers["accept-ranges"] == "bytes"
    etag = ranged.headers["etag"]
    assert etag == f'"{digest}"'

    unchanged = client.get(
        "/api/jobs/job-a/alignment-artifacts/" + "a" * 64,
        headers={"If-None-Match": etag},
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""


def test_reads_route_requires_a_ready_session_and_never_returns_a_full_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import ngs_alignment_sessions as service

    monkeypatch.setattr(service, "resolve_session_bam", lambda *_args, **_kwargs: Path("/tmp/aligned.bam"))
    monkeypatch.setattr(
        service,
        "read_bam_page",
        lambda *_args, **kwargs: {
            "reads": [{"read_id": "r1", "length": 4}],
            "next_cursor": None,
            "limit": kwargs["limit"],
            "sequence_included": kwargs["include_sequence"],
        },
    )
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(output_dir="/tmp/job-a-run")
    client = TestClient(app)
    response = client.get("/api/jobs/job-a/reads?session_id=s1&limit=25")
    assert response.status_code == 200
    assert response.json() == {
        "reads": [{"read_id": "r1", "length": 4}],
        "next_cursor": None,
        "limit": 25,
        "sequence_included": False,
    }


def test_verified_snapshot_rejects_same_size_retimed_replacement(tmp_path: Path) -> None:
    from services import ngs_alignment_sessions as service

    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"original")
    original_stat = artifact.stat()
    expected_digest = hashlib.sha256(b"original").hexdigest()

    artifact.write_bytes(b"tampered")
    os.utime(artifact, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    with pytest.raises(service.AlignmentSessionError, match="integrity"):
        service.open_verified_artifact_snapshot(
            artifact,
            expected_size=8,
            expected_sha256=expected_digest,
        )


def test_artifact_descriptor_rehashes_same_size_retimed_replacement(tmp_path: Path) -> None:
    from services import ngs_alignment_sessions as service

    artifact = tmp_path / "artifact.bin"
    original = b"original"
    tampered = b"tampered"
    artifact.write_bytes(original)
    original_stat = artifact.stat()
    original_digest = hashlib.sha256(original).hexdigest()
    record = {
        "path": artifact,
        "manifest": "fastq_qc/qc_manifest.json",
        "declared_path": "artifact.bin",
        "declared_sha256": original_digest,
        "declared_size_bytes": len(original),
    }

    first = service._artifact_descriptor("job-a", record, "alignment")
    artifact.write_bytes(tampered)
    os.utime(artifact, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    second = service._artifact_descriptor("job-a", record, "alignment")

    assert first["observed_sha256"] == original_digest
    assert second["observed_sha256"] == hashlib.sha256(tampered).hexdigest()
    assert second["integrity_valid"] is False


def test_verified_snapshot_rejects_ancestor_symlink_swap(tmp_path: Path) -> None:
    from services import ngs_alignment_sessions as service

    expected = b"artifact"
    artifact_dir = tmp_path / "job" / "evidence"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "artifact.bin"
    artifact.write_bytes(expected)
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    (external_dir / "artifact.bin").write_bytes(expected)
    artifact_dir.rename(tmp_path / "original-evidence")
    artifact_dir.symlink_to(external_dir, target_is_directory=True)

    with pytest.raises(service.AlignmentSessionError, match="unsafe"):
        service.open_verified_artifact_snapshot(
            artifact,
            expected_size=len(expected),
            expected_sha256=hashlib.sha256(expected).hexdigest(),
        )


def test_verified_snapshot_is_stable_after_source_mutation(tmp_path: Path) -> None:
    from services import ngs_alignment_sessions as service

    original = b"verified"
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(original)
    snapshot = service.open_verified_artifact_snapshot(
        artifact,
        expected_size=len(original),
        expected_sha256=hashlib.sha256(original).hexdigest(),
    )
    try:
        artifact.write_bytes(b"mutated!")
        assert snapshot.read() == original
    finally:
        snapshot.close()


def test_verified_snapshot_descriptor_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services import ngs_alignment_sessions as service

    payload = b"immutable"
    _isolate_snapshot_state(service, monkeypatch, tmp_path, limit=1024)
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(payload)
    snapshot = service.open_verified_artifact_snapshot(
        artifact,
        expected_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )

    with pytest.raises(OSError):
        os.write(snapshot.fileno(), b"X")
    snapshot.seek(0)
    assert snapshot.read() == payload
    snapshot.close()


def _isolate_snapshot_state(service, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, limit: int) -> None:
    cache_dir = tmp_path / "snapshots"
    cache_dir.mkdir()
    lock = threading.RLock()
    monkeypatch.setattr(service, "SNAPSHOT_CACHE_MAX_BYTES", limit)
    monkeypatch.setattr(service, "_snapshot_cache_lock", lock)
    monkeypatch.setattr(service, "_snapshot_cache_condition", threading.Condition(lock), raising=False)
    monkeypatch.setattr(service, "_snapshot_cache_dir", cache_dir)
    monkeypatch.setattr(service, "_snapshot_cache_owner", None, raising=False)
    monkeypatch.setattr(service, "_snapshot_cache", service.OrderedDict())
    monkeypatch.setattr(service, "_snapshot_cache_leases", {}, raising=False)
    monkeypatch.setattr(service, "_snapshot_cache_bytes", 0)
    monkeypatch.setattr(service, "_snapshot_inflight", set(), raising=False)
    monkeypatch.setattr(service, "_snapshot_inflight_bytes", 0, raising=False)


def test_oversized_snapshot_is_rejected_before_source_or_temporary_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    _isolate_snapshot_state(service, monkeypatch, tmp_path, limit=4)
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"12345")
    source_opened = False
    temporary_opened = False

    def fail_source(_path: Path):
        nonlocal source_opened
        source_opened = True
        raise AssertionError("source must not open")

    def fail_temporary(*_args, **_kwargs):
        nonlocal temporary_opened
        temporary_opened = True
        raise AssertionError("temporary must not open")

    monkeypatch.setattr(service, "_open_regular_file_no_symlinks", fail_source)
    monkeypatch.setattr(service.tempfile, "NamedTemporaryFile", fail_temporary)

    with pytest.raises(service.AlignmentSessionError, match="exceeds snapshot limit"):
        service.open_verified_artifact_snapshot(
            artifact,
            expected_size=5,
            expected_sha256=hashlib.sha256(b"12345").hexdigest(),
        )

    assert source_opened is False
    assert temporary_opened is False


def test_same_digest_snapshot_copy_is_single_flight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    payload = b"12345678"
    _isolate_snapshot_state(service, monkeypatch, tmp_path, limit=len(payload))
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(payload)
    first_read_started = threading.Event()
    release_first_read = threading.Event()
    source_open_count = 0
    source_count_lock = threading.Lock()
    original_open = service._open_regular_file_no_symlinks

    class BlockingSource:
        def __init__(self, handle) -> None:
            self._handle = handle

        def read(self, size: int = -1) -> bytes:
            first_read_started.set()
            assert release_first_read.wait(timeout=2)
            return self._handle.read(size)

        def fileno(self) -> int:
            return self._handle.fileno()

        def close(self) -> None:
            self._handle.close()

    def open_source(path: Path):
        nonlocal source_open_count
        if path != artifact:
            return original_open(path)
        with source_count_lock:
            source_open_count += 1
        return BlockingSource(original_open(path))

    monkeypatch.setattr(service, "_open_regular_file_no_symlinks", open_source)
    kwargs = {
        "expected_size": len(payload),
        "expected_sha256": hashlib.sha256(payload).hexdigest(),
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.open_verified_artifact_snapshot, artifact, **kwargs)
        assert first_read_started.wait(timeout=2)
        second = executor.submit(service.open_verified_artifact_snapshot, artifact, **kwargs)
        assert second.done() is False
        release_first_read.set()
        first_snapshot = first.result(timeout=2)
        second_snapshot = second.result(timeout=2)

    assert source_open_count == 1
    assert first_snapshot.read() == payload
    assert second_snapshot.read() == payload
    first_snapshot.close()
    second_snapshot.close()


def test_active_snapshot_lease_causes_fail_fast_capacity_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    _isolate_snapshot_state(service, monkeypatch, tmp_path, limit=4)
    first_path = tmp_path / "first.bin"
    second_path = tmp_path / "second.bin"
    first_path.write_bytes(b"1111")
    second_path.write_bytes(b"2222")
    original_open = service._open_regular_file_no_symlinks
    second_source_opened = threading.Event()

    def tracked_open(path: Path):
        if path == second_path:
            second_source_opened.set()
        return original_open(path)

    monkeypatch.setattr(service, "_open_regular_file_no_symlinks", tracked_open)
    first_snapshot = service.open_verified_artifact_snapshot(
        first_path,
        expected_size=4,
        expected_sha256=hashlib.sha256(b"1111").hexdigest(),
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        second = executor.submit(
            service.open_verified_artifact_snapshot,
            second_path,
            expected_size=4,
            expected_sha256=hashlib.sha256(b"2222").hexdigest(),
        )
        try:
            with pytest.raises(service.AlignmentSessionError, match="capacity unavailable"):
                second.result(timeout=0.2)
            assert second_source_opened.is_set() is False
        finally:
            first_snapshot.close()

    second_snapshot = service.open_verified_artifact_snapshot(
        second_path,
        expected_size=4,
        expected_sha256=hashlib.sha256(b"2222").hexdigest(),
    )
    assert second_snapshot.read() == b"2222"
    second_snapshot.close()


def test_temporary_open_failure_closes_source_and_releases_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    payload = b"1234"
    _isolate_snapshot_state(service, monkeypatch, tmp_path, limit=4)
    source = io.BytesIO(payload)
    monkeypatch.setattr(service, "_open_regular_file_no_symlinks", lambda _path: source)
    monkeypatch.setattr(
        service.tempfile,
        "NamedTemporaryFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    with pytest.raises(OSError, match="disk unavailable"):
        service.open_verified_artifact_snapshot(
            tmp_path / "artifact.bin",
            expected_size=4,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )

    assert source.closed is True
    assert service._snapshot_inflight_bytes == 0
    assert service._snapshot_inflight == set()


def test_missing_cached_snapshot_releases_accounted_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    digest = "a" * 64
    monkeypatch.setattr(service, "_snapshot_cache_dir", tmp_path)
    monkeypatch.setattr(service, "_snapshot_cache", service.OrderedDict([(digest, 17)]))
    monkeypatch.setattr(service, "_snapshot_cache_bytes", 17)

    assert service._cached_snapshot(digest, 17) is None
    assert service._snapshot_cache == {}
    assert service._snapshot_cache_bytes == 0


@pytest.mark.asyncio
async def test_artifact_snapshot_open_runs_outside_the_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes

    artifact = tmp_path / "alignment.bam"
    artifact.write_bytes(b"bam")
    event_loop_thread = threading.get_ident()
    opener_thread: int | None = None

    def open_snapshot(*_args, **_kwargs):
        nonlocal opener_thread
        opener_thread = threading.get_ident()
        return io.BytesIO(b"bam")

    monkeypatch.setattr(routes.service, "open_verified_artifact_snapshot", open_snapshot)
    request = Request({"type": "http", "method": "GET", "path": "/artifact", "headers": []})
    response = await routes._serve_artifact(
        artifact,
        {"size_bytes": 3, "sha256": hashlib.sha256(b"bam").hexdigest(), "mime_type": "application/octet-stream"},
        request,
    )

    assert response.status_code == 200
    assert opener_thread is not None
    assert opener_thread != event_loop_thread
    await response.body_iterator.aclose()


def test_alignment_routes_enforce_the_job_authorization_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import ngs_alignment_sessions as service

    monkeypatch.setattr(
        service,
        "build_alignment_sessions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("service must not run")),
    )

    async def deny_access() -> str:
        raise HTTPException(status_code=403, detail="job access denied")

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = deny_access
    client = TestClient(app)
    for url in (
        "/api/jobs/job-b/alignment-sessions",
        f"/api/jobs/job-b/alignment-session-artifacts/primary/alignment/{'0' * 64}",
    ):
        response = client.get(url)
        assert response.status_code == 403
        assert response.json() == {"detail": "job access denied"}


def test_read_inspection_caps_cursor_and_total_records_scanned(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import ngs_alignment_sessions as service

    with pytest.raises(service.AlignmentSessionError, match="cursor must not exceed"):
        service.read_bam_page(Path("/tmp/aligned.bam"), cursor=str(service.MAX_READ_CURSOR + 1))

    monkeypatch.setattr(service, "MAX_READ_SCAN", 2)
    monkeypatch.setattr(
        service,
        "_iter_sam_lines",
        lambda *_args, **_kwargs: iter(
            [
                "r1\t0\tref\t1\t60\t4M\t*\t0\t0\tACGT\tIIII",
                "r2\t0\tref\t2\t60\t4M\t*\t0\t0\tACGT\tIIII",
                "r3\t0\tref\t3\t60\t4M\t*\t0\t0\tACGT\tIIII",
            ]
        ),
    )
    page = service.read_bam_page(Path("/tmp/aligned.bam"), q="not-present")
    assert page["reads"] == []
    assert page["next_cursor"] is None
    assert page["scan_truncated"] is True


def test_equal_length_wrong_reference_fails_exact_identity_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    bam = tmp_path / "aligned.bam"
    index = tmp_path / "aligned.bam.bai"
    reference = tmp_path / "reference.fasta"
    bam.write_bytes(b"bam")
    index.write_bytes(b"bai")
    reference.write_text(">ref\nCCCCCCCC\n", encoding="utf-8")
    bam_reference_md5 = hashlib.md5(b"AAAAAAAA", usedforsecurity=False).hexdigest()

    def fake_run(command, **_kwargs):
        stdout = f"@SQ\tSN:ref\tLN:8\tM5:{bam_reference_md5}\n" if "-H" in command else ""
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    valid, reason = service._validate_alignment_bundle(bam, index, reference, None)

    assert valid is False
    assert reason is not None and "exact reference identity" in reason


def test_missing_m5_accepts_only_matching_server_manifest_reference_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    bam = tmp_path / "aligned.bam"
    index = tmp_path / "aligned.bam.bai"
    reference = tmp_path / "reference.fasta"
    bam.write_bytes(b"bam")
    index.write_bytes(b"bai")
    reference.write_text(">nondefault_contig\nACGTACGT\n", encoding="utf-8")

    def fake_run(command, **_kwargs):
        stdout = "@SQ\tSN:nondefault_contig\tLN:8\n" if "-H" in command else ""
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    expected = hashlib.sha256(b"ACGTACGT").hexdigest()

    assert service._validate_alignment_bundle(bam, index, reference, expected) == (True, None)
    valid, reason = service._validate_alignment_bundle(bam, index, reference, "0" * 64)
    assert valid is False
    assert reason is not None and "manifest binding" in reason


def test_manifest_declared_integrity_is_preserved_and_mismatch_is_not_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    manifest_dir = tmp_path / "job-a" / "fastq_qc"
    _write_manifest(manifest_dir)
    payload = json.loads((manifest_dir / "qc_manifest.json").read_text(encoding="utf-8"))
    for artifact in payload["artifacts"]:
        path = manifest_dir / artifact["path"]
        artifact["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        artifact["size_bytes"] = path.stat().st_size
    payload["artifacts"][2]["sha256"] = "0" * 64
    (manifest_dir / "qc_manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_: (True, None))

    primary = service.build_alignment_sessions("job-a", results_dir=tmp_path)[0]

    assert primary["ready"] is False
    alignment = primary["artifacts"]["alignment"]
    assert alignment["declared_sha256"] == "0" * 64
    assert alignment["observed_sha256"] != alignment["declared_sha256"]
    assert "integrity" in primary["unavailable_reason"].lower()


def test_exact_read_detail_scan_exhaustion_is_not_reported_as_404(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import ngs_alignment_sessions as service

    monkeypatch.setattr(service, "resolve_session_bam", lambda *_args, **_kwargs: Path("/tmp/aligned.bam"))
    monkeypatch.setattr(
        service,
        "read_bam_exact",
        lambda *_args, **_kwargs: {"read": None, "scan_truncated": True},
        raising=False,
    )
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(output_dir="/tmp/job-a-run")

    response = TestClient(app).get("/api/jobs/job-a/reads/target?session_id=session-a")

    assert response.status_code == 409
    assert response.json()["detail"]["scan_truncated"] is True


def test_production_dimer_process_emits_discoverable_authoritative_manifest() -> None:
    module_path = API_ROOT.parents[1] / "modules" / "ngs" / "fastq_dimer_qc.nf"
    source = module_path.read_text(encoding="utf-8")

    assert 'path "qc_manifest.json", emit: qc_manifest' in source
    assert 'scripts/build_alignment_session_manifest.sh' in source
    assert 'dimer_candidates.aligned.bam' in source
    assert 'dimer_reference.fasta' in source
