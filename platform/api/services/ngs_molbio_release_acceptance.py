"""Fail-closed validation for the final shared-package acceptance receipt.

This module can materialize a receipt only after an external release lane supplies
closed, content-addressed evidence. It verifies retained evidence bytes, live fenced
migration/source authority, active-work quiescence, and exact package provenance.
It never resolves Git state or launches scientific work.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Mapping

import rfc8785  # type: ignore[import-not-found]
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import Text, cast, func, or_, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job
from experiment_migrations import (
    LATEST_MIGRATION_VERSION as EXPERIMENT_MIGRATION_VERSION,
    attest_schema as attest_experiment_schema,
)
from experiment_models import (
    ExperimentDispatchOutbox,
    ExperimentDomainConnectorCommand,
    ExperimentDomainConnectorInbox,
    ExperimentLaunchContext,
    ExperimentOperationalReceipt,
    ExperimentArtifactBlob,
    ExperimentResourceAdmission,
    ExperimentRunAttempt,
    ExperimentRunControlCommand,
    ExperimentRunGroup,
)
from experiment_services import (
    _failed_outbox_materialization_evidence,
    _failed_outbox_recovery_is_open,
    canonical_json,
    now,
)
from molbio_database import MOLBIO_MIGRATIONS, molbio_health, molbio_session
from molbio_models import MolecularOperation
from molbio_ngs_database import molbio_ngs_session_factory
from molbio_ngs_migrations import (
    LATEST_MIGRATION_VERSION as MOLBIO_NGS_MIGRATION_VERSION,
    attest_schema as attest_molbio_ngs_schema,
)
from molbio_ngs_models import (
    MolBioNGSConnectorAcknowledgement,
    MolBioNGSIdempotencyClaim,
    MolBioNGSOutboxEvent,
)
from services.ngs_molbio_quiescence import (
    NgsMolBioQuiescenceError,
    package_acceptance_exclusive_fence,
)
from services.ngs_molbio_runtime_status import runtime_implementation_record

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_PATH = _REPO_ROOT / "schemas/ngs_molbio_runtime/shared-global-package-acceptance-v1.schema.json"
_EVIDENCE_SCHEMA_PATH = _REPO_ROOT / "schemas/ngs_molbio_runtime/shared-global-package-evidence-v1.schema.json"
_N0_RECEIPT_PATH = _REPO_ROOT / "docs/reports/ngs-molbio-phase-n0-verification-v1.json"
_REQUIRED_MIGRATION_STORES = {
    "global-experiments": EXPERIMENT_MIGRATION_VERSION,
    "molbio-domain": MOLBIO_MIGRATIONS[-1][0],
    "molbio-ngs-domain": MOLBIO_NGS_MIGRATION_VERSION,
}
_TERMINAL_RUNTIME_STATES = ("completed", "failed", "cancelled", "awaiting_input")
_TERMINAL_RUN_GROUP_STATES = ("completed", "failed", "cancelled")
_ACTIVE_DISPATCH_OUTBOX_STATES = ("pending", "dispatching")
_ACTIVE_RUN_CONTROL_STATES = ("pending", "leased", "retryable")
_ACTIVE_ADMISSION_STATES = ("admitted", "queued")
_ACTIVE_CONNECTOR_COMMAND_STATES = ("pending", "leased", "retryable")
_ACTIVE_LOCAL_OUTBOX_STATES = ("pending", "leased", "retryable_error")
_ACTIVE_LOCAL_CONNECTOR_ACK_STATES = ("retryable", "deferred_gap")
_LOCAL_SOURCE_STORE_ID = "bms.molbio-ngs.domain-store.v1"
_CORE_EXECUTION_ATTEMPTS_PARAM = "execution_attempts"
_CORE_EXECUTION_ATTEMPT_SCHEMA = "bms.workflow-execution-attempt.v1"
_CORE_EXECUTION_TERMINAL_STATES = frozenset(
    {"completed", "failed", "cancelled", "interrupted_owner", "rejected"}
)
_CORE_EXECUTION_ACTIVE_STATES = frozenset({"planned", "started"})
_MAX_CORE_EXECUTION_OWNER_ROWS = 10_000
_MAX_CORE_EXECUTION_ATTEMPTS_PER_JOB = 10_000
_MAX_RECOVERABLE_DISPATCH_ROWS = 10_000
_MAX_CONNECTOR_CONVERGENCE_ROWS = 10_000
_MAX_EVIDENCE_RECEIPT_BYTES = 262_144
_MAX_IDENTITY_SCAN_DEPTH = 64
_MAX_IDENTITY_SCAN_NODES = 10_000
_PACKAGE_EVIDENCE_SCHEMAS = {
    "migration": "bms.shared-package.migration-acceptance.v1",
    "connector": "bms.shared-package.connector-acceptance.v1",
    "workflow": "bms.shared-package.workflow-acceptance.v1",
    "development_deployment": "bms.shared-package.development-deployment.v1",
    "exact_tree_review": "bms.shared-package.exact-tree-review.v1",
    "health": "bms.shared-package.health-acceptance.v1",
    "browser": "bms.shared-package.browser-acceptance.v1",
    "focused_check": "bms.shared-package.focused-check.v1",
}


class SharedPackageAcceptanceError(RuntimeError):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SharedPackageAcceptanceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    except SharedPackageAcceptanceError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SharedPackageAcceptanceError(f"acceptance authority is unreadable: {path}") from exc
    if type(value) is not dict:
        raise SharedPackageAcceptanceError(f"acceptance authority must be an object: {path}")
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _content_sha256(document: Mapping[str, Any]) -> str:
    value = dict(document)
    value.pop("content_sha256", None)
    return _sha256(rfc8785.dumps(value))


def _receipt_document(row: ExperimentOperationalReceipt) -> dict[str, Any]:
    try:
        receipt_bytes = row.receipt_json.encode("utf-8")
    except UnicodeError as exc:
        raise SharedPackageAcceptanceError(
            f"operational receipt {row.receipt_id} is not valid UTF-8"
        ) from exc
    if len(receipt_bytes) > _MAX_EVIDENCE_RECEIPT_BYTES:
        raise SharedPackageAcceptanceError(
            f"operational receipt {row.receipt_id} exceeds its fail-closed byte bound"
        )
    try:
        value = json.loads(row.receipt_json, object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, SharedPackageAcceptanceError) as exc:
        raise SharedPackageAcceptanceError(f"operational receipt {row.receipt_id} is invalid JSON") from exc
    if type(value) is not dict:
        raise SharedPackageAcceptanceError(f"operational receipt {row.receipt_id} is not an object")
    if _sha256(receipt_bytes) != row.receipt_sha256:
        raise SharedPackageAcceptanceError(f"operational receipt {row.receipt_id} digest mismatch")
    return value


def _receipt_source_identities(receipt: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Collect every source/build/deployment identity and reject partial aliases."""
    identities: list[tuple[str, str, str]] = []
    visited_nodes = 0
    groups = (
        (("source_commit", "source_revision"), ("source_tree",), "source"),
        (("build_commit", "build_revision", "source_build_revision"), ("build_tree", "source_build_tree"), "build"),
        (("deployment_commit", "deployment_revision"), ("deployment_tree",), "deployment"),
    )

    def collect(
        value: Any,
        path: str,
        identity_context: bool = False,
        depth: int = 0,
    ) -> None:
        nonlocal visited_nodes
        visited_nodes += 1
        if depth > _MAX_IDENTITY_SCAN_DEPTH or visited_nodes > _MAX_IDENTITY_SCAN_NODES:
            raise SharedPackageAcceptanceError(
                "operational receipt identity scan exceeded its fail-closed bound"
            )
        if isinstance(value, Mapping):
            for commit_keys, tree_keys, label in groups:
                commits = [value[key] for key in commit_keys if key in value]
                trees = [value[key] for key in tree_keys if key in value]
                if commits or trees:
                    if (
                        not commits
                        or not trees
                        or any(type(item) is not str or not item for item in commits + trees)
                        or len(set(commits)) != 1
                        or len(set(trees)) != 1
                    ):
                        raise SharedPackageAcceptanceError(
                            f"operational receipt has partial or contradictory {label} identity at {path}"
                        )
                    identities.append((str(commits[0]), str(trees[0]), f"{path}:{label}"))
            if identity_context:
                commits = [value[key] for key in ("commit", "revision") if key in value]
                trees = [value["tree"]] if "tree" in value else []
                if commits or trees:
                    if (
                        not commits
                        or not trees
                        or any(type(item) is not str or not item for item in commits + trees)
                        or len(set(commits)) != 1
                        or len(set(trees)) != 1
                    ):
                        raise SharedPackageAcceptanceError(
                            f"operational receipt has partial or contradictory identity at {path}"
                        )
                    identities.append((str(commits[0]), str(trees[0]), f"{path}:identity"))
            for key, child in value.items():
                collect(
                    child,
                    f"{path}.{key}",
                    key in {"source_identity", "build_identity", "deployment_identity"},
                    depth + 1,
                )
        elif isinstance(value, list):
            for index, child in enumerate(value):
                collect(child, f"{path}[{index}]", identity_context, depth + 1)

    collect(receipt, "<root>")
    return identities


