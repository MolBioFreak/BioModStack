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
class SourceIdentity:
    """Committed source metadata; admission owns tracked-byte cleanliness."""
    revision: str
    tree: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or len(value) != 40
               or any(char not in '0123456789abcdef' for char in value)
               for value in (self.revision, self.tree)):
            raise ValueError('committed BMS source identity is invalid')

    @classmethod
    def from_checkout(cls, root: Path) -> SourceIdentity:
        # Read Git metadata, not the worktree. Remote admission retains its
        # existing clean-source check; do not add a scan to every compiler call.
        import subprocess
        values = subprocess.run(['git', 'rev-parse', 'HEAD', 'HEAD^{tree}'],
            cwd=Path(root).resolve(), check=True, capture_output=True, text=True,
            timeout=60).stdout.splitlines()
        if len(values) != 2:
            raise ValueError('committed BMS source identity is unavailable')
        return cls(*values)


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
class UnresolvedField:
    component_or_dependency_id: str
    field: str
    needed_authority: str
    reason: str
    blocks: tuple[str, ...] = ('preview_acceptance', 'provision', 'launch')


@dataclass(frozen=True)
class SelectedDependency:
    """Logical native dependency, not byte approval or a placement binding."""
    logical_id: str
    kind: str
    relative_path: str | None
    authority: str
    semantic_release: str | None = None
    selector: str | None = None
    compatibility_authority: str | None = None
    requiredness: str = 'required'
    condition: str | None = None


@dataclass(frozen=True)
class NativeArtifactRole:
    role_id: str
    component_key: str
    direction: str
    category: str
    authority: str
    requiredness: str = 'required'
    identity_authority: str | None = None
    publication_authority: str | None = None
    condition: str | None = None
    format: str | None = None
    cardinality_authority: str | None = None
    native_declaration: str | None = None
    source_role_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NativeComponent:
    component_key: str
    authority: str
    selection_json: bytes
    depends_on: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()
    input_role_ids: tuple[str, ...] = ()
    output_role_ids: tuple[str, ...] = ()
    requiredness: str = 'required'
    resources_json: bytes | None = None
    grouping_authority: str | None = None
    lifecycle_authority: str | None = None
    condition: str | None = None
    expansion_authority: str | None = None
    lifecycle_json: bytes | None = None
    expansion_json: bytes | None = None


@dataclass(frozen=True)
class ExternalServiceIntent:
    logical_id: str
    provider: str | None
    authority: str
    settings_json: bytes
    input_role_ids: tuple[str, ...]
    output_role_ids: tuple[str, ...]
    state: str
    operation_identity: str | None = None


def _selected_value(value: Any) -> Any:
    """Detached JSON projection of immutable native descriptors."""
    from dataclasses import fields, is_dataclass
    if is_dataclass(value):
        return {field.name: _selected_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, bytes):
        return json.loads(value)
    if isinstance(value, tuple):
        return [_selected_value(item) for item in value]
    return value


def _require_immutable(value: Any) -> None:
    from dataclasses import fields, is_dataclass
    if is_dataclass(value):
        for field in fields(value):
            _require_immutable(getattr(value, field.name))
    elif isinstance(value, tuple):
        for item in value:
            _require_immutable(item)
    elif type(value) is bytes:
        if canonical_bytes(json.loads(value)) != value:
            raise ValueError('selected metadata snapshot must be canonical JSON')
    elif value is not None and type(value) not in (str, bool, int, float):
        raise ValueError('selected metadata must be deeply immutable')


@dataclass(frozen=True)
class SelectedExecutionMetadata:
    availability: str
    settings_authority: str
    static_components: tuple[NativeComponent, ...]
    dynamic_templates: tuple[NativeComponent, ...]
    dependencies: tuple[SelectedDependency, ...]
    artifact_roles: tuple[NativeArtifactRole, ...]
    external_services: tuple[ExternalServiceIntent, ...]
    result_contract_json: bytes
    admission_authority: str | None
    retrieval_authority: str | None
    blockers: tuple[UnresolvedField, ...]
    closure_reviewed: bool = False
    descriptors_reviewed: bool = False

    def __post_init__(self) -> None:
        for name, item_type in (('static_components', NativeComponent),
                ('dynamic_templates', NativeComponent), ('dependencies', SelectedDependency),
                ('artifact_roles', NativeArtifactRole), ('external_services', ExternalServiceIntent),
                ('blockers', UnresolvedField)):
            items = getattr(self, name)
            if type(items) is not tuple or any(not isinstance(item, item_type) for item in items):
                raise ValueError('selected metadata collections must be immutable and typed')
        _require_immutable(self)

    @property
    def dependency_closure_complete(self) -> bool:
        return (self.closure_reviewed and bool(self.dependencies) and
                not any(row.field == 'dependency_closure' for row in self.blockers))

    def blockers_for(self, operation: str) -> tuple[UnresolvedField, ...]:
        """Provisioning is independent of computational callback migration."""
        if operation not in {'preview_acceptance', 'provision', 'launch'}:
            raise ValueError('unknown selected-plan operation')
        return tuple(row for row in self.blockers if operation in row.blocks)

    @property
    def complete(self) -> bool:
        components = (*self.static_components, *self.dynamic_templates)
        component_ids = {row.component_key for row in components}
        dependency_ids = {row.logical_id for row in self.dependencies}
        role_ids = {row.role_id for row in self.artifact_roles}
        described = bool(components) and all(
            row.authority and row.resources_json and row.lifecycle_authority and
            row.lifecycle_json and row.input_role_ids and row.output_role_ids and
            set(row.depends_on) <= component_ids and
            set(row.dependency_ids) <= dependency_ids and
            set((*row.input_role_ids, *row.output_role_ids)) <= role_ids
            for row in components)
        return (self.descriptors_reviewed and self.dependency_closure_complete and
                bool(self.static_components) and described and not self.blockers and
                len(component_ids) == len(components) and len(role_ids) == len(self.artifact_roles))

    def to_dict(self) -> dict[str, Any]:
        return {**_selected_value(self), 'dependency_closure_complete': self.dependency_closure_complete,
                'complete': self.complete}


# Only additions made by the existing trusted launch owner belong here. These
# are not scientific reselection and are intentionally outside the plan digest.
_LAUNCH_BINDING_KEYS = frozenset({
    'protenix_prepared_msa_dir', 'protenix_prepared_msa_sha256',
    'boltz_prepared_msa_dir', 'boltz_prepared_msa_sha256', 'bcp_input_path',
    'boltz_launch_authority_base64', 'boltz_launch_authority_path',
    'boltz_launch_authority_sha256', 'protein_science_contract_revision',
})


