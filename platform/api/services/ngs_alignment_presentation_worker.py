"""Existing presentation worker, now claiming independent catalog/preview products."""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import uuid
from typing import Any

from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from database import Job, NgsAlignmentDerivedProduct
from services import ngs_alignment_derived_products as lifecycle
from services import ngs_alignment_sessions
from services.ngs_alignment_presentation import PresentationClaimLost, PresentationSourceStale

logger = logging.getLogger(__name__)
PresentationBuildFailure = ngs_alignment_sessions.AlignmentPresentationFailure


class PresentationBuildAborted(RuntimeError):
    """Synchronous builder has observed revocation and must quiesce."""


class PresentationAbort:
    def __init__(self) -> None:
        self._event = threading.Event()

    def abort(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def checkpoint(self) -> None:
        if self._event.is_set():
            raise PresentationBuildAborted("derived build ownership was revoked")


class NgsAlignmentPresentationWorker:
    """One owner, alternating product claims; preview never holds a catalog lease.

    Sealed orphan bytes are adopted only after an explicitly retried request is
    claimed and its own builder validates them. Expiry never requeues work.
    Legacy combined requests/artifacts remain untouched, not relabelled as v2/v6.
    """
    def __init__(self, session_factory: Any, *, build=None, resolve_source_authority=None,
                 poll_interval: float = 2.0, lease_seconds: int = 300) -> None:
        self._session_factory = session_factory
        self._build = build
        self._resolve_source_authority = resolve_source_authority
        self._build_inputs = {}
        self._poll_interval = max(0.05, float(poll_interval))
        self._lease_seconds = max(1, int(lease_seconds))
        self._task = None
        self._stop = asyncio.Event()
        self._active_abort = None
        self._next_product = "catalog"

    async def _resolve_owned_source(self, request, abort):
        abort.checkpoint()
        if self._resolve_source_authority is not None:
            value = self._resolve_source_authority(request)
            value = await value if inspect.isawaitable(value) else value
        else:
            async with self._session_factory() as session:
                job = await session.get(Job, request.job_id)
            if job is None:
                raise PresentationSourceStale("scientific source owner is missing")
            try:
                current, inputs = await run_in_threadpool(lifecycle.resolve_product_source, job, request)
            except (ngs_alignment_sessions.AlignmentSessionError, OSError, ValueError, KeyError) as exc:
                raise PresentationSourceStale("accepted source could not be re-resolved") from exc
            self._build_inputs[request.id] = inputs
            value = lifecycle.identity_sha256(current)
        abort.checkpoint()
        if value != request.source_authority_sha256:
            raise PresentationSourceStale("derived source generation changed")
        return value

    async def _process_claim(self, request, abort):
        await self._resolve_owned_source(request, abort)
        if self._build is None:
            from services.ngs_alignment_product_builder import build_product
            catalog = None
            if request.product == "preview":
                async with self._session_factory() as session:
                    catalog = await session.get(NgsAlignmentDerivedProduct, request.catalog_request_id)
            package = await run_in_threadpool(build_product, request, self._build_inputs[request.id],
                                             abort.checkpoint, catalog_request=catalog)
        else:
            def invoke():
                if len(inspect.signature(self._build).parameters) >= 2:
                    return self._build(request, abort)
                return self._build(request)
            package = await run_in_threadpool(invoke)
        abort.checkpoint()
        current = await self._resolve_owned_source(request, abort)
        return package, current

    async def _cleanup_restarted_attempts(self):
        from services.ngs_alignment_product_builder import cleanup_restarted_attempts
        # Bounded keyset reads, no source decoding, no new intent, and no ready
        # object eviction. The filesystem owner must independently be quiescent.
        after = ""
        while True:
            async with self._session_factory() as session:
                rows = (await session.execute(select(NgsAlignmentDerivedProduct, Job)
                    .join(Job, Job.id == NgsAlignmentDerivedProduct.job_id)
                    .where(NgsAlignmentDerivedProduct.id > after)
                    .order_by(NgsAlignmentDerivedProduct.id).limit(64))).all()
            if not rows:
                return
            for request, job in rows:
                after = request.id
                try:
                    await run_in_threadpool(cleanup_restarted_attempts, job, request)
                except (OSError, ValueError, ngs_alignment_sessions.AlignmentSessionError):
                    # Unsafe or unavailable roots are not cleanup authority.
                    logger.warning("NGS startup temporary cleanup skipped for %s", request.id)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        async with self._session_factory() as session:
            await lifecycle.recover_expired_products(session)
        await run_in_threadpool(ngs_alignment_sessions.recover_verified_cache)
        await self._cleanup_restarted_attempts()
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="ngs-alignment-presentation-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._active_abort is not None:
            self._active_abort.abort()
        task = self._task
        if task is not None:
            # Never cancel a thread-pool future and then clean up its live files.
            await asyncio.shield(task)
            self._task = None

    async def _quiesce(self, task, abort):
        abort.abort()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # Repeated outer cancellation still cannot abandon native I/O.
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            try:
                task.result()
            except Exception:
                pass

    async def _fail(self, request, token, code):
        async with self._session_factory() as session:
            try:
                await lifecycle.fail_product(session, request, token, error_code=code)
            except PresentationClaimLost:
                pass

    async def run_once(self):
        if self._stop.is_set():
            return None
        token = "ngs-product-claim-" + uuid.uuid4().hex
        order = (self._next_product, "preview" if self._next_product == "catalog" else "catalog")
        request = None
        async with self._session_factory() as session:
            await lifecycle.recover_expired_products(session)
            for product in order:
                request = await lifecycle.claim_next_product(session, product=product,
                    claim_token=token, lease_seconds=self._lease_seconds)
                if request is not None:
                    self._next_product = "preview" if product == "catalog" else "catalog"
                    break
        if request is None:
            return None
        abort = PresentationAbort()
        self._active_abort = abort
        work = asyncio.create_task(self._process_claim(request, abort))
        interval = max(0.1, self._lease_seconds / 3)
        try:
            while not work.done():
                if self._stop.is_set():
                    await self._quiesce(work, abort)
                    await self._fail(request, token, "cancelled")
                    return request.id
                done, _ = await asyncio.wait({work}, timeout=interval)
                if done:
                    break
                async with self._session_factory() as session:
                    await lifecycle.renew_product_lease(session, request, token,
                                                        lease_seconds=self._lease_seconds)
            package, current = await asyncio.shield(work)
            if self._stop.is_set():
                abort.abort()
            abort.checkpoint()
            if not isinstance(package, dict) or package.get("source_authority_sha256") != current:
                raise PresentationSourceStale("product publication source mismatch")
            async with self._session_factory() as session:
                await lifecycle.publish_product(session, request, token,
                    source_authority_sha256=current, authority_sha256=package["authority_sha256"],
                    manifest_sha256=package["manifest_sha256"])
        except asyncio.CancelledError:
            await self._quiesce(work, abort)
            await self._fail(request, token, "cancelled")
            raise
        except PresentationClaimLost:
            await self._quiesce(work, abort)
        except Exception as exc:
            await self._quiesce(work, abort)
            if isinstance(exc, PresentationSourceStale):
                code = "source_invalid"
            elif isinstance(exc, PresentationBuildAborted):
                code = "cancelled"
            elif isinstance(exc, PresentationBuildFailure):
                code = exc.code if exc.code in lifecycle.ERROR_CODES else "integrity_mismatch"
            elif isinstance(exc, (KeyError, TypeError, ValueError)):
                code = "integrity_mismatch"
            else:
                code = "infrastructure_failed"
            await self._fail(request, token, code)
        finally:
            # The work task has quiesced before this owner drops its inputs.
            self._build_inputs.pop(request.id, None)
            if self._active_abort is abort:
                self._active_abort = None
        return request.id

    async def _run(self):
        while not self._stop.is_set():
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NGS derived-product worker iteration failed")
                processed = None
            if processed is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
                except asyncio.TimeoutError:
                    pass


__all__ = ["NgsAlignmentPresentationWorker", "PresentationBuildFailure"]
