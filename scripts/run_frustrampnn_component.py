#!/usr/bin/env python3
"""Run one canonical FrustraMPNN component invocation or runtime preflight."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import rfc8785

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.frustrampnn import runtime as _runtime  # noqa: E402
from services.frustrampnn import settings as _settings  # noqa: E402
from services.frustrampnn.analysis import (  # noqa: E402
    LandscapeValidationError,
    finalize_landscape,
    finalize_landscape_v2,
    summarize_landscape,
)
from services.frustrampnn.analytics import build_statistics_receipt  # noqa: E402
from services.frustrampnn.contracts import (  # noqa: E402
    AUTHORITY_ARTIFACT_PATH,
    ContractValidationError,
    canonical_json_bytes,
    canonical_json_loads,
    canonical_sha256,
    request_sha256,
    validate_schema,
)
from services.frustrampnn.manifests import (  # noqa: E402
    MANIFEST_PATH,
    V2_MANIFEST_PATH,
    V3_MANIFEST_PATH,
    ManifestValidationError,
    build_result_manifest,
    summarize_landscape_v2,
    summarize_landscape_v3,
    validate_external_authority_artifact,
    validate_result_manifest,
    validate_v2_input_closure,
    validate_v3_input_closure,
)
from services.frustrampnn.structure import (  # noqa: E402
    NORMALIZER_VERSION,
    StructureNormalizationError,
    normalize_structure,
)


ADAPTER_VERSION = "run_frustrampnn_component_v1"
FINALIZER_VERSION = "frustrampnn_landscape_finalizer_v1"
DEFAULT_TIMEOUT_SECONDS = 3600.0
MAX_AUTHORITY_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_RUNTIME_LOG_BYTES = 4 * 1024 * 1024
MAX_COMPONENT_REQUEST_BYTES = 16 * 1024 * 1024
MAX_SOURCE_STRUCTURE_BYTES = 64 * 1024 * 1024
MAX_STRUCTURE_MAP_BYTES = 64 * 1024 * 1024
MAX_RAW_SHARD_BYTES = 256 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024


class ComponentRunError(RuntimeError):
    """A bounded, classified component failure that must not publish a manifest."""

    def __init__(self, failure_class: str, diagnostic: str):
        bounded = " ".join(str(diagnostic).split())[:2048] or failure_class
        self.failure_class = failure_class
        self.diagnostic = bounded
        super().__init__(f"{failure_class}: {bounded}")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> bytes:
    payload = canonical_json_bytes(dict(value))
    path.write_bytes(payload)
    return payload


def _fsync_regular(path: Path) -> None:
    descriptor = _runtime.open_regular_no_follow(path, label=f"published artifact {path.name}")
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(os.fspath(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _file_generation(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_regular(path: Path | str, *, label: str, max_bytes: int) -> bytes:
    """Read a role-bounded regular file without following links or races."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("regular-file read bound must be a non-negative integer")

    descriptor = _runtime.open_regular_no_follow(path, label=label)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ComponentRunError("request_invalid", f"{label} is not a regular file")
        if metadata.st_size > max_bytes:
            raise ComponentRunError(
                "artifact_too_large",
                f"{label} size {metadata.st_size} exceeds its {max_bytes}-byte read limit",
            )
        generation = _file_generation(metadata)
        chunks: list[bytes] = []
        offset = 0
        while offset < metadata.st_size:
            count = min(_READ_CHUNK_BYTES, metadata.st_size - offset)
            chunk = os.pread(descriptor, count, offset)
            if not chunk:
                raise ComponentRunError(
                    "artifact_changed",
                    f"{label} changed size or identity during bounded read",
                )
            chunks.append(chunk)
            offset += len(chunk)
        metadata_after = os.fstat(descriptor)
        if offset != metadata.st_size or _file_generation(metadata_after) != generation:
            raise ComponentRunError(
                "artifact_changed",
                f"{label} changed size or identity during bounded read",
            )
        payload = b"".join(chunks)
        if len(payload) != metadata.st_size:
            raise ComponentRunError(
                "artifact_changed",
                f"{label} changed size or identity during bounded read",
            )
        return payload
    finally:
        os.close(descriptor)


def _load_request_payload(payload: bytes) -> dict[str, Any]:
    try:
        request = canonical_json_loads(payload)
        if not isinstance(request, dict):
            raise ContractValidationError("request must be an object")
        version = request.get("schema_version")
        if version not in {1, 2, 3}:
            raise ContractValidationError("request schema generation is unsupported")
        validate_schema(f"workflow_component_request_v{version}", request)
        if version in {2, 3} and payload != canonical_json_bytes(request):
            raise ContractValidationError(
                f"v{version} request file must be exact canonical JSON"
            )
    except Exception as exc:
        raise ComponentRunError("request_invalid", str(exc)) from exc
    return request


def _validate_request_before_runtime(
    request: Mapping[str, Any],
    *,
    runtime_identity: _runtime.FrustraMPNNRuntimeIdentity,
) -> None:
    try:
        validate_schema("workflow_component_request_v1", request)
    except Exception as exc:
        raise ComponentRunError("request_invalid", str(exc)) from exc
    if request["parameters"]["checkpoint_id"] != runtime_identity.checkpoint_id:
        raise ComponentRunError(
            "checkpoint_mismatch",
            "request checkpoint_id does not match the canonical runtime registry",
        )
    _external_authority_payload(request)


