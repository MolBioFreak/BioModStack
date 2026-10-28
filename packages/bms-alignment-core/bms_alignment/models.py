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
    stockholm_annotations: list[dict] = Field(default_factory=list)
    format_observations: list[str] = Field(default_factory=list)
    # Observational record of selected and excluded Dataset occurrences.
    source_evidence: list[dict] = Field(default_factory=list)

    @model_validator(mode='after')
    def unique_row_ids(self):
        ids = [row.row_id for row in self.rows]
        if len(ids) != len(set(ids)):
            raise ValueError('row IDs must be unique within a roster')
        return self

class MafftSettings(Closed):
    engine: Literal['mafft'] = 'mafft'
    strategy: Literal['fft_ns_2', 'linsi', 'ginsi', 'einsi', 'auto'] = 'fft_ns_2'
    threads: int = Field(default=1, ge=1)
    treeout: bool = False
    pair_method: Literal['6merpair', '10merpair', 'hybridpair', 'localgenafpair', 'oldgenafpair', 'shortlongpair', 'longshortpair'] | None = Field(default=None, description='Explicit native pair method, compiled after strategy; null preserves the original strategy. Native aliases retain their exact spelling. 10mer requires nucleotide input. Hybridpair faults in the pinned synthetic protein probe; no fallback is substituted.')
    tree_method: Literal['large', 'memsavetree', 'memsavetreex', 'stepadd', 'youngestlinkage', 'pileup', 'randomchain'] | None = Field(default=None, description='Native tree construction override; native restrictions and failures remain visible, without switching algorithms.')
    refinement_policy: Literal['bestfirst', 'adhoc0', 'adhoc1', 'adhoc2', 'simplehillclimbing'] | None = Field(default=None, description='Native iterative parallelization policy; aliases are not normalized.')
    alga: bool = False
    allowshift: bool = Field(default=False, description='Native shift-aware scoring; requires globalpair in the pinned engine.')
    anchors: str | None = Field(default=None, description='Exact native external anchor table text: seven integers per line, i j starti endi startj endj score. Sequence and position indices are 1-based; i must precede j. Uses roster order, not display labels; retained verbatim.', json_schema_extra={'format': 'mafft-anchors'})
    bunkatsunashi: bool = False
    focus: bool = Field(default=False, description='Native target-focused consistency. Native row names must carry _focus_; use focus_rows to explicitly mark selected roster row IDs.')
    focus_rows: str | None = Field(default=None, description='One exact roster row ID per line to mark with the native _focus_ prefix. Explicit reversible native row-map is retained. Unknown IDs are observations, not an admission gate; null leaves native identifiers unchanged.')
    gep: float | None = Field(default=None, description='Native global pairwise offset (--gep), distinct from --ep and --lep.')
    gexp: float | None = Field(default=None, description='Native global pairwise extension (--gexp), distinct from --exp and --lexp.')
    gop: float | None = Field(default=None, description='Native global pairwise opening (--gop), distinct from --op and --lop.')
    kappa: float | None = None
    leavegappyregion: bool = Field(default=False, description='Exact native alias of legacygappenalty, preserved in requested settings.')
    lhlimit: float | None = Field(default=None, description='Native nodepair local-homology limit; consumed by memory-saving pairwise tree paths.')
    minimumweight: float | None = None
    noscore: bool = False
    oneiteration: bool = False
    shiftpenalty: float | None = None
    similaritylevel: float | None = None
    skipiterate: float | None = None
    sp: bool = Field(default=False, description='Rigorous sum-of-pairs scoring; native refinement requires threadit=0.')
    sparsepickup: int | None = Field(default=None, description='Native sparse pickup. Can omit rows; actual output and notused artifact are retained, never silently replenished.')
    terminalmargin: int | None = None
    unalignlevel: float | None = None
    nodeout: bool = False
    distance_output: Literal['distout', 'distoutclustalo'] | None = None
    scoreout: bool = Field(default=False, description='Request native score diagnostics; engine.log retains emitted diagnostics. Pinned L-INS-i synthetic probe faults in tbfast with this option; failure is retained without substitution.')
    op: float | None = None
    ep: float | None = None
    weighti: float | None = Field(default=None, description='Pairwise consistency weight; applies to iterative pairwise strategies.')
    lop: float | None = Field(default=None, description='Local pairwise gap opening penalty.')
    lep: float | None = Field(default=None, description='Local pairwise offset.')
    lexp: float | None = Field(default=None, description='Local pairwise gap extension penalty.')
    long_gap_open: float | None = Field(default=None, description='E-INS-i skip penalty (native --LOP).')
    long_gap_extend: float | None = Field(default=None, description='E-INS-i skip extension penalty (native --LEXP).')
    bl: Literal[30, 45, 62, 80] | None = Field(default=None, description='Protein BLOSUM matrix number.')
    jtt: int | None = Field(default=None, description='Protein JTT PAM distance.')
    tm: int | None = Field(default=None, description='Protein transmembrane PAM distance.')
    kimura: int | None = Field(default=None, description='Nucleotide transition/transversion parameter.')
    randomseed: int | None = Field(default=None, description='Native random seed where used by MAFFT.')
    nofft: bool = False
    fft: bool = Field(default=False, description='Force FFT; compiled before nofft if both are selected.')
    threadit: int | None = Field(default=None, description='Native iterative-step threads; 0 disables threaded refinement, -1 selects automatic.')
    threadtb: int | None = Field(default=None, description='Native progressive-step threads; -1 selects automatic.')
    parttree: Literal['parttree', 'dpparttree'] | None = Field(default=None, description='PartTree distance mode; native interactions with pairwise strategies are retained.')
    partsize: int | None = None
    groupsize: int | None = None
    linkage: Literal['minimumlinkage', 'averagelinkage'] | None = None
    mixedlinkage: float | None = Field(default=None, description='Mixed linkage coefficient; compiled after linkage when both are supplied.')
    unweight: bool = False
    termgappenalty: bool = False
    legacygappenalty: bool = False
    anysymbol: bool = False
    preservecase: bool = False
    direction: Literal['adjustdirection', 'adjustdirectionaccurately'] | None = Field(default=None, description='Native nucleotide direction adjustment. Raw input and native output remain retained; no inferred coordinate remap.')
    memsave: Literal['memsave', 'nomemsave'] | None = None
    fmodel: bool = False
    nucleotide_scoring: Literal['nwildcard', 'nzero'] | None = None
    opdist: float | None = None
    exp: float | None = None
    treein: str | None = Field(default=None, description='Exact MAFFT merge-table file text (not Newick): one merge per line, 1-based roster indices and branch lengths. Retained verbatim as treein.txt.', json_schema_extra={'format': 'mafft-merge-table'})
    aamatrix: str | None = Field(default=None, description='Exact native protein matrix file text: amino-acid header, lower triangular scores, optional frequency/norescale record. Retained verbatim as aamatrix.txt; compiled after named substitution models.', json_schema_extra={'format': 'mafft-amino-matrix'})
    retree: int | None = Field(default=None, ge=0, description='Override the strategy guide-tree rebuild count.')
    maxiterate: int | None = Field(default=None, ge=0, description='Override the strategy refinement iteration count.')
    reorder: bool = False
    quiet: bool = False

