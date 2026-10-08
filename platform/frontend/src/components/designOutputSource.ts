export type OutputSourceFilter = 'all' | 'rfantibody' | 'fampnn' | 'ppiflow' | 'validation';
export type AnalysisLens = 'validation' | 'rfantibody' | 'fampnn' | 'ppiflow' | 'frustrampnn' | 'protenix';

type OutputSourceDesign = {
    pdb_path?: string | null;
    confidence_metrics?: Record<string, unknown> | null;
    source_stage?: string | null;
    artifact_group?: string | null;
    stage_family?: string | null;
    stage_mode?: string | null;
    source_stage_family?: string | null;
    source_stage_mode?: string | null;
};

type AnalysisLensDesign = OutputSourceDesign & Record<string, unknown>;

type AnalysisLensJob = {
    name?: string | null;
    model_id?: string | null;
    mode?: string | null;
    params?: Record<string, unknown> | null;
    current_stage?: string | null;
    awaiting_stage?: string | null;
    awaiting_payload?: Record<string, unknown> | null;
};

const ANALYSIS_LENS_PRIORITY: AnalysisLens[] = ['rfantibody', 'fampnn', 'ppiflow', 'frustrampnn', 'protenix', 'validation'];

const containsAny = (value: string, needles: string[]): boolean => needles.some((needle) => value.includes(needle));

const hasMetricKeys = (design: AnalysisLensDesign, keys: string[]): boolean => {
    const metrics = design.confidence_metrics;
    return keys.some((key) => {
        const direct = design[key];
        if (typeof direct === 'number' && Number.isFinite(direct)) return true;
        if (typeof direct === 'boolean') return true;
        const nested = metrics?.[key];
        if (typeof nested === 'number' && Number.isFinite(nested)) return true;
        if (typeof nested === 'boolean') return true;
        return false;
    });
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
    const sourceStage = String(design.source_stage || '').toLowerCase();
    const stageFamily = String(design.stage_family || '').toLowerCase();
    const stageMode = String(design.stage_mode || '').toLowerCase();
    const artifactGroup = String(design.artifact_group || '').toLowerCase();
    const metrics = design.confidence_metrics || {};

    if (containsAny(stageFamily, ['rfantibody']) || containsAny(stageMode, ['rfantibody', 'post_rfantibody'])) {
        return 'rfantibody';
    }

    if (containsAny(stageFamily, ['fampnn']) || containsAny(stageMode, ['fampnn', 'post_fampnn'])) {
        return 'fampnn';
    }

    if (containsAny(stageFamily, ['ppiflow', 'maturation']) || containsAny(stageMode, ['ppiflow', 'maturation', 'backbone_refine'])) {
        return 'ppiflow';
    }

    if (containsAny(stageFamily, ['validation', 'protenix', 'boltz']) || containsAny(stageMode, ['validation', 'post_structure_validation'])) {
        return 'validation';
    }

    if (sourceStage === 'post_rfantibody' || containsAny(artifactGroup, ['raw', 'filtered'])) {
        return 'rfantibody';
    }

    if (sourceStage === 'post_fampnn' || artifactGroup === 'candidate') {
        return 'fampnn';
    }

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
        path.includes('/collected/backbone_refine/') ||
        path.includes('/run/ppiflow/results/') ||
        path.includes('/run/ppiflow/') ||
        path.includes('/ppiflow_backbone/') ||
        path.includes('/ppiflow_maturation/') ||
        path.includes('/ppiflow_repair/') ||
        path.includes('/maturation/')
    ) {
        return 'ppiflow';
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

    if (hasMetricKeys(design as AnalysisLensDesign, ['backbone_id', 'epitope_contact_count', 'target_contact_count', 'rfd_rog'])) {
        return 'rfantibody';
    }

    if (hasMetricKeys(design as AnalysisLensDesign, ['fampnn_psce', 'mpnn_score'])) {
        return 'fampnn';
    }

    if (hasMetricKeys(design as AnalysisLensDesign, ['maturation_delta_interface', 'maturation_interface_score', 'maturation_rmsd'])) {
        return 'ppiflow';
    }

    if (hasValidationMetrics(metrics)) return 'validation';

    return 'all';
};

export const inferDesignAnalysisLens = (design: AnalysisLensDesign): AnalysisLens | null => {
    const path = (design.pdb_path || '').toLowerCase();

    if (
        containsAny(path, ['/ppiflow/', '/ppiflow_maturation/', '/ppiflow_backbone/', '/ppiflow_repair/', '/maturation/']) ||
        hasMetricKeys(design, ['maturation_delta_interface', 'maturation_interface_score', 'maturation_rmsd'])
    ) {
        return 'ppiflow';
    }

    if (
        path.includes('/frustrampnn/') ||
        hasMetricKeys(design, ['frustration_high_count', 'frustration_min_count', 'frustration_pct_high'])
    ) {
        return 'frustrampnn';
    }

    if (
        containsAny(path, ['/protenix/', '/run/protenix/']) ||
        hasMetricKeys(design, ['protein_iptm', 'ligand_iptm', 'complex_iplddt', 'complex_ipde', 'disorder', 'num_recycles', 'has_clash'])
    ) {
        return 'protenix';
    }

    const source = inferDesignOutputSource(design);
    if (source === 'rfantibody') return 'rfantibody';
    if (source === 'fampnn') return 'fampnn';
    if (source === 'ppiflow') return 'ppiflow';
    if (source === 'validation') return 'validation';

    return null;
};

