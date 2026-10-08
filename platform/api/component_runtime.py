"""Placement-neutral component grouping and durable expansion contracts.

This is not a scheduler: Nextflow/host adapters still own execution and process
termination. In particular cancel_requested is intent, never proof of quiescence.
No database server, scientific dependencies, or host callbacks are needed here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import uuid
import os
import tempfile
import time
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Any, Callable, Mapping, Sequence, TypeVar

T = TypeVar("T")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class SourceIdentity:
    """Committed scientific source identity shared by placement projections."""
    revision: str
    tree: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or len(value) != 40
               or any(char not in '0123456789abcdef' for char in value)
               for value in (self.revision, self.tree)):
            raise ValueError('committed BMS source identity is invalid')

    @classmethod
    def from_checkout(cls, root: Path) -> SourceIdentity:
        import subprocess
        def git(*args):
            return subprocess.run(['git', *args], cwd=Path(root).resolve(),
                check=True, capture_output=True, text=True, timeout=60).stdout.strip()
        if git('status', '--porcelain', '--untracked-files=no'):
            raise ValueError('execution requires a clean tracked source checkout')
        revision = git('rev-parse', 'HEAD')
        return cls(revision, git('rev-parse', revision + '^{tree}'))


def durable_write(path: Path, payload: bytes) -> None:
    """Atomic publication with durable file and containing-directory metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass(frozen=True)
