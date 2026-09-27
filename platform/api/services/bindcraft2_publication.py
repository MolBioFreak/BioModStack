"""BC2 publication adapter: immutable native bytes and exact candidate bindings.

The native reader is the sole interpretation of scientific CSV values. This adapter
registers the sealed input documents, binds them to a Job and offers verified
readback through that same reader. It does not assert that a native CIF is a PDB
or that a native retained row is selectable by legacy Design consumers.
"""
from __future__ import annotations

import hashlib
import mimetypes
import stat
import uuid
from pathlib import Path

from sqlalchemy import select

from database import Design, Job, JobArtifact
from services.bindcraft2_candidate_projection import project_native_candidates
from services.bindcraft2_native_results import NativePublication, read_native_publication


class PublicationError(ValueError):
    pass


_SCHEMA = "bindcraft2.native-publication.v1"
_TABLES = (
    "1_Trajectories/!_Trajectories.csv", "2_Refolded/!_Refolded.csv",
    "3_Ranked/!_Ranked.csv", "trajectories.csv", "candidates.csv", "ranked.csv",
    "accepted.csv", ".campaign_state.json", "campaign_metadata.json",
    ".redesigned_sequences.txt", "summary.csv", "scored.csv", "best_settings.json",
    "filtered.csv", "rejected.csv", "native_score.txt",
)
# Pinned native docs/outputs.md families. Preserve bytes, not invented score rows.
_NATIVE_DIRS = ("1_Trajectories", "2_Refolded", "3_Ranked", "trajectories", "refolded", "accepted", "workers")


def native_campaign_root(job: Job, root: Path, campaign_root: Path | None = None) -> Path:
    """Resolve an explicit execution root within the owned Job; never glob for it.

    bc2_campaign_dir is the existing materialized runtime directory, not the
    native project_folder. Old flat publications have no such declaration.
    The publication receipt wins on reopen/replay, including historical jobs.
    """
    previous = (job.provenance or {}).get("bindcraft2_native_publication")
    if campaign_root is None:
        if previous is not None:
            campaign_root = root / previous.get("campaign_root", ".")
        elif (job.params or {}).get("bc2_campaign_dir"):
            campaign_root = Path(job.params["bc2_campaign_dir"]) / "campaign"
        else:
            campaign_root = root
    campaign_root = Path(campaign_root)
    if not campaign_root.is_absolute():
        campaign_root = root / campaign_root
    if ".." in campaign_root.parts or not campaign_root.is_relative_to(root):
        raise PublicationError("native campaign root escapes owned job output directory")
    current = root
    for part in campaign_root.relative_to(root).parts:
        current /= part
        if current.is_symlink() or not current.is_dir():
            raise PublicationError("missing or unsafe native campaign root")
    return campaign_root


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


def _inventory(root: Path, publication: NativePublication, *, legacy: bool = False) -> dict[str, dict]:
    names = {"sweep.csv"} if (root / "sweep.csv").exists() else set()

    def collect(directory: Path) -> None:
        if directory.is_symlink() or not directory.is_dir():
            raise PublicationError("unsafe native output directory")
        for entry in directory.iterdir():
            # Native coordination/unfinished writes and compiled caches are not outputs.
            if entry.name.endswith((".lock", ".partial")) or entry.name in {"__pycache__", ".cache"}:
                continue
            if entry.is_symlink():
                raise PublicationError("unsafe native output path")
            if entry.is_dir():
                collect(entry)
            else:
                names.add(entry.relative_to(root).as_posix())

    folders = [root / arm.name if arm.name else root for arm in publication.arms]
    if root not in folders and not legacy:
        folders.append(root)  # sweep-level summaries, settings and durable state
    for folder in folders:
        tables = _TABLES[:6] + (".campaign_state.json", "campaign_metadata.json") if legacy else _TABLES
        for name in tables:
            if (folder / name).exists():
                names.add((folder / name).relative_to(root).as_posix())
        if legacy:
            attempts = folder / "1_Trajectories/!_BMS_Attempts"
            if attempts.exists():
                collect(attempts)
        else:
            for directory in _NATIVE_DIRS:
                if (folder / directory).exists():
                    collect(folder / directory)
            for pattern in ("campaign_metadata*.json", "ranked_by_*.csv"):
                for path in folder.glob(pattern):
                    names.add(path.relative_to(root).as_posix())
    for arm in publication.arms:
        names.update(doc.path for doc in arm.documents)
    inventory = {}
    for name in sorted(names):
        path, content = _regular(root, name)
        inventory[name] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
                           "media_type": "chemical/x-mmcif" if path.suffix.lower() in (".cif", ".mmcif") else
                           "chemical/x-pdb" if path.suffix.lower() in (".pdb", ".ent") else
                           "text/csv" if path.suffix.lower() == ".csv" else
                           "application/json" if legacy else
                           mimetypes.guess_type(path.name)[0] or "application/octet-stream"}
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
            "files": inventory, "arms": _summary(publication)}

