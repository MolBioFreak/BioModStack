from __future__ import annotations

from fastapi import HTTPException

from biomodstack_runtime_profile import install_feature_enabled


MD_MODEL_ID = "molecular_dynamics"
MD_FEATURE_NAME = "molecular_dynamics"
MD_FEATURE_DISABLED = "MD_FEATURE_DISABLED"
MD_FEATURE_DISABLED_MESSAGE = (
    "Molecular dynamics is a default-off experimental workflow. "
    "Enable BMS_FEATURE_MOLECULAR_DYNAMICS=1 to expose experimental MD."
)


class MolecularDynamicsFeatureDisabled(RuntimeError):
    """Raised before any durable or process side effect when MD is disabled."""

    code = MD_FEATURE_DISABLED

    def __init__(self) -> None:
        super().__init__(MD_FEATURE_DISABLED_MESSAGE)


def molecular_dynamics_feature_enabled() -> bool:
    """Resolve the single install/runtime gate for every first-class MD surface."""
    return install_feature_enabled(MD_FEATURE_NAME)


def ensure_molecular_dynamics_feature_enabled(model_id: str | None) -> None:
    """Fail closed for public MD access while leaving unrelated models untouched."""
    if str(model_id or "").strip() != MD_MODEL_ID:
        return
    if not molecular_dynamics_feature_enabled():
        raise MolecularDynamicsFeatureDisabled()


def require_molecular_dynamics_feature(model_id: str | None) -> None:
    """FastAPI ingress guard with a stable machine-readable error payload."""
    try:
        ensure_molecular_dynamics_feature_enabled(model_id)
    except MolecularDynamicsFeatureDisabled as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": exc.code,
                "message": str(exc),
                "experimental": True,
            },
        ) from exc
