"""Saved model-owned requests must not inherit legacy antibody-template repair."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import UserTemplate
from routers.user_templates import (
    UserTemplateCreate,
    UserTemplateUpdate,
    create_user_template,
    get_user_template,
    list_user_templates,
    update_user_template,
)


@pytest.mark.asyncio
async def test_native_binder_template_create_update_list_get_preserve_exact_params(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'templates.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(UserTemplate.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            params = {
                "antigen_chains": "A,B",
                "epitope_residues": "A12,B25",
                "framework_type": "sabdab",
                "sabdab_framework": {"filePath": "inputs/from-preview.pdb"},
                "custom_framework_path": "inputs/owned-source.pdb",
                "framework_pdb": "inputs/owned-source.pdb",
                "native_filters": {"accept_zero": 0, "enable_rank": False},
                "targets": [{"chain": "A", "weight": 0}, {"chain": "B", "weight": 1}],
                "denovo_generator": "bindcraft2",
                "bindcraft2_settings": {
                    "max_trajectories": 12,
                    "modality": ["binder"],
                    "targets": [{"name": "on", "target_path": "inputs/on.cif"}],
                    "filters": {"i_pTM": {"threshold": 0.8, "higher": True}},
                },
            }
            created = await create_user_template(UserTemplateCreate(
                name="native bindcraft2 settings",
                model_id="bindcraft2",
                base_template_id="antibody_denovo",
                mode="campaign",
                params=params,
            ), session=session)
            assert created.params == params
            assert (await get_user_template(created.id, session=session)).params == params
            assert (await list_user_templates(search=None, model_id="bindcraft2", limit=100, offset=0,
                                               session=session))[0].params == params
            changed = {**params, "native_filters": {"accept_zero": 0, "enable_rank": True}}
            updated = await update_user_template(created.id, UserTemplateUpdate(params=changed), session=session)
            assert updated.params == changed
        async with sessions() as session:
            assert (await get_user_template(created.id, session=session)).params == changed
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_existing_antibody_template_repairs_only_legacy_identity(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy-templates.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(UserTemplate.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            session.add(UserTemplate(id="legacy", name="legacy antibody", model_id="template_antibody_denovo",
                                     base_template_id="antibody_denovo", mode="antibody_denovo_pipeline",
                                     params={"antigen_chains": "A,B", "epitope_residues": "A12,B25"}))
            await session.commit()
            repaired = await get_user_template("legacy", session=session)
            assert repaired.params["selected_chain"] == "A"
            assert repaired.params["selected_residues"] == ["A12", "B25"]
        async with sessions() as session:
            assert (await session.get(UserTemplate, "legacy")).params["selected_chain"] == "A"
    finally:
        await engine.dispose()
