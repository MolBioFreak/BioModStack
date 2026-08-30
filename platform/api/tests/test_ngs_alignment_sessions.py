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
from typing import Any, cast


import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import files as files_router  # noqa: E402
from routers import ngs_alignment_sessions as ngs_routes  # noqa: E402


@pytest.fixture(autouse=True)
def _stable_derived_artifact_creation_authority(monkeypatch: pytest.MonkeyPatch):
    from services import ngs_alignment_sessions as service

    monkeypatch.setattr(service, "_creation_authority", lambda: ("1" * 40, "2" * 40))


def _ngs_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(ngs_routes.OntNgsRouteError, ngs_routes.ont_ngs_route_error_handler)
    ngs_routes.install_governed_ngs_openapi(app)
    return app


def test_governed_read_query_validation_is_runtime_typed() -> None:
    app = _ngs_app()
    app.include_router(ngs_routes.router, prefix="/api")
    app.dependency_overrides[ngs_routes.require_alignment_job] = lambda: SimpleNamespace(id="00000000-0000-4000-8000-000000000001")
    client = TestClient(app, client=("127.0.0.1", 40000))

    for url in (
        "/api/jobs/00000000-0000-4000-8000-000000000001/reads",
        "/api/jobs/00000000-0000-4000-8000-000000000001/reads?session_id=s&limit=not-an-int",
        "/api/jobs/00000000-0000-4000-8000-000000000001/reads/read-a?session_id=s&start=0",
    ):
        response = client.get(url)
        assert response.status_code == 400
        assert response.json() == {
            "schema": "bms.ngs.error.v1",
            "code": "NGS_RANGE_INVALID",
            "message": "The read query parameters are invalid.",
            "job_id": "00000000-0000-4000-8000-000000000001",
            "resource": "read",
            "retryable": False,
        }


def test_content_disposition_uses_descriptor_authority() -> None:
    digest = "a" * 64
    assert ngs_routes._artifact_content_disposition(
        Path("consensus.fasta"),
        {"kind": "consensus_fasta", "content_disposition": "attachment", "filename_extension": "fasta"},
        digest,
    ) == 'attachment; filename="consensus_fasta-aaaaaaaaaaaa.fasta"'
    assert ngs_routes._artifact_content_disposition(
        Path("coverage.bedgraph"),
        {"kind": "depth_bedgraph", "content_disposition": "inline", "filename_extension": "bedgraph"},
        digest,
    ) == 'inline; filename="depth_bedgraph-aaaaaaaaaaaa.bedgraph"'


def test_governed_ngs_openapi_has_exact_web6_components_and_status_maps() -> None:
    from routers import sequence_qc

    app = _ngs_app()
    app.include_router(ngs_routes.router, prefix="/api")
    app.include_router(sequence_qc.router, prefix="/api")
    document = app.openapi()
    components = document["components"]["schemas"]
    assert {
        "OntFastqQcResultV1",
        "OntAlignmentSessionListV1",
        "OntAlignmentSessionDetailV1",
        "OntNgsRotationSuccessV1",
        "OntNgsCapabilityRevocationSuccessV1",
        "OntNgsErrorV1",
        "BinaryArtifactResponse",
        "OntAlignmentPresentationV1",
        "OntAlignmentLocusSliceRequestV1",
        "OntAlignmentLocusSliceV1",
    } <= set(components)
    error_schema = components["OntNgsErrorV1"]
    assert error_schema["properties"]["job_id"]["format"] == "uuid"
    assert error_schema["properties"]["message"]["minLength"] == 1
    assert error_schema["properties"]["message"]["maxLength"] == 512
    assert error_schema["allOf"][0]["then"]["properties"]["retryable"] == {"const": True}
    sessions_schema = components["OntAlignmentSessionListV1"]["properties"]["sessions"]
    assert sessions_schema["minItems"] == 1 and sessions_schema["maxItems"] == 2
    assert sessions_schema["items"] is False
    assert len(sessions_schema["prefixItems"]) == 2
    artifact_roles = components["OntAlignmentArtifactsV1"]
    assert {"alignment", "alignment_index", "reference", "reference_index"} <= set(artifact_roles["required"])
    for role in ("alignment", "alignment_index", "reference", "reference_index"):
        assert artifact_roles["properties"][role] == {"$ref": "#/components/schemas/OntAlignmentArtifactV1"}

    exact_statuses = {
        ("/api/jobs/{job_id}/ngs-result", "get"): {"200", "403", "404", "409"},
        ("/api/jobs/{job_id}/alignment-sessions", "get"): {"200", "403", "404", "409"},
        ("/api/jobs/{job_id}/alignment-sessions/{session_id}", "get"): {"200", "403", "404", "409"},
        ("/api/jobs/{job_id}/alignment-access/rotate", "post"): {"200", "403", "404", "409"},
        ("/api/jobs/{job_id}/alignment-access", "delete"): {"200", "403", "404", "409"},
        ("/api/jobs/{job_id}/reads", "get"): {"200", "400", "403", "404", "409"},
        ("/api/jobs/{job_id}/reads/{read_id}", "get"): {"200", "400", "403", "404", "409"},
        ("/api/jobs/{job_id}/alignment-sessions/{session_id}/presentation", "get"): {"200", "403", "404", "409"},
        ("/api/jobs/{job_id}/alignment-sessions/{session_id}/locus-slices", "post"): {"200", "400", "403", "404", "409"},
    }
    binary_statuses = {"200", "206", "304", "400", "403", "404", "409", "416"}
    for path in (
        "/api/jobs/{job_id}/ngs-artifacts/{artifact_id}",
        "/api/jobs/{job_id}/alignment-artifacts/{artifact_id}",
        "/api/jobs/{job_id}/alignment-session-artifacts/{mode}/{role}/{sha256}",
        "/api/jobs/{job_id}/alignment-sessions/{session_id}/preview/{kind}",
        "/api/jobs/{job_id}/alignment-sessions/{session_id}/presentation/{kind}",
        "/api/jobs/{job_id}/alignment-sessions/{session_id}/locus-slices/{slice_id}/{kind}",
    ):
        exact_statuses[(path, "get")] = binary_statuses
        exact_statuses[(path, "head")] = binary_statuses

    for (path, method), statuses in exact_statuses.items():
        responses = document["paths"][path][method]["responses"]
        assert set(responses) == statuses
        for status in statuses - {"200", "206", "304"}:
            schema = responses[status]["content"]["application/json"]["schema"]
            assert schema["$ref"] == "#/components/schemas/OntNgsErrorV1"
        if statuses == binary_statuses:
            assert responses["206"]["headers"]["Content-Range"]["schema"] == {"type": "string"}
            assert responses["416"]["headers"]["Content-Range"]["schema"] == {"type": "string"}


def test_alignment_preview_is_deterministic_and_source_bound(tmp_path: Path) -> None:
    import pysam
    from services import ngs_alignment_sessions as service

    source_bam = tmp_path / "source.bam"
    header = {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "plasmid", "LN": 1000}]}
    with pysam.AlignmentFile(source_bam, "wb", header=header) as output:
        for index in range(12):
            read = pysam.AlignedSegment()
            read.query_name = f"read-{index:02d}"
            read.query_sequence = "ACGT" * 10
            read.flag = 0
            read.reference_id = 0
            read.reference_start = index * 10
            read.mapping_quality = 60
            read.cigar = ((0, 40),)
            read.query_qualities = pysam.qualitystring_to_array("I" * 40)
            output.write(read)
    pysam.index(str(source_bam))
    source_index = Path(f"{source_bam}.bai")
    bam_sha, bam_size = service._sha256_file_and_size(source_bam)
    index_sha, index_size = service._sha256_file_and_size(source_index)

    first = service.build_alignment_preview(
        source_bam,
        bam_sha256=bam_sha,
        bam_size_bytes=bam_size,
        index=source_index,
        index_sha256=index_sha,
        index_size_bytes=index_size,
        cache_root=tmp_path / "cache",
        target_reads=3,
    )
    second = service.build_alignment_preview(
        source_bam,
        bam_sha256=bam_sha,
        bam_size_bytes=bam_size,
        index=source_index,
        index_sha256=index_sha,
        index_size_bytes=index_size,
        cache_root=tmp_path / "cache",
        target_reads=3,
    )

    assert first[1]["sha256"] == second[1]["sha256"]
    assert first[3]["sha256"] == second[3]["sha256"]
    assert first[1]["source_alignment_sha256"] == bam_sha
    assert first[1]["source_index_sha256"] == index_sha
    with pysam.AlignmentFile(first[0], "rb") as preview:
        assert preview.count(until_eof=True) == 3


def _write_governed_alignment_fixture(path: Path, *, read_count: int = 12) -> tuple[Path, str, int, str, int]:
    import pysam
    from services import ngs_alignment_sessions as service

    header = {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "plasmid", "LN": 1000}]}
    with pysam.AlignmentFile(path, "wb", header=header) as output:
        supplementary_records = []
        for index in range(read_count):
            primary = pysam.AlignedSegment()
            primary.query_name = f"read-{index:02d}"
            primary.query_sequence = "ACGT" * 10
            primary.flag = 16 if index % 2 else 0
            primary.reference_id = 0
            primary.reference_start = index * 10
            primary.mapping_quality = 60
            primary.cigar = ((0, 40),)
            primary.query_qualities = pysam.qualitystring_to_array("I" * 40)
            output.write(primary)
            if index < 3:
                supplementary = pysam.AlignedSegment()
                supplementary.query_name = primary.query_name
                supplementary.query_sequence = primary.query_sequence
                supplementary.flag = 2048
                supplementary.reference_id = 0
                supplementary.reference_start = 500 + index * 10
                supplementary.mapping_quality = 30
                supplementary.cigar = ((0, 40),)
                supplementary.query_qualities = primary.query_qualities
                supplementary_records.append(supplementary)
        for supplementary in supplementary_records:
            output.write(supplementary)
        secondary = pysam.AlignedSegment()
        secondary.query_name = "read-00"
        secondary.query_sequence = "ACGT" * 10
        secondary.flag = 256
        secondary.reference_id = 0
        secondary.reference_start = 900
        secondary.mapping_quality = 20
        secondary.cigar = ((0, 40),)
        secondary.query_qualities = pysam.qualitystring_to_array("I" * 40)
        output.write(secondary)
        unmapped = pysam.AlignedSegment()
        unmapped.query_name = "unmapped"
        unmapped.query_sequence = "A" * 40
        unmapped.flag = 4
        unmapped.reference_id = -1
        unmapped.reference_start = -1
        unmapped.query_qualities = pysam.qualitystring_to_array("I" * 40)
        output.write(unmapped)
    pysam.index(str(path))
    index = Path(f"{path}.bai")
    bam_sha, bam_size = service._sha256_file_and_size(path)
    bai_sha, bai_size = service._sha256_file_and_size(index)
    return index, bam_sha, bam_size, bai_sha, bai_size


