from __future__ import annotations

import hashlib
import json
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job
from routers import jobs
from routers import ont_runs
from schemas import JobStatus
from services import alignment_access, stage_reporting


def _request(path: str, token: str | None = None) -> Request:
    headers = [(b"x-forwarded-proto", b"https")]
    if token is not None:
        if "/api/jobs/" in path:
            job_id = path.split("/api/jobs/", 1)[1].split("/", 1)[0]
            cookie = f"{alignment_access.cookie_name(job_id, secure=True)}={token}"
            headers.append((b"cookie", cookie.encode("ascii")))
        else:
            headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "headers": headers,
        }
    )


def test_stage_callback_credential_is_digest_only_and_wired_end_to_end() -> None:
    token, digest = stage_reporting.issue_stage_report_token()
    provenance = {stage_reporting.PROVENANCE_DIGEST_KEY: digest}
    assert token not in json.dumps(provenance)
    assert stage_reporting.token_is_authorized(provenance, token)
    assert not stage_reporting.token_is_authorized(provenance, token + "x")
    nextflow_source = (Path(__file__).resolve().parents[1] / "services" / "nextflow.py").read_text(encoding="utf-8")
    reporter_source = (Path(__file__).resolve().parents[3] / "scripts" / "stage_reporter.py").read_text(encoding="utf-8")
    assert "env[stage_reporting.ENV_TOKEN_KEY] = stage_report_token" in nextflow_source
    assert 'headers = {"Authorization": f"Bearer {STAGE_REPORT_TOKEN}"}' in reporter_source


def _assert_job_scoped_capability(response: Response, job: Job) -> None:
    header = response.headers.get("set-cookie")
    assert header is not None
    cookie = SimpleCookie()
    cookie.load(header)
    name = alignment_access.cookie_name(job.id, secure=True)
    token = cookie[name].value
    provenance = job.provenance
    assert isinstance(provenance, dict)
    assert hashlib.sha256(token.encode("utf-8")).hexdigest() == provenance[alignment_access.PROVENANCE_DIGEST_KEY]
    assert provenance[alignment_access.PROVENANCE_SCHEME_KEY] == alignment_access.SCHEME
    assert cookie[name]["path"] == "/"
    assert cookie[name]["max-age"] == "1800"
    assert cookie[name]["httponly"] is True
    assert cookie[name]["secure"] is True
    assert cookie[name]["samesite"].lower() == "strict"