def _candidate_identity(binding: dict) -> dict:
    return {**{key: binding[key] for key in ("arm", "retained_design", "scored_design", "trajectory_design", "attempt_sha256")},
            "structures": sorted([{key: structure[key] for key in
                ("sha256", "target_state", "primary", "variant", "binder_chains", "target_chains")}
                for structure in binding["structures"]], key=lambda item: (item["target_state"], item["variant"], item["sha256"]))}


async def _publication_source(job: Job, session, previous: dict | None) -> dict | None:
    # Snapshot the existing source join once. Reopen never requires the source
    # worker/files to be online and never changes an immutable published parent.
    if previous is not None:
        return previous.get("source")
    source_id = (job.params or {}).get("bc2_source_job_id")
    if not source_id:
        return None
    source = await session.get(Job, source_id)
    publication = (source.provenance or {}).get("bindcraft2_native_publication", {}) if source else {}
    source_root = (source.lineage_root_job_id or publication.get("source", {}).get("lineage_root_job_id") or source.id) if source else job.id
    return {"job_id": source_id,
            "lineage_root_job_id": job.lineage_root_job_id or source_root,
            "candidate_origins": [{"identity": _candidate_identity(binding),
                "parent_design_id": binding["design_id"],
                "origin_design_id": binding.get("origin_design_id", binding["design_id"]),
                "origin_job_id": binding.get("origin_job_id", source_id)}
                for binding in publication.get("candidates", [])]}


def _candidate_bindings(publication: NativePublication, artifacts: dict[str, JobArtifact], job_id: str,
                        source: dict | None = None) -> list[dict]:
    """Bind producer-joined candidates to registered native documents, never names inferred from paths."""
    bindings = []
    for candidate in project_native_candidates(publication):
        structures = []
        for structure in candidate.structures:
            artifact = artifacts.get("bindcraft2/native/" + structure.path)
            if artifact is None or artifact.sha256 != structure.sha256:
                raise PublicationError("projected CIF lacks matching registered JobArtifact")
            structures.append({"artifact_id": artifact.id, "logical_path": artifact.logical_path,
                               "sha256": structure.sha256, "target_state": structure.target_state,
                               "primary": structure.primary, "variant": structure.variant,
                               "binder_chains": structure.binder_chains,
                               "target_chains": structure.target_chains})
        bindings.append({"design_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "bindcraft2:" +
                        repr((job_id, candidate.arm, candidate.retained_design,
                              candidate.scored_design, candidate.trajectory_design,
                              candidate.attempt_sha256, candidate.primary_structure.sha256)))),
                         "arm": candidate.arm, "retained_design": candidate.retained_design,
                         "scored_design": candidate.scored_design,
                         "trajectory_design": candidate.trajectory_design,
                         "attempt_sha256": candidate.attempt_sha256,
                         "sequence": candidate.sequence, "native_rank": candidate.native_rank,
                         "structures": structures})
    if source is not None:
        for binding in bindings:
            binding.update(source_job_id=source["job_id"], lineage_root_job_id=source["lineage_root_job_id"])
            matches = [origin for origin in source["candidate_origins"]
                       if origin["identity"] == _candidate_identity(binding)]
            if len(matches) == 1:
                binding.update({key: matches[0][key] for key in ("parent_design_id", "origin_design_id", "origin_job_id")})
            # Changed/new outputs keep their own origin; no prior confidence or
            # validation is copied, and absent associations remain absent.
    return bindings


