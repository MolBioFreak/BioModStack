"""Compile immutable workflow-neutral packages for remote execution."""
from __future__ import annotations

import hashlib

import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal
from component_runtime import NativeInvocation, SelectedExecutionPlan, SourceIdentity

from paths import get_code_root, get_container_dir, get_data_root, get_weights_root, get_inputs_dir, get_results_dir
from services.result_contracts import resolve_result_contract

from .contracts import RemoteExecutionEnvelope, RemoteFileRecord
from .images import IMAGE_SELECTORS, resolve_image


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


def bind_resource_admission(bundle: PreparedRemoteBundle, admission: dict, *,
                            resource_monitor: dict | None = None) -> PreparedRemoteBundle:
    """Seal admission into existing context/envelope bytes before their staging CAS."""
    from dataclasses import replace
    resources = json.loads(bundle.envelope.environment['BMS_TARGET_RESOURCES'])
    if (admission.get('execution_target_id') != bundle.envelope.execution_target_id
            or admission.get('required') != resources['required']
            or admission.get('minimum_gpu_memory_mb', 0) < resources.get('minimum_gpu_memory_mb', 0)):
        raise RemoteBundleError('Target resource admission differs from the selected plan')
    resources.update(admission=admission, admission_required=False)
    records = list(bundle.envelope.files)
    for transfer in bundle.input_transfers:
        if transfer.source.name == 'component-context.json':
            context = json.loads(transfer.source.read_text())
            context['resources'].update(resources)
            transfer.source.write_bytes(_canonical_bytes(context))
            records = [_record_file(transfer.source, row.relative_path, row.role)
                       if row.relative_path == 'inputs/component-context.json' else row for row in records]
    envelope = bundle.envelope.model_copy(update={'files': records, 'resource_monitor': resource_monitor,
        'environment': {**bundle.envelope.environment, 'BMS_TARGET_RESOURCES': json.dumps(resources, sort_keys=True)}})
    payload = _canonical_bytes(envelope.model_dump(mode='json', by_alias=True))
    (bundle.local_attempt_dir / 'execution-envelope.json').write_bytes(payload)
    return replace(bundle, envelope=envelope, envelope_sha256=hashlib.sha256(payload).hexdigest())


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
    worker's shared-image store. Scientific readers pin canonical regular objects;
    semantic aliases exist only for compatible callers.
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
    if role == "input" and path.stat().st_nlink != 1:
        raise RemoteBundleError(f"Package input must be an unaliased regular file: {path}")
    if path.stat().st_mode & 0o7000:
        raise RemoteBundleError(f"Package file has an unapproved special mode: {path}")
    if role == 'runtime' and path.name == 'runtime.sif' and path.parent.parent.name == 'sha256':
        # The resolver already established release approval; do not let a later
        # read silently turn changed bytes into a new transport identity.
        from .images import verify_image
        digest = verify_image(path, path.parent.name)['sha256']
    else:
        digest = _sha256_file(path)
    return RemoteFileRecord(
        relative_path=relative_path,
        size_bytes=path.stat().st_size,
        sha256=digest,
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


def verify_selected_runtime_hashes(plan, observed_hashes):
    """Match selected metadata digests to the final existing byte inventory."""
    prefixes = {'image': 'containers', 'weights': 'weights', 'database': 'data',
                'reference_database': 'data', 'runtime_data': 'data'}
    for dependency in plan.dependencies:
        release = dependency.semantic_release
        if not isinstance(release, str) or not release.startswith('sha256:'):
            continue
        digest = release.removeprefix('sha256:')
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise RemoteBundleError('Invalid selected runtime metadata identity: ' + dependency.logical_id)
        if dependency.kind not in prefixes or not dependency.relative_path:
            raise RemoteBundleError('Selected runtime metadata has no artifact binding: ' + dependency.logical_id)
        name = prefixes[dependency.kind] + '/' + dependency.relative_path
        if observed_hashes.get(name) != digest:
            raise RemoteBundleError('Selected runtime metadata changed or is missing: ' + name)


def verify_selected_preparation_inputs(plan, observed=None):
    """Bind native preparation decisions to their declared immutable input bytes.

    Bundle callers supply their existing final input inventory. Read-only
    preview/local admission checks hash only the explicitly declared inputs.
    """
    expected = {}
    for component in (*plan.metadata.static_components, *plan.metadata.dynamic_templates):
        selection = json.loads(component.selection_json)
        native = selection.get('native_preparation_metadata') or {}
        for record in (native.get('config_identity', {}).get('input_files') or {}).values():
            path = record['path']
            identity = (record['sha256'], record['size_bytes'])
            if path in expected and expected[path] != identity:
                raise RemoteBundleError('Conflicting native preparation input identities')
            expected[path] = identity
    if not expected:
        return
    if observed is None:
        from scripts.lib.portable_inputs import _contained, MAX_DOCUMENT_BYTES
        import stat
        roots = [get_inputs_dir(), get_results_dir(), get_data_root(), get_code_root()]
        observed = {}
        for original in expected:
            path = _contained(original, [root.resolve() for root in roots])
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_DOCUMENT_BYTES:
                    raise RemoteBundleError('Native preparation input is not a bounded regular file')
                data = stream.read(MAX_DOCUMENT_BYTES + 1)
                after = os.fstat(stream.fileno())
            if len(data) > MAX_DOCUMENT_BYTES or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise RemoteBundleError('Native preparation input changed during verification')
            observed[original] = (hashlib.sha256(data).hexdigest(), len(data))
    for path, identity in expected.items():
        if observed.get(path) != identity:
            raise RemoteBundleError('Native preparation input changed or is missing: ' + path)


def _runtime_assets(model_id: str, mode: str, params: dict[str, Any], *,
                    include_support=False, native_invocation: NativeInvocation | None = None,
                    selected_plan: SelectedExecutionPlan | None = None,
                    only_kinds: frozenset[str] | None = None) -> list[tuple[Path, str]]:
    """Materialize the shared selected dependency projection, never reselect science.

    The native compiler owns conditional stage selection. This boundary only
    binds its declared logical references to installation-owned filesystem paths;
    the existing cache/manifest owner pins and verifies their immutable bytes.
    """
    if native_invocation is not None:
        if (not isinstance(native_invocation, NativeInvocation)
                or native_invocation.model_id != model_id or native_invocation.mode != mode
                or native_invocation.execution_plan is None):
            raise RemoteBundleError('Runtime selection requires the matching selected native invocation')
        if selected_plan is not None and selected_plan != native_invocation.execution_plan:
            raise RemoteBundleError('Runtime selection has conflicting selected plans')
        selected_plan = native_invocation.execution_plan
    if (not isinstance(selected_plan, SelectedExecutionPlan)
            or selected_plan.model_id != model_id or selected_plan.mode != mode):
        raise RemoteBundleError('Runtime selection requires the matching selected execution plan')
    plan = selected_plan
    blocking = [item for item in plan.blockers
                if item.field == 'dependency_closure' and 'provision' in item.blocks]
    if not plan.dependency_closure_complete or blocking:
        raise RemoteBundleError('Selected dependency closure is incomplete: ' + '; '.join(
            item.component_or_dependency_id + ': ' + item.reason for item in blocking))
    roots = {'image': get_container_dir().resolve(), 'weights': get_weights_root().resolve(),
             'database': get_data_root().resolve(), 'reference_database': get_data_root().resolve(),
             'runtime_data': get_data_root().resolve()}
    prefixes = {'image': 'containers', 'weights': 'weights', 'database': 'data',
                'reference_database': 'data', 'runtime_data': 'data'}
    source_root = get_code_root().resolve()
    assets = {}
    for dependency in plan.dependencies:
        if only_kinds is not None and dependency.kind not in only_kinds:
            continue
        if dependency.kind in {'source_release', 'critical_runtime'}:
            continue  # Existing source archive/attached critical generation own these.
        if dependency.kind == 'support_tool':
            if dependency.relative_path is None:
                raise RemoteBundleError('Selected source dependency has no contained reference')
            source = source_root / dependency.relative_path
            if not source.resolve().is_relative_to(source_root) or not source.is_file():
                raise RemoteBundleError('Required source dependency is unavailable: ' + dependency.logical_id)
            continue
        if dependency.kind == 'support_python':
            if include_support:
                runtime = Path(os.getenv('BMS_CM_API_RUNTIME_DIR',
                    str(get_data_root() / 'runtime/cm-api-python'))) / 'current'
                assets['support-python'] = runtime.resolve()
            continue
        if dependency.kind not in roots:
            raise RemoteBundleError('Unsupported declared runtime role: ' + dependency.kind)
        root = roots[dependency.kind]
        relative = dependency.relative_path
        selected = params.get(dependency.selector) if dependency.selector else None
        if dependency.kind == 'image' and relative:
            path = (resolve_image(relative, root, params) if relative in IMAGE_SELECTORS or not selected
                    else Path(str(selected)).expanduser())
        else:
            path = Path(str(selected)).expanduser() if selected else root / relative if relative else None
        if path is None or not path.is_absolute():
            raise RemoteBundleError('Selected runtime dependency has no managed binding: ' + dependency.logical_id)
        if dependency.kind == 'image' and (path.is_symlink() or any(parent.is_symlink() for parent in path.parents)):
            raise RemoteBundleError(f'Selected runtime image is not a no-follow path: {path}')
        path = path.resolve()
        allowed = (get_container_dir().resolve(), get_weights_root().resolve(), get_data_root().resolve(),
            Path(os.environ.get('BMS_RUNTIME_IMAGE_STORE') or get_container_dir() / '.image-store').resolve())
        if not any(_under(path, allowed_root) for allowed_root in allowed):
            raise RemoteBundleError('Selected runtime dependency escapes managed storage: ' + dependency.logical_id)
        if not path.exists():
            raise RemoteBundleError('Required runtime asset is unavailable: ' + dependency.logical_id)
        logical = relative or (path.relative_to(root).as_posix() if _under(path, root) else
            'selected/' + dependency.logical_id.replace(':', '-') + '/' + path.name)
        if PurePosixPath(logical).is_absolute() or '..' in PurePosixPath(logical).parts:
            raise RemoteBundleError('Selected runtime dependency has an invalid logical reference')
        destination = prefixes[dependency.kind] + '/' + logical
        if destination in assets and assets[destination] != path:
            raise RemoteBundleError('Selected runtime dependency destination collision: ' + destination)
        assets[destination] = path
    return [(path, relative) for relative, path in sorted(assets.items())]


def _input_assets(
    params: dict[str, Any],
    *,
    native_invocation: NativeInvocation,
    repo_root: Path,
    runtime_paths: set[Path],
    output_dir: Path,
    references: list[dict[str, Any]] | None = None,
    runtime_references: dict[str, dict[str, Any]] | None = None,
) -> list[tuple[Path, str]]:
    selected: dict[Path, str] = {}
    input_roots = (get_data_root().resolve(), get_inputs_dir().resolve(), get_results_dir().resolve())
    runtime_roots = [path for path in runtime_paths if path.is_dir()]
    system_roots = {get_weights_root().resolve(), get_container_dir().resolve()}
    # Store destinations are worker-owned output locations, not input assets.
    if params.get("runtime_image_store"):
        system_roots.add(Path(params["runtime_image_store"]).resolve())
    destinations = {"work_dir", "out_dir", "out", "data_root", "code_root",
                    "weights_root", "container_dir", "msa_cache_dir", "cm_api_runtime_dir",
                    "runtime_image_store"}
    runtime_fields = {"laproteina_checkpoint_dir", "laproteina_data_path",
                      "disco_checkpoint_path", "disco_cutlass_path"}
    for key in runtime_fields:
        value = params.get(key)
        if not value:
            continue
        if params.get("backend") and not key.startswith(str(params["backend"]) + "_"):
            continue
        if str(Path(value).resolve()) not in (runtime_references or {}):
            raise RemoteBundleError(f"Native runtime field has no selected dependency binding: {key}")
    candidates = _flatten_strings({key: value for key, value in params.items()
                                   if key not in destinations and key not in runtime_fields})
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
    for generated in native_invocation.generated_inputs:
        candidate = output_dir / generated.relative_path
        if any(part.is_symlink() for part in (candidate, *candidate.parents)):
            raise RemoteBundleError(f"Generated input path traverses a symlink: {candidate}")
        path = candidate.resolve()
        if not _under(path, output_dir) or path == output_dir.resolve():
            raise RemoteBundleError("Generated input escapes its output binding")
        if not path.is_file():
            raise RemoteBundleError(f"Declared generated input is unavailable: {candidate}")
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
        selected[path] = f"{digest}/{path.name}"
    import sys
    import yaml
    scripts_root = str(Path(__file__).resolve().parents[4] / "scripts")
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    from lib.portable_inputs import discover_native_input_references
    document_owners = {}
    prepared = params.get("bcp_input_path")
    if prepared and params.get("boltz_prepared_msa_sha256"):
        prepared_root = Path(prepared)
        manifest_path = prepared_root / "msa-inputs.json"
        if _sha256_file(manifest_path) != params["boltz_prepared_msa_sha256"]:
            raise RemoteBundleError("Prepared Fold-CP manifest identity changed")
        manifest = json.loads(manifest_path.read_bytes())
        if manifest.get("schema") == "bms.boltz-cp-msa-inputs.v1":
            requested = json.loads(native_invocation.requested_json)
            original = Path(requested.get("bcp_input_path") or requested.get("input_path") or "")
            if not original.is_absolute() or not any(_under(original, root) for root in input_roots):
                raise RemoteBundleError("Prepared Fold-CP configs have no trusted native source owner")
            for config in manifest["configs"]:
                relative = PurePosixPath(config["path"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise RemoteBundleError("Prepared Fold-CP config escapes its root")
                source = original / str(relative) if original.is_dir() else original
                if any(part.is_symlink() for part in (source, *source.parents)):
                    raise RemoteBundleError("Fold-CP source owner traverses a symlink")
                if _sha256_file(source) != config["source_sha256"]:
                    raise RemoteBundleError("Prepared Fold-CP source owner identity changed")
                document_owners[str(prepared_root / str(relative))] = str(source)
    try:
        discovered = discover_native_input_references(
            native_invocation.model_id, native_invocation.mode, params,
            native_invocation.generated_inputs, output_dir=output_dir,
            allowed_roots=input_roots, yaml_loader=yaml.safe_load,
            runtime_references=runtime_references, document_owners=document_owners)
    except (OSError, ValueError) as exc:
        raise RemoteBundleError(f"Native input closure is invalid: {exc}") from exc
    if references is not None:
        references.extend(discovered)
    for reference in discovered:
        if reference["role"] == "runtime":
            continue
        path = Path(reference["source_path"])
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
        selected.setdefault(path, f"{digest}/{path.name}")
    # CM owns sibling request/plan/registry and relative registered assets, not
    # a synthetic job-output alias. Preserve that layout below a trusted root.
    cm_roots = {Path(ref["source_path"]).parent for ref in discovered if ref["format"] == "cm-request"}
    trusted = get_results_dir().resolve()
    for root in cm_roots:
        if root == trusted or not _under(root, trusted):
            raise RemoteBundleError("Canonical request must be below the trusted results root")
        for path in selected:
            if _under(path, root):
                selected[path] = "trusted-results/" + path.relative_to(trusted).as_posix()
    # A selected input directory already owns its contained generated files.
    # Do not transfer/hash the same bytes again as standalone child inputs.
    selected = {path: relative for path, relative in selected.items()
                if not any(parent in selected for parent in path.parents)}
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


def compile_remote_dependencies(
    model_id: str, mode: str, command: list[str], *, native_invocation: NativeInvocation,
) -> tuple[list[str], dict[str, Any]]:
    """Project sealed native parameters into a separate placement copy."""
    if not isinstance(native_invocation, NativeInvocation):
        raise RemoteBundleError("Remote compilation requires a NativeInvocation")
    if (model_id != native_invocation.model_id or mode != native_invocation.mode
            or tuple(command) != native_invocation.command):
        raise RemoteBundleError("Remote command/model/mode does not match its native invocation")
    command = list(command)
    # Native admission owns supported modes. Remote placement consumes the same
    # complete selected plan; filenames are not a separate callback allowlist.
    plan = native_invocation.execution_plan
    if (not isinstance(plan, SelectedExecutionPlan)
            or plan.model_id != model_id or plan.mode != mode
            or plan.entrypoint != native_invocation.entrypoint or not plan.complete):
        raise RemoteBundleError(
            'Remote execution requires its complete selected native execution plan; '
            'no local fallback or partial scientific execution was performed.'
        )
    params = native_invocation.native_parameters
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
        if backend in {"colabfold_api", "neurosnap_api"} or str(params.get("protenix_use_msa", "true")).lower() == "false":
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
    params = {key: value for key, value in params.items() if key not in omitted}
    # Resolve before inventory AND argv translation. This also covers saved-job
    # prewarm, whose argv is rebuilt by the ordinary Job command compiler.
    names = {name for name, (flag, _) in IMAGE_SELECTORS.items() if flag in params}
    if model_id.lower() == 'protenix':
        names.add('protenix.sif')
    if model_id.lower() == 'frustrampnn' or params.get('run_frustrampnn') is True:
        names.add('frustrampnn.sif')
    for name in sorted(names):
        flag, selector = IMAGE_SELECTORS[name]
        if name == 'frustrampnn.sif' and flag not in params and not os.environ.get(selector):
            # The default is inventoried/verified below; no override to compile.
            continue
        try:
            selected = str(resolve_image(name, get_container_dir().resolve(), params))
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            raise RemoteBundleError(f"Selected runtime image is unavailable: {name}") from exc
        if (flag not in params and selected == str(get_container_dir().resolve() / name)):
            # Legacy default has no typed override. Inventory still requires it;
            # the authenticated worker environment selects its published object.
            continue
        if '--' + flag in compiled:
            for index, value in enumerate(compiled[:-1]):
                if value == '--' + flag:
                    compiled[index + 1] = selected
        else:
            compiled.extend(['--' + flag, selected])
        params[flag] = selected
    return compiled, params


def _input_records(path: Path, prefix: str, *, native_invocation: NativeInvocation,
                   output_dir: Path, generated_by_path=None) -> list[RemoteFileRecord]:
    """Check compiler bytes using the hashes already required for transfer."""
    if generated_by_path is None:
        generated_by_path = {bound: item.reference for item in native_invocation.generated_inputs
                             if (bound := (output_dir / item.relative_path).resolve()) == path
                             or path in bound.parents}
    unmatched = set(generated_by_path)
    records = _records_for_source(path, prefix, "input")
    for record in records:
        suffix = record.relative_path[len(prefix):].lstrip('/')
        bound = path / suffix if suffix else path
        reference = generated_by_path.get(bound)
        unmatched.discard(bound)
        if reference is not None and (record.sha256 != reference["sha256"]
                or record.size_bytes != reference["size_bytes"]):
            raise RemoteBundleError("Compiler-generated input identity changed during recording")
    if unmatched:
        raise RemoteBundleError("Compiler-generated input disappeared during recording")
    return records


def _write_portable_bindings(*, staging_root: Path, remote_attempt: str,
                            references: list[dict[str, Any]], input_transfers: list[TransferPlan],
                            input_records: list[RemoteFileRecord], remote_runtime: str,
                            remote_results: str) -> tuple[TransferPlan, RemoteFileRecord]:
    """Seal placement separately, using the already-recorded transfer identities."""
    files = {}
    directories = {}
    directory_references = {}
    record_by_path = {record.relative_path: record for record in input_records}
    for transfer in input_transfers:
        prefix = transfer.remote_destination.removeprefix(remote_attempt + "/bundle/")
        directory = transfer.source.is_dir()
        if directory:
            source_key = str(transfer.source)
            directories[source_key] = transfer.remote_destination
            members = [record.model_dump(mode="json") for name, record in record_by_path.items()
                       if name.startswith(prefix + "/")]
            directory_references[source_key] = dict(
                logical_id="native-directory:" + hashlib.sha256(source_key.encode()).hexdigest(),
                source_path=source_key, role="input", format="directory-manifest",
                sha256=hashlib.sha256(_canonical_bytes(members)).hexdigest(),
                size_bytes=sum(member["size_bytes"] for member in members),
                owner="native-invocation", lineage=[], selector=[])
        for name, record in record_by_path.items():
            if name != prefix and not name.startswith(prefix + "/"):
                continue
            suffix = name[len(prefix):].lstrip("/")
            source = transfer.source / suffix if suffix else transfer.source
            destination = transfer.remote_destination + ("/" + suffix if suffix else "")
            files[str(source)] = (destination, record)
    bindings = []
    covered = set()
    for reference in references:
        source = reference["source_path"]
        if reference["role"] == "runtime":
            if reference["format"] == "runtime-directory":
                directories[source] = reference["path"]
                directory_references[source] = reference
            else:
                bindings.append({"reference": reference, "path": reference["path"]})
            continue
        if source not in files:
            raise RemoteBundleError("Native input reference is absent from transferred closure")
        destination, record = files[source]
        if (reference["sha256"], reference["size_bytes"]) != (record.sha256, record.size_bytes):
            raise RemoteBundleError("Native input changed between discovery and transfer recording")
        bindings.append({"reference": reference, "path": destination})
        covered.add(source)
        if reference["format"] == "boltz-authority":
            authority = json.loads(Path(source).read_bytes())
            result_root = authority.get("result_root")
            if not isinstance(result_root, str) or not Path(result_root).is_absolute():
                raise RemoteBundleError("Boltz authority requires an absolute result root identity")
            directories[result_root] = remote_results
    for source, (destination, record) in files.items():
        if source in covered:
            continue
        reference = dict(logical_id="native-input:" + hashlib.sha256(source.encode()).hexdigest(),
                         source_path=source, sha256=record.sha256, size_bytes=record.size_bytes,
                         role="input", format=Path(source).suffix.lstrip(".") or "binary",
                         owner="native-invocation", lineage=[], selector=[])
        bindings.append({"reference": reference, "path": destination})
    payload = {
        "schema": "bms.portable-input-bindings.v1",
        "roots": [remote_attempt + "/bundle/inputs", remote_runtime, remote_results,
                  remote_attempt + "/work", remote_attempt + "/msa-cache"],
        "results_root": remote_attempt + "/bundle/inputs/trusted-results",
        "bindings": bindings,
        "directories": [{"source_path": source, "path": target,
                         **({"reference": directory_references[source]} if source in directory_references else {})}
                        for source, target in sorted(directories.items())],
    }
    path = staging_root / "portable-input-bindings.json"
    path.write_bytes(_canonical_bytes(payload))
    relative = "inputs/.bms/portable-input-bindings.json"
    return (TransferPlan(path, remote_attempt + "/bundle/" + relative),
            _record_file(path, relative, "input"))


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
    # A shell/Python prologue supports long or spaced remote interpreter paths.
    launcher = ("#!/bin/sh\n'''exec' " + shlex.quote(remote_destination + '/venv/bin/python')
                + " \"$0\" \"$@\"\n' '''\n").encode()
    for script in (destination / "venv" / "bin").iterdir():
        if script.is_symlink() or not script.is_file():
            continue
        with script.open("rb") as handle:
            first = handle.readline(4096)
        if first.startswith(b"#!") and b"python" in first:
            payload = script.read_bytes()
            script.write_bytes(launcher + payload[len(first):])
    return destination


def prepare_remote_bundle(
    *,
    job: Any,
    target: Any,
    command: list[str],
    native_invocation: NativeInvocation,
    environment: dict[str, str] | None = None,
    attempt_id: str | None = None,
) -> PreparedRemoteBundle:
    if not isinstance(native_invocation, NativeInvocation):
        raise RemoteBundleError("Remote bundling requires a NativeInvocation")
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
    if native_invocation.source_identity != SourceIdentity(revision, inherited_tree):
        raise RemoteBundleError("Native invocation source identity does not match the Remote Job")
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
    command, effective_params = compile_remote_dependencies(
        str(job.model_id), str(job.mode), command, native_invocation=native_invocation)
    # Preparation is controller-owned and happens before command compilation in
    # nextflow.launch_nextflow_job, identically for local and remote execution.
    # Never start a provider search while materializing a remote worker bundle.
    if (str(job.model_id).lower() == 'boltz2'
            and str(effective_params.get('boltz_use_msa', False)).lower() == 'true'
            and not effective_params.get('msa_path')
            and not effective_params.get('boltz_prepared_msa_dir')
            and not effective_params.get('complex_json_path')):
        raise RemoteBundleError('Controller-prepared or supplied Boltz MSA inputs are required before remote bundling')
    if str(job.model_id).lower() == 'protenix':
        if effective_params.get('pred_method', 'protenix') != 'protenix':
            raise RemoteBundleError('A mixed model invocation requires separate model-native MSA preparation')
        search = (str(effective_params.get('protenix_use_msa', True)).lower() != 'false'
                  and effective_params.get('protenix_msa_backend') not in {'none', 'esm'})
        if search and not effective_params.get('protenix_prepared_msa_dir'):
            raise RemoteBundleError('Controller-prepared Protenix MSA inputs are required before remote bundling')
    binding = (getattr(target, "capabilities", None) or {}).get("critical_runtime_binding")
    runtime_assets = _runtime_assets(str(job.model_id), str(job.mode), effective_params,
                                    include_support=not bool(binding), native_invocation=native_invocation)
    runtime_paths = {path.resolve() for path, _ in runtime_assets}
    runtime_references: dict[str, dict[str, Any]] = {}
    runtime_records: list[RemoteFileRecord] = []
    runtime_hashes: dict[str, str] = {}
    runtime_transfers: list[TransferPlan] = []
    runtime_path_map: dict[str, str] = {}
    images: dict[str, CacheTransferArtifact] = {}
    manifest_destination = ""
    manifest_sha256 = ""
    remote_runtime = f"{remote_attempt}/materialized/runtime"
    support_root = (str(PurePosixPath(binding["paths"]["python"]).parents[2])
                    if binding else f"{remote_runtime}/support-python")
    if binding:
        host_support = Path(os.getenv("BMS_CM_API_RUNTIME_DIR", str(data_root / "runtime/cm-api-python"))) / "current"
        runtime_path_map[str(host_support)] = support_root
        runtime_path_map[str(host_support.resolve())] = support_root
        runtime_paths.add(host_support.resolve())
        if effective_params.get("api_python"):
            runtime_paths.add(Path(effective_params["api_python"]).resolve())
    for path, relative in runtime_assets:
        destination = f"{remote_runtime}/{relative}"
        source = path
        if _is_runtime_image(path, relative):
            # Preserve semantic aliases, but transfer/publish only one object per digest.
            source = path.resolve()
            record = _record_file(source, f"runtime/{relative}", "runtime")
            runtime_hashes[relative] = record.sha256
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
        recorded = _records_for_source(source, f"runtime/{relative}", "runtime")
        runtime_records.extend(recorded)
        runtime_hashes.update({record.relative_path.removeprefix("runtime/"): record.sha256
                               for record in recorded if record.link_target is None})
        # Reuse this exact inventory for typed runtime fields; no second tree scan.
        for record in recorded:
            suffix = record.relative_path[len(f"runtime/{relative}"):].lstrip("/")
            original = path / suffix if suffix else path
            if record.link_target is None:
                runtime_references[str(original)] = dict(
                    sha256=record.sha256, size_bytes=record.size_bytes,
                    format=original.suffix.lstrip(".") or "binary",
                    path=destination + ("/" + suffix if suffix else ""))
        if path.is_dir():
            directory_members: dict[Path, list[RemoteFileRecord]] = {path: []}
            for record in recorded:
                suffix = record.relative_path[len(f"runtime/{relative}"):].lstrip("/")
                for parent in (path / suffix).parents:
                    if not _under(parent, path):
                        break
                    directory_members.setdefault(parent, []).append(record)
            for directory, members in directory_members.items():
                suffix = directory.relative_to(path).as_posix()
                runtime_references[str(directory)] = dict(
                    sha256=hashlib.sha256(_canonical_bytes([r.model_dump(mode="json") for r in members])).hexdigest(),
                    size_bytes=sum(r.size_bytes for r in members), format="runtime-directory",
                    path=destination if suffix == "." else destination + "/" + suffix)
        runtime_transfers.append(TransferPlan(source, destination, origin=path))
        runtime_path_map[str(path.resolve())] = destination
    verify_selected_runtime_hashes(native_invocation.execution_plan, runtime_hashes)
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
    runtime_payload = [record.model_dump(mode="json") for record in runtime_records]
    runtime_identity = hashlib.sha256(_canonical_bytes(
        {"files": runtime_payload, "critical_runtime": binding} if binding else runtime_payload
    )).hexdigest()

    if job.model_id == "nanopore":
        from services.ont_submission_trust import verify_launch_input_snapshots
        verify_launch_input_snapshots(dict(job.params or {}))
    native_references: list[dict[str, Any]] = []
    input_discovery_params = effective_params
    custody = effective_params.get('ont_input_provenance') or {}
    if job.model_id == 'nanopore' and custody.get('source') == 'managed_fastq_launch_snapshot':
        # The immutable launch snapshot is the input. Historical import location
        # is provenance, not a second live acquisition/transfer of the FASTQ.
        input_discovery_params = dict(effective_params, ont_input_provenance={
            key: value for key, value in custody.items() if key != 'submitted_path'})
    input_assets = _input_assets(
        input_discovery_params,
        native_invocation=native_invocation,
        repo_root=repo_root,
        runtime_paths=runtime_paths,
        output_dir=local_output,
        references=native_references, runtime_references=runtime_references,
    )
    input_records: list[RemoteFileRecord] = []
    input_transfers: list[TransferPlan] = []
    input_path_map: dict[str, str] = {}
    input_hashes: dict[str, tuple[str, int]] = {}
    generated_by_source = {path: {} for path, _ in input_assets}
    for item in native_invocation.generated_inputs:
        bound = (local_output / item.relative_path).resolve()
        owner = next((path for path in (bound, *bound.parents)
                      if path in generated_by_source), None)
        if owner is None:
            raise RemoteBundleError("Compiler-generated input is absent from the input projection")
        generated_by_source[owner][bound] = item.reference
    for path, relative in input_assets:
        prefix = f"inputs/{relative}"
        recorded = _input_records(path, prefix, native_invocation=native_invocation,
                                  output_dir=local_output, generated_by_path=generated_by_source[path])
        input_records.extend(recorded)
        for record in recorded:
            suffix = record.relative_path[len(prefix):].lstrip("/")
            original = path / suffix if suffix else path
            if record.link_target is None:
                input_hashes[str(original.resolve())] = (record.sha256, record.size_bytes)
        remote_destination = f"{remote_attempt}/bundle/{prefix}"
        input_transfers.append(TransferPlan(path, remote_destination))
        input_path_map[str(path.resolve())] = remote_destination

    verify_selected_preparation_inputs(native_invocation.execution_plan, input_hashes)
    source_records = _records_for_source(source_root, "source", "source")
    remote_results = f"{remote_attempt}/results"
    bindings_transfer, bindings_record = _write_portable_bindings(
        staging_root=staging_root, remote_attempt=remote_attempt,
        references=native_references, input_transfers=input_transfers,
        input_records=input_records, remote_runtime=remote_runtime,
        remote_results=remote_results)
    input_transfers.append(bindings_transfer)
    input_records.append(bindings_record)
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
                              "--cm_api_runtime_dir": support_root,
                              "--runtime_image_store": f"{remote_root}/cache/runtime-images"}.items():
        if flag in command:
            path_map[command[command.index(flag) + 1]] = destination
    nextflow_executable = str(command[0]) if command else ""
    translated_command = [_rewrite(str(value), path_map) for value in command]
    if translated_command and Path(nextflow_executable).name == "nextflow":
        translated_command[0] = binding["paths"]["nextflow"] if binding else f"{remote_root}/runner/nextflow"
    elif translated_command and Path(nextflow_executable).name in {"python", "python3"}:
        translated_command[0] = f"{support_root}/venv/bin/python"

    if images:
        # The normal worker authenticates this small manifest as a regular bundle file.
        # This stdlib-only boundary verifies immutable objects and all semantic aliases
        # before exec; no SIF or external symlink is smuggled through file records.
        translated_command = [binding["paths"]["python"] if binding else "python3",
                              f"{remote_source}/platform/api/tools/bms_artifact_cache.py",
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
    from .targets import selected_plan_target_resources
    resources = selected_plan_target_resources(target, native_invocation.execution_plan,
        gpu_ids=assigned_gpu_indices,
        # Cached source/runtime/images are installation capacity, not new
        # per-job scratch. Their lifecycle/provisioning owner handles misses.
        scratch_bytes=sum(row.size_bytes for row in input_records if row.link_target is None))
    import sys
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from scripts.lib.component_adapter import native_resource_config
    resource_file = staging_root / 'component-resources.config'
    resource_destination = f'{remote_attempt}/bundle/inputs/{resource_file.name}'
    resource_lock = f'{remote_attempt}/compute.lock'
    resource_file.write_text(native_resource_config(native_invocation.execution_plan.to_dict(),
        resources, resource_lock, repo_root))
    input_records.append(_record_file(resource_file, f'inputs/{resource_file.name}', 'input'))
    input_transfers.append(TransferPlan(resource_file, resource_destination))
    translated_command = [*translated_command, '-c', resource_destination]
    effective_environment = {
        "BMS_TARGET_RESOURCES": json.dumps(resources, sort_keys=True),
        "BMS_HOME": remote_source,
        "BMS_DATA": f"{remote_attempt}/data",
        "BMS_WEIGHTS": f"{remote_runtime}/weights",
        "BMS_CONTAINER_DIR": f"{remote_runtime}/containers",
        "BMS_RUNTIME_IMAGE_STORE": f"{remote_root}/cache/runtime-images",
        "BMS_CM_API_RUNTIME_DIR": support_root,
        "BMS_API_PYTHON": f"{support_root}/venv/bin/python",
        "BMS_MSA_CACHE": f"{remote_attempt}/msa-cache",
        "BMS_REMOTE_EXECUTION": "1",
        "BMS_REMOTE_ATTEMPT_ID": attempt_id,
        "BMS_REMOTE_JOB_ID": str(job.id),
        "BMS_REMOTE_OUTPUT_ROOT": remote_results,
        "BMS_PORTABLE_INPUT_BINDINGS": bindings_transfer.remote_destination,
        "APPTAINERENV_BMS_PORTABLE_INPUT_BINDINGS": bindings_transfer.remote_destination,
        "BMS_WORK": f"{remote_attempt}/work",
        "NXF_CACHE_DIR": f"{remote_attempt}/.nextflow",
        "NXF_HOME": f"{remote_root}/cache/nextflow",
        "NXF_APPTAINER_CACHEDIR": f"{remote_attempt}/apptainer-cache",
        "NXF_ANSI_LOG": "false",
        "CUDA_VISIBLE_DEVICES": ",".join(str(value) for value in assigned_gpu_indices),
    }

    if binding:
        effective_environment.update(binding["environment"])

    for name, (_, selector) in IMAGE_SELECTORS.items():
        alias = f"{remote_runtime}/containers/{name}"
        for image in images.values():
            if alias in image.aliases:
                effective_environment[selector] = image.remote_destination

    for key, value in dict(environment or {}).items():
        if key in {
            "PYTORCH_CUDA_ALLOC_CONF",
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
        }:
            effective_environment[key] = str(value)

    from services.nextflow import component_launch_context, needs_component_runtime
    if needs_component_runtime(native_invocation):
        context_file = staging_root / 'component-context.json'
        context_destination = f'{remote_attempt}/bundle/inputs/{context_file.name}'
        context = component_launch_context(native_invocation, job,
            command=translated_command, context_path=context_destination,
            artifact_root=remote_results, working_directory=remote_source,
            attempt_id=attempt_id, target_id=target.id,
            lease_id=assignment['lease_id'],
            resources={**resources, 'gpu_id': job.assigned_gpu})
        context['resource_lock_path'] = resource_lock
        context.update(ledger_path=f'{remote_attempt}/component-runtime.sqlite',
                       root_state_path=f'{remote_attempt}/component-state.json',
                       child_work_root=f'{remote_attempt}/component-work')
        context_file.write_bytes(_canonical_bytes(context))
        input_records.append(_record_file(context_file, f'inputs/{context_file.name}', 'input'))
        input_transfers.append(TransferPlan(context_file, context_destination))
        effective_environment['BMS_COMPONENT_CONTEXT'] = context_destination
        effective_environment['APPTAINERENV_BMS_COMPONENT_CONTEXT'] = context_destination
        translated_command = [binding['paths']['python'] if binding else f'{support_root}/venv/bin/python',
                              f'{remote_source}/scripts/lib/component_adapter.py',
                              '--context', context_destination]

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
