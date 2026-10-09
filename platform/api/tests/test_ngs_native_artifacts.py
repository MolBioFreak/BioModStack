"""Native publication readback through the real Job SQL and governed routes."""
from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job, get_session
from routers import files, ngs_alignment_sessions as routes
from services import alignment_access, job_result_roots, ngs_alignment_sessions as service


@pytest_asyncio.fixture
async def native_http(tmp_path, monkeypatch):
    root = tmp_path / "results"
    output = root / "collector" / "native"
    output.mkdir(parents=True)
    monkeypatch.setattr(service, "get_results_dir", lambda: root)
    monkeypatch.setattr(job_result_roots, "get_results_dir", lambda: root)
    monkeypatch.setattr(files, "get_allowed_roots", lambda: {"bms_results": root})
    monkeypatch.setattr(files, "resolve_allowed_path", lambda value: root / Path(value).relative_to("bms_results"))
    monkeypatch.setenv("BMS_RUNTIME_MODE", "dev")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    token = "synthetic-native-read-capability"
    async with factory() as session:
        for jid in ("clone-native", "other-native"):
            session.add(Job(id=jid, name=jid, model_id="nanopore", mode="clone_validation", status="completed",
                            params={"ont_workflow_id": "wf_clone_validation"},
                            output_dir=str(root / "wrong-parent"), child_output_dir=str(output),
                            provenance={alignment_access.PROVENANCE_DIGEST_KEY: alignment_access.token_sha256(token)}))
        await session.commit()
    app = FastAPI()
    app.add_exception_handler(routes.OntNgsRouteError, routes.ont_ngs_route_error_handler)
    app.include_router(routes.router, prefix="/api")
    app.include_router(files.router, prefix="/api/files")

    async def sessions():
        async with factory() as session:
            yield session

    async def unused_domain():
        yield None

    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[routes.get_molbio_ngs_session] = unused_domain
    app.dependency_overrides[routes.get_experiment_session] = unused_domain
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        client.cookies.set(alignment_access.cookie_name("clone-native"), token)
        client.cookies.set(alignment_access.cookie_name("other-native"), token)
        yield client, output, factory
    await engine.dispose()


def publish(output, relative, payload):
    path = output / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


