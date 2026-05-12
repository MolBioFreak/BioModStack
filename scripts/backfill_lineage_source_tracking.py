#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import select


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "platform" / "api"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from antibody_pipeline_contract import (  # noqa: E402
    ANTIBODY_PIPELINE_CONTRACT_VERSION,
    infer_antibody_artifact_class_from_stage,
    infer_selected_input_artifact_class,
    normalize_antibody_artifact_class,
    normalize_antibody_pipeline_contract_version,
)
from database import async_session, Job, Design  # noqa: E402


job_lookup: Dict[str, Job] = {}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_manifest(path_value: Any) -> Dict[str, Any]:
    path_text = _text(path_value)
    if not path_text:
        return {}
    path = Path(path_text)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_stage_token(value: Any) -> Optional[str]:
    token = _text(value)
    return token.lower() if token else None


def _derive_job_stage_family(job: Job) -> Optional[str]:
    params = _as_dict(getattr(job, "params", None))
    stage_mode = _normalize_stage_token(getattr(job, "stage_mode", None) or params.get("stage_mode"))
    if stage_mode in {"post_rfantibody", "backbone_refine"}:
        return "ppiflow" if params.get("run_ppiflow_backbone_refine") is not False else "rfantibody"
    if stage_mode in {"post_fampnn", "fampnn"}:
        return "fampnn"
    if stage_mode in {"maturation", "post_validation"}:
        return "ppiflow"

    candidates = [
        getattr(job, "stage_family", None),
        params.get("stage_family"),
        _as_dict(getattr(job, "provenance", None)).get("stage_family"),
        getattr(job, "child_stage", None),
        getattr(job, "awaiting_stage", None),
        getattr(job, "current_stage", None),
        getattr(job, "model_id", None),
        getattr(job, "mode", None),
    ]
    for candidate in candidates:
        token = _normalize_stage_token(candidate)
        if not token:
            continue
        if "rfantibody" in token:
            return "rfantibody"
        if "fampnn" in token or "fa-mpnn" in token:
            return "fampnn"
        if "ppiflow" in token or "maturation" in token:
            return "ppiflow"
        if "validation" in token or "protenix" in token or "boltz" in token:
            return "validation"
        if "frustrampnn" in token:
            return "frustrampnn"
    return None


def _derive_job_stage_mode(job: Job) -> Optional[str]:
    params = _as_dict(getattr(job, "params", None))
    provenance = _as_dict(getattr(job, "provenance", None))
    for candidate in (
        getattr(job, "stage_mode", None),
        params.get("stage_mode"),
        provenance.get("stage_mode"),
        getattr(job, "awaiting_stage", None),
        getattr(job, "current_stage", None),
    ):
        token = _text(candidate)
        if token:
            return token
    return None


