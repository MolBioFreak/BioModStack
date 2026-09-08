"""Compile immutable workflow-neutral packages for remote execution."""
from __future__ import annotations

import hashlib

import json
import os
import re
import shutil
import subprocess
import tarfile
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal

from paths import get_code_root, get_container_dir, get_data_root, get_weights_root, get_inputs_dir, get_results_dir
from services.result_contracts import resolve_result_contract

from .contracts import RemoteExecutionEnvelope, RemoteFileRecord


class RemoteBundleError(RuntimeError):
    pass


_SOURCE_IDENTITY_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class TransferPlan:
    source: Path
    remote_destination: str
    origin: Path | None = None


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
    runtime_images: tuple[CacheTransferArtifact, ...] = ()


@dataclass(frozen=True, slots=True)
class CacheTransferArtifact:
    source: Path
    remote_destination: str
    sha256: str
    size_bytes: int
    mode: int
    role: str
    aliases: tuple[str, ...] = ()


def uncached_runtime_transfers(bundle: PreparedRemoteBundle) -> tuple[TransferPlan, ...]:
    """Only destination-dependent support-python bypasses the shared cache."""
    return tuple(transfer for transfer in bundle.runtime_transfers
                 if transfer.remote_destination == bundle.remote_runtime_dir.rstrip('/') + '/support-python')


