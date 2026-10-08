from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database import Design, ExternalResultImport, Job
from paths import get_data_root
from services.external_imports.boltz_api import BoltzImportError, preview_boltz_api_run
from services.external_imports.service import queue_external_import


BOLTZ_API_MODEL = "boltz-2.1"
BOLTZ_API_PROVIDER = "boltz_api"
BOLTZ_API_POLL_INTERVAL_SECONDS = 15.0
TERMINAL_PROVIDER_STATES = {"failed", "stopped"}
SUPPORTED_COMPONENT_TYPES = {"protein", "peptide", "dna", "rna", "ligand", "ion"}
logger = logging.getLogger(__name__)


class BoltzApiJobError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 422, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _safe_job_name(value: str) -> str:
    normalized = "".join(char if char.isalnum() or char in "._-" else "_" for char in value.strip())
    return normalized[:80].strip("._-") or "boltz-api-prediction"


def _normalized_chain_ids(value: Any, fallback: str) -> list[str]:
    raw = value if isinstance(value, list) else [value]
    chain_ids = [str(item).strip() for item in raw if str(item or "").strip()]
    if not chain_ids:
        chain_ids = [fallback]
    if len(chain_ids) != len(set(chain_ids)):
        raise BoltzApiJobError("BOLTZ_API_INPUT_INVALID", "component chain identifiers must be unique")
    for chain_id in chain_ids:
        if not chain_id.isalnum() or len(chain_id) > 8:
            raise BoltzApiJobError("BOLTZ_API_INPUT_INVALID", f"invalid chain identifier: {chain_id!r}")
    return chain_ids


def build_boltz_api_input(
    *,
    sequence: str,
    primary_chain_id: str,
    complex_components: list[dict[str, Any]] | None,
    num_samples: int,
    use_msa: bool,
) -> dict[str, Any]:
    if not 1 <= int(num_samples) <= 10:
        raise BoltzApiJobError("BOLTZ_API_INPUT_INVALID", "num_samples must be between 1 and 10")

    components = list(complex_components or [])
    if not components:
        components = [{"type": "protein", "id": primary_chain_id or "A", "sequence": sequence}]

    entities: list[dict[str, Any]] = []
    used_chains: set[str] = set()
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            raise BoltzApiJobError("BOLTZ_API_INPUT_INVALID", f"component {index} must be an object")
        component_type = str(component.get("type") or "").strip().lower()
        if component_type not in SUPPORTED_COMPONENT_TYPES:
            raise BoltzApiJobError("BOLTZ_API_INPUT_INVALID", f"component {index} has unsupported type {component_type!r}")
        if component_type == "peptide":
            provider_type = "protein"
        elif component_type in {"ligand", "ion"}:
            provider_type = "ligand_ccd" if str(component.get("ccd") or "").strip() else "ligand_smiles"
        else:
            provider_type = component_type
        chain_ids = _normalized_chain_ids(component.get("chain_ids") or component.get("id"), chr(65 + index))
        if used_chains.intersection(chain_ids):
            raise BoltzApiJobError("BOLTZ_API_INPUT_INVALID", "chain identifiers must be unique across components")
        used_chains.update(chain_ids)

        if provider_type in {"protein", "dna", "rna"}:
            value = str(component.get("sequence") or (sequence if index == 0 else "")).strip().upper()
            alphabet = {
                "protein": set("ACDEFGHIKLMNPQRSTVWYBXZJUO"),
                "dna": set("ACGTN"),
                "rna": set("ACGUN"),
            }[provider_type]
            if not value or any(char not in alphabet for char in value):
                raise BoltzApiJobError("BOLTZ_API_INPUT_INVALID", f"component {index} has an invalid {provider_type} sequence")
        else:
            value = str(component.get("ccd") or component.get("smiles") or "").strip()
            if not value:
                raise BoltzApiJobError("BOLTZ_API_INPUT_INVALID", f"component {index} needs a CCD code or SMILES value")

        entity: dict[str, Any] = {
            "type": provider_type,
            "value": value,
            "chain_ids": chain_ids,
        }
        if provider_type == "protein" and not use_msa:
            entity["msa"] = {"type": "empty"}
        entities.append(entity)

    if not any(entity["type"] == "protein" for entity in entities):
        raise BoltzApiJobError("BOLTZ_API_INPUT_INVALID", "at least one protein entity is required")
    return {"entities": entities, "num_samples": int(num_samples)}


