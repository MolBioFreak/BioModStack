"""Shared authentication, ownership, and error helpers for Project / Global CM routers."""
from __future__ import annotations

import json
import os
import secrets
from collections.abc import Mapping

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import ExperimentAuditEvent, ExperimentResource
from experiment_operations import BackupNotFound, ExportNotFound
from experiment_services import (
    ExperimentServiceError,
    IdempotencyConflict,
    NotFound,
    RevisionConflict,
    ValidationFailure,
)

_APPLICATION_PROXY_HEADER = "X-BMS-CM-Proxy-Secret"


def _authenticated_principal(request: Request) -> tuple[str, frozenset[str]]:
    """Resolve the authenticated actor and roles at the existing application boundary."""
    principal = getattr(request.state, "authenticated_principal", None)
    if principal is None:
        configured = os.getenv("BMS_CM_TRUSTED_PROXY_SECRET", "")
        supplied = request.headers.get(_APPLICATION_PROXY_HEADER, "")
        if configured and supplied and secrets.compare_digest(configured, supplied):
            return "local-application-operator", frozenset({"operator"})
        raise HTTPException(status_code=401, detail="authenticated global CM principal required")
    if isinstance(principal, Mapping):
        actor = principal.get("id") or principal.get("subject")
        roles = principal.get("roles") or []
    else:
        actor = getattr(principal, "id", None) or getattr(principal, "subject", None)
        roles = getattr(principal, "roles", [])
    if isinstance(roles, str):
        roles = [roles]
    normalized_roles = frozenset(str(role).strip().lower() for role in roles)
    if not actor:
        raise HTTPException(status_code=403, detail="authenticated global CM actor required")
    return str(actor), normalized_roles


def _mutation_principal(request: Request) -> str:
    """Require an authenticated global-CM scientist, operator, or admin."""

    actor, normalized_roles = _authenticated_principal(request)
    if not normalized_roles.intersection({"scientist", "operator", "admin"}):
        raise HTTPException(status_code=403, detail="global CM scientist/operator role required")
    return actor


def _operator_principal(request: Request) -> str:
    """Require operator authority for a mutation that cannot bind one workspace owner."""

    actor, normalized_roles = _authenticated_principal(request)
    if not normalized_roles.intersection({"operator", "admin"}):
        raise HTTPException(status_code=403, detail="global CM operator/admin role required")
    return actor


async def _require_mutation_owner(
    request: Request,
    session: AsyncSession,
    *,
    resource_id: str,
) -> str:
    """Require the persisted owner binding for the target resource's workspace."""

    principal_id = _mutation_principal(request)
    resource = await session.get(ExperimentResource, resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="global CM mutation target not found")
    workspace_id = resource.id if resource.kind == "workspace" else resource.workspace_id
    if not workspace_id:
        raise HTTPException(status_code=404, detail="global CM mutation target not found")
    owner_events = (
        await session.execute(
            select(ExperimentAuditEvent).where(
                ExperimentAuditEvent.workspace_id == workspace_id,
                ExperimentAuditEvent.resource_id == workspace_id,
                ExperimentAuditEvent.event_type == "workspace_owner_bound",
            )
        )
    ).scalars().all()
    if len(owner_events) != 1:
        raise HTTPException(status_code=404, detail="global CM mutation target not found")
    try:
        owner_payload = json.loads(owner_events[0].payload_json)
    except (TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=404, detail="global CM mutation target not found")
    if not isinstance(owner_payload, dict) or owner_payload.get("principal_id") != principal_id:
        raise HTTPException(status_code=404, detail="global CM mutation target not found")
    return principal_id


def _error(exc: ExperimentServiceError) -> HTTPException:
    if isinstance(exc, (NotFound, BackupNotFound, ExportNotFound)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (RevisionConflict, IdempotencyConflict)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValidationFailure):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))
