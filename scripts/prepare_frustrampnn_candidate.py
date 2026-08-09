#!/usr/bin/env python3
"""Materialize one immutable PDB source and its canonical component request."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.frustrampnn.configuration import (  # noqa: E402
    execution_configuration,
    request_parameters,
)
from services.frustrampnn.contracts import (  # noqa: E402
    canonical_json_bytes,
    canonical_json_loads,
    canonical_sha256,
    validate_schema,
)
from services.frustrampnn.identity import deterministic_candidate_id  # noqa: E402
from services.frustrampnn.settings import (  # noqa: E402
    requested_settings_sha256,
    resolve_effective_settings,
    validate_persisted_requested_settings,
)
from services.frustrampnn.structure import (  # noqa: E402
    derive_mmcif_atom_site_authority,
    normalize_structure,
)


def _canonical_relative_key(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{field} is invalid")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.as_posix() != value or any(
        part in {"", ".", ".."} for part in parsed.parts
    ):
        raise ValueError(f"{field} is noncanonical")
    return value


def producer_identity_sha256(metadata: dict[str, Any]) -> str:
    """Return a representation-neutral digest for one typed producer candidate."""
    required = {
        "producer_method",
        "producer_sample",
        "producer_rank",
        "producer_output_key",
    }
    if set(metadata) != required:
        raise ValueError("producer identity fields are not exact")
    method = metadata["producer_method"]
    sample = metadata["producer_sample"]
    rank = metadata["producer_rank"]
    output_key = metadata["producer_output_key"]
    if not isinstance(method, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", method):
        raise ValueError("producer_method is invalid")
    if sample is not None and (not isinstance(sample, str) or not sample):
        raise ValueError("producer_sample must be a non-empty string or null")
    if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or rank < 0):
        raise ValueError("producer_rank must be a non-negative integer or null")
    output_key = _canonical_relative_key(output_key, field="producer_output_key")
    parsed_key = PurePosixPath(output_key)
    neutral_key = (
        parsed_key.with_suffix("").as_posix()
        if parsed_key.suffix.lower() in {".pdb", ".ent", ".cif", ".mmcif"}
        else output_key
    )
    domain = {
        "producer_method": method,
        "producer_sample": sample,
        "producer_rank": rank,
        "producer_output_key": neutral_key,
    }
    encoded = json.dumps(domain, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_format_authority(source: Path, claimed_format: str) -> str:
    suffix = source.suffix.lower()
    extension_format = (
        "pdb"
        if suffix in {".pdb", ".ent"}
        else "mmcif"
        if suffix in {".cif", ".mmcif"}
        else None
    )
    if extension_format is None or extension_format != claimed_format:
        raise ValueError("source_format disagrees with source extension authority")
    source_bytes = source.read_bytes()
    if claimed_format == "mmcif":
        try:
            derive_mmcif_atom_site_authority(source_bytes)
        except Exception as exc:
            raise ValueError("source_format disagrees with actual source content") from exc
    elif not any(
        line.startswith((b"ATOM  ", b"HETATM")) for line in source_bytes.splitlines()
    ):
        raise ValueError("source_format disagrees with actual source content")
    return hashlib.sha256(source_bytes).hexdigest()


def _verify_producer_metadata(payload: dict[str, Any], source: Path) -> None:
    identity_fields = {
        key: payload[key]
        for key in (
            "producer_method",
            "producer_sample",
            "producer_rank",
            "producer_output_key",
        )
    }
    expected_identity = producer_identity_sha256(identity_fields)
    claimed_identity = payload["producer_identity_sha256"]
    if claimed_identity != expected_identity:
        raise ValueError("producer_identity_sha256 does not match producer identity")
    artifact_sha = payload["producer_artifact_sha256"]
    if not isinstance(artifact_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_sha):
        raise ValueError("producer_artifact_sha256 must be lowercase SHA-256")
    source_format = payload["source_format"]
    if source_format not in {"pdb", "mmcif"}:
        raise ValueError("source_format must be pdb or mmcif")
    expected_suffixes = {
        "pdb": {".pdb", ".ent"},
        "mmcif": {".cif", ".mmcif"},
    }
    output_key = str(identity_fields["producer_output_key"])
    if PurePosixPath(output_key).suffix.lower() not in expected_suffixes[source_format]:
        raise ValueError("producer_output_key suffix disagrees with source_format")
    physical_sha = _source_format_authority(source, source_format)
    if artifact_sha != physical_sha:
        raise ValueError("producer_artifact_sha256 does not match physical source bytes")


def _decode_metadata(
    encoded: str,
    *,
    source: Path | None = None,
    request_version: int = 1,
) -> dict[str, Any]:
    try:
        payload = json.loads(base64.b64decode(encoded, validate=True))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("candidate metadata is not canonical base64 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("candidate metadata must be an object")
    common_required = {
        "parent_job_id",
        "parent_workflow_id",
        "producer_stage",
        "producer_candidate_key",
        "requiredness",
    }
    if request_version not in {1, 2}:
        raise ValueError("request_version must be 1 or 2")
    required = common_required | ({"checkpoint_id"} if request_version == 1 else set())
    producer_fields = {
        "producer_method",
        "producer_sample",
        "producer_rank",
        "producer_output_key",
        "producer_identity_sha256",
        "producer_artifact_sha256",
        "source_format",
    }
    supplied = set(payload)
    accepted = {
        frozenset(required),
        frozenset(required | {"candidate_id"}),
        frozenset(required | producer_fields),
        frozenset(required | producer_fields | {"candidate_id"}),
    }
    if supplied not in accepted or any(payload[key] in (None, "") for key in required):
        raise ValueError("candidate metadata fields are not exact")
    if payload["requiredness"] != "required":
        raise ValueError("requiredness must be required")
    _canonical_relative_key(payload["producer_candidate_key"], field="producer_candidate_key")
    if producer_fields <= supplied:
        if source is None:
            raise ValueError("physical source is required for producer provenance verification")
        _verify_producer_metadata(payload, source)
    expected = deterministic_candidate_id(
        parent_job_id=str(payload["parent_job_id"]),
        parent_workflow_id=str(payload["parent_workflow_id"]),
        producer_stage=str(payload["producer_stage"]),
        producer_candidate_key=str(payload["producer_candidate_key"]),
    )
    if "candidate_id" in payload and payload["candidate_id"] != expected:
        raise ValueError("candidate_id does not match deterministic parent identity")
    payload["candidate_id"] = expected
    return payload


def _decode_v2_settings(
    payload: bytes,
    claimed_sha256: str,
    settings_value_origin: str | None,
):
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("v2 requested settings bytes are required")
    if settings_value_origin not in {"bms_default", "operator_request"}:
        raise ValueError("v2 settings value origin must be bms_default or operator_request")
    parsed = canonical_json_loads(payload)
    if not isinstance(parsed, dict) or canonical_json_bytes(parsed) != payload:
        raise ValueError("requested settings must be exact canonical JSON bytes")
    if "settings_value_origin" in parsed:
        raise ValueError("requested settings bytes must not duplicate settings value origin")
    requested = validate_persisted_requested_settings(
        {**parsed, "settings_value_origin": settings_value_origin}
    )
    if requested_settings_sha256(requested) != claimed_sha256:
        raise ValueError("requested settings SHA-256 does not bind settings value origin")
    return requested


def _source_identity_authority(source: Path, source_bytes: bytes) -> tuple[dict[str, Any], str]:
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    suffix = source.suffix.lower()
    if suffix in {".cif", ".mmcif"}:
        return derive_mmcif_atom_site_authority(source_bytes), "mmcif_atom_site"
    if suffix in {".pdb", ".ent"}:
        return {
            "kind": "pdb_self_identity_v1",
            "identity_domain": "candidate_local",
            "authority_artifact_sha256": source_sha,
        }, "pdb_coordinates"
    raise ValueError("candidate source must be PDB or mmCIF")


def _prepare_candidate_v2(
    *,
    source: Path,
    output_pdb: Path,
    structure_map_path: Path,
    request_path: Path,
    metadata: dict[str, Any],
    settings_payload: bytes,
    settings_sha256: str,
    settings_value_origin: str,
) -> dict[str, Any]:
    requested = _decode_v2_settings(
        settings_payload,
        settings_sha256,
        settings_value_origin,
    )
    source_bytes = source.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    identity_authority, request_identity_authority = _source_identity_authority(
        source, source_bytes
    )
    candidate_id = str(metadata["candidate_id"])
    try:
        structure_map = normalize_structure(
            input_path=source,
            output_pdb_path=output_pdb,
            map_path=structure_map_path,
            target_id=str(metadata["parent_job_id"]),
            parent_job_id=str(metadata["parent_job_id"]),
            candidate_id=candidate_id,
            identity_authority=identity_authority,
            protein_selection={"mode": "all_protein_entities"},
            selected_model=requested.source_structure.selected_model_number,
            altloc_policy=(
                "blank_or_explicit:"
                + (requested.source_structure.preferred_altloc or "<blank>")
            ),
        )
        effective = resolve_effective_settings(requested, structure_map)
        configuration = execution_configuration(effective)
        normalized_sha = hashlib.sha256(output_pdb.read_bytes()).hexdigest()
        structure_map_sha = canonical_sha256(structure_map)
        if structure_map_path.read_bytes() != canonical_json_bytes(structure_map):
            raise ValueError("normalizer emitted a noncanonical structure map")
        if (
            normalized_sha != effective.resolution_identity.normalized_pdb_sha256
            or structure_map_sha != effective.resolution_identity.structure_map_sha256
        ):
            raise ValueError("normalized artifacts disagree with effective settings authority")
        requested_payload = requested.model_dump(mode="json", exclude_none=False)
        effective_payload = effective.model_dump(mode="json", exclude_none=False)
        configuration_payload = configuration.model_dump(mode="json", exclude_none=False)
        request = {
            "schema_name": "workflow_component_request",
            "schema_version": 2,
            "component_id": "frustrampnn",
            "component_contract_version": "2.0",
            "invocation_id": f"frustrampnn:{candidate_id}",
            "parent_job_id": str(metadata["parent_job_id"]),
            "parent_workflow_id": str(metadata["parent_workflow_id"]),
            "candidate_id": candidate_id,
            "source_artifact": {
                "relative_path": str(metadata["producer_candidate_key"]),
                "sha256": source_sha,
                "media_type": (
                    "chemical/x-mmcif"
                    if source.suffix.lower() in {".cif", ".mmcif"}
                    else "chemical/x-pdb"
                ),
                "producer_stage": str(metadata["producer_stage"]),
                "artifact_id": candidate_id,
            },
            "requiredness": str(metadata["requiredness"]),
            "identity_authority": request_identity_authority,
            "settings_value_origin": requested.settings_value_origin,
            "requested_settings": requested_payload,
            "requested_settings_sha256": settings_sha256,
            "effective_settings": effective_payload,
            "effective_settings_sha256": effective.effective_settings_sha256,
            "classification_policy_sha256": effective.threshold_policy_sha256,
            "capability_inventory_byte_sha256": effective.capability_inventory_byte_sha256,
            "runtime_identity_sha256": configuration.runtime_identity_sha256,
            "structure_map_sha256": structure_map_sha,
            "normalized_pdb_sha256": normalized_sha,
            "execution_configuration": configuration_payload,
            "execution_configuration_sha256": configuration.configuration_sha256,
            "requested_outputs": [
                "structure_map",
                "raw_csv",
                "landscape",
                "summary",
                "execution_receipt",
            ],
        }
        producer_fields = {
            "producer_method",
            "producer_sample",
            "producer_rank",
            "producer_output_key",
            "producer_identity_sha256",
            "producer_artifact_sha256",
            "source_format",
        }
        if producer_fields <= set(metadata):
            request["producer_provenance"] = {
                "producer_method": metadata["producer_method"],
                "producer_sample": metadata["producer_sample"],
                "producer_rank": metadata["producer_rank"],
                "producer_output_key": metadata["producer_output_key"],
                "producer_identity_sha256": metadata["producer_identity_sha256"],
                "original_source_format": metadata["source_format"],
                "original_source_sha256": source_sha,
                "source_to_normalized_binding": {
                    "kind": "sha256_pair_v1",
                    "source_sha256": source_sha,
                    "normalized_pdb_sha256": normalized_sha,
                },
            }
        validate_schema("workflow_component_request_v2", request)
        request_path.write_bytes(canonical_json_bytes(request))
        return request
    except Exception:
        request_path.unlink(missing_ok=True)
        output_pdb.unlink(missing_ok=True)
        structure_map_path.unlink(missing_ok=True)
        raise


def prepare_candidate(
    *,
    source: Path,
    output_pdb: Path,
    request_path: Path,
    metadata: dict[str, Any],
    request_version: int = 1,
    structure_map_path: Path | None = None,
    settings_payload: bytes | None = None,
    settings_sha256: str | None = None,
    settings_value_origin: str | None = None,
) -> dict[str, Any]:
    if request_version == 2:
        if settings_value_origin not in {"bms_default", "operator_request"}:
            raise ValueError(
                "v2 preparation requires a canonical settings value origin"
            )
        if structure_map_path is None or settings_payload is None or settings_sha256 is None:
            raise ValueError(
                "v2 preparation requires structure map and exact settings bytes/hash"
            )
        return _prepare_candidate_v2(
            source=source,
            output_pdb=output_pdb,
            structure_map_path=structure_map_path,
            request_path=request_path,
            metadata=metadata,
            settings_payload=settings_payload,
            settings_sha256=settings_sha256,
            settings_value_origin=settings_value_origin,
        )
    if request_version != 1:
        raise ValueError("request_version must be 1 or 2")
    producer_fields = {
        "producer_method",
        "producer_sample",
        "producer_rank",
        "producer_output_key",
        "producer_identity_sha256",
        "producer_artifact_sha256",
        "source_format",
    }
    if producer_fields <= set(metadata):
        _verify_producer_metadata(metadata, source)
    source_bytes = source.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    suffix = source.suffix.lower()
    if suffix in {".cif", ".mmcif"}:
        identity_authority = derive_mmcif_atom_site_authority(source_bytes)
    elif suffix in {".pdb", ".ent"}:
        identity_authority = {
            "kind": "pdb_self_identity_v1",
            "identity_domain": "candidate_local",
            "authority_artifact_sha256": source_sha,
        }
    else:
        raise ValueError("candidate source must be PDB or mmCIF")
    map_path = output_pdb.with_suffix(".structure-map.json")
    normalize_structure(
        input_path=source,
        output_pdb_path=output_pdb,
        map_path=map_path,
        target_id=str(metadata["candidate_id"]),
        parent_job_id=str(metadata["parent_job_id"]),
        candidate_id=str(metadata["candidate_id"]),
        identity_authority=identity_authority,
        protein_selection={"mode": "all_protein_entities"},
        selected_model=1,
        altloc_policy="blank_or_explicit:<blank>",
    )
    map_path.unlink()
    pdb_sha = hashlib.sha256(output_pdb.read_bytes()).hexdigest()
    candidate_id = str(metadata["candidate_id"])
    canonical_parameters = request_parameters()
    metadata_checkpoint = metadata.get("checkpoint_id")
    if metadata_checkpoint is not None and str(metadata_checkpoint) != canonical_parameters["checkpoint_id"]:
        raise ValueError("producer checkpoint_id disagrees with global FrustraMPNN configuration")
    request = {
        "schema_name": "workflow_component_request",
        "schema_version": 1,
        "component_id": "frustrampnn",
        "component_contract_version": "1.0",
        "invocation_id": f"frustrampnn:{candidate_id}",
        "parent_job_id": str(metadata["parent_job_id"]),
        "parent_workflow_id": str(metadata["parent_workflow_id"]),
        "candidate_id": candidate_id,
        "source_artifact": {
            "relative_path": str(metadata["producer_candidate_key"]),
            "sha256": pdb_sha,
            "media_type": "chemical/x-pdb",
            "producer_stage": str(metadata["producer_stage"]),
            "artifact_id": candidate_id,
        },
        "requiredness": str(metadata["requiredness"]),
        "identity_authority": "pdb_coordinates",
        "protein_selection": {"mode": "all_protein_entities"},
        "parameters": canonical_parameters,
        "requested_outputs": [
            "structure_map",
            "raw_csv",
            "landscape",
            "summary",
            "execution_receipt",
        ],
    }
    if producer_fields <= set(metadata):
        request["producer_provenance"] = {
            "producer_method": metadata["producer_method"],
            "producer_sample": metadata["producer_sample"],
            "producer_rank": metadata["producer_rank"],
            "producer_output_key": metadata["producer_output_key"],
            "producer_identity_sha256": metadata["producer_identity_sha256"],
            "original_source_format": metadata["source_format"],
            "original_source_sha256": source_sha,
            "source_to_normalized_binding": {
                "kind": "sha256_pair_v1",
                "source_sha256": source_sha,
                "normalized_pdb_sha256": pdb_sha,
            },
        }
    validate_schema("workflow_component_request_v1", request)
    request_path.write_bytes(canonical_json_bytes(request))
    return request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-pdb", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--metadata-base64", required=True)
    parser.add_argument("--request-version", type=int, choices=(1, 2), default=1)
    parser.add_argument("--structure-map", type=Path)
    parser.add_argument("--settings-base64")
    parser.add_argument("--settings-sha256")
    parser.add_argument("--settings-value-origin")
    args = parser.parse_args(argv)
    try:
        metadata = _decode_metadata(
            args.metadata_base64,
            source=args.source,
            request_version=args.request_version,
        )
        settings_payload = (
            base64.b64decode(args.settings_base64, validate=True)
            if args.settings_base64 is not None
            else None
        )
        prepare_candidate(
            source=args.source,
            output_pdb=args.output_pdb,
            request_path=args.request,
            metadata=metadata,
            request_version=args.request_version,
            structure_map_path=args.structure_map,
            settings_payload=settings_payload,
            settings_sha256=args.settings_sha256,
            settings_value_origin=args.settings_value_origin,
        )
    except Exception as exc:
        print(f"frustrampnn_candidate_preparation_error:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