def _estimate_binding(value: Any) -> Any:
    """Strip volatile provider metadata while retaining all cost-bearing estimate fields."""
    if isinstance(value, dict):
        return {
            key: _estimate_binding(item)
            for key, item in value.items()
            if key.lower() not in {
                "id", "request_id", "created_at", "updated_at", "expires_at", "timestamp"
            }
        }
    if isinstance(value, list):
        return [_estimate_binding(item) for item in value]
    return value


def estimate_fingerprint(*, model: str, provider_input: dict[str, Any], estimate: dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_json({"model": model, "input": provider_input, "estimate": _estimate_binding(estimate)}).encode("utf-8")
    ).hexdigest()


def _cli_binary() -> str | None:
    configured = os.getenv("BMS_BOLTZ_API_CLI", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if "/" in configured:
            return str(path) if path.is_file() and os.access(path, os.X_OK) else None
        return shutil.which(configured)
    managed = get_data_root() / "tools" / "boltz-api"
    if managed.is_file() and os.access(managed, os.X_OK):
        return str(managed)
    return shutil.which("boltz-api")


def _cli_home() -> Path:
    configured = os.getenv("BMS_BOLTZ_API_HOME", "").strip()
    return Path(configured).expanduser() if configured else get_data_root() / "secrets" / "boltz-api-home"


def _provider_env() -> dict[str, str]:
    env = os.environ.copy()
    managed_config = _cli_home() / ".config" / "boltz-api" / "config.yaml"
    if managed_config.is_file():
        env["HOME"] = str(_cli_home())
    return env


def _boltz_download_root() -> Path:
    configured = os.getenv("BMS_BOLTZ_DOWNLOAD_ROOT", "").strip()
    root = Path(configured).expanduser() if configured else get_data_root() / "boltz_results"
    return root.resolve()


@contextmanager
def _job_lock(job_id: str) -> Iterator[bool]:
    lock_root = get_data_root() / "state" / "boltz-api-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{job_id}.lock"
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def provider_configuration() -> dict[str, Any]:
    binary = _cli_binary()
    config_path = _cli_home() / ".config" / "boltz-api" / "config.yaml"
    config_readable = config_path.is_file() and os.access(config_path, os.R_OK)
    credential_present = bool(os.getenv("BOLTZ_API_KEY")) or config_readable
    return {
        "available": bool(binary and credential_present),
        "cli_available": bool(binary),
        "credential_configured": credential_present,
        "model": BOLTZ_API_MODEL,
        "message": (
            "Boltz API is ready"
            if binary and credential_present
            else "Boltz API CLI and server-side credentials must be configured in the API runtime"
        ),
    }


async def _run_cli_json(args: list[str], *, timeout: float = 120.0) -> dict[str, Any]:
    binary = _cli_binary()
    if not binary:
        raise BoltzApiJobError("BOLTZ_API_NOT_CONFIGURED", "Boltz API CLI is not installed in the API runtime", status_code=503)
    if not provider_configuration()["credential_configured"]:
        raise BoltzApiJobError("BOLTZ_API_NOT_CONFIGURED", "Boltz API credentials are not configured in the API runtime", status_code=503)

    process = await asyncio.create_subprocess_exec(
        binary,
        "--format",
        "json",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_provider_env(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise BoltzApiJobError("BOLTZ_API_TIMEOUT", "Boltz API command timed out", status_code=504, retryable=True) from exc
    if len(stdout) > 2_000_000 or len(stderr) > 2_000_000:
        raise BoltzApiJobError("BOLTZ_API_RESPONSE_INVALID", "Boltz API command returned an oversized response", status_code=502)
    if process.returncode != 0:
        raw_message = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
        lowered = raw_message.lower()
        code = "BOLTZ_API_AUTH_FAILED" if "auth" in lowered or "api key" in lowered or "unauthorized" in lowered else "BOLTZ_API_REQUEST_FAILED"
        status_code = 503 if code == "BOLTZ_API_AUTH_FAILED" else 502
        message = "Boltz API authentication failed" if code == "BOLTZ_API_AUTH_FAILED" else f"Boltz API command failed with exit code {process.returncode}"
        raise BoltzApiJobError(code, message, status_code=status_code, retryable=code != "BOLTZ_API_AUTH_FAILED")
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BoltzApiJobError("BOLTZ_API_RESPONSE_INVALID", "Boltz API returned malformed JSON", status_code=502) from exc
    if not isinstance(value, dict):
        raise BoltzApiJobError("BOLTZ_API_RESPONSE_INVALID", "Boltz API returned an unexpected response", status_code=502)
    return value


async def probe_provider_status() -> dict[str, Any]:
    configured = provider_configuration()
    if not configured["cli_available"]:
        return {**configured, "available": False, "message": "Boltz API CLI is not installed in the API runtime"}
    if not configured["credential_configured"]:
        return {**configured, "available": False, "message": "Boltz API authentication is not configured in the API runtime"}
    try:
        auth = await _run_cli_json(["auth", "status"], timeout=15.0)
    except BoltzApiJobError:
        return {**configured, "available": False, "message": "Boltz API authentication is missing or expired"}
    authenticated = bool(auth.get("authenticated"))
    return {
        **configured,
        "available": authenticated,
        "message": "Boltz API is ready" if authenticated else "Boltz API authentication is missing or expired",
    }


async def _with_payload_file(provider_input: dict[str, Any], operation: str, *, model: str, idempotency_key: str | None = None) -> dict[str, Any]:
    staging_root = get_data_root() / ".external-provider-staging"
    staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, raw_path = tempfile.mkstemp(prefix="boltz-api-", suffix=".json", dir=staging_root)
    path = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(provider_input, handle, sort_keys=True)
        args = ["predictions:structure-and-binding", operation, "--model", model, "--input", f"@json://{path}"]
        if idempotency_key:
            args.extend(["--idempotency-key", idempotency_key])
        return await _run_cli_json(args)
    finally:
        path.unlink(missing_ok=True)


async def estimate_boltz_api_cost(*, model: str, provider_input: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if model != BOLTZ_API_MODEL:
        raise BoltzApiJobError("BOLTZ_API_MODEL_UNSUPPORTED", f"only {BOLTZ_API_MODEL} is supported")
    estimate = await _with_payload_file(provider_input, "estimate-cost", model=model)
    return estimate, estimate_fingerprint(model=model, provider_input=provider_input, estimate=estimate)


async def queue_boltz_api_job(
    session: AsyncSession,
    *,
    name: str,
    client_request_id: str,
    model: str,
    provider_input: dict[str, Any],
    approved_estimate_fingerprint: str,
) -> Job:
    job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"biomodstack:boltz-api:{client_request_id}"))
    existing = await session.get(Job, job_id)
    if existing is not None:
        existing_params = dict(existing.params or {})
        if (
            existing.model_id != "boltz_api"
            or existing_params.get("client_request_id") != client_request_id
            or existing_params.get("provider_model") != model
            or existing_params.get("provider_input") != provider_input
            or existing_params.get("approved_estimate_fingerprint") != approved_estimate_fingerprint
        ):
            raise BoltzApiJobError(
                "BOLTZ_API_IDEMPOTENCY_CONFLICT",
                "This client request ID is already bound to a different submission",
                status_code=409,
            )
        return existing

    estimate, current_fingerprint = await estimate_boltz_api_cost(model=model, provider_input=provider_input)
    if approved_estimate_fingerprint != current_fingerprint:
        raise BoltzApiJobError(
            "BOLTZ_API_ESTIMATE_CHANGED",
            "The current provider estimate differs from the approved estimate; review it again before queueing",
            status_code=409,
        )
    idempotency_key = f"bms-{client_request_id}"
    now = datetime.utcnow()
    job = Job(
        id=job_id,
        name=name.strip(),
        status="queued",
        queue_status="queued",
        model_id="boltz_api",
        mode="external_api",
        params={
            "external_provider": BOLTZ_API_PROVIDER,
            "client_request_id": client_request_id,
            "external_resource": "predictions:structure-and-binding",
            "provider_model": model,
            "provider_input": provider_input,
            "provider_state": "submitting",
            "provider_idempotency_key": idempotency_key,
            "approved_estimate_fingerprint": current_fingerprint,
            "provider_estimate": estimate,
            "submitted_via": "structure_prediction",
        },
        created_at=now,
        sequence_length=max(
            (len(entity.get("value", "")) for entity in provider_input["entities"] if entity.get("type") == "protein"),
            default=0,
        ),
        max_retries=3,
        retry_count=0,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def _download_results(provider_job_id: str, run_dir: Path) -> None:
    binary = _cli_binary()
    if not binary:
        raise BoltzApiJobError("BOLTZ_API_NOT_CONFIGURED", "Boltz API CLI is not installed", status_code=503)
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        binary,
        "download-results",
        "--id",
        provider_job_id,
        "--run-dir",
        str(run_dir),
        "--download-mode",
        "everything",
        "--poll-interval-seconds",
        str(int(BOLTZ_API_POLL_INTERVAL_SECONDS)),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_provider_env(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=3600)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise BoltzApiJobError(
            "BOLTZ_API_DOWNLOAD_TIMEOUT",
            "Boltz API result download timed out",
            status_code=504,
            retryable=True,
        ) from exc
    if process.returncode != 0:
        raise BoltzApiJobError(
            "BOLTZ_API_DOWNLOAD_FAILED",
            f"Boltz API result download failed with exit code {process.returncode}",
            status_code=502,
            retryable=True,
        )


async def process_boltz_api_job(session: AsyncSession, job: Job) -> None:
    params = dict(job.params or {})
    state = str(params.get("provider_state") or "")
    model = str(params.get("provider_model") or BOLTZ_API_MODEL)
    provider_input = params.get("provider_input")
    if not isinstance(provider_input, dict):
        raise BoltzApiJobError("BOLTZ_API_JOB_INVALID", "queued job is missing its provider input")

    if state == "importing":
        import_id = str(params.get("external_import_id") or "").strip()
        record = await session.get(ExternalResultImport, import_id) if import_id else None
        if record is None or record.bms_job_id != job.id:
            raise BoltzApiJobError("BOLTZ_API_IMPORT_MISSING", "linked result ingestion record is missing")
        if record.state == "failed":
            raise BoltzApiJobError(
                record.failure_code or "BOLTZ_API_IMPORT_FAILED",
                record.failure_message or "Boltz API result ingestion failed",
            )
        if record.state == "completed":
            design = (
                await session.execute(select(Design).where(Design.job_id == job.id).limit(1))
            ).scalar_one_or_none()
            if job.status != "completed" or design is None:
                raise BoltzApiJobError(
                    "BOLTZ_API_IMPORT_INCOMPLETE",
                    "Result ingestion reported completion without canonical job artifacts",
                )
        return

    if state == "submitting":
        response = await _with_payload_file(
            provider_input,
            "start",
            model=model,
            idempotency_key=str(params.get("provider_idempotency_key") or f"bms-{job.id}"),
        )
        provider_job_id = str(response.get("id") or "").strip()
        if not provider_job_id:
            raise BoltzApiJobError("BOLTZ_API_RESPONSE_INVALID", "submission response did not include a provider job ID", status_code=502)
        await session.refresh(job)
        if str(job.status or "").lower() == "cancelled":
            params = dict(job.params or {})
            params.update({"provider_job_id": provider_job_id, "provider_state": "cancelled_after_submission"})
            job.params = params
            await session.commit()
            return
        params = dict(job.params or {})
        params.update({"provider_job_id": provider_job_id, "provider_state": "submitted"})
        job.params = params
        job.status = "running"
        job.queue_status = "running"
        job.started_at = datetime.utcnow()
        job.error_message = None
        await session.commit()
        return

    if state in {"submitted", "running"}:
        provider_job_id = str(params.get("provider_job_id") or "").strip()
        if not provider_job_id:
            raise BoltzApiJobError("BOLTZ_API_JOB_INVALID", "submitted job is missing its provider job ID")
        response = await _run_cli_json(["predictions:structure-and-binding", "retrieve", "--id", provider_job_id])
        await session.refresh(job)
        if str(job.status or "").lower() == "cancelled":
            return
        params = dict(job.params or {})
        provider_state = str(response.get("status") or "").strip().lower()
        params["provider_last_status"] = provider_state
        if provider_state in TERMINAL_PROVIDER_STATES:
            job.params = params
            job.status = "failed"
            job.queue_status = "failed"
            job.completed_at = datetime.utcnow()
            job.error_message = str(response.get("error") or f"Boltz API job ended in state {provider_state}")[:2000]
            await session.commit()
            return
        if provider_state != "succeeded":
            params["provider_state"] = "running"
            job.params = params
            await session.commit()
            return
        params["provider_state"] = "downloading"
        job.params = params
        await session.commit()
        state = "downloading"

    if state == "downloading":
        provider_job_id = str(params.get("provider_job_id") or "").strip()
        run_dir = _boltz_download_root() / "api_runs" / f"bms-{job.id}"
        await _download_results(provider_job_id, run_dir)
        await session.refresh(job)
        if str(job.status or "").lower() == "cancelled":
            return
        preview = await asyncio.to_thread(preview_boltz_api_run, run_dir)
        record = await queue_external_import(
            session,
            source_dir=run_dir,
            preview_fingerprint=preview.source_fingerprint,
            dataset_name="Boltz API structure predictions",
            job_name=job.name,
            bms_job_id=job.id,
        )
        await session.refresh(job)
        if str(job.status or "").lower() in {"cancelled", "completed"}:
            return
        params = dict(job.params or {})
        params.update({
            "provider_state": "importing",
            "downloaded_run_path": str(run_dir),
            "external_import_id": record.id,
        })
        job.params = params
        job.output_dir = str(run_dir)
        await session.commit()


class BoltzApiJobWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_interval: float = BOLTZ_API_POLL_INTERVAL_SECONDS,
    ):
        self._session_factory = session_factory
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="boltz-api-job-worker")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def run_once(self) -> int:
        async with self._session_factory() as session:
            job_ids = list((await session.execute(
                select(Job.id).where(Job.model_id == "boltz_api", Job.status.in_(["queued", "running"]))
            )).scalars())
            processed = 0
            for job_id in job_ids:
                with _job_lock(job_id) as claimed:
                    if not claimed:
                        continue
                    job = await session.get(Job, job_id)
                    if job is None or str(job.status or "").lower() not in {"queued", "running"}:
                        continue
                    try:
                        await process_boltz_api_job(session, job)
                        processed += 1
                    except Exception as exc:
                        await session.rollback()
                        job = await session.get(Job, job_id)
                        if job is None or str(job.status or "").lower() == "cancelled":
                            continue
                        params = dict(job.params or {})
                        attempts = int(params.get("provider_attempts") or 0) + 1
                        if isinstance(exc, (BoltzApiJobError, BoltzImportError)):
                            code = getattr(exc, "code", "BOLTZ_API_JOB_FAILED")
                            message = str(exc)[:2000]
                            retryable = bool(getattr(exc, "retryable", False))
                        else:
                            code = "BOLTZ_API_WORKER_ERROR"
                            message = "Unexpected Boltz API worker failure"
                            retryable = True
                        params["provider_attempts"] = attempts
                        params["provider_last_error_code"] = code
                        job.params = params
                        retryable = retryable and attempts < int(job.max_retries or 3)
                        job.error_message = message
                        if not retryable:
                            job.status = "failed"
                            job.queue_status = "failed"
                            job.completed_at = datetime.utcnow()
                        await session.commit()
            return processed

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:
                # Keep the durable poller alive; individual job failures are handled in run_once.
                logger.exception("Boltz API worker iteration failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                pass
