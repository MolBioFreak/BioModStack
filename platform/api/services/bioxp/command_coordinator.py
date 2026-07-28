from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, cast
from uuid import uuid4

from .command_models import CommandRequest
from .command_policy import CommandAdmissionContext, evaluate_command
from .command_registry import CommandDefinition, CommandName
from .errors import RobotResponseError
from .models import BioXpSnapshot, CommandRecord, EmergencyStopResult


class CommandBusyError(RuntimeError):
    status_code = 409


class CommandDeniedError(RuntimeError):
    status_code = 409

    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class IdempotencyConflictError(RuntimeError):
    status_code = 409


class ConnectionProtocol(Protocol):
    active_client: Any

    def snapshot(self) -> BioXpSnapshot: ...


_INLINE_HARDWARE_EVIDENCE_COMMANDS = frozenset({
    "activate_usb_for_service",
    "initialize_oem_environment",
    "record_oem_motor_stage_observation",
    "collect_axis_diagnostics",
    "run_axis_diagnostic",
})


class CommandCoordinator:
    """Serializes normal commands and keeps emergency delivery independent."""

    def __init__(
        self,
        connection: ConnectionProtocol,
        registry: Mapping[CommandName, CommandDefinition],
        *,
        history_limit: int = 100,
    ) -> None:
        self.connection = connection
        self.registry = registry
        self._normal_busy = False
        self._history: deque[CommandRecord] = deque(maxlen=history_limit)
        self._by_id: dict[str, CommandRecord] = {}
        self._idempotent: dict[str, tuple[str, CommandRecord | EmergencyStopResult]] = {}
        self._inflight: dict[
            str,
            tuple[str, asyncio.Future[CommandRecord | EmergencyStopResult]],
        ] = {}

    async def execute(
        self,
        request: CommandRequest,
        *,
        mutations_enabled: bool,
    ) -> CommandRecord:
        definition = self.registry[request.command]
        if not definition.enabled:
            raise CommandDeniedError((
                definition.disabled_reason or f"command {request.command} is disabled",
            ))
        # A component stop is an interrupt lane, not a normal workflow. It must
        # be deliverable while the generation lease is held by an in-flight
        # diagnostic; reconnect/disconnect are already excluded by that lease.
        if request.command == "stop_axis_diagnostic":
            return await self._execute_interrupt(request, mutations_enabled=mutations_enabled)
        lease_factory = cast(
            Callable[[int], AbstractAsyncContextManager[Any]] | None,
            getattr(self.connection, "workflow_lease", None),
        )
        if callable(lease_factory):
            async with lease_factory(request.expected_generation):
                return await self._execute_with_generation_lease(
                    request,
                    mutations_enabled=mutations_enabled,
                )
        # Compatibility path for isolated unit-test fakes. Production
        # BioXpConnectionService always supplies workflow_lease.
        return await self._execute_with_generation_lease(
            request,
            mutations_enabled=mutations_enabled,
        )

    async def _execute_interrupt(
        self,
        request: CommandRequest,
        *,
        mutations_enabled: bool,
    ) -> CommandRecord:
        definition = self.registry[request.command]
        fingerprint = _fingerprint(request.model_dump(mode="json"))
        snapshot = self.connection.snapshot()
        context = CommandAdmissionContext(
            mutations_enabled=mutations_enabled,
            active=snapshot.active,
            generation=snapshot.generation,
            observation_fresh=snapshot.observation_fresh,
            runtime_ready=snapshot.runtime_ready,
            hardware_ready=snapshot.hardware_ready,
            capabilities=frozenset(snapshot.capabilities),
            startup_lifecycle=snapshot.startup_lifecycle,
            maintenance_state=snapshot.maintenance_state,
        )
        decision = evaluate_command(request, definition, context)
        if not decision.allowed:
            raise CommandDeniedError(decision.reasons)

        prior = self._idempotent.get(request.idempotency_key)
        if prior is not None:
            if prior[0] != fingerprint or not isinstance(prior[1], CommandRecord):
                raise IdempotencyConflictError("Idempotency key was already used for a different operation")
            return prior[1]
        inflight = self._inflight.get(request.idempotency_key)
        if inflight is not None:
            if inflight[0] != fingerprint:
                raise IdempotencyConflictError("Idempotency key was already used for a different operation")
            joined = await asyncio.shield(inflight[1])
            if not isinstance(joined, CommandRecord):
                raise IdempotencyConflictError("Idempotency key was already used for a different operation")
            return joined

        client = self.connection.active_client
        if client is None or definition.route_key is None:
            raise CommandDeniedError(("Active robot client is unavailable",))
        future: asyncio.Future[CommandRecord | EmergencyStopResult] = asyncio.get_running_loop().create_future()
        self._inflight[request.idempotency_key] = (fingerprint, future)
        started_at = _utcnow()
        command_id = str(uuid4())
        try:
            payload = request.model_dump(mode="json", exclude={"command", "expected_generation", "idempotency_key"})
            try:
                response = await client.request(definition.route_key, json_data=payload or None)
                handler_response = dict(response) if isinstance(response, Mapping) else {"response": response}
                acknowledged = _strict_acknowledgement(response)
                semantic_rejected = isinstance(response, Mapping) and response.get("ok") is False
                if semantic_rejected:
                    status = "delivery_failed"
                    detail = f"Robot reported stop failure: {response.get('error') or response.get('detail') or 'ok=false'}"
                else:
                    status = "acknowledged" if acknowledged else "delivered_unacknowledged"
                    detail = "Robot acknowledged component stop" if acknowledged else "Component stop delivered; robot acknowledgement absent"
            except RobotResponseError as exc:
                acknowledged = False
                status = "delivery_failed"
                handler_response = {"http_status": exc.status_code, "detail": exc.detail}
                detail = f"Robot rejected component stop with HTTP {exc.status_code}"
            except Exception as exc:
                acknowledged = False
                status = "delivery_failed"
                handler_response = None
                detail = str(exc) or exc.__class__.__name__
            record = CommandRecord(
                command_id=command_id,
                command=request.command,
                idempotency_key=request.idempotency_key,
                generation=snapshot.generation,
                status=status,
                started_at=started_at,
                finished_at=_utcnow(),
                remote_acknowledged=acknowledged,
                physical_effect_verified=False,
                detail=detail,
                handler_response=handler_response,
            )
            self._remember(record)
            self._idempotent[request.idempotency_key] = (fingerprint, record)
            future.set_result(record)
            return record
        except BaseException:
            future.cancel()
            raise
        finally:
            self._inflight.pop(request.idempotency_key, None)

    async def _execute_with_generation_lease(
        self,
        request: CommandRequest,
        *,
        mutations_enabled: bool,
    ) -> CommandRecord:
        definition = self.registry[request.command]
        fingerprint = _fingerprint(request.model_dump(mode="json"))
        snapshot = self.connection.snapshot()
        context = CommandAdmissionContext(
            mutations_enabled=mutations_enabled,
            active=snapshot.active,
            generation=snapshot.generation,
            observation_fresh=snapshot.observation_fresh,
            runtime_ready=snapshot.runtime_ready,
            hardware_ready=snapshot.hardware_ready,
            capabilities=frozenset(snapshot.capabilities),
            startup_lifecycle=snapshot.startup_lifecycle,
            maintenance_state=snapshot.maintenance_state,
        )
        # A completed activation may itself change runtime readiness. Replays
        # still require all current authorization and generation gates; only
        # that activation-mutated predicate is suppressed before replay lookup.
        replay_definition = replace(definition, requires_runtime_inactive=False)
        replay_decision = evaluate_command(request, replay_definition, context)
        if not replay_decision.allowed:
            raise CommandDeniedError(replay_decision.reasons)
        prior = self._idempotent.get(request.idempotency_key)
        if prior is not None:
            if prior[0] != fingerprint or not isinstance(prior[1], CommandRecord):
                raise IdempotencyConflictError("Idempotency key was already used for a different operation")
            return prior[1]
        inflight = self._inflight.get(request.idempotency_key)
        if inflight is not None:
            if inflight[0] != fingerprint:
                raise IdempotencyConflictError("Idempotency key was already used for a different operation")
            joined = await asyncio.shield(inflight[1])
            if not isinstance(joined, CommandRecord):
                raise IdempotencyConflictError("Idempotency key was already used for a different operation")
            return joined
        decision = evaluate_command(request, definition, context)
        if not decision.allowed:
            raise CommandDeniedError(decision.reasons)

        if self._normal_busy:
            raise CommandBusyError("Another normal BioXP command is already active")

        client = self.connection.active_client
        if client is None or definition.route_key is None:
            raise CommandDeniedError(("Active robot client is unavailable",))
        future: asyncio.Future[CommandRecord | EmergencyStopResult] = asyncio.get_running_loop().create_future()
        self._inflight[request.idempotency_key] = (fingerprint, future)
        self._normal_busy = True
        _set_command_active(self.connection, True)
        started_at = _utcnow()
        command_id = str(uuid4())
        try:
            payload = request.model_dump(mode="json", exclude={"command", "expected_generation", "idempotency_key"})
            if request.command == "run_oem_motor_stage":
                payload = {
                    "name": "startupHomingStepwise",
                    "mode": request.mode,
                    "operator_ack": request.operator_ack,
                    "params": {"homing_step": request.stage},
                }
            elif request.command == "record_oem_motor_stage_observation":
                params = {
                    "homing_step": request.stage,
                    "record_stage_observation": True,
                    "observed_pass": request.observed_pass,
                }
                params["operator_note"] = request.operator_note
                payload = {
                    "name": "startupHomingStepwise",
                    "mode": "live",
                    "operator_ack": request.operator_ack,
                    "params": params,
                }
            elif request.command == "recover_motion_non_homing":
                payload = {
                    "run_homing": False,
                    "operator_ack": request.operator_ack,
                    "operator_reason": request.reason,
                }
            try:
                response = await client.request(definition.route_key, json_data=payload or None)
                handler_response = dict(response) if isinstance(response, Mapping) else {"response": response}
                observer = getattr(self.connection, "observe_command_response", None)
                if callable(observer):
                    observer(response)
                acknowledged = _strict_acknowledgement(response)
                queue_receipt = isinstance(response, Mapping) and "queued" in response
                queued = queue_receipt and acknowledged
                semantic_rejected = isinstance(response, Mapping) and (
                    response.get("ok") is False or (queue_receipt and not queued)
                )
                if semantic_rejected:
                    status = "delivery_failed"
                    detail = f"Robot reported command failure: {response.get('error') or response.get('detail') or 'ok=false'}"
                elif queued:
                    status = "queued"
                    detail = "Robot accepted the command into its queue; execution and physical effect are not yet observed"
                else:
                    status = "acknowledged" if acknowledged else "delivered_unacknowledged"
                    detail = "Robot acknowledged command" if acknowledged else "Command delivered; robot acknowledgement absent"
                if acknowledged and not queued and request.command in _INLINE_HARDWARE_EVIDENCE_COMMANDS:
                    handler_response["inline_hardware_evidence"] = await _collect_inline_hardware_evidence(client)
            except RobotResponseError as exc:
                acknowledged = False
                status = "delivery_failed"
                handler_response = {"http_status": exc.status_code, "detail": exc.detail}
                observer = getattr(self.connection, "observe_command_response", None)
                if callable(observer):
                    observer(exc.detail)
                detail = f"Robot rejected command with HTTP {exc.status_code}"
            except Exception as exc:
                acknowledged = False
                status = "delivery_failed"
                handler_response = None
                detail = str(exc) or exc.__class__.__name__
            record = CommandRecord(
                command_id=command_id,
                command=request.command,
                idempotency_key=request.idempotency_key,
                generation=snapshot.generation,
                status=status,
                started_at=started_at,
                finished_at=_utcnow(),
                remote_acknowledged=acknowledged,
                physical_effect_verified=False,
                detail=detail,
                handler_response=handler_response,
            )
            self._remember(record)
            self._idempotent[request.idempotency_key] = (fingerprint, record)
            future.set_result(record)
            return record
        except BaseException:
            future.cancel()
            raise
        finally:
            self._inflight.pop(request.idempotency_key, None)
            self._normal_busy = False
            _set_command_active(self.connection, False)

    async def emergency_stop(
        self,
        *,
        expected_generation: int,
        idempotency_key: str,
        mutations_enabled: bool,
    ) -> EmergencyStopResult:
        request_shape = {
            "operation": "emergency_stop",
            "expected_generation": expected_generation,
            "idempotency_key": idempotency_key,
        }
        fingerprint = _fingerprint(request_shape)
        snapshot = self.connection.snapshot()
        reasons = []
        if not mutations_enabled:
            reasons.append("BioXP mutations are disabled by the server kill switch")
        if not snapshot.active or self.connection.active_client is None:
            reasons.append("An active target connection is required")
        if expected_generation != snapshot.generation:
            reasons.append("Expected connection generation does not match the active generation")
        if reasons:
            raise CommandDeniedError(tuple(reasons))

        prior = self._idempotent.get(idempotency_key)
        if prior is not None:
            if prior[0] != fingerprint or not isinstance(prior[1], EmergencyStopResult):
                raise IdempotencyConflictError("Idempotency key was already used for a different operation")
            return prior[1]
        inflight = self._inflight.get(idempotency_key)
        if inflight is not None:
            if inflight[0] != fingerprint:
                raise IdempotencyConflictError("Idempotency key was already used for a different operation")
            joined = await asyncio.shield(inflight[1])
            if not isinstance(joined, EmergencyStopResult):
                raise IdempotencyConflictError("Idempotency key was already used for a different operation")
            return joined

        future: asyncio.Future[CommandRecord | EmergencyStopResult] = asyncio.get_running_loop().create_future()
        self._inflight[idempotency_key] = (fingerprint, future)

        attempted_at = _utcnow()
        try:
            try:
                response = await self.connection.active_client.request("emergency_stop", json_data={})
                acknowledged = _strict_acknowledgement(response)
                detail = (
                    "Emergency-stop request acknowledged remotely; physical effect is not verified"
                    if acknowledged
                    else "Emergency-stop request delivered; remote acknowledgement and physical effect are not verified"
                )
            except Exception as exc:
                acknowledged = False
                detail = f"Emergency-stop delivery failed; physical effect is not verified: {exc}"
            result = EmergencyStopResult(
                idempotency_key=idempotency_key,
                generation=snapshot.generation,
                attempted_at=attempted_at,
                delivery_attempted=True,
                remote_acknowledged=acknowledged,
                physical_effect_verified=False,
                detail=detail,
            )
            self._idempotent[idempotency_key] = (fingerprint, result)
            future.set_result(result)
            return result
        except BaseException:
            future.cancel()
            raise
        finally:
            self._inflight.pop(idempotency_key, None)

    def get(self, command_id: str) -> CommandRecord | None:
        return self._by_id.get(command_id)

    def history(self) -> tuple[CommandRecord, ...]:
        return tuple(self._history)

    def _remember(self, record: CommandRecord) -> None:
        if len(self._history) == self._history.maxlen and self._history:
            evicted = self._history[0]
            self._by_id.pop(evicted.command_id, None)
        self._history.append(record)
        self._by_id[record.command_id] = record


