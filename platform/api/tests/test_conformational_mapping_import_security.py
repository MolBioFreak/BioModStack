from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path, PurePosixPath

import pytest

from services.conformational_mapping import import_stager

from services.conformational_mapping.import_stager import (
    ImportStagingError,
    RegisteredArtifact,
    finalize_staged_import,
    stage_registered_artifacts,
    verify_registered_artifact,
)
from services.conformational_mapping.contracts import candidate_id, canonical_sha256
from services.conformational_mapping.import_snapshot import (
    ImportSnapshotError,
    build_import_snapshot_from_mmcif,
    normalized_import_snapshot_sha256,
    read_staged_import_file,
)


PDB = b"ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 20.00           C\n"
MMCIF = b"""data_minimal
_entry.id minimal
loop_
_entity.id
_entity.type
1 polymer
loop_
_struct_asym.id
_struct_asym.entity_id
ASYM_A 1
_entity_poly.entity_id 1
_entity_poly.type 'polypeptide(L)'
_entity_poly.pdbx_seq_one_letter_code_can AG
loop_
_entity_poly_seq.entity_id
_entity_poly_seq.num
_entity_poly_seq.mon_id
_entity_poly_seq.hetero
1 1 ALA n
1 2 GLY n
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.pdbx_PDB_model_num
ATOM 1 N N . ALA ASYM_A 1 1 ? 0 0 0 1.00 10.00 10 ALA A N 1
ATOM 2 C CA . ALA ASYM_A 1 1 ? 1 0 0 1.00 10.00 10 ALA A CA 1
ATOM 3 C C . ALA ASYM_A 1 1 ? 2 0 0 1.00 10.00 10 ALA A C 1
ATOM 4 O O . ALA ASYM_A 1 1 ? 3 0 0 1.00 10.00 10 ALA A O 1
ATOM 5 N N . GLY ASYM_A 1 2 ? 4 0 0 1.00 10.00 11 GLY A N 1
ATOM 6 C CA . GLY ASYM_A 1 2 ? 5 0 0 1.00 10.00 11 GLY A CA 1
ATOM 7 C C . GLY ASYM_A 1 2 ? 6 0 0 1.00 10.00 11 GLY A C 1
ATOM 8 O O . GLY ASYM_A 1 2 ? 7 0 0 1.00 10.00 11 GLY A O 1
"""


def _registered(root: Path, relative: str = "input.pdb", *, principal: str = "alice") -> RegisteredArtifact:
    path = root / relative
    payload = path.read_bytes() if path.is_file() and not path.is_symlink() else PDB
    return RegisteredArtifact("artifact-1", principal, root, relative, hashlib.sha256(payload).hexdigest(), len(payload))


def test_registered_verify_and_read_reject_oversize_before_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.pdb"
    source.write_bytes(PDB)
    artifact = _registered(tmp_path)

    def unexpected_digest(*_args, **_kwargs):
        raise AssertionError("oversized descriptor must be rejected before hashing")

    monkeypatch.setattr(import_stager, "_digest_fd", unexpected_digest)
    for operation in (import_stager.verify_registered_artifact, import_stager.read_registered_artifact):
        with pytest.raises(ImportStagingError, match="byte limit"):
            operation(artifact, principal_id="alice", maximum_bytes=len(PDB) - 1)


def test_descriptor_reader_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    root = tmp_path / "staged"
    root.mkdir()
    os.mkfifo(root / "artifact.cif")
    with pytest.raises(ImportSnapshotError, match="regular file"):
        read_staged_import_file(root, PurePosixPath("artifact.cif"))


@pytest.mark.parametrize("value", ["../input.pdb", "dir/../../input.pdb"])
def test_cm6_001_rejects_dotdot(tmp_path: Path, value: str) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    with pytest.raises(ImportStagingError, match="unsafe|canonical"):
        verify_registered_artifact(_registered(tmp_path, value), principal_id="alice")


def test_cm6_002_rejects_absolute_path(tmp_path: Path) -> None:
    path = tmp_path / "input.pdb"
    path.write_bytes(PDB)
    with pytest.raises(ImportStagingError, match="canonical"):
        verify_registered_artifact(_registered(tmp_path, str(path)), principal_id="alice")


