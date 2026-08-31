"""Compile immutable workflow-neutral packages for remote execution."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import subprocess
import tarfile
import uuid
from urllib.parse import urlsplit
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal

from paths import get_code_root, get_container_dir, get_data_root, get_weights_root
from services.result_contracts import resolve_result_contract

from .contracts import RemoteExecutionEnvelope, RemoteFileRecord


class RemoteBundleError(RuntimeError):
    pass


_SOURCE_IDENTITY_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class TransferPlan:
    source: Path
    remote_destination: str


@dataclass(frozen=True, slots=True)
class PreparedRemoteBundle:
    attempt_id: str
    local_attempt_dir: Path
    remote_attempt_dir: str
    remote_source_dir: str
    remote_runtime_dir: str
    remote_output_alias: str
    local_output_dir: Path
    envelope: RemoteExecutionEnvelope
    envelope_sha256: str
    runtime_identity_sha256: str
    source_transfer: TransferPlan
    runtime_transfers: tuple[TransferPlan, ...]
    input_transfers: tuple[TransferPlan, ...]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout.strip()


def current_source_identity(source_root: Path | None = None) -> tuple[str, str]:
    """Return the committed source identity used for new remote Jobs."""
    repo_root = (source_root or get_code_root()).resolve()
    if _git(repo_root, "status", "--porcelain", "--untracked-files=no"):
        raise RemoteBundleError("Remote execution requires a clean tracked source checkout")
    revision = _git(repo_root, "rev-parse", "HEAD")
    tree = _git(repo_root, "rev-parse", f"{revision}^{{tree}}")
    if not _SOURCE_IDENTITY_RE.fullmatch(revision) or not _SOURCE_IDENTITY_RE.fullmatch(tree):
        raise RemoteBundleError("Committed BMS source identity is invalid")
    return revision, tree


def resolve_job_result_contract(job: Any) -> dict[str, Any]:
    """Resolve the exact local ingestion contract bound into a remote attempt."""
    return resolve_result_contract(
        model_type=job.model_id,
        stage_family=job.stage_family,
        stage_mode=job.stage_mode or job.mode,
        artifact_class=job.selected_input_artifact_class,
        provenance=dict(job.provenance or {}),
    ).model_dump(mode="json")


def _safe_extract(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive_path, "r:") as archive:
        for member in archive.getmembers():
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RemoteBundleError("Git archive contains an unsafe path")
            if member.issym() or member.islnk():
                raise RemoteBundleError("Git archive symlinks are not accepted by the remote package")
            resolved = destination.joinpath(*member_path.parts).resolve()
            if resolved != root and root not in resolved.parents:
                raise RemoteBundleError("Git archive path escapes the revision root")
        archive.extractall(destination, filter="fully_trusted")


def _record_file(
    path: Path,
    relative_path: str,
    role: Literal["source", "input", "runtime", "result", "log", "receipt"],
) -> RemoteFileRecord:
    if path.is_symlink() or not path.is_file():
        raise RemoteBundleError(f"Package input is not one regular file: {path}")
    return RemoteFileRecord(
        relative_path=relative_path,
        size_bytes=path.stat().st_size,
        sha256=_sha256_file(path),
        role=role,
    )


def _record_runtime_symlink(path: Path, relative_path: str) -> RemoteFileRecord:
    target = os.readlink(path)
    payload = target.encode("utf-8")
    return RemoteFileRecord(
        relative_path=relative_path,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        role="runtime",
        link_target=target,
    )


def _records_for_source(
    source: Path,
    prefix: str,
    role: Literal["source", "input", "runtime", "result", "log", "receipt"],
) -> list[RemoteFileRecord]:
    if source.is_symlink():
        if role != "runtime":
            raise RemoteBundleError(f"Package path cannot be a symlink: {source}")
        return [_record_runtime_symlink(source, prefix)]
    if source.is_file():
        return [_record_file(source, prefix, role)]
    if not source.is_dir():
        raise RemoteBundleError(f"Required package path is unavailable: {source}")
    records: list[RemoteFileRecord] = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            if role != "runtime":
                raise RemoteBundleError(f"Package tree contains a symlink: {path}")
            relative_path = path.relative_to(source).as_posix()
            resolved = path.resolve()
            resolved_source = source.resolve()
            if resolved != resolved_source and resolved_source not in resolved.parents:
                if relative_path == "venv/.venv":
                    continue
                raise RemoteBundleError(f"Runtime package symlink escapes its release: {path}")
            relative = f"{prefix.rstrip('/')}/{relative_path}"
            records.append(_record_runtime_symlink(path, relative))
            continue
        if path.is_file():
            records.append(
                _record_file(path, f"{prefix.rstrip('/')}/{path.relative_to(source).as_posix()}", role)
            )
    if not records:
        raise RemoteBundleError(f"Required package directory is empty: {source}")
    return records


def _flatten_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _flatten_strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _flatten_strings(child)


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _runtime_assets(model_id: str, mode: str, params: dict[str, Any]) -> list[tuple[Path, str]]:
    container_root = get_container_dir().resolve()
    weights_root = get_weights_root().resolve()
    data_root = get_data_root().resolve()
    normalized_model = str(model_id or "").strip().lower()
    normalized_mode = str(mode or "").strip().lower()
    container_names: set[str] = set()
    weight_names: set[str] = set()
    extra_paths: set[Path] = set()
    api_runtime = data_root / "runtime" / "cm-api-python" / "current"
    if not api_runtime.exists():
        raise RemoteBundleError(f"Managed workflow Python runtime is unavailable: {api_runtime}")
    extra_paths.add(api_runtime.resolve())

    if normalized_model == "protenix":
        container_names.add("protenix.sif")
        weight_names.add("protenix")
    if normalized_model in {"esmfold2", "esmfold2_experimental"}:
        container_names.add("esmfold2.sif")
        weight_names.add("esmfold2")
    if normalized_model in {"protein_local_redesign", "protein_modification_experimental"}:
        container_names.update({"foundry.sif", "fampnn.sif"})
        validators = str(
            params.get("plr_structure_validators")
            or params.get("structure_validator")
            or params.get("pred_method")
            or "protenix_v2"
        ).lower()
        if "protenix" in validators:
            container_names.add("protenix.sif")
            weight_names.add("protenix")
        if "esmfold2" in validators:
            container_names.add("esmfold2.sif")
            weight_names.add("esmfold2")
    if normalized_model in {"fampnn", "fampnn_child"}:
        container_names.add("fampnn.sif")
    if normalized_model == "frustrampnn" or params.get("run_frustrampnn") is True:
        container_names.add("frustrampnn.sif")
    if normalized_model == "molecular_dynamics":
        if normalized_mode in {"simulate", "replica"}:
            container_names.update(
                {"md-preparation-v1.sif", "gromacs-md-2025.3.sif", "openmm-md-8.5.2.sif"}
            )
            extra_paths.add(data_root / "md-preparation" / "env-v1-explicit.txt")
        if normalized_mode in {"simulate", "analyze"}:
            container_names.add("md-analysis-1.0.0.sif")
    if normalized_model in {"boltz2", "boltz_cp_experimental"}:
        weight_names.add("boltz")
        if normalized_model == "boltz2":
            container_names.add("boltz2-v2.9.5-7ebf1be.sif")
        explicit_bcp_container = str(params.get("bcp_container_path") or "").strip()
        if explicit_bcp_container:
            extra_paths.add(Path(explicit_bcp_container).expanduser().resolve())
        explicit_bcp_repo = str(params.get("bcp_repo_path") or "").strip()
        if explicit_bcp_repo:
            extra_paths.add(Path(explicit_bcp_repo).expanduser().resolve())

    for key, value in params.items():
        normalized_key = str(key).lower()
        if not any(
            token in normalized_key
            for token in ("container_path", "_container", "checkpoint_path", "runtime_lock", "repo_path")
        ):
            continue
        if isinstance(value, str) and value.startswith("/"):
            candidate = Path(value).expanduser().resolve()
            if candidate.exists():
                extra_paths.add(candidate)

    assets: list[tuple[Path, str]] = []
    for name in sorted(container_names):
        assets.append((container_root / name, f"containers/{name}"))
    for name in sorted(weight_names):
        assets.append((weights_root / name, f"weights/{name}"))
    for path in sorted(extra_paths):
        if _under(path, container_root):
            relative = path.relative_to(container_root).as_posix()
            assets.append((path, f"containers/{relative}"))
        elif _under(path, weights_root):
            relative = path.relative_to(weights_root).as_posix()
            assets.append((path, f"weights/{relative}"))
        elif _under(path, data_root):
            relative = path.relative_to(data_root).as_posix()
            assets.append((path, f"data/{relative}"))
        else:
            raise RemoteBundleError(
                f"Runtime asset is outside BMS-managed storage and cannot be transferred: {path}"
            )

    deduped: dict[str, Path] = {}
    for path, relative in assets:
        if not path.exists():
            raise RemoteBundleError(f"Required runtime asset is unavailable: {path}")
        existing = deduped.get(relative)
        if existing is not None and existing != path:
            raise RemoteBundleError(f"Runtime package destination collision: {relative}")
        deduped[relative] = path
    return [(path, relative) for relative, path in sorted(deduped.items())]


def _input_assets(
    params: dict[str, Any],
    *,
    command: list[str],
    repo_root: Path,
    runtime_paths: set[Path],
    output_dir: Path,
) -> list[tuple[Path, str]]:
    selected: dict[Path, str] = {}
    data_root = get_data_root().resolve()
    runtime_roots = [path for path in runtime_paths if path.is_dir()]
    candidates = [*_flatten_strings(params), *(str(value) for value in command[1:])]
    for raw in candidates:
        if not raw.startswith("/"):
            continue
        candidate = Path(raw).expanduser()
        if candidate.is_symlink():
            raise RemoteBundleError(f"Input symlink is not allowed: {candidate}")
        path = candidate.resolve()
        if not path.exists() or path == output_dir.resolve() or _under(path, repo_root):
            continue
        if not _under(path, data_root):
            raise RemoteBundleError(
                f"Job input is outside BMS-managed storage and cannot be transferred: {path}"
            )
        if path in runtime_paths or any(_under(path, root) for root in runtime_roots):
            continue
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
        selected[path] = f"{digest}/{path.name}"
    return [(path, relative) for path, relative in sorted(selected.items(), key=lambda item: str(item[0]))]


def _rewrite(value: str, path_map: dict[str, str]) -> str:
    rewritten = value
    for local, remote in sorted(path_map.items(), key=lambda item: len(item[0]), reverse=True):
        rewritten = rewritten.replace(local, remote)
    return rewritten


def prepare_remote_bundle(
    *,
    job: Any,
    target: Any,
    command: list[str],
    environment: dict[str, str] | None = None,
    attempt_id: str | None = None,
) -> PreparedRemoteBundle:
    repo_root = get_code_root().resolve()
    data_root = get_data_root().resolve()
    container_root = get_container_dir().resolve()
    weights_root = get_weights_root().resolve()
    raw_output = Path(str(job.child_output_dir or job.output_dir)).expanduser()
    local_output = raw_output.resolve()
    if raw_output.is_symlink() or Path(os.path.abspath(str(raw_output))) != local_output:
        raise RemoteBundleError("Remote Job output path cannot traverse symlinks")
    reserved_output_roots = (
        container_root,
        weights_root,
        data_root / "runtime",
        data_root / "remote-execution",
    )
    if (
        not _under(local_output, data_root)
        or local_output == data_root
        or any(_under(local_output, reserved_root.resolve()) for reserved_root in reserved_output_roots)
    ):
        raise RemoteBundleError("Remote Job output must remain under BMS-managed storage")
    if local_output.exists():
        for output_entry in local_output.rglob("*"):
            if output_entry.is_symlink():
                raise RemoteBundleError(f"Remote Job output seed contains a symlink: {output_entry}")
    attempt_id = str(attempt_id or uuid.uuid4())
    try:
        parsed_attempt_id = uuid.UUID(attempt_id)
    except ValueError as exc:
        raise RemoteBundleError("Remote attempt identity is invalid") from exc
    if str(parsed_attempt_id) != attempt_id:
        raise RemoteBundleError("Remote attempt identity is invalid")
    remote_root = str(target.remote_root).rstrip("/")
    remote_attempt = f"{remote_root}/attempts/{attempt_id}"
    root_job_id = str(job.lineage_root_job_id or job.parent_job_id or job.id)
    safe_root_job_id = re.sub(r"[^A-Za-z0-9_.-]", "_", root_job_id)
    if not safe_root_job_id:
        raise RemoteBundleError("Job lineage root identity is invalid")

    staging_root = data_root / "remote-execution" / "staging" / attempt_id
    staging_root.mkdir(parents=True, exist_ok=False)
    source_root = staging_root / "source"
    archive_path = staging_root / "source.tar"
    revision = str(job.execution_source_revision or "").strip()
    inherited_tree = str(job.execution_source_tree or "").strip()
    if not _SOURCE_IDENTITY_RE.fullmatch(revision) or not _SOURCE_IDENTITY_RE.fullmatch(
        inherited_tree
    ):
        raise RemoteBundleError("Remote Job is missing a valid immutable source identity")
    current_revision, current_tree = current_source_identity(repo_root)
    if current_revision != revision or current_tree != inherited_tree:
        raise RemoteBundleError(
            "Job source identity no longer matches the code compiling this remote command"
        )
    tree = _git(repo_root, "rev-parse", f"{revision}^{{tree}}")
    if inherited_tree != tree:
        raise RemoteBundleError("Inherited source tree does not match the inherited revision")
    with archive_path.open("wb") as archive_handle:
        completed = subprocess.run(
            ["git", "archive", "--format=tar", revision],
            cwd=repo_root,
            check=True,
            stdout=archive_handle,
            stderr=subprocess.PIPE,
            timeout=300,
        )
        if completed.returncode != 0:
            raise RemoteBundleError("Unable to archive the committed BMS source")
    source_archive_sha256 = _sha256_file(archive_path)
    _safe_extract(archive_path, source_root)
    archive_copy = source_root / ".bms-source.tar"
    archive_path.replace(archive_copy)

    remote_source = f"{remote_root}/revisions/{tree}"
    runtime_assets = _runtime_assets(str(job.model_id), str(job.mode), dict(job.params or {}))
    runtime_paths = {path.resolve() for path, _ in runtime_assets}
    runtime_records: list[RemoteFileRecord] = []
    runtime_transfers: list[TransferPlan] = []
    runtime_path_map: dict[str, str] = {}
    remote_runtime = f"{remote_root}/lineages/{safe_root_job_id}/runtime"
    for path, relative in runtime_assets:
        runtime_records.extend(_records_for_source(path, f"runtime/{relative}", "runtime"))
        runtime_transfers.append(TransferPlan(path, f"{remote_runtime}/{relative}"))
        runtime_path_map[str(path.resolve())] = f"{remote_runtime}/{relative}"
    runtime_identity = hashlib.sha256(
        _canonical_bytes([record.model_dump(mode="json") for record in runtime_records])
    ).hexdigest()

    input_assets = _input_assets(
        dict(job.params or {}),
        command=command,
        repo_root=repo_root,
        runtime_paths=runtime_paths,
        output_dir=local_output,
    )
    input_records: list[RemoteFileRecord] = []
    input_transfers: list[TransferPlan] = []
    input_path_map: dict[str, str] = {}
    for path, relative in input_assets:
        prefix = f"inputs/{relative}"
        input_records.extend(_records_for_source(path, prefix, "input"))
        remote_destination = f"{remote_attempt}/bundle/{prefix}"
        input_transfers.append(TransferPlan(path, remote_destination))
        input_path_map[str(path.resolve())] = remote_destination

    source_records = _records_for_source(source_root, "source", "source")
    remote_results = f"{remote_attempt}/results"
    path_map: dict[str, str] = {
        **runtime_path_map,
        **input_path_map,
        str(repo_root): remote_source,
        str(container_root): f"{remote_runtime}/containers",
        str(weights_root): f"{remote_runtime}/weights",
        str(data_root): str(data_root),
    }
    nextflow_executable = str(command[0]) if command else ""
    translated_command = [_rewrite(str(value), path_map) for value in command]
    if translated_command and Path(nextflow_executable).name == "nextflow":
        translated_command[0] = f"{remote_root}/runner/nextflow"
    elif translated_command and Path(nextflow_executable).name in {"python", "python3"}:
        translated_command[0] = str(
            data_root / "runtime" / "cm-api-python" / "current" / "venv" / "bin" / "python"
        )

    result_contract = resolve_job_result_contract(job)
    assignment = (
        dict(job.provenance.get("remote_execution_assignment") or {})
        if isinstance(job.provenance, dict)
        else {}
    )
    assigned_gpu_indices = assignment.get("gpu_indices")
    if not isinstance(assigned_gpu_indices, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in assigned_gpu_indices
    ):
        assigned_gpu_indices = [] if job.assigned_gpu is None else [int(job.assigned_gpu)]
    effective_environment = {
        "BMS_HOME": remote_source,
        "BMS_DATA": str(data_root),
        "BMS_WEIGHTS": str(data_root / "weights"),
        "BMS_CONTAINER_DIR": str(data_root / "apptainer"),
        "BMS_API_PYTHON": str(
            data_root / "runtime" / "cm-api-python" / "current" / "venv" / "bin" / "python"
        ),
        "BMS_WORK": f"{remote_attempt}/work",
        "NXF_CACHE_DIR": f"{remote_attempt}/.nextflow",
        "NXF_HOME": f"{remote_root}/cache/nextflow",
        "NXF_APPTAINER_CACHEDIR": f"{remote_attempt}/apptainer-cache",
        "NXF_ANSI_LOG": "false",
        "CUDA_VISIBLE_DEVICES": ",".join(str(value) for value in assigned_gpu_indices),
    }
    api_url = os.getenv("BMS_REMOTE_API_BASE_URL", "").strip()
    parsed_api_url = urlsplit(api_url)
    if (
        parsed_api_url.scheme not in {"http", "https"}
        or not parsed_api_url.hostname
        or parsed_api_url.username
        or parsed_api_url.password
        or parsed_api_url.query
        or parsed_api_url.fragment
    ):
        raise RemoteBundleError("BMS_REMOTE_API_BASE_URL is not configured as a credential-free HTTP(S) URL")
    api_host = parsed_api_url.hostname.lower()
    if api_host == "localhost" or api_host.endswith(".localhost"):
        raise RemoteBundleError("BMS_REMOTE_API_BASE_URL must be reachable from the remote worker")
    try:
        api_address = ipaddress.ip_address(api_host)
    except ValueError:
        api_address = None
    if api_address is not None and (
        api_address.is_loopback or api_address.is_link_local or api_address.is_unspecified
    ):
        raise RemoteBundleError("BMS_REMOTE_API_BASE_URL must be reachable from the remote worker")
    effective_environment["API_BASE_URL"] = api_url.rstrip("/")
    for key, value in dict(environment or {}).items():
        if key in {
            "PYTORCH_CUDA_ALLOC_CONF",
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
        }:
            effective_environment[key] = str(value)

    records = [*source_records, *runtime_records, *input_records]
    envelope = RemoteExecutionEnvelope(
        job_id=str(job.id),
        root_job_id=root_job_id,
        parent_job_id=str(job.parent_job_id) if job.parent_job_id else None,
        attempt_id=attempt_id,
        execution_target_id=str(target.id),
        source_revision=revision,
        source_tree=tree,
        source_archive_sha256=source_archive_sha256,
        command=translated_command,
        working_directory=remote_source,
        environment=effective_environment,
        output_directory=remote_results,
        expected_result_contract=result_contract,
        path_map=path_map,
        files=records,
        created_at=datetime.now(timezone.utc),
    )
    envelope_payload = envelope.model_dump(mode="json", by_alias=True)
    envelope_bytes = _canonical_bytes(envelope_payload)
    envelope_sha256 = hashlib.sha256(envelope_bytes).hexdigest()
    local_attempt = staging_root / "attempt"
    local_attempt.mkdir()
    (local_attempt / "execution-envelope.json").write_bytes(envelope_bytes)
    return PreparedRemoteBundle(
        attempt_id=attempt_id,
        local_attempt_dir=local_attempt,
        remote_attempt_dir=remote_attempt,
        remote_source_dir=remote_source,
        remote_runtime_dir=remote_runtime,
        remote_output_alias=str(local_output),
        local_output_dir=local_output,
        envelope=envelope,
        envelope_sha256=envelope_sha256,
        runtime_identity_sha256=runtime_identity,
        source_transfer=TransferPlan(source_root, remote_source),
        runtime_transfers=tuple(runtime_transfers),
        input_transfers=tuple(input_transfers),
    )