def _design_fields(job: Job, binding: dict, root: Path) -> dict:
    primary = next(s for s in binding["structures"] if s["primary"])
    return {"id": binding["design_id"], "job_id": job.id,
            "name": binding["retained_design"], "producer_model_id": "bindcraft2",
            # The legacy column name is historical: this remains the native CIF.
            "pdb_path": str(root / primary["logical_path"].removeprefix("bindcraft2/native/")),
            "lineage_root_job_id": binding.get("lineage_root_job_id", job.id),
            "origin_job_id": binding.get("origin_job_id", job.id),
            **({"parent_design_id": binding.get("parent_design_id"),
                "origin_design_id": binding.get("origin_design_id"),
                "source_stage_job_id": binding["source_job_id"]} if binding.get("source_job_id") else {}),
            "stage_family": "bindcraft2", "stage_mode": job.mode,
            "artifact_class": "binder_complex", "artifact_schema_version": 1,
            "provenance": {"schema": "bindcraft2.candidate-lineage.v1",
                           "arm": binding["arm"], "retained_design": binding["retained_design"],
                           "scored_design": binding["scored_design"],
                           "trajectory_design": binding["trajectory_design"],
                           "attempt_sha256": binding["attempt_sha256"],
                           "primary_target_state": primary["target_state"],
                           "primary_artifact_id": primary["artifact_id"],
                           **({"source_job_id": binding["source_job_id"],
                               "copied_source_design_id": binding.get("parent_design_id")}
                              if binding.get("source_job_id") else {})}}


async def _verify_designs(job: Job, session, bindings: list[dict], root: Path) -> None:
    with session.no_autoflush:
        rows = (await session.scalars(select(Design).where(Design.job_id == job.id))).all()
    expected = {b["design_id"]: _design_fields(job, b, root) for b in bindings}
    if len(expected) != len(bindings) or set(expected) != {row.id for row in rows}:
        raise PublicationError("BC2 persisted candidate Design identity changed")
    for row in rows:
        for key, value in expected[row.id].items():
            if getattr(row, key) != value:
                raise PublicationError(f"BC2 persisted candidate Design lineage changed: {key}")


