"""Cartesian composition of native requests; no second scientific or retention owner."""
from __future__ import annotations

import math
import random
from typing import Annotated, Literal
from pydantic import Field, model_validator
from services.assembly.golden_gate_design_types import Closed, Part, Source
from services.assembly.golden_gate_workflow_types import AssembleTask, AutomaticPrimerSelection, EditRequest, WorkflowResult
from services.assembly.golden_gate_reaction import ReactionPart


class SlotAlternative(Closed):
    id: str = Field(min_length=1)
    source: Source
    part: Part
    automatic_primer: AutomaticPrimerSelection | None = None
    domestication: EditRequest | None = None
    reaction_part: ReactionPart | None = None


class BatchSlot(Closed):
    part_id: str
    alternatives: list[SlotAlternative] = Field(min_length=1)


class FullScope(Closed):
    mode: Literal['full'] = 'full'


class SampledScope(Closed):
    mode: Literal['sampled']
    count: int = Field(ge=1)
    seed: int


class BatchRequest(Closed):
    schema_version: Literal['bms.golden-gate-batch.v1'] = 'bms.golden-gate-batch.v1'
    base: AssembleTask
    slots: list[BatchSlot] = Field(min_length=1)
    scope: Annotated[FullScope | SampledScope, Field(discriminator='mode')] = Field(default_factory=FullScope)

    @model_validator(mode='after')
    def identities(self):
        part_ids = {p.id for p in self.base.parts}
        if len({s.part_id for s in self.slots}) != len(self.slots):
            raise ValueError('A part slot may be varied only once')
        for slot in self.slots:
            if slot.part_id not in part_ids:
                raise ValueError('Batch slot must identify a base part')
            if len({a.id for a in slot.alternatives}) != len(slot.alternatives):
                raise ValueError('Alternative IDs must be unique within a slot')
            for a in slot.alternatives:
                if a.part.id != slot.part_id or a.part.source_id != a.source.id:
                    raise ValueError('Alternative part/source identities must match its slot')
                if a.automatic_primer and a.automatic_primer.part_id != slot.part_id:
                    raise ValueError('Automatic primer must identify its alternative part')
                if a.domestication and a.domestication.source_id != a.source.id:
                    raise ValueError('Domestication must identify its alternative source')
                if a.reaction_part and a.reaction_part.part_id != slot.part_id:
                    raise ValueError('Reaction amount must identify its alternative part')
        if self.scope.mode == 'sampled' and self.scope.count > combination_count(self):
            raise ValueError('Sample count exceeds the Cartesian domain')
        return self


def combination_count(request: BatchRequest) -> int:
    return math.prod(len(slot.alternatives) for slot in request.slots)


def selected_indices(request: BatchRequest):
    """Last slot varies fastest. Seeded Floyd sampling without enumerating the domain."""
    total = combination_count(request)
    if request.scope.mode == 'full':
        return range(total)
    rng = random.Random(request.scope.seed)
    selected = set()
    for j in range(total - request.scope.count, total):
        t = rng.randrange(j + 1)
        selected.add(j if t in selected else t)
    return sorted(selected)


def combination(request: BatchRequest, index: int) -> tuple[AssembleTask, list[str]]:
    alternatives = []
    for slot in reversed(request.slots):
        index, choice = divmod(index, len(slot.alternatives))
        alternatives.append(slot.alternatives[choice])
    alternatives.reverse()
    by_part = {a.part.id: a for a in alternatives}
    parts = [by_part[p.id].part if p.id in by_part else p for p in request.base.parts]
    # Only selected material enters the scalar owner; conflicting aliases are not silently overwritten.
    sources = {}
    base_sources = {s.id: s for s in request.base.sources}
    for part in parts:
        source = by_part[part.id].source if part.id in by_part else base_sources[part.source_id]
        if source.id in sources and sources[source.id] != source:
            raise ValueError('Selected alternatives give one source ID different materials; use distinct source IDs')
        sources[source.id] = source
    primers = [p for p in request.base.automatic_primers if p.part_id not in by_part]
    primers += [a.automatic_primer for a in alternatives if a.automatic_primer]
    base_edits = {e.source_id: e for e in request.base.domestication}
    edits = {}
    for part in parts:
        edit = by_part[part.id].domestication if part.id in by_part else base_edits.get(part.source_id)
        if part.source_id in edits and edits[part.source_id] != edit:
            raise ValueError('Selected source has conflicting domestication settings; use distinct source IDs')
        edits[part.source_id] = edit
    reaction = request.base.reaction
    if reaction is not None:
        reaction = reaction.model_copy(update={'parts': [p for p in reaction.parts if p.part_id not in by_part] +
            [a.reaction_part for a in alternatives if a.reaction_part]})
    native = request.base.model_copy(update=dict(sources=list(sources.values()), parts=parts,
        automatic_primers=primers, domestication=[e for e in edits.values() if e is not None], reaction=reaction))
    return native, [a.id for a in alternatives]


class BatchProgress(Closed):
    # Decimal strings keep exact counts beyond JavaScript's integer range.
    total: str
    selected: str
    evaluated: str
    completed: str
    omitted: str
    remaining: str


class ScopeEvent(Closed):
    event: Literal['scope'] = 'scope'
    mode: Literal['full', 'sampled']
    sampling: Literal['python-random-floyd-v1'] = 'python-random-floyd-v1'
    seed: int | None
    progress: BatchProgress


class ResultEvent(Closed):
    event: Literal['result'] = 'result'
    index: str
    alternatives: list[str]
    result: WorkflowResult
    progress: BatchProgress


class ErrorEvent(Closed):
    event: Literal['error'] = 'error'
    index: str
    message: str
    progress: BatchProgress


class FinishedEvent(Closed):
    event: Literal['finished'] = 'finished'
    progress: BatchProgress
    coverage: Literal['full', 'sampled', 'incomplete']


BatchEvent = Annotated[ScopeEvent | ResultEvent | ErrorEvent | FinishedEvent, Field(discriminator='event')]
