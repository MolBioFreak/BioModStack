"""Durable controller-local result generations (caller holds attempt/DB fences).

The DB provenance marker is the commit authority, not the journal phase. Native
importers need canonical paths, so publication precedes their transaction commit;
recovery restores prior output unless that *same* generation was committed.
No network transfer or scientific retry is authorized by recovery.
"""
from __future__ import annotations

import hashlib
import json
import os
import fcntl
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import uuid

_launch_generation: ContextVar[tuple[str, str] | None] = ContextVar('result_launch_generation', default=None)

def launch_generation(incoming: Path) -> str | None:
    current = _launch_generation.get()
    return current[1] if current and current[0] == str(checked(incoming)) else None


class GenerationError(ValueError):
    pass


def checked(path: Path) -> Path:
    path = path.expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise GenerationError("Result generation path traverses a symlink")
    return path.resolve()


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_json(path: Path, value: dict) -> None:
    checked(path)
    tmp = checked(path.with_name(path.name + ".tmp"))
    with tmp.open("w") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    sync_dir(path.parent)


def move(source: Path, destination: Path) -> None:
    checked(source)
    checked(destination)
    os.replace(source, destination)
    sync_dir(source.parent)
    if source.parent != destination.parent:
        sync_dir(destination.parent)


def output_path(job) -> Path:
    # A native continuation selects a collector subtree, not a second return
    # store. Keep the generation journal bound to the original envelope root.
    receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
    bound = receipt.get('local_result_root') if job.execution_target_id else None
    return checked(Path(str(bound or job.child_output_dir or job.output_dir)))


def identity(job, digest: str) -> dict:
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise GenerationError("Result generation requires a terminal manifest digest")
    return dict(schema="bms.remote-result-generation.v1", job_id=str(job.id),
                attempt_id=str(job.remote_attempt_id), target_id=str(job.execution_target_id),
                source_revision=str(job.execution_source_revision), source_tree=str(job.execution_source_tree),
                envelope_sha256=str(job.execution_bundle_sha256), manifest_sha256=digest,
                output_dir=str(output_path(job)))