def test_presentation_selects_unique_primary_reads_and_truthful_counts(tmp_path: Path) -> None:
    import pysam
    from services import ngs_alignment_sessions as service

    source = tmp_path / "source.bam"
    index, bam_sha, bam_size, bai_sha, bai_size = _write_governed_alignment_fixture(source)
    package = service.build_alignment_presentation(
        source,
        bam_sha256=bam_sha,
        bam_size_bytes=bam_size,
        index=index,
        index_sha256=bai_sha,
        index_size_bytes=bai_size,
        source_manifest_sha256="a" * 64,
        job_id="job-a",
        session_id="1" * 24,
        mode="primary",
        cache_root=tmp_path / "cache",
        target_reads=5,
        max_output_bytes=1_000_000,
    )

    receipt = package["manifest"]
    with pysam.AlignmentFile(package["bam_path"], "rb") as preview:
        records = list(preview.fetch(until_eof=True))
    assert len({record.query_name for record in records}) == 5
    assert all(not record.is_unmapped and not record.is_secondary and not record.is_supplementary for record in records)
    assert receipt["selected_read_count"] == 5
    assert receipt["selected_alignment_record_count"] == len(records) == 5
    assert receipt["source_primary_mapped_read_count"] == 12
    assert receipt["source_primary_mapped_alignment_record_count"] == 12
    assert receipt["source_alignment_record_count"] == 17
    assert receipt["source_record_counts"] == {
        "mapped_primary": 12,
        "secondary": 1,
        "supplementary": 3,
        "unmapped": 1,
    }
    assert receipt["selection_unit"] == "unique mapped primary read ID"
    assert receipt["inclusion_rules"] == ["mapped", "primary", "query_name present"]
    assert receipt["exclusion_rules"] == ["secondary", "supplementary", "unmapped", "missing query_name"]

    from routers import ngs_alignment_sessions as router
    response = router._presentation_response("job-a", "1" * 24, package)
    validated = router.OntAlignmentPresentationV1.model_validate(response)
    base = f"/api/jobs/job-a/alignment-sessions/{'1' * 24}/presentation/{receipt['authority_sha256']}"
    assert validated.preview.bam.url == f"{base}/bam"
    assert validated.preview.bam.kind == "alignment_preview"
    assert validated.coverage.artifact.url == f"{base}/coverage"
    assert validated.manifest.mime_type == "application/json"


def test_presentation_byte_ceiling_reduces_selection_deterministically(tmp_path: Path) -> None:
    from services import ngs_alignment_sessions as service

    source = tmp_path / "source.bam"
    index, bam_sha, bam_size, bai_sha, bai_size = _write_governed_alignment_fixture(source, read_count=30)
    common = dict(
        bam_sha256=bam_sha, bam_size_bytes=bam_size, index=index,
        index_sha256=bai_sha, index_size_bytes=bai_size,
        source_manifest_sha256="b" * 64, job_id="job-b", session_id="2" * 24,
        mode="primary", target_reads=30, max_output_bytes=250,
    )
    first = service.build_alignment_presentation(source, cache_root=tmp_path / "cache-a", **common)
    second = service.build_alignment_presentation(source, cache_root=tmp_path / "cache-b", **common)
    assert first["manifest"]["selected_read_count"] < 30
    assert first["manifest"]["output_byte_ceiling"] == 250
    assert first["bam_metadata"]["size_bytes"] <= 250
    assert first["manifest"]["selected_read_set_sha256"] == second["manifest"]["selected_read_set_sha256"]


def test_presentation_uses_direct_verified_descriptor_above_snapshot_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    source = tmp_path / "source.bam"
    index, bam_sha, bam_size, bai_sha, bai_size = _write_governed_alignment_fixture(source)
    monkeypatch.setattr(service, "SNAPSHOT_CACHE_MAX_BYTES", bam_size - 1)
    monkeypatch.setattr(
        service, "open_verified_artifact_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("shared snapshot cache used")),
    )
    package = service.build_alignment_presentation(
        source, bam_sha256=bam_sha, bam_size_bytes=bam_size, index=index,
        index_sha256=bai_sha, index_size_bytes=bai_size, source_manifest_sha256="c" * 64,
        job_id="job-c", session_id="3" * 24, mode="primary",
        cache_root=tmp_path / "cache", target_reads=3, max_output_bytes=1_000_000,
    )
    assert package["manifest"]["source_identity"]["size_bytes"] == bam_size


def test_presentation_coverage_uses_all_source_primary_records(tmp_path: Path) -> None:
    from services import ngs_alignment_sessions as service

    source = tmp_path / "source.bam"
    index, bam_sha, bam_size, bai_sha, bai_size = _write_governed_alignment_fixture(source)
    package = service.build_alignment_presentation(
        source, bam_sha256=bam_sha, bam_size_bytes=bam_size, index=index,
        index_sha256=bai_sha, index_size_bytes=bai_size, source_manifest_sha256="d" * 64,
        job_id="job-d", session_id="4" * 24, mode="primary", cache_root=tmp_path / "cache",
        target_reads=1, max_output_bytes=1_000_000,
    )
    coverage = package["coverage_path"].read_text(encoding="utf-8")
    assert package["manifest"]["coverage_semantics"] == "mean primary mapped alignment depth from full source"
    assert package["manifest"]["coverage_bin_width"] >= 1
    assert "plasmid\t" in coverage
    assert package["manifest"]["source_primary_mapped_alignment_record_count"] == 12


def test_cached_presentation_resolution_does_not_open_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services import ngs_alignment_sessions as service

    source = tmp_path / "source.bam"
    index, bam_sha, bam_size, bai_sha, bai_size = _write_governed_alignment_fixture(source)
    service.build_alignment_presentation(
        source, bam_sha256=bam_sha, bam_size_bytes=bam_size, index=index,
        index_sha256=bai_sha, index_size_bytes=bai_size, source_manifest_sha256="e" * 64,
        job_id="job-e", session_id="5" * 24, mode="primary", cache_root=tmp_path / "cache",
        target_reads=3, max_output_bytes=1_000_000,
    )
    monkeypatch.setattr(service, "_open_regular_file_no_symlinks", lambda *_args: (_ for _ in ()).throw(AssertionError("source opened")))
    package = service.resolve_cached_alignment_presentation("job-e", "5" * 24, cache_root=tmp_path / "cache")
    assert package["manifest"]["source_alignment_sha256"] == bam_sha


def test_locus_slice_validates_and_deterministically_caps_primary_reads(tmp_path: Path) -> None:
    import pysam
    from services import ngs_alignment_sessions as service

    source = tmp_path / "source.bam"
    index, bam_sha, bam_size, bai_sha, bai_size = _write_governed_alignment_fixture(source)
    identity = service.source_stat_identity(source)
    common = dict(
        bam_sha256=bam_sha, bam_size_bytes=bam_size, index=index,
        index_sha256=bai_sha, index_size_bytes=bai_size, source_identity=identity,
        source_manifest_sha256="f" * 64, job_id="job-f", session_id="6" * 24,
        contig="plasmid", start=1, end=600, max_reads=3, cache_root=tmp_path / "cache",
    )
    first = service.build_alignment_locus_slice(source, **common)
    second = service.build_alignment_locus_slice(source, **common)
    assert first["receipt"]["overlapping_read_count"] > 3
    assert first["receipt"]["selected_read_count"] == 3
    assert first["receipt"]["capped"] is True
    assert first["receipt"]["selected_read_set_sha256"] == second["receipt"]["selected_read_set_sha256"]
    assert first["receipt"]["source_alignment_sha256"] == bam_sha
    assert first["receipt"]["source_index_sha256"] == bai_sha
    assert first["receipt"]["selection_unit"] == "unique mapped primary read ID with associated supplementary records"
    assert first["receipt"]["policy"] == {
        "id": "bounded-full-source-locus-slice",
        "version": 1,
        "max_reads": 3,
        "max_records": service.LOCUS_MAX_RECORDS,
        "max_bytes": service.LOCUS_MAX_BYTES,
        "max_span_bp": service.LOCUS_MAX_SPAN,
        "max_seconds": service.LOCUS_MAX_SECONDS,
    }
    with pysam.AlignmentFile(first["bam_path"], "rb") as sliced:
        assert len({record.query_name for record in sliced.fetch(until_eof=True)}) == 3
    with pytest.raises(service.AlignmentSessionError, match="contig"):
        service.build_alignment_locus_slice(source, **{**common, "contig": "../bad"})
    with pytest.raises(service.AlignmentSessionError, match="span"):
        service.build_alignment_locus_slice(source, **{**common, "end": service.LOCUS_MAX_SPAN + 1})


def test_locus_slice_caps_output_records_without_rejecting_a_deep_source_region(tmp_path: Path) -> None:
    import pysam
    from services import ngs_alignment_sessions as service

    source = tmp_path / "source.bam"
    index, bam_sha, bam_size, bai_sha, bai_size = _write_governed_alignment_fixture(source, read_count=30)
    package = service.build_alignment_locus_slice(
        source,
        bam_sha256=bam_sha,
        bam_size_bytes=bam_size,
        index=index,
        index_sha256=bai_sha,
        index_size_bytes=bai_size,
        source_identity=service.source_stat_identity(source),
        source_manifest_sha256="9" * 64,
        job_id="job-deep",
        session_id="7" * 24,
        contig="plasmid",
        start=1,
        end=600,
        max_reads=10,
        max_records=3,
        cache_root=tmp_path / "cache",
    )

    assert package["receipt"]["overlapping_read_count"] > 10
    assert package["receipt"]["selected_read_count"] <= 3
    assert package["receipt"]["selected_record_count"] <= 3
    assert package["receipt"]["capped"] is True
    with pysam.AlignmentFile(package["bam_path"], "rb") as sliced:
        assert sliced.count(until_eof=True) <= 3


@pytest.mark.asyncio
async def test_presentation_get_path_never_materializes_a_missing_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager
    from routers import ngs_alignment_sessions as router
    from services import ngs_alignment_sessions as service

    job = SimpleNamespace(output_dir=str(tmp_path), child_output_dir=None)
    monkeypatch.setattr(
        service,
        "resolve_cached_alignment_presentation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(service.AlignmentSessionError("not prepared")),
    )

    @asynccontextmanager
    async def pinned_root(_job):
        yield tmp_path

    monkeypatch.setattr(router, "_validated_pinned_result_root", pinned_root)
    monkeypatch.setattr(router, "_job_session_authority", lambda _job: {})
    monkeypatch.setattr(
        service,
        "build_alignment_sessions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP request tried to prepare presentation")),
    )

    with pytest.raises(service.AlignmentSessionError, match="not prepared"):
        await router._prepare_presentation("job-a", "1" * 24, job)


def test_locus_slice_rejects_source_identity_mismatch(tmp_path: Path) -> None:
    from services import ngs_alignment_sessions as service

    source = tmp_path / "source.bam"
    index, bam_sha, bam_size, bai_sha, bai_size = _write_governed_alignment_fixture(source)
    identity = service.source_stat_identity(source)
    identity["mtime_ns"] += 1
    with pytest.raises(service.AlignmentSessionError, match="identity"):
        service.build_alignment_locus_slice(
            source, bam_sha256=bam_sha, bam_size_bytes=bam_size, index=index,
            index_sha256=bai_sha, index_size_bytes=bai_size, source_identity=identity,
            source_manifest_sha256="f" * 64, job_id="job-f", session_id="6" * 24,
            contig="plasmid", start=1, end=100, max_reads=3, cache_root=tmp_path / "cache",
        )


def test_samtools_command_uses_pinned_no_network_ont_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    container_dir = tmp_path / "apptainer"
    container_dir.mkdir()
    image = container_dir / "dorado-v1.3.1-samtools-v1.24.sif"
    image.write_bytes(b"pinned-runtime")
    monkeypatch.setenv("BMS_NGS_RUNTIME_SIF", str(image))
    monkeypatch.setattr(
        service.shutil,
        "which",
        lambda command: "/usr/bin/apptainer" if command == "apptainer" else None,
    )
    monkeypatch.setattr(
        service,
        "_ngs_runtime_identity",
        lambda: (hashlib.sha256(image.read_bytes()).hexdigest(), "1.24"),
    )
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(stdout="samtools 1.24\nUsing htslib 1.24\n")

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    service._clear_samtools_runtime_cache()
    try:
        command = service._samtools_command()
        assert tuple(command) == (
            "/usr/bin/apptainer",
            "exec",
            "--no-home",
            "--pid",
            "--net",
            "--network",
            "none",
            f"/proc/self/fd/{command.pass_fds[0]}",
            "samtools",
        )
        assert command.runtime_path is not None
        assert command.runtime_path.parent.parent == container_dir
        assert os.stat(command.runtime_path, follow_symlinks=False).st_ino == os.fstat(command.pass_fds[0]).st_ino
        assert os.fstat(command.pass_fds[0]).st_mode & 0o222 == 0
        assert "--bind" not in command.argv
        assert observed["command"] == [*command, "--version"]
        assert observed["kwargs"]["pass_fds"] == command.pass_fds
    finally:
        service._clear_samtools_runtime_cache()


