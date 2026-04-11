from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Iterable, Sequence

from sqlalchemy import select

from database import Design, Job, async_session
from services.analysis_registry import (
    ANTIBODY_ANNOTATION_PACK_ANALYSIS,
    CHAIN_METRICS_ANALYSIS,
    FAMPNN_PSCE_PROFILE_ANALYSIS,
    IPSAE_INTERFACE_ANALYSIS,
    STRUCTURE_SUMMARY_ANALYSIS,
)
from services.analysis_runs import request_design_analysis


logger = logging.getLogger(__name__)

_RECENT_AUTORUN_REQUESTS: dict[str, float] = {}
_AUTORUN_DEBOUNCE_SECONDS = 45.0


def _autorun_max_designs() -> int:
    try:
        return max(1, int(os.getenv("BMS_ANALYSIS_AUTORUN_MAX_DESIGNS", "500")))
    except (TypeError, ValueError):
        return 500


def _is_antibody_like_job(job: Job | None) -> bool:
    if job is None:
        return False
    model_id = str(job.model_id or "").strip().lower()
    mode = str(job.mode or "").strip().lower()
    name = str(job.name or "").strip().lower()
    return (
        model_id in {"rfantibody", "antibody_denovo", "template_antibody_denovo", "antibody_child"} or
        "antibody" in model_id or
        "antibody" in mode or
        "nanobody" in mode or
        "vhh" in mode or
        "antibody" in name or
        "nanobody" in name or
        "vhh" in name
    )


def _terminal_or_review_ready(job: Job | None) -> bool:
    if job is None:
        return False
    status = str(job.status or "").strip().lower()
    return status in {"completed", "awaiting_input"}


def _viewer_minimum_analysis_types(job: Job, design: Design) -> list[str]:
    analysis_types = [
        STRUCTURE_SUMMARY_ANALYSIS,
        CHAIN_METRICS_ANALYSIS,
    ]
    if design.aligned_error_path and design.aligned_error_format:
        analysis_types.append(IPSAE_INTERFACE_ANALYSIS)
    if _is_antibody_like_job(job):
        analysis_types.append(ANTIBODY_ANNOTATION_PACK_ANALYSIS)
    if design.fampnn_psce is not None:
        analysis_types.append(FAMPNN_PSCE_PROFILE_ANALYSIS)
    return analysis_types


def _iter_unique(items: Sequence[str]) -> Iterable[str]:
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        yield item


async def ensure_viewer_minimum_analyses_for_job(job_id: str) -> dict[str, int]:
    async with async_session() as session:
        result = await session.execute(select(Job).where(Job.id == str(job_id)))
        job = result.scalar_one_or_none()
        if job is None or not _terminal_or_review_ready(job):
            return {"designs": 0, "queued": 0, "reused": 0, "skipped": 0}

        # Prioritize top-level/results-view jobs to avoid duplicating auto work on
        # every transient child shard that also writes structures.
        if job.parent_job_id and str(job.status or "").strip().lower() != "awaiting_input":
            return {"designs": 0, "queued": 0, "reused": 0, "skipped": 0}

        design_result = await session.execute(
            select(Design)
            .where(Design.job_id == str(job.id))
            .order_by(Design.created_at.asc(), Design.id.asc())
        )
        designs = list(design_result.scalars().all())
        design_count = len(designs)
        if design_count == 0:
            return {"designs": 0, "queued": 0, "reused": 0, "skipped": 0}
        if design_count > _autorun_max_designs():
            logger.info(
                "[ANALYSIS AUTO] Skipping viewer-minimum bundle for %s (%s designs exceeds cap %s)",
                job.name,
                design_count,
                _autorun_max_designs(),
            )
            return {"designs": design_count, "queued": 0, "reused": 0, "skipped": design_count}

        queued = 0
        reused = 0
        skipped = 0
        for design in designs:
            if not design.pdb_path:
                skipped += 1
                continue
            for analysis_type in _iter_unique(_viewer_minimum_analysis_types(job, design)):
                try:
                    _run, was_reused = await request_design_analysis(
                        session,
                        design,
                        analysis_type,
                        requested_by="system:auto",
                    )
                    if was_reused:
                        reused += 1
                    else:
                        queued += 1
                except Exception as exc:
                    logger.warning(
                        "[ANALYSIS AUTO] Failed to queue %s for %s (%s): %s",
                        analysis_type,
                        design.name,
                        design.id,
                        exc,
                    )
        logger.info(
            "[ANALYSIS AUTO] Viewer-minimum bundle queued for %s: designs=%s queued=%s reused=%s skipped=%s",
            job.name,
            design_count,
            queued,
            reused,
            skipped,
        )
        return {"designs": design_count, "queued": queued, "reused": reused, "skipped": skipped}


def schedule_viewer_minimum_analyses_for_job(job_id: str | None) -> bool:
    if not job_id:
        return False
    normalized = str(job_id).strip()
    if not normalized:
        return False

    now = time.monotonic()
    last_requested = _RECENT_AUTORUN_REQUESTS.get(normalized)
    if last_requested is not None and (now - last_requested) < _AUTORUN_DEBOUNCE_SECONDS:
        return False
    _RECENT_AUTORUN_REQUESTS[normalized] = now

    async def _runner() -> None:
        try:
            await ensure_viewer_minimum_analyses_for_job(normalized)
        except Exception as exc:
            logger.warning("[ANALYSIS AUTO] Failed viewer-minimum autorun for %s: %s", normalized, exc)

    asyncio.create_task(_runner())
    return True

