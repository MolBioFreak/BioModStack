"""Standalone MD prediction snapshots on the existing saved-draft revision owner.

No Project, setup, Job, placement or operation lifecycle is created here.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from fastapi import HTTPException
from database import UserTemplate, UserTemplateRevision
from schemas import CandidateRef


class MdPredictionDetour(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    candidate_ref: CandidateRef | None = None
    composition: dict[str, Any]
    round_plan: dict[str, Any]
    md_draft: dict[str, Any]
    destination_launch_context_id: str | None = None
    return_to: str | None = Field(default=None, pattern=r'^/designs/[^\\\s?#]+(?:[?#][^\\\r\n]*)?$')


async def save_prediction_detour(payload: MdPredictionDetour, session) -> str:
    identity = str(uuid.uuid4())
    snapshot = payload.model_dump(mode='json', exclude_unset=True)
    session.add(UserTemplate(id=identity, name=f'MD prediction draft {identity}',
        model_id='molecular_dynamics', mode='md_prediction_detour',
        params={'schema': 'md_prediction_detour_v1', 'revision': 1}))
    session.add(UserTemplateRevision(template_id=identity, revision=1, snapshot=snapshot))
    await session.commit()
    return identity


async def load_prediction_detour(identity: str, session) -> MdPredictionDetour:
    template = await session.get(UserTemplate, identity)
    revision = await session.get(UserTemplateRevision, (identity, 1))
    if (template is None or template.model_id != 'molecular_dynamics'
            or template.mode != 'md_prediction_detour' or revision is None):
        raise HTTPException(404, 'Saved MD prediction draft is unavailable.')
    return MdPredictionDetour.model_validate(revision.snapshot)