@pytest.mark.parametrize("unsafe", ["digest", "version", "runtime_symlink", "parent_symlink"])
def test_samtools_runtime_rejects_unpinned_or_unsafe_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    from services import ngs_alignment_sessions as service

    real = tmp_path / "real"
    real.mkdir()
    image = real / "dorado.sif"
    image.write_bytes(b"approved")
    runtime_path = image
    expected_digest = hashlib.sha256(image.read_bytes()).hexdigest()
    expected_version = "1.24"
    if unsafe == "digest":
        expected_digest = "0" * 64
    elif unsafe == "version":
        expected_version = "1.23"
    elif unsafe == "runtime_symlink":
        runtime_path = tmp_path / "runtime-link.sif"
        runtime_path.symlink_to(image)
    elif unsafe == "parent_symlink":
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(real, target_is_directory=True)
        runtime_path = linked_parent / image.name
    monkeypatch.setenv("BMS_NGS_RUNTIME_SIF", str(runtime_path))
    monkeypatch.setattr(service.shutil, "which", lambda _command: "/usr/bin/apptainer")
    monkeypatch.setattr(service, "_ngs_runtime_identity", lambda: (expected_digest, expected_version))
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="samtools 1.24\nUsing htslib 1.24\n"),
    )
    service._clear_samtools_runtime_cache()
    try:
        with pytest.raises(service.AlignmentSessionError):
            service._samtools_command()
    finally:
        service._clear_samtools_runtime_cache()


def test_samtools_read_inspection_uses_inherited_snapshot_descriptors_without_results_bind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    image = tmp_path / "dorado.sif"
    image.write_bytes(b"approved")
    monkeypatch.setenv("BMS_NGS_RUNTIME_SIF", str(image))
    monkeypatch.setattr(service.shutil, "which", lambda _command: "/usr/bin/apptainer")
    monkeypatch.setattr(
        service,
        "_ngs_runtime_identity",
        lambda: (hashlib.sha256(image.read_bytes()).hexdigest(), "1.24"),
    )
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="samtools 1.24\nUsing htslib 1.24\n"),
    )
    bam = tmp_path / "aligned.bam"
    bai = tmp_path / "aligned.bam.bai"
    bam.write_bytes(b"bam")
    bai.write_bytes(b"bai")
    observed = {}

    class FakeProcess:
        stdout = io.StringIO("")
        stderr = io.StringIO("")
        def wait(self, timeout=None):
            return 0
        def poll(self):
            return 0

    def fake_popen(command, **kwargs):
        observed["command"] = command
        observed["pass_fds"] = kwargs["pass_fds"]
        return FakeProcess()

    monkeypatch.setattr(service.subprocess, "Popen", fake_popen)
    service._clear_samtools_runtime_cache()
    try:
        list(service._iter_sam_lines(bam, index=bai))
        assert "--bind" not in observed["command"]
        assert "-X" in observed["command"]
        descriptor_inputs = [item for item in observed["command"] if item.startswith("/proc/self/fd/")]
        assert len(descriptor_inputs) == 3
        assert set(int(item.rsplit("/", 1)[1]) for item in descriptor_inputs).issubset(set(observed["pass_fds"]))
    finally:
        service._clear_samtools_runtime_cache()


def test_samtools_runtime_private_snapshot_survives_source_mutation_after_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    image = tmp_path / "dorado.sif"
    image.write_bytes(b"approved")
    monkeypatch.setenv("BMS_NGS_RUNTIME_SIF", str(image))
    monkeypatch.setattr(service.shutil, "which", lambda _command: "/usr/bin/apptainer")
    monkeypatch.setattr(
        service,
        "_ngs_runtime_identity",
        lambda: (hashlib.sha256(b"approved").hexdigest(), "1.24"),
    )
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="samtools 1.24\nUsing htslib 1.24\n"),
    )
    service._clear_samtools_runtime_cache()
    try:
        command = service._samtools_command()
        runtime_fd = command.pass_fds[0]
        assert os.pread(runtime_fd, command.runtime_size or 0, 0) == b"approved"
        assert os.fstat(runtime_fd).st_mode & 0o222 == 0
        with image.open("r+b") as handle:
            handle.seek(0)
            handle.write(b"tampered")
            handle.truncate()
        command.verify_runtime()
        assert os.pread(runtime_fd, command.runtime_size or 0, 0) == b"approved"
    finally:
        service._clear_samtools_runtime_cache()


def test_samtools_runtime_rejects_wrong_observed_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    image = tmp_path / "dorado.sif"
    image.write_bytes(b"approved")
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setenv("BMS_NGS_RUNTIME_SIF", str(image))
    monkeypatch.setenv("BMS_RESULTS_DIR", str(results))
    monkeypatch.setattr(service.shutil, "which", lambda _command: "/usr/bin/apptainer")
    monkeypatch.setattr(
        service,
        "_ngs_runtime_identity",
        lambda: (hashlib.sha256(image.read_bytes()).hexdigest(), "1.24"),
    )
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="samtools 1.23.1\nUsing htslib 1.23.1\n"),
    )
    service._clear_samtools_runtime_cache()
    try:
        with pytest.raises(service.AlignmentSessionError, match="version mismatch"):
            service._samtools_command()
    finally:
        service._clear_samtools_runtime_cache()


