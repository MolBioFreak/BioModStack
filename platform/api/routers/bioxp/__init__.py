"""Compact, typed BioXP API. No arbitrary robot proxy paths are exposed."""

from fastapi import APIRouter

from . import commands, connection, jobs, oem_full_lifecycle, protocols
from .dependencies import (
    CONNECTION_MUTATIONS,
    SAFE_LOCAL_MUTATIONS,
    require_bioxp_mutation_access,
)

router = APIRouter()
for child_router in (
    connection.router,
    protocols.router,
    jobs.router,
    commands.router,
    oem_full_lifecycle.router,
):
    router.routes.extend(child_router.routes)

__all__ = [
    "CONNECTION_MUTATIONS",
    "SAFE_LOCAL_MUTATIONS",
    "require_bioxp_mutation_access",
    "router",
]