class FamsaSettings(Closed):
    engine: Literal['famsa'] = 'famsa'
    threads: int = Field(default=1, ge=1)
    guide_tree: Literal['sl', 'upgma', 'nj', 'import'] = 'sl'
    guide_tree_newick: str | None = Field(default=None, description='Supplied Newick file text used with guide_tree=import. Leaves are native bmsrow00000001 identifiers unless explicitly mapped; original and submitted trees are retained.', json_schema_extra={'format': 'newick'})
    leaf_mapping: dict[str, str] = Field(default_factory=dict, description='Explicit supplied tree leaf → roster row ID mapping. Unmapped leaves are retained and reported, never guessed.')
    refine_mode: Literal['auto', 'on', 'off'] = 'auto'
    keep_duplicates: bool = False
    medoidtree: bool = False
    medoid_seeds: int | None = None
    medoid_threshold: int | None = None
    trim_columns: float | None = None
    r: int | None = None
    sm: Literal['MIQS', 'PFASUM31', 'PFASUM43', 'PFASUM60'] | None = None
    go: int | None = None
    ge: int | None = None
    tgo: int | None = None
    tge: int | None = None
    dist: Literal['indel_div_lcs', 'indel075_div_lcs'] | None = None
    gsd: int | None = None
    gsl: int | None = None
    dgr: bool = False
    dgo: bool = False
    dsp: bool = False
    verbose: bool = False

