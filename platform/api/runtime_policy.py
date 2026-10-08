from __future__ import annotations

import logging
import os
from typing import Any

CORE_RUNTIME_MODE_ENV = "BMS_CORE_RUNTIME_MODE"
logger = logging.getLogger(__name__)


TRUE_STRINGS = {"1", "true", "yes", "on"}
FALSE_STRINGS = {"0", "false", "no", "off"}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_STRINGS:
            return True
        if normalized in FALSE_STRINGS:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def core_runtime_mode_enabled() -> bool:
    raw_value = os.getenv(CORE_RUNTIME_MODE_ENV)
    if raw_value is None:
        return False

    normalized = raw_value.strip().lower()
    if not normalized:
        return False
    if normalized in TRUE_STRINGS:
        return True
    if normalized in FALSE_STRINGS:
        return False

    logger.warning(
        "Invalid %s=%r; defaulting to enabled for safety in core-runtime mode.",
        CORE_RUNTIME_MODE_ENV,
        raw_value,
    )
    return True


def workflow_launches_allowed() -> bool:
    return not core_runtime_mode_enabled()


def workflow_launch_block_detail(action: str = "launch workflows") -> str:
    normalized_action = str(action or "launch workflows").strip()
    return (
        f"Cannot {normalized_action} while BioModStack is running in core-runtime container mode. "
        "This first-wave container runtime does not yet own Nextflow/workflow execution truth; "
        "use the host-native BioModStack runtime for workflow launches, resumes, and resubmits."
    )


def assert_workflow_launch_allowed(action: str = "launch workflows") -> None:
    if not workflow_launches_allowed():
        raise RuntimeError(workflow_launch_block_detail(action))