def _set_command_active(connection: object, value: bool) -> None:
    setter = getattr(connection, "set_command_active", None)
    if callable(setter):
        setter(value)


async def _collect_inline_hardware_evidence(client: Any) -> dict[str, Any]:
    try:
        response = await client.request("collect_hardware_snapshot", json_data=None)
        payload = dict(response) if isinstance(response, Mapping) else {}
        snapshot = payload.get("snapshot")
        snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, Mapping) else None
        published = (
            payload.get("ok") is True
            and payload.get("published") is True
            and isinstance(snapshot_id, str)
            and bool(snapshot_id)
        )
        if not published:
            return {
                "attempted": True,
                "published": False,
                "error": str(
                    payload.get("error")
                    or payload.get("detail")
                    or "hardware snapshot was not published"
                ),
            }
        return {
            "attempted": True,
            "published": True,
            "snapshot_id": snapshot_id,
        }
    except Exception as exc:
        return {
            "attempted": True,
            "published": False,
            "error": str(exc) or exc.__class__.__name__,
        }


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _strict_acknowledgement(response: object) -> bool:
    if not isinstance(response, Mapping):
        return False
    if "queued" in response:
        return response.get("ok") is True and response.get("queued") is True
    return response.get("acknowledged") is True or response.get("ok") is True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
