from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True, revalidate_instances='always')

class Row(Closed):
    row_id: str
    header: str
    sequence: str
    source_member_id: str | None = None
    occurrence_id: str | None = None
    source_offset: int | None = None

    @field_validator('row_id')
    @classmethod
    def safe_row_id(cls, value):
        if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or '>' in value:
            raise ValueError('row_id must be a nonempty FASTA identifier without whitespace or control characters')
        return value

    @field_validator('header', 'sequence')
    @classmethod
    def single_line(cls, value):
        if any(c in value for c in '\r\n'):
            raise ValueError('header and sequence must be single-line values')
        return value

class SequenceSet(Closed):
    molecule: Literal['protein', 'dna', 'rna']
    rows: list[Row]
    source_format: Literal['fasta', 'stockholm', 'clustal', 'a3m'] = 'fasta'
    source_text: str | None = None

    @model_validator(mode='after')
    def unique_row_ids(self):
        ids = [row.row_id for row in self.rows]
        if len(ids) != len(set(ids)):
            raise ValueError('row IDs must be unique within a roster')
        return self

class MafftSettings(Closed):
    engine: Literal['mafft'] = 'mafft'
    strategy: Literal['fft_ns_2', 'linsi', 'ginsi', 'auto'] = 'fft_ns_2'
    threads: int = Field(default=1, ge=1)
    treeout: bool = False

class AlignmentRequest(Closed):
    roster: SequenceSet
    settings: MafftSettings
