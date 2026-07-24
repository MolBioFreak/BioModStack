from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any

import rfc8785

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_BINDINGS = 10_000
MAX_VOLUMES = 32
FORBIDDEN_TRANSPORT_KEYS = {
    "contenturl", "content_url", "sourceurl", "source_url", "signedurl", "signed_url",
    "filesystempath", "filesystem_path", "absolutepath", "absolute_path", "token",
    "authorization", "cookie", "headers", "pluginref", "plugin_ref", "bloburl", "blob_url",
}
BINDING_KINDS = {
    "document", "trajectory", "frame_map", "volume", "metric", "mapping",
    "alignment", "segmentation", "analysis",
}


class ViewerResourceError(ValueError):
    def __init__(self, message: str, *, code: str = "VIEWER_SCHEMA_UNSUPPORTED", status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ValidatedSnapshotCreate:
    label: str
    snapshot_id: str
    snapshot_sha256: str
    snapshot: dict[str, Any]
    canonical_bytes: bytes


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as exc:
        raise ViewerResourceError("Snapshot is not RFC 8785 canonical JSON data") from exc


def _walk_transport_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).replace("-", "_").lower()
            collapsed = normalized.replace("_", "")
            if normalized in FORBIDDEN_TRANSPORT_KEYS or collapsed in FORBIDDEN_TRANSPORT_KEYS:
                raise ViewerResourceError(f"Snapshot contains transport-only field {key!r}")
            _walk_transport_keys(child)
    elif isinstance(value, list):
        for child in value:
            _walk_transport_keys(child)


def _validate_binding(binding: Any, seen: set[tuple[str, str]]) -> None:
    if not isinstance(binding, dict):
        raise ViewerResourceError("Snapshot bindings must be objects")
    kind = binding.get("kind")
    resource_id = binding.get("resourceId")
    digest = binding.get("sha256")
    required = binding.get("required")
    capability = binding.get("capabilityId")
    if kind not in BINDING_KINDS or not isinstance(resource_id, str) or not resource_id.strip():
        raise ViewerResourceError("Snapshot binding identity is invalid")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise ViewerResourceError("Snapshot binding SHA-256 is invalid")
    if not isinstance(required, bool) or capability is not None and (not isinstance(capability, str) or not capability.strip()):
        raise ViewerResourceError("Snapshot binding required/capability state is invalid")
    identity = (kind, resource_id)
    if identity in seen:
        raise ViewerResourceError(f"Duplicate snapshot binding {kind}:{resource_id}")
    seen.add(identity)


