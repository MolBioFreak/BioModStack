from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

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
    lane = workflow_adapter_lane(required=False)
    if lane is not None:
        try:
            normalized = validate_adapter_url_for_lane(normalized, lane)
        except LaneMismatchError:
            raise
    return normalized or None


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
    host_state_dir = str(os.getenv("BMS_STATE_DIR") or resolved.get("data_root") or "")
    container_state_path = str(os.getenv("BMS_CONTAINER_STATE_PATH") or resolved.get("container_state_path") or "")
    inputs_container_path = str(os.getenv("BMS_INPUTS_CONTAINER_PATH") or resolved.get("inputs_container_path") or "")
    db_container_path = str(os.getenv("BMS_DB_CONTAINER_PATH") or resolved.get("db_container_path") or "")

    mappings = [
        (db_container_path.rstrip("/"), str(Path(host_state_dir) / "biomodstack.db") if host_state_dir else ""),
        (inputs_container_path.rstrip("/"), str(Path(host_state_dir) / "inputs") if host_state_dir else ""),
        (container_state_path.rstrip("/"), host_state_dir),
    ]
    mappings = [(container_root, host_root) for container_root, host_root in mappings if container_root and host_root]
    mappings.sort(key=lambda pair: len(pair[0]), reverse=True)

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