class ArtifactReference:
    """Placement-neutral identity; storage roots are supplied separately.

    Native adapters supply format/schema and semantic ownership. A verified
    byte reference does not assert that the native scientific result is valid.
    """
    logical_id: str
    owner_kind: str
    owner_id: str
    role: str
    relative_path: str
    sha256: str
    size_bytes: int
    format_schema: str
    job_id: str | None = None
    component_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.logical_id, self.owner_id, self.format_schema):
            if type(value) is not str or not value or '\x00' in value:
                raise ValueError('artifact identity and format/schema must be nonempty text')
        if self.owner_kind not in {'request', 'job', 'component', 'release'}:
            raise ValueError('artifact owner kind is not supported')
        if self.role not in {'source', 'input', 'runtime', 'result', 'log', 'receipt', 'review'}:
            raise ValueError('artifact role is not supported')
        for value in (self.job_id, self.component_id):
            if value is not None and (type(value) is not str or not value or '\x00' in value):
                raise ValueError('artifact lineage identifiers must be nonempty text')
        if self.owner_kind == 'component' and self.component_id != self.owner_id:
            raise ValueError('component artifact owner and lineage disagree')
        if self.owner_kind == 'job' and self.job_id != self.owner_id:
            raise ValueError('job artifact owner and lineage disagree')
        if type(self.relative_path) is not str or '\x00' in self.relative_path:
            raise ValueError('artifact path must be text without NUL')
        path = PurePosixPath(self.relative_path)
        if (not self.relative_path or path.is_absolute() or '..' in path.parts
                or '\\' in self.relative_path or path.as_posix() != self.relative_path
                or self.relative_path == '.'):
            raise ValueError('artifact reference requires a contained relative path')
        if (type(self.sha256) is not str or len(self.sha256) != 64
                or any(c not in '0123456789abcdef' for c in self.sha256)
                or type(self.size_bytes) is not int or self.size_bytes < 0):
            raise ValueError('artifact reference requires a SHA256 and nonnegative byte size')

    @property
    def payload(self) -> dict[str, Any]:
        return {'schema': 'bms.artifact-reference.v1', **asdict(self)}

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> ArtifactReference:
        document = dict(value)
        if document.pop('schema', None) != 'bms.artifact-reference.v1':
            raise ValueError('unsupported artifact reference schema')
        return cls(**document)

    def resolve(self, root: Path) -> Path:
        """Verify regular-file bytes through a no-follow directory binding.

        The caller must retain its attempt/publication ownership fence while
        consuming the returned path. Verification does not acquire that fence.
        """
        from stat import S_ISREG
        root = Path(root).absolute()
        if '..' in root.parts:
            raise ValueError('artifact root must not contain parent traversal')
        directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = (*root.parts[1:], *PurePosixPath(self.relative_path).parts[:-1])
            for part in parts:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(PurePosixPath(self.relative_path).name,
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as handle:
                before = os.fstat(handle.fileno())
                if not S_ISREG(before.st_mode) or before.st_size != self.size_bytes:
                    raise ValueError('artifact reference is not a regular file of the bound size')
                checksum = hashlib.sha256()
                size = 0
                while block := handle.read(1024 * 1024):
                    size += len(block)
                    if size > self.size_bytes:
                        raise ValueError('artifact grew during verification')
                    checksum.update(block)
                after = os.fstat(handle.fileno())
                if (size != self.size_bytes or checksum.hexdigest() != self.sha256
                        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                    raise ValueError('artifact reference byte binding mismatch')
        finally:
            os.close(directory)
        return root / self.relative_path


@dataclass(frozen=True)
class GeneratedInput:
    """Compiler-produced bytes, materialized only by the execution owner."""
    relative_path: str
    payload: bytes

    def __post_init__(self) -> None:
        if type(self.relative_path) is not str or '\x00' in self.relative_path:
            raise ValueError('generated input path must be text without NUL')
        path = PurePosixPath(self.relative_path)
        if (not self.relative_path or path.is_absolute() or '..' in path.parts
                or '\\' in self.relative_path or path.as_posix() != self.relative_path
                or self.relative_path == '.' or type(self.payload) is not bytes):
            raise ValueError('generated input requires a contained path and immutable bytes')

    @property
    def reference(self) -> dict[str, Any]:
        return {'relative_path': self.relative_path, 'size_bytes': len(self.payload),
                'sha256': hashlib.sha256(self.payload).hexdigest(), 'role': 'input'}

    def bind(self, *, owner_kind: str, owner_id: str,
             format_schema: str, job_id: str | None = None,
             component_id: str | None = None) -> ArtifactReference:
        return ArtifactReference(logical_id=self.relative_path,
            owner_kind=owner_kind, owner_id=owner_id, job_id=job_id,
            component_id=component_id, role='input', relative_path=self.relative_path,
            sha256=hashlib.sha256(self.payload).hexdigest(), size_bytes=len(self.payload),
            format_schema=format_schema)

    def materialize(self, root: Path) -> None:
        from secrets import token_hex
        from stat import S_ISLNK

        root = Path(root).absolute()
        if '..' in root.parts:
            raise ValueError('generated input root must not contain parent traversal')
        # Bind every directory with O_NOFOLLOW. A prior is_symlink() check
        # followed by a pathname write would permit a concurrent substitution.
        directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = (*root.parts[1:], *PurePosixPath(self.relative_path).parts[:-1])
            for part in parts:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=directory)
                    os.fsync(directory)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
                os.close(directory)
                directory = child
            leaf = PurePosixPath(self.relative_path).name
            try:
                if S_ISLNK(os.stat(leaf, dir_fd=directory, follow_symlinks=False).st_mode):
                    raise ValueError('generated input target cannot be a symlink')
            except FileNotFoundError:
                pass
            temporary = '.bms-input-' + token_hex(16)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(self.payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, leaf, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
        finally:
            os.close(directory)


@dataclass(frozen=True)
class ModelContractReference:
    """Commitment to an existing registry definition, not a second schema."""
    model_id: str
    version: str
    definition_sha256: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value or '\x00' in value
               for value in (self.model_id, self.version, self.definition_sha256)):
            raise ValueError('model contract reference requires nonempty text')
        if not re.fullmatch(r'[0-9a-f]{64}', self.definition_sha256):
            raise ValueError('model contract definition digest is invalid')


@dataclass(frozen=True)
class NativeInvocation:
    """Immutable output of the existing scientific command compiler.

    This is the native-invocation projection of a plan, not a replacement
    scientific compiler or a claim of complete workflow dependency coverage.
    JSON snapshots prevent later request/path mutation from changing authority.
    """
    model_id: str
    mode: str
    command: tuple[str, ...]
    requested_json: bytes
    effective_json: bytes
    native_parameters_json: bytes
    generated_inputs: tuple[GeneratedInput, ...] = ()
    source_identity: SourceIdentity | None = None
    entrypoint: str | None = None
    model_contracts: tuple[ModelContractReference, ...] = ()
    execution_policy_json: bytes = b'{}'

    def __post_init__(self) -> None:
        if (type(self.model_id) is not str or not self.model_id
                or type(self.mode) is not str or not self.mode
                or type(self.command) is not tuple or not self.command):
            raise ValueError('native invocation requires model, mode and command')
        if any(not isinstance(value, str) or '\x00' in value for value in self.command):
            raise ValueError('native invocation command must contain text arguments')
        if self.source_identity is not None and not isinstance(self.source_identity, SourceIdentity):
            raise ValueError('native invocation source identity must be typed')
        if self.entrypoint is not None:
            if type(self.entrypoint) is not str or '\x00' in self.entrypoint:
                raise ValueError('native workflow entrypoint must be text')
            entrypoint = PurePosixPath(self.entrypoint)
            if (entrypoint.is_absolute() or '..' in entrypoint.parts
                    or '\\' in self.entrypoint or entrypoint.as_posix() != self.entrypoint
                    or entrypoint.suffix != '.nf'):
                raise ValueError('native workflow entrypoint must be a contained Nextflow source path')
        if (type(self.model_contracts) is not tuple
                or any(not isinstance(item, ModelContractReference) for item in self.model_contracts)):
            raise ValueError('model contract references must be immutable and typed')
        if len({item.model_id for item in self.model_contracts}) != len(self.model_contracts):
            raise ValueError('model contract references contain duplicate model identities')
        if type(self.generated_inputs) is not tuple or any(not isinstance(item, GeneratedInput) for item in self.generated_inputs):
            raise ValueError('generated input roster must be immutable and typed')
        paths = [item.relative_path for item in self.generated_inputs]
        if len(paths) != len(set(paths)):
            raise ValueError('generated input paths must be unique')
        path_set = set(paths)
        if any(parent.as_posix() in path_set for path in paths
               for parent in PurePosixPath(path).parents if parent.as_posix() != '.'):
            raise ValueError('generated input files cannot also be parent directories')
        for payload in (self.requested_json, self.effective_json, self.native_parameters_json,
                        self.execution_policy_json):
            if type(payload) is not bytes:
                raise ValueError('native invocation snapshots must be immutable bytes')
            value = json.loads(payload)
            if not isinstance(value, dict) or canonical_bytes(value) != payload:
                raise ValueError('native invocation settings must be canonical objects')

    @classmethod
    def capture(cls, *, model_id: str, mode: str, command: Sequence[str],
                requested: Mapping[str, Any], effective: Mapping[str, Any],
                native_parameters: Mapping[str, Any],
                entrypoint: str,
                model_contracts: Sequence[ModelContractReference] = (),
                generated_inputs: Sequence[GeneratedInput] = ()) -> NativeInvocation:
        return cls(model_id, mode, tuple(command), canonical_bytes(dict(requested)),
                   canonical_bytes(dict(effective)), canonical_bytes(dict(native_parameters)),
                   generated_inputs=tuple(generated_inputs), entrypoint=entrypoint,
                   model_contracts=tuple(model_contracts))

    @property
    def native_parameters(self) -> dict[str, Any]:
        return json.loads(self.native_parameters_json)

    @property
    def payload(self) -> dict[str, Any]:
        return {
            'schema_name': 'bms.native-invocation.v2', 'schema_version': 2,
            'model_id': self.model_id, 'mode': self.mode,
            'source_identity': asdict(self.source_identity) if self.source_identity is not None else None,
            'entrypoint': self.entrypoint,
            'model_contracts': [asdict(item) for item in self.model_contracts],
            'execution_policy': json.loads(self.execution_policy_json),
            'execution_policy_sha256': hashlib.sha256(self.execution_policy_json).hexdigest(),
            'command': list(self.command),
            'requested': json.loads(self.requested_json),
            'requested_sha256': hashlib.sha256(self.requested_json).hexdigest(),
            'effective': json.loads(self.effective_json),
            'effective_sha256': hashlib.sha256(self.effective_json).hexdigest(),
            'native_parameters': self.native_parameters,
            'native_parameters_sha256': hashlib.sha256(self.native_parameters_json).hexdigest(),
            'generated_inputs': [item.bind(owner_kind='request',
                owner_id=hashlib.sha256(self.requested_json).hexdigest(),
                format_schema='application/octet-stream').payload
                for item in self.generated_inputs],
        }

    def materialize_inputs(self, root: Path) -> None:
        for item in self.generated_inputs:
            item.materialize(root)

    @property
    def invocation_sha256(self) -> str:
        return digest(self.payload)


NATIVE_RECEIPT_INPUT = 'receipts/native-invocation.json'
NATIVE_RECEIPT_RESULT = '.bms-native-invocation.json'


def execution_projection_digest(envelope: Mapping[str, Any]) -> str:
    """Bind placement without a self-reference to its own receipt file.

    This is a placement projection, not a replacement scientific plan schema.
    Excluding only the reserved receipt and creation time preserves v1 archives.
    """
    fields = ('job_id', 'root_job_id', 'parent_job_id', 'attempt_id',
              'execution_target_id', 'source_revision', 'source_tree',
              'source_archive_sha256', 'command', 'working_directory',
              'environment', 'output_directory', 'expected_result_contract', 'path_map')
    projection = {key: envelope[key] for key in fields}
    projection['files'] = [record for record in envelope['files']
                           if record['relative_path'] != NATIVE_RECEIPT_INPUT]
    return digest(projection)


def native_invocation_receipt(invocation: NativeInvocation,
                              envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Publish commitments, never raw request settings or credential values."""
    if invocation.source_identity is None or invocation.entrypoint is None:
        raise ValueError('receipt requires a source-bound native invocation')
    receipt = {
        'schema': 'bms.native-invocation-receipt.v1',
        'job_id': envelope['job_id'], 'attempt_id': envelope['attempt_id'],
        'model_id': invocation.model_id, 'mode': invocation.mode,
        'entrypoint': invocation.entrypoint,
        'source_identity': asdict(invocation.source_identity),
        'invocation_sha256': invocation.invocation_sha256,
        'requested_sha256': hashlib.sha256(invocation.requested_json).hexdigest(),
        'effective_sha256': hashlib.sha256(invocation.effective_json).hexdigest(),
        'native_parameters_sha256': hashlib.sha256(invocation.native_parameters_json).hexdigest(),
        'execution_projection_sha256': execution_projection_digest(envelope),
    }
    validate_native_invocation_receipt(receipt, envelope)
    return receipt


def validate_native_invocation_receipt(receipt: Mapping[str, Any],
                                       envelope: Mapping[str, Any]) -> None:
    keys = {'schema', 'job_id', 'attempt_id', 'model_id', 'mode', 'entrypoint',
            'source_identity', 'invocation_sha256', 'requested_sha256',
            'effective_sha256', 'native_parameters_sha256', 'execution_projection_sha256'}
    if (not isinstance(receipt, Mapping) or set(receipt) != keys
            or receipt['schema'] != 'bms.native-invocation-receipt.v1'):
        raise ValueError('unsupported native invocation receipt')
    for key in keys - {'source_identity'}:
        value = receipt[key]
        if type(value) is not str or not value or '\x00' in value:
            raise ValueError('native invocation receipt fields must be nonempty text')
        if key.endswith('_sha256') and not re.fullmatch(r'[0-9a-f]{64}', value):
            raise ValueError('native invocation receipt digest is invalid')
    source = receipt['source_identity']
    if not isinstance(source, dict) or set(source) != {'revision', 'tree'}:
        raise ValueError('native invocation receipt source is invalid')
    identity = SourceIdentity(**source)
    if ((receipt['job_id'], receipt['attempt_id']) != (envelope['job_id'], envelope['attempt_id'])
            or (identity.revision, identity.tree) != (envelope['source_revision'], envelope['source_tree'])
            or receipt['execution_projection_sha256'] != execution_projection_digest(envelope)):
        raise ValueError('native invocation receipt differs from execution authority')
    entrypoint = PurePosixPath(receipt['entrypoint'])
    if (entrypoint.is_absolute() or '..' in entrypoint.parts
            or '\\' in receipt['entrypoint'] or entrypoint.as_posix() != receipt['entrypoint']
            or entrypoint.suffix != '.nf'):
        raise ValueError('native invocation receipt entrypoint is invalid')


def publish_native_invocation_receipt(root: Path, payload: bytes) -> Path:
    """Atomically add a reserved receipt, never overwrite native output bytes."""
    root = Path(root)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = f'.native-receipt-{uuid.uuid4().hex}.tmp'
    created = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o644, dir_fd=directory)
        created = True
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, NATIVE_RECEIPT_RESULT, src_dir_fd=directory,
                    dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            descriptor = os.open(NATIVE_RECEIPT_RESULT,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(descriptor, 'rb') as handle:
                import stat
                if (not stat.S_ISREG(os.fstat(handle.fileno()).st_mode)
                        or handle.read(len(payload) + 1) != payload):
                    raise ValueError('native invocation receipt conflicts with existing output')
        os.fsync(directory)
    finally:
        try:
            if created:
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)
        finally:
            os.close(directory)
    return root / NATIVE_RECEIPT_RESULT


@dataclass(frozen=True, order=True)
class CandidateIdentity:
    producer_candidate_key: str
    candidate_id: str

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> CandidateIdentity:
        # Prepared native requests preserve the producer key as source_artifact's
        # logical relative path. Never sort by the staging directory or hash ID.
        key = record.get("producer_candidate_key")
        if key is None and record.get('parent_workflow_id') == 'conformational_mapping':
            # CM's existing parent adapter orders by this logical producer key,
            # not the backend's physical structure path. Preserve that authority
            # when consuming original CM prepared requests without re-uploading.
            key = f"conformational_mapping/{record.get('candidate_id')}.pdb"
        if key is None:
            key = (record.get("source_artifact") or {}).get("relative_path")
        candidate = record.get("candidate_id")
        if not isinstance(key, str) or not key or not isinstance(candidate, str) or not candidate:
            raise ValueError("candidate ordering authority is missing")
        return cls(key, candidate)


def ordered_candidates(items: Sequence[T], record: Callable[[T], Mapping[str, Any]]) -> tuple[T, ...]:
    identities = [CandidateIdentity.from_record(record(item)) for item in items]
    if len(set(identities)) != len(identities):
        raise ValueError("terminal structure dataset has duplicate ordering identities")
    ids = [item.candidate_id for item in identities]
    if len(set(ids)) != len(ids):
        raise ValueError("terminal structure dataset has duplicate candidate IDs")
    return tuple(item for _, item in sorted(zip(identities, items), key=lambda pair: pair[0]))


def partition_ordered(items: Sequence[T], *, batching_enabled: bool,
                      structures_per_job: int) -> tuple[tuple[T, ...], ...]:
    """Preserve the caller's authoritative order; disabled batching means singles."""
    if not isinstance(batching_enabled, bool):
        raise ValueError("batching_enabled must be a boolean")
    if type(structures_per_job) is not int or structures_per_job < 1:
        raise ValueError("structures_per_job must be a positive integer")
    if not items:
        raise ValueError("terminal structure dataset is empty")
    size = structures_per_job if batching_enabled else 1
    return tuple(tuple(items[start:start + size]) for start in range(0, len(items), size))


@dataclass(frozen=True)
class GroupingPlan:
    parent_job_id: str
    parent_workflow_id: str
    settings_sha256: str
    candidate_authority_sha256: str
    groups: tuple[tuple[CandidateIdentity, ...], ...]

    @property
    def payload(self) -> dict[str, Any]:
        return {"schema_name": "bms.component-grouping.v1", "schema_version": 1, **asdict(self)}

    @property
    def plan_id(self) -> str:
        return digest(self.payload)

    def component_id(self, ordinal: int) -> str:
        if not 0 <= ordinal < len(self.groups):
            raise ValueError("foreign group ordinal")
        # Logical identity is placement-neutral; the ledger additionally binds
        # the full adapter-native input closure through candidate_authority_sha256.
        logical = dict(self.payload)
        logical.pop("candidate_authority_sha256")
        return f"frustrampnn:{digest(logical)}:{ordinal}"

    def require_groups(self, observed: Sequence[Sequence[str]]) -> None:
        expected = [[member.candidate_id for member in group] for group in self.groups]
        if [list(group) for group in observed] != expected:
            raise ValueError("component grouping/order does not match required exact join")


def plan_frustrampnn(records: Sequence[Mapping[str, Any]], settings: Mapping[str, Any]) -> GroupingPlan:
    ordered = ordered_candidates(records, lambda record: record)
    if not ordered:
        raise ValueError("terminal structure dataset is empty")
    first = ordered[0]
    for record in ordered:
        if any(record.get(key) != first.get(key) for key in ("parent_job_id", "parent_workflow_id")):
            raise ValueError("candidates mix parent authority")
        if record.get("requiredness") != "required":
            raise ValueError("FrustraMPNN grouping requires explicit required candidates")
        if "requested_settings" in record and record["requested_settings"] != settings:
            raise ValueError("candidates mix requested settings authority")
    if not first.get("parent_job_id") or not first.get("parent_workflow_id"):
        raise ValueError("parent identity is required")
    groups = partition_ordered(ordered, batching_enabled=settings["batching_enabled"],
                               structures_per_job=settings["structures_per_job"])
    return GroupingPlan(first["parent_job_id"], first["parent_workflow_id"], digest(settings),
                        digest(ordered), tuple(tuple(CandidateIdentity.from_record(r) for r in g) for g in groups))


def plan_boltzgen(total_designs: int, designs_per_job: int, settings: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Stable campaign ordinals and remainder sizes; no scheduler/placement inputs.

    The entire effective settings map is bound, not reconstructed from argv.
    A design count override must be compiled before expansion, never after it.
    """
    if type(total_designs) is not int or type(designs_per_job) is not int or min(total_designs, designs_per_job) < 1:
        raise ValueError('BoltzGen campaign sizes must be positive integers')
    return tuple({'index': ordinal, 'designs': min(designs_per_job, total_designs-start),
                  'design_start': start, 'requiredness': 'attempt_required_result_optional', 'settings': dict(settings),
                  'settings_sha256': digest(settings)}
                 for ordinal, start in enumerate(range(0, total_designs, designs_per_job)))


@dataclass(frozen=True)
class ResultReference:
    component_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    schema: str

    def __post_init__(self) -> None:
        # Keep the historical wire shape while sharing the artifact contract.
        self.as_artifact_reference()

    def as_artifact_reference(self, *, job_id: str | None = None) -> ArtifactReference:
        return ArtifactReference(
            logical_id=self.relative_path, owner_kind='component',
            owner_id=self.component_id, component_id=self.component_id,
            job_id=job_id, role='result', relative_path=self.relative_path,
            sha256=self.sha256, size_bytes=self.size_bytes, format_schema=self.schema)

    def resolve(self, root: Path) -> Path:
        return self.as_artifact_reference().resolve(root)


class GroupingLedger:
    """Attempt-local SQLite expansion journal, safe on restart and concurrent writers.

    Sealing records adapter-validated result references; it is not scientific
    validation or process cancellation. Adapters must prove both separately.
    """
    def __init__(self, path: Path, *, attempt_id: str, plan: GroupingPlan):
        if not attempt_id:
            raise ValueError("attempt identity is required")
        self.path, self.attempt_id, self.plan = Path(path), attempt_id, plan
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS authority (singleton INTEGER PRIMARY KEY CHECK(singleton=1), attempt TEXT NOT NULL, plan BLOB NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS results (component TEXT PRIMARY KEY, reference BLOB NOT NULL)")
            db.execute("INSERT OR IGNORE INTO authority(singleton,attempt,plan) VALUES(1,?,?)",
                       (attempt_id, canonical_bytes(plan.payload)))
            row = db.execute("SELECT attempt,plan FROM authority WHERE singleton=1").fetchone()
            if row != (attempt_id, canonical_bytes(plan.payload)):
                raise ValueError("ledger attempt or immutable grouping plan conflicts")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def check_active(self) -> None:
        with self._connect() as db:
            if db.execute("SELECT cancel_requested FROM authority").fetchone()[0]:
                raise RuntimeError("component cancellation requested; quiescence is not yet established")

    def request_cancel(self) -> None:
        with self._connect() as db:
            db.execute("UPDATE authority SET cancel_requested=1 WHERE singleton=1")

    def seal(self, reference: ResultReference) -> None:
        if reference.component_id not in {self.plan.component_id(i) for i in range(len(self.plan.groups))}:
            raise ValueError("foreign component result")
        reference.as_artifact_reference(job_id=self.plan.parent_job_id)
        payload = canonical_bytes(asdict(reference))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT cancel_requested FROM authority").fetchone()[0]:
                raise RuntimeError("cannot seal after cancellation intent")
            db.execute("INSERT OR IGNORE INTO results VALUES(?,?)", (reference.component_id, payload))
            if db.execute("SELECT reference FROM results WHERE component=?", (reference.component_id,)).fetchone()[0] != payload:
                raise ValueError("component result reference conflicts")

    def join(self) -> tuple[ResultReference, ...]:
        with self._connect() as db:
            db.execute("BEGIN")
            if db.execute("SELECT cancel_requested FROM authority").fetchone()[0]:
                raise RuntimeError("cannot join after cancellation intent")
            rows = dict(db.execute("SELECT component,reference FROM results").fetchall())
        ids = [self.plan.component_id(i) for i in range(len(self.plan.groups))]
        if set(rows) != set(ids):
            raise RuntimeError("required component result join is incomplete")
        return tuple(ResultReference(**json.loads(rows[identity])) for identity in ids)


class ComponentBoundary:
    """Shared submit/wait/result guards around adapter-owned execution.

    Submission is called once, never retried on ambiguous failure. The adapter
    owns idempotent replay and native response/scientific validation. Waiting
    owns no resource reservation: the underlying scheduler/Nextflow owns leases
    and process termination. A cancellation exception is not quiescence proof.
    """

    def __init__(self, ledger: GroupingLedger):
        self.ledger = ledger

    def submit(self, operation: Callable[[], T]) -> T:
        self.ledger.check_active()
        result = operation()
        self.ledger.check_active()
        return result

    def wait(self, observe: Callable[[], T], complete: Callable[[T], bool], *,
             poll_interval: float, timeout: float = 0,
             clock: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep) -> T:
        if poll_interval < 0 or timeout < 0:
            raise ValueError("component wait intervals must be nonnegative")
        started = clock()
        while True:
            self.ledger.check_active()
            observation = observe()
            self.ledger.check_active()
            if complete(observation):
                return observation
            elapsed = clock() - started
            if timeout and elapsed >= timeout:
                raise RuntimeError("timed out waiting for required components")
            sleep(min(poll_interval, max(0, timeout - elapsed)) if timeout else poll_interval)

    def result(self, reference: ResultReference, root: Path) -> Path:
        """Seal only adapter-validated, byte-bound native results."""
        self.ledger.check_active()
        resolved = reference.resolve(root)
        self.ledger.seal(reference)
        return resolved

    def join(self, root: Path) -> tuple[Path, ...]:
        """Exact required-set join; recheck bytes even on replay."""
        paths = tuple(reference.resolve(root) for reference in self.ledger.join())
        self.ledger.check_active()
        return paths
