"""Import retained native Protenix results, not scheduler execution receipts.

protenix-import.json is an operator-authored custody descriptor, not a run receipt.
Its paths are relative to the source directory; all listed bytes are retained.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .archive import sha256_file
from .boltz_api import BoltzImportError
from .contracts import ExternalImportPreview


class NativeSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_id: str = Field(min_length=1)
    structure_path: str
    summary_path: str
    aligned_error_path: str
    rank: int | None = None
    is_best_sample: bool | None = None


class NativeImportDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_id: Literal["bms.protenix-external-import.v1"] = Field(alias="schema")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    model_version: str | None = None
    native_input_path: str
    entities: list[dict[str, Any]] = Field(min_length=1)
    samples: list[NativeSample] = Field(min_length=1)
    settings: dict[str, Any] = Field(default_factory=dict)
    evidence_paths: list[str] = Field(default_factory=list)
    original_paths: dict[str, str] = Field(default_factory=dict)

PROVIDER_ID = "protenix_native"


def _file(root: Path, name: str) -> Path:
    relative = Path(name)
    path = root / relative
    if relative.is_absolute() or ".." in relative.parts or any(
        parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent
    ) or not path.is_file():
        raise BoltzImportError("SOURCE_NOT_ALLOWED", f"not a regular source-relative file: {name}")
    path.resolve().relative_to(root.resolve())
    return path


def _read(root: Path):
    descriptor = _file(root, "protenix-import.json")
    try:
        data = NativeImportDescriptor.model_validate_json(descriptor.read_bytes()).model_dump(by_alias=True)
    except ValidationError as exc:
        raise BoltzImportError("RUN_METADATA_INVALID", str(exc)) from exc
    samples = data.get("samples", [])
    ids = [sample["sample_id"] for sample in samples]
    if not samples or len(set(ids)) != len(ids):
        raise BoltzImportError("ARTIFACT_SET_INCOMPLETE", "distinct native samples are required")
    names = {"protenix-import.json", data["native_input_path"], *data.get("evidence_paths", [])}
    for sample in samples:
        names.update(sample[key] for key in ("structure_path", "summary_path", "aligned_error_path"))
    hashes = {name: sha256_file(_file(root, name)) for name in sorted(names)}
    return data, hashes


def preview_protenix_run(source_dir: Path) -> ExternalImportPreview:
    data, hashes = _read(source_dir)
    fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return ExternalImportPreview(
        provider=PROVIDER_ID, resource_type="native_structure_prediction",
        provider_job_id=data["id"], model="protenix", model_version=data.get("model_version"),
        status="retained_external_results", sample_count=len(data["samples"]),
        entities=data["entities"], source_fingerprint=fingerprint,
        run_metadata_sha256=hashes["protenix-import.json"], archive_sha256=None,
        importable=True, provider_metadata={"origin": "external_native_execution"},
    )


def verify_imported_publication(job, design, root, *, confidence=True, scalar_only=False) -> dict[str, Any]:
    """Read import-owned hashes; never invent scheduler or remote-run receipts."""
    from services.protenix_scientific_consumer import _snapshot, _json, derive_native_identity
    provenance = job.provenance["external_import"]
    if (job.model_id != "protenix" or job.mode != "external_import" or design.job_id != job.id
            or provenance["provider"] != PROVIDER_ID):
        raise ValueError("foreign native import")
    descriptor, raw = _snapshot(root, "normalized/import-manifest.json")
    if descriptor["sha256"] != provenance["manifest_sha256"]:
        raise ValueError("changed native import manifest")
    manifest = _json(raw)
    sample_id = design.provenance["external_import"]["sample_id"]
    matches = [s for s in manifest["samples"] if s["sample_id"] == sample_id]
    if len(matches) != 1:
        raise ValueError("missing native import sample")
    sample = matches[0]
    if Path(design.pdb_path) != root / sample["structure_path"]:
        raise ValueError("foreign native import structure")
    inventory = {a["path"]: a for a in manifest["artifacts"]}
    paths = {"structure": sample["structure_path"]}
    if confidence:
        paths["metrics"] = "source/" + sample["summary_path"]
        if not scalar_only:
            paths["pae"] = sample["aligned_error_path"]
    artifacts, snapshots = {}, {}
    for kind, path in paths.items():
        artifact, raw = _snapshot(root, path)
        expected = inventory[path]
        if artifact["sha256"] != expected["sha256"] or len(raw) != expected["size_bytes"]:
            raise ValueError("changed native import artifact")
        artifacts[kind], snapshots[kind] = artifact, raw
    selected = dict(design_id=design.id, publication_root=root, artifacts=artifacts, snapshots=snapshots,
                    block=dict(candidate_id=sample_id, document_id=sample_id), source_kind="mmcif",
                    producer_version=manifest["provider"].get("provider_version") or "unreported")
    if confidence and not scalar_only:
        selected["native"] = derive_native_identity(snapshots["structure"], snapshots["pae"], snapshots["metrics"], selected["block"])
    return selected


def normalize_protenix_run(source_dir: Path, data_root: Path, preview: ExternalImportPreview) -> dict:
    current = preview_protenix_run(source_dir)
    if current.source_fingerprint != preview.source_fingerprint:
        raise BoltzImportError("SOURCE_CHANGED_AFTER_PREVIEW", "source changed after preview")
    data, hashes = _read(source_dir)
    final = data_root / "external_imports" / PROVIDER_ID / preview.resource_type / preview.provider_job_id / preview.source_fingerprint
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        return json.loads((final / "normalized/import-manifest.json").read_text())
    staging = Path(tempfile.mkdtemp(prefix=".protenix-", dir=final.parent))
    try:
        artifacts = []
        for name, digest in hashes.items():
            target = staging / "source" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_file(source_dir, name), target)
            if sha256_file(target) != digest:
                raise BoltzImportError("SOURCE_CHANGED_AFTER_PREVIEW", name)
            artifacts.append({"path": f"source/{name}", "sha256": digest, "size_bytes": target.stat().st_size})
        samples = []
        for sample in data["samples"]:
            raw = json.loads((staging / "source" / sample["summary_path"]).read_text())
            samples.append({
                **sample, "rank": sample.get("rank"), "is_best_sample": sample.get("is_best_sample"),
                "structure_path": f"source/{sample['structure_path']}",
                "aligned_error_path": f"source/{sample['aligned_error_path']}",
                "provider_metrics": raw,
                "canonical_metrics": {"plddt_overall": raw.get("plddt"), "ptm": raw.get("ptm"), "iptm": raw.get("iptm")},
            })
        manifest = {
            "schema": "bms.external-result.v1", "schema_version": 1,
            "provider": {"id": PROVIDER_ID, "resource": preview.resource_type, "job_id": preview.provider_job_id,
                         "model": "protenix", "provider_version": preview.model_version, "status": preview.status},
            "input": {"entities": data["entities"], "num_samples": len(samples)},
            "settings": data.get("settings", {}),
            "native_input_path": f"source/{data['native_input_path']}",
            "artifacts": artifacts, "samples": samples,
            "source": {"source_fingerprint": preview.source_fingerprint, "run_metadata_sha256": preview.run_metadata_sha256,
                       "archive_sha256": None, "original_paths": data.get("original_paths", {})},
        }
        (staging / "normalized").mkdir()
        (staging / "normalized/import-manifest.json").write_text(json.dumps(manifest, sort_keys=True))
        os.replace(staging, final)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