@pytest.mark.parametrize("revoked", [False, True])
def test_completed_nanopore_alignment_access_can_rotate_active_or_fresh_revoked_authority(
    monkeypatch: pytest.MonkeyPatch,
    revoked: bool,
) -> None:
    from routers import ngs_alignment_sessions as router
    from services import alignment_access
    from services import ont_ngs_hierarchy

    old_token = "old-completed-job-capability"
    job = SimpleNamespace(
        id="job-rotate",
        status="completed",
        model_id="nanopore",
        output_dir="/tmp/job-rotate",
        child_output_dir=None,
        params={
            "reference_sequence_sha256": "a" * 64,
            "ont_workflow_id": "ont_fastq_qc",
            "ont_input_mode": "fastq",
        },
        provenance=(
            {
                "alignment_access_revoked": True,
                alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
            }
            if revoked else {
                alignment_access.PROVENANCE_DIGEST_KEY: alignment_access.token_sha256(old_token),
                alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
            }
        ),
    )

    class SelectResult:
        def scalar_one_or_none(self):
            return job

    class UpdateResult:
        rowcount = 1

    class FakeSession:
        def __init__(self) -> None:
            self.execute_count = 0
            self.commits = 0
            self.rollbacks = 0
            self.updated_provenance = None

        async def execute(self, statement):
            self.execute_count += 1
            if self.execute_count == 1:
                return SelectResult()
            params = statement.compile().params
            self.updated_provenance = next(
                value
                for value in params.values()
                if isinstance(value, dict)
                and alignment_access.PROVENANCE_DIGEST_KEY in value
            )
            return UpdateResult()

        async def commit(self) -> None:
            self.commits += 1

        async def rollback(self) -> None:
            self.rollbacks += 1

    session = FakeSession()
    hierarchy_document = {"job": {"id": job.id}}
    hierarchy = ont_ngs_hierarchy.OntNgsHierarchyAuthority(
        project_id="project-a",
        digest=hashlib.sha256(
            json.dumps(hierarchy_document, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest(),
        document=hierarchy_document,
    )

    async def resolve_hierarchy(_job, _domain_session, _experiment_session):
        return hierarchy

    async def allow_project(_request, _session, _project_id, _job_id):
        return "operator-a"

    monkeypatch.setenv("BMS_RUNTIME_MODE", "dev")
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "http://127.0.0.1:18082/")
    monkeypatch.setattr(router, "LOCAL_DEVELOPMENT_ADMIN_HOSTS", frozenset({"127.0.0.1"}))
    monkeypatch.setattr(router, "resolve_ont_ngs_hierarchy_authority", resolve_hierarchy, raising=False)
    monkeypatch.setattr(router, "_require_governed_project_principal", allow_project, raising=False)
    async def valid_package(_job):
        return {"schema": "bms.ngs.fastq-qc-result.v1"}

    monkeypatch.setattr(router, "build_ont_fastq_qc_result", valid_package)
    app = _ngs_app()
    app.include_router(router.router, prefix="/api")
    app.dependency_overrides[router.get_session] = lambda: session
    app.dependency_overrides[router.get_molbio_ngs_session] = lambda: object()
    app.dependency_overrides[router.get_experiment_session] = lambda: object()
    headers = {
        "Origin": "http://127.0.0.1:18082",
        "Sec-Fetch-Site": "same-origin",
    }

    with TestClient(app, client=("127.0.0.1", 40000)) as client:
        rejected = client.post("/api/jobs/job-rotate/alignment-access/rotate")
        response = client.post(
            "/api/jobs/job-rotate/alignment-access/rotate",
            headers=headers,
        )
        rotated_token = client.cookies.get(alignment_access.cookie_name(job.id))

    assert rejected.status_code == 403
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.pop("expires_at").endswith("Z")
    assert payload == {
        "schema": "bms.ngs.rotation-success.v1",
        "job_id": job.id,
        "rotated": True,
        "scheme": alignment_access.SCHEME,
        "rotation_count": 1,
    }
    assert rotated_token and rotated_token not in response.text
    assert session.updated_provenance is not None
    assert alignment_access.capability_matches(
        rotated_token,
        session.updated_provenance[alignment_access.PROVENANCE_DIGEST_KEY],
    )
    assert "alignment_access_revoked" not in session.updated_provenance
    if not revoked:
        assert not alignment_access.capability_matches(
            old_token,
            session.updated_provenance[alignment_access.PROVENANCE_DIGEST_KEY],
        )
    assert session.updated_provenance[ont_ngs_hierarchy.PROVENANCE_HIERARCHY_KEY] == (
        ont_ngs_hierarchy.hierarchy_authority_record(hierarchy)
    )
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie
    assert "max-age=1800" in cookie
    assert session.commits == 1
    assert session.rollbacks == 0


def test_revocation_without_browser_cookie_revokes_persisted_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as router
    from services import alignment_access, ont_ngs_hierarchy

    job = SimpleNamespace(
        id="job-revoke", status="completed", model_id="nanopore", output_dir="/tmp/job-revoke",
        child_output_dir=None,
        params={"reference_sequence_sha256": "a" * 64, "ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
        provenance={
            alignment_access.PROVENANCE_DIGEST_KEY: alignment_access.token_sha256("active-token"),
            alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
        },
    )

    class SelectResult:
        def scalar_one_or_none(self):
            return job

    class UpdateResult:
        rowcount = 1

    class FakeSession:
        def __init__(self):
            self.calls = 0
            self.updated_provenance = None
            self.commits = 0
            self.rollbacks = 0

        async def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return SelectResult()
            self.updated_provenance = next(
                value for value in statement.compile().params.values()
                if isinstance(value, dict) and value.get("alignment_access_revoked") is True
            )
            return UpdateResult()

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    session = FakeSession()
    hierarchy = ont_ngs_hierarchy.OntNgsHierarchyAuthority(
        project_id="project-a", digest="b" * 64, document={"job": {"id": job.id}},
    )

    async def resolve_hierarchy(*_args):
        return hierarchy

    async def allow_project(*_args):
        return "operator-a"

    async def valid_package(_job):
        return {"schema": "bms.ngs.fastq-qc-result.v1"}

    monkeypatch.setenv("BMS_RUNTIME_MODE", "dev")
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "http://127.0.0.1:18082/")
    monkeypatch.setattr(router, "LOCAL_DEVELOPMENT_ADMIN_HOSTS", frozenset({"127.0.0.1"}))
    monkeypatch.setattr(router, "resolve_ont_ngs_hierarchy_authority", resolve_hierarchy)
    monkeypatch.setattr(router, "_require_governed_project_principal", allow_project)
    monkeypatch.setattr(router, "build_ont_fastq_qc_result", valid_package)
    app = _ngs_app()
    app.include_router(router.router, prefix="/api")
    app.dependency_overrides[router.get_session] = lambda: session
    app.dependency_overrides[router.get_molbio_ngs_session] = lambda: object()
    app.dependency_overrides[router.get_experiment_session] = lambda: object()

    response = TestClient(app, client=("127.0.0.1", 40000)).delete(
        "/api/jobs/job-revoke/alignment-access",
        headers={"Origin": "http://127.0.0.1:18082", "Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "schema": "bms.ngs.capability-revocation-success.v1",
        "job_id": "job-revoke", "revoked": True, "scheme": alignment_access.SCHEME,
    }
    assert session.updated_provenance is not None
    assert alignment_access.PROVENANCE_DIGEST_KEY not in session.updated_provenance
    assert session.updated_provenance["alignment_access_revoked"] is True
    assert session.commits == 1
    assert "max-age=0" in response.headers["set-cookie"].lower()


@pytest.mark.parametrize(
    "fresh_provenance",
    [
        {"alignment_access_scheme": "opaque_job_capability_v1"},
        {"alignment_access_revoked": True, "alignment_access_scheme": "wrong-scheme"},
    ],
)
def test_revocation_lost_cas_requires_durable_closed_revoked_authority(
    monkeypatch: pytest.MonkeyPatch,
    fresh_provenance: dict[str, object],
) -> None:
    from routers import ngs_alignment_sessions as router
    from services import alignment_access, ont_ngs_hierarchy

    token = "active-token"
    job = SimpleNamespace(
        id="00000000-0000-4000-8000-000000000002", status="completed", model_id="nanopore",
        output_dir="/tmp/revoke-cas", child_output_dir=None,
        params={"reference_sequence_sha256": "a" * 64, "ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
        provenance={
            alignment_access.PROVENANCE_DIGEST_KEY: alignment_access.token_sha256(token),
            alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
        },
    )

    class Result:
        def __init__(self, value=None, rowcount=None):
            self.value = value
            self.rowcount = rowcount
        def scalar_one_or_none(self):
            return self.value

    class LostCasSession:
        def __init__(self):
            self.calls = 0
            self.commits = 0
            self.rollbacks = 0
        async def execute(self, _statement):
            self.calls += 1
            if self.calls == 1:
                return Result(job)
            if self.calls == 2:
                return Result(rowcount=0)
            return Result(SimpleNamespace(provenance=fresh_provenance))
        async def commit(self):
            self.commits += 1
        async def rollback(self):
            self.rollbacks += 1

    session = LostCasSession()
    hierarchy = ont_ngs_hierarchy.OntNgsHierarchyAuthority(
        project_id="project-a", digest="b" * 64, document={"job": {"id": job.id}},
    )
    async def resolve_hierarchy(*_args):
        return hierarchy
    async def allow_project(*_args):
        return "operator-a"
    async def valid_package(_job):
        return {"schema": "bms.ngs.fastq-qc-result.v1"}

    monkeypatch.setenv("BMS_RUNTIME_MODE", "dev")
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "http://127.0.0.1:18082/")
    monkeypatch.setattr(router, "LOCAL_DEVELOPMENT_ADMIN_HOSTS", frozenset({"127.0.0.1"}))
    monkeypatch.setattr(router, "resolve_ont_ngs_hierarchy_authority", resolve_hierarchy)
    monkeypatch.setattr(router, "_require_governed_project_principal", allow_project)
    monkeypatch.setattr(router, "build_ont_fastq_qc_result", valid_package)
    app = _ngs_app()
    app.include_router(router.router, prefix="/api")
    app.dependency_overrides[router.get_session] = lambda: session
    app.dependency_overrides[router.get_molbio_ngs_session] = lambda: object()
    app.dependency_overrides[router.get_experiment_session] = lambda: object()
    response = TestClient(app, client=("127.0.0.1", 40000)).delete(
        f"/api/jobs/{job.id}/alignment-access",
        headers={"Origin": "http://127.0.0.1:18082", "Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "NGS_CAPABILITY_ROTATION_CONFLICT"
    assert session.rollbacks == 1
    assert session.commits == 0


@pytest.mark.parametrize(
    ("runtime_mode", "headers"),
    [
        ("container", {"Origin": "http://127.0.0.1:18082", "Sec-Fetch-Site": "same-origin"}),
        ("dev", {"Origin": "http://evil.invalid", "Sec-Fetch-Site": "same-origin"}),
        ("dev", {"Origin": "http://127.0.0.1:18082", "Sec-Fetch-Site": "cross-site"}),
    ],
)
def test_alignment_access_rotation_denials_do_not_reach_persistence(
    monkeypatch: pytest.MonkeyPatch,
    runtime_mode: str,
    headers: dict[str, str],
) -> None:
    from routers import ngs_alignment_sessions as router

    class NoPersistenceSession:
        async def execute(self, _statement):
            raise AssertionError("denied rotation reached persistence")

    monkeypatch.setenv("BMS_RUNTIME_MODE", runtime_mode)
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "http://127.0.0.1:18082/")
    monkeypatch.setattr(router, "LOCAL_DEVELOPMENT_ADMIN_HOSTS", frozenset({"127.0.0.1"}))
    app = _ngs_app()
    app.include_router(router.router, prefix="/api")
    app.dependency_overrides[router.get_session] = lambda: NoPersistenceSession()

    response = TestClient(app, client=("127.0.0.1", 40000)).post(
        "/api/jobs/job-rotate/alignment-access/rotate",
        headers=headers,
    )

    assert response.status_code == 403


def test_alignment_access_rotation_conflict_rolls_back_without_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as router
    from services import alignment_access
    from services import ont_ngs_hierarchy

    job = SimpleNamespace(
        id="job-conflict",
        status="completed",
        model_id="nanopore",
        output_dir="/tmp/job-conflict",
        child_output_dir=None,
        params={
            "reference_sequence_sha256": "a" * 64,
            "ont_workflow_id": "ont_fastq_qc",
            "ont_input_mode": "fastq",
        },
        provenance={
            alignment_access.PROVENANCE_DIGEST_KEY: "b" * 64,
            alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
        },
    )

    class Result:
        def __init__(self, rowcount=None):
            self.rowcount = rowcount

        def scalar_one_or_none(self):
            return job

    class ConflictSession:
        def __init__(self):
            self.calls = 0
            self.commits = 0
            self.rollbacks = 0

        async def execute(self, _statement):
            self.calls += 1
            return Result() if self.calls == 1 else Result(rowcount=0)

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    session = ConflictSession()
    hierarchy_document = {"job": {"id": job.id}}
    hierarchy = ont_ngs_hierarchy.OntNgsHierarchyAuthority(
        project_id="project-a",
        digest=hashlib.sha256(
            json.dumps(hierarchy_document, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest(),
        document=hierarchy_document,
    )
    job.provenance[ont_ngs_hierarchy.PROVENANCE_HIERARCHY_KEY] = (
        ont_ngs_hierarchy.hierarchy_authority_record(hierarchy)
    )

    async def resolve_hierarchy(_job, _domain_session, _experiment_session):
        return hierarchy

    async def allow_project(_request, _session, _project_id, _job_id):
        return "operator-a"

    monkeypatch.setenv("BMS_RUNTIME_MODE", "dev")
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "http://127.0.0.1:18082/")
    monkeypatch.setattr(router, "LOCAL_DEVELOPMENT_ADMIN_HOSTS", frozenset({"127.0.0.1"}))
    monkeypatch.setattr(router, "resolve_ont_ngs_hierarchy_authority", resolve_hierarchy, raising=False)
    monkeypatch.setattr(router, "_require_governed_project_principal", allow_project, raising=False)
    async def valid_package(_job):
        return {"schema": "bms.ngs.fastq-qc-result.v1"}

    monkeypatch.setattr(router, "build_ont_fastq_qc_result", valid_package)
    app = _ngs_app()
    app.include_router(router.router, prefix="/api")
    app.dependency_overrides[router.get_session] = lambda: session
    app.dependency_overrides[router.get_molbio_ngs_session] = lambda: object()
    app.dependency_overrides[router.get_experiment_session] = lambda: object()

    response = TestClient(app, client=("127.0.0.1", 40000)).post(
        "/api/jobs/job-conflict/alignment-access/rotate",
        headers={"Origin": "http://127.0.0.1:18082", "Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 409
    assert "set-cookie" not in response.headers
    assert session.commits == 0
    assert session.rollbacks == 1


def test_secure_same_origin_rotation_is_not_loopback_or_development_restricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as router

    monkeypatch.setenv("BMS_RUNTIME_MODE", "container")
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "https://ngs.example/")
    request = Request({
        "type": "http", "method": "POST", "scheme": "https", "path": "/",
        "client": ("203.0.113.10", 40000),
        "headers": [
            (b"origin", b"https://ngs.example"),
            (b"sec-fetch-site", b"same-origin"),
        ],
    })
    router._require_local_development_browser(request, "00000000-0000-4000-8000-000000000003")


def test_https_config_does_not_upgrade_an_actual_remote_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as router
    from services import alignment_access

    monkeypatch.setenv("BMS_RUNTIME_MODE", "container")
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "https://ngs.example/")
    request = Request({
        "type": "http", "method": "POST", "scheme": "http", "path": "/",
        "client": ("203.0.113.10", 40000),
        "headers": [
            (b"origin", b"https://ngs.example"),
            (b"sec-fetch-site", b"same-origin"),
        ],
    })

    assert alignment_access.secure_alignment_transport(request) is False
    with pytest.raises(router.OntNgsRouteError) as denied:
        router._require_local_development_browser(request, "00000000-0000-4000-8000-000000000003")
    assert denied.value.status_code == 403


def test_insecure_local_development_allowlist_contains_only_actual_loopback_addresses() -> None:
    from routers import ngs_alignment_sessions as router

    assert router.LOCAL_DEVELOPMENT_ADMIN_HOSTS == frozenset({"127.0.0.1", "::1"})


def test_reconciled_fastq_qc_session_authority_uses_the_validated_historical_receipt() -> None:
    from routers import ngs_alignment_sessions as router

    digest = "a" * 64
    job = SimpleNamespace(
        id="31f02bd5-830f-4558-aa78-3873c515de68",
        params={
            "reference_sequence_sha256": "b" * 64,
            "ont_workflow_id": "ont_fastq_qc",
            "ont_input_mode": "fastq",
        },
        provenance={
            "result_integrity": {"result_kind": "legacy_design"},
            "ont_fastq_qc_reconciliation_v1": {
                "schema": "bms.ont-fastq-qc-reconciliation.v1",
                "job_id": "31f02bd5-830f-4558-aa78-3873c515de68",
                "workflow_id": "ont_fastq_qc",
                "input_mode": "fastq",
                "artifact_set_sha256": digest,
            },
        },
    )

    assert router._job_session_authority(cast(Any, job))["package_artifact_set_sha256"] == digest


@pytest.mark.asyncio
async def test_pinned_result_root_validates_external_signal_alignment_package_without_fastq_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database import Job
    from services.ont_ngs_results import OntNgsResultError
    from routers import ngs_alignment_sessions as router

    result_root = tmp_path / "external-alignment"
    result_root.mkdir()
    authority = {
        "artifact_set_sha256": "a" * 64,
        "declared_artifact_count": 5,
        "present_artifact_count": 5,
        "unavailable_artifact_count": 0,
    }
    job = Job(
        id="external-alignment-job",
        name="external alignment",
        model_id="nanopore",
        mode="plasmid_qc",
        status="completed",
        queue_status="completed",
        output_dir=str(result_root),
        params={
            "ont_workflow_id": "ont_plasmid_qc",
            "ont_input_mode": "bam",
            "reference_sequence_sha256": "b" * 64,
            "bam_path": str(tmp_path / "source.bam"),
            "run_fastq_qc": False,
            "source_move_source_id": "move-source",
            "source_external_move_registration_receipt_id": "registration-receipt",
        },
        provenance={"result_integrity": {"result_kind": "ngs_alignment_session", **authority}},
        awaiting_input=False,
        paused=False,
    )
    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(router, "resolve_persisted_job_result_root", lambda _job: result_root)
    monkeypatch.setattr(router, "is_ont_signal_alignment_job", lambda _job: True, raising=False)
    monkeypatch.setattr(
        router,
        "_build_file_projection_from_pinned_root",
        lambda *_args: (_ for _ in ()).throw(OntNgsResultError("FASTQ projection must not run")),
    )
    monkeypatch.setattr(
        router.service,
        "build_ngs_package_artifacts",
        lambda *_args, **kwargs: calls.append(kwargs) or [{"artifact": "package"}],
    )
    monkeypatch.setattr(router, "canonical_ngs_package_authority", lambda _artifacts: authority, raising=False)

    async with router._validated_pinned_result_root(job) as pinned_root:
        assert pinned_root.name.isdigit()

    assert calls == [
        {
            "source_reference_sha256": "b" * 64,
            "workflow_id": "ont_plasmid_qc",
            "input_mode": "bam",
            "source_input_path": str(tmp_path / "source.bam"),
            "job_output_dir": Path(calls[0]["job_output_dir"]),
            "pinned_root_descriptor": True,
        }
    ]


def test_sequence_qc_manifest_wrapper_accepts_the_pinned_result_root_descriptor(tmp_path: Path) -> None:
    from routers import ngs_alignment_sessions as router

    manifest = tmp_path / "fastq_qc" / "qc_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert router.find_canonical_fastq_manifest(Path(f"/proc/self/fd/{descriptor}")) == (
            Path(f"/proc/self/fd/{descriptor}") / "fastq_qc" / "qc_manifest.json"
        )
    finally:
        os.close(descriptor)


def test_sequence_qc_manifest_is_available_below_job_scoped_cookie_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as router

    job = SimpleNamespace(
        id="job-manifest",
        model_id="nanopore",
        params={
            "reference_sequence_sha256": "a" * 64,
            "ont_workflow_id": "ont_fastq_qc",
            "ont_input_mode": "fastq",
        },
        output_dir="/tmp/job-manifest",
        child_output_dir=None,
    )
    monkeypatch.setattr(router, "resolve_persisted_job_result_root", lambda _job: Path("/tmp/job-manifest"))
    monkeypatch.setattr(router, "find_canonical_fastq_manifest", lambda _root: Path("/tmp/job-manifest/qc_manifest.json"))
    manifest_bytes = b'{"schema":"sequence_qc.manifest.v1"}'
    monkeypatch.setattr(
        router.service,
        "_read_bounded_json_nofollow",
        lambda *_args, **_kwargs: ({"schema": "sequence_qc.manifest.v1"}, manifest_bytes, "a" * 64, len(manifest_bytes)),
    )
    monkeypatch.setattr(
        router,
        "load_sequence_qc_manifest",
        lambda *_args, **kwargs: {
            "schema": "sequence_qc.manifest.v1",
            "authority": kwargs,
            "raw_bytes": kwargs.get("raw_bytes"),
        },
    )
    app = _ngs_app()
    app.include_router(router.router, prefix="/api")
    app.dependency_overrides[router.require_alignment_job] = lambda: job

    response = TestClient(app, client=("127.0.0.1", 40000)).get("/api/jobs/job-manifest/sequence-qc-manifest")

    assert response.status_code == 200
    assert response.json()["raw_bytes"] == manifest_bytes.decode("utf-8")
    assert response.json()["authority"] == {
        "raw_bytes": manifest_bytes.decode("utf-8"),
        "expected_job_id": "job-manifest",
        "expected_workflow_id": "ont_fastq_qc",
        "expected_input_mode": "fastq",
        "expected_analysis_status": "completed",
    }


def test_generic_file_routes_hide_governed_ngs_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result_root = tmp_path / "result"
    fastq_qc = result_root / "fastq_qc"
    fastq_qc.mkdir(parents=True)
    report = fastq_qc / "igv_report.html"
    report.write_text("<html></html>", encoding="utf-8")
    structure = fastq_qc / "governed.pdb"
    structure.write_text("ATOM\n", encoding="utf-8")
    verification = result_root / "verification"
    verification.mkdir()
    verification_report = verification / "construct_verification_report.html"
    verification_report.write_text("<html>verification</html>", encoding="utf-8")
    (verification / "qc_manifest.json").write_text(
        json.dumps({"schema": "biomodstack.construct_verification.v2", "artifacts": []}),
        encoding="utf-8",
    )
    (fastq_qc / "qc_manifest.json").write_text(
        json.dumps({"schema": "sequence_qc.manifest.v1", "artifacts": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(files_router, "get_allowed_roots", lambda: {"bms_results": tmp_path})
    monkeypatch.setattr(files_router, "resolve_allowed_path", lambda value: tmp_path / Path(value).relative_to("bms_results"))

    app = _ngs_app()
    app.include_router(files_router.router, prefix="/api/files")
    app.dependency_overrides[files_router.get_governed_ngs_result_roots] = lambda: (result_root,)
    client = TestClient(app, client=("127.0.0.1", 40000))
    parent = client.get("/api/files/browse", params={"path": "bms_results"})
    assert parent.status_code == 200
    assert parent.json()["entries"] == []
    assert client.get("/api/files/browse", params={"path": "bms_results/result/fastq_qc"}).status_code == 403
    governed_routes = (
        "/api/files/download/bms_results/result/fastq_qc/igv_report.html",
        "/api/files/stream/bms_results/result/fastq_qc/igv_report.html",
        "/api/files/download/bms_results/result/verification/construct_verification_report.html",
        "/api/files/stream/bms_results/result/verification/construct_verification_report.html",
        "/api/files/pdb/bms_results/result/fastq_qc/governed.pdb",
    )
    for route in governed_routes:
        assert client.get(route).status_code == 403
    assert client.post(
        "/api/files/extract-chain",
        data={"input_path": "bms_results/result/fastq_qc/governed.pdb", "chain_id": "A"},
    ).status_code == 403
    assert not (fastq_qc / "governed_chainA.pdb").exists()

    (fastq_qc / "qc_manifest.json").unlink()
    (verification / "qc_manifest.json").unlink()
    for route in governed_routes:
        assert client.get(route).status_code == 403
    assert client.post(
        "/api/files/upload",
        data={"path": "bms_results/result"},
        files={"file": ("replacement.html", b"tampered", "text/html")},
    ).status_code == 403
    assert not (result_root / "replacement.html").exists()


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
        "summary": {"reference_topology": "circular"},
        "artifacts": [
            {"kind": "reference", "path": "reference.fasta", "required": True, "state": "present"},
            {"kind": "reference_index", "path": "reference.fasta.fai", "required": False, "state": "present"},
            {"kind": "alignment_bam", "path": f"{prefix}.bam", "required": False, "state": "present"},
            {"kind": "alignment_bai", "path": f"{prefix}.bam.bai", "required": False, "state": "present"},
        ],
    }
    if "dimer" in directory.name:
        payload["alignment_session"]["source_reference_sequence_sha256"] = hashlib.sha256(
            b"ACGTACGT"
        ).hexdigest()
    for artifact in payload["artifacts"]:
        path = directory / artifact["path"]
        artifact["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        artifact["size_bytes"] = path.stat().st_size
    (directory / "qc_manifest.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_session_verification_authority(result_root: Path) -> None:
    verification = result_root / "verification"
    verification.mkdir(parents=True, exist_ok=True)
    (verification / "qc_manifest.json").write_text(
        json.dumps({
            "artifact_schema_version": 2,
            "schema": "biomodstack.construct_verification.v2",
            "summary": {"reference_topology": "circular"},
            "artifacts": [],
        }),
        encoding="utf-8",
    )


def _ready_session_wire(job_id: str = "job-a") -> dict[str, Any]:
    digest = "a" * 64
    def artifact(role: str) -> dict[str, Any]:
        return {
            "artifact_id": hashlib.sha256(role.encode()).hexdigest(),
            "url": f"/api/jobs/{job_id}/alignment-artifacts/{hashlib.sha256(role.encode()).hexdigest()}",
            "sha256": digest,
            "size_bytes": 3,
            "mime_type": "application/octet-stream",
            "range_capable": True,
            "source_manifest_sha256": "b" * 64,
        }
    return {
        "schema": "bms.ngs.alignment-session.v1",
        "session_id": "1" * 24,
        "job_id": job_id,
        "mode": "primary",
        "ready": True,
        "unavailable_reason": None,
        "reads_url": f"/api/jobs/{job_id}/reads?session_id={'1' * 24}",
        "sequence_qc_manifest_sha256": "b" * 64,
        "verification_manifest_sha256": "c" * 64,
        "artifact_set_sha256": "d" * 64,
        "reference": {
            "contig": "ref", "length_bp": 8, "topology": "circular",
            "normalized_sequence_sha256": digest, "fasta_sha256": digest, "fai_sha256": digest,
        },
        "artifacts": {role: artifact(role) for role in ("alignment", "alignment_index", "reference", "reference_index")},
        "alignment_pair_sha256": "e" * 64,
    }


def test_alignment_session_wire_shape_validates_against_normative_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jsonschema import Draft202012Validator, FormatChecker
    from services import ngs_alignment_sessions as service

    job_id = "00000000-0000-4000-8000-000000000001"
    _write_manifest(tmp_path / job_id / "fastq_qc", job_id=job_id)
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))
    sessions = service.build_alignment_sessions(
        job_id,
        source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(),
        results_dir=tmp_path,
    )
    schema = json.loads((API_ROOT.parents[1] / "schemas/ngs/ont_alignment_session_v1.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate({
        "schema": "bms.ngs.alignment-session-list.v1",
        "job_id": job_id,
        "sessions": sessions,
    })


def test_package_builder_accepts_only_explicitly_pinned_result_root_descriptor(tmp_path: Path) -> None:
    from services import ngs_alignment_sessions as service

    job_root = tmp_path / "job-a"
    _write_manifest(job_root / "fastq_qc")
    source_input = tmp_path / "input.fastq.gz"
    source_input.write_bytes(b"fastq")
    descriptor = os.open(job_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        artifacts = service.build_ngs_package_artifacts(
            "job-a",
            source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(),
            workflow_id="ont_fastq_qc",
            input_mode="fastq",
            source_input_path=source_input,
            results_dir=tmp_path,
            job_output_dir=Path(f"/proc/self/fd/{descriptor}"),
            pinned_root_descriptor=True,
        )
    finally:
        os.close(descriptor)

    assert any(item["kind"] == "alignment_bam" and item["state"] == "present" for item in artifacts)


def test_package_builder_reads_pinned_descriptor_across_aba_root_replacement(tmp_path: Path) -> None:
    from services import ngs_alignment_sessions as service

    source_input = tmp_path / "input.fastq.gz"
    source_input.write_bytes(b"fastq")
    original = tmp_path / "job-a"
    _write_manifest(original / "fastq_qc")
    descriptor = os.open(original, os.O_RDONLY | os.O_DIRECTORY)
    held = tmp_path / "job-a-held"
    original.rename(held)
    replacement = tmp_path / "job-a"
    _write_manifest(replacement / "fastq_qc", prefix="replacement")
    try:
        artifacts = service.build_ngs_package_artifacts(
            "job-a",
            source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(),
            workflow_id="ont_fastq_qc",
            input_mode="fastq",
            source_input_path=source_input,
            results_dir=tmp_path,
            job_output_dir=Path(f"/proc/self/fd/{descriptor}"),
            pinned_root_descriptor=True,
        )
    finally:
        os.close(descriptor)

    alignment = next(item for item in artifacts if item["kind"] == "alignment_bam")
    assert alignment["sha256"] == hashlib.sha256(b"bam").hexdigest()
    assert alignment["relative_path"].endswith("aligned.bam")


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
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    sessions = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")
    assert primary["ready"] is False
    assert "manifest job_id does not match requested job" in primary["unavailable_reason"]


def test_manifest_workflow_and_input_mode_must_match_authorized_job_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    manifest_dir = tmp_path / "job-a" / "fastq_qc"
    _write_manifest(manifest_dir)
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    sessions = service.build_alignment_sessions(
        "job-a",
        source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(),
        workflow_id="ont_plasmid_qc",
        input_mode="pod5",
        results_dir=tmp_path,
    )
    primary = next(item for item in sessions if item["mode"] == "primary")
    assert primary["ready"] is False
    assert "workflow_id does not match authorized job provenance" in primary["unavailable_reason"]


@pytest.mark.parametrize("input_mode", ["fastq", "bam", "pod5"])
def test_canonical_input_modes_can_become_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_mode: str,
) -> None:
    from services import ngs_alignment_sessions as service

    manifest_dir = tmp_path / "job-a" / "fastq_qc"
    _write_manifest(manifest_dir)
    manifest_path = manifest_dir / "qc_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["input_mode"] = input_mode
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    sessions = service.build_alignment_sessions(
        "job-a",
        source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(),
        workflow_id="ont_fastq_qc",
        input_mode=input_mode,
        results_dir=tmp_path,
    )
    primary = next(item for item in sessions if item["mode"] == "primary")
    assert primary["ready"] is True


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
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    sessions = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)
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
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    sessions = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)
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
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    ready_sessions = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)
    artifact_id = next(item for item in ready_sessions if item["mode"] == "primary")["artifacts"]["alignment"]["artifact_id"]
    alignment = next(item for item in payload["artifacts"] if item["kind"] == "alignment_bam")
    alignment["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    sessions = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")
    assert primary["ready"] is False
    assert primary["artifacts"] == {}

    with pytest.raises(service.AlignmentSessionError, match="not found"):
        service.resolve_alignment_artifact("job-a", artifact_id, source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)


def test_primary_session_is_opaque_job_scoped_and_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services import ngs_alignment_sessions as service

    _write_manifest(tmp_path / "job-a" / "fastq_qc")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    sessions = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")
    assert primary["ready"] is True
    assert primary["reference"]["contig"] == "ref"
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
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    sessions = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)
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
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    sessions = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")

    assert primary["ready"] is False
    assert "alignment" not in primary["artifacts"]
    assert "contradictory primary session mode" in primary["unavailable_reason"]


def test_bam_primary_session_accepts_persisted_sequence_manifest_package_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    output_dir = tmp_path / "job-bam"
    _write_manifest(output_dir, job_id="job-bam")
    manifest_path = output_dir / "qc_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["workflow_id"] = "ont_plasmid_qc"
    payload["input_mode"] = "bam"
    payload["alignment_session"]["source_reference_sequence_sha256"] = hashlib.sha256(
        b"ACGTACGT"
    ).hexdigest()
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    sessions = service.build_alignment_sessions(
        "job-bam",
        source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(),
        workflow_id="ont_plasmid_qc",
        input_mode="bam",
        package_artifact_set_sha256="d" * 64,
        results_dir=tmp_path,
        job_output_dir=output_dir,
    )

    primary = next(item for item in sessions if item["mode"] == "primary")
    assert primary["ready"] is True
    assert primary["sequence_qc_manifest_sha256"]
    assert primary["verification_manifest_sha256"] == primary["sequence_qc_manifest_sha256"]
    assert primary["artifact_set_sha256"] == "d" * 64


def test_persisted_production_output_directory_resolves_sessions_and_stays_confined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import ngs_alignment_sessions as service

    results = tmp_path / "results"
    output_dir = results / "submitted-name_20260719_040000"
    _write_manifest(output_dir / "fastq_qc", job_id="opaque-job-uuid")
    _write_session_verification_authority(output_dir)
    monkeypatch.setattr(service, "get_results_dir", lambda: results)
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    sessions = service.build_alignment_sessions(
        "opaque-job-uuid",
        source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(),
        results_dir=results,
        job_output_dir=output_dir,
    )
    assert next(item for item in sessions if item["mode"] == "primary")["ready"] is True

    app = _ngs_app()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(
        child_output_dir=None,
        params={"reference_sequence_sha256": hashlib.sha256(b"ACGTACGT").hexdigest(), "ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
        output_dir=str(output_dir),
        provenance={"result_integrity": {"artifact_set_sha256": "d" * 64}},
    )
    response = TestClient(app, client=("127.0.0.1", 40000)).get("/api/jobs/opaque-job-uuid/alignment-sessions")
    assert response.status_code == 200
    assert next(item for item in response.json()["sessions"] if item["mode"] == "primary")["ready"] is True

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(service.AlignmentSessionError, match="unsafe job root"):
        service.build_alignment_sessions(
            "opaque-job-uuid",
            source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(),
            results_dir=results,
            job_output_dir=outside,
        )


def test_alignment_capability_enforces_two_principal_cross_job_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import alignment_access
    from services import ont_ngs_hierarchy

    token_a, digest_a = alignment_access.issue_alignment_access_token()
    hierarchy_document = {"job": {"id": "job-a"}}
    authority_a = ont_ngs_hierarchy.OntNgsHierarchyAuthority(
        project_id="project-a",
        digest=hashlib.sha256(
            json.dumps(hierarchy_document, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest(),
        document=hierarchy_document,
    )
    hierarchy_record = ont_ngs_hierarchy.hierarchy_authority_record(authority_a)

    class Result:
        def __init__(self, job_id: str):
            self.job = SimpleNamespace(
                id=job_id,
                model_id="nanopore",
                params={"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
                output_dir=f"/tmp/results/{job_id}-run",
                provenance={
                    alignment_access.PROVENANCE_DIGEST_KEY: digest_a,
                    ont_ngs_hierarchy.PROVENANCE_HIERARCHY_KEY: json.loads(json.dumps(hierarchy_record)),
                },
            )

        def scalar_one_or_none(self):
            return self.job

    class Session:
        def __init__(self, job_id: str):
            self.job_id = job_id

        async def execute(self, _query):
            return Result(self.job_id)

    async def resolve_hierarchy(_job, _domain_session, _experiment_session):
        return authority_a

    async def allow_project(_request, _session, _project_id, _job_id):
        return "operator-a"

    monkeypatch.setattr(routes, "resolve_ont_ngs_hierarchy_authority", resolve_hierarchy, raising=False)
    monkeypatch.setattr(routes, "_require_governed_project_principal", allow_project, raising=False)
    monkeypatch.setattr(routes, "build_ont_fastq_qc_result", lambda _job: asyncio.sleep(0, result={"job_id": "job-a"}))
    request_a = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/jobs/job-a/alignment-sessions",
            "headers": [(b"cookie", f"{alignment_access.cookie_name('job-a', secure=True)}={token_a}".encode())],
        }
    )
    domain_session = object()
    experiment_session = object()
    authorized = asyncio.run(
        routes.require_alignment_job(
            "job-a",
            request_a,
            Session("job-a"),
            domain_session,
            experiment_session,
        )
    )
    assert authorized.output_dir == "/tmp/results/job-a-run"

    with pytest.raises(routes.OntNgsRouteError) as exc_info:
        asyncio.run(
            routes.require_alignment_job(
                "job-b",
                request_a,
                Session("job-b"),
                domain_session,
                experiment_session,
            )
        )
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
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    primary = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)[0]

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
            "job-a", descriptor["artifact_id"], source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path
        ).name == expected_name


def test_primary_never_mixes_dimer_sidecars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services import ngs_alignment_sessions as service

    _write_manifest(tmp_path / "job-a" / "fastq_qc", prefix="aligned")
    _write_manifest(tmp_path / "job-a" / "dimer_qc", prefix="dimer_candidates")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    sessions = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)
    primary = next(item for item in sessions if item["mode"] == "primary")
    dimer = next(item for item in sessions if item["mode"] == "dimer_candidates")
    assert primary["session_id"] != dimer["session_id"]
    primary_id = primary["artifacts"]["alignment"]["artifact_id"]
    dimer_id = dimer["artifacts"]["alignment"]["artifact_id"]
    assert service.resolve_alignment_artifact("job-a", primary_id, source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path).name == "aligned.bam"
    assert service.resolve_alignment_artifact("job-a", dimer_id, source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path).name == "dimer_candidates.bam"


