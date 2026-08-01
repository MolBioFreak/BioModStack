"""CM-owned adapter from canonical snapshots to neutral FrustraMPNN authority."""

from __future__ import annotations

import copy
import hashlib
import io
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Mapping

from services.frustrampnn.analysis import THRESHOLD_POLICY
from services.frustrampnn.contracts import canonical_json_bytes
from services.frustrampnn.structure import (
    StructureNormalizationError,
    normalize_structure_bytes,
    read_structure_bytes,
)

from .contracts import (
    canonical_sha256,
    validate_schema,
    validate_structure_map_snapshot_binding,
)


class FrustraMPNNAdapterError(ValueError):
    """A CM snapshot cannot safely authorize neutral structure normalization."""


# Backward-readable CM v1 wire identity.  Its accompanying hash is always the
# hash of ``services.frustrampnn.analysis.THRESHOLD_POLICY``; this adapter owns
# no thresholds or classification semantics.
CM_THRESHOLD_POLICY_ADAPTER_ID = "frustrampnn_class_v1"


def bind_cm_candidate_snapshot_bytes(
    snapshot: Mapping[str, Any], *, candidate_id: str,
    source_bytes: bytes, source_suffix: str,
) -> dict[str, Any]:
    """Bind candidate hierarchy from the exact no-follow source generation."""

    try:
        validate_schema("cm_complex_snapshot_v1", snapshot)
        observed: dict[str, tuple[str, str]] = {}
        if source_suffix.lower() in {".cif", ".mmcif"}:
            from Bio.PDB.MMCIF2Dict import MMCIF2Dict

            cif = MMCIF2Dict(io.StringIO(source_bytes.decode("utf-8")))

            def values(name: str) -> list[str]:
                value = cif.get(name, [])
                return [str(item) for item in value] if isinstance(value, list) else [str(value)]

            auth = values("_atom_site.auth_asym_id")
            label = values("_atom_site.label_asym_id")
            entity = values("_atom_site.label_entity_id")
            if not auth or len({len(auth), len(label), len(entity)}) != 1:
                raise FrustraMPNNAdapterError(
                    "candidate mmCIF has incomplete output hierarchy identity"
                )
            for auth_id, label_id, entity_id in zip(auth, label, entity, strict=True):
                identity = (label_id, entity_id)
                previous = observed.setdefault(auth_id, identity)
                if previous != identity:
                    raise FrustraMPNNAdapterError(
                        "candidate mmCIF maps one author chain ambiguously"
                    )
        elif source_suffix.lower() == ".pdb":
            from Bio.PDB import PDBParser

            text = io.StringIO(source_bytes.decode("ascii"))
            model = next(PDBParser(QUIET=True).get_structure("candidate", text).get_models())
            observed = {str(chain.id): (str(chain.id), "") for chain in model.get_chains()}
            if not observed:
                raise FrustraMPNNAdapterError("candidate PDB has no output chains")
        else:
            raise FrustraMPNNAdapterError("candidate structure format is unsupported")

        bound = copy.deepcopy(dict(snapshot))
        entity_by_source = {item["source_entity_id"]: item for item in bound["entities"]}
        unique_mappings: list[dict[str, Any]] = []
        seen_source_keys: set[tuple[str, str]] = set()
        for mapping in bound["instance_mappings"]:
            source_key = (mapping["source_entity_id"], mapping["source_instance_id"])
            if source_key not in seen_source_keys:
                seen_source_keys.add(source_key)
                unique_mappings.append(mapping)
        bound["instance_mappings"] = unique_mappings
        for mapping in bound["instance_mappings"]:
            declared_auth = str(
                mapping.get("output_auth_asym_id") or mapping["source_instance_id"]
            )
            output = observed.get(declared_auth) or observed.get(mapping["source_instance_id"])
            source_entity = entity_by_source[mapping["source_entity_id"]]
            if output is None:
                if source_entity["entity_type"] == "protein":
                    raise FrustraMPNNAdapterError(
                        "candidate structure omits an authorized protein instance"
                    )
            else:
                mapping["output_label_asym_id"] = output[0]
                mapping["output_entity_id"] = output[1] or mapping["source_entity_id"]
                mapping["output_auth_asym_id"] = (
                    declared_auth if declared_auth in observed else mapping["source_instance_id"]
                )
            mapping["candidate_id"] = candidate_id
        bound["normalized_source_sha256"] = canonical_sha256({
            key: value for key, value in bound.items()
            if key != "normalized_source_sha256"
        })
        validate_schema("cm_complex_snapshot_v1", bound)
        return bound
    except FrustraMPNNAdapterError:
        raise
    except Exception as exc:
        raise FrustraMPNNAdapterError(
            f"cannot bind candidate output hierarchy from immutable bytes: {exc}"
        ) from exc


