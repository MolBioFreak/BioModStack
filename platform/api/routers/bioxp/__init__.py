"""Compact, typed BioXP API. No arbitrary robot proxy paths are exposed."""

from fastapi import APIRouter

from . import calibration, camera, connection, jobs, methods, operator_controls, protocols, workflows
from .dependencies import (
    CONNECTION_MUTATIONS,
    SAFE_LOCAL_MUTATIONS,
    require_bioxp_mutation_access,
)

router = APIRouter()
for child_router in (
    connection.router,
    camera.router,
    calibration.router,
    protocols.router,
    methods.router,
    jobs.router,
    operator_controls.router,
    workflows.router,
):
    router.routes.extend(child_router.routes)

__all__ = [
    "CONNECTION_MUTATIONS",
    "SAFE_LOCAL_MUTATIONS",
    "require_bioxp_mutation_access",
    "router",
]
