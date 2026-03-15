import { useState, useEffect, useMemo, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';

import { buildFileDownloadUrl, buildFileStreamUrl, fetchJobs, fetchDesigns, fetchStructureAnalysis, fetchAntibodyData, fetchBackboneSummary, launchAntibodyIteration, launchManualMutagenesis, saveReviewFilterSet, deleteReviewFilterSet } from '../lib/api';
import type { AntibodyCdrIndelConfig, AntibodyIterationAction, Design, DesignFilters, DesignSortField, Job, ManualMutagenesisConfig, SavedReviewFilterSet as ApiSavedReviewFilterSet } from '../lib/api';
import {
    getOutputSourceBadgeClass,
    getOutputSourceLabel,
    inferDesignAnalysisLens,
    inferDesignOutputSource,
    inferPreferredAnalysisLens,
    type AnalysisLens,
    type OutputSourceFilter,
} from './designOutputSource';
import MolstarViewer from './MolstarViewer';
// FloatingViewer - unused, kept for reference
import { StabilityHeatmap } from './MetricCharts';
import { BatchComparePane } from './BatchComparePane';
import { DesignComparePane } from './DesignComparePane';
// ReferenceSelector and MetricOverlay - unused, kept for reference
import { AnalyticsDashboard } from './AnalyticsDashboard';
import StructureViewerPane from './StructureViewerPane';

// Tab definitions
const TABS = [
    { id: 'overview', label: 'Overview', icon: 'View' },
    { id: 'charts', label: 'Charts', icon: 'Chart' },
    { id: 'structure', label: 'Structure', icon: '3D' },
    { id: 'antibody', label: 'Binder Info', icon: 'Immune' },
    { id: 'table', label: 'Data Table', icon: 'List' },
    { id: 'compare_designs', label: 'Compare Designs', icon: 'Chart' },
    { id: 'compare', label: 'Compare Jobs', icon: 'Vs' },
] as const;

type TabId = typeof TABS[number]['id'];
const ANALYTICS_CHART_DESIGN_CAP = 1500;
const MAX_BULK_SELECTION_DESIGNS = 10000;
const SERVER_SORT_FIELDS = new Set<DesignSortField>([
    'name',
    'plddt',
    'plddt_overall',
    'plddt_binder',
    'plddt_target',
    'iptm',
    'ptm',
    'pae',
    'pae_overall',
    'pae_interaction',
    'conf_score',
    'rog',
    'rfd_rog',
    'backbone',
    'backbone_id',
    'binder_length',
    'cdr_h1_length',
    'cdr_h2_length',
    'cdr_h3_length',
    'epitope_contact_count',
    'target_contact_count',
    'epitope_min_distance',
    'target_min_distance',
    'epitope_min_atom_distance',
    'target_min_atom_distance',
    'epitope_centroid_distance',
    'target_centroid_distance',
    'rfa_hotspot_covered_count',
    'rfa_hotspot_min_distance',
    'rfa_hotspot_avg_min_distance',
    'rfa_runtime_seconds',
    'rfa_plddt_final',
    'rfa_plddt_delta',
    'affinity_score',
    'binder_probability',
    'fampnn_psce',
    'frustration_high_count',
    'frustration_pct_high',
    'maturation_delta_interface',
    'maturation_rmsd',
    'fr2_contacts',
    'binding_tier',
    'is_favorite',
]);

const SORT_OPTIONS: Array<{ value: string; label: string }> = [
    { value: 'name', label: 'Name' },
    { value: 'backbone', label: 'Backbone' },
    { value: 'binding_tier', label: 'Binding Tier' },
    { value: 'plddt_overall', label: 'pLDDT' },
    { value: 'binder_length', label: 'Binder Size' },
    { value: 'cdr_h1_length', label: 'CDR-H1' },
    { value: 'cdr_h2_length', label: 'CDR-H2' },
    { value: 'cdr_h3_length', label: 'CDR-H3' },
    { value: 'epitope_contact_count', label: 'Epitope Contacts' },
    { value: 'target_contact_count', label: 'Any-Target Contacts' },
    { value: 'epitope_min_distance', label: 'Epitope Dist' },
    { value: 'target_min_distance', label: 'Any-Target Dist' },
    { value: 'epitope_centroid_distance', label: 'Epi Centroid' },
    { value: 'target_centroid_distance', label: 'Tgt Centroid' },
    { value: 'rfa_hotspot_covered_count', label: 'Hotspot Coverage' },
    { value: 'rfa_hotspot_min_distance', label: 'Hotspot Min Dist' },
    { value: 'rfa_hotspot_avg_min_distance', label: 'Hotspot Avg Dist' },
    { value: 'rfa_runtime_seconds', label: 'RF Runtime' },
    { value: 'rfa_plddt_final', label: 'RF pLDDT Final' },
    { value: 'rfa_plddt_delta', label: 'RF pLDDT Δ' },
    { value: 'affinity_score', label: 'Affinity' },
    { value: 'binder_probability', label: 'Binder %' },
    { value: 'fampnn_psce', label: 'pSCE' },
    { value: 'iptm', label: 'iPTM' },
    { value: 'ptm', label: 'pTM' },
    { value: 'pae_overall', label: 'PAE' },
    { value: 'rog', label: 'RoG' },
    { value: 'rfd_rog', label: 'RFD RoG' },
    { value: 'frustration_high_count', label: 'Frust High' },
    { value: 'frustration_pct_high', label: 'High Frust %' },
    { value: 'maturation_delta_interface', label: 'ΔIface' },
    { value: 'maturation_rmsd', label: 'Mat RMSD' },
    { value: 'is_favorite', label: 'Favorite' },
];

const ASCENDING_DEFAULT_SORT_FIELDS = new Set<string>([
    'name',
    'backbone',
    'backbone_id',
    'binder_length',
    'cdr_h1_length',
    'cdr_h2_length',
    'cdr_h3_length',
    'epitope_min_distance',
    'target_min_distance',
    'epitope_min_atom_distance',
    'target_min_atom_distance',
    'epitope_centroid_distance',
    'target_centroid_distance',
    'rfa_hotspot_min_distance',
    'rfa_hotspot_avg_min_distance',
    'rfa_runtime_seconds',
    'pae',
    'pae_overall',
    'pae_interaction',
    'rog',
    'rfd_rog',
    'maturation_rmsd',
]);

// Formatting helpers
const formatMetric = (val: number | null | undefined, decimals = 2): string =>
    val != null ? val.toFixed(decimals) : '—';

const formatMetricRange = (
    avg: number | null | undefined,
    min: number | null | undefined,
    max: number | null | undefined,
    decimals = 1,
    suffix = '',
): string => {
    if (avg == null && min == null && max == null) return '—';
    const averageLabel = avg != null ? `${avg.toFixed(decimals)}${suffix}` : '—';
    if (min == null && max == null) return averageLabel;
    const minLabel = min != null ? min.toFixed(decimals) : '—';
    const maxLabel = max != null ? max.toFixed(decimals) : '—';
    return `${averageLabel} (${minLabel}-${maxLabel}${suffix})`;
};

const getMetricColor = (metric: string, value: number | null): string => {
    if (value == null) return 'text-slate-500';
    if (metric === 'plddt_overall' || metric === 'plddt_binder' || metric === 'plddt_target') {
        if (value >= 90) return 'text-blue-400';
        if (value >= 70) return 'text-cyan-400';
        if (value >= 50) return 'text-yellow-400';
        return 'text-orange-400';
    }
    if (metric === 'pae_overall' || metric === 'pae_interaction') {
        return value <= 5 ? 'text-emerald-400' : value <= 10 ? 'text-amber-400' : 'text-red-400';
    }
    if (metric === 'ptm' || metric === 'conf_score') {
        return value >= 0.7 ? 'text-emerald-400' : value >= 0.5 ? 'text-amber-400' : 'text-red-400';
    }
    if (metric === 'fampnn_psce') {
        return value <= 0.2 ? 'text-emerald-400' : value <= 0.4 ? 'text-amber-400' : 'text-red-400';
    }
    return 'text-slate-300';
};

// Binding quality tier based on iPTM (interface predicted TM-score)
// A: Excellent binding confidence, B: Good, C: Moderate, D: Low/uncertain
const getBindingTier = (iptm: number | null | undefined, epitopeContacts: number | null | undefined): { tier: string; color: string; bgColor: string; label: string } => {
    if (iptm == null) return { tier: '—', color: 'text-slate-500', bgColor: 'bg-slate-700/50', label: 'No data' };

    // Bonus for epitope contacts (validates iPTM with physical proximity)
    const contactBonus = (epitopeContacts ?? 0) >= 5 ? 0.05 : 0;
    const adjustedIptm = iptm + contactBonus;

    if (adjustedIptm >= 0.75) return { tier: 'A', color: 'text-emerald-300', bgColor: 'bg-emerald-500/30 border-emerald-500/50', label: 'Excellent' };
    if (adjustedIptm >= 0.55) return { tier: 'B', color: 'text-blue-300', bgColor: 'bg-blue-500/30 border-blue-500/50', label: 'Good' };
    if (adjustedIptm >= 0.40) return { tier: 'C', color: 'text-amber-300', bgColor: 'bg-amber-500/30 border-amber-500/50', label: 'Moderate' };
    return { tier: 'D', color: 'text-red-300', bgColor: 'bg-red-500/30 border-red-500/50', label: 'Low' };
};

const getRfScreenStatus = (design: Pick<Design, 'passed_screen' | 'screening_reason'>): { label: string; color: string; bgColor: string; tooltip: string } => {
    if (design.passed_screen === true) {
        return {
            label: 'Pass',
            color: 'text-emerald-200',
            bgColor: 'bg-emerald-500/20 border-emerald-500/40',
            tooltip: design.screening_reason || 'Passed workflow screen',
        };
    }
    if (design.passed_screen === false) {
        return {
            label: 'Filtered',
            color: 'text-amber-200',
            bgColor: 'bg-amber-500/20 border-amber-500/40',
            tooltip: design.screening_reason || 'Filtered by workflow screen',
        };
    }
    return {
        label: '—',
        color: 'text-slate-400',
        bgColor: 'bg-slate-700/40 border-slate-600/50',
        tooltip: design.screening_reason || 'No persisted workflow screen result',
    };
};

const compareRfEngagement = (
    a: Pick<Design, 'target_contact_count' | 'epitope_contact_count' | 'epitope_min_distance' | 'rfa_hotspot_covered_count' | 'plddt_overall'>,
    b: Pick<Design, 'target_contact_count' | 'epitope_contact_count' | 'epitope_min_distance' | 'rfa_hotspot_covered_count' | 'plddt_overall'>,
): number => (
    ((b.target_contact_count ?? 0) - (a.target_contact_count ?? 0)) ||
    ((b.epitope_contact_count ?? 0) - (a.epitope_contact_count ?? 0)) ||
    ((a.epitope_min_distance ?? Number.POSITIVE_INFINITY) - (b.epitope_min_distance ?? Number.POSITIVE_INFINITY)) ||
    ((b.rfa_hotspot_covered_count ?? 0) - (a.rfa_hotspot_covered_count ?? 0)) ||
    ((b.plddt_overall ?? 0) - (a.plddt_overall ?? 0))
);

const isNgsJob = (job: Pick<Job, 'model_id' | 'mode'>): boolean => {
    const modelId = (job.model_id || '').toLowerCase();
    const mode = (job.mode || '').toLowerCase();
    return (
        modelId === 'nanopore' ||
        modelId.includes('nanopore') ||
        mode === 'methylation_analysis' ||
        mode === 'nanopore_methylation'
    );
};

const inferPreferredOutputSource = (job: Job | null | undefined): OutputSourceFilter => {
    const stage = String(job?.awaiting_stage || job?.current_stage || '').toLowerCase();
    const candidateDir = String(job?.awaiting_payload?.candidate_dir || '').toLowerCase();

    if (stage === 'post_structure_validation' || candidateDir.includes('structure_validation')) return 'validation';
    if (stage === 'post_fampnn' || candidateDir.includes('fampnn')) return 'fampnn';
    if (stage === 'post_rfantibody' || candidateDir.includes('rfantibody')) return 'rfantibody';
    return 'all';
};

const hasExplicitBinderTargetRoles = (job: Job | null | undefined): boolean => {
    if (!job) return false;
    const params = job.params && typeof job.params === 'object' ? job.params as Record<string, unknown> : {};
    const modelId = String(job.model_id || '').toLowerCase();
    const mode = String(job.mode || '').toLowerCase();
    const rfdMode = String(params.rfd_mode || '').toLowerCase();

    return (
        rfdMode === 'antibody_denovo_pipeline' ||
        modelId.includes('antibody') ||
        mode.includes('antibody') ||
        Boolean(params.antibody_chains)
    );
};

const normalizeValidationDesignName = (name: string): string => {
    let normalized = name;
    while (/^\d+_/.test(normalized)) {
        normalized = normalized.replace(/^\d+_/, '');
    }
    return normalized;
};

const validationDesignPreference = (
    design: { job_id: string; pdb_path?: string | null },
    selectedJobId: string
): number => {
    const path = design.pdb_path || '';
    let score = 0;
    if (design.job_id === selectedJobId) score += 100;
    if (path.includes('/validated_designs/') || path.includes('/collected/structure_validation/')) score += 50;
    if (path.endsWith('.pdb')) score += 10;
    if (path.includes('/pdb_files/predictions/')) score += 5;
    return score;
};

const getFriendlyDesignName = (design: { name: string; pdb_path?: string | null; confidence_metrics?: Record<string, any> | null }): string => {
    const source = inferDesignOutputSource(design);
    const sampleMatch = design.name.match(/_sample_(\d+)$/);
    const seqMatch = design.name.match(/_seq_(\d+)/);
    if (source === 'validation' && sampleMatch) {
        return seqMatch
            ? `Seq ${seqMatch[1]} • ${getOutputSourceLabel(design)} Sample ${sampleMatch[1]}`
            : `${getOutputSourceLabel(design)} Sample ${sampleMatch[1]}`;
    }
    if (source === 'fampnn') return seqMatch ? `FAMPNN Seq ${seqMatch[1]}` : 'FAMPNN Candidate';
    if (source === 'rfantibody') {
        const jobMatch = design.name.match(/job[_-]?(\d+)/i);
        return jobMatch ? `RFantibody Backbone ${jobMatch[1]}` : 'RFantibody Backbone';
    }
    return design.name;
};

const getAvailableCdrLoopIds = (job: Job | null | undefined): string[] => {
    const selectedLoops = job?.params?.selected_cdr_loops;
    if (Array.isArray(selectedLoops) && selectedLoops.length > 0) {
        return selectedLoops.map((loopId) => String(loopId).trim().toUpperCase()).filter(Boolean);
    }
    const rawLoops = job?.params?.antibody_design_loops;
    if (typeof rawLoops === 'string' && rawLoops.trim()) {
        return rawLoops.split(',').map((loopId) => loopId.trim().toUpperCase()).filter(Boolean);
    }
    return ['H1', 'H2', 'H3'];
};

type AntibodyLoopRow = {
    chain: 'H' | 'L';
    region: 'H1' | 'H2' | 'H3' | 'L1' | 'L2' | 'L3';
    sequence: string | null;
    length: number | null;
};

type GateBackboneSummary = {
    mode?: string | null;
    total?: number | null;
    assigned_total?: number | null;
    unassigned_total?: number | null;
    backbones?: Record<string, {
        count?: number | null;
        representative_file?: string | null;
        preview?: string[] | null;
        sample_names?: string[] | null;
    }> | null;
};

type ReviewRepresentative = {
    name?: string | null;
    plddt_overall?: number | null;
    epitope_contact_count?: number | null;
    epitope_min_distance?: number | null;
    target_contact_count?: number | null;
    target_min_distance?: number | null;
    rfa_hotspot_covered_count?: number | null;
};

type RfReviewSet = 'filtered' | 'raw';
type SavedReviewFilterState = {
    rf_review_set?: RfReviewSet;
    output_source_filter?: OutputSourceFilter;
    sort_field?: string;
    sort_dir?: 'asc' | 'desc';
    filter_text?: string;
    selected_backbone_id?: number | null;
    plddt_min?: number;
    iptm_min?: number;
    contacts_min?: number;
    target_contacts_min?: number;
    binder_size_min?: string;
    binder_size_max?: string;
    cdr_h1_min?: string;
    cdr_h1_max?: string;
    cdr_h2_min?: string;
    cdr_h2_max?: string;
    cdr_h3_min?: string;
    cdr_h3_max?: string;
    rog_min?: string;
    rog_max?: string;
    rfd_rog_min?: string;
    rfd_rog_max?: string;
    epitope_max_dist?: string;
    target_max_dist?: string;
};

type SavedReviewFilterSet = Omit<ApiSavedReviewFilterSet, 'filter_state'> & {
    design_ids?: string[];
    filter_state: SavedReviewFilterState;
};

type FilterDraftState = {
    sortField: string;
    sortDir: 'asc' | 'desc';
    plddtMin: number;
    iptmMin: number;
    contactsMin: number;
    targetContactsMin: number;
    binderSizeMin: string;
    binderSizeMax: string;
    cdrH1Min: string;
    cdrH1Max: string;
    cdrH2Min: string;
    cdrH2Max: string;
    cdrH3Min: string;
    cdrH3Max: string;
    rogMin: string;
    rogMax: string;
    rfdRogMin: string;
    rfdRogMax: string;
    epitopeMaxDist: string;
    targetMaxDist: string;
};

const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const getRfReviewSetLabel = (value: RfReviewSet) => value === 'raw' ? 'Raw' : 'Screened';
const isPostRfantibodyStage = (job: Job | null | undefined): boolean =>
    String(job?.awaiting_stage || job?.current_stage || '').toLowerCase() === 'post_rfantibody';

const coerceGateBackboneSummary = (value: unknown): GateBackboneSummary | null => {
    if (!value || typeof value !== 'object') return null;
    const typed = value as Record<string, unknown>;
    if (!typed.backbones || typeof typed.backbones !== 'object') return null;
    return typed as GateBackboneSummary;
};

const getDefaultSortDirection = (field: string): 'asc' | 'desc' =>
    ASCENDING_DEFAULT_SORT_FIELDS.has(field) ? 'asc' : 'desc';

const coerceSavedReviewFilterSets = (value: unknown): SavedReviewFilterSet[] => {
    if (!Array.isArray(value)) return [];
    return value
        .map((entry): SavedReviewFilterSet | null => {
            if (!entry || typeof entry !== 'object') return null;
            const typed = entry as Record<string, unknown>;
            const filterState = typed.filter_state && typeof typed.filter_state === 'object'
                ? typed.filter_state as SavedReviewFilterState
                : {};
            const designIds = Array.isArray(typed.design_ids)
                ? typed.design_ids.filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
                : [];
            const id = String(typed.id || '').trim();
            const name = String(typed.name || '').trim();
            if (!id || !name) return null;
            return {
                id,
                name,
                created_at: String(typed.created_at || ''),
                visible_count: typeof typed.visible_count === 'number' ? typed.visible_count : (designIds.length > 0 ? designIds.length : null),
                source_total_count: typeof typed.source_total_count === 'number' ? typed.source_total_count : (designIds.length > 0 ? designIds.length : null),
                design_ids: designIds,
                filter_state: filterState,
            } satisfies SavedReviewFilterSet;
        })
        .filter((entry): entry is SavedReviewFilterSet => entry != null);
};

const normalizeSavedReviewFilterState = (state: SavedReviewFilterState): SavedReviewFilterState => ({
    rf_review_set: state.rf_review_set === 'raw' ? 'raw' : 'filtered',
    output_source_filter: state.output_source_filter === 'rfantibody'
        || state.output_source_filter === 'fampnn'
        || state.output_source_filter === 'validation'
        || state.output_source_filter === 'all'
        ? state.output_source_filter
        : 'all',
    sort_field: typeof state.sort_field === 'string' && state.sort_field.trim() ? state.sort_field : 'name',
    sort_dir: state.sort_dir === 'desc' ? 'desc' : 'asc',
    filter_text: typeof state.filter_text === 'string' ? state.filter_text : '',
    selected_backbone_id: typeof state.selected_backbone_id === 'number' && Number.isFinite(state.selected_backbone_id)
        ? state.selected_backbone_id
        : null,
    plddt_min: typeof state.plddt_min === 'number' ? state.plddt_min : 0,
    iptm_min: typeof state.iptm_min === 'number' ? state.iptm_min : 0,
    contacts_min: typeof state.contacts_min === 'number' ? state.contacts_min : 0,
    target_contacts_min: typeof state.target_contacts_min === 'number' ? state.target_contacts_min : 0,
    binder_size_min: typeof state.binder_size_min === 'string' ? state.binder_size_min : '',
    binder_size_max: typeof state.binder_size_max === 'string' ? state.binder_size_max : '',
    cdr_h1_min: typeof state.cdr_h1_min === 'string' ? state.cdr_h1_min : '',
    cdr_h1_max: typeof state.cdr_h1_max === 'string' ? state.cdr_h1_max : '',
    cdr_h2_min: typeof state.cdr_h2_min === 'string' ? state.cdr_h2_min : '',
    cdr_h2_max: typeof state.cdr_h2_max === 'string' ? state.cdr_h2_max : '',
    cdr_h3_min: typeof state.cdr_h3_min === 'string' ? state.cdr_h3_min : '',
    cdr_h3_max: typeof state.cdr_h3_max === 'string' ? state.cdr_h3_max : '',
    rog_min: typeof state.rog_min === 'string' ? state.rog_min : '',
    rog_max: typeof state.rog_max === 'string' ? state.rog_max : '',
    rfd_rog_min: typeof state.rfd_rog_min === 'string' ? state.rfd_rog_min : '',
    rfd_rog_max: typeof state.rfd_rog_max === 'string' ? state.rfd_rog_max : '',
    epitope_max_dist: typeof state.epitope_max_dist === 'string' ? state.epitope_max_dist : '',
    target_max_dist: typeof state.target_max_dist === 'string' ? state.target_max_dist : '',
});

const savedReviewStatesEqual = (a: SavedReviewFilterState, b: SavedReviewFilterState): boolean =>
    JSON.stringify(normalizeSavedReviewFilterState(a)) === JSON.stringify(normalizeSavedReviewFilterState(b));

export function ResultsViewer() {
    const { jobId } = useParams();
    const navigate = useNavigate();
    const queryClient = useQueryClient();

    // State
    const [selectedJobId, setSelectedJobId] = useState<string>(jobId || '');
    const [activeTab, setActiveTab] = useState<TabId>('overview');
    const [selectedDesignId, setSelectedDesignId] = useState<string>('');
    const [selectedDesignIds, setSelectedDesignIds] = useState<string[]>([]);
    const [iterationMessage, setIterationMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
    const [outputSourceFilter, setOutputSourceFilter] = useState<OutputSourceFilter>('all');
    const [antibodySourceFilter, setAntibodySourceFilter] = useState<OutputSourceFilter>('all');
    const [showCdrIndelModal, setShowCdrIndelModal] = useState(false);
    const [showManualMutagenesisModal, setShowManualMutagenesisModal] = useState(false);
    const [cdrIndelConfig, setCdrIndelConfig] = useState<AntibodyCdrIndelConfig>({
        loop_ids: [],
        variants_per_design: 10,
        allow_insertions: true,
        allow_deletions: true,
        indel_sizes: [1, 2],
        indel_probability: 0.1,
        allowed_aas: [], // Empty means all allowed
        predictor: 'protenix',
        msa_provider: 'local',
    });
    const [manualMutagenesisConfig, setManualMutagenesisConfig] = useState<ManualMutagenesisConfig & { mutation_sets_text: string }>({
        chain_id: '',
        mutation_sets: [],
        mutation_sets_text: '',
        predictor: 'protenix',
        msa_provider: 'local',
    });

    const [pipelineOverrides, setPipelineOverrides] = useState({
        run_structure_validation: false,
        structure_validator: 'boltz2',
        run_ppiflow: false,
        maturation_redesign_temp: 0.01,
        lock_target_chains: true,
        lock_antibody_framework: true,
        run_frustrampnn: false,
        interactive_gating: true,
    });
    const workflowOnlyRefinement = true;
    const [showParamOverrides, setShowParamOverrides] = useState(false);

    // Filter state
    const [sortField, setSortField] = useState<string>('name');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
    const [filterText, setFilterText] = useState('');
    const [colorMode, setColorMode] = useState<'default' | 'plddt' | 'cdr' | 'frustration'>('plddt');  // Structure coloring mode
    // Compare feature disabled for now:
    // const [showReferencePanel, setShowReferencePanel] = useState(false);
    // const [referenceStructures, setReferenceStructures] = useState<Array<{ url: string; format: 'pdb' | 'cif'; name: string }>>([]);
    const [selectedBackboneId, setSelectedBackboneId] = useState<number | null>(null);
    const [rfReviewSet, setRfReviewSet] = useState<RfReviewSet>('filtered');
    const [plddtMin, setPlddtMin] = useState<number>(0);
    const [iptmMin, setIptmMin] = useState<number>(0);
    const [contactsMin, setContactsMin] = useState<number>(0);
    const [targetContactsMin, setTargetContactsMin] = useState<number>(0);
    const [binderSizeMin, setBinderSizeMin] = useState<string>('');
    const [binderSizeMax, setBinderSizeMax] = useState<string>('');
    const [cdrH1Min, setCdrH1Min] = useState<string>('');
    const [cdrH1Max, setCdrH1Max] = useState<string>('');
    const [cdrH2Min, setCdrH2Min] = useState<string>('');
    const [cdrH2Max, setCdrH2Max] = useState<string>('');
    const [cdrH3Min, setCdrH3Min] = useState<string>('');
    const [cdrH3Max, setCdrH3Max] = useState<string>('');
    const [rogMin, setRogMin] = useState<string>('');
    const [rogMax, setRogMax] = useState<string>('');
    const [rfdRogMin, setRfdRogMin] = useState<string>('');
    const [rfdRogMax, setRfdRogMax] = useState<string>('');
    const [epitopeMaxDist, setEpitopeMaxDist] = useState<string>('');
    const [targetMaxDist, setTargetMaxDist] = useState<string>('');
    // const MAX_COMPARE_VIEWERS = 3; // unused

    // Pagination state for large design sets
    const [pageSize, setPageSize] = useState<number>(0);
    const [currentPage, setCurrentPage] = useState<number>(1);
    const [topSelectionCount, setTopSelectionCount] = useState<string>('25');
    const [savedFilterSetName, setSavedFilterSetName] = useState<string>('');
    const [appliedSavedFilterSetId, setAppliedSavedFilterSetId] = useState<string | null>(null);
    const PAGE_SIZE_OPTIONS = [50, 100, 250, 500, 1000, 0]; // 0 = All
    const [filterDraft, setFilterDraft] = useState<FilterDraftState>({
        sortField: 'name',
        sortDir: 'asc',
        plddtMin: 0,
        iptmMin: 0,
        contactsMin: 0,
        targetContactsMin: 0,
        binderSizeMin: '',
        binderSizeMax: '',
        cdrH1Min: '',
        cdrH1Max: '',
        cdrH2Min: '',
        cdrH2Max: '',
        cdrH3Min: '',
        cdrH3Max: '',
        rogMin: '',
        rogMax: '',
        rfdRogMin: '',
        rfdRogMax: '',
        epitopeMaxDist: '',
        targetMaxDist: '',
    });

    // Fetch jobs list (include children for aggregation)
    const { data: jobsData } = useQuery({
        queryKey: ['jobs', 'include_children'],
        queryFn: () => fetchJobs({ include_children: true }),
    });
    const jobs = jobsData?.data.jobs ?? [];
    const nonNgsJobs = useMemo(() => jobs.filter((j: Job) => !isNgsJob(j)), [jobs]);
    const activeJob = useMemo(
        () => nonNgsJobs.find((j: Job) => j.id === selectedJobId),
        [nonNgsJobs, selectedJobId]
    );
    const activeParentJob = useMemo(
        () => activeJob?.parent_job_id ? nonNgsJobs.find((j: Job) => j.id === activeJob.parent_job_id) : undefined,
        [nonNgsJobs, activeJob?.parent_job_id]
    );
    const isAntibodyContext = useMemo(() => {
        if (!activeJob) return false;
        const modelId = (activeJob.model_id || '').toLowerCase();
        const mode = (activeJob.mode || '').toLowerCase();
        const name = (activeJob.name || '').toLowerCase();
        const rfdMode = String(activeJob.params?.rfd_mode || '').toLowerCase();
        return (
            modelId.includes('antibody') ||
            modelId === 'fampnn_child' ||
            mode.includes('antibody') ||
            name.includes('antibody') ||
            rfdMode === 'antibody_denovo_pipeline'
        );
    }, [activeJob]);
    const showBinderTargetConfidence = useMemo(() => hasExplicitBinderTargetRoles(activeJob), [activeJob]);
    const rfRawCount = Number(activeJob?.awaiting_payload?.raw_candidate_count || 0);
    const rfFilteredCount = Number(activeJob?.awaiting_payload?.filtered_candidate_count || 0);
    const savedReviewFilterSets = useMemo(
        () => coerceSavedReviewFilterSets(activeJob?.awaiting_payload?.review_filter_sets),
        [activeJob?.awaiting_payload?.review_filter_sets],
    );
    const appliedSavedReviewFilterSet = useMemo(
        () => (appliedSavedFilterSetId
            ? savedReviewFilterSets.find((filterSet) => filterSet.id === appliedSavedFilterSetId) ?? null
            : null),
        [appliedSavedFilterSetId, savedReviewFilterSets],
    );
    const availableCdrLoopIds = useMemo(() => getAvailableCdrLoopIds(activeJob), [activeJob]);

    useEffect(() => {
        const heavyLoops = availableCdrLoopIds.filter((loopId) => loopId.startsWith('H'));
        const preferredLoop = heavyLoops.includes('H3') ? ['H3'] : heavyLoops.slice(0, 1);
        const fallbackLoops = preferredLoop.length > 0 ? preferredLoop : availableCdrLoopIds.slice(0, 1);
        setCdrIndelConfig((current) => ({
            ...current,
            loop_ids: (() => {
                const kept = current.loop_ids.filter((loopId) => availableCdrLoopIds.includes(loopId));
                return kept.length > 0 ? kept : fallbackLoops;
            })(),
        }));
    }, [availableCdrLoopIds]);

    useEffect(() => {
        const antibodyChains = String(activeJob?.params?.antibody_chains || '')
            .split(',')
            .map((value) => value.trim().toUpperCase())
            .filter(Boolean);
        const preferredChain = antibodyChains[0] || '';
        if (!preferredChain) return;
        setManualMutagenesisConfig((current) => current.chain_id ? current : { ...current, chain_id: preferredChain });
    }, [activeJob?.params?.antibody_chains]);

    // Sync URL with selection
    useEffect(() => {
        if (nonNgsJobs.length === 0) {
            if (selectedJobId) {
                setSelectedJobId('');
                setSelectedDesignId('');
            }
            if (jobId) {
                navigate('/designs', { replace: true });
            }
            return;
        }

        if (jobId) {
            const requestedJob = nonNgsJobs.find((j: Job) => j.id === jobId);
            if (requestedJob) {
                if (selectedJobId !== requestedJob.id) {
                    setSelectedJobId(requestedJob.id);
                    setSelectedDesignId('');
                }
                return;
            }

            if (selectedJobId) {
                setSelectedJobId('');
                setSelectedDesignId('');
            }
            navigate('/designs', { replace: true });
            return;
        }

        if (selectedJobId && !activeJob) {
            setSelectedJobId('');
            setSelectedDesignId('');
        }
    }, [jobId, nonNgsJobs, selectedJobId, activeJob, navigate]);

    useEffect(() => {
        if (!activeJob?.parent_job_id || !activeParentJob) return;
        const parentOwnsInteractiveReview = Boolean(activeParentJob.awaiting_input) || activeParentJob.status === 'awaiting_input';
        if (!parentOwnsInteractiveReview) return;
        if (selectedJobId === activeParentJob.id) return;
        setSelectedJobId(activeParentJob.id);
        setSelectedDesignId('');
        setCurrentPage(1);
        navigate(`/designs/${activeParentJob.id}`, { replace: true });
    }, [activeJob?.id, activeJob?.parent_job_id, activeParentJob?.id, navigate, selectedJobId]);

    const handleJobChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const newId = e.target.value;
        setSelectedJobId(newId);
        setSelectedDesignId('');
        setCurrentPage(1); // Reset pagination when switching jobs
        if (newId) navigate(`/designs/${newId}`);
        else navigate('/designs');
    };

    const rogMinValue = rogMin.trim() === '' ? undefined : Number(rogMin);
    const rogMaxValue = rogMax.trim() === '' ? undefined : Number(rogMax);
    const rfdRogMinValue = rfdRogMin.trim() === '' ? undefined : Number(rfdRogMin);
    const rfdRogMaxValue = rfdRogMax.trim() === '' ? undefined : Number(rfdRogMax);
    const binderSizeMinValue = binderSizeMin.trim() === '' ? undefined : Number(binderSizeMin);
    const binderSizeMaxValue = binderSizeMax.trim() === '' ? undefined : Number(binderSizeMax);
    const cdrH1MinValue = cdrH1Min.trim() === '' ? undefined : Number(cdrH1Min);
    const cdrH1MaxValue = cdrH1Max.trim() === '' ? undefined : Number(cdrH1Max);
    const cdrH2MinValue = cdrH2Min.trim() === '' ? undefined : Number(cdrH2Min);
    const cdrH2MaxValue = cdrH2Max.trim() === '' ? undefined : Number(cdrH2Max);
    const cdrH3MinValue = cdrH3Min.trim() === '' ? undefined : Number(cdrH3Min);
    const cdrH3MaxValue = cdrH3Max.trim() === '' ? undefined : Number(cdrH3Max);
    const epitopeMaxDistValue = epitopeMaxDist.trim() === '' ? undefined : Number(epitopeMaxDist);
    const targetMaxDistValue = targetMaxDist.trim() === '' ? undefined : Number(targetMaxDist);
    const apiSortField = SERVER_SORT_FIELDS.has(sortField as DesignSortField) ? sortField as DesignSortField : undefined;
    const isPostRFantibodyReview = isAntibodyContext && isPostRfantibodyStage(activeJob);
    const availableSortOptions = useMemo(
        () => SORT_OPTIONS.filter((option) => !isPostRFantibodyReview || option.value !== 'binding_tier'),
        [isPostRFantibodyReview],
    );
    const activeRfArtifactGroup = isPostRFantibodyReview ? rfReviewSet : undefined;
    const activeSavedSubsetDesignIds = appliedSavedReviewFilterSet?.design_ids?.length
        ? appliedSavedReviewFilterSet.design_ids
        : undefined;
    const designQueryFilters = useMemo<DesignFilters>(() => ({
        job_id: selectedJobId,
        include_children: false, // Parent review should default to aggregated parent outputs, not raw child artifacts
        design_ids: activeSavedSubsetDesignIds,
        q: filterText.trim() || undefined,
        limit: pageSize === 0 ? MAX_BULK_SELECTION_DESIGNS : pageSize,
        offset: pageSize === 0 ? 0 : (currentPage - 1) * pageSize,
        sort_by: apiSortField,
        sort_desc: sortDir === 'desc',
        backbone_id: selectedBackboneId ?? undefined,
        plddt_min: plddtMin > 0 ? plddtMin : undefined,
        iptm_min: iptmMin > 0 ? iptmMin : undefined,
        epitope_contacts_min: contactsMin > 0 ? contactsMin : undefined,
        target_contacts_min: targetContactsMin > 0 ? targetContactsMin : undefined,
        epitope_max_dist: Number.isFinite(epitopeMaxDistValue) && epitopeMaxDistValue! > 0 ? epitopeMaxDistValue : undefined,
        target_max_dist: Number.isFinite(targetMaxDistValue) && targetMaxDistValue! > 0 ? targetMaxDistValue : undefined,
        binder_length_min: Number.isFinite(binderSizeMinValue) ? binderSizeMinValue : undefined,
        binder_length_max: Number.isFinite(binderSizeMaxValue) ? binderSizeMaxValue : undefined,
        cdr_h1_min: Number.isFinite(cdrH1MinValue) ? cdrH1MinValue : undefined,
        cdr_h1_max: Number.isFinite(cdrH1MaxValue) ? cdrH1MaxValue : undefined,
        cdr_h2_min: Number.isFinite(cdrH2MinValue) ? cdrH2MinValue : undefined,
        cdr_h2_max: Number.isFinite(cdrH2MaxValue) ? cdrH2MaxValue : undefined,
        cdr_h3_min: Number.isFinite(cdrH3MinValue) ? cdrH3MinValue : undefined,
        cdr_h3_max: Number.isFinite(cdrH3MaxValue) ? cdrH3MaxValue : undefined,
        rog_min: rogMinValue,
        rog_max: rogMaxValue,
        rfd_rog_min: rfdRogMinValue,
        rfd_rog_max: rfdRogMaxValue,
        artifact_group: activeRfArtifactGroup,
    }), [
        selectedJobId,
        filterText,
        pageSize,
        currentPage,
        apiSortField,
        sortDir,
        selectedBackboneId,
        plddtMin,
        iptmMin,
        contactsMin,
        targetContactsMin,
        epitopeMaxDistValue,
        targetMaxDistValue,
        binderSizeMinValue,
        binderSizeMaxValue,
        cdrH1MinValue,
        cdrH1MaxValue,
        cdrH2MinValue,
        cdrH2MaxValue,
        cdrH3MinValue,
        cdrH3MaxValue,
        rogMinValue,
        rogMaxValue,
        rfdRogMinValue,
        rfdRogMaxValue,
        activeRfArtifactGroup,
        activeSavedSubsetDesignIds,
    ]);
    const bulkSelectionFilters = useMemo<DesignFilters>(() => ({
        ...designQueryFilters,
        limit: MAX_BULK_SELECTION_DESIGNS,
        offset: 0,
    }), [designQueryFilters]);

    const { data: designsData, isLoading: designsLoading } = useQuery({
        queryKey: ['designs', designQueryFilters],
        queryFn: () => fetchDesigns(designQueryFilters),
        enabled: !!activeJob,
    });
    const rawDesigns = designsData?.data.designs ?? [];
    const designs = useMemo(() => {
        const deduped: typeof rawDesigns = [];
        const validationIndices = new Map<string, number>();

        for (const design of rawDesigns) {
            if (inferDesignOutputSource(design as any) !== 'validation') {
                deduped.push(design);
                continue;
            }

            const key = normalizeValidationDesignName(design.name);
            const existingIndex = validationIndices.get(key);
            if (existingIndex == null) {
                validationIndices.set(key, deduped.length);
                deduped.push(design);
                continue;
            }

            const existing = deduped[existingIndex];
            if (validationDesignPreference(design, selectedJobId) > validationDesignPreference(existing, selectedJobId)) {
                deduped[existingIndex] = design;
            }
        }

        return deduped;
    }, [rawDesigns, selectedJobId]);
    const totalDesigns = designsData?.data.total ?? designs.length;
    const totalPages = pageSize === 0 ? 1 : Math.ceil(totalDesigns / pageSize);

    // Fetch backbone summary for toggle UI
    const { data: backboneSummaryData } = useQuery({
        queryKey: ['backboneSummary', selectedJobId, activeRfArtifactGroup],
        queryFn: () => fetchBackboneSummary(selectedJobId, activeRfArtifactGroup),
        enabled: !!activeJob,
    });
    const backboneSummary = backboneSummaryData?.data;
    const gateCandidateBackboneSummary = useMemo(
        () => coerceGateBackboneSummary(activeJob?.awaiting_payload?.candidate_backbone_summary),
        [activeJob?.awaiting_payload?.candidate_backbone_summary]
    );
    const gateRawBackboneSummary = useMemo(
        () => coerceGateBackboneSummary(activeJob?.awaiting_payload?.raw_backbone_summary),
        [activeJob?.awaiting_payload?.raw_backbone_summary]
    );
    const gateFilteredBackboneSummary = useMemo(
        () => coerceGateBackboneSummary(activeJob?.awaiting_payload?.filtered_backbone_summary),
        [activeJob?.awaiting_payload?.filtered_backbone_summary]
    );
    const currentGateBackboneSummary = useMemo(() => {
        if (!isPostRFantibodyReview) return gateCandidateBackboneSummary ?? backboneSummary ?? null;
        return rfReviewSet === 'raw'
            ? (gateRawBackboneSummary ?? backboneSummary ?? null)
            : (gateFilteredBackboneSummary ?? gateCandidateBackboneSummary ?? backboneSummary ?? null);
    }, [
        isPostRFantibodyReview,
        rfReviewSet,
        gateCandidateBackboneSummary,
        gateRawBackboneSummary,
        gateFilteredBackboneSummary,
        backboneSummary,
    ]);
    const reviewBackboneRows = useMemo(() => {
        const ids = new Set<number>();
        const apiBackbones = (backboneSummary?.backbones || {}) as Record<string, any>;
        const currentEntries = (currentGateBackboneSummary?.backbones || {}) as Record<string, any>;
        for (const key of Object.keys(currentEntries)) {
            const parsed = Number(key);
            if (Number.isFinite(parsed)) ids.add(parsed);
        }
        if (!ids.size) {
            for (const key of Object.keys(apiBackbones)) {
                const parsed = Number(key);
                if (Number.isFinite(parsed)) ids.add(parsed);
            }
        }

        return Array.from(ids)
            .sort((a, b) => a - b)
            .map((backboneId) => {
                const idKey = String(backboneId);
                const apiEntry = apiBackbones[idKey];
                const currentEntry = currentEntries[idKey];
                const summaryEntry = apiEntry ?? currentEntry;
                const candidateEntry = gateCandidateBackboneSummary?.backbones?.[idKey];
                const rawEntry = gateRawBackboneSummary?.backbones?.[idKey];
                const filteredEntry = gateFilteredBackboneSummary?.backbones?.[idKey];
                return {
                    id: backboneId,
                    count: currentEntry?.count ?? apiEntry?.count ?? 0,
                    avgPlddt: summaryEntry?.avg_plddt ?? null,
                    avgIptm: summaryEntry?.avg_iptm ?? null,
                    avgH3: summaryEntry?.avg_cdr_h3_length ?? null,
                    avgTargetContacts: summaryEntry?.avg_target_contacts ?? null,
                    minTargetContacts: summaryEntry?.min_target_contacts ?? null,
                    maxTargetContacts: summaryEntry?.max_target_contacts ?? null,
                    avgEpitopeContacts: summaryEntry?.avg_epitope_contacts ?? null,
                    minEpitopeContacts: summaryEntry?.min_epitope_contacts ?? null,
                    maxEpitopeContacts: summaryEntry?.max_epitope_contacts ?? null,
                    avgEpitopeDistance: summaryEntry?.avg_epitope_distance ?? null,
                    minEpitopeDistance: summaryEntry?.min_epitope_distance ?? null,
                    maxEpitopeDistance: summaryEntry?.max_epitope_distance ?? null,
                    representative: summaryEntry?.representative ?? null,
                    candidateCount: candidateEntry?.count ?? null,
                    rawCount: rawEntry?.count ?? null,
                    filteredCount: filteredEntry?.count ?? null,
                    previewName: candidateEntry?.sample_names?.[0] || null,
                };
            });
    }, [backboneSummary, currentGateBackboneSummary, gateCandidateBackboneSummary, gateRawBackboneSummary, gateFilteredBackboneSummary]);
    const reviewBackboneTotal = useMemo(() => {
        if (isPostRFantibodyReview) {
            if (rfReviewSet === 'raw') return gateRawBackboneSummary?.total ?? backboneSummary?.total ?? 0;
            return gateFilteredBackboneSummary?.total ?? gateCandidateBackboneSummary?.total ?? backboneSummary?.total ?? 0;
        }
        return backboneSummary?.total ?? 0;
    }, [isPostRFantibodyReview, rfReviewSet, gateCandidateBackboneSummary?.total, gateRawBackboneSummary?.total, gateFilteredBackboneSummary?.total, backboneSummary?.total]);
    const selectedReviewBackbone = useMemo(
        () => (selectedBackboneId == null ? null : reviewBackboneRows.find((row) => row.id === selectedBackboneId) ?? null),
        [reviewBackboneRows, selectedBackboneId],
    );
    useEffect(() => {
        if (selectedBackboneId == null) return;
        if (!reviewBackboneRows.some((row) => row.id === selectedBackboneId)) {
            setSelectedBackboneId(null);
        }
    }, [selectedBackboneId, reviewBackboneRows]);

    // Fetch structure analysis for selected design (Biotite-powered)
    const { data: structureAnalysisData, isLoading: _structureAnalysisLoading } = useQuery({
        queryKey: ['structureAnalysis', selectedDesignId],
        queryFn: () => fetchStructureAnalysis(selectedDesignId),
        enabled: !!selectedDesignId && activeTab === 'structure',
    });
    const structureAnalysis = structureAnalysisData?.data;

    // Fetch Antibody Data (CDRs, Stability)
    const { data: antibodyDataWrapper } = useQuery({
        queryKey: ['antibodyData', selectedDesignId],
        queryFn: () => fetchAntibodyData(selectedDesignId),
        enabled: !!selectedDesignId && (activeTab === 'antibody' || activeTab === 'structure'),
        retry: false
    });
    const antibodyData = antibodyDataWrapper?.data;

    // Antibody selections for Molstar - prefer IMGT-standard chain/range overlays when available.
    const antibodySelections = useMemo(() => {
        const design = designs.find(d => d.id === selectedDesignId) as any;
        if (!design) return undefined;

        const selections = [];
        const hasImgT = Boolean(antibodyData?.imgt_pdb_url);
        const heavyChainId = hasImgT ? 'H' : 'A';
        const lightChainId = hasImgT ? 'L' : 'C';
        const cdrH1Length = design.cdr_h1_length ?? antibodyData?.cdrs?.H1?.length ?? null;
        const cdrH2Length = design.cdr_h2_length ?? antibodyData?.cdrs?.H2?.length ?? null;
        const cdrH3Length = design.cdr_h3_length ?? antibodyData?.cdrs?.H3?.length ?? null;
        const cdrL1Length = design.cdr_l1_length ?? antibodyData?.cdrs?.L1?.length ?? null;
        const cdrL2Length = design.cdr_l2_length ?? antibodyData?.cdrs?.L2?.length ?? null;
        const cdrL3Length = design.cdr_l3_length ?? antibodyData?.cdrs?.L3?.length ?? null;

        if (cdrH1Length || cdrH2Length || cdrH3Length) {
            if (hasImgT) {
                selections.push(
                    { chain_id: heavyChainId, start_residue_number: 27, end_residue_number: 38, color: { r: 255, g: 50, b: 50 } },
                    { chain_id: heavyChainId, start_residue_number: 56, end_residue_number: 65, color: { r: 50, g: 255, b: 50 } },
                    { chain_id: heavyChainId, start_residue_number: 105, end_residue_number: 117, color: { r: 50, g: 100, b: 255 } },
                );
            } else {
                const h1Start = 27;
                const h1End = 26 + (cdrH1Length || 12);
                const h2Start = h1End + 17;
                const h2End = h2Start + (cdrH2Length || 10) - 1;
                const h3Start = h2End + 39;
                const h3End = h3Start + (cdrH3Length || 12) - 1;
                selections.push(
                    { chain_id: heavyChainId, start_residue_number: h1Start, end_residue_number: h1End, color: { r: 255, g: 50, b: 50 } },
                    { chain_id: heavyChainId, start_residue_number: h2Start, end_residue_number: h2End, color: { r: 50, g: 255, b: 50 } },
                    { chain_id: heavyChainId, start_residue_number: h3Start, end_residue_number: h3End, color: { r: 50, g: 100, b: 255 } },
                );
            }
        }

        if (cdrL1Length || cdrL2Length || cdrL3Length) {
            if (hasImgT) {
                selections.push(
                    { chain_id: lightChainId, start_residue_number: 27, end_residue_number: 38, color: { r: 255, g: 255, b: 50 } },
                    { chain_id: lightChainId, start_residue_number: 56, end_residue_number: 65, color: { r: 50, g: 255, b: 255 } },
                    { chain_id: lightChainId, start_residue_number: 105, end_residue_number: 117, color: { r: 255, g: 50, b: 255 } },
                );
            } else {
                const l1Start = 27;
                const l1End = 26 + (cdrL1Length || 11);
                const l2Start = l1End + 16;
                const l2End = l2Start + (cdrL2Length || 7) - 1;
                const l3Start = l2End + 33;
                const l3End = l3Start + (cdrL3Length || 9) - 1;
                selections.push(
                    { chain_id: lightChainId, start_residue_number: l1Start, end_residue_number: l1End, color: { r: 255, g: 255, b: 50 } },
                    { chain_id: lightChainId, start_residue_number: l2Start, end_residue_number: l2End, color: { r: 50, g: 255, b: 255 } },
                    { chain_id: lightChainId, start_residue_number: l3Start, end_residue_number: l3End, color: { r: 255, g: 50, b: 255 } },
                );
            }
        }

        return selections.length > 0 ? selections : undefined;
    }, [designs, selectedDesignId, antibodyData?.imgt_pdb_url]);

    const orderedDesigns = designs;
    const analyticsChartDesigns = useMemo(
        () => (designs.length > ANALYTICS_CHART_DESIGN_CAP ? designs.slice(0, ANALYTICS_CHART_DESIGN_CAP) : designs),
        [designs],
    );
    const preferredAnalysisLens = useMemo<AnalysisLens | 'auto'>(() => {
        if (outputSourceFilter !== 'all' && designs.some((design) => inferDesignOutputSource(design as any) === outputSourceFilter)) {
            return outputSourceFilter;
        }
        if (antibodySourceFilter !== 'all' && designs.some((design) => inferDesignOutputSource(design as any) === antibodySourceFilter)) {
            return antibodySourceFilter;
        }
        return inferPreferredAnalysisLens(activeJob, designs as any) ?? 'auto';
    }, [activeJob, antibodySourceFilter, designs, outputSourceFilter]);
    const selectedDesignSet = useMemo(() => new Set(selectedDesignIds), [selectedDesignIds]);
    const tableDesigns = useMemo(() => {
        if (outputSourceFilter === 'all') return orderedDesigns;
        return orderedDesigns.filter((design) => inferDesignOutputSource(design as any) === outputSourceFilter);
    }, [orderedDesigns, outputSourceFilter]);
    const visibleDesignIds = useMemo(() => tableDesigns.map((design) => design.id), [tableDesigns]);
    const visibleSelectionRef = useRef<HTMLInputElement | null>(null);
    const visibleSelectedCount = useMemo(
        () => visibleDesignIds.filter((designId) => selectedDesignSet.has(designId)).length,
        [selectedDesignSet, visibleDesignIds],
    );
    const allVisibleSelected = visibleDesignIds.length > 0 && visibleSelectedCount === visibleDesignIds.length;
    const someVisibleSelected = visibleSelectedCount > 0 && !allVisibleSelected;
    const parsedTopSelectionCount = Math.max(1, Math.min(MAX_BULK_SELECTION_DESIGNS, Number(topSelectionCount) || 0));
    const activeReviewSetLabel = getRfReviewSetLabel(rfReviewSet);
    const loadedSavedReviewFilterSet = appliedSavedReviewFilterSet?.design_ids?.length
        ? appliedSavedReviewFilterSet
        : null;
    const loadedSavedDatasetDesignCount = loadedSavedReviewFilterSet?.design_ids?.length ?? 0;
    const activeLaunchReviewFilterSetId = selectedDesignIds.length === 0
        ? loadedSavedReviewFilterSet?.id
        : undefined;
    const activeLaunchDesignCount = selectedDesignIds.length > 0
        ? selectedDesignIds.length
        : loadedSavedDatasetDesignCount;
    const canLaunchWorkingSet = activeLaunchDesignCount > 0;
    const activeCurrentSetLabel = appliedSavedReviewFilterSet
        ? appliedSavedReviewFilterSet.name
        : `${activeReviewSetLabel} set`;
    const activeBadgeLabel = useMemo(() => {
        if (isPostRFantibodyReview) {
            return `${activeCurrentSetLabel} ${totalDesigns.toLocaleString()} outputs`;
        }
        if (outputSourceFilter !== 'all' && tableDesigns.length !== totalDesigns) {
            return `${tableDesigns.length.toLocaleString()} visible`;
        }
        return `${totalDesigns.toLocaleString()} designs`;
    }, [activeCurrentSetLabel, isPostRFantibodyReview, outputSourceFilter, tableDesigns.length, totalDesigns]);
    const paginationSubject = isPostRFantibodyReview
        ? `${activeCurrentSetLabel.toLowerCase()} outputs`
        : outputSourceFilter !== 'all'
            ? `${outputSourceFilter} outputs`
            : 'designs';

    // Fetch PDB content when design selected
    // Note: MolstarViewer now fetches structure directly from API URL

    // Auto-select first design
    useEffect(() => {
        if (designs.length > 0 && !selectedDesignId) {
            setSelectedDesignId(designs[0].id);
        }
    }, [designs, selectedDesignId]);

    useEffect(() => {
        if (activeTab !== 'structure') return;
        if (tableDesigns.length === 0) return;
        if (!selectedDesignId || !tableDesigns.some((design) => design.id === selectedDesignId)) {
            setSelectedDesignId(tableDesigns[0].id);
        }
    }, [activeTab, tableDesigns, selectedDesignId]);

    useEffect(() => {
        if (!visibleSelectionRef.current) return;
        visibleSelectionRef.current.indeterminate = someVisibleSelected;
    }, [someVisibleSelected]);

    useEffect(() => {
        setSelectedDesignIds([]);
        setIterationMessage(null);
        setSavedFilterSetName('');
    }, [selectedJobId]);

    useEffect(() => {
        setCurrentPage(1);
    }, [
        selectedJobId,
        filterText,
        selectedBackboneId,
        plddtMin,
        iptmMin,
        contactsMin,
        targetContactsMin,
        binderSizeMin,
        binderSizeMax,
        cdrH1Min,
        cdrH1Max,
        cdrH2Min,
        cdrH2Max,
        cdrH3Min,
        cdrH3Max,
        epitopeMaxDist,
        targetMaxDist,
        rogMin,
        rogMax,
        rfdRogMin,
        rfdRogMax,
        sortField,
        sortDir,
    ]);

    useEffect(() => {
        const preferredSource = inferPreferredOutputSource(activeJob);
        const nextSource = preferredSource !== 'all' && designs.some((design) => inferDesignOutputSource(design as any) === preferredSource)
            ? preferredSource
            : 'all';
        setOutputSourceFilter(nextSource);
        setAntibodySourceFilter(nextSource);
    }, [selectedJobId, activeJob?.awaiting_stage, activeJob?.current_stage, activeJob?.awaiting_payload?.candidate_dir, designs]);

    useEffect(() => {
        if (!isPostRFantibodyReview) return;
        setRfReviewSet(rfFilteredCount > 0 ? 'filtered' : 'raw');
    }, [selectedJobId, isPostRFantibodyReview, rfFilteredCount]);

    useEffect(() => {
        if (!isPostRFantibodyReview) return;
        setSortField((current) => (current === 'name' ? 'backbone' : current));
    }, [selectedJobId, isPostRFantibodyReview]);

    useEffect(() => {
        setAppliedSavedFilterSetId(null);
    }, [selectedJobId]);

    useEffect(() => {
        setFilterDraft({
            sortField,
            sortDir,
            plddtMin,
            iptmMin,
            contactsMin,
            targetContactsMin,
            binderSizeMin,
            binderSizeMax,
            cdrH1Min,
            cdrH1Max,
            cdrH2Min,
            cdrH2Max,
            cdrH3Min,
            cdrH3Max,
            rogMin,
            rogMax,
            rfdRogMin,
            rfdRogMax,
            epitopeMaxDist,
            targetMaxDist,
        });
    }, [
        sortField,
        sortDir,
        plddtMin,
        iptmMin,
        contactsMin,
        targetContactsMin,
        binderSizeMin,
        binderSizeMax,
        cdrH1Min,
        cdrH1Max,
        cdrH2Min,
        cdrH2Max,
        cdrH3Min,
        cdrH3Max,
        rogMin,
        rogMax,
        rfdRogMin,
        rfdRogMax,
        epitopeMaxDist,
        targetMaxDist,
    ]);

    const selectedDesign = designs.find(d => d.id === selectedDesignId);
    const hasCdrAnnotation = Boolean(
        selectedDesign && (
            selectedDesign.cdr_h1_length ||
            selectedDesign.cdr_h2_length ||
            selectedDesign.cdr_h3_length ||
            selectedDesign.cdr_l1_length ||
            selectedDesign.cdr_l2_length ||
            selectedDesign.cdr_l3_length
        )
    );
    const selectedDesignLens = useMemo<AnalysisLens | null>(() => {
        if (selectedDesign) {
            return inferDesignAnalysisLens(selectedDesign as any);
        }
        return inferPreferredAnalysisLens(activeJob, designs as any) ?? null;
    }, [activeJob, designs, selectedDesign]);
    // For oligo_design jobs: default to element coloring (B-factors are design confidence, not AlphaFold pLDDT)
    const isOligoJob = (activeJob?.model_id || '').toLowerCase().includes('oligo');
    useEffect(() => {
        if (isOligoJob) {
            setColorMode('default');
            return;
        }
        if (selectedDesignLens === 'frustrampnn') {
            setColorMode(selectedDesign?.frustration_residues?.length ? 'frustration' : 'default');
            return;
        }
        if (selectedDesignLens === 'validation' || selectedDesignLens === 'protenix') {
            setColorMode('plddt');
            return;
        }
        if (hasCdrAnnotation) {
            setColorMode('cdr');
            return;
        }
        setColorMode('default');
    }, [hasCdrAnnotation, isOligoJob, selectedDesign?.frustration_residues?.length, selectedDesignLens, selectedJobId]);
    const antibodyDesignGroups = useMemo(() => {
        const grouped: Record<OutputSourceFilter, typeof designs> = { all: [], rfantibody: [], fampnn: [], validation: [] };
        for (const design of orderedDesigns) {
            const source = inferDesignOutputSource(design);
            if (source === 'rfantibody' || source === 'fampnn' || source === 'validation') grouped[source].push(design);
            else grouped.all.push(design);
        }
        return grouped;
    }, [orderedDesigns]);
    const antibodyTabDesigns = useMemo(() => {
        if (antibodySourceFilter === 'all') return orderedDesigns;
        return antibodyDesignGroups[antibodySourceFilter] || [];
    }, [orderedDesigns, antibodyDesignGroups, antibodySourceFilter]);
    const selectedDesignSource = selectedDesign ? inferDesignOutputSource(selectedDesign) : 'all';
    useEffect(() => {
        if (activeTab !== 'antibody') return;
        if (!selectedDesignId && antibodyTabDesigns.length > 0) {
            setSelectedDesignId(antibodyTabDesigns[0].id);
            return;
        }
        if (selectedDesignId && antibodyTabDesigns.length > 0 && !antibodyTabDesigns.some((d) => d.id === selectedDesignId)) {
            setSelectedDesignId(antibodyTabDesigns[0].id);
        }
    }, [activeTab, antibodyTabDesigns, selectedDesignId]);
    const antibodyLoopRows = useMemo<AntibodyLoopRow[]>(() => {
        if (!selectedDesign) return [];
        const rows: AntibodyLoopRow[] = [
            { chain: 'H', region: 'H1', sequence: selectedDesign.cdr_h1 ?? antibodyData?.cdrs?.H1 ?? null, length: selectedDesign.cdr_h1_length ?? antibodyData?.cdrs?.H1?.length ?? null },
            { chain: 'H', region: 'H2', sequence: selectedDesign.cdr_h2 ?? antibodyData?.cdrs?.H2 ?? null, length: selectedDesign.cdr_h2_length ?? antibodyData?.cdrs?.H2?.length ?? null },
            { chain: 'H', region: 'H3', sequence: selectedDesign.cdr_h3 ?? antibodyData?.cdrs?.H3 ?? null, length: selectedDesign.cdr_h3_length ?? antibodyData?.cdrs?.H3?.length ?? null },
            { chain: 'L', region: 'L1', sequence: selectedDesign.cdr_l1 ?? antibodyData?.cdrs?.L1 ?? null, length: selectedDesign.cdr_l1_length ?? antibodyData?.cdrs?.L1?.length ?? null },
            { chain: 'L', region: 'L2', sequence: selectedDesign.cdr_l2 ?? antibodyData?.cdrs?.L2 ?? null, length: selectedDesign.cdr_l2_length ?? antibodyData?.cdrs?.L2?.length ?? null },
            { chain: 'L', region: 'L3', sequence: selectedDesign.cdr_l3 ?? antibodyData?.cdrs?.L3 ?? null, length: selectedDesign.cdr_l3_length ?? antibodyData?.cdrs?.L3?.length ?? null },
        ];
        return rows.filter((row) => row.sequence || row.length || row.chain === 'H');
    }, [selectedDesign, antibodyData]);
    const antibodyTopFrustrationResidues = useMemo(() => {
        const rows = Array.isArray(selectedDesign?.frustration_residues) ? selectedDesign.frustration_residues : [];
        return [...rows]
            .filter((row) => row && typeof row.pos === 'number' && isFiniteNumber(row.frust))
            .sort((a, b) => Math.abs(b.frust) - Math.abs(a.frust))
            .slice(0, 8);
    }, [selectedDesign]);
    const antibodyHasAnnotation = antibodyLoopRows.some((row) => Boolean(row.sequence));
    // Detect structure format from file extension
    const structureFormat = selectedDesign?.pdb_path?.endsWith('.cif') ? 'cif' : 'pdb';
    const isLoading = designsLoading;

    // Quick stats for overview
    const stats = useMemo(() => {
        if (!designs.length) return null;
        const plddts = designs.map(d => d.plddt_overall).filter((v): v is number => v != null);
        const paes = designs.map(d => d.pae_overall).filter((v): v is number => v != null);
        const ptms = designs.map(d => d.ptm).filter((v): v is number => v != null);
        const affinities = designs.map(d => d.affinity_score).filter((v): v is number => v != null);
        const binderProbs = designs.map(d => d.binder_probability).filter((v): v is number => v != null);
        const epitopeContacts = designs.map(d => d.epitope_contact_count).filter((v): v is number => v != null);
        const targetContacts = designs.map(d => d.target_contact_count).filter((v): v is number => v != null);
        const epitopeDistances = designs.map(d => d.epitope_min_distance).filter((v): v is number => v != null);
        const targetDistances = designs.map(d => d.target_min_distance).filter((v): v is number => v != null);
        const hotspotCoverage = designs.map(d => d.rfa_hotspot_covered_count).filter((v): v is number => v != null);
        const psces = designs.map(d => d.fampnn_psce).filter((v): v is number => v != null);
        const frustrationHigh = designs.map(d => d.frustration_high_count).filter((v): v is number => v != null);
        const frustrationPct = designs.map(d => d.frustration_pct_high).filter((v): v is number => v != null);
        const screenPassed = designs.filter((d) => d.passed_screen === true).length;
        const screenFailed = designs.filter((d) => d.passed_screen === false).length;
        const screeningReasons = new Map<string, number>();

        const tierCounts = { A: 0, B: 0, C: 0, D: 0, none: 0 };
        designs.forEach(d => {
            const tier = getBindingTier(d.iptm, d.epitope_contact_count);
            if (tier.tier === 'A') tierCounts.A++;
            else if (tier.tier === 'B') tierCounts.B++;
            else if (tier.tier === 'C') tierCounts.C++;
            else if (tier.tier === 'D') tierCounts.D++;
            else tierCounts.none++;
            const reason = String(d.screening_reason || '').trim();
            if (reason) {
                screeningReasons.set(reason, (screeningReasons.get(reason) || 0) + 1);
            }
        });

        return {
            total: totalDesigns, // Use API total, not current page length
            pageSize: designs.length, // Current page count
            favorites: designs.filter(d => d.is_favorite).length,
            avgPlddt: plddts.length ? plddts.reduce((a, b) => a + b, 0) / plddts.length : null,
            avgPae: paes.length ? paes.reduce((a, b) => a + b, 0) / paes.length : null,
            avgPtm: ptms.length ? ptms.reduce((a, b) => a + b, 0) / ptms.length : null,
            avgAffinity: affinities.length ? affinities.reduce((a, b) => a + b, 0) / affinities.length : null,
            avgBinderProb: binderProbs.length ? binderProbs.reduce((a, b) => a + b, 0) / binderProbs.length : null,
            avgEpitopeContacts: epitopeContacts.length ? epitopeContacts.reduce((a, b) => a + b, 0) / epitopeContacts.length : null,
            avgTargetContacts: targetContacts.length ? targetContacts.reduce((a, b) => a + b, 0) / targetContacts.length : null,
            avgEpitopeDistance: epitopeDistances.length ? epitopeDistances.reduce((a, b) => a + b, 0) / epitopeDistances.length : null,
            avgTargetDistance: targetDistances.length ? targetDistances.reduce((a, b) => a + b, 0) / targetDistances.length : null,
            avgHotspotCoverage: hotspotCoverage.length ? hotspotCoverage.reduce((a, b) => a + b, 0) / hotspotCoverage.length : null,
            avgPsce: psces.length ? psces.reduce((a, b) => a + b, 0) / psces.length : null,
            avgFrustrationHigh: frustrationHigh.length ? frustrationHigh.reduce((a, b) => a + b, 0) / frustrationHigh.length : null,
            avgFrustrationPctHigh: frustrationPct.length ? frustrationPct.reduce((a, b) => a + b, 0) / frustrationPct.length : null,
            annotatedWithFrustration: frustrationHigh.length,
            highConfidence: plddts.filter(v => v >= 80).length,
            lowError: paes.filter(v => v <= 5).length,
            highContacts: epitopeContacts.filter(v => v >= 5).length,
            tierA: tierCounts.A,
            tierB: tierCounts.B,
            tierC: tierCounts.C,
            tierD: tierCounts.D,
            screenPassed,
            screenFailed,
            topScreeningReasons: Array.from(screeningReasons.entries())
                .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
                .slice(0, 4),
        };
    }, [designs, totalDesigns]);
    const rfReviewFallbackStats = useMemo(() => {
        if (!isPostRFantibodyReview || reviewBackboneRows.length === 0) return null;

        const representatives = reviewBackboneRows
            .map((row) => row.representative as ReviewRepresentative | null)
            .filter((representative): representative is ReviewRepresentative => representative != null);
        const average = (values: Array<number | null | undefined>) => {
            const numeric = values.filter(isFiniteNumber);
            return numeric.length ? numeric.reduce((sum, value) => sum + value, 0) / numeric.length : null;
        };
        const total = reviewBackboneTotal || reviewBackboneRows.reduce((sum, row) => sum + (row.count ?? 0), 0);
        const screenPassed = rfReviewSet === 'filtered'
            ? total
            : Math.min(rfFilteredCount, total);
        const screenFailed = Math.max(total - screenPassed, 0);

        return {
            total,
            pageSize: representatives.length,
            favorites: 0,
            avgPlddt: average(representatives.map((representative) => representative.plddt_overall)),
            avgPae: null,
            avgPtm: null,
            avgAffinity: null,
            avgBinderProb: null,
            avgEpitopeContacts: average(representatives.map((representative) => representative.epitope_contact_count)),
            avgTargetContacts: average(representatives.map((representative) => representative.target_contact_count)),
            avgEpitopeDistance: average(representatives.map((representative) => representative.epitope_min_distance)),
            avgTargetDistance: average(representatives.map((representative) => representative.target_min_distance)),
            avgHotspotCoverage: average(representatives.map((representative) => representative.rfa_hotspot_covered_count)),
            avgPsce: null,
            avgFrustrationHigh: null,
            avgFrustrationPctHigh: null,
            annotatedWithFrustration: 0,
            highConfidence: representatives.filter((representative) => (representative.plddt_overall ?? 0) >= 80).length,
            lowError: 0,
            highContacts: representatives.filter((representative) => (representative.epitope_contact_count ?? 0) >= 5).length,
            tierA: 0,
            tierB: 0,
            tierC: 0,
            tierD: 0,
            screenPassed,
            screenFailed,
            topScreeningReasons: [],
            representativeFallback: true,
        };
    }, [isPostRFantibodyReview, reviewBackboneRows, reviewBackboneTotal, rfFilteredCount, rfReviewSet]);
    const overviewStats = stats ?? rfReviewFallbackStats;
    const usingReviewRepresentativeFallback = !stats && !!rfReviewFallbackStats;

    const handleSort = (field: string) => {
        if (sortField === field) {
            setSortDir(d => d === 'asc' ? 'desc' : 'asc');
        } else {
            setSortField(field);
            setSortDir(getDefaultSortDirection(field));
        }
    };

    const updateFilterDraft = <K extends keyof FilterDraftState>(key: K, value: FilterDraftState[K]) => {
        setFilterDraft((current) => ({ ...current, [key]: value }));
    };

    const applyDraftFilters = () => {
        setSortField(filterDraft.sortField);
        setSortDir(filterDraft.sortDir);
        setPlddtMin(filterDraft.plddtMin);
        setIptmMin(filterDraft.iptmMin);
        setContactsMin(filterDraft.contactsMin);
        setTargetContactsMin(filterDraft.targetContactsMin);
        setBinderSizeMin(filterDraft.binderSizeMin);
        setBinderSizeMax(filterDraft.binderSizeMax);
        setCdrH1Min(filterDraft.cdrH1Min);
        setCdrH1Max(filterDraft.cdrH1Max);
        setCdrH2Min(filterDraft.cdrH2Min);
        setCdrH2Max(filterDraft.cdrH2Max);
        setCdrH3Min(filterDraft.cdrH3Min);
        setCdrH3Max(filterDraft.cdrH3Max);
        setEpitopeMaxDist(filterDraft.epitopeMaxDist);
        setTargetMaxDist(filterDraft.targetMaxDist);
        setRogMin(filterDraft.rogMin);
        setRogMax(filterDraft.rogMax);
        setRfdRogMin(filterDraft.rfdRogMin);
        setRfdRogMax(filterDraft.rfdRogMax);
    };

    const filterDraftDirty = (
        filterDraft.sortField !== sortField ||
        filterDraft.sortDir !== sortDir ||
        filterDraft.plddtMin !== plddtMin ||
        filterDraft.iptmMin !== iptmMin ||
        filterDraft.contactsMin !== contactsMin ||
        filterDraft.targetContactsMin !== targetContactsMin ||
        filterDraft.binderSizeMin !== binderSizeMin ||
        filterDraft.binderSizeMax !== binderSizeMax ||
        filterDraft.cdrH1Min !== cdrH1Min ||
        filterDraft.cdrH1Max !== cdrH1Max ||
        filterDraft.cdrH2Min !== cdrH2Min ||
        filterDraft.cdrH2Max !== cdrH2Max ||
        filterDraft.cdrH3Min !== cdrH3Min ||
        filterDraft.cdrH3Max !== cdrH3Max ||
        filterDraft.rogMin !== rogMin ||
        filterDraft.rogMax !== rogMax ||
        filterDraft.rfdRogMin !== rfdRogMin ||
        filterDraft.rfdRogMax !== rfdRogMax ||
        filterDraft.epitopeMaxDist !== epitopeMaxDist ||
        filterDraft.targetMaxDist !== targetMaxDist
    );

    const handleDraftFilterKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            applyDraftFilters();
        }
    };

    const currentSavedFilterState = useMemo<SavedReviewFilterState>(() => ({
        rf_review_set: rfReviewSet,
        output_source_filter: outputSourceFilter,
        sort_field: sortField,
        sort_dir: sortDir,
        filter_text: filterText,
        selected_backbone_id: selectedBackboneId,
        plddt_min: plddtMin,
        iptm_min: iptmMin,
        contacts_min: contactsMin,
        target_contacts_min: targetContactsMin,
        binder_size_min: binderSizeMin,
        binder_size_max: binderSizeMax,
        cdr_h1_min: cdrH1Min,
        cdr_h1_max: cdrH1Max,
        cdr_h2_min: cdrH2Min,
        cdr_h2_max: cdrH2Max,
        cdr_h3_min: cdrH3Min,
        cdr_h3_max: cdrH3Max,
        rog_min: rogMin,
        rog_max: rogMax,
        rfd_rog_min: rfdRogMin,
        rfd_rog_max: rfdRogMax,
        epitope_max_dist: epitopeMaxDist,
        target_max_dist: targetMaxDist,
    }), [
        rfReviewSet,
        outputSourceFilter,
        sortField,
        sortDir,
        filterText,
        selectedBackboneId,
        plddtMin,
        iptmMin,
        contactsMin,
        targetContactsMin,
        binderSizeMin,
        binderSizeMax,
        cdrH1Min,
        cdrH1Max,
        cdrH2Min,
        cdrH2Max,
        cdrH3Min,
        cdrH3Max,
        rogMin,
        rogMax,
        rfdRogMin,
        rfdRogMax,
        epitopeMaxDist,
        targetMaxDist,
    ]);
    useEffect(() => {
        if (!appliedSavedReviewFilterSet) return;
        if (!savedReviewStatesEqual(appliedSavedReviewFilterSet.filter_state, currentSavedFilterState)) {
            setAppliedSavedFilterSetId(null);
        }
    }, [appliedSavedReviewFilterSet, currentSavedFilterState]);

    const applySavedReviewFilterSet = (filterSet: SavedReviewFilterSet) => {
        const nextState = filterSet.filter_state || {};
        const nextSortField = typeof nextState.sort_field === 'string' && nextState.sort_field.trim()
            ? nextState.sort_field
            : 'name';
        const nextSortDir = nextState.sort_dir === 'desc' ? 'desc' : 'asc';
        const nextRfReviewSet = nextState.rf_review_set === 'raw' ? 'raw' : 'filtered';
        const nextOutputSourceFilter = (nextState.output_source_filter === 'rfantibody'
            || nextState.output_source_filter === 'fampnn'
            || nextState.output_source_filter === 'validation'
            || nextState.output_source_filter === 'all')
            ? nextState.output_source_filter
            : 'all';
        const nextBackboneId = typeof nextState.selected_backbone_id === 'number' && Number.isFinite(nextState.selected_backbone_id)
            ? nextState.selected_backbone_id
            : null;

        setRfReviewSet(nextRfReviewSet);
        setOutputSourceFilter(nextOutputSourceFilter);
        setSortField(nextSortField);
        setSortDir(nextSortDir);
        setFilterText(typeof nextState.filter_text === 'string' ? nextState.filter_text : '');
        setSelectedBackboneId(nextBackboneId);
        setPlddtMin(typeof nextState.plddt_min === 'number' ? nextState.plddt_min : 0);
        setIptmMin(typeof nextState.iptm_min === 'number' ? nextState.iptm_min : 0);
        setContactsMin(typeof nextState.contacts_min === 'number' ? nextState.contacts_min : 0);
        setTargetContactsMin(typeof nextState.target_contacts_min === 'number' ? nextState.target_contacts_min : 0);
        setBinderSizeMin(typeof nextState.binder_size_min === 'string' ? nextState.binder_size_min : '');
        setBinderSizeMax(typeof nextState.binder_size_max === 'string' ? nextState.binder_size_max : '');
        setCdrH1Min(typeof nextState.cdr_h1_min === 'string' ? nextState.cdr_h1_min : '');
        setCdrH1Max(typeof nextState.cdr_h1_max === 'string' ? nextState.cdr_h1_max : '');
        setCdrH2Min(typeof nextState.cdr_h2_min === 'string' ? nextState.cdr_h2_min : '');
        setCdrH2Max(typeof nextState.cdr_h2_max === 'string' ? nextState.cdr_h2_max : '');
        setCdrH3Min(typeof nextState.cdr_h3_min === 'string' ? nextState.cdr_h3_min : '');
        setCdrH3Max(typeof nextState.cdr_h3_max === 'string' ? nextState.cdr_h3_max : '');
        setRogMin(typeof nextState.rog_min === 'string' ? nextState.rog_min : '');
        setRogMax(typeof nextState.rog_max === 'string' ? nextState.rog_max : '');
        setRfdRogMin(typeof nextState.rfd_rog_min === 'string' ? nextState.rfd_rog_min : '');
        setRfdRogMax(typeof nextState.rfd_rog_max === 'string' ? nextState.rfd_rog_max : '');
        setEpitopeMaxDist(typeof nextState.epitope_max_dist === 'string' ? nextState.epitope_max_dist : '');
        setTargetMaxDist(typeof nextState.target_max_dist === 'string' ? nextState.target_max_dist : '');
        setFilterDraft({
            sortField: nextSortField,
            sortDir: nextSortDir,
            plddtMin: typeof nextState.plddt_min === 'number' ? nextState.plddt_min : 0,
            iptmMin: typeof nextState.iptm_min === 'number' ? nextState.iptm_min : 0,
            contactsMin: typeof nextState.contacts_min === 'number' ? nextState.contacts_min : 0,
            targetContactsMin: typeof nextState.target_contacts_min === 'number' ? nextState.target_contacts_min : 0,
            binderSizeMin: typeof nextState.binder_size_min === 'string' ? nextState.binder_size_min : '',
            binderSizeMax: typeof nextState.binder_size_max === 'string' ? nextState.binder_size_max : '',
            cdrH1Min: typeof nextState.cdr_h1_min === 'string' ? nextState.cdr_h1_min : '',
            cdrH1Max: typeof nextState.cdr_h1_max === 'string' ? nextState.cdr_h1_max : '',
            cdrH2Min: typeof nextState.cdr_h2_min === 'string' ? nextState.cdr_h2_min : '',
            cdrH2Max: typeof nextState.cdr_h2_max === 'string' ? nextState.cdr_h2_max : '',
            cdrH3Min: typeof nextState.cdr_h3_min === 'string' ? nextState.cdr_h3_min : '',
            cdrH3Max: typeof nextState.cdr_h3_max === 'string' ? nextState.cdr_h3_max : '',
            rogMin: typeof nextState.rog_min === 'string' ? nextState.rog_min : '',
            rogMax: typeof nextState.rog_max === 'string' ? nextState.rog_max : '',
            rfdRogMin: typeof nextState.rfd_rog_min === 'string' ? nextState.rfd_rog_min : '',
            rfdRogMax: typeof nextState.rfd_rog_max === 'string' ? nextState.rfd_rog_max : '',
            epitopeMaxDist: typeof nextState.epitope_max_dist === 'string' ? nextState.epitope_max_dist : '',
            targetMaxDist: typeof nextState.target_max_dist === 'string' ? nextState.target_max_dist : '',
        });
        setAppliedSavedFilterSetId(filterSet.id);
        setSelectedDesignIds([]);
        setCurrentPage(1);
        setIterationMessage({ kind: 'success', text: `Loaded saved dataset '${filterSet.name}'.` });
    };

    const clearRfaFilters = () => {
        setFilterText('');
        setPlddtMin(0);
        setIptmMin(0);
        setContactsMin(0);
        setTargetContactsMin(0);
        setBinderSizeMin('');
        setBinderSizeMax('');
        setCdrH1Min('');
        setCdrH1Max('');
        setCdrH2Min('');
        setCdrH2Max('');
        setCdrH3Min('');
        setCdrH3Max('');
        setEpitopeMaxDist('');
        setTargetMaxDist('');
        setRogMin('');
        setRogMax('');
        setRfdRogMin('');
        setRfdRogMax('');
        setFilterDraft((current) => ({
            ...current,
            plddtMin: 0,
            iptmMin: 0,
            contactsMin: 0,
            targetContactsMin: 0,
            binderSizeMin: '',
            binderSizeMax: '',
            cdrH1Min: '',
            cdrH1Max: '',
            cdrH2Min: '',
            cdrH2Max: '',
            cdrH3Min: '',
            cdrH3Max: '',
            epitopeMaxDist: '',
            targetMaxDist: '',
            rogMin: '',
            rogMax: '',
            rfdRogMin: '',
            rfdRogMax: '',
        }));
        setCurrentPage(1);
    };

    const toggleDesignSelection = (designId: string, selected: boolean) => {
        setSelectedDesignIds((current) => {
            const currentSet = new Set(current);
            if (selected) currentSet.add(designId);
            else currentSet.delete(designId);
            return Array.from(currentSet);
        });
    };

    const clearSelectedDesigns = () => {
        setSelectedDesignIds([]);
    };

    const selectVisibleDesigns = () => {
        setSelectedDesignIds((current) => Array.from(new Set([...current, ...visibleDesignIds])));
    };

    const bulkSelectionMutation = useMutation({
        mutationFn: async ({ mode, topN }: { mode: 'all_filtered' | 'top_ranked'; topN?: number }) => {
            if (!selectedJobId) {
                throw new Error('Select a job before building a working set.');
            }
            const response = await fetchDesigns(bulkSelectionFilters);
            if (response.data.total > MAX_BULK_SELECTION_DESIGNS) {
                throw new Error(`Filtered result set exceeds ${MAX_BULK_SELECTION_DESIGNS.toLocaleString()} outputs. Narrow the filters first.`);
            }
            const scopedDesigns = outputSourceFilter === 'all'
                ? response.data.designs
                : response.data.designs.filter((design) => inferDesignOutputSource(design as any) === outputSourceFilter);
            if (mode === 'top_ranked') {
                return scopedDesigns.slice(0, Math.max(1, topN || 1)).map((design) => design.id);
            }
            return scopedDesigns.map((design) => design.id);
        },
        onSuccess: (designIds, variables) => {
            setSelectedDesignIds((current) => Array.from(new Set([...current, ...designIds])));
            if (designIds.length === 0) {
                setIterationMessage({ kind: 'error', text: 'No outputs matched the current filtered set.' });
                return;
            }
            const message = variables.mode === 'all_filtered'
                ? `Added ${designIds.length.toLocaleString()} filtered outputs to the re-orchestration set.`
                : `Added the top ${designIds.length.toLocaleString()} filtered outputs to the re-orchestration set.`;
            setIterationMessage({ kind: 'success', text: message });
        },
        onError: (error) => {
            setIterationMessage({ kind: 'error', text: getErrorMessage(error) });
        },
    });

    const saveFilterSetMutation = useMutation({
        mutationFn: async () => {
            if (!selectedJobId) {
                throw new Error('Select a job before saving a dataset.');
            }
            const response = await fetchDesigns(bulkSelectionFilters);
            if (response.data.total > MAX_BULK_SELECTION_DESIGNS) {
                throw new Error(`Filtered result set exceeds ${MAX_BULK_SELECTION_DESIGNS.toLocaleString()} outputs. Narrow the filters first before saving a dataset.`);
            }
            const scopedDesigns = outputSourceFilter === 'all'
                ? response.data.designs
                : response.data.designs.filter((design) => inferDesignOutputSource(design as any) === outputSourceFilter);
            if (scopedDesigns.length === 0) {
                throw new Error('No outputs matched the current filtered set.');
            }
            return saveReviewFilterSet(selectedJobId, {
                name: savedFilterSetName.trim() || undefined,
                visible_count: scopedDesigns.length,
                source_total_count: scopedDesigns.length,
                design_ids: scopedDesigns.map((design) => design.id),
                filter_state: currentSavedFilterState as Record<string, unknown>,
            });
        },
        onSuccess: (response) => {
            setSavedFilterSetName('');
            setAppliedSavedFilterSetId(response.data.filter_set.id);
            setIterationMessage({ kind: 'success', text: response.data.message });
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            queryClient.invalidateQueries({ queryKey: ['jobs', 'include_children'] });
        },
        onError: (error) => {
            setIterationMessage({ kind: 'error', text: getErrorMessage(error) });
        },
    });

    const deleteFilterSetMutation = useMutation({
        mutationFn: async (filterSetId: string) => {
            if (!selectedJobId) {
                throw new Error('Select a job before deleting a saved dataset.');
            }
            return deleteReviewFilterSet(selectedJobId, filterSetId);
        },
        onSuccess: (response, filterSetId) => {
            if (appliedSavedFilterSetId === filterSetId) {
                setAppliedSavedFilterSetId(null);
            }
            setIterationMessage({ kind: 'success', text: response.data.message });
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            queryClient.invalidateQueries({ queryKey: ['jobs', 'include_children'] });
        },
        onError: (error) => {
            setIterationMessage({ kind: 'error', text: getErrorMessage(error) });
        },
    });

    const selectAllFilteredDesigns = () => {
        setIterationMessage(null);
        bulkSelectionMutation.mutate({ mode: 'all_filtered' });
    };

    const selectTopRankedDesigns = () => {
        setIterationMessage(null);
        bulkSelectionMutation.mutate({ mode: 'top_ranked', topN: parsedTopSelectionCount });
    };

    const toggleVisibleSelection = (selected: boolean) => {
        if (selected) {
            selectVisibleDesigns();
            return;
        }
        setSelectedDesignIds((current) => current.filter((designId) => !visibleDesignIds.includes(designId)));
    };

    const openPipelineReorchestration = (savedFilterSet?: SavedReviewFilterSet | null) => {
        if (!activeJob) {
            setIterationMessage({ kind: 'error', text: 'Select a job before opening Pipeline Re-orchestration.' });
            return;
        }

        const launchDesignIds = savedFilterSet ? [] : selectedDesignIds;
        const resolvedSavedFilterSet = savedFilterSet ?? (launchDesignIds.length === 0 ? loadedSavedReviewFilterSet : null);
        if (launchDesignIds.length === 0 && !resolvedSavedFilterSet) {
            setIterationMessage({
                kind: 'error',
                text: 'Select at least one design or load a saved dataset before opening Pipeline Re-orchestration.',
            });
            return;
        }

        const savedFilterState = resolvedSavedFilterSet?.filter_state ?? {};
        const savedSourceArtifactGroup = savedFilterState.rf_review_set === 'raw'
            ? 'raw'
            : savedFilterState.rf_review_set === 'filtered'
                ? 'filtered'
                : null;
        const savedSourceOutputFilter = typeof savedFilterState.output_source_filter === 'string'
            ? savedFilterState.output_source_filter
            : null;
        const savedSourceSortField = typeof savedFilterState.sort_field === 'string'
            ? savedFilterState.sort_field
            : null;
        const savedSourceSortDir = savedFilterState.sort_dir === 'desc' ? 'desc' : 'asc';
        const savedDatasetCount = resolvedSavedFilterSet?.design_ids?.length
            ?? resolvedSavedFilterSet?.visible_count
            ?? null;

        navigate('/submit?template=antibody_denovo', {
            state: {
                refinementMode: true,
                sourceJobId: activeJob.id,
                sourceArtifactGroup: resolvedSavedFilterSet ? savedSourceArtifactGroup : activeRfArtifactGroup,
                sourceOutputSourceFilter: resolvedSavedFilterSet ? savedSourceOutputFilter : outputSourceFilter,
                sourceSortField: resolvedSavedFilterSet ? savedSourceSortField : sortField,
                sourceSortDir: resolvedSavedFilterSet ? savedSourceSortDir : sortDir,
                sourceVisibleCount: resolvedSavedFilterSet ? savedDatasetCount : tableDesigns.length,
                sourceTotalCount: resolvedSavedFilterSet ? savedDatasetCount : totalDesigns,
                selectedDesignIds: launchDesignIds.length > 0 ? launchDesignIds : undefined,
                sourceSavedFilterSetId: resolvedSavedFilterSet?.id,
                sourceSavedFilterSetName: resolvedSavedFilterSet?.name,
                sourceSavedFilterSetCreatedAt: resolvedSavedFilterSet?.created_at,
                sourceSavedFilterSetDesignCount: savedDatasetCount,
                reviewFilterSetId: resolvedSavedFilterSet?.id,
                reviewFilterSetName: resolvedSavedFilterSet?.name,
                reviewFilterSetCreatedAt: resolvedSavedFilterSet?.created_at,
                reviewFilterSetDesignCount: savedDatasetCount,
            }
        });
    };

    const getErrorMessage = (error: unknown): string => {
        const detail = (error as any)?.response?.data?.detail;
        if (typeof detail === 'string') return detail;
        if (Array.isArray(detail)) return detail.join(', ');
        if (detail && typeof detail === 'object') return JSON.stringify(detail);
        if (error instanceof Error) return error.message;
        return 'Launch failed';
    };

    const launchIterationMutation = useMutation({
        mutationFn: async ({
            action,
            cdrIndelConfig,
            paramOverrides,
        }: {
            action: AntibodyIterationAction;
            cdrIndelConfig?: AntibodyCdrIndelConfig;
            paramOverrides?: Record<string, unknown>;
        }) => {
            if (!selectedJobId) {
                throw new Error('Select a job before launching a new round.');
            }
            if (!canLaunchWorkingSet) {
                throw new Error('Select at least one design or load a saved dataset before launching a new round.');
            }
            return launchAntibodyIteration({
                source_job_id: selectedJobId,
                design_ids: selectedDesignIds,
                review_filter_set_id: activeLaunchReviewFilterSetId,
                action,
                cdr_indel_config: cdrIndelConfig,
                param_overrides: paramOverrides,
            });
        },
        onSuccess: (response) => {
            const launchedJob = response.data.launched_job;
            setIterationMessage({
                kind: 'success',
                text: `${response.data.message} New job: ${launchedJob.name} (${launchedJob.id}).`,
            });
            setShowCdrIndelModal(false);
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            queryClient.invalidateQueries({ queryKey: ['jobs', 'include_children'] });
        },
        onError: (error) => {
            setIterationMessage({
                kind: 'error',
                text: getErrorMessage(error),
            });
        },
    });

    const launchManualMutagenesisMutation = useMutation({
        mutationFn: async () => {
            if (!selectedJobId) {
                throw new Error('Select a job before launching a new round.');
            }
            if (!canLaunchWorkingSet) {
                throw new Error('Select at least one design or load a saved dataset before launching a new round.');
            }
            const mutationSets = manualMutagenesisConfig.mutation_sets_text
                .split('\n')
                .map((entry) => entry.trim())
                .filter(Boolean);
            if (mutationSets.length === 0) {
                throw new Error('Add at least one manual mutation set, one per line.');
            }
            return launchManualMutagenesis({
                source_job_id: selectedJobId,
                design_ids: selectedDesignIds,
                review_filter_set_id: activeLaunchReviewFilterSetId,
                config: {
                    chain_id: manualMutagenesisConfig.chain_id?.trim() || undefined,
                    mutation_sets: mutationSets,
                    predictor: manualMutagenesisConfig.predictor,
                    msa_provider: manualMutagenesisConfig.msa_provider,
                },
            });
        },
        onSuccess: (response) => {
            const launchedJob = response.data.launched_job;
            setIterationMessage({
                kind: 'success',
                text: `${response.data.message} New job: ${launchedJob.name} (${launchedJob.id}).`,
            });
            setShowManualMutagenesisModal(false);
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            queryClient.invalidateQueries({ queryKey: ['jobs', 'include_children'] });
        },
        onError: (error) => {
            setIterationMessage({
                kind: 'error',
                text: getErrorMessage(error),
            });
        },
    });

    const launchBusy = launchIterationMutation.isPending || launchManualMutagenesisMutation.isPending;
    const manualMutationSetCount = manualMutagenesisConfig.mutation_sets_text
        .split('\n')
        .map((entry) => entry.trim())
        .filter(Boolean).length;

    return (
        <div className="min-h-screen bg-slate-950 text-slate-200">
            {/* Background */}
            <div className="fixed inset-0 pointer-events-none">
                <div className="absolute top-0 left-0 w-1/2 h-1/2 bg-blue-500/5 rounded-full blur-[150px]" />
                <div className="absolute bottom-0 right-0 w-1/2 h-1/2 bg-violet-500/5 rounded-full blur-[150px]" />
            </div>

            <div className="relative z-10 w-full px-3 sm:px-4 lg:px-5 xl:px-6 2xl:px-8">
                {/* Header */}
                <div className="flex flex-col md:flex-row justify-between items-start md:items-center mb-6 gap-4">
                    <div>
                        <h1 className="text-3xl font-bold text-white">Results Viewer</h1>
                        <p className="text-slate-400 text-sm mt-1">
                            {activeJob ? `${activeJob.name} • ${activeJob.model_id}` : 'Select a job to analyze'}
                        </p>
                    </div>

                    {/* Smart Job Selector */}
                    <div className="flex items-center gap-3">
                        <div className="relative">
                            <select
                                value={selectedJobId}
                                onChange={handleJobChange}
                                className="bg-slate-800/80 text-white border border-slate-600 rounded-xl pl-4 pr-10 py-3 min-w-[400px] focus:ring-2 focus:ring-blue-500 focus:border-blue-500 appearance-none cursor-pointer font-medium shadow-lg transition-all hover:border-slate-500"
                            >
                                <option value="">Select a job...</option>
                                {(() => {
                                    const sortedJobs = [...nonNgsJobs].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

                                    // Build batch-based aggregation map for jobs sharing batch_id
                                    // This handles mutagenesis workflows where child jobs have batch_id but no parent_job_id
                                    const batchMap = new Map<string, { msaJob: Job | null; children: Job[]; totalDesigns: number }>();
                                    const childJobIds = new Set<string>();

                                    // First pass: group jobs by batch_id
                                    sortedJobs.forEach(job => {
                                        // Also handle parent_job_id for other workflows
                                        if (job.parent_job_id) {
                                            childJobIds.add(job.id);
                                        }

                                        // Group by batch_id
                                        if (job.batch_id) {
                                            const isMsaJob = job.mode === 'msa_generation' || job.name.endsWith('_msa');
                                            const existing = batchMap.get(job.batch_id);

                                            if (existing) {
                                                if (isMsaJob) {
                                                    // MSA job found - it becomes the parent
                                                    existing.msaJob = job;
                                                    // Also add MSA job's designs (usually 0)
                                                    existing.totalDesigns += job.design_count || 0;
                                                } else {
                                                    // Add as child
                                                    existing.children.push(job);
                                                    existing.totalDesigns += job.design_count || 0;
                                                    childJobIds.add(job.id);
                                                }
                                            } else {
                                                // First job in this batch
                                                if (isMsaJob) {
                                                    batchMap.set(job.batch_id, {
                                                        msaJob: job,
                                                        children: [],
                                                        totalDesigns: job.design_count || 0
                                                    });
                                                } else {
                                                    // Non-MSA job encountered first - still add to children, mark for aggregation
                                                    batchMap.set(job.batch_id, {
                                                        msaJob: null,
                                                        children: [job],
                                                        totalDesigns: job.design_count || 0
                                                    });
                                                    childJobIds.add(job.id); // Also mark as child!
                                                }
                                            }
                                        }
                                    });

                                    const elements: React.ReactNode[] = [];

                                    // Render jobs (skip child jobs entirely)
                                    sortedJobs.forEach(job => {
                                        // Skip child jobs - they're aggregated under parent/MSA
                                        if (childJobIds.has(job.id)) {
                                            return;
                                        }

                                        const date = new Date(job.created_at);
                                        const dateStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                                        const timeStr = date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
                                        const statusIcon = job.status === 'completed' ? '✓' : job.status === 'running' ? '⟳' : job.status === 'failed' ? '✗' : '○';

                                        // Check if this job has batch children
                                        const batchData = job.batch_id ? batchMap.get(job.batch_id) : null;
                                        const hasChildren = batchData && batchData.children.length > 0;
                                        const displayDesigns = (() => {
                                            if (isPostRfantibodyStage(job)) {
                                                const rawCount = Number(job.awaiting_payload?.raw_candidate_count || 0);
                                                const screenedCount = Number(job.awaiting_payload?.filtered_candidate_count || 0);
                                                if (screenedCount > 0) {
                                                    return `Raw ${rawCount.toLocaleString()} / Screened ${screenedCount.toLocaleString()}`;
                                                }
                                                if (rawCount > 0) {
                                                    return `Raw ${rawCount.toLocaleString()}`;
                                                }
                                            }
                                            const total = hasChildren ? batchData.totalDesigns : (job.design_count || 0);
                                            return `${total.toLocaleString()} designs`;
                                        })();
                                        const childIndicator = hasChildren ? ` (${batchData.children.length} variants)` : '';

                                        elements.push(
                                            <option key={job.id} value={job.id}>
                                                {statusIcon} {job.name} │ {dateStr} {timeStr} │ {job.model_id || job.mode}{childIndicator} │ {displayDesigns}
                                            </option>
                                        );
                                    });

                                    return elements;
                                })()}
                            </select>
                            {/* Custom dropdown arrow */}
                            <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none">
                                <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                                </svg>
                            </div>
                        </div>

                        {/* Quick job info badge */}
                        {activeJob && (
                            <div className="flex items-center gap-2 px-3 py-2 bg-slate-800/60 border border-slate-700 rounded-lg text-xs">
                                <span className={`w-2 h-2 rounded-full ${activeJob.status === 'completed' ? 'bg-emerald-400' :
                                    activeJob.status === 'running' ? 'bg-blue-400 animate-pulse' :
                                        activeJob.status === 'failed' ? 'bg-red-400' : 'bg-slate-400'
                                    }`}></span>
                                <span className="text-slate-300 font-medium">{activeBadgeLabel}</span>
                            </div>
                        )}
                    </div>
                </div>

                {nonNgsJobs.length === 0 && (
                    <div className="bg-slate-900/50 rounded-xl border border-slate-800 p-8 text-center text-slate-400">
                        No protein workflow jobs available for Data Viewer. NGS jobs are available in NGS Data Visualization Toolkit.
                    </div>
                )}

                {activeJob && (
                    <>
                        {isPostRFantibodyReview && (
                            <div className="mb-4 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
                                <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
                                    <div>
                                        <div className="text-sm font-semibold text-emerald-200">Paused after RFantibody backbone generation</div>
                                        <div className="text-xs text-slate-300">
                                            Review this stage by backbone family first. The UI is using existing <span className="font-mono text-emerald-300">backbone_id</span> as the first family primitive.
                                        </div>
                                    </div>
                                    <div className="flex flex-wrap gap-2 text-[11px] text-slate-300">
                                        {gateRawBackboneSummary?.total != null && (
                                            <span className="rounded-lg border border-slate-700 bg-slate-900/70 px-2 py-1">
                                                Raw {gateRawBackboneSummary.total}
                                            </span>
                                        )}
                                        {(gateFilteredBackboneSummary?.total != null || gateCandidateBackboneSummary?.total != null) && (
                                            <span className="rounded-lg border border-slate-700 bg-slate-900/70 px-2 py-1">
                                                Screened {gateFilteredBackboneSummary?.total ?? gateCandidateBackboneSummary?.total ?? 0}
                                            </span>
                                        )}
                                        <span className="rounded-lg border border-slate-700 bg-slate-900/70 px-2 py-1">
                                            Backbone families {reviewBackboneRows.length}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Tabs */}
                        <div className="flex gap-1 mb-6 border-b border-slate-800 pb-px">
                            {TABS.map(tab => (
                                <button
                                    key={tab.id}
                                    onClick={() => setActiveTab(tab.id)}
                                    className={`px-5 py-2.5 text-sm font-medium rounded-t-lg transition-colors flex items-center gap-2 ${activeTab === tab.id
                                        ? 'bg-slate-800 text-white border-b-2 border-blue-500 -mb-px'
                                        : 'text-slate-400 hover:text-white hover:bg-slate-800/50'
                                        }`}
                                >
                                    {tab.icon && <span className="opacity-70 text-xs uppercase tracking-wider">{tab.icon}</span>}
                                    {tab.label}
                                </button>
                            ))}
                        </div>

                        {/* Global Pagination Bar - visible on all tabs */}
                        {totalDesigns > 0 && (
                            <div className="flex items-center justify-between px-4 py-2 mb-2 bg-gradient-to-r from-slate-800/60 to-slate-900/60 rounded-lg border border-slate-700/50">
                                <div className="flex items-center gap-3">
                                    <span className="text-xs text-slate-400">
                                        {pageSize === 0 ? (
                                            <>Showing <span className="text-blue-400 font-semibold">all {totalDesigns.toLocaleString()}</span> {paginationSubject}</>
                                        ) : (
                                            <>Showing <span className="text-white font-medium">{((currentPage - 1) * pageSize) + 1}-{Math.min(currentPage * pageSize, totalDesigns)}</span> of <span className="text-blue-400 font-semibold">{totalDesigns.toLocaleString()}</span> {paginationSubject}</>
                                        )}
                                        {sortField !== 'name' && <span className="ml-2 text-emerald-400">(sorted by {sortField} {sortDir})</span>}
                                    </span>
                                    <select
                                        value={pageSize}
                                        onChange={(e) => {
                                            setPageSize(Number(e.target.value));
                                            setCurrentPage(1);
                                        }}
                                        className="bg-slate-700 border border-slate-600 rounded px-2 py-0.5 text-xs text-white cursor-pointer"
                                    >
                                        {PAGE_SIZE_OPTIONS.map(size => (
                                            <option key={size} value={size}>{size === 0 ? 'All' : `${size} per page`}</option>
                                        ))}
                                    </select>
                                </div>
                                {/* Only show nav if not viewing all */}
                                {pageSize !== 0 && totalPages > 1 && (
                                    <div className="flex items-center gap-1">
                                        <button
                                            onClick={() => setCurrentPage(1)}
                                            disabled={currentPage === 1}
                                            className="px-2 py-0.5 text-xs bg-slate-700 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed rounded"
                                        >
                                            ⏮
                                        </button>
                                        <button
                                            onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                                            disabled={currentPage === 1}
                                            className="px-2 py-0.5 text-xs bg-slate-700 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed rounded"
                                        >
                                            ←
                                        </button>
                                        <span className="px-2 py-0.5 text-xs text-white bg-blue-600/30 rounded border border-blue-500/40">
                                            {currentPage} / {totalPages}
                                        </span>
                                        <button
                                            onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                                            disabled={currentPage >= totalPages}
                                            className="px-2 py-0.5 text-xs bg-slate-700 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed rounded"
                                        >
                                            →
                                        </button>
                                        <button
                                            onClick={() => setCurrentPage(totalPages)}
                                            disabled={currentPage >= totalPages}
                                            className="px-2 py-0.5 text-xs bg-slate-700 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed rounded"
                                        >
                                            ⏭
                                        </button>
                                    </div>
                                )}
                            </div>
                        )}

                        {isAntibodyContext && (
                            <div className="mb-4 rounded-xl border border-indigo-500/25 bg-indigo-500/5 p-4">
                                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                    <div>
                                        <div className="text-sm font-medium text-indigo-100">Pipeline Re-orchestration</div>
                                        <p className="mt-1 text-xs text-slate-400">
                                            Sort and filter the current output set, then promote visible, filtered, or top-ranked outputs into a working set for relaunch through the main workflow UI.
                                        </p>
                                        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                                            <span className="rounded-full border border-indigo-500/30 bg-slate-900/70 px-2 py-1 text-indigo-100">
                                                {selectedDesignIds.length} selected
                                            </span>
                                            {loadedSavedReviewFilterSet && (
                                                <span className="rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-1 text-sky-100">
                                                    Loaded dataset '{loadedSavedReviewFilterSet.name}' ({loadedSavedDatasetDesignCount.toLocaleString()} outputs)
                                                </span>
                                            )}
                                            <span className="rounded-full border border-slate-700 bg-slate-900/70 px-2 py-1 text-slate-400">
                                                {tableDesigns.length} visible in {loadedSavedReviewFilterSet ? `'${loadedSavedReviewFilterSet.name}'` : activeReviewSetLabel}
                                            </span>
                                            {loadedSavedReviewFilterSet && (
                                                <span className="rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-1 text-sky-100">
                                                    Saved dataset
                                                </span>
                                            )}
                                            {loadedSavedReviewFilterSet && selectedDesignIds.length > 0 && (
                                                <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-amber-200">
                                                    Manual selection overrides the loaded dataset at launch
                                                </span>
                                            )}
                                            {isPostRFantibodyReview && (
                                                <>
                                                    <span className={`rounded-full border px-2 py-1 ${rfReviewSet === 'raw' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200' : 'border-slate-700 bg-slate-900/70 text-slate-400'}`}>
                                                        Raw {rfRawCount.toLocaleString()}
                                                    </span>
                                                    <span className={`rounded-full border px-2 py-1 ${rfReviewSet === 'filtered' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200' : 'border-slate-700 bg-slate-900/70 text-slate-400'}`}>
                                                        Screened {rfFilteredCount.toLocaleString()}
                                                    </span>
                                                </>
                                            )}
                                            {!isPostRFantibodyReview && (
                                                <span className="rounded-full border border-slate-700 bg-slate-900/70 px-2 py-1 text-slate-400">
                                                    {totalDesigns.toLocaleString()} total after filters
                                                </span>
                                            )}
                                            {launchBusy && (
                                                <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-amber-200">
                                                    Launching {launchIterationMutation.isPending
                                                        ? launchIterationMutation.variables?.action
                                                        : 'manual_mutagenesis'}...
                                                </span>
                                            )}
                                            {bulkSelectionMutation.isPending && (
                                                <span className="rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-1 text-sky-200">
                                                    Building working set...
                                                </span>
                                            )}
                                        </div>
                                        <div className="mt-3 flex flex-wrap items-center gap-2">
                                            <input
                                                type="text"
                                                value={savedFilterSetName}
                                                onChange={(event) => setSavedFilterSetName(event.target.value)}
                                                placeholder="Save current dataset..."
                                                className="min-w-[220px] rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-xs text-slate-100 outline-none placeholder:text-slate-500"
                                            />
                                            <button
                                                type="button"
                                                onClick={() => {
                                                    setIterationMessage(null);
                                                    saveFilterSetMutation.mutate();
                                                }}
                                                disabled={!selectedJobId || saveFilterSetMutation.isPending}
                                                className="rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-3 py-2 text-xs text-indigo-100 transition-colors hover:border-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
                                            >
                                                {saveFilterSetMutation.isPending ? 'Saving…' : 'Save Dataset'}
                                            </button>
                                        </div>
                                        {savedReviewFilterSets.length > 0 && (
                                            <div className="mt-3 flex flex-wrap gap-2">
                                                {savedReviewFilterSets.map((filterSet) => (
                                                    <div key={filterSet.id} className="min-w-[220px] rounded-xl border border-slate-700/70 bg-slate-900/65 px-3 py-2 text-xs">
                                                        <div className="flex items-start justify-between gap-3">
                                                                <div>
                                                                    <div className="font-medium text-slate-100">{filterSet.name}</div>
                                                                    <div className="mt-1 flex flex-wrap gap-2 text-[10px] text-slate-400">
                                                                        <span>{filterSet.filter_state.rf_review_set === 'raw' ? 'Raw' : 'Screened'}</span>
                                                                        {filterSet.filter_state.selected_backbone_id != null && (
                                                                            <span>BB {filterSet.filter_state.selected_backbone_id}</span>
                                                                        )}
                                                                        {filterSet.design_ids?.length ? (
                                                                            <span>{filterSet.design_ids.length.toLocaleString()} frozen outputs</span>
                                                                        ) : filterSet.visible_count != null && (
                                                                            <span>{filterSet.visible_count.toLocaleString()} visible</span>
                                                                        )}
                                                                    </div>
                                                                </div>
                                                                <div className="flex gap-2">
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => applySavedReviewFilterSet(filterSet)}
                                                                        className="rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-[10px] text-emerald-100"
                                                                    >
                                                                        {appliedSavedFilterSetId === filterSet.id ? 'Loaded' : 'Load Dataset'}
                                                                    </button>
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => openPipelineReorchestration(filterSet)}
                                                                        className="rounded border border-indigo-500/40 bg-indigo-500/10 px-2 py-1 text-[10px] text-indigo-100"
                                                                    >
                                                                        Re-orchestrate
                                                                    </button>
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => {
                                                                            setIterationMessage(null);
                                                                        deleteFilterSetMutation.mutate(filterSet.id);
                                                                    }}
                                                                    disabled={deleteFilterSetMutation.isPending}
                                                                    className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-[10px] text-slate-300 disabled:cursor-not-allowed disabled:opacity-50"
                                                                >
                                                                    Delete
                                                                </button>
                                                            </div>
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>

                                    <div className="flex flex-wrap gap-2">
                                        <button
                                            type="button"
                                            onClick={selectVisibleDesigns}
                                            disabled={visibleDesignIds.length === 0}
                                            className="rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-2 text-xs text-slate-200 transition-colors hover:border-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
                                        >
                                            {allVisibleSelected ? 'Visible Rows Selected' : 'Add Visible Rows'}
                                        </button>
                                        <button
                                            type="button"
                                            onClick={selectAllFilteredDesigns}
                                            disabled={totalDesigns === 0 || bulkSelectionMutation.isPending}
                                            className="rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-2 text-xs text-slate-200 transition-colors hover:border-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
                                        >
                                            Select All Filtered
                                        </button>
                                        <div className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/70 px-2 py-1.5">
                                            <input
                                                type="number"
                                                min={1}
                                                max={MAX_BULK_SELECTION_DESIGNS}
                                                value={topSelectionCount}
                                                onChange={(event) => setTopSelectionCount(event.target.value)}
                                                className="w-16 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-100 outline-none"
                                            />
                                            <button
                                                type="button"
                                                onClick={selectTopRankedDesigns}
                                                disabled={totalDesigns === 0 || bulkSelectionMutation.isPending || parsedTopSelectionCount < 1}
                                                className="text-xs font-medium text-slate-200 transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                                            >
                                                Select Top N
                                            </button>
                                        </div>
                                        <button
                                            type="button"
                                            onClick={clearSelectedDesigns}
                                            disabled={selectedDesignIds.length === 0}
                                            className="rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-2 text-xs text-slate-200 transition-colors hover:border-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
                                        >
                                            Clear
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => openPipelineReorchestration()}
                                            disabled={!canLaunchWorkingSet}
                                            className="flex items-center gap-1.5 rounded-lg border border-indigo-500/60 bg-indigo-500/20 px-4 py-2 text-xs font-semibold text-indigo-100 transition-colors hover:border-indigo-400 hover:bg-indigo-500/30 disabled:cursor-not-allowed disabled:opacity-50 shadow-sm shadow-indigo-900/20"
                                            title={selectedDesignIds.length > 0
                                                ? 'Re-orchestrate a new workflow run using the highlighted outputs as the input set.'
                                                : loadedSavedReviewFilterSet
                                                    ? `Re-orchestrate a new workflow run from the loaded saved dataset '${loadedSavedReviewFilterSet.name}'.`
                                                    : 'Load a saved dataset or select outputs before re-orchestrating.'}
                                        >
                                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                            </svg>
                                            Pipeline Re-orchestration
                                        </button>
                                        {!workflowOnlyRefinement && (
                                            <>
                                                <button
                                                    type="button"
                                                    onClick={() => setShowParamOverrides(!showParamOverrides)}
                                                    className={`rounded-lg border px-3 py-2 text-xs transition-colors ${showParamOverrides ? 'border-indigo-500/40 bg-indigo-500/10 text-indigo-200' : 'border-slate-700 bg-slate-800/80 text-slate-200 hover:border-slate-600'}`}
                                                    title="Override pipeline parameters for the next round (JSON format)"
                                                >
                                                    ⚙️ Overrides
                                                </button>
                                                {([
                                                    ['validate_boltz2', 'Boltz-2'],
                                                    ['validate_protenix', 'Protenix'],
                                                    ['ppiflow_maturation', 'PPIFlow'],
                                                    ['fampnn_redesign', 'FAMPNN'],
                                                    ['frustrampnn', 'FrustraMPNN'],
                                                ] as Array<[AntibodyIterationAction, string]>).map(([action, label]) => (
                                                    <button
                                                        key={action}
                                                        type="button"
                                                        onClick={() => {
                                                            setIterationMessage(null);
                                                            let paramOverrides = undefined;

                                                            if (showParamOverrides) {
                                                                paramOverrides = {
                                                                    ...(pipelineOverrides.run_structure_validation && {
                                                                        run_structure_validation: true,
                                                                        structure_validator: pipelineOverrides.structure_validator,
                                                                        interactive_gate_stage: 'post_structure_validation'
                                                                    }),
                                                                    ...(pipelineOverrides.run_ppiflow && {
                                                                        run_post_validation_maturation: true,
                                                                        run_post_boltz_maturation: true,
                                                                        run_maturation: true,
                                                                        maturation_redesign_temp: pipelineOverrides.maturation_redesign_temp,
                                                                    }),
                                                                    lock_target_chains: pipelineOverrides.lock_target_chains,
                                                                    lock_antibody_framework: pipelineOverrides.lock_antibody_framework,
                                                                    ...(pipelineOverrides.run_frustrampnn && {
                                                                        run_frustrampnn: true
                                                                    }),
                                                                    interactive_gating: pipelineOverrides.interactive_gating
                                                                };
                                                            }

                                                            launchIterationMutation.mutate({ action, paramOverrides });
                                                        }}
                                                        disabled={!canLaunchWorkingSet || launchBusy}
                                                        className="hidden"
                                                    >
                                                        {label}
                                                    </button>
                                                ))}
                                            </>
                                        )}
                                    </div>
                                </div>

                                {!workflowOnlyRefinement && showParamOverrides && (
                                    <div className="mt-4 border-t border-slate-700/50 pt-4">
                                        <div className="text-xs text-indigo-300 font-medium mb-3">Pipeline Add-ons & Overrides</div>
                                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-7 gap-4">
                                            <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-slate-600 cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={pipelineOverrides.run_structure_validation}
                                                    onChange={(e) => setPipelineOverrides(prev => ({ ...prev, run_structure_validation: e.target.checked }))}
                                                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-slate-900"
                                                />
                                                Structure Validation
                                            </label>

                                            <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-slate-600 cursor-pointer">
                                                <span className="text-slate-400">Validator:</span>
                                                <select
                                                    value={pipelineOverrides.structure_validator}
                                                    onChange={(e) => setPipelineOverrides(prev => ({ ...prev, structure_validator: e.target.value }))}
                                                    disabled={!pipelineOverrides.run_structure_validation}
                                                    className="bg-transparent border-none text-indigo-300 focus:ring-0 p-0 text-xs w-full disabled:opacity-50 outline-none"
                                                >
                                                    <option value="boltz2">Boltz-2</option>
                                                    <option value="protenix">Protenix</option>
                                                </select>
                                            </label>

                                            <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-slate-600 cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={pipelineOverrides.run_ppiflow}
                                                    onChange={(e) => setPipelineOverrides(prev => ({ ...prev, run_ppiflow: e.target.checked }))}
                                                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-slate-900"
                                                />
                                                PPIFlow Maturation
                                            </label>

                                            <label className="rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-slate-600">
                                                <div className="flex items-center justify-between gap-3">
                                                    <span className="text-slate-400">Redesign temp</span>
                                                    <input
                                                        type="number"
                                                        min={0.0001}
                                                        max={1}
                                                        step={0.0001}
                                                        value={pipelineOverrides.maturation_redesign_temp}
                                                        onChange={(e) => {
                                                            const next = Number(e.target.value);
                                                            setPipelineOverrides(prev => ({
                                                                ...prev,
                                                                maturation_redesign_temp: Number.isFinite(next)
                                                                    ? Math.min(1, Math.max(0.0001, next))
                                                                    : prev.maturation_redesign_temp,
                                                            }));
                                                        }}
                                                        disabled={!pipelineOverrides.run_ppiflow}
                                                        className="w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-right text-xs text-slate-200 outline-none focus:border-indigo-500 disabled:opacity-50"
                                                    />
                                                </div>
                                                <div className="mt-1 text-[10px] text-slate-500">
                                                    Sent as `maturation_redesign_temp` with quick PPIFlow launches.
                                                </div>
                                            </label>

                                            <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-slate-600 cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={pipelineOverrides.lock_target_chains}
                                                    onChange={(e) => setPipelineOverrides(prev => ({ ...prev, lock_target_chains: e.target.checked }))}
                                                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-slate-900"
                                                />
                                                Lock Target Chains
                                            </label>

                                            <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-slate-600 cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={pipelineOverrides.lock_antibody_framework}
                                                    onChange={(e) => setPipelineOverrides(prev => ({ ...prev, lock_antibody_framework: e.target.checked }))}
                                                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-slate-900"
                                                />
                                                Lock Framework
                                            </label>

                                            <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-slate-600 cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={pipelineOverrides.run_frustrampnn}
                                                    onChange={(e) => setPipelineOverrides(prev => ({ ...prev, run_frustrampnn: e.target.checked }))}
                                                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-slate-900"
                                                />
                                                FrustraMPNN
                                            </label>

                                            <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-slate-600 cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={pipelineOverrides.interactive_gating}
                                                    onChange={(e) => setPipelineOverrides(prev => ({ ...prev, interactive_gating: e.target.checked }))}
                                                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-slate-900"
                                                />
                                                Interactive Gating
                                            </label>
                                        </div>
                                        <p className="mt-3 text-[10px] text-slate-500">
                                            These settings will augment the base preset of the action button you click below.
                                            For example, checking "Structure Validation" and clicking "FAMPNN" will run FAMPNN followed immediately by your chosen validator.
                                        </p>
                                    </div>
                                )}

                                {iterationMessage && (
                                    <div
                                        className={`mt-3 rounded-lg border px-3 py-2 text-xs ${iterationMessage.kind === 'success'
                                            ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
                                            : 'border-red-500/30 bg-red-500/10 text-red-200'
                                            }`}
                                    >
                                        {iterationMessage.text}
                                    </div>
                                )}
                            </div>
                        )}

                        {!workflowOnlyRefinement && showCdrIndelModal && isAntibodyContext && (
                            <div className="mb-4 rounded-xl border border-fuchsia-500/30 bg-slate-950/95 p-4 shadow-2xl">
                                <div className="flex items-start justify-between gap-4">
                                    <div>
                                        <div className="text-sm font-medium text-fuchsia-200">CDR Indel Round</div>
                                        <p className="mt-1 text-xs text-slate-400">
                                            Generate explicit insertion/deletion variants on the selected CDR loops, preserve the full complex context, then launch a new validation round.
                                        </p>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => setShowCdrIndelModal(false)}
                                        className="rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-1.5 text-xs text-slate-300 transition-colors hover:border-slate-600"
                                    >
                                        Close
                                    </button>
                                </div>

                                <div className="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
                                    <div className="space-y-4">
                                        <div>
                                            <div className="text-xs text-slate-500 mb-2">Target loops</div>
                                            <div className="flex flex-wrap gap-2">
                                                {availableCdrLoopIds.map((loopId) => {
                                                    const selected = cdrIndelConfig.loop_ids.includes(loopId);
                                                    return (
                                                        <button
                                                            key={loopId}
                                                            type="button"
                                                            onClick={() => {
                                                                setCdrIndelConfig((current) => {
                                                                    const next = new Set(current.loop_ids);
                                                                    if (next.has(loopId)) next.delete(loopId);
                                                                    else next.add(loopId);
                                                                    return { ...current, loop_ids: Array.from(next).sort() };
                                                                });
                                                            }}
                                                            className={`rounded-lg border px-3 py-2 text-xs transition-colors ${selected
                                                                ? 'border-fuchsia-400 bg-fuchsia-400/10 text-fuchsia-200'
                                                                : 'border-slate-700 bg-slate-800/70 text-slate-300 hover:border-slate-600'
                                                                }`}
                                                        >
                                                            {loopId}
                                                        </button>
                                                    );
                                                })}
                                            </div>
                                            <p className="mt-2 text-[11px] text-slate-500">
                                                Keep all loops on one chain family per round. Mixed H/L indels are rejected because variant generation is chain-specific.
                                            </p>
                                        </div>

                                        <div className="grid grid-cols-2 gap-3">
                                            <label className="text-xs text-slate-500">
                                                Variants / design
                                                <input
                                                    type="number"
                                                    min={1}
                                                    max={200}
                                                    value={cdrIndelConfig.variants_per_design}
                                                    onChange={(e) => setCdrIndelConfig((current) => ({
                                                        ...current,
                                                        variants_per_design: Math.max(1, Math.min(200, Number(e.target.value) || 1)),
                                                    }))}
                                                    className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                                />
                                            </label>
                                            <label className="text-xs text-slate-500">
                                                Indel sizes
                                                <input
                                                    type="text"
                                                    value={cdrIndelConfig.indel_sizes.join(',')}
                                                    onChange={(e) => {
                                                        const sizes = e.target.value
                                                            .split(',')
                                                            .map((token) => Number(token.trim()))
                                                            .filter((value) => Number.isFinite(value) && value > 0)
                                                            .map((value) => Math.floor(value));
                                                        setCdrIndelConfig((current) => ({
                                                            ...current,
                                                            indel_sizes: sizes.length > 0 ? Array.from(new Set(sizes)).sort((a, b) => a - b) : [1],
                                                        }));
                                                    }}
                                                    className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                                    placeholder="1,2,3"
                                                />
                                            </label>
                                        </div>

                                        <div className="grid grid-cols-2 gap-3">
                                            <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300">
                                                <input
                                                    type="checkbox"
                                                    checked={cdrIndelConfig.allow_insertions}
                                                    onChange={(e) => setCdrIndelConfig((current) => ({ ...current, allow_insertions: e.target.checked }))}
                                                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-fuchsia-500"
                                                />
                                                Allow insertions
                                            </label>
                                            <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300">
                                                <input
                                                    type="checkbox"
                                                    checked={cdrIndelConfig.allow_deletions}
                                                    onChange={(e) => setCdrIndelConfig((current) => ({ ...current, allow_deletions: e.target.checked }))}
                                                    className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-fuchsia-500"
                                                />
                                                Allow deletions
                                            </label>
                                        </div>

                                        <label className="block text-xs text-slate-500">
                                            Allowed insertion amino acids
                                            <input
                                                type="text"
                                                value={(cdrIndelConfig.allowed_aas || []).join('')}
                                                onChange={(e) => {
                                                    const aas = Array.from(new Set(
                                                        e.target.value.toUpperCase().replace(/[^A-Z]/g, '').split('')
                                                    )).filter((aa) => 'ACDEFGHIKLMNPQRSTVWY'.includes(aa));
                                                    setCdrIndelConfig((current) => ({ ...current, allowed_aas: aas }));
                                                }}
                                                className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                                placeholder="Leave blank for full AA set"
                                            />
                                        </label>
                                    </div>

                                    <div className="space-y-4">
                                        <div className="grid grid-cols-2 gap-3">
                                            <label className="text-xs text-slate-500">
                                                Validator
                                                <select
                                                    value={cdrIndelConfig.predictor}
                                                    onChange={(e) => setCdrIndelConfig((current) => ({
                                                        ...current,
                                                        predictor: e.target.value === 'boltz2' ? 'boltz2' : 'protenix',
                                                    }))}
                                                    className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                                >
                                                    <option value="protenix">Protenix</option>
                                                    <option value="boltz2">Boltz-2</option>
                                                </select>
                                            </label>
                                            <label className="text-xs text-slate-500">
                                                MSA provider
                                                <select
                                                    value={cdrIndelConfig.msa_provider}
                                                    onChange={(e) => setCdrIndelConfig((current) => ({
                                                        ...current,
                                                        msa_provider: e.target.value === 'colabfold_api' ? 'colabfold_api' : 'local',
                                                    }))}
                                                    className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                                >
                                                    <option value="local">Local</option>
                                                    <option value="colabfold_api">ColabFold API</option>
                                                </select>
                                            </label>
                                        </div>

                                        <label className="block text-xs text-slate-500">
                                            Indel probability
                                            <input
                                                type="number"
                                                min={0}
                                                max={1}
                                                step={0.05}
                                                value={cdrIndelConfig.indel_probability}
                                                onChange={(e) => setCdrIndelConfig((current) => ({
                                                    ...current,
                                                    indel_probability: Math.max(0, Math.min(1, Number(e.target.value) || 0)),
                                                }))}
                                                className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-fuchsia-500 outline-none"
                                            />
                                        </label>

                                        <div className="rounded-lg border border-slate-700/60 bg-slate-900/60 p-3 text-xs text-slate-400">
                                            <div className="text-slate-200 font-medium mb-1">Launch summary</div>
                                            <div>{activeLaunchDesignCount} input design{activeLaunchDesignCount === 1 ? '' : 's'}</div>
                                            <div>{cdrIndelConfig.variants_per_design} variant{cdrIndelConfig.variants_per_design === 1 ? '' : 's'} per design</div>
                                            <div className="mt-1 text-fuchsia-200">
                                                {activeLaunchDesignCount * cdrIndelConfig.variants_per_design} total variant predictions
                                            </div>
                                            {cdrIndelConfig.msa_provider === 'colabfold_api' && activeLaunchDesignCount * cdrIndelConfig.variants_per_design > 1 && (
                                                <div className="mt-2 text-amber-300">
                                                    Multi-variant indel rounds are automatically downgraded to local MSA.
                                                </div>
                                            )}
                                        </div>

                                        <div className="flex justify-end gap-2">
                                            <button
                                                type="button"
                                                onClick={() => setShowCdrIndelModal(false)}
                                                className="rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-slate-600"
                                            >
                                                Cancel
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => {
                                                    setIterationMessage(null);
                                                    let paramOverrides = undefined;

                                                    if (showParamOverrides) {
                                                        paramOverrides = {
                                                            ...(pipelineOverrides.run_structure_validation && {
                                                                run_structure_validation: true,
                                                                structure_validator: pipelineOverrides.structure_validator,
                                                                interactive_gate_stage: 'post_structure_validation'
                                                            }),
                                                            ...(pipelineOverrides.run_ppiflow && {
                                                                run_post_validation_maturation: true,
                                                                run_post_boltz_maturation: true,
                                                                run_maturation: true,
                                                                maturation_redesign_temp: pipelineOverrides.maturation_redesign_temp,
                                                            }),
                                                            lock_target_chains: pipelineOverrides.lock_target_chains,
                                                            lock_antibody_framework: pipelineOverrides.lock_antibody_framework,
                                                            ...(pipelineOverrides.run_frustrampnn && {
                                                                run_frustrampnn: true
                                                            }),
                                                            interactive_gating: pipelineOverrides.interactive_gating
                                                        };
                                                    }

                                                    launchIterationMutation.mutate({
                                                        action: 'cdr_indel_round',
                                                        cdrIndelConfig,
                                                        paramOverrides,
                                                    });
                                                }}
                                                disabled={
                                                    launchBusy ||
                                                    !canLaunchWorkingSet ||
                                                    cdrIndelConfig.loop_ids.length === 0 ||
                                                    (!cdrIndelConfig.allow_insertions && !cdrIndelConfig.allow_deletions)
                                                }
                                                className="rounded-lg border border-fuchsia-500/40 bg-fuchsia-500/10 px-3 py-2 text-xs text-fuchsia-200 transition-colors hover:border-fuchsia-400 disabled:cursor-not-allowed disabled:opacity-50"
                                            >
                                                Launch CDR Indel Round
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        )}

                        {!workflowOnlyRefinement && showManualMutagenesisModal && (
                            <div className="mb-4 rounded-xl border border-emerald-500/30 bg-slate-950/95 p-4 shadow-2xl">
                                <div className="flex items-start justify-between gap-4">
                                    <div>
                                        <div className="text-sm font-medium text-emerald-200">Manual Mutagenesis Round</div>
                                        <p className="mt-1 text-xs text-slate-400">
                                            Apply explicit substitution sets to one protein chain while preserving every other protein chain exactly as-is.
                                        </p>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => setShowManualMutagenesisModal(false)}
                                        className="rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-1.5 text-xs text-slate-300 transition-colors hover:border-slate-600"
                                    >
                                        Close
                                    </button>
                                </div>

                                <div className="mt-4 grid gap-4 lg:grid-cols-[1.3fr_0.7fr]">
                                    <div className="space-y-4">
                                        <label className="block text-xs text-slate-500">
                                            Mutation sets
                                            <textarea
                                                value={manualMutagenesisConfig.mutation_sets_text}
                                                onChange={(e) => setManualMutagenesisConfig((current) => ({
                                                    ...current,
                                                    mutation_sets_text: e.target.value,
                                                }))}
                                                placeholder={'One variant set per line\nS31Y\nS31Y,K58R'}
                                                className="mt-1 h-40 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none"
                                            />
                                            <span className="mt-1 block text-[10px] text-slate-500">
                                                Use one substitution set per line. Format each mutation as `W52A` or comma-separate multiple substitutions on a line.
                                            </span>
                                        </label>
                                    </div>

                                    <div className="space-y-4">
                                        <label className="block text-xs text-slate-500">
                                            Chain to mutate
                                            <input
                                                type="text"
                                                value={manualMutagenesisConfig.chain_id || ''}
                                                onChange={(e) => setManualMutagenesisConfig((current) => ({
                                                    ...current,
                                                    chain_id: e.target.value.toUpperCase().slice(0, 2),
                                                }))}
                                                placeholder="H"
                                                className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none"
                                            />
                                            <span className="mt-1 block text-[10px] text-slate-500">
                                                Leave blank only for single-chain designs. Antibody jobs default to the binder chain when available.
                                            </span>
                                        </label>

                                        <div className="grid grid-cols-2 gap-3">
                                            <label className="text-xs text-slate-500">
                                                Validator
                                                <select
                                                    value={manualMutagenesisConfig.predictor}
                                                    onChange={(e) => setManualMutagenesisConfig((current) => ({
                                                        ...current,
                                                        predictor: e.target.value === 'boltz2' ? 'boltz2' : 'protenix',
                                                    }))}
                                                    className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none"
                                                >
                                                    <option value="protenix">Protenix</option>
                                                    <option value="boltz2">Boltz-2</option>
                                                </select>
                                            </label>

                                            <label className="text-xs text-slate-500">
                                                MSA provider
                                                <select
                                                    value={manualMutagenesisConfig.msa_provider}
                                                    onChange={(e) => setManualMutagenesisConfig((current) => ({
                                                        ...current,
                                                        msa_provider: e.target.value === 'colabfold_api' ? 'colabfold_api' : 'local',
                                                    }))}
                                                    className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white focus:ring-2 focus:ring-emerald-500 outline-none"
                                                >
                                                    <option value="local">Local</option>
                                                    <option value="colabfold_api">ColabFold API</option>
                                                </select>
                                            </label>
                                        </div>

                                        <div className="rounded-lg border border-slate-700/60 bg-slate-900/60 p-3 text-xs text-slate-400">
                                            <div className="text-slate-200 font-medium mb-1">Launch summary</div>
                                            <div>{activeLaunchDesignCount} input design{activeLaunchDesignCount === 1 ? '' : 's'}</div>
                                            <div>{manualMutationSetCount} manual variant set{manualMutationSetCount === 1 ? '' : 's'}</div>
                                            <div className="mt-1 text-emerald-200">
                                                {activeLaunchDesignCount * manualMutationSetCount} total variant predictions
                                            </div>
                                            {manualMutagenesisConfig.msa_provider === 'colabfold_api' && (
                                                <div className="mt-2 text-amber-300">
                                                    Batch mutagenesis currently downgrades ColabFold API requests to local MSA.
                                                </div>
                                            )}
                                        </div>

                                        <div className="flex justify-end gap-2">
                                            <button
                                                type="button"
                                                onClick={() => setShowManualMutagenesisModal(false)}
                                                className="rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-slate-600"
                                            >
                                                Cancel
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => {
                                                    setIterationMessage(null);
                                                    launchManualMutagenesisMutation.mutate();
                                                }}
                                                disabled={launchBusy || !canLaunchWorkingSet || manualMutationSetCount === 0}
                                                className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200 transition-colors hover:border-emerald-400 disabled:cursor-not-allowed disabled:opacity-50"
                                            >
                                                Launch Manual Mutation Round
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Content */}
                        <div className="bg-slate-900/50 rounded-xl border border-slate-800 min-h-[600px]">
                            {isLoading ? (
                                <div className="flex items-center justify-center h-96">
                                    <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500" />
                                </div>
                            ) : (
                                <>
                                    {isPostRFantibodyReview && (
                                        <div className="border-b border-slate-800/80 px-6 py-3">
                                            <div className="flex flex-wrap items-center gap-3 text-xs">
                                                <span className="text-slate-400">RF review set</span>
                                                <div className="inline-flex rounded-lg border border-slate-700/70 bg-slate-900/70 p-1">
                                                    <button
                                                        type="button"
                                                        onClick={() => setRfReviewSet('filtered')}
                                                        className={`rounded px-3 py-1 transition-colors ${rfReviewSet === 'filtered' ? 'bg-emerald-500/20 text-emerald-200' : 'text-slate-400 hover:text-slate-200'}`}
                                                    >
                                                        Screened {rfFilteredCount > 0 ? `(${rfFilteredCount})` : ''}
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={() => setRfReviewSet('raw')}
                                                        className={`rounded px-3 py-1 transition-colors ${rfReviewSet === 'raw' ? 'bg-blue-500/20 text-blue-200' : 'text-slate-400 hover:text-slate-200'}`}
                                                    >
                                                        Raw {rfRawCount > 0 ? `(${rfRawCount})` : ''}
                                                    </button>
                                                </div>
                                                <span className="text-slate-500">
                                                    Viewing {loadedSavedReviewFilterSet
                                                        ? `'${loadedSavedReviewFilterSet.name}' on ${rfReviewSet === 'filtered' ? 'screened' : 'raw'} RF backbones`
                                                        : rfReviewSet === 'filtered'
                                                            ? 'screened RF backbones'
                                                            : 'all raw RF backbones'}.
                                                </span>
                                            </div>
                                            {savedReviewFilterSets.length > 0 && (
                                                <div className="mt-3 flex flex-wrap gap-2">
                                                    {savedReviewFilterSets.map((filterSet) => (
                                                        <button
                                                            key={filterSet.id}
                                                            type="button"
                                                            onClick={() => applySavedReviewFilterSet(filterSet)}
                                                            className={`rounded-full border px-3 py-1 text-[11px] transition-colors ${loadedSavedReviewFilterSet?.id === filterSet.id
                                                                ? 'border-sky-500/40 bg-sky-500/15 text-sky-100'
                                                                : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:border-slate-600 hover:text-slate-100'
                                                                }`}
                                                        >
                                                            {filterSet.name}
                                                            {filterSet.visible_count != null ? ` (${filterSet.visible_count})` : ''}
                                                        </button>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    {/* OVERVIEW TAB */}
                                    {activeTab === 'overview' && overviewStats && (
                                        <div className="p-6 space-y-6">
                                            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-4">
                                                {isPostRFantibodyReview ? (
                                                    <>
                                                        <StatCard label="Outputs in Set" value={overviewStats.total.toLocaleString()} />
                                                        <StatCard
                                                            label="Current Set"
                                                            value={loadedSavedReviewFilterSet?.name || (rfReviewSet === 'filtered' ? 'Screened' : 'Raw')}
                                                            color={loadedSavedReviewFilterSet ? 'text-sky-300' : (rfReviewSet === 'filtered' ? 'text-emerald-300' : 'text-blue-300')}
                                                        />
                                                        <StatCard label="Avg RF pLDDT" value={formatMetric(overviewStats.avgPlddt, 1)} color="text-blue-400" />
                                                        <StatCard label="Avg Epi Cts" value={formatMetric(overviewStats.avgEpitopeContacts, 1)} color="text-lime-400" />
                                                        <StatCard label="Avg Tgt Cts" value={formatMetric(overviewStats.avgTargetContacts, 1)} color="text-emerald-400" />
                                                        <StatCard label="Avg Epi Dist" value={overviewStats.avgEpitopeDistance != null ? `${overviewStats.avgEpitopeDistance.toFixed(1)} Å` : '—'} color="text-amber-300" />
                                                        <StatCard label="Avg Tgt Dist" value={overviewStats.avgTargetDistance != null ? `${overviewStats.avgTargetDistance.toFixed(1)} Å` : '—'} color="text-cyan-300" />
                                                        <StatCard label="Avg Hotspot Cov" value={formatMetric(overviewStats.avgHotspotCoverage, 1)} color="text-violet-300" />
                                                    </>
                                                ) : (
                                                    <>
                                                        <StatCard label="Total Designs" value={overviewStats.total.toLocaleString()} />
                                                        <StatCard label="Favorites" value={overviewStats.favorites} color="text-yellow-400" />
                                                        <StatCard label="Avg pLDDT" value={formatMetric(overviewStats.avgPlddt, 1)} color="text-blue-400" />
                                                        <StatCard label="Avg pSCE" value={formatMetric(overviewStats.avgPsce, 2)} subtitle="FAMPNN" color="text-cyan-400" />
                                                        <StatCard label="Avg Affinity" value={formatMetric(overviewStats.avgAffinity, 2)} color="text-emerald-400" />
                                                        <StatCard label="Avg Binder %" value={overviewStats.avgBinderProb ? (overviewStats.avgBinderProb * 100).toFixed(0) + '%' : '—'} color="text-emerald-400" />
                                                        <StatCard label="Avg pTM" value={formatMetric(overviewStats.avgPtm, 2)} color="text-violet-400" />
                                                        <StatCard label="Avg Contacts" value={formatMetric(overviewStats.avgEpitopeContacts, 1)} color="text-lime-400" />
                                                        <StatCard label="High Contacts" value={overviewStats.highContacts} subtitle="≥5 epitope" color="text-lime-400" />
                                                        {overviewStats.annotatedWithFrustration > 0 && (
                                                            <>
                                                                <StatCard label="Avg High Frust" value={formatMetric(overviewStats.avgFrustrationHigh, 1)} color="text-red-400" />
                                                                <StatCard label="Avg % High Frust" value={overviewStats.avgFrustrationPctHigh != null ? `${overviewStats.avgFrustrationPctHigh.toFixed(1)}%` : '—'} color="text-orange-400" />
                                                            </>
                                                        )}
                                                    </>
                                                )}
                                            </div>
                                            {usingReviewRepresentativeFallback && (
                                                <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/5 px-3 py-2 text-xs text-cyan-100">
                                                    Detailed review rows have not been materialized yet for this paused RF stage, so the overview is currently derived from backbone-family representatives and gate metadata instead of persisted design rows.
                                                </div>
                                            )}

                                            <div className="bg-gradient-to-br from-slate-800/60 to-slate-900/60 rounded-xl p-5 border border-slate-700/50">
                                                {isPostRFantibodyReview ? (
                                                    <>
                                                        <h3 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                                                            <span>🧭</span>
                                                            Workflow Screening Summary
                                                            <span className="text-xs text-slate-500 font-normal">(persisted RFA screen outputs)</span>
                                                        </h3>
                                                        <div className="grid grid-cols-4 gap-3">
                                                            <div className="bg-emerald-500/20 rounded-lg p-3 text-center border border-emerald-500/30">
                                                                <div className="text-2xl font-bold text-emerald-300">{overviewStats.screenPassed}</div>
                                                                <div className="text-xs text-emerald-400 font-medium">Passed Screen</div>
                                                                <div className="text-[10px] text-slate-500">workflow-filtered keepers</div>
                                                            </div>
                                                            <div className="bg-amber-500/20 rounded-lg p-3 text-center border border-amber-500/30">
                                                                <div className="text-2xl font-bold text-amber-300">{overviewStats.screenFailed}</div>
                                                                <div className="text-xs text-amber-400 font-medium">Filtered Out</div>
                                                                <div className="text-[10px] text-slate-500">persisted workflow rejects</div>
                                                            </div>
                                                            <div className="bg-cyan-500/20 rounded-lg p-3 text-center border border-cyan-500/30">
                                                                <div className="text-2xl font-bold text-cyan-300">{overviewStats.highContacts}</div>
                                                                <div className="text-xs text-cyan-400 font-medium">High Epi Contact</div>
                                                                <div className="text-[10px] text-slate-500">≥5 epitope contacts</div>
                                                            </div>
                                                            <div className="bg-violet-500/20 rounded-lg p-3 text-center border border-violet-500/30">
                                                                <div className="text-2xl font-bold text-violet-300">{overviewStats.topScreeningReasons.length}</div>
                                                                <div className="text-xs text-violet-400 font-medium">Tracked Reasons</div>
                                                                <div className="text-[10px] text-slate-500">distinct screening labels</div>
                                                            </div>
                                                        </div>
                                                        {overviewStats.topScreeningReasons.length > 0 && (
                                                            <div className="mt-3 grid gap-2 md:grid-cols-2">
                                                                {overviewStats.topScreeningReasons.map(([reason, count]) => (
                                                                    <div key={reason} className="flex items-center justify-between rounded-lg border border-slate-700/50 bg-slate-900/50 px-3 py-2 text-xs">
                                                                        <span className="truncate text-slate-300">{reason}</span>
                                                                        <span className="font-mono text-slate-400">{count}</span>
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        )}
                                                    </>
                                                ) : (
                                                    <>
                                                        <h3 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                                                            <span>🔗</span>
                                                            Binding Quality Distribution
                                                            <span className="text-xs text-slate-500 font-normal">(based on iPTM)</span>
                                                        </h3>
                                                        <div className="grid grid-cols-4 gap-3">
                                                            <div className="bg-emerald-500/20 rounded-lg p-3 text-center border border-emerald-500/30">
                                                                <div className="text-2xl font-bold text-emerald-300">{overviewStats.tierA}</div>
                                                                <div className="text-xs text-emerald-400 font-medium">Tier A</div>
                                                                <div className="text-[10px] text-slate-500">Excellent</div>
                                                            </div>
                                                            <div className="bg-blue-500/20 rounded-lg p-3 text-center border border-blue-500/30">
                                                                <div className="text-2xl font-bold text-blue-300">{overviewStats.tierB}</div>
                                                                <div className="text-xs text-blue-400 font-medium">Tier B</div>
                                                                <div className="text-[10px] text-slate-500">Good</div>
                                                            </div>
                                                            <div className="bg-amber-500/20 rounded-lg p-3 text-center border border-amber-500/30">
                                                                <div className="text-2xl font-bold text-amber-300">{overviewStats.tierC}</div>
                                                                <div className="text-xs text-amber-400 font-medium">Tier C</div>
                                                                <div className="text-[10px] text-slate-500">Moderate</div>
                                                            </div>
                                                            <div className="bg-red-500/20 rounded-lg p-3 text-center border border-red-500/30">
                                                                <div className="text-2xl font-bold text-red-300">{overviewStats.tierD}</div>
                                                                <div className="text-xs text-red-400 font-medium">Tier D</div>
                                                                <div className="text-[10px] text-slate-500">Low</div>
                                                            </div>
                                                        </div>
                                                        <div className="mt-3 text-xs text-slate-500 flex items-center gap-3">
                                                            <span>Thresholds: A ≥ 0.75, B ≥ 0.55, C ≥ 0.40, D &lt; 0.40</span>
                                                            <span className="text-amber-500/80">+0.05 bonus for ≥5 epitope contacts</span>
                                                        </div>
                                                    </>
                                                )}
                                            </div>
                                            {pageSize !== 0 && totalDesigns > designs.length && (
                                                <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-100">
                                                    Overview cards and distributions are computed from the currently loaded page of {designs.length} designs, not the full {totalDesigns.toLocaleString()}-design set. Use `All` to score the whole set at once.
                                                </div>
                                            )}
                                            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                                                {/* Top Designs */}
                                                <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
                                                    <h3 className="text-sm font-semibold text-slate-300 mb-4">
                                                        {isPostRFantibodyReview ? 'Top Backbones by Target Engagement' : 'Top Designs by pLDDT'}
                                                    </h3>
                                                    <div className="space-y-2">
                                                        {(isPostRFantibodyReview
                                                            ? [...reviewBackboneRows]
                                                                .sort((a, b) => compareRfEngagement(
                                                                    a.representative || {},
                                                                    b.representative || {},
                                                                ))
                                                                .slice(0, 5)
                                                                .map((row) => (
                                                                    <div key={row.id} className="flex justify-between items-center py-2 px-3 bg-slate-900/50 rounded-lg">
                                                                        <span className="text-sm truncate flex-1">#{row.id}{row.representative?.name ? ` • ${row.representative.name}` : ''}</span>
                                                                        <span className="text-sm font-mono text-emerald-300">
                                                                            {row.representative?.target_contact_count ?? 0} cts • {row.representative?.target_min_distance != null ? row.representative.target_min_distance.toFixed(1) : '—'} Å
                                                                        </span>
                                                                    </div>
                                                                ))
                                                            : [...designs]
                                                                .sort((a, b) => (b.plddt_overall ?? 0) - (a.plddt_overall ?? 0))
                                                                .slice(0, 5)
                                                                .map((d) => (
                                                                    <div key={d.id} className="flex justify-between items-center py-2 px-3 bg-slate-900/50 rounded-lg">
                                                                        <span className="text-sm truncate flex-1">{d.name}</span>
                                                                        <span className={`text-sm font-mono ${getMetricColor('plddt_overall', d.plddt_overall)}`}>
                                                                            {formatMetric(d.plddt_overall, 1)}
                                                                        </span>
                                                                    </div>
                                                                )))}
                                                    </div>
                                                </div>

                                                {/* Model Info */}
                                                <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
                                                    <h3 className="text-sm font-semibold text-slate-300 mb-4">Job Information</h3>
                                                    <dl className="space-y-3 text-sm">
                                                        <div className="flex justify-between"><dt className="text-slate-500">Job Name</dt><dd>{activeJob?.name}</dd></div>
                                                        <div className="flex justify-between"><dt className="text-slate-500">Model</dt><dd>{activeJob?.model_id}</dd></div>
                                                        <div className="flex justify-between"><dt className="text-slate-500">Mode</dt><dd>{activeJob?.mode}</dd></div>
                                                        <div className="flex justify-between"><dt className="text-slate-500">Status</dt><dd className="text-emerald-400">{activeJob?.status}</dd></div>
                                                        <div className="flex justify-between"><dt className="text-slate-500">Output Dir</dt><dd className="truncate max-w-[200px]">{activeJob?.output_dir}</dd></div>
                                                    </dl>
                                                </div>
                                            </div>

                                            {/* CDR/Region Annotation Button - Works for antibodies, nanobodies, and TCRs */}
                                            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
                                                <h3 className="text-sm font-semibold text-slate-300 mb-3">ANARCII Sequence Annotation</h3>
                                                <p className="text-xs text-slate-400 mb-4">
                                                    Run ANARCII to extract CDR regions (antibodies/nanobodies) or variable regions (TCRs).
                                                    Uses IMGT numbering scheme.
                                                </p>
                                                <button
                                                    onClick={async () => {
                                                        const jobIdToUse = activeJob?.id || selectedJobId;
                                                        if (!jobIdToUse) return;
                                                        try {
                                                            const btn = document.getElementById('cdr-annotate-btn');
                                                            if (btn) {
                                                                btn.textContent = 'Annotating...';
                                                                btn.setAttribute('disabled', 'true');
                                                            }
                                                            const res = await fetch(`/api/jobs/${jobIdToUse}/annotate-cdrs?include_children=true`, { method: 'POST' });
                                                            const data = await res.json();
                                                            alert(data.message || 'ANARCII annotation started - refresh in 1-2 minutes');
                                                        } catch (err) {
                                                            alert('ANARCII annotation failed: ' + err);
                                                        }
                                                    }}
                                                    id="cdr-annotate-btn"
                                                    className="px-4 py-2 text-sm bg-violet-600 hover:bg-violet-500 text-white rounded-lg font-medium transition-colors flex items-center gap-2"
                                                >
                                                    Run ANARCII
                                                </button>
                                            </div>
                                        </div>
                                    )}
                                    {activeTab === 'overview' && !overviewStats && (
                                        <div className="flex min-h-[360px] items-center justify-center px-6 py-12">
                                            <div className="max-w-xl rounded-xl border border-slate-700/60 bg-slate-900/60 p-6 text-center">
                                                <div className="text-sm font-semibold text-slate-100">No overview data loaded</div>
                                                <p className="mt-2 text-sm leading-6 text-slate-400">
                                                    This job does not have persisted design rows or review-summary metadata yet. Once the RFA review rows are materialized, the overview and charts surfaces will populate automatically.
                                                </p>
                                            </div>
                                        </div>
                                    )}

                                    {/* STRUCTURE TAB - Fullscreen-Aware with Overlays */}
                                    {activeTab === 'structure' && (
                                        <div className="p-4 space-y-3">
                                            <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-3">
                                                <div className="flex flex-wrap items-center gap-3">
                                                    <label className="flex items-center gap-2 text-xs text-slate-400">
                                                        <span>Sort by</span>
                                                        <select
                                                            value={filterDraft.sortField}
                                                            onChange={(e) => {
                                                                const field = e.target.value;
                                                                setFilterDraft((current) => ({
                                                                    ...current,
                                                                    sortField: field,
                                                                    sortDir: getDefaultSortDirection(field),
                                                                }));
                                                            }}
                                                            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-white"
                                                        >
                                                            {availableSortOptions.map((option) => (
                                                                <option key={option.value} value={option.value}>{option.label}</option>
                                                            ))}
                                                        </select>
                                                    </label>
                                                    <button
                                                        type="button"
                                                        onClick={() => updateFilterDraft('sortDir', filterDraft.sortDir === 'asc' ? 'desc' : 'asc')}
                                                        className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200"
                                                    >
                                                        {filterDraft.sortDir === 'asc' ? 'Asc ↑' : 'Desc ↓'}
                                                    </button>
                                                    <label className="flex items-center gap-2 text-xs text-slate-400">
                                                        <span>Epi Cts ≥</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            value={filterDraft.contactsMin}
                                                            onChange={(e) => updateFilterDraft('contactsMin', Number(e.target.value || 0))}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            className="w-14 rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs text-white"
                                                        />
                                                    </label>
                                                    <label className="flex items-center gap-2 text-xs text-slate-400">
                                                        <span>Any Tgt Cts ≥</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            value={filterDraft.targetContactsMin}
                                                            onChange={(e) => updateFilterDraft('targetContactsMin', Number(e.target.value || 0))}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            className="w-14 rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs text-white"
                                                        />
                                                    </label>
                                                    <label className="flex items-center gap-2 text-xs text-slate-400">
                                                        <span>Epi Dist ≤</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="0.5"
                                                            value={filterDraft.epitopeMaxDist}
                                                            onChange={(e) => updateFilterDraft('epitopeMaxDist', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            className="w-16 rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs text-white"
                                                        />
                                                    </label>
                                                    <label className="flex items-center gap-2 text-xs text-slate-400">
                                                        <span>Any-Tgt Dist ≤</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="0.5"
                                                            value={filterDraft.targetMaxDist}
                                                            onChange={(e) => updateFilterDraft('targetMaxDist', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            className="w-16 rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs text-white"
                                                        />
                                                    </label>
                                                    <label className="flex items-center gap-2 text-xs text-slate-400">
                                                        <span>H3</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            value={filterDraft.cdrH3Min}
                                                            onChange={(e) => updateFilterDraft('cdrH3Min', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="min"
                                                            className="w-14 rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs text-white"
                                                        />
                                                        <span>–</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            value={filterDraft.cdrH3Max}
                                                            onChange={(e) => updateFilterDraft('cdrH3Max', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="max"
                                                            className="w-14 rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs text-white"
                                                        />
                                                    </label>
                                                    <button
                                                        type="button"
                                                        onClick={applyDraftFilters}
                                                        disabled={!filterDraftDirty}
                                                        className={`rounded border px-2 py-1 text-xs transition-colors ${filterDraftDirty ? 'border-blue-500/50 bg-blue-500/10 text-blue-200 hover:bg-blue-500/20' : 'border-slate-700 bg-slate-900 text-slate-500'}`}
                                                    >
                                                        Apply Filters
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={clearRfaFilters}
                                                        className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-300"
                                                    >
                                                        Clear
                                                    </button>
                                                    <span className="ml-auto text-[11px] text-slate-500">
                                                        {pageSize === 0
                                                            ? `${tableDesigns.length} structures in current structure set`
                                                            : `${tableDesigns.length} loaded on this page • ${totalDesigns.toLocaleString()} total`}
                                                    </span>
                                                </div>
                                                <div className="mt-2 text-[11px] text-slate-500">
                                                    `Any-Target Dist` measures nearest binder CA to any target residue, so it can be smaller than `Epitope Dist`, which only measures against the selected epitope residues.
                                                </div>
                                            </div>
                                            <StructureViewerPane
                                                selectedDesignId={selectedDesignId}
                                                setSelectedDesignId={setSelectedDesignId}
                                                designs={tableDesigns}
                                                selectedDesign={selectedDesign}
                                                colorMode={colorMode}
                                                setColorMode={setColorMode}
                                                structureFormat={structureFormat}
                                                antibodySelections={antibodySelections}
                                                antibodyStructureUrl={antibodyData?.imgt_pdb_url}
                                                structureAnalysis={structureAnalysis}
                                                activeJob={activeJob}
                                                getMetricColor={getMetricColor}
                                            />
                                        </div>
                                    )}

                                    {/* ANTIBODY TAB */}
                                    {activeTab === 'antibody' && (
                                        <div className="p-6 space-y-6">
                                            {!selectedDesign ? (
                                                <div className="flex flex-col items-center justify-center py-20 text-slate-500">
                                                    <div className="text-4xl mb-4">🧬</div>
                                                    <p>Select an antibody design to inspect.</p>
                                                </div>
                                            ) : (
                                                <>
                                                    <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
                                                        <div className="flex flex-col gap-4">
                                                            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                                                <div>
                                                                    <div className="text-sm font-semibold text-white">Binder Info Inspector</div>
                                                                    <div className="mt-1 text-xs text-slate-400">
                                                                        Inspect CDR annotation, validation metrics, frustration hotspots, and antibody-specific structure overlays for the selected design.
                                                                    </div>
                                                                </div>
                                                                <div className="relative">
                                                                    <select
                                                                        value={selectedDesignId ?? ''}
                                                                        onChange={(e) => setSelectedDesignId(e.target.value)}
                                                                        className="appearance-none rounded-lg border border-slate-600/50 bg-slate-700/60 px-3 py-2 pr-8 text-xs text-blue-300 transition-colors hover:bg-slate-600/60 min-w-[280px]"
                                                                    >
                                                                        {(['rfantibody', 'fampnn', 'validation'] as OutputSourceFilter[])
                                                                            .filter((source) => antibodyDesignGroups[source].length > 0 && (antibodySourceFilter === 'all' || antibodySourceFilter === source))
                                                                            .map((source) => (
                                                                                <optgroup key={source} label={`${getOutputSourceLabel(antibodyDesignGroups[source][0])} (${antibodyDesignGroups[source].length})`}>
                                                                                    {antibodyDesignGroups[source].map((d) => (
                                                                                        <option key={d.id} value={d.id}>
                                                                                            {getFriendlyDesignName(d)}{d.plddt_overall ? ` | pLDDT ${d.plddt_overall.toFixed(0)}` : ''}
                                                                                        </option>
                                                                                    ))}
                                                                                </optgroup>
                                                                            ))}
                                                                    </select>
                                                                    <div className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 text-xs">▾</div>
                                                                </div>
                                                            </div>
                                                            <div className="flex flex-wrap gap-2">
                                                                {(['all', 'rfantibody', 'fampnn', 'validation'] as OutputSourceFilter[]).map((source) => {
                                                                    const count = source === 'all' ? designs.length : antibodyDesignGroups[source].length;
                                                                    if (source !== 'all' && count === 0) return null;
                                                                    const active = antibodySourceFilter === source;
                                                                    return (
                                                                        <button
                                                                            key={source}
                                                                            type="button"
                                                                            onClick={() => setAntibodySourceFilter(source)}
                                                                            className={`rounded-lg border px-3 py-1.5 text-xs transition-colors ${active ? getOutputSourceBadgeClass(source) : 'border-slate-700 bg-slate-900/50 text-slate-400 hover:border-slate-600'}`}
                                                                        >
                                                                            {source === 'all' ? 'All Outputs' : getOutputSourceLabel(antibodyDesignGroups[source][0] || selectedDesign || {})} <span className="opacity-70">{count}</span>
                                                                        </button>
                                                                    );
                                                                })}
                                                            </div>
                                                            <div className="text-[11px] text-slate-500">
                                                                Current set: {antibodyTabDesigns.length} design{antibodyTabDesigns.length === 1 ? '' : 's'}{selectedDesign ? ` • inspecting ${getOutputSourceLabel(selectedDesign)}` : ''}.
                                                            </div>
                                                            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-700/50 bg-slate-900/50 p-3">
                                                                    <label className="flex items-center gap-2 text-xs text-slate-400">
                                                                        <span>Sort by</span>
                                                                        <select
                                                                        value={filterDraft.sortField}
                                                                        onChange={(e) => {
                                                                            const field = e.target.value;
                                                                            setFilterDraft((current) => ({
                                                                                ...current,
                                                                                sortField: field,
                                                                                sortDir: getDefaultSortDirection(field),
                                                                            }));
                                                                        }}
                                                                        className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-white"
                                                                    >
                                                                        {availableSortOptions.map((option) => (
                                                                            <option key={option.value} value={option.value}>{option.label}</option>
                                                                        ))}
                                                                    </select>
                                                                </label>
                                                                <button
                                                                    type="button"
                                                                    onClick={() => updateFilterDraft('sortDir', filterDraft.sortDir === 'asc' ? 'desc' : 'asc')}
                                                                    className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200"
                                                                >
                                                                    {filterDraft.sortDir === 'asc' ? 'Asc ↑' : 'Desc ↓'}
                                                                </button>
                                                                <label className="flex items-center gap-2 text-xs text-slate-400">
                                                                    <span>Epi Cts ≥</span>
                                                                    <input
                                                                        type="number"
                                                                        min="0"
                                                                        value={filterDraft.contactsMin}
                                                                        onChange={(e) => updateFilterDraft('contactsMin', Number(e.target.value || 0))}
                                                                        onKeyDown={handleDraftFilterKeyDown}
                                                                        className="w-14 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-white"
                                                                    />
                                                                </label>
                                                                <label className="flex items-center gap-2 text-xs text-slate-400">
                                                                    <span>Any Tgt Cts ≥</span>
                                                                    <input
                                                                        type="number"
                                                                        min="0"
                                                                        value={filterDraft.targetContactsMin}
                                                                        onChange={(e) => updateFilterDraft('targetContactsMin', Number(e.target.value || 0))}
                                                                        onKeyDown={handleDraftFilterKeyDown}
                                                                        className="w-14 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-white"
                                                                    />
                                                                </label>
                                                                <label className="flex items-center gap-2 text-xs text-slate-400">
                                                                    <span>Epi Dist ≤</span>
                                                                    <input
                                                                        type="number"
                                                                        min="0"
                                                                        step="0.5"
                                                                        value={filterDraft.epitopeMaxDist}
                                                                        onChange={(e) => updateFilterDraft('epitopeMaxDist', e.target.value)}
                                                                        onKeyDown={handleDraftFilterKeyDown}
                                                                        className="w-16 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-white"
                                                                    />
                                                                </label>
                                                                <label className="flex items-center gap-2 text-xs text-slate-400">
                                                                    <span>Any-Tgt Dist ≤</span>
                                                                    <input
                                                                        type="number"
                                                                        min="0"
                                                                        step="0.5"
                                                                        value={filterDraft.targetMaxDist}
                                                                        onChange={(e) => updateFilterDraft('targetMaxDist', e.target.value)}
                                                                        onKeyDown={handleDraftFilterKeyDown}
                                                                        className="w-16 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-white"
                                                                    />
                                                                </label>
                                                                <label className="flex items-center gap-2 text-xs text-slate-400">
                                                                    <span>H3</span>
                                                                    <input
                                                                        type="number"
                                                                        min="0"
                                                                        value={filterDraft.cdrH3Min}
                                                                        onChange={(e) => updateFilterDraft('cdrH3Min', e.target.value)}
                                                                        onKeyDown={handleDraftFilterKeyDown}
                                                                        placeholder="min"
                                                                        className="w-14 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-white"
                                                                    />
                                                                    <span>–</span>
                                                                    <input
                                                                        type="number"
                                                                        min="0"
                                                                        value={filterDraft.cdrH3Max}
                                                                        onChange={(e) => updateFilterDraft('cdrH3Max', e.target.value)}
                                                                        onKeyDown={handleDraftFilterKeyDown}
                                                                        placeholder="max"
                                                                        className="w-14 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-white"
                                                                    />
                                                                </label>
                                                                <button
                                                                    type="button"
                                                                    onClick={applyDraftFilters}
                                                                    disabled={!filterDraftDirty}
                                                                    className={`rounded border px-2 py-1 text-xs transition-colors ${filterDraftDirty ? 'border-blue-500/50 bg-blue-500/10 text-blue-200 hover:bg-blue-500/20' : 'border-slate-700 bg-slate-950 text-slate-500'}`}
                                                                >
                                                                    Apply Filters
                                                                </button>
                                                                <button
                                                                    type="button"
                                                                    onClick={clearRfaFilters}
                                                                    className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-300"
                                                                >
                                                                    Clear
                                                                </button>
                                                            </div>
                                                            <div className="text-[11px] text-slate-500">
                                                                `Any-Target Dist` is nearest binder CA to the full target surface. `Epitope Dist` is nearest binder CA only to the selected epitope residues, so the any-target distance can legitimately be smaller.
                                                            </div>
                                                        </div>
                                                    </div>

                                                    <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
                                                        {[
                                                            { label: 'Output', value: getOutputSourceLabel(selectedDesign), tone: selectedDesignSource === 'rfantibody' ? 'text-violet-300' : selectedDesignSource === 'fampnn' ? 'text-emerald-300' : 'text-cyan-300' },
                                                            { label: 'Binder Type', value: selectedDesign.antibody_type?.toUpperCase() || '—', tone: 'text-slate-200' },
                                                            { label: 'pLDDT', value: formatMetric(selectedDesign.plddt_overall, 1), tone: getMetricColor('plddt_overall', selectedDesign.plddt_overall) },
                                                            { label: 'iPTM', value: formatMetric(selectedDesign.iptm, 2), tone: getMetricColor('ptm', selectedDesign.iptm ?? null) },
                                                            { label: 'Epitope Contacts', value: selectedDesign.epitope_contact_count ?? '—', tone: 'text-slate-200' },
                                                            { label: 'Epitope Dist', value: selectedDesign.epitope_min_distance != null ? `${selectedDesign.epitope_min_distance.toFixed(2)} Å` : '—', tone: 'text-slate-200' },
                                                            { label: 'Any-Target Dist', value: selectedDesign.target_min_distance != null ? `${selectedDesign.target_min_distance.toFixed(2)} Å` : '—', tone: 'text-slate-200' },
                                                            { label: 'Epitope Atom Dist', value: selectedDesign.epitope_min_atom_distance != null ? `${selectedDesign.epitope_min_atom_distance.toFixed(2)} Å` : '—', tone: 'text-slate-200' },
                                                            { label: 'Target Atom Dist', value: selectedDesign.target_min_atom_distance != null ? `${selectedDesign.target_min_atom_distance.toFixed(2)} Å` : '—', tone: 'text-slate-200' },
                                                            { label: 'Val RMSD All', value: selectedDesign.rmsd_overall != null ? `${selectedDesign.rmsd_overall.toFixed(2)} Å` : '—', tone: 'text-slate-200' },
                                                            { label: 'Val RMSD Bd', value: selectedDesign.rmsd_binder != null ? `${selectedDesign.rmsd_binder.toFixed(2)} Å` : '—', tone: 'text-slate-200' },
                                                            { label: 'Humanness', value: antibodyData?.humanness_score != null ? `${(antibodyData.humanness_score * 100).toFixed(0)}%` : '—', tone: antibodyData?.humanness_score != null ? ((antibodyData.humanness_score > 0.8) ? 'text-emerald-300' : (antibodyData.humanness_score > 0.6 ? 'text-amber-300' : 'text-red-300')) : 'text-slate-500' },
                                                            { label: 'High Frust %', value: selectedDesign.frustration_pct_high != null ? `${selectedDesign.frustration_pct_high.toFixed(1)}%` : '—', tone: 'text-amber-300' },
                                                            { label: 'High Frust Count', value: selectedDesign.frustration_high_count ?? '—', tone: 'text-amber-300' },
                                                            { label: 'Maturation ΔIface', value: selectedDesign.maturation_delta_interface != null ? selectedDesign.maturation_delta_interface.toFixed(2) : '—', tone: 'text-fuchsia-300' },
                                                        ].map((card) => (
                                                            <div key={card.label} className="rounded-xl border border-slate-700/50 bg-slate-800/50 p-4">
                                                                <div className="text-[11px] uppercase tracking-wider text-slate-500">{card.label}</div>
                                                                <div className={`mt-2 text-lg font-semibold ${card.tone}`}>{card.value as any}</div>
                                                            </div>
                                                        ))}
                                                    </div>

                                                    <div className="mt-4 grid grid-cols-1 xl:grid-cols-2 gap-4">
                                                        <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
                                                            <div className="text-[11px] uppercase tracking-wider text-slate-500">Nearest Epitope Pair</div>
                                                            <div className="mt-2 text-sm text-slate-200">
                                                                {selectedDesign.epitope_nearest_antibody_residue && selectedDesign.epitope_nearest_target_residue
                                                                    ? `${selectedDesign.epitope_nearest_antibody_residue} ↔ ${selectedDesign.epitope_nearest_target_residue}`
                                                                    : '—'}
                                                            </div>
                                                            <div className="mt-2 text-xs text-slate-500">
                                                                Atom pair: {selectedDesign.epitope_nearest_antibody_atom && selectedDesign.epitope_nearest_target_atom
                                                                    ? `${selectedDesign.epitope_nearest_antibody_atom} ↔ ${selectedDesign.epitope_nearest_target_atom}`
                                                                    : '—'}
                                                            </div>
                                                        </div>
                                                        <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
                                                            <div className="text-[11px] uppercase tracking-wider text-slate-500">Nearest Target Pair</div>
                                                            <div className="mt-2 text-sm text-slate-200">
                                                                {selectedDesign.target_nearest_antibody_residue && selectedDesign.target_nearest_target_residue
                                                                    ? `${selectedDesign.target_nearest_antibody_residue} ↔ ${selectedDesign.target_nearest_target_residue}`
                                                                    : '—'}
                                                            </div>
                                                            <div className="mt-2 text-xs text-slate-500">
                                                                Atom pair: {selectedDesign.target_nearest_antibody_atom && selectedDesign.target_nearest_target_atom
                                                                    ? `${selectedDesign.target_nearest_antibody_atom} ↔ ${selectedDesign.target_nearest_target_atom}`
                                                                    : '—'}
                                                            </div>
                                                        </div>
                                                    </div>

                                                    {!antibodyHasAnnotation && (
                                                        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-200">
                                                            Binder CDR annotation is not populated for this design yet. Lengths and structure-level metrics are still shown below when available.
                                                        </div>
                                                    )}

                                                    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
                                                        <div className="xl:col-span-2 rounded-xl border border-slate-700/50 bg-slate-800/50 overflow-hidden">
                                                            <div className="flex items-center justify-between border-b border-slate-700/50 bg-slate-800/80 px-4 py-3">
                                                                <h3 className="text-sm font-semibold text-white">CDR Loops</h3>
                                                                <div className="text-[11px] text-slate-500">{antibodyData?.imgt_pdb_url ? 'IMGT renumbered view available' : 'Using original structure numbering'}</div>
                                                            </div>
                                                            <div className="overflow-x-auto p-4">
                                                                <table className="w-full text-sm">
                                                                    <thead>
                                                                        <tr className="border-b border-slate-700/50 text-left text-xs uppercase tracking-wider text-slate-400">
                                                                            <th className="pb-2">Chain</th>
                                                                            <th className="pb-2">Region</th>
                                                                            <th className="pb-2">Sequence</th>
                                                                            <th className="pb-2 text-right">Length</th>
                                                                        </tr>
                                                                    </thead>
                                                                    <tbody className="divide-y divide-slate-700/30 font-mono">
                                                                        {antibodyLoopRows.map((row) => (
                                                                            <tr key={row.region} className="hover:bg-slate-700/20">
                                                                                <td className="py-2 text-slate-500">{row.chain}</td>
                                                                                <td className="py-2 font-bold text-slate-300">{row.region}</td>
                                                                                <td className="py-2 text-white break-all">{row.sequence || '—'}</td>
                                                                                <td className="py-2 text-right text-slate-500">{row.length ?? '—'}</td>
                                                                            </tr>
                                                                        ))}
                                                                    </tbody>
                                                                </table>
                                                            </div>
                                                        </div>

                                                        <div className="space-y-6">
                                                            <div className="rounded-xl border border-slate-700/50 bg-slate-800/50 p-4">
                                                                <h3 className="text-sm font-semibold text-white">Framework Contact Hotspots</h3>
                                                                <div className="mt-3 space-y-2 text-xs">
                                                                    {[
                                                                        ['FR2', selectedDesign.fr2_contacts],
                                                                        ['DE', selectedDesign.de_loop],
                                                                        ['FR3', selectedDesign.fr3_contacts],
                                                                        ['FR4', selectedDesign.fr4_contacts],
                                                                    ].map(([label, value]) => (
                                                                        <div key={label} className="flex items-center justify-between rounded-lg bg-slate-900/50 px-3 py-2">
                                                                            <span className="text-slate-500">{label}</span>
                                                                            <span className="font-mono text-slate-200">{(value as string) || '—'}</span>
                                                                        </div>
                                                                    ))}
                                                                </div>
                                                            </div>

                                                            <div className="rounded-xl border border-slate-700/50 bg-slate-800/50 p-4">
                                                                <div className="flex items-center justify-between gap-3">
                                                                    <h3 className="text-sm font-semibold text-white">FrustraMPNN Hotspots</h3>
                                                                    {selectedDesign.frustration_csv_relpath && (
                                                                        <div className="flex items-center gap-2">
                                                                            <a
                                                                                href={buildFileStreamUrl(selectedDesign.frustration_csv_relpath)}
                                                                                target="_blank"
                                                                                rel="noreferrer"
                                                                                className="rounded-lg border border-slate-600 px-2.5 py-1 text-[11px] font-medium text-slate-300 transition-colors hover:border-slate-500 hover:text-white"
                                                                            >
                                                                                Open CSV
                                                                            </a>
                                                                            <a
                                                                                href={buildFileDownloadUrl(selectedDesign.frustration_csv_relpath)}
                                                                                className="rounded-lg border border-slate-600 px-2.5 py-1 text-[11px] font-medium text-slate-300 transition-colors hover:border-slate-500 hover:text-white"
                                                                            >
                                                                                Download CSV
                                                                            </a>
                                                                        </div>
                                                                    )}
                                                                </div>
                                                                <div className="mt-3 space-y-2 text-xs">
                                                                    {antibodyTopFrustrationResidues.length > 0 ? antibodyTopFrustrationResidues.map((row: any) => (
                                                                        <div key={`${row.chain}:${row.pos}`} className="flex items-center justify-between rounded-lg bg-slate-900/50 px-3 py-2">
                                                                            <span className="font-mono text-slate-300">{row.chain}{row.pos}</span>
                                                                            <span className={`${row.frust <= -1 ? 'text-amber-300' : row.frust >= 0.58 ? 'text-red-300' : 'text-slate-400'}`}>{row.frust.toFixed(2)} {row.frustClass}</span>
                                                                        </div>
                                                                    )) : (
                                                                        <div className="rounded-lg bg-slate-900/50 px-3 py-3 text-slate-500">No frustration annotation on this design.</div>
                                                                    )}
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </div>

                                                    <div className="h-[500px] overflow-hidden rounded-xl border border-slate-700/50 bg-slate-900 relative">
                                                        <div className="absolute top-3 left-3 z-10 rounded bg-slate-900/80 px-3 py-1 text-xs text-slate-300 pointer-events-none">
                                                            {getFriendlyDesignName(selectedDesign)} • {antibodyData?.imgt_pdb_url ? 'IMGT Renumbered Structure' : 'Original Structure'}
                                                        </div>
                                                        <MolstarViewer
                                                            key={selectedDesignId + '_ab'}
                                                            structureUrl={antibodyData?.imgt_pdb_url || (selectedDesignId ? `/api/designs/${selectedDesignId}/pdb` : undefined)}
                                                            format="pdb"
                                                            alphafoldView={false}
                                                            height="100%"
                                                            backgroundColor="#0f172a"
                                                            hideControls={true}
                                                            selections={antibodySelections}
                                                            label="CDR overlay: H1 red, H2 green, H3 blue, L1 yellow, L2 cyan, L3 magenta"
                                                        />
                                                    </div>

                                                    {antibodyData?.stability_data && (
                                                        <div className="rounded-xl border border-slate-700/50 bg-slate-800/50 p-6">
                                                            <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-white">
                                                                <div className="h-2 w-2 rounded-full bg-accent-secondary"></div>
                                                                ThermoMPNN Stability Scan (ddG)
                                                            </h3>
                                                            <div className="flex h-[400px] w-full items-center justify-center rounded-lg border border-slate-800 bg-slate-900/50">
                                                                <StabilityHeatmap data={antibodyData.stability_data} width={800} height={380} />
                                                            </div>
                                                        </div>
                                                    )}
                                                </>
                                            )}
                                        </div>
                                    )}

                                    {/* DATA TABLE TAB */}
                                    {activeTab === 'table' && (
                                        <div className="p-4">
                                            {/* Backbone / Family Toggle Bar */}
                                            {reviewBackboneRows.length > 0 && (
                                                <div className="mb-4 p-3 bg-slate-800/50 rounded-xl border border-slate-700/50">
                                                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                                                        <div>
                                                            <div className="text-xs font-medium text-slate-200">
                                                                {isPostRFantibodyReview ? 'Backbone families' : 'Backbone'}
                                                            </div>
                                                            <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
                                                                <span className={`rounded-full border px-2 py-1 ${selectedBackboneId == null ? 'border-blue-500/40 bg-blue-500/15 text-blue-100' : 'border-slate-700 bg-slate-900/70 text-slate-400'}`}>
                                                                    All {reviewBackboneTotal}
                                                                </span>
                                                            {selectedReviewBackbone && (
                                                                <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-emerald-100">
                                                                    ID {selectedReviewBackbone.id} | {selectedReviewBackbone.count} in current set
                                                                </span>
                                                            )}
                                                                {isPostRFantibodyReview && selectedReviewBackbone && (
                                                                    <span className="rounded-full border border-slate-700 bg-slate-900/70 px-2 py-1 text-slate-300">
                                                                        S {selectedReviewBackbone.filteredCount ?? selectedReviewBackbone.candidateCount ?? 0} | R {selectedReviewBackbone.rawCount ?? 0}
                                                                    </span>
                                                                )}
                                                            </div>
                                                            {selectedReviewBackbone && (
                                                                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-300">
                                                                    <span>
                                                                        <span className="text-slate-500">Avg Any</span>{' '}
                                                                        {formatMetricRange(
                                                                            selectedReviewBackbone.avgTargetContacts,
                                                                            selectedReviewBackbone.minTargetContacts,
                                                                            selectedReviewBackbone.maxTargetContacts,
                                                                            1,
                                                                        )}
                                                                    </span>
                                                                    <span>
                                                                        <span className="text-slate-500">Avg Epi</span>{' '}
                                                                        {formatMetricRange(
                                                                            selectedReviewBackbone.avgEpitopeContacts,
                                                                            selectedReviewBackbone.minEpitopeContacts,
                                                                            selectedReviewBackbone.maxEpitopeContacts,
                                                                            1,
                                                                        )}
                                                                    </span>
                                                                    <span>
                                                                        <span className="text-slate-500">Avg Epi Dist</span>{' '}
                                                                        {formatMetricRange(
                                                                            selectedReviewBackbone.avgEpitopeDistance,
                                                                            selectedReviewBackbone.minEpitopeDistance,
                                                                            selectedReviewBackbone.maxEpitopeDistance,
                                                                            1,
                                                                            ' Å',
                                                                        )}
                                                                    </span>
                                                                </div>
                                                            )}
                                                        </div>
                                                        {isPostRFantibodyReview && (
                                                            <div className="text-[11px] text-slate-400">
                                                                Screened / raw counts come from the paused stage gate payload.
                                                            </div>
                                                        )}
                                                    </div>
                                                    <div className="mt-3 grid max-h-64 grid-cols-1 gap-2 overflow-y-auto pr-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
                                                        <button
                                                            onClick={() => setSelectedBackboneId(null)}
                                                            className={`rounded-xl border px-3 py-2 text-left transition-colors ${selectedBackboneId === null
                                                                ? 'border-blue-500/40 bg-blue-500/15 text-blue-100'
                                                                : 'border-slate-700 bg-slate-900/60 text-slate-300 hover:border-slate-600 hover:bg-slate-800/70'
                                                                }`}
                                                        >
                                                            <div className="flex items-center justify-between gap-2">
                                                                <span className="text-xs font-semibold uppercase tracking-wider">All</span>
                                                                <span className="rounded-full bg-slate-950/80 px-2 py-0.5 text-[10px] font-medium text-slate-300">
                                                                    {reviewBackboneTotal}
                                                                </span>
                                                            </div>
                                                            <div className="mt-1 text-[11px] text-slate-400">
                                                                Entire current review set
                                                            </div>
                                                        </button>
                                                        {reviewBackboneRows.map((row) => (
                                                            <button
                                                                key={row.id}
                                                                onClick={() => setSelectedBackboneId(row.id)}
                                                                className={`rounded-xl border px-3 py-3 text-left transition-colors ${selectedBackboneId === row.id
                                                                    ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-100 shadow-sm shadow-emerald-950/30'
                                                                    : 'border-slate-700 bg-slate-900/60 text-slate-300 hover:border-slate-600 hover:bg-slate-800/70'
                                                                    }`}
                                                                title={
                                                                    row.representative?.name
                                                                        ? `Representative: ${row.representative.name} | Avg Any: ${formatMetricRange(row.avgTargetContacts, row.minTargetContacts, row.maxTargetContacts, 1)} | Avg Epi: ${formatMetricRange(row.avgEpitopeContacts, row.minEpitopeContacts, row.maxEpitopeContacts, 1)}`
                                                                        : `Avg Any: ${formatMetricRange(row.avgTargetContacts, row.minTargetContacts, row.maxTargetContacts, 1)} | Avg Epi: ${formatMetricRange(row.avgEpitopeContacts, row.minEpitopeContacts, row.maxEpitopeContacts, 1)}`
                                                                }
                                                            >
                                                                <div className="flex items-start justify-between gap-3">
                                                                    <div>
                                                                        <div className="text-sm font-semibold text-slate-100">
                                                                            ID {row.id}
                                                                        </div>
                                                                        <div className="mt-1 text-[11px] text-slate-400">
                                                                            {row.count} in current set
                                                                        </div>
                                                                    </div>
                                                                    <span className="rounded-full bg-slate-950/80 px-2 py-0.5 text-[10px] font-medium text-slate-300">
                                                                        {row.count}
                                                                    </span>
                                                                </div>
                                                                {isPostRFantibodyReview && (
                                                                    <div className="mt-3 space-y-2">
                                                                        <div className="text-[10px] text-slate-400">
                                                                            S {row.filteredCount ?? row.candidateCount ?? 0}
                                                                            {row.rawCount != null ? ` | R ${row.rawCount}` : ''}
                                                                        </div>
                                                                        <div className="grid gap-1.5 text-[11px]">
                                                                            <div className="flex items-center justify-between gap-3">
                                                                                <span className="text-slate-500">Avg Any</span>
                                                                                <span className="font-mono text-emerald-200">
                                                                                    {formatMetricRange(row.avgTargetContacts, row.minTargetContacts, row.maxTargetContacts, 1)}
                                                                                </span>
                                                                            </div>
                                                                            <div className="flex items-center justify-between gap-3">
                                                                                <span className="text-slate-500">Avg Epi</span>
                                                                                <span className="font-mono text-cyan-200">
                                                                                    {formatMetricRange(row.avgEpitopeContacts, row.minEpitopeContacts, row.maxEpitopeContacts, 1)}
                                                                                </span>
                                                                            </div>
                                                                            <div className="flex items-center justify-between gap-3">
                                                                                <span className="text-slate-500">Avg Epi Dist</span>
                                                                                <span className="font-mono text-amber-200">
                                                                                    {formatMetricRange(row.avgEpitopeDistance, row.minEpitopeDistance, row.maxEpitopeDistance, 1, ' Å')}
                                                                                </span>
                                                                            </div>
                                                                        </div>
                                                                    </div>
                                                                )}
                                                                {!isPostRFantibodyReview && (
                                                                    <div className="mt-2 text-[10px] text-slate-500">
                                                                        pLDDT {row.avgPlddt ?? '—'}
                                                                        {row.avgH3 != null ? ` | H3 ${row.avgH3}` : ''}
                                                                    </div>
                                                                )}
                                                            </button>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}

                                            {/* Quality Filters */}
                                            <div className="mb-4 p-3 bg-slate-800/50 rounded-xl border border-slate-700/50">
                                                <div className="flex items-center gap-4 flex-wrap">
                                                    <label className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500">Sort by</span>
                                                        <select
                                                            value={filterDraft.sortField}
                                                            onChange={(e) => {
                                                                const field = e.target.value;
                                                                setFilterDraft((current) => ({
                                                                    ...current,
                                                                    sortField: field,
                                                                    sortDir: getDefaultSortDirection(field),
                                                                }));
                                                            }}
                                                            className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200"
                                                        >
                                                            {availableSortOptions.map((option) => (
                                                                <option key={option.value} value={option.value}>{option.label}</option>
                                                            ))}
                                                        </select>
                                                    </label>
                                                    <button
                                                        type="button"
                                                        onClick={() => updateFilterDraft('sortDir', filterDraft.sortDir === 'asc' ? 'desc' : 'asc')}
                                                        className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300"
                                                    >
                                                        {filterDraft.sortDir === 'asc' ? 'Asc ↑' : 'Desc ↓'}
                                                    </button>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500">pLDDT ≥</span>
                                                        <input
                                                            type="range"
                                                            min="0"
                                                            max="100"
                                                            value={filterDraft.plddtMin}
                                                            onChange={(e) => updateFilterDraft('plddtMin', Number(e.target.value))}
                                                            className="w-24 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
                                                        />
                                                        <span className="text-xs text-blue-400 font-mono w-8">{filterDraft.plddtMin}</span>
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500">iPTM ≥</span>
                                                        <input
                                                            type="range"
                                                            min="0"
                                                            max="1"
                                                            step="0.05"
                                                            value={filterDraft.iptmMin}
                                                            onChange={(e) => updateFilterDraft('iptmMin', Number(e.target.value))}
                                                            className="w-24 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-emerald-500"
                                                        />
                                                        <span className="text-xs text-emerald-400 font-mono w-8">{filterDraft.iptmMin.toFixed(2)}</span>
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500">Contacts ≥</span>
                                                        <input
                                                            type="range"
                                                            min="0"
                                                            max="20"
                                                            value={filterDraft.contactsMin}
                                                            onChange={(e) => updateFilterDraft('contactsMin', Number(e.target.value))}
                                                            className="w-24 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-amber-500"
                                                        />
                                                        <span className="text-xs text-amber-400 font-mono w-8">{filterDraft.contactsMin}</span>
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500">Any Tgt Cts ≥</span>
                                                        <input
                                                            type="range"
                                                            min="0"
                                                            max="80"
                                                            value={filterDraft.targetContactsMin}
                                                            onChange={(e) => updateFilterDraft('targetContactsMin', Number(e.target.value))}
                                                            className="w-24 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-lime-500"
                                                        />
                                                        <span className="text-xs text-lime-400 font-mono w-8">{filterDraft.targetContactsMin}</span>
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500" title="Maximum nearest CA distance from the binder to the selected epitope residues">Epi Dist ≤</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="0.5"
                                                            value={filterDraft.epitopeMaxDist}
                                                            onChange={(e) => updateFilterDraft('epitopeMaxDist', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="max (Å)"
                                                            className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500" title="Maximum nearest CA distance from the binder to any target residue">Any-Tgt Dist ≤</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="0.5"
                                                            value={filterDraft.targetMaxDist}
                                                            onChange={(e) => updateFilterDraft('targetMaxDist', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="max (Å)"
                                                            className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500">Size</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="1"
                                                            value={filterDraft.binderSizeMin}
                                                            onChange={(e) => updateFilterDraft('binderSizeMin', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="min"
                                                            className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                        <span className="text-xs text-slate-500">–</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="1"
                                                            value={filterDraft.binderSizeMax}
                                                            onChange={(e) => updateFilterDraft('binderSizeMax', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="max"
                                                            className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500">H1</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="1"
                                                            value={filterDraft.cdrH1Min}
                                                            onChange={(e) => updateFilterDraft('cdrH1Min', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="min"
                                                            className="w-14 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                        <span className="text-xs text-slate-500">–</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="1"
                                                            value={filterDraft.cdrH1Max}
                                                            onChange={(e) => updateFilterDraft('cdrH1Max', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="max"
                                                            className="w-14 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500">H2</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="1"
                                                            value={filterDraft.cdrH2Min}
                                                            onChange={(e) => updateFilterDraft('cdrH2Min', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="min"
                                                            className="w-14 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                        <span className="text-xs text-slate-500">–</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="1"
                                                            value={filterDraft.cdrH2Max}
                                                            onChange={(e) => updateFilterDraft('cdrH2Max', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="max"
                                                            className="w-14 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500">H3</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="1"
                                                            value={filterDraft.cdrH3Min}
                                                            onChange={(e) => updateFilterDraft('cdrH3Min', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="min"
                                                            className="w-14 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                        <span className="text-xs text-slate-500">–</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="1"
                                                            value={filterDraft.cdrH3Max}
                                                            onChange={(e) => updateFilterDraft('cdrH3Max', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="max"
                                                            className="w-14 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500">RoG</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="0.1"
                                                            value={filterDraft.rogMin}
                                                            onChange={(e) => updateFilterDraft('rogMin', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="min"
                                                            className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                        <span className="text-xs text-slate-500">–</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="0.1"
                                                            value={filterDraft.rogMax}
                                                            onChange={(e) => updateFilterDraft('rogMax', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="max"
                                                            className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs text-slate-500">RFD RoG</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="0.1"
                                                            value={filterDraft.rfdRogMin}
                                                            onChange={(e) => updateFilterDraft('rfdRogMin', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="min"
                                                            className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                        <span className="text-xs text-slate-500">–</span>
                                                        <input
                                                            type="number"
                                                            min="0"
                                                            step="0.1"
                                                            value={filterDraft.rfdRogMax}
                                                            onChange={(e) => updateFilterDraft('rfdRogMax', e.target.value)}
                                                            onKeyDown={handleDraftFilterKeyDown}
                                                            placeholder="max"
                                                            className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                        />
                                                    </div>
                                                    <button
                                                        type="button"
                                                        onClick={applyDraftFilters}
                                                        disabled={!filterDraftDirty}
                                                        className={`rounded border px-2 py-1 text-xs transition-colors ${filterDraftDirty ? 'border-blue-500/50 bg-blue-500/10 text-blue-200 hover:bg-blue-500/20' : 'border-slate-700 bg-slate-800 text-slate-500'}`}
                                                    >
                                                        Apply Filters
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={clearRfaFilters}
                                                        className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300"
                                                    >
                                                        Clear Filters
                                                    </button>
                                                    <span className="text-xs text-slate-500 ml-auto">
                                                        Page {currentPage} • Showing {tableDesigns.length} of {totalDesigns.toLocaleString()} designs
                                                    </span>
                                                </div>
                                                <div className="mt-2 text-[11px] text-slate-500">
                                                    `Any-Target Dist` can be smaller than `Epitope Dist` because it measures against the whole target surface, not just the selected epitope residues.
                                                </div>
                                            </div>
                                            {/* Text Filter + Annotate CDRs */}
                                            <div className="mb-4 flex items-center gap-4">
                                                <input
                                                    type="text"
                                                    placeholder="Filter by name..."
                                                    value={filterText}
                                                    onChange={e => setFilterText(e.target.value)}
                                                    className="bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-sm w-64"
                                                />
                                                <span className="text-xs text-slate-500">
                                                    Check rows to build a launch set. The header checkbox now selects all visible rows, and row clicks still open the structure view.
                                                </span>
                                            </div>
                                            <div className="mb-4 flex flex-wrap items-center gap-2">
                                                {([
                                                    ['all', 'All'],
                                                    ['rfantibody', 'RFantibody'],
                                                    ['fampnn', 'FAMPNN'],
                                                    ['validation', 'Validation'],
                                                ] as Array<[OutputSourceFilter, string]>).map(([value, label]) => (
                                                    <button
                                                        key={value}
                                                        type="button"
                                                        onClick={() => setOutputSourceFilter(value)}
                                                        className={`rounded-lg border px-3 py-1.5 text-xs transition-colors ${outputSourceFilter === value
                                                            ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200'
                                                            : 'border-slate-700 bg-slate-800 text-slate-300 hover:border-slate-600'
                                                            }`}
                                                    >
                                                        {label}
                                                    </button>
                                                ))}
                                                <span className="text-xs text-slate-500">
                                                    {pageSize === 0
                                                        ? `${tableDesigns.length} rows in current output set`
                                                        : `${tableDesigns.length} rows loaded in current output set • ${totalDesigns.toLocaleString()} total after filters`}
                                                </span>
                                            </div>
                                            {/* Table */}
                                            <div className="w-full overflow-x-auto pb-2">
                                                <table className="w-full min-w-max text-sm">
                                                    <thead>
                                                        <tr className="border-b border-slate-700">
                                                            {[
                                                                {
                                                                    key: 'selected', label: (
                                                <input
                                                                            type="checkbox"
                                                                            ref={visibleSelectionRef}
                                                                            checked={allVisibleSelected}
                                                                            onChange={(e) => toggleVisibleSelection(e.target.checked)}
                                                                            className="h-4 w-4 rounded border-slate-600 bg-slate-900 text-cyan-500"
                                                                            aria-label={allVisibleSelected ? 'Clear all visible rows' : 'Select all visible rows'}
                                                                            title={allVisibleSelected ? 'Clear all visible rows' : someVisibleSelected ? `Add remaining visible rows (${visibleDesignIds.length.toLocaleString()} total)` : `Select all visible rows (${visibleDesignIds.length.toLocaleString()})`}
                                                                        />
                                                                    )
                                                                },
                                                                { key: 'name', label: 'Output' },
                                                                { key: 'binding_tier', label: isPostRFantibodyReview ? 'Screen' : 'Binding' },
                                                                { key: 'binder_length', label: 'Size' },
                                                                { key: 'cdr_h1_length', label: 'CDR-H1' },
                                                                { key: 'cdr_h2_length', label: 'CDR-H2' },
                                                                { key: 'cdr_h3_length', label: 'CDR-H3' },
                                                                { key: 'epitope_contact_count', label: 'Epi Cts' },
                                                                { key: 'target_contact_count', label: 'Any Tgt Cts' },
                                                                { key: 'epitope_min_distance', label: 'Epi Dist' },
                                                                { key: 'target_min_distance', label: 'Any-Tgt Dist' },
                                                                { key: 'affinity_score', label: 'Affinity' },
                                                                { key: 'binder_probability', label: 'Binder %' },
                                                                { key: 'fampnn_psce', label: 'pSCE' },
                                                                { key: 'plddt_overall', label: 'pLDDT' },
                                                                ...(showBinderTargetConfidence ? [
                                                                    { key: 'plddt_binder', label: 'pLDDT Binder' },
                                                                    { key: 'plddt_target', label: 'pLDDT Target' },
                                                                ] : []),
                                                                { key: 'pae_overall', label: 'PAE' },
                                                                { key: 'pae_interaction', label: 'iPAE' },
                                                                { key: 'ptm', label: 'pTM' },
                                                                { key: 'iptm', label: 'iPTM' },
                                                                { key: 'ligand_iptm', label: 'Lig iPTM' },
                                                                { key: 'conf_score', label: 'Conf' },
                                                                { key: 'rmsd_binder', label: 'Val RMSD Bd' },
                                                                { key: 'rmsd_overall', label: 'Val RMSD All' },
                                                                { key: 'rmsd_target', label: 'Val RMSD Tgt' },
                                                                { key: 'screening_reason', label: 'RFA Screen' },
                                                                { key: 'frustration_high_count', label: 'Frust High' },
                                                                { key: 'frustration_pct_high', label: '% High Frust' },
                                                                { key: 'has_clash', label: 'Clash' },
                                                                { key: 'maturation_delta_interface', label: 'ΔIface' },
                                                                { key: 'maturation_rmsd', label: 'Mat RMSD' },
                                                                { key: 'rog', label: 'RoG' },
                                                                { key: 'rfd_rog', label: 'RFD RoG' },
                                                                { key: 'fr2_contacts', label: 'FR2' },
                                                                { key: 'is_favorite', label: '★' },
                                                            ].map(col => (
                                                                <th
                                                                    key={col.key}
                                                                    onClick={col.key === 'selected' ? undefined : () => handleSort(col.key)}
                                                                    className={`px-3 py-2 text-left font-medium text-slate-400 ${col.key === 'selected' ? '' : 'cursor-pointer hover:text-white'}`}
                                                                >
                                                                    {col.label}
                                                                    {sortField === col.key && (
                                                                        <span className="ml-1">{sortDir === 'asc' ? '▲' : '▼'}</span>
                                                                    )}
                                                                </th>
                                                            ))}
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {tableDesigns.map(d => (
                                                            <tr
                                                                key={d.id}
                                                                className={`border-b border-slate-800 cursor-pointer hover:bg-slate-800/30 ${selectedDesignSet.has(d.id) ? 'bg-cyan-500/5' : ''
                                                                    }`}
                                                                onClick={() => {
                                                                    setSelectedDesignId(d.id);
                                                                    setActiveTab('structure');
                                                                }}
                                                            >
                                                                <td className="px-3 py-2">
                                                                    <input
                                                                        type="checkbox"
                                                                        checked={selectedDesignSet.has(d.id)}
                                                                        onChange={(e) => toggleDesignSelection(d.id, e.target.checked)}
                                                                        onClick={(e) => e.stopPropagation()}
                                                                        className="h-4 w-4 rounded border-slate-600 bg-slate-900 text-cyan-500"
                                                                    />
                                                                </td>
                                                                <td className="px-3 py-2 max-w-[260px]">
                                                                    <div className="flex items-center gap-2">
                                                                        <span className={`px-2 py-0.5 text-[10px] font-semibold rounded border ${inferDesignOutputSource(d as any) === 'validation'
                                                                            ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200'
                                                                            : inferDesignOutputSource(d as any) === 'fampnn'
                                                                                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                                                                                : inferDesignOutputSource(d as any) === 'rfantibody'
                                                                                    ? 'border-violet-500/40 bg-violet-500/10 text-violet-200'
                                                                                    : 'border-slate-600 bg-slate-800 text-slate-300'
                                                                            }`}>
                                                                            {getOutputSourceLabel(d as any)}
                                                                        </span>
                                                                        {d.frustration_high_count != null && (
                                                                            <span className="px-2 py-0.5 text-[10px] font-semibold rounded border border-amber-500/40 bg-amber-500/10 text-amber-200">
                                                                                Frustra
                                                                            </span>
                                                                        )}
                                                                        <span className="font-medium truncate">{getFriendlyDesignName(d as any)}</span>
                                                                    </div>
                                                                    <div className="mt-1 truncate text-[11px] text-slate-500" title={d.name}>
                                                                        {d.name}
                                                                    </div>
                                                                </td>

                                                                {/* Binding Quality Tier */}
                                                                <td className="px-3 py-2">
                                                                    {(() => {
                                                                        if (isPostRFantibodyReview) {
                                                                            const screenStatus = getRfScreenStatus(d);
                                                                            return (
                                                                                <span
                                                                                    className={`px-2 py-0.5 text-xs font-bold rounded border ${screenStatus.bgColor} ${screenStatus.color}`}
                                                                                    title={screenStatus.tooltip}
                                                                                >
                                                                                    {screenStatus.label}
                                                                                </span>
                                                                            );
                                                                        }
                                                                        const tier = getBindingTier(d.iptm, d.epitope_contact_count);
                                                                        return (
                                                                            <span
                                                                                className={`px-2 py-0.5 text-xs font-bold rounded border ${tier.bgColor} ${tier.color}`}
                                                                                title={`${tier.label} binding (iPTM: ${d.iptm?.toFixed(2) ?? '—'}, Contacts: ${d.epitope_contact_count ?? '—'})`}
                                                                            >
                                                                                {tier.tier}
                                                                            </span>
                                                                        );
                                                                    })()}
                                                                </td>

                                                                {/* Binder Size (AA count) */}
                                                                <td className="px-3 py-2 font-mono text-slate-400">
                                                                    {(d as any).binder_length ?? '—'}
                                                                </td>

                                                                {/* CDR-H1 Length */}
                                                                <td className="px-3 py-2 font-mono text-violet-400">
                                                                    {(d as any).cdr_h1_length ?? '—'}
                                                                </td>

                                                                {/* CDR-H2 Length */}
                                                                <td className="px-3 py-2 font-mono text-violet-400">
                                                                    {(d as any).cdr_h2_length ?? '—'}
                                                                </td>

                                                                {/* CDR-H3 Length */}
                                                                <td className="px-3 py-2 font-mono text-violet-400">
                                                                    {(d as any).cdr_h3_length ?? '—'}
                                                                </td>

                                                                {/* Epitope Contact Count */}
                                                                <td className={`px-3 py-2 font-mono ${(d.epitope_contact_count ?? 0) >= 5 ? 'text-emerald-400' :
                                                                    (d.epitope_contact_count ?? 0) > 0 ? 'text-amber-400' : 'text-slate-500'}`}>
                                                                    {d.epitope_contact_count ?? '—'}
                                                                </td>

                                                                {/* Target Contact Count */}
                                                                <td className={`px-3 py-2 font-mono ${((d as any).target_contact_count ?? 0) >= 5 ? 'text-emerald-400' :
                                                                    ((d as any).target_contact_count ?? 0) > 0 ? 'text-amber-400' : 'text-slate-500'}`}>
                                                                    {(d as any).target_contact_count ?? '—'}
                                                                </td>

                                                                {/* Epitope Min Distance */}
                                                                <td className={`px-3 py-2 font-mono ${d.epitope_min_distance != null && d.epitope_min_distance <= 4 ? 'text-emerald-400' :
                                                                    d.epitope_min_distance != null && d.epitope_min_distance <= 8 ? 'text-amber-400' : 'text-slate-500'}`}>
                                                                    {formatMetric(d.epitope_min_distance, 1)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${d.target_min_distance != null && d.target_min_distance <= 4 ? 'text-emerald-400' :
                                                                    d.target_min_distance != null && d.target_min_distance <= 8 ? 'text-amber-400' : 'text-slate-500'}`}>
                                                                    {formatMetric(d.target_min_distance, 1)}
                                                                </td>

                                                                {/* Affinity */}
                                                                <td className={`px-3 py-2 font-mono ${d.affinity_score != null && d.affinity_score > 6 ? 'text-emerald-400' :
                                                                    d.affinity_score != null && d.affinity_score > 4 ? 'text-white' : 'text-slate-500'}`}>
                                                                    {formatMetric(d.affinity_score, 2)}
                                                                </td>

                                                                {/* Binder Prob */}
                                                                <td className={`px-3 py-2 font-mono ${d.binder_probability != null && d.binder_probability > 0.8 ? 'text-emerald-400' :
                                                                    d.binder_probability != null && d.binder_probability > 0.5 ? 'text-amber-400' : 'text-slate-500'}`}>
                                                                    {d.binder_probability ? (d.binder_probability * 100).toFixed(0) + '%' : '—'}
                                                                </td>

                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('fampnn_psce', d.fampnn_psce)}`}>
                                                                    {formatMetric(d.fampnn_psce, 2)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('plddt_overall', d.plddt_overall)}`}>
                                                                    {formatMetric(d.plddt_overall, 1)}
                                                                </td>
                                                                {showBinderTargetConfidence && (
                                                                    <>
                                                                        <td className={`px-3 py-2 font-mono ${getMetricColor('plddt_binder', d.plddt_binder)}`}>
                                                                            {formatMetric(d.plddt_binder, 1)}
                                                                        </td>
                                                                        <td className={`px-3 py-2 font-mono ${getMetricColor('plddt_target', d.plddt_target)}`}>
                                                                            {formatMetric(d.plddt_target, 1)}
                                                                        </td>
                                                                    </>
                                                                )}
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('pae_overall', d.pae_overall)}`}>
                                                                    {formatMetric(d.pae_overall, 1)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('pae_interaction', d.pae_interaction)}`}>
                                                                    {formatMetric(d.pae_interaction, 1)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('ptm', d.ptm)}`}>
                                                                    {formatMetric(d.ptm, 2)}
                                                                </td>

                                                                {/* iPTM (basic interface pTM) */}
                                                                <td className={`px-3 py-2 font-mono ${d.iptm != null && d.iptm > 0.7 ? 'text-emerald-400' : d.iptm != null && d.iptm > 0.5 ? 'text-blue-400' : 'text-slate-500'}`}>
                                                                    {formatMetric(d.iptm, 2)}
                                                                </td>

                                                                {/* Ligand iPTM */}
                                                                <td className={`px-3 py-2 font-mono ${d.ligand_iptm != null && d.ligand_iptm > 0.8 ? 'text-emerald-400' : 'text-slate-500'}`}>
                                                                    {formatMetric(d.ligand_iptm, 2)}
                                                                </td>

                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('conf_score', d.conf_score)}`}>
                                                                    {formatMetric(d.conf_score, 2)}
                                                                </td>
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.rmsd_binder, 2)}</td>
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.rmsd_overall, 2)}</td>
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric((d as any).rmsd_target, 2)}</td>
                                                                <td className="px-3 py-2 max-w-[180px] truncate text-xs text-slate-400" title={(d as any).screening_reason ?? ''}>
                                                                    {(d as any).screening_reason ?? '—'}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${d.frustration_high_count != null
                                                                    ? d.frustration_high_count > 5
                                                                        ? 'text-red-400'
                                                                        : 'text-emerald-400'
                                                                    : 'text-slate-500'
                                                                    }`}>
                                                                    {d.frustration_high_count ?? '—'}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${d.frustration_pct_high != null
                                                                    ? d.frustration_pct_high > 10
                                                                        ? 'text-orange-400'
                                                                        : 'text-emerald-400'
                                                                    : 'text-slate-500'
                                                                    }`}>
                                                                    {d.frustration_pct_high != null ? `${d.frustration_pct_high.toFixed(1)}%` : '—'}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${d.has_clash ? 'text-red-400' : d.has_clash === false ? 'text-green-400' : 'text-slate-500'}`}>
                                                                    {d.has_clash == null ? '—' : d.has_clash ? '✗' : '✓'}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${d.maturation_delta_interface != null && d.maturation_delta_interface < 0 ? 'text-emerald-400' :
                                                                    d.maturation_delta_interface != null && d.maturation_delta_interface > 0 ? 'text-red-400' : 'text-slate-500'}`}
                                                                    title={d.maturation_delta_interface != null ? `ΔInterface: ${d.maturation_delta_interface.toFixed(1)} REU` : '—'}>
                                                                    {formatMetric(d.maturation_delta_interface, 1)}
                                                                </td>
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.maturation_rmsd, 2)}</td>
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.rog, 1)}</td>
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.rfd_rog, 1)}</td>
                                                                <td className="px-3 py-2 font-mono text-accent" title={`FR2: ${d.fr2_contacts || '—'}`}>
                                                                    {d.fr2_contacts || '—'}
                                                                </td>
                                                                <td className="px-3 py-2">{d.is_favorite ? '★' : ''}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>

                                            {/* Pagination Controls */}
                                            <div className="mt-4 flex items-center justify-between px-4 py-3 bg-slate-800/50 rounded-lg border border-slate-700/50">
                                                {/* Page Size Selector */}
                                                <div className="flex items-center gap-2">
                                                    <span className="text-xs text-slate-400">Per page:</span>
                                                    <select
                                                        value={pageSize}
                                                        onChange={(e) => {
                                                            setPageSize(Number(e.target.value));
                                                            setCurrentPage(1); // Reset to page 1 on size change
                                                        }}
                                                        className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-white cursor-pointer"
                                                    >
                                                        {PAGE_SIZE_OPTIONS.map(size => (
                                                            <option key={size} value={size}>{size}</option>
                                                        ))}
                                                    </select>
                                                </div>

                                                {/* Page Info */}
                                                <div className="text-xs text-slate-400">
                                                    Showing {((currentPage - 1) * pageSize) + 1} - {Math.min(currentPage * pageSize, totalDesigns)} of {totalDesigns.toLocaleString()}
                                                </div>

                                                {/* Navigation Buttons */}
                                                <div className="flex items-center gap-2">
                                                    <button
                                                        onClick={() => setCurrentPage(1)}
                                                        disabled={currentPage === 1}
                                                        className="px-2 py-1 text-xs bg-slate-700 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed rounded border border-slate-600"
                                                    >
                                                        ⏮
                                                    </button>
                                                    <button
                                                        onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                                                        disabled={currentPage === 1}
                                                        className="px-3 py-1 text-xs bg-slate-700 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed rounded border border-slate-600"
                                                    >
                                                        ← Prev
                                                    </button>
                                                    <span className="px-3 py-1 text-xs text-white bg-blue-600/30 rounded border border-blue-500/40">
                                                        Page {currentPage} / {totalPages}
                                                    </span>
                                                    <button
                                                        onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                                                        disabled={currentPage >= totalPages}
                                                        className="px-3 py-1 text-xs bg-slate-700 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed rounded border border-slate-600"
                                                    >
                                                        Next →
                                                    </button>
                                                    <button
                                                        onClick={() => setCurrentPage(totalPages)}
                                                        disabled={currentPage >= totalPages}
                                                        className="px-2 py-1 text-xs bg-slate-700 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed rounded border border-slate-600"
                                                    >
                                                        ⏭
                                                    </button>
                                                </div>
                                            </div>
                                        </div>
                                    )}

                                    {/* COMPARE TAB */}
                                    {activeTab === 'compare' && (
                                        <BatchComparePane initialJobId={selectedJobId} />
                                    )}

                                    {/* Compare Designs Pane */}
                                    {activeTab === 'compare_designs' && (
                                        <DesignComparePane
                                            designs={designs}
                                            preSelectedId={selectedDesignId}
                                        />
                                    )}

                                    {/* CHARTS TAB - Full Analytics Dashboard */}
                                    {activeTab === 'charts' && (
                                        <AnalyticsDashboard
                                            designs={analyticsChartDesigns}
                                            jobName={activeJob?.name}
                                            jobId={activeJob?.id}
                                            preferredAnalysisLens={preferredAnalysisLens}
                                            loadedDesignCount={designs.length}
                                        />
                                    )}
                                </>
                            )}
                        </div>
                    </>
                )
                }
            </div >
        </div >
    );
}

// Stat Card Component
function StatCard({ label, value, subtitle, color = 'text-white' }: { label: string; value: string | number; subtitle?: string; color?: string }) {
    return (
        <div className="bg-slate-800/50 rounded-lg p-4 border border-slate-700/50">
            <div className="text-xs text-slate-500 uppercase tracking-wider mb-1">{label}</div>
            <div className={`text-2xl font-bold ${color}`}>{value}</div>
            {subtitle && <div className="text-xs text-slate-500 mt-1">{subtitle}</div>}
        </div>
    );
}

export default ResultsViewer;
