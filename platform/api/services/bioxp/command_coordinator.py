from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict, deque
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, cast
from uuid import uuid4

from .command_models import CommandRequest, StopAxisDiagnosticCommand
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


class CommandCoordinator:
    """Serializes normal commands and keeps emergency delivery independent."""

    def __init__(
        self,
        connection: ConnectionProtocol,
        registry: Mapping[CommandName, CommandDefinition],
        *,
        history_limit: int = 100,
    ) -> None:
        if history_limit < 1:
            raise ValueError("history_limit must be at least 1")
        self.connection = connection
        self.registry = registry
        self._normal_busy = False
        self._history_limit = history_limit
        self._history: deque[CommandRecord] = deque(maxlen=history_limit)
        self._by_id: dict[str, CommandRecord] = {}
        self._idempotent: OrderedDict[str, tuple[str, CommandRecord | EmergencyStopResult]] = OrderedDict()
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
        request: StopAxisDiagnosticCommand,
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
            ownership=snapshot.ownership,
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
            payload = {
                "axis": request.axis,
                "operator_ack": "STOP_AXIS",
                "reason": f"BMS operator requested {request.axis} stop",
            }
            try:
                response = await client.request(definition.route_key, json_data=payload or None)
                handler_response = _bounded_handler_response(response)
                acknowledged = _strict_acknowledgement(response)
                semantic_rejected = isinstance(response, Mapping) and response.get("ok") is False
                if semantic_rejected:
                    status = "delivery_failed"
                    detail = _bounded_text(
                        f"Robot reported stop failure: {response.get('error') or response.get('detail') or 'ok=false'}",
                        limit=4_096,
                    )
                else:
                    status = "acknowledged" if acknowledged else "delivered_unacknowledged"
                    detail = "Robot acknowledged component stop" if acknowledged else "Component stop delivered; robot acknowledgement absent"
            except RobotResponseError as exc:
                acknowledged = False
                status = "delivery_failed"
                handler_response = _bounded_handler_response({"http_status": exc.status_code, "detail": exc.detail})
                detail = f"Robot rejected component stop with HTTP {exc.status_code}"
            except Exception as exc:
                acknowledged = False
                status = "delivery_failed"
                handler_response = None
                detail = _bounded_text(str(exc) or exc.__class__.__name__, limit=4_096)
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
            self._remember_idempotent(request.idempotency_key, fingerprint, record)
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
            ownership=snapshot.ownership,
        )
        # Activation changes the exact predicates that admitted it. Its replay
        # may suppress only those activation-mutated gates; every other command
        # must satisfy its current ownership and runtime policy before lookup.
        is_activation = request.command == "activate_usb_for_service"
        if is_activation and not _activation_replay_ownership_is_canonical(context.ownership):
            ownership = context.ownership
            raise CommandDeniedError((
                "Activation replay requires canonical unbound or service-owned current ownership "
                f"(ownership={ownership!r})",
            ))
        replay_definition = (
            replace(
                definition,
                requires_runtime_inactive=False,
                ownership_policy="independent",
            )
            if request.command == "activate_usb_for_service"
            else definition
        )
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
                    "operator_ack": "RECOVER_MOTION",
                    "operator_reason": "BMS operator requested controller initialization",
                }
            elif request.command == "run_axis_diagnostic":
                payload = {
                    "axis": request.axis,
                    "operation": request.operation,
                    "operator_ack": "RUN_AXIS_DIAGNOSTIC",
                    "reason": f"BMS operator requested {request.axis} {request.operation}",
                }
            try:
                response = await client.request(definition.route_key, json_data=payload or None)
                handler_response = _bounded_handler_response(response)
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
                    detail = _bounded_text(
                        f"Robot reported command failure: {response.get('error') or response.get('detail') or 'ok=false'}",
                        limit=4_096,
                    )
                elif queued:
                    status = "queued"
                    detail = "Robot accepted the command into its queue; execution and physical effect are not yet observed"
                else:
                    status = "acknowledged" if acknowledged else "delivered_unacknowledged"
                    detail = "Robot acknowledged command" if acknowledged else "Command delivered; robot acknowledgement absent"
            except RobotResponseError as exc:
                acknowledged = False
                status = "delivery_failed"
                handler_response = _bounded_handler_response({"http_status": exc.status_code, "detail": exc.detail})
                observer = getattr(self.connection, "observe_command_response", None)
                if callable(observer):
                    observer(exc.detail)
                detail = f"Robot rejected command with HTTP {exc.status_code}"
            except Exception as exc:
                acknowledged = False
                status = "delivery_failed"
                handler_response = None
                detail = _bounded_text(str(exc) or exc.__class__.__name__, limit=4_096)
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
            self._remember_idempotent(request.idempotency_key, fingerprint, record)
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
                detail = _bounded_text(
                    f"Emergency-stop delivery failed; physical effect is not verified: {exc}",
                    limit=4_096,
                )
            result = EmergencyStopResult(
                idempotency_key=idempotency_key,
                generation=snapshot.generation,
                attempted_at=attempted_at,
                delivery_attempted=True,
                remote_acknowledged=acknowledged,
                physical_effect_verified=False,
                detail=detail,
            )
            self._remember_idempotent(idempotency_key, fingerprint, result)
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
            self._idempotent.pop(evicted.idempotency_key, None)
        self._history.append(record)
        self._by_id[record.command_id] = record

    def _remember_idempotent(
        self,
        key: str,
        fingerprint: str,
        result: CommandRecord | EmergencyStopResult,
    ) -> None:
        self._idempotent.pop(key, None)
        self._idempotent[key] = (fingerprint, result)
        while len(self._idempotent) > self._history_limit:
            self._idempotent.popitem(last=False)


