"""Cross-process transaction fence for atomic NGS/MolBio package acceptance."""
from __future__ import annotations

import asyncio
import fcntl
import os
import stat
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import AsyncIterator, Iterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from paths import get_db_path


_FENCE_OVERRIDE_ENV = "BMS_NGS_MOLBIO_QUIESCENCE_FENCE_PATH"
_DEFAULT_FENCE_NAME = ".ngs-molbio-source-writers.fence"
_MAX_OVERRIDE_BYTES = 1024
_SHARED_FENCE_FD = "ngs_molbio_quiescence_shared_fd"
_EXCLUSIVE_FENCE_OWNER = "ngs_molbio_quiescence_exclusive_owner"


class NgsMolBioQuiescenceError(RuntimeError):
    """The package source-writer fence could not be acquired safely."""


class NgsMolBioQuiescedSession(Session):
    """Sync session used underneath every fenced AsyncSession factory."""


def package_quiescence_fence_path() -> Path:
    """Resolve one bounded package-local fence path beside the configured core DB."""
    core_parent = get_db_path().expanduser().resolve().parent
    configured = os.getenv(_FENCE_OVERRIDE_ENV)
    if configured is None:
        return core_parent / _DEFAULT_FENCE_NAME
    if not configured or "\x00" in configured or len(configured.encode("utf-8")) > _MAX_OVERRIDE_BYTES:
        raise NgsMolBioQuiescenceError(f"{_FENCE_OVERRIDE_ENV} is empty or exceeds the bounded path contract")
    override = Path(configured).expanduser()
    candidate = (override if override.is_absolute() else core_parent / override).resolve()
    if candidate.parent != core_parent or candidate.name in {"", ".", ".."}:
        raise NgsMolBioQuiescenceError(
            f"{_FENCE_OVERRIDE_ENV} must resolve to one direct child of the configured core DB parent"
        )
    return candidate


def _open_fence_fd() -> int:
    path = package_quiescence_fence_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NgsMolBioQuiescenceError("package quiescence fence is not a regular file")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _release_fence_fd(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _release_session_shared_fence(session: Session) -> None:
    fd = session.info.pop(_SHARED_FENCE_FD, None)
    if isinstance(fd, int):
        _release_fence_fd(fd)


def _after_transaction_create(session: Session, transaction) -> None:  # noqa: ANN001
    if transaction.parent is not None or session.info.get(_EXCLUSIVE_FENCE_OWNER) is True:
        return
    if _SHARED_FENCE_FD in session.info:
        raise NgsMolBioQuiescenceError("root transaction already owns a package source-writer fence")
    fd = _open_fence_fd()
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise NgsMolBioQuiescenceError("package acceptance currently owns the exclusive source-writer fence") from exc
    except BaseException:
        os.close(fd)
        raise
    session.info[_SHARED_FENCE_FD] = fd


def _after_transaction_end(session: Session, transaction) -> None:  # noqa: ANN001
    if transaction.parent is None:
        _release_session_shared_fence(session)


def _after_soft_rollback(session: Session, previous_transaction) -> None:  # noqa: ANN001
    # A SAVEPOINT rollback can leave the Session inactive while its root
    # transaction is still open.  Releasing here on ``session.is_active``
    # would let package acceptance cross an unfinished root rollback/close.
    if previous_transaction.parent is None:
        _release_session_shared_fence(session)


def _register_session_fence_listeners() -> None:
    listeners = (
        ("after_transaction_create", _after_transaction_create),
        ("after_transaction_end", _after_transaction_end),
        ("after_soft_rollback", _after_soft_rollback),
    )
    for event_name, listener in listeners:
        if not event.contains(NgsMolBioQuiescedSession, event_name, listener):
            event.listen(NgsMolBioQuiescedSession, event_name, listener)


_register_session_fence_listeners()


def _sync_session(session: AsyncSession | Session) -> Session:
    return session.sync_session if isinstance(session, AsyncSession) else session


@contextmanager
def exclusive_fence_owner_sessions(
    *sessions: AsyncSession | Session,
) -> Iterator[None]:
    """Mark supplied idle sessions as the exclusive fence owner's own sessions."""
    sync_sessions = tuple(_sync_session(session) for session in sessions)
    for session in sync_sessions:
        if not isinstance(session, NgsMolBioQuiescedSession):
            raise NgsMolBioQuiescenceError("package acceptance received an unfenced SQLAlchemy session")
        if session.in_transaction():
            raise NgsMolBioQuiescenceError(
                "package acceptance sessions must be transaction-idle before exclusive fence acquisition"
            )
    previous = tuple(session.info.get(_EXCLUSIVE_FENCE_OWNER) for session in sync_sessions)
    try:
        for session in sync_sessions:
            session.info[_EXCLUSIVE_FENCE_OWNER] = True
        yield
    finally:
        for session, prior in zip(sync_sessions, previous, strict=True):
            if prior is None:
                session.info.pop(_EXCLUSIVE_FENCE_OWNER, None)
            else:
                session.info[_EXCLUSIVE_FENCE_OWNER] = prior


def _release_abandoned_exclusive_wait(waiter: asyncio.Task[None], fd: int) -> None:
    try:
        waiter.result()
    except BaseException:
        os.close(fd)
    else:
        _release_fence_fd(fd)


async def _acquire_exclusive_fence() -> int:
    fd = _open_fence_fd()
    waiter = asyncio.create_task(asyncio.to_thread(fcntl.flock, fd, fcntl.LOCK_EX))
    try:
        await asyncio.shield(waiter)
    except asyncio.CancelledError:
        waiter.add_done_callback(lambda done: _release_abandoned_exclusive_wait(done, fd))
        raise
    except BaseException:
        os.close(fd)
        raise
    return fd


@asynccontextmanager
async def package_acceptance_exclusive_fence(
    *sessions: AsyncSession | Session,
) -> AsyncIterator[None]:
    """Wait for current writers, bar new writers, and admit only owner sessions."""
    sync_sessions = tuple(_sync_session(session) for session in sessions)
    for session in sync_sessions:
        if not isinstance(session, NgsMolBioQuiescedSession):
            raise NgsMolBioQuiescenceError("package acceptance received an unfenced SQLAlchemy session")
        if session.in_transaction():
            raise NgsMolBioQuiescenceError(
                "package acceptance sessions must be transaction-idle before exclusive fence acquisition"
            )
    fd = await _acquire_exclusive_fence()
    try:
        with exclusive_fence_owner_sessions(*sync_sessions):
            yield
    finally:
        _release_fence_fd(fd)


__all__ = [
    "NgsMolBioQuiescedSession",
    "NgsMolBioQuiescenceError",
    "exclusive_fence_owner_sessions",
    "package_acceptance_exclusive_fence",
    "package_quiescence_fence_path",
]