def _external_authority_payload(request: Mapping[str, Any]) -> bytes | None:
    identity_authority = request.get("identity_authority")
    external = isinstance(identity_authority, str) and identity_authority in {
        "producer_manifest", "cm_complex_snapshot",
    }
    envelope = request.get("identity_authority_artifact")
    if not external:
        if "identity_authority_artifact" in request:
            raise ComponentRunError(
                "request_invalid", "self identity authority must not carry an external artifact",
            )
        return None
    if not isinstance(envelope, Mapping):
        raise ComponentRunError("request_invalid", "external identity authority artifact is required")
    source_artifact = request.get("source_artifact")
    if not isinstance(source_artifact, Mapping) or not isinstance(source_artifact.get("sha256"), str):
        raise ComponentRunError("request_invalid", "external authority source artifact is required")
    try:
        payload = base64.b64decode(envelope["canonical_json_base64"], validate=True)
    except Exception as exc:
        raise ComponentRunError("request_invalid", "external authority base64 is invalid") from exc
    if not payload or len(payload) > MAX_AUTHORITY_ARTIFACT_BYTES:
        raise ComponentRunError("request_invalid", "external authority artifact size is invalid")
    if request.get("schema_version") == 2 and envelope.get("bytes") != len(payload):
        raise ComponentRunError("request_invalid", "external authority artifact byte count is invalid")
    declared_sha256 = envelope.get("sha256")
    if not isinstance(declared_sha256, str) or _sha256(payload) != declared_sha256:
        raise ComponentRunError("request_invalid", "external authority artifact digest mismatch")
    try:
        authority = canonical_json_loads(payload)
    except Exception as exc:
        raise ComponentRunError("request_invalid", "external authority artifact JSON is invalid") from exc
    if not isinstance(authority, dict) or canonical_json_bytes(authority) != payload:
        raise ComponentRunError("request_invalid", "external authority artifact is not canonical JSON")
    if (
        authority.get("schema_name") != "producer_manifest"
        or authority.get("schema_version") != 1
        or authority.get("source_sha256") != source_artifact["sha256"]
    ):
        raise ComponentRunError(
            "request_invalid", "external authority type or source binding is invalid",
        )
    base_fields = {"schema_name", "schema_version", "source_sha256", "entities"}
    if identity_authority == "cm_complex_snapshot":
        snapshot_digest = authority.get("cm_complex_snapshot_sha256")
        if (
            set(authority) != base_fields | {"cm_complex_snapshot_sha256"}
            or snapshot_digest != envelope.get("cm_complex_snapshot_sha256")
            or not isinstance(snapshot_digest, str)
            or len(snapshot_digest) != 64
            or any(character not in "0123456789abcdef" for character in snapshot_digest)
        ):
            raise ComponentRunError(
                "request_invalid", "CM authority snapshot digest binding is invalid",
            )
    elif set(authority) != base_fields or "cm_complex_snapshot_sha256" in envelope:
        raise ComponentRunError(
            "request_invalid", "producer authority typed fields are not exact",
        )
    return payload


def _identity_authority(request: Mapping[str, Any]) -> dict[str, Any]:
    source_hash = request["source_artifact"]["sha256"]
    authority = request["identity_authority"]
    if authority == "pdb_coordinates":
        return {
            "kind": "pdb_self_identity_v1",
            "identity_domain": "candidate_local",
            "authority_artifact_sha256": source_hash,
        }
    if authority == "mmcif_atom_site":
        selection = request["protein_selection"]
        if selection["mode"] != "explicit":
            raise ComponentRunError(
                "identity_ambiguous",
                "mmcif_atom_site authority requires explicit typed protein entities in the current request contract",
            )
        return {
            "kind": "mmcif_atom_site_v1",
            "identity_domain": "source_authoritative",
            "authority_artifact_sha256": source_hash,
            "entities": [dict(entity) for entity in selection["entities"]],
        }
    if authority in {"producer_manifest", "cm_complex_snapshot"}:
        envelope = request["identity_authority_artifact"]
        return {
            "kind": "producer_manifest_v1",
            "identity_domain": "source_authoritative",
            "authority_artifact_sha256": envelope["sha256"],
            "source_sha256": source_hash,
        }
    raise ComponentRunError("request_invalid", f"unsupported identity authority: {authority}")


def preflight_runtime(
    *,
    container: Path | str,
    apptainer: Path | str = "apptainer",
    runtime_identity: _runtime.FrustraMPNNRuntimeIdentity | None = None,
) -> dict[str, Any]:
    """Authenticate the exact SIF generation and its executable/checkpoint without inference."""

    selected_runtime_identity = (
        _runtime.FRUSTRAMPNN_RUNTIME_IDENTITY
        if runtime_identity is None
        else runtime_identity
    )
    pinned: _runtime.PinnedContainer | None = None
    try:
        configured_container = _runtime.validate_configured_container_path(
            container, identity=selected_runtime_identity
        )
        pinned = _runtime.open_verified_container(
            configured_container, selected_runtime_identity.sif_sha256
        )
        assets = _runtime.verify_container_assets(
            apptainer, pinned, identity=selected_runtime_identity
        )
        return {
            "schema_name": "frustrampnn_runtime_preflight",
            "schema_version": 1,
            "status": "ready",
            "sif_sha256": pinned.sha256,
            "executable_sha256": assets["executable_sha256"],
            "checkpoint_id": selected_runtime_identity.checkpoint_id,
            "checkpoint_sha256": assets["checkpoint_sha256"],
        }
    except _runtime.RuntimeValidationError as exc:
        diagnostic = str(exc)
        failure_class = (
            "runtime_digest_mismatch"
            if "SHA-256" in diagnostic or "digest" in diagnostic
            else "runtime_unavailable"
        )
        raise ComponentRunError(failure_class, diagnostic) from exc
    finally:
        if pinned is not None:
            pinned.close()


def _artifact_record(
    root: Path,
    relative: str,
    *,
    schema_name: str | None,
    schema_version: int | None,
    cardinality: Mapping[str, Any] | None,
    role: str | None = None,
) -> dict[str, Any]:
    payload = (root / relative).read_bytes()
    record: dict[str, Any] = {
        "relative_path": relative,
        "schema_name": schema_name,
        "schema_version": schema_version,
        "sha256": _sha256(payload),
        "bytes": len(payload),
        "cardinality": dict(cardinality) if cardinality is not None else None,
    }
    if role is not None:
        record["role"] = role
    return record


def _result_artifacts(
    root: Path,
    *,
    structure_map: Mapping[str, Any],
    landscape: Mapping[str, Any],
    has_external_authority: bool,
) -> list[dict[str, Any]]:
    mapped_count = sum(row["status"] == "mapped" for row in structure_map["rows"])
    artifacts: list[dict[str, Any]] = []
    if has_external_authority:
        artifacts.append(
            _artifact_record(
                root,
                AUTHORITY_ARTIFACT_PATH,
                schema_name="producer_manifest",
                schema_version=1,
                cardinality={"kind": "records", "count": 1},
                role="identity_authority",
            )
        )
    specs = (
        ("normalized_input.pdb", None, None, {"kind": "residues", "count": mapped_count}),
        (
            "frustrampnn_structure_map_v1.json",
            "frustrampnn_structure_map",
            1,
            {"kind": "residues", "count": len(structure_map["rows"])},
        ),
        (
            "raw_frustrampnn.csv",
            None,
            None,
            {"kind": "rows", "count": len(landscape["residues"]) * 20},
        ),
        (
            "frustrampnn_landscape_v1.json",
            "frustrampnn_landscape",
            1,
            {"kind": "residues", "count": len(landscape["residues"])},
        ),
        (
            "frustrampnn_summary_v1.json",
            "frustrampnn_summary",
            1,
            {"kind": "records", "count": 1},
        ),
        ("frustrampnn_stdout.log", None, None, None),
        ("frustrampnn_stderr.log", None, None, None),
        (
            "frustrampnn_execution_receipt_v1.json",
            "frustrampnn_execution_receipt",
            1,
            {"kind": "records", "count": 1},
        ),
    )
    artifacts.extend(
        _artifact_record(
            root,
            relative,
            schema_name=schema_name,
            schema_version=schema_version,
            cardinality=cardinality,
        )
        for relative, schema_name, schema_version, cardinality in specs
    )
    return artifacts


