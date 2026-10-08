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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import parse_qs, urlencode

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    ConformationalMappingArtifact,
    ConformationalMappingRecord,
    ConformationalMappingRequest,
    Design,
    FrustraMPNNResult,
    Job,
    MdRun,
    MolBioNgsReceipt,
    NgsReferenceSetManifest,
    RFD3LocalRedesignRequest,
)
from molbio_database import molbio_session
from molbio_models import MolecularRevision
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
from services.molbio_ngs_workup import safe_job_result_root


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
    value = str(os.getenv("BMS_BUILD_SHA") or os.getenv("GIT_SHA") or "").strip()
    if not value or len(value) > 160:
        raise AdapterError(
            "source_revision_unavailable",
            "source build revision is unavailable; fail closed rather than issue unverifiable receipt",
        )
    return value


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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file(path_value: Any, expected_sha256: Any, expected_size: Any = None) -> Path:
    digest = _sha256(expected_sha256, "artifact sha256")
    if not isinstance(path_value, str) or not path_value.strip():
        raise AdapterError("source_contract_invalid", "native artifact path is unavailable")
    path = Path(path_value)
    try:
        if path.is_symlink() or not path.is_file():
            raise AdapterError("source_artifact_unavailable", "native artifact is unavailable or unsafe")
        size = path.stat().st_size
        if size < 0 or size > _MAX_FILE_BYTES:
            raise AdapterError("source_artifact_unavailable", "native artifact exceeds verification bounds")
        if expected_size is not None and (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or expected_size != size
        ):
            raise AdapterError("source_digest_mismatch", "native artifact size does not match authority")
        if _file_sha256(path) != digest:
            raise AdapterError("source_digest_mismatch", "native artifact digest does not match authority")
    except AdapterError:
        raise
    except OSError as exc:
        raise AdapterError("source_artifact_unavailable", "native artifact could not be verified") from exc
    return path


def _receipt(
    adapter: DomainAdapter,
    *,
    entity_id: str,
    content_digest: str,
    contract_digest: str | None,
    reopen_uri: str,
    metadata: dict[str, Any],
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
        "entity_revision_id": contract_digest or content_digest,
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
        _verify_file(authoritative.get("path"), authoritative.get("sha256"), authoritative.get("bytes"))
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


class Rfd3LocalRedesignAdapter:
    adapter_id = "bms.rfd3.local-redesign-reference.adapter.v1"
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
        if str(record.status) == "completed" and result_digest is None:
            raise AdapterError("source_contract_unavailable", "completed RFD3 result has no authoritative manifest digest")
        content_digest = _sha256(result_digest, "RFD3 result manifest digest") if result_digest else expected_request_digest
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
        record_digests: list[dict[str, str]] = []
        for record in records:
            observed = cm_canonical_sha256(record.payload_json)
            if observed != _sha256(record.content_sha256, "conformational-mapping record digest"):
                raise AdapterError("source_digest_mismatch", "conformational-mapping record digest is invalid")
            record_digests.append({"record_type": record.record_type, "record_key": record.record_key, "sha256": observed})
        artifact_digests: list[dict[str, Any]] = []
        for artifact in artifacts:
            _verify_file(artifact.storage_path, artifact.content_sha256, artifact.size_bytes)
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
            reopen_uri=f"/designs/{row.parent_job_id}",
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
        path = _verify_file(receipt.reference_snapshot_path, reference_sha)
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
        root = safe_job_result_root(job)
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
        if job is None or job.model_id not in _NGS_MODEL_IDS:
            raise AdapterError("entity_not_found", "sequence-QC job does not exist")
        manifest_path = _sequence_qc_manifest_path(job)
        try:
            if manifest_path.stat().st_size > 10 * 1024 * 1024:
                raise AdapterError("source_contract_unbounded", "sequence-QC manifest exceeds adapter bounds")
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            native = load_sequence_qc_manifest(manifest_path)
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


for _adapter in (
    CoreProteinResultAdapter(),
    Rfd3LocalRedesignAdapter(),
    ConformationalMappingProtenixAdapter(),
    ConformationalMappingConfornetsAdapter(),
    MolecularDynamicsResultAdapter(),
    FrustraMpnnResultAdapter(),
    MolBioRevisionAdapter(),
    NgsExpectedReferenceReceiptAdapter(),
    NgsReferenceSetAdapter(),
    SequenceQcReferenceAdapter(),
    NgsAlignmentViewerReferenceAdapter(),
):
    registry.register(_adapter)