@pytest.mark.parametrize("value", ["%2e%2e/input.pdb", "%252e%252e/input.pdb"])
def test_cm6_003_rejects_encoded_traversal(tmp_path: Path, value: str) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    with pytest.raises(ImportStagingError, match="unsafe"):
        verify_registered_artifact(_registered(tmp_path, value), principal_id="alice")


@pytest.mark.parametrize("token", ["*", "?", "[a]", "{a}", ";", "|", "$()", "`", "\n"])
def test_cm6_004_rejects_glob_and_metacharacters(tmp_path: Path, token: str) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    with pytest.raises(ImportStagingError, match="unsafe"):
        verify_registered_artifact(_registered(tmp_path, f"input{token}.pdb"), principal_id="alice")


def test_cm6_005_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.pdb"
    outside.write_bytes(PDB)
    (tmp_path / "input.pdb").symlink_to(outside)
    artifact = RegisteredArtifact("artifact-1", "alice", tmp_path, "input.pdb", hashlib.sha256(PDB).hexdigest(), len(PDB))
    with pytest.raises(ImportStagingError, match="unsafe"):
        verify_registered_artifact(artifact, principal_id="alice")


def test_cm6_006_rejects_symlink_swap_or_retarget(tmp_path: Path) -> None:
    path = tmp_path / "input.pdb"
    path.write_bytes(PDB)
    artifact = _registered(tmp_path)
    path.unlink()
    path.symlink_to(tmp_path / "missing.pdb")
    with pytest.raises(ImportStagingError, match="unsafe|unavailable"):
        verify_registered_artifact(artifact, principal_id="alice")


def test_cm6_007_rejects_registered_artifact_retarget_before_schedule(tmp_path: Path) -> None:
    path = tmp_path / "input.pdb"
    path.write_bytes(PDB)
    artifact = _registered(tmp_path)
    path.write_bytes(PDB + b"REMARK changed\n")
    with pytest.raises(ImportStagingError, match="identity changed"):
        stage_registered_artifacts([artifact], principal_id="alice", request_id="r", destination_root=tmp_path / "staged")
    assert not (tmp_path / "staged").exists()


def test_cm6_008_regular_file_and_content_match(tmp_path: Path) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    staged = stage_registered_artifacts([_registered(tmp_path)], principal_id="alice", request_id="r", destination_root=tmp_path / "staged")
    assert staged.root.joinpath(staged.receipt["entries"][0]["destination_relative_path"]).read_bytes() == PDB


def test_cm6_009_rehash_after_copy(tmp_path: Path) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    staged = stage_registered_artifacts([_registered(tmp_path)], principal_id="alice", request_id="r", destination_root=tmp_path / "staged")
    entry = staged.receipt["entries"][0]
    assert entry["source_content_sha256"] == entry["staged_content_sha256"] == hashlib.sha256(PDB).hexdigest()


def test_cm6_010_limits_and_collision_safe_names(tmp_path: Path) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    staged = stage_registered_artifacts([_registered(tmp_path)], principal_id="alice", request_id="r", destination_root=tmp_path / "staged")
    assert staged.receipt["entries"][0]["destination_relative_path"].startswith("structures/000000_")
    fifo = tmp_path / "pipe.pdb"
    os.mkfifo(fifo)
    bad = RegisteredArtifact("pipe", "alice", tmp_path, fifo.name, "0" * 64, 0)
    with pytest.raises(ImportStagingError, match="regular"):
        verify_registered_artifact(bad, principal_id="alice")


def test_cm6_011_authorized_registered_id(tmp_path: Path) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    artifact = _registered(tmp_path)
    assert verify_registered_artifact(artifact, principal_id="alice") == (artifact.content_sha256, artifact.size_bytes)
    with pytest.raises(ImportStagingError, match="authorized"):
        verify_registered_artifact(artifact, principal_id="mallory")


