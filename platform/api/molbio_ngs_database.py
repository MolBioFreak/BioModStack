"""Async ownership boundary for the dedicated MolBio/NGS domain-state SQLite store."""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from migrations.sqlite_sha256 import register_sqlite_sha256
from molbio_ngs_migrations import health, run_all
from molbio_ngs_models import MolBioNGSBase
from paths import get_molbio_ngs_db_path, get_molbio_ngs_db_url


def _sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite"):
        return None
    if ":///" in url:
        return Path(url.split(":///", 1)[1]).expanduser().resolve()
    parsed = urlparse(url)
    return Path(parsed.path).expanduser().resolve() if parsed.path else None


def create_molbio_ngs_engine(url: str | None = None):
    database_url = url or get_molbio_ngs_db_url()
    if not database_url.startswith("sqlite"):
        raise ValueError("MolBio/NGS scientific-state persistence currently supports SQLite only")
    target = _sqlite_path_from_url(database_url)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(database_url, echo=False, connect_args={"timeout": 30})

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):  # noqa: ANN001
        register_sqlite_sha256(dbapi_connection)
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()

    return engine


def create_molbio_ngs_session_factory(engine):  # noqa: ANN001
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


molbio_ngs_engine = create_molbio_ngs_engine()
molbio_ngs_session_factory = create_molbio_ngs_session_factory(molbio_ngs_engine)


async def init_molbio_ngs_db() -> None:
    path = _sqlite_path_from_url(get_molbio_ngs_db_url())
    if path is None:
        raise RuntimeError("MolBio/NGS scientific-state persistence currently requires SQLite")
    await asyncio.to_thread(run_all, path)


async def molbio_ngs_health() -> dict[str, object]:
    return await asyncio.to_thread(health, get_molbio_ngs_db_path())


async def get_molbio_ngs_session():
    async with molbio_ngs_session_factory() as session:
        yield session


__all__ = [
    "MolBioNGSBase",
    "create_molbio_ngs_engine",
    "create_molbio_ngs_session_factory",
    "get_molbio_ngs_db_path",
    "get_molbio_ngs_db_url",
    "get_molbio_ngs_session",
    "init_molbio_ngs_db",
    "molbio_ngs_engine",
    "molbio_ngs_health",
    "molbio_ngs_session_factory",
]
