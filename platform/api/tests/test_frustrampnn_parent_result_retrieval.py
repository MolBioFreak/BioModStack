"""Transport/security tests; real canonical bundle, mocked scientific runtime only."""
from __future__ import annotations

import copy
import hashlib
import importlib
import io
import json
import tarfile
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from database import get_session
from routers import frustrampnn as routes
from services.frustrampnn import parent_results
from services.frustrampnn.jobs import ENVELOPE_KEY
from services.frustrampnn.manifests import validate_result_manifest
from test_frustrampnn_component_phase3 import _mock_v2_runtime, _v2_inputs
from test_frustrampnn_parent_workflow_fanout import _load_client


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    component = importlib.import_module("scripts.run_frustrampnn_component")
    request, normalized, structure_map, _ = _v2_inputs(
        tmp_path, residues=[("A", 1)], selected=[("A", 0)], request_generation=3,
    )
    _mock_v2_runtime(component, monkeypatch, tmp_path)
    child_root = tmp_path / "api-host-only"
    root = child_root / "frustrampnn" / "results" / request["candidate_id"]
    manifest = component.run_component(
        request=request, request_payload=component.canonical_json_bytes(request),
        source_structure=normalized, structure_map=structure_map, output_dir=root,
        container=tmp_path / "mock.sif", physical_gpu_id=3,
    )
    manifest_name = "frustrampnn_result_manifest_v3.json"
    result = {key: manifest[key] for key in (
        "candidate_id", "invocation_id", "request_sha256", "source_artifact_sha256",
    )}
    result.update(status="succeeded", manifest_sha256=hashlib.sha256((root / manifest_name).read_bytes()).hexdigest())
    receipt = {
        "parent_job_id": "parent-1", "results": [result],
        "candidates": [{"candidate_id": result["candidate_id"],
                        "invocation_id": result["invocation_id"],
                        "component_request_sha256": result["request_sha256"]}],
    }
    parent = SimpleNamespace(id="parent-1", status="running", queue_status="running", provenance={})
    child = SimpleNamespace(
        id=request["parent_job_id"], parent_job_id=parent.id, status="completed",
        model_id="frustrampnn", child_stage="frustrampnn", output_dir=str(child_root),
        params={ENVELOPE_KEY: {
            "source_parent_job_id": parent.id, "execution_owner_job_id": request["parent_job_id"],
            "trigger": "parent_workflow_terminal_dataset",
        }},
    )
    return parent, child, receipt, root, manifest


def test_snapshot_roundtrip_uses_validated_bytes_and_preserves_native_bundle(bundle, tmp_path):
    parent, child, receipt, root, manifest = bundle
    destination = tmp_path / "retrieved"
    destination.mkdir()
    client = _load_client()
    parent_results.check_child_lineage(parent, child)
    with parent_results.result_snapshot(child, receipt, manifest["candidate_id"]) as snapshot:
        client._unpack_result(snapshot, destination, parent_job_id=parent.id,
                              child_id=child.id, candidate_id=manifest["candidate_id"], result=receipt["results"][0])
    assert validate_result_manifest(destination, manifest) == validate_result_manifest(root, manifest)


@pytest.mark.parametrize("mutation", ["manifest_digest", "request", "invocation", "symlink", "tamper"])
def test_server_rejects_receipt_drift_and_unsafe_source(bundle, mutation):
    _parent, child, receipt, root, manifest = bundle
    receipt = copy.deepcopy(receipt)
    if mutation == "manifest_digest":
        receipt["results"][0]["manifest_sha256"] = "0" * 64
    elif mutation == "request":
        receipt["candidates"][0]["component_request_sha256"] = "0" * 64
    elif mutation == "invocation":
        receipt["results"][0]["invocation_id"] = "foreign-invocation"
    elif mutation == "symlink":
        source = root / "raw_frustrampnn.csv"
        other = root.parent / "other.csv"
        source.rename(other)
        source.symlink_to(other)
    else:
        (root / "raw_frustrampnn.csv").write_text("changed")
    with pytest.raises(ValueError):
        parent_results.result_snapshot(child, receipt, manifest["candidate_id"])


