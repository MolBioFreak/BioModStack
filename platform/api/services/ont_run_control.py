"""ONT instrument-run preflight/control service.

This service deliberately separates live acquisition readiness from existing
file-analysis jobs. It only reports start readiness when real host-agent/MinKNOW
state supports it; it does not fabricate protocol options or device positions.

The BMS run ledger is BMS-owned durable state. Host-agent calls remain the sole
instrument-control seam; this module does not make a non-live host agent capable
of starting, stopping, or polling physical hardware.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import OntInstrumentRun, OntInstrumentRunEvent, OntInstrumentRunPreflight, OntProtocolOptionReceipt, async_session
from services.host_agent_client import get_ont_position, get_ont_status, request_host_agent
from services.ont_device_control import _public_mk1d_device


RUN_STATES = frozenset({"armed", "starting", "running", "stopping", "stopped", "completed", "failed", "unknown"})
TERMINAL_RUN_STATES = frozenset({"stopped", "completed", "failed"})
TERMINAL_ARTIFACT_MANIFEST_SCHEMA = "bms.ont.instrument-terminal-artifacts.v1"
TERMINAL_ARTIFACT_MANIFEST_SCHEMA_VERSION = 1
TERMINAL_ARTIFACT_KINDS = frozenset({"fastq", "pod5", "bam"})
_TERMINAL_ARTIFACT_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "run_id",
        "minknow_run_id_sha256",
        "terminal_state",
        "observed_generation",
        "artifacts",
    }
)
_TERMINAL_ARTIFACT_FIELDS = frozenset({"kind", "path", "bytes", "sha256"})
_ALLOWED_STATE_EDGES: dict[str, frozenset[str]] = {
    "armed": frozenset({"armed", "starting", "failed", "unknown"}),
    "starting": frozenset({"starting", "running", "stopping", "stopped", "completed", "failed", "unknown"}),
    "running": frozenset({"running", "stopping", "stopped", "completed", "failed", "unknown"}),
    "stopping": frozenset({"stopping", "stopped", "completed", "failed", "unknown"}),
    "stopped": frozenset({"stopped"}),
    "completed": frozenset({"completed"}),
    "failed": frozenset({"failed"}),
    "unknown": RUN_STATES,
}


class InstrumentRunStateTransitionError(ValueError):
    """Raised when a persisted run would leave an allowed lifecycle edge."""


def _new_run_id() -> str:
    return f"ont-run-{uuid4().hex}"


def _new_event_id() -> str:
    return f"ont-run-event-{uuid4().hex}"


def _new_option_id() -> str:
    return f"ont-option-{uuid4().hex}"


def _new_preflight_id() -> str:
    return f"ont-preflight-{uuid4().hex}"


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _flow_cell_identity(position: dict[str, Any]) -> str:
    flow_cell = position.get("flow_cell") if isinstance(position, dict) else None
    if not isinstance(flow_cell, dict) or not flow_cell.get("present"):
        return _canonical_digest({"present": False})
    return _canonical_digest(
        {
            "present": True,
            "flow_cell_id": flow_cell.get("flow_cell_id") or flow_cell.get("user_specified_flow_cell_id"),
            "product_code": flow_cell.get("product_code") or flow_cell.get("user_specified_product_code"),
            "channel_count": flow_cell.get("channel_count"),
            "sample_rate": flow_cell.get("sample_rate"),
        }
    )


def _sanitize_label(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text[:160] if text else fallback


def _option_snapshot(position: str, host_payload: dict[str, Any]) -> dict[str, Any]:
    """Retain host-authoritative details server-side; never return them to callers."""
    return {
        "position": position,
        "protocol_id": host_payload.get("protocol_id"),
        "kit": host_payload.get("kit"),
        "basecalling_enabled": bool(host_payload.get("basecalling_enabled")),
        "basecalling_options": host_payload.get("basecalling_options") if isinstance(host_payload.get("basecalling_options"), dict) else {},
        "output_directories": host_payload.get("output_directories") if isinstance(host_payload.get("output_directories"), dict) else {},
        "flow_cell": host_payload.get("flow_cell") if isinstance(host_payload.get("flow_cell"), dict) else {"present": False},
    }


def _catalog_blockers(host_payload: dict[str, Any]) -> list[str]:
    blockers = [str(item) for item in (host_payload.get("blockers") or [])]
    if not str(host_payload.get("protocol_id") or "").strip() and "protocol_unavailable" not in blockers:
        blockers.append("protocol_unavailable")
    return blockers


def _normalize_position_payload(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("position") if isinstance(payload, dict) else None
    return nested if isinstance(nested, dict) else payload


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_datetime(value: datetime) -> str:
    normalized = value
    if normalized.tzinfo is not None:
        normalized = normalized.astimezone(timezone.utc).replace(tzinfo=None)
    return f"{normalized.isoformat(timespec='microseconds')}Z"


def _flowcell_present(position: dict[str, Any]) -> bool:
    flow_cell = position.get("flow_cell") if isinstance(position, dict) else None
    return bool(isinstance(flow_cell, dict) and flow_cell.get("present"))


def build_start_preflight(
    *,
    position: dict[str, Any],
    kit: str | None,
    basecalling_enabled: bool = True,
    basecalling_options: dict[str, Any] | None = None,
    output_directories: dict[str, Any] | None = None,
    protocol_id: str | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    if not _flowcell_present(position):
        blockers.append("flowcell_absent")
    if bool(position.get("running")):
        blockers.append("position_already_running")
    if not str(kit or "").strip():
        blockers.append("kit_missing")
    if basecalling_enabled and not (basecalling_options or {}).get("simplex_models"):
        blockers.append("basecalling_model_missing")
    if not output_directories:
        blockers.append("output_directory_missing")

    return {
        "position": position.get("position"),
        "device_type": position.get("device_type"),
        "can_start": not blockers,
        "blockers": blockers,
        "protocol_id": protocol_id,
        "kit": kit,
        "basecalling_enabled": bool(basecalling_enabled),
        "basecalling_options": basecalling_options or {},
        "output_directories": output_directories or {},
        "flow_cell": position.get("flow_cell") or {"present": False},
        "fake_or_demo_devices": False,
    }


def begin_position_hardware_check(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Fail closed; physical diagnostics require separately supervised commissioning."""
    del position, payload
    raise NotImplementedError(
        "Mk1D hardware-check activation is disabled pending separately authorized supervised commissioning."
    )


