"""Internal trust boundary for canonical ONT job creation."""

from __future__ import annotations

from contextvars import ContextVar, Token
import hashlib
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from paths import get_inputs_dir

ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS = frozenset(
    {
        "bam_reference_sha256",
        "bam_source_sha256",
        "expected_reference_fasta_sha256",
        "expected_result_manifest_schema",
        "global_domain_experiment_id",
        "molbio_ngs_state_revision_id",
        "ngs_reference_artifact_id",
        "ngs_reference_id",
        "ngs_reference_revision_id",
        "selected_reference_sha256",
        "state_membership_receipt_id",
        "managed_reference_snapshot_sha256",
        "managed_reference_snapshot_size_bytes",
        "reference_sequence_sha256",
        "dataset_id",
        "source_external_move_registration_receipt_id",
        "source_filtered_move_bam_sha256",
        "source_barcode_manifest_sha256",
        "source_barcode_unit",
        "source_ont_job_id",
        "source_instrument_run_id",
        "source_minknow_run_id",
        "source_instrument_observed_generation",
        "source_instrument_artifact_manifest_sha256",
        "source_barcode_units_manifest_sha256",
        "source_barcode_unit_manifest_sha256",
        "source_barcode_source_calls_sha256",
        "source_barcode_preflight_sha256",
        "source_barcode_demux_manifest_sha256",
        "reference_set_binding",
        "ngs_reference_set_binding",
        "barcode_mapping_binding",
        "molbio_revision_binding",
        "ont_instrument_run_receipt_id",
        "ont_instrument_run_binding",
        "source_instrument_artifact_sha256",
        "source_instrument_artifact_bytes",
        "source_move_bam_sha256",
        "source_move_source_id",
        "source_raw_representation_id",
        "source_read_inventory_sha256",
    }
)

ONT_SERVER_CONTROLLED_RUNTIME_PARAMS = frozenset(
    {
        "code_root",
        "container_dir",
        "data_root",
        "dorado_lock_manifest",
        "dorado_lock_sha256",
        "dorado_device",
        "dorado_model_root",
        "dorado_preflight",
        "dorado_resolved_model_id",
        "dorado_runtime_sif",
        "dorado_stereo_model",
        "managed_reference_fasta_path",
        "managed_reference_path",
        "msa_cache_dir",
        "msa_local_db",
        "nxf_home",
        "out_dir",
        "output_dir",
        "pod5_python",
        "resume_source_dir",
        "resume_work_dir",
        "singularity_cache",
        "weights_root",
        "wf_clone_nxf_home",
        "wf_clone_lock_manifest",
        "wf_clone_profile",
        "wf_clone_revision",
        "wf_clone_runtime_provenance",
        "wf_clone_singularity_cache",
        "wf_clone_source",
        "wf_clone_workflow_dir",
        "work_dir",
    }
)

ONT_SERVER_CONTROLLED_PARAMS = ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS | ONT_SERVER_CONTROLLED_RUNTIME_PARAMS

_trusted_ont_job_creation: ContextVar[bool] = ContextVar("trusted_ont_job_creation", default=False)
_alignment_capability_digest: ContextVar[str | None] = ContextVar("ont_alignment_capability_digest", default=None)


def begin_trusted_ont_job_creation(capability_digest: str | None = None) -> tuple[Token[bool], Token[str | None]]:
    return (
        _trusted_ont_job_creation.set(True),
        _alignment_capability_digest.set(capability_digest),
    )


def end_trusted_ont_job_creation(tokens: tuple[Token[bool], Token[str | None]]) -> None:
    trust_token, digest_token = tokens
    _alignment_capability_digest.reset(digest_token)
    _trusted_ont_job_creation.reset(trust_token)


def is_trusted_ont_job_creation() -> bool:
    return _trusted_ont_job_creation.get()


def alignment_capability_digest() -> str | None:
    return _alignment_capability_digest.get()


def _canonical_absolute_path(value: Any) -> Path | None:
    raw = str(value or "").strip()
    if not raw or len(raw) > 4096 or not os.path.isabs(raw):
        return None
    if any(component in {".", ".."} for component in raw.split(os.sep)):
        return None
    return Path(os.path.abspath(raw))


def _descriptor_flags() -> tuple[int, int]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    if (
        not isinstance(nofollow, int)
        or not isinstance(directory, int)
        or not isinstance(cloexec, int)
    ):
        raise ValueError("launch snapshot verification requires no-follow descriptor support")
    return os.O_RDONLY | nofollow | cloexec, directory


