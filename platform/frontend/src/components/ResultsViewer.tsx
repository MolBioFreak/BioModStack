import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';

import { buildFileDownloadUrl, buildFileStreamUrl, fetchJobs, fetchJobById, fetchDesignById, fetchDesigns, fetchDesignAnalysis, triggerDesignAnalysis, fetchBackboneSummary, launchAntibodyIteration, launchManualMutagenesis, saveReviewFilterSet, deleteReviewFilterSet, continueProteinLocalReview } from '../lib/api';
import type {
    AntibodyData,
    AntibodyCdrIndelConfig,
    AntibodyIterationAction,
    ChainMetric,
    ContactMapData,
    Design,
    IpsaeInterfaceAnalysis,
    DesignFilters,
    DesignSortField,
    Job,
    PAEData,
    PersistedAnalysisRun,
    ManualMutagenesisConfig,
    RfLoopMetric,
    RfLoopMetrics,
    RfScopeHeadlineMetrics,
    RfScreeningScope,
    SavedReviewFilterSet as ApiSavedReviewFilterSet,
    StructureAnalysis,
} from '../lib/api';
import {
    getOutputSourceBadgeClass,
    getOutputSourceLabel,
    inferDesignAnalysisLens,
    inferDesignOutputSource,
    inferPreferredAnalysisLens,
    type AnalysisLens,
    type OutputSourceFilter,
} from './designOutputSource';
import { isAntibodyPipelineMode } from '../lib/antibodyModes';
import MolstarViewer from './MolstarViewer';
// FloatingViewer - unused, kept for reference
import { StabilityHeatmap } from './MetricCharts';
import { BatchComparePane } from './BatchComparePane';
import { DesignComparePane } from './DesignComparePane';
// ReferenceSelector and MetricOverlay - unused, kept for reference
import { AnalyticsDashboard } from './AnalyticsDashboard';
import StructureViewerPane from './StructureViewerPane';
import {
    saveAntibodyRefinementLaunchState,
    type AntibodyRefinementLaunchState,
} from '../lib/refinementLaunchState';

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
    'ligand_iptm',
    'rmsd_binder',
    'rmsd_overall',
    'rmsd_target',
    'has_clash',
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
    'rfa_plddt_selected',
    'rfa_plddt_delta',
    'affinity_score',
    'binder_probability',
    'fampnn_psce',
    'ipsae',
    'frustration_high_count',
    'frustration_pct_high',
    'maturation_delta_interface',
    'maturation_interface_score',
    'maturation_rmsd',
    'maturation_selected_delta_interface',
    'maturation_selected_interface_score',
    'maturation_selected_rmsd',
    'maturation_nonselected_rmsd',
    'ppiflow_objective_score',
    'ppiflow_primary_loop_rmsd',
    'ppiflow_primary_loop_target_contact_delta',
    'ppiflow_primary_loop_target_distance_delta',
    'ppiflow_primary_loop_epitope_contact_delta',
    'ppiflow_primary_loop_epitope_distance_delta',
    'fr2_contacts',
    'binding_tier',
    'is_favorite',
]);
const CLIENT_RENDER_SORT_FIELDS = new Set<string>([
    'maturation_delta_interface',
    'maturation_interface_score',
    'maturation_rmsd',
    'maturation_selected_delta_interface',
    'maturation_selected_interface_score',
    'maturation_selected_rmsd',
    'maturation_nonselected_rmsd',
    'ppiflow_objective_score',
    'ppiflow_primary_loop',
    'ppiflow_primary_loop_rmsd',
    'ppiflow_primary_loop_target_contact_delta',
    'ppiflow_primary_loop_target_distance_delta',
    'ppiflow_primary_loop_epitope_contact_delta',
    'ppiflow_primary_loop_epitope_distance_delta',
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
    { value: 'rfa_plddt_final', label: 'RF pLDDT Global' },
    { value: 'rfa_plddt_selected', label: 'RF pLDDT Selected' },
    { value: 'rfa_plddt_delta', label: 'RF pLDDT Δ' },
    { value: 'affinity_score', label: 'Affinity' },
    { value: 'binder_probability', label: 'Binder %' },
    { value: 'fampnn_psce', label: 'pSCE' },
    { value: 'ipsae', label: 'ipSAE' },
    { value: 'iptm', label: 'iPTM' },
    { value: 'ptm', label: 'pTM' },
    { value: 'pae_overall', label: 'PAE' },
    { value: 'ligand_iptm', label: 'Ligand iPTM' },
    { value: 'rmsd_binder', label: 'Val RMSD Binder' },
    { value: 'rmsd_overall', label: 'Val RMSD Overall' },
    { value: 'rmsd_target', label: 'Val RMSD Target' },
    { value: 'has_clash', label: 'Clash' },
    { value: 'rog', label: 'RoG' },
    { value: 'rfd_rog', label: 'RFD RoG' },
    { value: 'frustration_high_count', label: 'Frust High' },
    { value: 'frustration_pct_high', label: 'High Frust %' },
    { value: 'maturation_delta_interface', label: 'ΔIface Global' },
    { value: 'maturation_selected_delta_interface', label: 'ΔIface Selected' },
    { value: 'maturation_interface_score', label: 'Iface Global' },
    { value: 'maturation_selected_interface_score', label: 'Iface Selected' },
    { value: 'maturation_rmsd', label: 'RMSD Global' },
    { value: 'maturation_selected_rmsd', label: 'RMSD Selected' },
    { value: 'maturation_nonselected_rmsd', label: 'RMSD Rest' },
    { value: 'ppiflow_objective_score', label: 'PPIFlow Objective' },
    { value: 'ppiflow_primary_loop', label: 'Primary Loop' },
    { value: 'ppiflow_primary_loop_rmsd', label: 'Primary Loop RMSD' },
    { value: 'ppiflow_primary_loop_target_contact_delta', label: 'Loop ΔTarget Cts' },
    { value: 'ppiflow_primary_loop_target_distance_delta', label: 'Loop ΔTarget Dist' },
    { value: 'ppiflow_primary_loop_epitope_contact_delta', label: 'Loop ΔEpitope Cts' },
    { value: 'ppiflow_primary_loop_epitope_distance_delta', label: 'Loop ΔEpitope Dist' },
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
    'fampnn_psce',
    'rmsd_binder',
    'rmsd_overall',
    'rmsd_target',
    'rfa_hotspot_min_distance',
    'rfa_hotspot_avg_min_distance',
    'rfa_runtime_seconds',
    'maturation_delta_interface',
    'pae',
    'pae_overall',
    'pae_interaction',
    'rog',
    'rfd_rog',
    'maturation_interface_score',
    'maturation_selected_interface_score',
    'maturation_rmsd',
    'maturation_selected_rmsd',
    'maturation_nonselected_rmsd',
    'ppiflow_objective_score',
    'ppiflow_primary_loop',
    'ppiflow_primary_loop_rmsd',
    'ppiflow_primary_loop_target_distance_delta',
    'ppiflow_primary_loop_epitope_distance_delta',
    'has_clash',
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

const formatPersistedAnalysisStatus = (status: PersistedAnalysisRun['status'] | 'missing'): string => {
    if (status === 'completed') return 'Cached';
    if (status === 'running') return 'Running';
    if (status === 'queued') return 'Queued';
    if (status === 'failed') return 'Failed';
    if (status === 'stale') return 'Stale';
    if (status === 'cancelled') return 'Cancelled';
    return 'Not computed';
};

const getPersistedAnalysisStatusClass = (status: PersistedAnalysisRun['status'] | 'missing'): string => {
    if (status === 'completed') return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200';
    if (status === 'running') return 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200';
    if (status === 'queued') return 'border-blue-500/30 bg-blue-500/10 text-blue-200';
    if (status === 'failed' || status === 'cancelled') return 'border-rose-500/30 bg-rose-500/10 text-rose-200';
    if (status === 'stale') return 'border-amber-500/30 bg-amber-500/10 text-amber-200';
    return 'border-slate-700 bg-slate-900/70 text-slate-400';
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
        return value <= 0.9 ? 'text-emerald-400' : value <= 1.2 ? 'text-cyan-400' : value <= 1.6 ? 'text-amber-400' : 'text-rose-400';
    }
    if (metric === 'fampnn_max_residue_psce') {
        return value <= 1.6 ? 'text-emerald-400' : value <= 2.4 ? 'text-cyan-400' : value <= 3.0 ? 'text-amber-400' : 'text-rose-400';
    }
    if (metric === 'ipsae') {
        return value >= 0.75 ? 'text-emerald-400' : value >= 0.55 ? 'text-amber-400' : 'text-red-400';
    }
    return 'text-slate-300';
};

type BindingMetricKey = 'ipsae' | 'iptm';

type BindingMetricSummary = {
    key: BindingMetricKey;
    label: 'ipSAE' | 'iPTM';
    rawValue: number | null;
    scoreValue: number | null;
    contactBonus: number;
};

type BindingTierInfo = {
    tier: string;
    color: string;
    bgColor: string;
    label: string;
    metric: BindingMetricSummary;
};

const getBindingMetricSummary = (
    design: Pick<Design, 'ipsae' | 'iptm' | 'epitope_contact_count'>,
): BindingMetricSummary => {
    if (typeof design.ipsae === 'number' && Number.isFinite(design.ipsae)) {
        return {
            key: 'ipsae',
            label: 'ipSAE',
            rawValue: design.ipsae,
            scoreValue: design.ipsae,
            contactBonus: 0,
        };
    }

    const iptm = typeof design.iptm === 'number' && Number.isFinite(design.iptm) ? design.iptm : null;
    const contactBonus = iptm != null && (design.epitope_contact_count ?? 0) >= 5 ? 0.05 : 0;
    return {
        key: 'iptm',
        label: 'iPTM',
        rawValue: iptm,
        scoreValue: iptm != null ? iptm + contactBonus : null,
        contactBonus,
    };
};

const summarizeBindingMetricUsage = (
    designs: Array<Pick<Design, 'ipsae' | 'iptm' | 'epitope_contact_count'>>,
): { label: string; detail: string; thresholds: string } => {
    const metrics = designs.map((design) => getBindingMetricSummary(design));
    const usableCount = metrics.filter((metric) => metric.scoreValue != null).length;
    const ipsaeCount = metrics.filter((metric) => metric.key === 'ipsae' && metric.scoreValue != null).length;
    const iptmCount = metrics.filter((metric) => metric.key === 'iptm' && metric.scoreValue != null).length;

    if (usableCount === 0) {
        return {
            label: 'interface metric',
            detail: 'No interface-confidence metric was available for the current page.',
            thresholds: 'Thresholds: A ≥ 0.75, B ≥ 0.55, C ≥ 0.40, D < 0.40',
        };
    }

    if (ipsaeCount === usableCount) {
        return {
            label: 'ipSAE',
            detail: 'Thresholds: A ≥ 0.75, B ≥ 0.55, C ≥ 0.40, D < 0.40',
            thresholds: 'ipSAE tiers use the raw interface score with no epitope-contact bonus.',
        };
    }

    if (iptmCount === usableCount) {
        return {
            label: 'iPTM',
            detail: 'Thresholds: A ≥ 0.75, B ≥ 0.55, C ≥ 0.40, D < 0.40',
            thresholds: 'iPTM receives a +0.05 bonus for designs with ≥5 epitope contacts.',
        };
    }

    return {
        label: 'interface metric',
        detail: 'Thresholds: A ≥ 0.75, B ≥ 0.55, C ≥ 0.40, D < 0.40',
        thresholds: 'Uses ipSAE when available, otherwise falls back to iPTM (+0.05 for ≥5 epitope contacts).',
    };
};

// Binding quality tier based on the best persisted interface-confidence metric.
const getBindingTier = (
    design: Pick<Design, 'ipsae' | 'iptm' | 'epitope_contact_count'>,
): BindingTierInfo => {
    const metric = getBindingMetricSummary(design);
    if (metric.scoreValue == null) {
        return {
            tier: '—',
            color: 'text-slate-500',
            bgColor: 'bg-slate-700/50',
            label: 'No data',
            metric,
        };
    }

    if (metric.scoreValue >= 0.75) {
        return { tier: 'A', color: 'text-emerald-300', bgColor: 'bg-emerald-500/30 border-emerald-500/50', label: 'Excellent', metric };
    }
    if (metric.scoreValue >= 0.55) {
        return { tier: 'B', color: 'text-blue-300', bgColor: 'bg-blue-500/30 border-blue-500/50', label: 'Good', metric };
    }
    if (metric.scoreValue >= 0.40) {
        return { tier: 'C', color: 'text-amber-300', bgColor: 'bg-amber-500/30 border-amber-500/50', label: 'Moderate', metric };
    }
    return { tier: 'D', color: 'text-red-300', bgColor: 'bg-red-500/30 border-red-500/50', label: 'Low', metric };
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
    const stageFamily = String(job?.stage_family || '').toLowerCase();
    const stageMode = String(job?.stage_mode || '').toLowerCase();
    const modelId = String(job?.model_id || '').toLowerCase();
    const mode = String(job?.mode || '').toLowerCase();
    const candidateDir = String(job?.awaiting_payload?.candidate_dir || '').toLowerCase();

    if (stage === 'post_structure_validation' || candidateDir.includes('structure_validation')) return 'validation';
    if (
        stage === 'post_boltzgen' ||
        stageFamily.includes('boltzgen') ||
        stageMode.includes('nanobody_binder') ||
        stageMode.includes('antibody_binder') ||
        (modelId === 'boltzgen' && (mode === 'nanobody_binder' || mode === 'antibody_binder'))
    ) {
        return 'boltzgen';
    }
    if (
        stage === 'post_ppiflow_generator' ||
        stageMode === 'generator_backbone_refine' ||
        (modelId === 'ppiflow' && mode === 'generator_backbone_refine')
    ) {
        return 'ppiflow';
    }
    if (
        stage.includes('ppiflow') ||
        stage.includes('maturation') ||
        candidateDir.includes('ppiflow') ||
        candidateDir.includes('backbone_refine') ||
        candidateDir.includes('maturation')
    ) {
        return 'ppiflow';
    }
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
        isAntibodyPipelineMode(rfdMode) ||
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

const titleCaseWords = (value: string): string => value.replace(/\b([a-z])/g, (match) => match.toUpperCase());

const getValidationSourceDisplayName = (
    design: { name: string; source_design_name?: string | null; source_pdb_path?: string | null }
): string | null => {
    const direct = typeof design.source_design_name === 'string' ? design.source_design_name.trim() : '';
    if (direct) return direct;

    const sourceStem = typeof design.source_pdb_path === 'string' && design.source_pdb_path.trim()
        ? design.source_pdb_path.split('/').pop()?.replace(/\.(pdb|cif|mmcif|json)$/i, '').trim()
        : '';
    if (sourceStem) return sourceStem;

    const duplicateVariantMatch = design.name.match(/^(variant_\d+)_\1_model_(\d+)$/i);
    if (duplicateVariantMatch) {
        return duplicateVariantMatch[1];
    }
    return null;
};

const formatValidationBaseLabel = (rawName: string): string => {
    const text = rawName.trim();
    if (!text) return 'Validation Output';

    const variantMatch = text.match(/^variant_(\d+)$/i);
    if (variantMatch) return `Variant ${variantMatch[1]}`;

    const snakeReadable = text
        .replace(/^rbx1[_-]/i, 'RBX1 ')
        .replace(/[_-]+/g, ' ')
        .replace(/\bseq\b/gi, 'Seq');

    return titleCaseWords(snakeReadable);
};

const formatValidationDesignLabel = (
    design: { name: string; source_design_name?: string | null; source_pdb_path?: string | null }
): string => {
    const sourceLabel = formatValidationBaseLabel(getValidationSourceDisplayName(design) || design.name);
    const modelMatch = design.name.match(/_model_(\d+)$/i);
    if (modelMatch) return `${sourceLabel} • Model ${modelMatch[1]}`;
    const sampleMatch = design.name.match(/(?:_sample_(\d+)|_sample(\d+))$/i);
    if (sampleMatch) {
        const sampleIndex = sampleMatch[1] ?? sampleMatch[2];
        return `${sourceLabel} • Sample ${sampleIndex}`;
    }
    return sourceLabel;
};

const getFriendlyDesignName = (design: { name: string; pdb_path?: string | null; confidence_metrics?: Record<string, any> | null }): string => {
    const source = inferDesignOutputSource(design);
    const sampleMatch = design.name.match(/(?:_sample_(\d+)|_ppiflow_sample(\d+))$/i);
    const sampleIndex = sampleMatch?.[1] ?? sampleMatch?.[2] ?? null;
    const seqMatch = design.name.match(/_seq_(\d+)/);
    if (source === 'validation' && sampleMatch) {
        return seqMatch
            ? `Seq ${seqMatch[1]} • ${getOutputSourceLabel(design)} Sample ${sampleIndex}`
            : `${getOutputSourceLabel(design)} Sample ${sampleIndex}`;
    }
    if (source === 'validation') return formatValidationDesignLabel(design as any);
    if (source === 'boltzgen') {
        const inputMatch = design.name.match(/boltzgen_input_(\d+)/i);
        const variantMatch = design.name.match(/variant_(\d+)/i);
        if (inputMatch) return `BoltzGen Candidate ${inputMatch[1]}`;
        if (variantMatch) return `BoltzGen Variant ${variantMatch[1]}`;
        return 'BoltzGen Candidate';
    }
    if (source === 'fampnn') return seqMatch ? `FAMPNN Seq ${seqMatch[1]}` : 'FAMPNN Candidate';
    if (source === 'ppiflow') {
        const ppiflowRecord = (
            design && typeof design === 'object' && 'provenance' in design && design.provenance && typeof design.provenance === 'object'
                ? ((design.provenance as Record<string, any>).ppiflow as Record<string, any> | undefined)
                : undefined
        );
        const sourceName = typeof ppiflowRecord?.source_design_name === 'string' ? ppiflowRecord.source_design_name.trim() : '';
        const backboneMatch = sourceName.match(/^(\d+)_/);
        const backboneLabel = backboneMatch ? `BB ${backboneMatch[1]}` : (sourceName || 'PPIFlow');
        if (sampleIndex !== null) return `${backboneLabel} • Sample ${sampleIndex}`;
        return backboneLabel;
    }
    if (source === 'rfantibody') {
        const jobMatch = design.name.match(/job[_-]?(\d+)/i);
        return jobMatch ? `RFantibody Backbone ${jobMatch[1]}` : 'RFantibody Backbone';
    }
    return design.name;
};

const getDesignSelectorMetricLabel = (design: Design): string | null => {
    const source = inferDesignOutputSource(design);
    if (source === 'rfantibody' && typeof design.plddt_overall === 'number') {
        return `RF pLDDT ${design.plddt_overall.toFixed(0)}`;
    }
    if (source === 'boltzgen') {
        if (typeof design.affinity_score === 'number') {
            return `Affinity ${design.affinity_score.toFixed(2)}`;
        }
        if (typeof design.conf_score === 'number') {
            return `Conf ${design.conf_score.toFixed(2)}`;
        }
        if (typeof design.plddt_overall === 'number') {
            return `pLDDT ${design.plddt_overall.toFixed(0)}`;
        }
    }
    if (source === 'validation' && typeof design.plddt_overall === 'number') {
        return `pLDDT ${design.plddt_overall.toFixed(0)}`;
    }
    if (source === 'fampnn' && typeof design.fampnn_psce === 'number') {
        return `pSCE ${design.fampnn_psce.toFixed(2)}`;
    }
    if (source === 'ppiflow') {
        const deltaIface = design.maturation_selected_delta_interface ?? design.maturation_delta_interface;
        if (typeof deltaIface === 'number') {
            return `ΔIface ${deltaIface.toFixed(2)}`;
        }
    }
    return null;
};

const asRecord = (value: unknown): Record<string, any> | null => (
    value && typeof value === 'object' && !Array.isArray(value)
        ? value as Record<string, any>
        : null
);

const getPpiflowRecord = (design: { provenance?: unknown } | null | undefined): Record<string, any> | null => (
    asRecord(asRecord(design?.provenance)?.ppiflow)
);

const getPpiflowScoreRecord = (design: { provenance?: unknown } | null | undefined): Record<string, any> | null => {
    const record = getPpiflowRecord(design);
    return (
        asRecord(record?.maturation_score)
        ?? asRecord(record?.partial_flow_score)
        ?? asRecord(asRecord(record?.maturation_filter)?.score_data)
    );
};

const getPpiflowAnchorRecord = (design: { provenance?: unknown } | null | undefined): Record<string, any> | null => (
    asRecord(getPpiflowRecord(design)?.anchors)
);

const getPpiflowLoopMetricsRecord = (design: { provenance?: unknown; ppiflow_loop_metrics?: unknown } | null | undefined): Record<string, Record<string, any>> | null => {
    const direct = asRecord(design && 'ppiflow_loop_metrics' in design ? design.ppiflow_loop_metrics : null);
    if (direct) {
        return Object.fromEntries(
            Object.entries(direct).filter(([, value]) => value && typeof value === 'object' && !Array.isArray(value))
        ) as Record<string, Record<string, any>>;
    }
    const fallback = asRecord(getPpiflowScoreRecord(design)?.loop_metrics);
    if (!fallback) return null;
    return Object.fromEntries(
        Object.entries(fallback).filter(([, value]) => value && typeof value === 'object' && !Array.isArray(value))
    ) as Record<string, Record<string, any>>;
};

const getPpiflowLoopEntries = (
    design: { provenance?: unknown; ppiflow_loop_metrics?: unknown } | null | undefined,
): Array<{ loopId: string; metrics: Record<string, any> }> => {
    const metrics = getPpiflowLoopMetricsRecord(design);
    if (!metrics) return [];
    return Object.entries(metrics)
        .filter(([loopId]) => loopId !== 'SELECTED')
        .map(([loopId, metric]) => ({ loopId, metrics: metric }))
        .sort((a, b) => a.loopId.localeCompare(b.loopId));
};

const getFampnnRecord = (design: { provenance?: unknown; confidence_metrics?: unknown } | null | undefined): Record<string, any> | null => (
    asRecord(asRecord(design?.provenance)?.fampnn)
    ?? asRecord(asRecord(getPpiflowRecord(design))?.fampnn)
    ?? asRecord(asRecord(design?.confidence_metrics)?.fampnn)
);

const getFampnnScalar = (
    design: { provenance?: unknown; confidence_metrics?: unknown } | null | undefined,
    ...keys: string[]
): number | null => {
    const directRecord = asRecord(design as unknown);
    const record = getFampnnRecord(design);
    for (const key of keys) {
        const directValue = directRecord?.[key];
        if (typeof directValue === 'number' && Number.isFinite(directValue)) return directValue;
        const value = record?.[key];
        if (typeof value === 'number' && Number.isFinite(value)) return value;
    }
    return null;
};

const getFampnnMaxResiduePsce = (design: { provenance?: unknown; confidence_metrics?: unknown } | null | undefined): number | null => (
    getFampnnScalar(design, 'fampnn_max_residue_psce', 'max_residue_psce')
);

const getPpiflowSourceName = (design: { name?: string; provenance?: unknown; source_design_name?: string | null } | null | undefined): string | null => {
    if (typeof design?.source_design_name === 'string' && design.source_design_name.trim()) {
        return design.source_design_name.trim();
    }
    const record = getPpiflowRecord(design);
    const sourceName = record?.source_design_name;
    if (typeof sourceName === 'string' && sourceName.trim()) {
        return sourceName.trim();
    }
    if (typeof design?.name === 'string') {
        return design.name.replace(/_ppiflow_sample\d+$/i, '');
    }
    return null;
};

const getPpiflowSampleIndex = (design: { name?: string; provenance?: unknown } | null | undefined): number | null => {
    const record = getPpiflowRecord(design);
    const direct = record?.sample_index;
    if (typeof direct === 'number' && Number.isFinite(direct)) {
        return direct;
    }
    const match = typeof design?.name === 'string' ? design.name.match(/_ppiflow_sample(\d+)$/i) : null;
    if (!match) return null;
    const parsed = Number(match[1]);
    return Number.isFinite(parsed) ? parsed : null;
};

const getPpiflowSourceKey = (design: { id?: string; name?: string; provenance?: unknown } | null | undefined): string => (
    getPpiflowSourceName(design) ?? design?.id ?? design?.name ?? ''
);

const getPpiflowSourceOrdinal = (design: { name?: string; provenance?: unknown } | null | undefined): number | null => {
    const sourceName = getPpiflowSourceName(design);
    const match = typeof sourceName === 'string' ? sourceName.match(/^(\d+)_/) : null;
    if (!match) return null;
    const parsed = Number(match[1]);
    return Number.isFinite(parsed) ? parsed : null;
};

const formatStageDescriptor = (family: unknown, mode: unknown): string | null => {
    const familyText = typeof family === 'string' ? family.trim() : '';
    const modeText = typeof mode === 'string' ? mode.trim() : '';
    if (!familyText && !modeText) return null;
    if (familyText && modeText) return `${familyText} • ${modeText}`;
    return familyText || modeText || null;
};

const formatSourceSummary = (job: Job | null | undefined): string | null => {
    if (!job) return null;
    const stageLabel = formatStageDescriptor(job.source_stage_family, job.source_stage_mode);
    const count = typeof job.source_selection_count === 'number' ? job.source_selection_count : null;
    const dataset = typeof job.selection_dataset_name === 'string' && job.selection_dataset_name.trim() ? job.selection_dataset_name.trim() : null;
    const parts = [stageLabel, count != null ? `${count} selected inputs` : null, dataset ? `dataset ${dataset}` : null].filter(Boolean);
    return parts.length > 0 ? parts.join(' • ') : null;
};

const formatLineagePathSummary = (
    producedFamily: unknown,
    producedMode: unknown,
    sourceFamily: unknown,
    sourceMode: unknown,
): string | null => {
    const produced = formatStageDescriptor(producedFamily, producedMode);
    const source = formatStageDescriptor(sourceFamily, sourceMode);
    if (source && produced) return `${source} → ${produced}`;
    return produced || source || null;
};

type StageSequenceEntry = {
    chain: string;
    sequence: string;
    length: number;
    psce: number | null;
};

const formatSequenceViewerText = (entries: StageSequenceEntry[]): string => {
    if (entries.length === 0) return '';
    const groupsPerLine = 5;
    const residuesPerGroup = 10;
    const residuesPerLine = groupsPerLine * residuesPerGroup;
    const totalLengthWidth = String(Math.max(...entries.map((entry) => entry.length))).length;

    return entries.map((entry) => {
        const lines: string[] = [`Chain ${entry.chain}`];
        for (let start = 0; start < entry.sequence.length; start += residuesPerLine) {
            const chunk = entry.sequence.slice(start, start + residuesPerLine);
            const grouped = chunk.match(new RegExp(`.{1,${residuesPerGroup}}`, 'g'))?.join(' ') ?? chunk;
            const lineStart = String(start + 1).padStart(totalLengthWidth, ' ');
            const lineEnd = String(Math.min(start + residuesPerLine, entry.sequence.length)).padStart(totalLengthWidth, ' ');
            lines.push(`${lineStart}-${lineEnd}  ${grouped}`);
        }
        return lines.join('\n');
    }).join('\n\n');
};

const parseStageSequenceEntries = (
    sequenceValue: unknown,
    chainAvgValue: unknown,
    chainLabelsValue?: unknown,
): StageSequenceEntry[] => {
    if (typeof sequenceValue !== 'string' || !sequenceValue.trim()) return [];
    const chainAvg = asRecord(chainAvgValue);
    const chainLabels = typeof chainLabelsValue === 'string'
        ? chainLabelsValue
            .split(/[|,]/)
            .map((token) => token.trim())
            .filter(Boolean)
        : [];
    return sequenceValue
        .split('|')
        .map((token) => token.trim())
        .filter(Boolean)
        .map((token, index) => {
            const [chainLabel, rawSequence] = token.includes(':')
                ? token.split(':', 2)
                : [chainLabels[index] || (chainLabels.length === 1 ? chainLabels[0] : `Chain ${index + 1}`), token];
            const chain = String(chainLabel || '?').trim() || '?';
            const sequence = String(rawSequence || '').trim();
            const psceRaw = chainAvg?.[chain];
            return {
                chain,
                sequence,
                length: sequence.length,
                psce: typeof psceRaw === 'number' ? psceRaw : (typeof psceRaw === 'string' ? Number(psceRaw) : null),
            };
        })
        .filter((entry) => entry.sequence.length > 0);
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
type ReviewSourceSelectorValue = '' | 'live:filtered' | 'live:raw' | `saved:${string}`;
type SavedReviewFilterState = {
    rf_review_set?: RfReviewSet | null;
    output_source_filter?: OutputSourceFilter;
    sort_field?: string;
    sort_dir?: 'asc' | 'desc';
    filter_text?: string;
    selected_backbone_id?: number | null;
    plddt_min?: number;
    iptm_min?: number;
    ipsae_min?: number;
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
    ipsaeMin: number;
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

type BoltzgenClusterMode = 'exact_sequence' | 'identity_95' | 'identity_90' | 'cdr_h3_exact';

type BoltzgenCluster = {
    key: string;
    representative: Design;
    members: Design[];
    sequence: string | null;
    cdrH3: string | null;
};

type BoltzgenClusterSummary = {
    clusters: BoltzgenCluster[];
    totalCount: number;
    uniqueCount: number;
    duplicateCount: number;
    largestClusterSize: number;
    uniqueSequenceCount: number;
    uniqueCdrH3Count: number;
    medianConfidence: number | null;
    medianAffinity: number | null;
    medianBinderProbability: number | null;
    medianBinderLength: number | null;
};

type JobSelectorOption = {
    key: string;
    value: string;
    group: 'jobs' | 'lineage';
    groupKey?: string;
    title: string;
    detail: string;
    searchText: string;
    createdAt: number;
};

const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const getRfReviewSetLabel = (value: RfReviewSet | null | undefined) => value === 'raw' ? 'Raw' : value === 'filtered' ? 'Screened' : 'No Set';
const isStageReviewJob = (job: Job | null | undefined): boolean =>
    ['post_rfantibody', 'post_boltzgen', 'post_ppiflow_generator', 'post_fampnn', 'post_structure_validation']
        .includes(String(job?.awaiting_stage || job?.current_stage || '').toLowerCase());
const isPostRfantibodyStage = (job: Job | null | undefined): boolean =>
    String(job?.awaiting_stage || job?.current_stage || '').toLowerCase() === 'post_rfantibody';
const ANALYSIS_LENS_LABELS: Record<AnalysisLens, string> = {
    validation: 'Validation',
    rfantibody: 'RFantibody',
    boltzgen: 'BoltzGen',
    fampnn: 'FA-MPNN',
    ppiflow: 'PPIFlow',
    frustrampnn: 'FrustraMPNN',
    protenix: 'Protenix',
};
const LINEAGE_GROUP_ORDER: Record<string, number> = {
    rfantibody: 0,
    boltzgen: 1,
    fampnn: 2,
    ppiflow: 3,
    validation: 4,
    frustrampnn: 5,
    child: 6,
};
const normalizeLoopScopeLabel = (value: unknown): string | null => {
    if (Array.isArray(value)) {
        const loops = value.map((item) => String(item).trim().toUpperCase()).filter(Boolean);
        return loops.length > 0 ? loops.join(', ') : null;
    }
    if (value && typeof value === 'object') {
        const scopeRecord = value as Record<string, unknown>;
        const regionMode = String(
            scopeRecord.region_mode
            ?? scopeRecord.ppiflow_region_mode
            ?? scopeRecord.ppiflow_backbone_region_mode
            ?? scopeRecord.ppiflow_maturation_region_mode
            ?? ''
        ).trim().toLowerCase();
        const selectedLoops = scopeRecord.selected_loops ?? scopeRecord.ppiflow_selected_loops;
        const selectedLoopLabel = normalizeLoopScopeLabel(selectedLoops);
        if (regionMode === 'all_cdrs') return 'All CDRs';
        if (regionMode === 'framework_only') return 'Framework Only';
        if (regionMode === 'all_antibody') return 'Whole Antibody';
        if (regionMode === 'selected_cdrs') return selectedLoopLabel ? `Selected CDRs: ${selectedLoopLabel}` : 'Selected CDRs';
        return selectedLoopLabel;
    }
    if (typeof value === 'string') {
        const loops = value
            .replace(/[\[\]]/g, '')
            .split(/[\s,;|]+/)
            .map((item) => item.trim().toUpperCase())
            .filter(Boolean);
        return loops.length > 0 ? loops.join(', ') : null;
    }
    return null;
};
const getLineageFamily = (job: Job): string => {
    const stageFamily = String(job.stage_family || '').trim().toLowerCase();
    if (stageFamily) {
        if (stageFamily.includes('boltzgen')) return 'boltzgen';
        if (stageFamily.includes('validation') || stageFamily.includes('protenix') || stageFamily.includes('boltz2')) return 'validation';
        if (stageFamily.includes('ppiflow') || stageFamily.includes('maturation')) return 'ppiflow';
        if (stageFamily.includes('fampnn')) return 'fampnn';
        if (stageFamily.includes('frustrampnn')) return 'frustrampnn';
        if (stageFamily.includes('rfantibody')) return 'rfantibody';
    }
    const lens = inferPreferredAnalysisLens(job, []);
    return lens ?? 'child';
};
const getLineageGroupLabel = (job: Job): string => {
    const family = getLineageFamily(job);
    if (family === 'validation') {
        const validator = String(job.params?.structure_validator || '').toLowerCase();
        if (validator === 'protenix') return 'Validation';
    }
    if (family === 'child') return 'Child Outputs';
    return ANALYSIS_LENS_LABELS[family as AnalysisLens] ?? 'Child Outputs';
};
const getLineageOutputLabel = (job: Job): string => {
    const family = getLineageFamily(job);
    const stageMode = String(job.stage_mode || '').trim().toLowerCase();
    if (family === 'boltzgen') {
        if (stageMode === 'nanobody_binder') return 'Nanobody Generation';
        if (stageMode === 'antibody_binder') return 'Antibody Generation';
        return 'BoltzGen';
    }
    if (family === 'ppiflow') {
        if (stageMode === 'generator_backbone_refine') return 'Seeded Generation';
        if (stageMode === 'backbone_refine' || stageMode === 'post_rfantibody') return 'Backbone Refinement';
        if (stageMode === 'post_ppiflow') return 'Backbone Reattempt';
        if (stageMode === 'maturation' || stageMode === 'post_fampnn') return 'Maturation';
        if (stageMode === 'post_validation') return 'Post-Validation Repair';
        return 'PPIFlow';
    }
    if (family === 'validation') {
        const validator = String(job.params?.structure_validator || '').toLowerCase();
        if (validator === 'protenix') return 'Protenix';
        if (validator === 'boltz2') return 'Boltz-2';
    }
    return getLineageGroupLabel(job);
};
const getLineageOutputScopeLabel = (job: Job): string | null => {
    const scopeRecord = (job.selected_loop_scope && typeof job.selected_loop_scope === 'object'
        ? job.selected_loop_scope
        : {}) as Record<string, unknown>;
    const paramsScopeRecord = {
        region_mode: job.params?.ppiflow_region_mode ?? job.params?.ppiflow_backbone_region_mode ?? job.params?.ppiflow_maturation_region_mode,
        selected_loops: job.params?.ppiflow_selected_loops,
    };
    const stageMode = String(job.stage_mode || '').trim().toLowerCase();
    const candidates: unknown[] = stageMode === 'generator_backbone_refine' || stageMode === 'backbone_refine' || stageMode === 'post_rfantibody' || stageMode === 'post_ppiflow'
        ? [
            scopeRecord,
            paramsScopeRecord,
            scopeRecord.ppiflow_backbone_loop_scope,
            job.params?.ppiflow_backbone_loop_scope,
            scopeRecord.ppiflow_selected_loops,
            job.params?.ppiflow_selected_loops,
        ]
        : stageMode === 'maturation' || stageMode === 'post_fampnn'
            ? [
                scopeRecord,
                paramsScopeRecord,
                scopeRecord.ppiflow_maturation_loop_scope,
                job.params?.ppiflow_maturation_loop_scope,
                scopeRecord.ppiflow_selected_loops,
                job.params?.ppiflow_selected_loops,
            ]
            : [
                scopeRecord,
                paramsScopeRecord,
                scopeRecord.ppiflow_selected_loops,
                job.params?.ppiflow_selected_loops,
            ];
    for (const candidate of candidates) {
        const label = normalizeLoopScopeLabel(candidate);
        if (label) return label;
    }
    return null;
};
const normalizeRfScreeningScope = (value: unknown): RfScreeningScope | null =>
    value === 'whole_antibody' ? 'whole_antibody' : (value === 'cdr_loops' ? 'cdr_loops' : null);
const RF_SCOPE_LABELS: Record<RfScreeningScope, { short: string; target: string; epitope: string; distance: string }> = {
    cdr_loops: {
        short: 'CDR Loops',
        target: 'CDR-Target Contacts',
        epitope: 'CDR-Epitope Contacts',
        distance: 'CDR-Target Dist',
    },
    whole_antibody: {
        short: 'Whole Antibody',
        target: 'Whole-Ab Target Contacts',
        epitope: 'Whole-Ab Epitope Contacts',
        distance: 'Whole-Ab Target Dist',
    },
};

const coerceRfLoopMetrics = (value: unknown): RfLoopMetrics | null => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    return value as RfLoopMetrics;
};

const getRfLoopSummary = (design: Design | ReviewRepresentative | null | undefined): Record<string, unknown> | null => {
    if (!design || !('rfa_loop_metrics' in design)) return null;
    const metrics = coerceRfLoopMetrics((design as Design).rfa_loop_metrics);
    const summary = metrics?._screening;
    return summary && typeof summary === 'object' && !Array.isArray(summary) ? summary as Record<string, unknown> : null;
};

const getRfHeadlineMetrics = (
    design: Design | ReviewRepresentative | null | undefined,
    scope: RfScreeningScope,
): RfScopeHeadlineMetrics | null => {
    const summary = getRfLoopSummary(design);
    const headlineMetrics = summary?.headline_metrics_by_scope;
    if (!headlineMetrics || typeof headlineMetrics !== 'object' || Array.isArray(headlineMetrics)) return null;
    const scopedMetrics = (headlineMetrics as Record<string, unknown>)[scope];
    if (!scopedMetrics || typeof scopedMetrics !== 'object' || Array.isArray(scopedMetrics)) return null;
    return scopedMetrics as RfScopeHeadlineMetrics;
};

const getRfHeadlineMetricValue = (
    design: Design | ReviewRepresentative | null | undefined,
    scope: RfScreeningScope,
    key: keyof RfScopeHeadlineMetrics,
): number | null => {
    const scopedMetrics = getRfHeadlineMetrics(design, scope);
    const scopedValue = scopedMetrics?.[key];
    if (isFiniteNumber(scopedValue)) return scopedValue;
    if (design && key in design) {
        const fallback = (design as Record<string, unknown>)[key];
        return isFiniteNumber(fallback) ? fallback : null;
    }
    return null;
};

const getRfLoopEntries = (design: Design | null | undefined): Array<{ loopId: string; metrics: RfLoopMetric }> => {
    const metrics = coerceRfLoopMetrics(design?.rfa_loop_metrics);
    if (!metrics) return [];
    return Object.entries(metrics)
        .filter(([loopId, value]) => /^[HL][123]$/.test(loopId) && value && typeof value === 'object' && !Array.isArray(value))
        .map(([loopId, value]) => ({ loopId, metrics: value as RfLoopMetric }))
        .sort((a, b) => a.loopId.localeCompare(b.loopId));
};

const getPreferredRfMetricScope = (
    job: Job | null | undefined,
    design: Design | null | undefined,
): RfScreeningScope => {
    const fromJob = normalizeRfScreeningScope(job?.params?.rfantibody_screen_reference_scope);
    if (fromJob) return fromJob;
    const summary = getRfLoopSummary(design);
    const fromSummary = normalizeRfScreeningScope(summary?.effective_scope ?? summary?.requested_scope);
    return fromSummary ?? 'cdr_loops';
};

const coerceGateBackboneSummary = (value: unknown): GateBackboneSummary | null => {
    if (!value || typeof value !== 'object') return null;
    const typed = value as Record<string, unknown>;
    if (!typed.backbones || typeof typed.backbones !== 'object') return null;
    return typed as GateBackboneSummary;
};

const getDefaultSortDirection = (field: string): 'asc' | 'desc' =>
    ASCENDING_DEFAULT_SORT_FIELDS.has(field) ? 'asc' : 'desc';

const isTableColumnSortable = (field: string, sortable?: boolean): boolean =>
    sortable !== false && field !== 'selected';

const getDirectNumberField = (design: Design, field: string): number | null => {
    const value = (design as unknown as Record<string, unknown>)[field];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
};

const getDirectStringField = (design: Design, field: string): string | null => {
    const value = (design as unknown as Record<string, unknown>)[field];
    return typeof value === 'string' && value.trim().length > 0 ? value : null;
};

const getDesignSortValue = (design: Design, field: string): string | number | boolean | null => {
    switch (field) {
        case 'plddt':
            return design.plddt_overall ?? null;
        case 'pae':
            return design.pae_overall ?? null;
        case 'confidence':
            return design.conf_score ?? null;
        case 'backbone':
            return design.backbone_id ?? null;
        case 'binding_tier':
            return getBindingMetricSummary(design).scoreValue ?? null;
        case 'maturation_delta_interface':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.delta_interface_score ?? null;
        case 'maturation_interface_score':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.interface_score_matured ?? null;
        case 'maturation_rmsd':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.rmsd_backbone ?? null;
        case 'maturation_selected_delta_interface':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.selected_delta_interface_score ?? null;
        case 'maturation_selected_interface_score':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.selected_interface_score_matured ?? null;
        case 'maturation_selected_rmsd':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.selected_rmsd_backbone ?? null;
        case 'maturation_nonselected_rmsd':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.nonselected_rmsd_backbone ?? null;
        case 'ppiflow_source_name':
            return getPpiflowSourceName(design) ?? null;
        case 'ppiflow_sample_index':
            return getPpiflowSampleIndex(design);
        case 'ppiflow_objective_score':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.objective_score ?? null;
        case 'ppiflow_primary_loop':
            return getDirectStringField(design, field) ?? getPpiflowScoreRecord(design)?.primary_loop ?? null;
        case 'ppiflow_primary_loop_rmsd':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.primary_loop_rmsd ?? null;
        case 'ppiflow_primary_loop_target_contact_delta':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.primary_loop_target_contact_delta ?? null;
        case 'ppiflow_primary_loop_target_distance_delta':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.primary_loop_target_distance_delta ?? null;
        case 'ppiflow_primary_loop_epitope_contact_delta':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.primary_loop_epitope_contact_delta ?? null;
        case 'ppiflow_primary_loop_epitope_distance_delta':
            return getDirectNumberField(design, field) ?? getPpiflowScoreRecord(design)?.primary_loop_epitope_distance_delta ?? null;
        case 'ppiflow_seq_identity': {
            const direct = (design as unknown as Record<string, unknown>).ppiflow_seq_identity;
            if (typeof direct === 'number' && Number.isFinite(direct)) return direct;
            const fallback = getPpiflowScoreRecord(design)?.sequence_identity;
            return typeof fallback === 'number' && Number.isFinite(fallback) ? fallback : null;
        }
        case 'ppiflow_anchor_count': {
            const direct = (design as unknown as Record<string, unknown>).ppiflow_anchor_count;
            if (typeof direct === 'number' && Number.isFinite(direct)) return direct;
            const fallback = getPpiflowAnchorRecord(design)?.anchor_count;
            return typeof fallback === 'number' && Number.isFinite(fallback) ? fallback : null;
        }
        case 'ppiflow_clash_count': {
            const direct = (design as unknown as Record<string, unknown>).ppiflow_clash_count;
            if (typeof direct === 'number' && Number.isFinite(direct)) return direct;
            const fallback = getPpiflowScoreRecord(design)?.clash_count_ca;
            return typeof fallback === 'number' && Number.isFinite(fallback) ? fallback : null;
        }
        default: {
            const value = (design as unknown as Record<string, unknown>)[field];
            if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
                return value;
            }
            return null;
        }
    }
};

const compareDesignSortValues = (
    left: string | number | boolean | null,
    right: string | number | boolean | null,
    direction: 'asc' | 'desc',
): number => {
    const normalize = (value: string | number | boolean | null): string | number | null => {
        if (value == null) return null;
        if (typeof value === 'boolean') return value ? 1 : 0;
        if (typeof value === 'string') {
            const trimmed = value.trim();
            return trimmed.length > 0 ? trimmed.toLocaleLowerCase() : null;
        }
        return Number.isFinite(value) ? value : null;
    };

    const leftValue = normalize(left);
    const rightValue = normalize(right);

    if (leftValue == null && rightValue == null) return 0;
    if (leftValue == null) return 1;
    if (rightValue == null) return -1;

    let result = 0;
    if (typeof leftValue === 'string' || typeof rightValue === 'string') {
        result = String(leftValue).localeCompare(String(rightValue), undefined, { numeric: true, sensitivity: 'base' });
    } else {
        result = leftValue - rightValue;
    }

    return direction === 'asc' ? result : -result;
};

const compareDesignsByField = (
    left: Design,
    right: Design,
    field: string,
    direction: 'asc' | 'desc',
): number => {
    const valueCompare = compareDesignSortValues(
        getDesignSortValue(left, field),
        getDesignSortValue(right, field),
        direction,
    );
    if (valueCompare !== 0) return valueCompare;

    const nameCompare = left.name.localeCompare(right.name, undefined, { numeric: true, sensitivity: 'base' });
    if (nameCompare !== 0) return nameCompare;
    return left.id.localeCompare(right.id);
};

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
    rf_review_set: state.rf_review_set === 'raw' ? 'raw' : state.rf_review_set === 'filtered' ? 'filtered' : undefined,
    output_source_filter: state.output_source_filter === 'rfantibody'
        || state.output_source_filter === 'boltzgen'
        || state.output_source_filter === 'fampnn'
        || state.output_source_filter === 'ppiflow'
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
    ipsae_min: typeof state.ipsae_min === 'number' ? state.ipsae_min : 0,
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

const normalizeBoltzgenSequence = (value: unknown): string | null => {
    if (typeof value !== 'string') return null;
    const normalized = value.trim().toUpperCase().replace(/[^A-Z|]/g, '');
    return normalized || null;
};

const collapseBoltzgenBinderSequence = (value: unknown): string | null => {
    const normalized = normalizeBoltzgenSequence(value);
    if (!normalized) return null;
    return normalized.replace(/\|/g, '');
};

const sameLengthSequenceIdentity = (left: string, right: string): number => {
    if (!left || !right || left.length !== right.length) return 0;
    let matches = 0;
    for (let index = 0; index < left.length; index += 1) {
        if (left[index] === right[index]) matches += 1;
    }
    return matches / left.length;
};

const medianMetric = (values: Array<number | null | undefined>): number | null => {
    const filtered = values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
    if (filtered.length === 0) return null;
    const sorted = [...filtered].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    if (sorted.length % 2 === 1) return sorted[middle];
    return (sorted[middle - 1] + sorted[middle]) / 2;
};

const buildBoltzgenClusters = (designs: Design[], mode: BoltzgenClusterMode): BoltzgenClusterSummary => {
    const clusters: BoltzgenCluster[] = [];
    const exactSequenceKeys = new Set<string>();
    const cdrH3Keys = new Set<string>();
    const exactClusters = new Map<string, BoltzgenCluster>();
    const cdrH3Clusters = new Map<string, BoltzgenCluster>();
    const lengthBuckets = new Map<number, BoltzgenCluster[]>();

    for (const design of designs) {
        const binderSequence = collapseBoltzgenBinderSequence(design.binder_sequence);
        const cdrH3 = normalizeBoltzgenSequence(design.cdr_h3);

        if (binderSequence) exactSequenceKeys.add(binderSequence);
        if (cdrH3) cdrH3Keys.add(cdrH3);

        if (mode === 'exact_sequence') {
            const key = binderSequence ? `seq:${binderSequence}` : `missing:${design.id}`;
            const existing = exactClusters.get(key);
            if (existing) {
                existing.members.push(design);
                continue;
            }
            const cluster = { key, representative: design, members: [design], sequence: binderSequence, cdrH3 };
            exactClusters.set(key, cluster);
            clusters.push(cluster);
            continue;
        }

        if (mode === 'cdr_h3_exact') {
            const key = cdrH3 ? `cdrh3:${cdrH3}` : `missing:${design.id}`;
            const existing = cdrH3Clusters.get(key);
            if (existing) {
                existing.members.push(design);
                continue;
            }
            const cluster = { key, representative: design, members: [design], sequence: binderSequence, cdrH3 };
            cdrH3Clusters.set(key, cluster);
            clusters.push(cluster);
            continue;
        }

        const threshold = mode === 'identity_95' ? 0.95 : 0.90;
        if (!binderSequence) {
            clusters.push({
                key: `missing:${design.id}`,
                representative: design,
                members: [design],
                sequence: null,
                cdrH3,
            });
            continue;
        }

        const candidates = lengthBuckets.get(binderSequence.length) || [];
        const matchingCluster = candidates.find((cluster) =>
            cluster.sequence
            && cluster.sequence.length === binderSequence.length
            && sameLengthSequenceIdentity(cluster.sequence, binderSequence) >= threshold
        );
        if (matchingCluster) {
            matchingCluster.members.push(design);
            continue;
        }
        clusters.push({
            key: `${mode}:${clusters.length}:${design.id}`,
            representative: design,
            members: [design],
            sequence: binderSequence,
            cdrH3,
        });
        const bucket = lengthBuckets.get(binderSequence.length) || [];
        bucket.push(clusters[clusters.length - 1]);
        lengthBuckets.set(binderSequence.length, bucket);
    }

    const largestClusterSize = clusters.reduce((maxSize, cluster) => Math.max(maxSize, cluster.members.length), 0);
    return {
        clusters,
        totalCount: designs.length,
        uniqueCount: clusters.length,
        duplicateCount: Math.max(0, designs.length - clusters.length),
        largestClusterSize,
        uniqueSequenceCount: exactSequenceKeys.size,
        uniqueCdrH3Count: cdrH3Keys.size,
        medianConfidence: medianMetric(designs.map((design) => design.conf_score ?? design.plddt_overall)),
        medianAffinity: medianMetric(designs.map((design) => design.affinity_score)),
        medianBinderProbability: medianMetric(designs.map((design) => design.binder_probability)),
        medianBinderLength: medianMetric(designs.map((design) => design.binder_length)),
    };
};

export function ResultsViewer() {
    const { jobId } = useParams();
    const navigate = useNavigate();
    const queryClient = useQueryClient();

    // State
    const [selectedJobId, setSelectedJobId] = useState<string>(jobId || '');
    const [showJobSelectorMenu, setShowJobSelectorMenu] = useState(false);
    const [jobSelectorSearch, setJobSelectorSearch] = useState('');
    const [showOverviewAnalysisMenu, setShowOverviewAnalysisMenu] = useState(false);
    const [expandedLineageGroups, setExpandedLineageGroups] = useState<Set<string>>(new Set());
    const [activeTab, setActiveTab] = useState<TabId>('overview');
    const [selectedDesignId, setSelectedDesignId] = useState<string>('');
    const [selectedDesignIds, setSelectedDesignIds] = useState<string[]>([]);
    const [iterationMessage, setIterationMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
    const [outputSourceFilter, setOutputSourceFilter] = useState<OutputSourceFilter>('all');
    const [antibodySourceFilter, setAntibodySourceFilter] = useState<OutputSourceFilter>('all');
    const manualOutputSourceSelectionRef = useRef(false);
    const outputSourceSelectionJobRef = useRef<string | null>(jobId || null);
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
    const [colorMode, setColorMode] = useState<'default' | 'plddt' | 'cdr' | 'frustration' | 'fampnn_psce'>('plddt');  // Structure coloring mode
    // Compare feature disabled for now:
    // const [showReferencePanel, setShowReferencePanel] = useState(false);
    // const [referenceStructures, setReferenceStructures] = useState<Array<{ url: string; format: 'pdb' | 'cif'; name: string }>>([]);
    const [selectedBackboneId, setSelectedBackboneId] = useState<number | null>(null);
    const [rfReviewSet, setRfReviewSet] = useState<RfReviewSet | null>(null);
    const [rfMetricScope, setRfMetricScope] = useState<RfScreeningScope>('cdr_loops');
    const [plddtMin, setPlddtMin] = useState<number>(0);
    const [iptmMin, setIptmMin] = useState<number>(0);
    const [ipsaeMin, setIpsaeMin] = useState<number>(0);
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
    const [pageSize, setPageSize] = useState<number>(100);
    const [currentPage, setCurrentPage] = useState<number>(1);
    const [topSelectionCount, setTopSelectionCount] = useState<string>('25');
    const [boltzgenClusterMode, setBoltzgenClusterMode] = useState<BoltzgenClusterMode>('exact_sequence');
    const [savedFilterSetName, setSavedFilterSetName] = useState<string>('');
    const [appliedSavedFilterSetId, setAppliedSavedFilterSetId] = useState<string | null>(null);
    const [savedReviewFilterSetsOverride, setSavedReviewFilterSetsOverride] = useState<SavedReviewFilterSet[] | null>(null);
    const [sequenceCopyFeedback, setSequenceCopyFeedback] = useState<'full' | 'fasta' | 'error' | null>(null);
    const PAGE_SIZE_OPTIONS = [50, 100, 250, 500, 1000, 0]; // 0 = All
    const [filterDraft, setFilterDraft] = useState<FilterDraftState>({
        sortField: 'name',
        sortDir: 'asc',
        plddtMin: 0,
        iptmMin: 0,
        ipsaeMin: 0,
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
    const jobSelectorRef = useRef<HTMLDivElement | null>(null);
    const overviewAnalysisMenuRef = useRef<HTMLDivElement | null>(null);

    // Fetch jobs list (include children for aggregation)
    const { data: jobsData, isLoading: jobsLoading } = useQuery({
        queryKey: ['jobs', 'include_children'],
        queryFn: () => fetchJobs({ include_children: true, limit: 2000 }),
    });
    const jobFromList = useMemo(
        () => (jobId ? (jobsData?.data.jobs ?? []).find((job: Job) => job.id === jobId) : undefined),
        [jobId, jobsData?.data.jobs],
    );
    const { data: routedJobData, isLoading: routedJobLoading } = useQuery({
        queryKey: ['job', jobId, 'direct'],
        queryFn: () => fetchJobById(jobId!),
        enabled: Boolean(jobId) && !jobFromList,
        retry: false,
    });
    const routedJob = routedJobData?.data;
    const jobs = useMemo(() => {
        const baseJobs = jobsData?.data.jobs ?? [];
        if (!routedJob || baseJobs.some((job: Job) => job.id === routedJob.id)) {
            return baseJobs;
        }
        return [routedJob, ...baseJobs];
    }, [jobsData?.data.jobs, routedJob]);
    const nonNgsJobs = useMemo(() => jobs.filter((j: Job) => !isNgsJob(j)), [jobs]);
    const jobsById = useMemo(
        () => new Map(nonNgsJobs.map((job: Job) => [job.id, job])),
        [nonNgsJobs],
    );
    const activeJob = useMemo(
        () => nonNgsJobs.find((j: Job) => j.id === selectedJobId),
        [nonNgsJobs, selectedJobId]
    );
    const activeParentJob = useMemo(
        () => activeJob?.parent_job_id ? nonNgsJobs.find((j: Job) => j.id === activeJob.parent_job_id) : undefined,
        [nonNgsJobs, activeJob?.parent_job_id]
    );
    const activeChildJobs = useMemo(
        () => activeJob
            ? nonNgsJobs.filter((job: Job) => job.parent_job_id === activeJob.id)
            : [],
        [activeJob, nonNgsJobs],
    );
    const activeJobHasDesignBearingChildren = useMemo(
        () => activeChildJobs.some((job) => (job.design_count || 0) > 0),
        [activeChildJobs],
    );
    const activeParentHasDesignBearingChildren = useMemo(
        () => activeParentJob
            ? nonNgsJobs.some((job: Job) => job.parent_job_id === activeParentJob.id && (job.design_count || 0) > 0)
            : false,
        [activeParentJob, nonNgsJobs],
    );
    const selectableLineageChildJobs = useMemo(
        () => nonNgsJobs.filter((job: Job) => {
            if (!job.parent_job_id || (job.design_count || 0) <= 0) return false;
            const parentJob = jobsById.get(job.parent_job_id);
            if (!parentJob) return true;
            return Boolean(parentJob.awaiting_input) || parentJob.status === 'awaiting_input' || (parentJob.design_count || 0) === 0;
        }),
        [jobsById, nonNgsJobs],
    );
    const selectableLineageChildJobIds = useMemo(
        () => new Set(selectableLineageChildJobs.map((job) => job.id)),
        [selectableLineageChildJobs],
    );
    const selectedLineageGroupKey = useMemo(() => {
        if (!activeJob?.parent_job_id || !selectableLineageChildJobIds.has(activeJob.id)) return null;
        return `${activeJob.parent_job_id}:${getLineageFamily(activeJob)}`;
    }, [activeJob, selectableLineageChildJobIds]);
    const jobSelectorOptions = useMemo<JobSelectorOption[]>(() => {
        const sortedJobs = [...nonNgsJobs].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
        const batchMap = new Map<string, { msaJob: Job | null; children: Job[]; totalDesigns: number }>();
        const childJobIds = new Set<string>();
        const lineageGroups = new Map<string, {
            representative: Job;
            parentJob: Job | undefined;
            totalDesigns: number;
            jobCount: number;
        }>();

        sortedJobs.forEach((job) => {
            if (job.parent_job_id) {
                childJobIds.add(job.id);
            }
            if (!job.batch_id) return;
            const isMsaJob = job.mode === 'msa_generation' || job.name.endsWith('_msa');
            const existing = batchMap.get(job.batch_id);
            if (existing) {
                if (isMsaJob) {
                    existing.msaJob = job;
                    existing.totalDesigns += job.design_count || 0;
                } else {
                    existing.children.push(job);
                    existing.totalDesigns += job.design_count || 0;
                    childJobIds.add(job.id);
                }
                return;
            }
            if (isMsaJob) {
                batchMap.set(job.batch_id, {
                    msaJob: job,
                    children: [],
                    totalDesigns: job.design_count || 0,
                });
                return;
            }
            batchMap.set(job.batch_id, {
                msaJob: null,
                children: [job],
                totalDesigns: job.design_count || 0,
            });
            childJobIds.add(job.id);
        });

        const regularOptions: JobSelectorOption[] = [];
        sortedJobs.forEach((job) => {
            const isSelectableLineageChild = selectableLineageChildJobIds.has(job.id);
            if (isSelectableLineageChild) {
                const groupKey = `${job.parent_job_id}:${getLineageFamily(job)}`;
                const existing = lineageGroups.get(groupKey);
                if (existing) {
                    existing.totalDesigns += job.design_count || 0;
                    existing.jobCount += 1;
                } else {
                    lineageGroups.set(groupKey, {
                        representative: job,
                        parentJob: job.parent_job_id ? jobsById.get(job.parent_job_id) : undefined,
                        totalDesigns: job.design_count || 0,
                        jobCount: 1,
                    });
                }
                return;
            }

            if (childJobIds.has(job.id)) return;

            const date = new Date(job.created_at);
            const dateStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            const timeStr = date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
            const batchData = job.batch_id ? batchMap.get(job.batch_id) : null;
            const hasChildren = Boolean(batchData && batchData.children.length > 0);
            const displayDesigns = (() => {
                if (isPostRfantibodyStage(job)) {
                    const rawCount = Number(job.awaiting_payload?.raw_candidate_count || 0);
                    const screenedCount = Number(job.awaiting_payload?.filtered_candidate_count || 0);
                    if (screenedCount > 0) return `Raw ${rawCount.toLocaleString()} / Screened ${screenedCount.toLocaleString()}`;
                    if (rawCount > 0) return `Raw ${rawCount.toLocaleString()}`;
                }
                const total = hasChildren ? (batchData?.totalDesigns || 0) : (job.design_count || 0);
                return `${total.toLocaleString()} designs`;
            })();
            const childIndicator = hasChildren ? ` • ${batchData?.children.length || 0} variants` : '';
            const sourceSummary = formatSourceSummary(job);
            const detailParts = [
                `${dateStr} ${timeStr}`,
                job.model_id || job.mode,
                childIndicator ? childIndicator.slice(3) : null,
                displayDesigns,
                sourceSummary ? `from ${sourceSummary}` : null,
            ].filter(Boolean);
            regularOptions.push({
                key: job.id,
                value: job.id,
                group: 'jobs',
                title: job.name,
                detail: detailParts.join(' • '),
                searchText: `${job.name} ${job.model_id || ''} ${job.mode || ''} ${displayDesigns} ${sourceSummary || ''}`.toLowerCase(),
                createdAt: date.getTime(),
            });
        });

        const lineageOptions = Array.from(lineageGroups.entries()).map(([groupKey, group]) => {
            const date = new Date(group.representative.created_at);
            const dateStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            const timeStr = date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
            const groupLabel = getLineageGroupLabel(group.representative);
            const parentName = group.parentJob?.name || 'Paused review root';
            const sourceSummary = formatSourceSummary(group.representative);
            return {
                key: groupKey,
                value: group.representative.id,
                group: 'lineage' as const,
                groupKey,
                title: `${parentName} • ${groupLabel}`,
                detail: [
                    `${dateStr} ${timeStr}`,
                    `${group.jobCount} job${group.jobCount === 1 ? '' : 's'}`,
                    `${group.totalDesigns.toLocaleString()} outputs`,
                    sourceSummary ? `from ${sourceSummary}` : null,
                ].filter(Boolean).join(' • '),
                searchText: `${parentName} ${groupLabel} ${group.representative.name} ${group.totalDesigns} ${sourceSummary || ''}`.toLowerCase(),
                createdAt: date.getTime(),
            };
        });

        return [...regularOptions, ...lineageOptions].sort((a, b) => b.createdAt - a.createdAt);
    }, [jobsById, nonNgsJobs, selectableLineageChildJobIds]);
    const filteredJobSelectorOptions = useMemo(() => {
        const query = jobSelectorSearch.trim().toLowerCase();
        if (!query) return jobSelectorOptions;
        return jobSelectorOptions.filter((option) => option.searchText.includes(query));
    }, [jobSelectorOptions, jobSelectorSearch]);
    const groupedJobSelectorOptions = useMemo(() => ([
        {
            key: 'jobs',
            label: 'Jobs',
            options: filteredJobSelectorOptions.filter((option) => option.group === 'jobs'),
        },
        {
            key: 'lineage',
            label: 'Lineage Outputs',
            options: filteredJobSelectorOptions.filter((option) => option.group === 'lineage'),
        },
    ]).filter((group) => group.options.length > 0), [filteredJobSelectorOptions]);
    const activeLineageRootJob = useMemo(() => {
        if (isPostRfantibodyStage(activeJob)) return activeJob;
        if (activeParentJob && isPostRfantibodyStage(activeParentJob)) return activeParentJob;
        if (activeJob && activeJobHasDesignBearingChildren) return activeJob;
        if (activeParentJob && activeParentHasDesignBearingChildren) return activeParentJob;
        return null;
    }, [activeJob, activeJobHasDesignBearingChildren, activeParentHasDesignBearingChildren, activeParentJob]);
    const activeLineageOutputJobs = useMemo(
        () => activeLineageRootJob
            ? nonNgsJobs
                .filter((job: Job) => job.parent_job_id === activeLineageRootJob.id && (job.design_count || 0) > 0)
                .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
            : [],
        [activeLineageRootJob, nonNgsJobs],
    );
    const activeLineageOutputGroups = useMemo(() => {
        const groups = new Map<string, {
            key: string;
            label: string;
            family: string;
            jobs: Job[];
            designCount: number;
        }>();
        activeLineageOutputJobs.forEach((job) => {
            const family = getLineageFamily(job);
            const key = family || 'child';
            const existing = groups.get(key);
            if (existing) {
                existing.jobs.push(job);
                existing.designCount += job.design_count || 0;
                return;
            }
            groups.set(key, {
                key,
                label: getLineageGroupLabel(job),
                family,
                jobs: [job],
                designCount: job.design_count || 0,
            });
        });
        return Array.from(groups.values()).sort((a, b) => {
            const orderDelta = (LINEAGE_GROUP_ORDER[a.family] ?? 999) - (LINEAGE_GROUP_ORDER[b.family] ?? 999);
            if (orderDelta !== 0) return orderDelta;
            return a.label.localeCompare(b.label);
        });
    }, [activeLineageOutputJobs]);
    const activeLineageOutputDesignCount = useMemo(
        () => activeLineageOutputJobs.reduce((sum, job) => sum + (job.design_count || 0), 0),
        [activeLineageOutputJobs],
    );
    const isAntibodyContext = useMemo(() => {
        if (!activeJob) return false;
        const modelId = (activeJob.model_id || '').toLowerCase();
        const mode = (activeJob.mode || '').toLowerCase();
        const name = (activeJob.name || '').toLowerCase();
        const rfdMode = String(activeJob.params?.rfd_mode || '').toLowerCase();
        const boltzgenMode = String(activeJob.params?.boltzgen_mode || activeJob.mode || '').toLowerCase();
        const frameworkType = String(activeJob.params?.framework_type || '').toLowerCase();
        const antibodyChains = String(activeJob.params?.antibody_chains || '').trim();
        const isPpiFlowSeededAntibody = modelId === 'ppiflow' && (
            mode === 'generator_backbone_refine' ||
            frameworkType === 'nanobody' ||
            Boolean(antibodyChains)
        );
        const isBoltzGenNanobody = modelId === 'boltzgen' && (
            boltzgenMode === 'nanobody_binder' ||
            frameworkType === 'nanobody'
        );
        return (
            modelId.includes('antibody') ||
            modelId === 'fampnn_child' ||
            mode.includes('antibody') ||
            name.includes('antibody') ||
            isAntibodyPipelineMode(rfdMode) ||
            isBoltzGenNanobody ||
            isPpiFlowSeededAntibody
        );
    }, [activeJob]);
    const isProteinLocalRedesignContext = useMemo(() => {
        if (!activeJob) return false;
        const modelId = String(activeJob.model_id || '').toLowerCase();
        const mode = String(activeJob.mode || '').toLowerCase();
        const rfdMode = String(activeJob.params?.rfd_mode || '').toLowerCase();
        return modelId === 'protein_local_redesign' || mode === 'local_redesign' || rfdMode === 'protein_local_redesign';
    }, [activeJob]);
    const isProteinLocalRedesignReviewContext = useMemo(
        () => isProteinLocalRedesignContext && Boolean(activeJob?.awaiting_input) && Boolean(activeJob?.awaiting_stage),
        [activeJob?.awaiting_input, activeJob?.awaiting_stage, isProteinLocalRedesignContext],
    );
    const showReviewWorkingSetPanel = isAntibodyContext || isProteinLocalRedesignReviewContext;
    const showBinderTargetConfidence = useMemo(() => hasExplicitBinderTargetRoles(activeJob), [activeJob]);
    const rfRawCount = Number(activeJob?.awaiting_payload?.raw_candidate_count || 0);
    const rfFilteredCount = Number(activeJob?.awaiting_payload?.filtered_candidate_count || 0);
    const persistedSavedReviewFilterSets = useMemo(
        () => coerceSavedReviewFilterSets(activeJob?.saved_selection_sets ?? activeJob?.awaiting_payload?.review_filter_sets),
        [activeJob?.awaiting_payload?.review_filter_sets, activeJob?.saved_selection_sets],
    );
    const savedReviewFilterSets = useMemo(
        () => savedReviewFilterSetsOverride ?? persistedSavedReviewFilterSets,
        [persistedSavedReviewFilterSets, savedReviewFilterSetsOverride],
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

    useEffect(() => {
        setSequenceCopyFeedback(null);
    }, [selectedDesignId]);

    useEffect(() => {
        if (!sequenceCopyFeedback) return undefined;
        const timeoutId = window.setTimeout(() => setSequenceCopyFeedback(null), 1800);
        return () => window.clearTimeout(timeoutId);
    }, [sequenceCopyFeedback]);

    const handleCopySequenceText = useCallback(async (text: string, kind: 'full' | 'fasta') => {
        try {
            await navigator.clipboard.writeText(text);
            setSequenceCopyFeedback(kind);
        } catch (error) {
            console.error('Failed to copy sequence text', error);
            setSequenceCopyFeedback('error');
        }
    }, []);

    // Sync URL with selection
    useEffect(() => {
        if (nonNgsJobs.length === 0) {
            if (jobsLoading || (jobId && routedJobLoading)) {
                return;
            }
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

            if (jobsLoading || routedJobLoading) {
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
    }, [jobId, nonNgsJobs, selectedJobId, activeJob, navigate, jobsLoading, routedJobLoading]);

    useEffect(() => {
        if (!activeJob?.parent_job_id || !activeParentJob) return;
        if ((activeJob.design_count || 0) > 0) return;
        const parentOwnsInteractiveReview = Boolean(activeParentJob.awaiting_input) || activeParentJob.status === 'awaiting_input';
        if (!parentOwnsInteractiveReview) return;
        if (selectedJobId === activeParentJob.id) return;
        setSelectedJobId(activeParentJob.id);
        setSelectedDesignId('');
        setCurrentPage(1);
        navigate(`/designs/${activeParentJob.id}`, { replace: true });
    }, [
        activeJob?.design_count,
        activeJob?.id,
        activeJob?.parent_job_id,
        activeParentJob,
        navigate,
        selectedJobId,
    ]);

    useEffect(() => {
        if (!activeJob) return;
        if (Boolean(activeJob.awaiting_input) || activeJob.status === 'awaiting_input') return;
        if ((activeJob.design_count || 0) > 0) return;

        const designBearingChildren = activeChildJobs.filter((job) => (job.design_count || 0) > 0);
        if (designBearingChildren.length !== 1) return;

        const childJob = designBearingChildren[0];
        if (!childJob?.id || selectedJobId === childJob.id) return;

        setSelectedJobId(childJob.id);
        setSelectedDesignId('');
        setCurrentPage(1);
        navigate(`/designs/${childJob.id}`, { replace: true });
    }, [
        activeChildJobs,
        activeJob,
        navigate,
        selectedJobId,
    ]);

    const handleSelectJob = useCallback((newId: string, replace = false) => {
        setSelectedJobId(newId);
        setSelectedDesignId('');
        setCurrentPage(1); // Reset pagination when switching jobs
        setShowJobSelectorMenu(false);
        setJobSelectorSearch('');
        if (newId) {
            navigate(`/designs/${newId}`, replace ? { replace: true } : undefined);
        } else {
            navigate('/designs', replace ? { replace: true } : undefined);
        }
    }, [navigate]);
    const handleSelectLineageGroup = useCallback((family: string) => {
        if (!activeLineageRootJob?.id) return;
        const sourceFilter = (
            family === 'rfantibody'
            || family === 'boltzgen'
            || family === 'fampnn'
            || family === 'ppiflow'
            || family === 'validation'
        ) ? family as OutputSourceFilter : 'all';
        manualOutputSourceSelectionRef.current = true;
        outputSourceSelectionJobRef.current = activeLineageRootJob.id;
        setOutputSourceFilter(sourceFilter);
        setAntibodySourceFilter(sourceFilter);
        setSelectedBackboneId(null);
        setSelectedJobId(activeLineageRootJob.id);
        setSelectedDesignId('');
        setCurrentPage(1);
        navigate(`/designs/${activeLineageRootJob.id}`, { replace: true });
    }, [activeLineageRootJob, navigate]);
    const toggleExpandedLineageGroup = useCallback((groupKey: string) => {
        setExpandedLineageGroups((current) => {
            const next = new Set(current);
            if (next.has(groupKey)) next.delete(groupKey);
            else next.add(groupKey);
            return next;
        });
    }, []);

    useEffect(() => {
        if (!showJobSelectorMenu) return;
        const handlePointerDown = (event: MouseEvent) => {
            const target = event.target as Node | null;
            if (!jobSelectorRef.current?.contains(target)) {
                setShowJobSelectorMenu(false);
                setJobSelectorSearch('');
            }
        };
        document.addEventListener('mousedown', handlePointerDown);
        return () => document.removeEventListener('mousedown', handlePointerDown);
    }, [showJobSelectorMenu]);

    useEffect(() => {
        if (!showOverviewAnalysisMenu) return;
        const handlePointerDown = (event: MouseEvent) => {
            const target = event.target as Node | null;
            if (!overviewAnalysisMenuRef.current?.contains(target)) {
                setShowOverviewAnalysisMenu(false);
            }
        };
        document.addEventListener('mousedown', handlePointerDown);
        return () => document.removeEventListener('mousedown', handlePointerDown);
    }, [showOverviewAnalysisMenu]);

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
    const useClientRenderedValueSort = CLIENT_RENDER_SORT_FIELDS.has(sortField);
    const apiSortField = SERVER_SORT_FIELDS.has(sortField as DesignSortField) && !useClientRenderedValueSort
        ? sortField as DesignSortField
        : undefined;
    const isPostRFantibodyReview = isAntibodyContext && isPostRfantibodyStage(activeJob);
    const hasExplicitReviewSelection = !isPostRFantibodyReview || rfReviewSet !== null;
    const reviewSelectionRequired = isPostRFantibodyReview && !hasExplicitReviewSelection;
    const availableSortOptions = useMemo(
        () => SORT_OPTIONS
            .filter((option) => !isPostRFantibodyReview || option.value !== 'binding_tier')
            .map((option) => option.value === 'plddt_overall' && isPostRFantibodyReview
                ? { ...option, label: 'RF pLDDT' }
                : option),
        [isPostRFantibodyReview],
    );
    const activeRfArtifactGroup = isPostRFantibodyReview && rfReviewSet ? rfReviewSet : undefined;
    const activeSavedSubsetDesignIds = hasExplicitReviewSelection && appliedSavedReviewFilterSet?.design_ids?.length
        ? appliedSavedReviewFilterSet.design_ids
        : undefined;
    const activeReviewSourceSelection = useMemo<ReviewSourceSelectorValue>(() => {
        if (!isPostRFantibodyReview) return '';
        if (appliedSavedReviewFilterSet?.id) {
            return `saved:${appliedSavedReviewFilterSet.id}`;
        }
        if (rfReviewSet) {
            return `live:${rfReviewSet}`;
        }
        return '';
    }, [appliedSavedReviewFilterSet?.id, isPostRFantibodyReview, rfReviewSet]);
    const backboneFilterApplies = outputSourceFilter === 'all' || outputSourceFilter === 'rfantibody' || outputSourceFilter === 'boltzgen';
    const useClientSourcePagination = outputSourceFilter !== 'all';
    const requiresClientOnlySort = useClientRenderedValueSort
        || (!SERVER_SORT_FIELDS.has(sortField as DesignSortField) && isTableColumnSortable(sortField));
    const forceBulkLoadForSorting = useClientSourcePagination || pageSize === 0 || requiresClientOnlySort;
    const isReviewStageJob = isStageReviewJob(activeJob);
    const designQueryFilters = useMemo<DesignFilters>(() => ({
        job_id: selectedJobId,
        include_children: !isReviewStageJob && Boolean(
            activeJobHasDesignBearingChildren && !(activeJob?.design_count || 0)
        ),
        design_ids: activeSavedSubsetDesignIds,
        q: filterText.trim() || undefined,
        limit: forceBulkLoadForSorting ? MAX_BULK_SELECTION_DESIGNS : pageSize,
        offset: forceBulkLoadForSorting ? 0 : (currentPage - 1) * pageSize,
        sort_by: apiSortField,
        sort_desc: sortDir === 'desc',
        backbone_id: backboneFilterApplies ? (selectedBackboneId ?? undefined) : undefined,
        plddt_min: plddtMin > 0 ? plddtMin : undefined,
        iptm_min: iptmMin > 0 ? iptmMin : undefined,
        ipsae_min: ipsaeMin > 0 ? ipsaeMin : undefined,
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
        isReviewStageJob,
        filterText,
        pageSize,
        currentPage,
        apiSortField,
        sortDir,
        selectedBackboneId,
        plddtMin,
        iptmMin,
        ipsaeMin,
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
        activeJob?.design_count,
        activeJobHasDesignBearingChildren,
        backboneFilterApplies,
        isPostRFantibodyReview,
        useClientSourcePagination,
        forceBulkLoadForSorting,
    ]);
    const bulkSelectionFilters = useMemo<DesignFilters>(() => ({
        ...designQueryFilters,
        limit: MAX_BULK_SELECTION_DESIGNS,
        offset: 0,
    }), [designQueryFilters]);

    const { data: designsData, isLoading: designsLoading } = useQuery({
        queryKey: ['designs', designQueryFilters],
        queryFn: () => fetchDesigns(designQueryFilters),
        enabled: !!activeJob && !reviewSelectionRequired,
    });
    const rawDesigns = designsData?.data.designs ?? [];
    const serverTotalDesigns = designsData?.data.total ?? rawDesigns.length;
    const canClientSortLoadedDesigns = forceBulkLoadForSorting || serverTotalDesigns <= rawDesigns.length;
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
    const orderedDesigns = useMemo(() => {
        if (!canClientSortLoadedDesigns) return designs;
        return [...designs].sort((left, right) => compareDesignsByField(left, right, sortField, sortDir));
    }, [canClientSortLoadedDesigns, designs, sortDir, sortField]);

    const { data: selectedDesignDetailData } = useQuery({
        queryKey: ['design', selectedDesignId],
        queryFn: () => (selectedDesignId ? fetchDesignById(selectedDesignId).then((response) => response.data) : null),
        enabled: !!selectedDesignId,
        staleTime: 30_000,
    });

    // Fetch backbone summary for toggle UI
    const { data: backboneSummaryData } = useQuery({
        queryKey: ['backboneSummary', selectedJobId, activeRfArtifactGroup],
        queryFn: () => fetchBackboneSummary(selectedJobId, activeRfArtifactGroup),
        enabled: !!activeJob && !reviewSelectionRequired,
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
        if (reviewSelectionRequired) return null;
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
        reviewSelectionRequired,
    ]);
    const reviewBackboneRows = useMemo(() => {
        if (reviewSelectionRequired) return [];
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
    }, [backboneSummary, currentGateBackboneSummary, gateCandidateBackboneSummary, gateRawBackboneSummary, gateFilteredBackboneSummary, reviewSelectionRequired]);
    const availableReviewBackboneFamilyCount = useMemo(() => {
        if (reviewBackboneRows.length > 0) return reviewBackboneRows.length;
        const fallbackBackbones =
            gateCandidateBackboneSummary?.backbones
            || gateFilteredBackboneSummary?.backbones
            || gateRawBackboneSummary?.backbones
            || backboneSummary?.backbones
            || null;
        return fallbackBackbones ? Object.keys(fallbackBackbones).length : 0;
    }, [backboneSummary?.backbones, gateCandidateBackboneSummary?.backbones, gateFilteredBackboneSummary?.backbones, gateRawBackboneSummary?.backbones, reviewBackboneRows.length]);
    const reviewBackboneTotal = useMemo(() => {
        if (reviewSelectionRequired) return 0;
        if (isPostRFantibodyReview) {
            if (rfReviewSet === 'raw') return gateRawBackboneSummary?.total ?? backboneSummary?.total ?? 0;
            return gateFilteredBackboneSummary?.total ?? gateCandidateBackboneSummary?.total ?? backboneSummary?.total ?? 0;
        }
        return backboneSummary?.total ?? 0;
    }, [isPostRFantibodyReview, rfReviewSet, gateCandidateBackboneSummary?.total, gateRawBackboneSummary?.total, gateFilteredBackboneSummary?.total, backboneSummary?.total, reviewSelectionRequired]);
    const selectedReviewBackbone = useMemo(
        () => (selectedBackboneId == null ? null : reviewBackboneRows.find((row) => row.id === selectedBackboneId) ?? null),
        [reviewBackboneRows, selectedBackboneId],
    );
    useEffect(() => {
        if (selectedBackboneId == null) return;
        if (!backboneFilterApplies || !reviewBackboneRows.some((row) => row.id === selectedBackboneId)) {
            setSelectedBackboneId(null);
        }
    }, [backboneFilterApplies, selectedBackboneId, reviewBackboneRows]);

    const { data: structureAnalysisRun } = useQuery({
        queryKey: ['design-analysis', 'structure_summary', selectedDesignId],
        queryFn: () => (
            selectedDesignId
                ? fetchDesignAnalysis<StructureAnalysis>(selectedDesignId, 'structure_summary').then((response) => response.data)
                : null
        ),
        enabled: !!selectedDesignId && (activeTab === 'structure' || activeTab === 'overview'),
        refetchInterval: (query) => {
            const status = (query.state.data as PersistedAnalysisRun<StructureAnalysis> | null | undefined)?.status;
            return status === 'queued' || status === 'running' ? 1500 : false;
        },
    });
    const structureAnalysis = structureAnalysisRun?.status === 'completed'
        ? (structureAnalysisRun.result as StructureAnalysis | null)
        : null;
    const runStructureAnalysis = useMutation({
        mutationFn: async () => {
            if (!selectedDesignId) {
                throw new Error('No design selected');
            }
            const response = await triggerDesignAnalysis<StructureAnalysis>(selectedDesignId, 'structure_summary');
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['design-analysis', 'structure_summary', selectedDesignId] });
        },
    });
    const structureAnalysisBusy = runStructureAnalysis.isPending
        || structureAnalysisRun?.status === 'queued'
        || structureAnalysisRun?.status === 'running';

    const { data: antibodyAnalysisRun } = useQuery({
        queryKey: ['design-analysis', 'antibody_annotation_pack', selectedDesignId],
        queryFn: () => (
            selectedDesignId
                ? fetchDesignAnalysis<AntibodyData>(selectedDesignId, 'antibody_annotation_pack').then((response) => response.data)
                : null
        ),
        enabled: !!selectedDesignId && (activeTab === 'antibody' || activeTab === 'structure' || activeTab === 'overview'),
        retry: false,
        refetchInterval: (query) => {
            const status = (query.state.data as PersistedAnalysisRun<AntibodyData> | null | undefined)?.status;
            return status === 'queued' || status === 'running' ? 1500 : false;
        },
    });
    const antibodyData = antibodyAnalysisRun?.status === 'completed'
        ? (antibodyAnalysisRun.result as AntibodyData | null)
        : null;
    const runAntibodyAnalysis = useMutation({
        mutationFn: async () => {
            if (!selectedDesignId) {
                throw new Error('No design selected');
            }
            const response = await triggerDesignAnalysis<AntibodyData>(selectedDesignId, 'antibody_annotation_pack');
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['design-analysis', 'antibody_annotation_pack', selectedDesignId] });
        },
    });
    const antibodyAnalysisBusy = runAntibodyAnalysis.isPending
        || antibodyAnalysisRun?.status === 'queued'
        || antibodyAnalysisRun?.status === 'running';
    const antibodyAnalysisStatus = antibodyAnalysisRun?.status ?? 'missing';
    const antibodyAnalysisStatusCopy = antibodyAnalysisStatus === 'completed'
        ? 'Cached'
        : antibodyAnalysisStatus === 'running'
            ? 'Running'
            : antibodyAnalysisStatus === 'queued'
                ? 'Queued'
                : antibodyAnalysisStatus === 'failed'
                    ? 'Failed'
                    : 'Not computed';
    const autoTriggeredAntibodyAnalysisRef = useRef<Set<string>>(new Set());
    useEffect(() => {
        if (activeTab !== 'antibody' || !selectedDesignId) return;
        if (antibodyAnalysisStatus !== 'missing' || antibodyAnalysisBusy) return;
        if (autoTriggeredAntibodyAnalysisRef.current.has(selectedDesignId)) return;
        autoTriggeredAntibodyAnalysisRef.current.add(selectedDesignId);
        runAntibodyAnalysis.mutate();
    }, [activeTab, selectedDesignId, antibodyAnalysisBusy, antibodyAnalysisStatus, runAntibodyAnalysis]);

    const { data: chainMetricsAnalysisRun } = useQuery({
        queryKey: ['design-analysis', 'chain_metrics', selectedDesignId],
        queryFn: () => (
            selectedDesignId
                ? fetchDesignAnalysis<Record<string, ChainMetric>>(selectedDesignId, 'chain_metrics').then((response) => response.data)
                : null
        ),
        enabled: !!selectedDesignId && activeTab === 'overview',
        staleTime: 60000,
        refetchInterval: (query) => {
            const status = (query.state.data as PersistedAnalysisRun<Record<string, ChainMetric>> | null | undefined)?.status;
            return status === 'queued' || status === 'running' ? 1500 : false;
        },
    });
    const chainMetricsAnalysis = chainMetricsAnalysisRun?.status === 'completed'
        ? (chainMetricsAnalysisRun.result as Record<string, ChainMetric> | null)
        : null;
    const runChainMetricsAnalysis = useMutation({
        mutationFn: async () => {
            if (!selectedDesignId) {
                throw new Error('No design selected');
            }
            const response = await triggerDesignAnalysis<Record<string, ChainMetric>>(selectedDesignId, 'chain_metrics');
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['design-analysis', 'chain_metrics', selectedDesignId] });
        },
    });
    const chainMetricsAnalysisBusy = runChainMetricsAnalysis.isPending
        || chainMetricsAnalysisRun?.status === 'queued'
        || chainMetricsAnalysisRun?.status === 'running';

    const { data: ipsaeAnalysisRun } = useQuery({
        queryKey: ['design-analysis', 'ipsae_interface', selectedDesignId],
        queryFn: () => (
            selectedDesignId
                ? fetchDesignAnalysis<IpsaeInterfaceAnalysis>(selectedDesignId, 'ipsae_interface').then((response) => response.data)
                : null
        ),
        enabled: !!selectedDesignId && activeTab === 'overview',
        staleTime: 60000,
        refetchInterval: (query) => {
            const status = (query.state.data as PersistedAnalysisRun<IpsaeInterfaceAnalysis> | null | undefined)?.status;
            return status === 'queued' || status === 'running' ? 1500 : false;
        },
    });
    const ipsaeAnalysis = ipsaeAnalysisRun?.status === 'completed'
        ? (ipsaeAnalysisRun.result as IpsaeInterfaceAnalysis | null)
        : null;
    const runIpsaeAnalysis = useMutation({
        mutationFn: async () => {
            if (!selectedDesignId) {
                throw new Error('No design selected');
            }
            const response = await triggerDesignAnalysis<IpsaeInterfaceAnalysis>(selectedDesignId, 'ipsae_interface');
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['design-analysis', 'ipsae_interface', selectedDesignId] });
        },
    });
    const ipsaeAnalysisBusy = runIpsaeAnalysis.isPending
        || ipsaeAnalysisRun?.status === 'queued'
        || ipsaeAnalysisRun?.status === 'running';

    const { data: paeMatrixAnalysisRun } = useQuery({
        queryKey: ['design-analysis', 'pae_matrix', selectedDesignId],
        queryFn: () => (
            selectedDesignId
                ? fetchDesignAnalysis<PAEData>(selectedDesignId, 'pae_matrix', { max_size: 200 }).then((response) => response.data)
                : null
        ),
        enabled: !!selectedDesignId && activeTab === 'overview',
        staleTime: 60000,
        refetchInterval: (query) => {
            const status = (query.state.data as PersistedAnalysisRun<PAEData> | null | undefined)?.status;
            return status === 'queued' || status === 'running' ? 1500 : false;
        },
    });
    const paeMatrixAnalysis = paeMatrixAnalysisRun?.status === 'completed'
        ? (paeMatrixAnalysisRun.result as PAEData | null)
        : null;
    const runPaeMatrixAnalysis = useMutation({
        mutationFn: async () => {
            if (!selectedDesignId) {
                throw new Error('No design selected');
            }
            const response = await triggerDesignAnalysis<PAEData>(selectedDesignId, 'pae_matrix', { max_size: 200 });
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['design-analysis', 'pae_matrix', selectedDesignId] });
        },
    });
    const paeMatrixAnalysisBusy = runPaeMatrixAnalysis.isPending
        || paeMatrixAnalysisRun?.status === 'queued'
        || paeMatrixAnalysisRun?.status === 'running';

    const { data: contactMapAnalysisRun } = useQuery({
        queryKey: ['design-analysis', 'contact_map', selectedDesignId],
        queryFn: () => (
            selectedDesignId
                ? fetchDesignAnalysis<ContactMapData>(selectedDesignId, 'contact_map', { max_size: 300 }).then((response) => response.data)
                : null
        ),
        enabled: !!selectedDesignId && activeTab === 'overview',
        staleTime: 60000,
        refetchInterval: (query) => {
            const status = (query.state.data as PersistedAnalysisRun<ContactMapData> | null | undefined)?.status;
            return status === 'queued' || status === 'running' ? 1500 : false;
        },
    });
    const contactMapAnalysis = contactMapAnalysisRun?.status === 'completed'
        ? (contactMapAnalysisRun.result as ContactMapData | null)
        : null;
    const runContactMapAnalysis = useMutation({
        mutationFn: async () => {
            if (!selectedDesignId) {
                throw new Error('No design selected');
            }
            const response = await triggerDesignAnalysis<ContactMapData>(selectedDesignId, 'contact_map', { max_size: 300 });
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['design-analysis', 'contact_map', selectedDesignId] });
        },
    });
    const contactMapAnalysisBusy = runContactMapAnalysis.isPending
        || contactMapAnalysisRun?.status === 'queued'
        || contactMapAnalysisRun?.status === 'running';

    // Antibody selections for Molstar are sourced from backend-issued chain/range overlays.
    // Do not synthesize them from parent workflow hints.
    const antibodySelections = useMemo(() => {
        const overlaySelections = antibodyData?.overlay_selections;
        if (!overlaySelections?.length) return undefined;

        const regionColors: Record<string, { r: number; g: number; b: number }> = {
            H1: { r: 255, g: 50, b: 50 },
            H2: { r: 50, g: 255, b: 50 },
            H3: { r: 50, g: 100, b: 255 },
            L1: { r: 255, g: 255, b: 50 },
            L2: { r: 50, g: 255, b: 255 },
            L3: { r: 255, g: 50, b: 255 },
        };

        return overlaySelections.map((selection) => ({
            chain_id: selection.chain_id,
            start_residue_number: selection.start_residue_number,
            end_residue_number: selection.end_residue_number,
            color: regionColors[selection.region] ?? { r: 148, g: 163, b: 184 },
        }));
    }, [antibodyData?.overlay_selections]);

    const analyticsChartDesigns = designs;
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
    const sourceScopedDesigns = useMemo(() => {
        if (outputSourceFilter === 'all') return orderedDesigns;
        return orderedDesigns.filter((design) => inferDesignOutputSource(design as any) === outputSourceFilter);
    }, [orderedDesigns, outputSourceFilter]);
    const boltzgenScopedDesigns = useMemo(
        () => sourceScopedDesigns.filter((design) => inferDesignOutputSource(design as any) === 'boltzgen'),
        [sourceScopedDesigns],
    );
    const boltzgenClusterSummary = useMemo(
        () => buildBoltzgenClusters(boltzgenScopedDesigns, boltzgenClusterMode),
        [boltzgenClusterMode, boltzgenScopedDesigns],
    );
    const showBoltzgenClusterPanel = showReviewWorkingSetPanel && boltzgenScopedDesigns.length > 0;
    const totalDesigns = useClientSourcePagination ? sourceScopedDesigns.length : serverTotalDesigns;
    const totalPages = pageSize === 0 ? 1 : Math.ceil(totalDesigns / pageSize);
    const tableDesigns = useMemo(() => {
        if (!useClientSourcePagination || pageSize === 0) return sourceScopedDesigns;
        const start = Math.max(0, (currentPage - 1) * pageSize);
        return sourceScopedDesigns.slice(start, start + pageSize);
    }, [currentPage, pageSize, sourceScopedDesigns, useClientSourcePagination]);
    const showPpiflowColumns = outputSourceFilter === 'ppiflow'
        || preferredAnalysisLens === 'ppiflow'
        || orderedDesigns.some((design) => inferDesignOutputSource(design as any) === 'ppiflow');
    const visibleDesignIds = useMemo(() => tableDesigns.map((design) => design.id), [tableDesigns]);
    const visibleSelectionRef = useRef<HTMLInputElement | null>(null);
    const tableScrollViewportRef = useRef<HTMLDivElement | null>(null);
    const tablePanStateRef = useRef({
        pointerId: null as number | null,
        startX: 0,
        startY: 0,
        scrollLeft: 0,
        scrollTop: 0,
        moved: false,
    });
    const tableSuppressClickUntilRef = useRef(0);
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
        : rfReviewSet
            ? `${activeReviewSetLabel} set`
            : 'No review set selected';
    const activeBadgeLabel = useMemo(() => {
        if (isPostRFantibodyReview && reviewSelectionRequired) {
            return 'Select a review source';
        }
        if (isPostRFantibodyReview) {
            return `${activeCurrentSetLabel} ${totalDesigns.toLocaleString()} outputs`;
        }
        if (outputSourceFilter !== 'all' && tableDesigns.length !== totalDesigns) {
            return `${tableDesigns.length.toLocaleString()} visible`;
        }
        return `${totalDesigns.toLocaleString()} designs`;
    }, [activeCurrentSetLabel, isPostRFantibodyReview, outputSourceFilter, reviewSelectionRequired, tableDesigns.length, totalDesigns]);
    const paginationSubject = isPostRFantibodyReview
        ? reviewSelectionRequired
            ? 'outputs'
            : `${activeCurrentSetLabel.toLowerCase()} outputs`
        : outputSourceFilter !== 'all'
            ? `${outputSourceFilter} outputs`
            : 'designs';
    const beginTablePan = (event: React.PointerEvent<HTMLDivElement>) => {
        if (event.button !== 0) return;
        const target = event.target as HTMLElement | null;
        if (target?.closest('input, button, select, a, label, thead, th, [data-table-interactive="true"]')) return;
        const viewport = tableScrollViewportRef.current;
        if (!viewport) return;
        tablePanStateRef.current = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            scrollLeft: viewport.scrollLeft,
            scrollTop: viewport.scrollTop,
            moved: false,
        };
        viewport.setPointerCapture?.(event.pointerId);
    };
    const moveTablePan = (event: React.PointerEvent<HTMLDivElement>) => {
        const viewport = tableScrollViewportRef.current;
        const state = tablePanStateRef.current;
        if (!viewport || state.pointerId !== event.pointerId) return;
        const deltaX = event.clientX - state.startX;
        const deltaY = event.clientY - state.startY;
        if (!state.moved && (Math.abs(deltaX) > 8 || Math.abs(deltaY) > 8)) {
            state.moved = true;
        }
        if (!state.moved) return;
        tableSuppressClickUntilRef.current = Date.now() + 75;
        viewport.scrollLeft = state.scrollLeft - deltaX;
        viewport.scrollTop = state.scrollTop - deltaY;
    };
    const endTablePan = (event: React.PointerEvent<HTMLDivElement>) => {
        const viewport = tableScrollViewportRef.current;
        const state = tablePanStateRef.current;
        if (state.pointerId !== event.pointerId) return;
        if (state.moved) {
            tableSuppressClickUntilRef.current = Date.now() + 75;
        }
        viewport?.releasePointerCapture?.(event.pointerId);
        tablePanStateRef.current = {
            pointerId: null,
            startX: 0,
            startY: 0,
            scrollLeft: 0,
            scrollTop: 0,
            moved: false,
        };
    };
    const shouldSuppressTableClick = () => Date.now() < tableSuppressClickUntilRef.current;

    // Fetch PDB content when design selected
    // Note: MolstarViewer now fetches structure directly from API URL

    // Auto-select first design
    useEffect(() => {
        if (reviewSelectionRequired) {
            if (selectedDesignId) setSelectedDesignId('');
            return;
        }
        if (designs.length > 0 && !selectedDesignId) {
            setSelectedDesignId(designs[0].id);
        }
    }, [designs, reviewSelectionRequired, selectedDesignId]);

    useEffect(() => {
        if (reviewSelectionRequired) return;
        if (activeTab !== 'structure') return;
        if (tableDesigns.length === 0) return;
        if (!selectedDesignId || !tableDesigns.some((design) => design.id === selectedDesignId)) {
            setSelectedDesignId(tableDesigns[0].id);
        }
    }, [activeTab, reviewSelectionRequired, tableDesigns, selectedDesignId]);

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
        setShowOverviewAnalysisMenu(false);
    }, [selectedDesignId]);

    useEffect(() => {
        setCurrentPage(1);
    }, [
        selectedJobId,
        filterText,
        selectedBackboneId,
        plddtMin,
        iptmMin,
        ipsaeMin,
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
        outputSourceFilter,
    ]);

    useEffect(() => {
        if (manualOutputSourceSelectionRef.current && outputSourceSelectionJobRef.current === selectedJobId) {
            return;
        }
        const preferredSource = inferPreferredOutputSource(activeJob);
        const nextSource = preferredSource !== 'all' && designs.some((design) => inferDesignOutputSource(design as any) === preferredSource)
            ? preferredSource
            : 'all';
        outputSourceSelectionJobRef.current = selectedJobId || null;
        setOutputSourceFilter(nextSource);
        setAntibodySourceFilter(nextSource);
    }, [selectedJobId, activeJob?.awaiting_stage, activeJob?.current_stage, activeJob?.awaiting_payload?.candidate_dir, designs]);

    useEffect(() => {
        setRfReviewSet(null);
        setSelectedBackboneId(null);
        setSelectedDesignId('');
    }, [selectedJobId, isPostRFantibodyReview]);

    useEffect(() => {
        if (!isPostRFantibodyReview) return;
        setSortField((current) => (current === 'name' ? 'backbone' : current));
    }, [selectedJobId, isPostRFantibodyReview]);

    useEffect(() => {
        setAppliedSavedFilterSetId(null);
    }, [selectedJobId]);

    useEffect(() => {
        setSavedReviewFilterSetsOverride(null);
    }, [selectedJobId]);

    useEffect(() => {
        setFilterDraft({
            sortField,
            sortDir,
            plddtMin,
            iptmMin,
            ipsaeMin,
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
        ipsaeMin,
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

    const selectedDesign = selectedDesignDetailData ?? designs.find(d => d.id === selectedDesignId);
    const preferredRfMetricScope = useMemo(
        () => getPreferredRfMetricScope(activeJob, designs[0] || null),
        [activeJob, designs],
    );
    const selectedDesignRfLoopEntries = useMemo(() => getRfLoopEntries(selectedDesign), [selectedDesign]);
    const selectedDesignRfLoopSummary = useMemo(() => getRfLoopSummary(selectedDesign), [selectedDesign]);
    const rfMetricLabels = RF_SCOPE_LABELS[rfMetricScope];

    useEffect(() => {
        if (!isPostRFantibodyReview) return;
        setRfMetricScope(preferredRfMetricScope);
    }, [isPostRFantibodyReview, preferredRfMetricScope, selectedJobId]);
    const hasCdrAnnotation = Boolean(
        selectedDesign && (
            selectedDesign.cdr_h1_length ||
            selectedDesign.cdr_h2_length ||
            selectedDesign.cdr_h3_length ||
            selectedDesign.cdr_l1_length ||
            selectedDesign.cdr_l2_length ||
            selectedDesign.cdr_l3_length ||
            antibodyData?.cdr_lengths?.H1 ||
            antibodyData?.cdr_lengths?.H2 ||
            antibodyData?.cdr_lengths?.H3 ||
            antibodyData?.cdr_lengths?.L1 ||
            antibodyData?.cdr_lengths?.L2 ||
            antibodyData?.cdr_lengths?.L3
        )
    );
    const hasCdrOverlay = Boolean(antibodySelections?.length);
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
        if (selectedDesignLens === 'fampnn') {
            setColorMode('fampnn_psce');
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
        if (hasCdrAnnotation && hasCdrOverlay) {
            setColorMode('cdr');
            return;
        }
        setColorMode('default');
    }, [hasCdrAnnotation, hasCdrOverlay, isOligoJob, selectedDesign?.frustration_residues?.length, selectedDesignLens, selectedJobId]);
    const antibodyDesignGroups = useMemo(() => {
        const grouped: Record<OutputSourceFilter, typeof designs> = { all: [], rfantibody: [], boltzgen: [], fampnn: [], ppiflow: [], validation: [] };
        for (const design of orderedDesigns) {
            const source = inferDesignOutputSource(design);
            if (source === 'rfantibody' || source === 'boltzgen' || source === 'fampnn' || source === 'ppiflow' || source === 'validation') grouped[source].push(design);
            else grouped.all.push(design);
        }
        grouped.ppiflow.sort((a, b) => {
            const ordinalDelta = (getPpiflowSourceOrdinal(a) ?? Number.MAX_SAFE_INTEGER) - (getPpiflowSourceOrdinal(b) ?? Number.MAX_SAFE_INTEGER);
            if (ordinalDelta !== 0) return ordinalDelta;
            const sourceDelta = getPpiflowSourceKey(a).localeCompare(getPpiflowSourceKey(b));
            if (sourceDelta !== 0) return sourceDelta;
            return (getPpiflowSampleIndex(a) ?? Number.MAX_SAFE_INTEGER) - (getPpiflowSampleIndex(b) ?? Number.MAX_SAFE_INTEGER);
        });
        return grouped;
    }, [orderedDesigns]);
    const antibodyTabDesigns = useMemo(() => {
        if (antibodySourceFilter === 'all') return orderedDesigns;
        return antibodyDesignGroups[antibodySourceFilter] || [];
    }, [orderedDesigns, antibodyDesignGroups, antibodySourceFilter]);
    const selectedDesignSource = selectedDesign ? inferDesignOutputSource(selectedDesign) : 'all';
    const selectedDesignProvenance = useMemo(() => asRecord(selectedDesign?.provenance), [selectedDesign?.provenance]);
    const selectedDesignFampnnPayload = useMemo(() => (
        getFampnnRecord(selectedDesign)
    ), [selectedDesign]);
    const selectedDesignFampnnMaxResiduePsce = useMemo(
        () => getFampnnMaxResiduePsce(selectedDesign),
        [selectedDesign],
    );
    const selectedDesignPpiflowRecord = useMemo(() => (
        getPpiflowRecord(selectedDesign)
    ), [selectedDesign]);
    const selectedDesignHasPpiflowLens = selectedDesignLens === 'ppiflow' || Boolean(selectedDesignPpiflowRecord);
    const selectedDesignSequenceSource = useMemo(() => {
        const stageSequence = typeof selectedDesignFampnnPayload?.sequence === 'string' ? selectedDesignFampnnPayload.sequence.trim() : '';
        if (stageSequence) {
            return { sequence: stageSequence, kind: 'stage' as const };
        }
        const binderSequence = [
            selectedDesign?.binder_sequence,
            selectedDesignFampnnPayload?.binder_sequence,
        ].find((value) => typeof value === 'string' && value.trim());
        return {
            sequence: typeof binderSequence === 'string' ? binderSequence.trim() : '',
            kind: 'binder' as const,
        };
    }, [selectedDesign?.binder_sequence, selectedDesignFampnnPayload]);
    const selectedDesignSequenceEntries = useMemo(
        () => parseStageSequenceEntries(
            selectedDesignSequenceSource.sequence,
            selectedDesignFampnnPayload?.chain_avg_psce,
            selectedDesign?.detected_antibody_chains,
        ),
        [selectedDesign?.detected_antibody_chains, selectedDesignFampnnPayload, selectedDesignSequenceSource],
    );
    const selectedDesignSequenceSourceLabel = selectedDesignSequenceSource.kind === 'stage'
        ? 'Stage-native sequence output'
        : 'Structure-derived binder sequence';
    const selectedDesignFullSequence = useMemo(
        () => selectedDesignSequenceEntries.map((entry) => entry.sequence).join(''),
        [selectedDesignSequenceEntries],
    );
    const selectedDesignFullSequenceFasta = useMemo(() => {
        if (selectedDesignSequenceEntries.length === 0) return '';
        const recordName = selectedDesign?.name?.trim() || 'selected_design';
        return selectedDesignSequenceEntries
            .map((entry) => `>${recordName}|chain_${entry.chain}\n${entry.sequence}`)
            .join('\n');
    }, [selectedDesign?.name, selectedDesignSequenceEntries]);
    const selectedDesignSequenceChainSummary = useMemo(
        () => selectedDesignSequenceEntries.map((entry) => `${entry.chain} (${entry.length} aa)`).join(' • '),
        [selectedDesignSequenceEntries],
    );
    const selectedDesignSequenceViewerText = useMemo(
        () => formatSequenceViewerText(selectedDesignSequenceEntries),
        [selectedDesignSequenceEntries],
    );
    const selectedDesignStageSettings = useMemo(() => {
        const provenanceSettings = asRecord(selectedDesignProvenance?.stage_settings)
            ?? asRecord(selectedDesignPpiflowRecord?.stage_settings);
        if (provenanceSettings) return provenanceSettings;
        const params = activeJob?.params || {};
        if (selectedDesignSource === 'fampnn') {
            return {
                fampnn_checkpoint: params.fampnn_checkpoint ?? params.fampnn_checkpoint_path,
                fampnn_temperature: params.fampnn_temperature,
                fampnn_num_steps: params.fampnn_num_steps,
                fampnn_psce_threshold: params.fampnn_psce_threshold,
                fampnn_constraint_mode: params.fampnn_constraint_mode,
                seqs_per_design: params.seqs_per_design,
            };
        }
        if (!selectedDesignHasPpiflowLens) return null;
        return {
            ppiflow_mode: params.ppiflow_mode ?? params.ppiflow_stage_mode ?? activeJob?.stage_mode,
            ppiflow_start_t: params.ppiflow_start_t,
            ppiflow_samples_per_target: params.ppiflow_samples_per_target,
            ppiflow_checkpoint: params.ppiflow_checkpoint ?? params.ppiflow_checkpoint_path,
            ppiflow_objective_mode: params.ppiflow_objective_mode,
            ppiflow_objective_threshold: params.ppiflow_objective_threshold,
            ppiflow_rotamer_enrichment_enabled: params.ppiflow_rotamer_enrichment_enabled,
            ppiflow_require_anchors: params.ppiflow_require_anchors,
            ppiflow_rotamer_shell_cutoff: params.ppiflow_rotamer_shell_cutoff,
            ppiflow_selected_loops: params.ppiflow_selected_loops,
            maturation_design_mode: params.maturation_design_mode,
            maturation_redesign_enabled: params.maturation_redesign_enabled,
            maturation_redesign_temp: params.maturation_redesign_temp,
            maturation_redesign_steps: params.maturation_redesign_steps,
            maturation_anchor_threshold: params.maturation_anchor_threshold,
            maturation_anchor_distance_cutoff: params.maturation_anchor_distance_cutoff,
            maturation_min_improvement: params.maturation_min_improvement,
            maturation_filter_percentile: params.maturation_filter_percentile,
        };
    }, [activeJob?.params, activeJob?.stage_mode, selectedDesignHasPpiflowLens, selectedDesignPpiflowRecord, selectedDesignProvenance, selectedDesignSource]);
    const activeJobSelectedLoops = useMemo(
        () => normalizeLoopScopeLabel(activeJob?.selected_loop_scope) || '',
        [activeJob?.selected_loop_scope]
    );
    const selectedDesignStageSettingsRows = useMemo(() => {
        const settings = selectedDesignStageSettings || {};
        const baseRows = selectedDesignSource === 'fampnn'
            ? [
                ['Checkpoint', settings.fampnn_checkpoint],
                ['Temperature', settings.fampnn_temperature],
                ['Steps', settings.fampnn_num_steps],
                ['pSCE Gate', settings.fampnn_psce_threshold],
                ['Constraint Mode', settings.fampnn_constraint_mode],
                ['Seqs/Backbone', settings.seqs_per_design],
            ]
            : [
                ['Mode', settings.ppiflow_mode],
                ['Region', normalizeLoopScopeLabel(settings.ppiflow_selected_loops) ?? selectedDesignPpiflowRecord?.selected_loop_scope ?? activeJobSelectedLoops],
                ['Start t', settings.ppiflow_start_t],
                ['Samples/Target', settings.ppiflow_samples_per_target],
                ['Checkpoint', settings.ppiflow_checkpoint],
                ['Objective', settings.ppiflow_objective_mode],
                ['Objective ≤', settings.ppiflow_objective_threshold],
                ['Rotamer Enrichment', settings.ppiflow_rotamer_enrichment_enabled],
                ['Require Anchors', settings.ppiflow_require_anchors],
                ['Rotamer Shell', settings.ppiflow_rotamer_shell_cutoff],
                ['Design Mode', settings.maturation_design_mode],
                ['Redesign', settings.maturation_redesign_enabled],
                ['Redesign Temp', settings.maturation_redesign_temp],
                ['Redesign Steps', settings.maturation_redesign_steps],
                ['Pair Cutoff', settings.maturation_anchor_distance_cutoff],
                ['ΔIface Gate', settings.maturation_min_improvement],
                ['Percentile', settings.maturation_filter_percentile],
            ];
        return baseRows
            .filter(([, value]) => value !== undefined && value !== null && value !== '')
            .map(([label, value]) => [label, Array.isArray(value) ? value.join(', ') : String(value)] as const);
    }, [activeJobSelectedLoops, selectedDesignPpiflowRecord, selectedDesignSource, selectedDesignStageSettings]);
    const selectedDesignPpiflowSummaryRows = useMemo(() => {
        if (!selectedDesignHasPpiflowLens || !selectedDesign) return [];
        return [
            ['Source Design', selectedDesign.source_design_name ?? selectedDesignPpiflowRecord?.source_design_name],
            ['Source PDB', selectedDesign.source_pdb_path ?? selectedDesignPpiflowRecord?.source_pdb_path],
            ['Movable Span', selectedDesignPpiflowRecord?.ppiflow_positions ?? selectedDesignPpiflowRecord?.movable_region_positions],
            ['Anchor Count', selectedDesignPpiflowRecord?.anchors?.anchor_count],
            ['Anchor Candidates', selectedDesignPpiflowRecord?.anchors?.anchor_candidate_count],
            ['Movable Anchors', selectedDesignPpiflowRecord?.anchors?.movable_anchor_candidate_count],
            ['Iface Residues', selectedDesignPpiflowRecord?.interface_score?.interface_residue_count ?? selectedDesignPpiflowRecord?.maturation_score?.interface_residue_count_matured],
            ['Enriched Complex', selectedDesignPpiflowRecord?.enriched_complex_pdb],
            ['ΔIface Selected', selectedDesign.maturation_selected_delta_interface ?? selectedDesignPpiflowRecord?.maturation_score?.selected_delta_interface_score],
            ['ΔIface Global', selectedDesign.maturation_delta_interface],
            ['Iface Selected', selectedDesign.maturation_selected_interface_score ?? selectedDesignPpiflowRecord?.maturation_score?.selected_interface_score_matured],
            ['Iface Global', selectedDesign.maturation_interface_score],
            ['Backbone RMSD Selected', selectedDesign.maturation_selected_rmsd ?? selectedDesignPpiflowRecord?.maturation_score?.selected_rmsd_backbone],
            ['Backbone RMSD Global', selectedDesign.maturation_rmsd ?? selectedDesignPpiflowRecord?.maturation_score?.rmsd_backbone],
            ['Backbone RMSD Rest', selectedDesign.maturation_nonselected_rmsd ?? selectedDesignPpiflowRecord?.maturation_score?.nonselected_rmsd_backbone],
            ['Primary Loop', selectedDesign.ppiflow_primary_loop],
            ['Primary Loop RMSD', selectedDesign.ppiflow_primary_loop_rmsd],
            ['Objective Mode', selectedDesign.ppiflow_objective_mode ?? selectedDesignPpiflowRecord?.maturation_score?.objective_mode],
            ['Objective Score', selectedDesign.ppiflow_objective_score ?? selectedDesignPpiflowRecord?.maturation_score?.objective_score],
            ['Primary Loop ΔTgt Cts', selectedDesign.ppiflow_primary_loop_target_contact_delta],
            ['Primary Loop ΔTgt Dist', selectedDesign.ppiflow_primary_loop_target_distance_delta],
            ['Primary Loop ΔEpi Cts', selectedDesign.ppiflow_primary_loop_epitope_contact_delta],
            ['Primary Loop ΔEpi Dist', selectedDesign.ppiflow_primary_loop_epitope_distance_delta],
            ['Seq Identity', selectedDesignPpiflowRecord?.maturation_score?.sequence_identity],
            ['CA Clashes', selectedDesignPpiflowRecord?.maturation_score?.clash_count_ca],
            ['Filter Pass', selectedDesign.ppiflow_filter_passed ?? selectedDesignPpiflowRecord?.maturation_filter?.passed],
            ['Filter Reason', selectedDesign.ppiflow_filter_reason ?? selectedDesignPpiflowRecord?.maturation_filter?.filter_reason],
        ]
            .filter(([, value]) => value !== undefined && value !== null && value !== '')
            .map(([label, value]) => {
                if (typeof value === 'boolean') return [label, value ? 'true' : 'false'] as const;
                if (typeof value === 'number') return [label, Number.isInteger(value) ? String(value) : value.toFixed(2)] as const;
                return [label, String(value)] as const;
            });
    }, [selectedDesign, selectedDesignHasPpiflowLens, selectedDesignPpiflowRecord]);
    const selectedDesignLineageRows = useMemo(() => {
        if (!selectedDesign) return [] as readonly (readonly [string, string])[];
        const sourceStageLabel = formatStageDescriptor(selectedDesign.source_stage_family ?? activeJob?.source_stage_family, selectedDesign.source_stage_mode ?? activeJob?.source_stage_mode);
        const producedStageLabel = formatStageDescriptor(selectedDesign.stage_family, selectedDesign.stage_mode);
        const stagePathLabel = formatLineagePathSummary(
            selectedDesign.stage_family,
            selectedDesign.stage_mode,
            selectedDesign.source_stage_family ?? activeJob?.source_stage_family,
            selectedDesign.source_stage_mode ?? activeJob?.source_stage_mode,
        );
        return [
            ['Stage Path', stagePathLabel],
            ['Produced By', producedStageLabel],
            ['Source Stage', sourceStageLabel],
            ['Source Stage Job', selectedDesign.source_stage_job_id ?? activeJob?.source_stage_job_id ?? null],
            ['Selection Dataset', activeJob?.selection_dataset_name ?? null],
            ['Selection Set Size', activeJob?.source_selection_count != null ? String(activeJob.source_selection_count) : null],
            ['Selection Manifest', activeJob?.source_selection_manifest_path ?? null],
            ['Lineage Root Job', selectedDesign.lineage_root_job_id ?? activeJob?.lineage_root_job_id ?? null],
            ['Immediate Parent Design', selectedDesign.parent_design_id],
            ['Origin Design', selectedDesign.origin_design_id],
            ['Origin Backbone', selectedDesign.origin_backbone_design_id],
            ['Source Design', selectedDesign.source_design_name ?? getPpiflowSourceName(selectedDesign as any)],
            ['Source PDB', selectedDesign.source_pdb_path ?? null],
        ]
            .filter(([, value]) => value !== undefined && value !== null && value !== '')
            .map(([label, value]) => [label, String(value)] as const);
    }, [activeJob?.lineage_root_job_id, activeJob?.selection_dataset_name, activeJob?.source_selection_count, activeJob?.source_selection_manifest_path, activeJob?.source_stage_family, activeJob?.source_stage_job_id, activeJob?.source_stage_mode, selectedDesign]);
    const selectedDesignPpiflowLoopRows = useMemo(() => {
        if (!selectedDesignHasPpiflowLens || !selectedDesign) return [] as Array<{
            loopId: string;
            selected: boolean;
            objectiveScore: number | null;
            deltaInterface: number | null;
            rmsd: number | null;
            targetContactDelta: number | null;
            targetDistanceDelta: number | null;
            epitopeContactDelta: number | null;
            epitopeDistanceDelta: number | null;
        }>;
        return getPpiflowLoopEntries(selectedDesign).map(({ loopId, metrics }) => ({
            loopId,
            selected: Boolean(metrics.selected),
            objectiveScore: typeof metrics.objective_score === 'number' ? metrics.objective_score : null,
            deltaInterface: typeof metrics.delta_interface_score === 'number' ? metrics.delta_interface_score : null,
            rmsd: typeof metrics.rmsd_backbone === 'number' ? metrics.rmsd_backbone : null,
            targetContactDelta: typeof metrics.target_contact_delta === 'number' ? metrics.target_contact_delta : null,
            targetDistanceDelta: typeof metrics.target_distance_delta === 'number' ? metrics.target_distance_delta : null,
            epitopeContactDelta: typeof metrics.epitope_contact_delta === 'number' ? metrics.epitope_contact_delta : null,
            epitopeDistanceDelta: typeof metrics.epitope_distance_delta === 'number' ? metrics.epitope_distance_delta : null,
        }));
    }, [selectedDesign, selectedDesignHasPpiflowLens]);

    const selectedDesignMetricCards = useMemo(() => {
        if (!selectedDesign) return [];
        if (selectedDesignHasPpiflowLens) {
            const ppiflowScore = selectedDesignPpiflowRecord?.maturation_score
                ?? selectedDesignPpiflowRecord?.partial_flow_score
                ?? selectedDesignPpiflowRecord?.maturation_filter?.score_data
                ?? null;
            const deltaIface = selectedDesign.maturation_delta_interface;
            const clashCount = typeof ppiflowScore?.clash_count_ca === 'number' ? ppiflowScore.clash_count_ca : null;
            const seqIdentity = typeof ppiflowScore?.sequence_identity === 'number' ? ppiflowScore.sequence_identity : null;
            const anchorCount = typeof selectedDesignPpiflowRecord?.anchors?.anchor_count === 'number'
                ? selectedDesignPpiflowRecord.anchors.anchor_count
                : null;
            const selectedDeltaIface = selectedDesign.maturation_selected_delta_interface ?? (typeof ppiflowScore?.selected_delta_interface_score === 'number' ? ppiflowScore.selected_delta_interface_score : null);
            const selectedIfaceScore = selectedDesign.maturation_selected_interface_score ?? (typeof ppiflowScore?.selected_interface_score_matured === 'number' ? ppiflowScore.selected_interface_score_matured : null);
            const selectedRmsd = selectedDesign.maturation_selected_rmsd ?? (typeof ppiflowScore?.selected_rmsd_backbone === 'number' ? ppiflowScore.selected_rmsd_backbone : null);
            const nonselectedRmsd = selectedDesign.maturation_nonselected_rmsd ?? (typeof ppiflowScore?.nonselected_rmsd_backbone === 'number' ? ppiflowScore.nonselected_rmsd_backbone : null);
            const sampleIndex = getPpiflowSampleIndex(selectedDesign as any);
            const loopScope = normalizeLoopScopeLabel(selectedDesignPpiflowRecord?.selected_loop_scope ?? selectedDesignProvenance?.selected_loop_scope);
            const movableSpan = selectedDesignPpiflowRecord?.ppiflow_positions ?? selectedDesignPpiflowRecord?.movable_region_positions ?? null;
            const objectiveScore = selectedDesign.ppiflow_objective_score ?? (typeof ppiflowScore?.objective_score === 'number' ? ppiflowScore.objective_score : null);
            const primaryLoop = selectedDesign.ppiflow_primary_loop ?? (typeof ppiflowScore?.primary_loop === 'string' ? ppiflowScore.primary_loop : null);
            const primaryLoopRmsd = selectedDesign.ppiflow_primary_loop_rmsd ?? (typeof ppiflowScore?.primary_loop_rmsd === 'number' ? ppiflowScore.primary_loop_rmsd : null);
            const primaryLoopTargetDelta = selectedDesign.ppiflow_primary_loop_target_contact_delta ?? (typeof ppiflowScore?.primary_loop_target_contact_delta === 'number' ? ppiflowScore.primary_loop_target_contact_delta : null);
            const primaryLoopEpitopeDelta = selectedDesign.ppiflow_primary_loop_epitope_contact_delta ?? (typeof ppiflowScore?.primary_loop_epitope_contact_delta === 'number' ? ppiflowScore.primary_loop_epitope_contact_delta : null);
            return [
                { label: 'Output', value: getOutputSourceLabel(selectedDesign), tone: 'text-cyan-300' },
                { label: 'Source Backbone', value: getPpiflowSourceName(selectedDesign as any) ?? '—', tone: 'text-slate-200' },
                { label: 'Sample', value: sampleIndex != null ? String(sampleIndex) : '—', tone: 'text-slate-200' },
                { label: 'Region', value: loopScope ?? '—', tone: 'text-slate-200' },
                { label: 'Movable Span', value: movableSpan ?? '—', tone: 'text-slate-200' },
                {
                    label: 'ΔIface Sel',
                    value: selectedDeltaIface != null ? selectedDeltaIface.toFixed(2) : '—',
                    tone: selectedDeltaIface != null ? (selectedDeltaIface < 0 ? 'text-emerald-300' : selectedDeltaIface > 0 ? 'text-rose-300' : 'text-slate-200') : 'text-slate-500',
                },
                {
                    label: 'ΔIface Glob',
                    value: deltaIface != null ? deltaIface.toFixed(2) : '—',
                    tone: deltaIface != null ? (deltaIface < 0 ? 'text-emerald-300' : deltaIface > 0 ? 'text-rose-300' : 'text-slate-200') : 'text-slate-500',
                },
                { label: 'Iface Sel', value: selectedIfaceScore != null ? selectedIfaceScore.toFixed(2) : '—', tone: 'text-fuchsia-300' },
                { label: 'Iface Glob', value: selectedDesign.maturation_interface_score != null ? selectedDesign.maturation_interface_score.toFixed(2) : '—', tone: 'text-violet-300' },
                {
                    label: 'Objective',
                    value: objectiveScore != null ? objectiveScore.toFixed(2) : '—',
                    tone: objectiveScore != null ? (objectiveScore <= 0 ? 'text-emerald-300' : 'text-rose-300') : 'text-slate-500',
                },
                { label: 'Primary Loop', value: primaryLoop ?? '—', tone: 'text-slate-200' },
                { label: 'Loop RMSD', value: primaryLoopRmsd != null ? `${primaryLoopRmsd.toFixed(2)} Å` : '—', tone: 'text-teal-300' },
                {
                    label: 'Loop ΔTgt Cts',
                    value: primaryLoopTargetDelta != null ? (primaryLoopTargetDelta > 0 ? `+${primaryLoopTargetDelta}` : String(primaryLoopTargetDelta)) : '—',
                    tone: primaryLoopTargetDelta != null ? (primaryLoopTargetDelta > 0 ? 'text-emerald-300' : primaryLoopTargetDelta < 0 ? 'text-rose-300' : 'text-slate-200') : 'text-slate-500',
                },
                {
                    label: 'Loop ΔEpi Cts',
                    value: primaryLoopEpitopeDelta != null ? (primaryLoopEpitopeDelta > 0 ? `+${primaryLoopEpitopeDelta}` : String(primaryLoopEpitopeDelta)) : '—',
                    tone: primaryLoopEpitopeDelta != null ? (primaryLoopEpitopeDelta > 0 ? 'text-emerald-300' : primaryLoopEpitopeDelta < 0 ? 'text-rose-300' : 'text-slate-200') : 'text-slate-500',
                },
                { label: 'RMSD Sel', value: selectedRmsd != null ? `${selectedRmsd.toFixed(2)} Å` : '—', tone: 'text-cyan-300' },
                { label: 'RMSD Glob', value: selectedDesign.maturation_rmsd != null ? `${selectedDesign.maturation_rmsd.toFixed(2)} Å` : '—', tone: 'text-sky-300' },
                { label: 'RMSD Rest', value: nonselectedRmsd != null ? `${nonselectedRmsd.toFixed(2)} Å` : '—', tone: 'text-amber-300' },
                { label: 'Seq Identity', value: seqIdentity != null ? seqIdentity.toFixed(2) : '—', tone: 'text-slate-200' },
                { label: 'Anchors', value: anchorCount != null ? String(anchorCount) : '—', tone: 'text-slate-200' },
                {
                    label: 'CA Clash',
                    value: clashCount != null ? String(clashCount) : '—',
                    tone: clashCount != null ? (clashCount > 0 ? 'text-rose-300' : 'text-emerald-300') : 'text-slate-500',
                },
            ];
        }
        return [
            {
                label: 'Output',
                value: getOutputSourceLabel(selectedDesign),
                tone: selectedDesignSource === 'rfantibody'
                    ? 'text-violet-300'
                    : selectedDesignSource === 'boltzgen'
                        ? 'text-amber-300'
                        : selectedDesignSource === 'fampnn'
                            ? 'text-emerald-300'
                            : 'text-cyan-300',
            },
            { label: 'Binder Type', value: (selectedDesign.antibody_type ?? antibodyData?.antibody_type)?.toUpperCase() || '—', tone: 'text-slate-200' },
            ...(selectedDesignSource === 'fampnn'
                ? [
                    {
                        label: 'Avg PSCE',
                        value: formatMetric(selectedDesign.fampnn_psce, 2),
                        tone: getMetricColor('fampnn_psce', selectedDesign.fampnn_psce),
                    },
                    {
                        label: 'Worst Residue PSCE',
                        value: formatMetric(selectedDesignFampnnMaxResiduePsce, 2),
                        tone: getMetricColor('fampnn_max_residue_psce', selectedDesignFampnnMaxResiduePsce),
                    },
                ]
                : [{
                    label: selectedDesignSource === 'rfantibody' ? 'RF pLDDT Global' : 'pLDDT',
                    value: formatMetric(selectedDesign.plddt_overall, 1),
                    tone: getMetricColor('plddt_overall', selectedDesign.plddt_overall),
                }]),
            ...(selectedDesignSource === 'rfantibody'
                ? [{
                    label: 'RF pLDDT Selected',
                    value: selectedDesign.rfa_plddt_selected != null ? selectedDesign.rfa_plddt_selected.toFixed(1) : '—',
                    tone: getMetricColor('plddt_overall', selectedDesign.rfa_plddt_selected ?? null),
                } as const]
                : []),
            { label: 'iPTM', value: formatMetric(selectedDesign.iptm, 2), tone: getMetricColor('ptm', selectedDesign.iptm ?? null) },
            {
                label: selectedDesignSource === 'rfantibody' ? rfMetricLabels.epitope : 'Epitope Contacts',
                value: selectedDesignSource === 'rfantibody'
                    ? (getRfHeadlineMetricValue(selectedDesign, rfMetricScope, 'epitope_contact_count') ?? '—')
                    : (selectedDesign.epitope_contact_count ?? '—'),
                tone: 'text-slate-200',
            },
            {
                label: selectedDesignSource === 'rfantibody' ? `${rfMetricLabels.short} Epitope Dist` : 'Epitope Dist',
                value: (() => {
                    const metricValue = selectedDesignSource === 'rfantibody'
                        ? getRfHeadlineMetricValue(selectedDesign, rfMetricScope, 'epitope_min_distance')
                        : selectedDesign.epitope_min_distance;
                    return metricValue != null ? `${metricValue.toFixed(2)} Å` : '—';
                })(),
                tone: 'text-slate-200',
            },
            {
                label: selectedDesignSource === 'rfantibody' ? rfMetricLabels.distance : 'Any-Target Dist',
                value: (() => {
                    const metricValue = selectedDesignSource === 'rfantibody'
                        ? getRfHeadlineMetricValue(selectedDesign, rfMetricScope, 'target_min_distance')
                        : selectedDesign.target_min_distance;
                    return metricValue != null ? `${metricValue.toFixed(2)} Å` : '—';
                })(),
                tone: 'text-slate-200',
            },
            {
                label: selectedDesignSource === 'rfantibody' ? `${rfMetricLabels.short} Epi Atom Dist` : 'Epitope Atom Dist',
                value: (() => {
                    const metricValue = selectedDesignSource === 'rfantibody'
                        ? getRfHeadlineMetricValue(selectedDesign, rfMetricScope, 'epitope_min_atom_distance')
                        : selectedDesign.epitope_min_atom_distance;
                    return metricValue != null ? `${metricValue.toFixed(2)} Å` : '—';
                })(),
                tone: 'text-slate-200',
            },
            {
                label: selectedDesignSource === 'rfantibody' ? `${rfMetricLabels.short} Target Atom Dist` : 'Target Atom Dist',
                value: (() => {
                    const metricValue = selectedDesignSource === 'rfantibody'
                        ? getRfHeadlineMetricValue(selectedDesign, rfMetricScope, 'target_min_atom_distance')
                        : selectedDesign.target_min_atom_distance;
                    return metricValue != null ? `${metricValue.toFixed(2)} Å` : '—';
                })(),
                tone: 'text-slate-200',
            },
            { label: 'Val RMSD All', value: selectedDesign.rmsd_overall != null ? `${selectedDesign.rmsd_overall.toFixed(2)} Å` : '—', tone: 'text-slate-200' },
            { label: 'Val RMSD Bd', value: selectedDesign.rmsd_binder != null ? `${selectedDesign.rmsd_binder.toFixed(2)} Å` : '—', tone: 'text-slate-200' },
            { label: 'Humanness', value: antibodyData?.humanness_score != null ? `${(antibodyData.humanness_score * 100).toFixed(0)}%` : '—', tone: antibodyData?.humanness_score != null ? ((antibodyData.humanness_score > 0.8) ? 'text-emerald-300' : (antibodyData.humanness_score > 0.6 ? 'text-amber-300' : 'text-red-300')) : 'text-slate-500' },
            { label: 'High Frust %', value: selectedDesign.frustration_pct_high != null ? `${selectedDesign.frustration_pct_high.toFixed(1)}%` : '—', tone: 'text-amber-300' },
            { label: 'High Frust Count', value: selectedDesign.frustration_high_count ?? '—', tone: 'text-amber-300' },
            { label: 'Maturation ΔIface', value: (selectedDesign.maturation_selected_delta_interface ?? selectedDesign.maturation_delta_interface) != null ? (selectedDesign.maturation_selected_delta_interface ?? selectedDesign.maturation_delta_interface)!.toFixed(2) : '—', tone: 'text-fuchsia-300' },
        ];
    }, [antibodyData?.antibody_type, antibodyData?.humanness_score, getMetricColor, rfMetricLabels.distance, rfMetricLabels.epitope, rfMetricLabels.short, rfMetricScope, selectedDesign, selectedDesignFampnnMaxResiduePsce, selectedDesignHasPpiflowLens, selectedDesignPpiflowRecord, selectedDesignProvenance?.selected_loop_scope, selectedDesignSource]);
    const overviewAnalysisItems = useMemo(() => ([
        {
            key: 'structure_summary',
            label: 'Structure Summary',
            scope: 'selected output',
            status: structureAnalysisRun?.status ?? 'missing',
            busy: structureAnalysisBusy,
            error: structureAnalysisRun?.error_message,
            summary: structureAnalysis
                ? `${structureAnalysis.residue_count} residues • ${structureAnalysis.chain_ids?.length ?? 0} chains`
                : null,
            run: () => runStructureAnalysis.mutateAsync(),
        },
        {
            key: 'antibody_annotation_pack',
            label: 'ANARCII / Annotation Pack',
            scope: 'selected output',
            status: antibodyAnalysisRun?.status ?? 'missing',
            busy: antibodyAnalysisBusy,
            error: antibodyAnalysisRun?.error_message,
            summary: antibodyData
                ? `${Object.values(antibodyData.cdr_lengths || {}).filter((value) => typeof value === 'number').length} annotated loops`
                : null,
            run: () => runAntibodyAnalysis.mutateAsync(),
        },
        {
            key: 'chain_metrics',
            label: 'Chain Metrics',
            scope: 'selected output',
            status: chainMetricsAnalysisRun?.status ?? 'missing',
            busy: chainMetricsAnalysisBusy,
            error: chainMetricsAnalysisRun?.error_message,
            summary: chainMetricsAnalysis
                ? `${Object.keys(chainMetricsAnalysis).length} chains cached`
                : null,
            run: () => runChainMetricsAnalysis.mutateAsync(),
        },
        {
            key: 'ipsae_interface',
            label: 'ipSAE Interface',
            scope: 'selected output',
            status: ipsaeAnalysisRun?.status ?? 'missing',
            busy: ipsaeAnalysisBusy,
            error: ipsaeAnalysisRun?.error_message,
            summary: ipsaeAnalysis
                ? `${formatMetric(ipsaeAnalysis.ipsae, 2)} ${ipsaeAnalysis.ipsae_chain_pair ? `• ${ipsaeAnalysis.ipsae_chain_pair}` : ''}`
                : null,
            run: () => runIpsaeAnalysis.mutateAsync(),
        },
        {
            key: 'pae_matrix',
            label: 'PAE Matrix',
            scope: 'selected output',
            status: paeMatrixAnalysisRun?.status ?? 'missing',
            busy: paeMatrixAnalysisBusy,
            error: paeMatrixAnalysisRun?.error_message,
            summary: paeMatrixAnalysis
                ? `${paeMatrixAnalysis.size} × ${paeMatrixAnalysis.size} matrix`
                : null,
            run: () => runPaeMatrixAnalysis.mutateAsync(),
        },
        {
            key: 'contact_map',
            label: 'Contact Map',
            scope: 'selected output',
            status: contactMapAnalysisRun?.status ?? 'missing',
            busy: contactMapAnalysisBusy,
            error: contactMapAnalysisRun?.error_message,
            summary: contactMapAnalysis
                ? `${contactMapAnalysis.size} × ${contactMapAnalysis.size} matrix`
                : null,
            run: () => runContactMapAnalysis.mutateAsync(),
        },
    ]), [
        antibodyAnalysisBusy,
        antibodyAnalysisRun?.error_message,
        antibodyAnalysisRun?.status,
        antibodyData,
        chainMetricsAnalysis,
        chainMetricsAnalysisBusy,
        chainMetricsAnalysisRun?.error_message,
        chainMetricsAnalysisRun?.status,
        ipsaeAnalysis,
        ipsaeAnalysisBusy,
        ipsaeAnalysisRun?.error_message,
        ipsaeAnalysisRun?.status,
        contactMapAnalysis,
        contactMapAnalysisBusy,
        contactMapAnalysisRun?.error_message,
        contactMapAnalysisRun?.status,
        paeMatrixAnalysis,
        paeMatrixAnalysisBusy,
        paeMatrixAnalysisRun?.error_message,
        paeMatrixAnalysisRun?.status,
        runAntibodyAnalysis,
        runChainMetricsAnalysis,
        runContactMapAnalysis,
        runPaeMatrixAnalysis,
        runStructureAnalysis,
        structureAnalysis,
        structureAnalysisBusy,
        structureAnalysisRun?.error_message,
        structureAnalysisRun?.status,
    ]);
    const overviewAnalysisCounts = useMemo(() => {
        if (!selectedDesignId) {
            return { cached: 0, running: 0, missing: 0, attention: 0 };
        }
        return overviewAnalysisItems.reduce((acc, item) => {
            if (item.status === 'completed') acc.cached += 1;
            else if (item.status === 'running' || item.status === 'queued') acc.running += 1;
            else if (item.status === 'failed' || item.status === 'stale' || item.status === 'cancelled') acc.attention += 1;
            else acc.missing += 1;
            return acc;
        }, { cached: 0, running: 0, missing: 0, attention: 0 });
    }, [overviewAnalysisItems, selectedDesignId]);
    const runMissingOverviewAnalyses = useCallback(async () => {
        if (!selectedDesignId) return;
        const pendingItems = overviewAnalysisItems.filter((item) => (
            item.status === 'missing'
            || item.status === 'failed'
            || item.status === 'stale'
            || item.status === 'cancelled'
        ));
        if (!pendingItems.length) return;
        await Promise.allSettled(pendingItems.map((item) => item.run()));
    }, [overviewAnalysisItems, selectedDesignId]);
    useEffect(() => {
        if (reviewSelectionRequired) return;
        if (activeTab !== 'antibody') return;
        if (!selectedDesignId && antibodyTabDesigns.length > 0) {
            setSelectedDesignId(antibodyTabDesigns[0].id);
            return;
        }
        if (selectedDesignId && antibodyTabDesigns.length > 0 && !antibodyTabDesigns.some((d) => d.id === selectedDesignId)) {
            setSelectedDesignId(antibodyTabDesigns[0].id);
        }
    }, [activeTab, antibodyTabDesigns, reviewSelectionRequired, selectedDesignId]);
    const antibodyLoopRows = useMemo<AntibodyLoopRow[]>(() => {
        if (!selectedDesign) return [];
        const rows: AntibodyLoopRow[] = [
            { chain: 'H', region: 'H1', sequence: selectedDesign.cdr_h1 ?? antibodyData?.cdrs?.H1 ?? null, length: selectedDesign.cdr_h1_length ?? antibodyData?.cdr_lengths?.H1 ?? antibodyData?.cdrs?.H1?.length ?? null },
            { chain: 'H', region: 'H2', sequence: selectedDesign.cdr_h2 ?? antibodyData?.cdrs?.H2 ?? null, length: selectedDesign.cdr_h2_length ?? antibodyData?.cdr_lengths?.H2 ?? antibodyData?.cdrs?.H2?.length ?? null },
            { chain: 'H', region: 'H3', sequence: selectedDesign.cdr_h3 ?? antibodyData?.cdrs?.H3 ?? null, length: selectedDesign.cdr_h3_length ?? antibodyData?.cdr_lengths?.H3 ?? antibodyData?.cdrs?.H3?.length ?? null },
            { chain: 'L', region: 'L1', sequence: selectedDesign.cdr_l1 ?? antibodyData?.cdrs?.L1 ?? null, length: selectedDesign.cdr_l1_length ?? antibodyData?.cdr_lengths?.L1 ?? antibodyData?.cdrs?.L1?.length ?? null },
            { chain: 'L', region: 'L2', sequence: selectedDesign.cdr_l2 ?? antibodyData?.cdrs?.L2 ?? null, length: selectedDesign.cdr_l2_length ?? antibodyData?.cdr_lengths?.L2 ?? antibodyData?.cdrs?.L2?.length ?? null },
            { chain: 'L', region: 'L3', sequence: selectedDesign.cdr_l3 ?? antibodyData?.cdrs?.L3 ?? null, length: selectedDesign.cdr_l3_length ?? antibodyData?.cdr_lengths?.L3 ?? antibodyData?.cdrs?.L3?.length ?? null },
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
        const iptms = designs.map(d => d.iptm).filter((v): v is number => v != null);
        const ipsaes = designs.map(d => d.ipsae).filter((v): v is number => v != null);
        const affinities = designs.map(d => d.affinity_score).filter((v): v is number => v != null);
        const binderProbs = designs.map(d => d.binder_probability).filter((v): v is number => v != null);
        const epitopeContacts = designs.map(d => d.epitope_contact_count).filter((v): v is number => v != null);
        const targetContacts = designs.map(d => d.target_contact_count).filter((v): v is number => v != null);
        const epitopeDistances = designs.map(d => d.epitope_min_distance).filter((v): v is number => v != null);
        const targetDistances = designs.map(d => d.target_min_distance).filter((v): v is number => v != null);
        const hotspotCoverage = designs.map(d => d.rfa_hotspot_covered_count).filter((v): v is number => v != null);
        const psces = designs.map(d => d.fampnn_psce).filter((v): v is number => v != null);
        const maxResiduePsces = designs.map(d => getFampnnMaxResiduePsce(d)).filter((v): v is number => v != null);
        const ppiflowDeltas = designs.map(d => d.maturation_selected_delta_interface ?? d.maturation_delta_interface).filter((v): v is number => v != null);
        const ppiflowInterfaceScores = designs.map(d => d.maturation_selected_interface_score ?? d.maturation_interface_score).filter((v): v is number => v != null);
        const ppiflowRmsd = designs.map(d => d.maturation_selected_rmsd ?? d.maturation_rmsd).filter((v): v is number => v != null);
        const ppiflowSeqIdentity = designs
            .map(d => {
                const score = getPpiflowScoreRecord(d as any);
                return typeof score?.sequence_identity === 'number' ? score.sequence_identity : null;
            })
            .filter((v): v is number => v != null);
        const ppiflowAnchorCounts = designs
            .map(d => {
                const anchors = getPpiflowAnchorRecord(d as any);
                return typeof anchors?.anchor_count === 'number' ? anchors.anchor_count : null;
            })
            .filter((v): v is number => v != null);
        const ppiflowClashCounts = designs
            .map(d => {
                const score = getPpiflowScoreRecord(d as any);
                return typeof score?.clash_count_ca === 'number' ? score.clash_count_ca : null;
            })
            .filter((v): v is number => v != null);
        const ppiflowDesigns = designs.filter((d) => inferDesignOutputSource(d as any) === 'ppiflow');
        const ppiflowUniqueSources = new Set(ppiflowDesigns.map((d) => getPpiflowSourceKey(d as any)).filter(Boolean));
        const frustrationHigh = designs.map(d => d.frustration_high_count).filter((v): v is number => v != null);
        const frustrationPct = designs.map(d => d.frustration_pct_high).filter((v): v is number => v != null);
        const screenPassed = designs.filter((d) => d.passed_screen === true).length;
        const screenFailed = designs.filter((d) => d.passed_screen === false).length;
        const screeningReasons = new Map<string, number>();
        const bindingMetricUsage = summarizeBindingMetricUsage(designs);

        const tierCounts = { A: 0, B: 0, C: 0, D: 0, none: 0 };
        designs.forEach(d => {
            const tier = getBindingTier(d);
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
            avgIptm: iptms.length ? iptms.reduce((a, b) => a + b, 0) / iptms.length : null,
            avgIpsae: ipsaes.length ? ipsaes.reduce((a, b) => a + b, 0) / ipsaes.length : null,
            avgAffinity: affinities.length ? affinities.reduce((a, b) => a + b, 0) / affinities.length : null,
            avgBinderProb: binderProbs.length ? binderProbs.reduce((a, b) => a + b, 0) / binderProbs.length : null,
            avgEpitopeContacts: epitopeContacts.length ? epitopeContacts.reduce((a, b) => a + b, 0) / epitopeContacts.length : null,
            avgTargetContacts: targetContacts.length ? targetContacts.reduce((a, b) => a + b, 0) / targetContacts.length : null,
            avgEpitopeDistance: epitopeDistances.length ? epitopeDistances.reduce((a, b) => a + b, 0) / epitopeDistances.length : null,
            avgTargetDistance: targetDistances.length ? targetDistances.reduce((a, b) => a + b, 0) / targetDistances.length : null,
            avgHotspotCoverage: hotspotCoverage.length ? hotspotCoverage.reduce((a, b) => a + b, 0) / hotspotCoverage.length : null,
            avgPsce: psces.length ? psces.reduce((a, b) => a + b, 0) / psces.length : null,
            avgMaxResiduePsce: maxResiduePsces.length ? maxResiduePsces.reduce((a, b) => a + b, 0) / maxResiduePsces.length : null,
            avgPpiflowDeltaInterface: ppiflowDeltas.length ? ppiflowDeltas.reduce((a, b) => a + b, 0) / ppiflowDeltas.length : null,
            avgPpiflowInterfaceScore: ppiflowInterfaceScores.length ? ppiflowInterfaceScores.reduce((a, b) => a + b, 0) / ppiflowInterfaceScores.length : null,
            avgPpiflowRmsd: ppiflowRmsd.length ? ppiflowRmsd.reduce((a, b) => a + b, 0) / ppiflowRmsd.length : null,
            avgPpiflowSeqIdentity: ppiflowSeqIdentity.length ? ppiflowSeqIdentity.reduce((a, b) => a + b, 0) / ppiflowSeqIdentity.length : null,
            avgPpiflowAnchors: ppiflowAnchorCounts.length ? ppiflowAnchorCounts.reduce((a, b) => a + b, 0) / ppiflowAnchorCounts.length : null,
            ppiflowUniqueSources: ppiflowUniqueSources.size,
            ppiflowImproved: ppiflowDeltas.filter((value) => value < 0).length,
            ppiflowStable: ppiflowDeltas.filter((value) => value >= 0 && value <= 25).length,
            ppiflowDegraded: ppiflowDeltas.filter((value) => value > 25).length,
            ppiflowZeroClash: ppiflowClashCounts.filter((value) => value === 0).length,
            ppiflowClashy: ppiflowClashCounts.filter((value) => value > 0).length,
            ppiflowLowDrift: ppiflowRmsd.filter((value) => value <= 1.0).length,
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
            bindingMetricLabel: bindingMetricUsage.label,
            bindingMetricThresholds: bindingMetricUsage.detail,
            bindingMetricDetail: bindingMetricUsage.thresholds,
            psceExcellent: psces.filter((value) => value <= 0.9).length,
            psceGood: psces.filter((value) => value > 0.9 && value <= 1.2).length,
            psceModerate: psces.filter((value) => value > 1.2 && value <= 1.6).length,
            psceReview: psces.filter((value) => value > 1.6).length,
            worstPsceClean: maxResiduePsces.filter((value) => value <= 1.6).length,
            worstPsceWatch: maxResiduePsces.filter((value) => value > 1.6 && value <= 2.4).length,
            worstPsceOutlier: maxResiduePsces.filter((value) => value > 2.4 && value <= 3.0).length,
            worstPsceSevere: maxResiduePsces.filter((value) => value > 3.0).length,
            screenPassed,
            screenFailed,
            topScreeningReasons: Array.from(screeningReasons.entries())
                .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
                .slice(0, 4),
        };
    }, [designs, totalDesigns]);
    const rfReviewFallbackStats = useMemo(() => {
        if (!isPostRFantibodyReview || reviewSelectionRequired || reviewBackboneRows.length === 0) return null;

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
            avgIptm: null,
            avgIpsae: null,
            avgAffinity: null,
            avgBinderProb: null,
            avgEpitopeContacts: average(representatives.map((representative) => representative.epitope_contact_count)),
            avgTargetContacts: average(representatives.map((representative) => representative.target_contact_count)),
            avgEpitopeDistance: average(representatives.map((representative) => representative.epitope_min_distance)),
            avgTargetDistance: average(representatives.map((representative) => representative.target_min_distance)),
            avgHotspotCoverage: average(representatives.map((representative) => representative.rfa_hotspot_covered_count)),
            avgPsce: null,
            avgMaxResiduePsce: null,
            avgPpiflowDeltaInterface: null,
            avgPpiflowInterfaceScore: null,
            avgPpiflowRmsd: null,
            avgPpiflowSeqIdentity: null,
            avgPpiflowAnchors: null,
            ppiflowUniqueSources: 0,
            ppiflowImproved: 0,
            ppiflowStable: 0,
            ppiflowDegraded: 0,
            ppiflowZeroClash: 0,
            ppiflowClashy: 0,
            ppiflowLowDrift: 0,
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
            bindingMetricLabel: 'binding quality',
            bindingMetricThresholds: 'Tier A-D thresholds are unavailable for this review-only aggregate.',
            bindingMetricDetail: 'Open a full design set to recover the normal binding metric distribution summary.',
            psceExcellent: 0,
            psceGood: 0,
            psceModerate: 0,
            psceReview: 0,
            worstPsceClean: 0,
            worstPsceWatch: 0,
            worstPsceOutlier: 0,
            worstPsceSevere: 0,
            screenPassed,
            screenFailed,
            topScreeningReasons: [],
            representativeFallback: true,
        };
    }, [isPostRFantibodyReview, reviewBackboneRows, reviewBackboneTotal, reviewSelectionRequired, rfFilteredCount, rfReviewSet]);
    const overviewStats = reviewSelectionRequired ? null : (stats ?? rfReviewFallbackStats);
    const usingReviewRepresentativeFallback = !stats && !!rfReviewFallbackStats;

    const handleSort = (field: string) => {
        const nextSortDir = sortField === field
            ? (sortDir === 'asc' ? 'desc' : 'asc')
            : getDefaultSortDirection(field);
        setCurrentPage(1);
        if (sortField === field) {
            setSortDir(nextSortDir);
        } else {
            setSortField(field);
            setSortDir(nextSortDir);
        }
        setFilterDraft((current) => ({
            ...current,
            sortField: field,
            sortDir: nextSortDir,
        }));
    };

    const updateFilterDraft = <K extends keyof FilterDraftState>(key: K, value: FilterDraftState[K]) => {
        setFilterDraft((current) => ({ ...current, [key]: value }));
    };

    const applyDraftFilters = () => {
        setSortField(filterDraft.sortField);
        setSortDir(filterDraft.sortDir);
        setPlddtMin(filterDraft.plddtMin);
        setIptmMin(filterDraft.iptmMin);
        setIpsaeMin(filterDraft.ipsaeMin);
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
        filterDraft.ipsaeMin !== ipsaeMin ||
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
        ipsae_min: ipsaeMin,
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
        ipsaeMin,
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
            || nextState.output_source_filter === 'boltzgen'
            || nextState.output_source_filter === 'fampnn'
            || nextState.output_source_filter === 'ppiflow'
            || nextState.output_source_filter === 'validation'
            || nextState.output_source_filter === 'all')
            ? nextState.output_source_filter
            : 'all';
        const nextBackboneId = typeof nextState.selected_backbone_id === 'number' && Number.isFinite(nextState.selected_backbone_id)
            ? nextState.selected_backbone_id
            : null;

        setRfReviewSet(nextRfReviewSet);
        manualOutputSourceSelectionRef.current = true;
        outputSourceSelectionJobRef.current = selectedJobId || null;
        setOutputSourceFilter(nextOutputSourceFilter);
        setAntibodySourceFilter(nextOutputSourceFilter);
        setSortField(nextSortField);
        setSortDir(nextSortDir);
        setFilterText(typeof nextState.filter_text === 'string' ? nextState.filter_text : '');
        setSelectedBackboneId(nextBackboneId);
        setSelectedDesignId('');
        setPlddtMin(typeof nextState.plddt_min === 'number' ? nextState.plddt_min : 0);
        setIptmMin(typeof nextState.iptm_min === 'number' ? nextState.iptm_min : 0);
        setIpsaeMin(typeof nextState.ipsae_min === 'number' ? nextState.ipsae_min : 0);
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
            ipsaeMin: typeof nextState.ipsae_min === 'number' ? nextState.ipsae_min : 0,
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

    const clearReviewSourceSelection = () => {
        setAppliedSavedFilterSetId(null);
        setRfReviewSet(null);
        manualOutputSourceSelectionRef.current = true;
        outputSourceSelectionJobRef.current = selectedJobId || null;
        setOutputSourceFilter('all');
        setAntibodySourceFilter('all');
        setFilterText('');
        setSelectedBackboneId(null);
        setSelectedDesignId('');
        setSelectedDesignIds([]);
        setPlddtMin(0);
        setIptmMin(0);
        setIpsaeMin(0);
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
            ipsaeMin: 0,
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
        setIterationMessage({ kind: 'success', text: 'Cleared the active review source.' });
    };

    const clearRfaFilters = () => {
        setFilterText('');
        setPlddtMin(0);
        setIptmMin(0);
        setIpsaeMin(0);
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
            ipsaeMin: 0,
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

    const selectLiveReviewSet = (nextRfReviewSet: RfReviewSet) => {
        setAppliedSavedFilterSetId(null);
        setRfReviewSet(nextRfReviewSet);
        manualOutputSourceSelectionRef.current = true;
        outputSourceSelectionJobRef.current = selectedJobId || null;
        setOutputSourceFilter('all');
        setAntibodySourceFilter('all');
        setFilterText('');
        setSelectedBackboneId(null);
        setSelectedDesignId('');
        setSelectedDesignIds([]);
        setCurrentPage(1);
        clearRfaFilters();
        setIterationMessage({
            kind: 'success',
            text: nextRfReviewSet === 'raw'
                ? `Loaded the live raw RF review set (${rfRawCount.toLocaleString()} outputs).`
                : `Loaded the live screened RF review set (${rfFilteredCount.toLocaleString()} outputs).`,
        });
    };

    const handleReviewSourceSelectionChange = (value: ReviewSourceSelectorValue) => {
        if (!isPostRFantibodyReview) return;
        setIterationMessage(null);
        if (!value) {
            clearReviewSourceSelection();
            return;
        }
        if (value === 'live:raw' || value === 'live:filtered') {
            selectLiveReviewSet(value === 'live:raw' ? 'raw' : 'filtered');
            return;
        }
        if (value.startsWith('saved:')) {
            const filterSetId = value.slice('saved:'.length);
            const filterSet = savedReviewFilterSets.find((entry) => entry.id === filterSetId);
            if (filterSet) {
                applySavedReviewFilterSet(filterSet);
            }
        }
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
            if (reviewSelectionRequired) {
                throw new Error('Select a live RF review set or saved dataset before building a working set.');
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
                ? `Added ${designIds.length.toLocaleString()} filtered outputs to the antibody refinement set.`
                : `Added the top ${designIds.length.toLocaleString()} filtered outputs to the antibody refinement set.`;
            setIterationMessage({ kind: 'success', text: message });
        },
        onError: (error) => {
            setIterationMessage({ kind: 'error', text: getErrorMessage(error) });
        },
    });

    const clusterSelectionMutation = useMutation({
        mutationFn: async (mode: BoltzgenClusterMode) => {
            if (!selectedJobId) {
                throw new Error('Select a job before clustering a BoltzGen cohort.');
            }
            if (reviewSelectionRequired) {
                throw new Error('Select a live RF review set or saved dataset before clustering outputs.');
            }
            const response = await fetchDesigns(bulkSelectionFilters);
            if (response.data.total > MAX_BULK_SELECTION_DESIGNS) {
                throw new Error(`Filtered result set exceeds ${MAX_BULK_SELECTION_DESIGNS.toLocaleString()} outputs. Narrow the filters first.`);
            }
            const boltzgenDesigns = response.data.designs.filter((design) => inferDesignOutputSource(design as any) === 'boltzgen');
            if (boltzgenDesigns.length === 0) {
                throw new Error('No BoltzGen outputs matched the current filtered set.');
            }
            const summary = buildBoltzgenClusters(boltzgenDesigns, mode);
            return {
                mode,
                representativeIds: summary.clusters.map((cluster) => cluster.representative.id),
                totalCount: summary.totalCount,
                uniqueCount: summary.uniqueCount,
                duplicateCount: summary.duplicateCount,
                largestClusterSize: summary.largestClusterSize,
            };
        },
        onSuccess: (result) => {
            setSelectedDesignIds((current) => Array.from(new Set([...current, ...result.representativeIds])));
            const clusterLabel = result.mode === 'exact_sequence'
                ? 'exact-sequence'
                : result.mode === 'cdr_h3_exact'
                    ? 'CDR-H3'
                    : result.mode === 'identity_95'
                        ? '95% identity'
                        : '90% identity';
            setIterationMessage({
                kind: 'success',
                text: `Added ${result.uniqueCount.toLocaleString()} BoltzGen representative outputs from ${result.totalCount.toLocaleString()} filtered designs using ${clusterLabel} clustering.`,
            });
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
            if (reviewSelectionRequired) {
                throw new Error('Select a live RF review set or saved dataset before saving a dataset.');
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
            const nextFilterSets = coerceSavedReviewFilterSets(response.data.filter_sets);
            setSavedReviewFilterSetsOverride(nextFilterSets);
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
            setSavedReviewFilterSetsOverride(coerceSavedReviewFilterSets(response.data.filter_sets));
            if (appliedSavedFilterSetId === filterSetId) {
                clearReviewSourceSelection();
            } else {
                setIterationMessage({ kind: 'success', text: response.data.message });
            }
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

    const exportFasta = (mode: 'binder' | 'cdr') => {
        if (!selectedJobId) return;
        const url = `/api/designs/export/fasta?job_id=${encodeURIComponent(selectedJobId)}&mode=${mode}`;
        const a = document.createElement('a');
        a.href = url;
        a.download = '';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    };

    const openPipelineReorchestration = (savedFilterSet?: SavedReviewFilterSet | null) => {
        if (!activeJob) {
            setIterationMessage({ kind: 'error', text: 'Select a job before opening Antibody Refinement.' });
            return;
        }
        if (reviewSelectionRequired && !savedFilterSet) {
            setIterationMessage({ kind: 'error', text: 'Select a live RF review set or saved dataset before opening Antibody Refinement.' });
            return;
        }

        const launchDesignIds = savedFilterSet ? [] : selectedDesignIds;
        const resolvedSavedFilterSet = savedFilterSet ?? (launchDesignIds.length === 0 ? loadedSavedReviewFilterSet : null);
        if (launchDesignIds.length === 0 && !resolvedSavedFilterSet) {
            setIterationMessage({
                kind: 'error',
                text: 'Select at least one design or load a saved dataset before opening Antibody Refinement.',
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
        const launchDesignIdSet = launchDesignIds.length > 0 ? new Set(launchDesignIds) : null;
        const selectedLaunchDesigns = launchDesignIdSet
            ? designs.filter((design) => launchDesignIdSet.has(design.id))
            : [];
        const selectedLaunchSourceFilters = Array.from(new Set(
            selectedLaunchDesigns.map((design) => inferDesignOutputSource(design as any))
        ));
        const launchSourceOutputFilter = resolvedSavedFilterSet
            ? savedSourceOutputFilter
            : selectedLaunchSourceFilters.length === 1
                ? selectedLaunchSourceFilters[0]
                : outputSourceFilter;
        const refinementLaunchState: AntibodyRefinementLaunchState = {
            refinementMode: true,
            sourceJobId: activeJob.id,
            sourceArtifactGroup: resolvedSavedFilterSet ? savedSourceArtifactGroup : activeRfArtifactGroup,
            sourceOutputSourceFilter: launchSourceOutputFilter,
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
        };

        saveAntibodyRefinementLaunchState(refinementLaunchState);

        navigate('/submit?template=antibody_denovo&refinement=1', {
            state: {
                refinementMode: true,
                sourceJobId: activeJob.id,
                sourceArtifactGroup: refinementLaunchState.sourceArtifactGroup,
                sourceOutputSourceFilter: refinementLaunchState.sourceOutputSourceFilter,
                sourceSortField: refinementLaunchState.sourceSortField,
                sourceSortDir: refinementLaunchState.sourceSortDir,
                sourceVisibleCount: refinementLaunchState.sourceVisibleCount,
                sourceTotalCount: refinementLaunchState.sourceTotalCount,
                sourceSavedFilterSetId: refinementLaunchState.sourceSavedFilterSetId,
                sourceSavedFilterSetName: refinementLaunchState.sourceSavedFilterSetName,
                sourceSavedFilterSetCreatedAt: refinementLaunchState.sourceSavedFilterSetCreatedAt,
                sourceSavedFilterSetDesignCount: refinementLaunchState.sourceSavedFilterSetDesignCount,
                reviewFilterSetId: refinementLaunchState.reviewFilterSetId,
                reviewFilterSetName: refinementLaunchState.reviewFilterSetName,
                reviewFilterSetCreatedAt: refinementLaunchState.reviewFilterSetCreatedAt,
                reviewFilterSetDesignCount: refinementLaunchState.reviewFilterSetDesignCount,
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

    const continueProteinLocalReviewMutation = useMutation({
        mutationFn: async () => {
            if (!selectedJobId) {
                throw new Error('Select a paused Protein Local Redesign job before continuing.');
            }
            const designIds = selectedDesignIds.length > 0
                ? selectedDesignIds
                : (loadedSavedReviewFilterSet?.design_ids ?? []);
            if (!designIds.length) {
                throw new Error('Select at least one design or load a saved dataset before continuing.');
            }
            return continueProteinLocalReview(selectedJobId, designIds);
        },
        onSuccess: (response) => {
            setIterationMessage({
                kind: 'success',
                text: `${response.data.message} New job: ${response.data.new_job_name} (${response.data.new_job_id}).`,
            });
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

    const launchBusy = launchIterationMutation.isPending || launchManualMutagenesisMutation.isPending || continueProteinLocalReviewMutation.isPending;
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
                        {activeJob && (
                            <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-slate-300">
                                {formatLineagePathSummary(
                                    activeJob.stage_family,
                                    activeJob.stage_mode,
                                    activeJob.source_stage_family,
                                    activeJob.source_stage_mode,
                                ) && (
                                    <span className="rounded-lg border border-slate-700 bg-slate-900/70 px-2 py-1">
                                        Path {formatLineagePathSummary(
                                            activeJob.stage_family,
                                            activeJob.stage_mode,
                                            activeJob.source_stage_family,
                                            activeJob.source_stage_mode,
                                        )}
                                    </span>
                                )}
                                {formatSourceSummary(activeJob) && (
                                    <span className="rounded-lg border border-slate-700 bg-slate-900/70 px-2 py-1">
                                        Source {formatSourceSummary(activeJob)}
                                    </span>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Smart Job Selector */}
                    <div className="flex items-center gap-3">
                        <div className="relative" ref={jobSelectorRef}>
                            <button
                                type="button"
                                onClick={() => setShowJobSelectorMenu((current) => !current)}
                                className="flex min-w-[420px] items-center justify-between gap-3 rounded-xl border border-slate-600 bg-slate-800/80 px-4 py-3 text-left shadow-lg transition-all hover:border-slate-500"
                            >
                                <div className="min-w-0">
                                    <div className="truncate text-sm font-medium text-white">
                                        {activeJob ? activeJob.name : 'Select a job...'}
                                    </div>
                                    <div className="truncate text-[11px] text-slate-400">
                                        {activeJob
                                            ? `${activeJob.model_id || activeJob.mode} • ${activeBadgeLabel}`
                                            : 'Search workflows or downstream stage groups'}
                                    </div>
                                </div>
                                <svg
                                    className={`h-5 w-5 shrink-0 text-slate-400 transition-transform ${showJobSelectorMenu ? 'rotate-180' : ''}`}
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                >
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                                </svg>
                            </button>

                            {showJobSelectorMenu && (
                                <div className="absolute right-0 z-50 mt-2 w-[520px] overflow-hidden rounded-2xl border border-slate-700 bg-slate-900/98 shadow-2xl shadow-slate-950/70 backdrop-blur">
                                    <div className="border-b border-slate-800 p-3">
                                        <input
                                            autoFocus
                                            type="text"
                                            value={jobSelectorSearch}
                                            onChange={(event) => setJobSelectorSearch(event.target.value)}
                                            placeholder="Search jobs, lineages, or stage families..."
                                            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none placeholder:text-slate-500 focus:border-blue-500"
                                        />
                                    </div>
                                    <div className="max-h-[70vh] overflow-y-auto p-2">
                                        {groupedJobSelectorOptions.length === 0 ? (
                                            <div className="rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-6 text-center text-sm text-slate-400">
                                                No jobs match this search.
                                            </div>
                                        ) : (
                                            <div className="space-y-3">
                                                {groupedJobSelectorOptions.map((group) => (
                                                    <div key={group.key}>
                                                        <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                                                            {group.label}
                                                        </div>
                                                        <div className="space-y-1">
                                                            {group.options.map((option) => {
                                                                const isSelected = option.value === selectedJobId || option.groupKey === selectedLineageGroupKey;
                                                                return (
                                                                    <button
                                                                        key={option.key}
                                                                        type="button"
                                                                        onClick={() => handleSelectJob(option.value)}
                                                                        className={`w-full rounded-xl border px-3 py-2 text-left transition-colors ${isSelected
                                                                            ? 'border-blue-500/60 bg-blue-500/12 text-white'
                                                                            : 'border-slate-800 bg-slate-950/50 text-slate-200 hover:border-slate-700 hover:bg-slate-900'
                                                                            }`}
                                                                    >
                                                                        <div className="truncate text-sm font-medium">
                                                                            {option.title}
                                                                        </div>
                                                                        <div className="truncate text-[11px] text-slate-400">
                                                                            {option.detail}
                                                                        </div>
                                                                    </button>
                                                                );
                                                            })}
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}
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
                        {activeLineageRootJob && (
                            <div className={`mb-4 rounded-xl border p-4 ${isPostRFantibodyReview ? 'border-emerald-500/20 bg-emerald-500/5' : 'border-sky-500/20 bg-sky-500/5'}`}>
                                <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
                                    <div>
                                        <div className={`text-sm font-semibold ${isPostRFantibodyReview ? 'text-emerald-200' : 'text-sky-200'}`}>
                                            {isPostRFantibodyReview
                                                ? 'Paused after RFantibody backbone generation'
                                                : `Viewing persisted downstream outputs from ${activeLineageRootJob.name}`}
                                        </div>
                                        <div className="text-xs text-slate-300">
                                            {isPostRFantibodyReview ? (
                                                <>
                                                    Review this stage by backbone family first. The UI is using existing <span className="font-mono text-emerald-300">backbone_id</span> as the first family primitive.
                                                </>
                                            ) : (
                                                <>
                                                    This lineage already has persisted downstream child outputs. Switch between them directly here without losing access to the paused RF review parent.
                                                </>
                                            )}
                                            {(!isPostRFantibodyReview && formatSourceSummary(activeJob)) && (
                                                <span className="ml-2 text-sky-200">Launch source: {formatSourceSummary(activeJob)}</span>
                                            )}
                                        </div>
                                    </div>
                                    <div className="flex flex-wrap gap-2 text-[11px] text-slate-300">
                                        {isPostRFantibodyReview && gateRawBackboneSummary?.total != null && (
                                            <span className="rounded-lg border border-slate-700 bg-slate-900/70 px-2 py-1">
                                                Raw {gateRawBackboneSummary.total}
                                            </span>
                                        )}
                                        {isPostRFantibodyReview && (gateFilteredBackboneSummary?.total != null || gateCandidateBackboneSummary?.total != null) && (
                                            <span className="rounded-lg border border-slate-700 bg-slate-900/70 px-2 py-1">
                                                Screened {gateFilteredBackboneSummary?.total ?? gateCandidateBackboneSummary?.total ?? 0}
                                            </span>
                                        )}
                                        {isPostRFantibodyReview && (
                                            <span className="rounded-lg border border-slate-700 bg-slate-900/70 px-2 py-1">
                                                Backbone families {availableReviewBackboneFamilyCount}
                                            </span>
                                        )}
                                        {activeLineageOutputJobs.length > 0 && (
                                            <>
                                                <span className="rounded-lg border border-slate-700 bg-slate-900/70 px-2 py-1">
                                                    Stage groups {activeLineageOutputGroups.length}
                                                </span>
                                                <span className="rounded-lg border border-slate-700 bg-slate-900/70 px-2 py-1">
                                                    Child shards {activeLineageOutputJobs.length}
                                                </span>
                                                <span className="rounded-lg border border-slate-700 bg-slate-900/70 px-2 py-1">
                                                    Persisted outputs {activeLineageOutputDesignCount.toLocaleString()}
                                                </span>
                                            </>
                                        )}
                                    </div>
                                </div>
                                {activeLineageOutputJobs.length > 0 && (
                                    <div className="mt-4 border-t border-slate-800/70 pt-4">
                                        <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
                                            Downstream lineage outputs
                                        </div>
                                        <div className="grid gap-3 lg:grid-cols-2">
                                            {activeLineageOutputGroups.map((group) => (
                                                <div
                                                    key={group.key}
                                                    className="rounded-xl border border-slate-800/80 bg-slate-950/50 p-3"
                                                >
                                                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                                                        <div className="min-w-0">
                                                            <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-300">
                                                                {group.label}
                                                            </div>
                                                            <div className="mt-1 text-[11px] text-slate-400">
                                                                {group.designCount.toLocaleString()} outputs
                                                            </div>
                                                            {group.jobs.length > 1 && (
                                                                <div className="mt-1 text-[10px] text-slate-500">
                                                                    Produced across {group.jobs.length} execution shard{group.jobs.length === 1 ? '' : 's'}
                                                                </div>
                                                            )}
                                                            {group.jobs.some((job) => getLineageOutputScopeLabel(job)) && (
                                                                <div className="mt-1 text-[10px] text-cyan-300">
                                                                    Loop scopes: {Array.from(new Set(group.jobs.map((job) => getLineageOutputScopeLabel(job)).filter(Boolean) as string[])).join(' • ')}
                                                                </div>
                                                            )}
                                                        </div>
                                                        <div className="flex flex-wrap gap-2">
                                                            <button
                                                                type="button"
                                                                onClick={() => handleSelectLineageGroup(group.family)}
                                                                className={`rounded-lg border px-3 py-2 text-xs transition-colors ${((activeLineageRootJob?.id === selectedJobId && outputSourceFilter === ((group.family === 'rfantibody' || group.family === 'boltzgen' || group.family === 'fampnn' || group.family === 'ppiflow' || group.family === 'validation') ? group.family : 'all')) || selectedLineageGroupKey === `${group.jobs[0].parent_job_id}:${group.family}`)
                                                                    ? 'border-sky-400/60 bg-sky-500/15 text-white'
                                                                    : 'border-slate-700 bg-slate-900/70 text-slate-200 hover:border-slate-600'
                                                                    }`}
                                                            >
                                                                Open {group.label}
                                                            </button>
                                                            {group.jobs.length > 1 && (
                                                                <button
                                                                    type="button"
                                                                    onClick={() => toggleExpandedLineageGroup(group.key)}
                                                                    className="rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-slate-600"
                                                                >
                                                                    {expandedLineageGroups.has(group.key) ? 'Hide Child Jobs' : `Show Child Jobs (${group.jobs.length})`}
                                                                </button>
                                                            )}
                                                        </div>
                                                    </div>
                                                    {expandedLineageGroups.has(group.key) && (
                                                        <div className="mt-3 flex flex-wrap gap-2 border-t border-slate-800/70 pt-3">
                                                            {group.jobs.map((job) => {
                                                                const isSelectedLineageJob = selectedJobId === job.id;
                                                                const scopeLabel = getLineageOutputScopeLabel(job);
                                                                return (
                                                                    <button
                                                                        key={job.id}
                                                                        type="button"
                                                                        onClick={() => handleSelectJob(job.id)}
                                                                        className={`min-w-[220px] rounded-lg border px-3 py-2 text-left transition-colors ${isSelectedLineageJob
                                                                            ? 'border-sky-400/60 bg-sky-500/15 text-white'
                                                                            : 'border-slate-700 bg-slate-900/70 text-slate-200 hover:border-slate-600'
                                                                            }`}
                                                                    >
                                                                        <div className="text-xs font-semibold">
                                                                            {getLineageOutputLabel(job)} • {job.design_count.toLocaleString()} output{job.design_count === 1 ? '' : 's'}
                                                                        </div>
                                                                        <div className="mt-1 text-[11px] text-slate-500">
                                                                            Child job detail
                                                                        </div>
                                                                        {scopeLabel && (
                                                                            <div className="mt-1 text-[10px] text-cyan-300">
                                                                                Loops: {scopeLabel}
                                                                            </div>
                                                                        )}
                                                                    </button>
                                                                );
                                                            })}
                                                        </div>
                                                    )}
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}
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

                        {showReviewWorkingSetPanel && (
                            <div className="mb-4 rounded-xl border border-indigo-500/25 bg-indigo-500/5 p-4">
                                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                        <div>
                                            <div className="text-sm font-medium text-indigo-100">
                                                {isProteinLocalRedesignReviewContext ? 'Review Working Set' : 'Antibody Refinement'}
                                            </div>
                                            <p className="mt-1 text-xs text-slate-400">
                                                {isProteinLocalRedesignReviewContext
                                                    ? 'Filter the paused review table, promote the outputs you want to keep into a working set, then continue the paused Protein Local Redesign workflow from that subset.'
                                                    : 'Sort and filter the current output set, then promote visible, filtered, or top-ranked outputs into a working set for the antibody refinement workflow.'}
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
                                                {reviewSelectionRequired && isPostRFantibodyReview
                                                    ? 'No RF review source selected'
                                                    : `${tableDesigns.length} visible in ${loadedSavedReviewFilterSet ? `'${loadedSavedReviewFilterSet.name}'` : activeReviewSetLabel}`}
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
                                                    <span className={`rounded-full border px-2 py-1 ${activeReviewSourceSelection === 'live:raw' ? 'border-blue-500/30 bg-blue-500/10 text-blue-200' : 'border-slate-700 bg-slate-900/70 text-slate-400'}`}>
                                                        Raw {rfRawCount.toLocaleString()}
                                                    </span>
                                                    <span className={`rounded-full border px-2 py-1 ${activeReviewSourceSelection === 'live:filtered' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200' : 'border-slate-700 bg-slate-900/70 text-slate-400'}`}>
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
                                                    {continueProteinLocalReviewMutation.isPending
                                                        ? 'Continuing protein local redesign...'
                                                        : `Launching ${launchIterationMutation.isPending
                                                            ? launchIterationMutation.variables?.action
                                                            : 'manual_mutagenesis'}...`}
                                                </span>
                                            )}
                                            {bulkSelectionMutation.isPending && (
                                                <span className="rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-1 text-sky-200">
                                                    Building working set...
                                                </span>
                                            )}
                                            {clusterSelectionMutation.isPending && (
                                                <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-amber-200">
                                                    Building BoltzGen representative set...
                                                </span>
                                            )}
                                        </div>
                                        {showBoltzgenClusterPanel && (
                                            <div className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 p-3">
                                                <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
                                                    <div>
                                                        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-200">
                                                            BoltzGen Cohort Triage
                                                        </div>
                                                        <p className="mt-1 max-w-3xl text-[11px] text-slate-400">
                                                            Reduce near-duplicate BoltzGen binders before refinement. The stats below reflect the currently loaded BoltzGen rows; the clustering action re-queries the full filtered BoltzGen cohort for the current job/output-source scope.
                                                        </p>
                                                        <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-6">
                                                            <div className="rounded-lg border border-slate-700/70 bg-slate-900/70 px-3 py-2">
                                                                <div className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Loaded BoltzGen</div>
                                                                <div className="mt-1 text-sm font-semibold text-white">{boltzgenClusterSummary.totalCount.toLocaleString()}</div>
                                                            </div>
                                                            <div className="rounded-lg border border-slate-700/70 bg-slate-900/70 px-3 py-2">
                                                                <div className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Unique Sequences</div>
                                                                <div className="mt-1 text-sm font-semibold text-white">{boltzgenClusterSummary.uniqueSequenceCount.toLocaleString()}</div>
                                                            </div>
                                                            <div className="rounded-lg border border-slate-700/70 bg-slate-900/70 px-3 py-2">
                                                                <div className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Unique CDR-H3</div>
                                                                <div className="mt-1 text-sm font-semibold text-white">{boltzgenClusterSummary.uniqueCdrH3Count.toLocaleString()}</div>
                                                            </div>
                                                            <div className="rounded-lg border border-slate-700/70 bg-slate-900/70 px-3 py-2">
                                                                <div className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Largest Cluster</div>
                                                                <div className="mt-1 text-sm font-semibold text-white">{boltzgenClusterSummary.largestClusterSize.toLocaleString()}</div>
                                                            </div>
                                                            <div className="rounded-lg border border-slate-700/70 bg-slate-900/70 px-3 py-2">
                                                                <div className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Median Conf / Affinity</div>
                                                                <div className="mt-1 text-sm font-semibold text-white">
                                                                    {boltzgenClusterSummary.medianConfidence != null ? boltzgenClusterSummary.medianConfidence.toFixed(2) : '—'}
                                                                    <span className="mx-1 text-slate-500">/</span>
                                                                    {boltzgenClusterSummary.medianAffinity != null ? boltzgenClusterSummary.medianAffinity.toFixed(2) : '—'}
                                                                </div>
                                                            </div>
                                                            <div className="rounded-lg border border-slate-700/70 bg-slate-900/70 px-3 py-2">
                                                                <div className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Median Prob / Size</div>
                                                                <div className="mt-1 text-sm font-semibold text-white">
                                                                    {boltzgenClusterSummary.medianBinderProbability != null ? boltzgenClusterSummary.medianBinderProbability.toFixed(2) : '—'}
                                                                    <span className="mx-1 text-slate-500">/</span>
                                                                    {boltzgenClusterSummary.medianBinderLength != null ? boltzgenClusterSummary.medianBinderLength.toFixed(0) : '—'}
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </div>
                                                    <div className="min-w-[280px] rounded-xl border border-slate-700/70 bg-slate-950/40 p-3">
                                                        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-300">
                                                            Representative Selection
                                                        </div>
                                                        <div className="mt-2 flex flex-wrap gap-2">
                                                            {([
                                                                ['exact_sequence', 'Exact Sequence'],
                                                                ['identity_95', '95% Identity'],
                                                                ['identity_90', '90% Identity'],
                                                                ['cdr_h3_exact', 'CDR-H3 Exact'],
                                                            ] as Array<[BoltzgenClusterMode, string]>).map(([value, label]) => (
                                                                <button
                                                                    key={value}
                                                                    type="button"
                                                                    onClick={() => setBoltzgenClusterMode(value)}
                                                                    className={`rounded-lg border px-3 py-1.5 text-[11px] transition-colors ${boltzgenClusterMode === value
                                                                        ? 'border-amber-400/60 bg-amber-500/15 text-amber-100'
                                                                        : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:border-slate-600'
                                                                        }`}
                                                                >
                                                                    {label}
                                                                </button>
                                                            ))}
                                                        </div>
                                                        <div className="mt-3 text-[11px] text-slate-400">
                                                            Current mode yields <span className="font-semibold text-amber-100">{boltzgenClusterSummary.uniqueCount.toLocaleString()}</span> representatives and removes <span className="font-semibold text-amber-100">{boltzgenClusterSummary.duplicateCount.toLocaleString()}</span> duplicates from the loaded cohort.
                                                        </div>
                                                        <button
                                                            type="button"
                                                            onClick={() => {
                                                                setIterationMessage(null);
                                                                clusterSelectionMutation.mutate(boltzgenClusterMode);
                                                            }}
                                                            disabled={clusterSelectionMutation.isPending || boltzgenClusterSummary.totalCount === 0}
                                                            className="mt-3 w-full rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-semibold text-amber-100 transition-colors hover:border-amber-400 hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-50"
                                                        >
                                                            {clusterSelectionMutation.isPending ? 'Selecting Representatives…' : 'Add Cluster Representatives'}
                                                        </button>
                                                    </div>
                                                </div>
                                            </div>
                                        )}
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
                                                disabled={!selectedJobId || saveFilterSetMutation.isPending || reviewSelectionRequired}
                                                className="rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-3 py-2 text-xs text-indigo-100 transition-colors hover:border-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
                                            >
                                                {saveFilterSetMutation.isPending ? 'Saving…' : 'Save Dataset'}
                                            </button>
                                            {isPostRFantibodyReview && (
                                                <>
                                                    <select
                                                        value={activeReviewSourceSelection}
                                                        onChange={(event) => handleReviewSourceSelectionChange(event.target.value as ReviewSourceSelectorValue)}
                                                        className="min-w-[220px] rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-xs text-slate-100 outline-none disabled:cursor-not-allowed disabled:opacity-50"
                                                    >
                                                        <option value="">Select review source...</option>
                                                        <option value="live:filtered">Live screened ({rfFilteredCount.toLocaleString()})</option>
                                                        <option value="live:raw">Live raw ({rfRawCount.toLocaleString()})</option>
                                                        {savedReviewFilterSets.length > 0 && (
                                                            <optgroup label="Saved datasets">
                                                                {savedReviewFilterSets.map((filterSet) => (
                                                                    <option key={filterSet.id} value={`saved:${filterSet.id}`}>
                                                                        {filterSet.name} ({filterSet.design_ids?.length ?? filterSet.visible_count ?? 0})
                                                                    </option>
                                                                ))}
                                                            </optgroup>
                                                        )}
                                                    </select>
                                                    {!!activeReviewSourceSelection && (
                                                        <button
                                                            type="button"
                                                            onClick={clearReviewSourceSelection}
                                                            className="rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-2 text-xs text-slate-200 transition-colors hover:border-slate-600"
                                                        >
                                                            Clear Selection
                                                        </button>
                                                    )}
                                                </>
                                            )}
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
                                                                    {isAntibodyContext && (
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => openPipelineReorchestration(filterSet)}
                                                                        className="rounded border border-indigo-500/40 bg-indigo-500/10 px-2 py-1 text-[10px] text-indigo-100"
                                                                    >
                                                                        Refine Dataset
                                                                    </button>
                                                                    )}
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
                                        {isAntibodyContext ? (
                                            <button
                                                type="button"
                                                onClick={() => openPipelineReorchestration()}
                                                disabled={!canLaunchWorkingSet}
                                                className="flex items-center gap-1.5 rounded-lg border border-indigo-500/60 bg-indigo-500/20 px-4 py-2 text-xs font-semibold text-indigo-100 transition-colors hover:border-indigo-400 hover:bg-indigo-500/30 disabled:cursor-not-allowed disabled:opacity-50 shadow-sm shadow-indigo-900/20"
                                                title={selectedDesignIds.length > 0
                                                    ? 'Open the antibody refinement workflow using the highlighted outputs as the locked input set.'
                                                    : loadedSavedReviewFilterSet
                                                        ? `Open antibody refinement from the loaded saved dataset '${loadedSavedReviewFilterSet.name}'.`
                                                        : 'Load a saved dataset or select outputs before opening antibody refinement.'}
                                            >
                                                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                                </svg>
                                                Open Antibody Refinement
                                            </button>
                                        ) : (
                                            <button
                                                type="button"
                                                onClick={() => continueProteinLocalReviewMutation.mutate()}
                                                disabled={!canLaunchWorkingSet || continueProteinLocalReviewMutation.isPending}
                                                className="flex items-center gap-1.5 rounded-lg border border-emerald-500/60 bg-emerald-500/20 px-4 py-2 text-xs font-semibold text-emerald-100 transition-colors hover:border-emerald-400 hover:bg-emerald-500/30 disabled:cursor-not-allowed disabled:opacity-50 shadow-sm shadow-emerald-900/20"
                                                title={selectedDesignIds.length > 0
                                                    ? 'Continue the paused Protein Local Redesign workflow from the highlighted outputs.'
                                                    : loadedSavedReviewFilterSet
                                                        ? `Continue from the loaded saved dataset '${loadedSavedReviewFilterSet.name}'.`
                                                        : 'Load a saved dataset or select outputs before continuing.'}
                                            >
                                                Continue Workflow
                                            </button>
                                        )}
                                        {!workflowOnlyRefinement && isAntibodyContext && (
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
                                                    ['ppiflow_backbone_refine', 'PPIFlow Backbone'],
                                                    ['ppiflow_maturation', 'PPIFlow Maturation'],
                                                    ['fampnn_redesign', 'FAMPNN'],
                                                    ['frustrampnn', 'FrustraMPNN'],
                                                ] as Array<[AntibodyIterationAction, string]>).map(([action, label]) => (
                                                    <button
                                                        key={action}
                                                        type="button"
                                                        onClick={() => {
                                                            setIterationMessage(null);
                                                            let paramOverrides: Record<string, unknown> | undefined = undefined;

                                                            if (showParamOverrides) {
                                                                paramOverrides = {
                                                                    ...(pipelineOverrides.run_structure_validation && {
                                                                        run_structure_validation: true,
                                                                        structure_validator: pipelineOverrides.structure_validator,
                                                                        interactive_gate_stage: 'post_structure_validation'
                                                                    }),
                                                                    ...(pipelineOverrides.run_ppiflow && {
                                                                        run_ppiflow_maturation: true,
                                                                        run_maturation: true,
                                                                        ppiflow_stage_mode: 'post_fampnn',
                                                                        run_post_validation_maturation: false,
                                                                        run_post_boltz_maturation: false,
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
                                                    let paramOverrides: Record<string, unknown> | undefined = undefined;

                                                    if (showParamOverrides) {
                                                        paramOverrides = {
                                                            ...(pipelineOverrides.run_structure_validation && {
                                                                run_structure_validation: true,
                                                                structure_validator: pipelineOverrides.structure_validator,
                                                                interactive_gate_stage: 'post_structure_validation'
                                                            }),
                                                            ...(pipelineOverrides.run_ppiflow && {
                                                                run_ppiflow_maturation: true,
                                                                run_maturation: true,
                                                                ppiflow_stage_mode: 'post_fampnn',
                                                                run_post_validation_maturation: false,
                                                                run_post_boltz_maturation: false,
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
                                            <div className="flex flex-wrap items-center gap-3">
                                                <span className="text-xs text-slate-400">Review source</span>
                                                <select
                                                    value={activeReviewSourceSelection}
                                                    onChange={(event) => handleReviewSourceSelectionChange(event.target.value as ReviewSourceSelectorValue)}
                                                    className="min-w-[280px] rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-xs text-slate-100 outline-none"
                                                >
                                                    <option value="">Select review source...</option>
                                                    <option value="live:filtered">Live screened ({rfFilteredCount.toLocaleString()})</option>
                                                    <option value="live:raw">Live raw ({rfRawCount.toLocaleString()})</option>
                                                    {savedReviewFilterSets.length > 0 && (
                                                        <optgroup label="Saved datasets">
                                                            {savedReviewFilterSets.map((filterSet) => (
                                                                <option key={filterSet.id} value={`saved:${filterSet.id}`}>
                                                                    {filterSet.name} ({filterSet.design_ids?.length ?? filterSet.visible_count ?? 0})
                                                                </option>
                                                            ))}
                                                        </optgroup>
                                                    )}
                                                </select>
                                                {!!activeReviewSourceSelection && (
                                                    <button
                                                        type="button"
                                                        onClick={clearReviewSourceSelection}
                                                        className="rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-xs text-slate-200 transition-colors hover:border-slate-600"
                                                    >
                                                        Clear
                                                    </button>
                                                )}
                                                <span className="text-xs text-slate-500">
                                                    {reviewSelectionRequired
                                                        ? 'Select a live review set or saved dataset to render the cached RF review data.'
                                                        : loadedSavedReviewFilterSet
                                                            ? `Viewing '${loadedSavedReviewFilterSet.name}' on ${rfReviewSet === 'filtered' ? 'screened' : 'raw'} RF backbones.`
                                                            : rfReviewSet === 'filtered'
                                                                ? 'Viewing the live screened RF backbones.'
                                                                : 'Viewing the live raw RF backbones.'}
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

                                    {reviewSelectionRequired ? (
                                        <div className="flex min-h-[360px] items-center justify-center px-6 py-12">
                                            <div className="max-w-xl rounded-xl border border-slate-700/60 bg-slate-900/60 p-6 text-center">
                                                <div className="text-sm font-semibold text-slate-100">Select a review source to load data</div>
                                                <p className="mt-2 text-sm leading-6 text-slate-400">
                                                    Nothing is rendered by default for paused RF review jobs. Choose a live raw set, live screened set, or saved dataset from the selector above, and the previously analyzed outputs will be reloaded for that exact selection.
                                                </p>
                                            </div>
                                        </div>
                                    ) : (
                                        <>
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
                                                ) : preferredAnalysisLens === 'ppiflow' ? (
                                                    <>
                                                        <StatCard label="Total Outputs" value={overviewStats.total.toLocaleString()} />
                                                        <StatCard label="Source Backbones" value={overviewStats.ppiflowUniqueSources ?? 0} color="text-cyan-300" />
                                                        <StatCard label="Avg ΔIface" value={formatMetric(overviewStats.avgPpiflowDeltaInterface, 2)} color="text-fuchsia-300" />
                                                        <StatCard label="Improved" value={overviewStats.ppiflowImproved ?? 0} subtitle="ΔIface < 0" color="text-emerald-300" />
                                                        <StatCard label="Avg RMSD" value={overviewStats.avgPpiflowRmsd != null ? `${overviewStats.avgPpiflowRmsd.toFixed(2)} Å` : '—'} color="text-cyan-300" />
                                                        <StatCard label="Avg Seq ID" value={formatMetric(overviewStats.avgPpiflowSeqIdentity, 2)} color="text-slate-200" />
                                                        <StatCard label="Zero CA Clash" value={overviewStats.ppiflowZeroClash ?? 0} color="text-emerald-300" />
                                                        <StatCard label="Avg Anchors" value={formatMetric(overviewStats.avgPpiflowAnchors, 1)} color="text-amber-300" />
                                                    </>
                                                ) : (
                                                    <>
                                                        <StatCard label="Total Designs" value={overviewStats.total.toLocaleString()} />
                                                        <StatCard label="Favorites" value={overviewStats.favorites} color="text-yellow-400" />
                                                        <StatCard label="Avg pLDDT" value={formatMetric(overviewStats.avgPlddt, 1)} color="text-blue-400" />
                                                        {(preferredAnalysisLens === 'validation' || preferredAnalysisLens === 'protenix') ? (
                                                            <>
                                                                <StatCard label="Avg iPTM" value={formatMetric(overviewStats.avgIptm, 2)} color="text-fuchsia-300" />
                                                                <StatCard label="Avg ipSAE" value={formatMetric(overviewStats.avgIpsae, 3)} color="text-cyan-300" />
                                                                <StatCard label="Avg pTM" value={formatMetric(overviewStats.avgPtm, 2)} color="text-violet-400" />
                                                                <StatCard label="Avg PAE" value={formatMetric(overviewStats.avgPae, 1)} color="text-amber-300" />
                                                                <StatCard label="Avg Contacts" value={formatMetric(overviewStats.avgEpitopeContacts, 1)} color="text-lime-400" />
                                                                <StatCard label="High Contacts" value={overviewStats.highContacts} subtitle="≥5 epitope" color="text-lime-400" />
                                                            </>
                                                        ) : (
                                                            <>
                                                                <StatCard label="Avg pSCE" value={formatMetric(overviewStats.avgPsce, 2)} subtitle="FAMPNN" color="text-cyan-400" />
                                                                <StatCard label="Avg Affinity" value={formatMetric(overviewStats.avgAffinity, 2)} color="text-emerald-400" />
                                                                <StatCard label="Avg Binder %" value={overviewStats.avgBinderProb ? (overviewStats.avgBinderProb * 100).toFixed(0) + '%' : '—'} color="text-emerald-400" />
                                                                <StatCard label="Avg pTM" value={formatMetric(overviewStats.avgPtm, 2)} color="text-violet-400" />
                                                                <StatCard label="Avg Contacts" value={formatMetric(overviewStats.avgEpitopeContacts, 1)} color="text-lime-400" />
                                                                <StatCard label="High Contacts" value={overviewStats.highContacts} subtitle="≥5 epitope" color="text-lime-400" />
                                                            </>
                                                        )}
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
                                                ) : preferredAnalysisLens === 'fampnn' ? (
                                                    <>
                                                        <h3 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                                                            <span>🧬</span>
                                                            FA-MPNN pSCE Distribution
                                                            <span className="text-xs text-slate-500 font-normal">(average vs worst residue)</span>
                                                        </h3>
                                                        <div className="space-y-4">
                                                            <div>
                                                                <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Average pSCE</div>
                                                                <div className="grid grid-cols-4 gap-3">
                                                                    <div className="bg-emerald-500/20 rounded-lg p-3 text-center border border-emerald-500/30">
                                                                        <div className="text-2xl font-bold text-emerald-300">{overviewStats.psceExcellent ?? 0}</div>
                                                                        <div className="text-xs text-emerald-400 font-medium">Excellent</div>
                                                                        <div className="text-[10px] text-slate-500">≤ 0.90</div>
                                                                    </div>
                                                                    <div className="bg-cyan-500/20 rounded-lg p-3 text-center border border-cyan-500/30">
                                                                        <div className="text-2xl font-bold text-cyan-300">{overviewStats.psceGood ?? 0}</div>
                                                                        <div className="text-xs text-cyan-400 font-medium">Good</div>
                                                                        <div className="text-[10px] text-slate-500">0.90 - 1.20</div>
                                                                    </div>
                                                                    <div className="bg-amber-500/20 rounded-lg p-3 text-center border border-amber-500/30">
                                                                        <div className="text-2xl font-bold text-amber-300">{overviewStats.psceModerate ?? 0}</div>
                                                                        <div className="text-xs text-amber-400 font-medium">Moderate</div>
                                                                        <div className="text-[10px] text-slate-500">1.20 - 1.60</div>
                                                                    </div>
                                                                    <div className="bg-rose-500/20 rounded-lg p-3 text-center border border-rose-500/30">
                                                                        <div className="text-2xl font-bold text-rose-300">{overviewStats.psceReview ?? 0}</div>
                                                                        <div className="text-xs text-rose-400 font-medium">Review</div>
                                                                        <div className="text-[10px] text-slate-500">&gt; 1.60</div>
                                                                    </div>
                                                                </div>
                                                            </div>
                                                            <div>
                                                                <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Worst Residue pSCE</div>
                                                                <div className="grid grid-cols-4 gap-3">
                                                                    <div className="bg-emerald-500/20 rounded-lg p-3 text-center border border-emerald-500/30">
                                                                        <div className="text-2xl font-bold text-emerald-300">{overviewStats.worstPsceClean ?? 0}</div>
                                                                        <div className="text-xs text-emerald-400 font-medium">Clean</div>
                                                                        <div className="text-[10px] text-slate-500">≤ 1.60</div>
                                                                    </div>
                                                                    <div className="bg-cyan-500/20 rounded-lg p-3 text-center border border-cyan-500/30">
                                                                        <div className="text-2xl font-bold text-cyan-300">{overviewStats.worstPsceWatch ?? 0}</div>
                                                                        <div className="text-xs text-cyan-400 font-medium">Watch</div>
                                                                        <div className="text-[10px] text-slate-500">1.60 - 2.40</div>
                                                                    </div>
                                                                    <div className="bg-amber-500/20 rounded-lg p-3 text-center border border-amber-500/30">
                                                                        <div className="text-2xl font-bold text-amber-300">{overviewStats.worstPsceOutlier ?? 0}</div>
                                                                        <div className="text-xs text-amber-400 font-medium">Outlier</div>
                                                                        <div className="text-[10px] text-slate-500">2.40 - 3.00</div>
                                                                    </div>
                                                                    <div className="bg-rose-500/20 rounded-lg p-3 text-center border border-rose-500/30">
                                                                        <div className="text-2xl font-bold text-rose-300">{overviewStats.worstPsceSevere ?? 0}</div>
                                                                        <div className="text-xs text-rose-400 font-medium">Severe</div>
                                                                        <div className="text-[10px] text-slate-500">&gt; 3.00</div>
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        </div>
                                                        <div className="mt-3 text-xs text-slate-500 flex items-center gap-3">
                                                            <span>Average pSCE captures overall sidechain confidence; worst-residue pSCE surfaces local outliers. Lower is better for both.</span>
                                                        </div>
                                                    </>
                                                ) : preferredAnalysisLens === 'ppiflow' ? (
                                                    <>
                                                        <h3 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                                                            <span>🌀</span>
                                                            PPIFlow Refinement Summary
                                                            <span className="text-xs text-slate-500 font-normal">(refined outputs in the current set)</span>
                                                        </h3>
                                                        <div className="grid grid-cols-4 gap-3">
                                                            <div className="bg-emerald-500/20 rounded-lg p-3 text-center border border-emerald-500/30">
                                                                <div className="text-2xl font-bold text-emerald-300">{overviewStats.ppiflowImproved ?? 0}</div>
                                                                <div className="text-xs text-emerald-400 font-medium">Improved</div>
                                                                <div className="text-[10px] text-slate-500">ΔIface &lt; 0</div>
                                                            </div>
                                                            <div className="bg-blue-500/20 rounded-lg p-3 text-center border border-blue-500/30">
                                                                <div className="text-2xl font-bold text-blue-300">{overviewStats.ppiflowLowDrift ?? 0}</div>
                                                                <div className="text-xs text-blue-400 font-medium">Low Drift</div>
                                                                <div className="text-[10px] text-slate-500">RMSD ≤ 1.0 Å</div>
                                                            </div>
                                                            <div className="bg-cyan-500/20 rounded-lg p-3 text-center border border-cyan-500/30">
                                                                <div className="text-2xl font-bold text-cyan-300">{overviewStats.ppiflowZeroClash ?? 0}</div>
                                                                <div className="text-xs text-cyan-400 font-medium">Zero CA Clash</div>
                                                                <div className="text-[10px] text-slate-500">clean local geometry</div>
                                                            </div>
                                                            <div className="bg-rose-500/20 rounded-lg p-3 text-center border border-rose-500/30">
                                                                <div className="text-2xl font-bold text-rose-300">{overviewStats.ppiflowDegraded ?? 0}</div>
                                                                <div className="text-xs text-rose-400 font-medium">Strong Loss</div>
                                                                <div className="text-[10px] text-slate-500">ΔIface &gt; 25</div>
                                                            </div>
                                                        </div>
                                                        <div className="mt-3 text-xs text-slate-500 flex items-center gap-3">
                                                            <span>Lower ΔIface is better. Use RMSD and CA clash to separate local refinement from bad backbone drift.</span>
                                                        </div>
                                                    </>
                                                ) : (
                                                    <>
                                                        <h3 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                                                            <span>🔗</span>
                                                            Binding Quality Distribution
                                                            <span className="text-xs text-slate-500 font-normal">(based on {overviewStats.bindingMetricLabel})</span>
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
                                                            <span>{overviewStats.bindingMetricThresholds}</span>
                                                            <span className="text-amber-500/80">{overviewStats.bindingMetricDetail}</span>
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
                                                        {isPostRFantibodyReview
                                                            ? 'Top Backbones by Target Engagement'
                                                            : preferredAnalysisLens === 'fampnn'
                                                                ? 'Top Designs by pSCE'
                                                                : preferredAnalysisLens === 'ppiflow'
                                                                    ? 'Top Refined Backbones by ΔIface'
                                                                    : 'Top Designs by pLDDT'}
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
                                                                .sort((a, b) => (
                                                                    preferredAnalysisLens === 'fampnn'
                                                                        ? (a.fampnn_psce ?? Number.POSITIVE_INFINITY) - (b.fampnn_psce ?? Number.POSITIVE_INFINITY)
                                                                        : preferredAnalysisLens === 'ppiflow'
                                                                            ? ((a.maturation_delta_interface ?? Number.POSITIVE_INFINITY) - (b.maturation_delta_interface ?? Number.POSITIVE_INFINITY))
                                                                        : (b.plddt_overall ?? 0) - (a.plddt_overall ?? 0)
                                                                ))
                                                                .slice(0, 5)
                                                                .map((d) => (
                                                                    <div key={d.id} className="flex justify-between items-center py-2 px-3 bg-slate-900/50 rounded-lg">
                                                                        <span className="text-sm truncate flex-1">
                                                                            {preferredAnalysisLens === 'ppiflow'
                                                                                ? `${getPpiflowSourceName(d as any) ?? d.name}${getPpiflowSampleIndex(d as any) != null ? ` • sample ${getPpiflowSampleIndex(d as any)}` : ''}`
                                                                                : d.name}
                                                                        </span>
                                                                        <span className={`text-sm font-mono ${preferredAnalysisLens === 'fampnn'
                                                                            ? getMetricColor('fampnn_psce', d.fampnn_psce)
                                                                            : preferredAnalysisLens === 'ppiflow'
                                                                                ? ((d.maturation_delta_interface ?? 0) < 0 ? 'text-emerald-300' : (d.maturation_delta_interface ?? 0) > 0 ? 'text-rose-300' : 'text-slate-300')
                                                                                : getMetricColor('plddt_overall', d.plddt_overall)}`}>
                                                                            {preferredAnalysisLens === 'fampnn'
                                                                                ? formatMetric(d.fampnn_psce, 2)
                                                                                : preferredAnalysisLens === 'ppiflow'
                                                                                    ? formatMetric(d.maturation_delta_interface, 2)
                                                                                : formatMetric(d.plddt_overall, 1)}
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

                                            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
                                                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                                                    <div>
                                                        <h3 className="text-sm font-semibold text-slate-300">Analysis Control</h3>
                                                        <p className="mt-2 text-xs leading-5 text-slate-400">
                                                            Run the persisted per-output analyses from one place. These are separate from stage-native metrics like RF review, FA-MPNN pSCE, and PPIFlow outputs.
                                                        </p>
                                                        <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                                                            <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-emerald-200">
                                                                Cached {overviewAnalysisCounts.cached}
                                                            </span>
                                                            <span className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2.5 py-1 text-cyan-200">
                                                                Running {overviewAnalysisCounts.running}
                                                            </span>
                                                            <span className="rounded-full border border-slate-700 bg-slate-900/70 px-2.5 py-1 text-slate-300">
                                                                Missing {overviewAnalysisCounts.missing}
                                                            </span>
                                                            {overviewAnalysisCounts.attention > 0 && (
                                                                <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-amber-200">
                                                                    Attention {overviewAnalysisCounts.attention}
                                                                </span>
                                                            )}
                                                        </div>
                                                    </div>
                                                    <div className="relative flex items-center gap-2 self-start" ref={overviewAnalysisMenuRef}>
                                                        {isAntibodyContext && (
                                                            <button
                                                                type="button"
                                                                onClick={() => runAntibodyAnalysis.mutate()}
                                                                disabled={!selectedDesignId || antibodyAnalysisBusy}
                                                                className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-semibold transition-colors ${selectedDesignId && !antibodyAnalysisBusy
                                                                    ? 'border-amber-500/40 bg-amber-500/10 text-amber-100 hover:bg-amber-500/20'
                                                                    : 'cursor-not-allowed border-slate-700 bg-slate-900/70 text-slate-500'
                                                                    }`}
                                                                title="Run persisted ANARCII/CDR annotation for the selected output"
                                                            >
                                                                {antibodyAnalysisBusy ? 'Running ANARCII…' : antibodyHasAnnotation ? 'Refresh ANARCII' : 'Run ANARCII'}
                                                            </button>
                                                        )}
                                                        <button
                                                            type="button"
                                                            onClick={() => setShowOverviewAnalysisMenu((current) => !current)}
                                                            disabled={!selectedDesignId}
                                                            className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-semibold transition-colors ${selectedDesignId
                                                                ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200 hover:bg-cyan-500/20'
                                                                : 'cursor-not-allowed border-slate-700 bg-slate-900/70 text-slate-500'
                                                                }`}
                                                        >
                                                            Analyses
                                                            <span className={`text-[10px] transition-transform ${showOverviewAnalysisMenu ? 'rotate-180' : ''}`}>▾</span>
                                                        </button>
                                                        {showOverviewAnalysisMenu && (
                                                            <div className="absolute right-0 z-20 mt-2 w-[430px] rounded-xl border border-slate-700/80 bg-slate-950/95 p-3 shadow-2xl backdrop-blur">
                                                                <div className="border-b border-slate-800/80 pb-3">
                                                                    <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Selected Output</div>
                                                                    <div className="mt-2 text-sm font-medium text-white">
                                                                        {selectedDesign ? getFriendlyDesignName(selectedDesign as any) : 'No output selected'}
                                                                    </div>
                                                                    <div className="mt-1 text-[11px] text-slate-400">
                                                                        {selectedDesign ? `${getOutputSourceLabel(selectedDesign)} • ${selectedDesignSource === 'rfantibody' ? 'review/source metrics stay automatic' : 'derived analyses persist once run'}` : 'Choose an output in the viewer first.'}
                                                                    </div>
                                                                </div>
                                                                <div className="mt-3 space-y-2">
                                                                    {overviewAnalysisItems.map((item) => {
                                                                        const statusCopy = formatPersistedAnalysisStatus(item.status);
                                                                        const canRun = Boolean(selectedDesignId) && !item.busy;
                                                                        return (
                                                                            <div key={item.key} className="rounded-lg border border-slate-800/80 bg-slate-900/70 p-3">
                                                                                <div className="flex items-start justify-between gap-3">
                                                                                    <div className="min-w-0 flex-1">
                                                                                        <div className="flex flex-wrap items-center gap-2">
                                                                                            <div className="text-sm font-medium text-slate-100">{item.label}</div>
                                                                                            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${getPersistedAnalysisStatusClass(item.status)}`}>
                                                                                                {statusCopy}
                                                                                            </span>
                                                                                        </div>
                                                                                        <div className="mt-1 text-[11px] text-slate-500">{item.scope}</div>
                                                                                        {item.summary && (
                                                                                            <div className="mt-2 text-[11px] text-slate-300">{item.summary}</div>
                                                                                        )}
                                                                                        {item.error && (
                                                                                            <div className="mt-2 text-[11px] text-rose-300">Last error: {item.error}</div>
                                                                                        )}
                                                                                    </div>
                                                                                    <button
                                                                                        type="button"
                                                                                        onClick={() => { void item.run(); }}
                                                                                        disabled={!canRun}
                                                                                        className={`shrink-0 rounded border px-2.5 py-1.5 text-[10px] font-semibold uppercase tracking-wider transition-colors ${canRun
                                                                                            ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200 hover:bg-cyan-500/20'
                                                                                            : 'cursor-wait border-slate-700 bg-slate-800 text-slate-500'
                                                                                            }`}
                                                                                    >
                                                                                        {item.busy ? 'Running…' : item.status === 'completed' ? 'Refresh' : 'Run'}
                                                                                    </button>
                                                                                </div>
                                                                            </div>
                                                                        );
                                                                    })}
                                                                </div>
                                                                <div className="mt-3 flex items-center justify-between gap-3 border-t border-slate-800/80 pt-3">
                                                                    <div className="text-[11px] text-slate-500">
                                                                        Run only what you need. Results persist and are reused across tabs.
                                                                    </div>
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => { void runMissingOverviewAnalyses(); }}
                                                                        disabled={!selectedDesignId || overviewAnalysisItems.every((item) => item.status === 'completed')}
                                                                        className={`rounded border px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider transition-colors ${selectedDesignId && overviewAnalysisItems.some((item) => item.status !== 'completed')
                                                                            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20'
                                                                            : 'cursor-not-allowed border-slate-700 bg-slate-900/70 text-slate-500'
                                                                            }`}
                                                                    >
                                                                        Run Missing
                                                                    </button>
                                                                </div>
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>
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
                                                    {isPostRFantibodyReview
                                                        ? `RF headline distances here are using the ${rfMetricLabels.short.toLowerCase()} lens. Switch lenses in the viewer or binder detail panel to compare against the alternate scope.`
                                                        : '`Any-Target Dist` measures nearest binder CA to any target residue, so it can be smaller than `Epitope Dist`, which only measures against the selected epitope residues.'}
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
                                                structureAnalysisRun={structureAnalysisRun}
                                                onRunStructureAnalysis={() => {
                                                    if (!structureAnalysisBusy) {
                                                        runStructureAnalysis.mutate();
                                                    }
                                                }}
                                                structureAnalysisBusy={structureAnalysisBusy}
                                                activeJob={activeJob}
                                                getMetricColor={getMetricColor}
                                                rfMetricScope={rfMetricScope}
                                                setRfMetricScope={setRfMetricScope}
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
                                                                        {(['rfantibody', 'boltzgen', 'fampnn', 'ppiflow', 'validation'] as OutputSourceFilter[])
                                                                            .filter((source) => antibodyDesignGroups[source].length > 0 && (antibodySourceFilter === 'all' || antibodySourceFilter === source))
                                                                            .map((source) => (
                                                                                <optgroup key={source} label={`${getOutputSourceLabel(antibodyDesignGroups[source][0])} (${antibodyDesignGroups[source].length})`}>
                                                                                    {antibodyDesignGroups[source].map((d) => (
                                                                                        <option key={d.id} value={d.id}>
                                                                                            {getFriendlyDesignName(d)}{getDesignSelectorMetricLabel(d) ? ` | ${getDesignSelectorMetricLabel(d)}` : ''}
                                                                                        </option>
                                                                                    ))}
                                                                                </optgroup>
                                                                            ))}
                                                                    </select>
                                                                    <div className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 text-xs">▾</div>
                                                                </div>
                                                            </div>
                                                            <div className="flex flex-wrap gap-2">
                                                                {(['all', 'rfantibody', 'boltzgen', 'fampnn', 'ppiflow', 'validation'] as OutputSourceFilter[]).map((source) => {
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
                                                                    <span>{isPostRFantibodyReview ? 'CDR Epi Cts ≥' : 'Epi Cts ≥'}</span>
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
                                                                    <span>{isPostRFantibodyReview ? 'CDR Tgt Cts ≥' : 'Any Tgt Cts ≥'}</span>
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
                                                                    <span>{isPostRFantibodyReview ? 'CDR Epi Dist ≤' : 'Epi Dist ≤'}</span>
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
                                                                    <span>{isPostRFantibodyReview ? 'CDR-Tgt Dist ≤' : 'Any-Tgt Dist ≤'}</span>
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
                                                                {selectedDesignSource === 'rfantibody'
                                                                    ? `The RF review cards below are showing ${rfMetricLabels.short.toLowerCase()} engagement. Switch to whole-antibody mode when you want framework-mediated nanobody contacts in the headline metrics.`
                                                                    : '`Any-Target Dist` is nearest binder CA to the full target surface. `Epitope Dist` is nearest binder CA only to the selected epitope residues, so the any-target distance can legitimately be smaller.'}
                                                            </div>
                                                        </div>
                                                    </div>

                                                    {selectedDesignSource === 'rfantibody' && (
                                                        <div className="mb-4 rounded-xl border border-violet-500/20 bg-violet-500/5 p-4">
                                                            <div className="flex flex-wrap items-center justify-between gap-3">
                                                                <div>
                                                                    <div className="text-[11px] uppercase tracking-wider text-violet-200">RF Screening Lens</div>
                                                                    <div className="mt-1 text-sm text-slate-300">
                                                                        Headline engagement metrics are currently showing <span className="font-semibold text-white">{rfMetricLabels.short}</span>.
                                                                    </div>
                                                                </div>
                                                                <div className="inline-flex rounded-lg border border-slate-700/70 bg-slate-950/60 p-1">
                                                                    {(['cdr_loops', 'whole_antibody'] as RfScreeningScope[]).map((scope) => {
                                                                        const active = rfMetricScope === scope;
                                                                        return (
                                                                            <button
                                                                                key={scope}
                                                                                type="button"
                                                                                onClick={() => setRfMetricScope(scope)}
                                                                                className={`rounded-md px-3 py-1.5 text-xs transition-colors ${active
                                                                                    ? 'bg-violet-500/20 text-violet-100'
                                                                                    : 'text-slate-400 hover:text-slate-200'}`}
                                                                            >
                                                                                {RF_SCOPE_LABELS[scope].short}
                                                                            </button>
                                                                        );
                                                                    })}
                                                                </div>
                                                            </div>
                                                            {Array.isArray(selectedDesignRfLoopSummary?.redesign_candidate_loops) && selectedDesignRfLoopSummary.redesign_candidate_loops.length > 0 && (
                                                                <div className="mt-3 text-xs text-amber-200">
                                                                    Suggested redesign loops: {(selectedDesignRfLoopSummary.redesign_candidate_loops as string[]).join(', ')}
                                                                </div>
                                                            )}
                                                        </div>
                                                    )}

                                                    <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
                                                        {selectedDesignMetricCards.map((card) => (
                                                            <div key={card.label} className="rounded-xl border border-slate-700/50 bg-slate-800/50 p-4">
                                                                <div className="text-[11px] uppercase tracking-wider text-slate-500">{card.label}</div>
                                                                <div className={`mt-2 text-lg font-semibold ${card.tone}`}>{card.value as any}</div>
                                                            </div>
                                                        ))}
                                                    </div>

                                                    {selectedDesignSequenceEntries.length > 0 && (
                                                        <div className="mt-4 rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
                                                            <div className="flex flex-wrap items-start justify-between gap-3">
                                                                <div>
                                                                    <div className="text-[11px] uppercase tracking-wider text-slate-500">
                                                                        {selectedDesignHasPpiflowLens ? 'PPIFlow Sequence Record' : 'FAMPNN Sequence Record'}
                                                                    </div>
                                                                    <div className="mt-2 text-xs font-semibold text-white">Full Amino-Acid Sequence</div>
                                                                    <div className="mt-1 text-[11px] text-slate-400">
                                                                        {selectedDesignSequenceSourceLabel} • {selectedDesignSequenceChainSummary || 'stage-native order'} • {selectedDesignFullSequence.length} aa total
                                                                    </div>
                                                                </div>
                                                                <div className="flex flex-wrap items-center gap-2">
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => void handleCopySequenceText(selectedDesignFullSequence, 'full')}
                                                                        disabled={!selectedDesignFullSequence}
                                                                        className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-[11px] font-medium text-emerald-100 transition-colors hover:bg-emerald-500/20"
                                                                    >
                                                                        {sequenceCopyFeedback === 'full' ? 'Copied Full Sequence' : 'Copy Full Sequence'}
                                                                    </button>
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => void handleCopySequenceText(selectedDesignFullSequenceFasta, 'fasta')}
                                                                        disabled={!selectedDesignFullSequenceFasta}
                                                                        className="rounded-md border border-slate-600 bg-slate-900/60 px-3 py-1.5 text-[11px] font-medium text-slate-200 transition-colors hover:border-slate-500 hover:bg-slate-900"
                                                                    >
                                                                        {sequenceCopyFeedback === 'fasta' ? 'Copied FASTA' : 'Copy FASTA'}
                                                                    </button>
                                                                </div>
                                                            </div>
                                                            <div className="mt-3 overflow-x-auto rounded-lg border border-slate-700/40 bg-slate-950/55 p-4">
                                                                <pre className="font-mono text-xs leading-6 text-slate-100 whitespace-pre">
                                                                    {selectedDesignSequenceViewerText}
                                                                </pre>
                                                            </div>
                                                            <div className="mt-3 flex flex-wrap gap-2">
                                                                {selectedDesignSequenceEntries.map((entry) => (
                                                                    <div key={`${entry.chain}:${entry.length}`} className="rounded-full border border-slate-700/50 bg-slate-950/40 px-3 py-1 text-[11px] text-slate-300">
                                                                        Chain {entry.chain} • {entry.length} aa{entry.psce != null ? ` • pSCE ${entry.psce.toFixed(2)}` : ''}
                                                                    </div>
                                                                ))}
                                                            </div>
                                                            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
                                                                <span>Wrapped in 10-aa blocks with line ranges for manual inspection.</span>
                                                                <span className={sequenceCopyFeedback === 'error' ? 'text-rose-300' : 'text-slate-500'}>
                                                                    {sequenceCopyFeedback === 'error'
                                                                        ? 'Clipboard write failed. Manual select/copy still works.'
                                                                        : 'Use FASTA copy to preserve explicit chain boundaries.'}
                                                                </span>
                                                            </div>
                                                        </div>
                                                    )}

                                                    {(selectedDesignStageSettingsRows.length > 0 || selectedDesignPpiflowSummaryRows.length > 0 || selectedDesignLineageRows.length > 0 || selectedDesignPpiflowLoopRows.length > 0) && (
                                                        <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-4">
                                                            {selectedDesignLineageRows.length > 0 && (
                                                                <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
                                                                    <div className="text-[11px] uppercase tracking-wider text-slate-500">Lineage & Source</div>
                                                                    <div className="mt-3 space-y-2 text-xs">
                                                                        {selectedDesignLineageRows.map((entry) => {
                                                                            const [label, value] = entry;
                                                                            return (
                                                                            <div key={label ?? 'lineage'} className="flex items-start justify-between gap-3 rounded-lg bg-slate-950/40 px-3 py-2">
                                                                                <span className="text-slate-500">{label}</span>
                                                                                <span className="max-w-[60%] break-words text-right font-mono text-slate-200">{value}</span>
                                                                            </div>
                                                                            );
                                                                        })}
                                                                    </div>
                                                                </div>
                                                            )}

                                                            {selectedDesignStageSettingsRows.length > 0 && (
                                                                <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
                                                                    <div className="text-[11px] uppercase tracking-wider text-slate-500">
                                                                        {selectedDesignSource === 'fampnn' ? 'FAMPNN Settings' : 'PPIFlow Settings'}
                                                                    </div>
                                                                    <div className="mt-3 space-y-2 text-xs">
                                                                        {selectedDesignStageSettingsRows.map(([label, value]) => (
                                                                            <div key={label} className="flex items-start justify-between gap-3 rounded-lg bg-slate-950/40 px-3 py-2">
                                                                                <span className="text-slate-500">{label}</span>
                                                                                <span className="max-w-[60%] break-words text-right font-mono text-slate-200">{value}</span>
                                                                            </div>
                                                                        ))}
                                                                    </div>
                                                                </div>
                                                            )}

                                                            {selectedDesignPpiflowSummaryRows.length > 0 && (
                                                                <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
                                                                    <div className="text-[11px] uppercase tracking-wider text-slate-500">PPIFlow Refinement Record</div>
                                                                    <div className="mt-3 space-y-2 text-xs">
                                                                        {selectedDesignPpiflowSummaryRows.map(([label, value]) => (
                                                                            <div key={label} className="flex items-start justify-between gap-3 rounded-lg bg-slate-950/40 px-3 py-2">
                                                                                <span className="text-slate-500">{label}</span>
                                                                                <span className="max-w-[60%] break-words text-right font-mono text-slate-200">{value}</span>
                                                                            </div>
                                                                        ))}
                                                                    </div>
                                                                </div>
                                                            )}

                                                            {selectedDesignPpiflowLoopRows.length > 0 && (
                                                                <div className="rounded-xl border border-slate-700/50 bg-slate-800/40 p-4 xl:col-span-4">
                                                                    <div className="text-[11px] uppercase tracking-wider text-slate-500">PPIFlow Loop Deltas</div>
                                                                    <div className="mt-3 overflow-x-auto rounded-lg border border-slate-700/40 bg-slate-950/40">
                                                                        <table className="min-w-full text-xs">
                                                                            <thead className="bg-slate-900/80 text-slate-400">
                                                                                <tr>
                                                                                    <th className="px-3 py-2 text-left font-medium">Loop</th>
                                                                                    <th className="px-3 py-2 text-left font-medium">Selected</th>
                                                                                    <th className="px-3 py-2 text-left font-medium">Objective</th>
                                                                                    <th className="px-3 py-2 text-left font-medium">ΔIface</th>
                                                                                    <th className="px-3 py-2 text-left font-medium">RMSD</th>
                                                                                    <th className="px-3 py-2 text-left font-medium">ΔTgt Cts</th>
                                                                                    <th className="px-3 py-2 text-left font-medium">ΔTgt Dist</th>
                                                                                    <th className="px-3 py-2 text-left font-medium">ΔEpi Cts</th>
                                                                                    <th className="px-3 py-2 text-left font-medium">ΔEpi Dist</th>
                                                                                </tr>
                                                                            </thead>
                                                                            <tbody>
                                                                                {selectedDesignPpiflowLoopRows.map((row) => (
                                                                                    <tr key={row.loopId} className="border-t border-slate-800/70">
                                                                                        <td className="px-3 py-2 font-mono text-slate-200">{row.loopId}</td>
                                                                                        <td className={`px-3 py-2 ${row.selected ? 'text-emerald-300' : 'text-slate-500'}`}>{row.selected ? 'yes' : 'no'}</td>
                                                                                        <td className={`px-3 py-2 font-mono ${row.objectiveScore != null ? (row.objectiveScore <= 0 ? 'text-emerald-300' : 'text-rose-300') : 'text-slate-500'}`}>{formatMetric(row.objectiveScore, 2)}</td>
                                                                                        <td className={`px-3 py-2 font-mono ${row.deltaInterface != null ? (row.deltaInterface < 0 ? 'text-emerald-300' : row.deltaInterface > 0 ? 'text-rose-300' : 'text-slate-300') : 'text-slate-500'}`}>{formatMetric(row.deltaInterface, 2)}</td>
                                                                                        <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(row.rmsd, 2)}</td>
                                                                                        <td className={`px-3 py-2 font-mono ${row.targetContactDelta != null ? (row.targetContactDelta > 0 ? 'text-emerald-300' : row.targetContactDelta < 0 ? 'text-rose-300' : 'text-slate-300') : 'text-slate-500'}`}>{row.targetContactDelta != null ? (row.targetContactDelta > 0 ? `+${row.targetContactDelta}` : String(row.targetContactDelta)) : '—'}</td>
                                                                                        <td className={`px-3 py-2 font-mono ${row.targetDistanceDelta != null ? (row.targetDistanceDelta > 0 ? 'text-emerald-300' : row.targetDistanceDelta < 0 ? 'text-rose-300' : 'text-slate-300') : 'text-slate-500'}`}>{formatMetric(row.targetDistanceDelta, 2)}</td>
                                                                                        <td className={`px-3 py-2 font-mono ${row.epitopeContactDelta != null ? (row.epitopeContactDelta > 0 ? 'text-emerald-300' : row.epitopeContactDelta < 0 ? 'text-rose-300' : 'text-slate-300') : 'text-slate-500'}`}>{row.epitopeContactDelta != null ? (row.epitopeContactDelta > 0 ? `+${row.epitopeContactDelta}` : String(row.epitopeContactDelta)) : '—'}</td>
                                                                                        <td className={`px-3 py-2 font-mono ${row.epitopeDistanceDelta != null ? (row.epitopeDistanceDelta > 0 ? 'text-emerald-300' : row.epitopeDistanceDelta < 0 ? 'text-rose-300' : 'text-slate-300') : 'text-slate-500'}`}>{formatMetric(row.epitopeDistanceDelta, 2)}</td>
                                                                                    </tr>
                                                                                ))}
                                                                            </tbody>
                                                                        </table>
                                                                    </div>
                                                                </div>
                                                            )}
                                                        </div>
                                                    )}

                                                    {selectedDesignSource === 'rfantibody' && selectedDesignRfLoopEntries.length > 0 && (
                                                        <div className="mt-4 rounded-xl border border-slate-700/50 bg-slate-800/40 p-4">
                                                            <div className="flex items-center justify-between gap-3">
                                                                <div className="text-[11px] uppercase tracking-wider text-slate-500">Per-Loop RF Engagement</div>
                                                                <div className="text-[11px] text-slate-500">Spot detached loops before PPIFlow or redesign.</div>
                                                            </div>
                                                            <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-3">
                                                                {selectedDesignRfLoopEntries.map(({ loopId, metrics }) => {
                                                                    const status = String(metrics.screening_status || metrics.screening_note || '').trim();
                                                                    return (
                                                                        <div key={loopId} className={`rounded-lg border p-3 ${metrics.redesign_candidate ? 'border-amber-500/30 bg-amber-500/10' : 'border-slate-700/50 bg-slate-900/40'}`}>
                                                                            <div className="flex items-center justify-between gap-2">
                                                                                <div className="text-sm font-semibold text-white">{loopId}</div>
                                                                                {status && (
                                                                                    <span className={`rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wider ${metrics.redesign_candidate ? 'bg-amber-500/20 text-amber-100' : 'bg-emerald-500/20 text-emerald-100'}`}>
                                                                                        {status.replace(/_/g, ' ')}
                                                                                    </span>
                                                                                )}
                                                                            </div>
                                                                            <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                                                                                <div className="rounded bg-slate-950/60 px-2 py-2 text-slate-300">
                                                                                    <div className="text-[10px] uppercase tracking-wider text-slate-500">Epi Cts</div>
                                                                                    <div className="mt-1 font-mono text-sm text-white">{metrics.epitope_contact_count ?? '—'}</div>
                                                                                </div>
                                                                                <div className="rounded bg-slate-950/60 px-2 py-2 text-slate-300">
                                                                                    <div className="text-[10px] uppercase tracking-wider text-slate-500">Target Cts</div>
                                                                                    <div className="mt-1 font-mono text-sm text-white">{metrics.target_contact_count ?? '—'}</div>
                                                                                </div>
                                                                                <div className="rounded bg-slate-950/60 px-2 py-2 text-slate-300">
                                                                                    <div className="text-[10px] uppercase tracking-wider text-slate-500">Epi Dist</div>
                                                                                    <div className="mt-1 font-mono text-sm text-white">{metrics.epitope_min_distance != null ? `${metrics.epitope_min_distance.toFixed(2)} Å` : '—'}</div>
                                                                                </div>
                                                                                <div className="rounded bg-slate-950/60 px-2 py-2 text-slate-300">
                                                                                    <div className="text-[10px] uppercase tracking-wider text-slate-500">Target Dist</div>
                                                                                    <div className="mt-1 font-mono text-sm text-white">{metrics.target_min_distance != null ? `${metrics.target_min_distance.toFixed(2)} Å` : '—'}</div>
                                                                                </div>
                                                                            </div>
                                                                            {metrics.screening_note && metrics.screening_note !== metrics.screening_status && (
                                                                                <div className="mt-3 text-[11px] text-slate-400">
                                                                                    {metrics.screening_note}
                                                                                </div>
                                                                            )}
                                                                            {metrics.redesign_candidate && (
                                                                                <div className="mt-3 text-[11px] text-amber-200">
                                                                                    Candidate for PPIFlow/redesign triage.
                                                                                </div>
                                                                            )}
                                                                        </div>
                                                                    );
                                                                })}
                                                            </div>
                                                        </div>
                                                    )}

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
                                                        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-100">
                                                            <div className="flex flex-wrap items-center justify-between gap-3">
                                                                <div>
                                                                    <div className="font-semibold uppercase tracking-wider text-amber-200">Binder Annotation</div>
                                                                    <div className="mt-1 text-amber-100/90">
                                                                        CDR annotation is now on-demand and persisted. Run it once for this design to cache loops, overlays, and framework-contact regions.
                                                                    </div>
                                                                </div>
                                                                <div className="flex items-center gap-3">
                                                                    <span className="text-[11px] uppercase tracking-wider text-amber-200/80">{antibodyAnalysisStatusCopy}</span>
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => runAntibodyAnalysis.mutate()}
                                                                        disabled={antibodyAnalysisBusy}
                                                                        className={`rounded-lg border px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider transition-colors ${antibodyAnalysisBusy
                                                                            ? 'cursor-wait border-amber-500/20 bg-amber-500/10 text-amber-300/50'
                                                                            : 'border-amber-400/50 bg-amber-400/15 text-amber-100 hover:bg-amber-400/25'
                                                                            }`}
                                                                    >
                                                                        {antibodyAnalysisBusy ? 'Running…' : 'Run Annotation'}
                                                                    </button>
                                                                </div>
                                                            </div>
                                                            {antibodyAnalysisRun?.error_message && (
                                                                <div className="mt-3 text-[11px] text-rose-200">Last error: {antibodyAnalysisRun.error_message}</div>
                                                            )}
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
                                                                        ['FR2', selectedDesign.fr2_contacts ?? antibodyData?.framework_regions?.fr2_contacts],
                                                                        ['DE', selectedDesign.de_loop ?? antibodyData?.framework_regions?.de_loop],
                                                                        ['FR3', selectedDesign.fr3_contacts ?? antibodyData?.framework_regions?.fr3_contacts],
                                                                        ['FR4', selectedDesign.fr4_contacts ?? antibodyData?.framework_regions?.fr4_contacts],
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
                                            {reviewBackboneRows.length > 0 && backboneFilterApplies && (
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
                                                        <span className="text-xs text-slate-500">{isPostRFantibodyReview ? 'RF pLDDT ≥' : 'pLDDT ≥'}</span>
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
                                                        <span className="text-xs text-slate-500">ipSAE ≥</span>
                                                        <input
                                                            type="range"
                                                            min="0"
                                                            max="1"
                                                            step="0.05"
                                                            value={filterDraft.ipsaeMin}
                                                            onChange={(e) => updateFilterDraft('ipsaeMin', Number(e.target.value))}
                                                            className="w-24 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                                        />
                                                        <span className="text-xs text-cyan-400 font-mono w-8">{filterDraft.ipsaeMin.toFixed(2)}</span>
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
                                                    {isPostRFantibodyReview
                                                        ? `RF table metrics are using the ${rfMetricLabels.short.toLowerCase()} lens. Use the RF lens toggle in review to compare loop-only and whole-antibody screening.`
                                                        : '`Any-Target Dist` can be smaller than `Epitope Dist` because it measures against the whole target surface, not just the selected epitope residues.'}
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
                                                    ['boltzgen', 'BoltzGen'],
                                                    ['fampnn', 'FAMPNN'],
                                                    ['ppiflow', 'PPIFlow'],
                                                    ['validation', 'Validation'],
                                                ] as Array<[OutputSourceFilter, string]>).map(([value, label]) => (
                                                    <button
                                                        key={value}
                                                        type="button"
                                                        onClick={() => {
                                                            manualOutputSourceSelectionRef.current = true;
                                                            outputSourceSelectionJobRef.current = selectedJobId || null;
                                                            setOutputSourceFilter(value);
                                                        }}
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
                                                <span className="flex items-center gap-1.5 ml-auto">
                                                    <button
                                                        type="button"
                                                        onClick={() => exportFasta('binder')}
                                                        disabled={!selectedJobId}
                                                        className="flex items-center gap-1 rounded border border-teal-500/40 bg-teal-500/10 px-2 py-1 text-[11px] text-teal-200 transition-colors hover:border-teal-400 hover:bg-teal-500/20 disabled:opacity-40 disabled:cursor-not-allowed"
                                                        title="Export full binder sequences for all designs in this job as FASTA"
                                                    >
                                                        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
                                                        Binder FASTA
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={() => exportFasta('cdr')}
                                                        disabled={!selectedJobId}
                                                        className="flex items-center gap-1 rounded border border-teal-500/40 bg-teal-500/10 px-2 py-1 text-[11px] text-teal-200 transition-colors hover:border-teal-400 hover:bg-teal-500/20 disabled:opacity-40 disabled:cursor-not-allowed"
                                                        title="Export CDR loop sequences for all designs in this job as FASTA"
                                                    >
                                                        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
                                                        CDR FASTA
                                                    </button>
                                                </span>
                                            </div>
                                            {/* Table */}
                                            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-700/50 bg-slate-900/40 px-3 py-2 text-[11px] text-slate-400">
                                                <span>Drag inside the table to pan horizontally or vertically. Scrollbars still work.</span>
                                                <span>{pageSize === 0 ? 'Windowed table view enabled' : 'Use page size + drag-to-pan for dense scans'}</span>
                                            </div>
                                            <div
                                                ref={tableScrollViewportRef}
                                                onPointerDown={beginTablePan}
                                                onPointerMove={moveTablePan}
                                                onPointerUp={endTablePan}
                                                onPointerCancel={endTablePan}
                                                className="w-full max-h-[70vh] overflow-auto rounded-xl border border-slate-700/60 bg-slate-950/30 pb-2 cursor-grab active:cursor-grabbing select-none [scrollbar-width:thin] [&_table]:text-[11px] [&_table]:leading-tight [&_thead_th]:sticky [&_thead_th]:top-0 [&_thead_th]:z-10 [&_thead_th]:bg-slate-950/95 [&_thead_th]:backdrop-blur [&_th]:px-2 [&_th]:py-1.5 [&_th]:text-[10px] [&_th]:uppercase [&_th]:tracking-[0.04em] [&_td]:px-2 [&_td]:py-1.5"
                                            >
                                                <table className="w-full min-w-[2700px]">
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
                                                                { key: 'epitope_contact_count', label: isPostRFantibodyReview ? 'CDR Epi Cts' : 'Epi Cts' },
                                                                { key: 'target_contact_count', label: isPostRFantibodyReview ? 'CDR Tgt Cts' : 'Any Tgt Cts' },
                                                                { key: 'epitope_min_distance', label: isPostRFantibodyReview ? 'CDR Epi Dist' : 'Epi Dist' },
                                                                { key: 'target_min_distance', label: isPostRFantibodyReview ? 'CDR-Tgt Dist' : 'Any-Tgt Dist' },
                                                                { key: 'epitope_centroid_distance', label: isPostRFantibodyReview ? 'CDR Epi Cent' : 'Epi Cent' },
                                                                { key: 'target_centroid_distance', label: isPostRFantibodyReview ? 'CDR Tgt Cent' : 'Any-Tgt Cent' },
                                                                { key: 'affinity_score', label: 'Affinity' },
                                                                { key: 'binder_probability', label: 'Binder %' },
                                                                { key: 'fampnn_psce', label: 'pSCE' },
                                                                { key: 'ipsae', label: 'ipSAE' },
                                                                { key: 'plddt_overall', label: isPostRFantibodyReview ? 'RF pLDDT Glob' : 'pLDDT' },
                                                                ...(isPostRFantibodyReview ? [
                                                                    { key: 'rfa_plddt_selected', label: 'RF pLDDT Sel' },
                                                                ] : []),
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
                                                                { key: 'maturation_selected_delta_interface', label: 'ΔIface Sel' },
                                                                { key: 'maturation_delta_interface', label: 'ΔIface Glob' },
                                                                { key: 'maturation_selected_rmsd', label: 'RMSD Sel' },
                                                                { key: 'maturation_rmsd', label: 'RMSD Glob' },
                                                                { key: 'rog', label: 'RoG' },
                                                                { key: 'rfd_rog', label: 'RFD RoG' },
                                                                { key: 'fr2_contacts', label: 'FR2' },
                                                                { key: 'is_favorite', label: '★' },
                                                                ...(showPpiflowColumns ? [
                                                                    { key: 'ppiflow_source_name', label: 'PPI Src' },
                                                                    { key: 'ppiflow_sample_index', label: 'Sample' },
                                                                    { key: 'ppiflow_objective_score', label: 'Obj' },
                                                                    { key: 'ppiflow_primary_loop', label: 'Loop' },
                                                                    { key: 'ppiflow_primary_loop_rmsd', label: 'Loop RMSD' },
                                                                    { key: 'ppiflow_primary_loop_target_contact_delta', label: 'ΔTgt Cts' },
                                                                    { key: 'ppiflow_primary_loop_target_distance_delta', label: 'ΔTgt Dist' },
                                                                    { key: 'ppiflow_primary_loop_epitope_contact_delta', label: 'ΔEpi Cts' },
                                                                    { key: 'ppiflow_primary_loop_epitope_distance_delta', label: 'ΔEpi Dist' },
                                                                    { key: 'maturation_selected_interface_score', label: 'Iface Sel' },
                                                                    { key: 'maturation_interface_score', label: 'Iface Glob' },
                                                                    { key: 'ppiflow_seq_identity', label: 'Seq ID' },
                                                                    { key: 'ppiflow_anchor_count', label: 'Anchors' },
                                                                    { key: 'ppiflow_clash_count', label: 'CA Clash' },
                                                                ] : []),
                                                            ].map(col => {
                                                                const sortable = isTableColumnSortable(
                                                                    col.key,
                                                                    'sortable' in col && typeof col.sortable === 'boolean' ? col.sortable : undefined,
                                                                );
                                                                return (
                                                                    <th
                                                                        key={col.key}
                                                                        data-table-interactive="true"
                                                                        onClick={sortable ? () => handleSort(col.key) : undefined}
                                                                        className={`px-3 py-2 text-left font-medium text-slate-400 ${sortable ? 'cursor-pointer hover:text-white' : ''}`}
                                                                    >
                                                                        {col.label}
                                                                        {sortable && sortField === col.key && (
                                                                            <span className="ml-1">{sortDir === 'asc' ? '▲' : '▼'}</span>
                                                                        )}
                                                                    </th>
                                                                );
                                                            })}
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {tableDesigns.map(d => {
                                                            const rowPpiflowScore = getPpiflowScoreRecord(d);
                                                            const rowPpiflowAnchors = getPpiflowAnchorRecord(d);
                                                            return (
                                                            <tr
                                                                key={d.id}
                                                                className={`border-b border-slate-800 cursor-pointer hover:bg-slate-800/30 ${selectedDesignSet.has(d.id) || selectedDesignId === d.id ? 'bg-cyan-500/5' : ''
                                                                    }`}
                                                                onClick={() => {
                                                                    if (shouldSuppressTableClick()) return;
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
                                                                            : inferDesignOutputSource(d as any) === 'boltzgen'
                                                                                ? 'border-amber-500/40 bg-amber-500/10 text-amber-200'
                                                                            : inferDesignOutputSource(d as any) === 'fampnn'
                                                                                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                                                                                : inferDesignOutputSource(d as any) === 'ppiflow'
                                                                                    ? 'border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-200'
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
                                                                        const tier = getBindingTier(d);
                                                                        const bonusNote = tier.metric.contactBonus > 0
                                                                            ? `, Bonus: +${tier.metric.contactBonus.toFixed(2)}`
                                                                            : '';
                                                                        return (
                                                                            <span
                                                                                className={`px-2 py-0.5 text-xs font-bold rounded border ${tier.bgColor} ${tier.color}`}
                                                                                title={`${tier.label} binding (${tier.metric.label}: ${tier.metric.rawValue?.toFixed(2) ?? '—'}, Score: ${tier.metric.scoreValue?.toFixed(2) ?? '—'}, Contacts: ${d.epitope_contact_count ?? '—'}${bonusNote})`}
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
                                                                <td className={`px-3 py-2 font-mono ${d.epitope_centroid_distance != null && d.epitope_centroid_distance <= 6 ? 'text-emerald-400' :
                                                                    d.epitope_centroid_distance != null && d.epitope_centroid_distance <= 10 ? 'text-amber-400' : 'text-slate-500'}`}>
                                                                    {formatMetric(d.epitope_centroid_distance, 1)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${d.target_centroid_distance != null && d.target_centroid_distance <= 6 ? 'text-emerald-400' :
                                                                    d.target_centroid_distance != null && d.target_centroid_distance <= 10 ? 'text-amber-400' : 'text-slate-500'}`}>
                                                                    {formatMetric(d.target_centroid_distance, 1)}
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
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('ipsae', d.ipsae)}`}>
                                                                    {formatMetric(d.ipsae, 2)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('plddt_overall', d.plddt_overall)}`}>
                                                                    {formatMetric(d.plddt_overall, 1)}
                                                                </td>
                                                                {isPostRFantibodyReview && (
                                                                    <td className={`px-3 py-2 font-mono ${getMetricColor('plddt_overall', d.rfa_plddt_selected ?? null)}`}>
                                                                        {formatMetric(d.rfa_plddt_selected, 1)}
                                                                    </td>
                                                                )}
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
                                                                <td className={`px-3 py-2 font-mono ${d.maturation_selected_delta_interface != null && d.maturation_selected_delta_interface < 0 ? 'text-emerald-400' :
                                                                    d.maturation_selected_delta_interface != null && d.maturation_selected_delta_interface > 0 ? 'text-red-400' : 'text-slate-500'}`}
                                                                    title={d.maturation_selected_delta_interface != null ? `Selected ΔInterface: ${d.maturation_selected_delta_interface.toFixed(1)} REU` : '—'}>
                                                                    {formatMetric(d.maturation_selected_delta_interface ?? rowPpiflowScore?.selected_delta_interface_score, 1)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${d.maturation_delta_interface != null && d.maturation_delta_interface < 0 ? 'text-emerald-400' :
                                                                    d.maturation_delta_interface != null && d.maturation_delta_interface > 0 ? 'text-red-400' : 'text-slate-500'}`}
                                                                    title={d.maturation_delta_interface != null ? `Global ΔInterface: ${d.maturation_delta_interface.toFixed(1)} REU` : '—'}>
                                                                    {formatMetric(d.maturation_delta_interface, 1)}
                                                                </td>
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.maturation_selected_rmsd ?? rowPpiflowScore?.selected_rmsd_backbone, 2)}</td>
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.maturation_rmsd, 2)}</td>
                                                                {showPpiflowColumns && (
                                                                    <>
                                                                        <td className="px-3 py-2 max-w-[200px] truncate text-xs text-slate-300" title={getPpiflowSourceName(d as any) ?? ''}>
                                                                            {getPpiflowSourceName(d as any) ?? '—'}
                                                                        </td>
                                                                        <td className="px-3 py-2 font-mono text-slate-300">{getPpiflowSampleIndex(d as any) ?? '—'}</td>
                                                                        <td className={`px-3 py-2 font-mono ${(d.ppiflow_objective_score ?? rowPpiflowScore?.objective_score) != null ? ((d.ppiflow_objective_score ?? rowPpiflowScore?.objective_score) <= 0 ? 'text-emerald-400' : 'text-rose-400') : 'text-slate-500'}`}>
                                                                            {formatMetric(d.ppiflow_objective_score ?? rowPpiflowScore?.objective_score, 2)}
                                                                        </td>
                                                                        <td className="px-3 py-2 font-mono text-slate-300">{d.ppiflow_primary_loop ?? rowPpiflowScore?.primary_loop ?? '—'}</td>
                                                                        <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.ppiflow_primary_loop_rmsd ?? rowPpiflowScore?.primary_loop_rmsd, 2)}</td>
                                                                        <td className={`px-3 py-2 font-mono ${(d.ppiflow_primary_loop_target_contact_delta ?? rowPpiflowScore?.primary_loop_target_contact_delta) != null ? ((d.ppiflow_primary_loop_target_contact_delta ?? rowPpiflowScore?.primary_loop_target_contact_delta) > 0 ? 'text-emerald-400' : (d.ppiflow_primary_loop_target_contact_delta ?? rowPpiflowScore?.primary_loop_target_contact_delta) < 0 ? 'text-rose-400' : 'text-slate-300') : 'text-slate-500'}`}>
                                                                            {(d.ppiflow_primary_loop_target_contact_delta ?? rowPpiflowScore?.primary_loop_target_contact_delta) != null ? (() => {
                                                                                const value = d.ppiflow_primary_loop_target_contact_delta ?? rowPpiflowScore?.primary_loop_target_contact_delta;
                                                                                return value > 0 ? `+${value}` : String(value);
                                                                            })() : '—'}
                                                                        </td>
                                                                        <td className={`px-3 py-2 font-mono ${(d.ppiflow_primary_loop_target_distance_delta ?? rowPpiflowScore?.primary_loop_target_distance_delta) != null ? ((d.ppiflow_primary_loop_target_distance_delta ?? rowPpiflowScore?.primary_loop_target_distance_delta) > 0 ? 'text-emerald-400' : (d.ppiflow_primary_loop_target_distance_delta ?? rowPpiflowScore?.primary_loop_target_distance_delta) < 0 ? 'text-rose-400' : 'text-slate-300') : 'text-slate-500'}`}>
                                                                            {formatMetric(d.ppiflow_primary_loop_target_distance_delta ?? rowPpiflowScore?.primary_loop_target_distance_delta, 2)}
                                                                        </td>
                                                                        <td className={`px-3 py-2 font-mono ${(d.ppiflow_primary_loop_epitope_contact_delta ?? rowPpiflowScore?.primary_loop_epitope_contact_delta) != null ? ((d.ppiflow_primary_loop_epitope_contact_delta ?? rowPpiflowScore?.primary_loop_epitope_contact_delta) > 0 ? 'text-emerald-400' : (d.ppiflow_primary_loop_epitope_contact_delta ?? rowPpiflowScore?.primary_loop_epitope_contact_delta) < 0 ? 'text-rose-400' : 'text-slate-300') : 'text-slate-500'}`}>
                                                                            {(d.ppiflow_primary_loop_epitope_contact_delta ?? rowPpiflowScore?.primary_loop_epitope_contact_delta) != null ? (() => {
                                                                                const value = d.ppiflow_primary_loop_epitope_contact_delta ?? rowPpiflowScore?.primary_loop_epitope_contact_delta;
                                                                                return value > 0 ? `+${value}` : String(value);
                                                                            })() : '—'}
                                                                        </td>
                                                                        <td className={`px-3 py-2 font-mono ${(d.ppiflow_primary_loop_epitope_distance_delta ?? rowPpiflowScore?.primary_loop_epitope_distance_delta) != null ? ((d.ppiflow_primary_loop_epitope_distance_delta ?? rowPpiflowScore?.primary_loop_epitope_distance_delta) > 0 ? 'text-emerald-400' : (d.ppiflow_primary_loop_epitope_distance_delta ?? rowPpiflowScore?.primary_loop_epitope_distance_delta) < 0 ? 'text-rose-400' : 'text-slate-300') : 'text-slate-500'}`}>
                                                                            {formatMetric(d.ppiflow_primary_loop_epitope_distance_delta ?? rowPpiflowScore?.primary_loop_epitope_distance_delta, 2)}
                                                                        </td>
                                                                        <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.maturation_selected_interface_score ?? rowPpiflowScore?.selected_interface_score_matured, 1)}</td>
                                                                        <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.maturation_interface_score ?? rowPpiflowScore?.interface_score_matured, 1)}</td>
                                                                        <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(rowPpiflowScore?.sequence_identity, 2)}</td>
                                                                        <td className="px-3 py-2 font-mono text-slate-300">{rowPpiflowAnchors?.anchor_count ?? '—'}</td>
                                                                        <td className={`px-3 py-2 font-mono ${rowPpiflowScore?.clash_count_ca != null ? (rowPpiflowScore.clash_count_ca > 0 ? 'text-red-400' : 'text-emerald-400') : 'text-slate-500'}`}>
                                                                            {rowPpiflowScore?.clash_count_ca ?? '—'}
                                                                        </td>
                                                                    </>
                                                                )}
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.rog, 1)}</td>
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.rfd_rog, 1)}</td>
                                                                <td className="px-3 py-2 font-mono text-accent" title={`FR2: ${d.fr2_contacts || '—'}`}>
                                                                    {d.fr2_contacts || '—'}
                                                                </td>
                                                                <td className="px-3 py-2">{d.is_favorite ? '★' : ''}</td>
                                                            </tr>
                                                        )})}
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