def test_artifact_id_cannot_cross_jobs_or_accept_path_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services import ngs_alignment_sessions as service

    _write_manifest(tmp_path / "job-a" / "fastq_qc")
    _write_manifest(tmp_path / "job-b" / "fastq_qc")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))
    artifact_id = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)[0]["artifacts"]["alignment"][
        "artifact_id"
    ]

    with pytest.raises(service.AlignmentSessionError, match="not found"):
        service.resolve_alignment_artifact("job-b", artifact_id, source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)
    with pytest.raises(service.AlignmentSessionError, match="unsafe"):
        service.build_alignment_sessions("../job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)


def test_symlink_or_special_file_never_becomes_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services import ngs_alignment_sessions as service

    target = tmp_path / "outside.bam"
    target.write_bytes(b"outside")
    manifest_dir = tmp_path / "job-a" / "fastq_qc"
    _write_manifest(manifest_dir)
    (manifest_dir / "aligned.bam").unlink()
    (manifest_dir / "aligned.bam").symlink_to(target)
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    primary = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)[0]
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
        service.build_alignment_sessions("job-symlink", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)


def test_semantic_role_resolver_requires_ready_exact_mode_role_and_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import ngs_alignment_sessions as service

    _write_manifest(tmp_path / "job-a" / "fastq_qc")
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))
    primary = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)[0]
    alignment = primary["artifacts"]["alignment"]

    path, metadata = service.resolve_alignment_artifact_by_role(
        "job-a",
        "primary",
        "alignment",
        alignment["sha256"],
        source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(),
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
                "job-a", mode, role, digest,
                source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(),
                results_dir=tmp_path
            )


