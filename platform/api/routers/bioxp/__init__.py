"""Compact, typed BioXP API. No arbitrary robot proxy paths are exposed."""

from fastapi import APIRouter, Depends

from . import commands, connection, jobs, protocols
from .dependencies import (
    SAFE_LOCAL_MUTATIONS,
    _BIOXP_OPERATOR_HEADER,
    require_bioxp_mutation_access,
)

router = APIRouter(dependencies=[Depends(require_bioxp_mutation_access)])
router.include_router(connection.router)
router.include_router(protocols.router)
router.include_router(jobs.router)
router.include_router(commands.router)

__all__ = [
    "SAFE_LOCAL_MUTATIONS",
    "_BIOXP_OPERATOR_HEADER",
    "require_bioxp_mutation_access",
    "router",
]
