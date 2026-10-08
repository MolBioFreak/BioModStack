"""Lossless Protenix v2 request conversion and canonical finalization."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import stat
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .contracts import (
    ContractValidationError,
    ResumeDescriptor,
    SHA256_RE,
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
_PROTENIX_RUNTIME_GLOBAL_ROLES = {
    "runtime_input", "feature_policy", "log", "runtime_config", "composition_audit",
    "coordinate_ledger", "coordinate_context", "preprocessing_record", "msa_record",
    "template_record", "runtime_attestation", "runtime_image_receipt",
    "execution_snapshot_receipt",
}


def _open_pinned_file(path: Path) -> tuple[int, int, str, os.stat_result, os.stat_result]:
    """Open one native file without following a swapped directory component."""

    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if len(parts) < 2 or parts[0] != os.sep:
        raise ProtenixMappingError(f"native artifact path is not absolute: {path}")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(os.sep, directory_flags)
    file_fd: int | None = None
    try:
        for component in parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        leaf = parts[-1]
        file_fd = os.open(
            leaf,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(file_fd)
        path_before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        return file_fd, parent_fd, leaf, opened, path_before
    except Exception:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)
        raise


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return all(
        getattr(left, field) == getattr(right, field)
        for field in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    )


def _stable_file_measurement(path: Path) -> tuple[str, int]:
    try:
        file_fd, parent_fd, leaf, before, path_before = _open_pinned_file(path)
    except OSError as exc:
        raise ProtenixMappingError(f"native artifact is unavailable: {path}") from exc
    try:
        if not stat.S_ISREG(before.st_mode):
            raise ProtenixMappingError(f"native artifact is not a regular file: {path}")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(file_fd)
        path_after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        visible_after = os.lstat(path)
        if not _same_file_identity(before, after) or not _same_file_identity(path_before, path_after) or not _same_file_identity(before, visible_after):
            raise ProtenixMappingError(f"native artifact path or bytes changed during measurement: {path}")
        if size != before.st_size:
            raise ProtenixMappingError(f"native artifact size changed during measurement: {path}")
        return digest.hexdigest(), size
    finally:
        os.close(file_fd)
        os.close(parent_fd)


def _sha256_file(path: Path) -> str:
    return _stable_file_measurement(path)[0]


def _stable_file_bytes(path: Path) -> bytes:
    try:
        file_fd, parent_fd, leaf, before, path_before = _open_pinned_file(path)
    except OSError as exc:
        raise ProtenixMappingError(f"native artifact is unavailable: {path}") from exc
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(file_fd)
        path_after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        visible_after = os.lstat(path)
        if not _same_file_identity(before, after) or not _same_file_identity(path_before, path_after) or not _same_file_identity(before, visible_after):
            raise ProtenixMappingError(f"native artifact path changed while reading: {path}")
        payload = b"".join(chunks)
        if len(payload) != before.st_size:
            raise ProtenixMappingError(f"native artifact size changed while reading: {path}")
        return payload
    finally:
        os.close(file_fd)
        os.close(parent_fd)


def _native_file(root: Path, relative_path: str) -> Path:
    candidate = root / Path(*PurePosixPath(relative_path).parts)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ProtenixMappingError("native artifact escapes the native root") from exc
    return candidate


def _observed_native_files(root: Path) -> set[str]:
    observed: set[str] = set()
    for directory, directories, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in directories:
            path = directory_path / name
            if path.is_symlink():
                raise ProtenixMappingError("native Protenix tree contains a symlinked directory")
        for name in filenames:
            path = directory_path / name
            if path.is_symlink() or not stat.S_ISREG(os.lstat(path).st_mode):
                raise ProtenixMappingError("native Protenix tree contains an unsafe file")
            observed.add(path.relative_to(root).as_posix())
    return observed


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
    try:
        payload = _stable_file_bytes(path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtenixMappingError("coordinate ledger is not UTF-8") from exc
    for line_number, line in enumerate(payload.splitlines(), start=1):
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
        payload = _stable_file_bytes(path)
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".cif") as temporary:
            temporary.write(payload)
            temporary.flush()
            cif = MMCIF2Dict(temporary.name)
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
        "candidate_structure_sha256": hashlib.sha256(payload).hexdigest(),
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


def _validate_runtime_attestation(runtime: Mapping[str, Any]) -> None:
    """Reject expected/registry identity copied into runtime output as observation."""

    required = {
        "schema_name",
        "schema_version",
        "status",
        "runtime_image",
        "execution_snapshot",
        "checkpoint",
        "backend_source",
        "executed_wrapper",
        "backend_version",
        "backend_commit",
        "runtime_identity",
        "container_digest",
        "checkpoint_sha256",
        "model_id",
        "started_at",
        "completed_at",
        "command",
        "global_artifacts",
        "attestation_sha256",
    }
    if set(runtime) != required:
        raise ProtenixMappingError(
            "Protenix runtime output has no complete observed runtime attestation"
        )
    if (
        runtime.get("schema_name") != "cm_protenix_runtime_attestation"
        or runtime.get("schema_version") != 1
        or runtime.get("status") != "observed_and_verified"
    ):
        raise ProtenixMappingError("Protenix observed runtime attestation is not verified")
    attested = {key: runtime[key] for key in required if key != "attestation_sha256"}
    if canonical_sha256(attested) != runtime.get("attestation_sha256"):
        raise ProtenixMappingError("Protenix observed runtime attestation digest mismatch")
    if not isinstance(runtime.get("attestation_sha256"), str) or not SHA256_RE.fullmatch(runtime["attestation_sha256"]):
        raise ProtenixMappingError("Protenix observed runtime attestation digest is malformed")
    try:
        validate_schema("cm_protenix_runtime_attestation_v1", runtime)
    except (ContractValidationError, KeyError, TypeError) as exc:
        raise ProtenixMappingError("Protenix observed runtime attestation schema is invalid") from exc
    image = runtime.get("runtime_image")
    execution_snapshot = runtime.get("execution_snapshot")
    checkpoint = runtime.get("checkpoint")
    source = runtime.get("backend_source")
    wrapper = runtime.get("executed_wrapper")
    if not isinstance(image, Mapping):
        raise ProtenixMappingError("Protenix observed runtime image identity is malformed")
    if not isinstance(execution_snapshot, Mapping):
        raise ProtenixMappingError("Protenix execution snapshot identity is malformed")
    if not isinstance(checkpoint, Mapping):
        raise ProtenixMappingError("Protenix observed checkpoint identity is malformed")
    if not isinstance(source, Mapping):
        raise ProtenixMappingError("Protenix observed backend source identity is malformed")
    if not isinstance(wrapper, Mapping):
        raise ProtenixMappingError("Protenix observed wrapper identity is malformed")
    image_sha = image.get("sha256")
    checkpoint_sha = checkpoint.get("sha256")
    source_sha = source.get("manifest_sha256")
    wrapper_sha = wrapper.get("sha256")
    if not all(
        isinstance(value, str) and SHA256_RE.fullmatch(value)
        for value in (image_sha, checkpoint_sha, source_sha, wrapper_sha)
    ):
        raise ProtenixMappingError("Protenix observed runtime digests are malformed")
    for label, identity in (("runtime image", image), ("checkpoint", checkpoint), ("wrapper", wrapper)):
        value = identity.get("bytes")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProtenixMappingError(f"Protenix observed {label} byte count is malformed")
    receipt = image.get("receipt")
    host_observed = image.get("host_observed_source")
    verified_snapshot = image.get("host_verified_snapshot")
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("schema_name") != "cm_runtime_image_receipt"
        or receipt.get("schema_version") != 1
        or receipt.get("status") != "verified_immutable_snapshot"
        or not isinstance(receipt.get("sha256"), str)
        or not SHA256_RE.fullmatch(receipt["sha256"])
        or isinstance(receipt.get("bytes"), bool)
        or not isinstance(receipt.get("bytes"), int)
        or not isinstance(host_observed, Mapping)
        or host_observed.get("sha256") != image_sha
        or not isinstance(verified_snapshot, Mapping)
        or verified_snapshot.get("sha256") != image_sha
        or verified_snapshot.get("bytes") != image.get("bytes")
    ):
        raise ProtenixMappingError("Protenix runtime image has no verified host receipt")
    execution_receipt = execution_snapshot.get("receipt")
    if (
        not isinstance(execution_receipt, Mapping)
        or execution_receipt.get("schema_name") != "cm_protenix_execution_snapshot"
        or execution_receipt.get("schema_version") != 1
        or execution_receipt.get("status") != "verified_before_execution"
        or not isinstance(execution_snapshot.get("sha256"), str)
        or not SHA256_RE.fullmatch(execution_snapshot["sha256"])
        or isinstance(execution_snapshot.get("bytes"), bool)
        or not isinstance(execution_snapshot.get("bytes"), int)
    ):
        raise ProtenixMappingError("Protenix runtime has no verified execution snapshot receipt")
    if not isinstance(checkpoint.get("relative_path"), str) or not checkpoint["relative_path"]:
        raise ProtenixMappingError("Protenix observed checkpoint path is missing")
    _relative(checkpoint["relative_path"])
    files = source.get("files")
    if not isinstance(files, list) or not files:
        raise ProtenixMappingError("Protenix backend source manifest is empty")
    if canonical_sha256(files) != source_sha:
        raise ProtenixMappingError("Protenix backend source manifest digest mismatch")
    for record in files:
        if (
            not isinstance(record, Mapping)
            or not isinstance(record.get("relative_path"), str)
            or not isinstance(record.get("sha256"), str)
            or not SHA256_RE.fullmatch(record["sha256"])
            or isinstance(record.get("bytes"), bool)
            or not isinstance(record.get("bytes"), int)
        ):
            raise ProtenixMappingError("Protenix backend source file measurement is malformed")
        _relative(record["relative_path"])
    global_artifacts = runtime.get("global_artifacts")
    if (
        not isinstance(global_artifacts, list)
        or {item.get("semantic_role") for item in global_artifacts if isinstance(item, Mapping)} != _PROTENIX_RUNTIME_GLOBAL_ROLES
        or len(global_artifacts) != len(_PROTENIX_RUNTIME_GLOBAL_ROLES)
    ):
        raise ProtenixMappingError("Protenix global artifact roles are incomplete")
    global_paths: set[str] = set()
    for item in global_artifacts:
        if not isinstance(item, Mapping) or not isinstance(item.get("relative_path"), str):
            raise ProtenixMappingError("Protenix global artifact record is malformed")
        _relative(item["relative_path"])
        if item["relative_path"] in global_paths:
            raise ProtenixMappingError("Protenix global artifact paths are duplicated")
        global_paths.add(item["relative_path"])
    if (
        runtime.get("container_digest") != f"sha256:{image_sha}"
        or runtime.get("checkpoint_sha256") != checkpoint_sha
        or runtime.get("backend_commit") != source.get("commit")
        or runtime.get("backend_version") != source.get("distribution_version")
        or runtime.get("runtime_identity") != (
            f"apptainer-sif-sha256:{image_sha}"
            f"+checkpoint-sha256:{checkpoint_sha}"
            f"+protenix-source-sha256:{source_sha}"
            f"+wrapper-sha256:{wrapper_sha}"
        )
    ):
        raise ProtenixMappingError(
            "Protenix flat runtime identity differs from observed component identities"
        )


def finalize_protenix(
    request: Mapping[str, Any],
    snapshots: Sequence[Mapping[str, Any]],
    native_root: Path | str,
    output_root: Path | str,
    runtime: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the runtime ledger and publish native/ensemble authorities."""

    _validate_runtime_attestation(runtime)
    root_input = Path(native_root)
    try:
        root_info = os.lstat(root_input)
    except OSError as exc:
        raise ProtenixMappingError("native Protenix root is unavailable") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ProtenixMappingError("native Protenix root is not a real directory")
    root = root_input.resolve(strict=True)
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
        structure_path = _native_file(root, _relative(structure.get("relative_path")))
        output_composition_audits.append({
            "coordinates": coordinates,
            **_audit_output_composition(structure_path, snapshot_by_target[coordinates["target_id"]]),
        })
    composition_path = root / "runtime" / "composition-audit.json"
    try:
        input_composition = json.loads(_stable_file_bytes(composition_path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtenixMappingError("Protenix composition audit is not stable JSON") from exc
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
            artifact = _native_file(root, relative_path)
            if relative_path in referenced:
                raise ProtenixMappingError("one native artifact is shared by multiple coordinates")
            referenced.add(relative_path)
            digest, artifact_bytes = _stable_file_measurement(artifact)
            if digest != item.get("sha256") or artifact_bytes != item.get("bytes"):
                raise ProtenixMappingError("ledger artifact byte identity mismatch")
            role = item["semantic_role"]
            paths_by_role[role] = relative_path
            files.append(
                {
                    "relative_path": relative_path,
                    "sha256": digest,
                    "bytes": artifact_bytes,
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
    required_global_roles = set(_PROTENIX_RUNTIME_GLOBAL_ROLES)
    if not isinstance(mandatory_globals, list) or {item.get("semantic_role") for item in mandatory_globals} != required_global_roles:
        raise ProtenixMappingError("Protenix global artifact roles are incomplete")
    for item in mandatory_globals:
        relative_path = _relative(item.get("relative_path"))
        artifact = _native_file(root, relative_path)
        if relative_path in referenced:
            raise ProtenixMappingError("invalid or duplicate global artifact")
        referenced.add(relative_path)
        digest, artifact_bytes = _stable_file_measurement(artifact)
        files.append(
            {
                "relative_path": relative_path,
                "sha256": digest,
                "bytes": artifact_bytes,
                "media_type": mimetypes.guess_type(artifact.name)[0] or "application/octet-stream",
                "semantic_role": item["semantic_role"],
                "candidate_id": None,
                "backend_coordinates": None,
                "provenance_sha256": runtime_digest,
                "related_paths": [],
            }
        )

    observed_native = _observed_native_files(root)
    if referenced - observed_native:
        raise ProtenixMappingError(
            f"native Protenix tree is missing declared files: {sorted(referenced - observed_native)}"
        )
    for relative_path in sorted(observed_native - referenced):
        artifact = _native_file(root, relative_path)
        referenced.add(relative_path)
        digest, artifact_bytes = _stable_file_measurement(artifact)
        files.append(
            {
                "relative_path": relative_path,
                "sha256": digest,
                "bytes": artifact_bytes,
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
    runtime_attestation_files = [
        item for item in files if item["semantic_role"] == "runtime_attestation"
    ]
    if len(runtime_attestation_files) != 1:
        raise ProtenixMappingError("Protenix runtime attestation artifact is not unique")
    runtime_attestation_sha256 = runtime_attestation_files[0]["sha256"]
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
        "runtime_attestation_sha256": runtime_attestation_sha256,
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