def cache_transfer_artifacts(bundle: PreparedRemoteBundle) -> tuple[CacheTransferArtifact, ...]:
    """Project the existing authoritative envelope, never a second model registry.

    Relocated support-python contains destination-dependent bytes and symlinks;
    it deliberately stays on the verified legacy transport path. Regular source,
    workflow and model files share the ordinary byte-addressed cache protocol.
    Shared SIF identities/aliases come from the authenticated manifest and use the
    worker's shared-image store. FrustraMPNN alone retains ordinary authenticated
    materialization for its exact-path, no-follow consumer.
    """
    result: list[CacheTransferArtifact] = []
    for record in bundle.envelope.files:
        relative = PurePosixPath(record.relative_path)
        if (record.link_target is not None or relative.parts[0] not in {"source", "runtime"}
                or record.role != relative.parts[0]):
            continue
        if relative.parts[:2] == ("runtime", "support-python"):
            continue
        if relative.parts[0] == "source":
            # The archive carries every workflow/source leaf without thousands of SSH calls.
            if record.relative_path != "source/.bms-source.tar":
                continue
            leaf = PurePosixPath(*relative.parts[1:])
            source = bundle.source_transfer.source.joinpath(*leaf.parts)
            destination = f"{bundle.remote_source_dir}/{leaf}"
        else:
            destination = f"{bundle.remote_runtime_dir}/{'/'.join(relative.parts[1:])}"
            matches = [transfer for transfer in bundle.runtime_transfers
                       if destination == transfer.remote_destination
                       or destination.startswith(transfer.remote_destination.rstrip('/') + '/')]
            if len(matches) != 1:
                raise RemoteBundleError("Cache artifact has ambiguous transport authority")
            transfer = matches[0]
            suffix = destination[len(transfer.remote_destination):].lstrip('/')
            source = transfer.source / suffix if suffix else transfer.source
        result.append(CacheTransferArtifact(
            source=source, remote_destination=destination, sha256=record.sha256,
            size_bytes=record.size_bytes, mode=record.mode, role=relative.parts[0],
        ))
    return tuple(result) + tuple(getattr(bundle, "runtime_images", ()))


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
    if path.stat().st_mode & 0o7000:
        raise RemoteBundleError(f"Package file has an unapproved special mode: {path}")
    return RemoteFileRecord(
        relative_path=relative_path,
        size_bytes=path.stat().st_size,
        sha256=_sha256_file(path),
        role=role,
        mode=path.stat().st_mode & 0o777,
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


def _is_runtime_image(path: Path, relative: str) -> bool:
    """Only SIF runtime leaves use immutable references, never inputs/weights trees."""
    return relative.lower().endswith(".sif") and path.is_file()


def _legacy_runtime_image(relative: str) -> bool:
    # FrustraMPNN requires its registered semantic path to be a no-follow regular
    # file. Keep this one legacy materialization until that consumer is migrated;
    # it retains an ordinary cache object plus a per-attempt copy, not deduped SIFs.
    return relative.removeprefix("runtime/") == "containers/frustrampnn.sif"


def _runtime_assets(model_id: str, mode: str, params: dict[str, Any]) -> list[tuple[Path, str]]:
    container_root = get_container_dir().resolve()
    weights_root = get_weights_root().resolve()
    data_root = get_data_root().resolve()
    normalized_model = str(model_id or "").strip().lower()
    normalized_mode = str(mode or "").strip().lower()
    container_names: set[str] = set()
    weight_names: set[str] = set()
    extra_paths: set[Path] = set()
    api_runtime = Path(os.getenv("BMS_CM_API_RUNTIME_DIR", str(data_root / "runtime" / "cm-api-python"))) / "current"
    if not api_runtime.exists():
        raise RemoteBundleError(f"Managed workflow Python runtime is unavailable: {api_runtime}")
    extra_paths.add(api_runtime.resolve())

    from model_registry import INDEPENDENT_RUNTIME_MODELS, model_runtime_dependencies
    if normalized_model in INDEPENDENT_RUNTIME_MODELS:
        for ref in model_runtime_dependencies(normalized_model):
            (container_names if ref.kind == "image" else weight_names).add(ref.relative_path)
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
    if normalized_model == "fampnn_child":
        container_names.add("fampnn.sif")
    if params.get("run_frustrampnn") is True:
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
            for token in ("container_path", "_container", "runtime_sif", "checkpoint_path", "runtime_lock", "repo_path")
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
        if path == api_runtime.resolve():
            assets.append((path, "support-python"))
        elif _under(path, container_root):
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
        if _is_runtime_image(path, relative) and not any(
                _under(path, root) for root in (container_root, weights_root, data_root)):
            raise RemoteBundleError(f"Runtime image alias escapes managed storage: {path}")
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
    input_roots = (get_data_root().resolve(), get_inputs_dir().resolve(), get_results_dir().resolve())
    runtime_roots = [path for path in runtime_paths if path.is_dir()]
    system_roots = {get_weights_root().resolve(), get_container_dir().resolve()}
    # Store destinations are worker-owned output locations, not input assets.
    if "--runtime_image_store" in command:
        store_index = command.index("--runtime_image_store") + 1
        if store_index >= len(command):
            raise RemoteBundleError("runtime image store destination is missing")
        system_roots.add(Path(command[store_index]).resolve())
    candidates = [*_flatten_strings(params), *(str(value) for value in command[1:])]
    for raw in candidates:
        if not raw.startswith("/"):
            continue
        candidate = Path(raw).expanduser()
        path = candidate.resolve()
        if path in system_roots or path in runtime_paths or any(_under(path, root) for root in runtime_roots):
            continue
        if any(part.is_symlink() for part in (candidate, *candidate.parents)):
            raise RemoteBundleError(f"Input path traverses a symlink: {candidate}")
        if path == output_dir.resolve() or _under(path, repo_root):
            continue
        if not path.exists():
            raise RemoteBundleError(f"Declared input is unavailable: {candidate}")
        if not any(_under(path, root) for root in input_roots):
            raise RemoteBundleError(
                f"Job input is outside BMS-managed storage and cannot be transferred: {path}"
            )
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
        selected[path] = f"{digest}/{path.name}"
    return [(path, relative) for path, relative in sorted(selected.items(), key=lambda item: str(item[0]))]


def _rewrite(value: str, path_map: dict[str, str]) -> str:
    candidate = PurePosixPath(value)
    if not candidate.is_absolute() or ".." in candidate.parts:
        return value
    for local, remote in sorted(path_map.items(), key=lambda item: len(item[0]), reverse=True):
        try:
            relative = candidate.relative_to(PurePosixPath(local))
        except ValueError:
            continue
        return str(PurePosixPath(remote) / relative)
    return value


def compile_remote_dependencies(model_id: str, mode: str, command: list[str]) -> tuple[list[str], dict[str, Any]]:
    """Compile the normalized launch argv, never the original persisted defaults.

    This boundary runs only for remote packages; local command/model policy is
    unchanged. The returned argv is the sole authority for runtime and input
    selection below.
    """
    # These current workflows still require controller-side child orchestration.
    # Reject rather than silently dropping required stages or falling back locally.
    callback_workflows = {
        'conformational_mapping.nf', 'protein_design.nf', 'boltz_cp_experimental.nf',
        'antibody_denovo.nf', 'protein_local_redesign.nf', 'ppiflow_generator_design.nf',
    }
    selected_workflows = {Path(value).name for value in command if value.endswith('.nf')}
    blocked = selected_workflows & callback_workflows
    if blocked:
        raise RemoteBundleError(
            'Remote workflow closure is not implemented for ' + ', '.join(sorted(blocked))
            + '; controller callbacks/child scheduling are still required. '
            'No local fallback or partial scientific execution was performed.'
        )
    params: dict[str, Any] = {}
    for index, value in enumerate(command):
        if value.startswith("--"):
            raw = command[index + 1] if index + 1 < len(command) and not command[index + 1].startswith("--") else True
            params[value[2:]] = {"true": True, "false": False}.get(raw, raw) if isinstance(raw, str) else raw
    from services.ont_ngs_contract import CANONICAL_ONT_WORKFLOWS, resolve_ont_workflow_alias
    if resolve_ont_workflow_alias(model_id) in CANONICAL_ONT_WORKFLOWS:
        # Dorado preflight rejects symlink runtime_sif. Pin the supported typed
        # selector explicitly rather than letting Nextflow choose our name alias.
        if not params.get("dorado_runtime_sif"):
            selected = os.getenv("BMS_NGS_RUNTIME_SIF") or str(get_container_dir() / "dorado.sif")
            command = [*command, "--dorado_runtime_sif", selected]
            params["dorado_runtime_sif"] = selected
        selected = params["dorado_runtime_sif"]
        if not isinstance(selected, str) or not Path(selected).is_absolute():
            raise RemoteBundleError("Dorado runtime SIF selector must be an absolute managed path")
    omitted: set[str] = set()
    if model_id.lower() == "protenix":
        omitted.update({"rfd_models", "af2_models", "boltz_models", "alphafold_params"})
        omitted.update(key for key in params if key.startswith(("bcp_", "esmf_", "plr_", "md_", "rfantibody_")))
        omitted.update(key for key in params
                       if any(token in key for token in ("container_path", "_container", "runtime_sif", "checkpoint_path", "runtime_lock", "repo_path"))
                       and not key.startswith(("protenix_", "frustrampnn_")))
        backend = str(params.get("protenix_msa_backend", "auto")).lower()
        provider = str(params.get("msa_provider", "")).strip().lower()
        if backend in {"", "auto"} and provider in {"local", "colabfold_api"}:
            backend = provider
            params["protenix_msa_backend"] = backend
            command = list(command)
            if "--protenix_msa_backend" in command:
                for index, value in enumerate(command[:-1]):
                    if value == "--protenix_msa_backend":
                        command[index + 1] = backend
            else:
                command.extend(["--protenix_msa_backend", backend])
        if backend == "colabfold_api" or str(params.get("protenix_use_msa", "true")).lower() == "false":
            omitted.add("msa_local_db")
    compiled: list[str] = []
    index = 0
    while index < len(command):
        value = command[index]
        if value.startswith("--") and value[2:] in omitted:
            index += 1
            if index < len(command) and not command[index].startswith("--"):
                index += 1
            continue
        compiled.append(value)
        index += 1
    return compiled, {key: value for key, value in params.items() if key not in omitted}


def _input_command(command: list[str]) -> list[str]:
    """Exclude typed output/cache/system-root options before input admission."""
    generated = {"-w", "--work_dir", "--out_dir", "--out", "--data_root", "--code_root",
                 "--weights_root", "--container_dir", "--msa_cache_dir", "--cm_api_runtime_dir"}
    return [value for index, value in enumerate(command)
            if value not in generated and (index == 0 or command[index - 1] not in generated)]


def _relocate_python_runtime(source: Path, destination: Path, remote_destination: str) -> Path:
    """Materialize a self-contained release and hash its final-path bytes later."""
    source = source.resolve()
    shutil.copytree(source, destination, symlinks=True,
                    ignore=lambda directory, names: [".venv"] if Path(directory) == source / "venv" and ".venv" in names else [])
    mapping = {str(source): remote_destination}
    # Absolute internal links become relative so validation also works in staging.
    for original in sorted(source.rglob("*")):
        relative = original.relative_to(source)
        copied = destination / relative
        if relative == Path("venv/.venv"):
            continue
        if original.is_symlink():
            resolved = original.resolve()
            if not resolved.is_relative_to(source) or not resolved.exists():
                raise RemoteBundleError(f"Runtime symlink escapes or is missing: {original}")
            copied.unlink()
            copied.symlink_to(os.path.relpath(destination / resolved.relative_to(source), copied.parent),
                              target_is_directory=resolved.is_dir())
    for config in destination.rglob("pyvenv.cfg"):
        lines = []
        for line in config.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() in {"home", "executable"}:
                base_path = Path(value.strip()).resolve()
                translated = _rewrite(str(base_path), mapping)
                if not base_path.is_relative_to(source) or not base_path.exists():
                    raise RemoteBundleError("Python base runtime is not contained in its managed release")
                line = f"{key.strip()} = {translated}"
            lines.append(line)
        config.write_text("\n".join(lines) + "\n")
    # Managed venv console scripts may still name the provisioning checkout.
    for script in (destination / "venv" / "bin").iterdir():
        if script.is_symlink() or not script.is_file():
            continue
        with script.open("rb") as handle:
            first = handle.readline(4096)
        if first.startswith(b"#!") and b"python" in first:
            payload = script.read_bytes()
            script.write_bytes(f"#!{remote_destination}/venv/bin/python\n".encode() + payload[len(first):])
    return destination


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
        not any(_under(local_output, root) and local_output != root for root in (data_root, get_results_dir().resolve()))
        or any(_under(local_output, reserved_root.resolve()) for reserved_root in reserved_output_roots)
    ):
        raise RemoteBundleError("Remote Job output must remain under BMS-managed storage")
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

    # Byte-addressed cache objects are shared; runnable trees never are.
    remote_source = f"{remote_attempt}/materialized/source"
    command, effective_params = compile_remote_dependencies(str(job.model_id), str(job.mode), command)
    # Preparation is controller-owned and happens before command compilation in
    # nextflow.launch_nextflow_job, identically for local and remote execution.
    # Never start a provider search while materializing a remote worker bundle.
    if (str(job.model_id).lower() == 'boltz2'
            and str(effective_params.get('boltz_use_msa', False)).lower() == 'true'
            and not effective_params.get('msa_path')
            and not effective_params.get('complex_json_path')):
        raise RemoteBundleError('Controller-prepared or supplied Boltz MSA inputs are required before remote bundling')
    if str(job.model_id).lower() == 'protenix':
        if effective_params.get('pred_method', 'protenix') != 'protenix':
            raise RemoteBundleError('A mixed model invocation requires separate model-native MSA preparation')
        search = (str(effective_params.get('protenix_use_msa', True)).lower() != 'false'
                  and effective_params.get('protenix_msa_backend') not in {'none', 'esm'})
        if search and not effective_params.get('protenix_prepared_msa_dir'):
            raise RemoteBundleError('Controller-prepared Protenix MSA inputs are required before remote bundling')
    runtime_assets = _runtime_assets(str(job.model_id), str(job.mode), effective_params)
    runtime_paths = {path.resolve() for path, _ in runtime_assets}
    runtime_records: list[RemoteFileRecord] = []
    runtime_transfers: list[TransferPlan] = []
    runtime_path_map: dict[str, str] = {}
    images: dict[str, CacheTransferArtifact] = {}
    manifest_destination = ""
    manifest_sha256 = ""
    remote_runtime = f"{remote_attempt}/materialized/runtime"
    for path, relative in runtime_assets:
        destination = f"{remote_runtime}/{relative}"
        source = path
        if _is_runtime_image(path, relative) and _legacy_runtime_image(relative):
            # Materialize bytes, not a controller alias, for the strict consumer.
            source = path.resolve()
        elif _is_runtime_image(path, relative):
            # Preserve semantic aliases, but transfer/publish only one object per digest.
            source = path.resolve()
            record = _record_file(source, f"runtime/{relative}", "runtime")
            shared = f"{remote_root}/cache/runtime-images/objects/sha256/{record.sha256}/runtime.sif"
            previous = images.get(record.sha256)
            aliases = tuple(sorted(set((previous.aliases if previous else ()) + (destination,))))
            images[record.sha256] = CacheTransferArtifact(
                previous.source if previous else source, shared, record.sha256,
                record.size_bytes, 0o400, "image", aliases)
            runtime_path_map[str(path)] = shared
            runtime_path_map[str(source)] = shared
            continue
        if relative == "support-python":
            source = _relocate_python_runtime(path, staging_root / "support-python", destination)
            lexical_runtime = Path(os.getenv("BMS_CM_API_RUNTIME_DIR", str(data_root / "runtime" / "cm-api-python")))
            runtime_path_map[str(lexical_runtime / "current")] = destination
        runtime_records.extend(_records_for_source(source, f"runtime/{relative}", "runtime"))
        runtime_transfers.append(TransferPlan(source, destination, origin=path))
        runtime_path_map[str(path.resolve())] = destination
    if images:
        manifest = staging_root / ".bms-runtime-images.json"
        manifest.write_bytes(_canonical_bytes({
            "schema": "bms.runtime-image-references.v1",
            "runtime_root": remote_runtime,
            "images": [{"sha256": image.sha256, "size_bytes": image.size_bytes,
                        "aliases": list(image.aliases)} for image in sorted(images.values(), key=lambda x: x.sha256)],
        }))
        manifest_destination = f"{remote_runtime}/{manifest.name}"
        manifest_record = _record_file(manifest, f"runtime/{manifest.name}", "runtime")
        manifest_sha256 = manifest_record.sha256
        runtime_records.append(manifest_record)
        runtime_transfers.append(TransferPlan(manifest, manifest_destination, origin=manifest))
    runtime_identity = hashlib.sha256(
        _canonical_bytes([record.model_dump(mode="json") for record in runtime_records])
    ).hexdigest()

    input_assets = _input_assets(
        {},
        command=_input_command(command),
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
        str(data_root): f"{remote_attempt}/data",
        str(local_output): remote_results,
    }
    for flag, destination in {"-w": f"{remote_attempt}/work", "--work_dir": f"{remote_attempt}/work",
                              "--msa_cache_dir": f"{remote_attempt}/msa-cache",
                              "--cm_api_runtime_dir": f"{remote_runtime}/support-python",
                              "--runtime_image_store": f"{remote_root}/cache/runtime-images"}.items():
        if flag in command:
            path_map[command[command.index(flag) + 1]] = destination
    nextflow_executable = str(command[0]) if command else ""
    translated_command = [_rewrite(str(value), path_map) for value in command]
    if translated_command and Path(nextflow_executable).name == "nextflow":
        translated_command[0] = f"{remote_root}/runner/nextflow"
    elif translated_command and Path(nextflow_executable).name in {"python", "python3"}:
        translated_command[0] = f"{remote_runtime}/support-python/venv/bin/python"

    if images:
        # The normal worker authenticates this small manifest as a regular bundle file.
        # This stdlib-only boundary verifies immutable objects and all semantic aliases
        # before exec; no SIF or external symlink is smuggled through file records.
        translated_command = ["python3", f"{remote_source}/platform/api/tools/bms_artifact_cache.py",
                              "--root", f"{remote_root}/cache/artifacts/v1",
                              "--execute-runtime", manifest_destination,
                              "--manifest-sha256", manifest_sha256, "--", *translated_command]

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
        "BMS_DATA": f"{remote_attempt}/data",
        "BMS_WEIGHTS": f"{remote_runtime}/weights",
        "BMS_CONTAINER_DIR": f"{remote_runtime}/containers",
        "BMS_RUNTIME_IMAGE_STORE": f"{remote_root}/cache/runtime-images",
        "BMS_CM_API_RUNTIME_DIR": f"{remote_runtime}/support-python",
        "BMS_API_PYTHON": f"{remote_runtime}/support-python/venv/bin/python",
        "BMS_MSA_CACHE": f"{remote_attempt}/msa-cache",
        "BMS_REMOTE_EXECUTION": "1",
        "BMS_REMOTE_ATTEMPT_ID": attempt_id,
        "BMS_REMOTE_JOB_ID": str(job.id),
        "BMS_REMOTE_OUTPUT_ROOT": remote_results,
        "BMS_WORK": f"{remote_attempt}/work",
        "NXF_CACHE_DIR": f"{remote_attempt}/.nextflow",
        "NXF_HOME": f"{remote_root}/cache/nextflow",
        "NXF_APPTAINER_CACHEDIR": f"{remote_attempt}/apptainer-cache",
        "NXF_ANSI_LOG": "false",
        "CUDA_VISIBLE_DEVICES": ",".join(str(value) for value in assigned_gpu_indices),
    }

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
        remote_output_alias=remote_results,
        local_output_dir=local_output,
        envelope=envelope,
        envelope_sha256=envelope_sha256,
        runtime_identity_sha256=runtime_identity,
        source_transfer=TransferPlan(source_root, remote_source),
        runtime_transfers=tuple(runtime_transfers),
        input_transfers=tuple(input_transfers),
        runtime_images=tuple(sorted(images.values(), key=lambda image: image.sha256)),
    )
