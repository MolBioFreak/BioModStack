// Operator-facing presentation of the closed native settings schema. This
// changes only labels, grouping and option text; keys, values, defaults and
// the submitted request remain exactly those of the request schema.

export type EngineId = 'mafft' | 'famsa' | 'muscle' | 'twilight'

export const engineLabels: Record<EngineId, string> = {
    mafft: 'MAFFT 7.525',
    famsa: 'FAMSA 2.5.2',
    muscle: 'MUSCLE 5.3',
    twilight: 'TWILIGHT 0.3.0',
}

/** Settings shown before the collapsed expert section. */
export const primaryFields: Record<EngineId, string[]> = {
    mafft: ['strategy', 'maxiterate', 'retree', 'op', 'ep', 'bl', 'kimura', 'randomseed', 'reorder', 'treeout'],
    famsa: ['guide_tree', 'guide_tree_newick', 'leaf_mapping', 'refine_mode', 'keep_duplicates', 'sm', 'go', 'ge'],
    muscle: ['mode', 'ensemble', 'replicates', 'permutation', 'perturb', 'randseed', 'consiters', 'refineiters', 'output_order'],
    twilight: ['method', 'guide_source', 'guide_tree', 'leaf_mapping', 'acc_mode', 'match', 'mismatch', 'transition', 'gap_open', 'gap_extend', 'consistency_weight', 'remove_gappy'],
}

const proteinOnly = new Set(['bl', 'jtt', 'tm', 'aamatrix'])
const nucleotideOnly = new Set(['kimura'])

/** Primary settings for an engine and molecule. Settings for the other
 *  molecule type stay available under Expert settings; nothing is removed. */
export function primaryFieldsFor(engine: EngineId, molecule: string): Set<string> {
    const skip = molecule === 'protein' ? nucleotideOnly : proteinOnly
    return new Set(primaryFields[engine].filter(key => !(engine === 'mafft' && skip.has(key))))
}

const labels: Record<string, string> = {
    // MAFFT
    strategy: 'Strategy', treeout: 'Save guide tree', pair_method: 'Pair method', tree_method: 'Tree method',
    refinement_policy: 'Refinement policy', alga: 'Alternative gap algorithm', allowshift: 'Allow shifted gaps',
    anchors: 'Anchor table', bunkatsunashi: 'Disable segment splitting', focus: 'Focus consistency', focus_rows: 'Focus rows',
    gep: 'Global pair gap offset', gexp: 'Global pair gap extension', gop: 'Global pair gap opening', kappa: 'Kappa',
    leavegappyregion: 'Leave gappy regions', lhlimit: 'Local-homology limit', minimumweight: 'Minimum weight',
    noscore: 'Skip scoring', oneiteration: 'Single iteration', shiftpenalty: 'Shift penalty', similaritylevel: 'Similarity level',
    skipiterate: 'Skip iterations', sp: 'Sum-of-pairs scoring', sparsepickup: 'Sparse pickup', terminalmargin: 'Terminal margin',
    unalignlevel: 'Unalign level', nodeout: 'Write node output', distance_output: 'Distance output', scoreout: 'Score diagnostics',
    op: 'Gap opening penalty', ep: 'Gap offset', weighti: 'Consistency weight', lop: 'Local gap opening', lep: 'Local gap offset',
    lexp: 'Local gap extension', long_gap_open: 'Long gap opening', long_gap_extend: 'Long gap extension',
    bl: 'BLOSUM matrix', jtt: 'JTT PAM distance', tm: 'Transmembrane PAM distance', kimura: 'Kimura parameter',
    randomseed: 'Random seed', nofft: 'Disable FFT', fft: 'Force FFT', threadit: 'Refinement threads', threadtb: 'Progressive threads',
    parttree: 'PartTree mode', partsize: 'Partition size', groupsize: 'Group size', linkage: 'Linkage', mixedlinkage: 'Mixed linkage',
    unweight: 'Unweighted', termgappenalty: 'Terminal gap penalty', legacygappenalty: 'Legacy gap penalty', anysymbol: 'Allow any symbol',
    preservecase: 'Preserve case', direction: 'Direction adjustment', memsave: 'Memory saving', fmodel: 'F-model',
    nucleotide_scoring: 'N scoring', opdist: 'Distance gap opening', exp: 'Gap extension', treein: 'Guide merge table',
    aamatrix: 'Amino-acid matrix', retree: 'Guide-tree rebuilds', maxiterate: 'Refinement iterations', reorder: 'Reorder output',
    quiet: 'Quiet', verbose: 'Verbose',
    // FAMSA
    guide_tree: 'Guide tree', guide_tree_newick: 'Guide tree (Newick)', leaf_mapping: 'Leaf mapping', refine_mode: 'Refinement',
    keep_duplicates: 'Keep duplicates', medoidtree: 'Medoid tree', medoid_seeds: 'Medoid seeds', medoid_threshold: 'Medoid threshold',
    trim_columns: 'Trim columns', r: 'Refinement iterations', sm: 'Substitution matrix', go: 'Gap opening', ge: 'Gap extension',
    tgo: 'Terminal gap opening', tge: 'Terminal gap extension', dist: 'Distance measure', gsd: 'Gap scaling divisor',
    gsl: 'Gap scaling log', dgr: 'Disable gap rescaling', dgo: 'Disable gap optimisation', dsp: 'Disable sum-of-pairs refinement',
    // MUSCLE
    mode: 'Mode', guidetreeout: 'Write guide tree only', hmmin: 'HMM parameters file', hmmout: 'Write HMM parameters',
    anchor_letter: 'Anchor letter', randseed: 'Random seed', accs: 'Parse accessions', randomchaintree: 'Random-chain guide tree',
    native_log: 'Native log', permutation: 'Guide-tree permutation', perturb: 'Perturbation seed', ensemble: 'Ensemble',
    replicates: 'Replicates', consiters: 'Consistency iterations', refineiters: 'Refinement iterations', minsuper: 'Super5 threshold',
    output_order: 'Output order', m_is: 'HMM match → short insertion', m_il: 'HMM match → long insertion',
    is_is: 'HMM short insertion extend', il_il: 'HMM long insertion extend', s_is: 'HMM start → short insertion', s_il: 'HMM start → long insertion',
    // TWILIGHT
    method: 'Method', acc_mode: 'Accurate mode', acc_max_group: 'Max group size', acc_sample_rate: 'Sample rate',
    acc_sample_top: 'Sample top', consistency_weight: 'Consistency weight', max_subtree: 'Max subtree', remove_gappy: 'Gappy column threshold',
    wildcard: 'Wildcard N', rooted: 'Rooted tree', prune: 'Prune tree', filter: 'Filter sequences', length_deviation: 'Length deviation',
    max_ambig: 'Max ambiguity', max_len: 'Max length', min_len: 'Min length', write_filtered: 'Write filtered', write_prune: 'Write pruned tree',
    check: 'Check alignment', match: 'Match score', mismatch: 'Mismatch score', transition: 'Transition score', gap_open: 'Gap opening',
    gap_extend: 'Gap extension', gap_ends: 'End gap penalty', scale: 'Score scale', xdrop: 'X-drop', matrix: 'Scoring matrix file',
    guide_source: 'Guide tree source',
}

