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
        return resolved ? `Strategy default (${resolved})` : 'Strategy default (not passed)'
    }
    return 'Engine default (not passed)'
}
