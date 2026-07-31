"""Lossless Protenix v2 request conversion and canonical finalization."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .contracts import (
    ContractValidationError,
    ResumeDescriptor,
    candidate_id,
    canonical_json_bytes,
    canonical_sha256,
    validate_complex_case,
    validate_schema,
)


class ProtenixMappingError(ValueError):
    """Complete-complex conversion or output validation failed closed."""


_ENTITY_KEYS = {
    "protein": "proteinChain",
    "dna": "dnaSequence",
    "rna": "rnaSequence",
    "ligand_ccd": "ligand",
    "ligand_smiles": "ligand",
    "ion": "ion",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(value: object) -> str:
    if not isinstance(value, str):
        raise ProtenixMappingError("native artifact path must be a string")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in {"", ".", ".."} for part in path.parts) or "\\" in value:
        raise ProtenixMappingError(f"unsafe native artifact path: {value!r}")
    return value


def _modification(entity_type: str, value: Mapping[str, Any]) -> dict[str, Any]:
    code = str(value.get("modification") or "").strip().upper()
    position = value.get("position")
    if not code or isinstance(position, bool) or not isinstance(position, int) or position < 1:
        raise ProtenixMappingError("modification identity is incomplete")
    if entity_type == "protein":
        return {"ptmType": f"CCD_{code.removeprefix('CCD_')}", "ptmPosition": position}
    return {"modificationType": f"CCD_{code.removeprefix('CCD_')}", "basePosition": position}


def snapshot_to_protenix(snapshot: Mapping[str, Any], ordered_seeds: Sequence[int]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Translate every admitted entity/copy/modification/bond without coercion."""

    try:
        validate_complex_case(snapshot)
    except (ContractValidationError, KeyError, TypeError) as exc:
        raise ProtenixMappingError(str(exc)) from exc
    if snapshot.get("unsupported_fields") or snapshot.get("admission", {}).get("conversion_omissions"):
        raise ProtenixMappingError("snapshot contains unsupported or omitted fields")
    if not ordered_seeds or len(set(ordered_seeds)) != len(ordered_seeds):
        raise ProtenixMappingError("ordered seeds must be nonempty and unique")

    sequences: list[dict[str, Any]] = []
    instance_lookup: dict[str, tuple[int, int]] = {}
    source_runtime: list[dict[str, Any]] = []
    for entity_index, entity in enumerate(snapshot["entities"], start=1):
        entity_type = entity["entity_type"]
        key = _ENTITY_KEYS.get(entity_type)
        if key is None:
            raise ProtenixMappingError(f"unsupported entity type: {entity_type}")
        count = entity.get("count")
        instance_ids = entity.get("ordered_instance_ids")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ProtenixMappingError("entity count must be a positive integer")
        if not isinstance(instance_ids, list) or len(instance_ids) != count or len(set(instance_ids)) != count:
            raise ProtenixMappingError("one ordered unique instance ID is required per copy")
        payload: dict[str, Any] = {"count": count, "id": list(instance_ids)}
        if entity_type in {"protein", "dna", "rna"}:
            payload["sequence"] = entity["sequence"]
        elif entity_type == "ligand_ccd":
            payload["ligand"] = f"CCD_{str(entity['ccd']).removeprefix('CCD_')}"
        elif entity_type == "ligand_smiles":
            payload["ligand"] = entity["smiles"]
        else:
            payload["ion"] = str(entity["ccd"]).removeprefix("CCD_")
        modifications = entity.get("modifications", [])
        if modifications:
            payload["modifications"] = [_modification(entity_type, item) for item in modifications]
        sequences.append({key: payload})
        for copy_index, instance_id in enumerate(instance_ids, start=1):
            instance_lookup[instance_id] = (entity_index, copy_index)
            source_runtime.append(
                {
                    "source_entity_id": entity["source_entity_id"],
                    "source_instance_id": instance_id,
                    "runtime_entity_id": str(entity_index),
                    "runtime_instance_id": instance_id,
                    "runtime_order": len(source_runtime),
                }
            )

    bonds: list[dict[str, Any]] = []
    for bond in snapshot.get("bonds", []):
        left = bond["left"]
        right = bond["right"]
        try:
            entity1, copy1 = instance_lookup[left["instance_id"]]
            entity2, copy2 = instance_lookup[right["instance_id"]]
        except KeyError as exc:
            raise ProtenixMappingError("bond references an unknown instance") from exc
        bonds.append(
            {
                "entity1": str(entity1),
                "copy1": copy1,
                "position1": str(left["position"]),
                "atom1": left["atom"],
                "entity2": str(entity2),
                "copy2": copy2,
                "position2": str(right["position"]),
                "atom2": right["atom"],
            }
        )
    task = {
        "name": snapshot["target_id"],
        "modelSeeds": list(ordered_seeds),
        "sequences": sequences,
    }
    if bonds:
        task["covalent_bonds"] = bonds
    audit = {
        "schema_name": "cm_protenix_input_composition",
        "schema_version": 1,
        "target_id": snapshot["target_id"],
        "snapshot_sha256": canonical_sha256(snapshot),
        "entity_count": len(sequences),
        "instance_count": len(source_runtime),
        "bond_count": len(bonds),
        "source_to_runtime": source_runtime,
        "protenix_input_sha256": canonical_sha256(task),
    }
    return task, audit