export function fieldLabel(key: string): string {
    return labels[key] ?? key.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase())
}

const optionLabels: Record<string, Record<string, string>> = {
    strategy: {
        fft_ns_2: 'FFT-NS-2 — fast progressive',
        linsi: 'L-INS-i — accurate, local pairwise',
        ginsi: 'G-INS-i — accurate, global pairwise',
        einsi: 'E-INS-i — accurate, long unalignable regions',
        auto: 'Auto — MAFFT chooses from input size',
    },
    guide_tree: { sl: 'Single linkage', upgma: 'UPGMA', nj: 'Neighbour joining', import: 'Import supplied tree' },
    mode: { align: 'Align (PPP)', super5: 'Super5 (large inputs)' },
    method: { pairwise: 'Pairwise', tcs: 'TCS' },
    guide_source: { supplied: 'Supplied Newick tree', mafft: 'Generate with MAFFT FFT-NS-2' },
    ensemble: { none: 'None', stratified: 'Stratified', diversified: 'Diversified' },
    output_order: { input_order: 'Input order', tree_order: 'Guide-tree order' },
}

export function optionLabel(key: string, value: string | number): string {
    return optionLabels[key]?.[String(value)] ?? String(value)
}

const strategyControlled = new Set(['pair_method', 'tree_method', 'refinement_policy', 'retree', 'maxiterate'])

// Values the core compiler (bms_alignment/native.py) passes when these
// MAFFT fields are omitted; shown so "default" is never an unexplained value.
const strategyRetree: Record<string, string> = { fft_ns_2: '2' }
const strategyMaxiterate: Record<string, string> = { fft_ns_2: '0', linsi: '1000', ginsi: '1000', einsi: '1000' }
const strategyPair: Record<string, string> = { linsi: 'localpair', ginsi: 'globalpair', einsi: 'genafpair' }

/** Text for an omitted (null) value: the flag is not set by the operator. */
export function nullLabel(key: string, settings: Record<string, unknown> = {}): string {
    if (settings.engine === 'mafft' && strategyControlled.has(key)) {
        const strategy = String(settings.strategy ?? 'fft_ns_2')
        const resolved = key === 'retree' ? strategyRetree[strategy] : key === 'maxiterate' ? strategyMaxiterate[strategy] : key === 'pair_method' ? strategyPair[strategy] : undefined
        return resolved ? `Default (${resolved})` : 'Default'
    }
    return 'Default'
}