def _extract_job_source_fields(job: Job) -> Dict[str, Any]:
    params = _as_dict(getattr(job, "params", None))
    provenance = _as_dict(getattr(job, "provenance", None))
    selected_input_dir = (
        _text(params.get("selected_input_dir"))
        or _text(params.get("iteration_selection_dir"))
        or _text(params.get("rfantibody_input_pdbs"))
        or _text(params.get("fampnn_collected_pdbs"))
        or _text(provenance.get("selected_input_dir"))
    )
    manifest_path = (
        _text(getattr(job, "source_selection_manifest_path", None))
        or _text(params.get("selected_input_manifest"))
        or _text(params.get("source_selection_manifest_path"))
        or _text(provenance.get("source_selection_manifest_path"))
        or (str(Path(selected_input_dir) / "selection_manifest.json") if selected_input_dir and (Path(selected_input_dir) / "selection_manifest.json").exists() else None)
    )
    manifest = _load_manifest(manifest_path)
    manifest_designs = manifest.get("designs")
    manifest_count = len(manifest_designs) if isinstance(manifest_designs, list) else None

    parent_job = None
    parent_job_id = _text(getattr(job, "parent_job_id", None))
    if parent_job_id:
        parent_job = job_lookup.get(parent_job_id) if 'job_lookup' in globals() else None

    source_stage_job_id = (
        _text(getattr(job, "source_stage_job_id", None))
        or _text(params.get("source_stage_job_id"))
        or _text(provenance.get("source_stage_job_id"))
        or _text(manifest.get("source_stage_job_id"))
        or _text(params.get("selected_input_source_job_id"))
        or _text(params.get("selection_source_job_id"))
        or _text(getattr(parent_job, "source_stage_job_id", None) if parent_job else None)
        or _text(getattr(parent_job, "selection_source_job_id", None) if parent_job else None)
    )
    source_stage_job = job_lookup.get(source_stage_job_id) if source_stage_job_id and 'job_lookup' in globals() else None
    source_stage_family = (
        _text(getattr(job, "source_stage_family", None))
        or _text(params.get("source_stage_family"))
        or _text(provenance.get("source_stage_family"))
        or _text(manifest.get("source_stage_family"))
        or _text(params.get("selected_input_stage_family"))
        or _text(getattr(parent_job, "source_stage_family", None) if parent_job else None)
        or _text(_derive_job_stage_family(source_stage_job) if source_stage_job else None)
    )
    source_stage_mode = (
        _text(getattr(job, "source_stage_mode", None))
        or _text(params.get("source_stage_mode"))
        or _text(provenance.get("source_stage_mode"))
        or _text(manifest.get("source_stage_mode"))
        or _text(params.get("selected_input_stage_mode"))
        or _text(getattr(parent_job, "source_stage_mode", None) if parent_job else None)
        or _text(_derive_job_stage_mode(source_stage_job) if source_stage_job else None)
    )
    selection_source_type = (
        _text(getattr(job, "selection_source_type", None))
        or _text(params.get("selection_source_type"))
        or _text(provenance.get("selection_source_type"))
    )
    selection_source_job_id = (
        _text(getattr(job, "selection_source_job_id", None))
        or _text(params.get("selection_source_job_id"))
        or _text(provenance.get("selection_source_job_id"))
        or source_stage_job_id
    )
    selection_dataset_name = (
        _text(getattr(job, "selection_dataset_name", None))
        or _text(params.get("selection_dataset_name"))
        or _text(provenance.get("selection_dataset_name"))
    )
    selection_count = (
        getattr(job, "source_selection_count", None)
        or params.get("source_selection_count")
        or provenance.get("source_selection_count")
        or manifest.get("source_selection_count")
        or manifest_count
        or (len(params.get("iteration_source_design_ids")) if isinstance(params.get("iteration_source_design_ids"), list) else None)
    )
    selected_loop_scope = (
        getattr(job, "selected_loop_scope", None)
        or params.get("selected_loop_scope")
        or provenance.get("selected_loop_scope")
    )
    selected_input_artifact_class = normalize_antibody_artifact_class(
        getattr(job, "selected_input_artifact_class", None)
        or params.get("selected_input_artifact_class")
        or provenance.get("selected_input_artifact_class")
        or manifest.get("selected_input_artifact_class")
        or infer_selected_input_artifact_class(
            selected_input_stage_family=source_stage_family,
            selected_input_stage_mode=source_stage_mode,
            rfantibody_input_pdbs=params.get("rfantibody_input_pdbs"),
            fampnn_collected_pdbs=params.get("fampnn_collected_pdbs"),
        )
    )
    selected_input_schema_version = normalize_antibody_pipeline_contract_version(
        getattr(job, "selected_input_schema_version", None)
        or params.get("selected_input_schema_version")
        or provenance.get("selected_input_schema_version")
        or manifest.get("selected_input_schema_version")
    )
    if selected_input_artifact_class and selected_input_schema_version is None:
        selected_input_schema_version = ANTIBODY_PIPELINE_CONTRACT_VERSION

    return {
        "source_stage_job_id": source_stage_job_id,
        "source_stage_family": source_stage_family,
        "source_stage_mode": source_stage_mode,
        "source_selection_manifest_path": manifest_path,
        "source_selection_count": selection_count,
        "selected_input_artifact_class": selected_input_artifact_class,
        "selected_input_schema_version": selected_input_schema_version,
        "selection_source_type": selection_source_type,
        "selection_source_job_id": selection_source_job_id,
        "selection_dataset_name": selection_dataset_name,
        "selected_loop_scope": selected_loop_scope,
    }


