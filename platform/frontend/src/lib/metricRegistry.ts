export type MetricDirection = 'higher_is_better' | 'lower_is_better' | 'more_negative_is_better' | 'informational';

export interface MetricDescriptor {
    key: string;
    aliases?: string[];
    label: string;
    group: string;
    unit?: string;
    direction: MetricDirection;
    source: string;
    caveat: string;
    recommendedUse: string;
}

export interface MetricCompletenessInput {
    metric_completeness?: {
        overall_status?: string;
        status?: string;
        missing?: string[];
    } | null;
}

export interface MetricCompletenessStatus {
    status: 'complete' | 'partial' | 'unknown';
    label: string;
    missing: string[];
}

export const METRIC_REGISTRY: MetricDescriptor[] = [
    {
        key: 'fampnn_psce',
        aliases: ['fampnn_avg_psce'],
        label: 'FA-MPNN avg pSCE',
        group: 'FA-MPNN sidechain confidence',
        unit: 'Å',
        direction: 'lower_is_better',
        source: 'fampnn_output_pdb_bfactor',
        caveat: 'Predicted sidechain confidence/error; not binding evidence and not a complete FA-MPNN sequence-rank metric.',
        recommendedUse: 'QC gate',
    },
    {
        key: 'fampnn_mean_sampled_log_prob',
        label: 'FA-MPNN sampled-sequence log-prob',
        group: 'FA-MPNN sequence confidence',
        direction: 'higher_is_better',
        source: 'fampnn_sample_pkl_seq_probs',
        caveat: 'Computed from upstream FA-MPNN seq_probs; complements pSCE sequence-design review.',
        recommendedUse: 'Sequence confidence review',
    },
    {
        key: 'fampnn_mean_entropy',
        label: 'FA-MPNN sequence entropy',
        group: 'FA-MPNN sequence confidence',
        direction: 'lower_is_better',
        source: 'fampnn_sample_pkl_seq_probs',
        caveat: 'High entropy marks uncertain residue choices, especially important in CDR/interface positions.',
        recommendedUse: 'Uncertainty hotspot review',
    },
    {
        key: 'fampnn_mutation_log_odds_delta',
        aliases: ['fampnn_top_model_favored_mutations', 'fampnn_mutation_score'],
        label: 'FA-MPNN mutation log-odds delta',
        group: 'FA-MPNN mutation scoring',
        direction: 'higher_is_better',
        source: 'fampnn_sample_pkl_seq_probs',
        caveat: 'Single-substitution likelihood delta computed from FA-MPNN seq_probs for the sampled design; positive values mark model-favored alternatives, not experimental stability or binding truth.',
        recommendedUse: 'Manual mutagenesis triage',
    },
    {
        key: 'ppiflow_objective_score',
        aliases: ['bms_ppiflow_local_objective_score'],
        label: 'BMS local PPIFlow objective',
        group: 'PPIFlow local maturation',
        direction: 'lower_is_better',
        source: 'ppiflow_local_score_json',
        caveat: 'BMS-local pair-energy/geometry refinement heuristic; not upstream PPIFlow paper final rank.',
        recommendedUse: 'Local triage only',
    },
    {
        key: 'ppiflow_paper_rank_score',
        label: 'PPIFlow paper-style composite rank',
        group: 'Final rank',
        direction: 'higher_is_better',
        source: 'validator_confidence_plus_rosetta_interface',
        caveat: 'Only available when validator iPTM and Rosetta interface score are present and formula/sign convention are recorded.',
        recommendedUse: 'Paper-style rerank',
    },
    {
        key: 'rosetta_interface_score',
        aliases: ['rosetta_interface_dg'],
        label: 'Rosetta interface score',
        group: 'Rosetta/interface energetics',
        unit: 'REU',
        direction: 'more_negative_is_better',
        source: 'rosetta_interface_analyzer',
        caveat: 'Raw Rosetta InterfaceAnalyzerMover dG; more negative is better. Keep sign convention explicit before composite ranking.',
        recommendedUse: 'Interface energetics and composite rerank input',
    },
];

const DESCRIPTOR_BY_KEY = new Map<string, MetricDescriptor>();
for (const descriptor of METRIC_REGISTRY) {
    DESCRIPTOR_BY_KEY.set(descriptor.key, descriptor);
    for (const alias of descriptor.aliases || []) {
        DESCRIPTOR_BY_KEY.set(alias, descriptor);
    }
}

export function getMetricDescriptor(key: string): MetricDescriptor | null {
    return DESCRIPTOR_BY_KEY.get(key) || null;
}

export function getMetricDisplayLabel(key: string): string {
    return getMetricDescriptor(key)?.label || key;
}

export function getMetricTooltip(key: string): string {
    const descriptor = getMetricDescriptor(key);
    if (!descriptor) return key;
    const unit = descriptor.unit ? ` Unit: ${descriptor.unit}.` : '';
    return `${descriptor.label}. ${descriptor.caveat} Direction: ${descriptor.direction}.${unit} Source: ${descriptor.source}. Use: ${descriptor.recommendedUse}.`;
}

export function resolveDesignMetricCompletenessStatus(design: MetricCompletenessInput | null | undefined): MetricCompletenessStatus {
    const completeness = design?.metric_completeness;
    const rawStatus = completeness?.overall_status || completeness?.status;
    const missing = Array.isArray(completeness?.missing) ? completeness.missing : [];
    const status: MetricCompletenessStatus['status'] = rawStatus === 'complete'
        ? 'complete'
        : rawStatus === 'partial' || missing.length > 0
            ? 'partial'
            : 'unknown';
    return {
        status,
        label: status === 'unknown' ? 'Metric coverage: unknown' : `Metric coverage: ${status}`,
        missing,
    };
}
