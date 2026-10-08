from __future__ import annotations

import asyncio
import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from biomodstack_runtime_profile import resolve_runtime_paths
from .execution_ownership import (
    LaneMismatchError,
    configured_lane,
    lane_for_runtime_mode,
    validate_adapter_url_for_lane,
)


DEFAULT_ADAPTER_TIMEOUT_SECONDS = 15.0
_TRUE_STRINGS = {"1", "true", "yes", "on"}
_FALSE_STRINGS = {"0", "false", "no", "off"}

ADAPTER_ADMISSION_SCHEMA = "bms.workflow-adapter-admission.v1"
ADMISSION_TIMEOUT_ENV = "BMS_WORKFLOW_ADAPTER_ADMISSION_TIMEOUT_SECONDS"
ADMISSION_STRICT_ENV = "BMS_WORKFLOW_ADAPTER_ADMISSION_STRICT"
ADMISSION_MAX_POLL_SECONDS = 5.0
DEFAULT_ADMISSION_TIMEOUT_SECONDS = 120.0
# Dispatch re-probes are bounded well under the scheduler's poll interval so a
# down adapter delays new launches without stalling the fleet loop.
DISPATCH_ADMISSION_TIMEOUT_SECONDS = 3.0
ADMISSION_OBSERVATION_MAX_AGE_SECONDS = 5.0

_admission_lock = threading.Lock()
_admission_receipt: dict[str, Any] = {
    "schema": ADAPTER_ADMISSION_SCHEMA,
    "configured": False,
    "admitted": None,
    "attempts": 0,
    "last_status": "unobserved",
    "deadline_seconds": None,
    "observed_at": None,
    "detail": "Workflow adapter admission has not been observed in this process",
}


def workflow_adapter_admission() -> dict[str, Any]:
    """This process's latest scheduler-admission observation."""
    with _admission_lock:
        return dict(_admission_receipt)


def record_workflow_adapter_admission(**fields: Any) -> dict[str, Any]:
    with _admission_lock:
        _admission_receipt.update(fields)
        _admission_receipt["observed_at"] = datetime.utcnow().isoformat()
        return dict(_admission_receipt)