_MAX_HANDLER_DEPTH = 8
_MAX_HANDLER_ITEMS = 32
_MAX_HANDLER_NODES = 512
_MAX_HANDLER_TEXT = 2_048
_MAX_HANDLER_BUDGET = 8_192
_TRUNCATED = "…[truncated]"


def _bounded_text(value: object, *, limit: int = _MAX_HANDLER_TEXT) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(_TRUNCATED))
    return f"{text[:keep]}{_TRUNCATED}"


def _bounded_handler_response(value: object) -> dict[str, Any]:
    budget = [_MAX_HANDLER_BUDGET]
    nodes = [_MAX_HANDLER_NODES]

    def normalize(item: object, depth: int) -> Any:
        if nodes[0] <= 0 or budget[0] <= 0:
            return _TRUNCATED
        nodes[0] -= 1
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, str):
            allowed = min(_MAX_HANDLER_TEXT, budget[0])
            text = _bounded_text(item, limit=max(len(_TRUNCATED), allowed))
            budget[0] -= len(text)
            return text
        if depth >= _MAX_HANDLER_DEPTH:
            return _TRUNCATED
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for index, (key, child) in enumerate(item.items()):
                if index >= _MAX_HANDLER_ITEMS or nodes[0] <= 0 or budget[0] <= 0:
                    result["_truncated"] = True
                    break
                result[_bounded_text(key, limit=128)] = normalize(child, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            list_result = [normalize(child, depth + 1) for child in item[:_MAX_HANDLER_ITEMS]]
            if len(item) > _MAX_HANDLER_ITEMS:
                list_result.append(_TRUNCATED)
            return list_result
        return _bounded_text(item)

    bounded = normalize(value, 0)
    response = bounded if isinstance(bounded, dict) else {"response": bounded}
    if _serialized_chars(response) <= _MAX_HANDLER_BUDGET:
        return response

    # The recursive budget above preserves useful ordinary structure, while
    # this final projection is the authoritative whole-record size guard. It
    # accounts for mapping keys, scalar serialization, and JSON punctuation.
    summary: dict[str, Any] = {"_truncated": True}
    if isinstance(value, Mapping):
        for key in ("ok", "acknowledged", "queued", "http_status", "status"):
            scalar = value.get(key)
            if scalar is None or isinstance(scalar, (bool, int, float)):
                if key in value:
                    summary[key] = scalar
    detail = _receipt_operator_detail(value)
    if detail:
        summary["detail"] = detail
    # Fixed keys plus one capped detail keep this comfortably below the hard
    # ceiling; retain the assertion as a fail-closed invariant.
    if _serialized_chars(summary) > _MAX_HANDLER_BUDGET:
        summary = {"_truncated": True, "detail": _bounded_text(detail or "receipt omitted", limit=2_048)}
    assert _serialized_chars(summary) <= _MAX_HANDLER_BUDGET
    return summary


def _serialized_chars(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))


def _receipt_operator_detail(value: object, depth: int = 0) -> str | None:
    if depth > _MAX_HANDLER_DEPTH or value is None:
        return None
    if isinstance(value, str):
        text = _bounded_text(value, limit=4_096).strip()
        return text or None
    if isinstance(value, Mapping):
        for key in ("detail", "error", "message", "reason", "block_reason", "startup_error"):
            if key in value:
                found = _receipt_operator_detail(value[key], depth + 1)
                if found:
                    return found
        return None
    if isinstance(value, (list, tuple)):
        details = []
        for child in value[:_MAX_HANDLER_ITEMS]:
            found = _receipt_operator_detail(child, depth + 1)
            if found:
                details.append(found)
            if sum(len(item) for item in details) >= 4_096:
                break
        return _bounded_text("; ".join(details), limit=4_096) if details else None
    return None


def _activation_replay_ownership_is_canonical(ownership: object) -> bool:
    if not isinstance(ownership, Mapping):
        return False
    projection = (ownership.get("transport"), ownership.get("usb"), ownership.get("router"))
    return projection == ("unbound", "unbound", "unbound") or projection == ("owned", "service", "running")


def _set_command_active(connection: object, value: bool) -> None:
    setter = getattr(connection, "set_command_active", None)
    if callable(setter):
        setter(value)


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
