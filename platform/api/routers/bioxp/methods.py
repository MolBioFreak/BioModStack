"""Source-free method facade over UserTemplate and the canonical protocol relay."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictInt, TypeAdapter, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from database import UserTemplate, UserTemplateRevision, get_session
from routers import user_templates
from services.user_template_revisions import exact_revision
from services.bioxp.protocol_models import (
    ProtocolSubmission, ProtocolControlRequest, ProtocolReviewRequest, ProtocolJob,
    ProtocolJobObservation, ProtocolControlResponse,
)
from services.bioxp.method_report import occurrence_outcomes, application_report, duration_report
from services.bioxp.method_recovery import resolve_occurrence, original_occurrences
from . import protocols
from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access

router = APIRouter(prefix="/methods")
Collection = Literal["library", "liquid-classes", "presets"]
SCHEMAS = {"library": "bms.bioxp-method.v1", "liquid-classes": "bms.bioxp-liquid-class.v1",
           "presets": "bms.bioxp-method-preset.v1"}
LEGACY = {"bms.bioxp-workflow-draft.v1", "bms.bioxp-workflow-draft.v2"}
Revision = Annotated[StrictInt, Field(ge=0)]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DraftCreate(Wire):
    method: dict[str, JsonValue]
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=500)


class DraftUpdate(DraftCreate):
    expected_base_revision: Revision


class DraftImport(DraftCreate):
    # Export identities are provenance only. Import always allocates a new head.
    id: str | None = None
    revision: Revision | None = None


class DraftRecord(Wire):
    id: str
    revision: Revision
    name: str
    description: str | None
    method: dict[str, JsonValue]


class Compilation(BaseModel):
    # Compiler additions remain lossless; this is the published shared envelope.
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    document: dict[str, JsonValue] | None
    issues: list[dict[str, JsonValue]]
    digest: str | None = None
    resolved: dict[str, JsonValue] = Field(default_factory=dict)
    dependencies: dict[str, JsonValue] = Field(default_factory=dict)
    water_substitutions: list[dict[str, JsonValue]] = Field(default_factory=list)
    provenance: list[dict[str, JsonValue]] = Field(default_factory=list)
    simulation: dict[str, JsonValue] = Field(default_factory=dict)


class MethodRunResponse(ProtocolJob):
    method_snapshot: dict[str, JsonValue]


class Duplicate(Wire):
    name: str = Field(min_length=1, max_length=255)
    revision: Revision | None = None


class CompileRequest(Wire):
    method: dict[str, JsonValue]
    bindings: dict[str, JsonValue] = Field(default_factory=dict)
    dependencies: dict[str, JsonValue] = Field(default_factory=dict)
    initial_state: dict[str, JsonValue] | None = None


class RunRequest(Wire):
    bindings: dict[str, JsonValue] = Field(default_factory=dict)
    dependencies: dict[str, JsonValue] = Field(default_factory=dict)
    initial_state: dict[str, JsonValue] | None = None
    idempotency_key: str = Field(min_length=1, max_length=256)
    expected_generation: Annotated[StrictInt, Field(ge=0)]
    acknowledge_live: StrictBool
    recovery: dict[str, JsonValue] | None = None

    @field_validator("idempotency_key")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("idempotency_key must not be blank")
        return value


class SavedRun(RunRequest):
    revision: Revision


class QuickRun(RunRequest):
    method: dict[str, JsonValue]


class Recovery(Wire):
    occurrence: dict[str, JsonValue] | None = None
    initial_state: dict[str, JsonValue] | None = None


def compile_method(request):
    # Lazy import keeps raw Save/Open independent of compiler availability.
    from bioxp_method_compiler import compile_method as compile_owned
    return compile_owned(request)


@router.get("/catalog")
def catalog():
    from bioxp_method_model import method_catalog
    return method_catalog()


@router.get("/schema")
def schema():
    from bioxp_method_model import method_schema
    return {"method": method_schema(), "requests": {
        **{cls.__name__: cls.model_json_schema() for cls in (
            DraftCreate, DraftUpdate, DraftImport, CompileRequest, SavedRun, QuickRun, Recovery, ProtocolReviewRequest)},
        "ProtocolControlRequest": TypeAdapter(ProtocolControlRequest).json_schema(),
    }, "results": {cls.__name__: cls.model_json_schema() for cls in (
        DraftRecord, Compilation, MethodRunResponse, ProtocolJobObservation, ProtocolControlResponse)},
        "openapi": "/openapi.json"}


@router.get("/examples")
def examples():
    from bioxp_method_model import method_examples
    return method_examples()


@router.post("/check", response_model=Compilation, response_model_exclude_unset=True)
@router.post("/compile", response_model=Compilation, response_model_exclude_unset=True)
def compile_draft(request: CompileRequest):
    return compile_method(deepcopy(request.model_dump(mode="json", exclude_unset=True)))


@router.post("/migrate")
def migrate_draft(request: DraftCreate):
    from bioxp_method_model import migrate_legacy
    return migrate_legacy(deepcopy(request.method))


@router.get("/liquid-classes/starters")
def starter_classes():
    from bioxp_method_liquids import starter_entries
    return starter_entries()


@router.get("/liquid-classes/source")
def liquid_source():
    from bioxp_method_liquids import source_catalog_text
    return Response(source_catalog_text(), media_type="application/json")


async def _template(session, collection, template_id):
    template = await user_templates.get_user_template(template_id, session)
    schemas = {SCHEMAS[collection]} | (LEGACY if collection == "library" else set())
    if template.mode != "bioxp_workflow" or template.params.get("schema") not in schemas:
        raise HTTPException(404, "Template not found in this collection")
    return template


def _check_schema(collection, method):
    if method.get("schema") != SCHEMAS[collection]:
        raise HTTPException(422, {"code": "draft_schema", "category": "representation",
                                  "path": "/method/schema", "message": f"Expected {SCHEMAS[collection]}"})


async def _create(collection, data, session):
    _check_schema(collection, data.method)
    name = data.name if data.name is not None else data.method.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 255:
        raise HTTPException(422, "An explicit draft name of 1–255 characters is required")
    template = await user_templates.create_user_template(user_templates.UserTemplateCreate(
        name=name, description=data.description, mode="bioxp_workflow",
        model_id=None, base_template_id=None, params=deepcopy(data.method)), session)
    return await exact_revision(session, template, 1)


async def _submit(method, data, response, runtime, *, saved=None):
    # Freeze before any network await. Compiler failures never emit a prefix.
    request = deepcopy({"method": method, "bindings": data.bindings, "dependencies": data.dependencies})
    if "initial_state" in data.model_fields_set:
        request["initial_state"] = deepcopy(data.initial_state)
    compiled = await run_in_threadpool(compile_method, deepcopy(request))
    if compiled.get("document") is None:
        raise HTTPException(422, {"code": "method_not_representable", "category": "representation",
                                  "path": "/method", "message": "No complete native document was emitted",
                                  "compilation": compiled, "delivery": "not_submitted"})
    snapshot = deepcopy({"schema": "bms.bioxp-method-run.v1", **request,
                         "saved": saved, "compilation": compiled})
    if "recovery" in data.model_fields_set:
        snapshot["recovery"] = deepcopy(data.recovery)
    document = deepcopy(compiled["document"])
    document.setdefault("metadata", {})["bms_method_run"] = snapshot
    submission = ProtocolSubmission(
        expected_connection_generation=data.expected_generation, source_type="native",
        document=document, dry_run=False, idempotency_key=data.idempotency_key,
        live_execution_ack=data.acknowledge_live,
    )
    job_id = "protocol-live-" + sha256(data.idempotency_key.strip().encode("utf-8")).hexdigest()
    try:
        job = await protocols.submit_bioxp_protocol(submission, response, runtime)
        if job.job_id != job_id:
            raise HTTPException(502, "BioXP returned a different canonical submission identity")
    except HTTPException as exc:
        # HTTP code does not prove lack of admission. Preserve upstream evidence
        # and the original identity even when response parsing/readback failed.
        not_submitted = getattr(exc, "bioxp_dispatch_started", None) is False or (
            isinstance(exc.detail, dict) and exc.detail.get("dispatch_state") == "not_dispatched")
        delivery = "not_submitted" if not_submitted else "uncertain"
        raise HTTPException(exc.status_code, {
            "code": "method_submission_error", "category": "delivery",
            "message": "This attempt was not dispatched" if not_submitted else "Observe the original job; do not automatically resubmit",
            "path": "/runs", "native_reason": exc.detail, "delivery": delivery,
            "job_id": job_id, "idempotency_key": data.idempotency_key,
            "expected_generation": data.expected_generation, "method_snapshot": snapshot,
        }, headers=exc.headers) from exc
    return {**job.model_dump(mode="json", exclude_unset=True), "method_snapshot": snapshot}


@router.post("/quick-runs", response_model=MethodRunResponse, response_model_exclude_unset=True,
             dependencies=[Depends(require_bioxp_mutation_access)])
async def quick_run(data: QuickRun, response: Response, runtime=Depends(get_bioxp_runtime)):
    return await _submit(data.method, data, response, runtime)


@router.get("/runs")
async def runs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=99),
               search: str | None = None,
               expected_connection_generation: int | None = Query(None, ge=0),
               runtime=Depends(get_bioxp_runtime)):
    native_limit = 100 if search else min(100, offset + limit)
    page = await protocols.list_protocol_jobs(native_limit, expected_connection_generation, runtime)
    rows = page.model_dump(mode="json", exclude_unset=True)["rows"]
    if search:
        rows = [row for row in rows if search.casefold() in json.dumps(row, ensure_ascii=False).casefold()]
    return {"rows": rows[offset:offset + limit],
            "window": {"native_limit": native_limit, "offset": offset, "limit": limit,
                       "scope": "latest_native_jobs", "complete_history": False}}


@router.get("/runs/{job_id}", response_model=ProtocolJob | ProtocolJobObservation, response_model_exclude_unset=True)
async def run(job_id: str, expected_connection_generation: int | None = Query(None, ge=0),
              observation: bool = False, runtime=Depends(get_bioxp_runtime)):
    return await protocols.get_protocol_job(job_id, expected_connection_generation, observation, runtime)


@router.post("/runs/{job_id}/control", response_model=ProtocolControlResponse, response_model_exclude_unset=True,
             dependencies=[Depends(require_bioxp_mutation_access)])
async def control(job_id: str, data: ProtocolControlRequest, runtime=Depends(get_bioxp_runtime)):
    return await protocols.control_protocol_job(job_id, data, runtime)


@router.post("/runs/{job_id}/review", response_model=ProtocolJob, response_model_exclude_unset=True,
             dependencies=[Depends(require_bioxp_mutation_access)])
async def review(job_id: str, data: ProtocolReviewRequest, runtime=Depends(get_bioxp_runtime)):
    return await protocols.review_protocol_job(job_id, data, runtime)


def _snapshot(job):
    from services.bioxp.method_snapshot import method_snapshot
    return method_snapshot(job.protocol.document)


def _evidence(job, snapshot):
    from services.bioxp.method_snapshot import snapshot_evidence
    return snapshot_evidence(job.protocol.document, snapshot)


@router.get("/runs/{job_id}/report")
async def report(job_id: str, expected_connection_generation: int | None = Query(None, ge=0),
                 runtime=Depends(get_bioxp_runtime)):
    job = await protocols.get_protocol_job(job_id, expected_connection_generation, False, runtime)
    snapshot = _snapshot(job)
    state = job.execution.runtime_state
    return {"job_id": job.job_id, "status": job.status, "command": job.command,
            "method_snapshot": snapshot, "snapshot_available": snapshot is not None,
            "snapshot_evidence": _evidence(job, snapshot),
            "created_at": job.created_at, "updated_at": job.updated_at,
            "duration": duration_report(state),
            "workflow": state.workflow, "stage_states": state.stage_states,
            "occurrences": occurrence_outcomes(snapshot, state),
            "recovery": snapshot.get("recovery") if snapshot else None,
            "action_results": state.action_results, "events": state.events,
            "reported_applied": application_report(state),
            "operator": job.operator}


@router.post("/runs/{job_id}/clone")
async def clone_run(job_id: str, expected_connection_generation: int | None = Query(None, ge=0),
                    runtime=Depends(get_bioxp_runtime)):
    job = await protocols.get_protocol_job(job_id, expected_connection_generation, False, runtime)
    snapshot = _snapshot(job)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("method"), dict):
        raise HTTPException(422, {"code": "lossless_reconstruction_unavailable", "category": "representation",
                                  "path": "/protocol/document/metadata/bms_method_run",
                                  "message": "Original raw method snapshot is unavailable"})
    result = {key: deepcopy(snapshot[key]) for key in ("method", "bindings", "dependencies", "initial_state") if key in snapshot}
    return {**result, "original_job_id": job_id, "submitted": False,
            "snapshot_evidence": _evidence(job, snapshot)}


@router.post("/runs/{job_id}/recovery-draft")
async def recovery(job_id: str, data: Recovery,
                   expected_connection_generation: int | None = Query(None, ge=0),
                   runtime=Depends(get_bioxp_runtime)):
    job = await protocols.get_protocol_job(job_id, expected_connection_generation, False, runtime)
    snapshot = _snapshot(job)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("method"), dict):
        raise HTTPException(422, {"code": "lossless_reconstruction_unavailable", "category": "representation",
                                  "path": "/protocol/document/metadata/bms_method_run",
                                  "message": "Original raw method snapshot is unavailable"})
    assumptions = ({"initial_state": deepcopy(data.initial_state)} if "initial_state" in data.model_fields_set
                   else {"initial_state": deepcopy(snapshot["initial_state"])} if "initial_state" in snapshot else {})
    return {"method": deepcopy(snapshot["method"]), "bindings": deepcopy(snapshot.get("bindings", {})),
            "dependencies": deepcopy(snapshot.get("dependencies", {})),
            **assumptions,
            "recovery": {"original_job_id": job_id, "occurrence": data.occurrence,
                         "occurrence_resolution": resolve_occurrence(snapshot, data.occurrence),
                         "original_assumptions": {"initial_state": deepcopy(snapshot["initial_state"])} if "initial_state" in snapshot else {},
                         "snapshot_evidence": _evidence(job, snapshot),
                         "assumptions_overridden": "initial_state" in data.model_fields_set,
                         "included_occurrences": original_occurrences(snapshot),
                         "automatic_setup": [], "excluded_actions": [], "submitted": False,
                         "strategy": "whole_original_draft_for_explicit_editing"},
            "issues": [{"code": "recovery_requires_authoring", "category": "advisory", "path": "/method",
                        "message": "Original actions retained, not replayed. Author intended recovery actions and starting assumptions; partial operations are not self-contained."}]}


# Static run/discovery routes precede collection parameter routes.
@router.get("/{collection}", response_model=list[DraftRecord])
async def library(collection: Collection, limit: int = Query(100, ge=1, le=500),
                  offset: int = Query(0, ge=0), search: str | None = None,
                  session: AsyncSession = Depends(get_session)):
    schemas = [SCHEMAS[collection]] + (list(LEGACY) if collection == "library" else [])
    query = select(UserTemplate).where(UserTemplate.mode == "bioxp_workflow",
        UserTemplate.params["schema"].as_string().in_(schemas))
    if search:
        query = query.where(UserTemplate.name.ilike(f"%{search}%") | UserTemplate.description.ilike(f"%{search}%"))
    rows = (await session.scalars(query.order_by(UserTemplate.created_at.desc(), UserTemplate.id).offset(offset).limit(limit))).all()
    return [await exact_revision(session, row) for row in rows]


@router.post("/{collection}", status_code=201, response_model=DraftRecord)
async def create(collection: Collection, data: DraftCreate, session: AsyncSession = Depends(get_session)):
    return await _create(collection, data, session)


@router.post("/{collection}/import", status_code=201, response_model=DraftRecord)
async def import_draft(collection: Collection, data: DraftImport, session: AsyncSession = Depends(get_session)):
    # Import never overwrites an identity/name implicitly.
    return await _create(collection, data, session)


@router.get("/{collection}/{template_id}", response_model=DraftRecord)
async def get(collection: Collection, template_id: str, session: AsyncSession = Depends(get_session)):
    return await exact_revision(session, await _template(session, collection, template_id))


@router.put("/{collection}/{template_id}", response_model=DraftRecord)
async def update(collection: Collection, template_id: str, data: DraftUpdate,
                 session: AsyncSession = Depends(get_session)):
    await _template(session, collection, template_id)
    _check_schema(collection, data.method)
    fields = {"params": deepcopy(data.method), "expected_base_revision": data.expected_base_revision}
    for key in ("name", "description"):
        if key in data.model_fields_set:
            fields[key] = getattr(data, key)
    template = await user_templates.update_user_template(template_id, user_templates.UserTemplateUpdate(**fields), session)
    return await exact_revision(session, template, data.expected_base_revision + 1)


@router.get("/{collection}/{template_id}/revisions", response_model=list[DraftRecord])
async def revisions(collection: Collection, template_id: str, limit: int = Query(100, ge=1, le=500),
                    offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_session)):
    template = await _template(session, collection, template_id)
    rows = (await session.scalars(select(UserTemplateRevision).where(UserTemplateRevision.template_id == template_id)
        .order_by(UserTemplateRevision.revision.desc()).offset(offset).limit(limit))).all()
    if not rows and offset == 0:
        current = await exact_revision(session, template)
        return [current] if current["revision"] == 0 else []
    return [{**deepcopy(row.snapshot), "revision": row.revision} for row in rows]


@router.get("/{collection}/{template_id}/revisions/{revision}", response_model=DraftRecord)
async def revision(collection: Collection, template_id: str, revision: int,
                   session: AsyncSession = Depends(get_session)):
    return await exact_revision(session, await _template(session, collection, template_id), revision)


@router.get("/{collection}/{template_id}/export", response_model=DraftRecord)
async def export(collection: Collection, template_id: str, revision: int | None = Query(None, ge=0),
                 session: AsyncSession = Depends(get_session)):
    return await exact_revision(session, await _template(session, collection, template_id), revision)


def raw_diff(before, after, path=""):
    """JSON Pointer edits, preserving absent/null/type distinctions; lists atomic."""
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in before:
                changes.append({"op": "add", "path": pointer, "value": after[key]})
            elif key not in after:
                changes.append({"op": "remove", "path": pointer, "before": before[key]})
            else:
                changes.extend(raw_diff(before[key], after[key], pointer))
        return changes
    if json.dumps(before, sort_keys=True, ensure_ascii=False) != json.dumps(after, sort_keys=True, ensure_ascii=False):
        return [{"op": "replace", "path": path, "before": before, "value": after}]
    return []


@router.get("/{collection}/{template_id}/diff")
async def diff(collection: Collection, template_id: str,
               from_revision: int = Query(..., ge=0), to_revision: int = Query(..., ge=0),
               session: AsyncSession = Depends(get_session)):
    template = await _template(session, collection, template_id)
    before = await exact_revision(session, template, from_revision)
    after = await exact_revision(session, template, to_revision)
    return {"from_revision": from_revision, "to_revision": to_revision, "kind": "raw",
            "changes": raw_diff(before["method"], after["method"])}


@router.post("/{collection}/{template_id}/duplicate", status_code=201, response_model=DraftRecord)
async def duplicate(collection: Collection, template_id: str, data: Duplicate,
                    session: AsyncSession = Depends(get_session)):
    original = await exact_revision(session, await _template(session, collection, template_id), data.revision)
    return await _create(collection, DraftCreate(method=original["method"], name=data.name,
                                                description=original["description"]), session)


@router.post("/library/{template_id}/runs", response_model=MethodRunResponse, response_model_exclude_unset=True,
             dependencies=[Depends(require_bioxp_mutation_access)])
async def saved_run(template_id: str, data: SavedRun, response: Response,
                    session: AsyncSession = Depends(get_session), runtime=Depends(get_bioxp_runtime)):
    saved = await exact_revision(session, await _template(session, "library", template_id), data.revision)
    return await _submit(saved["method"], data, response, runtime,
                         saved={"id": saved["id"], "revision": saved["revision"], "name": saved["name"]})
