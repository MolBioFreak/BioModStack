"""Native MD preview, provisioning and Job launch."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, NoReturn
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import BackgroundTasks, HTTPException
from services.md.chemistry_catalog import ChemistryCatalogError, ChemistryProfileSelectionError, get_chemistry_catalog
from schemas import JobCreate
from services.global_experiments.launch_contexts import LaunchContextError
from services.md.launch_contract import MDLaunchError, V2_SERVER_RESOLVED_CHEMISTRY_FIELDS, native_input_identity, normalize_md_job_spec
from services.md.starting_structures import MdGuidedNativeInput, MdLaunchIntent, MdLaunchRequest, MdLaunchSourceIdentity, MdNativeLaunchIntent, MdNativeLaunchPreview, StartingStructureError, StartingStructureSourceRef, compile_native_job_intent, normalize_typed_launch_settings, read_resolved_structure, resolve_source, resolved_runtime_path
from services.workflow_request_types import NativeWorkflowDependencyRequest
from experiment_database import experiment_session_factory
from services.md.feature_gate import require_molecular_dynamics_feature


def _catalog_view_or_503():
    try:
        return get_chemistry_catalog().view()
    except ChemistryCatalogError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MD_LAUNCH_SERVICE_UNAVAILABLE",
                "message": "The molecular-dynamics launch service is temporarily unavailable.",
            },
        ) from exc


def _starting_structure_http_error(exc: StartingStructureError) -> NoReturn:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    ) from exc


def _launch_context_source_refs(
    normalized_request: dict[str, Any], workflow: dict[str, Any]
) -> list[StartingStructureSourceRef]:
    scheduler = workflow.get("scheduler")
    scheduler_params = scheduler.get("params") if isinstance(scheduler, dict) else None
    locations = [
        scheduler_params,
        scheduler_params.get("intent") if isinstance(scheduler_params, dict) else None,
        normalized_request,
        normalized_request.get("intent"),
        workflow.get("parameters"),
    ]
    refs: list[StartingStructureSourceRef] = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        raw_ref = location.get("source_ref")
        if raw_ref is not None:
            try:
                refs.append(StartingStructureSourceRef.model_validate(raw_ref))
            except ValueError as exc:
                raise LaunchContextError(
                    "launch_context_preparation_mismatch",
                    "Prepared MD source authority is invalid.",
                    status_code=409,
                ) from exc
        raw_design_id = location.get("source_design_id")
        if isinstance(raw_design_id, str) and raw_design_id:
            refs.append(StartingStructureSourceRef(kind="design", id=raw_design_id))
    return refs


async def _validate_preview_launch_context(intent: MdLaunchIntent | MdNativeLaunchIntent) -> None:
    if intent.launch_context_id is None:
        return
    from experiment_models import (
        ExperimentAggregateHead,
        ExperimentRevision,
        ExperimentWorkflowPreparation,
    )
    from services.global_experiments.launch_contexts import resolve_launch_context

    async with experiment_session_factory() as experiment_session:
        context = await resolve_launch_context(experiment_session, intent.launch_context_id)
        preparation = await experiment_session.get(
            ExperimentWorkflowPreparation, context.preparation_id
        )
        workflow = await experiment_session.get(ExperimentAggregateHead, context.workflow_id)
        revision = await experiment_session.get(
            ExperimentRevision, context.workflow_revision_id
        )
        domain = await experiment_session.get(
            ExperimentAggregateHead, context.domain_experiment_id
        )
        domain_revision = await experiment_session.get(
            ExperimentRevision,
            domain.current_revision_id if domain is not None else "",
        )
        if (
            preparation is None
            or workflow is None
            or revision is None
            or preparation.resource_id != context.preparation_id
            or preparation.workflow_revision_id != context.workflow_revision_id
            or preparation.workspace_id != context.project_id
        ):
            raise LaunchContextError(
                "launch_context_preparation_mismatch",
                "Prepared MD authority does not match the launch context.",
                status_code=409,
            )
        if (
            workflow.current_revision_id != context.workflow_revision_id
            or revision.subject_id != context.workflow_id
        ):
            raise LaunchContextError(
                "launch_context_revision_mismatch",
                "Prepared MD authority is not the current Workflow Revision.",
                status_code=409,
            )
        try:
            workflow_payload = json.loads(revision.canonical_payload)
            normalized_request = json.loads(preparation.normalized_request_json)
            domain_payload = (
                json.loads(domain_revision.canonical_payload)
                if domain_revision is not None
                else None
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LaunchContextError(
                "launch_context_preparation_mismatch",
                "Prepared MD authority is not valid canonical JSON.",
                status_code=409,
            ) from exc
        if not all(
            isinstance(value, dict)
            for value in (workflow_payload, normalized_request, domain_payload)
        ):
            raise LaunchContextError(
                "launch_context_preparation_mismatch",
                "Prepared MD authority has an invalid document shape.",
                status_code=409,
            )
        normalized_digest = hashlib.sha256(
            preparation.normalized_request_json.encode("utf-8")
        ).hexdigest()
        scheduler = workflow_payload.get("scheduler")
        if (
            normalized_digest != preparation.normalized_request_sha256
            or normalized_digest != context.normalized_request_sha256
            or normalized_request.get("workflow_revision_id") != context.workflow_revision_id
            or normalized_request.get("workflow") != workflow_payload
            or not isinstance(scheduler, dict)
            or preparation.scheduler_payload_json
            != json.dumps(
                scheduler,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        ):
            raise LaunchContextError(
                "launch_context_preparation_mismatch",
                "Prepared MD request identity is stale or inconsistent.",
                status_code=409,
            )
        if (
            domain_payload.get("domain_kind") != "protein_in_silico"
            or scheduler.get("model_id") != "molecular_dynamics"
            or scheduler.get("mode") != "simulate"
        ):
            raise LaunchContextError(
                "launch_context_scope_mismatch",
                "Launch context is not scoped to the current Molecular Dynamics workflow.",
                status_code=409,
            )
        if isinstance(intent, MdNativeLaunchIntent):
            authority = intent.model_dump(mode="json", exclude={
                "name", "launch_context_id", "execution_target_id", "execution_policy",
            })
            if scheduler.get("params") != authority:
                raise LaunchContextError(
                    "launch_context_preparation_mismatch",
                    "Native MD intent differs from the prepared Project request.", status_code=409,
                )
            return
        bound_refs = _launch_context_source_refs(normalized_request, workflow_payload)
        if intent.source_ref.kind not in {"design", "candidate"}:
            raise LaunchContextError(
                "launch_context_source_mismatch",
                "Project Molecular Dynamics launch intent must select one prepared source.",
                status_code=409,
            )
        if len(bound_refs) != 1:
            raise LaunchContextError(
                "launch_context_preparation_mismatch",
                "Prepared Project MD authority must bind exactly one source reference.",
                status_code=409,
            )
        bound_ref = bound_refs[0]
        if intent.source_ref.kind == "candidate" or bound_ref.kind == "candidate":
            # Native documents carry artifact/state/MODEL identity. Compare the
            # existing prepared source, not a lossy Design-id projection.
            if bound_ref.model_dump(mode="json") != intent.source_ref.model_dump(mode="json"):
                raise LaunchContextError(
                    "launch_context_source_mismatch",
                    "Selected native document does not match the prepared Project source.",
                    status_code=409,
                )
            return
        if bound_ref.kind != "design" or bound_ref.id != intent.source_ref.id:
            raise LaunchContextError(
                "launch_context_source_mismatch",
                "Selected Design does not match the prepared Project source.",
                status_code=409,
            )


def _typed_launch_chemistry(intent: MdLaunchIntent) -> tuple[Any, Any]:
    """Read the same chemistry catalog generation for both preview consumers."""
    try:
        catalog = get_chemistry_catalog()
        view = catalog.view()
    except ChemistryCatalogError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MD_LAUNCH_SERVICE_UNAVAILABLE",
                "message": "The molecular-dynamics chemistry catalog is unavailable.",
            },
        ) from exc
    profile = view.get_profile(intent.chemistry_profile_id)
    return view, profile


async def normalize_md_provision_request(
    intent: MdLaunchIntent | MdNativeLaunchIntent, session: AsyncSession,
) -> NativeWorkflowDependencyRequest:
    """Dependency-only projection of the actual typed starting-structure intent.

    The source remains a declared binding, never a synthetic resolved structure.
    /launch-preview and /launch still resolve and verify the exact source bytes.
    """
    require_molecular_dynamics_feature("molecular_dynamics")
    if isinstance(intent, MdNativeLaunchIntent):
        profile = None
        if isinstance(intent.input, MdGuidedNativeInput):
            view = _catalog_view_or_503()
            profile = view.get_profile(intent.input.chemistry_profile_id)
        config = compile_native_job_intent(intent, profile=profile)
        bindings = ({
            "role": "starting_structure", "source_ref": intent.input.source_ref.model_dump(mode="json"),
            "expected_sha256": intent.input.expected_source_sha256, "required": True, "state": "declared",
        },) if isinstance(intent.input, MdGuidedNativeInput) else tuple(
            {"role": field, "path": path, "required": True, "state": "declared"}
            for field, path in config["input"].items()
        )
        return NativeWorkflowDependencyRequest(
            model_id="molecular_dynamics", mode="simulate", requested_params=intent.model_dump(mode="json"),
            effective_params={"md_config": config},
            entrypoint="workflows/experimental/molecular_dynamics/orchestrator.nf", input_bindings=bindings,
        )
    try:
        await _validate_preview_launch_context(intent)
        view, profile = _typed_launch_chemistry(intent)
        effective, warnings, blockers = normalize_typed_launch_settings(
            intent=intent, profile=profile, current_catalog_digest=view.catalog_digest,
        )
        if blockers:
            raise StartingStructureError(
                "MD_LAUNCH_BLOCKED",
                "The molecular-dynamics launch controls contain blockers.",
                status_code=409,
            )
        config = effective.model_dump(mode="json")
        config["chemistry"] = {
            "profile_id": intent.chemistry_profile_id,
            "profile_sha256": intent.chemistry_profile_sha256,
            "catalog_digest": view.catalog_digest,
            "requested_scope": profile["scientific_validation"]["scope"]["launch_scope"],
        }
        return NativeWorkflowDependencyRequest(
            model_id="molecular_dynamics", mode="simulate",
            requested_params=intent.model_dump(mode="json"),
            effective_params={"md_config": config},
            entrypoint="workflows/experimental/molecular_dynamics/orchestrator.nf",
            input_bindings=({
                "role": "starting_structure",
                "source_ref": intent.source_ref.model_dump(mode="json"),
                "expected_sha256": intent.expected_source_sha256,
                "required": True,
                "state": "declared",
            },),
        )
    except (MDLaunchError, ChemistryProfileSelectionError) as exc:
        raise HTTPException(status_code=getattr(exc, "status_code", 422), detail={"code": exc.code, "message": str(exc)}) from exc
    except StartingStructureError as exc:
        _starting_structure_http_error(exc)
    except LaunchContextError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


async def _compile_native_preview(intent: MdNativeLaunchIntent, session: AsyncSession):
    from component_runtime import canonical_bytes, digest, SourceIdentity
    from services.job_requests import _resolve_md_input_path_for_runtime

    require_molecular_dynamics_feature("molecular_dynamics")
    resolved = None
    source = None
    profile = None
    catalog = None
    view = None
    source_token = "bms-md-starting-structure:selected"
    await _validate_preview_launch_context(intent)
    try:
        if isinstance(intent.input, MdGuidedNativeInput):
            catalog = get_chemistry_catalog()
            view = catalog.view()
            profile = view.get_profile(intent.input.chemistry_profile_id)
            resolved = await resolve_source(intent.input.source_ref, session)
            structure = read_resolved_structure(resolved)
            if structure.sha256 != intent.input.expected_source_sha256:
                raise StartingStructureError("MD_STARTING_STRUCTURE_CHANGED", "The selected source bytes changed.", status_code=409)
            source = MdLaunchSourceIdentity(
                source_ref=resolved.source_ref, label=resolved.label, format=structure.format,
                size_bytes=structure.size_bytes, sha256=structure.sha256, pdb_id=resolved.pdb_id,
                producer_job_id=resolved.producer_job_id, design_id=resolved.design_id,
            )

        def resolver(value: str) -> str:
            if resolved is not None and value == source_token:
                return resolved_runtime_path(resolved)
            if resolved is not None and value == "bms-md-starting-structure:original" and resolved.original_runtime_path:
                return resolved.original_runtime_path
            return _resolve_md_input_path_for_runtime(value)

        spec = compile_native_job_intent(intent, profile=profile, source_token=source_token)
        normalized = normalize_md_job_spec(
            params={"md_job_spec": spec}, job_id="assigned-by-server", resolve_runtime_path=resolver,
            chemistry_catalog=catalog, chemistry_view=view,
        )
        identity = native_input_identity(normalized)
        if resolved is not None and resolved.original_runtime_path:
            assert resolved.source_provenance is not None
            identity["original_structure"] = resolved.source_provenance["original_sha256"]
        if source is not None:
            normalized["input"]["structure"] = source_token
        for field in V2_SERVER_RESOLVED_CHEMISTRY_FIELDS:
            normalized.get("chemistry", {}).pop(field, None)
        preimage = {
            "effective_request": normalized, "input_identity": identity,
            "source": source.model_dump(mode="json") if source else None,
            "execution_target_id": intent.execution_target_id,
            "execution_policy": intent.execution_policy.model_dump(mode="json"),
        }
        preview = MdNativeLaunchPreview(
            **preimage, preview_digest=hashlib.sha256(canonical_bytes({**preimage, **({"return_to": intent.return_to} if intent.return_to is not None else {})})).hexdigest(),
        )
        if intent.execution_target_id:
            from services.nextflow import build_selected_execution_plan
            from services.remote_execution.bundle import current_source_identity, RemoteBundleError
            try:
                source_identity = SourceIdentity(*current_source_identity())
            except RemoteBundleError as exc:
                raise StartingStructureError("MD_REMOTE_SOURCE_UNAVAILABLE", "Committed BMS source identity is unavailable.", status_code=503) from exc
            plan = build_selected_execution_plan(
                model_id="molecular_dynamics", mode="simulate",
                entrypoint="workflows/experimental/molecular_dynamics/orchestrator.nf",
                requested=intent.model_dump(mode="json"), effective={"md_config": normalized},
                metadata_settings={"md_config": normalized},
                native_parameters={"input_bindings": normalized["input"]},
                source_identity=source_identity,
            )
            preview.execution_plan = plan.to_dict()
            preview.preview_digest = digest({"native_preview_digest": preview.preview_digest, "execution_plan": preview.execution_plan})
            if not plan.complete:
                raise StartingStructureError("MD_REMOTE_PLAN_UNSUPPORTED", "Selected shared MD execution plan is incomplete: " + "; ".join(row.reason for row in plan.blockers), status_code=422)
        return preview, resolved, resolver
    except Exception as exc:
        if resolved is not None:
            resolved.close()
        if isinstance(exc, ValueError) and not isinstance(exc, (MDLaunchError, StartingStructureError, ChemistryProfileSelectionError)):
            raise MDLaunchError("MD_JOB_CONTRACT_INVALID", "The native MD document is invalid.", status_code=422) from exc
        raise


def _native_md_job_request(request, preview, resolved, *, md_input_resolver=None):
    """One canonical request builder for standalone and retained round MD."""
    provenance: dict[str, Any] = {}
    if resolved is not None:
        provenance.update(copy.deepcopy(resolved.source_provenance or {}))
        assert preview.source is not None
        provenance.update(source_ref=resolved.source_ref.model_dump(mode="json"), source_sha256=preview.source.sha256)
    provenance["native_input_identity"] = preview.input_identity
    params = {"md_job_spec": preview.effective_request, "md_source_provenance": provenance,
              "intent": request.intent.model_dump(mode="json")}
    if resolved is not None and resolved.design_id is not None:
        params.update(
            source_design_id=resolved.design_id, lineage_root_job_id=provenance.get("lineage_root_job_id"),
            selection_source_job_id=resolved.producer_job_id, selection_source_type="design",
            source_stage_job_id=resolved.producer_job_id, source_stage_family=provenance.get("source_stage_family"),
            source_stage_mode=provenance.get("source_stage_mode"), source_selection_count=1,
        )
    job_data = JobCreate(
        name=request.intent.name, model_id="molecular_dynamics", mode="simulate", parent_job_id=None,
        params=params, launch_context_id=request.intent.launch_context_id,
        execution_target_id=request.intent.execution_target_id,
        execution_policy=request.intent.execution_policy,
    )
    if md_input_resolver is not None:
        from services.job_requests import normalize_job_request
        job_data = normalize_job_request(job_data, md_input_resolver=md_input_resolver)
    return job_data


async def _launch_native_md_job(request: MdLaunchRequest, session: AsyncSession, *, round_step=None,
                                destination_launch_context_id=None, experiment_session=None):
    from services.job_submission import submit_job
    from services.job_submission import TypedMdProjectLaunch
    from services.job_requests import ApprovedExecutionPlan
    from component_runtime import canonical_bytes

    assert isinstance(request.intent, MdNativeLaunchIntent)
    preview, resolved, resolver = await _compile_native_preview(request.intent, session)
    try:
        if preview.preview_digest != request.preview_digest:
            raise StartingStructureError("MD_LAUNCH_PREVIEW_STALE", "The native launch preview is stale.", status_code=409)
        job_data = _native_md_job_request(request, preview, resolved,
            md_input_resolver=resolver if round_step is not None else None)
        params = job_data.params
        if round_step is not None:
            job_data.binder_round_step = round_step
        kwargs = {"_md_output_creation": {}, "_md_input_resolver": resolver}
        if request.intent.execution_target_id:
            kwargs["_approved_execution_plan"] = ApprovedExecutionPlan(
                canonical_bytes(job_data.model_dump(mode="json")),
                canonical_bytes({"approval_digest": preview.preview_digest, "plan": preview.execution_plan,
                                 "admissible": True, "deferred_preparation": [], "blockers": []}),
            )
        if destination_launch_context_id is not None and round_step is not None:
            # Native preview/source verification precedes the ordinary Project
            # child handoff. The destination is never consumed as MD authority.
            from services.global_experiments.launch_contexts import prepare_child_launch_contexts, resolve_launch_context_for_display
            from services.job_submission import _create_prepared_child_batch, _reserve_selected_run_group
            prepared = await prepare_child_launch_contexts(experiment_session,
                destination_launch_context_id=destination_launch_context_id,
                job_requests=[job_data],
                native_entrypoints=["workflows/experimental/molecular_dynamics/orchestrator.nf"],
                idempotency_key=round_step.step_id, core_session=session, md_input_resolver=resolver)
            bound = JobCreate.model_validate(prepared['children'][0]['job_request'])
            destination = await resolve_launch_context_for_display(experiment_session, destination_launch_context_id)
            await _reserve_selected_run_group(experiment_session, group_id=prepared['run_group_id'],
                                              domain_id=destination.domain_experiment_id)
            if request.intent.execution_target_id:
                kwargs['_approved_execution_plan'] = ApprovedExecutionPlan(
                    canonical_bytes(bound.model_dump(mode='json')),
                    kwargs['_approved_execution_plan'].preview_json)
            return (await _create_prepared_child_batch([bound], BackgroundTasks(), session,
                experiment_session, native_job_kwargs=kwargs))[0]
        if request.intent.launch_context_id is not None:
            adapter = TypedMdProjectLaunch(
                request_schema_version=request.schema_version,
                intent=request.intent.model_dump(mode="json"), preview=preview.model_dump(mode="json"),
                preview_digest=request.preview_digest, md_job_spec=copy.deepcopy(preview.effective_request),
                source_token="bms-md-starting-structure:selected",
                source_params=copy.deepcopy({key: value for key, value in params.items() if key != "md_job_spec"}),
            )
            async with experiment_session_factory() as experiment_session:
                return await submit_job(job_data, BackgroundTasks(), session,
                    experiment_session=experiment_session, _typed_md_project_launch=adapter, **kwargs)
        return await submit_job(job_data, BackgroundTasks(), session, **kwargs)
    except Exception:
        if destination_launch_context_id is not None and experiment_session is not None:
            await experiment_session.rollback()
        raise
    finally:
        if resolved is not None:
            resolved.close()
