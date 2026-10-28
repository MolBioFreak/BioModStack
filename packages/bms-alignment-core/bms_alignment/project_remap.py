"""Explicit Project-export identity remapping, independent of server custody.

This module does not import or persist anything.  Unmapped references remain
unchanged and are reported; callers must not treat them as destination IDs.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ArtifactEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    logical_path: str | None = None
    path: str = Field(min_length=1)


class StateEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1)
    path: str = Field(min_length=1)


class ProjectInventory(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    schema_name: str = Field(alias='schema', pattern=r'^bms\.family-msa\.project-export\.v1$')
    project_id: str = Field(min_length=1)
    receipt_id: str = Field(min_length=1)
    artifacts: list[ArtifactEntry]
    shared_states: list[StateEntry]


class UnmappedReference(BaseModel):
    location: str
    source_artifact_id: str


class RemappedState(BaseModel):
    state: dict[str, Any]
    unresolved: list[UnmappedReference]


def remap_state(state: dict[str, Any], artifact_ids: dict[str, str]) -> RemappedState:
    """Remap only schema-defined artifact references; never infer identities.

    Source member and occurrence identifiers are scientific identities and are
    intentionally preserved. Arbitrary provenance/report payloads are untouched.
    """
    result = deepcopy(state)
    unresolved = []

    def remap(obj: dict, field: str, location: str) -> None:
        value = obj[field]
        if value in artifact_ids:
            obj[field] = artifact_ids[value]
        else:
            unresolved.append(UnmappedReference(location=location, source_artifact_id=value))

    for i, mark in enumerate(result.get('marks', [])):
        coord = mark['coordinate']
        if coord['scope'] == 'artifact_columns':
            remap(coord, 'artifact_id', f'marks/{i}/coordinate/artifact_id')
    for i, view in enumerate(result.get('views', [])):
        remap(view, 'artifact_id', f'views/{i}/artifact_id')
    for i, comparison in enumerate(result.get('comparisons', [])):
        for field in ('left_artifact_id', 'right_artifact_id'):
            remap(comparison, field, f'comparisons/{i}/{field}')
    return RemappedState(state=result, unresolved=unresolved)
