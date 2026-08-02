from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import (
    Base,
    Design,
    FrustraMPNNArtifact,
    Job,
    get_session,
)
from routers.frustrampnn import router
from services.frustrampnn.contracts import canonical_json_bytes
from services.frustrampnn.manifests import MANIFEST_PATH, build_result_manifest
from services.frustrampnn.persistence import ingest_result_bundle


TESTS_DIR = Path(__file__).resolve().parent


def _fixture_module():
    name = "_frustrampnn_manifest_fixture_for_results_api"
    path = TESTS_DIR / "test_frustrampnn_manifests.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MANIFEST_FIXTURE = _fixture_module()


def _bundle(
    root: Path,
    *,
    parent_job_id: str = "job-1",
    candidate_id: str = "candidate-1",
    design_id: str = "design-1",
) -> dict:
    root.mkdir(parents=True)
    MANIFEST_FIXTURE._bundle(root)
    replacements = {
        "job-1": parent_job_id,
        "candidate-1": candidate_id,
    }

    def replace_identity(value):
        if isinstance(value, dict):
            return {key: replace_identity(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_identity(item) for item in value]
        if isinstance(value, str):
            return replacements.get(value, value)
        return value

    for path in sorted(root.glob("*.json")):
        payload = replace_identity(json.loads(path.read_text(encoding="utf-8")))
        assert isinstance(payload, dict)
        if path.name == "workflow_component_request_v1.json":
            payload["source_artifact"]["artifact_id"] = design_id
        path.write_bytes(canonical_json_bytes(payload))
    MANIFEST_FIXTURE._rehash_bundle(root)
    manifest = build_result_manifest(root)
    (root / MANIFEST_PATH).write_bytes(canonical_json_bytes(manifest))
    return json.loads((root / "workflow_component_result_v1.json").read_text(encoding="utf-8"))


@pytest_asyncio.fixture
async def api(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'results-api.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    root = tmp_path / "candidate_bundle"
    terminal = _bundle(root)
    second_root = tmp_path / "candidate_bundle_job_2"
    second_terminal = _bundle(
        second_root,
        parent_job_id="job-2",
        candidate_id="candidate-2",
        design_id="design-2",
    )
    pdb = tmp_path / "design-1.pdb"
    pdb.write_bytes(MANIFEST_FIXTURE._pdb())
    second_pdb = tmp_path / "design-2.pdb"
    second_pdb.write_bytes(MANIFEST_FIXTURE._pdb())
    async with sessions() as session:
        for job_id in ("job-1", "job-2"):
            session.add(
                Job(
                    id=job_id,
                    name=job_id,
                    status="completed",
                    queue_status="completed",
                    model_id="boltz2",
                    mode="predict",
                    params={},
                    output_dir=str(tmp_path),
                )
            )
        session.add(
            Design(
                id="design-1",
                job_id="job-1",
                name="candidate-1",
                pdb_path=str(pdb),
            )
        )
        session.add(
            Design(
                id="design-2",
                job_id="job-2",
                name="candidate-2",
                pdb_path=str(second_pdb),
            )
        )
        await session.commit()
        await ingest_result_bundle(
            session,
            root,
            parent_job_id="job-1",
            terminal_envelope=terminal,
        )
        await ingest_result_bundle(
            session,
            second_root,
            parent_job_id="job-2",
            terminal_envelope=second_terminal,
        )

    app = FastAPI()
    app.include_router(router)

    async def override_session():
        async with sessions() as session:
            async def forbidden_commit():
                raise AssertionError("GET endpoint attempted a database commit")

            session.commit = forbidden_commit  # type: ignore[method-assign]
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, sessions, root
    await engine.dispose()


def _assert_no_server_path(value) -> None:
    if isinstance(value, dict):
        assert "storage_path" not in value
        for item in value.values():
            _assert_no_server_path(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_server_path(item)


@pytest.mark.asyncio
async def test_result_list_detail_and_artifact_metadata_are_job_scoped(api) -> None:
    client, _sessions, _root = api
    listed = await client.get(
        "/api/frustrampnn/jobs/job-1/results",
        params={"limit": 1, "offset": 0, "candidate_id": "candidate-1"},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["limit"] == 1 and body["offset"] == 0 and body["total"] == 1
    assert [item["invocation_id"] for item in body["items"]] == ["invoke-1"]
    assert body["items"][0]["manifest_sha256"]
    assert body["items"][0]["request_sha256"]
    assert body["items"][0]["runtime_identity"]["checkpoint_sha256"]

    filtered = await client.get(
        "/api/frustrampnn/jobs/job-1/results",
        params={"candidate_id": "not-candidate-1"},
    )
    assert filtered.status_code == 200 and filtered.json()["total"] == 0

    detail = await client.get(
        "/api/frustrampnn/results/invoke-1", params={"job_id": "job-1"}
    )
    assert detail.status_code == 200
    assert detail.json()["source_artifact_id"] == "design-1"
    mismatch = await client.get(
        "/api/frustrampnn/results/invoke-1", params={"job_id": "job-other"}
    )
    assert mismatch.status_code == 404
    second_detail = await client.get(
        "/api/frustrampnn/results/invoke-1", params={"job_id": "job-2"}
    )
    assert second_detail.status_code == 200
    assert second_detail.json()["source_artifact_id"] == "design-2"

    artifacts = await client.get(
        "/api/frustrampnn/results/invoke-1/artifacts", params={"job_id": "job-1"}
    )
    assert artifacts.status_code == 200
    assert len(artifacts.json()["items"]) == 10
    assert {item["role"] for item in artifacts.json()["items"]} >= {"raw_csv", "landscape"}
    _assert_no_server_path(body)
    _assert_no_server_path(detail.json())
    _assert_no_server_path(artifacts.json())


@pytest.mark.asyncio
async def test_landscape_pagination_is_stable_bounded_and_exactly_filtered(api) -> None:
    client, _sessions, _root = api
    first = await client.get(
        "/api/frustrampnn/results/invoke-1/landscape",
        params={"job_id": "job-1", "limit": 7, "offset": 0},
    )
    second = await client.get(
        "/api/frustrampnn/results/invoke-1/landscape",
        params={"job_id": "job-1", "limit": 7, "offset": 7},
    )
    assert first.status_code == second.status_code == 200
    first_body, second_body = first.json(), second.json()
    assert first_body["total"] == second_body["total"] == 20
    assert len(first_body["items"]) == len(second_body["items"]) == 7
    assert set(item["id"] for item in first_body["items"]).isdisjoint(
        item["id"] for item in second_body["items"]
    )
    repeated = await client.get(
        "/api/frustrampnn/results/invoke-1/landscape",
        params={"job_id": "job-1", "limit": 7, "offset": 0},
    )
    assert repeated.json()["items"] == first_body["items"]

    exact = await client.get(
        "/api/frustrampnn/results/invoke-1/landscape",
        params={"job_id": "job-1", "mutation_aa": "G"},
    )
    assert exact.status_code == 200
    assert exact.json()["total"] == 1
    assert exact.json()["items"][0]["mutation_aa"] == "G"
    absent = await client.get(
        "/api/frustrampnn/results/invoke-1/landscape",
        params={"job_id": "job-1", "auth_asym_id": "B"},
    )
    assert absent.status_code == 200 and absent.json()["total"] == 0
    oversized = await client.get(
        "/api/frustrampnn/results/invoke-1/landscape",
        params={"job_id": "job-1", "limit": 501},
    )
    assert oversized.status_code == 422


@pytest.mark.asyncio
async def test_verified_artifact_download_supports_etag_ranges_and_416(api) -> None:
    client, sessions, root = api
    async with sessions() as session:
        artifact = (
            await session.execute(
                select(FrustraMPNNArtifact).where(
                    FrustraMPNNArtifact.parent_job_id == "job-1",
                    FrustraMPNNArtifact.role == "raw_csv",
                )
            )
        ).scalar_one()
        artifact_id = artifact.artifact_id
        expected_hash = artifact.content_sha256
    expected = (root / "raw_frustrampnn.csv").read_bytes()

    full = await client.get(
        f"/api/frustrampnn/artifacts/{artifact_id}", params={"job_id": "job-1"}
    )
    assert full.status_code == 200
    assert full.content == expected
    assert hashlib.sha256(full.content).hexdigest() == expected_hash
    assert full.headers["etag"] == f'"{expected_hash}"'
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-length"] == str(len(expected))

    partial = await client.get(
        f"/api/frustrampnn/artifacts/{artifact_id}",
        params={"job_id": "job-1"},
        headers={"Range": "bytes=2-11"},
    )
    assert partial.status_code == 206
    assert partial.content == expected[2:12]
    assert partial.headers["content-range"] == f"bytes 2-11/{len(expected)}"

    unsatisfied = await client.get(
        f"/api/frustrampnn/artifacts/{artifact_id}",
        params={"job_id": "job-1"},
        headers={"Range": f"bytes={len(expected)}-"},
    )
    assert unsatisfied.status_code == 416
    assert unsatisfied.headers["content-range"] == f"bytes */{len(expected)}"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["hash", "symlink", "escape"])
async def test_download_fails_closed_for_changed_or_unsafe_artifact(api, mutation: str) -> None:
    client, sessions, root = api
    async with sessions() as session:
        artifact = (
            await session.execute(
                select(FrustraMPNNArtifact).where(
                    FrustraMPNNArtifact.parent_job_id == "job-1",
                    FrustraMPNNArtifact.role == "raw_csv",
                )
            )
        ).scalar_one()
        artifact_id = artifact.artifact_id
        if mutation == "escape":
            artifact.storage_path = str(root.parent / "outside.csv")
            await session.commit()
        else:
            target = Path(artifact.storage_path)
            if mutation == "hash":
                target.write_bytes(b"x" * artifact.size_bytes)
            else:
                replacement = root.parent / "replacement.csv"
                replacement.write_bytes(target.read_bytes())
                target.unlink()
                target.symlink_to(replacement)

    response = await client.get(
        f"/api/frustrampnn/artifacts/{artifact_id}", params={"job_id": "job-1"}
    )
    assert response.status_code == 409
