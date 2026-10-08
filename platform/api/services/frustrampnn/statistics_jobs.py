"""Durable, independently retryable FrustraMPNN CPU statistics children."""

from __future__ import annotations

import re
import uuid
import hashlib
import os
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database import (
    FrustraMPNNResult,
    FrustraMPNNStatisticsAnalysis,
    Job,
    ScientificArtifactReceipt,
)


FORMULA_VERSION = "frustrampnn_statistics_formula_v1"
POLICY_VERSION = "frustrampnn_statistics_policy_v1"
PACKAGE_VERSION = "biomodstack_frustrampnn_statistics_v1"
SCHEMA_VERSION = 1
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LOGGER = logging.getLogger(__name__)


class FrustraMPNNStatisticsJobError(ValueError):
    """Statistics child lifecycle authority is invalid."""


def _analysis_id(result: FrustraMPNNResult, landscape_sha256: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "bms:frustrampnn:statistics:"
            f"{result.parent_job_id}:{result.invocation_id}:{landscape_sha256}",
        )
    )


def _landscape_sha256(result: FrustraMPNNResult) -> str:
    summary = result.summary_json
    value: Any = summary.get("landscape_sha256") if isinstance(summary, dict) else None
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise FrustraMPNNStatisticsJobError(
            "core result has no exact landscape SHA-256 authority"
        )
    if not isinstance(result.manifest_sha256, str) or _HEX64.fullmatch(result.manifest_sha256) is None:
        raise FrustraMPNNStatisticsJobError(
            "core result has no exact manifest SHA-256 authority"
        )
    return value


