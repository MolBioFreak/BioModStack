"""Typed project-shared alignment state, stored by the Project Dataset owner."""
from typing import Literal, Annotated
from typing_extensions import TypedDict
from pydantic import BaseModel, ConfigDict, Field, model_validator, with_config


@with_config(ConfigDict(extra='forbid'))
class RendererState(TypedDict, total=False):
    scrollX: float
    scrollY: float
    columnWidth: float
    rowHeight: float
    colorScheme: str
    hiddenColumns: list[int]
    hideGaps: bool
    allowedGappyness: float
    zoomLevel: float


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ArtifactColumns(Closed):
    scope: Literal['artifact_columns']
    artifact_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode='after')
    def ordered(self):
        if self.end < self.start:
            raise ValueError('end must not precede start')
        return self


class SourcePositions(Closed):
    scope: Literal['source_positions']
    source_member_id: str = Field(min_length=1)
    occurrence_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode='after')
    def ordered(self):
        if self.end < self.start:
            raise ValueError('end must not precede start')
        return self


Coordinate = Annotated[ArtifactColumns | SourcePositions, Field(discriminator='scope')]


class Mark(Closed):
    id: str = Field(min_length=1)
    label: str
    coordinate: Coordinate
    note: str = ''
    provenance: dict = Field(default_factory=dict)


class View(Closed):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    hidden_row_ids: list[str] = Field(default_factory=list)
    row_order: list[str] = Field(default_factory=list)
    selected_marks: list[str] = Field(default_factory=list)
    renderer_state: RendererState | None = None
    viewer: str | None = None
    selected_row_id: str | None = None
    selected_column: int | None = Field(default=None, ge=0)


class Comparison(Closed):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    left_artifact_id: str = Field(min_length=1)
    right_artifact_id: str = Field(min_length=1)
    method: str = Field(min_length=1)
    report: dict
    provenance: dict = Field(default_factory=dict)


class SharedState(Closed):
    schema_name: Literal['bms.family-msa.shared-state.v1'] = Field(default='bms.family-msa.shared-state.v1', alias='schema')
    marks: list[Mark] = Field(default_factory=list)
    views: list[View] = Field(default_factory=list)
    comparisons: list[Comparison] = Field(default_factory=list)