def test_cm6_012_immutable_receipt_and_import_identity(tmp_path: Path) -> None:
    source_bytes = MMCIF
    (tmp_path / "input.cif").write_bytes(source_bytes)
    request_id = "5bd7e715-6f73-4e6a-a270-09f486d1da86"
    staged = stage_registered_artifacts(
        [_registered(tmp_path, "input.cif")], principal_id="alice",
        request_id=request_id, destination_root=tmp_path / "staged",
    )
    entry = staged.receipt["entries"][0]
    coordinates = {
        "backend": "external_import", "target_id": "t", "staged_index": 0,
        "source_content_sha256": entry["source_content_sha256"],
        "staged_receipt_sha256": staged.receipt["receipt_sha256"],
    }
    stable_id = candidate_id(coordinates)
    snapshot = build_import_snapshot_from_mmcif(
        source_bytes, target_id="t", candidate_id=stable_id,
        original_source_path=f"registered_import/{entry['destination_relative_path']}",
    )
    request_without_hash = {
        "schema_name": "cm_request", "schema_version": 1, "request_id": request_id,
        "backend": "external_import", "targets": [{"target_id": "t", "target_order": 0}],
        "ordered_seeds": [0], "samples_per_seed": 1,
        "feature_policy": {"mode": "features_disabled_control_v1"},
        "runtime_policy": {"use_default_params": True},
        "analysis_policy": {
            "sign_zero_epsilon": 1e-6, "clash_detector_id": "bms_clash",
            "clash_detector_version": "1", "outer_support_minimum": 1.0,
            "inner_support_minimum": 1.0, "sign_consistency_minimum": 1.0,
            "clash_free_minimum": 1.0, "rank_stability_minimum": 1.0,
            "minimum_common_ranked_universe_size": 3,
        },
        "import_receipt_id": staged.receipt["receipt_sha256"],
        "source_snapshot_sha256": canonical_sha256(snapshot),
        "source": {"kind": "api_submission_v1", "sha256": "a" * 64},
        "created_by": {"principal_id": "alice"},
    }
    request = {**request_without_hash, "request_sha256": canonical_sha256(request_without_hash)}
    native, ensemble = finalize_staged_import(
        request, snapshot, staged.root, tmp_path / "canonical"
    )
    assert ensemble["expected_cardinality"] == 1
    assert ensemble["source_snapshot_sha256"] == canonical_sha256(snapshot)
    assert native["files"][0]["backend_coordinates"]["staged_receipt_sha256"] == staged.receipt["receipt_sha256"]

    bound_mismatch = copy.deepcopy(snapshot)
    bound_mismatch["admission"]["atom_count"] += 1
    bound_mismatch["normalized_source_sha256"] = normalized_import_snapshot_sha256(bound_mismatch)
    with pytest.raises(ImportStagingError, match="request-bound"):
        finalize_staged_import(request, bound_mismatch, staged.root, tmp_path / "bound-mismatch")

    normalized_mismatch = copy.deepcopy(snapshot)
    normalized_mismatch["normalized_source_sha256"] = "0" * 64
    normalized_request_without_hash = {
        **{key: value for key, value in request.items() if key != "request_sha256"},
        "source_snapshot_sha256": canonical_sha256(normalized_mismatch),
    }
    normalized_request = {
        **normalized_request_without_hash,
        "request_sha256": canonical_sha256(normalized_request_without_hash),
    }
    with pytest.raises(ImportStagingError, match="normalized identity"):
        finalize_staged_import(
            normalized_request, normalized_mismatch, staged.root, tmp_path / "normalized-mismatch",
        )

    source_mismatch = copy.deepcopy(snapshot)
    source_mismatch["original_source_sha256"] = "0" * 64
    source_mismatch["normalized_source_sha256"] = normalized_import_snapshot_sha256(source_mismatch)
    source_request_without_hash = {
        **{key: value for key, value in request.items() if key != "request_sha256"},
        "source_snapshot_sha256": canonical_sha256(source_mismatch),
    }
    source_request = {
        **source_request_without_hash,
        "request_sha256": canonical_sha256(source_request_without_hash),
    }
    with pytest.raises(ImportStagingError, match="source identity"):
        finalize_staged_import(source_request, source_mismatch, staged.root, tmp_path / "source-mismatch")


    bad_receipt_without_hash = {
        **{key: value for key, value in request.items() if key != "request_sha256"},
        "import_receipt_id": "0" * 64,
    }
    bad_receipt_request = {
        **bad_receipt_without_hash,
        "request_sha256": canonical_sha256(bad_receipt_without_hash),
    }
    with pytest.raises(ImportStagingError, match="receipt identity"):
        finalize_staged_import(
            bad_receipt_request, snapshot, staged.root, tmp_path / "receipt-mismatch",
        )

    order_mismatch = copy.deepcopy(snapshot)
    order_mismatch["target_order"] = 1
    order_mismatch["normalized_source_sha256"] = normalized_import_snapshot_sha256(order_mismatch)
    order_request_without_hash = {
        **{key: value for key, value in request.items() if key != "request_sha256"},
        "source_snapshot_sha256": canonical_sha256(order_mismatch),
    }
    order_request = {
        **order_request_without_hash,
        "request_sha256": canonical_sha256(order_request_without_hash),
    }
    with pytest.raises(ImportStagingError, match="cardinality"):
        finalize_staged_import(order_request, order_mismatch, staged.root, tmp_path / "order-mismatch")