async def ensure_statistics_child(
    session: AsyncSession,
    *,
    result: FrustraMPNNResult,
    core_artifact_id: str,
    core_bundle_relative_path: str,
) -> FrustraMPNNStatisticsAnalysis:
    """Create or return the one child for an exact core landscape generation."""

    terminal = result.terminal_result_json or {}
    if terminal.get("component_contract_version") != "3.0" or terminal.get("status") != "succeeded":
        raise FrustraMPNNStatisticsJobError(
            "statistics children require one successful v3 core result"
        )
    landscape_sha256 = _landscape_sha256(result)
    normalized_artifact_id = str(core_artifact_id).strip()
    if not normalized_artifact_id or len(normalized_artifact_id) > 384:
        raise FrustraMPNNStatisticsJobError("core artifact identity is invalid")
    relative = PurePosixPath(str(core_bundle_relative_path))
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise FrustraMPNNStatisticsJobError(
            "core bundle relative path is outside the supported boundary"
        )
    normalized_relative = relative.as_posix()
    analysis_id = _analysis_id(result, landscape_sha256)
    existing = await session.get(FrustraMPNNStatisticsAnalysis, analysis_id)
    if existing is not None:
        if (
            existing.parent_job_id != result.parent_job_id
            or existing.invocation_id != result.invocation_id
            or existing.core_landscape_sha256 != landscape_sha256
            or existing.core_manifest_sha256 != result.manifest_sha256
            or existing.core_artifact_id != normalized_artifact_id
            or existing.core_bundle_relative_path != normalized_relative
        ):
            raise FrustraMPNNStatisticsJobError(
                "statistics child identity conflicts with core authority"
            )
        return existing
    child = FrustraMPNNStatisticsAnalysis(
        analysis_id=analysis_id,
        parent_job_id=result.parent_job_id,
        invocation_id=result.invocation_id,
        core_artifact_id=normalized_artifact_id,
        core_bundle_relative_path=normalized_relative,
        core_landscape_sha256=landscape_sha256,
        core_manifest_sha256=result.manifest_sha256,
        state="queued",
        attempt_count=0,
        formula_version=FORMULA_VERSION,
        policy_version=POLICY_VERSION,
        package_version=PACKAGE_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    session.add(child)
    await session.flush()
    return child


async def _child(
    session: AsyncSession,
    analysis_id: str,
) -> FrustraMPNNStatisticsAnalysis:
    child = await session.get(FrustraMPNNStatisticsAnalysis, analysis_id)
    if child is None:
        raise FrustraMPNNStatisticsJobError("statistics child does not exist")
    return child


async def claim_statistics_child(
    session: AsyncSession,
    *,
    analysis_id: str,
) -> FrustraMPNNStatisticsAnalysis:
    claimed_at = datetime.utcnow()
    claimed = await session.execute(
        update(FrustraMPNNStatisticsAnalysis)
        .where(
            FrustraMPNNStatisticsAnalysis.analysis_id == analysis_id,
            FrustraMPNNStatisticsAnalysis.state == "queued",
        )
        .values(
            state="running",
            attempt_count=FrustraMPNNStatisticsAnalysis.attempt_count + 1,
            diagnostic=None,
            updated_at=claimed_at,
        )
    )
    if claimed.rowcount != 1:
        raise FrustraMPNNStatisticsJobError("only queued statistics children can run")
    child = await _child(session, analysis_id)
    await session.refresh(child)
    return child


async def recover_abandoned_statistics_claims(
    session: AsyncSession,
    *,
    stale_before: datetime,
) -> int:
    recovered = await session.execute(
        update(FrustraMPNNStatisticsAnalysis)
        .where(
            FrustraMPNNStatisticsAnalysis.state == "running",
            FrustraMPNNStatisticsAnalysis.updated_at < stale_before,
        )
        .values(
            state="queued",
            diagnostic="recovered abandoned statistics claim",
            updated_at=datetime.utcnow(),
        )
    )
    return int(recovered.rowcount or 0)


async def fail_statistics_child(
    session: AsyncSession,
    *,
    analysis_id: str,
    diagnostic: str,
) -> FrustraMPNNStatisticsAnalysis:
    child = await _child(session, analysis_id)
    if child.state != "running":
        raise FrustraMPNNStatisticsJobError("only running statistics children can fail")
    normalized = str(diagnostic).strip()
    if not normalized or len(normalized) > 512 or "/" in normalized:
        raise FrustraMPNNStatisticsJobError("statistics diagnostic is missing or unsafe")
    child.state = "failed"
    child.diagnostic = normalized
    child.updated_at = datetime.utcnow()
    await session.flush()
    return child


async def retry_statistics_child(
    session: AsyncSession,
    *,
    analysis_id: str,
) -> FrustraMPNNStatisticsAnalysis:
    child = await _child(session, analysis_id)
    if child.state != "failed":
        raise FrustraMPNNStatisticsJobError("only failed statistics children can retry")
    result = await session.get(
        FrustraMPNNResult, (child.parent_job_id, child.invocation_id)
    )
    if result is None:
        raise FrustraMPNNStatisticsJobError(
            "statistics retry requires the exact successful v3 core result"
        )
    terminal = result.terminal_result_json
    if (
        not isinstance(terminal, dict)
        or terminal.get("component_contract_version") != "3.0"
        or terminal.get("status") != "succeeded"
        or result.manifest_sha256 != child.core_manifest_sha256
        or _landscape_sha256(result) != child.core_landscape_sha256
    ):
        raise FrustraMPNNStatisticsJobError(
            "statistics retry requires the exact successful v3 core result"
        )
    child.state = "queued"
    child.diagnostic = None
    child.artifact_relative_path = None
    child.artifact_sha256 = None
    child.statistics_sha256 = None
    child.updated_at = datetime.utcnow()
    await session.flush()
    return child


async def run_statistics_child_once(
    session: AsyncSession,
    *,
    analysis_id: str,
) -> dict[str, Any]:
    """Compute one committed-running CPU child against its exact v3 core bundle."""

    from services.scientific_artifacts.persistence import publish_json_payload

    from . import runtime as runtime_contract
    from .analytics import build_statistics_receipt
    from .contracts import canonical_json_loads, canonical_sha256
    from .persistence import load_and_validate_result_bundle
    from .settings import _CAPABILITY_INVENTORY_PATH, load_capability_inventory

    child = await _child(session, analysis_id)
    if child.state != "running":
        raise FrustraMPNNStatisticsJobError(
            "statistics computation requires a committed running claim"
        )
    result = await session.get(
        FrustraMPNNResult,
        (child.parent_job_id, child.invocation_id),
    )
    job = await session.get(Job, child.parent_job_id)
    if result is None or job is None:
        raise FrustraMPNNStatisticsJobError(
            "statistics child lost its core result or owning Job"
        )
    output_root_value = job.child_output_dir or job.output_dir
    if not isinstance(output_root_value, str) or not output_root_value.strip():
        raise FrustraMPNNStatisticsJobError(
            "statistics child owning Job has no output-root authority"
        )
    output_root = Path(output_root_value).resolve(strict=True)
    bundle_root = (output_root / child.core_bundle_relative_path).resolve(strict=True)
    try:
        bundle_root.relative_to(output_root)
    except ValueError as exc:
        raise FrustraMPNNStatisticsJobError(
            "statistics child core bundle escapes its owning Job root"
        ) from exc

    terminal_path = bundle_root / "workflow_component_result_v3.json"
    descriptor = runtime_contract.open_regular_no_follow(
        terminal_path,
        label="statistics child terminal result",
    )
    try:
        terminal_chunks: list[bytes] = []
        offset = 0
        while True:
            chunk = os.pread(descriptor, 1024 * 1024, offset)
            if not chunk:
                break
            terminal_chunks.append(chunk)
            offset += len(chunk)
    finally:
        os.close(descriptor)
    terminal = canonical_json_loads(b"".join(terminal_chunks))
    if not isinstance(terminal, dict):
        raise FrustraMPNNStatisticsJobError(
            "statistics child terminal result is not an object"
        )
    bundle = load_and_validate_result_bundle(
        bundle_root,
        terminal_envelope=terminal,
        expected_parent_job_id=child.parent_job_id,
    )
    if bundle.contract_version != 3:
        raise FrustraMPNNStatisticsJobError(
            "statistics child requires a v3 core bundle"
        )
    if (
        hashlib.sha256(bundle.manifest_bytes).hexdigest()
        != child.core_manifest_sha256
        or canonical_sha256(bundle.landscape) != child.core_landscape_sha256
        or bundle.manifest["parent_job_id"] != child.parent_job_id
        or bundle.manifest["invocation_id"] != child.invocation_id
    ):
        raise FrustraMPNNStatisticsJobError(
            "statistics child core bundle identity no longer matches its receipt"
        )
    core_artifact = await session.get(
        ScientificArtifactReceipt,
        child.core_artifact_id,
    )
    if (
        core_artifact is None
        or core_artifact.owner_kind != "frustrampnn_result"
        or core_artifact.owner_id != f"{child.parent_job_id}:{child.invocation_id}"
        or core_artifact.role != "landscape"
    ):
        raise FrustraMPNNStatisticsJobError(
            "statistics child core artifact receipt no longer matches"
        )

    inventory, inventory_byte_sha256 = load_capability_inventory()
    inventory_bytes = _CAPABILITY_INVENTORY_PATH.read_bytes()
    if (
        hashlib.sha256(inventory_bytes).hexdigest() != inventory_byte_sha256
        or inventory_byte_sha256
        != bundle.request["capability_inventory_byte_sha256"]
    ):
        raise FrustraMPNNStatisticsJobError(
            "statistics child capability inventory bytes no longer match the core"
        )
    analysis_receipt = {
        "schema_name": "frustrampnn_statistics_analysis_receipt",
        "schema_version": 1,
        "analysis_id": child.analysis_id,
        "core_artifact_id": child.core_artifact_id,
        "core_bundle_relative_path": child.core_bundle_relative_path,
        "core_landscape_sha256": child.core_landscape_sha256,
        "core_manifest_sha256": child.core_manifest_sha256,
        "formula_version": child.formula_version,
        "policy_version": child.policy_version,
        "package_version": child.package_version,
        "statistics_schema_version": 2,
        "attempt_count": child.attempt_count,
    }
    structure_map = canonical_json_loads(
        bundle.payloads["frustrampnn_structure_map_v1.json"]
    )
    if not isinstance(structure_map, dict):
        raise FrustraMPNNStatisticsJobError(
            "statistics child structure map is not an object"
        )
    statistics = build_statistics_receipt(
        request=bundle.request,
        execution_receipt=bundle.receipt,
        landscape=bundle.landscape,
        structure_map=structure_map,
        capability_inventory=inventory,
        capability_inventory_bytes=inventory_bytes,
        analysis_receipt=analysis_receipt,
    )
    reference = await publish_json_payload(
        session,
        owner_kind="frustrampnn_statistics_analysis",
        owner_id=child.analysis_id,
        role="statistics",
        schema_id="bms.frustrampnn-statistics-envelope.v2",
        payload=statistics,
        source_sha256=str(statistics["statistics_sha256"]),
    )
    result.statistics_sha256 = statistics["statistics_sha256"]
    result.statistics_json = reference
    result.comparison_compatibility_id = statistics[
        "comparison_compatibility_id"
    ]
    child.state = "completed"
    child.statistics_sha256 = statistics["statistics_sha256"]
    child.artifact_relative_path = reference["relative_path"]
    child.artifact_sha256 = reference["content_sha256"]
    child.diagnostic = None
    child.updated_at = datetime.utcnow()
    await session.flush()
    return statistics


class FrustraMPNNStatisticsWorker:
    """Single-owner CPU worker for durable statistics children."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval_seconds = max(0.05, float(poll_interval_seconds))
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def run_pending_once(self) -> str | None:
        async with self._session_factory() as claim_session:
            analysis_id = (
                await claim_session.execute(
                    select(FrustraMPNNStatisticsAnalysis.analysis_id)
                    .where(FrustraMPNNStatisticsAnalysis.state == "queued")
                    .order_by(
                        FrustraMPNNStatisticsAnalysis.created_at,
                        FrustraMPNNStatisticsAnalysis.analysis_id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if analysis_id is None:
                return None
            await claim_statistics_child(claim_session, analysis_id=analysis_id)
            await claim_session.commit()
        async with self._session_factory() as session:
            try:
                await run_statistics_child_once(session, analysis_id=analysis_id)
                await session.commit()
                return str(analysis_id)
            except Exception as exc:
                await session.rollback()
                async with self._session_factory() as failure_session:
                    child = await failure_session.get(
                        FrustraMPNNStatisticsAnalysis,
                        analysis_id,
                    )
                    if child is not None and child.state == "running":
                        await fail_statistics_child(
                            failure_session,
                            analysis_id=analysis_id,
                            diagnostic=f"{type(exc).__name__}: analysis failed",
                        )
                        await failure_session.commit()
                raise

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        async with self._session_factory() as session:
            await recover_abandoned_statistics_claims(
                session,
                stale_before=datetime.utcnow() - timedelta(hours=1),
            )
            await session.commit()
        self._task = asyncio.create_task(
            self._run(),
            name="frustrampnn-statistics-worker",
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            processed = False
            try:
                processed = await self.run_pending_once() is not None
            except Exception as exc:
                _LOGGER.error(
                    "FrustraMPNN statistics child failed: %s",
                    type(exc).__name__,
                )
            if processed:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

__all__ = [
    "FrustraMPNNStatisticsWorker",
    "FrustraMPNNStatisticsJobError",
    "claim_statistics_child",
    "ensure_statistics_child",
    "fail_statistics_child",
    "retry_statistics_child",
    "recover_abandoned_statistics_claims",
    "run_statistics_child_once",
]