def derive_producer_authority(
    complex_snapshot: Mapping[str, Any],
    *,
    source_sha256: str,
    target_id: str,
    candidate_id: str,
) -> dict[str, Any]:
    """Validate a CM snapshot and derive only neutral producer output identity."""

    try:
        validate_schema("cm_complex_snapshot_v1", complex_snapshot)
    except Exception as exc:
        raise FrustraMPNNAdapterError(f"CM snapshot typed schema is invalid: {exc}") from exc
    expected_normalized = canonical_sha256({
        key: value
        for key, value in complex_snapshot.items()
        if key != "normalized_source_sha256"
    })
    if complex_snapshot["normalized_source_sha256"] != expected_normalized:
        raise FrustraMPNNAdapterError("CM snapshot normalized canonical hash mismatch")
    if complex_snapshot["target_id"] != target_id:
        raise FrustraMPNNAdapterError("CM snapshot target identity mismatch")
    if complex_snapshot["original_source_sha256"] != source_sha256:
        raise FrustraMPNNAdapterError("CM snapshot source hash binding mismatch")

    mappings: dict[tuple[str, str], Mapping[str, Any]] = {}
    for mapping in complex_snapshot["instance_mappings"]:
        if mapping["candidate_id"] != candidate_id:
            continue
        key = (mapping["source_entity_id"], mapping["source_instance_id"])
        if key in mappings:
            raise FrustraMPNNAdapterError("CM snapshot candidate protein mapping is ambiguous")
        mappings[key] = mapping

    entities: list[dict[str, Any]] = []
    output_identities: set[tuple[str, str, str]] = set()
    for source_entity in complex_snapshot["entities"]:
        if source_entity["entity_type"] != "protein":
            continue
        source_entity_id = source_entity["source_entity_id"]
        for instance_id in source_entity["ordered_instance_ids"]:
            mapping = mappings.get((source_entity_id, instance_id))
            if mapping is None:
                raise FrustraMPNNAdapterError(
                    "CM snapshot has no candidate mapping for an authorized protein instance"
                )
            output_identity = (
                mapping["output_entity_id"],
                mapping["output_label_asym_id"],
                mapping["output_auth_asym_id"],
            )
            if output_identity in output_identities:
                raise FrustraMPNNAdapterError("CM snapshot output protein identity is ambiguous")
            output_identities.add(output_identity)
            entities.append({
                "entity_type": "protein",
                "entity_instance_id": instance_id,
                # This authority describes the candidate coordinate output, not
                # the historical source entity that produced that output.
                "source_entity_id": mapping["output_entity_id"],
                "label_asym_id": mapping["output_label_asym_id"],
                "auth_asym_id": mapping["output_auth_asym_id"],
                "sequence": source_entity["sequence"],
            })
    if not entities:
        raise FrustraMPNNAdapterError("CM snapshot authorizes no protein entities")
    return {
        "schema_name": "producer_manifest",
        "schema_version": 1,
        "source_sha256": source_sha256,
        "cm_complex_snapshot_sha256": canonical_sha256(complex_snapshot),
        "entities": entities,
    }



