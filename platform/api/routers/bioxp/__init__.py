"""Compact, typed BioXP API. No arbitrary robot proxy paths are exposed."""

from fastapi import APIRouter

from . import commands, connection, jobs, protocols
from .dependencies import (
    SAFE_LOCAL_MUTATIONS,
    _BIOXP_OPERATOR_HEADER,
    require_bioxp_mutation_access,
)

router = APIRouter()
for child_router in (
    connection.router,
    protocols.router,
    jobs.router,
    commands.router,
):
    router.routes.extend(child_router.routes)

__all__ = [
    "SAFE_LOCAL_MUTATIONS",
    "_BIOXP_OPERATOR_HEADER",
    "require_bioxp_mutation_access",
    "router",
]
