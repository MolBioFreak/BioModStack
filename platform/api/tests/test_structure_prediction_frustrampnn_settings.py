from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job
from routers import jobs as jobs_router
from schemas import JobCreate
from services.frustrampnn.settings import FrustraMPNNRequestedSettings, default_settings
from template_registry import TemplateRegistry


def _custom_settings() -> dict[str, object]:
    payload = default_settings().model_dump(mode="json")
    payload.pop("settings_value_origin")
    payload["classification_policy"] = {
        "mode": "custom",
        "high_max": -0.6,
        "minimal_min": 0.35,
    }
    return payload


@pytest_asyncio.fixture
async def job_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(jobs_router, "get_results_dir", lambda: results)
    monkeypatch.setattr(jobs_router, "workflow_launches_allowed", lambda: True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def _submit(session, *, name: str, params: dict[str, object]):
    return await jobs_router.create_job(
        JobCreate(
            name=name,
            model_id="boltz2",
            mode="predict",
            params={
                "sequence": "ACDE",
                "sequence_name": name,
                "boltz_use_msa": False,
                **params,
            },
        ),
        BackgroundTasks(),
        session,
    )


@pytest.mark.asyncio
async def test_structure_prediction_enabled_omission_persists_canonical_typed_defaults(job_db) -> None:
    async with job_db() as session:
        response = await _submit(
            session,
            name="frustrampnn-defaults",
            params={"run_frustrampnn": True},
        )
        job_id = response.id

    async with job_db() as session:
        persisted = await session.get(Job, job_id)
        assert persisted is not None
        assert persisted.params["run_frustrampnn"] is True
        assert persisted.params["frustrampnn_requiredness"] == "required"
        assert persisted.params["frustrampnn_settings"] == default_settings().model_dump(mode="json")
        assert FrustraMPNNRequestedSettings.model_validate(
            persisted.params["frustrampnn_settings"]
        ).model_dump(mode="json") == default_settings().model_dump(mode="json")


@pytest.mark.asyncio
async def test_structure_prediction_enabled_custom_settings_persist_exact_normalized_object(job_db) -> None:
    custom = _custom_settings()
    async with job_db() as session:
        response = await _submit(
            session,
            name="frustrampnn-custom",
            params={
                "run_frustrampnn": True,
                "frustrampnn_requiredness": "required",
                "frustrampnn_settings": custom,
            },
        )
        job_id = response.id

    async with job_db() as session:
        persisted = await session.get(Job, job_id)
        assert persisted is not None
        assert persisted.params["frustrampnn_settings"] == {
            **custom,
            "settings_value_origin": "operator_request",
        }
        assert set(persisted.params["frustrampnn_settings"]) == {
            "schema_name",
            "schema_version",
            "settings_value_origin",
            "protein_selection",
            "source_structure",
            "classification_policy",
        }
        assert not {
            "gpu_id",
            "pinned_gpu",
            "output_dir",
            "work_dir",
            "runtime",
            "container",
            "checkpoint",
            "command",
        }.intersection(persisted.params["frustrampnn_settings"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("settings", "expected_location"),
    [
        (
            {"schema_name": "frustrampnn_settings", "schema_version": 1},
            "frustrampnn_settings",
        ),
        (
            {**_custom_settings(), "caller_command": ["--device", "cpu"]},
            "caller_command",
        ),
    ],
)
async def test_structure_prediction_partial_or_unknown_settings_reject_before_job_commit(
    job_db,
    settings,
    expected_location,
) -> None:
    async with job_db() as session:
        with pytest.raises(HTTPException) as error:
            await _submit(
                session,
                name=f"invalid-{expected_location}",
                params={
                    "run_frustrampnn": True,
                    "frustrampnn_settings": settings,
                },
            )
        assert error.value.status_code == 422
        assert expected_location in str(error.value.detail)

    async with job_db() as session:
        assert (await session.execute(select(func.count(Job.id)))).scalar_one() == 0


@pytest.mark.asyncio
async def test_structure_prediction_disabled_rejects_settings_with_field_specific_422(job_db) -> None:
    async with job_db() as session:
        with pytest.raises(HTTPException) as error:
            await _submit(
                session,
                name="disabled-with-settings",
                params={
                    "run_frustrampnn": False,
                    "frustrampnn_settings": default_settings().model_dump(mode="json"),
                },
            )
        assert error.value.status_code == 422
        assert error.value.detail == [
            {
                "type": "value_error.frustrampnn_disabled",
                "loc": ["body", "params", "frustrampnn_settings"],
                "msg": "frustrampnn_settings requires run_frustrampnn=true",
            }
        ]

    async with job_db() as session:
        assert (await session.execute(select(func.count(Job.id)))).scalar_one() == 0


@pytest.mark.asyncio
async def test_structure_prediction_requiredness_cannot_be_weakened(job_db) -> None:
    async with job_db() as session:
        with pytest.raises(HTTPException) as error:
            await _submit(
                session,
                name="optional-frustrampnn-forbidden",
                params={
                    "run_frustrampnn": True,
                    "frustrampnn_requiredness": "optional",
                },
            )
        assert error.value.status_code == 422
        assert "frustrampnn_requiredness" in str(error.value.detail)


@pytest.mark.asyncio
async def test_structure_prediction_disabled_without_settings_preserves_requiredness_policy(job_db) -> None:
    async with job_db() as session:
        response = await _submit(
            session,
            name="frustrampnn-disabled",
            params={"run_frustrampnn": False},
        )
        job_id = response.id

    async with job_db() as session:
        persisted = await session.get(Job, job_id)
        assert persisted is not None
        assert persisted.params["run_frustrampnn"] is False
        assert persisted.params["frustrampnn_requiredness"] == "required"
        assert "frustrampnn_settings" not in persisted.params


def test_structure_prediction_template_declares_one_consumable_nested_settings_contract() -> None:
    registry = TemplateRegistry(
        Path(__file__).resolve().parents[1] / "config" / "templates"
    )
    template = registry.get_template("structure_prediction")
    assert template is not None
    params = {param.name: param for param in template.user_params}
    assert len(params) == len(template.user_params)
    assert params["run_frustrampnn"].type == "boolean"
    assert params["run_frustrampnn"].default is True
    assert params["frustrampnn_requiredness"].type == "enum"
    assert params["frustrampnn_requiredness"].enum == ["required"]
    assert params["frustrampnn_requiredness"].default == "required"
    nested = params["frustrampnn_settings"]
    assert nested.type == "object"
    assert nested.ui_control == "frustrampnn_settings"
    assert nested.default_source == "frustrampnn.canonical_defaults"
    assert nested.condition == {"param": "run_frustrampnn", "values": [True]}
    assert "FrustraMPNNRequestedSettings" in nested.description
    assert "schema version 1" in nested.description