def project_cm_structure_map(
    neutral_map: Mapping[str, Any], complex_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate a neutral map into the unchanged CM structure-map presentation."""

    try:
        validate_schema("cm_complex_snapshot_v1", complex_snapshot)
        mappings = {
            (
                mapping["source_instance_id"],
                mapping["output_entity_id"],
                mapping["output_label_asym_id"],
                mapping["output_auth_asym_id"],
            ): mapping
            for mapping in complex_snapshot["instance_mappings"]
            if mapping["candidate_id"] == neutral_map["candidate_id"]
        }
        rows: list[dict[str, Any]] = []
        for row in neutral_map["rows"]:
            mapping = mappings.get((
                row["entity_instance_id"], row["source_entity_id"],
                row["label_asym_id"], row["auth_asym_id"],
            ))
            if mapping is None:
                raise FrustraMPNNAdapterError(
                    "neutral structure row is not bound to the CM candidate mapping"
                )
            if not isinstance(row["label_seq_id"], int):
                raise FrustraMPNNAdapterError(
                    "CM presentation requires source-authoritative label sequence identity"
                )
            selected_model = row["selected_model"]
            rows.append({
                "entity_instance_id": row["entity_instance_id"],
                "source_entity_id": mapping["source_entity_id"],
                "source_model": selected_model,
                "label_asym_id": row["label_asym_id"],
                "auth_asym_id": row["auth_asym_id"],
                "label_seq_id": row["label_seq_id"],
                "auth_seq_id": row["auth_seq_id"],
                "insertion_code": row["insertion_code"],
                "residue_name": row["residue_name"],
                "sequence_index": row["sequence_index"],
                "pdb_chain_id": row["pdb_chain_id"],
                "pdb_residue_id": row["pdb_residue_id"],
                "pdb_insertion_code": row["pdb_insertion_code"],
                "backbone_atoms": {
                    name: (
                        source_id.removeprefix("cif:")
                        if isinstance(source_id, str) else None
                    )
                    for name, source_id in row["backbone_atoms"].items()
                },
                "selected_altloc": row["selected_altloc"],
                "model_decision": f"only_source_model:{selected_model}",
                "status": row["status"],
                "reason": row["reason"],
            })
        cm_map = {
            "schema_name": "cm_structure_map",
            "schema_version": 1,
            "target_id": neutral_map["target_id"],
            "candidate_id": neutral_map["candidate_id"],
            "original_cif_sha256": neutral_map["source_sha256"],
            "source_format": neutral_map["source_format"],
            "source_sha256": neutral_map["source_sha256"],
            "source_bytes": neutral_map["source_bytes"],
            "normalized_pdb_sha256": neutral_map["normalized_pdb_sha256"],
            "selected_source_model": neutral_map["selected_source_model"],
            "altloc_policy": neutral_map["altloc_policy"],
            "normalizer_version": "cm_structure_normalizer_v1",
            "rows": rows,
        }
        validate_schema("cm_structure_map_v1", cm_map)
        validate_structure_map_snapshot_binding(cm_map, complex_snapshot)
        return cm_map
    except FrustraMPNNAdapterError:
        raise
    except Exception as exc:
        raise FrustraMPNNAdapterError(
            f"neutral structure map cannot be projected to CM: {exc}"
        ) from exc


def project_cm_landscape(
    neutral_landscape: Mapping[str, Any],
    *,
    checkpoint_id: str,
    checkpoint_sha256: str,
    tool_id: str,
    tool_sha256: str,
    container_sha256: str,
) -> dict[str, Any]:
    """Translate a complete neutral landscape into the unchanged CM presentation."""

    threshold_policy = neutral_landscape.get("threshold_policy")
    threshold_policy_sha256 = neutral_landscape.get("threshold_policy_sha256")
    canonical_policy_sha256 = canonical_sha256(THRESHOLD_POLICY)
    if not isinstance(threshold_policy, dict) or canonical_sha256(
        threshold_policy
    ) != threshold_policy_sha256:
        raise FrustraMPNNAdapterError(
            "neutral FrustraMPNN landscape threshold policy is missing or unbound"
        )
    if (
        threshold_policy != THRESHOLD_POLICY
        or threshold_policy_sha256 != canonical_policy_sha256
    ):
        raise FrustraMPNNAdapterError(
            "neutral FrustraMPNN landscape does not use the canonical threshold policy"
        )
    class_names = {"minimal": "minimally_frustrated"}
    landscape = {
        "schema_name": "cm_frustration_landscape",
        "schema_version": 1,
        "target_id": neutral_landscape["target_id"],
        "candidate_id": neutral_landscape["candidate_id"],
        "raw_csv_sha256": neutral_landscape["raw_csv_sha256"],
        "checkpoint_id": checkpoint_id,
        "checkpoint_sha256": checkpoint_sha256,
        "tool_id": tool_id,
        "tool_sha256": tool_sha256,
        "container_sha256": container_sha256,
        "threshold_policy_id": CM_THRESHOLD_POLICY_ADAPTER_ID,
        "threshold_policy_sha256": canonical_policy_sha256,
        "input_issues": [],
        "residues": [
            {
                "entity_instance_id": residue["entity_instance_id"],
                "auth_asym_id": residue["auth_asym_id"],
                "auth_seq_id": residue["auth_seq_id"],
                "insertion_code": residue["insertion_code"],
                "sequence_index": residue["sequence_index"],
                "wt": residue["wt"],
                "slots": [
                    {
                        "wt": residue["wt"],
                        "mutation_aa": slot["mutation_aa"],
                        "score": slot["score"],
                        "class": class_names.get(slot["class"], slot["class"]),
                        "scoreable": slot["scoreable"],
                        "status": slot["status"],
                        "reason": slot["reason"],
                        "native": slot["native"],
                    }
                    for slot in residue["slots"]
                ],
            }
            for residue in neutral_landscape["residues"]
        ],
    }
    try:
        validate_schema("cm_frustration_landscape_v1", landscape)
    except Exception as exc:
        raise FrustraMPNNAdapterError(
            f"neutral landscape cannot be projected to CM: {exc}"
        ) from exc
    return landscape


def _open_output_parent(path: Path | str) -> tuple[int, str]:
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise FrustraMPNNAdapterError("unsafe producer authority output path")
    absolute = raw.startswith("/")
    parts = (raw[1:] if absolute else raw).split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise FrustraMPNNAdapterError("unsafe producer authority output path component")
    flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    parent = os.open("/" if absolute else ".", flags)
    try:
        for component in parts[:-1]:
            child = os.open(component, flags, dir_fd=parent)
            os.close(parent)
            parent = child
        return parent, parts[-1]
    except OSError as exc:
        os.close(parent)
        raise FrustraMPNNAdapterError(
            "producer authority output parent must be an existing no-follow directory"
        ) from exc


def _materialize_canonical(path: Path | str, payload: bytes) -> tuple[int, int]:
    parent, leaf = _open_output_parent(path)
    temporary = f".{leaf}.{secrets.token_hex(12)}.tmp"
    descriptor = -1
    published = False
    published_identity: tuple[int, int] | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FrustraMPNNAdapterError("producer authority write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise FrustraMPNNAdapterError("producer authority output must be a regular file")
        try:
            os.link(
                temporary, leaf,
                src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise FrustraMPNNAdapterError(
                "producer authority output already exists"
            ) from exc
        published = True
        published_identity = (metadata.st_dev, metadata.st_ino)
        os.fsync(parent)
        return published_identity
    except BaseException:
        if published and published_identity is not None:
            current = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISREG(current.st_mode)
                    or (current.st_dev, current.st_ino) != published_identity):
                raise FrustraMPNNAdapterError(
                    "producer authority changed while rolling back publication"
                )
            os.unlink(leaf, dir_fd=parent)
            os.fsync(parent)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def _remove_published_if_same(path: Path | str, identity: tuple[int, int]) -> None:
    parent, leaf = _open_output_parent(path)
    try:
        try:
            metadata = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != identity:
            raise FrustraMPNNAdapterError(
                "producer authority changed while rolling back failed normalization"
            )
        os.unlink(leaf, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def normalize_cm_structure(
    *,
    input_path: Path | str,
    output_pdb_path: Path | str,
    map_path: Path | str,
    authority_artifact_path: Path | str,
    target_id: str,
    parent_job_id: str,
    candidate_id: str,
    complex_snapshot: Mapping[str, Any],
    selected_model: int | None,
    altloc_policy: str,
    source_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Materialize neutral authority and normalize one CM candidate structure."""

    try:
        source_bytes = (
            read_structure_bytes(input_path) if source_bytes is None else bytes(source_bytes)
        )
    except StructureNormalizationError as exc:
        raise FrustraMPNNAdapterError(
            f"cannot read CM candidate source without following symlinks: {exc}"
        ) from exc
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    manifest = derive_producer_authority(
        complex_snapshot, source_sha256=source_sha256,
        target_id=target_id, candidate_id=candidate_id,
    )
    payload = canonical_json_bytes(manifest)
    authority_path = Path(authority_artifact_path)
    published_identity = _materialize_canonical(authority_path, payload)
    authority_hash = hashlib.sha256(payload).hexdigest()
    try:
        return normalize_structure_bytes(
            source_bytes=source_bytes,
            input_path=input_path,
            output_pdb_path=output_pdb_path,
            map_path=map_path,
            target_id=target_id,
            parent_job_id=parent_job_id,
            candidate_id=candidate_id,
            identity_authority={
                "kind": "producer_manifest_v1",
                "identity_domain": "source_authoritative",
                "authority_artifact_sha256": authority_hash,
                "source_sha256": manifest["source_sha256"],
            },
            authority_artifact_path=authority_path,
            protein_selection={"mode": "all_protein_entities"},
            selected_model=selected_model,
            altloc_policy=altloc_policy,
        )
    except StructureNormalizationError as exc:
        _remove_published_if_same(authority_path, published_identity)
        raise FrustraMPNNAdapterError(f"CM structure authority/identity rejected: {exc}") from exc


__all__ = [
    "CM_THRESHOLD_POLICY_ADAPTER_ID",
    "FrustraMPNNAdapterError",
    "bind_cm_candidate_snapshot_bytes",
    "derive_producer_authority",
    "normalize_cm_structure",
    "project_cm_landscape",
    "project_cm_structure_map",
]
