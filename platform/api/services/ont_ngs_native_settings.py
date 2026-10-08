"""Immutable native execution settings, independent of GPU lease cleanup.

The receipt retains the exact validated execution parameters (including physical
placement). Only the global owner's deterministic release transformation may
separate those bytes from Job.params. No receipt is repaired by a reader.
"""
from copy import deepcopy
import hashlib

import rfc8785

from services.execution_ownership import ExecutionOwnershipError, release_scheduler_gpu_assignment

SCHEMA = "bms.ngs.native-effective-params.v1"


def _sha256(value):
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _released(params):
    try:
        return release_scheduler_gpu_assignment(params)
    except ExecutionOwnershipError as exc:
        raise ValueError("native scheduler settings authority is malformed") from exc


def seal_native_settings(params):
    """Return receipt fields; publication remains the terminal CAS owner's job."""
    effective = deepcopy(params)
    # Validate scheduler metadata now, not for the first time during reopen.
    _released(effective)
    return {
        "effective_params_schema": SCHEMA,
        "effective_params": effective,
        "effective_params_sha256": _sha256(effective),
    }


def accepted_native_settings(params, receipt):
    """Verify exact execution or its exact released form; return frozen settings.

    Pre-seal receipts retain their original strict whole-params check. In
    particular, a historically broken GPU receipt is not silently migrated.
    """
    if "effective_params_schema" not in receipt and "effective_params" not in receipt:
        if _sha256(params) != receipt.get("effective_params_sha256"):
            raise ValueError("native effective settings authority changed")
        return deepcopy(params)
    effective = receipt.get("effective_params")
    if receipt.get("effective_params_schema") != SCHEMA or not isinstance(effective, dict):
        raise ValueError("native effective settings authority is malformed")
    digest = _sha256(effective)
    if digest != receipt.get("effective_params_sha256"):
        raise ValueError("native effective settings authority changed")
    current_digest = _sha256(params)
    released_digest = _sha256(_released(effective))
    if current_digest not in {digest, released_digest}:
        raise ValueError("native effective settings authority changed")
    return deepcopy(effective)
