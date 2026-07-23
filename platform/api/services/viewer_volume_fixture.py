"""Publish deterministic, provenance-labelled Stage-3 viewer fixture artifacts."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import numpy as np
from Bio.PDB import MMCIFParser

from services.viewer_resource_contracts import ViewerResourceError, canonical_json_bytes
from services.viewer_resources import (
    _valid_structure_document_id,
    load_volume_inventory,
)

_NAMESPACE = "https://biomodstack.local/viewer-fixtures/1ubq-stage3/"
_LABELS = ((1, "Residues 1–25", 0x2563EB), (2, "Residues 26–50", 0x16A34A), (3, "Residues 51–76", 0xEA580C))


@dataclass(frozen=True)
class PublishedVolumeFixture:
    structure_sha256: str
    density_volume_id: str
    segmentation_volume_id: str


def _id(job_id: str, name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{_NAMESPACE}{job_id}/{name}"))


def _ccp4_bytes(values: np.ndarray, origin: np.ndarray, spacing: float) -> bytes:
    nz, ny, nx = values.shape
    header = bytearray(1024)
    struct.pack_into("<4i", header, 0, nx, ny, nz, 2)
    struct.pack_into("<3i", header, 28, nx, ny, nz)
    struct.pack_into("<3f", header, 40, nx * spacing, ny * spacing, nz * spacing)
    struct.pack_into("<3f", header, 52, 90.0, 90.0, 90.0)
    struct.pack_into("<3i", header, 64, 1, 2, 3)
    struct.pack_into("<3f", header, 76, float(values.min()), float(values.max()), float(values.mean()))
    struct.pack_into("<3f", header, 196, *map(float, origin))
    header[208:212] = b"MAP "
    header[212:216] = bytes((0x44, 0x41, 0, 0))
    struct.pack_into("<f", header, 216, float(values.std()))
    return bytes(header) + np.asarray(values, dtype="<f4").tobytes(order="C")


def _write_ccp4(path: Path, values: np.ndarray, origin: np.ndarray, spacing: float) -> tuple[str, int]:
    """Write an IEEE little-endian float32 CCP4/MRC grid with z/y/x data ordering."""
    payload = _ccp4_bytes(values, origin, spacing)
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    path.write_bytes(payload)
    return sha256(payload).hexdigest(), len(payload)


def _coordinates(path: Path) -> tuple[np.ndarray, np.ndarray]:
    structure = MMCIFParser(QUIET=True).get_structure("1ubq-stage3", str(path))
    records = [
        (np.asarray(residue["CA"].coord, dtype=np.float32), int(residue.id[1]))
        for model in structure for chain in model for residue in chain
        if residue.id[0] == " " and "CA" in residue
    ]
    coordinates, residues = zip(*records, strict=True)
    if list(residues) != list(range(1, 77)):
        raise ValueError("authoritative 1UBQ fixture must contain CA residues 1 through 76 exactly once")
    return np.stack(coordinates), np.asarray(residues, dtype=np.int16)


def _fields(coordinates: np.ndarray, residues: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    spacing = 2.0
    origin = coordinates.min(axis=0) - 5.0
    dimensions = np.ceil((coordinates.max(axis=0) + 5.0 - origin) / spacing).astype(int) + 1
    nx, ny, nz = map(int, dimensions)
    z, y, x = np.meshgrid(
        origin[2] + spacing * np.arange(nz, dtype=np.float32),
        origin[1] + spacing * np.arange(ny, dtype=np.float32),
        origin[0] + spacing * np.arange(nx, dtype=np.float32),
        indexing="ij",
    )
    density = np.zeros((nz, ny, nx), dtype=np.float32)
    nearest = np.full((nz, ny, nx), np.inf, dtype=np.float32)
    labels = np.zeros((nz, ny, nx), dtype=np.float32)
    for coordinate, residue in zip(coordinates, residues, strict=True):
        distance_sq = (x - coordinate[0]) ** 2 + (y - coordinate[1]) ** 2 + (z - coordinate[2]) ** 2
        density += np.exp(-distance_sq / 8.0)
        replace = distance_sq < nearest
        nearest[replace] = distance_sq[replace]
        labels[replace] = min(3, (int(residue) - 1) // 25 + 1)
    return density, np.where(nearest <= 16.0, labels, 0.0).astype(np.float32), origin, spacing


def _registration(registration_id: str, structure_document_id: str, structure_sha256: str, volume: dict[str, Any], transform: list[float]) -> dict[str, Any]:
    record = {
        "schema": "bms.viewer.volume-registration.v1", "registrationId": registration_id,
        "structureDocumentId": structure_document_id, "structureSha256": structure_sha256,
        "volumeId": volume["volumeId"], "volumeSha256": volume["artifactSha256"],
        "transformRowMajor4x4": transform, "method": "supplied_transform_v1",
        "provenanceRef": "viewer/fixture-provenance.json#grid-is-structure-world-coordinates",
    }
    return {**record, "artifactSha256": sha256(canonical_json_bytes(record)).hexdigest()}


def materialize_1ubq_registered_volume_fixture(*, job_id: str, output_dir: Path, structure_path: Path, structure_document_id: str) -> PublishedVolumeFixture:
    """Write job-owned synthetic fixture maps; never label them experimental or inferred."""
    if not job_id.strip() or not structure_path.is_file():
        raise ValueError("job ID and authoritative mmCIF are required")
    if not _valid_structure_document_id(structure_document_id):
        raise ValueError("a safe direct-viewer document ID is required")
    viewer = output_dir.resolve() / "viewer"
    manifest_path = viewer / "volumes.json"
    density_id, labels_id = _id(job_id, "density-volume"), _id(job_id, "segmentation-volume")
    density_artifact, labels_artifact = _id(job_id, "density-artifact"), _id(job_id, "segmentation-artifact")
    density_registration, labels_registration = _id(job_id, "density-registration"), _id(job_id, "segmentation-registration")
    existing: dict[str, list[dict[str, Any]]] = {"volumes": [], "segmentations": [], "registrations": []}
    if manifest_path.exists():
        try:
            load_volume_inventory(SimpleNamespace(id=job_id, output_dir=str(output_dir)))
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ViewerResourceError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("existing viewer volume inventory is invalid") from exc
        for key in existing:
            value = parsed.get(key, [])
            if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
                raise ValueError(f"existing viewer volume inventory {key} is invalid")
            existing[key] = value
    fixture_volume_ids = {density_id, labels_id}
    fixture_artifact_ids = {density_artifact, labels_artifact}
    fixture_segmentation_ids = {_id(job_id, "segmentation-metadata")}
    fixture_paths = {"viewer/artifacts/1ubq-fixture-density.ccp4", "viewer/artifacts/1ubq-fixture-labels.ccp4"}
    for entry in existing["volumes"]:
        if entry.get("volumeId") in fixture_volume_ids or entry.get("artifactId") in fixture_artifact_ids or entry.get("relativePath") in fixture_paths:
            raise ValueError("existing viewer artifact collides with the Stage-3 fixture paths or identities")
    for entry in existing["segmentations"]:
        if entry.get("segmentationId") in fixture_segmentation_ids or entry.get("artifactId") in fixture_artifact_ids or entry.get("relativePath") in fixture_paths:
            raise ValueError("existing viewer artifact collides with the Stage-3 fixture paths or identities")
    coordinates, residues = _coordinates(structure_path)
    density, labels, origin, spacing = _fields(coordinates, residues)
    density_hash, density_bytes = _write_ccp4(viewer / "artifacts" / "1ubq-fixture-density.ccp4", density, origin, spacing)
    labels_hash, labels_bytes = _write_ccp4(viewer / "artifacts" / "1ubq-fixture-labels.ccp4", labels, origin, spacing)
    structure_hash = sha256(structure_path.read_bytes()).hexdigest()
    transform = [spacing, 0, 0, float(origin[0]), 0, spacing, 0, float(origin[1]), 0, 0, spacing, float(origin[2]), 0, 0, 0, 1]
    dimensions = [int(density.shape[2]), int(density.shape[1]), int(density.shape[0])]
    density_volume = {
        "schemaVersion": 1, "volumeId": density_id, "artifactId": density_artifact, "artifactSha256": density_hash,
        "relativePath": "viewer/artifacts/1ubq-fixture-density.ccp4", "byteLength": density_bytes, "format": "ccp4",
        "dimensions": dimensions, "axisOrder": [0, 1, 2], "gridToWorldRowMajor4x4": transform,
        "coordinateUnits": "Å", "valueUnits": "arbitrary", "semanticKind": "density", "channelCount": 1,
        "statistics": {"min": float(density.min()), "max": float(density.max()), "mean": float(density.mean()), "sigma": float(density.std())},
        "recommendedDisplay": {"channel": 0, "contourSigma": 1.0, "opacity": 0.4}, "registrationRef": density_registration,
        "provenanceRef": "viewer/fixture-provenance.json#density",
    }
    segmentation_volume = {
        "schemaVersion": 1, "volumeId": labels_id, "artifactId": labels_artifact, "artifactSha256": labels_hash,
        "relativePath": "viewer/artifacts/1ubq-fixture-labels.ccp4", "byteLength": labels_bytes, "format": "ccp4",
        "dimensions": dimensions, "axisOrder": [0, 1, 2], "gridToWorldRowMajor4x4": transform,
        "coordinateUnits": "Å", "valueUnits": None, "semanticKind": "segmentation", "channelCount": 1,
        "statistics": None, "recommendedDisplay": None, "registrationRef": labels_registration,
        "provenanceRef": "viewer/fixture-provenance.json#segmentation",
    }
    provenance = {
        "schema": "bms.viewer.fixture-provenance.v1", "fixture": "1ubq-ca-coordinate-volume-v1",
        "structure": {"sha256": structure_hash, "documentId": structure_document_id},
        "density": "Deterministic CA-coordinate Gaussian fixture field; not experimental density.",
        "segmentation": "Supplied integer labels by authoritative 1UBQ residue ranges; not inferred biological segmentation.",
        "registration": "Identity: grids are generated directly in source structure world coordinates.",
    }
    viewer.mkdir(mode=0o750, parents=True, exist_ok=True)
    (viewer / "fixture-provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    fixture_registration_ids = {density_registration, labels_registration}
    fixture_segmentation_ids = {_id(job_id, "segmentation-metadata")}
    manifest = {
        "schema": "bms.viewer.volume-list.v1", "jobId": job_id,
        "volumes": [entry for entry in existing["volumes"] if entry.get("volumeId") not in fixture_volume_ids] + [density_volume, segmentation_volume],
        "segmentations": [entry for entry in existing["segmentations"] if entry.get("segmentationId") not in fixture_segmentation_ids] + [{
            "schema": "bms.viewer.volume-segmentation.v1", "segmentationId": _id(job_id, "segmentation-metadata"),
            "volumeId": labels_id, "artifactId": labels_artifact, "artifactSha256": labels_hash,
            "labels": [{"segmentId": i, "parentSegmentId": None, "label": label, "recommendedColor": color} for i, label, color in _LABELS],
            "provenanceRef": "viewer/fixture-provenance.json#segmentation",
        }],
        "registrations": [entry for entry in existing["registrations"] if entry.get("registrationId") not in fixture_registration_ids] + [_registration(density_registration, structure_document_id, structure_hash, density_volume, transform), _registration(labels_registration, structure_document_id, structure_hash, segmentation_volume, transform)],
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_path = tempfile.mkstemp(prefix=".volumes-", suffix=".json", dir=viewer)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(manifest_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, manifest_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return PublishedVolumeFixture(structure_hash, density_id, labels_id)