def _raw_failure_class(exc: LandscapeValidationError) -> str:
    diagnostic = str(exc).lower()
    if "incomplete" in diagnostic or "missing substitutions" in diagnostic:
        return "landscape_incomplete"
    if "wt disagreement" in diagnostic:
        return "wildtype_mismatch"
    if "no authoritative structure-map row" in diagnostic:
        return "position_mapping_failed"
    return "raw_output_invalid"


def _run_component_v1(
    *,
    request: Mapping[str, Any],
    source_structure: Path | str,
    output_dir: Path | str,
    container: Path | str,
    physical_gpu_id: int,
    apptainer: Path | str = "apptainer",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    runtime_identity: _runtime.FrustraMPNNRuntimeIdentity = _runtime.FRUSTRAMPNN_RUNTIME_IDENTITY,
) -> dict[str, Any]:
    """Produce and atomically publish one complete canonical candidate bundle."""

    request_value = dict(request)
    _validate_request_before_runtime(request_value, runtime_identity=runtime_identity)
    authority_payload = _external_authority_payload(request_value)
    if (
        isinstance(physical_gpu_id, bool)
        or not isinstance(physical_gpu_id, int)
        or physical_gpu_id < 0
    ):
        raise ComponentRunError(
            "gpu_admission_failed", "physical GPU ID must be a non-negative integer"
        )
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ComponentRunError("request_invalid", "timeout must be positive")

    output = Path(output_dir)
    output_parent = output.parent
    if not output_parent.is_dir():
        raise ComponentRunError("publication_failed", "output parent does not exist")
    if output.exists() or output.is_symlink():
        raise ComponentRunError("publication_failed", "output bundle already exists")

    work_root = Path(
        tempfile.mkdtemp(prefix=".frustrampnn-component-", dir=output_parent)
    )
    staging = work_root / "candidate_bundle"
    prepared = work_root / "prepared_input"
    staging.mkdir()
    prepared.mkdir()
    pinned: _runtime.PinnedContainer | None = None
    published = False
    stdout_log = staging / ".stdout.txt"
    stderr_log = staging / ".stderr.txt"
    try:
        _write_json(staging / "workflow_component_request_v1.json", request_value)
        # Runtime hardening requires the read-only normalized input bind to be
        # physically outside the writable model-output bind.
        normalized = prepared / "normalized_input.pdb"
        structure_map_path = prepared / "frustrampnn_structure_map_v1.json"
        authority_path: Path | None = None
        if authority_payload is not None:
            authority_file = prepared / AUTHORITY_ARTIFACT_PATH
            authority_file.write_bytes(authority_payload)
            authority_path = authority_file
        try:
            structure_map = normalize_structure(
                input_path=source_structure,
                output_pdb_path=normalized,
                map_path=structure_map_path,
                # The v1 request lacks a separate target identity. Candidate identity is
                # therefore the only request-bound non-inferred target token available.
                target_id=request_value["candidate_id"],
                parent_job_id=request_value["parent_job_id"],
                candidate_id=request_value["candidate_id"],
                identity_authority=_identity_authority(request_value),
                authority_artifact_path=authority_path,
                protein_selection=request_value["protein_selection"],
                selected_model=request_value["parameters"]["selected_model_number"],
                altloc_policy=request_value["parameters"]["altloc_policy"],
            )
        except ComponentRunError:
            raise
        except StructureNormalizationError as exc:
            raise ComponentRunError("normalization_failed", str(exc)) from exc
        if structure_map["source_sha256"] != request_value["source_artifact"]["sha256"]:
            raise ComponentRunError(
                "source_hash_mismatch",
                "staged source bytes do not match request source_artifact.sha256",
            )

        try:
            configured_container = _runtime.validate_configured_container_path(
                container, identity=runtime_identity
            )
            pinned = _runtime.open_verified_container(
                configured_container, runtime_identity.sif_sha256
            )
            assets = _runtime.verify_container_assets(
                apptainer, pinned, identity=runtime_identity
            )
            raw = staging / "raw_frustrampnn.csv"
            invocation = _runtime.build_frustrampnn_command(
                apptainer=apptainer,
                container=pinned.proc_path,
                normalized=normalized,
                raw=raw,
                output_root=staging,
                physical_gpu_id=physical_gpu_id,
                tool=runtime_identity.executable_path,
                checkpoint=runtime_identity.checkpoint_path,
            )
        except _runtime.RuntimeValidationError as exc:
            diagnostic = str(exc)
            failure_class = (
                "runtime_digest_mismatch"
                if "SHA-256" in diagnostic or "digest" in diagnostic
                else "runtime_unavailable"
            )
            raise ComponentRunError(failure_class, diagnostic) from exc

        started = _utc_now()
        try:
            with stdout_log.open("wb") as stdout_handle, stderr_log.open("wb") as stderr_handle:
                completed = _runtime.execute_frustrampnn(
                    invocation,
                    pinned,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    timeout=float(timeout_seconds),
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise ComponentRunError(
                "inference_timeout", f"FrustraMPNN exceeded {timeout_seconds:g} seconds"
            ) from exc
        except OSError as exc:
            raise ComponentRunError("runtime_unavailable", str(exc)) from exc
        ended = _utc_now()
        duration = (ended - started).total_seconds()
        if completed.returncode != 0:
            diagnostic = "FrustraMPNN exited nonzero"
            try:
                tail = stderr_log.read_bytes()[-1536:].decode("utf-8", errors="replace")
                if tail.strip():
                    diagnostic = f"{diagnostic}: {tail}"
            except OSError:
                pass
            raise ComponentRunError("inference_nonzero_exit", diagnostic)
        if not raw.is_file():
            raise ComponentRunError("raw_output_missing", "model produced no raw CSV")

        try:
            landscape = finalize_landscape(
                raw,
                structure_map,
                expected_normalized_pdb_sha256=structure_map["normalized_pdb_sha256"],
                expected_model_ready_sequence_sha256=structure_map[
                    "model_ready_sequence_sha256"
                ],
            )
            summary = summarize_landscape(landscape, structure_map)
        except LandscapeValidationError as exc:
            raise ComponentRunError(_raw_failure_class(exc), str(exc)) from exc
        shutil.copyfile(normalized, staging / "normalized_input.pdb")
        shutil.copyfile(
            structure_map_path, staging / "frustrampnn_structure_map_v1.json"
        )
        if authority_path is not None:
            shutil.copyfile(authority_path, staging / AUTHORITY_ARTIFACT_PATH)
        _write_json(staging / "frustrampnn_landscape_v1.json", landscape)
        _write_json(staging / "frustrampnn_summary_v1.json", summary)

        for source_log, published_name in (
            (stdout_log, "frustrampnn_stdout.log"),
            (stderr_log, "frustrampnn_stderr.log"),
        ):
            if source_log.stat().st_size > MAX_RUNTIME_LOG_BYTES:
                raise ComponentRunError(
                    "runtime_log_too_large",
                    f"{published_name} exceeds the 4 MiB result-closure limit",
                )
            shutil.copyfile(source_log, staging / published_name)
            source_log.unlink()
        argv = list(invocation.argv)
        bind_policy = [
            argv[index + 1]
            for index, token in enumerate(argv)
            if token == "--bind" and index + 1 < len(argv)
        ]
        receipt = {
            "schema_name": "frustrampnn_execution_receipt",
            "schema_version": 1,
            "invocation_id": request_value["invocation_id"],
            "argv": argv,
            "working_directory_policy": "apptainer_containall_v1",
            "bind_policy": bind_policy,
            "sif_path": str(pinned.proc_path),
            "configured_sif_path": configured_container,
            "sif_sha256": pinned.sha256,
            "executable_path": runtime_identity.executable_path,
            "executable_sha256": assets["executable_sha256"],
            "checkpoint_path": runtime_identity.checkpoint_path,
            "checkpoint_id": runtime_identity.checkpoint_id,
            "checkpoint_sha256": assets["checkpoint_sha256"],
            "input_sha256": structure_map["source_sha256"],
            "normalized_pdb_sha256": structure_map["normalized_pdb_sha256"],
            "raw_csv_sha256": landscape["raw_csv_sha256"],
            "landscape_sha256": canonical_sha256(landscape),
            "summary_sha256": canonical_sha256(summary),
            "assigned_physical_gpu_id": str(physical_gpu_id),
            "task_visible_device_index": 0,
            "exit_code": completed.returncode,
            "stdout_artifact": "frustrampnn_stdout.log",
            "stderr_artifact": "frustrampnn_stderr.log",
            "started_at": _timestamp(started),
            "ended_at": _timestamp(ended),
            "duration_seconds": duration,
            "software_versions": {
                "frustrampnn": runtime_identity.package_version,
                "adapter": ADAPTER_VERSION,
                "normalizer": NORMALIZER_VERSION,
                "finalizer": FINALIZER_VERSION,
                "source_commit": runtime_identity.source_commit,
                "python": runtime_identity.python_version,
                "pytorch": runtime_identity.pytorch_version,
                "image": runtime_identity.image_version,
            },
        }
        try:
            validate_schema("frustrampnn_execution_receipt_v1", receipt)
        except Exception as exc:
            raise ComponentRunError("manifest_invalid", str(exc)) from exc
        _write_json(staging / "frustrampnn_execution_receipt_v1.json", receipt)

        result = {
            "schema_name": "workflow_component_result",
            "schema_version": 1,
            "request_sha256": request_sha256(request_value),
            "invocation_id": request_value["invocation_id"],
            "component_id": "frustrampnn",
            "component_contract_version": request_value["component_contract_version"],
            "candidate_id": request_value["candidate_id"],
            "parent_job_id": request_value["parent_job_id"],
            "parent_workflow_id": request_value["parent_workflow_id"],
            "status": "succeeded",
            "failure_class": None,
            "diagnostic": None,
            "source_artifact": request_value["source_artifact"],
            "runtime_identity": {
                "sif_sha256": receipt["sif_sha256"],
                "executable_sha256": receipt["executable_sha256"],
                "checkpoint_id": receipt["checkpoint_id"],
                "checkpoint_sha256": receipt["checkpoint_sha256"],
            },
            "artifacts": _result_artifacts(
                staging,
                structure_map=structure_map,
                landscape=landscape,
                has_external_authority=authority_payload is not None,
            ),
            "result_payload": {
                "schema_name": "frustrampnn_summary",
                "schema_version": 1,
            },
            "started_at": receipt["started_at"],
            "ended_at": receipt["ended_at"],
            "duration_seconds": receipt["duration_seconds"],
            "assigned_gpu": {
                "physical_device_id": str(physical_gpu_id),
                "task_visible_device_index": 0,
            },
        }
        try:
            validate_schema("workflow_component_result_v1", result)
        except Exception as exc:
            raise ComponentRunError("manifest_invalid", str(exc)) from exc
        _write_json(staging / "workflow_component_result_v1.json", result)

        try:
            manifest = build_result_manifest(staging)
            _write_json(staging / MANIFEST_PATH, manifest)
            validate_result_manifest(staging, manifest)
        except (ManifestValidationError, ContractValidationError) as exc:
            raise ComponentRunError("manifest_invalid", str(exc)) from exc

        try:
            for artifact_path in sorted(staging.iterdir(), key=lambda path: path.name):
                if artifact_path.is_file() and not artifact_path.is_symlink():
                    _fsync_regular(artifact_path)
            _fsync_directory(staging)
            os.replace(staging, output)
            _fsync_directory(output_parent)
        except (OSError, _runtime.RuntimeValidationError) as exc:
            if output.is_dir() and not output.is_symlink():
                shutil.rmtree(output)
                try:
                    _fsync_directory(output_parent)
                except (OSError, _runtime.RuntimeValidationError):
                    pass
            raise ComponentRunError(
                "publication_failed", "FrustraMPNN result bundle was not durably published"
            ) from exc
        published = True
        return manifest
    finally:
        if pinned is not None:
            pinned.close()
        shutil.rmtree(work_root, ignore_errors=True)


def _raw_row_count(payload: bytes, *, label: str) -> int:
    try:
        text = payload.decode("utf-8")
        reader = csv.DictReader(text.splitlines())
        if reader.fieldnames != [
            "frustration_pred", "position", "wildtype", "mutation", "chain", "pdb",
        ]:
            raise ValueError("header is not exact")
        count = sum(1 for row in reader if None not in row and None not in row.values())
    except Exception as exc:
        raise ComponentRunError("raw_output_invalid", f"{label} CSV is invalid: {exc}") from exc
    if count <= 0:
        raise ComponentRunError("raw_output_invalid", f"{label} CSV contains no rows")
    return count


def _run_component_v2(
    *,
    request: Mapping[str, Any],
    request_payload: bytes,
    source_structure: Path | str,
    structure_map_path: Path | str,
    output_dir: Path | str,
    container: Path | str,
    physical_gpu_id: int,
    apptainer: Path | str,
    timeout_seconds: float,
    runtime_identity: _runtime.FrustraMPNNRuntimeIdentity,
) -> dict[str, Any]:
    generation = request.get("schema_version")
    if generation not in {2, 3}:
        raise ComponentRunError("request_invalid", "modern request generation is unsupported")
    if request_payload != canonical_json_bytes(dict(request)):
        raise ComponentRunError(
            "request_invalid", f"v{generation} request file is not exact canonical JSON"
        )
    try:
        authority_payload = _external_authority_payload(request)
        normalized_payload = _read_regular(
            source_structure,
            label="normalized PDB",
            max_bytes=MAX_SOURCE_STRUCTURE_BYTES,
        )
        structure_map_payload = _read_regular(
            structure_map_path,
            label="structure map",
            max_bytes=MAX_STRUCTURE_MAP_BYTES,
        )
        closure_validator = (
            validate_v3_input_closure if generation == 3 else validate_v2_input_closure
        )
        structure, effective, configuration = closure_validator(
            request, normalized_payload, structure_map_payload
        )
        validate_external_authority_artifact(request, structure, authority_payload)
    except ComponentRunError:
        raise
    except (OSError, _runtime.RuntimeValidationError, ManifestValidationError) as exc:
        raise ComponentRunError("request_invalid", str(exc)) from exc
    if _runtime.runtime_identity_dict(runtime_identity) != configuration.runtime.model_dump(
        mode="json", exclude_none=False
    ):
        raise ComponentRunError(
            "runtime_identity_mismatch",
            "v2 execution configuration runtime does not match the selected runtime identity",
        )
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ComponentRunError("request_invalid", "timeout must be positive")
    if isinstance(physical_gpu_id, bool) or not isinstance(physical_gpu_id, int) or physical_gpu_id < 0:
        raise ComponentRunError("gpu_unavailable", "physical GPU ID must be non-negative")

    output = Path(output_dir)
    output_parent = output.parent
    if output.exists() or output.is_symlink():
        raise ComponentRunError("publication_failed", "output directory already exists")
    output_parent.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix=f".{output.name}.phase3b-", dir=output_parent))
    staging = work_root / "bundle"
    staging.mkdir(mode=0o700)
    request_name = f"workflow_component_request_v{generation}.json"
    landscape_name = f"frustrampnn_landscape_v{generation}.json"
    summary_name = f"frustrampnn_summary_v{generation}.json"
    receipt_name = f"frustrampnn_execution_receipt_v{generation}.json"
    result_name = f"workflow_component_result_v{generation}.json"
    manifest_name = V3_MANIFEST_PATH if generation == 3 else V2_MANIFEST_PATH
    normalized = work_root / "normalized.pdb"
    normalized.write_bytes(normalized_payload)
    pinned: _runtime.PinnedContainer | None = None
    published = False
    try:
        _write_json(staging / request_name, request)
        if authority_payload is not None:
            (staging / AUTHORITY_ARTIFACT_PATH).write_bytes(authority_payload)
        (staging / "normalized_input.pdb").write_bytes(normalized_payload)
        (staging / "frustrampnn_structure_map_v1.json").write_bytes(structure_map_payload)
        (staging / "frustrampnn_stdout.log").write_bytes(b"")
        (staging / "frustrampnn_stderr.log").write_bytes(b"")
        try:
            plan = _runtime.compile_frustrampnn_command_plan(effective)
            configured_container = _runtime.validate_configured_container_path(
                container, identity=runtime_identity
            )
            pinned = _runtime.open_verified_container(
                configured_container, runtime_identity.sif_sha256
            )
            assets = _runtime.verify_container_assets(
                apptainer, pinned, identity=runtime_identity
            )
        except _runtime.RuntimeValidationError as exc:
            raise ComponentRunError("runtime_identity_mismatch", str(exc)) from exc
        if (
            assets["executable_sha256"] != configuration.runtime.executable_sha256
            or assets["checkpoint_sha256"] != configuration.runtime.checkpoint_sha256
        ):
            raise ComponentRunError(
                "runtime_identity_mismatch", "authenticated runtime assets disagree with v2 configuration"
            )

        commands: list[dict[str, Any]] = []
        shard_payloads: list[bytes] = []
        overall_started = _utc_now()
        with (staging / "frustrampnn_stdout.log").open("ab") as stdout_handle, (
            staging / "frustrampnn_stderr.log"
        ).open("ab") as stderr_handle:
            for entry in plan.entries:
                raw = staging / entry.shard_relative_path
                try:
                    invocation = _runtime.build_frustrampnn_command(
                        apptainer=apptainer,
                        container=pinned.proc_path,
                        normalized=normalized,
                        raw=raw,
                        output_root=staging,
                        physical_gpu_id=physical_gpu_id,
                        tool=runtime_identity.executable_path,
                        checkpoint=runtime_identity.checkpoint_path,
                        chains=entry.chains,
                        positions=entry.positions,
                    )
                    started = _utc_now()
                    completed = _runtime.execute_frustrampnn(
                        invocation,
                        pinned,
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                        timeout=float(timeout_seconds),
                        check=False,
                    )
                    ended = _utc_now()
                except subprocess.TimeoutExpired as exc:
                    raise ComponentRunError("timeout", "FrustraMPNN shard exceeded timeout") from exc
                except (OSError, _runtime.RuntimeValidationError) as exc:
                    raise ComponentRunError("inference_failed", str(exc)) from exc
                if completed.returncode != 0:
                    raise ComponentRunError(
                        "inference_failed",
                        f"FrustraMPNN shard {entry.ordinal} returned nonzero exit {completed.returncode}",
                    )
                try:
                    shard_payload = _read_regular(
                        raw,
                        label=f"shard {entry.ordinal}",
                        max_bytes=MAX_RAW_SHARD_BYTES,
                    )
                except (_runtime.RuntimeValidationError, OSError) as exc:
                    raise ComponentRunError(
                        "raw_output_invalid", f"FrustraMPNN shard {entry.ordinal} is missing"
                    ) from exc
                row_count = _raw_row_count(shard_payload, label=f"shard {entry.ordinal}")
                shard_payloads.append(shard_payload)
                commands.append({
                    **entry.canonical_payload(),
                    "argv": list(invocation.argv),
                    "argv_sha256": invocation.argv_sha256,
                    "status": "succeeded",
                    "exit_code": completed.returncode,
                    "shard_sha256": _sha256(shard_payload),
                    "shard_row_count": row_count,
                    "started_at": _timestamp(started),
                    "ended_at": _timestamp(ended),
                    "duration_seconds": max(0.0, (ended - started).total_seconds()),
                })
        for log_name in ("frustrampnn_stdout.log", "frustrampnn_stderr.log"):
            if (staging / log_name).stat().st_size > MAX_RUNTIME_LOG_BYTES:
                raise ComponentRunError(
                    "runtime_log_too_large",
                    f"{log_name} exceeds the 4 MiB result-closure limit",
                )
        overall_ended = _utc_now()

        try:
            merged_raw, landscape = finalize_landscape_v2(
                tuple(shard_payloads),
                effective,
                execution_configuration=configuration,
                target_id=structure["target_id"],
                parent_job_id=request["parent_job_id"],
                candidate_id=request["candidate_id"],
                source_artifact_sha256=request["source_artifact"]["sha256"],
            )
            summary_builder = (
                summarize_landscape_v3 if generation == 3 else summarize_landscape_v2
            )
            summary = summary_builder(landscape, effective)
        except (LandscapeValidationError, ManifestValidationError) as exc:
            raise ComponentRunError("raw_output_invalid", str(exc)) from exc
        for entry in plan.entries:
            (staging / entry.shard_relative_path).unlink()
        (staging / "raw_frustrampnn.csv").write_bytes(merged_raw)
        _write_json(staging / landscape_name, landscape)
        _write_json(staging / summary_name, summary)
        receipt = {
            "schema_name": "frustrampnn_execution_receipt",
            "schema_version": generation,
            "invocation_id": request["invocation_id"],
            "execution_configuration_sha256": request["execution_configuration_sha256"],
            "requested_settings_sha256": request["requested_settings_sha256"],
            "effective_settings_sha256": request["effective_settings_sha256"],
            "runtime_identity_sha256": request["runtime_identity_sha256"],
            "source_artifact_sha256": request["source_artifact"]["sha256"],
            "structure_map_sha256": request["structure_map_sha256"],
            "normalized_pdb_sha256": request["normalized_pdb_sha256"],
            "command_plan": {
                "entries": [entry.canonical_payload() for entry in plan.entries],
                "plan_sha256": plan.plan_sha256,
            },
            "command_count": len(commands),
            "commands": commands,
            "merged_raw_csv_sha256": _sha256(merged_raw),
            "landscape_sha256": canonical_sha256(landscape),
            "summary_sha256": canonical_sha256(summary),
            "assigned_physical_gpu_id": str(physical_gpu_id),
            "task_visible_device_index": 0,
            "stdout_artifact": "frustrampnn_stdout.log",
            "stderr_artifact": "frustrampnn_stderr.log",
            "started_at": _timestamp(overall_started),
            "ended_at": _timestamp(overall_ended),
            "duration_seconds": max(0.0, (overall_ended - overall_started).total_seconds()),
        }
        if generation == 3:
            receipt["execution_method"] = "predict"
        try:
            validate_schema(f"frustrampnn_execution_receipt_v{generation}", receipt)
        except Exception as exc:
            raise ComponentRunError("manifest_invalid", str(exc)) from exc
        _write_json(staging / receipt_name, receipt)

        if generation == 2:
            try:
                capability_inventory, inventory_sha256 = (
                    _settings.load_capability_inventory()
                )
                capability_inventory_bytes = _read_regular(
                    _settings._CAPABILITY_INVENTORY_PATH,
                    label="canonical capability inventory",
                    max_bytes=MAX_AUTHORITY_ARTIFACT_BYTES,
                )
                if (
                    _sha256(capability_inventory_bytes) != inventory_sha256
                    or inventory_sha256 != request["capability_inventory_byte_sha256"]
                ):
                    raise ContractValidationError(
                        "installed capability inventory bytes disagree with v2 request authority"
                    )
                statistics = build_statistics_receipt(
                    request=request,
                    execution_receipt=receipt,
                    landscape=landscape,
                    structure_map=structure,
                    capability_inventory=capability_inventory,
                    capability_inventory_bytes=capability_inventory_bytes,
                )
                statistics_payload = rfc8785.dumps(statistics)
                (staging / "frustrampnn_statistics_v1.json").write_bytes(
                    statistics_payload
                )
            except (OSError, ContractValidationError, rfc8785.CanonicalizationError) as exc:
                raise ComponentRunError("manifest_invalid", str(exc)) from exc

        try:
            manifest = build_result_manifest(staging)
            _write_json(staging / manifest_name, manifest)
            result = {
                "schema_name": "workflow_component_result",
                "schema_version": generation,
                "component_id": "frustrampnn",
                "component_contract_version": f"{generation}.0",
                "request_sha256": request_sha256(request),
                "invocation_id": request["invocation_id"],
                "parent_job_id": request["parent_job_id"],
                "parent_workflow_id": request["parent_workflow_id"],
                "candidate_id": request["candidate_id"],
                "status": "succeeded",
                "failure_class": None,
                "diagnostic": None,
                "result_manifest": {
                    "relative_path": manifest_name,
                    "sha256": canonical_sha256(manifest),
                },
                "result_payload": {
                    "relative_path": summary_name,
                    "schema_name": "frustrampnn_summary",
                    "schema_version": generation,
                    "sha256": canonical_sha256(summary),
                },
            }
            validate_schema(f"workflow_component_result_v{generation}", result)
            _write_json(staging / result_name, result)
            validate_result_manifest(staging, manifest)
        except (ManifestValidationError, ContractValidationError) as exc:
            raise ComponentRunError("manifest_invalid", str(exc)) from exc

        try:
            for artifact_path in sorted(staging.iterdir(), key=lambda path: path.name):
                _fsync_regular(artifact_path)
            _fsync_directory(staging)
            os.replace(staging, output)
            _fsync_directory(output_parent)
        except (OSError, _runtime.RuntimeValidationError) as exc:
            if output.is_dir() and not output.is_symlink():
                shutil.rmtree(output)
            raise ComponentRunError(
                "publication_failed",
                f"FrustraMPNN v{generation} bundle was not durably published",
            ) from exc
        published = True
        return manifest
    finally:
        if pinned is not None:
            pinned.close()
        shutil.rmtree(work_root, ignore_errors=True)