const inferJobAnalysisLens = (job: AnalysisLensJob | null | undefined): AnalysisLens | null => {
    if (!job) return null;

    const stage = String(job.awaiting_stage || job.current_stage || '').toLowerCase();
    const name = String(job.name || '').toLowerCase();
    const modelId = String(job.model_id || '').toLowerCase();
    const mode = String(job.mode || '').toLowerCase();
    const candidateDir = String(job.awaiting_payload?.candidate_dir || '').toLowerCase();
    const params = job.params ?? {};
    const validator = String(params.structure_validator || '').toLowerCase();

    if (
        stage === 'post_rfantibody' ||
        containsAny(stage, ['rfantibody']) ||
        containsAny(candidateDir, ['rfantibody']) ||
        containsAny(name, ['rfantibody'])
    ) {
        return 'rfantibody';
    }

    if (
        stage === 'post_fampnn' ||
        containsAny(modelId, ['fampnn']) ||
        containsAny(mode, ['fampnn']) ||
        containsAny(stage, ['fampnn']) ||
        containsAny(candidateDir, ['fampnn', 'fa-mpnn']) ||
        containsAny(name, ['fampnn_redesign', 'fampnn', 'fa-mpnn'])
    ) {
        return 'fampnn';
    }

    if (
        containsAny(stage, ['ppiflow', 'maturation']) ||
        containsAny(name, ['ppiflow', 'maturation']) ||
        containsAny(candidateDir, ['ppiflow', 'maturation']) ||
        params.run_ppiflow_backbone_refine === true ||
        params.run_ppiflow_maturation === true ||
        params.run_post_validation_maturation === true ||
        params.run_post_boltz_maturation === true ||
        params.run_maturation === true
    ) {
        return 'ppiflow';
    }

    if (
        containsAny(stage, ['frustrampnn']) ||
        containsAny(name, ['frustrampnn']) ||
        containsAny(candidateDir, ['frustrampnn']) ||
        params.run_frustrampnn === true
    ) {
        return 'frustrampnn';
    }

    if (
        validator === 'protenix' ||
        containsAny(name, ['validate_protenix', 'protenix']) ||
        containsAny(stage, ['protenix']) ||
        containsAny(candidateDir, ['protenix'])
    ) {
        return 'protenix';
    }

    if (
        stage === 'post_structure_validation' ||
        containsAny(stage, ['validation', 'boltz']) ||
        containsAny(candidateDir, ['structure_validation', 'validated_designs', 'boltz']) ||
        params.run_structure_validation === true ||
        modelId.includes('boltz') ||
        mode.includes('validation')
    ) {
        return 'validation';
    }

    return null;
};

export const inferPreferredAnalysisLens = (
    job: AnalysisLensJob | null | undefined,
    designs: AnalysisLensDesign[] = [],
): AnalysisLens | null => {
    const jobLens = inferJobAnalysisLens(job);
    if (jobLens) return jobLens;

    const counts = new Map<AnalysisLens, number>();
    for (const design of designs) {
        const lens = inferDesignAnalysisLens(design);
        if (!lens) continue;
        counts.set(lens, (counts.get(lens) || 0) + 1);
    }

    let bestLens: AnalysisLens | null = null;
    let bestCount = 0;
    for (const lens of ANALYSIS_LENS_PRIORITY) {
        const count = counts.get(lens) || 0;
        if (count > bestCount) {
            bestLens = lens;
            bestCount = count;
        }
    }

    return bestLens;
};

export const getOutputSourceLabel = (design: OutputSourceDesign): string => {
    const source = inferDesignOutputSource(design);
    if (source === 'validation') {
        return hasValidationMetrics(design.confidence_metrics || null) ? 'Protenix' : 'Validation';
    }
    if (source === 'ppiflow') return 'PPIFlow';
    if (source === 'fampnn') return 'FAMPNN';
    if (source === 'rfantibody') return 'RFantibody';
    return 'Other';
};

export const getOutputSourceBadgeClass = (source: OutputSourceFilter): string => {
    if (source === 'rfantibody') return 'border-violet-500/40 bg-violet-500/10 text-violet-200';
    if (source === 'fampnn') return 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200';
    if (source === 'ppiflow') return 'border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-200';
    if (source === 'validation') return 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200';
    return 'border-slate-600/40 bg-slate-700/30 text-slate-300';
};