def _terminal_anchor(manifest: Path) -> dict[str, Any]:
    products = {
        key: {"path": key, "sha256": "c" * 64}
        for key in ("barcode_units_manifest", "dorado_preflight", "dorado_runtime_provenance")
    }
    products["demux_manifest"] = {
        "path": "demux/demux_manifest.json",
        "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    }
    return {
        "schema": "biomodstack.ont_dorado_terminal_products.v1",
        "stage": "dorado_demux",
        "products": products,
    }


def _write_terminal_product_tree(output: Path) -> tuple[Path, dict[str, Any]]:
    basecall = output / "basecall"
    demux_dir = output / "demux"
    unit_dir = demux_dir / "demux" / "units"
    unit_manifest_dir = demux_dir / "demux" / "manifests"
    basecall.mkdir(parents=True)
    unit_dir.mkdir(parents=True)
    unit_manifest_dir.mkdir(parents=True)
    lock_path = Path(__file__).resolve().parents[3] / "config" / "ngs" / "dorado_v1.3.1.lock.json"
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    lock_sha = hashlib.sha256(lock_bytes).hexdigest()
    approved_model = lock["models"]["dna"]["fast"]
    model_id = approved_model["id"]
    model_aggregate_sha = approved_model["aggregate_sha256"]
    runtime_sha = lock["dorado"]["sif_sha256"]
    runtime_version = lock["dorado"]["version"]
    calls_sha = "3" * 64
    bam = unit_dir / "barcode01.bam"
    bam.write_bytes(b"anchored-barcode-bam")
    bam_sha = hashlib.sha256(bam.read_bytes()).hexdigest()
    unit_payload = {
        "schema": "biomodstack.dorado_barcode_unit.v1",
        "unit_id": "barcode01",
        "bam_path": "demux/units/barcode01.bam",
        "bam_sha256": bam_sha,
        "read_count": 1,
    }

    preflight = basecall / "dorado_preflight.json"
    preflight.write_text(
        json.dumps(
            {
                "schema": "biomodstack.dorado_preflight.v1",
                "lock": {"sha256": lock_sha},
                "selection": {
                    "model_id": model_id,
                    "model_aggregate_sha256": model_aggregate_sha,
                    "mode": "simplex",
                    "molecule": "dna",
                    "quality": "fast",
                    "modified_bases": "none",
                    "modified_bases_model_id": None,
                    "stereo_model_id": None,
                },
                "runtime": {
                    "version": runtime_version,
                    "sif_sha256": runtime_sha,
                    "assets": {"runtime_sif": {"sha256": runtime_sha}},
                },
                "barcoding": {"kit": "SQK-RBK114-96"},
            }
        ),
        encoding="utf-8",
    )
    preflight_sha = hashlib.sha256(preflight.read_bytes()).hexdigest()
    unit_payload.update({"source_calls_sha256": calls_sha, "preflight_sha256": preflight_sha})
    unit_manifest = unit_manifest_dir / "barcode01.json"
    unit_manifest.write_text(json.dumps(unit_payload), encoding="utf-8")
    unit = {
        **{key: unit_payload[key] for key in (
            "unit_id", "bam_path", "bam_sha256", "read_count", "source_calls_sha256", "preflight_sha256"
        )},
        "unit_manifest_path": "demux/manifests/barcode01.json",
        "unit_manifest_sha256": hashlib.sha256(unit_manifest.read_bytes()).hexdigest(),
    }
    (basecall / "dorado_runtime_provenance.json").write_text(
        json.dumps(
            {
                "schema": "biomodstack.dorado_runtime_provenance.v1",
                "preflight_sha256": preflight_sha,
                "model_id": model_id,
                "mode": "simplex",
                "runtime_sha256": runtime_sha,
                "calls_bam": {"sha256": calls_sha, "read_count": 1},
            }
        ),
        encoding="utf-8",
    )
    manifest = demux_dir / "demux_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "biomodstack.dorado_demux.v1",
                "preflight_sha256": preflight_sha,
                "source_calls": {"sha256": calls_sha, "read_count": 1},
                "total_reads": 1,
                "units": [unit],
            }
        ),
        encoding="utf-8",
    )
    (demux_dir / "per_barcode_units.json").write_text(
        json.dumps({"schema": "biomodstack.dorado_barcode_units.v1", "units": [unit]}),
        encoding="utf-8",
    )
    return manifest, {
        "barcode_kit": "SQK-RBK114-96",
        "dorado_lock_sha256": lock_sha,
        "dorado_resolved_model_id": model_id,
        "dorado_basecall_mode": "simplex",
        "dorado_quality_mode": "fast",
        "modified_bases": "none",
    }