def build_protenix_runtime_bundle(
    request: Mapping[str, Any], snapshots: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    by_target = {snapshot["target_id"]: snapshot for snapshot in snapshots}
    requested_targets = [target["target_id"] for target in request["targets"]]
    if list(by_target) != requested_targets:
        raise ProtenixMappingError("snapshot target order must equal request target order")
    tasks: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for target_id in requested_targets:
        task, audit = snapshot_to_protenix(by_target[target_id], request["ordered_seeds"])
        tasks.append(task)
        audits.append(audit)
    return {
        "input": tasks,
        "composition_audits": audits,
        "coordinate_context": {
            "schema_name": "cm_protenix_coordinate_context",
            "schema_version": 1,
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "target_by_pdb_id": {task["name"]: task["name"] for task in tasks},
        },
    }


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProtenixMappingError(f"invalid coordinate ledger line {line_number}") from exc
            if not isinstance(record, dict):
                raise ProtenixMappingError("coordinate ledger rows must be objects")
            records.append(record)
    return records


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(dict(payload)))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(item) for item in value] if isinstance(value, list) else [str(value)]


def _audit_output_composition(path: Path, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Audit native mmCIF hierarchy against every admitted source instance."""

    try:
        from Bio.PDB.MMCIF2Dict import MMCIF2Dict

        cif = MMCIF2Dict(str(path))
    except Exception as exc:
        raise ProtenixMappingError(f"cannot parse authoritative Protenix mmCIF: {exc}") from exc
    auth_chains = _as_list(cif.get("_atom_site.auth_asym_id"))
    label_chains = _as_list(cif.get("_atom_site.label_asym_id"))
    comp_ids = _as_list(cif.get("_atom_site.label_comp_id"))
    auth_seq = _as_list(cif.get("_atom_site.auth_seq_id"))
    atom_ids = _as_list(cif.get("_atom_site.label_atom_id"))
    lengths = {len(auth_chains), len(label_chains), len(comp_ids), len(auth_seq), len(atom_ids)}
    if len(lengths) != 1 or not auth_chains:
        raise ProtenixMappingError("authoritative Protenix mmCIF has incomplete atom identity")
    observed_instances = list(dict.fromkeys(auth_chains))
    expected_instances = [
        instance
        for entity in snapshot["entities"]
        for instance in entity["ordered_instance_ids"]
    ]
    missing = [value for value in expected_instances if value not in observed_instances]
    unexpected = [value for value in observed_instances if value not in expected_instances]
    if missing or unexpected:
        raise ProtenixMappingError(
            f"Protenix output instance composition mismatch: missing={missing}, unexpected={unexpected}"
        )
    modification_checks: list[dict[str, Any]] = []
    for entity in snapshot["entities"]:
        for modification in entity.get("modifications", []):
            expected_comp = str(modification["modification"]).removeprefix("CCD_").upper()
            position = str(modification["position"])
            for instance in entity["ordered_instance_ids"]:
                present = any(
                    chain == instance and sequence == position and comp.upper().removeprefix("CCD_") == expected_comp
                    for chain, sequence, comp in zip(auth_chains, auth_seq, comp_ids, strict=True)
                )
                modification_checks.append({
                    "instance_id": instance, "position": int(position),
                    "modification": expected_comp, "present": present,
                })
                if not present:
                    raise ProtenixMappingError("Protenix output omitted an admitted modification")
    connection_rows = []
    p1_chain = _as_list(cif.get("_struct_conn.ptnr1_auth_asym_id"))
    if p1_chain:
        fields = [
            p1_chain, _as_list(cif.get("_struct_conn.ptnr1_auth_seq_id")),
            _as_list(cif.get("_struct_conn.ptnr1_label_atom_id")),
            _as_list(cif.get("_struct_conn.ptnr2_auth_asym_id")),
            _as_list(cif.get("_struct_conn.ptnr2_auth_seq_id")),
            _as_list(cif.get("_struct_conn.ptnr2_label_atom_id")),
        ]
        if len({len(value) for value in fields}) != 1:
            raise ProtenixMappingError("Protenix output connection identity is incomplete")
        connection_rows = [tuple(value) for value in zip(*fields, strict=True)]
    bond_checks: list[dict[str, Any]] = []
    for bond in snapshot.get("bonds", []):
        left, right = bond["left"], bond["right"]
        forward = (
            str(left["instance_id"]), str(left["position"]), str(left["atom"]),
            str(right["instance_id"]), str(right["position"]), str(right["atom"]),
        )
        reverse = (*forward[3:], *forward[:3])
        present = forward in connection_rows or reverse in connection_rows
        bond_checks.append({"left": dict(left), "right": dict(right), "present": present})
        if not present:
            raise ProtenixMappingError("Protenix output omitted an admitted covalent bond")
    return {
        "candidate_structure_sha256": _sha256_file(path),
        "expected_instances": expected_instances,
        "observed_instances": observed_instances,
        "instance_label_mapping": [
            {"auth_asym_id": auth, "label_asym_id": label}
            for auth, label in dict.fromkeys(zip(auth_chains, label_chains, strict=True))
        ],
        "modification_checks": modification_checks,
        "bond_checks": bond_checks,
        "atom_count": len(auth_chains),
        "status": "complete_match",
    }


def finalize_protenix(
    request: Mapping[str, Any],
    snapshots: Sequence[Mapping[str, Any]],
    native_root: Path | str,
    output_root: Path | str,
    runtime: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the runtime ledger and publish native/ensemble authorities."""

    root = Path(native_root).resolve(strict=True)
    output = Path(output_root)
    if output.exists():
        raise ProtenixMappingError("canonical output already exists")
    ledger_path = root / "cm_protenix_coordinate_ledger_v1.jsonl"
    if not ledger_path.is_file() or ledger_path.is_symlink():
        raise ProtenixMappingError("authoritative Protenix coordinate ledger is missing")
    records = _read_ledger(ledger_path)
    expected = [
        {
            "backend": "protenix_v2_ensemble",
            "target_id": target["target_id"],
            "ordered_seed": seed,
            "sample_index": sample_index,
        }
        for target in request["targets"]
        for seed in request["ordered_seeds"]
        for sample_index in range(request["samples_per_seed"])
    ]
    by_coordinate: dict[bytes, dict[str, Any]] = {}
    for record in records:
        coordinates = record.get("coordinates")
        key = canonical_json_bytes(coordinates)
        if key in by_coordinate:
            raise ProtenixMappingError("duplicate Protenix coordinate")
        by_coordinate[key] = record
    if set(by_coordinate) != {canonical_json_bytes(item) for item in expected}:
        raise ProtenixMappingError("observed Protenix coordinates do not equal the request plan")

    snapshot_by_target = {value["target_id"]: value for value in snapshots}
    output_composition_audits: list[dict[str, Any]] = []
    for coordinates in expected:
        record = by_coordinate[canonical_json_bytes(coordinates)]
        structure = next(
            (item for item in record.get("artifacts", []) if item.get("semantic_role") == "authoritative_cif"),
            None,
        )
        if not isinstance(structure, Mapping):
            raise ProtenixMappingError("coordinate has no authoritative structure for composition audit")
        structure_path = (root / _relative(structure.get("relative_path"))).resolve(strict=True)
        output_composition_audits.append({
            "coordinates": coordinates,
            **_audit_output_composition(structure_path, snapshot_by_target[coordinates["target_id"]]),
        })
    composition_path = root / "runtime" / "composition-audit.json"
    input_composition = json.loads(composition_path.read_text(encoding="utf-8"))
    _atomic_json(composition_path, {
        "schema_name": "cm_protenix_composition_audit", "schema_version": 1,
        "input_audits": (
            input_composition.get("audits", input_composition)
            if isinstance(input_composition, dict)
            else input_composition
        ),
        "output_audits": output_composition_audits,
    })

    runtime_digest = canonical_sha256(runtime)
    candidates: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    referenced: set[str] = set()
    for coordinates in expected:
        record = by_coordinate[canonical_json_bytes(coordinates)]
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, list):
            raise ProtenixMappingError("coordinate ledger row has no artifact records")
        roles = {item.get("semantic_role") for item in artifacts if isinstance(item, dict)}
        if roles != {"authoritative_cif", "confidence_json", "full_data_json"}:
            raise ProtenixMappingError("coordinate ledger mandatory roles are incomplete")
        stable_id = candidate_id(coordinates)
        paths_by_role: dict[str, str] = {}
        for item in artifacts:
            relative_path = _relative(item.get("relative_path"))
            artifact = (root / relative_path).resolve(strict=True)
            artifact.relative_to(root)
            if not artifact.is_file() or artifact.is_symlink():
                raise ProtenixMappingError("ledger artifact is not a safe regular file")
            if relative_path in referenced:
                raise ProtenixMappingError("one native artifact is shared by multiple coordinates")
            referenced.add(relative_path)
            digest = _sha256_file(artifact)
            if digest != item.get("sha256") or artifact.stat().st_size != item.get("bytes"):
                raise ProtenixMappingError("ledger artifact byte identity mismatch")
            role = item["semantic_role"]
            paths_by_role[role] = relative_path
            files.append(
                {
                    "relative_path": relative_path,
                    "sha256": digest,
                    "bytes": artifact.stat().st_size,
                    "media_type": "chemical/x-mmcif" if artifact.suffix.lower() in {".cif", ".mmcif"} else "application/json",
                    "semantic_role": role,
                    "candidate_id": stable_id,
                    "backend_coordinates": coordinates,
                    "provenance_sha256": runtime_digest,
                    "related_paths": sorted(
                        other["relative_path"] for other in artifacts if other is not item
                    ),
                }
            )
        candidates.append(
            {
                "candidate_id": stable_id,
                "backend_coordinates": coordinates,
                "authoritative_structure_path": paths_by_role["authoritative_cif"],
                "authoritative_structure_sha256": next(
                    item["sha256"] for item in artifacts if item["semantic_role"] == "authoritative_cif"
                ),
                "sidecar_paths": [paths_by_role["confidence_json"], paths_by_role["full_data_json"]],
            }
        )

    mandatory_globals = runtime.get("global_artifacts")
    required_global_roles = {
        "runtime_input", "feature_policy", "log", "runtime_config", "composition_audit",
        "coordinate_ledger", "coordinate_context", "preprocessing_record", "msa_record",
        "template_record",
    }
    if not isinstance(mandatory_globals, list) or {item.get("semantic_role") for item in mandatory_globals} != required_global_roles:
        raise ProtenixMappingError("Protenix global artifact roles are incomplete")
    for item in mandatory_globals:
        relative_path = _relative(item.get("relative_path"))
        artifact = (root / relative_path).resolve(strict=True)
        artifact.relative_to(root)
        if relative_path in referenced or not artifact.is_file() or artifact.is_symlink():
            raise ProtenixMappingError("invalid or duplicate global artifact")
        referenced.add(relative_path)
        files.append(
            {
                "relative_path": relative_path,
                "sha256": _sha256_file(artifact),
                "bytes": artifact.stat().st_size,
                "media_type": mimetypes.guess_type(artifact.name)[0] or "application/octet-stream",
                "semantic_role": item["semantic_role"],
                "candidate_id": None,
                "backend_coordinates": None,
                "provenance_sha256": runtime_digest,
                "related_paths": [],
            }
        )

    observed_native = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    if referenced - observed_native:
        raise ProtenixMappingError(
            f"native Protenix tree is missing declared files: {sorted(referenced - observed_native)}"
        )
    for relative_path in sorted(observed_native - referenced):
        artifact = (root / relative_path).resolve(strict=True)
        artifact.relative_to(root)
        if not artifact.is_file() or artifact.is_symlink():
            raise ProtenixMappingError("native Protenix auxiliary artifact is unsafe")
        referenced.add(relative_path)
        files.append(
            {
                "relative_path": relative_path,
                "sha256": _sha256_file(artifact),
                "bytes": artifact.stat().st_size,
                "media_type": mimetypes.guess_type(artifact.name)[0] or "application/octet-stream",
                "semantic_role": (
                    "native_state" if artifact.suffix.lower() in {".pt", ".pth", ".ckpt"}
                    else "optional_analytics" if artifact.suffix.lower() in {".csv", ".npz", ".npy"}
                    else "preprocess"
                ),
                "candidate_id": None,
                "backend_coordinates": None,
                "provenance_sha256": runtime_digest,
                "related_paths": [],
            }
        )

    for item in files:
        item["relative_path"] = f"native/{item['relative_path']}"
        item["related_paths"] = [f"native/{path}" for path in item["related_paths"]]
    for candidate in candidates:
        candidate["authoritative_structure_path"] = f"native/{candidate['authoritative_structure_path']}"
        candidate["sidecar_paths"] = [f"native/{path}" for path in candidate["sidecar_paths"]]

    native = {
        "schema_name": "cm_native_artifacts",
        "schema_version": 1,
        "request_id": request["request_id"],
        "backend": "protenix_v2_ensemble",
        "settings_sha256": canonical_sha256(request["runtime_policy"]),
        "files": files,
    }
    validate_schema("cm_native_artifacts_v1", native)
    snapshot_sha256 = canonical_sha256(list(snapshots))
    feature_sha256 = canonical_sha256(request["feature_policy"])
    resume_descriptor = ResumeDescriptor(
        request_sha256=request["request_sha256"],
        source_snapshot_sha256=snapshot_sha256,
        complex_snapshot_sha256=snapshot_sha256,
        backend="protenix_v2_ensemble",
        backend_version=str(runtime["backend_version"]),
        backend_commit=str(runtime["backend_commit"]),
        runtime_identity=str(runtime["runtime_identity"]),
        container_digest=str(runtime["container_digest"]),
        model_id=str(runtime.get("model_id") or "protenix-v2"),
        checkpoint_sha256=str(runtime["checkpoint_sha256"]),
        feature_policy=dict(request["feature_policy"]),
        feature_policy_sha256=feature_sha256,
        ordered_seeds=list(request["ordered_seeds"]),
        samples_per_seed=int(request["samples_per_seed"]),
        coordinate_plan=expected,
        expected_candidate_cardinality=len(expected),
        expected_manifest_schema="cm_ensemble",
        expected_manifest_version=1,
        required_artifact_roles=sorted(required_global_roles | {"authoritative_cif", "confidence_json", "full_data_json"}),
        expected_manifest_contract_sha256=canonical_sha256({"schema": "cm_ensemble", "version": 1}),
        settings_runtime_policy_sha256=canonical_sha256(request["runtime_policy"]),
    )
    native_hash = canonical_sha256(native)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    ensemble = {
        "schema_name": "cm_ensemble",
        "schema_version": 1,
        "request_id": request["request_id"],
        "request_sha256": request["request_sha256"],
        "source_snapshot_sha256": snapshot_sha256,
        "backend": "protenix_v2_ensemble",
        "runtime_identity": runtime["runtime_identity"],
        "container_digest": runtime["container_digest"],
        "checkpoint_sha256": runtime["checkpoint_sha256"],
        "feature_policy_sha256": feature_sha256,
        "expected_cardinality": len(expected),
        "expected_coordinates": expected,
        "candidates": candidates,
        "native_manifest_path": "cm_native_artifacts_v1.json",
        "native_manifest_sha256": native_hash,
        "warnings": [],
        "omissions": [],
        "terminal_status": "complete",
        "started_at": runtime.get("started_at", now),
        "completed_at": runtime.get("completed_at", now),
        "command": list(runtime["command"]),
        "resume_key": resume_descriptor.resume_key,
        "resumable": True,
        # `resume_key` is an ensemble authority field; it is a computed
        # descriptor property and must not be serialized into the strict
        # descriptor schema itself.
        "resume_descriptor": resume_descriptor.model_dump(mode="json", exclude_computed_fields=True),
    }
    validate_schema("cm_ensemble_v1", ensemble)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        shutil.copytree(root, temporary / "native", copy_function=shutil.copy2)
        _atomic_json(temporary / "cm_native_artifacts_v1.json", native)
        _atomic_json(temporary / "cm_ensemble_v1.json", ensemble)
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return native, ensemble