def validate_snapshot_create(payload: Any) -> ValidatedSnapshotCreate:
    if not isinstance(payload, dict) or payload.get("schema") != "bms.viewer.snapshot-create.v2":
        raise ViewerResourceError("Unsupported snapshot create schema")
    label = payload.get("label")
    snapshot = payload.get("snapshot")
    supplied_hash = payload.get("snapshotSha256")
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 120:
        raise ViewerResourceError("Snapshot label must contain 1 to 120 characters")
    if not isinstance(snapshot, dict) or snapshot.get("schema") != "bms.viewer.snapshot.v2" or snapshot.get("schemaVersion") != 2:
        raise ViewerResourceError("Unsupported snapshot schema version")
    snapshot_id = snapshot.get("snapshotId")
    if not isinstance(snapshot_id, str) or not UUID_RE.fullmatch(snapshot_id):
        raise ViewerResourceError("Snapshot ID must be a UUID")
    if not isinstance(supplied_hash, str) or not SHA256_RE.fullmatch(supplied_hash):
        raise ViewerResourceError("Snapshot SHA-256 is invalid")
    engine = snapshot.get("engine")
    if not isinstance(engine, dict) or set(engine) != {"package", "engineVersion", "adapterId", "adapterVersion"}:
        raise ViewerResourceError("Snapshot engine/adapter identity is unsupported")
    if (engine.get("package"), engine.get("engineVersion"), engine.get("adapterId")) != ("molstar", "4.5.0", "bms-direct"):
        raise ViewerResourceError("Snapshot engine/adapter identity is unsupported")
    if not isinstance(engine.get("adapterVersion"), str) or not engine["adapterVersion"].strip():
        raise ViewerResourceError("Snapshot adapter version is invalid")
    capabilities = snapshot.get("requiredCapabilities")
    if (
        not isinstance(capabilities, list)
        or any(not isinstance(value, str) or not value for value in capabilities)
        or capabilities != sorted(set(capabilities))
    ):
        raise ViewerResourceError("Snapshot capabilities must be unique and sorted")
    bindings = snapshot.get("bindings")
    volume_states = snapshot.get("volumeStates")
    if not isinstance(bindings, list) or len(bindings) > MAX_BINDINGS:
        raise ViewerResourceError("Snapshot binding count exceeds the v2 limit", code="VIEWER_REQUEST_TOO_LARGE", status_code=413)
    if not isinstance(volume_states, list) or len(volume_states) > MAX_VOLUMES:
        raise ViewerResourceError("Snapshot volume-state count exceeds the v2 limit", code="VIEWER_REQUEST_TOO_LARGE", status_code=413)
    required_outer = {"scene", "collectionState", "comparisonState", "uiComposition", "provenance", "capturedAt"}
    if any(key not in snapshot for key in required_outer) or snapshot.get("uiComposition") not in {"standard", "compact"}:
        raise ViewerResourceError("Snapshot reproducibility state is incomplete")
    _walk_transport_keys(snapshot)
    seen: set[tuple[str, str]] = set()
    for binding in bindings:
        _validate_binding(binding, seen)
    binding_map = {(binding["kind"], binding["resourceId"]): binding for binding in bindings}
    scene = snapshot.get("scene")
    documents = scene.get("documents") if isinstance(scene, dict) else None
    if not isinstance(documents, list) or not documents:
        raise ViewerResourceError("Snapshot scene documents are missing")
    for document in documents:
        if not isinstance(document, dict) or not isinstance(document.get("documentId"), str) or not isinstance(document.get("contentSha256"), str):
            raise ViewerResourceError("Snapshot document identity is invalid")
        binding = binding_map.get(("document", document["documentId"]))
        if binding is None or not binding["required"] or binding["sha256"] != document["contentSha256"]:
            raise ViewerResourceError(f"Snapshot document {document['documentId']} is not exactly hash-bound")
    for state in volume_states:
        if not isinstance(state, dict) or not isinstance(state.get("volumeId"), str):
            raise ViewerResourceError("Snapshot volume state identity is invalid")
        binding = binding_map.get(("volume", state["volumeId"]))
        if binding is None or not binding["required"]:
            raise ViewerResourceError(f"Snapshot volume {state['volumeId']} is not required/hash-bound")
        registration_ref = state.get("registrationRef")
        if registration_ref is not None:
            analysis = binding_map.get(("analysis", registration_ref))
            if analysis is None or not analysis["required"]:
                raise ViewerResourceError(f"Snapshot registration {registration_ref} is not required/hash-bound")
    canonical = canonical_json_bytes(snapshot)
    if len(canonical) > MAX_SNAPSHOT_BYTES:
        raise ViewerResourceError("Snapshot body exceeds 8 MiB", code="VIEWER_REQUEST_TOO_LARGE", status_code=413)
    actual_hash = sha256(canonical).hexdigest()
    if actual_hash != supplied_hash:
        raise ViewerResourceError("Snapshot hash mismatch", code="VIEWER_HASH_MISMATCH", status_code=412)
    return ValidatedSnapshotCreate(
        label=label.strip(), snapshot_id=snapshot_id.lower(), snapshot_sha256=actual_hash,
        snapshot=snapshot, canonical_bytes=canonical,
    )


def viewer_error_detail(error: ViewerResourceError, *, resource_id: str | None = None) -> dict[str, Any]:
    return {
        "schema": "bms.viewer.error.v1", "code": error.code, "message": str(error),
        **({"resourceId": resource_id} if resource_id else {}), "retryable": False,
    }
