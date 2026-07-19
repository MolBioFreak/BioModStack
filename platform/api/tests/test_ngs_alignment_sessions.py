from __future__ import annotations

import asyncio
import json
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def _write_manifest(directory: Path, *, prefix: str = "aligned") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reference.fasta").write_text(">ref\nACGTACGT\n", encoding="utf-8")
    (directory / "reference.fasta.fai").write_text("ref\t8\t5\t8\t9\n", encoding="utf-8")
    (directory / f"{prefix}.bam").write_bytes(b"bam")
    (directory / f"{prefix}.bam.bai").write_bytes(b"bai")
    payload = {
        "artifact_schema_version": 2,
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
    _write_manifest(output_dir / "fastq_qc")
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
    response = TestClient(app).get("/api/jobs/job-b/alignment-sessions")
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
    service._validate_alignment_bundle_cached.cache_clear()

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
    service._validate_alignment_bundle_cached.cache_clear()
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
    assert 'scripts/build_alignment_session_manifest.py' in source
    assert 'dimer_candidates.aligned.bam' in source
    assert 'dimer_reference.fasta' in source