@dataclass(frozen=True)
class SelectedExecutionPlan:
    source_identity: SourceIdentity
    workflow: str
    model_id: str
    mode: str
    entrypoint: str
    requested_json: bytes
    effective_json: bytes
    native_parameters_json: bytes
    metadata: SelectedExecutionMetadata
    launch_bindings_json: bytes = b'{}'

    def __post_init__(self) -> None:
        _require_immutable(self)
        if not isinstance(self.source_identity, SourceIdentity):
            raise ValueError('selected plan requires source identity')
        if not isinstance(self.metadata, SelectedExecutionMetadata):
            raise ValueError('selected plan requires typed execution metadata')

    @property
    def dependencies(self) -> tuple[SelectedDependency, ...]:
        return self.metadata.dependencies

    @property
    def blockers(self) -> tuple[UnresolvedField, ...]:
        return self.metadata.blockers

    @property
    def dependency_closure_complete(self) -> bool:
        return self.metadata.dependency_closure_complete

    @property
    def complete(self) -> bool:
        return self.metadata.complete

    @property
    def requested_sha256(self) -> str:
        return hashlib.sha256(self.requested_json).hexdigest()

    @property
    def effective_sha256(self) -> str:
        return hashlib.sha256(self.effective_json).hexdigest()

    def _identity_payload(self) -> dict[str, Any]:
        payload = _selected_value(self)
        payload.pop('launch_bindings_json')
        return {'schema_name': 'bms.selected-execution-plan.v1', 'schema_version': 1,
                **payload, 'requested_sha256': self.requested_sha256,
                'effective_sha256': self.effective_sha256}

    @property
    def plan_sha256(self) -> str:
        return digest(self._identity_payload())

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        return {**payload, 'plan_sha256': digest(payload),
                'launch_bindings_json': json.loads(self.launch_bindings_json),
                'dependencies': [_selected_value(ref) for ref in self.dependencies],
                'blockers': [_selected_value(row) for row in self.blockers],
                'dependency_closure_complete': self.dependency_closure_complete,
                'complete': self.complete}

    def bind_invocation(self, invocation: NativeInvocation) -> SelectedExecutionPlan:
        """Rebind origin/transport without calling a scientific selector again."""
        from dataclasses import replace
        if ((invocation.model_id, invocation.mode, invocation.entrypoint) !=
                (self.model_id, self.mode, self.entrypoint) or
                invocation.effective_json != self.effective_json):
            raise ValueError('selected plan science changed; recompile descriptors')
        original = json.loads(self.native_parameters_json)
        current = invocation.native_parameters
        changed = {key for key in original.keys() | current.keys()
                   if key not in original or key not in current or original[key] != current[key]}
        if changed - _LAUNCH_BINDING_KEYS:
            raise ValueError('selected native science changed; recompile descriptors')
        bindings = {key: current[key] for key in sorted(changed) if key in current}
        return replace(self, requested_json=invocation.requested_json,
                       source_identity=invocation.source_identity or self.source_identity,
                       launch_bindings_json=canonical_bytes(bindings))