@pytest.mark.asyncio
async def test_nanopore_resubmit_and_resume_each_issue_fresh_job_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "get_results_dir", lambda: tmp_path)
    original_output = tmp_path / "original"
    (original_output / "work").mkdir(parents=True)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            source_capability = "source-job-" + "capability"
            original = Job(
                id="original-job",
                name="ont-capability",
                model_id="nanopore",
                mode="fastq_qc",
                params={"fastq_path": "/tmp/reads.fastq"},
                status=JobStatus.FAILED.value,
                output_dir=str(original_output),
                completed_stages=[],
                stage_outputs={},
                provenance={
                    alignment_access.PROVENANCE_DIGEST_KEY: alignment_access.token_sha256(source_capability),
                    alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
                },
            )
            session.add(original)
            await session.commit()

            with pytest.raises(HTTPException) as denied_resubmit:
                await jobs.resubmit_job(
                    original.id,
                    request=_request(f"/api/jobs/{original.id}/resubmit", "wrong-job-capability"),
                    response=Response(),
                    session=session,
                )
            assert denied_resubmit.value.status_code == 403

            with pytest.raises(HTTPException) as denied_resume:
                await jobs.resume_job(
                    original.id,
                    request_context=_request(f"/api/jobs/{original.id}/resume", "wrong-job-capability"),
                    response=Response(),
                    request=None,
                    background_tasks=BackgroundTasks(),
                    session=session,
                )
            assert denied_resume.value.status_code == 403

            for reserved_key in (
                "code_root",
                "wf_clone_source",
                "wf_clone_revision",
                "out_dir",
                "rfd_models",
                "af2_models",
                "boltz_models",
                "alphafold_params",
                "job_id",
                "fastq_path",
                "reference_fasta",
                "future_runtime_selector",
            ):
                with pytest.raises(HTTPException) as denied_override:
                    await jobs.resume_job(
                        original.id,
                        request_context=_request(f"/api/jobs/{original.id}/resume", source_capability),
                        response=Response(),
                        request=jobs.ResumeJobRequest(param_overrides={reserved_key: "/caller/value"}),
                        background_tasks=BackgroundTasks(),
                        session=session,
                    )
                assert denied_override.value.status_code == 422
                assert reserved_key in str(denied_override.value.detail)

            resubmit_response = Response()
            resubmitted = await jobs.resubmit_job(
                original.id,
                request=_request(f"/api/jobs/{original.id}/resubmit", source_capability),
                response=resubmit_response,
                session=session,
            )
            resubmitted_job = await session.get(Job, resubmitted["new_job_id"])
            assert resubmitted_job is not None
            _assert_job_scoped_capability(resubmit_response, resubmitted_job)

            resume_response = Response()
            resumed = await jobs.resume_job(
                original.id,
                request_context=_request(f"/api/jobs/{original.id}/resume", source_capability),
                response=resume_response,
                request=None,
                background_tasks=BackgroundTasks(),
                session=session,
            )
            resumed_job = await session.get(Job, resumed["new_job_id"])
            assert resumed_job is not None
            _assert_job_scoped_capability(resume_response, resumed_job)

            assert resubmitted_job.id != resumed_job.id
            assert (
                resubmitted_job.provenance[alignment_access.PROVENANCE_DIGEST_KEY]
                != resumed_job.provenance[alignment_access.PROVENANCE_DIGEST_KEY]
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_barcode_unit_requires_completed_authorized_exact_source(tmp_path: Path) -> None:
    output = tmp_path / "source"
    bam = output / "demux" / "demux" / "barcode01.bam"
    bam.parent.mkdir(parents=True)
    bam.write_bytes(b"exact-barcode-bam")
    digest = hashlib.sha256(bam.read_bytes()).hexdigest()
    unit_manifest = output / "demux" / "demux" / "manifests" / "barcode01.json"
    unit_manifest.parent.mkdir(parents=True)
    unit_manifest.write_text(json.dumps({
        "schema": "biomodstack.dorado_barcode_unit.v1",
        "unit_id": "barcode01",
        "bam_path": "demux/barcode01.bam",
        "bam_sha256": digest,
        "read_count": 3,
        "source_calls_sha256": "a" * 64,
        "preflight_sha256": "b" * 64,
    }), encoding="utf-8")
    manifest = output / "demux" / "demux_manifest.json"
    manifest.write_text(json.dumps({
        "schema": "biomodstack.dorado_demux.v1",
        "preflight_sha256": "b" * 64,
        "source_calls": {"sha256": "a" * 64, "read_count": 3},
        "total_reads": 3,
        "units": [{
            "unit_id": "barcode01",
            "bam_path": "demux/barcode01.bam",
            "bam_sha256": digest,
            "unit_manifest_path": "demux/manifests/barcode01.json",
            "unit_manifest_sha256": hashlib.sha256(unit_manifest.read_bytes()).hexdigest(),
            "read_count": 3,
            "source_calls_sha256": "a" * 64,
            "preflight_sha256": "b" * 64,
        }],
    }), encoding="utf-8")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            source_capability = "barcode-source-" + "capability"
            source = Job(
                id="barcode-source",
                name="barcode source",
                model_id="nanopore",
                mode="basecall_dna",
                params={"pod5_dir": "/retained/input", "barcode_kit": "SQK-RBK114-96"},
                status=JobStatus.COMPLETED.value,
                output_dir=str(output),
                completed_stages=["dorado_demux"],
                stage_outputs={"dorado_demux": [str(manifest)]},
                provenance={
                    alignment_access.PROVENANCE_DIGEST_KEY: alignment_access.token_sha256(source_capability),
                    alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
                    "ont_dorado_terminal_products": _terminal_anchor(manifest),
                },
            )
            session.add(source)
            await session.commit()

            with pytest.raises(HTTPException) as denied:
                await ont_runs._authorized_barcode_unit(source.id, "barcode01", _request(f"/api/jobs/{source.id}/barcode", "wrong"), session)
            assert denied.value.status_code == 403

            authorized_job, unit = await ont_runs._authorized_barcode_unit(
                source.id, "barcode01", _request(f"/api/jobs/{source.id}/barcode", source_capability), session
            )
            assert authorized_job.id == source.id
            assert unit["bam_sha256"] == digest
            assert unit["bam_path"] == str(bam.resolve())

            original_bam = bam.read_bytes()
            original_unit_manifest = unit_manifest.read_bytes()
            original_manifest = manifest.read_bytes()
            bam.write_bytes(b"coherently-substituted-barcode-bam")
            substituted_digest = hashlib.sha256(bam.read_bytes()).hexdigest()
            substituted_unit = json.loads(original_unit_manifest)
            substituted_unit["bam_sha256"] = substituted_digest
            unit_manifest.write_text(json.dumps(substituted_unit), encoding="utf-8")
            substituted_manifest = json.loads(original_manifest)
            substituted_manifest["units"][0]["bam_sha256"] = substituted_digest
            substituted_manifest["units"][0]["unit_manifest_sha256"] = hashlib.sha256(unit_manifest.read_bytes()).hexdigest()
            manifest.write_text(json.dumps(substituted_manifest), encoding="utf-8")
            with pytest.raises(HTTPException) as substituted:
                await ont_runs._authorized_barcode_unit(
                    source.id, "barcode01", _request(f"/api/jobs/{source.id}/barcode", source_capability), session
                )
            assert substituted.value.status_code == 409
            bam.write_bytes(original_bam)
            unit_manifest.write_bytes(original_unit_manifest)
            manifest.write_bytes(original_manifest)

            source.params = {"pod5_dir": "/retained/input"}
            await session.commit()
            with pytest.raises(HTTPException) as unbarcoded:
                await ont_runs._authorized_barcode_unit(source.id, "barcode01", _request(f"/api/jobs/{source.id}/barcode", source_capability), session)
            assert unbarcoded.value.status_code == 422

            source.params = {"pod5_dir": "/retained/input", "barcode_kit": "SQK-RBK114-96"}
            source.status = JobStatus.RUNNING.value
            await session.commit()
            with pytest.raises(HTTPException) as incomplete:
                await ont_runs._authorized_barcode_unit(source.id, "barcode01", _request(f"/api/jobs/{source.id}/barcode", source_capability), session)
            assert incomplete.value.status_code == 409
    finally:
        await engine.dispose()


def test_terminal_dorado_product_anchor_binds_demux_preflight_and_runtime(tmp_path: Path) -> None:
    output = tmp_path / "source"
    manifest, params = _write_terminal_product_tree(output)
    source = Job(
        id="anchor-source",
        name="anchor source",
        model_id="nanopore",
        mode="basecall_dna",
        params=params,
        output_dir=str(output),
        status=JobStatus.RUNNING.value,
    )
    anchor = jobs._anchor_dorado_demux_products(source)
    assert anchor["schema"] == "biomodstack.ont_dorado_terminal_products.v1"
    assert anchor["products"]["demux_manifest"]["sha256"] == hashlib.sha256(
        (output / "demux" / "demux_manifest.json").read_bytes()
    ).hexdigest()
    assert set(anchor["products"]) == {
        "demux_manifest",
        "barcode_units_manifest",
        "dorado_preflight",
        "dorado_runtime_provenance",
    }


@pytest.mark.parametrize(
    ("target", "field"),
    (
        ("basecall/dorado_preflight.json", "lock"),
        ("basecall/dorado_preflight.json", "model_aggregate"),
        ("basecall/dorado_runtime_provenance.json", "runtime_model"),
        ("basecall/dorado_runtime_provenance.json", "runtime_sif"),
        ("demux/demux_manifest.json", "calls_digest"),
        ("demux/demux_manifest.json", "read_count"),
        ("demux/per_barcode_units.json", "unit_catalog"),
        ("demux/demux/manifests/barcode01.json", "unit_manifest"),
        ("demux/demux/units/barcode01.bam", "bam_bytes"),
    ),
)
def test_terminal_anchor_rejects_cross_product_identity_contradictions(
    tmp_path: Path, target: str, field: str
) -> None:
    output = tmp_path / field
    _, params = _write_terminal_product_tree(output)
    path = output / target
    if field == "bam_bytes":
        path.write_bytes(b"substituted-unit")
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if field == "lock":
            payload["lock"]["sha256"] = "f" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            replacement_preflight_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            for linked in (
                output / "basecall" / "dorado_runtime_provenance.json",
                output / "demux" / "demux_manifest.json",
            ):
                linked_payload = json.loads(linked.read_text(encoding="utf-8"))
                linked_payload["preflight_sha256"] = replacement_preflight_sha
                linked.write_text(json.dumps(linked_payload), encoding="utf-8")
        elif field == "model_aggregate":
            payload["selection"]["model_aggregate_sha256"] = "c" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            replacement_preflight_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            for linked in (
                output / "basecall" / "dorado_runtime_provenance.json",
                output / "demux" / "demux_manifest.json",
            ):
                linked_payload = json.loads(linked.read_text(encoding="utf-8"))
                linked_payload["preflight_sha256"] = replacement_preflight_sha
                linked.write_text(json.dumps(linked_payload), encoding="utf-8")
        elif field == "runtime_model":
            payload["model_id"] = "unauthorized-model"
            path.write_text(json.dumps(payload), encoding="utf-8")
        elif field == "runtime_sif":
            payload["runtime_sha256"] = "d" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
        elif field == "calls_digest":
            payload["source_calls"]["sha256"] = "e" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
        elif field == "read_count":
            payload["source_calls"]["read_count"] = 2
            path.write_text(json.dumps(payload), encoding="utf-8")
        elif field == "unit_manifest":
            payload["read_count"] = 2
            path.write_text(json.dumps(payload), encoding="utf-8")
        else:
            payload["units"] = []
            path.write_text(json.dumps(payload), encoding="utf-8")
    source = Job(
        id=f"contradiction-{field}",
        name="contradictory terminal products",
        model_id="nanopore",
        mode="basecall_dna",
        params=params,
        output_dir=str(output),
        status=JobStatus.RUNNING.value,
    )
    with pytest.raises(HTTPException) as rejected:
        jobs._anchor_dorado_demux_products(source)
    assert rejected.value.status_code == 409


@pytest.mark.asyncio
async def test_stage_completion_persists_immutable_dorado_anchor(tmp_path: Path) -> None:
    output = tmp_path / "source"
    manifest, params = _write_terminal_product_tree(output)
    stage_token = "stage-report-token-0123456789abcdef"
    stage_digest = hashlib.sha256(stage_token.encode("ascii")).hexdigest()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            source = Job(
                id="stage-anchor-source", name="stage anchor source", model_id="nanopore", mode="basecall_dna",
                params=params, status=JobStatus.RUNNING.value, output_dir=str(output),
                provenance={"workflow_stage_report_token_sha256": stage_digest},
            )
            session.add(source)
            await session.commit()
            request = _request("/stage-complete", stage_token)
            with pytest.raises(HTTPException) as unauthenticated:
                await jobs.report_stage_complete(source.id, _request("/stage-complete"), "dorado_demux", [str(manifest)], session)
            assert unauthenticated.value.status_code == 403
            await jobs.report_stage_complete(source.id, request, "dorado_demux", [str(manifest)], session)
            await session.refresh(source)
            anchored = source.provenance["ont_dorado_terminal_products"]
            assert anchored["products"]["demux_manifest"]["sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
            assert "workflow_stage_report_token_sha256" not in source.provenance
            with pytest.raises(HTTPException) as replayed:
                await jobs.report_stage_complete(source.id, request, "dorado_demux", [str(manifest)], session)
            assert replayed.value.status_code == 403
            replacement_token = "replacement-stage-token-0123456789ab"
            replacement_provenance = dict(source.provenance)
            replacement_provenance["workflow_stage_report_token_sha256"] = hashlib.sha256(
                replacement_token.encode("ascii")
            ).hexdigest()
            source.provenance = replacement_provenance
            source.status = JobStatus.COMPLETED.value
            await session.commit()
            substituted = json.loads(manifest.read_text(encoding="utf-8"))
            substituted["substituted"] = True
            manifest.write_text(json.dumps(substituted), encoding="utf-8")
            with pytest.raises(HTTPException, match="immutable"):
                await jobs.report_stage_complete(
                    source.id,
                    _request("/stage-complete", replacement_token),
                    "dorado_demux",
                    [str(manifest)],
                    session,
                )
    finally:
        await engine.dispose()


def test_barcode_route_receives_job_scoped_httponly_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BMS_RUNTIME_MODE", "dev")
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "http://127.0.0.1:18082/")
    output = tmp_path / "source"
    bam = output / "demux" / "demux" / "units" / "barcode01.bam"
    bam.parent.mkdir(parents=True)
    bam.write_bytes(b"route-cookie-bam")
    bam_sha = hashlib.sha256(bam.read_bytes()).hexdigest()
    unit_manifest = output / "demux" / "demux" / "manifests" / "barcode01.json"
    unit_manifest.parent.mkdir(parents=True)
    unit_payload = {"schema": "biomodstack.dorado_barcode_unit.v1", "unit_id": "barcode01", "bam_path": "demux/units/barcode01.bam", "bam_sha256": bam_sha, "read_count": 1, "source_calls_sha256": "a" * 64, "preflight_sha256": "b" * 64}
    unit_manifest.write_text(json.dumps(unit_payload), encoding="utf-8")
    manifest = output / "demux" / "demux_manifest.json"
    manifest.write_text(json.dumps({"schema": "biomodstack.dorado_demux.v1", "preflight_sha256": "b" * 64, "source_calls": {"sha256": "a" * 64, "read_count": 1}, "total_reads": 1, "units": [{"unit_id": "barcode01", "bam_path": "demux/units/barcode01.bam", "bam_sha256": bam_sha, "read_count": 1, "source_calls_sha256": "a" * 64, "preflight_sha256": "b" * 64, "unit_manifest_path": "demux/manifests/barcode01.json", "unit_manifest_sha256": hashlib.sha256(unit_manifest.read_bytes()).hexdigest()}]}), encoding="utf-8")
    token = "route-cookie-" + "capability"
    source = Job(id="cookie-source", name="cookie source", model_id="nanopore", mode="basecall_dna", params={"barcode_kit": "SQK-RBK114-96"}, status=JobStatus.COMPLETED.value, output_dir=str(output), completed_stages=["dorado_demux"], stage_outputs={"dorado_demux": [str(manifest)]}, provenance={alignment_access.PROVENANCE_DIGEST_KEY: alignment_access.token_sha256(token), alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME, "ont_dorado_terminal_products": _terminal_anchor(manifest)})

    class FakeResult:
        def scalar_one_or_none(self):
            return source

    class FakeSession:
        async def execute(self, _statement):
            return FakeResult()

    app = FastAPI()
    app.include_router(ont_runs.barcode_router, prefix="/api/jobs")
    app.dependency_overrides[ont_runs.get_session] = lambda: FakeSession()
    with TestClient(app, client=("127.0.0.1", 40000)) as client:
        client.cookies.set(alignment_access.cookie_name("cookie-source"), token, path="/")
        response = client.get("/api/jobs/cookie-source/barcode-units")
    assert response.status_code == 200
    assert response.json()["units"][0]["unit_id"] == "barcode01"


@pytest.mark.asyncio
async def test_barcode_submit_is_retired_and_cannot_authorize_browser_paths() -> None:
    with pytest.raises(HTTPException) as raised:
        await ont_runs.ont_submit_barcode_unit(
            "source",
            "barcode01",
            ont_runs.OntBarcodeUnitSubmitRequest(
                target_workflow="ont_plasmid_qc",
                reference_fasta="/inputs/ref.fa",
            ),
            BackgroundTasks(),
            _request("/api/jobs/source/barcode-units/barcode01/submit", "token"),
            Response(),
            object(),
        )
    assert raised.value.status_code == 410