def test_cm6_013_size_bound_applies_before_staging_copy(tmp_path: Path) -> None:
    payload = PDB + b"X" * 32
    (tmp_path / "input.pdb").write_bytes(payload)
    with pytest.raises(ImportStagingError, match="byte limit"):
        stage_registered_artifacts(
            [_registered(tmp_path)], principal_id="alice", request_id="r",
            destination_root=tmp_path / "staged", maximum_bytes=len(PDB),
        )
    assert not (tmp_path / "staged").exists()


def test_second_copy_pass_enforces_bound_before_writing_mutated_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.pdb"
    source.write_bytes(PDB)
    artifact = _registered(tmp_path)
    real_digest_fd = import_stager._digest_fd
    written_sizes: list[int] = []

    def digest_then_grow(descriptor: int, *, maximum_bytes: int):
        result = real_digest_fd(descriptor, maximum_bytes=maximum_bytes)
        with source.open("ab") as stream:
            stream.write(b"X" * 4096)
        return result

    def record_write(_descriptor: int, payload: bytes) -> None:
        written_sizes.append(len(payload))

    monkeypatch.setattr(import_stager, "_digest_fd", digest_then_grow)
    monkeypatch.setattr(import_stager, "_write_all", record_write)
    with pytest.raises(ImportStagingError, match="byte limit during staging"):
        stage_registered_artifacts(
            [artifact], principal_id="alice", request_id="copy-bound",
            destination_root=tmp_path / "staged-copy-bound",
            maximum_bytes=len(PDB) + 1,
        )
    assert written_sizes == []
    assert not (tmp_path / "staged-copy-bound").exists()


def test_runtime_asset_second_copy_pass_enforces_bound_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.pdb"
    source.write_bytes(PDB)
    artifact = _registered(tmp_path)
    real_digest_fd = import_stager._digest_fd
    written_sizes: list[int] = []

    def digest_then_grow(descriptor: int, *, maximum_bytes: int):
        result = real_digest_fd(descriptor, maximum_bytes=maximum_bytes)
        with source.open("ab") as stream:
            stream.write(b"X" * 4096)
        return result

    def record_write(_descriptor: int, payload: bytes) -> None:
        written_sizes.append(len(payload))

    monkeypatch.setattr(import_stager, "_digest_fd", digest_then_grow)
    monkeypatch.setattr(import_stager, "_write_all", record_write)
    with pytest.raises(ImportStagingError, match="byte limit during staging"):
        import_stager.stage_registered_assets(
            [artifact], principal_id="alice",
            destination_root=tmp_path / "runtime-copy-bound",
            maximum_bytes=len(PDB) + 1,
        )
    assert written_sizes == []
    assert not (tmp_path / "runtime-copy-bound").exists()