/** One-line, plain explainers for each engine, shown in the engine picker. */
export const engineHelp: Record<EngineId, string> = {
    mafft: 'Good default for DNA, RNA and protein. Fast on large sets, accurate options for small ones.',
    famsa: 'Protein only. Very fast for thousands of sequences.',
    muscle: 'Protein or DNA. Accurate for small to medium sets; can build several alternative alignments to check confidence.',
    twilight: 'DNA/RNA only. For large nucleotide sets; needs a guide tree (let MAFFT make one).',
}

// Plain explainers shown under each main setting. Keyed per engine because
// some keys (e.g. guide_tree) mean different things in different engines.
const help: Record<EngineId, Record<string, string>> = {
    mafft: {
        threads: 'CPU cores to use. More is faster.',
        strategy: 'How hard MAFFT works. Auto picks for you. FFT-NS-2 is fast for many sequences. L-INS-i is most accurate for under ~200 sequences with one shared region. G-INS-i suits full-length sequences of similar length (e.g. plasmid variants). E-INS-i suits sequences with long unrelated stretches.',
        maxiterate: 'Extra polishing passes after the first alignment. 0 is fastest; ~1000 is the accurate setting. Blank uses the strategy default.',
        retree: 'How many times the guide tree is rebuilt. 2 is the usual value.',
        op: 'Cost of starting a gap. Higher means fewer, longer gaps. Blank is MAFFT\'s default (1.53).',
        ep: 'Small bonus that lets weakly similar regions line up. Usually leave blank (0.0).',
        bl: 'Protein scoring table. 62 is the standard; 80 for close relatives, 30-45 for distant ones.',
        kimura: 'DNA/RNA scoring. 1 for distant sequences, 2 (default) for close ones.',
        randomseed: 'Fix this to get the same result when you rerun.',
        reorder: 'Group similar sequences together in the output instead of keeping your order.',
        treeout: 'Also save the guide tree MAFFT built.',
    },
    famsa: {
        threads: 'CPU cores to use. More is faster.',
        guide_tree: 'How sequences are grouped before aligning. Single linkage (default) is fastest; UPGMA or neighbour joining can be slightly more accurate. Import uses your own tree.',
        guide_tree_newick: 'Your own guide tree, in Newick format. Only used when Guide tree is Import.',
        leaf_mapping: 'Only needed if tree leaf names differ from your sequence names.',
        refine_mode: 'Extra polishing after aligning. Auto turns it on for smaller sets.',
        keep_duplicates: 'Keep identical sequences as separate rows instead of merging them.',
        sm: 'Protein scoring table. Blank uses FAMSA\'s default (MIQS).',
        go: 'Cost of starting a gap. Higher means fewer gaps.',
        ge: 'Cost of making a gap longer.',
    },
    muscle: {
        threads: 'CPU cores to use. More is faster.',
        mode: 'Align is accurate and suits up to a few hundred sequences. Super5 is for thousands.',
        ensemble: 'Build several alternative alignments to see which columns are reliable. None gives one alignment.',
        replicates: 'How many alternative alignments to build when Ensemble is on.',
        permutation: 'Variations of the guide tree used for an ensemble. Usually leave blank.',
        perturb: 'Seed for small random changes used in an ensemble. Usually leave blank.',
        randseed: 'Fix this to get the same result when you rerun.',
        consiters: 'Consistency passes; more can improve accuracy and take longer.',
        refineiters: 'Polishing passes after aligning.',
        output_order: 'Keep your input order, or group similar sequences together.',
    },
    twilight: {
        threads: 'CPU cores to use. More is faster.',
        method: 'Pairwise is the normal method. TCS adds a consistency check and is slower.',
        guide_source: 'TWILIGHT needs a guide tree. Choose "Generate with MAFFT" unless you have your own.',
        guide_tree: 'Your own guide tree, in Newick format. Only used when the tree is supplied.',
        leaf_mapping: 'Only needed if tree leaf names differ from your sequence names.',
        acc_mode: 'Higher numbers are more accurate and slower. 0 is the default.',
        match: 'Score for matching bases. Leave as is unless you know you need to change it.',
        mismatch: 'Penalty for different bases.',
        transition: 'Penalty for A↔G or C↔T changes, which are more common than other changes.',
        gap_open: 'Cost of starting a gap.',
        gap_extend: 'Cost of making a gap longer.',
        consistency_weight: 'How much the TCS consistency check counts.',
        remove_gappy: 'Columns that are at least this fraction gaps are removed from the output.',
    },
}

export function fieldHelp(engine: EngineId, key: string): string | undefined {
    return help[engine]?.[key]
}

/** Standard settings for a set of plasmid sequences: MAFFT chooses its own
 *  strategy for the input size, and output keeps the order you picked. */
export const plasmidPreset = { engine: 'mafft' as const, strategy: 'auto', reorder: false }