async def publish_native_results(job: Job, root: Path, session, *, commit: bool = False,
                                 campaign_root: Path | None = None) -> int:
    """Publish native artifacts and exact producer-joined CIF Designs atomically.

    Unjoined rows and valid zero-yield campaigns remain native observations.
    Replay requires identical bytes, bindings and persisted Design identity.
    """
    if job.model_id != "bindcraft2":
        raise PublicationError("BC2 publication requires a persisted BC2 job")
    root = Path(root).absolute()
    if not root.is_dir() or root.is_symlink():
        raise PublicationError("missing or unsafe native root")
    if not job.output_dir or Path(job.output_dir).absolute() != root:
        raise PublicationError("native root differs from persisted job output directory")
    owned_root = root
    root = native_campaign_root(job, owned_root, campaign_root)
    previous = (job.provenance or {}).get("bindcraft2_native_publication")
    legacy = previous is not None and "inventory_version" not in previous
    publication = read_native_publication(root)
    inventory = _inventory(root, publication, legacy=legacy)
    if not inventory or not any(name.endswith(("!_Trajectories.csv", "trajectories.csv", ".campaign_state.json"))
                                for name in inventory):
        raise PublicationError("BC2 campaign has no native execution evidence")
    receipt = _receipt(job, owned_root, inventory, publication)
    if not legacy:
        receipt.update(campaign_root=root.relative_to(owned_root).as_posix(), inventory_version=2)
    source = await _publication_source(job, session, previous)
    if source is not None:
        receipt["source"] = source
    if previous is not None and {key: value for key, value in previous.items() if key != "candidates"} != receipt:
        raise PublicationError("BC2 native publication replay changed")
    with session.no_autoflush:
        existing = (await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))).all()
        designs = (await session.scalars(select(Design).where(Design.job_id == job.id))).all()
    if previous is None and designs:
        raise PublicationError("BC2 Designs exist without native publication receipt")
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
            artifact = JobArtifact(id=str(uuid.uuid4()), owner_job_id=job.id, attempt=attempt,
                                    logical_path=key, storage_path=str(root / name),
                                    sha256=entry["sha256"], bytes=entry["bytes"],
                                    media_type=entry["media_type"],
                                    provenance={"schema": _SCHEMA, "remote_attempt_id": job.remote_attempt_id})
            session.add(artifact)
            owned[key] = artifact
    if previous is not None and {name for name in owned if name.startswith("bindcraft2/native/")} != {
        "bindcraft2/native/" + name for name in inventory}:
        raise PublicationError("BC2 registered artifact inventory changed")
    receipt["candidates"] = _candidate_bindings(publication, owned, job.id, source=source)
    if previous is not None and previous != receipt:
        raise PublicationError("BC2 native publication replay changed")
    if previous is not None:
        await _verify_designs(job, session, receipt["candidates"], root)
    else:
        ids = [binding["design_id"] for binding in receipt["candidates"]]
        if len(ids) != len(set(ids)):
            raise PublicationError("BC2 candidate producer identities collide")
        for binding in receipt["candidates"]:
            session.add(Design(**_design_fields(job, binding, root)))
    job.provenance = {**(job.provenance or {}), "bindcraft2_native_publication": receipt}
    if commit:
        await session.commit()
    else:
        await session.flush()
    return len(receipt["candidates"])


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
    root = native_campaign_root(job, root)
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
    if (_inventory(root, publication, legacy="inventory_version" not in receipt) != receipt["files"]
            or _summary(publication) != receipt["arms"]):
        raise PublicationError("BC2 native inventory or accounting changed")
    if receipt.get("candidates") != _candidate_bindings(publication, {
            "bindcraft2/native/" + name: row for name, row in registered.items()}, job.id, source=receipt.get("source")):
        raise PublicationError("BC2 candidate artifact bindings changed")
    await _verify_designs(job, session, receipt["candidates"], root)
    return publication, receipt


def native_workbench_page(page: dict, receipt: dict) -> dict:
    """Decorate an already verified native page with existing Design/file handles.

    The caller obtains receipt from read_published_native_results in the same
    request. No filename/sequence join or second numerical authority is added.
    """
    from urllib.parse import quote
    from paths import to_allowed_relative

    root = Path(receipt["root"]) / receipt.get("campaign_root", ".")
    downloads = {}
    for name in receipt["files"]:
        try:
            relative = to_allowed_relative(root / name)
        except ValueError:
            continue  # Publication outside a configured download root remains readable.
        downloads[name] = "/api/files/download/" + quote(relative, safe="/")
    generation = page["stage"] == "generation"
    candidates = [binding for binding in receipt.get("candidates", [])
                  if generation or binding["arm"] == page["arm"]]
    generation_bindings = {item["candidate_key"]: item for item in candidates} if generation else {}
    rows = []
    for source in page["rows"]:
        row = dict(source)
        binding = (generation_bindings.get(row.get("candidate_key", row.get("candidate_id")))
                   if generation else next((item for item in candidates
                   if page["stage"] == "retained" and item["retained_design"] == row.get("design")), None))
        if binding:
            row["design_id"] = binding["design_id"]
            row["structures"] = [{**structure,
                "download_url": downloads.get(structure.get("path", structure["logical_path"].removeprefix("bindcraft2/native/")))}
                for structure in binding["structures"]]
        if page["stage"] == "document":
            row["download_url"] = downloads.get(row["path"])
        rows.append(row)
    prefix = f'{page["arm"]}/' if page["arm"] else ""
    return {**page, "rows": rows, "artifacts": [
        {"path": name, **entry, "download_url": downloads.get(name)}
        for name, entry in receipt["files"].items()
        if not prefix or name.startswith(prefix) or "/" not in name
    ]}
