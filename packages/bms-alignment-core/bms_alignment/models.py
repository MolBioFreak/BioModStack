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
    # One entry per sequence column; gaps and unknown residues are None.
    source_positions: list[int | None] | None = None
    transformation_provenance: str | None = None

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

class FamsaSettings(Closed):
    engine: Literal['famsa'] = 'famsa'
    threads: int = Field(default=1, ge=1)
    guide_tree: Literal['sl', 'upgma', 'nj'] = 'sl'
    refine_mode: Literal['auto', 'on', 'off'] = 'auto'
    keep_duplicates: bool = False
    medoidtree: bool = False
    medoid_seeds: int | None = None
    medoid_threshold: int | None = None
    trim_columns: float | None = None

class MuscleSettings(Closed):
    engine: Literal['muscle'] = 'muscle'
    mode: Literal['align', 'super5'] = 'align'
    threads: int = Field(default=1, ge=1)
    permutation: Literal['none', 'abc', 'acb', 'bca', 'all'] = 'none'
    perturb: int = Field(default=0, ge=0)
    ensemble: Literal['none', 'stratified', 'diversified'] = 'none'
    replicates: int | None = Field(default=None, ge=1)

class TwilightSettings(Closed):
    engine: Literal['twilight'] = 'twilight'
    threads: int = Field(default=1, ge=1)
    method: Literal['pairwise', 'tcs'] = 'pairwise'
    acc_mode: Literal[0, 1, 2, 3] = 0
    acc_max_group: int | None = None
    acc_sample_rate: float | None = None
    acc_sample_top: float | None = None
    consistency_weight: float = 5.0
    max_subtree: int | None = None
    remove_gappy: float = 0.95
    wildcard: bool = False
    rooted: bool = False
    prune: bool = False
    filter: bool = False
    match: float = 18
    mismatch: float = -8
    transition: float = -4
    gap_open: float | None = None
    gap_extend: float | None = None
    gap_ends: float | None = None
    scale: float | None = None
    xdrop: float = 600
    guide_source: Literal['supplied', 'mafft'] = 'supplied'
    guide_tree: str | None = None
    leaf_mapping: dict[str, str] = Field(default_factory=dict)

class AlignmentRequest(Closed):
    roster: SequenceSet
    settings: MafftSettings | FamsaSettings | MuscleSettings | TwilightSettings = Field(discriminator='engine')


    @model_validator(mode='before')
    @classmethod
    def default_mafft_discriminator(cls, value):
        if isinstance(value, dict) and isinstance(value.get('settings'), dict) and 'engine' not in value['settings']:
            value = {**value, 'settings': {'engine': 'mafft', **value['settings']}}
        return value
