"""Source-owned adapters exposed to the Project Manager.

Every adapter resolves an immutable native identity, re-validates the native
contract/digests, and returns a bounded receipt payload.  Filesystem paths are
never used as entity identities or persisted in receipt metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import parse_qs, urlencode

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    ApprovedNgsComparisonPanel,
    ConformationalMappingArtifact,
    ConformationalMappingRecord,
    ConformationalMappingRequest,
    Design,
    FrustraMPNNResult,
    FrustraMPNNComparison,
    FrustraMPNNGuidancePlan,
    Job,
    MdRun,
    MolBioNgsReceipt,
    NgsReferenceSetManifest,
    NgsPooledAssignmentRelease,
    NgsPooledAssignmentReleaseTarget,
    OntInstrumentRun,
    OntInstrumentRunEvent,
    RFD3LocalRedesignRequest,
)
from molbio_database import molbio_session
from molbio_models import (
    MolecularDocument,
    MolecularOperation,
    MolecularOperationInput,
    MolecularOperationOutput,
    MolecularRevision,
    PCRExperimentRevision,
    PrimerRevision,
)
from molbio_ngs_database import molbio_ngs_session_factory
from molbio_ngs_models import (
    MolBioNGSDomainStateMember,
    MolBioNGSDomainStateRevision,
    MolBioNGSEvidenceAssessment,
    MolBioNGSMemberReceipt,
    MolBioNGSReferenceResource,
    MolBioNGSReferenceRevision,
    MolBioNGSSampleRevision,
)
from scripts.rfd3_local_redesign.contract import request_sha256 as rfd3_request_sha256
from services.conformational_mapping.contracts import canonical_sha256 as cm_canonical_sha256
from services.frustrampnn.contracts import canonical_json_bytes as frustrampnn_canonical_bytes
from services.md.read_model import md_run_snapshot
from services.md.results import MDResultError, summary as md_result_summary
from services.md.state import canonical_sha256 as md_canonical_sha256
from services.ngs_alignment_sessions import AlignmentSessionError, build_alignment_sessions
from services.nucleotide_validation import canonicalize_nucleotide_sequence
from services.ont_ngs_contract import normalized_fasta_sequence_sha256
from services.ont_barcode_batches import BarcodeBatchError, get_reference_set
from services.result_contracts import resolve_result_contract
from services.sequence_qc_manifest import SequenceQcManifestError, load_sequence_qc_manifest
from services.ont_run_control import TERMINAL_RUN_STATES, _valid_terminal_manifest
from services.job_result_roots import resolve_persisted_job_result_root
from services.molbio_ngs_member_receipts import (
    ExternalMemberReceipt,
    resolve_approved_comparison_panel_receipt,
    resolve_molecular_operation_receipt,
    resolve_molecular_revision_receipt,
    resolve_ngs_job_receipt,
    resolve_ngs_result_manifest_receipt,
    resolve_ont_instrument_run_receipt,
    resolve_pcr_experiment_revision_receipt,
    resolve_primer_revision_receipt,
    resolve_sample_revision_receipt,
    resolve_state_revision_receipt,
    serialize_external_member_receipt,
)
from services.molbio_ngs_references import resolve_ngs_reference_revision_receipt
from services.molbio_ngs_evidence import resolve_evidence_assessment_receipt
from services.ngs_comparison_panels import _validated_panel_manifest
from services.ngs_molbio_source_authority import (
    SourceBuildRevisionError,
    source_build_revision,
)
from paths import get_inputs_dir, get_results_dir, resolve_runtime_data_path


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_QUERY_LENGTH = 256
_MAX_SEARCH_LIMIT = 100
_MAX_FILE_BYTES = 4 * 1024 * 1024 * 1024
_MAX_CM_RECORDS = 256
_MAX_CM_ARTIFACTS = 512
_MAX_ALIGNMENT_SESSIONS = 16
_MAX_ALIGNMENT_ARTIFACTS = 8
_NGS_MODEL_IDS = frozenset(
    {
        "nanopore",
        "ont_fastq_qc",
        "ont_plasmid_qc",
        "ont_construct_screening",
        "wf_clone_validation",
    }
)


class AdapterError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class EntityProjection:
    entity_id: str
    entity_kind: str
    label: str
    canonical_state: str
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_kind": self.entity_kind,
            "label": self.label,
            "canonical_state": self.canonical_state,
            "metadata": dict(self.metadata),
        }


class DomainAdapter(Protocol):
    adapter_id: str
    adapter_version: int
    display_name: str
    entity_kind: str
    domain_kind: str
    store_id: str

    async def search(
        self, core_session: AsyncSession, *, query: str, limit: int
    ) -> list[EntityProjection]: ...

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]: ...


class AdapterRegistry:
    def __init__(self) -> None:
        self._items: dict[str, DomainAdapter] = {}

    def register(self, adapter: DomainAdapter) -> None:
        if adapter.adapter_id in self._items:
            raise RuntimeError(f"duplicate adapter id: {adapter.adapter_id}")
        self._items[adapter.adapter_id] = adapter

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "adapter_id": item.adapter_id,
                "adapter_version": item.adapter_version,
                "display_name": item.display_name,
                "entity_kind": item.entity_kind,
                "domain_kind": item.domain_kind,
                "store_id": item.store_id,
            }
            for item in self._items.values()
        ]

    def get(self, adapter_id: str) -> DomainAdapter:
        adapter = self._items.get(adapter_id)
        if adapter is None:
            raise AdapterError("unknown_adapter", "unknown domain adapter")
        return adapter


registry = AdapterRegistry()


def _source_build_revision() -> str:
    try:
        return source_build_revision()
    except SourceBuildRevisionError as exc:
        raise AdapterError(
            "source_revision_unavailable",
            str(exc),
        ) from exc


def _search_inputs(query: str, limit: int) -> str:
    if limit < 1 or limit > _MAX_SEARCH_LIMIT:
        raise AdapterError("invalid_limit", "adapter search limit must be between 1 and 100")
    normalized = query.strip()
    if len(normalized) > _MAX_QUERY_LENGTH:
        raise AdapterError("invalid_query", "adapter search query must not exceed 256 characters")
    return normalized


def _bounded_label(value: Any, fallback: str) -> str:
    label = str(value or fallback).strip()
    return (label or fallback)[:160]


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise AdapterError("source_contract_invalid", f"native {field} is missing or invalid")
    return value


def _canonical_json_sha256(payload: Any) -> str:
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdapterError("source_contract_invalid", "native JSON is not canonicalizable") from exc
    return hashlib.sha256(encoded).hexdigest()


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _open_directory_at(parent_fd: int, component: str) -> int:
    observed = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
        raise OSError("artifact path contains an unsafe directory component")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(component, flags, dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    if not _same_file_identity(observed, opened):
        os.close(descriptor)
        raise OSError("artifact directory changed while it was opened")
    return descriptor


def _open_canonical_root(root: Path) -> int:
    if not root.is_absolute() or any(part == ".." for part in root.parts):
        raise OSError("artifact canonical root is invalid")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root.anchor, flags)
    try:
        for component in root.parts[1:]:
            child = _open_directory_at(descriptor, component)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_verified_file(
    path_value: Any,
    expected_sha256: Any = None,
    expected_size: Any = None,
    *,
    canonical_root: Path,
) -> tuple[Path, bytes]:
    digest = _sha256(expected_sha256, "artifact sha256") if expected_sha256 is not None else None
    if not isinstance(path_value, str) or not path_value.strip() or path_value != path_value.strip():
        raise AdapterError("source_contract_invalid", "native artifact path is unavailable")
    root = Path(canonical_root).expanduser()
    candidate = Path(path_value).expanduser()
    try:
        if candidate.is_absolute():
            relative = candidate.relative_to(root)
        else:
            relative = candidate
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("native artifact path traverses its canonical root")

        directory_fd = _open_canonical_root(root)
        try:
            for component in relative.parts[:-1]:
                child = _open_directory_at(directory_fd, component)
                os.close(directory_fd)
                directory_fd = child

            name = relative.parts[-1]
            observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
                raise OSError("native artifact is not a regular no-follow file")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            file_fd = os.open(name, flags, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)

        try:
            opened = os.fstat(file_fd)
            if not _same_file_identity(observed, opened) or not stat.S_ISREG(opened.st_mode):
                raise OSError("native artifact changed while it was opened")
            size = opened.st_size
            if size < 0 or size > _MAX_FILE_BYTES:
                raise AdapterError("source_artifact_unavailable", "native artifact exceeds verification bounds")
            if expected_size is not None and (
                isinstance(expected_size, bool)
                or not isinstance(expected_size, int)
                or expected_size != size
            ):
                raise AdapterError("source_digest_mismatch", "native artifact size does not match authority")

            observed_digest = hashlib.sha256()
            verified_bytes = bytearray()
            bytes_read = 0
            while bytes_read <= _MAX_FILE_BYTES:
                chunk = os.read(file_fd, min(1024 * 1024, _MAX_FILE_BYTES + 1 - bytes_read))
                if not chunk:
                    break
                bytes_read += len(chunk)
                observed_digest.update(chunk)
                verified_bytes.extend(chunk)
            if bytes_read > _MAX_FILE_BYTES:
                raise AdapterError("source_artifact_unavailable", "native artifact exceeds verification bounds")
            after_read = os.fstat(file_fd)
            if (
                not _same_file_identity(opened, after_read)
                or after_read.st_size != size
                or after_read.st_mtime_ns != opened.st_mtime_ns
                or after_read.st_ctime_ns != opened.st_ctime_ns
                or bytes_read != size
            ):
                raise AdapterError("source_artifact_unavailable", "native artifact changed during verification")
            if digest is not None and observed_digest.hexdigest() != digest:
                raise AdapterError("source_digest_mismatch", "native artifact digest does not match authority")
        finally:
            os.close(file_fd)
    except AdapterError:
        raise
    except (OSError, ValueError) as exc:
        raise AdapterError("source_artifact_unavailable", "native artifact could not be verified") from exc
    return root / relative, bytes(verified_bytes)


def _verify_file(
    path_value: Any,
    expected_sha256: Any,
    expected_size: Any = None,
    *,
    canonical_root: Path,
) -> Path:
    path, _content = _read_verified_file(
        path_value,
        expected_sha256,
        expected_size,
        canonical_root=canonical_root,
    )
    return path


def _receipt(
    adapter: DomainAdapter,
    *,
    entity_id: str,
    content_digest: str,
    contract_digest: str | None,
    reopen_uri: str,
    metadata: dict[str, Any],
    entity_revision_id: str | None = None,
) -> dict[str, Any]:
    _sha256(content_digest, "content digest")
    if contract_digest is not None:
        _sha256(contract_digest, "contract digest")
    if len(entity_id) > 512 or not reopen_uri.startswith("/") or len(reopen_uri) > 1024:
        raise AdapterError("source_contract_invalid", "native entity identity or reopen URI is invalid")
    return {
        "schema": "bms.global.external-entity-receipt.v1",
        "store_id": adapter.store_id,
        "entity_kind": adapter.entity_kind,
        "entity_id": entity_id,
        "entity_revision_id": entity_revision_id or contract_digest or content_digest,
        "content_digest": content_digest,
        "contract_digest": contract_digest,
        "source_build_revision": _source_build_revision(),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verifier_id": adapter.adapter_id,
        "availability": "available",
        "reopen_uri": reopen_uri,
        "metadata": {"adapter_version": adapter.adapter_version, **metadata},
    }


def _parse_composite_identity(entity_id: str, required: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(entity_id, str) or len(entity_id) > 512:
        raise AdapterError("invalid_entity_id", "composite entity identity is invalid")
    parsed = parse_qs(entity_id, strict_parsing=True, keep_blank_values=True)
    if set(parsed) != set(required) or any(len(parsed[key]) != 1 for key in required):
        raise AdapterError("invalid_entity_id", "composite entity identity is malformed")
    values = {key: parsed[key][0] for key in required}
    if any(not value or len(value) > 160 for value in values.values()):
        raise AdapterError("invalid_entity_id", "composite entity identity is malformed")
    return values


def _query_uri(path: str, **identity: str) -> str:
    return f"{path}?{urlencode(identity)}"


class CoreProteinResultAdapter:
    adapter_id = "bms.core.protein-result-reference.adapter.v1"
    adapter_version = 1
    display_name = "Core protein result"
    entity_kind = "design"
    domain_kind = "protein_in_silico"
    store_id = "core"

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(Design, Job).join(Job, Job.id == Design.job_id)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(
                or_(Design.id.ilike(pattern), Design.name.ilike(pattern), Job.id.ilike(pattern), Job.name.ilike(pattern))
            )
        rows = (await core_session.execute(statement.order_by(Design.created_at.desc()).limit(limit))).all()
        return [
            EntityProjection(
                entity_id=design.id,
                entity_kind=self.entity_kind,
                label=_bounded_label(design.name, design.id),
                canonical_state=str(job.status),
                metadata={"job_id": job.id, "job_status": str(job.status), "artifact_class": design.artifact_class},
            )
            for design, job in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        design = await core_session.get(Design, entity_id)
        if design is None:
            raise AdapterError("entity_not_found", "design does not exist")
        job = await core_session.get(Job, design.job_id)
        if job is None:
            raise AdapterError("source_contract_invalid", "design references a missing native job")
        contract = resolve_result_contract(review_profile_id=design.review_profile_id)
        if not contract.analysis_contract_id:
            raise AdapterError("source_contract_unavailable", "design review contract cannot be resolved")
        manifest = design.review_artifact_manifest
        if design.review_contract_source not in {"producer", "review"} or not isinstance(manifest, dict):
            raise AdapterError("source_contract_unavailable", "design has no authoritative review/producer manifest")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict) or len(artifacts) > 128:
            raise AdapterError("source_contract_unavailable", "design has no bounded authoritative artifact manifest")
        role_map = design.review_role_map if isinstance(design.review_role_map, dict) else {}
        preferred = role_map.get("result_role") or (manifest.get("roles") or {}).get("result_role")
        candidates: list[dict[str, Any]] = []
        if isinstance(preferred, str) and isinstance(artifacts.get(preferred), dict):
            candidates.append(artifacts[preferred])
        candidates.extend(
            value
            for key, value in artifacts.items()
            if key != preferred and isinstance(value, dict)
        )
        authoritative = next(
            (
                item
                for item in candidates
                if item.get("state") in {None, "ready"}
                and _SHA256_RE.fullmatch(str(item.get("sha256") or ""))
                and isinstance(item.get("path"), str)
                and Path(item["path"]).resolve() == Path(design.pdb_path).resolve()
            ),
            None,
        )
        if authoritative is None:
            raise AdapterError("source_contract_unavailable", "design manifest has no authoritative result digest")
        try:
            artifact_root = resolve_persisted_job_result_root(job)
        except (OSError, ValueError) as exc:
            raise AdapterError("source_contract_unavailable", "design result root is unavailable") from exc
        _verify_file(
            authoritative.get("path"),
            authoritative.get("sha256"),
            authoritative.get("bytes"),
            canonical_root=artifact_root,
        )
        content_digest = _sha256(authoritative.get("sha256"), "design result digest")
        return _receipt(
            self,
            entity_id=design.id,
            content_digest=content_digest,
            contract_digest=_canonical_json_sha256(manifest),
            reopen_uri=f"/designs/{job.id}",
            metadata={
                "canonical_state": str(job.status),
                "job_status": str(job.status),
                "job_id": job.id,
                "design_id": design.id,
                "artifact_class": design.artifact_class,
                "result_contract_id": contract.analysis_contract_id,
                "review_contract_version": int(design.review_contract_version or 1),
                "review_contract_source": design.review_contract_source,
            },
        )


def _rfd3_job_artifact(output_root: Path, relative_path: object) -> Path:
    if not isinstance(relative_path, str):
        raise AdapterError("source_contract_invalid", "RFD3 artifact path is missing")
    relative = Path(relative_path)
    if (
        relative.is_absolute()
        or relative.as_posix() != relative_path
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in relative_path
    ):
        raise AdapterError("source_contract_invalid", "RFD3 artifact path is unsafe")
    current = output_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise AdapterError("source_contract_invalid", "RFD3 artifact path contains a symlink")
    resolved = current.resolve()
    if not resolved.is_relative_to(output_root.resolve()) or not resolved.is_file():
        raise AdapterError("source_contract_unavailable", "RFD3 required artifact is missing")
    if resolved.stat().st_nlink != 1:
        raise AdapterError("source_contract_invalid", "RFD3 artifact must not be hard-linked")
    return resolved


def _verify_rfd3_result_manifest(
    job: Job,
    record: RFD3LocalRedesignRequest,
    expected_request_digest: str,
) -> tuple[str, int]:
    try:
        output_root = resolve_persisted_job_result_root(job)
    except (OSError, ValueError) as exc:
        raise AdapterError("source_contract_unavailable", "RFD3 job result root is unavailable") from exc
    manifest_path = output_root / "collected" / "protein_local_redesign" / "rfd3_result_manifest.json"
    try:
        _manifest_path, manifest_bytes = _read_verified_file(
            str(manifest_path),
            canonical_root=output_root,
        )
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except AdapterError as exc:
        if exc.code == "source_artifact_unavailable":
            raise AdapterError(
                "source_contract_unavailable",
                "RFD3 native result manifest is unavailable",
            ) from exc
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("source_contract_invalid", "RFD3 native result manifest is malformed") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != "bms.rfd3.local-redesign.result.v1":
        raise AdapterError("source_contract_invalid", "RFD3 native result manifest schema is invalid")
    unsigned = dict(manifest)
    claimed_digest = unsigned.pop("manifest_sha256", None)
    observed_digest = _canonical_json_sha256(unsigned)
    if (
        claimed_digest != observed_digest
        or record.result_manifest_sha256 != observed_digest
        or manifest.get("request_sha256") != expected_request_digest
    ):
        raise AdapterError("source_digest_mismatch", "RFD3 native result manifest digest or request binding is invalid")
    if manifest.get("result_contract_id") != "rfd3_local_redesign_v1":
        raise AdapterError("source_contract_invalid", "RFD3 native result contract is unsupported")

    request_input = record.request_json.get("input") if isinstance(record.request_json, dict) else None
    source_value = request_input.get("path") if isinstance(request_input, dict) else None
    if not isinstance(request_input, dict) or not isinstance(source_value, str):
        raise AdapterError("source_contract_invalid", "RFD3 immutable request has no source input")
    try:
        source_path = resolve_runtime_data_path(source_value).resolve()
    except (OSError, ValueError) as exc:
        raise AdapterError("source_contract_unavailable", "RFD3 source structure is unavailable") from exc
    source_root = next(
        (
            root
            for root in (get_inputs_dir().resolve(), get_results_dir().resolve())
            if source_path.is_relative_to(root)
        ),
        None,
    )
    if source_root is None:
        raise AdapterError("source_contract_invalid", "RFD3 source structure is outside canonical roots")
    _source_path, _source_bytes = _read_verified_file(
        str(source_path),
        request_input.get("sha256"),
        canonical_root=source_root,
    )

    artifacts = manifest.get("artifacts")
    candidates = manifest.get("candidates")
    if not isinstance(artifacts, list) or not isinstance(candidates, list) or not candidates:
        raise AdapterError("source_contract_unavailable", "RFD3 result lacks required artifacts or candidates")
    descriptor_by_path: dict[str, dict[str, Any]] = {}
    for descriptor in artifacts:
        if not isinstance(descriptor, dict):
            raise AdapterError("source_contract_invalid", "RFD3 artifact descriptor is malformed")
        role = descriptor.get("role")
        relative_path = descriptor.get("relative_path")
        storage_path = descriptor.get("storage_path")
        expected_sha = descriptor.get("sha256")
        expected_bytes = descriptor.get("bytes")
        if (
            not isinstance(role, str)
            or not role
            or not isinstance(relative_path, str)
            or relative_path in descriptor_by_path
            or not isinstance(storage_path, str)
            or not isinstance(expected_sha, str)
            or not _SHA256_RE.fullmatch(expected_sha)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 0
        ):
            raise AdapterError("source_contract_invalid", "RFD3 artifact descriptor is incomplete")
        if role == "source_structure":
            try:
                resolved = resolve_runtime_data_path(storage_path).resolve()
            except (OSError, ValueError) as exc:
                raise AdapterError("source_contract_unavailable", "RFD3 source artifact is unavailable") from exc
            if resolved != _source_path:
                raise AdapterError("source_contract_invalid", "RFD3 source artifact does not match the request")
            if len(_source_bytes) != expected_bytes or hashlib.sha256(_source_bytes).hexdigest() != expected_sha:
                raise AdapterError("source_digest_mismatch", "RFD3 source artifact descriptor disagrees with bound bytes")
        else:
            relative = Path(relative_path)
            expected_storage_path = output_root / relative
            if Path(storage_path).expanduser() != expected_storage_path:
                raise AdapterError("source_contract_invalid", "RFD3 artifact storage path is invalid")
            _verify_file(
                relative_path,
                expected_sha,
                expected_bytes,
                canonical_root=output_root,
            )
        descriptor_by_path[relative_path] = descriptor

    roles = {str(item.get("role") or "") for item in descriptor_by_path.values()}
    if not {"source_structure", "native_request"}.issubset(roles):
        raise AdapterError("source_contract_unavailable", "RFD3 result lacks source or native-request artifacts")
    seen_candidates: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise AdapterError("source_contract_invalid", "RFD3 candidate descriptor is malformed")
        candidate_id = candidate.get("candidate_id")
        candidate_artifacts = candidate.get("artifacts")
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in seen_candidates:
            raise AdapterError("source_contract_invalid", "RFD3 candidate identities are invalid")
        if not isinstance(candidate_artifacts, list) or not candidate_artifacts:
            raise AdapterError("source_contract_unavailable", "RFD3 candidate lacks required artifacts")
        if candidate.get("artifact_manifest_sha256") != _canonical_json_sha256(candidate_artifacts):
            raise AdapterError("source_digest_mismatch", "RFD3 candidate artifact manifest digest is invalid")
        candidate_roles = {
            str(item.get("role") or "") for item in candidate_artifacts if isinstance(item, dict)
        }
        if not {"structure", "native_prediction_metadata"}.issubset(candidate_roles):
            raise AdapterError("source_contract_unavailable", "RFD3 candidate lacks native structure metadata")
        for descriptor in candidate_artifacts:
            if not isinstance(descriptor, dict):
                raise AdapterError("source_contract_invalid", "RFD3 candidate artifact is malformed")
            relative_path = descriptor.get("relative_path")
            if relative_path not in descriptor_by_path or descriptor_by_path[relative_path] != descriptor:
                raise AdapterError("source_contract_invalid", "RFD3 candidate artifact is undeclared")
        seen_candidates.add(candidate_id)
    return observed_digest, len(candidates)


class Rfd3LocalRedesignAdapter:
    adapter_id = "bms.core-job.protein_local_redesign.adapter.v1"
    adapter_version = 1
    display_name = "RFD3 local redesign request/result"
    entity_kind = "rfd3_local_redesign_request"
    domain_kind = "protein_in_silico"
    store_id = "core"

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(RFD3LocalRedesignRequest, Job).join(Job, Job.id == RFD3LocalRedesignRequest.job_id)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(
                or_(
                    RFD3LocalRedesignRequest.request_id.ilike(pattern),
                    RFD3LocalRedesignRequest.job_id.ilike(pattern),
                    Job.name.ilike(pattern),
                )
            )
        rows = (await core_session.execute(statement.order_by(RFD3LocalRedesignRequest.created_at.desc()).limit(limit))).all()
        return [
            EntityProjection(
                entity_id=record.request_id,
                entity_kind=self.entity_kind,
                label=_bounded_label(job.name, record.request_id),
                canonical_state=str(record.status),
                metadata={"job_id": record.job_id, "job_status": str(job.status), "request_status": str(record.status)},
            )
            for record, job in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        record = await core_session.get(RFD3LocalRedesignRequest, entity_id)
        if record is None:
            record = (
                await core_session.execute(
                    select(RFD3LocalRedesignRequest).where(RFD3LocalRedesignRequest.job_id == entity_id).limit(2)
                )
            ).scalar_one_or_none()
        if record is None:
            raise AdapterError("entity_not_found", "RFD3 local-redesign request does not exist")
        job = await core_session.get(Job, record.job_id)
        if job is None or job.model_id != "protein_local_redesign":
            raise AdapterError("source_contract_invalid", "RFD3 request is not bound to its native job")
        expected_request_digest = rfd3_request_sha256(record.request_json)
        if expected_request_digest != _sha256(record.request_sha256, "RFD3 request digest"):
            raise AdapterError("source_digest_mismatch", "RFD3 request digest no longer matches native request")
        profile_digest = _sha256(record.profile_registry_sha256, "RFD3 profile registry digest")
        result_digest = record.result_manifest_sha256
        candidate_count = 0
        if str(record.status) == "completed":
            if result_digest is None:
                raise AdapterError("source_contract_unavailable", "completed RFD3 result has no authoritative manifest digest")
            content_digest, candidate_count = _verify_rfd3_result_manifest(job, record, expected_request_digest)
        else:
            content_digest = expected_request_digest
        return _receipt(
            self,
            entity_id=record.request_id,
            content_digest=content_digest,
            contract_digest=expected_request_digest,
            reopen_uri=f"/designs/{job.id}",
            metadata={
                "canonical_state": str(record.status),
                "job_status": str(job.status),
                "request_status": str(record.status),
                "job_id": job.id,
                "request_id": record.request_id,
                "profile_id": record.profile_id,
                "profile_registry_sha256": profile_digest,
                "result_contract_id": "rfd3_local_redesign_v1",
                "candidate_count": candidate_count,
            },
        )


class _ConformationalMappingAdapter:
    adapter_id: str
    display_name: str
    adapter_version = 1
    entity_kind = "conformational_mapping_request"
    domain_kind = "protein_in_silico"
    store_id = "core"
    backend: str

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = (
            select(ConformationalMappingRequest, Job)
            .join(Job, Job.id == ConformationalMappingRequest.job_id)
            .where(ConformationalMappingRequest.backend == self.backend)
        )
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(
                or_(ConformationalMappingRequest.request_id.ilike(pattern), Job.name.ilike(pattern))
            )
        rows = (await core_session.execute(statement.order_by(ConformationalMappingRequest.created_at.desc()).limit(limit))).all()
        return [
            EntityProjection(
                entity_id=record.request_id,
                entity_kind=self.entity_kind,
                label=_bounded_label(job.name, record.request_id),
                canonical_state=str(record.status),
                metadata={"backend": record.backend, "job_id": record.job_id, "job_status": str(job.status)},
            )
            for record, job in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        request = await core_session.get(ConformationalMappingRequest, entity_id)
        if request is None or request.backend != self.backend:
            raise AdapterError("entity_not_found", "conformational-mapping request does not exist for this adapter")
        job = await core_session.get(Job, request.job_id)
        if job is None:
            raise AdapterError("source_contract_invalid", "conformational-mapping request has no native job")
        request_json = request.request_json
        plan_json = request.coordinate_plan_json
        if not isinstance(request_json, dict) or not isinstance(plan_json, dict):
            raise AdapterError("source_contract_invalid", "conformational-mapping typed request is unavailable")
        request_payload = {key: value for key, value in request_json.items() if key != "request_sha256"}
        expected_request = cm_canonical_sha256(request_payload)
        if (
            expected_request != _sha256(request.request_sha256, "conformational-mapping request digest")
            or request_json.get("request_sha256") != expected_request
            or request_json.get("request_id") != request.request_id
            or request_json.get("backend") != request.backend
        ):
            raise AdapterError("source_digest_mismatch", "conformational-mapping request digest or identity is invalid")
        plan_payload = {key: value for key, value in plan_json.items() if key != "coordinate_plan_sha256"}
        expected_plan = cm_canonical_sha256(plan_payload)
        if (
            expected_plan != _sha256(request.coordinate_plan_sha256, "conformational-mapping coordinate-plan digest")
            or plan_json.get("coordinate_plan_sha256") != expected_plan
            or plan_json.get("request_id") != request.request_id
            or plan_json.get("request_sha256") != expected_request
        ):
            raise AdapterError("source_digest_mismatch", "conformational-mapping coordinate-plan digest is invalid")

        records = list(
            (
                await core_session.scalars(
                    select(ConformationalMappingRecord)
                    .where(ConformationalMappingRecord.request_id == request.request_id)
                    .order_by(ConformationalMappingRecord.record_type, ConformationalMappingRecord.record_key)
                    .limit(_MAX_CM_RECORDS + 1)
                )
            ).all()
        )
        artifacts = list(
            (
                await core_session.scalars(
                    select(ConformationalMappingArtifact)
                    .where(ConformationalMappingArtifact.request_id == request.request_id)
                    .order_by(ConformationalMappingArtifact.artifact_id)
                    .limit(_MAX_CM_ARTIFACTS + 1)
                )
            ).all()
        )
        if len(records) > _MAX_CM_RECORDS or len(artifacts) > _MAX_CM_ARTIFACTS:
            raise AdapterError("source_contract_unbounded", "conformational-mapping digest set exceeds adapter bounds")
        if str(request.status) == "completed" and (not records or not artifacts):
            raise AdapterError("source_contract_unavailable", "completed conformational-mapping result is incomplete")
        try:
            artifact_root = resolve_persisted_job_result_root(job)
        except (OSError, ValueError) as exc:
            raise AdapterError(
                "source_contract_unavailable",
                "conformational-mapping result root is unavailable",
            ) from exc
        record_digests: list[dict[str, str]] = []
        for record in records:
            observed = cm_canonical_sha256(record.payload_json)
            if observed != _sha256(record.content_sha256, "conformational-mapping record digest"):
                raise AdapterError("source_digest_mismatch", "conformational-mapping record digest is invalid")
            record_digests.append({"record_type": record.record_type, "record_key": record.record_key, "sha256": observed})
        artifact_digests: list[dict[str, Any]] = []
        for artifact in artifacts:
            _verify_file(
                artifact.storage_path,
                artifact.content_sha256,
                artifact.size_bytes,
                canonical_root=artifact_root,
            )
            artifact_digests.append(
                {
                    "artifact_id": artifact.artifact_id,
                    "role": artifact.role,
                    "sha256": artifact.content_sha256,
                    "size_bytes": artifact.size_bytes,
                }
            )
        digest_set = {
            "request_id": request.request_id,
            "request_sha256": expected_request,
            "coordinate_plan_sha256": expected_plan,
            "records": record_digests,
            "artifacts": artifact_digests,
        }
        content_digest = _canonical_json_sha256(digest_set)
        return _receipt(
            self,
            entity_id=request.request_id,
            content_digest=content_digest,
            contract_digest=expected_request,
            reopen_uri=f"/designs/{request.request_id}",
            metadata={
                "canonical_state": str(request.status),
                "job_status": str(job.status),
                "job_id": job.id,
                "request_id": request.request_id,
                "backend": request.backend,
                "request_sha256": expected_request,
                "coordinate_plan_sha256": expected_plan,
                "record_count": len(records),
                "artifact_count": len(artifacts),
                "digest_set_sha256": content_digest,
                "result_contract_id": request.result_contract_id,
            },
        )


class ConformationalMappingProtenixAdapter(_ConformationalMappingAdapter):
    adapter_id = "bms.cm.protenix_v2.adapter.v1"
    display_name = "Conformational Mapping — Protenix v2"
    backend = "protenix_v2_ensemble"


class ConformationalMappingConfornetsAdapter(_ConformationalMappingAdapter):
    adapter_id = "bms.cm.confornets.adapter.v1"
    display_name = "Conformational Mapping — ConforNets"
    backend = "confornets"


class MolecularDynamicsResultAdapter:
    adapter_id = "bms.md.result-reference.adapter.v1"
    adapter_version = 1
    display_name = "Molecular dynamics result"
    entity_kind = "md_result"
    domain_kind = "protein_in_silico"
    store_id = "core"

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(MdRun, Job).join(Job, Job.id == MdRun.job_id)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(or_(MdRun.job_id.ilike(pattern), Job.name.ilike(pattern), MdRun.phase.ilike(pattern)))
        rows = (await core_session.execute(statement.order_by(Job.created_at.desc()).limit(limit))).all()
        return [
            EntityProjection(
                entity_id=run.job_id,
                entity_kind=self.entity_kind,
                label=_bounded_label(job.name, run.job_id),
                canonical_state=str(run.phase),
                metadata={"job_status": str(job.status), "phase": str(run.phase), "state_version": int(run.state_version)},
            )
            for run, job in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        run = await core_session.get(MdRun, entity_id)
        job = await core_session.get(Job, entity_id)
        if run is None or job is None:
            raise AdapterError("entity_not_found", "MD run does not exist")
        request_digest = md_canonical_sha256(run.normalized_request)
        if request_digest != _sha256(run.request_sha256, "MD request digest"):
            raise AdapterError("source_digest_mismatch", "MD request digest no longer matches native request")
        try:
            snapshot = await md_run_snapshot(core_session, entity_id)
            summary = md_result_summary(job)
        except (MDResultError, OSError, ValueError) as exc:
            raise AdapterError("source_contract_unavailable", "native MD result summary could not be validated") from exc
        if snapshot.get("schema") != "bms.md.run-detail.v1" or snapshot.get("job_id") != entity_id:
            raise AdapterError("source_contract_invalid", "native MD snapshot is invalid")
        aggregate_digest = _sha256(summary.get("aggregate_manifest_sha256"), "MD aggregate manifest digest")
        provenance = job.provenance if isinstance(job.provenance, dict) else {}
        native_md = provenance.get("md") if isinstance(provenance.get("md"), dict) else {}
        if native_md.get("aggregate_manifest_sha256") != aggregate_digest:
            raise AdapterError("source_digest_mismatch", "MD summary is not bound to accepted native provenance")
        replica_set_digest = _sha256(native_md.get("replica_manifest_set_sha256"), "MD replica manifest-set digest")
        if summary.get("source") != "validated_job_owned_manifests" or summary.get("bounded") is not True:
            raise AdapterError("source_contract_invalid", "MD summary is not authoritative and bounded")
        content_digest = _canonical_json_sha256(
            {
                "job_id": entity_id,
                "request_sha256": request_digest,
                "aggregate_manifest_sha256": aggregate_digest,
                "replica_manifest_set_sha256": replica_set_digest,
                "state_version": int(run.state_version),
            }
        )
        return _receipt(
            self,
            entity_id=entity_id,
            content_digest=content_digest,
            contract_digest=request_digest,
            reopen_uri=f"/designs/{entity_id}",
            metadata={
                "canonical_state": str(run.phase),
                "job_status": str(job.status),
                "result_state": str(summary.get("result_state") or native_md.get("result_state") or "unavailable"),
                "phase": str(run.phase),
                "state_version": int(run.state_version),
                "aggregate_manifest_sha256": aggregate_digest,
                "replica_manifest_set_sha256": replica_set_digest,
                "replica_count": int(summary.get("replica_count") or 0),
                "artifact_count": int(summary.get("artifact_count") or 0),
                "result_contract_id": "molecular_dynamics_v1",
            },
        )


class FrustraMpnnResultAdapter:
    adapter_id = "bms.frustrampnn.result-reference.adapter.v1"
    adapter_version = 1
    display_name = "FrustraMPNN result"
    entity_kind = "frustrampnn_result"
    domain_kind = "protein_in_silico"
    store_id = "core"

    @staticmethod
    def _entity_id(parent_job_id: str, invocation_id: str) -> str:
        return urlencode({"parent_job_id": parent_job_id, "invocation_id": invocation_id})

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(FrustraMPNNResult)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(
                or_(
                    FrustraMPNNResult.parent_job_id.ilike(pattern),
                    FrustraMPNNResult.invocation_id.ilike(pattern),
                    FrustraMPNNResult.candidate_id.ilike(pattern),
                )
            )
        rows = list((await core_session.scalars(statement.order_by(FrustraMPNNResult.created_at.desc()).limit(limit))).all())
        return [
            EntityProjection(
                entity_id=self._entity_id(row.parent_job_id, row.invocation_id),
                entity_kind=self.entity_kind,
                label=_bounded_label(row.candidate_id, row.invocation_id),
                canonical_state=str((row.terminal_result_json or {}).get("status") or "completed"),
                metadata={"parent_job_id": row.parent_job_id, "invocation_id": row.invocation_id, "candidate_id": row.candidate_id},
            )
            for row in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        identity = _parse_composite_identity(entity_id, ("parent_job_id", "invocation_id"))
        row = await core_session.get(FrustraMPNNResult, (identity["parent_job_id"], identity["invocation_id"]))
        if row is None:
            raise AdapterError("entity_not_found", "FrustraMPNN result does not exist")
        job = await core_session.get(Job, row.parent_job_id)
        if job is None:
            raise AdapterError("source_contract_invalid", "FrustraMPNN result has no native parent job")
        manifest_sha = hashlib.sha256(frustrampnn_canonical_bytes(row.manifest_json)).hexdigest()
        summary_sha = hashlib.sha256(frustrampnn_canonical_bytes(row.summary_json)).hexdigest()
        if manifest_sha != _sha256(row.manifest_sha256, "FrustraMPNN manifest digest"):
            raise AdapterError("source_digest_mismatch", "FrustraMPNN manifest digest is invalid")
        if summary_sha != _sha256(row.summary_sha256, "FrustraMPNN summary digest"):
            raise AdapterError("source_digest_mismatch", "FrustraMPNN summary digest is invalid")
        request_sha = _sha256(row.request_sha256, "FrustraMPNN request digest")
        source_sha = _sha256(row.source_artifact_sha256, "FrustraMPNN source artifact digest")
        manifest = row.manifest_json
        if not isinstance(manifest, dict) or any(
            (
                manifest.get("parent_job_id") != row.parent_job_id,
                manifest.get("invocation_id") != row.invocation_id,
                manifest.get("candidate_id") != row.candidate_id,
                manifest.get("request_sha256") != request_sha,
                manifest.get("source_sha256") != source_sha,
            )
        ):
            raise AdapterError("source_digest_mismatch", "FrustraMPNN manifest identity is not bound to native result")
        terminal = row.terminal_result_json if isinstance(row.terminal_result_json, dict) else {}
        return _receipt(
            self,
            entity_id=entity_id,
            content_digest=manifest_sha,
            contract_digest=request_sha,
            reopen_uri=_query_uri(
                f"/designs/{row.parent_job_id}",
                frustrampnn_invocation_id=row.invocation_id,
            ),
            metadata={
                "canonical_state": str(terminal.get("status") or "completed"),
                "job_status": str(job.status),
                "parent_job_id": row.parent_job_id,
                "invocation_id": row.invocation_id,
                "candidate_id": row.candidate_id,
                "manifest_sha256": manifest_sha,
                "summary_sha256": summary_sha,
                "source_artifact_sha256": source_sha,
                "result_contract_id": "frustrampnn_result_v1",
            },
        )


class FrustraMpnnComparisonAdapter:
    adapter_id = "bms.frustrampnn.comparison-reference.adapter.v1"
    adapter_version = 1
    display_name = "FrustraMPNN comparison"
    entity_kind = "frustrampnn_comparison"
    domain_kind = "protein_in_silico"
    store_id = "core"

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(FrustraMPNNComparison)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(
                or_(
                    FrustraMPNNComparison.comparison_id.ilike(pattern),
                    FrustraMPNNComparison.reference_parent_job_id.ilike(pattern),
                    FrustraMPNNComparison.target_parent_job_id.ilike(pattern),
                )
            )
        rows = list((await core_session.scalars(
            statement.order_by(FrustraMPNNComparison.created_at.desc()).limit(limit)
        )).all())
        return [
            EntityProjection(
                entity_id=row.comparison_id,
                entity_kind=self.entity_kind,
                label=f"{row.reference_parent_job_id} → {row.target_parent_job_id}"[:160],
                canonical_state=str(row.status),
                metadata={"compatibility_status": (row.payload_json or {}).get("compatibility", {}).get("status")},
            )
            for row in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        row = await core_session.get(FrustraMPNNComparison, entity_id)
        if row is None or not isinstance(row.payload_json, dict):
            raise AdapterError("entity_not_found", "FrustraMPNN comparison does not exist")
        declared = _sha256(row.comparison_sha256, "FrustraMPNN comparison digest")
        if _canonical_json_sha256(row.payload_json) != declared:
            raise AdapterError("source_digest_mismatch", "FrustraMPNN comparison digest is invalid")
        compatibility = row.payload_json.get("compatibility")
        if not isinstance(compatibility, dict) or compatibility.get("status") not in {"comparable", "incompatible"}:
            raise AdapterError("source_contract_invalid", "FrustraMPNN compatibility receipt is invalid")
        return _receipt(
            self,
            entity_id=row.comparison_id,
            content_digest=declared,
            contract_digest=_sha256(row.configuration_sha256 or declared, "FrustraMPNN comparison contract digest"),
            reopen_uri=_query_uri(
                f"/designs/{row.reference_parent_job_id}",
                frustrampnn_comparison_id=row.comparison_id,
                frustrampnn_invocation_id=row.reference_invocation_id,
            ),
            metadata={
                "canonical_state": str(row.status),
                "compatibility": compatibility,
                "reference_parent_job_id": row.reference_parent_job_id,
                "reference_invocation_id": row.reference_invocation_id,
                "target_parent_job_id": row.target_parent_job_id,
                "target_invocation_id": row.target_invocation_id,
                "native_lineage": [
                    {
                        "relation": "compares",
                        "entity_kind": "frustrampnn_result",
                        "entity_id": FrustraMpnnResultAdapter._entity_id(
                            row.reference_parent_job_id,
                            row.reference_invocation_id,
                        ),
                    },
                    {
                        "relation": "compares",
                        "entity_kind": "frustrampnn_result",
                        "entity_id": FrustraMpnnResultAdapter._entity_id(
                            row.target_parent_job_id,
                            row.target_invocation_id,
                        ),
                    },
                ],
                "result_contract_id": "frustrampnn_comparison_v1",
            },
        )


class FrustraMpnnGuidanceAdapter:
    adapter_id = "bms.frustrampnn.guidance-reference.adapter.v1"
    adapter_version = 1
    display_name = "FrustraMPNN guidance plan"
    entity_kind = "frustrampnn_guidance"
    domain_kind = "protein_in_silico"
    store_id = "core"

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(FrustraMPNNGuidancePlan)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(
                or_(
                    FrustraMPNNGuidancePlan.guidance_id.ilike(pattern),
                    FrustraMPNNGuidancePlan.source_parent_job_id.ilike(pattern),
                    FrustraMPNNGuidancePlan.source_comparison_id.ilike(pattern),
                )
            )
        rows = list((await core_session.scalars(
            statement.order_by(FrustraMPNNGuidancePlan.created_at.desc()).limit(limit)
        )).all())
        return [
            EntityProjection(
                entity_id=row.guidance_id,
                entity_kind=self.entity_kind,
                label=_bounded_label(row.source_parent_job_id, row.guidance_id),
                canonical_state="immutable",
                metadata={"source_comparison_id": row.source_comparison_id},
            )
            for row in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        row = await core_session.get(FrustraMPNNGuidancePlan, entity_id)
        if row is None or not isinstance(row.payload_json, dict):
            raise AdapterError("entity_not_found", "FrustraMPNN guidance plan does not exist")
        declared = _sha256(row.guidance_sha256, "FrustraMPNN guidance digest")
        if _canonical_json_sha256(row.payload_json) != declared:
            raise AdapterError("source_digest_mismatch", "FrustraMPNN guidance digest is invalid")
        lineage = []
        if row.source_comparison_id:
            lineage.append({"relation": "derived_from", "entity_kind": "frustrampnn_comparison", "entity_id": row.source_comparison_id})
        if row.source_invocation_id and row.source_parent_job_id:
            lineage.append({
                "relation": "derived_from",
                "entity_kind": "frustrampnn_result",
                "entity_id": FrustraMpnnResultAdapter._entity_id(row.source_parent_job_id, row.source_invocation_id),
                "source_landscape_sha256": _sha256(row.source_landscape_sha256, "FrustraMPNN source landscape digest"),
            })
        reopen_params: dict[str, Any] = {
            "frustrampnn_guidance_id": row.guidance_id,
            "frustrampnn_invocation_id": row.source_invocation_id,
        }
        if row.source_comparison_id:
            reopen_params["frustrampnn_comparison_id"] = row.source_comparison_id
        return _receipt(
            self,
            entity_id=row.guidance_id,
            content_digest=declared,
            contract_digest=_sha256(row.configuration_sha256 or declared, "FrustraMPNN guidance contract digest"),
            reopen_uri=_query_uri(
                f"/designs/{row.source_parent_job_id}" if row.source_parent_job_id else "/designs",
                **reopen_params,
            ),
            metadata={
                "canonical_state": "immutable",
                "source_comparison_id": row.source_comparison_id,
                "source_parent_job_id": row.source_parent_job_id,
                "source_invocation_id": row.source_invocation_id,
                "native_lineage": lineage,
                "result_contract_id": "frustrampnn_guidance_v1",
            },
        )


class MolBioRevisionAdapter:
    adapter_id = "bms.molbio.revision-reference.adapter.v1"
    adapter_version = 1
    display_name = "Immutable molecular revision"
    entity_kind = "molbio_revision"
    domain_kind = "ngs_molbio"
    store_id = "molbio"

    def __init__(self, *, molbio_session_factory: Callable[[], Any] = molbio_session):
        self._molbio_session_factory = molbio_session_factory

    @staticmethod
    def _entity_id(sequence_id: str, revision_id: str) -> str:
        return urlencode({"sequence_id": sequence_id, "revision_id": revision_id})

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        del core_session
        normalized = _search_inputs(query, limit)
        async with self._molbio_session_factory() as session:
            statement = select(MolecularRevision)
            if normalized:
                pattern = f"%{normalized}%"
                statement = statement.where(
                    or_(MolecularRevision.id.ilike(pattern), MolecularRevision.document_id.ilike(pattern))
                )
            rows = list((await session.scalars(statement.order_by(MolecularRevision.created_at.desc()).limit(limit))).all())
        return [
            EntityProjection(
                entity_id=self._entity_id(row.document_id, row.id),
                entity_kind=self.entity_kind,
                label=_bounded_label((row.snapshot or {}).get("name"), row.id),
                canonical_state="immutable",
                metadata={"sequence_id": row.document_id, "revision_id": row.id, "revision_number": row.revision_number},
            )
            for row in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        del core_session
        identity = _parse_composite_identity(entity_id, ("sequence_id", "revision_id"))
        async with self._molbio_session_factory() as session:
            revision = await session.get(MolecularRevision, identity["revision_id"])
        if revision is None or revision.document_id != identity["sequence_id"]:
            raise AdapterError("entity_not_found", "immutable molecular revision does not exist")
        snapshot = revision.snapshot
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("sequence"), str):
            raise AdapterError("source_contract_invalid", "molecular revision has no canonical sequence snapshot")
        try:
            sequence = canonicalize_nucleotide_sequence(
                snapshot["sequence"],
                str(snapshot.get("sequence_type") or "dna").lower(),
                allow_empty=False,
            )
        except ValueError as exc:
            raise AdapterError("source_contract_invalid", "molecular revision sequence is invalid") from exc
        digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
        if digest != _sha256(revision.content_sha256, "molecular revision content digest") or len(sequence) != revision.content_length:
            raise AdapterError("source_digest_mismatch", "molecular revision digest or length is invalid")
        return _receipt(
            self,
            entity_id=entity_id,
            content_digest=digest,
            contract_digest=digest,
            reopen_uri=_query_uri("/designer", sequence_id=revision.document_id, revision_id=revision.id),
            metadata={
                "canonical_state": "immutable",
                "sequence_id": revision.document_id,
                "revision_id": revision.id,
                "revision_number": int(revision.revision_number),
                "content_length": int(revision.content_length),
                "change_kind": revision.change_kind,
                "result_contract_id": "molbio_immutable_revision_v1",
            },
        )


class MolBioConstructAdapter(MolBioRevisionAdapter):
    adapter_id = "bms.molbio.construct-reference.adapter.v1"
    display_name = "Immutable MolBio construct revision"
    entity_kind = "molbio_construct_revision"

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        identity = _parse_composite_identity(entity_id, ("sequence_id", "revision_id"))
        async with self._molbio_session_factory() as session:
            document = await session.get(MolecularDocument, identity["sequence_id"])
            revision = await session.get(MolecularRevision, identity["revision_id"])
            if document is None or revision is None or revision.document_id != document.id:
                raise AdapterError("entity_not_found", "molecular construct revision does not exist")
            snapshot = revision.snapshot if isinstance(revision.snapshot, dict) else {}
            is_circular = bool(snapshot.get("is_circular"))
            operation_id = revision.operation_id
            document_kind = str(document.document_kind)
            if document_kind not in {"dna", "rna"} or (not is_circular and operation_id is None):
                raise AdapterError("source_contract_invalid", "molecular revision is not classified as a construct")
        receipt = await super().verify(core_session, entity_id)
        receipt["metadata"].update({
            "document_kind": document_kind,
            "is_circular": is_circular,
            "operation_id": operation_id,
            "result_contract_id": "molbio_construct_revision_v1",
        })
        receipt["metadata"]["native_lineage"] = [
            {
                "relation": "is_revision_of",
                "entity_kind": "molbio_sequence",
                "entity_id": receipt["metadata"]["sequence_id"],
            }
        ]
        return receipt


class MolBioOperationAdapter:
    adapter_id = "bms.molbio.operation-reference.adapter.v1"
    adapter_version = 1
    display_name = "Immutable MolBio operation"
    entity_kind = "molbio_operation"
    domain_kind = "ngs_molbio"
    store_id = "molbio"

    def __init__(self, *, molbio_session_factory: Callable[[], Any] = molbio_session):
        self._molbio_session_factory = molbio_session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        del core_session
        normalized = _search_inputs(query, limit)
        async with self._molbio_session_factory() as session:
            statement = select(MolecularOperation)
            if normalized:
                pattern = f"%{normalized}%"
                statement = statement.where(
                    or_(
                        MolecularOperation.id.ilike(pattern),
                        MolecularOperation.operation_kind.ilike(pattern),
                    )
                )
            rows = list((await session.scalars(statement.order_by(MolecularOperation.created_at.desc()).limit(limit))).all())
        return [
            EntityProjection(
                entity_id=row.id,
                entity_kind=self.entity_kind,
                label=_bounded_label(row.operation_kind, row.id),
                canonical_state=str(row.status),
                metadata={"operation_kind": row.operation_kind, "implementation": row.implementation},
            )
            for row in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        del core_session
        async with self._molbio_session_factory() as session:
            operation = await session.get(MolecularOperation, entity_id)
            if operation is None:
                raise AdapterError("entity_not_found", "immutable molecular operation does not exist")
            inputs = list((await session.scalars(
                select(MolecularOperationInput)
                .where(MolecularOperationInput.operation_id == entity_id)
                .order_by(MolecularOperationInput.position)
            )).all())
            outputs = list((await session.scalars(
                select(MolecularOperationOutput)
                .where(MolecularOperationOutput.operation_id == entity_id)
                .order_by(MolecularOperationOutput.position)
            )).all())
            revision_ids = sorted({row.revision_id for row in [*inputs, *outputs]})
            revisions = {
                row.id: row
                for row in (
                    await session.scalars(select(MolecularRevision).where(MolecularRevision.id.in_(revision_ids)))
                ).all()
            } if revision_ids else {}
        if len(revisions) != len(revision_ids):
            raise AdapterError("source_contract_invalid", "molecular operation lineage references unavailable revisions")
        lineage = [
            {
                "relation": "uses_input",
                "role": row.role,
                "position": row.position,
                "entity_kind": "molbio_revision",
                "entity_id": MolBioRevisionAdapter._entity_id(revisions[row.revision_id].document_id, row.revision_id),
                "content_digest": _sha256(revisions[row.revision_id].content_sha256, "molecular revision digest"),
            }
            for row in inputs
        ] + [
            {
                "relation": "produced",
                "role": row.role,
                "position": row.position,
                "entity_kind": "molbio_revision",
                "entity_id": MolBioRevisionAdapter._entity_id(revisions[row.revision_id].document_id, row.revision_id),
                "content_digest": _sha256(revisions[row.revision_id].content_sha256, "molecular revision digest"),
            }
            for row in outputs
        ]
        payload = {
            "operation_id": operation.id,
            "operation_kind": operation.operation_kind,
            "implementation": operation.implementation,
            "implementation_version": operation.implementation_version,
            "status": operation.status,
            "parameters": operation.parameters,
            "warnings": operation.warnings,
            "provenance": operation.provenance,
            "lineage": lineage,
        }
        digest = _canonical_json_sha256(payload)
        contract_digest = operation.request_fingerprint or digest
        contract_digest = _sha256(contract_digest, "molecular operation contract digest")
        first_output = outputs[0] if outputs else None
        if first_output is not None:
            output_revision = revisions[first_output.revision_id]
            reopen_uri = _query_uri(
                "/designer",
                sequence_id=output_revision.document_id,
                revision_id=output_revision.id,
                operation_id=operation.id,
            )
        else:
            reopen_uri = _query_uri("/designer", operation_id=operation.id)
        return _receipt(
            self,
            entity_id=operation.id,
            content_digest=digest,
            contract_digest=contract_digest,
            reopen_uri=reopen_uri,
            metadata={
                "canonical_state": str(operation.status),
                "operation_kind": operation.operation_kind,
                "implementation": operation.implementation,
                "input_count": len(inputs),
                "output_count": len(outputs),
                "native_lineage": lineage,
                "result_contract_id": "molbio_operation_v1",
            },
        )


class NgsExpectedReferenceReceiptAdapter:
    adapter_id = "bms.ngs.expected-reference-receipt.adapter.v1"
    adapter_version = 1
    display_name = "MolBio NGS expected-reference receipt"
    entity_kind = "ngs_expected_reference_receipt"
    domain_kind = "ngs_molbio"
    store_id = "molbio"

    def __init__(self, *, molbio_session_factory: Callable[[], Any] = molbio_session):
        self._molbio_session_factory = molbio_session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(MolBioNgsReceipt)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(
                or_(
                    MolBioNgsReceipt.id.ilike(pattern),
                    MolBioNgsReceipt.sequence_id.ilike(pattern),
                    MolBioNgsReceipt.revision_id.ilike(pattern),
                )
            )
        rows = list((await core_session.scalars(statement.order_by(MolBioNgsReceipt.issued_at.desc()).limit(limit))).all())
        now = datetime.utcnow()
        return [
            EntityProjection(
                entity_id=row.id,
                entity_kind=self.entity_kind,
                label=f"{row.sequence_id} · {row.revision_id}"[:160],
                canonical_state=("consumed" if row.consumed_at is not None else "expired" if row.expires_at <= now else "available"),
                metadata={"sequence_id": row.sequence_id, "revision_id": row.revision_id, "consumed_job_id": row.consumed_job_id},
            )
            for row in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        receipt = await core_session.get(MolBioNgsReceipt, entity_id)
        if receipt is None:
            raise AdapterError("entity_not_found", "NGS expected-reference receipt does not exist")
        revision_sha = _sha256(receipt.revision_sha256, "receipt revision digest")
        reference_sha = _sha256(receipt.reference_snapshot_sha256, "expected-reference snapshot digest")
        path = _verify_file(
            receipt.reference_snapshot_path,
            reference_sha,
            canonical_root=get_inputs_dir() / "molbio_ngs_receipts" / receipt.id,
        )
        try:
            normalized_sha = normalized_fasta_sequence_sha256(path)
        except (OSError, ValueError) as exc:
            raise AdapterError("source_contract_invalid", "expected-reference FASTA is invalid") from exc
        if normalized_sha != revision_sha:
            raise AdapterError("source_digest_mismatch", "expected-reference sequence is not bound to revision digest")
        async with self._molbio_session_factory() as session:
            revision = await session.get(MolecularRevision, receipt.revision_id)
        if revision is None or revision.document_id != receipt.sequence_id or revision.content_sha256 != revision_sha:
            raise AdapterError("source_digest_mismatch", "expected-reference receipt is not bound to native revision")
        now = datetime.utcnow()
        state = "consumed" if receipt.consumed_at is not None else "expired" if receipt.expires_at <= now else "available"
        return _receipt(
            self,
            entity_id=receipt.id,
            content_digest=reference_sha,
            contract_digest=revision_sha,
            reopen_uri=_query_uri(
                "/designer",
                sequence_id=receipt.sequence_id,
                revision_id=receipt.revision_id,
                receipt_id=receipt.id,
            ),
            metadata={
                "canonical_state": state,
                "receipt_id": receipt.id,
                "sequence_id": receipt.sequence_id,
                "revision_id": receipt.revision_id,
                "revision_sha256": revision_sha,
                "consumed_job_id": receipt.consumed_job_id,
                "result_contract_id": "molbio_ngs_expected_reference_v2",
            },
        )


class NgsReferenceSetAdapter:
    adapter_id = "bms.ngs.reference-set-reference.adapter.v1"
    adapter_version = 1
    display_name = "Pooled NGS reference set"
    entity_kind = "ngs_reference_set"
    domain_kind = "ngs_molbio"
    store_id = "core"

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(NgsReferenceSetManifest)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(
                or_(
                    NgsReferenceSetManifest.id.ilike(pattern),
                    NgsReferenceSetManifest.source_job_id.ilike(pattern),
                    NgsReferenceSetManifest.target_workflow.ilike(pattern),
                )
            )
        rows = list((await core_session.scalars(statement.order_by(NgsReferenceSetManifest.created_at.desc()).limit(limit))).all())
        return [
            EntityProjection(
                entity_id=row.id,
                entity_kind=self.entity_kind,
                label=f"{row.target_workflow} · {row.id}"[:160],
                canonical_state="immutable",
                metadata={"source_job_id": row.source_job_id, "mode": row.mode, "target_workflow": row.target_workflow},
            )
            for row in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        row = await core_session.get(NgsReferenceSetManifest, entity_id)
        if row is None:
            raise AdapterError("entity_not_found", "NGS reference set does not exist")
        try:
            native = await get_reference_set(
                core_session,
                source_job_id=row.source_job_id,
                reference_set_id=row.id,
            )
        except (BarcodeBatchError, OSError, ValueError) as exc:
            raise AdapterError("source_contract_invalid", "NGS reference-set native validation failed") from exc
        digest = _sha256(native.get("manifest_sha256"), "NGS reference-set manifest digest")
        if digest != row.manifest_sha256 or native.get("reference_set_id") != row.id:
            raise AdapterError("source_digest_mismatch", "NGS reference-set authority changed")
        payload = native.get("manifest")
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list) or not entries or len(entries) > 384:
            raise AdapterError("source_contract_invalid", "NGS reference-set cardinality is invalid")
        return _receipt(
            self,
            entity_id=row.id,
            content_digest=digest,
            contract_digest=digest,
            reopen_uri=_query_uri("/ngs", reference_set_id=row.id, source_job_id=row.source_job_id),
            metadata={
                "canonical_state": "immutable",
                "source_job_id": row.source_job_id,
                "mode": row.mode,
                "target_workflow": row.target_workflow,
                "entry_count": len(entries),
                "manifest_sha256": digest,
                "result_contract_id": "ngs_reference_set_v1",
            },
        )


def _sequence_qc_manifest_path(job: Job) -> Path:
    try:
        root = resolve_persisted_job_result_root(job)
    except ValueError as exc:
        raise AdapterError("source_contract_invalid", "NGS job result root is invalid") from exc
    for relative in (
        Path("verification/qc_manifest.json"),
        Path("fastq_qc/qc_manifest.json"),
        Path("qc_manifest.json"),
    ):
        candidate = root / relative
        if candidate.is_symlink():
            raise AdapterError("source_contract_invalid", "sequence-QC manifest is unsafe")
        if candidate.is_file():
            return candidate
    raise AdapterError("source_contract_unavailable", "native sequence-QC manifest is unavailable")


class SequenceQcReferenceAdapter:
    adapter_id = "bms.ngs.sequence-qc-reference.adapter.v1"
    adapter_version = 1
    display_name = "Sequence-QC job manifest"
    entity_kind = "sequence_qc_job"
    domain_kind = "ngs_molbio"
    store_id = "core"
    require_qc_model = True

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(Job).where(Job.model_id.in_(_NGS_MODEL_IDS))
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(or_(Job.id.ilike(pattern), Job.name.ilike(pattern), Job.model_id.ilike(pattern)))
        jobs = list((await core_session.scalars(statement.order_by(Job.created_at.desc()).limit(limit))).all())
        return [
            EntityProjection(
                entity_id=job.id,
                entity_kind=self.entity_kind,
                label=_bounded_label(job.name, job.id),
                canonical_state=str(job.status),
                metadata={"job_status": str(job.status), "model_id": job.model_id},
            )
            for job in jobs
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        job = await core_session.get(Job, entity_id)
        if job is None or (self.require_qc_model and job.model_id not in _NGS_MODEL_IDS):
            raise AdapterError("entity_not_found", "sequence-QC job does not exist")
        manifest_path = _sequence_qc_manifest_path(job)
        job_params = dict(job.params or {})
        expected_workflow_id = job_params.get("ont_workflow_id") or job_params.get("workflow_id")
        if not isinstance(expected_workflow_id, str) or not expected_workflow_id:
            raise AdapterError("source_contract_invalid", "sequence-QC Job has no canonical workflow_id")
        expected_input_mode = job_params.get("ont_input_mode") or job_params.get("input_mode")
        if not isinstance(expected_input_mode, str) or not expected_input_mode:
            raise AdapterError("source_contract_invalid", "sequence-QC Job has no canonical input_mode")
        try:
            result_root = resolve_persisted_job_result_root(job)
            _, manifest_bytes = _read_verified_file(
                str(manifest_path),
                None,
                canonical_root=result_root,
            )
            if len(manifest_bytes) > 10 * 1024 * 1024:
                raise AdapterError("source_contract_unbounded", "sequence-QC manifest exceeds adapter bounds")
            raw = json.loads(manifest_bytes.decode("utf-8"))
            native = load_sequence_qc_manifest(
                manifest_path,
                expected_job_id=job.id,
                expected_workflow_id=expected_workflow_id,
                expected_input_mode=expected_input_mode,
                expected_analysis_status=str(job.status),
                manifest_document=raw,
            )
        except AdapterError:
            raise
        except (OSError, json.JSONDecodeError, SequenceQcManifestError) as exc:
            raise AdapterError("source_contract_invalid", "native sequence-QC manifest validation failed") from exc
        if not isinstance(raw, dict):
            raise AdapterError("source_contract_invalid", "native sequence-QC manifest is invalid")
        declared = _sha256(raw.get("manifest_sha256"), "sequence-QC manifest digest")
        unhashed = dict(raw)
        unhashed.pop("manifest_sha256", None)
        if _canonical_json_sha256(unhashed) != declared:
            raise AdapterError("source_digest_mismatch", "sequence-QC manifest self-digest is invalid")
        if native.get("schema") != "sequence_qc.manifest.v1":
            raise AdapterError("source_contract_invalid", "sequence-QC manifest schema is unsupported")
        artifacts = native.get("artifacts")
        if not isinstance(artifacts, list) or len(artifacts) > 256:
            raise AdapterError("source_contract_unbounded", "sequence-QC artifact set exceeds adapter bounds")
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise AdapterError("source_contract_invalid", "sequence-QC artifact descriptor is invalid")
            if artifact.get("required") is True and artifact.get("exists") is not True:
                raise AdapterError("source_artifact_unavailable", "required sequence-QC artifact is unavailable")
            if artifact.get("exists") is True:
                if artifact.get("integrity_valid") is not True:
                    raise AdapterError("source_digest_mismatch", "sequence-QC artifact integrity is invalid")
        return _receipt(
            self,
            entity_id=job.id,
            content_digest=declared,
            contract_digest=declared,
            reopen_uri=_query_uri("/ngs", job_id=job.id),
            metadata={
                "canonical_state": str(job.status),
                "job_status": str(job.status),
                "model_id": job.model_id,
                "workflow_id": native.get("workflow_id"),
                "input_mode": native.get("input_mode"),
                "analysis_status": native.get("analysis_status"),
                "manifest_schema": native.get("schema"),
                "artifact_count": len(artifacts),
                "manifest_sha256": declared,
                "result_contract_id": "sequence_qc_manifest_v1",
            },
        )


class NgsAlignmentViewerReferenceAdapter:
    adapter_id = "bms.ngs.alignment-viewer-reference.adapter.v1"
    adapter_version = 1
    display_name = "NGS alignment viewer source job"
    entity_kind = "ngs_alignment_job"
    domain_kind = "ngs_molbio"
    store_id = "core"

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        return await SequenceQcReferenceAdapter().search(core_session, query=query, limit=limit)

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        job = await core_session.get(Job, entity_id)
        if job is None or job.model_id not in _NGS_MODEL_IDS:
            raise AdapterError("entity_not_found", "NGS alignment source job does not exist")
        try:
            sessions = build_alignment_sessions(
                job.id,
                source_reference_sha256=str(job.params.get("reference_sequence_sha256") or ""),
                workflow_id=str(job.params.get("ont_workflow_id") or job.params.get("workflow_id") or ""),
                input_mode=str(job.params.get("ont_input_mode") or job.params.get("input_mode") or ""),
                job_output_dir=job.child_output_dir or job.output_dir,
            )
        except (AlignmentSessionError, OSError, ValueError) as exc:
            raise AdapterError("source_contract_unavailable", "native NGS alignment sessions could not be validated") from exc
        if len(sessions) > _MAX_ALIGNMENT_SESSIONS:
            raise AdapterError("source_contract_unbounded", "NGS alignment session set exceeds adapter bounds")
        digest_set: list[dict[str, Any]] = []
        for session in sessions:
            if session.get("ready") is not True:
                continue
            artifacts = session.get("artifacts")
            if not isinstance(artifacts, dict) or len(artifacts) > _MAX_ALIGNMENT_ARTIFACTS:
                raise AdapterError("source_contract_unbounded", "NGS alignment artifact set exceeds adapter bounds")
            artifact_digests: dict[str, dict[str, Any]] = {}
            for role, artifact in sorted(artifacts.items()):
                if not isinstance(artifact, dict) or artifact.get("integrity_valid") is not True:
                    raise AdapterError("source_digest_mismatch", "NGS alignment artifact integrity is invalid")
                artifact_digests[str(role)] = {
                    "sha256": _sha256(artifact.get("sha256"), "NGS alignment artifact digest"),
                    "size_bytes": int(artifact.get("size_bytes")),
                }
            digest_set.append({"mode": str(session.get("mode")), "artifacts": artifact_digests})
        if not digest_set:
            raise AdapterError("source_contract_unavailable", "NGS job has no ready authoritative alignment bundle")
        content_digest = _canonical_json_sha256({"job_id": job.id, "sessions": digest_set})
        return _receipt(
            self,
            entity_id=job.id,
            content_digest=content_digest,
            contract_digest=content_digest,
            reopen_uri=_query_uri("/ngs", job_id=job.id),
            metadata={
                "canonical_state": str(job.status),
                "job_status": str(job.status),
                "model_id": job.model_id,
                "ready_session_count": len(digest_set),
                "modes": [item["mode"] for item in digest_set],
                "alignment_digest_set_sha256": content_digest,
                "result_contract_id": "ngs_alignment_viewer_v1",
            },
        )


class OntInstrumentRunAdapter:
    adapter_id = "bms.ngs.ont-run-reference.adapter.v1"
    adapter_version = 1
    display_name = "ONT instrument acquisition run"
    entity_kind = "ont_instrument_run"
    domain_kind = "ngs_molbio"
    store_id = "core"

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(OntInstrumentRun)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(
                or_(
                    OntInstrumentRun.id.ilike(pattern),
                    OntInstrumentRun.position_id.ilike(pattern),
                    OntInstrumentRun.minknow_run_id.ilike(pattern),
                    OntInstrumentRun.sample_id.ilike(pattern),
                )
            )
        rows = list((await core_session.scalars(
            statement.order_by(OntInstrumentRun.observed_at.desc()).limit(limit)
        )).all())
        return [
            EntityProjection(
                entity_id=row.id,
                entity_kind=self.entity_kind,
                label=_bounded_label(row.sample_id, row.id),
                canonical_state=str(row.state),
                metadata={"position_id": row.position_id, "minknow_run_id": row.minknow_run_id, "handoff_ready": row.handoff_ready},
            )
            for row in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        run = await core_session.get(OntInstrumentRun, entity_id)
        if run is None:
            raise AdapterError("entity_not_found", "ONT instrument run does not exist")
        latest_event = await core_session.scalar(
            select(OntInstrumentRunEvent)
            .where(OntInstrumentRunEvent.run_id == run.id)
            .order_by(OntInstrumentRunEvent.observed_generation.desc())
            .limit(1)
        )
        if int(run.observed_generation or 0) > 0 and (
            latest_event is None
            or int(latest_event.observed_generation) != int(run.observed_generation)
            or str(latest_event.state) != str(run.state)
        ):
            raise AdapterError("source_contract_invalid", "ONT run does not match its latest observation ledger")
        validated_terminal = _valid_terminal_manifest(run)
        is_terminal = str(run.state) in TERMINAL_RUN_STATES
        if is_terminal and (run.handoff_ready or run.terminal_artifact_manifest is not None) and validated_terminal is None:
            raise AdapterError("source_contract_invalid", "ONT terminal manifest is not bound to native run authority")
        artifacts: list[dict[str, Any]] = []
        if validated_terminal is not None:
            manifest, raw_artifacts = validated_terminal
            for artifact in raw_artifacts:
                resolved = resolve_runtime_data_path(str(artifact["path"])).resolve()
                root = next(
                    (candidate.resolve() for candidate in (get_results_dir(), get_inputs_dir()) if resolved.is_relative_to(candidate.resolve())),
                    None,
                )
                if root is None:
                    raise AdapterError("source_contract_invalid", "ONT artifact is outside server-owned roots")
                _, artifact_bytes = _read_verified_file(
                    str(resolved),
                    _sha256(artifact["sha256"], "ONT artifact digest"),
                    expected_size=int(artifact["bytes"]),
                    canonical_root=root,
                )
                artifacts.append({
                    "kind": str(artifact["kind"]),
                    "sha256": str(artifact["sha256"]),
                    "size_bytes": len(artifact_bytes),
                })
            manifest_digest = _sha256(run.terminal_artifact_manifest_sha256, "ONT terminal manifest digest")
        else:
            manifest = None
            manifest_digest = None
        lifecycle_authority = {
            "run_id": run.id,
            "state": str(run.state),
            "observed_generation": int(run.observed_generation or 0),
            "minknow_run_id_sha256": hashlib.sha256((run.minknow_run_id or "").encode("utf-8")).hexdigest(),
            "position_id": run.position_id,
            "handoff_ready": bool(run.handoff_ready),
            "terminal_manifest_sha256": manifest_digest,
            "artifacts": artifacts,
        }
        content_digest = _canonical_json_sha256(lifecycle_authority)
        return _receipt(
            self,
            entity_id=run.id,
            content_digest=content_digest,
            contract_digest=_sha256(run.request_fingerprint or content_digest, "ONT run contract digest"),
            reopen_uri=_query_uri("/ngs", run_id=run.id),
            metadata={
                "canonical_state": str(run.state),
                "position_id": run.position_id,
                "minknow_run_id": run.minknow_run_id,
                "observed_generation": int(run.observed_generation or 0),
                "terminal_manifest_sha256": manifest_digest,
                "artifact_count": len(artifacts),
                "handoff_ready": bool(run.handoff_ready),
                "result_contract_id": "ont_instrument_run_v1",
            },
        )


class NgsPooledAssignmentReleaseAdapter:
    adapter_id = "bms.ngs.pooled-assignment-release.adapter.v1"
    adapter_version = 1
    display_name = "Pooled NGS assignment release"
    entity_kind = "ngs_pooled_assignment_release"
    domain_kind = "ngs_molbio"
    store_id = "core"

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(NgsPooledAssignmentRelease)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(
                or_(
                    NgsPooledAssignmentRelease.id.ilike(pattern),
                    NgsPooledAssignmentRelease.assignment_job_id.ilike(pattern),
                    NgsPooledAssignmentRelease.reference_set_id.ilike(pattern),
                )
            )
        rows = list((await core_session.scalars(
            statement.order_by(NgsPooledAssignmentRelease.created_at.desc()).limit(limit)
        )).all())
        return [
            EntityProjection(
                entity_id=row.id,
                entity_kind=self.entity_kind,
                label=f"{row.assignment_job_id} · {row.reference_set_id}"[:160],
                canonical_state="released",
                metadata={"assignment_job_id": row.assignment_job_id, "reference_set_id": row.reference_set_id},
            )
            for row in rows
        ]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        release = await core_session.get(NgsPooledAssignmentRelease, entity_id)
        if release is None:
            raise AdapterError("entity_not_found", "pooled assignment release does not exist")
        assignment_job = await core_session.get(Job, release.assignment_job_id)
        if assignment_job is None:
            raise AdapterError("source_contract_invalid", "pooled assignment release has no persisted Job")
        try:
            root = resolve_persisted_job_result_root(assignment_job)
        except ValueError as exc:
            raise AdapterError("source_contract_invalid", "pooled assignment result root is invalid") from exc
        summary_sha = _sha256(release.assignment_summary_sha256, "assignment summary digest")
        _, summary_bytes = _read_verified_file(
            release.assignment_summary_path,
            summary_sha,
            canonical_root=root,
        )
        targets = list((await core_session.scalars(
            select(NgsPooledAssignmentReleaseTarget)
            .where(NgsPooledAssignmentReleaseTarget.release_id == release.id)
            .order_by(NgsPooledAssignmentReleaseTarget.target_id)
        )).all())
        if not targets:
            raise AdapterError("source_contract_unavailable", "pooled assignment release has no target bindings")
        target_artifacts: dict[str, dict[str, int]] = {}
        server_roots = (get_inputs_dir().resolve(), get_results_dir().resolve())
        for target in targets:
            if target.assignment_job_id != release.assignment_job_id or target.reference_set_id != release.reference_set_id:
                raise AdapterError("source_contract_invalid", "assignment target scope disagrees with release")
            child_job = await core_session.get(Job, target.child_job_id)
            if child_job is None:
                raise AdapterError("source_contract_invalid", "assignment child Job does not exist")
            child_params = dict(child_job.params or {})
            target_binding = child_params.get("pooled_assignment_target_binding")
            release_binding = child_params.get("pooled_assignment_release_binding")
            reference_binding = child_params.get("reference_set_binding")
            revision_binding = child_params.get("molbio_revision_binding")
            if (
                child_params.get("fastq_path") != target.assigned_fastq_artifact_path
                or child_params.get("reference_fasta") != target.fasta_path
                or not isinstance(target_binding, dict)
                or target_binding.get("release_id") != release.id
                or target_binding.get("assignment_job_id") != release.assignment_job_id
                or target_binding.get("reference_set_id") != release.reference_set_id
                or target_binding.get("target_id") != target.target_id
                or target_binding.get("assigned_fastq_sha256") != target.assigned_fastq_sha256
                or target_binding.get("revision_id") != target.revision_id
                or target_binding.get("revision_sha256") != target.revision_sha256
                or not isinstance(release_binding, dict)
                or release_binding.get("release_id") != release.id
                or release_binding.get("assignment_job_id") != release.assignment_job_id
                or not isinstance(reference_binding, dict)
                or reference_binding.get("reference_set_id") != release.reference_set_id
                or not isinstance(revision_binding, dict)
                or revision_binding.get("sequence_id") != target.sequence_id
                or revision_binding.get("revision_id") != target.revision_id
                or revision_binding.get("revision_sha256") != target.revision_sha256
                or revision_binding.get("reference_snapshot_sha256") != target.fasta_sha256
                or revision_binding.get("receipt_id") != target.receipt_id
            ):
                raise AdapterError("source_contract_invalid", "assignment child Job topology does not match release target")
            native_receipt = await core_session.get(MolBioNgsReceipt, target.receipt_id)
            if (
                native_receipt is None
                or native_receipt.sequence_id != target.sequence_id
                or native_receipt.revision_id != target.revision_id
                or native_receipt.revision_sha256 != target.revision_sha256
            ):
                raise AdapterError("source_contract_invalid", "assignment target MolBio receipt disagrees")
            verified_sizes: dict[str, int] = {}
            for role, path_value, digest_value in (
                ("fasta", target.fasta_path, target.fasta_sha256),
                ("assigned_fastq", target.assigned_fastq_path, target.assigned_fastq_sha256),
            ):
                resolved = resolve_runtime_data_path(path_value).resolve()
                canonical_root = next((root for root in server_roots if resolved.is_relative_to(root)), None)
                if canonical_root is None:
                    raise AdapterError("source_contract_invalid", f"assignment {role} path is outside server roots")
                _, artifact_bytes = _read_verified_file(
                    str(resolved),
                    digest_value,
                    canonical_root=canonical_root,
                )
                verified_sizes[role] = len(artifact_bytes)
            target_artifacts[target.id] = verified_sizes
        lineage: list[dict[str, Any]] = []
        for target in targets:
            lineage.extend(
                [
                    {
                        "relation": "uses_expected_reference",
                        "entity_kind": "ngs_expected_reference_receipt",
                        "entity_id": target.receipt_id,
                        "sequence_id": target.sequence_id,
                        "revision_id": target.revision_id,
                        "content_digest": _sha256(target.revision_sha256, "assignment revision digest"),
                    },
                    {
                        "relation": "produced_child_analysis",
                        "entity_kind": "ngs_analysis_job",
                        "entity_id": target.child_job_id,
                        "content_digest": _sha256(target.assigned_fastq_sha256, "assigned FASTQ digest"),
                    },
                ]
            )
        payload = {
            "release_id": release.id,
            "assignment_job_id": release.assignment_job_id,
            "reference_set_id": release.reference_set_id,
            "target_workflow": release.target_workflow,
            "summary_sha256": summary_sha,
            "summary_size_bytes": len(summary_bytes),
            "targets": [
                {
                    "target_id": target.target_id,
                    "child_job_id": target.child_job_id,
                    "receipt_id": target.receipt_id,
                    "revision_sha256": target.revision_sha256,
                    "fasta_sha256": target.fasta_sha256,
                    "assigned_fastq_sha256": target.assigned_fastq_sha256,
                    "assigned_read_count": target.assigned_read_count,
                    "verified_artifact_sizes": target_artifacts[target.id],
                }
                for target in targets
            ],
            "lineage": lineage,
        }
        digest = _canonical_json_sha256(payload)
        return _receipt(
            self,
            entity_id=release.id,
            content_digest=digest,
            contract_digest=_sha256(release.request_fingerprint, "assignment contract digest"),
            reopen_uri=_query_uri(
                "/ngs",
                job_id=release.assignment_job_id,
                reference_set_id=release.reference_set_id,
                assignment_id=release.id,
            ),
            metadata={
                "canonical_state": "released",
                "assignment_job_id": release.assignment_job_id,
                "reference_set_id": release.reference_set_id,
                "target_count": len(targets),
                "native_lineage": lineage,
                "result_contract_id": "ngs_pooled_assignment_release_v1",
            },
        )


class NgsAnalysisReferenceAdapter(SequenceQcReferenceAdapter):
    adapter_id = "bms.ngs.analysis-reference.adapter.v1"
    display_name = "Workflow-aware NGS analysis"
    entity_kind = "ngs_analysis_job"
    require_qc_model = False

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        from services.ont_ngs_contract import CANONICAL_ONT_WORKFLOWS

        normalized = _search_inputs(query, limit)
        statement = select(Job).where(Job.status.in_(("completed", "failed", "cancelled")))
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(or_(Job.id.ilike(pattern), Job.name.ilike(pattern), Job.model_id.ilike(pattern)))
        rows = list((await core_session.scalars(statement.order_by(Job.created_at.desc()).limit(limit * 5))).all())
        projections: list[EntityProjection] = []
        for row in rows:
            workflow_id = (row.params or {}).get("ont_workflow_id") or (row.params or {}).get("workflow_id")
            if workflow_id not in CANONICAL_ONT_WORKFLOWS:
                continue
            projections.append(EntityProjection(
                entity_id=row.id,
                entity_kind=self.entity_kind,
                label=_bounded_label(row.name, row.id),
                canonical_state=str(row.status),
                metadata={"model_id": row.model_id, "workflow_id": workflow_id},
            ))
            if len(projections) >= limit:
                break
        return projections

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        receipt = await super().verify(core_session, entity_id)
        receipt["metadata"]["result_contract_id"] = "ngs_analysis_manifest_v1"
        return receipt


def _member_global_entity_id(authority: dict[str, Any]) -> str:
    kind = str(authority.get("entity_kind") or "")
    entity_id = str(authority.get("entity_id") or "")
    reopen = authority.get("reopen_destination")
    params = reopen.get("params") if isinstance(reopen, dict) else None
    if not isinstance(params, dict):
        raise AdapterError("source_contract_invalid", "native member receipt has no typed reopen identity")
    key_map = {
        "molecular_revision": ("sequence_id", "revision_id"),
        "primer_revision": ("primer_id", "revision_id"),
        "pcr_experiment_revision": ("experiment_id", "revision_id"),
        "molecular_operation": ("operation_id",),
        "ngs_reference_revision": (
            "global_domain_experiment_id",
            "reference_id",
            "revision_id",
        ),
        "ngs_comparison_panel": ("panel_id", "panel_version"),
        "ont_instrument_run": ("run_id", "observed_generation"),
        "ngs_job": ("job_id",),
        "ngs_evidence_assessment": ("global_domain_experiment_id", "evidence_id"),
        "sample_revision": (
            "global_domain_experiment_id",
            "sample_id",
            "sample_revision_id",
        ),
        "ngs_molbio_state_revision": (
            "global_domain_experiment_id",
            "state_revision_id",
        ),
    }
    if kind == "ngs_result_manifest":
        job_id = params.get("job_id")
        suffix = f"{job_id}:" if isinstance(job_id, str) else ""
        if not suffix or not entity_id.startswith(suffix):
            raise AdapterError("source_contract_invalid", "native manifest receipt identity is malformed")
        return urlencode(
            {
                "job_id": job_id,
                "manifest_identity": entity_id[len(suffix):],
            }
        )
    keys = key_map.get(kind)
    if keys is None or any(key not in params for key in keys):
        raise AdapterError("source_contract_invalid", "native member receipt identity is incomplete")
    if len(keys) == 1:
        value = str(params[keys[0]])
        if value != entity_id:
            raise AdapterError("source_contract_invalid", "native member receipt stable identity diverges")
        return value
    return urlencode({key: str(params[key]) for key in keys})


def _exact_member_receipt(
    adapter: DomainAdapter,
    *,
    requested_entity_id: str,
    member: ExternalMemberReceipt,
    reopen_uri: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    accepted_schemas = getattr(adapter, "source_schemas", frozenset())
    fixed_revision_marker = getattr(adapter, "fixed_revision_marker", None)
    if (
        member.entity_kind != adapter.entity_kind
        or member.source_store_id != adapter.store_id
        or member.availability != "available"
        or (accepted_schemas and member.source_schema not in accepted_schemas)
        or (
            fixed_revision_marker is not None
            and str(member.source_generation_or_revision) != fixed_revision_marker
        )
        or _member_global_entity_id({
            "entity_kind": member.entity_kind,
            "entity_id": member.entity_id,
            "reopen_destination": member.reopen_destination,
        }) != requested_entity_id
    ):
        raise AdapterError("source_contract_invalid", "native member receipt authority diverges from requested identity")
    return _receipt(
        adapter,
        entity_id=requested_entity_id,
        entity_revision_id=str(member.source_generation_or_revision),
        content_digest=_sha256(member.content_digest, "native member content digest"),
        contract_digest=_canonical_json_sha256(
            {
                "adapter_id": adapter.adapter_id,
                "source_build_revision": _source_build_revision(),
                "native_member_receipt_id": member.receipt_id,
                "content_digest": member.content_digest,
            }
        ),
        reopen_uri=reopen_uri,
        metadata={
            "canonical_state": "immutable",
            "native_member_receipt_id": member.receipt_id,
            "native_entity_id": member.entity_id,
            "native_revision_or_generation": str(member.source_generation_or_revision),
            "source_schema": member.source_schema,
            **metadata,
        },
    )


async def _resolve_exact_member(awaitable: Any) -> ExternalMemberReceipt:
    try:
        return await awaitable
    except KeyError as exc:
        raise AdapterError("entity_not_found", str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise AdapterError("source_contract_invalid", str(exc)) from exc


async def _exact_member_domain_owner(
    session: AsyncSession,
    *,
    receipt_id: str,
    expected_domain_id: str | None = None,
) -> str:
    statement = (
        select(MolBioNGSDomainStateRevision.global_domain_experiment_id)
        .join(
            MolBioNGSDomainStateMember,
            MolBioNGSDomainStateMember.state_revision_id == MolBioNGSDomainStateRevision.id,
        )
        .where(MolBioNGSDomainStateMember.receipt_id == receipt_id)
        .distinct()
        .limit(2)
    )
    if expected_domain_id is not None:
        statement = statement.where(
            MolBioNGSDomainStateRevision.global_domain_experiment_id == expected_domain_id
        )
    rows = list((await session.execute(statement)).scalars().all())
    if len(rows) != 1:
        raise AdapterError(
            "source_contract_invalid",
            "exact member receipt is not attached to the selected Domain"
            if expected_domain_id is not None
            else "exact member receipt does not resolve to one Domain owner",
        )
    return str(rows[0])


async def _exact_local_member_authority(
    session: AsyncSession,
    *,
    member: ExternalMemberReceipt,
    expected_domain_id: str | None = None,
) -> tuple[str, str]:
    """Resolve one persisted native receipt and its sole Domain owner.

    Native resolvers issue a fresh wrapper receipt on each call, so ownership
    cannot be proved from that transient receipt ID.  Resolve instead by the
    complete immutable native identity and digest, then verify the persisted
    canonical body before following its state-membership edge.
    """

    canonical_reopen = json.dumps(
        member.reopen_destination,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    rows = list((await session.scalars(
        select(MolBioNGSMemberReceipt)
        .where(
            MolBioNGSMemberReceipt.source_store_id == member.source_store_id,
            MolBioNGSMemberReceipt.entity_kind == member.entity_kind,
            MolBioNGSMemberReceipt.entity_id == member.entity_id,
            MolBioNGSMemberReceipt.source_generation_or_revision
            == str(member.source_generation_or_revision),
            MolBioNGSMemberReceipt.content_digest == member.content_digest,
            MolBioNGSMemberReceipt.availability == member.availability,
            MolBioNGSMemberReceipt.reopen_destination == canonical_reopen,
        )
        .order_by(MolBioNGSMemberReceipt.receipt_id)
        .limit(2)
    )).all())
    if len(rows) != 1:
        raise AdapterError(
            "source_contract_invalid",
            "native immutable identity and digest do not resolve to one local member receipt",
        )
    row = rows[0]
    try:
        authority = serialize_external_member_receipt(row)
    except ValueError as exc:
        raise AdapterError("source_digest_mismatch", str(exc)) from exc
    expected = {
        "source_store_id": member.source_store_id,
        "entity_kind": member.entity_kind,
        "entity_id": member.entity_id,
        "source_generation_or_revision": str(member.source_generation_or_revision),
        "content_digest": member.content_digest,
        "source_schema": member.source_schema,
        "availability": member.availability,
        "reopen_destination": member.reopen_destination,
    }
    if any(authority.get(key) != value for key, value in expected.items()):
        raise AdapterError(
            "source_digest_mismatch",
            "persisted local member receipt diverges from native immutable authority",
        )
    persisted_receipt_id = str(row.receipt_id)
    canonical_receipt_id = authority.get("receipt_id")
    if not isinstance(canonical_receipt_id, str) or canonical_receipt_id != persisted_receipt_id:
        raise AdapterError(
            "source_digest_mismatch",
            "persisted local member receipt ID diverges from its canonical body",
        )

    def canonical_timestamp(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("member receipt timestamp is invalid")
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("member receipt timestamp must be timezone-aware")
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")

    try:
        canonical_created_at = canonical_timestamp(authority.get("created_at"))
        persisted_created_at = canonical_timestamp(row.created_at)
    except (TypeError, ValueError) as exc:
        raise AdapterError("source_digest_mismatch", str(exc)) from exc
    if canonical_created_at != persisted_created_at:
        raise AdapterError(
            "source_digest_mismatch",
            "persisted local member receipt timestamp diverges from its canonical body",
        )

    domain_id = await _exact_member_domain_owner(
        session,
        receipt_id=canonical_receipt_id,
        expected_domain_id=expected_domain_id,
    )
    return domain_id, canonical_receipt_id


class ExactMolecularRevisionMemberAdapter:
    adapter_id = "bms.molbio.member-molecular-revision.adapter.v1"
    adapter_version = 1
    display_name = "Exact molecular revision member"
    entity_kind = "molecular_revision"
    domain_kind = "ngs_molbio"
    store_id = "molbio"
    source_schemas = frozenset({"bms.molbio.molecular-revision.v1"})

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] = molbio_session,
        domain_session_factory: Callable[[], Any] = molbio_ngs_session_factory,
    ):
        self._sessions = session_factory
        self._domain_sessions = domain_session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        del core_session
        normalized = _search_inputs(query, limit)
        async with self._sessions() as session:
            statement = select(MolecularRevision)
            if normalized:
                pattern = f"%{normalized}%"
                statement = statement.where(or_(MolecularRevision.id.ilike(pattern), MolecularRevision.document_id.ilike(pattern)))
            rows = list((await session.scalars(statement.order_by(MolecularRevision.created_at.desc()).limit(limit))).all())
        return [EntityProjection(
            entity_id=urlencode({"sequence_id": row.document_id, "revision_id": row.id}),
            entity_kind=self.entity_kind,
            label=_bounded_label((row.snapshot or {}).get("name"), row.id),
            canonical_state="immutable",
            metadata={"sequence_id": row.document_id, "revision_id": row.id, "revision_number": row.revision_number},
        ) for row in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        del core_session
        try:
            parsed = parse_qs(entity_id, strict_parsing=True, keep_blank_values=True)
        except ValueError as exc:
            raise AdapterError("source_contract_invalid", "molecular revision identity is malformed") from exc
        required = frozenset({"sequence_id", "revision_id"})
        keys = frozenset(parsed)
        if keys not in {required, required | {"domain_experiment_id"}} or any(
            len(values) != 1 or not values[0] for values in parsed.values()
        ):
            raise AdapterError("source_contract_invalid", "molecular revision identity has an invalid key shape")
        identity = {key: parsed[key][0] for key in required}
        expected_domain_id = parsed.get("domain_experiment_id", [None])[0]
        async with self._sessions() as session:
            member = await _resolve_exact_member(resolve_molecular_revision_receipt(session, **identity))
        async with self._domain_sessions() as domain_session:
            domain_id, native_receipt_id = await _exact_local_member_authority(
                domain_session, member=member, expected_domain_id=expected_domain_id
            )
        return _exact_member_receipt(
            self, requested_entity_id=urlencode(identity), member=member,
            reopen_uri=_query_uri("/designer", **identity),
            metadata={
                "sequence_id": identity["sequence_id"],
                "revision_id": identity["revision_id"],
                "global_domain_experiment_id": domain_id,
                "native_member_receipt_id": native_receipt_id,
                "result_contract_id": "molbio_molecular_revision_member_v1",
            },
        )


class ExactPrimerRevisionAdapter:
    adapter_id = "bms.molbio.primer-revision.adapter.v1"
    adapter_version = 1
    display_name = "Exact primer revision"
    entity_kind = "primer_revision"
    domain_kind = "ngs_molbio"
    store_id = "molbio"
    source_schemas = frozenset({"bms.molbio.primer-revision.v1"})

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] = molbio_session,
        domain_session_factory: Callable[[], Any] = molbio_ngs_session_factory,
    ):
        self._sessions = session_factory
        self._domain_sessions = domain_session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        del core_session
        normalized = _search_inputs(query, limit)
        async with self._sessions() as session:
            statement = select(PrimerRevision)
            if normalized:
                pattern = f"%{normalized}%"
                statement = statement.where(or_(PrimerRevision.id.ilike(pattern), PrimerRevision.primer_id.ilike(pattern)))
            rows = list((await session.scalars(statement.order_by(PrimerRevision.created_at.desc()).limit(limit))).all())
        return [EntityProjection(
            entity_id=urlencode({"primer_id": row.primer_id, "revision_id": row.id}),
            entity_kind=self.entity_kind, label=_bounded_label(row.id, row.primer_id),
            canonical_state="immutable", metadata={"primer_id": row.primer_id, "revision_number": row.revision_number},
        ) for row in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        del core_session
        identity = _parse_composite_identity(entity_id, ("primer_id", "revision_id"))
        async with self._sessions() as session:
            member = await _resolve_exact_member(resolve_primer_revision_receipt(session, **identity))
        async with self._domain_sessions() as domain_session:
            domain_id, native_receipt_id = await _exact_local_member_authority(
                domain_session, member=member
            )
        return _exact_member_receipt(
            self, requested_entity_id=entity_id, member=member,
            reopen_uri=_query_uri("/molbio", **identity),
            metadata={
                **identity,
                "global_domain_experiment_id": domain_id,
                "native_member_receipt_id": native_receipt_id,
                "result_contract_id": "molbio_primer_revision_v1",
            },
        )


class ExactPcrExperimentRevisionAdapter:
    adapter_id = "bms.molbio.pcr-experiment-revision.adapter.v1"
    adapter_version = 1
    display_name = "Exact PCR experiment revision"
    entity_kind = "pcr_experiment_revision"
    domain_kind = "ngs_molbio"
    store_id = "molbio"
    source_schemas = frozenset({"bms.molbio.pcr-experiment-revision.v1"})

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] = molbio_session,
        domain_session_factory: Callable[[], Any] = molbio_ngs_session_factory,
    ):
        self._sessions = session_factory
        self._domain_sessions = domain_session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        del core_session
        normalized = _search_inputs(query, limit)
        async with self._sessions() as session:
            statement = select(PCRExperimentRevision)
            if normalized:
                pattern = f"%{normalized}%"
                statement = statement.where(or_(PCRExperimentRevision.id.ilike(pattern), PCRExperimentRevision.experiment_id.ilike(pattern)))
            rows = list((await session.scalars(statement.order_by(PCRExperimentRevision.created_at.desc()).limit(limit))).all())
        return [EntityProjection(
            entity_id=urlencode({"experiment_id": row.experiment_id, "revision_id": row.id}),
            entity_kind=self.entity_kind, label=_bounded_label(row.id, row.experiment_id),
            canonical_state="immutable", metadata={"experiment_id": row.experiment_id, "revision_number": row.revision_number},
        ) for row in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        del core_session
        identity = _parse_composite_identity(entity_id, ("experiment_id", "revision_id"))
        async with self._sessions() as session:
            member = await _resolve_exact_member(resolve_pcr_experiment_revision_receipt(session, **identity))
        async with self._domain_sessions() as domain_session:
            domain_id, native_receipt_id = await _exact_local_member_authority(
                domain_session, member=member
            )
        return _exact_member_receipt(
            self, requested_entity_id=entity_id, member=member,
            reopen_uri=_query_uri("/molbio", **identity),
            metadata={
                **identity,
                "global_domain_experiment_id": domain_id,
                "native_member_receipt_id": native_receipt_id,
                "result_contract_id": "molbio_pcr_experiment_revision_v1",
            },
        )


class ExactMolecularOperationMemberAdapter:
    adapter_id = "bms.molbio.member-operation.adapter.v1"
    adapter_version = 1
    display_name = "Exact molecular operation member"
    entity_kind = "molecular_operation"
    domain_kind = "ngs_molbio"
    store_id = "molbio"
    source_schemas = frozenset({"bms.molbio.molecular-operation.v1"})
    fixed_revision_marker = "event"

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] = molbio_session,
        domain_session_factory: Callable[[], Any] = molbio_ngs_session_factory,
    ):
        self._sessions = session_factory
        self._domain_sessions = domain_session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        del core_session
        normalized = _search_inputs(query, limit)
        async with self._sessions() as session:
            statement = select(MolecularOperation)
            if normalized:
                pattern = f"%{normalized}%"
                statement = statement.where(or_(MolecularOperation.id.ilike(pattern), MolecularOperation.operation_kind.ilike(pattern)))
            rows = list((await session.scalars(statement.order_by(MolecularOperation.created_at.desc()).limit(limit))).all())
        return [EntityProjection(row.id, self.entity_kind, _bounded_label(row.operation_kind, row.id), str(row.status), {"operation_kind": row.operation_kind}) for row in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        del core_session
        if not entity_id or len(entity_id) > 160:
            raise AdapterError("invalid_entity_id", "molecular operation identity is invalid")
        async with self._sessions() as session:
            member = await _resolve_exact_member(resolve_molecular_operation_receipt(session, operation_id=entity_id))
            inputs = list((await session.scalars(select(MolecularOperationInput).where(MolecularOperationInput.operation_id == entity_id).order_by(MolecularOperationInput.position, MolecularOperationInput.id))).all())
            outputs = list((await session.scalars(select(MolecularOperationOutput).where(MolecularOperationOutput.operation_id == entity_id).order_by(MolecularOperationOutput.position, MolecularOperationOutput.id))).all())
            revision_ids = {item.revision_id for item in [*inputs, *outputs]}
            revisions = {row.id: row for row in (await session.scalars(select(MolecularRevision).where(MolecularRevision.id.in_(revision_ids)))).all()} if revision_ids else {}
        if len(revisions) != len(revision_ids):
            raise AdapterError("source_contract_invalid", "molecular operation lineage has a missing revision")
        async with self._domain_sessions() as domain_session:
            domain_id, native_receipt_id = await _exact_local_member_authority(
                domain_session, member=member
            )
        lineage = []
        for relation, rows in (("uses_input", inputs), ("produced", outputs)):
            for row in rows:
                revision = revisions[row.revision_id]
                lineage.append({
                    "relation": relation,
                    "role": row.role,
                    "ordinal": row.position,
                    "entity_kind": "molecular_revision",
                    "entity_id": urlencode({"sequence_id": revision.document_id, "revision_id": revision.id}),
                    "receipt_content_digest": _sha256(revision.content_sha256, "molecular revision digest"),
                })
        return _exact_member_receipt(
            self, requested_entity_id=entity_id, member=member,
            reopen_uri=_query_uri("/designer", operation_id=entity_id),
            metadata={
                "operation_id": entity_id,
                "global_domain_experiment_id": domain_id,
                "native_member_receipt_id": native_receipt_id,
                "native_lineage": lineage,
                "result_contract_id": "molbio_molecular_operation_member_v1",
            },
        )


class ExactSampleRevisionAdapter:
    adapter_id = "bms.ngs-molbio.sample-revision.adapter.v1"
    adapter_version = 1
    display_name = "Exact NGS/MolBio sample revision"
    entity_kind = "sample_revision"
    domain_kind = "ngs_molbio"
    store_id = "molbio-ngs-domain"
    source_schemas = frozenset({"bms.molbio-ngs.sample-revision.v1"})

    def __init__(self, *, session_factory: Callable[[], Any] = molbio_ngs_session_factory):
        self._sessions = session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        del core_session
        normalized = _search_inputs(query, limit)
        async with self._sessions() as session:
            statement = select(MolBioNGSSampleRevision)
            if normalized:
                pattern = f"%{normalized}%"
                statement = statement.where(or_(MolBioNGSSampleRevision.id.ilike(pattern), MolBioNGSSampleRevision.sample_id.ilike(pattern), MolBioNGSSampleRevision.global_domain_experiment_id.ilike(pattern)))
            rows = list((await session.scalars(statement.order_by(MolBioNGSSampleRevision.created_at.desc()).limit(limit))).all())
        return [EntityProjection(
            urlencode({"global_domain_experiment_id": row.global_domain_experiment_id, "sample_id": row.sample_id, "sample_revision_id": row.id}),
            self.entity_kind, _bounded_label(row.sample_id, row.id), "immutable",
            {"global_domain_experiment_id": row.global_domain_experiment_id, "sample_id": row.sample_id, "revision_number": row.revision_number},
        ) for row in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        del core_session
        identity = _parse_composite_identity(entity_id, ("global_domain_experiment_id", "sample_id", "sample_revision_id"))
        async with self._sessions() as session:
            member = await _resolve_exact_member(resolve_sample_revision_receipt(session, **identity))
        return _exact_member_receipt(
            self, requested_entity_id=entity_id, member=member,
            reopen_uri=_query_uri(f"/molbio-ngs/domain-experiments/{identity['global_domain_experiment_id']}", sample_id=identity["sample_id"], sample_revision_id=identity["sample_revision_id"]),
            metadata={**identity, "result_contract_id": "ngs_molbio_sample_revision_v1"},
        )


class ExactStateRevisionAdapter:
    adapter_id = "bms.ngs-molbio.state-revision.adapter.v1"
    adapter_version = 1
    display_name = "Exact NGS/MolBio scientific-state revision"
    entity_kind = "ngs_molbio_state_revision"
    domain_kind = "ngs_molbio"
    store_id = "molbio-ngs-domain"
    source_schemas = frozenset({"bms.molbio-ngs.domain-state-revision.v1"})

    def __init__(self, *, session_factory: Callable[[], Any] = molbio_ngs_session_factory):
        self._sessions = session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        del core_session
        normalized = _search_inputs(query, limit)
        async with self._sessions() as session:
            statement = select(MolBioNGSDomainStateRevision)
            if normalized:
                pattern = f"%{normalized}%"
                statement = statement.where(or_(MolBioNGSDomainStateRevision.id.ilike(pattern), MolBioNGSDomainStateRevision.global_domain_experiment_id.ilike(pattern)))
            rows = list((await session.scalars(statement.order_by(MolBioNGSDomainStateRevision.created_at.desc()).limit(limit))).all())
        return [EntityProjection(
            urlencode({"global_domain_experiment_id": row.global_domain_experiment_id, "state_revision_id": row.id}), self.entity_kind,
            _bounded_label(f"State revision {row.revision_number}", row.id), "immutable",
            {"global_domain_experiment_id": row.global_domain_experiment_id, "revision_number": row.revision_number, "binding_revision_id": row.binding_revision_id},
        ) for row in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        del core_session
        identity = _parse_composite_identity(entity_id, ("global_domain_experiment_id", "state_revision_id"))
        async with self._sessions() as session:
            revision = await session.get(MolBioNGSDomainStateRevision, identity["state_revision_id"])
            member = await _resolve_exact_member(resolve_state_revision_receipt(session, **identity))
            if revision is None or revision.global_domain_experiment_id != identity["global_domain_experiment_id"]:
                raise AdapterError("entity_not_found", "state revision identity is unavailable")
            from molbio_ngs_services import verify_state_revision_integrity
            payload, graph = await verify_state_revision_integrity(session, revision)
            sample_ids = list(payload.get("design", {}).get("sample_revision_ids", []))
            sample_rows = {
                row.id: row
                for row in (
                    await session.scalars(
                        select(MolBioNGSSampleRevision).where(
                            MolBioNGSSampleRevision.id.in_(sample_ids)
                        )
                    )
                ).all()
            } if sample_ids else {}
        lineage = [{
            "relation": "references",
            "role": item["role"],
            "ordinal": item["ordinal"],
            "entity_kind": item["entity_kind"],
            "entity_id": _member_global_entity_id(item),
            "receipt_content_digest": item["content_digest"],
        } for item in graph]
        lineage.extend({
            "relation": "references",
            "role": "sample",
            "ordinal": ordinal,
            "entity_kind": "sample_revision",
            "entity_id": urlencode({
                "global_domain_experiment_id": identity["global_domain_experiment_id"],
                "sample_id": sample_rows[sample_id].sample_id,
                "sample_revision_id": sample_id,
            }),
            "receipt_content_digest": sample_rows[sample_id].payload_sha256,
        } for ordinal, sample_id in enumerate(sample_ids))
        return _exact_member_receipt(
            self, requested_entity_id=entity_id, member=member,
            reopen_uri=_query_uri(f"/molbio-ngs/domain-experiments/{identity['global_domain_experiment_id']}", state_revision_id=identity["state_revision_id"]),
            metadata={
                **identity,
                "binding_revision_id": revision.binding_revision_id,
                "payload_sha256": revision.payload_sha256,
                "membership_graph_sha256": revision.membership_graph_sha256,
                "native_lineage": lineage,
                "result_contract_id": "ngs_molbio_state_revision_v1",
            },
        )


class ExactNgsReferenceRevisionAdapter:
    adapter_id = "bms.ngs.reference-revision.adapter.v1"
    adapter_version = 1
    display_name = "Exact managed NGS reference revision"
    entity_kind = "ngs_reference_revision"
    domain_kind = "ngs_molbio"
    store_id = "molbio-ngs-domain"
    source_schemas = frozenset({"bms.molbio-ngs.reference-revision.v1"})

    def __init__(self, *, session_factory: Callable[[], Any] = molbio_ngs_session_factory):
        self._sessions = session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        del core_session
        normalized = _search_inputs(query, limit)
        async with self._sessions() as session:
            statement = select(MolBioNGSReferenceRevision, MolBioNGSReferenceResource).join(MolBioNGSReferenceResource, MolBioNGSReferenceResource.id == MolBioNGSReferenceRevision.reference_id)
            if normalized:
                pattern = f"%{normalized}%"
                statement = statement.where(or_(MolBioNGSReferenceRevision.id.ilike(pattern), MolBioNGSReferenceRevision.reference_id.ilike(pattern), MolBioNGSReferenceResource.name.ilike(pattern)))
            rows = (await session.execute(statement.order_by(MolBioNGSReferenceRevision.created_at.desc()).limit(limit))).all()
        return [EntityProjection(
            urlencode({
                "global_domain_experiment_id": rev.global_domain_experiment_id,
                "reference_id": rev.reference_id,
                "revision_id": rev.id,
            }),
            self.entity_kind, _bounded_label(resource.name, rev.id), "immutable",
            {"global_domain_experiment_id": rev.global_domain_experiment_id, "reference_id": rev.reference_id, "revision_number": rev.revision_number},
        ) for rev, resource in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        del core_session
        identity = _parse_composite_identity(
            entity_id,
            ("global_domain_experiment_id", "reference_id", "revision_id"),
        )
        async with self._sessions() as session:
            member = await _resolve_exact_member(resolve_ngs_reference_revision_receipt(
                session,
                global_domain_experiment_id=identity["global_domain_experiment_id"],
                reference_id=identity["reference_id"],
                revision_id=identity["revision_id"],
            ))
            revision = await session.get(MolBioNGSReferenceRevision, identity["revision_id"])
        if revision is None:
            raise AdapterError("entity_not_found", "managed reference revision does not exist")
        payload = json.loads(revision.canonical_payload)
        provenance = payload.get("source_provenance") if isinstance(payload, dict) else None
        lineage = []
        if isinstance(provenance, dict) and provenance.get("kind") == "molbio_molecular_revision":
            sequence_id = provenance.get("sequence_id")
            revision_id = provenance.get("molecular_revision_id")
            digest = provenance.get("molecular_revision_sha256")
            if not all(isinstance(value, str) and value for value in (sequence_id, revision_id, digest)):
                raise AdapterError("source_contract_invalid", "managed reference molecular provenance is incomplete")
            lineage.append({
                "relation": "derived_from", "entity_kind": "molecular_revision",
                "entity_id": urlencode({"sequence_id": sequence_id, "revision_id": revision_id}),
                "receipt_content_digest": _sha256(digest, "molecular source digest"),
            })
        return _exact_member_receipt(
            self, requested_entity_id=entity_id, member=member,
            reopen_uri=_query_uri(
                f"/molbio-ngs/domain-experiments/{identity['global_domain_experiment_id']}",
                reference_id=identity["reference_id"],
                revision_id=identity["revision_id"],
            ),
            metadata={**identity, "canonical_fasta_size_bytes": revision.canonical_fasta_size_bytes, "native_lineage": lineage, "result_contract_id": "ngs_reference_revision_v1"},
        )


class ExactNgsComparisonPanelAdapter:
    adapter_id = "bms.ngs.comparison-panel.adapter.v1"
    adapter_version = 1
    display_name = "Exact approved NGS comparison panel"
    entity_kind = "ngs_comparison_panel"
    domain_kind = "ngs_molbio"
    store_id = "core-ngs"
    source_schemas = frozenset({"bms.ngs.approved-comparison-panel.v1"})

    def __init__(
        self,
        *,
        domain_session_factory: Callable[[], Any] = molbio_ngs_session_factory,
    ):
        self._domain_sessions = domain_session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(ApprovedNgsComparisonPanel)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(or_(ApprovedNgsComparisonPanel.id.ilike(pattern), ApprovedNgsComparisonPanel.label.ilike(pattern)))
        rows = list((await core_session.scalars(statement.order_by(ApprovedNgsComparisonPanel.created_at.desc()).limit(limit))).all())
        return [EntityProjection(urlencode({"panel_id": row.id, "panel_version": row.version}), self.entity_kind, _bounded_label(row.label, row.id), str(row.status), {"panel_version": row.version}) for row in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        identity = _parse_composite_identity(entity_id, ("panel_id", "panel_version"))
        try:
            panel_version = int(identity["panel_version"])
        except ValueError as exc:
            raise AdapterError("invalid_entity_id", "comparison panel version is invalid") from exc
        member = await _resolve_exact_member(resolve_approved_comparison_panel_receipt(core_session, panel_id=identity["panel_id"], panel_version=panel_version))
        async with self._domain_sessions() as domain_session:
            domain_id, native_receipt_id = await _exact_local_member_authority(
                domain_session, member=member
            )
        panel = await core_session.get(ApprovedNgsComparisonPanel, identity["panel_id"])
        if panel is None:
            raise AdapterError("entity_not_found", "comparison panel does not exist")
        manifest = _validated_panel_manifest(panel)
        role_map = {"host": "reference", "plasmid_decoy": "control"}
        lineage = [{
            "relation": "compared_with",
            "role": role_map[str(item["role"])],
            "ordinal": ordinal,
            "compatibility_contract_id": "bms.ngs.approved-comparison-panel.v1",
            "source_digest": item["revision_sha256"],
            "entity_kind": "molecular_revision",
            "entity_id": urlencode({"sequence_id": item["sequence_id"], "revision_id": item["revision_id"]}),
            "receipt_content_digest": item["revision_sha256"],
        } for ordinal, item in enumerate(manifest["entries"])]
        return _exact_member_receipt(
            self, requested_entity_id=entity_id, member=member,
            reopen_uri=_query_uri("/ngs", panel_id=identity["panel_id"], panel_version=str(panel_version)),
            metadata={
                "panel_id": identity["panel_id"],
                "panel_version": panel_version,
                "entry_count": len(lineage),
                "global_domain_experiment_id": domain_id,
                "native_member_receipt_id": native_receipt_id,
                "native_lineage": lineage,
                "result_contract_id": "ngs_comparison_panel_v1",
            },
        )


class ExactOntObservationAdapter:
    adapter_id = "bms.ngs.ont-observation.adapter.v1"
    adapter_version = 1
    display_name = "Exact ONT run observation"
    entity_kind = "ont_instrument_run"
    domain_kind = "ngs_molbio"
    store_id = "core-ngs"
    source_schemas = frozenset({"bms.ont.instrument-run-observation.v1"})

    def __init__(self, *, domain_session_factory: Callable[[], Any] = molbio_ngs_session_factory):
        self._domain_sessions = domain_session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(OntInstrumentRunEvent, OntInstrumentRun).join(OntInstrumentRun, OntInstrumentRun.id == OntInstrumentRunEvent.run_id)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(or_(OntInstrumentRunEvent.run_id.ilike(pattern), OntInstrumentRun.sample_id.ilike(pattern)))
        rows = (await core_session.execute(statement.order_by(OntInstrumentRunEvent.observed_at.desc()).limit(limit))).all()
        return [EntityProjection(urlencode({"run_id": event.run_id, "observed_generation": event.observed_generation}), self.entity_kind, _bounded_label(run.sample_id, event.run_id), str(event.state), {"observed_generation": event.observed_generation}) for event, run in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        identity = _parse_composite_identity(entity_id, ("run_id", "observed_generation"))
        try:
            generation = int(identity["observed_generation"])
        except ValueError as exc:
            raise AdapterError("invalid_entity_id", "ONT observation generation is invalid") from exc
        member = await _resolve_exact_member(resolve_ont_instrument_run_receipt(core_session, run_id=identity["run_id"], observed_generation=generation))
        run = await core_session.get(OntInstrumentRun, identity["run_id"])
        event = await core_session.scalar(
            select(OntInstrumentRunEvent).where(
                OntInstrumentRunEvent.run_id == identity["run_id"],
                OntInstrumentRunEvent.observed_generation == generation,
            )
        )
        if run is None or event is None:
            raise AdapterError("entity_not_found", "ONT observation authority is unavailable")
        async with self._domain_sessions() as domain_session:
            domain_id, native_receipt_id = await _exact_local_member_authority(
                domain_session, member=member
            )
        terminal_manifest_sha256 = None
        terminal_manifest_generation = None
        terminal_manifest_state = None
        terminal_artifacts: list[dict[str, Any]] = []
        if int(run.observed_generation or 0) == generation:
            terminal = _valid_terminal_manifest(run)
            if terminal is not None:
                manifest, artifacts = terminal
                terminal_manifest_sha256 = _sha256(
                    run.terminal_artifact_manifest_sha256,
                    "ONT terminal manifest digest",
                )
                terminal_manifest_generation = int(manifest["observed_generation"])
                terminal_manifest_state = str(manifest["terminal_state"])
                terminal_artifacts = [
                    {
                        "kind": str(artifact["kind"]),
                        "sha256": _sha256(artifact["sha256"], "ONT terminal artifact digest"),
                        "size_bytes": int(artifact["bytes"]),
                    }
                    for artifact in artifacts
                ]
        observed_at = (
            event.observed_at.isoformat()
            if hasattr(event.observed_at, "isoformat")
            else str(event.observed_at)
        )
        return _exact_member_receipt(
            self,
            requested_entity_id=entity_id,
            member=member,
            reopen_uri=_query_uri(
                "/ngs",
                run_id=identity["run_id"],
                observed_generation=str(generation),
            ),
            metadata={
                "run_id": identity["run_id"],
                "observed_generation": generation,
                "global_domain_experiment_id": domain_id,
                "native_member_receipt_id": native_receipt_id,
                "state": str(event.state),
                "observation_reason": (
                    f"event={event.event_type}; state={event.state}; "
                    f"observed_generation={generation}"
                ),
                "event_type": str(event.event_type),
                "observed_at": observed_at,
                "position_id": run.position_id,
                "sample_id": run.sample_id,
                "minknow_payload_sha256": _canonical_json_sha256(event.minknow_payload),
                "output_files_sha256": _canonical_json_sha256(event.output_files or {}),
                "output_file_roles": sorted(str(key) for key in (event.output_files or {})),
                "terminal_manifest_sha256": terminal_manifest_sha256,
                "terminal_manifest_observed_generation": terminal_manifest_generation,
                "terminal_manifest_state": terminal_manifest_state,
                "terminal_artifacts": terminal_artifacts,
                "handoff_ready": bool(run.handoff_ready),
                "result_contract_id": "ont_instrument_run_observation_v1",
            },
        )


class ExactNgsJobAdapter:
    adapter_id = "bms.ngs.job-reference.adapter.v1"
    adapter_version = 1
    display_name = "Exact NGS Job launch"
    entity_kind = "ngs_job"
    domain_kind = "ngs_molbio"
    store_id = "core-ngs"
    source_schemas = frozenset({"bms.core.ngs-job-launch.v1"})
    fixed_revision_marker = "launch"

    def __init__(self, *, domain_session_factory: Callable[[], Any] = molbio_ngs_session_factory):
        self._domain_sessions = domain_session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(Job).where(Job.model_id == "nanopore")
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(or_(Job.id.ilike(pattern), Job.name.ilike(pattern)))
        rows = list((await core_session.scalars(statement.order_by(Job.created_at.desc()).limit(limit))).all())
        return [EntityProjection(row.id, self.entity_kind, _bounded_label(row.name, row.id), str(row.status), {"workflow_id": (row.params or {}).get("ont_workflow_id")}) for row in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        if not entity_id or len(entity_id) > 160:
            raise AdapterError("invalid_entity_id", "NGS Job identity is invalid")
        member = await _resolve_exact_member(resolve_ngs_job_receipt(core_session, job_id=entity_id))
        job = await core_session.get(Job, entity_id)
        if job is None:
            raise AdapterError("entity_not_found", "NGS Job does not exist")
        params = job.params if isinstance(job.params, dict) else {}
        claimed_domain_id = params.get("global_domain_experiment_id")
        state_id = params.get("molbio_ngs_state_revision_id")
        if not isinstance(claimed_domain_id, str) or not claimed_domain_id:
            raise AdapterError("source_contract_invalid", "NGS Job lacks exact Domain ownership")
        if not isinstance(state_id, str) or not state_id:
            raise AdapterError("source_contract_invalid", "NGS Job lacks exact scientific-state revision authority")
        async with self._domain_sessions() as domain_session:
            domain_id, native_receipt_id = await _exact_local_member_authority(
                domain_session, member=member
            )
            if claimed_domain_id != domain_id:
                raise AdapterError(
                    "source_contract_invalid",
                    "NGS Job claimed Domain diverges from persisted member-receipt ownership",
                )
            state_member = await _resolve_exact_member(resolve_state_revision_receipt(
                domain_session,
                global_domain_experiment_id=domain_id,
                state_revision_id=state_id,
            ))
        lineage = []
        lineage.append({
            "relation": "uses_input", "entity_kind": "ngs_molbio_state_revision",
            "entity_id": urlencode({"global_domain_experiment_id": domain_id, "state_revision_id": state_id}),
            "receipt_content_digest": state_member.content_digest,
        })
        return _exact_member_receipt(
            self, requested_entity_id=entity_id, member=member,
            reopen_uri=_query_uri("/ngs", job_id=entity_id),
            metadata={"job_id": entity_id, "job_status": str(job.status), "workflow_id": params.get("ont_workflow_id"), "global_domain_experiment_id": domain_id, "native_member_receipt_id": native_receipt_id, "native_lineage": lineage, "result_contract_id": "ngs_job_launch_v1"},
        )


class ExactNgsResultManifestAdapter:
    adapter_id = "bms.ngs.result-manifest.adapter.v1"
    adapter_version = 1
    display_name = "Exact NGS result manifest"
    entity_kind = "ngs_result_manifest"
    domain_kind = "ngs_molbio"
    store_id = "core-ngs"
    source_schemas = frozenset({
        "biomodstack.construct_verification.v2",
        "sequence_qc.manifest.v1",
        "bms.sequence-qc.manifest.v1",
        "bms.sequence-qc.manifest.v2",
    })
    fixed_revision_marker = "result-manifest"

    def __init__(self, *, domain_session_factory: Callable[[], Any] = molbio_ngs_session_factory):
        self._domain_sessions = domain_session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(Job).where(Job.model_id == "nanopore")
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(or_(Job.id.ilike(pattern), Job.name.ilike(pattern)))
        rows = list((await core_session.scalars(statement.order_by(Job.created_at.desc()).limit(limit))).all())
        return [EntityProjection(urlencode({"job_id": row.id, "manifest_identity": "sequence-qc-manifest"}), self.entity_kind, _bounded_label(row.name, row.id), str(row.status), {"job_id": row.id, "manifest_identity": "sequence-qc-manifest"}) for row in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        identity = _parse_composite_identity(entity_id, ("job_id", "manifest_identity"))
        member = await _resolve_exact_member(resolve_ngs_result_manifest_receipt(core_session, **identity))
        job_member = await _resolve_exact_member(resolve_ngs_job_receipt(core_session, job_id=identity["job_id"]))
        job = await core_session.get(Job, identity["job_id"])
        if job is None:
            raise AdapterError("entity_not_found", "NGS result owner Job does not exist")
        params = job.params if isinstance(job.params, dict) else {}
        claimed_domain_id = params.get("global_domain_experiment_id")
        if not isinstance(claimed_domain_id, str) or not claimed_domain_id:
            raise AdapterError("source_contract_invalid", "NGS result owner Job lacks exact Domain ownership")
        async with self._domain_sessions() as domain_session:
            domain_id, native_receipt_id = await _exact_local_member_authority(
                domain_session, member=member
            )
        if claimed_domain_id != domain_id:
            raise AdapterError(
                "source_contract_invalid",
                "NGS result owner Job claimed Domain diverges from persisted manifest-receipt ownership",
            )
        return _exact_member_receipt(
            self, requested_entity_id=entity_id, member=member,
            reopen_uri=_query_uri("/ngs", **identity),
            metadata={
                **identity, "job_status": str(job.status),
                "global_domain_experiment_id": domain_id,
                "native_member_receipt_id": native_receipt_id,
                "native_lineage": [{
                    "relation": "derived_from",
                    "entity_kind": "ngs_job",
                    "entity_id": identity["job_id"],
                    "receipt_content_digest": job_member.content_digest,
                }],
                "result_contract_id": "ngs_result_manifest_v1",
            },
        )


class ExactEvidenceAssessmentAdapter:
    adapter_id = "bms.ngs-molbio.evidence-assessment.adapter.v1"
    adapter_version = 1
    display_name = "Exact NGS/MolBio evidence assessment"
    entity_kind = "ngs_evidence_assessment"
    domain_kind = "ngs_molbio"
    store_id = "molbio-ngs-domain"
    source_schemas = frozenset({"bms.molbio-ngs.ngs-evidence-receipt.v1"})
    fixed_revision_marker = "1"

    def __init__(self, *, session_factory: Callable[[], Any] = molbio_ngs_session_factory):
        self._sessions = session_factory

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        del core_session
        normalized = _search_inputs(query, limit)
        async with self._sessions() as session:
            statement = select(MolBioNGSEvidenceAssessment)
            if normalized:
                pattern = f"%{normalized}%"
                statement = statement.where(or_(MolBioNGSEvidenceAssessment.evidence_id.ilike(pattern), MolBioNGSEvidenceAssessment.global_domain_experiment_id.ilike(pattern)))
            rows = list((await session.scalars(statement.order_by(MolBioNGSEvidenceAssessment.created_at.desc()).limit(limit))).all())
        return [EntityProjection(urlencode({"global_domain_experiment_id": row.global_domain_experiment_id, "evidence_id": row.evidence_id}), self.entity_kind, _bounded_label(row.evidence_id, row.evidence_id), "immutable", {"scientific_assessment": row.scientific_assessment}) for row in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        del core_session
        identity = _parse_composite_identity(entity_id, ("global_domain_experiment_id", "evidence_id"))
        async with self._sessions() as session:
            member = await _resolve_exact_member(resolve_evidence_assessment_receipt(session, **identity))
            assessment = await session.get(MolBioNGSEvidenceAssessment, identity["evidence_id"])
            if assessment is None or assessment.global_domain_experiment_id != identity["global_domain_experiment_id"]:
                raise AdapterError("entity_not_found", "evidence assessment does not exist")
            state_member = await _resolve_exact_member(resolve_state_revision_receipt(
                session,
                global_domain_experiment_id=identity["global_domain_experiment_id"],
                state_revision_id=assessment.state_revision_id,
            ))
            receipt_ids = [
                assessment.ngs_job_receipt_id,
                assessment.ngs_result_manifest_receipt_id,
                assessment.ngs_reference_revision_receipt_id,
                assessment.ont_instrument_run_receipt_id,
                assessment.molecular_revision_receipt_id,
                assessment.ngs_comparison_panel_receipt_id,
            ]
            rows = {row.receipt_id: row for row in (await session.scalars(select(MolBioNGSMemberReceipt).where(MolBioNGSMemberReceipt.receipt_id.in_([item for item in receipt_ids if item])))).all()}
        lineage = [{
            "relation": "validated_by",
            "entity_kind": "ngs_molbio_state_revision",
            "entity_id": urlencode({"global_domain_experiment_id": identity["global_domain_experiment_id"], "state_revision_id": assessment.state_revision_id}),
            "receipt_content_digest": state_member.content_digest,
        }]
        if assessment.sample_revision_id:
            async with self._sessions() as session:
                sample = await session.get(MolBioNGSSampleRevision, assessment.sample_revision_id)
            if sample is None or sample.global_domain_experiment_id != identity["global_domain_experiment_id"]:
                raise AdapterError("source_contract_invalid", "evidence sample authority is unavailable")
            sample_identity = urlencode({"global_domain_experiment_id": identity["global_domain_experiment_id"], "sample_id": sample.sample_id, "sample_revision_id": sample.id})
            lineage.append({"relation": "validated_by", "entity_kind": "sample_revision", "entity_id": sample_identity, "receipt_content_digest": sample.payload_sha256})
        for receipt_id in receipt_ids:
            if receipt_id is None:
                continue
            row = rows.get(receipt_id)
            if row is None:
                raise AdapterError("source_contract_invalid", "evidence references an unavailable native receipt")
            try:
                authority = serialize_external_member_receipt(row)
            except ValueError as exc:
                raise AdapterError("source_digest_mismatch", str(exc)) from exc
            duplicated = {
                "receipt_id": row.receipt_id,
                "source_store_id": row.source_store_id,
                "entity_kind": row.entity_kind,
                "entity_id": row.entity_id,
                "source_generation_or_revision": row.source_generation_or_revision,
                "content_digest": row.content_digest,
                "availability": row.availability,
                "created_at": row.created_at,
            }
            if any(authority[key] != value for key, value in duplicated.items()):
                raise AdapterError("source_digest_mismatch", "evidence member receipt columns diverge from canonical authority")
            lineage.append({
                "relation": "validated_by", "entity_kind": authority["entity_kind"],
                "entity_id": _member_global_entity_id(authority),
                "receipt_content_digest": authority["content_digest"],
            })
        return _exact_member_receipt(
            self, requested_entity_id=entity_id, member=member,
            reopen_uri=_query_uri(f"/molbio-ngs/domain-experiments/{identity['global_domain_experiment_id']}", evidence_id=identity["evidence_id"]),
            metadata={
                **identity,
                "scientific_assessment": assessment.scientific_assessment,
                "assessment_rule_id": assessment.assessment_rule_id,
                "job_lifecycle_state": assessment.job_lifecycle_state,
                "manifest_integrity": assessment.manifest_integrity,
                "scientific_assessment_reason": (
                    f"rule={assessment.assessment_rule_id}; "
                    f"job_lifecycle_state={assessment.job_lifecycle_state}; "
                    f"manifest_integrity={assessment.manifest_integrity}"
                ),
                "global_domain_experiment_id": identity["global_domain_experiment_id"],
                "native_lineage": lineage,
                "result_contract_id": "ngs_evidence_assessment_v1",
            },
        )


class TypedCoreJobResultAdapter:
    domain_kind = "protein_in_silico"
    store_id = "core"
    adapter_version = 1
    entity_kind = "typed_core_job_result"

    def __init__(self, model_id: str):
        self.model_id = model_id
        self.adapter_id = f"bms.core-job.{model_id}.adapter.v1"
        self.display_name = f"Typed core Job result: {model_id}"

    async def search(self, core_session: AsyncSession, *, query: str, limit: int) -> list[EntityProjection]:
        normalized = _search_inputs(query, limit)
        statement = select(Job).where(Job.model_id == self.model_id)
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(or_(Job.id.ilike(pattern), Job.name.ilike(pattern)))
        rows = list((await core_session.scalars(statement.order_by(Job.created_at.desc()).limit(limit))).all())
        return [EntityProjection(
            entity_id=row.id,
            entity_kind=self.entity_kind,
            label=_bounded_label(row.name, row.id),
            canonical_state=str(row.status),
            metadata={"model_id": row.model_id, "mode": row.mode},
        ) for row in rows]

    async def verify(self, core_session: AsyncSession, entity_id: str) -> dict[str, Any]:
        job = await core_session.get(Job, entity_id)
        if job is None or job.model_id != self.model_id:
            raise AdapterError("entity_not_found", "typed core Job does not exist for this adapter")
        if str(job.status).lower() not in {"completed", "succeeded", "failed", "cancelled", "canceled"}:
            raise AdapterError("source_contract_unavailable", "typed core Job is not terminal")
        authority = {
            "job_id": job.id,
            "model_id": job.model_id,
            "mode": job.mode,
            "status": job.status,
            "params_sha256": _canonical_json_sha256(dict(job.params or {})),
            "stage_outputs": job.stage_outputs or {},
            "provenance": job.provenance or {},
        }
        content_digest = _canonical_json_sha256(authority)
        return _receipt(
            self,
            entity_id=job.id,
            content_digest=content_digest,
            contract_digest=_canonical_json_sha256({"model_id": job.model_id, "mode": job.mode, "params": job.params or {}}),
            reopen_uri=f"/jobs/{job.id}",
            metadata={
                "canonical_state": str(job.status),
                "job_status": str(job.status),
                "model_id": job.model_id,
                "mode": job.mode,
                "result_contract_id": "typed_core_job_result_v1",
            },
        )


_TYPED_CORE_RESULT_MODELS = {
    "boltz2", "boltz_cp_experimental", "boltzgen", "esmfold2", "ppiflow",
    "protein_modification_experimental", "protenix", "rf3", "template_antibody_denovo",
}


for _adapter in (
    CoreProteinResultAdapter(),
    Rfd3LocalRedesignAdapter(),
    ConformationalMappingProtenixAdapter(),
    ConformationalMappingConfornetsAdapter(),
    MolecularDynamicsResultAdapter(),
    FrustraMpnnResultAdapter(),
    FrustraMpnnComparisonAdapter(),
    FrustraMpnnGuidanceAdapter(),
    MolBioRevisionAdapter(),
    MolBioConstructAdapter(),
    MolBioOperationAdapter(),
    NgsExpectedReferenceReceiptAdapter(),
    NgsReferenceSetAdapter(),
    OntInstrumentRunAdapter(),
    NgsPooledAssignmentReleaseAdapter(),
    SequenceQcReferenceAdapter(),
    NgsAnalysisReferenceAdapter(),
    NgsAlignmentViewerReferenceAdapter(),
    ExactMolecularRevisionMemberAdapter(),
    ExactPrimerRevisionAdapter(),
    ExactPcrExperimentRevisionAdapter(),
    ExactMolecularOperationMemberAdapter(),
    ExactSampleRevisionAdapter(),
    ExactStateRevisionAdapter(),
    ExactNgsReferenceRevisionAdapter(),
    ExactNgsComparisonPanelAdapter(),
    ExactOntObservationAdapter(),
    ExactNgsJobAdapter(),
    ExactNgsResultManifestAdapter(),
    ExactEvidenceAssessmentAdapter(),
):
    registry.register(_adapter)

for _model_id in sorted(_TYPED_CORE_RESULT_MODELS):
    registry.register(TypedCoreJobResultAdapter(_model_id))