def _open_runtime_snapshot(
    path: Path, root: Path, *, label: str = "ONT instrument artifact"
) -> int:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} snapshot is outside the server-owned launch root") from exc
    if not relative.parts:
        raise ValueError(f"{label} snapshot path is invalid")
    flags, directory_flag = _descriptor_flags()
    directory_flags = flags | directory_flag
    try:
        root_fd = os.open(os.sep, directory_flags)
    except (AttributeError, NotImplementedError, OSError) as exc:
        raise ValueError(f"{label} snapshot descriptor verification is unavailable") from exc
    current_fd = root_fd
    try:
        for component in root.parts[1:]:
            child_fd = os.open(component, directory_flags, dir_fd=current_fd)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = child_fd
        for component in relative.parts[:-1]:
            child_fd = os.open(component, directory_flags, dir_fd=current_fd)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = child_fd
        file_fd = os.open(relative.parts[-1], flags, dir_fd=current_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            os.close(file_fd)
            raise ValueError(f"{label} snapshot is not a regular file")
        return file_fd
    except (AttributeError, NotImplementedError) as exc:
        raise ValueError(
            f"{label} snapshot descriptor verification is unavailable"
        ) from exc
    finally:
        if current_fd != root_fd:
            os.close(current_fd)
        os.close(root_fd)


def verify_instrument_artifact_snapshot(
    params: Mapping[str, Any],
    *,
    snapshot_root: Path | None = None,
) -> None:
    """Fail closed if a launch-bound ONT snapshot no longer matches its persisted authority."""
    authority_fields = {
        "source_instrument_run_id",
        "source_instrument_observed_generation",
        "source_instrument_artifact_manifest_sha256",
        "source_instrument_artifact_sha256",
        "source_instrument_artifact_bytes",
    }
    present = {key for key in authority_fields if params.get(key) is not None}
    if not present:
        return
    if present != authority_fields:
        raise ValueError("ONT instrument artifact snapshot authority is incomplete")

    run_id = params.get("source_instrument_run_id")
    generation = params.get("source_instrument_observed_generation")
    manifest_sha256 = params.get("source_instrument_artifact_manifest_sha256")
    expected_sha256 = params.get("source_instrument_artifact_sha256")
    expected_bytes = params.get("source_instrument_artifact_bytes")
    is_sha256 = lambda value: (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
    if (
        not isinstance(run_id, str)
        or not run_id
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or not is_sha256(manifest_sha256)
        or not is_sha256(expected_sha256)
        or isinstance(expected_bytes, bool)
        or not isinstance(expected_bytes, int)
        or expected_bytes < 0
    ):
        raise ValueError("ONT instrument artifact snapshot authority is invalid")

    configured_root = snapshot_root or (get_inputs_dir() / "ont_instrument_launch_snapshots")
    root = _canonical_absolute_path(configured_root)
    path = _canonical_absolute_path(params.get("fastq_path"))
    if root is None or path is None:
        raise ValueError("ONT instrument artifact snapshot path is invalid")
    file_fd = _open_runtime_snapshot(path, root)
    digest = hashlib.sha256()
    byte_count = 0
    try:
        while chunk := os.read(file_fd, 1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    finally:
        os.close(file_fd)
    if byte_count != expected_bytes:
        raise ValueError("ONT instrument artifact snapshot size mismatch before consumption")
    if digest.hexdigest() != expected_sha256:
        raise ValueError("ONT instrument artifact snapshot digest mismatch before consumption")


def verify_managed_reference_snapshot(
    params: Mapping[str, Any], *, snapshot_root: Path | None = None
) -> None:
    """Reverify the exact server-owned managed-reference launch snapshot."""

    authority_fields = {
        "global_domain_experiment_id",
        "molbio_ngs_state_revision_id",
        "ngs_reference_id",
        "ngs_reference_revision_id",
        "ngs_reference_artifact_id",
        "state_membership_receipt_id",
        "selected_reference_sha256",
        "expected_reference_fasta_sha256",
        "managed_reference_snapshot_sha256",
        "managed_reference_snapshot_size_bytes",
        "expected_result_manifest_schema",
    }
    present = {key for key in authority_fields if params.get(key) is not None}
    if not present:
        return
    if present != authority_fields:
        raise ValueError("managed reference snapshot authority is incomplete")
    is_sha256 = lambda value: (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
    identifiers = (
        params.get("global_domain_experiment_id"),
        params.get("molbio_ngs_state_revision_id"),
        params.get("ngs_reference_id"),
        params.get("ngs_reference_revision_id"),
        params.get("ngs_reference_artifact_id"),
        params.get("state_membership_receipt_id"),
        params.get("expected_result_manifest_schema"),
        params.get("ont_workflow_id"),
    )
    expected_bytes = params.get("managed_reference_snapshot_size_bytes")
    selected_sha256 = params.get("selected_reference_sha256")
    expected_reference_sha256 = params.get("expected_reference_fasta_sha256")
    snapshot_sha256 = params.get("managed_reference_snapshot_sha256")
    if (
        any(not isinstance(value, str) or not value for value in identifiers)
        or not is_sha256(selected_sha256)
        or selected_sha256 != expected_reference_sha256
        or not is_sha256(snapshot_sha256)
        or isinstance(expected_bytes, bool)
        or not isinstance(expected_bytes, int)
        or expected_bytes <= 0
    ):
        raise ValueError("managed reference snapshot authority is invalid")
    configured_root = snapshot_root or (
        get_inputs_dir() / "molbio_ngs_managed_launch_snapshots"
    )
    root = _canonical_absolute_path(configured_root)
    path = _canonical_absolute_path(params.get("reference_fasta"))
    if root is None or path is None:
        raise ValueError("managed reference snapshot path is invalid")
    file_fd = _open_runtime_snapshot(path, root, label="managed reference")
    digest = hashlib.sha256()
    byte_count = 0
    try:
        while chunk := os.read(file_fd, 1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    finally:
        os.close(file_fd)
    if byte_count != expected_bytes:
        raise ValueError("managed reference snapshot size mismatch before consumption")
    if digest.hexdigest() != snapshot_sha256:
        raise ValueError("managed reference snapshot digest mismatch before consumption")


def verify_launch_input_snapshots(params: Mapping[str, Any]) -> None:
    """Apply the shared final-boundary validation to every immutable input family."""

    verify_instrument_artifact_snapshot(params)
    verify_managed_reference_snapshot(params)