def _extract_design_source_fields(design: Design, job: Job, jobs_by_id: Dict[str, Job], designs_by_id: Dict[str, Design]) -> Dict[str, Any]:
    provenance = _as_dict(getattr(design, "provenance", None))
    ppiflow = _as_dict(provenance.get("ppiflow"))
    fampnn = _as_dict(provenance.get("fampnn"))
    source_design = designs_by_id.get(getattr(design, "parent_design_id", None) or "") or designs_by_id.get(getattr(design, "origin_design_id", None) or "")
    source_stage_job = jobs_by_id.get(getattr(job, "source_stage_job_id", None) or "") or (jobs_by_id.get(getattr(source_design, "job_id", None) or "") if source_design else None)

    source_pdb_path = (
        _text(getattr(design, "source_pdb_path", None))
        or _text(provenance.get("source_pdb_path"))
        or _text(ppiflow.get("source_pdb_path"))
        or _text(fampnn.get("source_pdb_path"))
        or _text(getattr(source_design, "pdb_path", None) if source_design else None)
    )
    source_design_name = (
        _text(getattr(design, "source_design_name", None))
        or _text(provenance.get("source_design_name"))
        or _text(ppiflow.get("source_design_name"))
        or _text(fampnn.get("source_design_name"))
        or _text(getattr(source_design, "name", None) if source_design else None)
    )
    source_stage_job_id = (
        _text(getattr(design, "source_stage_job_id", None))
        or _text(provenance.get("source_stage_job_id"))
        or _text(getattr(job, "source_stage_job_id", None))
        or _text(getattr(source_design, "job_id", None) if source_design else None)
    )
    source_stage_family = (
        _text(getattr(design, "source_stage_family", None))
        or _text(provenance.get("source_stage_family"))
        or _text(getattr(job, "source_stage_family", None))
        or _text(getattr(source_design, "stage_family", None) if source_design else None)
        or _text(_derive_job_stage_family(source_stage_job) if source_stage_job else None)
    )
    source_stage_mode = (
        _text(getattr(design, "source_stage_mode", None))
        or _text(provenance.get("source_stage_mode"))
        or _text(getattr(job, "source_stage_mode", None))
        or _text(getattr(source_design, "stage_mode", None) if source_design else None)
        or _text(_derive_job_stage_mode(source_stage_job) if source_stage_job else None)
    )
    lineage_root_job_id = (
        _text(getattr(design, "lineage_root_job_id", None))
        or _text(getattr(job, "lineage_root_job_id", None))
        or _text(getattr(job, "id", None))
    )
    origin_job_id = (
        _text(getattr(design, "origin_job_id", None))
        or _text(getattr(source_design, "job_id", None) if source_design else None)
    )
    artifact_class = normalize_antibody_artifact_class(
        getattr(design, "artifact_class", None)
        or provenance.get("artifact_class")
        or infer_antibody_artifact_class_from_stage(
            getattr(design, "stage_family", None) or getattr(job, "stage_family", None),
            getattr(design, "stage_mode", None) or getattr(job, "stage_mode", None),
        )
    )
    artifact_schema_version = normalize_antibody_pipeline_contract_version(
        getattr(design, "artifact_schema_version", None)
        or provenance.get("artifact_schema_version")
    )
    if artifact_class and artifact_schema_version is None:
        artifact_schema_version = ANTIBODY_PIPELINE_CONTRACT_VERSION

    return {
        "lineage_root_job_id": lineage_root_job_id,
        "origin_job_id": origin_job_id,
        "source_stage_job_id": source_stage_job_id,
        "source_stage_family": source_stage_family,
        "source_stage_mode": source_stage_mode,
        "source_pdb_path": source_pdb_path,
        "source_design_name": source_design_name,
        "artifact_class": artifact_class,
        "artifact_schema_version": artifact_schema_version,
    }


async def _run_backfill(job_id: Optional[str], apply: bool) -> None:
    async with async_session() as session:
        job_query = select(Job)
        if job_id:
            job_query = job_query.where(Job.id == job_id)
        jobs = list((await session.execute(job_query)).scalars().all())
        jobs_by_id = {job.id: job for job in jobs}
        globals()["job_lookup"] = jobs_by_id

        design_query = select(Design)
        if job_id:
            design_query = design_query.where(Design.job_id == job_id)
        designs = list((await session.execute(design_query)).scalars().all())
        designs_by_id = {design.id: design for design in designs}

        updated_jobs = 0
        updated_designs = 0

        for job in jobs:
            fields = _extract_job_source_fields(job)
            changed = False
            for key, value in fields.items():
                if value in (None, "", [], {}):
                    continue
                if getattr(job, key) != value:
                    setattr(job, key, value)
                    changed = True
            derived_family = _derive_job_stage_family(job)
            derived_mode = _derive_job_stage_mode(job)
            if derived_family and getattr(job, "stage_family", None) != derived_family:
                job.stage_family = derived_family
                changed = True
            if derived_mode and getattr(job, "stage_mode", None) != derived_mode:
                job.stage_mode = derived_mode
                changed = True
            if not getattr(job, "lineage_root_job_id", None):
                job.lineage_root_job_id = getattr(job, "parent_job_id", None) or job.id
                changed = True
            if changed:
                updated_jobs += 1

        for design in designs:
            job = jobs_by_id.get(design.job_id)
            if not job:
                continue
            fields = _extract_design_source_fields(design, job, jobs_by_id, designs_by_id)
            changed = False
            for key, value in fields.items():
                if value in (None, "", [], {}):
                    continue
                if getattr(design, key) != value:
                    setattr(design, key, value)
                    changed = True
            if not getattr(design, "stage_family", None):
                derived_family = getattr(job, "stage_family", None) or _derive_job_stage_family(job)
                if derived_family:
                    design.stage_family = derived_family
                    changed = True
            if not getattr(design, "stage_mode", None):
                derived_mode = getattr(job, "stage_mode", None) or _derive_job_stage_mode(job)
                if derived_mode:
                    design.stage_mode = derived_mode
                    changed = True
            if changed:
                updated_designs += 1

        print(f"Jobs updated: {updated_jobs}")
        print(f"Designs updated: {updated_designs}")

        if apply:
            await session.commit()
            print("Committed lineage/source backfill.")
        else:
            await session.rollback()
            print("Dry run only; rolled back changes.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill explicit lineage/source tracking fields on jobs and designs.")
    parser.add_argument("--job-id", help="Optional single job id to backfill")
    parser.add_argument("--apply", action="store_true", help="Persist updates. Without this flag the script runs as a dry run.")
    args = parser.parse_args()
    asyncio.run(_run_backfill(args.job_id, args.apply))


if __name__ == "__main__":
    main()