class MuscleSettings(Closed):
    engine: Literal['muscle'] = 'muscle'
    mode: Literal['align', 'super5'] = 'align'
    guidetreeout: bool = Field(default=False, description='Native guide-tree diagnostic: MPC writes guide.native.nwk then exits zero BEFORE alignment, including in ensembles. This is not an alignment-plus-tree option. Random-chain bypasses the writer. No automatic rerun or algorithm replacement.')
    hmmin: str | None = Field(default=None, description='Exact MUSCLE HMM parameter file text, retained verbatim as hmmin.txt. Consumed by align and Super5; no parameter conversion.', json_schema_extra={'format': 'muscle-hmm'})
    hmmout: bool = Field(default=False, description='Native InitProbcons parameter dump (Super5); align initializes its HMM directly and does not write this file. Dump precedes CmdLineUpdate; not a complete effective-parameter receipt.')
    anchor_letter: str | None = Field(default=None, min_length=1, max_length=1, description='Native one-character HMM anchor override; no case normalization.')
    randseed: int | None = Field(default=None, description='Native RNG seed; zero explicitly selects time/process-based seeding. Perturbation may reset the RNG afterwards.')
    accs: bool = Field(default=False, description='Native accession label parsing. The adapter still supplies opaque row IDs and retains the original row mapping.')
    randomchaintree: bool = Field(default=False, description='MPC random-chain guide tree; native tree writer is bypassed in this branch.')
    native_log: bool = Field(default=False, description='Retain MUSCLE internal -log output separately as muscle.native.log; engine.log always retains stdout/stderr.')
    threads: int = Field(default=1, ge=1)
    permutation: Literal['none', 'abc', 'acb', 'bca', 'all'] | None = Field(default=None, description='Null omits -perm (native single-run default none). Explicit none is passed even with an ensemble; native incompatibilities are not silently repaired.')
    perturb: int | None = Field(default=None, ge=0, description='Null omits -perturb (native single-run default 0). Explicit zero is passed even with an ensemble.')
    ensemble: Literal['none', 'stratified', 'diversified'] = 'none'
    replicates: int | None = Field(default=None, ge=1)
    consiters: int | None = Field(default=None, ge=0, description='Native align consistency iterations; not consumed by Super5.')
    refineiters: int | None = Field(default=None, ge=0, description='Native align refinement iterations; not consumed by Super5.')
    minsuper: int | None = Field(default=None, ge=0, description='Explicit native align-to-Super5 sequence-count threshold; null keeps the requested align algorithm.')
    output_order: Literal['input_order', 'tree_order'] | None = Field(default=None, description='Native output row order.')
    quiet: bool = False
    verbose: bool = False
    m_is: float | None = Field(default=None, description='Pair-HMM match to short insertion transition; native normalization applies.')
    m_il: float | None = Field(default=None, description='Pair-HMM match to long insertion transition; native normalization applies.')
    is_is: float | None = Field(default=None, description='Pair-HMM short insertion self-transition; native normalization applies.')
    il_il: float | None = Field(default=None, description='Pair-HMM long insertion self-transition; native normalization applies.')
    s_is: float | None = Field(default=None, description='Pair-HMM start to short insertion transition; native normalization applies.')
    s_il: float | None = Field(default=None, description='Pair-HMM start to long insertion transition; native normalization applies.')

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
    length_deviation: float | None = None
    max_ambig: float | None = None
    max_len: int | None = None
    min_len: int | None = None
    write_filtered: bool = False
    write_prune: bool = False
    check: bool = False
    verbose: bool = False
    match: float = 18
    mismatch: float = -8
    transition: float = -4
    gap_open: float | None = None
    gap_extend: float | None = None
    gap_ends: float | None = None
    scale: float | None = None
    xdrop: float = 600
    matrix: str | None = Field(default=None, description='Exact TWILIGHT nucleotide matrix text: A C G T header and 4x4 scores (optional N header and 5x5 scores). Native values are not rescaled. Retained as matrix.txt.', json_schema_extra={'format': 'twilight-nucleotide-matrix'})
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