@pytest.mark.asyncio
async def test_native_clone_catalog_and_exact_bytes_without_qc_manifest(native_http):
    client, output, _ = native_http
    fasta = publish(output, "assembly/wf_clone_out/sample.final.fasta", b">native\nACGT\n")
    report = publish(output, "assembly/wf_clone_out/wf-clone-validation-report.html", b"<html>native report</html>")
    response = await client.get("/api/jobs/clone-native/ngs-artifacts")
    assert response.status_code == 200, response.text
    catalog = response.json()
    sessions = await client.get("/api/jobs/clone-native/alignment-sessions")
    assert sessions.status_code == 200 and sessions.json()["sessions"] == []
    assert catalog["igv"] == []  # Never mislabel clone assembly as the BAM reference.
    by_name = {a["filename"]: a for a in catalog["artifacts"]}
    assert set(by_name) == {fasta.name, report.name}
    for path in (fasta, report):
        artifact = by_name[path.name]
        assert not {"relative_path", "_path"}.intersection(artifact)
        assert artifact["source"] == "assembly" and artifact["state"] == "present"
        assert artifact["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        download = await client.get(artifact["url"])
        assert download.status_code == 200 and download.content == path.read_bytes()
        head = await client.head(artifact["url"])
        assert head.status_code == 200 and not head.content
        assert int(head.headers["content-length"]) == path.stat().st_size
        partial = await client.get(artifact["url"], headers={"Range": "bytes=1-4"})
        assert partial.status_code == 206 and partial.content == path.read_bytes()[1:5]
        cached = await client.get(artifact["url"], headers={"If-None-Match": download.headers["etag"]})
        assert cached.status_code == 304
        assert (await client.get(artifact["url"].replace("clone-native", "other-native"))).status_code == 404
    assert (await client.get("/api/files/download/bms_results/collector/native/assembly/wf_clone_out/sample.final.fasta")).status_code == 403
    assert "sandbox" in (await client.get(by_name[report.name]["url"])).headers["content-security-policy"]
    client.cookies.clear()
    assert (await client.get("/api/jobs/clone-native/ngs-artifacts")).status_code == 403
    assert (await client.get(by_name[fasta.name]["url"])).status_code == 403


@pytest.mark.asyncio
async def test_native_identity_changes_and_symlinks_do_not_expose_bytes(native_http):
    client, output, _ = native_http
    path = publish(output, "methylation/modkit_summary.tsv", b"first bytes")
    old = (await client.get("/api/jobs/clone-native/ngs-artifacts")).json()["artifacts"][0]
    path.write_bytes(b"other bytes")
    assert (await client.get(old["url"])).status_code == 404
    new = (await client.get("/api/jobs/clone-native/ngs-artifacts")).json()["artifacts"][0]
    assert new["artifact_id"] != old["artifact_id"]
    outside = output.parent / "secret.tsv"
    outside.write_bytes(b"not a published native result")
    path.unlink()
    path.symlink_to(outside)
    (output / "assembly").symlink_to(output.parent, target_is_directory=True)
    assert (await client.get(new["url"])).status_code == 404
    assert (await client.get("/api/jobs/clone-native/ngs-artifacts")).json()["artifacts"] == []


@pytest.mark.asyncio
async def test_native_methylation_multimer_and_pooled_igv_urls(native_http):
    client, output, factory = native_http
    # Transport fixture only: native science is qualified with retained outputs separately.
    payloads = {
        "align/aligned.bam": b"native-bam-placeholder",
        "align/aligned.bam.bai": b"native-bai-placeholder",
        "align/reference.fasta": b">r\nACGT\n",
        "align/reference.fasta.fai": b"r\t4\t3\t4\t5\n",
        "methylation/methylation.bed": b"r\t0\t1\tm\t1\t+\n",
        "methylation/modkit_summary.tsv": b"base\tcount\nC\t1\n",
        "multimer_qc/dimer_analysis_summary.tsv": b"metric\tvalue\nreads\t1\n",
        "pooled_reference_assignment/assignment_summary.json": b"{}",
        "pooled_reference_assignment/pooled_assignment.bam": b"pooled-bam-placeholder",
        "pooled_reference_assignment/pooled_assignment.bam.bai": b"pooled-bai-placeholder",
        "pooled_reference_assignment/combined_intended_reference.fasta": b">r\nACGT\n",
        "pooled_reference_assignment/combined_intended_reference.fasta.fai": b"r\t4\t3\t4\t5\n",
    }
    for relative, content in payloads.items():
        publish(output, relative, content)
    async with factory() as session:
        job = await session.get(Job, "clone-native")
        job.mode = "pooled_reference_assignment"
        job.params = {}  # Historical native pooled identity needs no FASTQ-QC fields.
        await session.commit()
    response = await client.get("/api/jobs/clone-native/ngs-artifacts")
    assert response.status_code == 200, response.text
    catalog = response.json()
    assert len(catalog["artifacts"]) == len(payloads)
    assert {config["name"] for config in catalog["igv"]} == {"primary", "intended_pool"}
    artifacts = {a["url"]: a for a in catalog["artifacts"]}
    for config in catalog["igv"]:
        urls = list(config["reference"].values())
        for track in config["tracks"]:
            urls.extend(track[key] for key in ("url", "indexURL") if key in track)
        for url in urls:
            assert url in artifacts
            response = await client.get(url)
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == artifacts[url]["sha256"]


@pytest.mark.asyncio
async def test_native_catalog_keeps_persisted_root_confinement(native_http):
    client, output, factory = native_http
    outside = output.parents[2] / "outside-results"
    publish(outside, "assembly/not-authorized.fasta", b">secret\nACGT\n")
    async with factory() as session:
        job = await session.get(Job, "clone-native")
        job.child_output_dir = str(outside)
        await session.commit()
    response = await client.get("/api/jobs/clone-native/ngs-artifacts")
    assert response.status_code == 409
    assert response.json()["code"] == "NGS_AUTHORITY_CONFLICT"


def test_supplementary_native_artifacts_preserve_persisted_canonical_inventory(tmp_path):
    output = tmp_path / "job"
    publish(output, "methylation/modkit_summary.tsv", b"native optional output")
    canonical = {"relative_path": "fastq_qc/consensus.fasta", "source": "sequence_qc", "kind": "consensus"}
    result = service.build_ngs_package_artifacts(
        "job", results_dir=tmp_path, source_reference_sha256="a" * 64,
        published_artifacts=[canonical], include_native_outputs=True,
    )
    assert result[0] == canonical
    assert result[1]["filename"] == "modkit_summary.tsv"
