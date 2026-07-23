from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Job, ViewerSnapshotRecord
from routers.viewer_resources import _principal
from services.viewer_resource_contracts import ViewerResourceError, canonical_json_bytes, validate_snapshot_create
from services.viewer_resources import (
    create_snapshot_record, delete_snapshot_record, get_snapshot_record,
    list_snapshot_records, load_volume_inventory, resolve_viewer_artifact,
)
from services.viewer_volume_fixture import materialize_1ubq_registered_volume_fixture

HASH_A = "a" * 64
DOC_ID = "11111111-1111-4111-8111-111111111111"
VOLUME_ID = "44444444-4444-4444-8444-444444444444"
ARTIFACT_ID = "55555555-5555-4555-8555-555555555555"
REGISTRATION_ID = "66666666-6666-4666-8666-666666666666"
SNAPSHOT_ID = "22222222-2222-4222-8222-222222222222"


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request({"type": "http", "method": "GET", "scheme": "http", "path": "/api/jobs/job-1/viewer/snapshots", "query_string": b"", "headers": headers, "client": ("127.0.0.1", 5173), "server": ("127.0.0.1", 8000)})


def test_viewer_routes_accept_only_authenticated_or_trusted_proxy_principals(monkeypatch):
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", "test-proxy-secret")
    trusted = _request([(b"x-bms-cm-proxy-secret", b"test-proxy-secret")])
    assert _principal(trusted) == "local-application-operator"

    untrusted = _request([])
    with pytest.raises(Exception) as raised:
        _principal(untrusted)
    assert getattr(raised.value, "status_code", None) == 401


def _snapshot() -> dict:
    return {
        "schema": "bms.viewer.snapshot.v2", "schemaVersion": 2, "snapshotId": SNAPSHOT_ID,
        "capturedAt": "2026-07-21T00:00:00.000Z",
        "engine": {"package": "molstar", "engineVersion": "4.5.0", "adapterId": "bms-direct", "adapterVersion": "bms-direct:4.5.0"},
        "requiredCapabilities": ["snapshot-v2"],
        "bindings": [{"kind": "document", "resourceId": DOC_ID, "sha256": HASH_A, "required": True}],
        "scene": {
            "schemaVersion": 1,
            "ref": {"viewerId": "viewer-1", "sceneId": "scene-1", "generation": 1},
            "documents": [{"documentId": DOC_ID, "sourceKind": "pdb", "contentSha256": HASH_A}],
            "activeDocumentId": DOC_ID,
            "provenance": {"createdBy": "test", "createdAt": "2026-07-21T00:00:00.000Z", "jobId": "job-1"},
        },
        "collectionState": None, "comparisonState": None, "volumeStates": [], "uiComposition": "standard",
        "provenance": {"createdBy": "test", "createdAt": "2026-07-21T00:00:00.000Z", "jobId": "job-1"},
    }


def _create_payload(snapshot: dict) -> dict:
    return {
        "schema": "bms.viewer.snapshot-create.v2", "label": "State A", "snapshot": snapshot,
        "snapshotSha256": hashlib.sha256(canonical_json_bytes(snapshot)).hexdigest(),
    }


def test_snapshot_create_uses_rfc8785_and_rejects_hash_transport_and_duplicate_bindings():
    snapshot = _snapshot()
    validated = validate_snapshot_create(_create_payload(snapshot))
    assert validated.snapshot_id == SNAPSHOT_ID
    assert canonical_json_bytes({"z": 1e-7, "a": -0.0}) == b'{"a":0,"z":1e-7}'
    with pytest.raises(ViewerResourceError, match="canonical JSON"):
        canonical_json_bytes({"invalid": "\ud800"})

    payload = _create_payload(snapshot)
    payload["snapshotSha256"] = "b" * 64
    with pytest.raises(ViewerResourceError, match="hash mismatch"):
        validate_snapshot_create(payload)

    transported = _snapshot()
    transported["scene"]["documents"][0]["sourceUrl"] = "https://signed.invalid/?token=secret"
    with pytest.raises(ViewerResourceError, match="transport-only"):
        validate_snapshot_create(_create_payload(transported))

    duplicate = _snapshot()
    duplicate["bindings"].append(dict(duplicate["bindings"][0]))
    with pytest.raises(ViewerResourceError, match="Duplicate"):
        validate_snapshot_create(_create_payload(duplicate))

    unbound = _snapshot()
    unbound["bindings"] = []
    with pytest.raises(ViewerResourceError, match="not exactly hash-bound"):
        validate_snapshot_create(_create_payload(unbound))


