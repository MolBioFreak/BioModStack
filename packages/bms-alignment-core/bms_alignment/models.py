from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Row(Closed):
    row_id: str
    header: str
    sequence: str
    source_member_id: str | None = None
    occurrence_id: str | None = None
    source_offset: int | None = None

class SequenceSet(Closed):
    molecule: Literal['protein', 'dna', 'rna']
    rows: list[Row]
    source_format: Literal['fasta', 'stockholm', 'clustal', 'a3m'] = 'fasta'
    source_text: str | None = None

class MafftSettings(Closed):
    engine: Literal['mafft'] = 'mafft'
    strategy: Literal['fft_ns_2', 'linsi', 'ginsi', 'auto'] = 'fft_ns_2'
    threads: int = Field(default=1, ge=1)
    treeout: bool = False

class AlignmentRequest(Closed):
    roster: SequenceSet
    settings: MafftSettings
