"""BC2 publication adapter: immutable native bytes in JobArtifact, no Design projection.

The native reader is the sole interpretation of scientific CSV values. This adapter
registers the sealed input documents, binds them to a Job and offers verified
readback through that same reader. It does not assert that a native CIF is a PDB
or that a native retained row is selectable by legacy Design consumers.
"""
from __future__ import annotations

import hashlib
import stat
import uuid
from pathlib import Path

from sqlalchemy import select

from database import Design, Job, JobArtifact
from services.bindcraft2_native_results import NativePublication, read_native_publication


class PublicationError(ValueError):
    pass


_SCHEMA = "bindcraft2.native-publication.v1"
_TABLES = (
    "1_Trajectories/!_Trajectories.csv", "2_Refolded/!_Refolded.csv",
    "3_Ranked/!_Ranked.csv", "trajectories.csv", "candidates.csv", "ranked.csv",
    ".campaign_state.json", "campaign_metadata.json",
)


def _regular(root: Path, relative: str) -> tuple[Path, bytes]:
    path = root
    for part in Path(relative).parts:
        if part in (".", "..") or part == "":
            raise PublicationError("unsafe native relative path")
        path = path / part
        mode = path.lstat().st_mode
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise PublicationError(f"unsafe native path: {relative}")
    if not stat.S_ISREG(path.stat().st_mode):
        raise PublicationError(f"native file is not regular: {relative}")
    return path, path.read_bytes()


def _inventory(root: Path, publication: NativePublication) -> dict[str, dict]:
    names = {"sweep.csv"} if (root / "sweep.csv").exists() else set()
    for arm in publication.arms:
        prefix = f"{arm.name}/" if arm.name else ""
        for name in _TABLES:
            if (root / prefix / name).exists():
                names.add(prefix + name)
        attempts = root / prefix / "1_Trajectories/!_BMS_Attempts"
        if attempts.exists():
            if attempts.is_symlink() or not attempts.is_dir():
                raise PublicationError("unsafe attempt directory")
            names.update(prefix + "1_Trajectories/!_BMS_Attempts/" + entry.name for entry in attempts.iterdir())
        names.update(doc.path for doc in arm.documents)
    inventory = {}
    for name in sorted(names):
        path, content = _regular(root, name)
        inventory[name] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
                           "media_type": "chemical/x-mmcif" if path.suffix.lower() in (".cif", ".mmcif") else
                           "chemical/x-pdb" if path.suffix.lower() in (".pdb", ".ent") else
                           "text/csv" if path.suffix.lower() == ".csv" else "application/json"}
    return inventory


def _summary(publication: NativePublication) -> list[dict]:
    return [{"arm": arm.name, **arm.accounting,
             "verified_attempts": sum(row.attempt_sha256 is not None for row in arm.trajectories),
             "qualified_documents": sum(doc.retained_design is not None for doc in arm.documents),
             "unassociated_documents": sum(doc.retained_design is None for doc in arm.documents)}
            for arm in publication.arms]


def _receipt(job: Job, root: Path, inventory: dict, publication: NativePublication) -> dict:
    return {"schema": _SCHEMA, "root": str(root), "attempt": job.retry_count or 0,
            "remote_attempt_id": job.remote_attempt_id,
            "files": inventory, "arms": _summary(publication),
            "selection": {"eligible": False, "reason": "native CIF has no verified lossless Design/PDB conversion, chain role map, or explicit target-state identity"}}