def test_generic_alignment_routes_offload_blocking_service_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import ngs_alignment_sessions as service

    monkeypatch.setattr(
        service,
        "build_alignment_sessions",
        lambda *_args, **_kwargs: [_ready_session_wire()],
    )
    monkeypatch.setattr(
        service,
        "resolve_alignment_session",
        lambda *_args, **_kwargs: _ready_session_wire(),
    )
    monkeypatch.setattr(
        service,
        "_resolve_internal_artifact",
        lambda *_args, **_kwargs: (Path("/tmp/artifact"), {"artifact_id": "artifact-a"}),
    )
    monkeypatch.setattr(
        service,
        "resolve_session_alignment_bundle",
        lambda *_args, **_kwargs: (
            Path("/tmp/aligned.bam"), {"sha256": "a" * 64, "size_bytes": 3},
            Path("/tmp/aligned.bam.bai"), {"sha256": "b" * 64, "size_bytes": 3},
        ),
    )
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
    app = _ngs_app()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(
        child_output_dir=None,
        params={"reference_sequence_sha256": hashlib.sha256(b"ACGTACGT").hexdigest(), "ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
        output_dir="/tmp/job-a-run",
        provenance={"result_integrity": {"artifact_set_sha256": "d" * 64}},
    )
    client = TestClient(app, client=("127.0.0.1", 40000))

    assert client.get("/api/jobs/job-a/alignment-sessions").status_code == 200
    assert client.get("/api/jobs/job-a/alignment-sessions/session-a").status_code == 200
    assert client.get("/api/jobs/job-a/alignment-artifacts/artifact-a").status_code == 200
    assert client.get("/api/jobs/job-a/reads?session_id=session-a").status_code == 200
    assert client.get("/api/jobs/job-a/reads/read-a?session_id=session-a").status_code == 200

    assert service.build_alignment_sessions in threadpool_calls
    assert service.resolve_alignment_session in threadpool_calls
    assert service._resolve_internal_artifact in threadpool_calls
    assert service.resolve_session_alignment_bundle in threadpool_calls
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
    app = _ngs_app()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(
        child_output_dir=None,
        params={"reference_sequence_sha256": hashlib.sha256(b"ACGTACGT").hexdigest(), "ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
        output_dir="/tmp/job-a-run",
        provenance={"result_integrity": {"artifact_set_sha256": "d" * 64}},
    )
    client = TestClient(app, client=("127.0.0.1", 40000))

    response = client.get(
        f"/api/jobs/job-a/alignment-session-artifacts/primary/alignment/{digest}",
        headers={"Range": "bytes=3-6"},
    )

    assert response.status_code == 206
    assert response.content == b"defg"
    assert response.headers["content-range"] == "bytes 3-6/10"
    assert response.headers["etag"] == f'"sha256:{digest}"'
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
    app = _ngs_app()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(
        child_output_dir=None,
        params={"reference_sequence_sha256": hashlib.sha256(b"ACGTACGT").hexdigest(), "ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
        output_dir="/tmp/job-a-run",
    )

    response = TestClient(app, client=("127.0.0.1", 40000)).get(
        f"/api/jobs/job-a/alignment-session-artifacts/primary/alignment/{digest}",
        headers={"Range": "bytes=0-7"},
    )

    assert mutation_count == 1
    assert response.status_code == 409
    assert response.json()["code"] == "NGS_ARTIFACT_INTEGRITY_CONFLICT"


def test_package_authority_binds_persisted_source_input_path() -> None:
    from routers import ngs_alignment_sessions as routes

    job = SimpleNamespace(
        params={
            "reference_sequence_sha256": hashlib.sha256(b"ACGT").hexdigest(),
            "ont_workflow_id": "ont_fastq_qc",
            "ont_input_mode": "fastq",
            "fastq_path": "/inputs/reads.fastq.gz",
        }
    )

    authority = routes._job_package_authority(cast(routes.Job, job))

    assert authority["source_input_path"] == "/inputs/reads.fastq.gz"
    del job.params["fastq_path"]
    with pytest.raises(routes.service.AlignmentSessionError, match="source input path"):
        routes._job_package_authority(cast(routes.Job, job))


def test_verification_input_identity_rejects_relabelled_digest() -> None:
    from services import ngs_alignment_sessions as service

    manifest = {
        "inputs": {
            "source_reads": {
                "sha256": hashlib.sha256(b"replacement").hexdigest(),
                "size_bytes": 11,
            }
        }
    }

    assert service._verification_input_identity(manifest, "source_reads") != (
        hashlib.sha256(b"canonical").hexdigest(),
        9,
    )


def test_ngs_package_inventory_covers_persisted_fastq_qc_and_verification_artifacts() -> None:
    from services import ngs_alignment_sessions as service

    job_id = "5dceb3d6-0ac7-4058-96b4-b7d1aff6a8fa"
    output_dir = Path(
        "/home/dalab/.biomodstack-dev/bms_results/"
        "public_zenodo_7595170_AAZ605_pGM12_fastq_qc_racefix_rerun_20260810T024400Z_20260809_214452"
    )
    if not output_dir.is_dir():
        pytest.skip("Development acceptance package is unavailable")
    artifacts = service.build_ngs_package_artifacts(
        job_id,
        source_reference_sha256="b4c4f948cca0e583d9a7183fef975f54557c4c0dc925bfc940148ea3a9f2cf69",
        workflow_id="ont_fastq_qc",
        input_mode="fastq",
        source_input_path=Path(
            "/home/dalab/.biomodstack-dev/inputs/public/onramp-zenodo-7595170/AAZ605.basecalls.fastq.gz"
        ),
        results_dir=Path("/home/dalab/.biomodstack-dev/bms_results"),
        job_output_dir=output_dir,
    )
    by_kind = {artifact["kind"]: artifact for artifact in artifacts}
    for kind in (
        "source_reads_fastq",
        "alignment_bam",
        "alignment_bai",
        "reference",
        "reference_index",
        "consensus",
        "consensus_index",
        "summary",
        "per_base_support",
        "log",
        "sequence_qc_manifest",
        "construct_verification_manifest",
        "verification_summary",
        "human_evidence_report",
        "source_read_provenance",
    ):
        assert by_kind[kind]["state"] == "present"
        assert by_kind[kind]["range_capable"] is True
        assert by_kind[kind]["url"].startswith(f"/api/jobs/{job_id}/ngs-artifacts/")
    assert by_kind["source_reads_fastq"]["sha256"] == "d55928dfe4bd161ad3e0b1a29fcd3f0fff273d9243386d281fd94df0e61d149e"
    assert by_kind["source_reads_fastq"]["size_bytes"] == 51_826_738
    assert by_kind["signal_data"] == {
        "kind": "signal_data",
        "source": "input_mode",
        "relative_path": None,
        "state": "not_applicable_to_input_mode",
        "artifact_id": None,
        "owner_scope": "result_root",
        "scientific_role": "optional_evidence",
        "display_order": 36,
        "content_disposition": "none",
        "filename_extension": None,
        "sha256": None,
        "size_bytes": None,
        "mime_type": None,
        "url": None,
        "range_capable": False,
        "unavailable_reason": "FASTQ input has no retained raw signal artifact",
    }


def test_ngs_package_routes_support_authenticated_inventory_and_http_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import ngs_alignment_sessions as service

    artifact = tmp_path / "reads.fastq.gz"
    artifact.write_bytes(b"abcdefghij")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    descriptor = {
        "kind": "source_reads_fastq",
        "source": "construct_verification_input",
        "relative_path": "reads.fastq.gz",
        "state": "present",
        "sha256": digest,
        "size_bytes": 10,
        "mime_type": "application/gzip",
        "url": f"/api/jobs/job-a/ngs-artifacts/{digest}",
        "range_capable": True,
    }
    monkeypatch.setattr(service, "build_ngs_package_artifacts", lambda *_args, **_kwargs: [descriptor])
    monkeypatch.setattr(service, "resolve_ngs_package_artifact", lambda *_args, **_kwargs: (artifact, descriptor))
    app = _ngs_app()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(
        child_output_dir=None,
        params={
            "reference_sequence_sha256": hashlib.sha256(b"ACGTACGT").hexdigest(),
            "ont_workflow_id": "ont_fastq_qc",
            "ont_input_mode": "fastq",
            "fastq_path": str(artifact),
        },
        output_dir=str(tmp_path),
    )
    client = TestClient(app, client=("127.0.0.1", 40000))
    inventory = client.get("/api/jobs/job-a/ngs-artifacts")
    assert inventory.status_code == 200
    expected_public_descriptor = {key: value for key, value in descriptor.items() if key != "relative_path"}
    assert inventory.json() == {"job_id": "job-a", "artifacts": [expected_public_descriptor]}
    ranged = client.get(
        f"/api/jobs/job-a/ngs-artifacts/{digest}",
        headers={"Range": "bytes=2-5"},
    )
    assert ranged.status_code == 206
    assert ranged.content == b"cdef"
    assert ranged.headers["content-range"] == "bytes 2-5/10"
    assert ranged.headers["accept-ranges"] == "bytes"
    assert ranged.headers["etag"] == f'"sha256:{digest}"'


def test_ngs_package_routes_deny_requests_without_job_capability() -> None:
    from routers import ngs_alignment_sessions as routes
    from services import alignment_access

    job = SimpleNamespace(
        id="job-denied",
        params={
            "reference_sequence_sha256": "a" * 64,
            "ont_workflow_id": "ont_fastq_qc",
            "ont_input_mode": "fastq",
        },
        provenance={
            alignment_access.PROVENANCE_DIGEST_KEY: alignment_access.token_sha256("not-present"),
            alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
        },
        output_dir="/tmp/job-denied",
        child_output_dir=None,
    )

    class Result:
        def scalar_one_or_none(self):
            return job

    class Session:
        async def execute(self, _statement):
            return Result()

    app = _ngs_app()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.get_session] = lambda: Session()
    client = TestClient(app, client=("127.0.0.1", 40000))

    assert client.get("/api/jobs/job-denied/ngs-artifacts").status_code == 403
    assert client.get(f"/api/jobs/job-denied/ngs-artifacts/{'a' * 64}").status_code == 403


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
    app = _ngs_app()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(params={"reference_sequence_sha256": hashlib.sha256(b"ACGTACGT").hexdigest(), "ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"}, output_dir="/tmp/job-a-run")
    client = TestClient(app, client=("127.0.0.1", 40000))

    ranged = client.get(
        "/api/jobs/job-a/alignment-artifacts/" + "a" * 64,
        headers={"Range": "bytes=2-5"},
    )
    assert ranged.status_code == 206
    assert ranged.content == b"cdef"
    assert ranged.headers["content-range"] == "bytes 2-5/10"
    assert ranged.headers["accept-ranges"] == "bytes"
    etag = ranged.headers["etag"]
    assert etag == f'"sha256:{digest}"'

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

    monkeypatch.setattr(
        service,
        "resolve_session_alignment_bundle",
        lambda *_args, **_kwargs: (
            Path("/tmp/aligned.bam"), {"sha256": "a" * 64, "size_bytes": 3},
            Path("/tmp/aligned.bam.bai"), {"sha256": "b" * 64, "size_bytes": 3},
        ),
    )
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
    app = _ngs_app()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(params={"reference_sequence_sha256": hashlib.sha256(b"ACGTACGT").hexdigest(), "ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"}, output_dir="/tmp/job-a-run")
    client = TestClient(app, client=("127.0.0.1", 40000))
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
        "kind": "alignment_bam",
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
        job_id="00000000-0000-4000-8000-000000000001",
    )

    assert response.status_code == 200
    assert opener_thread is not None
    assert opener_thread != event_loop_thread
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_governed_package_html_descriptor_is_forced_to_sandboxed_attachment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes

    report = tmp_path / "evidence.html"
    report.write_bytes(b"<html></html>")
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    monkeypatch.setattr(routes.service, "open_verified_artifact_snapshot", lambda *_args, **_kwargs: io.BytesIO(report.read_bytes()))
    metadata = {
        "kind": "human_evidence_report",
        "source": "construct_verification",
        "size_bytes": report.stat().st_size,
        "sha256": digest,
        "mime_type": "text/html",
    }
    for headers, expected_status in (([], 200), ([(b"range", b"bytes=0-4")], 206)):
        request = Request({"type": "http", "method": "GET", "path": "/artifact", "headers": headers})
        response = await routes._serve_artifact(
            report, metadata, request, job_id="00000000-0000-4000-8000-000000000001"
        )
        assert response.status_code == expected_status
        assert response.headers["content-disposition"] == (
            f'attachment; filename="human_evidence_report-{digest[:12]}.html"'
        )
        assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert isinstance(response, StreamingResponse)
        chunks = [chunk async for chunk in response.body_iterator]
        body = b"".join(chunk.encode() if isinstance(chunk, str) else bytes(chunk) for chunk in chunks)
        assert body == report.read_bytes()[: len(body)]


@pytest.mark.asyncio
async def test_artifact_http_contract_handles_conditionals_ranges_head_and_typed_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes

    content = b"0123456789"
    artifact = tmp_path / "aligned.bam"
    artifact.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    etag = f'"sha256:{digest}"'
    job_id = "00000000-0000-4000-8000-000000000001"
    metadata = {
        "kind": "alignment",
        "size_bytes": len(content),
        "sha256": digest,
        "mime_type": "application/octet-stream",
    }
    async def serve(method: str = "GET", headers: list[tuple[bytes, bytes]] | None = None):
        request = Request({"type": "http", "method": method, "path": "/artifact", "headers": headers or []})
        return await routes._serve_artifact(artifact, metadata, request, job_id=job_id)

    not_modified = await serve(headers=[(b"if-none-match", etag.encode()), (b"range", b"broken")])
    assert not_modified.status_code == 304
    assert not_modified.headers["etag"] == etag

    artifact.write_bytes(b"replacement")
    drifted_not_modified = await serve(headers=[(b"if-none-match", etag.encode())])
    assert drifted_not_modified.status_code == 409
    artifact.write_bytes(content)

    malformed = await serve(headers=[(b"range", b"bytes=9-1"), (b"if-range", b'"different"')])
    assert malformed.status_code == 400
    assert json.loads(bytes(malformed.body))["code"] == "NGS_RANGE_INVALID"

    unsatisfiable = await serve(headers=[(b"range", b"bytes=10-")])
    assert unsatisfiable.status_code == 416
    assert unsatisfiable.headers["content-range"] == "bytes */10"
    assert unsatisfiable.headers["etag"] == etag
    assert json.loads(bytes(unsatisfiable.body))["code"] == "NGS_RANGE_UNSATISFIABLE"

    partial = await serve(headers=[(b"range", b"bytes=2-5"), (b"if-range", etag.encode())])
    assert partial.status_code == 206
    assert isinstance(partial, StreamingResponse)
    assert b"".join([chunk.encode() if isinstance(chunk, str) else bytes(chunk) async for chunk in partial.body_iterator]) == b"2345"

    full = await serve(headers=[(b"range", b"bytes=2-5"), (b"if-range", b'"different"')])
    assert full.status_code == 200
    assert isinstance(full, StreamingResponse)
    assert b"".join([chunk.encode() if isinstance(chunk, str) else bytes(chunk) async for chunk in full.body_iterator]) == content

    head = await serve(method="HEAD", headers=[(b"range", b"bytes=2-5")])
    assert head.status_code == 200
    assert head.body == b""
    assert head.headers["content-length"] == "10"
    assert head.headers["content-disposition"] == f'inline; filename="alignment-{digest[:12]}.bam"'

    monkeypatch.setattr(
        routes.service,
        "open_verified_artifact_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(routes.service.AlignmentSessionError("digest changed")),
    )
    conflict = await serve()
    assert conflict.status_code == 409

    from jsonschema import validate

    error_schema = json.loads(
        (API_ROOT.parents[1] / "schemas/ngs/ont_ngs_error_v1.schema.json").read_text(encoding="utf-8")
    )
    for response in (malformed, unsatisfiable, conflict):
        validate(json.loads(bytes(response.body)), error_schema)


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

    app = _ngs_app()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = deny_access
    client = TestClient(app, client=("127.0.0.1", 40000))
    for url in (
        "/api/jobs/job-b/alignment-sessions",
        f"/api/jobs/job-b/alignment-session-artifacts/primary/alignment/{'0' * 64}",
    ):
        response = client.get(url)
        assert response.status_code == 403
        assert response.json() == {"detail": "job access denied"}


def test_every_governed_read_route_uses_the_package_authority_dependency() -> None:
    from fastapi.routing import APIRoute
    from routers import ngs_alignment_sessions as routes

    governed = [
        route for route in routes.router.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/jobs/{job_id}/")
        and bool(route.methods.intersection({"GET", "HEAD"}))
    ]
    assert governed
    for route in governed:
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        assert routes.require_alignment_job in dependency_calls, route.path


def test_artifact_route_rejects_package_drift_before_descriptor_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import alignment_access
    from services.ont_ngs_results import OntNgsResultError

    job = SimpleNamespace(
        id="00000000-0000-4000-8000-000000000001",
        model_id="nanopore",
        status="completed",
        params={"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
        provenance={},
    )
    app, token = _build_public_ngs_result_test_app(monkeypatch, job, {})

    async def reject_drift(_job):
        raise OntNgsResultError("persisted package digest mismatch")

    monkeypatch.setattr(routes, "build_ont_fastq_qc_result", reject_drift)
    monkeypatch.setattr(
        routes.service,
        "resolve_ngs_package_artifact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("descriptor disclosed after drift")),
    )
    with TestClient(app, client=("127.0.0.1", 40000)) as client:
        client.cookies.set(alignment_access.cookie_name(job.id), token, path="/")
        response = client.get(f"/api/jobs/{job.id}/ngs-artifacts/{'a' * 64}")

    assert response.status_code == 409
    assert response.json()["code"] == "NGS_PACKAGE_INTEGRITY_CONFLICT"


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
    monkeypatch.setattr(
        service,
        "_samtools_command",
        lambda: service._PinnedSamtoolsCommand(("samtools",), (), None, None),
    )
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
    monkeypatch.setattr(
        service,
        "_samtools_command",
        lambda: service._PinnedSamtoolsCommand(("samtools",), (), None, None),
    )
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
    monkeypatch.setattr(service, "_validate_alignment_bundle", lambda *_args, **_kwargs: (True, None))

    primary = service.build_alignment_sessions("job-a", source_reference_sha256=hashlib.sha256(b"ACGTACGT").hexdigest(), results_dir=tmp_path)[0]

    assert primary["ready"] is False
    assert primary["artifacts"] == {}
    assert "integrity" in primary["unavailable_reason"].lower()


def test_exact_read_detail_scan_exhaustion_is_not_reported_as_404(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import ngs_alignment_sessions as routes
    from services import ngs_alignment_sessions as service

    monkeypatch.setattr(
        service,
        "resolve_session_alignment_bundle",
        lambda *_args, **_kwargs: (
            Path("/tmp/aligned.bam"), {"sha256": "a" * 64, "size_bytes": 3},
            Path("/tmp/aligned.bam.bai"), {"sha256": "b" * 64, "size_bytes": 3},
        ),
    )
    monkeypatch.setattr(
        service,
        "read_bam_exact",
        lambda *_args, **_kwargs: {"read": None, "scan_truncated": True},
        raising=False,
    )
    app = _ngs_app()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.require_alignment_job] = lambda: SimpleNamespace(params={"reference_sequence_sha256": hashlib.sha256(b"ACGTACGT").hexdigest(), "ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"}, output_dir="/tmp/job-a-run")

    response = TestClient(app, client=("127.0.0.1", 40000)).get("/api/jobs/job-a/reads/target?session_id=session-a")

    assert response.status_code == 409
    assert response.json()["code"] == "NGS_READ_SCAN_TRUNCATED"
    assert response.json()["resource"] == "read"


def test_production_dimer_process_emits_discoverable_authoritative_manifest() -> None:
    module_path = API_ROOT.parents[1] / "modules" / "ngs" / "fastq_dimer_qc.nf"
    source = module_path.read_text(encoding="utf-8")

    assert 'path "qc_manifest.json", emit: qc_manifest' in source
    assert 'scripts/build_alignment_session_manifest.sh' in source
    assert 'dimer_candidates.aligned.bam' in source
    assert 'dimer_reference.fasta' in source


def _build_public_ngs_result_test_app(monkeypatch: pytest.MonkeyPatch, job, payload: dict) -> tuple[FastAPI, str]:
    from routers import ngs_alignment_sessions as router
    from services import alignment_access

    class SelectResult:
        def scalar_one_or_none(self):
            return job

    class Session:
        async def execute(self, _statement):
            return SelectResult()

    async def resolve_hierarchy(_job, _domain_session, _experiment_session):
        return SimpleNamespace(project_id="project-a")

    async def allow_project(*_args, **_kwargs):
        return "test-operator"

    async def build_result(_job):
        return payload

    monkeypatch.setenv("BMS_RUNTIME_MODE", "dev")
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "http://127.0.0.1:18082/")
    monkeypatch.setattr(router, "LOCAL_DEVELOPMENT_ADMIN_HOSTS", frozenset({"127.0.0.1"}))
    monkeypatch.setattr(router, "resolve_ont_ngs_hierarchy_authority", resolve_hierarchy)
    monkeypatch.setattr(router, "capability_hierarchy_matches", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(router, "_require_governed_project_principal", allow_project)
    monkeypatch.setattr(router, "build_ont_fastq_qc_result", build_result)

    app = _ngs_app()
    app.include_router(router.router, prefix="/api")
    app.dependency_overrides[router.get_session] = lambda: Session()
    app.dependency_overrides[router.get_molbio_ngs_session] = lambda: object()
    app.dependency_overrides[router.get_experiment_session] = lambda: object()
    token, token_digest = alignment_access.issue_alignment_access_token()
    job.provenance[alignment_access.PROVENANCE_DIGEST_KEY] = token_digest
    job.provenance[alignment_access.PROVENANCE_SCHEME_KEY] = alignment_access.SCHEME
    return app, token


def test_public_ngs_result_route_requires_job_scoped_capability_and_serializes_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import alignment_access

    payload = json.loads(
        (API_ROOT / "tests" / "fixtures" / "ont_fastq_qc_result_retry3_v1.json").read_text(encoding="utf-8")
    )
    job = SimpleNamespace(
        id="job-public-result",
        model_id="nanopore",
        status="completed",
        params={
            "ont_workflow_id": "ont_fastq_qc",
            "ont_input_mode": "fastq",
            "reference_sequence_sha256": "b" * 64,
            "fastq_path": "/managed/input.fastq.gz",
        },
        provenance={},
    )
    app, token = _build_public_ngs_result_test_app(monkeypatch, job, payload)

    with TestClient(app, client=("127.0.0.1", 40000)) as client:
        missing = client.get(f"/api/jobs/{job.id}/ngs-result")
        wrong = client.get(
            f"/api/jobs/{job.id}/ngs-result",
            cookies={alignment_access.cookie_name(job.id): "wrong-token"},
        )
        client.cookies.set(
            alignment_access.cookie_name(job.id),
            token,
            path="/",
        )
        accepted = client.get(f"/api/jobs/{job.id}/ngs-result")

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert accepted.status_code == 200
    body = accepted.json()
    assert body["schema"] == "bms.ngs.fastq-qc-result.v1"
    assert body["job"]["id"] == payload["job"]["id"]
    assert all("bms_results/" not in json.dumps(item) for item in body["artifacts"])


def test_public_ngs_result_route_translates_result_builder_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as router
    from services import alignment_access
    from services.ont_ngs_results import OntNgsResultError

    job = SimpleNamespace(
        id="job-result-error",
        model_id="nanopore",
        status="completed",
        params={"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
        provenance={},
    )
    payload = {"schema": "bms.ngs.fastq-qc-result.v1"}
    app, token = _build_public_ngs_result_test_app(monkeypatch, job, payload)

    async def fail_builder(_job):
        raise OntNgsResultError("result artifact digest mismatch")

    monkeypatch.setattr(router, "build_ont_fastq_qc_result", fail_builder)
    with TestClient(app, client=("127.0.0.1", 40000)) as client:
        client.cookies.set(alignment_access.cookie_name(job.id), token, path="/")
        response = client.get(f"/api/jobs/{job.id}/ngs-result")

    assert response.status_code == 409
    assert response.json()["code"] == "NGS_PACKAGE_INTEGRITY_CONFLICT"


def test_public_ngs_result_route_translates_missing_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as router
    from services import alignment_access
    from services.ont_ngs_results import OntNgsResultError

    job = SimpleNamespace(
        id="job-result-missing",
        model_id="nanopore",
        status="completed",
        params={"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"},
        provenance={},
    )
    app, token = _build_public_ngs_result_test_app(monkeypatch, job, {})

    async def fail_builder(_job):
        raise OntNgsResultError("result artifact not found")

    monkeypatch.setattr(router, "build_ont_fastq_qc_result", fail_builder)
    with TestClient(app, client=("127.0.0.1", 40000)) as client:
        client.cookies.set(alignment_access.cookie_name(job.id), token, path="/")
        response = client.get(f"/api/jobs/{job.id}/ngs-result")

    assert response.status_code == 409
    assert response.json()["code"] == "NGS_PACKAGE_INTEGRITY_CONFLICT"


def test_rotation_validates_signal_alignment_package_without_fastq_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import ngs_alignment_sessions as router

    validate = cast(Any, getattr(router, "_validate_rotation_package_authority", None))
    assert callable(validate), "rotation package-authority validator is missing"
    job = SimpleNamespace(
        model_id="nanopore",
        params={
            "ont_workflow_id": "ont_plasmid_qc",
            "ont_input_mode": "bam",
            "run_fastq_qc": False,
            "source_move_source_id": "ont-moves-exact",
            "source_external_move_registration_receipt_id": "ont-external-move-exact",
        },
    )
    calls: list[str] = []

    class PinnedPackage:
        async def __aenter__(self):
            calls.append("signal")
            return Path("/proc/self/fd/test-package")

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(router, "_validated_pinned_result_root", lambda _job: PinnedPackage())

    async def reject_fastq(_job):
        raise AssertionError("signal-alignment rotation used the FASTQ-QC package builder")

    monkeypatch.setattr(router, "build_ont_fastq_qc_result", reject_fastq)

    asyncio.run(validate(job))

    assert calls == ["signal"]