def finalize_batched_component(
    *,
    request_path: Path | str,
    source_structure: Path | str,
    structure_map: Path | str,
    raw_csv: Path | str | None,
    terminal_evidence: Mapping[str, Any],
    batch_argv: Sequence[str],
    batch_argv_sha256: str,
    stdout_log: Path | str,
    stderr_log: Path | str,
    output_dir: Path | str,
    physical_gpu_id: int,
) -> Path:
    """Finalize one v3 candidate from one real shared predict_batch execution."""
    request_payload = _read_regular(
        request_path, label="component request", max_bytes=MAX_COMPONENT_REQUEST_BYTES
    )
    request = _load_request_payload(request_payload)
    if request.get("schema_version") != 3:
        raise ComponentRunError("request_invalid", "grouped execution requires a v3 request")
    normalized_payload = _read_regular(
        source_structure, label="normalized PDB", max_bytes=MAX_SOURCE_STRUCTURE_BYTES
    )
    structure_map_payload = _read_regular(
        structure_map, label="structure map", max_bytes=MAX_STRUCTURE_MAP_BYTES
    )
    try:
        structure, effective, configuration = validate_v3_input_closure(
            request, normalized_payload, structure_map_payload
        )
    except (ManifestValidationError, OSError, _runtime.RuntimeValidationError) as exc:
        raise ComponentRunError("request_invalid", str(exc)) from exc
    if (
        terminal_evidence.get("candidate_id") != request["candidate_id"]
        or terminal_evidence.get("invocation_id") != request["invocation_id"]
        or terminal_evidence.get("status") not in {"succeeded", "failed"}
    ):
        raise ComponentRunError("request_invalid", "batch terminal evidence identity is invalid")
    output = Path(output_dir)
    if output.exists() or output.is_symlink():
        raise ComponentRunError("publication_failed", "output directory already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix=f".{output.name}.batch-", dir=output.parent))
    staging = work_root / "bundle"
    staging.mkdir(mode=0o700)
    try:
        _write_json(staging / "workflow_component_request_v3.json", request)
        (staging / "normalized_input.pdb").write_bytes(normalized_payload)
        (staging / "frustrampnn_structure_map_v1.json").write_bytes(structure_map_payload)
        shutil.copyfile(stdout_log, staging / "frustrampnn_stdout.log")
        shutil.copyfile(stderr_log, staging / "frustrampnn_stderr.log")
        if terminal_evidence["status"] == "failed":
            failure_class = terminal_evidence.get("failure_code")
            diagnostic = terminal_evidence.get("diagnostic")
            if not isinstance(failure_class, str) or not failure_class:
                raise ComponentRunError("request_invalid", "failed batch evidence lacks a failure code")
            result = {
                "schema_name": "workflow_component_result", "schema_version": 3,
                "component_id": "frustrampnn", "component_contract_version": "3.0",
                "request_sha256": request_sha256(request),
                "invocation_id": request["invocation_id"], "parent_job_id": request["parent_job_id"],
                "parent_workflow_id": request["parent_workflow_id"], "candidate_id": request["candidate_id"],
                "status": "failed", "failure_class": failure_class,
                "diagnostic": " ".join(str(diagnostic or failure_class).split())[:2048],
                "result_manifest": None, "result_payload": None,
            }
            validate_schema("workflow_component_result_v3", result)
            _write_json(staging / "workflow_component_result_v3.json", result)
        else:
            if raw_csv is None:
                raise ComponentRunError("raw_output_invalid", "successful batch evidence lacks raw CSV")
            raw_payload = _read_regular(raw_csv, label="batch raw CSV", max_bytes=MAX_RAW_SHARD_BYTES)
            if hashlib.sha256(raw_payload).hexdigest() != terminal_evidence.get("output_sha256"):
                raise ComponentRunError("raw_output_invalid", "batch raw CSV digest disagrees with evidence")
            row_count = _raw_row_count(raw_payload, label="batch raw CSV")
            if row_count != terminal_evidence.get("row_count"):
                raise ComponentRunError("raw_output_invalid", "batch raw CSV cardinality disagrees with evidence")
            try:
                merged_raw, landscape = finalize_landscape_v2(
                    (raw_payload,), effective, execution_configuration=configuration,
                    target_id=structure["target_id"], parent_job_id=request["parent_job_id"],
                    candidate_id=request["candidate_id"],
                    source_artifact_sha256=request["source_artifact"]["sha256"],
                )
                summary = summarize_landscape_v3(landscape, effective)
            except (LandscapeValidationError, ManifestValidationError) as exc:
                raise ComponentRunError("raw_output_invalid", str(exc)) from exc
            (staging / "raw_frustrampnn.csv").write_bytes(merged_raw)
            _write_json(staging / "frustrampnn_landscape_v3.json", landscape)
            _write_json(staging / "frustrampnn_summary_v3.json", summary)
            plan = _runtime.compile_frustrampnn_command_plan(effective)
            started = str(terminal_evidence["started_at"])
            ended = str(terminal_evidence["terminal_at"])
            try:
                start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(ended.replace("Z", "+00:00"))
                duration = max(0.0, (end_dt - start_dt).total_seconds())
            except ValueError as exc:
                raise ComponentRunError("request_invalid", "batch evidence timestamps are invalid") from exc
            batch_plan_entry = {
                "ordinal": 0, "chains": None, "positions": None,
                "shard_relative_path": "raw_frustrampnn_shard_0000.csv",
            }
            command = {
                **batch_plan_entry,
                "argv": list(batch_argv), "argv_sha256": batch_argv_sha256,
                "status": "succeeded", "exit_code": 0,
                "shard_sha256": _sha256(raw_payload), "shard_row_count": row_count,
                "started_at": started, "ended_at": ended, "duration_seconds": duration,
            }
            receipt = {
                "schema_name": "frustrampnn_execution_receipt", "schema_version": 3,
                "execution_method": "predict_batch",
                "invocation_id": request["invocation_id"],
                "execution_configuration_sha256": request["execution_configuration_sha256"],
                "requested_settings_sha256": request["requested_settings_sha256"],
                "effective_settings_sha256": request["effective_settings_sha256"],
                "runtime_identity_sha256": request["runtime_identity_sha256"],
                "source_artifact_sha256": request["source_artifact"]["sha256"],
                "structure_map_sha256": request["structure_map_sha256"],
                "normalized_pdb_sha256": request["normalized_pdb_sha256"],
                "command_plan": {"entries": [entry.canonical_payload() for entry in plan.entries], "plan_sha256": plan.plan_sha256},
                "command_count": 1, "commands": [command],
                "merged_raw_csv_sha256": _sha256(merged_raw),
                "landscape_sha256": canonical_sha256(landscape), "summary_sha256": canonical_sha256(summary),
                "assigned_physical_gpu_id": str(physical_gpu_id), "task_visible_device_index": 0,
                "stdout_artifact": "frustrampnn_stdout.log", "stderr_artifact": "frustrampnn_stderr.log",
                "started_at": started, "ended_at": ended, "duration_seconds": duration,
            }
            validate_schema("frustrampnn_execution_receipt_v3", receipt)
            _write_json(staging / "frustrampnn_execution_receipt_v3.json", receipt)
            manifest = build_result_manifest(staging)
            _write_json(staging / V3_MANIFEST_PATH, manifest)
            result = {
                "schema_name": "workflow_component_result", "schema_version": 3,
                "component_id": "frustrampnn", "component_contract_version": "3.0",
                "request_sha256": request_sha256(request), "invocation_id": request["invocation_id"],
                "parent_job_id": request["parent_job_id"], "parent_workflow_id": request["parent_workflow_id"],
                "candidate_id": request["candidate_id"], "status": "succeeded", "failure_class": None,
                "diagnostic": None,
                "result_manifest": {"relative_path": V3_MANIFEST_PATH, "sha256": canonical_sha256(manifest)},
                "result_payload": {"relative_path": "frustrampnn_summary_v3.json", "schema_name": "frustrampnn_summary", "schema_version": 3, "sha256": canonical_sha256(summary)},
            }
            validate_schema("workflow_component_result_v3", result)
            _write_json(staging / "workflow_component_result_v3.json", result)
            validate_result_manifest(staging, manifest)
        for artifact in staging.iterdir():
            _fsync_regular(artifact)
        _fsync_directory(staging)
        os.replace(staging, output)
        _fsync_directory(output.parent)
        return output
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def run_component(
    *,
    request: Mapping[str, Any],
    source_structure: Path | str,
    output_dir: Path | str,
    container: Path | str,
    physical_gpu_id: int,
    structure_map: Path | str | None = None,
    request_payload: bytes | None = None,
    apptainer: Path | str = "apptainer",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    runtime_identity: _runtime.FrustraMPNNRuntimeIdentity | None = None,
) -> dict[str, Any]:
    selected_runtime_identity = (
        _runtime.FRUSTRAMPNN_RUNTIME_IDENTITY
        if runtime_identity is None
        else runtime_identity
    )
    version = request.get("schema_version") if isinstance(request, Mapping) else None
    if version == 1:
        if structure_map is not None:
            raise ComponentRunError(
                "request_invalid", "v1 rejects the v2 --structure-map argument as ambiguous"
            )
        return _run_component_v1(
            request=request,
            source_structure=source_structure,
            output_dir=output_dir,
            container=container,
            physical_gpu_id=physical_gpu_id,
            apptainer=apptainer,
            timeout_seconds=timeout_seconds,
            runtime_identity=selected_runtime_identity,
        )
    if version in {2, 3}:
        if structure_map is None:
            raise ComponentRunError(
                "request_invalid", f"v{version} requires exact --structure-map input"
            )
        exact_request = (
            canonical_json_bytes(dict(request))
            if request_payload is None
            else request_payload
        )
        return _run_component_v2(
            request=request,
            request_payload=exact_request,
            source_structure=source_structure,
            structure_map_path=structure_map,
            output_dir=output_dir,
            container=container,
            physical_gpu_id=physical_gpu_id,
            apptainer=apptainer,
            timeout_seconds=timeout_seconds,
            runtime_identity=selected_runtime_identity,
        )
    raise ComponentRunError("request_invalid", "unsupported component request schema generation")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    request_group = parser.add_mutually_exclusive_group()
    request_group.add_argument("--request", type=Path)
    request_group.add_argument("--request-base64")
    parser.add_argument("--structure", type=Path)
    parser.add_argument("--structure-map", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--container", type=Path, required=True)
    parser.add_argument("--apptainer", default="apptainer")
    parser.add_argument("--physical-gpu-id", type=int)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="authenticate the SIF and internal executable/checkpoint, then exit without inference",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.preflight_only:
            if (
                args.request is not None
                or args.request_base64 is not None
                or args.structure_map is not None
            ):
                raise ComponentRunError(
                    "request_invalid", "preflight-only does not accept a component request"
                )
            result = preflight_runtime(
                container=args.container, apptainer=args.apptainer
            )
            sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")
            return 0
        if args.structure is None or args.output_dir is None or args.physical_gpu_id is None:
            raise ComponentRunError(
                "request_invalid",
                "normal execution requires --structure, --output-dir, and --physical-gpu-id",
            )
        if args.request is not None:
            payload = _read_regular(
                args.request,
                label="component request",
                max_bytes=MAX_COMPONENT_REQUEST_BYTES,
            )
        elif args.request_base64 is not None:
            try:
                payload = base64.b64decode(args.request_base64, validate=True)
            except Exception as exc:
                raise ComponentRunError("request_invalid", "request base64 is invalid") from exc
            if len(payload) > MAX_COMPONENT_REQUEST_BYTES:
                raise ComponentRunError(
                    "artifact_too_large",
                    "component request exceeds its 16 MiB read limit",
                )
        else:
            raise ComponentRunError(
                "request_invalid", "normal execution requires exactly one request input"
            )
        request = _load_request_payload(payload)
        if request["schema_version"] in {2, 3} and args.request is None:
            raise ComponentRunError(
                "request_invalid", "modern requests require an exact --request file"
            )
        run_component(
            request=request,
            source_structure=args.structure,
            structure_map=args.structure_map,
            request_payload=payload,
            output_dir=args.output_dir,
            container=args.container,
            physical_gpu_id=args.physical_gpu_id,
            apptainer=args.apptainer,
            timeout_seconds=args.timeout_seconds,
        )
        return 0
    except ComponentRunError as exc:
        print(
            f"frustrampnn_component_error:{exc.failure_class}:{exc.diagnostic}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
