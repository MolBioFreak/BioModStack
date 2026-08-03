"""Async SQLAlchemy access to the global experiment/workspace SQLite store."""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from experiment_migrations import run_all
from experiment_models import ExperimentBase
from migrations.sqlite_sha256 import register_sqlite_sha256
from paths import get_experiment_db_path, get_experiment_db_url


def _sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite"):
        return None
    if ":///" in url:
        return Path(url.split(":///", 1)[1]).expanduser().resolve()
    parsed = urlparse(url)
    return Path(parsed.path).expanduser().resolve() if parsed.path else None


def create_experiment_engine(url: str | None = None):
    database_url = url or get_experiment_db_url()
    kwargs: dict[str, object] = {"echo": False}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"timeout": 30}
    engine = create_async_engine(database_url, **kwargs)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine.sync_engine, "connect")
        def _configure_experiment_sqlite(dbapi_connection, _connection_record):
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


experiment_engine = create_experiment_engine()
experiment_session_factory = async_sessionmaker(
    experiment_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def create_experiment_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_experiment_db() -> None:
    path = _sqlite_path_from_url(get_experiment_db_url())
    if path is None:
        raise RuntimeError("global experiment persistence currently requires a SQLite URL")
    await asyncio.to_thread(run_all, path)


async def get_experiment_session():
    async with experiment_session_factory() as session:
        yield session


__all__ = [
    "ExperimentBase",
    "experiment_engine",
    "experiment_session_factory",
    "create_experiment_engine",
    "create_experiment_session_factory",
    "get_experiment_db_path",
    "get_experiment_db_url",
    "get_experiment_session",
    "init_experiment_db",
]
