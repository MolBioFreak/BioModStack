from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from database import ExternalResultImport
from .service import process_external_import, recover_external_imports


logger = logging.getLogger(__name__)


class ExternalImportWorker:
    """Restart-safe poller for durable external-result import rows."""

    def __init__(self, session_factory: Any, *, data_root: Path | None = None, poll_interval: float = 2.0):
        self._session_factory = session_factory
        self._data_root = data_root
        self._poll_interval = max(0.05, float(poll_interval))
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        async with self._session_factory() as session:
            await recover_external_imports(session)
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="external-result-import-worker")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def run_once(self) -> str | None:
        async with self._session_factory() as session:
            import_id = (
                await session.execute(
                    select(ExternalResultImport.id)
                    .where(ExternalResultImport.state == "discovered")
                    .order_by(ExternalResultImport.created_at.asc(), ExternalResultImport.id.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if import_id is None:
                return None
            await process_external_import(session, import_id=import_id, data_root=self._data_root)
            return import_id

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("External result import worker iteration failed")
                processed = None
            if processed is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
                except asyncio.TimeoutError:
                    pass
