"""Standalone Caliby native publication using existing JobArtifact custody.

The native JSON remains the numerical authority. No Design projection or
additional completion receipt is required for empty/native-only publications.
"""
from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import uuid
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select

from database import JobArtifact
from paths import get_data_root, resolve_runtime_data_path, to_allowed_relative
from services.caliby_native import SUPPORTED_MODES, read_native_results, science_params
from services.ligandmpnn_interface_publication import regular_bytes

DIRECTORY = "caliby_native"


def publication_root(job, root=None):
    supplied = Path(root if root is not None else job.output_dir)
    owned = Path(job.output_dir)
    def resolve(path):
        return resolve_runtime_data_path(path) if path.is_absolute() else get_data_root() / path
    if resolve(supplied) != resolve(owned):
        raise ValueError("Native publication is outside the Job output root")
    return resolve(owned)


def native_files(root: Path, directory: str) -> dict:
    """Inventory exact published files, never original worker/source paths."""
    folder = root / directory
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("Missing or unsafe native output directory")
    files = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("Unsafe native output path")
        if path.is_dir():
            continue
        content = regular_bytes(path)
        suffix = path.suffix.lower()
        media = ({".cif": "chemical/x-mmcif", ".mmcif": "chemical/x-mmcif",
                  ".pdb": "chemical/x-pdb", ".fa": "text/plain", ".fasta": "text/plain"}
                 .get(suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        files[path.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "media_type": media}
    return files


async def native_artifacts(job, root, directory, files, session, *, publish=False):
    """Use JobArtifact's existing attempt/path identity and digest authority."""
    rows = (await session.scalars(select(JobArtifact).where(
        JobArtifact.owner_job_id == job.id, JobArtifact.attempt == (job.retry_count or 0),
        JobArtifact.logical_path.startswith(directory + "/")))).all()
    existing = {row.logical_path: row for row in rows}
    if (existing or not publish) and set(existing) != set(files):
        raise ValueError("Native artifact inventory differs from registered publication")
    for name, meta in files.items():
        row = existing.get(name)
        if row is None:
            row = JobArtifact(id=str(uuid.uuid4()), owner_job_id=job.id,
                attempt=job.retry_count or 0, logical_path=name, storage_path=str(root / name),
                **meta, provenance={"model_id": job.model_id, "mode": job.mode,
                                    "remote_attempt_id": job.remote_attempt_id})
            session.add(row)
            existing[name] = row
        elif (resolve_runtime_data_path(row.storage_path) != root / name
              or any(getattr(row, key) != value for key, value in meta.items())
              or row.provenance != {"model_id": job.model_id, "mode": job.mode,
                                    "remote_attempt_id": job.remote_attempt_id}):
            raise ValueError("Native artifact bytes or ownership differ from registered publication")
    if publish:
        await session.flush()
    return existing


def artifact_handles(root, directory, files, artifacts):
    result = []
    for name, meta in files.items():
        try:
            url = "/api/files/download/" + quote(to_allowed_relative(root / name), safe="/")
        except ValueError:
            url = None  # Same governed-root behavior as the native BC2 workbench.
        relative = str(Path(name).relative_to(directory))
        result.append({"path": relative, "relative_path": relative, **meta,
                       "artifact_id": artifacts[name].id, "download_url": url,
                       "stream_url": url.replace("/files/download/", "/files/stream/", 1) if url else None})
    return result


def _associate_request(job, document):
    """Bind selected science before sealing output bytes in JobArtifact custody.

    The result's own hashes are not independent source evidence. Compare them
    with the controller's retained request when available, without reopening
    original structures or requiring prepared inputs to survive cleanup.
    """
    params = job.params or {}
    selected = science_params(job.mode, params)
    request = document.get("request", {})
    if request.get("requested") != selected:
        raise ValueError("Caliby native requested settings differ from Job request")
    effective = copy.deepcopy(selected)
    states = ([state for ensemble in effective["ensembles"] for state in ensemble["states"]]
              if job.mode == "ensemble_design" else effective["structures"])
    sources = request.get("sources")
    if not isinstance(sources, list) or len(sources) != len(states):
        raise ValueError("Caliby native ordered sources differ from Job request")
    for index, (state, source) in enumerate(zip(states, sources)):
        relative = Path("structures") / f"state_{index:06d}{Path(state['path']).suffix.lower()}"
        identity = {"state_id": state["state_id"], "source_path": state["path"],
                    "path": relative.as_posix(), "native_example_id": relative.stem}
        if not isinstance(source, dict) or any(source.get(key) != value for key, value in identity.items()):
            raise ValueError("Caliby native source identity differs from Job request")
        state["path"] = relative.as_posix()
    if request.get("effective") != effective:
        raise ValueError("Caliby native effective settings differ from Job request")

    retained_dir = params.get("caliby_request_dir")
    if not retained_dir:
        return
    try:
        retained = json.loads(regular_bytes(resolve_runtime_data_path(retained_dir) / "request.json"))
    except (OSError, ValueError):
        # Missing/unreadable historical custody is unavailable, not a new
        # completion prerequisite. Existing output JobArtifacts seal replay.
        return
    if not isinstance(retained, dict):
        return
    for key, expected in (("requested", selected), ("effective", effective)):
        if key in retained and retained[key] != expected:
            raise ValueError("Caliby retained settings differ from Job request")
    retained_sources = retained.get("sources")
    if not isinstance(retained_sources, list):
        return
    for index, source in enumerate(sources):
        if index >= len(retained_sources) or not isinstance(retained_sources[index], dict):
            continue
        prior = retained_sources[index]
        for key in ("state_id", "source_path", "path", "native_example_id", "sha256"):
            if prior.get(key) is not None and source.get(key) is not None and prior[key] != source[key]:
                raise ValueError("Caliby native source differs from retained Job source identity")


def _read(job, root):
    document = read_native_results(root / DIRECTORY)
    if (job.model_id != "caliby_experimental" or job.mode not in SUPPORTED_MODES
            or document.get("schema") != "bms.caliby-native-results.v1"
            or document.get("task") != job.mode):
        raise ValueError("Caliby native publication does not match Job mode")
    _associate_request(job, document)
    return document


async def publish_native_results(job, root, session):
    root = publication_root(job, root)
    files = native_files(root, DIRECTORY)
    _read(job, root)
    await native_artifacts(job, root, DIRECTORY, files, session, publish=True)
    return 0  # Native records are not fabricated Design rows.


async def read_published_native_results(job, session):
    root = publication_root(job)
    files = native_files(root, DIRECTORY)
    artifacts = await native_artifacts(job, root, DIRECTORY, files, session)
    document = _read(job, root)
    handles = artifact_handles(root, DIRECTORY, files, artifacts)
    by_path = {item["path"]: item for item in handles}
    return {**document, "job_id": job.id, "mode": job.mode, "artifacts": handles,
            "records": [{**row, "structure": by_path[row["structure_path"]],
                         "artifacts": [by_path[row["structure_path"]]]} for row in document["records"]]}
