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
    pair_method: Literal['6merpair', '10merpair', 'hybridpair', 'localgenafpair', 'oldgenafpair', 'shortlongpair', 'longshortpair'] | None = Field(default=None, description='Pair method. Overrides the strategy pair method; blank keeps the strategy setting. 10merpair needs DNA/RNA. hybridpair can fail on protein input.')
    tree_method: Literal['large', 'memsavetree', 'memsavetreex', 'stepadd', 'youngestlinkage', 'pileup', 'randomchain'] | None = Field(default=None, description='Guide-tree method. Blank uses the strategy setting.')
    refinement_policy: Literal['bestfirst', 'adhoc0', 'adhoc1', 'adhoc2', 'simplehillclimbing'] | None = Field(default=None, description='Parallel refinement method.')
    alga: bool = False
    allowshift: bool = Field(default=False, description='Shift-aware scoring. Needs the G-INS-i (globalpair) strategy.')
    anchors: str | None = Field(default=None, description='Anchor table: 7 numbers per line (i j start_i end_i start_j end_j score), 1-based. i and j are sequence numbers in input order; i comes before j.', json_schema_extra={'format': 'mafft-anchors'})
    bunkatsunashi: bool = False
    focus: bool = Field(default=False, description='Focus the alignment on marked sequences. Use Focus rows to mark them.')
    focus_rows: str | None = Field(default=None, description='Row IDs to focus on, one per line. Unknown IDs are ignored.')
    gep: float | None = Field(default=None, description='Global pairwise offset (--gep).')
    gexp: float | None = Field(default=None, description='Global pairwise gap extension (--gexp).')
    gop: float | None = Field(default=None, description='Global pairwise gap opening (--gop).')
    kappa: float | None = None
    leavegappyregion: bool = Field(default=False, description='Same as legacygappenalty.')
    lhlimit: float | None = Field(default=None, description='Local-homology limit for memory-saving tree methods.')
    minimumweight: float | None = None
    noscore: bool = False
    oneiteration: bool = False
    shiftpenalty: float | None = None
    similaritylevel: float | None = None
    skipiterate: float | None = None
    sp: bool = Field(default=False, description='Rigorous sum-of-pairs scoring. Refinement needs Iteration threads = 0.')
    sparsepickup: int | None = Field(default=None, description='Sparse pickup. Some rows may be left out; those are listed in a separate file.')
    terminalmargin: int | None = None
    unalignlevel: float | None = None
    nodeout: bool = False
    distance_output: Literal['distout', 'distoutclustalo'] | None = None
    scoreout: bool = Field(default=False, description='Write alignment scores to the engine log. Can fail with L-INS-i.')
    op: float | None = None
    ep: float | None = None
    weighti: float | None = Field(default=None, description='Pairwise consistency weight; applies to iterative pairwise strategies.')
    lop: float | None = Field(default=None, description='Local pairwise gap opening penalty.')
    lep: float | None = Field(default=None, description='Local pairwise offset.')
    lexp: float | None = Field(default=None, description='Local pairwise gap extension penalty.')
    long_gap_open: float | None = Field(default=None, description='E-INS-i skip penalty (--LOP).')
    long_gap_extend: float | None = Field(default=None, description='E-INS-i skip extension penalty (--LEXP).')
    bl: Literal[30, 45, 62, 80] | None = Field(default=None, description='Protein BLOSUM matrix number.')
    jtt: int | None = Field(default=None, description='Protein JTT PAM distance.')
    tm: int | None = Field(default=None, description='Protein transmembrane PAM distance.')
    kimura: int | None = Field(default=None, description='Nucleotide transition/transversion parameter.')
    randomseed: int | None = Field(default=None, description='Random seed, where MAFFT uses one.')
    nofft: bool = False
    fft: bool = Field(default=False, description='Force FFT; compiled before nofft if both are selected.')
    threadit: int | None = Field(default=None, description='Threads for refinement. 0 turns off threaded refinement; -1 is automatic.')
    threadtb: int | None = Field(default=None, description='Threads for the progressive step. -1 is automatic.')
    parttree: Literal['parttree', 'dpparttree'] | None = Field(default=None, description='PartTree distance mode.')
    partsize: int | None = None
    groupsize: int | None = None
    linkage: Literal['minimumlinkage', 'averagelinkage'] | None = None
    mixedlinkage: float | None = Field(default=None, description='Mixed linkage coefficient; compiled after linkage when both are supplied.')
    unweight: bool = False
    termgappenalty: bool = False
    legacygappenalty: bool = False
    anysymbol: bool = False
    preservecase: bool = False
    direction: Literal['adjustdirection', 'adjustdirectionaccurately'] | None = Field(default=None, description='Reverse-complement sequences when needed (DNA/RNA).')
    memsave: Literal['memsave', 'nomemsave'] | None = None
    fmodel: bool = False
    nucleotide_scoring: Literal['nwildcard', 'nzero'] | None = None
    opdist: float | None = None
    exp: float | None = None
    treein: str | None = Field(default=None, description='MAFFT merge table (not Newick): one merge per line, 1-based sequence numbers and branch lengths.', json_schema_extra={'format': 'mafft-merge-table'})
    aamatrix: str | None = Field(default=None, description='Custom amino-acid scoring matrix. Overrides BLOSUM, JTT and transmembrane settings.', json_schema_extra={'format': 'mafft-amino-matrix'})
    retree: int | None = Field(default=None, ge=0, description='Override the strategy guide-tree rebuild count.')
    maxiterate: int | None = Field(default=None, ge=0, description='Override the strategy refinement iteration count.')
    reorder: bool = False
    quiet: bool = False