@dataclass(frozen=True)
class NativeInvocation:
    """Immutable in-process output of the existing scientific compiler.

    Command rendering and typed settings have one producer. This projection
    does not replace the shared plan or add a worker/result wire protocol.
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
    execution_plan: SelectedExecutionPlan | None = None

    def __post_init__(self) -> None:
        if (type(self.model_id) is not str or not self.model_id
                or type(self.mode) is not str or not self.mode
                or type(self.command) is not tuple or not self.command):
            raise ValueError('native invocation requires model, mode and command')
        if any(type(value) is not str or '\x00' in value for value in self.command):
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
        if type(self.generated_inputs) is not tuple or any(
                not isinstance(item, GeneratedInput) for item in self.generated_inputs):
            raise ValueError('generated input roster must be immutable and typed')
        paths = {item.relative_path for item in self.generated_inputs}
        if len(paths) != len(self.generated_inputs):
            raise ValueError('generated input paths must be unique')
        if any(parent.as_posix() in paths for path in paths
               for parent in PurePosixPath(path).parents if parent.as_posix() != '.'):
            raise ValueError('generated input files cannot also be parent directories')
        for payload in (self.requested_json, self.effective_json, self.native_parameters_json):
            if type(payload) is not bytes:
                raise ValueError('native invocation snapshots must be immutable bytes')
            value = json.loads(payload)
            if not isinstance(value, dict) or canonical_bytes(value) != payload:
                raise ValueError('native invocation settings must be canonical objects')
        if self.execution_plan is not None:
            if not isinstance(self.execution_plan, SelectedExecutionPlan):
                raise ValueError('native invocation execution plan must be typed')
            object.__setattr__(self, 'execution_plan', self.execution_plan.bind_invocation(self))

    @classmethod
    def capture(cls, *, model_id: str, mode: str, command: Sequence[str],
                requested: Mapping[str, Any], effective: Mapping[str, Any],
                native_parameters: Mapping[str, Any], entrypoint: str,
                generated_inputs: Sequence[GeneratedInput] = ()) -> NativeInvocation:
        return cls(model_id, mode, tuple(command), canonical_bytes(dict(requested)),
                   canonical_bytes(dict(effective)), canonical_bytes(dict(native_parameters)),
                   generated_inputs=tuple(generated_inputs), entrypoint=entrypoint)

    @property
    def native_parameters(self) -> dict[str, Any]:
        return json.loads(self.native_parameters_json)

    def materialize_inputs(self, root: Path) -> None:
        for item in self.generated_inputs:
            item.materialize(root)



@dataclass(frozen=True, order=True)
class CandidateIdentity:
    producer_candidate_key: str
    candidate_id: str

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> CandidateIdentity:
        # Prepared native requests preserve the producer key as source_artifact's
        # logical relative path. Never sort by the staging directory or hash ID.
        key = record.get("producer_candidate_key")
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


@dataclass(frozen=True)
class ResultReference:
    component_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    schema: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if (not self.relative_path or path.is_absolute() or ".." in path.parts
                or "\\" in self.relative_path or path.as_posix() != self.relative_path
                or self.relative_path == "."):
            raise ValueError("result reference must have a contained relative path")
        if (len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256)
                or type(self.size_bytes) is not int or self.size_bytes < 0 or not self.schema):
            raise ValueError("invalid result reference binding")

    def resolve(self, root: Path) -> Path:
        root = Path(root).resolve(strict=True)
        candidate = root / self.relative_path
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("result reference escapes artifact root")
        if file_identity(resolved) != (self.sha256, self.size_bytes):
            raise ValueError("result reference byte binding mismatch")
        return resolved


def file_identity(path: Path) -> tuple[str, int]:
    """Hash native artifacts without allocating whole trajectories in memory."""
    checksum, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
            size += len(chunk)
    return checksum.hexdigest(), size


class GroupingLedger:
    """Attempt-local SQLite expansion journal, safe on restart and concurrent writers.

    Sealing records adapter-validated result references; it is not scientific
    validation or process cancellation. Adapters must prove both separately.
    """
    def __init__(self, path: Path, *, attempt_id: str, plan: GroupingPlan):
        if not attempt_id:
            raise ValueError("attempt identity is required")
        self.path, self.attempt_id, self.plan = Path(path), attempt_id, plan
        self._initialize_authority(plan.payload)

    def _initialize_authority(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS authority (singleton INTEGER PRIMARY KEY CHECK(singleton=1), attempt TEXT NOT NULL, plan BLOB NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS results (component TEXT PRIMARY KEY, reference BLOB NOT NULL)")
            db.execute("INSERT OR IGNORE INTO authority(singleton,attempt,plan) VALUES(1,?,?)",
                       (self.attempt_id, canonical_bytes(payload)))
            row = db.execute("SELECT attempt,plan FROM authority WHERE singleton=1").fetchone()
            if row != (self.attempt_id, canonical_bytes(payload)):
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


@dataclass(frozen=True)
class ComponentRequest:
    """Native request envelope; the existing compiler owns scientific validation."""
    parent_job_id: str
    stage: str
    child_key: str
    payload_json: bytes
    required: bool = True

    def __post_init__(self) -> None:
        if any(type(x) is not str or not x for x in
               (self.parent_job_id, self.stage, self.child_key)) or type(self.required) is not bool:
            raise ValueError("component lineage and explicit requiredness are required")
        value = json.loads(self.payload_json)
        if type(self.payload_json) is not bytes or not isinstance(value, dict) or canonical_bytes(value) != self.payload_json:
            raise ValueError("component payload must be immutable canonical JSON")
        if any(key in value for key in ("command", "shell", "argv")):
            raise ValueError("component requests cannot supply executable commands")
        if not value.get("model_id") or not value.get("mode"):
            raise ValueError("component requires native model_id and mode")
        if value.get("parent_job_id", self.parent_job_id) != self.parent_job_id:
            raise ValueError("component payload parent conflicts")

    @classmethod
    def capture(cls, *, parent_job_id: str, stage: str, child_key: str,
                payload: Mapping[str, Any], required: bool = True) -> ComponentRequest:
        return cls(parent_job_id, stage, child_key, canonical_bytes(dict(payload)), required)

    @property
    def component_id(self) -> str:
        # Changed science under the same logical child conflicts, rather than
        # creating an unnoticed second child on replay.
        return "component:" + digest([self.parent_job_id, self.stage, self.child_key])

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)

    def to_dict(self) -> dict[str, Any]:
        return dict(parent_job_id=self.parent_job_id, stage=self.stage,
                    child_key=self.child_key, payload=self.payload, required=self.required)


class ComponentRuntime(GroupingLedger):
    """Generalized attempt journal using the existing ledger/transaction owner.

    A claim is a durable uncertain-start fence, not a renewable scheduler lease.
    Only the trusted compiler/Nextflow supervisor launches and proves stopped
    writers. There is deliberately no timeout-driven reclaim or shell executor.
    """
    def __init__(self, path: Path, *, attempt_id: str, root_job_id: str,
                 target_id: str, lease_id: str, artifact_root: Path,
                 source_identity: Mapping[str, Any] | None = None,
                 plan_sha256: str | None = None,
                 execution_plan: Mapping[str, Any] | None = None):
        if any(type(x) is not str or not x for x in
               (attempt_id, root_job_id, target_id, lease_id)):
            raise ValueError("attempt, root, target and lease identities are required")
        self.path, self.attempt_id = Path(path), attempt_id
        self.root_job_id, self.target_id, self.lease_id = root_job_id, target_id, lease_id
        self.artifact_root = Path(artifact_root).resolve(strict=True)
        self.source_identity = json.loads(canonical_bytes(source_identity))
        self.plan_sha256 = plan_sha256
        self.execution_plan = json.loads(canonical_bytes(execution_plan))
        self._initialize_authority(dict(schema_name="bms.component-attempt.v1",
            root_job_id=root_job_id, target_id=target_id, lease_id=lease_id,
            source_identity=self.source_identity, plan_sha256=plan_sha256,
            execution_plan=self.execution_plan))
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS components (component TEXT PRIMARY KEY, request BLOB NOT NULL, state TEXT NOT NULL, owner TEXT, boot TEXT, result BLOB, error TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS component_native_parents (component TEXT PRIMARY KEY, snapshot BLOB NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS component_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, component TEXT, phase TEXT NOT NULL, detail BLOB NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS root_execution (singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL, owner TEXT NOT NULL, boot TEXT NOT NULL, detail BLOB NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS component_groups (group_id TEXT PRIMARY KEY, children BLOB NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS external_services (request_id TEXT PRIMARY KEY, request BLOB NOT NULL, result BLOB)")
            db.execute("CREATE TABLE IF NOT EXISTS checkpoints (checkpoint TEXT PRIMARY KEY, payload BLOB NOT NULL, decision BLOB)")
            db.execute("CREATE TABLE IF NOT EXISTS checkpoint_operations (operation TEXT PRIMARY KEY, detail BLOB NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS component_replacements (original TEXT PRIMARY KEY, replacement TEXT UNIQUE NOT NULL, operation TEXT UNIQUE NOT NULL, detail BLOB NOT NULL)")

    @staticmethod
    def _active(db) -> None:
        if db.execute("SELECT cancel_requested FROM authority WHERE singleton=1").fetchone()[0]:
            raise RuntimeError("component cancellation requested; quiescence is not yet established")

    @staticmethod
    def _event(db, component: str | None, phase: str, detail: Mapping[str, Any]) -> None:
        db.execute("INSERT INTO component_events(component,phase,detail) VALUES(?,?,?)",
                   (component, phase, canonical_bytes(dict(detail))))

    def submit_external_service(self, service_id: str, native_input: Any) -> str:
        """Journal data for a compiler-declared external stage, not a child launch."""
        services = (self.execution_plan or {}).get('metadata', {}).get('external_services', [])
        selected = [row for row in services if row['logical_id'] == service_id
                    and row['state'] == 'planned_from_generated_candidates']
        if len(selected) != 1 or not self.plan_sha256 or not self.source_identity:
            raise ValueError('external service requires its selected plan/source authority')
        request = dict(attempt_id=self.attempt_id, root_job_id=self.root_job_id,
            target_id=self.target_id, lease_id=self.lease_id, plan_sha256=self.plan_sha256,
            source_identity=self.source_identity, service=selected[0], native_input=native_input)
        payload = canonical_bytes(request)
        if len(payload) > 4 * 1024 * 1024:
            raise ValueError('external service native input exceeds transport bound')
        identity = digest(request)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._active(db)
            db.execute('INSERT OR IGNORE INTO external_services VALUES(?,?,NULL)', (identity, payload))
        return identity

    def pending_external_services(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as db:
            self._active(db)
            # One bounded operation per controller reconciliation; no new scheduler.
            rows = db.execute('SELECT request_id,request FROM external_services WHERE result IS NULL ORDER BY rowid LIMIT 1')
            return tuple(dict(request_id=row[0], **json.loads(row[1])) for row in rows)

    def external_service(self, request_id: str) -> dict[str, Any]:
        with self._connect() as db:
            self._active(db)
            row = db.execute('SELECT request,result FROM external_services WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            raise ValueError('foreign external service request')
        return dict(request_id=request_id, **json.loads(row[0]), result=json.loads(row[1]) if row[1] else None)

    def fail_external_service(self, request_id: str) -> None:
        """Definitive controller failure; never overwrite delivered input custody."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._active(db)
            if not db.execute('SELECT 1 FROM external_services WHERE request_id=?', (request_id,)).fetchone():
                raise ValueError('foreign external service request')
            db.execute('UPDATE external_services SET result=? WHERE request_id=? AND result IS NULL',
                       (canonical_bytes({'error': 'controller MSA preparation failed'}), request_id))

    def complete_external_service(self, request_id: str, reference: ResultReference) -> None:
        if reference.component_id != request_id:
            raise ValueError('foreign external service artifact')
        reference.resolve(self.artifact_root)
        payload = canonical_bytes(asdict(reference))
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._active(db)
            row = db.execute('SELECT result FROM external_services WHERE request_id=?', (request_id,)).fetchone()
            if row is None or (row[0] is not None and row[0] != payload):
                raise ValueError('immutable external service result conflicts')
            db.execute('UPDATE external_services SET result=? WHERE request_id=?', (payload, request_id))

    def submit(self, request: ComponentRequest) -> str:
        if not isinstance(request, ComponentRequest):
            raise ValueError("typed component request required")
        identity, payload = request.component_id, canonical_bytes(request.to_dict())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._active(db)
            if request.parent_job_id != self.root_job_id and not db.execute(
                    "SELECT 1 FROM components WHERE component=?", (request.parent_job_id,)).fetchone():
                raise ValueError("foreign component parent")
            existing = db.execute("SELECT request FROM components WHERE component=?", (identity,)).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError("immutable component request conflicts")
                return identity
            db.execute("INSERT INTO components(component,request,state) VALUES(?,?,'queued')", (identity, payload))
            self._event(db, identity, "queued", {})
        return identity

    def request(self, component_id: str) -> ComponentRequest:
        with self._connect() as db:
            row = db.execute("SELECT request FROM components WHERE component=?", (component_id,)).fetchone()
        if not row:
            raise ValueError("foreign component")
        return ComponentRequest.capture(**json.loads(row[0]))

    def pending(self) -> tuple[str, ...]:
        with self._connect() as db:
            self._active(db)
            return tuple(row[0] for row in db.execute("SELECT component FROM components WHERE state='queued' ORDER BY rowid"))

    def bind_native_parent(self, component_id: str, snapshot: Mapping[str, Any], *,
                           owner_id: str, boot_id: str) -> None:
        """Retain the compiler's native parent snapshot for nested child requests."""
        if snapshot.get('id') != component_id:
            raise ValueError('native parent identity conflicts')
        payload = canonical_bytes(dict(snapshot))
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._active(db)
            self._owned(db, component_id, owner_id, boot_id)
            row = db.execute('SELECT snapshot FROM component_native_parents WHERE component=?',
                             (component_id,)).fetchone()
            if row is not None and row[0] != payload:
                raise ValueError('immutable native parent snapshot conflicts')
            db.execute('INSERT OR IGNORE INTO component_native_parents VALUES (?,?)',
                       (component_id, payload))

    def native_parent(self, component_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute('SELECT snapshot FROM component_native_parents WHERE component=?',
                             (component_id,)).fetchone()
        if row is None:
            raise ValueError('native parent has not been bound by its compiler')
        return json.loads(row[0])

    def unfinished(self) -> tuple[str, ...]:
        with self._connect() as db:
            return tuple(row[0] for row in db.execute("SELECT component FROM components WHERE state IN ('queued','running','uncertain') ORDER BY rowid"))

    def claim(self, component_id: str, *, owner_id: str, boot_id: str) -> bool:
        if not owner_id or not boot_id:
            raise ValueError("actual process owner and boot evidence are required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._active(db)
            row = db.execute("SELECT state FROM components WHERE component=?", (component_id,)).fetchone()
            if not row:
                raise ValueError("foreign component")
            if row[0] != "queued":
                return False
            db.execute("UPDATE components SET state='running',owner=?,boot=? WHERE component=?", (owner_id, boot_id, component_id))
            self._event(db, component_id, "claimed", dict(owner_id=owner_id, boot_id=boot_id))
        return True

    def dispatch(self, component_id: str, *, owner_id: str, boot_id: str,
                 compile_native: Callable[[ComponentRequest], NativeInvocation],
                 launch_native: Callable[[NativeInvocation, ComponentRequest, ComponentRuntime], None]) -> bool:
        if not self.claim(component_id, owner_id=owner_id, boot_id=boot_id):
            return False
        request = self.request(component_id)
        try:
            invocation = compile_native(request)
            if not isinstance(invocation, NativeInvocation):
                raise ValueError("existing compiler must return NativeInvocation")
        except Exception:
            self.fail(component_id, owner_id=owner_id, boot_id=boot_id,
                      reason="native compilation failed", quiescent=True)
            raise
        try:
            self.check_active()
            launch_native(invocation, request, self)
        except Exception:
            # Do not persist arbitrary exception strings: they may carry secrets.
            if self.child_status(component_id)["status"] != "failed":
                self.fail(component_id, owner_id=owner_id, boot_id=boot_id,
                          reason="native launch interrupted; reconcile owned writers", quiescent=False)
            raise
        return True

    @staticmethod
    def _owned(db, component_id: str, owner_id: str, boot_id: str):
        row = db.execute("SELECT state,owner,boot,result,error FROM components WHERE component=?", (component_id,)).fetchone()
        if not row or row[1:3] != (owner_id, boot_id):
            raise ValueError("component process/boot ownership conflicts")
        return row

    def complete(self, component_id: str, *, owner_id: str, boot_id: str,
                 result: Mapping[str, Any], references: Sequence[ResultReference]) -> None:
        """Native owner calls only after semantic validation and writer join."""
        if not references:
            raise ValueError("completion requires native artifact references")
        for reference in references:
            if reference.component_id != component_id:
                raise ValueError("foreign component result")
            reference.resolve(self.artifact_root)
        payload = canonical_bytes(dict(result=dict(result), references=[asdict(r) for r in references]))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._active(db)
            row = self._owned(db, component_id, owner_id, boot_id)
            if row[0] == "completed" and row[3] == payload:
                return
            if row[0] not in {"running", "uncertain", "execution_finished"}:
                raise ValueError("component result conflicts with terminal state")
            db.execute("UPDATE components SET state='completed',result=?,error=NULL WHERE component=?", (payload, component_id))
            self._event(db, component_id, "completed", {})

    def complete_validated_child(self, component_id: str, *, result: Mapping[str, Any],
                                 references: Sequence[ResultReference]) -> None:
        """An admitted native collector seals its already-quiescent child."""
        with self._connect() as db:
            self._active(db)
            row = db.execute("SELECT state,owner,boot,result FROM components WHERE component=?",
                             (component_id,)).fetchone()
        if not row or row[0] not in {"execution_finished", "completed"}:
            raise ValueError("native validation requires a quiescent child execution")
        native_result = {**json.loads(row[3])["result"], **dict(result)}
        self.complete(component_id, owner_id=row[1], boot_id=row[2],
                      result=native_result, references=references)

    def fail(self, component_id: str, *, owner_id: str, boot_id: str,
             reason: str, quiescent: bool = False,
             failure_receipt: Mapping[str, Any] | None = None) -> None:
        """Reason must be a sanitized diagnostic, never credentials/raw argv."""
        state = "failed" if quiescent else "uncertain"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned(db, component_id, owner_id, boot_id)
            if row[0] == state and row[4] == reason:
                return
            if row[0] not in {"running", "uncertain"}:
                raise ValueError("component terminal state conflicts")
            db.execute("UPDATE components SET state=?,error=? WHERE component=?", (state, reason, component_id))
            self._event(db, component_id, state, dict(reason=reason,
                **({"failure_receipt": dict(failure_receipt)} if failure_receipt else {})))

    def execution_finished(self, component_id: str, *, owner_id: str, boot_id: str,
                           output_dir: str, exit_code: int) -> None:
        """Process owner has joined all writers; native collectors still validate."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned(db, component_id, owner_id, boot_id)
            payload = canonical_bytes(dict(result=dict(output_dir=output_dir, exit_code=exit_code), references=[]))
            state = "execution_finished" if exit_code == 0 else "failed"
            if row[0] == state and row[3] == payload:
                return
            if row[0] not in {"running", "uncertain"}:
                raise ValueError("component execution state conflicts")
            db.execute("UPDATE components SET state=?,result=?,error=? WHERE component=?",
                       (state, payload, None if exit_code == 0 else "native process failed", component_id))
            self._event(db, component_id, state, dict(exit_code=exit_code))

    def claim_root(self, *, owner_id: str, boot_id: str) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._active(db)
            prior = db.execute("SELECT state,boot,detail FROM root_execution").fetchone()
            if prior:
                if prior[0] != "resume_ready" or prior[1] != boot_id:
                    return False
                db.execute("UPDATE root_execution SET state='starting',owner=?,boot=?", (owner_id, boot_id))
                self._event(db, self.root_job_id, "root_continuation_claimed", json.loads(prior[2]))
                return True
            db.execute("INSERT INTO root_execution VALUES(1,'starting',?,?,?)",
                       (owner_id, boot_id, canonical_bytes({})))
            self._event(db, self.root_job_id, "root_claimed", dict(owner_id=owner_id, boot_id=boot_id))
        return True

    def checkpoint_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT detail FROM checkpoint_operations WHERE operation=?", (operation_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def reject_checkpoint_operation(self, binding: Mapping[str, Any], error: str) -> dict[str, Any]:
        """Record definitive prestart rejection only while the root is still paused."""
        detail = dict(binding=dict(binding), state='rejected', error=error)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._active(db)
            prior = db.execute('SELECT detail FROM checkpoint_operations WHERE operation=?',
                               (binding['operation_id'],)).fetchone()
            if prior:
                if json.loads(prior[0]) != detail:
                    raise ValueError('checkpoint operation conflicts')
                return detail
            root = db.execute('SELECT state,boot,detail FROM root_execution').fetchone()
            if (not root or root[0] != 'paused' or root[1] != binding['boot_id']
                    or not json.loads(root[2]).get('quiescent')):
                raise ValueError('checkpoint rejection lacks paused root authority')
            db.execute('INSERT INTO checkpoint_operations VALUES(?,?)',
                       (binding['operation_id'], canonical_bytes(detail)))
        return detail

    def resume_checkpoint(self, checkpoint_id: str, *, checkpoint_sha256: str,
                          decision: Mapping[str, Any], actor: str, boot_id: str,
                          invocation: NativeInvocation, continuation_lease_id: str,
                          parent_snapshot: Mapping[str, Any], resources: Mapping[str, Any] | None = None,
                          operation_binding: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Authorize exactly one native root generation from paused/quiescent only."""
        status = self.checkpoint_status(checkpoint_id)
        if not actor or not decision or not continuation_lease_id or status["checkpoint_sha256"] != checkpoint_sha256:
            raise ValueError("explicit checkpoint decision binding required")
        for reference in status["checkpoint"]["artifacts"]:
            ResultReference(**reference).resolve(self.artifact_root)
        if invocation.source_identity is None or asdict(invocation.source_identity) != self.source_identity:
            raise ValueError("checkpoint continuation source changed")
        edge = dict(checkpoint_id=checkpoint_id, checkpoint_sha256=checkpoint_sha256,
            decision=dict(decision), actor=actor, continuation_lease_id=continuation_lease_id,
            command=list(invocation.command), effective=json.loads(invocation.effective_json),
            native_parameters=invocation.native_parameters, model_id=invocation.model_id, mode=invocation.mode,
            parent_snapshot=dict(parent_snapshot), resources=dict(resources) if resources is not None else None,
            execution_plan=invocation.execution_plan.to_dict() if invocation.execution_plan else None,
            plan_sha256=invocation.execution_plan.plan_sha256 if invocation.execution_plan else None)
        decision_payload = canonical_bytes(dict(actor=actor, decision=dict(decision), checkpoint_sha256=checkpoint_sha256))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._active(db)
            row = db.execute("SELECT state,boot,detail FROM root_execution").fetchone()
            if row and row[0] == "resume_ready" and json.loads(row[2]).get("continuation_edge") == edge:
                if operation_binding is not None:
                    prior_operation = db.execute('SELECT detail FROM checkpoint_operations WHERE operation=?',
                        (operation_binding['operation_id'],)).fetchone()
                    if not prior_operation or json.loads(prior_operation[0])['binding'] != dict(operation_binding):
                        raise ValueError('checkpoint operation conflicts with accepted generation')
                return self.root_state()
            if not row or row[0] != "paused" or row[1] != boot_id or not json.loads(row[2]).get("quiescent"):
                raise ValueError("checkpoint continuation requires same-boot paused quiescent root")
            if db.execute("SELECT 1 FROM components WHERE state IN ('running','uncertain','queued')").fetchone():
                raise ValueError("checkpoint descendants are not quiescent")
            prior = db.execute("SELECT decision FROM checkpoints WHERE checkpoint=?", (checkpoint_id,)).fetchone()
            if prior[0] is not None and prior[0] != decision_payload:
                raise ValueError("checkpoint decision conflicts")
            if db.execute("SELECT 1 FROM checkpoints WHERE decision IS NULL AND checkpoint!=?", (checkpoint_id,)).fetchone():
                raise ValueError("other checkpoints still require explicit review")
            generation = int(json.loads(row[2]).get("generation", 0)) + 1
            for item in invocation.generated_inputs:
                destination = self.artifact_root / item.relative_path
                if destination.exists() and destination.read_bytes() != item.payload:
                    raise ValueError("continuation cannot rewrite retained native input")
                item.materialize(self.artifact_root)
            detail = canonical_bytes(dict(generation=generation, continuation_edge=edge, quiescent=True))
            db.execute("UPDATE checkpoints SET decision=? WHERE checkpoint=?", (decision_payload, checkpoint_id))
            db.execute("UPDATE root_execution SET state='resume_ready',detail=?", (detail,))
            self._event(db, self.root_job_id, "checkpoint_continuation_authorized", json.loads(detail))
            if operation_binding is not None:
                if any(operation_binding.get(key) != value for key, value in {
                    'checkpoint_id': checkpoint_id, 'checkpoint_sha256': checkpoint_sha256,
                    'decision': dict(decision), 'boot_id': boot_id, 'attempt_id': self.attempt_id,
                    'original_lease_id': self.lease_id, 'continuation_lease_id': continuation_lease_id,
                }.items()):
                    raise ValueError('checkpoint operation binding conflicts')
                db.execute('INSERT INTO checkpoint_operations VALUES(?,?)',
                    (operation_binding['operation_id'], canonical_bytes(dict(binding=dict(operation_binding),
                        state='accepted', generation=generation, edge=edge))))
        return self.root_state()

    @staticmethod
    def _effective_children(db, child_ids: Sequence[str]) -> tuple[str, ...]:
        replacements = dict(db.execute("SELECT original,replacement FROM component_replacements"))
        resolved = []
        for identity in child_ids:
            seen = set()
            while identity in replacements:
                if identity in seen:
                    raise ValueError("component replacement cycle")
                seen.add(identity)
                identity = replacements[identity]
            resolved.append(identity)
        if len(set(resolved)) != len(resolved):
            raise ValueError("duplicate effective child in exact join")
        return tuple(resolved)

    def effective_children(self, child_ids: Sequence[str]) -> tuple[str, ...]:
        """Resolve authorized replacements, never edit original grouping authority."""
        with self._connect() as db:
            return self._effective_children(db, child_ids)

    def group_children(self, group_id: str) -> tuple[str, ...]:
        with self._connect() as db:
            row = db.execute("SELECT children FROM component_groups WHERE group_id=?", (group_id,)).fetchone()
            if not row:
                raise ValueError("foreign component group")
            return self._effective_children(db, json.loads(row[0]))

    def export_projection(self) -> dict[str, Any]:
        """Snapshot authenticated ledger facts after the execution owner joins writers.

        This is host projection evidence, not a scientific success receipt. Original
        requests/groups and all failed/replaced children remain auditable.
        """
        with self._connect() as db:
            db.execute('BEGIN')
            root = db.execute('SELECT state,owner,boot,detail FROM root_execution').fetchone()
            if (not root or root[0] not in {'completed', 'failed', 'cancelled', 'paused'}
                    or not json.loads(root[3]).get('quiescent')):
                raise ValueError('component projection requires proven quiescent execution')
            detail = json.loads(root[3])
            components, unexecuted = [], []
            failures = {}
            for component_id, raw_detail in db.execute(
                    "SELECT component,detail FROM component_events WHERE phase='failed' ORDER BY sequence"):
                receipt = json.loads(raw_detail).get('failure_receipt')
                if receipt is not None:
                    failures[component_id] = receipt
            snapshots = dict(db.execute('SELECT component,snapshot FROM component_native_parents'))
            for identity, request, state, result, error, owner in db.execute(
                    'SELECT component,request,state,result,error,owner FROM components ORDER BY rowid'):
                if (owner is None or identity not in snapshots
                        or state not in {'execution_finished', 'completed', 'failed', 'cancelled'}):
                    unexecuted.append(dict(component_id=identity, request=json.loads(request),
                        state=state, error=error, result=json.loads(result) if result else None))
                    continue
                sealed = json.loads(result) if result else {}
                native_result = sealed.get('result', {})
                output = native_result.get('output_dir') or json.loads(snapshots[identity]).get('output_dir')
                output_relative = None
                if output:
                    output_relative = Path(output).resolve().relative_to(self.artifact_root).as_posix()
                components.append(dict(component_id=identity, request=json.loads(request),
                    state=state, result=sealed if result else None,
                    output_relative_path=output_relative, error=error,
                    failure_receipt=failures.get(identity),
                    native_parent=json.loads(snapshots[identity])))
            groups = [dict(group_id=identity, children=json.loads(children),
                effective_children=list(self._effective_children(db, json.loads(children))))
                for identity, children in db.execute('SELECT group_id,children FROM component_groups ORDER BY rowid')]
            replacements = [dict(original=row[0], replacement=row[1], operation_id=row[2], detail=json.loads(row[3]))
                for row in db.execute('SELECT original,replacement,operation,detail FROM component_replacements ORDER BY rowid')]
            events = [dict(sequence=row[0], component_id=row[1], phase=row[2], detail=json.loads(row[3]))
                for row in db.execute('SELECT sequence,component,phase,detail FROM component_events ORDER BY sequence')]
        return dict(schema_name='bms.component-projection.v1', attempt_id=self.attempt_id,
            root_job_id=self.root_job_id, target_id=self.target_id, lease_id=self.lease_id,
            source_identity=self.source_identity,
            plan_sha256=self.plan_sha256,
            current_plan_sha256=(detail.get('continuation_edge') or {}).get('plan_sha256', self.plan_sha256),
            generation=int(detail.get('generation', 0)),
            root_state=dict(state=root[0], owner_id=root[1], boot_id=root[2], **detail),
            components=components, unprojected_components=unexecuted, groups=groups,
            replacements=replacements, events=events)

    def publish_projection(self) -> Path:
        payload = self.export_projection()
        encoded = canonical_bytes(payload)
        retained = self.artifact_root / 'component-projections' / f"generation-{payload['generation']}.json"
        if retained.exists() and retained.read_bytes() != encoded:
            raise ValueError('immutable terminal component projection conflicts')
        durable_write(retained, encoded)
        edge = payload['root_state'].get('continuation_edge') or {}
        parent_output = Path((edge.get('parent_snapshot') or {}).get('output_dir', self.artifact_root)).resolve()
        if not parent_output.is_relative_to(self.artifact_root):
            raise ValueError('component projection output escapes its attempt')
        current = parent_output / '.bms-components.json'
        if current.exists() and current.read_bytes() != encoded:
            raise ValueError('immutable native generation component projection conflicts')
        durable_write(current, encoded)
        return current

    def retry_status(self, operation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT detail FROM component_replacements WHERE operation=?", (operation_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def retry_component(self, component_id: str, *, replacement: ComponentRequest,
                        operation_id: str, actor: str, boot_id: str,
                        invocation: NativeInvocation, continuation_lease_id: str,
                        parent_snapshot: Mapping[str, Any],
                        resources: Mapping[str, Any],
                        retry_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Authorize one replacement and collector generation in this same attempt.

        The native adapter validates retry eligibility and unchanged science. The
        execution owner reacquires this target's resources and proves all former
        writers stopped. A durable edge survives response loss and owner restart;
        prior requests, results, group membership and terminal events stay intact.
        """
        if not operation_id or not actor or not continuation_lease_id or not boot_id:
            raise ValueError("explicit retry identity and execution ownership required")
        if (resources.get('execution_target_id') != self.target_id
                or retry_context is None or retry_context.get('resources') != dict(resources)):
            raise ValueError('retry compiler context must bind the same admitted target resources')
        original = self.request(component_id)
        if (not isinstance(replacement, ComponentRequest) or replacement.component_id == component_id
                or (replacement.parent_job_id, replacement.stage, replacement.required) !=
                   (original.parent_job_id, original.stage, original.required)):
            raise ValueError("replacement must preserve native lineage and requiredness")
        if (invocation.source_identity is None or asdict(invocation.source_identity) != self.source_identity
                or invocation.execution_plan is None or not invocation.execution_plan.complete):
            raise ValueError("retry requires the unchanged source and complete compiled continuation")
        if parent_snapshot.get('id') != self.root_job_id:
            raise ValueError("retry collector parent identity conflicts")
        output = Path(str(parent_snapshot.get('output_dir', ''))).resolve()
        if output == self.artifact_root or not output.is_relative_to(self.artifact_root):
            raise ValueError("retry collector output must be a fresh contained generation")
        edge: dict[str, Any] = dict(operation_id=operation_id, actor=actor, component_id=component_id,
            replacement=replacement.to_dict(), child_job_id=replacement.component_id,
            retry_context=dict(retry_context or {}),
            generated_inputs=[item.reference for item in invocation.generated_inputs],
            attempt_id=self.attempt_id, target_id=self.target_id,
            continuation_lease_id=continuation_lease_id, command=list(invocation.command),
            effective=json.loads(invocation.effective_json), native_parameters=invocation.native_parameters,
            model_id=invocation.model_id, mode=invocation.mode, parent_snapshot=dict(parent_snapshot),
            resources=dict(resources), execution_plan=invocation.execution_plan.to_dict(),
            plan_sha256=invocation.execution_plan.plan_sha256)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._active(db)
            prior = db.execute("SELECT detail FROM component_replacements WHERE operation=?", (operation_id,)).fetchone()
            if prior:
                saved = json.loads(prior[0])
                if {key: saved[key] for key in edge} != edge:
                    raise ValueError("immutable retry operation conflicts")
                return saved
            root = db.execute("SELECT state,boot,detail FROM root_execution").fetchone()
            if (not root or root[0] != 'failed' or root[1] != boot_id
                    or not json.loads(root[2]).get('quiescent')):
                raise ValueError("retry requires same-boot failed quiescent root")
            if db.execute("SELECT 1 FROM components WHERE state IN ('queued','running','uncertain')").fetchone():
                raise ValueError("retry descendants are not quiescent")
            if db.execute("SELECT state FROM components WHERE component=?", (component_id,)).fetchone()[0] != 'failed':
                raise ValueError("only a definitively failed component can be replaced")
            if db.execute("SELECT 1 FROM component_replacements WHERE original=?", (component_id,)).fetchone():
                raise ValueError("component already has an authorized replacement")
            if db.execute("SELECT 1 FROM checkpoints WHERE decision IS NULL").fetchone():
                raise ValueError("pending review cannot be bypassed by retry")
            generated = {self.artifact_root / item.relative_path for item in invocation.generated_inputs}
            if output.exists() and any(path.is_file() and path not in generated for path in output.rglob('*')):
                raise ValueError("retry collector generation already contains output")
            generation = int(json.loads(root[2]).get('generation', 0)) + 1
            edge['generation'] = generation
            # Materialization is immutable and restart-safe before the atomic edge.
            for item in invocation.generated_inputs:
                destination = self.artifact_root / item.relative_path
                if destination.exists() and destination.read_bytes() != item.payload:
                    raise ValueError("retry cannot rewrite retained native input")
                item.materialize(self.artifact_root)
            db.execute("INSERT INTO components(component,request,state) VALUES(?,?,'queued')",
                (replacement.component_id, canonical_bytes(replacement.to_dict())))
            db.execute("INSERT INTO component_replacements VALUES(?,?,?,?)",
                (component_id, replacement.component_id, operation_id, canonical_bytes(edge)))
            detail = dict(generation=generation, continuation_edge=edge, quiescent=True)
            db.execute("UPDATE root_execution SET state='resume_ready',detail=?", (canonical_bytes(detail),))
            self._event(db, replacement.component_id, 'replacement_authorized', edge)
        return edge

    def root_state(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT state,owner,boot,detail FROM root_execution").fetchone()
        return dict(state=row[0], owner_id=row[1], boot_id=row[2], **json.loads(row[3])) if row else None

    def set_root_state(self, state: str, *, owner_id: str, boot_id: str, **detail: Any) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,owner,boot,detail FROM root_execution").fetchone()
            if not row or row[1:3] != (owner_id, boot_id):
                raise ValueError("root process/boot ownership conflicts")
            if row[0] in {"completed", "failed", "cancelled", "paused"}:
                if (state, canonical_bytes(detail)) != (row[0], row[3]):
                    raise ValueError("root terminal state conflicts")
                return
            db.execute("UPDATE root_execution SET state=?,detail=?", (state, canonical_bytes(detail)))
            self._event(db, self.root_job_id, "root_" + state, {})

    def pending_checkpoints(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as db:
            ids = [row[0] for row in db.execute("SELECT checkpoint FROM checkpoints WHERE decision IS NULL ORDER BY rowid")]
        return tuple(self.checkpoint_status(identity) for identity in ids)

    def child_status(self, component_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT request,state,result,error FROM components WHERE component=?", (component_id,)).fetchone()
        if not row:
            raise ValueError("foreign component")
        request = json.loads(row[0])
        sealed = json.loads(row[2]) if row[2] else {}
        result = sealed.get("result", {})
        return dict(job_id=component_id, id=component_id, status=row[1],
                    parent_job_id=request["parent_job_id"], stage=request["stage"],
                    required=request["required"], params=request["payload"].get("params", {}),
                    output_dir=result.get("output_dir"), error=row[3], result=result,
                    references=sealed.get("references", []), payload=request["payload"])

    def children(self, parent_job_id: str | None = None, stage: str | None = None, *,
                 include_replaced: bool = False) -> tuple[dict[str, Any], ...]:
        with self._connect() as db:
            ids = [row[0] for row in db.execute("SELECT component FROM components ORDER BY rowid")]
            if not include_replaced:
                replaced = {row[0] for row in db.execute("SELECT original FROM component_replacements")}
                ids = [identity for identity in ids if identity not in replaced]
        rows = (self.child_status(identity) for identity in ids)
        return tuple(row for row in rows if (parent_job_id is None or row["parent_job_id"] == parent_job_id)
                     and (stage is None or row["stage"] == stage))

    def register_group(self, group_id: str, child_ids: Sequence[str]) -> tuple[str, ...]:
        """Seal caller-owned grouping/order, without inventing scientific grouping."""
        if not group_id or len(set(child_ids)) != len(child_ids):
            raise ValueError("group identity and unique ordered children required")
        payload = canonical_bytes(list(child_ids))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._active(db)
            for identity in child_ids:
                if not db.execute("SELECT 1 FROM components WHERE component=?", (identity,)).fetchone():
                    raise ValueError("foreign group component")
            row = db.execute("SELECT children FROM component_groups WHERE group_id=?", (group_id,)).fetchone()
            if row and row[0] != payload:
                raise ValueError("immutable component grouping conflicts")
            if not row:
                db.execute("INSERT INTO component_groups VALUES(?,?)", (group_id, payload))
                self._event(db, None, "group_sealed", dict(group_id=group_id, child_ids=list(child_ids)))
        return tuple(child_ids)

    def join_group(self, group_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as db:
            row = db.execute("SELECT children FROM component_groups WHERE group_id=?", (group_id,)).fetchone()
        if not row:
            raise ValueError("foreign component group")
        return self.join_children(json.loads(row[0]))

    def join_children(self, child_ids: Sequence[str]) -> tuple[dict[str, Any], ...]:
        self.check_active()
        if len(set(child_ids)) != len(child_ids):
            raise ValueError("duplicate child in exact join")
        rows = tuple(self.child_status(identity) for identity in self.effective_children(child_ids))
        if any(row["status"] not in {"completed", "failed", "cancelled"} for row in rows):
            raise RuntimeError("required component result join is incomplete")
        if any(row["required"] and row["status"] != "completed" for row in rows):
            raise RuntimeError("required component failed")
        for row in rows:
            for reference in row["references"]:
                ResultReference(**reference).resolve(self.artifact_root)
        self.check_active()
        return rows

    def request_cancel(self) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT cancel_requested FROM authority").fetchone()[0]:
                db.execute("UPDATE authority SET cancel_requested=1 WHERE singleton=1")
                db.execute("UPDATE components SET state='cancelled' WHERE state='queued'")
                self._event(db, None, "cancel_requested", {})

    def emit_progress(self, component_id: str, *, owner_id: str, boot_id: str,
                      phase: str, artifact: str | None = None,
                      completed_bytes: int | None = None, total_bytes: int | None = None) -> None:
        """Advisory native progress, never a scientific completion assertion."""
        if not phase or any(value is not None and (type(value) is not int or value < 0)
                            for value in (completed_bytes, total_bytes)):
            raise ValueError("progress requires a phase and nonnegative observed byte counts")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned(db, component_id, owner_id, boot_id)
            if row[0] not in {"running", "uncertain"}:
                raise ValueError("terminal component cannot emit progress")
            self._event(db, component_id, "progress", dict(phase=phase, artifact=artifact,
                completed_bytes=completed_bytes, total_bytes=total_bytes))

    def events(self, after: int = 0) -> tuple[dict[str, Any], ...]:
        with self._connect() as db:
            return tuple(dict(sequence=row[0], component_id=row[1], phase=row[2],
                              detail=json.loads(row[3]), attempt_id=self.attempt_id)
                         for row in db.execute("SELECT sequence,component,phase,detail FROM component_events WHERE sequence>? ORDER BY sequence", (after,)))

    def checkpoint(self, checkpoint_id: str, *, component_ids: Sequence[str],
                   artifacts: Sequence[ResultReference]) -> dict[str, Any]:
        if not checkpoint_id or not artifacts:
            raise ValueError("checkpoint identity and review artifacts required")
        if len(set(component_ids)) != len(component_ids):
            raise ValueError("duplicate checkpoint component")
        for identity in component_ids:
            if identity != self.root_job_id:
                self.request(identity)
        for reference in artifacts:
            if reference.component_id not in component_ids:
                raise ValueError("foreign checkpoint artifact")
            reference.resolve(self.artifact_root)
        payload = canonical_bytes(dict(schema_name="bms.component-checkpoint.v1",
            attempt_id=self.attempt_id, target_id=self.target_id, lease_id=self.lease_id,
            checkpoint_id=checkpoint_id, component_ids=list(component_ids),
            artifacts=[asdict(r) for r in artifacts]))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._active(db)
            row = db.execute("SELECT payload FROM checkpoints WHERE checkpoint=?", (checkpoint_id,)).fetchone()
            if row and row[0] != payload:
                raise ValueError("immutable checkpoint conflicts")
            if not row:
                db.execute("INSERT INTO checkpoints(checkpoint,payload) VALUES(?,?)", (checkpoint_id, payload))
                self._event(db, None, "checkpoint_waiting", dict(checkpoint_id=checkpoint_id))
        return self.checkpoint_status(checkpoint_id)

    def checkpoint_status(self, checkpoint_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT payload,decision FROM checkpoints WHERE checkpoint=?", (checkpoint_id,)).fetchone()
        if not row:
            raise ValueError("foreign checkpoint")
        return dict(checkpoint=json.loads(row[0]), checkpoint_sha256=hashlib.sha256(row[0]).hexdigest(),
                    decision=json.loads(row[1]) if row[1] else None)

    def decide_checkpoint(self, checkpoint_id: str, *, checkpoint_sha256: str,
                          decision: Mapping[str, Any], actor: str) -> dict[str, Any]:
        """Caller authenticates explicit actor and validates native selection policy."""
        if not actor or not decision:
            raise ValueError("explicit review actor and decision required")
        status = self.checkpoint_status(checkpoint_id)
        if status["checkpoint_sha256"] != checkpoint_sha256:
            raise ValueError("checkpoint decision binding conflicts")
        for reference in status["checkpoint"]["artifacts"]:
            ResultReference(**reference).resolve(self.artifact_root)
        payload = canonical_bytes(dict(actor=actor, decision=dict(decision), checkpoint_sha256=checkpoint_sha256))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._active(db)
            row = db.execute("SELECT decision FROM checkpoints WHERE checkpoint=?", (checkpoint_id,)).fetchone()
            if row[0] is not None and row[0] != payload:
                raise ValueError("explicit checkpoint decision conflicts")
            if row[0] is None:
                db.execute("UPDATE checkpoints SET decision=? WHERE checkpoint=?", (payload, checkpoint_id))
                self._event(db, None, "checkpoint_decided", dict(checkpoint_id=checkpoint_id))
        return self.checkpoint_status(checkpoint_id)
