export type OutputSourceFilter = 'all' | 'rfantibody' | 'fampnn' | 'validation';

type OutputSourceDesign = {
    pdb_path?: string | null;
    confidence_metrics?: Record<string, unknown> | null;
};

const hasValidationMetrics = (metrics: Record<string, unknown> | null | undefined): boolean => {
    if (!metrics || typeof metrics !== 'object') return false;
    return (
        'ranking_score' in metrics ||
        'gpde' in metrics ||
        'chain_pair_iptm' in metrics ||
        'protenix_target_rmsd' in metrics ||
        'rmsd_target' in metrics
    );
};

export const inferDesignOutputSource = (design: OutputSourceDesign): OutputSourceFilter => {
    const path = (design.pdb_path || '').toLowerCase();
    const metrics = design.confidence_metrics || {};

    if (
        path.includes('/validated_designs/') ||
        path.includes('/collected/structure_validation/') ||
        path.includes('/run/protenix/') ||
        path.includes('/run/boltz/')
    ) {
        return 'validation';
    }

    if (
        path.includes('/collected/fampnn/') ||
        path.includes('/collected/fampnn_filtered/') ||
        path.includes('/fampnn_filtered/') ||
        path.includes('/run/fampnn/results/')
    ) {
        return 'fampnn';
    }

    if (
        path.includes('/collected/rfantibody/') ||
        path.includes('/collected/rfantibody_raw/') ||
        path.includes('/collected/rfantibody_filtered/') ||
        path.includes('/run/rfantibody/') ||
        path.includes('/rfantibody/')
    ) {
        return 'rfantibody';
    }

    if (hasValidationMetrics(metrics)) return 'validation';

    return 'all';
};

export const getOutputSourceLabel = (design: OutputSourceDesign): string => {
    const source = inferDesignOutputSource(design);
    if (source === 'validation') {
        return hasValidationMetrics(design.confidence_metrics || null) ? 'Protenix' : 'Validation';
    }
    if (source === 'fampnn') return 'FAMPNN';
    if (source === 'rfantibody') return 'RFantibody';
    return 'Other';
};

export const getOutputSourceBadgeClass = (source: OutputSourceFilter): string => {
    if (source === 'rfantibody') return 'border-violet-500/40 bg-violet-500/10 text-violet-200';
    if (source === 'fampnn') return 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200';
    if (source === 'validation') return 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200';
    return 'border-slate-600/40 bg-slate-700/30 text-slate-300';
};