def refresh_position_state(position: str) -> dict[str, Any]:
    host_payload = request_host_agent("POST", f"/ont/positions/{position}/refresh", {"confirm_refresh": True})
    if not isinstance(host_payload, dict):
        raise RuntimeError(f"host-agent returned non-object refresh payload: {host_payload!r}")
    safe_position = _public_mk1d_device(_normalize_position_payload(host_payload))
    if safe_position is None:
        raise ValueError("only Mk1D position refresh is available")
    return {
        "action": "refresh",
        "detail": "Re-read the Mk1D position state without a power cycle.",
        "position": safe_position,
        "fake_or_demo_devices": False,
    }


def restart_position(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not bool(payload.get("confirm_restart")):
        raise ValueError("confirm_restart=true is required before requesting an ONT instrument restart")
    host_payload = request_host_agent("POST", f"/ont/positions/{position}/restart", payload)
    if not isinstance(host_payload, dict):
        raise RuntimeError(f"host-agent returned non-object restart payload: {host_payload!r}")
    return host_payload


def _normalized_state(value: Any, *, fallback: str | None = None) -> str:
    raw = str(value or "").strip().lower()
    if not raw and fallback:
        return fallback
    aliases = {
        "active": "running",
        "complete": "completed",
        "stop": "stopped",
        "stopped_by_operator": "stopped",
        "error": "failed",
    }
    state = aliases.get(raw, raw)
    if state not in RUN_STATES:
        raise ValueError(f"unrecognized ONT instrument run state: {value!r}")
    return state


def _validate_state_edge(previous: str, next_state: str) -> None:
    if next_state not in _ALLOWED_STATE_EDGES[previous]:
        raise InstrumentRunStateTransitionError(
            f"invalid ONT instrument run state transition: {previous!r} -> {next_state!r}"
        )


def _existing_output_files(raw_files: Any) -> dict[str, list[str]]:
    """Accept only bounded, regular files reported by the host-agent snapshot."""
    normalized: dict[str, list[str]] = {"fastq": [], "pod5": [], "bam": []}
    if not isinstance(raw_files, dict):
        return normalized
    for kind in normalized:
        values = raw_files.get(kind) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for value in values[:128]:
            if not isinstance(value, str) or not value.strip() or len(value) > 2048:
                continue
            path = Path(value).expanduser()
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if resolved.is_file() and not resolved.is_symlink():
                normalized[kind].append(str(resolved))
    for kind, paths in normalized.items():
        normalized[kind] = sorted(set(paths))
    return normalized


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _terminal_artifact_manifest(
    record: OntInstrumentRun,
    *,
    state: str,
    observed_generation: int,
    output_files: dict[str, list[str]],
) -> tuple[dict[str, Any], str]:
    """Bind one terminal host observation to exact readable output bytes.

    Paths stay strictly server-side.  The public projection exposes only the
    manifest digest and per-kind counts, while job creation receives the exact
    verified primary input through the internal handoff.
    """
    artifacts: list[dict[str, Any]] = []
    for kind in ("fastq", "pod5", "bam"):
        for raw_path in output_files.get(kind, []):
            path = Path(raw_path)
            try:
                stat = path.stat()
                if not path.is_file() or path.is_symlink():
                    continue
                artifacts.append(
                    {
                        "kind": kind,
                        "path": str(path),
                        "bytes": stat.st_size,
                        "sha256": _sha256_file(path),
                    }
                )
            except OSError:
                # A host snapshot can race output retention.  Preserve terminal
                # state but withhold that unavailable artifact from handoff.
                continue
    artifacts.sort(key=lambda artifact: (str(artifact["kind"]), str(artifact["path"])))
    manifest = {
        "schema": TERMINAL_ARTIFACT_MANIFEST_SCHEMA,
        "schema_version": TERMINAL_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "run_id": record.id,
        "minknow_run_id_sha256": hashlib.sha256((record.minknow_run_id or "").encode("utf-8")).hexdigest(),
        "terminal_state": state,
        "observed_generation": observed_generation,
        "artifacts": artifacts,
    }
    return manifest, _canonical_digest(manifest)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _valid_terminal_manifest(record: OntInstrumentRun) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Return only the canonical, digest-bound terminal evidence for this exact run."""
    manifest = record.terminal_artifact_manifest
    digest = record.terminal_artifact_manifest_sha256
    if not isinstance(manifest, dict) or not _is_sha256(digest) or _canonical_digest(manifest) != digest:
        return None
    if set(manifest) != _TERMINAL_ARTIFACT_MANIFEST_FIELDS:
        return None
    observed_generation = manifest.get("observed_generation")
    if (
        manifest.get("schema") != TERMINAL_ARTIFACT_MANIFEST_SCHEMA
        or manifest.get("schema_version") != TERMINAL_ARTIFACT_MANIFEST_SCHEMA_VERSION
        or manifest.get("run_id") != record.id
        or record.state not in TERMINAL_RUN_STATES
        or manifest.get("terminal_state") != record.state
        or isinstance(observed_generation, bool)
        or not isinstance(observed_generation, int)
        or observed_generation != record.observed_generation
        or observed_generation < 1
        or manifest.get("minknow_run_id_sha256")
        != hashlib.sha256((record.minknow_run_id or "").encode("utf-8")).hexdigest()
    ):
        return None
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        return None
    artifacts: list[dict[str, Any]] = []
    seen_artifacts: set[tuple[str, str]] = set()
    previous_order: tuple[str, str] | None = None
    for artifact in raw_artifacts:
        if not isinstance(artifact, dict) or set(artifact) != _TERMINAL_ARTIFACT_FIELDS:
            return None
        kind = artifact.get("kind")
        path = artifact.get("path")
        size = artifact.get("bytes")
        artifact_digest = artifact.get("sha256")
        if (
            kind not in TERMINAL_ARTIFACT_KINDS
            or not isinstance(path, str)
            or not path
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not _is_sha256(artifact_digest)
        ):
            return None
        artifact_key = (kind, path)
        if artifact_key in seen_artifacts or (previous_order is not None and artifact_key <= previous_order):
            return None
        seen_artifacts.add(artifact_key)
        previous_order = artifact_key
        artifacts.append(artifact)
    return manifest, artifacts


def _manifest_has_fastq(record: OntInstrumentRun) -> bool:
    validated = _valid_terminal_manifest(record)
    return bool(validated and any(artifact["kind"] == "fastq" for artifact in validated[1]))


def _terminal_manifest_response(record: OntInstrumentRun) -> dict[str, Any] | None:
    validated = _valid_terminal_manifest(record)
    if validated is None:
        return None
    manifest, artifacts = validated
    digest = record.terminal_artifact_manifest_sha256
    return {
        "sha256": digest,
        "terminal_state": manifest.get("terminal_state"),
        "artifact_counts": {kind: sum(1 for artifact in artifacts if isinstance(artifact, dict) and artifact.get("kind") == kind) for kind in ("fastq", "pod5", "bam")},
    }


async def _events_for_run(session: AsyncSession, run_id: str) -> list[OntInstrumentRunEvent]:
    result = await session.execute(
        select(OntInstrumentRunEvent)
        .where(OntInstrumentRunEvent.run_id == run_id)
        .order_by(OntInstrumentRunEvent.observed_generation)
    )
    return list(result.scalars())


def _event_response(event: OntInstrumentRunEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "state": event.state,
        "status": event.state,
        "observed_at": _json_datetime(event.observed_at),
        "observed_generation": event.observed_generation,
    }


async def _preflight_for_run(session: AsyncSession, run_id: str) -> OntInstrumentRunPreflight | None:
    return (
        await session.execute(select(OntInstrumentRunPreflight).where(OntInstrumentRunPreflight.run_id == run_id))
    ).scalar_one_or_none()


def _preflight_response(preflight: OntInstrumentRunPreflight | None) -> dict[str, Any] | None:
    if preflight is None:
        return None
    return {
        "id": preflight.id,
        "selected_option_id": preflight.selected_option_id,
        "flow_cell_identity": preflight.flow_cell_identity_sha256,
        "source_digest": preflight.source_digest,
        "capability_digest": preflight.capability_digest,
        "expires_at": _json_datetime(preflight.expires_at),
        "invalidated_at": _json_datetime(preflight.invalidated_at) if preflight.invalidated_at else None,
        "invalidation_reason": preflight.invalidation_reason,
    }


def _output_summary(output_files: dict[str, Any] | None) -> dict[str, int]:
    """Return only safe output availability/count labels, never filesystem paths."""
    files = output_files if isinstance(output_files, dict) else {}
    return {
        kind: len(files.get(kind) or []) if isinstance(files.get(kind), list) else 0
        for kind in ("fastq", "pod5", "bam")
    }


async def _run_response(session: AsyncSession, record: OntInstrumentRun) -> dict[str, Any]:
    events = await _events_for_run(session, record.id)
    preflight = await _preflight_for_run(session, record.id)
    return {
        "id": record.id,
        "position": record.position_id,
        "state": record.state,
        "status": record.state,
        "observed_at": _json_datetime(record.observed_at),
        "observed_generation": record.observed_generation,
        "sample_id": record.sample_id,
        "experiment_group": record.experiment_group,
        "output_summary": _output_summary(record.output_files),
        "handoff_ready": record.state == "completed" and _manifest_has_fastq(record),
        "terminal_artifact_manifest": _terminal_manifest_response(record),
        "preflight": _preflight_response(preflight),
        "selected_option_id": preflight.selected_option_id if preflight else None,
        "flow_cell_identity": preflight.flow_cell_identity_sha256 if preflight else None,
        "events": [_event_response(event) for event in events],
        "fake_or_demo_devices": False,
    }


async def _load_run(session: AsyncSession, run_id: str) -> OntInstrumentRun:
    record = await session.get(OntInstrumentRun, run_id)
    if record is None:
        raise KeyError(run_id)
    return record


async def _append_observation(
    session: AsyncSession,
    record: OntInstrumentRun,
    *,
    event_type: str,
    state: str,
    minknow_payload: dict[str, Any] | None,
    output_files: dict[str, list[str]] | None = None,
) -> None:
    _validate_state_edge(record.state, state)
    observed_at = _utc_now()
    next_generation = record.observed_generation + 1
    normalized_output_files = output_files if output_files is not None else (record.output_files or {"fastq": [], "pod5": [], "bam": []})
    record.state = state
    record.observed_at = observed_at
    record.observed_generation = next_generation
    record.last_minknow_payload = minknow_payload
    record.output_files = normalized_output_files
    if state in TERMINAL_RUN_STATES:
        existing_manifest = record.terminal_artifact_manifest
        existing_digest = record.terminal_artifact_manifest_sha256
        if existing_manifest is None and existing_digest is None and state == "completed":
            manifest, manifest_sha256 = _terminal_artifact_manifest(
                record,
                state=state,
                observed_generation=next_generation,
                output_files=normalized_output_files,
            )
            if manifest["artifacts"]:
                record.terminal_artifact_manifest = manifest
                record.terminal_artifact_manifest_sha256 = manifest_sha256
        elif existing_manifest is not None or existing_digest is not None:
            if _valid_terminal_manifest(record) is None:
                raise RuntimeError("stored terminal artifact manifest is invalid and cannot be rewritten")
        record.handoff_ready = state == "completed" and _manifest_has_fastq(record)
    else:
        record.handoff_ready = False
    session.add(
        OntInstrumentRunEvent(
            id=_new_event_id(),
            run_id=record.id,
            event_type=event_type,
            state=state,
            observed_at=observed_at,
            observed_generation=next_generation,
            minknow_payload=minknow_payload,
            output_files=normalized_output_files,
        )
    )


async def reset_ont_run_store() -> None:
    """Test-only compatibility helper that clears durable ledger rows, not memory."""
    async with async_session() as session:
        await session.execute(delete(OntInstrumentRunPreflight))
        await session.execute(delete(OntProtocolOptionReceipt))
        await session.execute(delete(OntInstrumentRunEvent))
        await session.execute(delete(OntInstrumentRun))
        await session.commit()


async def issue_position_protocol_catalog(position: str) -> dict[str, Any]:
    """Persist and return one short-lived opaque option receipt for a live host option.

    The host-agent response is intentionally treated as sensitive server-side
    protocol capability data. Only labels and opaque handles leave the API.
    """
    host_payload = get_position_protocol_options(position)
    if not isinstance(host_payload, dict):
        raise RuntimeError(f"host-agent returned non-object protocol options payload: {host_payload!r}")
    blockers = _catalog_blockers(host_payload)
    can_start = bool(host_payload.get("can_start")) and not blockers
    safe_response: dict[str, Any] = {
        "position": position,
        "can_start": can_start,
        "blockers": blockers,
        "flow_cell_present": _flowcell_present(host_payload),
        "options": [],
        "fake_or_demo_devices": False,
    }
    if str(host_payload.get("device_type") or "").strip().lower() != "mk1d":
        safe_response["can_start"] = False
        safe_response["blockers"] = ["unsupported_device_type"]
        return safe_response
    if not can_start:
        return safe_response

    snapshot = _option_snapshot(position, host_payload)
    source_digest = _canonical_digest(snapshot)
    capability_digest = _canonical_digest(
        {
            "protocol_id": snapshot["protocol_id"],
            "kit": snapshot["kit"],
            "basecalling_enabled": snapshot["basecalling_enabled"],
            "basecalling_options": snapshot["basecalling_options"],
        }
    )
    now = _utc_now()
    receipt = OntProtocolOptionReceipt(
        id=_new_preflight_id(),
        option_id=_new_option_id(),
        position_id=position,
        flow_cell_identity_sha256=_flow_cell_identity(host_payload),
        source_digest=source_digest,
        capability_digest=capability_digest,
        source_snapshot=snapshot,
        expires_at=now + timedelta(minutes=10),
        created_at=now,
    )
    async with async_session() as session:
        session.add(receipt)
        await session.commit()
    safe_response["options"] = [
        {
            "option_id": receipt.option_id,
            "option_receipt_id": receipt.id,
            "expires_at": _json_datetime(receipt.expires_at),
            "protocol_label": _sanitize_label("MinKNOW sequencing protocol", "MinKNOW sequencing protocol"),
            "basecalling_enabled": snapshot["basecalling_enabled"],
            "output_policy_id": f"ont-output-policy-{source_digest[:16]}",
            "output_policy_label": "MinKNOW-managed output policy",
        }
    ]
    return safe_response


def _bounded_metadata(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    normalized = value.strip()
    if len(normalized) > 255:
        raise ValueError(f"{key} must be at most 255 characters")
    return normalized or None


async def create_run_intent(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Consume one opaque option receipt into an ARMED durable ledger row."""
    allowed = {"option_id", "option_receipt_id", "sample_id", "experiment_group"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("run intent accepts only opaque option receipt IDs and bounded sample metadata")
    option_id = str(payload.get("option_id") or "").strip()
    receipt_id = str(payload.get("option_receipt_id") or "").strip()
    if not option_id or not receipt_id:
        raise ValueError("opaque option_id and option_receipt_id are required")
    sample_id = _bounded_metadata(payload, "sample_id")
    experiment_group = _bounded_metadata(payload, "experiment_group")
    now = _utc_now()
    async with async_session() as session:
        receipt = (
            await session.execute(
                select(OntProtocolOptionReceipt).where(
                    OntProtocolOptionReceipt.id == receipt_id,
                    OntProtocolOptionReceipt.option_id == option_id,
                    OntProtocolOptionReceipt.position_id == position,
                )
            )
        ).scalar_one_or_none()
        if receipt is None or receipt.consumed_at is not None or receipt.expires_at <= now:
            raise ValueError("opaque option receipt is unknown, expired, or already consumed")
        run_id = _new_run_id()
        record = OntInstrumentRun(
            id=run_id,
            position_id=position,
            minknow_run_id=None,
            state="armed",
            observed_at=now,
            observed_generation=1,
            sample_id=sample_id,
            experiment_group=experiment_group,
            kit=receipt.source_snapshot.get("kit") if isinstance(receipt.source_snapshot, dict) else None,
            output_directories={},
            output_files={"fastq": [], "pod5": [], "bam": []},
            handoff_ready=False,
            last_minknow_payload={"source_digest": receipt.source_digest, "event": "preflight_armed"},
        )
        preflight = OntInstrumentRunPreflight(
            id=_new_preflight_id(),
            run_id=run_id,
            option_receipt_id=receipt.id,
            selected_option_id=receipt.option_id,
            flow_cell_identity_sha256=receipt.flow_cell_identity_sha256,
            source_digest=receipt.source_digest,
            capability_digest=receipt.capability_digest,
            source_snapshot=receipt.source_snapshot,
            expires_at=receipt.expires_at,
            created_at=now,
        )
        event = OntInstrumentRunEvent(
            id=_new_event_id(),
            run_id=run_id,
            event_type="preflight_armed",
            state="armed",
            observed_at=now,
            observed_generation=1,
            minknow_payload={"preflight_id": preflight.id, "source_digest": receipt.source_digest},
            output_files={"fastq": [], "pod5": [], "bam": []},
        )
        receipt.consumed_at = now
        session.add_all((record, preflight, event))
        await session.commit()
        return await _run_response(session, record)


async def validate_armed_intent_start(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Freshly validate an intent but deliberately do not issue a MinKNOW start."""
    if not isinstance(payload, dict) or set(payload) - {"confirm_start", "intent_generation"}:
        raise ValueError("start accepts only confirm_start and the current intent_generation")
    if not bool(payload.get("confirm_start")):
        raise ValueError("confirm_start=true is required before validating a MinKNOW run intent")
    async with async_session() as session:
        record = await _load_run(session, run_id)
        preflight = await _preflight_for_run(session, run_id)
        if record.state != "armed" or preflight is None:
            raise ValueError("run is not an armed protocol intent")
        if payload.get("intent_generation") != record.observed_generation:
            raise ValueError("intent_generation does not match the current durable ledger generation")
        now = _utc_now()
        if preflight.expires_at <= now:
            preflight.invalidated_at = now
            preflight.invalidation_reason = "preflight_expired"
            await _append_observation(session, record, event_type="preflight_expired", state="armed", minknow_payload={"reason": "preflight_expired"})
            await session.commit()
            raise ValueError("protocol preflight receipt has expired")
        # The browser-facing position route is intentionally redacted. Re-read
        # the server-side protocol/options seam so the durable receipt can be
        # compared against the real flow-cell and capability data without
        # sending it back to the browser.
        live = get_position_protocol_options(record.position_id)
        invalid_reason = None
        if str(live.get("position") or record.position_id) != record.position_id:
            invalid_reason = "position_mismatch"
        elif not _flowcell_present(live):
            invalid_reason = "flowcell_absent"
        elif _flow_cell_identity(live) != preflight.flow_cell_identity_sha256:
            invalid_reason = "flowcell_mismatch"
        elif bool(live.get("running")) or live.get("current_protocol"):
            invalid_reason = "position_protocol_changed"
        else:
            fresh_catalog = get_position_protocol_options(record.position_id)
            if not isinstance(fresh_catalog, dict):
                invalid_reason = "protocol_capability_unavailable"
            elif str(fresh_catalog.get("device_type") or "").strip().lower() != "mk1d":
                invalid_reason = "unsupported_device_type"
            elif not bool(fresh_catalog.get("can_start")) or _catalog_blockers(fresh_catalog):
                invalid_reason = "protocol_capability_unavailable"
            else:
                fresh_snapshot = _option_snapshot(record.position_id, fresh_catalog)
                fresh_source_digest = _canonical_digest(fresh_snapshot)
                fresh_capability_digest = _canonical_digest(
                    {
                        "protocol_id": fresh_snapshot["protocol_id"],
                        "kit": fresh_snapshot["kit"],
                        "basecalling_enabled": fresh_snapshot["basecalling_enabled"],
                        "basecalling_options": fresh_snapshot["basecalling_options"],
                    }
                )
                if fresh_capability_digest != preflight.capability_digest:
                    invalid_reason = "capability_mismatch"
                elif fresh_source_digest != preflight.source_digest:
                    invalid_reason = "source_digest_mismatch"
        if invalid_reason:
            preflight.invalidated_at = now
            preflight.invalidation_reason = invalid_reason
            await _append_observation(session, record, event_type="preflight_invalidated", state="armed", minknow_payload={"reason": invalid_reason})
            await session.commit()
            raise ValueError(f"protocol preflight is stale: {invalid_reason}")
        await _append_observation(session, record, event_type="preflight_revalidated", state="armed", minknow_payload={"source": "fresh_position_read"})
        await session.commit()
        raise NotImplementedError("MinKNOW protocol start remains disabled pending separately authorized supervised commissioning")


async def get_instrument_run(run_id: str) -> dict[str, Any] | None:
    async with async_session() as session:
        record = await session.get(OntInstrumentRun, run_id)
        return await _run_response(session, record) if record else None


def _bounded_host_snapshot(record: OntInstrumentRun, payload: dict[str, Any]) -> tuple[str, dict[str, list[str]], dict[str, Any]]:
    """Normalize the host-agent's one-run observation before it reaches the ledger."""
    host_run_id = str(payload.get("minknow_run_id") or payload.get("run_id") or "").strip()
    if host_run_id and host_run_id != record.minknow_run_id:
        raise ValueError("host-agent run snapshot does not match the durable MinKNOW run binding")
    state = _normalized_state(payload.get("status") or payload.get("state"), fallback="unknown")
    output_files = _existing_output_files(payload.get("output_files"))
    snapshot = {
        "schema": "bms.ont.host-run-snapshot.v1",
        "state": state,
        "output_files": output_files,
    }
    return state, output_files, {**snapshot, "observation_digest": _canonical_digest(snapshot)}


def _event_type_for_observed_state(state: str) -> str:
    return {
        "running": "active_observed",
        "completed": "completed_observed",
        "failed": "failed_observed",
        "stopped": "stopped_observed",
    }.get(state, f"{state}_observed")


async def reconcile_instrument_run(run_id: str) -> dict[str, Any]:
    """Reconcile one durable run against a bounded, read-only host snapshot.

    This never invokes physical control.  Identical host evidence is a no-op so
    polling cannot inflate the durable generation or append duplicate events.
    """
    async with async_session() as session:
        record = await _load_run(session, run_id)
        if not record.minknow_run_id:
            return await _run_response(session, record)
        try:
            host_payload = request_host_agent("GET", f"/ont/runs/{record.minknow_run_id}")
        except Exception:  # noqa: BLE001 - terminal state must survive host loss
            if record.state in TERMINAL_RUN_STATES:
                return await _run_response(session, record)
            host_payload = {"status": "unknown", "output_files": {"fastq": [], "pod5": [], "bam": []}}
        if not isinstance(host_payload, dict):
            if record.state in TERMINAL_RUN_STATES:
                return await _run_response(session, record)
            host_payload = {"status": "unknown", "output_files": {"fastq": [], "pod5": [], "bam": []}}
        try:
            state, output_files, bounded_snapshot = _bounded_host_snapshot(record, host_payload)
        except Exception:  # noqa: BLE001 - terminal host diagnostics must not escape or mutate state
            if record.state in TERMINAL_RUN_STATES:
                return await _run_response(session, record)
            raise ValueError("host-agent returned an invalid run status") from None
        # A terminal observation and its manifest are immutable evidence. Later
        # host retention/output churn must not advance this projection and detach
        # the manifest's bound observation generation.
        if record.state in TERMINAL_RUN_STATES:
            return await _run_response(session, record)
        previous = record.last_minknow_payload if isinstance(record.last_minknow_payload, dict) else {}
        if previous.get("observation_digest") == bounded_snapshot["observation_digest"]:
            return await _run_response(session, record)
        await _append_observation(
            session,
            record,
            event_type=_event_type_for_observed_state(state),
            state=state,
            minknow_payload=bounded_snapshot,
            output_files=output_files,
        )
        await session.commit()
        return await _run_response(session, record)


async def refresh_instrument_run_status(run_id: str) -> dict[str, Any]:
    """Backward-compatible name for the bounded reconciliation operation."""
    return await reconcile_instrument_run(run_id)


async def build_plasmid_qc_handoff(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build generic plasmid-QC input only from a terminal, digested host artifact."""
    async with async_session() as session:
        record = await _load_run(session, run_id)
        if record.state != "completed":
            raise ValueError("instrument run must be durably observed as completed before plasmid QC handoff")
        reference = Path(str(payload.get("reference_fasta") or "")).expanduser()
        if not reference.is_file() or reference.is_symlink():
            raise ValueError("server-issued reference snapshot is unavailable")
        validated_manifest = _valid_terminal_manifest(record)
        if validated_manifest is None:
            raise ValueError("instrument run has no valid terminal artifact manifest")
        manifest, artifacts = validated_manifest
        manifest_sha256 = record.terminal_artifact_manifest_sha256
        fastq = next((item for item in artifacts if item["kind"] == "fastq"), None)
        if not isinstance(fastq, dict):
            raise ValueError("instrument run terminal manifest has no FASTQ artifact for plasmid QC handoff")
        fastq_path = Path(str(fastq.get("path") or ""))
        try:
            if (
                not fastq_path.is_file()
                or fastq_path.is_symlink()
                or fastq_path.stat().st_size != int(fastq["bytes"])
                or _sha256_file(fastq_path) != str(fastq["sha256"])
            ):
                raise ValueError
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("instrument run FASTQ artifact is unavailable or digest-mismatched") from exc
        return {
            "model_id": "nanopore",
            "mode": "plasmid_qc",
            "params": {
                "ont_workflow_id": "ont_plasmid_qc",
                "fastq_path": str(fastq_path.resolve()),
                "reference_fasta": str(reference.resolve()),
                "run_fastq_qc": True,
                "run_modkit": False,
                "modified_bases": "none",
                "fastq_minimap2_preset": "map-ont",
                "source_instrument_run_id": run_id,
                "source_minknow_run_id": record.minknow_run_id,
                "source_instrument_artifact_manifest_sha256": manifest_sha256,
            },
            "fake_or_demo_devices": False,
        }


async def stop_instrument_run(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not bool(payload.get("confirm_stop")):
        raise ValueError("confirm_stop=true is required before stopping a MinKNOW run")
    async with async_session() as session:
        record = await _load_run(session, run_id)
        if not record.minknow_run_id:
            raise RuntimeError("instrument run has no recorded MinKNOW run ID to stop")
        # Reject a terminal-state stop before asking the host-agent to do work.
        _validate_state_edge(record.state, "stopped")
        host_payload = request_host_agent("POST", f"/ont/runs/{record.minknow_run_id}/stop", {"confirm_stop": True})
        if not isinstance(host_payload, dict):
            raise RuntimeError(f"host-agent returned non-object stop payload: {host_payload!r}")
        state = _normalized_state(host_payload.get("status"), fallback="stopped")
        await _append_observation(
            session,
            record,
            event_type="stop_observed",
            state=state,
            minknow_payload=host_payload,
        )
        await session.commit()
        return await _run_response(session, record)


def get_position_protocol_options(position: str) -> dict[str, Any]:
    """Return host-agent protocol options/preflight for one ONT position."""
    host_payload = request_host_agent("GET", f"/ont/positions/{position}/protocol-options")
    if isinstance(host_payload, dict) and "can_start" in host_payload:
        return host_payload

    # Backward-compatible fallback for host agents that expose only position
    # discovery. This is still truthful: it never invents protocol IDs/models.
    position_payload = get_ont_position(position)
    position_detail = position_payload.get("position") if isinstance(position_payload.get("position"), dict) else position_payload
    if not isinstance(position_detail, dict):
        position_detail = {"position": position, "flow_cell": {"present": False}}
    status = get_ont_status()
    output_directories = ((status.get("minknow") or {}).get("output_directories") or {}) if isinstance(status, dict) else {}
    return build_start_preflight(
        position=position_detail,
        kit=None,
        basecalling_enabled=True,
        output_directories=output_directories,
    )