@pytest.mark.parametrize("mutation", ["parent", "envelope", "trigger", "stage", "incomplete"])
def test_server_rejects_foreign_child_lineage(bundle, mutation):
    parent, child, _receipt, _root, _manifest = bundle
    if mutation == "parent": child.parent_job_id = "foreign-parent"
    elif mutation == "envelope": child.params[ENVELOPE_KEY]["source_parent_job_id"] = "foreign-parent"
    elif mutation == "trigger": child.params[ENVELOPE_KEY]["trigger"] = "reanalyze"
    elif mutation == "stage": child.child_stage = "other"
    else: child.status = "running"
    with pytest.raises(ValueError):
        parent_results.check_child_lineage(parent, child)


@pytest.mark.asyncio
@pytest.mark.parametrize("token,parent_status,foreign,expected", [
    (None, "running", False, 403), ("wrong", "running", False, 403),
    ("capability", "completed", False, 409), ("capability", "running", True, 409),
    ("capability", "running", False, 200),
])
async def test_route_requires_running_parent_capability_and_lineage(bundle, monkeypatch, token, parent_status, foreign, expected):
    parent, child, receipt, _root, manifest = bundle
    parent.status = parent_status
    if foreign: child.parent_job_id = "foreign"
    class Session:
        async def get(self, _model, identity):
            return {parent.id: parent, child.id: child}.get(identity)
    async def session(): yield Session()
    async def child_receipt(_session, **_kwargs): return receipt
    monkeypatch.setattr(routes.stage_reporting, "token_is_authorized", lambda _provenance, value: value == "capability")
    monkeypatch.setattr(routes, "child_receipt", child_receipt)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_session] = session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as client:
        response = await client.get(
            f"/api/frustrampnn/jobs/{parent.id}/workflow-children/{child.id}/results/{manifest['candidate_id']}/bundle",
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )
    assert response.status_code == expected, response.text if expected != 200 else ""
    if expected == 200:
        assert response.headers["cache-control"] == "no-store"
        assert len(response.content) == int(response.headers["content-length"])


def _rewrite_archive(payload, change):
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        entries = [(member, archive.extractfile(member).read()) for member in archive]
    change(entries)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for member, content in entries:
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


@pytest.mark.parametrize("mutation", ["none", "lineage", "digest", "duplicate", "traversal", "symlink", "extra", "oversize", "truncated", "redirect", "transfer_limit"])
def test_remote_download_is_verified_before_publication(bundle, tmp_path, monkeypatch, mutation):
    parent, child, receipt, _root, manifest = bundle
    with parent_results.result_snapshot(child, receipt, manifest["candidate_id"]) as snapshot:
        payload = snapshot.read()
    def change(entries):
        if mutation in {"lineage", "oversize"}:
            inventory = json.loads(entries[0][1])
            if mutation == "lineage": inventory["parent_job_id"] = "foreign"
            else: inventory["files"][0]["bytes"] = 2**40
            entries[0] = (entries[0][0], json.dumps(inventory).encode())
        elif mutation == "digest": entries[-1] = (entries[-1][0], b"corrupted")
        elif mutation == "duplicate": entries.insert(2, entries[1])
        elif mutation == "traversal": entries[2][0].name = "../outside"
        elif mutation == "symlink":
            entries[2][0].type = tarfile.SYMTYPE
            entries[2][0].linkname = "/etc/passwd"
        elif mutation == "extra": entries.append((tarfile.TarInfo("extra"), b"unexpected"))
    payload = _rewrite_archive(payload, change)
    if mutation == "truncated": payload = payload[:512]
    client = _load_client()
    if mutation == "transfer_limit": monkeypatch.setattr(client, "_MAX_TRANSFER_BYTES", 100)
    class Response:
        status_code = 302 if mutation == "redirect" else 200
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def raise_for_status(self): pass
        def iter_content(self, **_kwargs): yield payload
    def get(url, **kwargs):
        assert url.startswith("https://private-ingress/base/api/frustrampnn/")
        assert kwargs["headers"] == {"Authorization": "Bearer capability"}
        assert kwargs["allow_redirects"] is False
        return Response()
    monkeypatch.setattr(client.requests, "get", get)
    destination = tmp_path / "remote-result"
    def retrieve():
        client._retrieve_result(api_url="https://private-ingress/base", capability="capability",
                                parent_job_id=parent.id, child_id=child.id,
                                candidate_id=manifest["candidate_id"], result=receipt["results"][0], destination=destination)
    if mutation == "none":
        retrieve()
        assert validate_result_manifest(destination, manifest) == validate_result_manifest(_root, manifest)
    else:
        with pytest.raises((RuntimeError, ValueError)):
            retrieve()
        assert not destination.exists()
    assert not list(tmp_path.glob(".child-result-*"))