class FamsaSettings(Closed):
    engine: Literal['famsa'] = 'famsa'
    threads: int = Field(default=1, ge=1)
    guide_tree: Literal['sl', 'upgma', 'nj', 'import'] = 'sl'
    guide_tree_newick: str | None = Field(default=None, description='Newick guide tree, used when Guide tree is set to import. Use Leaf mapping to match leaves to rows.', json_schema_extra={'format': 'newick'})
    leaf_mapping: dict[str, str] = Field(default_factory=dict, description='Matches tree leaf names to row IDs. Unmatched leaves are reported, not guessed.')
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
    guidetreeout: bool = Field(default=False, description='Write the guide tree only. MUSCLE stops before aligning, so no alignment is produced. Ignored with Random-chain tree.')
    hmmin: str | None = Field(default=None, description='MUSCLE HMM parameter file.', json_schema_extra={'format': 'muscle-hmm'})
    hmmout: bool = Field(default=False, description='Write the HMM parameters to a file (Super5 only).')
    anchor_letter: str | None = Field(default=None, min_length=1, max_length=1, description='One-character HMM anchor (case-sensitive).')
    randseed: int | None = Field(default=None, description='Random seed. 0 picks a seed from the clock.')
    accs: bool = Field(default=False, description='Parse accession labels from headers.')
    randomchaintree: bool = Field(default=False, description='Use a random-chain guide tree.')
    native_log: bool = Field(default=False, description='Also save the MUSCLE log file.')
    threads: int = Field(default=1, ge=1)
    permutation: Literal['none', 'abc', 'acb', 'bca', 'all'] | None = Field(default=None, description='Guide-tree permutation. Blank uses the MUSCLE default. "none" cannot be combined with ensembles.')
    perturb: int | None = Field(default=None, ge=0, description='Perturbation seed. Blank uses the MUSCLE default. 0 cannot be combined with ensembles.')
    ensemble: Literal['none', 'stratified', 'diversified'] = 'none'
    replicates: int | None = Field(default=None, ge=1)
    consiters: int | None = Field(default=None, ge=0, description='Consistency iterations (not used by Super5).')
    refineiters: int | None = Field(default=None, ge=0, description='Refinement iterations (not used by Super5).')
    minsuper: int | None = Field(default=None, ge=0, description='Switch to Super5 above this many sequences. Blank keeps the chosen algorithm.')
    output_order: Literal['input_order', 'tree_order'] | None = Field(default=None, description='Output row order.')
    quiet: bool = False
    verbose: bool = False
    m_is: float | None = Field(default=None, description='Pair-HMM match to short insertion transition.')
    m_il: float | None = Field(default=None, description='Pair-HMM match to long insertion transition.')
    is_is: float | None = Field(default=None, description='Pair-HMM short insertion self-transition.')
    il_il: float | None = Field(default=None, description='Pair-HMM long insertion self-transition.')
    s_is: float | None = Field(default=None, description='Pair-HMM start to short insertion transition.')
    s_il: float | None = Field(default=None, description='Pair-HMM start to long insertion transition.')

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
    matrix: str | None = Field(default=None, description='Nucleotide score matrix: A C G T header with 4×4 scores (or 5×5 with N).', json_schema_extra={'format': 'twilight-nucleotide-matrix'})
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

    @model_validator(mode='after')
    def engine_matches_molecule(self):
        if self.settings.engine == 'famsa' and self.roster.molecule != 'protein':
            raise ValueError('FAMSA supports protein alignments only')
        if self.settings.engine == 'twilight' and self.roster.molecule == 'protein':
            raise ValueError('TWILIGHT supports DNA/RNA alignments only')
        return self