def _volume_manifest(output: Path) -> tuple[dict, Path, str]:
    viewer = output / "viewer"
    artifacts = viewer / "artifacts"
    artifacts.mkdir(parents=True)
    volume_path = artifacts / "density.map"
    volume_bytes = b"CCP4-fixture"
    volume_path.write_bytes(volume_bytes)
    digest = hashlib.sha256(volume_bytes).hexdigest()
    manifest = {
        "schema": "bms.viewer.volume-list.v1", "jobId": "job-1",
        "volumes": [{
            "schemaVersion": 1, "volumeId": VOLUME_ID, "artifactId": ARTIFACT_ID, "artifactSha256": digest,
            "relativePath": "viewer/artifacts/density.map", "byteLength": len(volume_bytes), "format": "ccp4",
            "dimensions": [2, 2, 2], "axisOrder": [0, 1, 2],
            "gridToWorldRowMajor4x4": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            "coordinateUnits": "Å", "valueUnits": "e/Å³", "semanticKind": "density", "channelCount": 1,
            "statistics": {"min": 0, "max": 1, "mean": 0.5, "sigma": 0.25},
            "recommendedDisplay": {"channel": 0, "contourAbsolute": 0.5, "opacity": 0.4},
            "registrationRef": None, "provenanceRef": "analysis:volume-1",
        }], "segmentations": [], "registrations": [],
    }
    (viewer / "volumes.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest, volume_path, digest


def test_volume_inventory_is_exact_bounded_hash_verified_and_path_contained(tmp_path: Path):
    output = tmp_path / "job-output"
    manifest, volume_path, digest = _volume_manifest(output)
    job = SimpleNamespace(id="job-1", output_dir=str(output))
    inventory = load_volume_inventory(job)
    descriptor = inventory["volumes"][0]
    assert descriptor["artifactSha256"] == digest
    assert "relativePath" not in descriptor
    artifact = resolve_viewer_artifact(job, ARTIFACT_ID, verify=True)
    assert artifact.path == volume_path.resolve()

    manifest["volumes"][0]["relativePath"] = "../outside.map"
    (output / "viewer" / "volumes.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ViewerResourceError, match="contained"):
        load_volume_inventory(job)


def test_volume_inventory_rejects_tampered_map_before_publication(tmp_path: Path):
    output = tmp_path / "job-output"
    _, volume_path, _ = _volume_manifest(output)
    volume_path.write_bytes(b"X" * volume_path.stat().st_size)

    with pytest.raises(ViewerResourceError, match="hash mismatch"):
        load_volume_inventory(SimpleNamespace(id="job-1", output_dir=str(output)))


def test_volume_registration_requires_exact_canonical_identity_and_matching_volume(tmp_path: Path):
    output = tmp_path / "job-output"
    manifest, _, digest = _volume_manifest(output)
    registration = {
        "schema": "bms.viewer.volume-registration.v1", "registrationId": REGISTRATION_ID,
        "structureDocumentId": DOC_ID, "structureSha256": HASH_A, "volumeId": VOLUME_ID,
        "volumeSha256": digest, "transformRowMajor4x4": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        "method": "supplied_transform_v1", "provenanceRef": "analysis:registration-1",
    }
    registration["artifactSha256"] = hashlib.sha256(canonical_json_bytes(registration)).hexdigest()
    manifest["volumes"][0]["registrationRef"] = REGISTRATION_ID
    manifest["registrations"] = [registration]
    (output / "viewer" / "volumes.json").write_text(json.dumps(manifest), encoding="utf-8")
    inventory = load_volume_inventory(SimpleNamespace(id="job-1", output_dir=str(output)))
    assert inventory["registrations"] == [registration]

    manifest["registrations"][0]["volumeSha256"] = "b" * 64
    (output / "viewer" / "volumes.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ViewerResourceError, match="does not match"):
        load_volume_inventory(SimpleNamespace(id="job-1", output_dir=str(output)))


def test_volume_registration_accepts_the_direct_viewer_primary_document_identity(tmp_path: Path):
    output = tmp_path / "job-output"
    manifest, _, digest = _volume_manifest(output)
    registration = {
        "schema": "bms.viewer.volume-registration.v1", "registrationId": REGISTRATION_ID,
        "structureDocumentId": "primary", "structureSha256": HASH_A, "volumeId": VOLUME_ID,
        "volumeSha256": digest, "transformRowMajor4x4": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        "method": "supplied_transform_v1", "provenanceRef": "fixture:1ubq-derived-map-registration",
    }
    registration["artifactSha256"] = hashlib.sha256(canonical_json_bytes(registration)).hexdigest()
    manifest["volumes"][0]["registrationRef"] = REGISTRATION_ID
    manifest["registrations"] = [registration]
    (output / "viewer" / "volumes.json").write_text(json.dumps(manifest), encoding="utf-8")

    inventory = load_volume_inventory(SimpleNamespace(id="job-1", output_dir=str(output)))

    assert inventory["registrations"] == [registration]


def test_1ubq_fixture_publishes_hash_bound_registered_scalar_and_label_maps(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "conformational_mapping" / "real_1ubq" / "1UBQ.protein-only-authoritative.cif"
    output = tmp_path / "completed-job"

    published = materialize_1ubq_registered_volume_fixture(
        job_id="job-1",
        output_dir=output,
        structure_path=fixture,
        structure_document_id="primary",
    )

    inventory = load_volume_inventory(SimpleNamespace(id="job-1", output_dir=str(output)))
    assert {entry["semanticKind"] for entry in inventory["volumes"]} == {"density", "segmentation"}
    assert inventory["segmentations"][0]["labels"] == [
        {"segmentId": 1, "parentSegmentId": None, "label": "Residues 1–25", "recommendedColor": 0x2563EB},
        {"segmentId": 2, "parentSegmentId": None, "label": "Residues 26–50", "recommendedColor": 0x16A34A},
        {"segmentId": 3, "parentSegmentId": None, "label": "Residues 51–76", "recommendedColor": 0xEA580C},
    ]
    assert {entry["structureDocumentId"] for entry in inventory["registrations"]} == {"primary"}
    assert all(entry["structureSha256"] == published.structure_sha256 for entry in inventory["registrations"])
    assert all(resolve_viewer_artifact(SimpleNamespace(id="job-1", output_dir=str(output)), entry["artifactId"], verify=True).size_bytes > 1024 for entry in inventory["volumes"])
    assert (output / "viewer" / "fixture-provenance.json").is_file()


def test_1ubq_fixture_preserves_existing_job_owned_volume_inventory(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "conformational_mapping" / "real_1ubq" / "1UBQ.protein-only-authoritative.cif"
    output = tmp_path / "completed-job"
    existing, _, _ = _volume_manifest(output)

    materialize_1ubq_registered_volume_fixture(
        job_id="job-1", output_dir=output, structure_path=fixture, structure_document_id="primary",
    )

    published = json.loads((output / "viewer" / "volumes.json").read_text(encoding="utf-8"))
    assert any(entry["volumeId"] == existing["volumes"][0]["volumeId"] for entry in published["volumes"])


def test_1ubq_fixture_rejects_invalid_document_identity_before_publishing(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "conformational_mapping" / "real_1ubq" / "1UBQ.protein-only-authoritative.cif"
    output = tmp_path / "completed-job"

    with pytest.raises(ValueError, match="direct-viewer document ID"):
        materialize_1ubq_registered_volume_fixture(
            job_id="job-1", output_dir=output, structure_path=fixture, structure_document_id="../not-a-document",
        )

    assert not (output / "viewer").exists()


def test_1ubq_fixture_rejects_retained_artifact_path_collision_before_writing(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "conformational_mapping" / "real_1ubq" / "1UBQ.protein-only-authoritative.cif"
    output = tmp_path / "completed-job"
    manifest, _, _ = _volume_manifest(output)
    collision = output / "viewer" / "artifacts" / "1ubq-fixture-density.ccp4"
    original = b"unrelated-owned-artifact"
    collision.write_bytes(original)
    manifest["volumes"][0].update({
        "relativePath": "viewer/artifacts/1ubq-fixture-density.ccp4",
        "artifactSha256": hashlib.sha256(original).hexdigest(),
        "byteLength": len(original),
    })
    manifest_path = output / "viewer" / "volumes.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="collides"):
        materialize_1ubq_registered_volume_fixture(
            job_id="job-1", output_dir=output, structure_path=fixture, structure_document_id="primary",
        )

    assert collision.read_bytes() == original
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest


def test_1ubq_fixture_rejects_retained_segmentation_artifact_collision_before_writing(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "conformational_mapping" / "real_1ubq" / "1UBQ.protein-only-authoritative.cif"
    output = tmp_path / "completed-job"
    manifest, _, _ = _volume_manifest(output)
    collision = output / "viewer" / "artifacts" / "1ubq-fixture-density.ccp4"
    original = b"unrelated-segmentation-artifact"
    collision.write_bytes(original)
    manifest["volumes"][0].update({"semanticKind": "segmentation", "valueUnits": None})
    manifest["segmentations"] = [{
        "schema": "bms.viewer.volume-segmentation.v1",
        "segmentationId": "11111111-1111-4111-8111-111111111111",
        "volumeId": VOLUME_ID,
        "artifactId": "22222222-2222-4222-8222-222222222222",
        "artifactSha256": hashlib.sha256(original).hexdigest(),
        "relativePath": "viewer/artifacts/1ubq-fixture-density.ccp4",
        "labels": [],
        "provenanceRef": "analysis:existing-segmentation",
    }]
    manifest_path = output / "viewer" / "volumes.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="collides"):
        materialize_1ubq_registered_volume_fixture(
            job_id="job-1", output_dir=output, structure_path=fixture, structure_document_id="primary",
        )

    assert collision.read_bytes() == original
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest


def test_1ubq_fixture_rejects_malformed_retained_inventory_before_writing(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "conformational_mapping" / "real_1ubq" / "1UBQ.protein-only-authoritative.cif"
    output = tmp_path / "completed-job"
    viewer = output / "viewer"
    viewer.mkdir(parents=True)
    manifest = {"schema": "bms.viewer.volume-list.v1", "jobId": "job-1", "volumes": [{}], "segmentations": [], "registrations": []}
    manifest_path = viewer / "volumes.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid"):
        materialize_1ubq_registered_volume_fixture(
            job_id="job-1", output_dir=output, structure_path=fixture, structure_document_id="primary",
        )

    assert not (viewer / "artifacts" / "1ubq-fixture-density.ccp4").exists()
    assert not (viewer / "fixture-provenance.json").exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest


@pytest.mark.asyncio
async def test_snapshot_records_are_immutable_job_and_principal_scoped(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'viewer.db'}")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(Job(id="job-1", name="Viewer", status="completed", model_id="boltz2", mode="predict", params={}))
        await session.commit()
        validated = validate_snapshot_create(_create_payload(_snapshot()))
        created = await create_snapshot_record(session, "job-1", validated, created_by="alice")
        assert isinstance(created, ViewerSnapshotRecord)
        with pytest.raises(ViewerResourceError, match="already exists"):
            await create_snapshot_record(session, "job-1", validated, created_by="alice")
        assert [row.id for row in await list_snapshot_records(session, "job-1", limit=100, created_by="alice")] == [SNAPSHOT_ID]
        assert await list_snapshot_records(session, "job-1", limit=100, created_by="bob") == []
        with pytest.raises(ViewerResourceError, match="not found"):
            await get_snapshot_record(session, "job-1", SNAPSHOT_ID, created_by="bob")
        await delete_snapshot_record(session, "job-1", SNAPSHOT_ID, created_by="alice")
        with pytest.raises(ViewerResourceError, match="not found"):
            await get_snapshot_record(session, "job-1", SNAPSHOT_ID, created_by="alice")
    await engine.dispose()
