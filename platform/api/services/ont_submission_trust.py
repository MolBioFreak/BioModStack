"""Internal trust boundary for canonical ONT job creation."""

from __future__ import annotations

from contextvars import ContextVar, Token

ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS = frozenset(
    {
        "bam_reference_sha256",
        "bam_source_sha256",
        "reference_sequence_sha256",
        "source_barcode_manifest_sha256",
        "source_barcode_unit",
        "source_ont_job_id",
        "source_instrument_artifact_manifest_sha256",
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