def _validate_receipt_source_identities(
    receipt: Mapping[str, Any],
    *,
    expected_commit: str,
    expected_tree: str,
    receipt_id: str,
) -> None:
    identities = _receipt_source_identities(receipt)
    if not identities:
        raise SharedPackageAcceptanceError(
            f"operational receipt {receipt_id} contains no complete source/build/deployment identity"
        )
    for commit, tree, path in identities:
        if commit != expected_commit or tree != expected_tree:
            raise SharedPackageAcceptanceError(
                f"operational receipt {receipt_id} exact-tree identity mismatch at {path}"
            )


def _nonempty_string(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def _validate_closed_package_evidence(
    body: Mapping[str, Any],
    *,
    receipt_id: str,
    expected_runtime_implementation_sha256: str,
) -> None:
    schema = _read(_EVIDENCE_SCHEMA_PATH)
    errors = sorted(_validator(schema).iter_errors(dict(body)), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "$"
        raise SharedPackageAcceptanceError(
            f"operational receipt {receipt_id} violates its closed evidence schema at {location}: {first.message}"
        )
    if body.get("runtime_implementation_sha256") != expected_runtime_implementation_sha256:
        raise SharedPackageAcceptanceError(
            f"operational receipt {receipt_id} runtime implementation digest mismatch"
        )


def _package_evidence_artifacts(body: Mapping[str, Any]) -> dict[str, int]:
    artifacts = body.get("evidence_artifacts")
    if not isinstance(artifacts, list):
        return {}
    retained: dict[str, int] = {}
    for artifact in artifacts:
        if type(artifact) is not dict:
            raise SharedPackageAcceptanceError("package evidence artifact shape is invalid")
        digest = artifact.get("sha256")
        if artifact.get("artifact_id") != digest or type(digest) is not str:
            raise SharedPackageAcceptanceError("package evidence artifacts must use content-addressed identities")
        size = artifact.get("size_bytes")
        if type(size) is not int or size < 1:
            raise SharedPackageAcceptanceError("package evidence artifact size is invalid")
        previous = retained.setdefault(digest, size)
        if previous != size:
            raise SharedPackageAcceptanceError("package evidence artifact size authority conflicts")
    return retained


async def _verify_retained_package_artifacts(
    session: AsyncSession,
    required: Mapping[str, int],
) -> None:
    if not required or len(required) > 512:
        raise SharedPackageAcceptanceError("retained package evidence artifact authority is empty or exceeds its bound")
    rows = list((await session.scalars(
        select(ExperimentArtifactBlob).where(ExperimentArtifactBlob.sha256.in_(required))
    )).all())
    by_digest = {row.sha256: row for row in rows}
    if set(by_digest) != set(required):
        raise SharedPackageAcceptanceError("one or more package evidence artifacts are not retained")
    for digest, size in required.items():
        row = by_digest[digest]
        if row.size_bytes != size or row.state != "present" or not row.verified_at:
            raise SharedPackageAcceptanceError(
                f"package evidence artifact {digest} lacks verified retained bytes"
            )


def _validate_evidence_semantics(
    body: Mapping[str, Any],
    row: ExperimentOperationalReceipt,
    *,
    evidence_kind: str,
    store_id: str | None,
) -> None:
    if evidence_kind in _PACKAGE_EVIDENCE_SCHEMAS:
        if (
            body.get("schema") != _PACKAGE_EVIDENCE_SCHEMAS[evidence_kind]
            or body.get("evidence_kind") != evidence_kind
            or body.get("native_identity") != row.native_identity
            or body.get("outcome") != "pass"
            or body.get("verified_at") != row.verified_at
            or not _nonempty_string(body.get("verifier_id"))
            or body.get("verification_authority") != "authenticated_operator"
        ):
            raise SharedPackageAcceptanceError(
                f"operational receipt {row.receipt_id} is not valid {evidence_kind} package evidence"
            )
        if evidence_kind == "migration":
            attestation = body.get("schema_attestation")
            if (
                store_id is None
                or body.get("store_id") != store_id
                or body.get("migration_version") != _REQUIRED_MIGRATION_STORES[store_id]
                or type(attestation) is not dict
            ):
                raise SharedPackageAcceptanceError(
                    f"operational receipt {row.receipt_id} migration store/version/attestation mismatch"
                )
        elif evidence_kind == "connector" and body.get("connector_status") != "healthy":
            raise SharedPackageAcceptanceError("connector acceptance evidence is not healthy")
        elif evidence_kind == "workflow" and (
            not _nonempty_string(body.get("workflow_id")) or body.get("workflow_status") != "pass"
        ):
            raise SharedPackageAcceptanceError("workflow acceptance evidence is not passing")
        elif evidence_kind == "development_deployment" and (
            body.get("environment") != "Development" or body.get("deployment_status") != "deployed"
        ):
            raise SharedPackageAcceptanceError("Development deployment evidence is not deployed")
        elif evidence_kind == "exact_tree_review" and body.get("review_status") != "pass":
            raise SharedPackageAcceptanceError("exact-tree review evidence is not passing")
        elif evidence_kind == "health" and body.get("health_status") != "healthy":
            raise SharedPackageAcceptanceError("health acceptance evidence is not healthy")
        elif evidence_kind == "browser" and body.get("scenario_status") != "pass":
            raise SharedPackageAcceptanceError("browser acceptance evidence is not passing")
        elif evidence_kind == "focused_check" and (
            not _nonempty_string(body.get("check_id")) or body.get("check_status") != "pass"
        ):
            raise SharedPackageAcceptanceError("focused-check evidence is not passing")
        return
    if evidence_kind == "backup":
        if (
            body.get("schema") != "bms.experiment.backup-verification.v1"
            or body.get("backup_id") != row.native_identity
            or body.get("outcome") != "pass"
            or body.get("verified") is not True
            or body.get("provenance_valid") is not True
            or not _nonempty_string(body.get("creation_receipt_sha256"))
            or not _nonempty_string(body.get("verifier_id"))
            or body.get("verification_authority") != "authenticated_operator"
        ):
            raise SharedPackageAcceptanceError("backup acceptance evidence is not a passing authenticated verification")
        return
    if evidence_kind == "restoration":
        if (
            body.get("schema") != "bms.experiment.restoration.v1"
            or body.get("restore_id") != row.native_identity
            or body.get("outcome") != "pass"
            or body.get("verified") is not True
            or body.get("activated") is not False
            or not _nonempty_string(body.get("backup_creation_receipt_sha256"))
            or not _nonempty_string(body.get("verifier_id"))
            or body.get("verification_authority") != "authenticated_operator"
        ):
            raise SharedPackageAcceptanceError("restoration evidence is not a passing authenticated staging drill")
        return
    if evidence_kind == "export":
        if (
            body.get("schema") != "bms.experiment.workspace-export-verification.v1"
            or body.get("export_id") != row.native_identity
            or body.get("outcome") != "pass"
            or body.get("verified") is not True
            or body.get("provenance_valid") is not True
            or type(body.get("artifact_count")) is not int
            or body.get("artifact_count", -1) < 0
            or body.get("verified_artifact_count") != body.get("artifact_count")
            or not _nonempty_string(body.get("artifact_results_sha256"))
            or not _nonempty_string(body.get("creation_receipt_sha256"))
            or not _nonempty_string(body.get("verifier_id"))
            or body.get("verification_authority") != "authenticated_operator"
        ):
            raise SharedPackageAcceptanceError("export evidence is not a passing creation-bound verification")
        return
    if evidence_kind == "payload_audit":
        if (
            body.get("schema") != "bms.payload-ownership-retention-receipt.v1"
            or body.get("audit_id") != row.native_identity
            or body.get("outcome") != "pass"
            or body.get("no_active_jobs") is not True
            or not _nonempty_string(body.get("scanner_id"))
            or body.get("finding_count") != 0
        ):
            raise SharedPackageAcceptanceError("payload ownership evidence is not a passing retained audit")
        return
    raise SharedPackageAcceptanceError(f"unsupported acceptance evidence kind: {evidence_kind}")


async def _live_migration_authorities(
    experiment_session: AsyncSession,
    molbio_session_: AsyncSession,
    molbio_ngs_session_: AsyncSession,
) -> dict[str, dict[str, Any]]:
    def sqlite_path(session: AsyncSession, store_id: str) -> Path:
        bind = session.bind
        url = getattr(bind, "url", None)
        if url is None:
            sync_bind = session.sync_session.get_bind()
            url = getattr(sync_bind, "url", None)
        parsed = make_url(str(url)) if url is not None else None
        if (
            parsed is None
            or not parsed.drivername.startswith("sqlite")
            or not parsed.database
            or parsed.database == ":memory:"
        ):
            raise SharedPackageAcceptanceError(
                f"{store_id} migration attestation requires one file-backed SQLite authority"
            )
        return Path(parsed.database).expanduser().resolve()

    def source_attestation(
        path: Path,
        attestor: Any,
        store_id: str,
    ) -> dict[str, Any]:
        try:
            connection = sqlite3.connect(
                f"file:{path.as_posix()}?mode=ro",
                uri=True,
                timeout=30,
            )
        except sqlite3.Error as exc:
            raise SharedPackageAcceptanceError(
                f"{store_id} migration authority is unreadable"
            ) from exc
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            attestation = attestor(connection)
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as exc:
            raise SharedPackageAcceptanceError(
                f"{store_id} source-derived migration attestation failed"
            ) from exc
        finally:
            connection.close()
        if type(attestation) is not dict:
            raise SharedPackageAcceptanceError(
                f"{store_id} source-derived migration attestation is invalid"
            )
        return attestation

    async def inspect_store(
        session: AsyncSession,
        *,
        ledger_sql: str,
        expected_version: int | str,
        attestation: Mapping[str, Any],
        molbio_expected: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        rows = list((await session.execute(text(ledger_sql))).all())
        if not rows or len(rows) > 64:
            raise SharedPackageAcceptanceError("migration ledger is missing or exceeds the bounded authority")
        if molbio_expected is not None:
            actual = [(str(row[0]), str(row[1])) for row in rows]
            if actual != molbio_expected or actual[-1][0] != expected_version:
                raise SharedPackageAcceptanceError("MolBio migration ledger does not match source authority")
            migration_checksum = _sha256(canonical_json(actual).encode("utf-8"))
        else:
            versions = [int(row[0]) for row in rows]
            if versions != list(range(1, int(expected_version) + 1)):
                raise SharedPackageAcceptanceError("migration ledger versions are incomplete or divergent")
            migration_checksum = str(rows[-1][2])
        return {
            "migration_version": expected_version,
            "migration_checksum": migration_checksum,
            "schema_attestation": copy.deepcopy(dict(attestation)),
        }

    experiment_attestation = source_attestation(
        sqlite_path(experiment_session, "global-experiments"),
        attest_experiment_schema,
        "global-experiments",
    )
    molbio_ngs_attestation = source_attestation(
        sqlite_path(molbio_ngs_session_, "molbio-ngs-domain"),
        attest_molbio_ngs_schema,
        "molbio-ngs-domain",
    )
    molbio_attestation = await molbio_health(engine=molbio_session_.bind)
    if type(molbio_attestation) is not dict:
        raise SharedPackageAcceptanceError(
            "molbio-domain source-derived migration attestation is invalid"
        )
    return {
        "global-experiments": await inspect_store(
            experiment_session,
            ledger_sql=(
                "SELECT version,name,checksum FROM experiment_schema_migrations "
                "ORDER BY version LIMIT 65"
            ),
            expected_version=EXPERIMENT_MIGRATION_VERSION,
            attestation=experiment_attestation,
        ),
        "molbio-domain": await inspect_store(
            molbio_session_,
            ledger_sql=(
                "SELECT version,description,NULL FROM molbio_schema_migrations "
                "ORDER BY version LIMIT 65"
            ),
            expected_version=MOLBIO_MIGRATIONS[-1][0],
            attestation=molbio_attestation,
            molbio_expected=[(version, description) for version, description, _ in MOLBIO_MIGRATIONS],
        ),
        "molbio-ngs-domain": await inspect_store(
            molbio_ngs_session_,
            ledger_sql=(
                "SELECT version,name,checksum FROM molbio_ngs_schema_migrations "
                "ORDER BY version LIMIT 65"
            ),
            expected_version=MOLBIO_NGS_MIGRATION_VERSION,
            attestation=molbio_ngs_attestation,
        ),
    }


def _validate_live_migration_evidence(
    bodies: Mapping[str, Mapping[str, Any]],
    live: Mapping[str, Mapping[str, Any]],
) -> None:
    if set(bodies) != set(_REQUIRED_MIGRATION_STORES) or set(live) != set(_REQUIRED_MIGRATION_STORES):
        raise SharedPackageAcceptanceError("live migration authority is incomplete")
    for store_id, authority in live.items():
        body = bodies[store_id]
        attestation = body.get("schema_attestation")
        source_attestation = authority["schema_attestation"]
        source_passed = (
            source_attestation.get("status") == "healthy"
            if store_id == "molbio-domain"
            else source_attestation.get("ok") is True
        )
        if (
            body.get("migration_version") != authority["migration_version"]
            or body.get("migration_checksum") != authority["migration_checksum"]
            or type(attestation) is not dict
            or not source_passed
            or attestation != source_attestation
        ):
            raise SharedPackageAcceptanceError(
                f"migration receipt for {store_id} does not match live fenced source authority"
            )


def _evidence_pointers(
    candidate: Mapping[str, Any],
) -> list[tuple[dict[str, Any], str, str, str | None]]:
    pointers: list[tuple[dict[str, Any], str, str, str | None]] = []
    pointers.extend(
        (row, "package_acceptance", "migration", row["store_id"])
        for row in candidate["migration_receipts"]
    )
    pointers.append((candidate["connector_acceptance_receipt"], "package_acceptance", "connector", None))
    pointers.extend(
        (row, "package_acceptance", "workflow", None)
        for row in candidate["workflow_acceptance_receipts"]
    )
    pointers.append((candidate["payload_ownership_audit_receipt"], "payload_audit", "payload_audit", None))
    pointers.append((candidate["backup_receipt"], "backup", "backup", None))
    pointers.append((candidate["restoration_receipt"], "restoration", "restoration", None))
    pointers.append((candidate["export_receipt"], "export", "export", None))
    pointers.append((candidate["development_runtime_receipt"], "package_acceptance", "development_deployment", None))
    pointers.append((candidate["exact_tree_review_receipt"], "package_acceptance", "exact_tree_review", None))
    pointers.append((candidate["health_acceptance_receipt"], "package_acceptance", "health", None))
    pointers.append((candidate["browser_acceptance_receipt"], "package_acceptance", "browser", None))
    pointers.extend(
        (row, "package_acceptance", "focused_check", None)
        for row in candidate["focused_check_receipts"]
    )
    return pointers


async def _recoverable_dispatch_outbox_count(
    experiment_session: AsyncSession,
) -> int:
    failed_rows = list(
        (
            await experiment_session.scalars(
                select(ExperimentDispatchOutbox)
                .where(
                    ExperimentDispatchOutbox.status == "failed",
                    ExperimentDispatchOutbox.acknowledgement_json.is_not(None),
                )
                .order_by(ExperimentDispatchOutbox.id)
                .limit(_MAX_RECOVERABLE_DISPATCH_ROWS + 1)
            )
        ).all()
    )
    if len(failed_rows) > _MAX_RECOVERABLE_DISPATCH_ROWS:
        raise SharedPackageAcceptanceError(
            "recoverable dispatch scan exceeded its fail-closed row bound"
        )
    recoverable = 0
    for row in failed_rows:
        if (
            _failed_outbox_materialization_evidence(row) is not None
            and await _failed_outbox_recovery_is_open(experiment_session, row)
        ):
            recoverable += 1
    return recoverable


async def _connector_convergence_count(
    experiment_session: AsyncSession,
    local_session: AsyncSession,
) -> int:
    local_rows = list(
        (
            await local_session.scalars(
                select(MolBioNGSOutboxEvent)
                .order_by(MolBioNGSOutboxEvent.id)
                .limit(_MAX_CONNECTOR_CONVERGENCE_ROWS + 1)
            )
        ).all()
    )
    inbox_rows = list(
        (
            await experiment_session.scalars(
                select(ExperimentDomainConnectorInbox)
                .where(
                    ExperimentDomainConnectorInbox.source_store_id == _LOCAL_SOURCE_STORE_ID
                )
                .order_by(ExperimentDomainConnectorInbox.event_id)
                .limit(_MAX_CONNECTOR_CONVERGENCE_ROWS + 1)
            )
        ).all()
    )
    if (
        len(local_rows) > _MAX_CONNECTOR_CONVERGENCE_ROWS
        or len(inbox_rows) > _MAX_CONNECTOR_CONVERGENCE_ROWS
    ):
        raise SharedPackageAcceptanceError(
            "connector event convergence scan exceeded its fail-closed row bound"
        )
    local_by_id = {row.id: row for row in local_rows}
    inbox_by_id = {row.event_id: row for row in inbox_rows}
    settled_event_ids = {
        row.id for row in local_rows if row.status == "acknowledged"
    } | {
        row.event_id for row in inbox_rows if row.disposition in {"applied", "duplicate"}
    }
    divergent = 0
    for event_id in settled_event_ids:
        local = local_by_id.get(event_id)
        inbox = inbox_by_id.get(event_id)
        if (
            local is None
            or inbox is None
            or local.status != "acknowledged"
            or inbox.disposition not in {"applied", "duplicate"}
            or local.global_domain_experiment_id != inbox.domain_experiment_id
            or local.binding_revision_id != inbox.binding_revision_id
            or local.state_revision_id != inbox.state_revision_id
            or local.event_type != inbox.event_type
            or local.event_stream != inbox.event_stream
            or local.stream_generation != inbox.stream_generation
            or local.source_generation != inbox.source_generation
            or local.payload_sha256 != inbox.payload_sha256
            or local.acknowledgement_sha256 != inbox.acknowledgement_sha256
        ):
            divergent += 1

    command_rows = list(
        (
            await experiment_session.scalars(
                select(ExperimentDomainConnectorCommand)
                .order_by(ExperimentDomainConnectorCommand.command_id)
                .limit(_MAX_CONNECTOR_CONVERGENCE_ROWS + 1)
            )
        ).all()
    )
    acknowledgement_rows = list(
        (
            await local_session.scalars(
                select(MolBioNGSConnectorAcknowledgement)
                .order_by(MolBioNGSConnectorAcknowledgement.command_id)
                .limit(_MAX_CONNECTOR_CONVERGENCE_ROWS + 1)
            )
        ).all()
    )
    if (
        len(command_rows) > _MAX_CONNECTOR_CONVERGENCE_ROWS
        or len(acknowledgement_rows) > _MAX_CONNECTOR_CONVERGENCE_ROWS
    ):
        raise SharedPackageAcceptanceError(
            "connector command convergence scan exceeded its fail-closed row bound"
        )
    command_by_id = {row.command_id: row for row in command_rows}
    acknowledgement_by_command_id = {row.command_id: row for row in acknowledgement_rows}
    settled_command_ids = {
        row.command_id for row in command_rows if row.status in {"applied", "duplicate"}
    } | set(acknowledgement_by_command_id)
    for command_id in settled_command_ids:
        command = command_by_id.get(command_id)
        acknowledgement = acknowledgement_by_command_id.get(command_id)
        if (
            command is None
            or acknowledgement is None
            or command.status not in {"applied", "duplicate"}
            or command.status != acknowledgement.disposition
            or command.acknowledgement_id != acknowledgement.acknowledgement_id
            or command.binding_revision_id != acknowledgement.binding_revision_id
            or command.acknowledgement_sha256 != acknowledgement.acknowledgement_sha256
        ):
            divergent += 1
    return divergent


async def _active_core_execution_owner_count(core_session: AsyncSession) -> int:
    """Count newest nonterminal execution owners independently of Job status."""
    candidate_rows = list(
        (
            await core_session.execute(
                select(Job.id, Job.params)
                .where(
                    cast(Job.params, Text).contains(
                        f'"{_CORE_EXECUTION_ATTEMPTS_PARAM}"'
                    )
                )
                .order_by(Job.id)
                .limit(_MAX_CORE_EXECUTION_OWNER_ROWS + 1)
            )
        ).all()
    )
    if len(candidate_rows) > _MAX_CORE_EXECUTION_OWNER_ROWS:
        raise SharedPackageAcceptanceError(
            "core execution-owner scan exceeded its fail-closed row bound"
        )

    active = 0
    for job_id, raw_params in candidate_rows:
        if isinstance(raw_params, str):
            try:
                parsed_params = json.loads(raw_params, object_pairs_hook=_pairs)
            except (UnicodeError, json.JSONDecodeError, SharedPackageAcceptanceError) as exc:
                raise SharedPackageAcceptanceError(
                    f"core Job {job_id} has malformed execution-owner parameters"
                ) from exc
        else:
            parsed_params = raw_params
        if not isinstance(parsed_params, Mapping):
            raise SharedPackageAcceptanceError(
                f"core Job {job_id} execution-owner parameters are not an object"
            )
        if _CORE_EXECUTION_ATTEMPTS_PARAM not in parsed_params:
            continue
        history = parsed_params[_CORE_EXECUTION_ATTEMPTS_PARAM]
        if (
            not isinstance(history, list)
            or not history
            or len(history) > _MAX_CORE_EXECUTION_ATTEMPTS_PER_JOB
            or any(not isinstance(item, Mapping) for item in history)
        ):
            raise SharedPackageAcceptanceError(
                f"core Job {job_id} has malformed or unbounded execution-owner history"
            )
        newest = history[-1]
        state = newest.get("state")
        required_text = ("lane", "unit", "owner_nonce", "request_fingerprint", "planned_at")
        if (
            newest.get("schema") != _CORE_EXECUTION_ATTEMPT_SCHEMA
            or state not in _CORE_EXECUTION_ACTIVE_STATES | _CORE_EXECUTION_TERMINAL_STATES
            or type(newest.get("generation")) is not int
            or newest["generation"] < 1
            or type(newest.get("attempt")) is not int
            or newest["attempt"] < 1
            or any(not _nonempty_string(newest.get(field)) for field in required_text)
        ):
            raise SharedPackageAcceptanceError(
                f"core Job {job_id} has malformed newest execution-owner receipt"
            )
        if state in _CORE_EXECUTION_ACTIVE_STATES:
            active += 1
    return active


async def _verify_no_active_authority(
    experiment_session: AsyncSession,
    core_session: AsyncSession,
    local_session: AsyncSession,
    molbio_source_session: AsyncSession,
) -> None:
    current_time = now().replace("+00:00", "Z")
    counts = {
        "run_groups": int(
            await experiment_session.scalar(
                select(func.count(ExperimentRunGroup.resource_id)).where(
                    or_(
                        ExperimentRunGroup.state.is_(None),
                        ExperimentRunGroup.state.notin_(_TERMINAL_RUN_GROUP_STATES),
                    )
                )
            )
            or 0
        ),
        "run_attempts": int(
            await experiment_session.scalar(
                select(func.count(ExperimentRunAttempt.resource_id)).where(
                    or_(
                        ExperimentRunAttempt.state.is_(None),
                        ExperimentRunAttempt.state.notin_(_TERMINAL_RUNTIME_STATES),
                    )
                )
            )
            or 0
        ),
        "jobs": int(
            await core_session.scalar(
                select(func.count(Job.id)).where(
                    or_(Job.status.is_(None), Job.status.notin_(_TERMINAL_RUNTIME_STATES))
                )
            )
            or 0
        ),
        "core_execution_owners": await _active_core_execution_owner_count(core_session),
        "molecular_operations": int(
            await molbio_source_session.scalar(
                select(func.count(MolecularOperation.id)).where(
                    or_(
                        MolecularOperation.status.is_(None),
                        MolecularOperation.status.notin_(_TERMINAL_RUNTIME_STATES),
                    )
                )
            )
            or 0
        ),
        "dispatch_outbox": int(
            await experiment_session.scalar(
                select(func.count(ExperimentDispatchOutbox.id)).where(
                    ExperimentDispatchOutbox.status.in_(_ACTIVE_DISPATCH_OUTBOX_STATES)
                )
            )
            or 0
        ),
        "recoverable_dispatch_outbox": await _recoverable_dispatch_outbox_count(experiment_session),
        "run_control_commands": int(
            await experiment_session.scalar(
                select(func.count(ExperimentRunControlCommand.command_id)).where(
                    ExperimentRunControlCommand.status.in_(_ACTIVE_RUN_CONTROL_STATES)
                )
            )
            or 0
        ),
        "resource_admissions": int(
            await experiment_session.scalar(
                select(func.count(ExperimentResourceAdmission.admission_id)).where(
                    ExperimentResourceAdmission.state.in_(_ACTIVE_ADMISSION_STATES)
                )
            )
            or 0
        ),
        "launch_contexts_v2": int(
            await experiment_session.scalar(
                select(func.count(ExperimentLaunchContext.launch_context_id)).where(
                    ExperimentLaunchContext.contract_version == "2",
                    or_(
                        ExperimentLaunchContext.state == "reserved",
                        (
                            (ExperimentLaunchContext.state == "issued")
                            & (ExperimentLaunchContext.expires_at > current_time)
                        ),
                    ),
                )
            )
            or 0
        ),
        "connector_commands": int(
            await experiment_session.scalar(
                select(func.count(ExperimentDomainConnectorCommand.command_id)).where(
                    ExperimentDomainConnectorCommand.status.in_(_ACTIVE_CONNECTOR_COMMAND_STATES)
                )
            )
            or 0
        ),
        "connector_inbox_deferred": int(
            await experiment_session.scalar(
                select(func.count(ExperimentDomainConnectorInbox.event_id)).where(
                    ExperimentDomainConnectorInbox.source_store_id == _LOCAL_SOURCE_STORE_ID,
                    ExperimentDomainConnectorInbox.disposition == "deferred_gap",
                )
            )
            or 0
        ),
        "local_outbox": int(
            await local_session.scalar(
                select(func.count(MolBioNGSOutboxEvent.id)).where(
                    MolBioNGSOutboxEvent.status.in_(_ACTIVE_LOCAL_OUTBOX_STATES)
                )
            )
            or 0
        ),
        "local_idempotency_claims": int(
            await local_session.scalar(
                select(func.count()).select_from(MolBioNGSIdempotencyClaim).where(
                    MolBioNGSIdempotencyClaim.status == "pending"
                )
            )
            or 0
        ),
        "local_connector_acknowledgements": int(
            await local_session.scalar(
                select(func.count()).select_from(MolBioNGSConnectorAcknowledgement).where(
                    MolBioNGSConnectorAcknowledgement.disposition.in_(_ACTIVE_LOCAL_CONNECTOR_ACK_STATES)
                )
            )
            or 0
        ),
        "connector_convergence": await _connector_convergence_count(experiment_session, local_session),
    }
    if any(counts.values()):
        detail = ", ".join(f"{name}={count}" for name, count in counts.items())
        raise SharedPackageAcceptanceError(
            f"authoritative package runtime is not literally quiescent: {detail}"
        )


async def validate_shared_package_acceptance(
    experiment_session: AsyncSession,
    core_session: AsyncSession,
    local_session: AsyncSession,
    molbio_source_session: AsyncSession,
    document: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(document))
    schema = _read(_SCHEMA_PATH)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(candidate),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise SharedPackageAcceptanceError(
            f"shared package acceptance is invalid at {location}: {errors[0].message}"
        )
    if candidate["content_sha256"] != _content_sha256(candidate):
        raise SharedPackageAcceptanceError("shared package acceptance content digest mismatch")
    runtime = runtime_implementation_record()
    if candidate["runtime_implementation_sha256"] != runtime["content_sha256"]:
        raise SharedPackageAcceptanceError("acceptance binds a different runtime implementation record")
    if (
        candidate["source_commit"] != runtime["successor_source_commit"]
        or candidate["source_tree"] != runtime["successor_source_tree"]
    ):
        raise SharedPackageAcceptanceError("acceptance source identity differs from successor runtime authority")
    n0_receipt = _read(_N0_RECEIPT_PATH)
    static_fingerprint = candidate.get("static_package_fingerprint")
    n0_fingerprint = n0_receipt.get("payload_fingerprint_sha256")
    if (
        type(static_fingerprint) is not str
        or type(n0_fingerprint) is not str
        or len(static_fingerprint) != 64
        or len(n0_fingerprint) != 64
        or static_fingerprint != n0_fingerprint
    ):
        raise SharedPackageAcceptanceError("acceptance binds a different static package fingerprint")
    migration_store_ids = [row["store_id"] for row in candidate["migration_receipts"]]
    if (
        len(migration_store_ids) != len(set(migration_store_ids))
        or set(migration_store_ids) != set(_REQUIRED_MIGRATION_STORES)
    ):
        raise SharedPackageAcceptanceError(
            "migration acceptance evidence must contain exactly one receipt for every required store"
        )
    pointers = _evidence_pointers(candidate)
    identities = [(row["receipt_id"], row["receipt_sha256"]) for row, _, _, _ in pointers]
    if len(identities) != len(set(identities)):
        raise SharedPackageAcceptanceError("acceptance evidence contains duplicate receipt identities")
    receipt_ids = [row["receipt_id"] for row, _, _, _ in pointers]
    persisted = list(
        (
            await experiment_session.scalars(
                select(ExperimentOperationalReceipt).where(
                    ExperimentOperationalReceipt.receipt_id.in_(receipt_ids)
                )
            )
        ).all()
    )
    by_id = {row.receipt_id: row for row in persisted}
    if len(by_id) != len(receipt_ids):
        raise SharedPackageAcceptanceError("one or more acceptance evidence receipts are not persisted")
    migration_bodies: dict[str, Mapping[str, Any]] = {}
    retained_package_artifacts: dict[str, int] = {}
    for pointer, expected_kind, evidence_kind, store_id in pointers:
        row = by_id[pointer["receipt_id"]]
        if row.receipt_sha256 != pointer["receipt_sha256"]:
            raise SharedPackageAcceptanceError(f"operational receipt {row.receipt_id} hash does not match pointer")
        if row.native_identity != pointer["native_identity"]:
            raise SharedPackageAcceptanceError(f"operational receipt {row.receipt_id} native identity mismatch")
        if row.operation_kind != expected_kind or row.state != "verified":
            raise SharedPackageAcceptanceError(
                f"operational receipt {row.receipt_id} has the wrong operation kind or state"
            )
        if row.source_revision != candidate["source_commit"]:
            raise SharedPackageAcceptanceError(f"operational receipt {row.receipt_id} source revision mismatch")
        body = _receipt_document(row)
        _validate_receipt_source_identities(
            body,
            expected_commit=candidate["source_commit"],
            expected_tree=candidate["source_tree"],
            receipt_id=row.receipt_id,
        )
        if evidence_kind in _PACKAGE_EVIDENCE_SCHEMAS:
            _validate_closed_package_evidence(
                body,
                receipt_id=row.receipt_id,
                expected_runtime_implementation_sha256=candidate[
                    "runtime_implementation_sha256"
                ],
            )
            for digest, size in _package_evidence_artifacts(body).items():
                previous = retained_package_artifacts.setdefault(digest, size)
                if previous != size:
                    raise SharedPackageAcceptanceError(
                        "retained package evidence artifact authority conflicts"
                    )
        _validate_evidence_semantics(
            body,
            row,
            evidence_kind=evidence_kind,
            store_id=store_id,
        )
        if evidence_kind == "migration" and store_id is not None:
            migration_bodies[store_id] = body
    await _verify_retained_package_artifacts(experiment_session, retained_package_artifacts)
    live_migrations = await _live_migration_authorities(
        experiment_session,
        molbio_source_session,
        local_session,
    )
    _validate_live_migration_evidence(migration_bodies, live_migrations)
    await _verify_no_active_authority(
        experiment_session,
        core_session,
        local_session,
        molbio_source_session,
    )
    return candidate


async def _begin_immediate_source_transactions(
    *sessions: AsyncSession,
) -> None:
    """Hold SQLite write reservations across validation and receipt commit."""
    for session in sessions:
        await session.execute(text("BEGIN IMMEDIATE"))


async def acceptance_operational_receipt(
    experiment_session: AsyncSession,
    core_session: AsyncSession,
    evidence: Mapping[str, Any],
    *,
    accepted_by: str,
) -> ExperimentOperationalReceipt:
    local_session = molbio_ngs_session_factory()
    molbio_source_session = molbio_session()
    local_closed = False
    molbio_source_closed = False
    fence_entered = False
    try:
        async with package_acceptance_exclusive_fence(
            experiment_session,
            core_session,
            local_session,
            molbio_source_session,
        ):
            fence_entered = True
            try:
                await _begin_immediate_source_transactions(
                    experiment_session,
                    core_session,
                    local_session,
                    molbio_source_session,
                )
                reserved = {"accepted_at", "accepted_by", "no_active_jobs", "outcome", "content_sha256"}
                if reserved.intersection(evidence):
                    raise SharedPackageAcceptanceError("caller evidence contains service-owned acceptance fields")
                candidate = copy.deepcopy(dict(evidence))
                candidate.update(
                    {
                        "accepted_at": now(),
                        "accepted_by": accepted_by,
                        "no_active_jobs": True,
                        "outcome": "pass",
                    }
                )
                candidate["content_sha256"] = _content_sha256(candidate)
                accepted = await validate_shared_package_acceptance(
                    experiment_session,
                    core_session,
                    local_session,
                    molbio_source_session,
                    candidate,
                )
                body = canonical_json(accepted)
                request_authority = {
                    "accepted_by": accepted_by,
                    "evidence": copy.deepcopy(dict(evidence)),
                }
                receipt_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"bms:package-acceptance:{_sha256(rfc8785.dumps(request_authority))}",
                    )
                )
                receipt = ExperimentOperationalReceipt(
                    receipt_id=receipt_id,
                    operation_kind="package_acceptance",
                    workspace_id=None,
                    native_identity=f"{accepted['source_commit']}:{accepted['source_tree']}",
                    state="verified",
                    receipt_json=body,
                    receipt_sha256=_sha256(body.encode("utf-8")),
                    source_revision=accepted["source_commit"],
                    occurred_at=accepted["accepted_at"],
                    verified_at=accepted["accepted_at"],
                )
                existing_receipt = await experiment_session.get(
                    ExperimentOperationalReceipt, receipt_id
                )
                if existing_receipt is not None:
                    existing_body = _receipt_document(existing_receipt)
                    if (
                        existing_receipt.operation_kind != "package_acceptance"
                        or existing_receipt.state != "verified"
                        or existing_body.get("accepted_by") != accepted_by
                        or any(existing_body.get(key) != value for key, value in evidence.items())
                    ):
                        raise SharedPackageAcceptanceError(
                            "package acceptance idempotency authority conflicts with its persisted receipt"
                        )
                    receipt = existing_receipt
                else:
                    experiment_session.add(receipt)
                    await experiment_session.flush()
                await experiment_session.commit()
                await core_session.rollback()
                await local_session.rollback()
                await molbio_source_session.rollback()
                await local_session.close()
                local_closed = True
                await molbio_source_session.close()
                molbio_source_closed = True
                return receipt
            except BaseException as operation_error:
                rollback_error: BaseException | None = None
                for session in (
                    experiment_session,
                    core_session,
                    local_session,
                    molbio_source_session,
                ):
                    try:
                        await session.rollback()
                    except BaseException as exc:
                        if rollback_error is None:
                            rollback_error = exc
                if rollback_error is not None:
                    raise rollback_error from operation_error
                raise
            finally:
                if not local_closed:
                    await local_session.close()
                    local_closed = True
                if not molbio_source_closed:
                    await molbio_source_session.close()
                    molbio_source_closed = True
    except NgsMolBioQuiescenceError as exc:
        raise SharedPackageAcceptanceError(str(exc)) from exc
    finally:
        if not fence_entered and not local_closed:
            await local_session.close()
        if not fence_entered and not molbio_source_closed:
            await molbio_source_session.close()


__all__ = [
    "SharedPackageAcceptanceError",
    "acceptance_operational_receipt",
    "validate_shared_package_acceptance",
]
