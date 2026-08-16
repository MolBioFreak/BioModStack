"""Read-only status, list, and bounded detail API for retained ownership audits.

The module intentionally exports an importable ``router`` without mutating the
shared application entrypoint.  The owning API composition layer may mount it
when the N5 route table is registered.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from paths import get_data_root
from routers.experiment_workspaces import _operator_principal
from services.payload_ownership_audit import (
    PayloadOwnershipConfigurationError,
    RetainedAuditIntegrityError,
    RetainedAuditNotFound,
    RetainedAuditUnavailable,
    RetainedPayloadOwnershipAuditStore,
)

router = APIRouter(
    prefix="/api/operations/payload-ownership-audits",
    tags=["payload-ownership-audits"],
)
_MAX_RESPONSE_BYTES = 256 * 1024
_MAX_SUMMARY_BYTES = 256 * 1024


def _store() -> RetainedPayloadOwnershipAuditStore:
    configured = os.getenv("BMS_PAYLOAD_OWNERSHIP_AUDIT_DB_PATH")
    path = Path(configured).expanduser() if configured else get_data_root() / "payload-ownership-audits.db"
    return RetainedPayloadOwnershipAuditStore(path)


def _bounded(document: dict[str, Any], maximum: int) -> dict[str, Any]:
    encoded = json.dumps(
        document,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > maximum:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "response_too_large",
                "maximum_bytes": maximum,
            },
        )
    return document


def _read_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PayloadOwnershipConfigurationError):
        return HTTPException(status_code=422, detail={"code": "invalid_audit_query", "message": str(exc)})
    if isinstance(exc, RetainedAuditNotFound):
        return HTTPException(status_code=404, detail={"code": "audit_not_found"})
    if isinstance(exc, RetainedAuditIntegrityError):
        return HTTPException(
            status_code=503,
            detail={"code": "retained_audit_integrity_failure", "message": str(exc)},
        )
    return HTTPException(
        status_code=503,
        detail={"code": "retained_audit_unavailable", "message": str(exc)},
    )


@router.get("/status")
def payload_ownership_audit_status(request: Request) -> dict[str, Any]:
    _operator_principal(request)
    try:
        return _bounded(_store().status(), _MAX_SUMMARY_BYTES)
    except (RetainedAuditUnavailable, RetainedAuditIntegrityError) as exc:
        raise _read_error(exc) from exc


@router.get("")
def list_payload_ownership_audits(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=1024),
) -> dict[str, Any]:
    _operator_principal(request)
    try:
        return _bounded(_store().list(limit=limit, cursor=cursor), _MAX_SUMMARY_BYTES)
    except (
        PayloadOwnershipConfigurationError,
        RetainedAuditUnavailable,
        RetainedAuditIntegrityError,
    ) as exc:
        raise _read_error(exc) from exc


@router.get("/{audit_id}")
def get_payload_ownership_audit(
    audit_id: str,
    request: Request,
    finding_limit: int = Query(default=50, ge=1, le=100),
    finding_cursor: str | None = Query(default=None, max_length=1024),
) -> dict[str, Any]:
    _operator_principal(request)
    try:
        document = _store().detail(
            audit_id,
            finding_limit=finding_limit,
            finding_cursor=finding_cursor,
        )
        return _bounded(document, _MAX_RESPONSE_BYTES)
    except (
        PayloadOwnershipConfigurationError,
        RetainedAuditUnavailable,
        RetainedAuditNotFound,
        RetainedAuditIntegrityError,
    ) as exc:
        raise _read_error(exc) from exc
