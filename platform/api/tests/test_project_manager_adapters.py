from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job, MolBioNgsReceipt, NgsReferenceSetManifest, NgsReferenceSetMapping
from experiment_database import create_experiment_engine, create_experiment_session_factory
from experiment_migrations import run_all
from experiment_models import (
    ExperimentDomainAdapterReceipt,
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
)
from experiment_services import RevisionConflict, create_domain_experiment, create_global_experiment, create_project
from services.global_experiments.adapters import AdapterError, NgsReferenceSetAdapter
from services.global_experiments.read_models import build_project_manager_read_model
from services.global_experiments.receipts import attach_verified_entity
from services.global_experiments.result_surfaces import result_surface_for_receipt
import services.global_experiments.adapters as adapter_module
import services.ont_barcode_batches as barcode_batches


SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "docs" / "specs" / "schemas"


def _canonical_sha(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    ).hexdigest()




def _project_payload() -> dict:
    return {
        "schema": "bms.project.v1",
        "name": "Adapter project",
        "description": "",
        "research_objective": "Verify attachments",
        "owner": "operator",
        "contributors": [],
        "tags": [],
        "status": "active",
        "start_date": None,
        "target_end_date": None,
        "external_references": [],
        "created_by": "operator",
        "change_summary": "created",
    }


def _global_payload() -> dict:
    return {
        "schema": "bms.global-experiment.v1",
        "name": "Adapter experiment",
        "objective": "Verify source authorities",
        "scientific_question": "Can records reopen from verified receipts?",
        "hypothesis": None,
        "description": "",
        "status": "active",
        "priority": "normal",
        "tags": [],
        "shared_source_receipt_ids": [],
        "shared_dataset_ids": [],
        "comparison_plan": None,
        "success_criteria": ["Both sources verify"],
        "review_summary": None,
        "conclusion": None,
        "created_by": "operator",
        "change_summary": "created",
    }


def _domain_payload(kind: str) -> dict:
    domain_payload = (
        {
            "schema": "bms.protein-in-silico-experiment.v1",
            "experiment_mode": "redesign",
            "targets": [
                {
                    "target_id": "target-1",
                    "label": "Target 1",
                    "entity_receipt_ids": [],
                    "role": "target",
                }
            ],
            "scientific_objective": "Redesign a local region",
            "design_constraints": [],
            "planned_capabilities": ["rfd3_local_redesign"],
            "comparison_groups": [],
            "validation_strategy": ["boltz2"],
        }
        if kind == "protein_in_silico"
        else {"schema": "bms.ngs-molbio-experiment.v1"}
    )
    return {
        "schema": "bms.domain-experiment.v1",
        "domain_kind": kind,
        "domain_contract_version": "1",
        "name": "Protein" if kind == "protein_in_silico" else "NGS",
        "objective": "Verify attachment",
        "status": "active",
        "tags": [],
        "source_receipt_ids": [],
        "dataset_ids": [],
        "created_by": "operator",
        "change_summary": "created",
        "domain_payload": domain_payload,
    }


@pytest_asyncio.fixture
async def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BMS_BUILD_SHA", "adapter-test-build")
    monkeypatch.setattr(adapter_module, "source_build_revision", lambda: "adapter-test-build")
    monkeypatch.setenv("BMS_DATA", str(tmp_path))
    results_root = tmp_path / "results"
    results_root.mkdir()
    monkeypatch.setenv("BMS_RESULTS_DIR", str(results_root))
    inputs_root = tmp_path / "inputs"
    inputs_root.mkdir()
    monkeypatch.setattr(adapter_module, "get_inputs_dir", lambda: inputs_root)
    monkeypatch.setattr(barcode_batches, "get_inputs_dir", lambda: inputs_root)
    experiment_path = tmp_path / "experiments.db"
    run_all(experiment_path)
    experiment_engine = create_experiment_engine(f"sqlite+aiosqlite:///{experiment_path}")
    experiment_factory = create_experiment_session_factory(experiment_engine)
    core_path = tmp_path / "core.db"
    core_engine = create_async_engine(f"sqlite+aiosqlite:///{core_path}")
    async with core_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    core_factory = async_sessionmaker(core_engine, expire_on_commit=False)
    try:
        yield tmp_path, experiment_factory, core_factory
    finally:
        await experiment_engine.dispose()
        await core_engine.dispose()










