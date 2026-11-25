"""Async ownership boundary for the dedicated MolBio/NGS domain-state SQLite store."""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from biomodstack_runtime_profile import install_feature_enabled
from migrations.sqlite_sha256 import register_sqlite_sha256
from molbio_ngs_migrations import health, run_all
from molbio_ngs_models import MolBioNGSBase
from paths import get_molbio_ngs_db_path, get_molbio_ngs_db_url

_molbio_ngs_engine = None
_molbio_ngs_session_factory = None
_molbio_ngs_startup_error: str | None = None


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
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def set_molbio_ngs_startup_error(error: str | None) -> None:
    global _molbio_ngs_startup_error
    _molbio_ngs_startup_error = error


def get_molbio_ngs_startup_error() -> str | None:
    return _molbio_ngs_startup_error


def reset_molbio_ngs_state() -> None:
    """Reset cached lazy engine/factory and startup error (used by tests)."""
    global _molbio_ngs_engine, _molbio_ngs_session_factory, _molbio_ngs_startup_error
    _molbio_ngs_engine = None
    _molbio_ngs_session_factory = None
    _molbio_ngs_startup_error = None


def get_molbio_ngs_engine():
    global _molbio_ngs_engine
    if _molbio_ngs_engine is None:
        _molbio_ngs_engine = create_molbio_ngs_engine()
    return _molbio_ngs_engine


def get_molbio_ngs_session_factory():
    global _molbio_ngs_session_factory
    if _molbio_ngs_session_factory is None:
        _molbio_ngs_session_factory = create_molbio_ngs_session_factory(get_molbio_ngs_engine())
    return _molbio_ngs_session_factory


class _LazyMolBioNgsSessionFactory:
    """Defer MolBio/NGS engine and file creation until a session is actually opened."""

    def __call__(self, *args, **kwargs):
        return get_molbio_ngs_session_factory()(*args, **kwargs)


molbio_ngs_session_factory = _LazyMolBioNgsSessionFactory()


def __getattr__(name: str):
    if name == "molbio_ngs_engine":
        return get_molbio_ngs_engine()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class _LazyMolBioNgsSession:
    """Proxy that avoids opening the optional MolBio/NGS store unless actually queried."""

    _is_lazy_molbio_ngs_session = True

    def __init__(self) -> None:
        self._session: AsyncSession | None = None
        self._cm = None

    async def _ensure_session(self) -> AsyncSession:
        if self._session is not None:
            return self._session
        if not install_feature_enabled("ngs_ont"):
            raise HTTPException(
                status_code=503,
                detail="NGS/ONT module is disabled for this installation",
            )
        if _molbio_ngs_startup_error is not None:
            raise HTTPException(
                status_code=503,
                detail=f"MolBio/NGS store is unavailable: {_molbio_ngs_startup_error}",
            )
        try:
            cm = molbio_ngs_session_factory()
            session = await cm.__aenter__()
        except (OSError, SQLAlchemyError, RuntimeError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"MolBio/NGS store is unavailable: {exc}",
            ) from exc
        self._cm = cm
        self._session = session
        return session

    def __getattr__(self, name: str):
        async def _async_delegate(*args, **kwargs):
            session = await self._ensure_session()
            target = getattr(session, name)
            try:
                result = target(*args, **kwargs)
                if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                    return await result
                return result
            except (OSError, SQLAlchemyError) as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"MolBio/NGS store is unavailable: {exc}",
                ) from exc

        return _async_delegate

    async def close_if_opened(self) -> None:
        if self._cm is not None:
            cm = self._cm
            self._cm = None
            self._session = None
            await cm.__aexit__(None, None, None)


async def init_molbio_ngs_db() -> None:
    path = _sqlite_path_from_url(get_molbio_ngs_db_url())
    if path is None:
        raise RuntimeError("MolBio/NGS scientific-state persistence currently requires SQLite")
    await asyncio.to_thread(run_all, path, verify_data=False)
    set_molbio_ngs_startup_error(None)


async def molbio_ngs_health(*, deep: bool = True) -> dict[str, object]:
    return await asyncio.to_thread(health, get_molbio_ngs_db_path(), deep=deep)


async def get_molbio_ngs_session():
    proxy = _LazyMolBioNgsSession()
    try:
        yield proxy
    finally:
        await proxy.close_if_opened()


__all__ = [
    "MolBioNGSBase",
    "create_molbio_ngs_engine",
    "create_molbio_ngs_session_factory",
    "get_molbio_ngs_db_path",
    "get_molbio_ngs_db_url",
    "get_molbio_ngs_engine",
    "get_molbio_ngs_session",
    "get_molbio_ngs_session_factory",
    "get_molbio_ngs_startup_error",
    "init_molbio_ngs_db",
    "molbio_ngs_engine",
    "molbio_ngs_health",
    "molbio_ngs_session_factory",
    "reset_molbio_ngs_state",
    "set_molbio_ngs_startup_error",
]
