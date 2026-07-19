from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .command_models import CommandRequest
from .command_policy import CommandAdmissionContext, evaluate_command
from .command_registry import CommandDefinition, CommandName
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
        token_authorized: bool,
        mutations_enabled: bool,
    ) -> CommandRecord:
        definition = self.registry[request.command]
        snapshot = self.connection.snapshot()
        context = CommandAdmissionContext(
            token_authorized=token_authorized,
            mutations_enabled=mutations_enabled,
            active=snapshot.active,
            generation=snapshot.generation,
            observation_fresh=snapshot.observation_fresh,
            runtime_ready=snapshot.runtime_ready,
            hardware_ready=snapshot.hardware_ready,
            capabilities=frozenset(snapshot.capabilities),
        )
        decision = evaluate_command(request, definition, context)
        if not decision.allowed:
            raise CommandDeniedError(decision.reasons)

        fingerprint = _fingerprint(request.model_dump(mode="json"))
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
            try:
                response = await client.request(definition.route_key, json_data=payload)
                acknowledged = _strict_acknowledgement(response)
                status = "acknowledged" if acknowledged else "delivered_unacknowledged"
                detail = "Robot acknowledged command" if acknowledged else "Command delivered; robot acknowledgement absent"
            except Exception as exc:
                acknowledged = False
                status = "delivery_failed"
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
        token_authorized: bool,
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
        if not token_authorized:
            reasons.append("Valid operator credential is required")
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


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _strict_acknowledgement(response: object) -> bool:
    return isinstance(response, Mapping) and (
        response.get("acknowledged") is True or response.get("ok") is True
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
