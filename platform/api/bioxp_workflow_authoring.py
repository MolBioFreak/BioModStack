"""Offline draft composition; no controller imports, jobs, geometry or scheduler.

Native schema/template exported from manual_pipetting at the asset's source
commit. The small emitter mirrors that compiler; differential tests guard drift.
Draft persistence is structural only. Compilation never mutates the saved JSON.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_serializer, field_validator, model_validator


class Structure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class WorkflowDraftRow(Structure):
    step_id: str = Field(min_length=1)
    intent: dict[str, Any]


class WorkflowLabware(Structure):
    id: str
    station: str
    name: str
    profile_id: str


class WorkflowMaterial(Structure):
    id: str
    name: str
    kind: Literal["sample", "reagent"]
    description: str


class WorkflowAssignment(Structure):
    id: str
    labware_id: str
    well: str
    material_id: str
    volume_ul: str | float | None


class WorkflowDeckPlan(Structure):
    labware: list[WorkflowLabware]
    materials: list[WorkflowMaterial]
    assignments: list[WorkflowAssignment]


class WorkflowDraftBase(Structure):
    steps: list[WorkflowDraftRow]
    editor_state: dict[str, Any]

    @model_validator(mode="after")
    def distinct_steps(self):
        if len({row.step_id for row in self.steps}) != len(self.steps):
            raise ValueError("BioXP workflow draft step_id values must be distinct")
        return self


class WorkflowDraftV1(WorkflowDraftBase):
    schema_: Literal["bms.bioxp-workflow-draft.v1"] = Field(alias="schema")


class WorkflowPlan(WorkflowDraftBase):
    schema_: Literal["bms.bioxp-workflow-draft.v2"] = Field(alias="schema")
    deck_plan: WorkflowDeckPlan


WorkflowDraft = Annotated[WorkflowDraftV1 | WorkflowPlan, Field(discriminator="schema_")]
DRAFT_ADAPTER = TypeAdapter(WorkflowDraft)


class TransferEndpoint(Structure):
    station: str
    location_id: int | str
    wells: list[str]


class WorkflowTransferIntent(Structure):
    operation: Literal["transfer"]
    source: TransferEndpoint
    destination: TransferEndpoint
    channels: list[int]
    volume_ul: str | float
    aspirate_speed: str | float
    dispense_speed: str | float
    source_position_flag: str | float
    destination_position_flag: str | float
    source_lift_height_steps: str | float | None
    destination_lift_height_steps: str | float | None


class WorkflowPreviewRequest(Structure):
    draft: WorkflowDraft
    protocol_id: str


class WorkflowPreviewAction(Structure):
    index: int
    step_id: str
    pair_index: int | None
    kind: str
    params: dict[str, Any]
    label: str
    station: str | None
    well: str | None


class WorkflowPreviewIssue(Structure):
    step_id: str | None
    message: str


class WorkflowPreviewResponse(Structure):
    document: dict[str, Any] | None
    actions: list[WorkflowPreviewAction]
    issues: list[WorkflowPreviewIssue]


_NATIVE = json.loads((Path(__file__).parent / "schemas/bioxp_workflow_native.json").read_text())
_SCHEMA = _NATIVE["request"]
_OPERATIONS = _SCHEMA["properties"]["steps"]["items"]["discriminator"]["mapping"]


def validate_draft(value: Any) -> None:
    DRAFT_ADAPTER.validate_python(value)
    json.dumps(value, allow_nan=False)


class WorkflowJobCloneRequest(Structure):
    job_id: str = Field(min_length=1)
    document: dict[str, Any]


class WorkflowJobClone(Structure):
    name: str | None
    draft: WorkflowDraft | None
    issues: list[WorkflowPreviewIssue]

    @field_validator("draft", mode="wrap")
    @classmethod
    def retain_raw_snapshot(cls, value, handler):
        # Validate the existing typed contract, but never normalize raw saved JSON
        # (notably integer deck volumes into floats). Unknown intent/editor keys
        # belong to the snapshot, not to the native compilation boundary.
        handler(value)
        if isinstance(value, WorkflowDraftBase):
            value = value.model_dump(by_alias=True)
        if value is not None:
            json.dumps(value, allow_nan=False)
        return deepcopy(value)

    @field_serializer("draft")
    def serialize_raw_snapshot(self, value):
        return value


def _clone_behavior(document: dict) -> dict:
    """Ignore only native compiler identity and step-index provenance.

    Keep all stage/action flags, params and unknown fields. In particular,
    metadata is not generally ignorable: it may carry meaningful effects.
    """
    result = deepcopy(document)
    result.pop("protocol_id", None)
    for stage in result["stages"]:
        for action in stage["actions"]:
            action.pop("action_id", None)
            metadata = action.get("metadata")
            if isinstance(metadata, dict):
                metadata.pop("manual_step", None)
    return result


def _same_json(left: Any, right: Any) -> bool:
    """JSON structural equality: numeric spelling is immaterial, bool is not 0/1."""
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def clone_job(request: WorkflowJobCloneRequest) -> WorkflowJobClone:
    """Pure snapshot recovery or lossless canonical manual-action projection."""
    document = request.document
    try:
        metadata = document.get("metadata", {})
        if isinstance(metadata, dict) and "bms_saved_workflow" in metadata:
            snapshot = metadata["bms_saved_workflow"]
            if not isinstance(snapshot, dict):
                raise ValueError("bms_saved_workflow must be an object")
            value = snapshot.get("draft")
            validate_draft(value)
            value = deepcopy(value)
            name = snapshot.get("name")
            if name is not None and not isinstance(name, str):
                raise ValueError("saved workflow name must be a string or null")
            source_id = snapshot.get("id")
            if source_id is not None:
                if not isinstance(source_id, str):
                    raise ValueError("saved workflow id must be a string or null")
                value["editor_state"]["cloned_from_workflow_id"] = source_id
        else:
            stages = document.get("stages")
            if not isinstance(stages, list) or len(stages) != 1 or not isinstance(stages[0], dict):
                raise ValueError("legacy projection requires one canonical manual stage")
            actions = stages[0].get("actions")
            if not isinstance(actions, list) or not actions:
                raise ValueError("legacy projection requires explicit manual actions")
            rows = []
            for index, action in enumerate(actions):
                if not isinstance(action, dict) or not isinstance(action.get("params"), dict):
                    raise ValueError(f"action {index}: expected native action parameters")
                kind, params = action.get("kind"), action["params"]
                if kind in ("pipette_position", "pipette_manual_physical"):
                    intent = deepcopy(params)
                elif kind in ("pipette_aspirate", "pipette_dispense"):
                    # Extra liquid fields are checked by exact recomposition,
                    # never silently lost or interpreted as defaults.
                    intent = {"operation": kind.removeprefix("pipette_"),
                              **{key: deepcopy(params[key]) for key in ("channels", "volume_ul", "speed")}}
                else:
                    raise ValueError(f"action {index}: unsupported native kind {kind!r}")
                native_intent(intent)
                rows.append({"step_id": f"cloned-action-{index}", "intent": intent})
            value = {"schema": "bms.bioxp-workflow-draft.v1", "steps": rows, "editor_state": {}}
            rebuilt = preview(WorkflowPreviewRequest.model_validate({"draft": value, "protocol_id": "clone-comparison"}))
            if rebuilt.document is None or not _same_json(_clone_behavior(document), _clone_behavior(rebuilt.document)):
                raise ValueError("native document has noncanonical fields or effects that authoring cannot preserve exactly")
            name = None
        value["editor_state"]["cloned_from_job_id"] = request.job_id
        return WorkflowJobClone(name=f"{name} copy" if name is not None else None, draft=value, issues=[])
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return WorkflowJobClone(name=None, draft=None, issues=[WorkflowPreviewIssue(
            step_id=None, message=f"Clone unavailable: {exc}")])


def discovery() -> dict[str, Any]:
    return {
        "draft": DRAFT_ADAPTER.json_schema(),
        "transfer": WorkflowTransferIntent.model_json_schema(),
        "preview_request": WorkflowPreviewRequest.model_json_schema(),
        "preview_response": WorkflowPreviewResponse.model_json_schema(),
        "clone_request": WorkflowJobCloneRequest.model_json_schema(),
        "clone_response": WorkflowJobClone.model_json_schema(),
        "native_request": deepcopy(_SCHEMA),
        "native_source_commit": _NATIVE["source_commit"],
        "native_locations": deepcopy(_NATIVE["locations"]),
        "authoring_policy": "All native fields must be explicit; numeric text converts only for preview. No scientific defaults are inserted.",
        "alignment": "Ordered head-reference well pairs; actual machine TipLocation owns alignment. Channels select plungers, not independent XY.",
        "scope": "Logical native action sequence only, not physical feasibility or hardware verification.",
    }


def _convert(value: Any, schema: dict, field: str = "intent") -> Any:
    """Schema-directed numeric-text conversion, never bool/string coercion."""
    if "discriminator" in schema:
        d = schema["discriminator"]
        ref = d["mapping"].get(value.get(d["propertyName"])) if isinstance(value, dict) else None
        if ref is None:
            raise ValueError(f"{field}: unknown native operation")
        schema = _SCHEMA["$defs"][ref.rsplit("/", 1)[-1]]
    if schema.get("type") == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{field}: expected object")
        props = schema["properties"]
        # Matches the existing nativeIntent explicit-field authoring boundary.
        if set(value) != set(props):
            raise ValueError(f"{field}: missing {sorted(set(props)-set(value))}; unknown {sorted(set(value)-set(props))}")
        result = {k: _convert(v, props[k], k) for k, v in value.items()}
    elif schema.get("type") == "array" and isinstance(value, list):
        result = [_convert(v, schema["items"], field) for v in value]
    else:
        types = {s.get("type") for s in schema.get("anyOf", [schema])}
        result = value
        if value == "" and "null" in types and field in ("aspirate_delay_ms", "dispense_delay_ms"):
            # Preserve the legacy authoring boundary for optional source-mix delays.
            result = None
        elif types & {"integer", "number"} and "string" not in types and value is not None:
            if type(value) not in (str, int, float) or (isinstance(value, str) and not value.strip()):
                raise ValueError(f"{field}: enter an explicit finite number")
            try:
                if "integer" in types and "number" not in types:
                    exact = Decimal(str(value))
                    if not exact.is_finite() or exact != exact.to_integral_value():
                        raise ValueError()
                    result = int(exact)
                else:
                    result = float(str(value))
                    if not math.isfinite(result):
                        raise ValueError()
            except (ValueError, OverflowError, InvalidOperation):
                raise ValueError(f"{field}: enter an explicit finite {'integer' if 'integer' in types else 'number'}") from None
    errors = list(Draft202012Validator({**schema, "$defs": _SCHEMA["$defs"]}).iter_errors(result))
    if errors:
        raise ValueError(f"{field}: {errors[0].message}")
    return result


def native_intent(intent: dict) -> dict:
    step = _convert(intent, _SCHEMA["properties"]["steps"]["items"])
    if "location_id" in step and (str(step["location_id"]) not in _NATIVE["locations"] or step["location_id"] == 32):
        raise ValueError("location_id: expected OEM locationID, not UNKNOWN")
    if step["operation"] == "move":
        well = step["well"]
        valid = (type(well) is int and 0 <= well <= 95) or (isinstance(well, str) and (
            re.fullmatch(r"[A-Ha-h](?:1[0-2]|[1-9])", well.strip()) or
            (well.strip().isdecimal() and 0 <= int(well.strip()) <= 95)))
        if not valid:
            raise ValueError("well must be A1..H12 or canonical integer 0..95")
    if step["operation"] == "load_tip" and not re.fullmatch(r"[ABab](?:[1-9]|1[0-2])", step["well"]):
        raise ValueError("well must be A1..B12 (native load_tip)")
    obj = step.get("diagnostic", step)
    if "channels" in obj and len(set(obj["channels"])) != len(obj["channels"]):
        raise ValueError("channels must not contain duplicates")
    if step["operation"] in ("aspirate", "dispense", "mix"):
        if not 0 < step["volume_ul"] <= 1000:
            raise ValueError("volume_ul must be greater than 0 and <= 1000 (native command)")
        for key in ("speed", "aspirate_speed", "dispense_speed"):
            if key in step and step[key] <= 0:
                raise ValueError(f"{key} must be greater than 0 (native command)")
    return step


def expand_transfer(intent: dict) -> list[tuple[dict, int, str, str]]:
    t = WorkflowTransferIntent.model_validate(intent)
    if not t.source.wells or len(t.source.wells) != len(t.destination.wells):
        raise ValueError("Transfer requires nonempty equal-length ordered source/destination reference-well pairs; no broadcasting")
    expanded = []
    for pair, (source, destination) in enumerate(zip(t.source.wells, t.destination.wells)):
        for endpoint, well, side, operation in ((t.source, source, "source", "aspirate"), (t.destination, destination, "destination", "dispense")):
            steps = [
                {"operation": "move", "location_id": endpoint.location_id, "well": well, "position_flag": intent[f"{side}_position_flag"]},
                {"operation": "lower", "location_id": endpoint.location_id},
                {"operation": operation, "channels": t.channels, "volume_ul": t.volume_ul, "speed": intent[f"{operation}_speed"]},
                {"operation": "lift", "location_id": endpoint.location_id, "height_steps": intent[f"{side}_lift_height_steps"]},
            ]
            converted = [native_intent(s) for s in steps]
            station = _NATIVE["locations"][str(converted[0]["location_id"])]
            expanded.extend((s, pair, station, well) for s in converted)
    return expanded


def _actions(step: dict) -> list[tuple[str, dict]]:
    op = step["operation"]
    if op in ("move", "lower", "lift"):
        return [("pipette_position", step)]
    if op not in ("aspirate", "dispense", "mix"):
        return [("pipette_manual_physical", step)]
    result = []
    for _ in range(step.get("cycles", 1)):
        for liquid in (("aspirate", "dispense") if op == "mix" else (op,)):
            template = _NATIVE["document_template"]["stages"][0]["actions"][0 if liquid == "aspirate" else 1]
            params = deepcopy(template["params"])
            params.update(channels=step["channels"], volume_ul=step["volume_ul"], speed=step[f"{liquid}_speed"] if op == "mix" else step["speed"])
            result.append((template["kind"], params))
    return result


def preview(request: WorkflowPreviewRequest) -> WorkflowPreviewResponse:
    issues = []
    expanded = []
    if not request.protocol_id.strip():
        issues.append({"step_id": None, "message": "Provide a nonempty protocol ID"})
    if not request.draft.steps:
        issues.append({"step_id": None, "message": "Provide at least one explicit step"})
    for row in request.draft.steps:
        try:
            entries = expand_transfer(row.intent) if row.intent.get("operation") == "transfer" else [(native_intent(row.intent), None, None, None)]
            expanded.extend((row.step_id, *entry) for entry in entries)
        except (ValueError, TypeError, OverflowError) as exc:
            issues.append({"step_id": row.step_id, "message": str(exc)})
    if issues:
        return WorkflowPreviewResponse(document=None, actions=[], issues=issues)
    document = deepcopy(_NATIVE["document_template"])
    document["protocol_id"] = request.protocol_id
    actions = document["stages"][0]["actions"] = []
    visible = []
    for native_index, (step_id, step, pair, station, well) in enumerate(expanded):
        for kind, params in _actions(step):
            index = len(actions)
            action = deepcopy(_NATIVE["document_template"]["stages"][0]["actions"][0])
            action.update(action_id=f"manual-{native_index}-{index}", kind=kind, params=params, metadata={"manual_step": native_index})
            actions.append(action)
            visible.append(dict(index=index, step_id=step_id, pair_index=pair, kind=kind, params=deepcopy(params), label=step["operation"] if step["operation"] != "mix" else kind.removeprefix("pipette_"), station=station, well=well or (str(step["well"]) if "well" in step else None)))
    return WorkflowPreviewResponse(document=document, actions=visible, issues=[])