def workflow_adapter_admission_timeout_seconds() -> float:
    raw = os.getenv(ADMISSION_TIMEOUT_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_ADMISSION_TIMEOUT_SECONDS
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return DEFAULT_ADMISSION_TIMEOUT_SECONDS


def workflow_adapter_admission_strict() -> bool:
    return str(os.getenv(ADMISSION_STRICT_ENV, "")).strip().lower() in _TRUE_STRINGS


def _admission_age_seconds(receipt: dict[str, Any]) -> float | None:
    raw = receipt.get("observed_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        observed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if observed.tzinfo is not None:
        observed = observed.replace(tzinfo=None)
    return (datetime.utcnow() - observed).total_seconds()


async def run_workflow_adapter_admission(
    *,
    base_url: str | None,
    probe: Callable[[str], Awaitable[tuple[bool, str]]],
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = 0.25,
    strict: bool | None = None,
) -> dict[str, Any]:
    """Observe adapter liveness with bounded exponential backoff.

    Never refuses admission by default: an API that cannot finish starting is
    the only observer of its own in-flight attempts, so a momentary adapter
    outage must degrade dispatch, not the process. ``strict`` (or
    BMS_WORKFLOW_ADAPTER_ADMISSION_STRICT) keeps the old fail-closed startup for
    operators who want it.
    """
    strict = workflow_adapter_admission_strict() if strict is None else bool(strict)
    if not base_url:
        return record_workflow_adapter_admission(
            configured=False, admitted=True, attempts=0, last_status="not_configured",
            deadline_seconds=None,
            detail="No workflow adapter is configured; the local lane owns dispatch",
        )
    health_url = f"{base_url.rstrip('/')}/api/workflow-adapter/health"
    window = workflow_adapter_admission_timeout_seconds() if timeout_seconds is None else max(0.0, float(timeout_seconds))
    interval = max(0.0, float(poll_interval_seconds))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + window
    attempts = 0
    last_status = "unavailable"
    while True:
        ready, last_status = await probe(health_url)
        attempts += 1
        if ready:
            return record_workflow_adapter_admission(
                configured=True, admitted=True, attempts=attempts, last_status=last_status,
                deadline_seconds=window, detail="Workflow adapter answered health probes",
            )
        remaining = deadline - loop.time()
        if remaining <= 0 or interval <= 0:
            break
        await asyncio.sleep(min(interval, remaining))
        interval = min(max(interval * 2, 0.01), ADMISSION_MAX_POLL_SECONDS)
    detail = (
        f"Workflow adapter did not answer health probes within {window:g}s; "
        "scheduler dispatch stays closed until it does, and this process keeps "
        "reconciling the attempts it already owns"
    )
    receipt = record_workflow_adapter_admission(
        configured=True, admitted=False, attempts=attempts, last_status=last_status,
        deadline_seconds=window, detail=detail,
    )
    if strict:
        raise RuntimeError(
            f"Configured workflow adapter is not ready for scheduler admission: {last_status}"
        )
    return receipt


async def workflow_adapter_dispatch_admitted(*, probe=None) -> bool:
    """Fail-closed dispatch gate: closed until the adapter answers a probe.

    Existing attempts are never touched here; only new launches wait.
    """
    base_url = workflow_adapter_base_url()
    if base_url is None:
        return True  # No adapter: the local lane owns dispatch.
    receipt = workflow_adapter_admission()
    age = _admission_age_seconds(receipt)
    if age is not None and age < ADMISSION_OBSERVATION_MAX_AGE_SECONDS:
        return bool(receipt.get("admitted"))
    if probe is None:
        from readiness import http_readiness

        probe = http_readiness
    refreshed = await run_workflow_adapter_admission(
        base_url=base_url, probe=probe,
        timeout_seconds=DISPATCH_ADMISSION_TIMEOUT_SECONDS, poll_interval_seconds=0.5, strict=False,
    )
    return bool(refreshed.get("admitted"))


class WorkflowAdapterRequestError(RuntimeError):
    def __init__(self, *, status_code: int, detail: Any, url: str) -> None:
        self.status_code = int(status_code)
        self.detail = detail
        self.url = url
        super().__init__(f"Workflow adapter request to {url} failed with status {status_code}: {detail!r}")


def workflow_adapter_base_url() -> str | None:
    raw_value = os.getenv("BMS_WORKFLOW_ADAPTER_URL")
    if raw_value is None:
        return None
    normalized = raw_value.strip().rstrip("/")
    if not normalized:
        return None
    lane = workflow_adapter_lane(required=False)
    if lane is not None:
        try:
            normalized = validate_adapter_url_for_lane(normalized, lane)
        except LaneMismatchError:
            raise
    return normalized


def workflow_adapter_lane(*, required: bool = False) -> str | None:
    """Return the caller's explicit adapter lane, if one is configured."""
    raw_lane = os.getenv("BMS_WORKFLOW_ADAPTER_LANE")
    if raw_lane is not None and raw_lane.strip():
        return configured_lane(required=True)
    runtime_mode = os.getenv("BMS_RUNTIME_MODE")
    if runtime_mode is not None and runtime_mode.strip():
        return lane_for_runtime_mode(runtime_mode)
    if required:
        return configured_lane(required=True)
    return None



def workflow_adapter_enabled() -> bool:
    return workflow_adapter_base_url() is not None



def _core_runtime_mode_enabled() -> bool:
    raw_value = os.getenv("BMS_CORE_RUNTIME_MODE")
    if raw_value is None:
        return False

    normalized = raw_value.strip().lower()
    if not normalized:
        return False
    if normalized in _TRUE_STRINGS:
        return True
    if normalized in _FALSE_STRINGS:
        return False
    return True



def workflow_launch_mode() -> str:
    if workflow_adapter_enabled():
        return "adapter"
    if _core_runtime_mode_enabled():
        return "guarded"
    return "native"



def _decode_json_response(raw_body: str, *, url: str) -> Any:
    try:
        return json.loads(raw_body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Workflow adapter returned invalid JSON for {url}: {raw_body!r}") from exc



def request_via_workflow_adapter(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_ADAPTER_TIMEOUT_SECONDS,
) -> Any:
    base_url = workflow_adapter_base_url()
    if not base_url:
        raise RuntimeError("BMS_WORKFLOW_ADAPTER_URL is not configured")

    url = f"{base_url}{path}"
    data = None
    headers: dict[str, str] = {}
    lane = workflow_adapter_lane(required=False)
    if lane is not None:
        headers["X-BMS-Workflow-Adapter-Lane"] = lane
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=float(timeout_seconds)) as response:
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        detail: Any = raw_body
        if raw_body:
            try:
                detail = _decode_json_response(raw_body, url=url)
            except RuntimeError:
                detail = raw_body
        raise WorkflowAdapterRequestError(status_code=exc.code, detail=detail, url=url) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Workflow adapter request to {url} failed: {exc}") from exc

    return _decode_json_response(raw_body, url=url)



def _request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = DEFAULT_ADAPTER_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    parsed = request_via_workflow_adapter(
        method,
        path,
        payload,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(parsed, dict):
        base_url = workflow_adapter_base_url() or "<unconfigured>"
        raise RuntimeError(f"Workflow adapter returned non-object JSON for {base_url}{path}: {parsed!r}")
    return parsed



def _container_to_host_path(value: str) -> str:
    if not value:
        return value

    resolved = resolve_runtime_paths()
    from biomodstack_runtime_profile import core_runtime_path_mappings
    mappings = core_runtime_path_mappings(resolved, os.environ)

    for container_root, host_root in mappings:
        if value == container_root:
            return host_root
        prefix = f"{container_root}/"
        if value.startswith(prefix):
            suffix = value[len(prefix):]
            return str(Path(host_root) / suffix)
    return value



def _translate_container_paths(value: Any) -> Any:
    if isinstance(value, str):
        return _container_to_host_path(value)
    if isinstance(value, list):
        return [_translate_container_paths(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_translate_container_paths(item) for item in value)
    if isinstance(value, dict):
        return {key: _translate_container_paths(item) for key, item in value.items()}
    return value



def launch_via_workflow_adapter(
    *,
    job_id: str,
    model_id: str,
    mode: str,
    params: dict[str, Any],
    output_dir: str,
) -> dict[str, Any]:
    translated_params = _translate_container_paths(params)
    translated_output_dir = _container_to_host_path(output_dir)
    payload: dict[str, Any] = {
        "job_id": job_id,
        "model_id": model_id,
        "mode": mode,
        "params": translated_params,
        "output_dir": translated_output_dir,
    }
    lane = workflow_adapter_lane(required=False)
    if lane is not None:
        # The receiving adapter rejects a mismatched lane before it can claim a
        # deterministic systemd unit.
        payload["lane"] = lane
    return _request_json(
        "POST",
        "/api/workflow-adapter/launch",
        payload,
    )



def cancel_via_workflow_adapter(nextflow_run_id: str, *, graceful_timeout_seconds: float = 5.0) -> bool:
    response = _request_json(
        "POST",
        "/api/workflow-adapter/cancel",
        {
            "nextflow_run_id": nextflow_run_id,
            "graceful_timeout_seconds": float(graceful_timeout_seconds),
        },
        timeout_seconds=max(DEFAULT_ADAPTER_TIMEOUT_SECONDS, float(graceful_timeout_seconds) + 5.0),
    )
    return bool(response.get("cancelled", False))



def get_adapter_running_jobs() -> dict[str, int | str]:
    response = _request_json("GET", "/api/workflow-adapter/running-jobs")
    running_jobs = response.get("running_jobs", {})
    if not isinstance(running_jobs, dict):
        raise RuntimeError(f"Workflow adapter returned invalid running_jobs payload: {running_jobs!r}")
    normalized: dict[str, int | str] = {}
    for job_id, run_id in running_jobs.items():
        try:
            normalized[str(job_id)] = int(run_id)
        except (TypeError, ValueError):
            if isinstance(run_id, str) and run_id.strip():
                normalized[str(job_id)] = run_id.strip()
            else:
                raise RuntimeError(
                    f"Workflow adapter returned invalid run id for {job_id!r}: {run_id!r}"
                )
    return normalized