def key(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def staging_path(job, digest: str) -> Path:
    output = output_path(job)
    return checked(output.parent / f".{output.name}.remote-incoming" / key(identity(job, digest)))


def transfer_marker(incoming: Path) -> Path:
    return checked(incoming.with_name(incoming.name + ".transfer.json"))


@contextmanager
def transfer_handoff(incoming: Path):
    """Lock the stable inode through launch; the child inherits this open description."""
    path = checked(transfer_marker(incoming).with_suffix('.json.lock'))
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GenerationError("Result transport launch handoff is still owned") from exc
        yield fd
    finally:
        os.close(fd)


def prepare_transfer(incoming: Path) -> None:
    """Recover only proven never-spawned or supervisor-reaped transports.

    The lock excludes an unfinished launcher and an inherited supervisor, not
    an already-spawned writer. Never unlink the lock inode or infer PID death.
    """
    # An absent staging directory cannot contain an earlier transfer fence.
    # Inspecting a returning attempt must not create one merely to check it.
    if not checked(incoming).parent.exists():
        return
    with transfer_handoff(incoming):
        marker = transfer_marker(incoming)
        if marker.exists():
            record = json.loads(marker.read_text())
            if record.get('handoff') == 'kernel-lock-v1' and record.get('phase') in (None, 'starting'):
                try:
                    uuid.UUID(record['generation'])
                except (KeyError, ValueError, TypeError, AttributeError) as exc:
                    raise GenerationError('Invalid pre-spawn generation') from exc
                if (record.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                        or (record.get('phase') == 'starting' and
                            (record.get('schema') != 'bms.local-result-transport.v1'
                             or record.get('destination') != str(checked(incoming))
                             or 'supervisor' in record or 'writer' in record))
                        or (record.get('phase') is None and set(record) != {'boot_id', 'handoff', 'generation'})):
                    raise GenerationError("Invalid pre-spawn transport ownership record")
            else:
                validate_transfer_receipt(record, incoming)
            marker.unlink()
            sync_dir(marker.parent)


def validate_transfer_receipt(record: dict, incoming: Path) -> None:
    """Validate the supervisor's durable receipt without removing its fence."""
    import uuid

    try:
        uuid.UUID(record["boot_id"])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise GenerationError("Invalid retained transfer ownership record") from exc
    if record.get("schema") is not None:
        from .transfer_supervisor import SCHEMA

        if record.get("schema") != SCHEMA or record.get("destination") != str(checked(incoming)):
            raise GenerationError("Invalid retained transfer destination/ownership schema")
    if record.get("boot_id") == Path("/proc/sys/kernel/random/boot_id").read_text().strip():
        if record.get("schema") is None or record.get("phase") != "quiescent":
            raise GenerationError("Interrupted result transport requires local writer-quiescence recovery; supervisor receipt is not yet available")
        proof = record.get("quiescence")
        owners = [record.get("controller"), record.get("supervisor")]
        if proof == "descendants-reaped":
            owners.append(record.get("writer"))
        elif proof != "no-writer" or "writer" in record:
            raise GenerationError("Invalid transfer quiescence proof")
        fields = {"pid", "start_ticks", "process_group", "session"}
        if any(not isinstance(owner, dict) or set(owner) != fields
               or any(type(value) is not int or value <= 0 for value in owner.values())
               for owner in owners):
            raise GenerationError("Invalid transfer process identity receipt")
        if proof == "descendants-reaped":
            writer = record["writer"]
            if writer["pid"] != writer["process_group"] or writer["pid"] != writer["session"]:
                raise GenerationError("Invalid transfer writer process group")


def begin_transfer(incoming: Path) -> None:
    prepare_transfer(incoming)
    generation = str(uuid.uuid4())
    with transfer_handoff(incoming):
        durable_json(transfer_marker(incoming), dict(
            boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            handoff='kernel-lock-v1', generation=generation))
    _launch_generation.set((str(checked(incoming)), generation))


def end_transfer(incoming: Path) -> None:
    marker = transfer_marker(incoming)
    if marker.exists() and json.loads(marker.read_text()).get("schema") is not None:
        prepare_transfer(incoming)
        return
    # Legacy in-process transports have no supervisor record. Their ordinary
    # return/cleanup contract applies; restart still refuses their boot marker.
    marker.unlink(missing_ok=True)
    sync_dir(marker.parent)


def journal_path(job) -> Path:
    output = output_path(job)
    return checked(output.parent / f".{output.name}.remote-publication.json")


def _checkpoint(name: str) -> None:
    """Test fault-injection seam; deliberately no environment-controlled crash hook."""


def recover(job) -> bool:
    """Idempotent local repair under controller lock, using freshly loaded DB state."""
    journal = journal_path(job)
    if not journal.exists():
        return False
    record = json.loads(journal.read_text())
    ident = record["identity"]
    if ident != identity(job, ident["manifest_sha256"]):
        raise GenerationError("Publication journal belongs to a different attempt/source/destination")
    output = output_path(job)
    incoming = staging_path(job, ident["manifest_sha256"])
    backup = checked(incoming.with_name(incoming.name + ".previous"))
    committed = (job.provenance or {}).get("remote_result_generation") == ident
    if output.exists() and (committed or not incoming.exists()):
        manifest = checked(output / "result-manifest.json")
        if not manifest.is_file() or hashlib.sha256(manifest.read_bytes()).hexdigest() != ident["manifest_sha256"]:
            raise GenerationError("Visible generation does not match publication journal")
    if committed:
        if not output.is_dir() or incoming.exists():
            raise GenerationError("Committed result generation is missing or ambiguous")
    else:
        # Each move is itself recoverable; repeated recovery after either move
        # observes incoming present and will never move prior output into it.
        if not incoming.exists() and output.exists():
            move(output, incoming)
            _checkpoint("rollback_new")
        if backup.exists():
            if output.exists():
                raise GenerationError("Prior output restore destination is occupied")
            move(backup, output)
            _checkpoint("rollback_prior")
        if record["had_prior"] and not output.exists():
            raise GenerationError("Prior output missing during publication recovery")

    journal.unlink()
    sync_dir(journal.parent)
    return True


def publish(job, incoming: Path) -> tuple[Path, Path | None]:
    output = output_path(job)
    incoming = checked(incoming)
    digest = hashlib.sha256((incoming / "result-manifest.json").read_bytes()).hexdigest()
    ident = identity(job, digest)
    expected = staging_path(job, digest)
    if journal_path(job).exists():
        raise GenerationError("Unreconciled result publication journal")
    expected.parent.mkdir(parents=True, exist_ok=True)
    sync_dir(expected.parent.parent)
    if incoming != expected:
        if expected.exists():
            raise GenerationError("Result staging destination is occupied")
        move(incoming, expected)
        incoming = expected
    backup = checked(incoming.with_name(incoming.name + ".previous"))
    if backup.exists():
        raise GenerationError("Prior result generation already retained")
    # Persist the complete received tree before either rename or the DB commit.
    for path in incoming.rglob("*"):
        checked(path)
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    for path in sorted((p for p in incoming.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        sync_dir(path)
    sync_dir(incoming)
    had_prior = output.exists()
    durable_json(journal_path(job), dict(identity=ident, had_prior=had_prior))
    _checkpoint("prepared")
    if had_prior:
        move(output, backup)
    _checkpoint("prior_moved")
    move(incoming, output)
    _checkpoint("new_moved")
    job.provenance = dict(job.provenance or {}, remote_result_generation=ident)
    return output, backup if had_prior else None