async def publish_native_results(job: Job, root: Path, session, *, commit: bool = False) -> int:
    """Publish a sealed local or returned native campaign within the caller transaction.

    Returns zero Designs; native row counts live in the model-owned read result.
    A replay with changed bytes or changed attempt identity is refused.
    """
    if job.model_id != "bindcraft2":
        raise PublicationError("BC2 publication requires a persisted BC2 job")
    root = Path(root).absolute()
    if not root.is_dir() or root.is_symlink():
        raise PublicationError("missing or unsafe native root")
    if not job.output_dir or Path(job.output_dir).absolute() != root:
        raise PublicationError("native root differs from persisted job output directory")
    publication = read_native_publication(root)
    inventory = _inventory(root, publication)
    if not inventory or not any(name.endswith(("!_Trajectories.csv", "trajectories.csv", ".campaign_state.json"))
                                for name in inventory):
        raise PublicationError("BC2 campaign has no native execution evidence")
    receipt = _receipt(job, root, inventory, publication)
    previous = (job.provenance or {}).get("bindcraft2_native_publication")
    if previous is not None and previous != receipt:
        raise PublicationError("BC2 native publication replay changed")
    with session.no_autoflush:
        existing = (await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))).all()
        designs = (await session.scalars(select(Design.id).where(Design.job_id == job.id))).all()
    if designs:
        raise PublicationError("BC2 job already contains Designs; refusing mixed result authority")
    attempt = receipt["attempt"]
    owned = {a.logical_path: a for a in existing if a.attempt == attempt}
    if any(a.logical_path.startswith("bindcraft2/native/") and a.attempt != attempt for a in existing):
        raise PublicationError("BC2 native artifacts belong to another job attempt")
    if previous is None and any(name.startswith("bindcraft2/native/") for name in owned):
        raise PublicationError("BC2 native artifacts exist without publication receipt")
    for name, entry in inventory.items():
        key = "bindcraft2/native/" + name
        artifact = owned.get(key)
        expected = (entry["sha256"], entry["bytes"], str(root / name))
        if artifact:
            if (artifact.sha256, artifact.bytes, artifact.storage_path) != expected:
                raise PublicationError("BC2 registered artifact differs from sealed bytes")
        else:
            if previous is not None:
                raise PublicationError("BC2 publication missing registered artifact")
            session.add(JobArtifact(id=str(uuid.uuid4()), owner_job_id=job.id, attempt=attempt,
                                    logical_path=key, storage_path=str(root / name),
                                    sha256=entry["sha256"], bytes=entry["bytes"],
                                    media_type=entry["media_type"],
                                    provenance={"schema": _SCHEMA, "remote_attempt_id": job.remote_attempt_id}))
    if previous is not None and {name for name in owned if name.startswith("bindcraft2/native/")} != {
        "bindcraft2/native/" + name for name in inventory}:
        raise PublicationError("BC2 registered artifact inventory changed")
    job.provenance = {**(job.provenance or {}), "bindcraft2_native_publication": receipt}
    if commit:
        await session.commit()
    else:
        await session.flush()
    return 0


async def read_published_native_results(job: Job, session) -> tuple[NativePublication, dict]:
    """Reopen only if every registered artifact still matches the sealed receipt."""
    if job.model_id != "bindcraft2":
        raise PublicationError("not a BC2 job")
    receipt = (job.provenance or {}).get("bindcraft2_native_publication")
    if not isinstance(receipt, dict) or receipt.get("schema") != _SCHEMA:
        raise PublicationError("BC2 publication missing")
    root = Path(receipt["root"])
    if root.is_symlink() or not root.is_dir() or not job.output_dir or Path(job.output_dir).absolute() != root:
        raise PublicationError("BC2 published root is unavailable or changed")
    if receipt["attempt"] != (job.retry_count or 0) or receipt["remote_attempt_id"] != job.remote_attempt_id:
        raise PublicationError("BC2 job attempt changed")
    with session.no_autoflush:
        artifacts = (await session.scalars(select(JobArtifact).where(
            JobArtifact.owner_job_id == job.id, JobArtifact.attempt == receipt["attempt"]))).all()
    registered = {a.logical_path.removeprefix("bindcraft2/native/"): a for a in artifacts
                  if a.logical_path.startswith("bindcraft2/native/")}
    if set(registered) != set(receipt["files"]):
        raise PublicationError("BC2 registered artifact inventory changed")
    for name, expected in receipt["files"].items():
        path, content = _regular(root, name)
        row = registered[name]
        if (hashlib.sha256(content).hexdigest() != expected["sha256"] or len(content) != expected["bytes"]
                or row.sha256 != expected["sha256"] or row.bytes != expected["bytes"]
                or row.storage_path != str(path) or row.media_type != expected["media_type"]):
            raise PublicationError("BC2 published bytes or artifact receipt changed")
    publication = read_native_publication(root)
    if _inventory(root, publication) != receipt["files"] or _summary(publication) != receipt["arms"]:
        raise PublicationError("BC2 native inventory or accounting changed")
    return publication, receipt
