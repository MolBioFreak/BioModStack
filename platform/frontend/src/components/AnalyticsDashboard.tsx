import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Plot from 'react-plotly.js';
import type { Annotations, Data, Layout, Shape } from 'plotly.js';

import {
    fetchChainMetrics,
    fetchDesignPlotlyMetrics,
    fetchPAEData,
    type Design,
} from '../lib/api';
import { inferDesignAnalysisLens, type AnalysisLens } from './designOutputSource';

type MetricFamily = AnalysisLens | 'dynamic';
type ColorScaleName = 'Viridis' | 'Plasma' | 'Cividis' | 'Turbo';

interface MetricOption {
    key: string;
    label: string;
    color: string;
    family: MetricFamily;
}

interface AnalyticsDashboardProps {
    designs: Design[];
    jobName?: string;
    jobId?: string | null;
    preferredAnalysisLens?: AnalysisLens | 'auto';
    loadedDesignCount?: number;
}

const ANALYSIS_LENS_ORDER: AnalysisLens[] = ['rfantibody', 'fampnn', 'ppiflow', 'frustrampnn', 'protenix', 'validation'];

const FAMILY_META: Record<AnalysisLens, { title: string; description: string; accent: string }> = {
    validation: {
        title: 'Validation Loop',
        description: 'Primary structure-validation confidence, agreement, and validator-side quality metrics.',
        accent: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200',
    },
    rfantibody: {
        title: 'RFantibody Backbone Gate',
        description: 'Backbone screening and target-contact metrics from the RFA review flow. Orientation metrics will surface here automatically once they are persisted.',
        accent: 'border-violet-500/30 bg-violet-500/10 text-violet-200',
    },
    fampnn: {
        title: 'FAMPNN Sequence Design',
        description: 'Sequence design quality centered on PSCE plus any additional flattened FAMPNN sidechain signals.',
        accent: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
    },
    ppiflow: {
        title: 'PPIFlow Maturation',
        description: 'Post-validation maturation deltas, interface score shifts, and structural drift.',
        accent: 'border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-200',
    },
    frustrampnn: {
        title: 'FrustraMPNN QC',
        description: 'Frustration burden and post hoc structure quality checks for selected designs.',
        accent: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
    },
    protenix: {
        title: 'Protenix Validator Detail',
        description: 'Protenix-specific interface, disorder, recycle, and clash signals when that validator is in play.',
        accent: 'border-sky-500/30 bg-sky-500/10 text-sky-200',
    },
};

const COLOR_SCALE_OPTIONS: Array<{ value: ColorScaleName; label: string }> = [
    { value: 'Viridis', label: 'Viridis' },
    { value: 'Plasma', label: 'Plasma' },
    { value: 'Cividis', label: 'Cividis' },
    { value: 'Turbo', label: 'Turbo' },
];

const CORE_METRICS: MetricOption[] = [
    { key: 'plddt_overall', label: 'pLDDT', color: '#60a5fa', family: 'validation' },
    { key: 'plddt_binder', label: 'Binder pLDDT', color: '#3b82f6', family: 'validation' },
    { key: 'plddt_target', label: 'Target pLDDT', color: '#38bdf8', family: 'validation' },
    { key: 'pae_overall', label: 'PAE', color: '#fbbf24', family: 'validation' },
    { key: 'pae_interaction', label: 'Interaction PAE', color: '#f59e0b', family: 'validation' },
    { key: 'ptm', label: 'pTM', color: '#a78bfa', family: 'validation' },
    { key: 'iptm', label: 'iPTM', color: '#8b5cf6', family: 'validation' },
    { key: 'conf_score', label: 'Confidence', color: '#34d399', family: 'validation' },
    { key: 'affinity_score', label: 'Affinity', color: '#10b981', family: 'validation' },
    { key: 'binder_probability', label: 'Binder Probability', color: '#22c55e', family: 'validation' },
    { key: 'rmsd_overall', label: 'Overall RMSD', color: '#f87171', family: 'validation' },
    { key: 'rmsd_binder', label: 'Binder RMSD', color: '#fb7185', family: 'validation' },
    { key: 'rmsd_target', label: 'Target RMSD', color: '#f97316', family: 'validation' },
    { key: 'rog', label: 'Radius of Gyration', color: '#ec4899', family: 'validation' },
    { key: 'mpnn_score', label: 'MPNN Score', color: '#14b8a6', family: 'validation' },
    { key: 'binder_length', label: 'Binder Length', color: '#f8fafc', family: 'dynamic' },
    { key: 'cdr_h1_length', label: 'CDR-H1 Length', color: '#fca5a5', family: 'dynamic' },
    { key: 'cdr_h2_length', label: 'CDR-H2 Length', color: '#fdba74', family: 'dynamic' },
    { key: 'cdr_h3_length', label: 'CDR-H3 Length', color: '#fcd34d', family: 'dynamic' },
    { key: 'cdr_l1_length', label: 'CDR-L1 Length', color: '#86efac', family: 'dynamic' },
    { key: 'cdr_l2_length', label: 'CDR-L2 Length', color: '#67e8f9', family: 'dynamic' },
    { key: 'cdr_l3_length', label: 'CDR-L3 Length', color: '#c4b5fd', family: 'dynamic' },
    { key: 'rfd_rog', label: 'RFD Backbone RoG', color: '#a855f7', family: 'rfantibody' },
    { key: 'backbone_id', label: 'Backbone ID', color: '#c084fc', family: 'rfantibody' },
    { key: 'epitope_contact_count', label: 'Epitope Contacts', color: '#84cc16', family: 'rfantibody' },
    { key: 'epitope_min_distance', label: 'Epitope Min Distance', color: '#eab308', family: 'rfantibody' },
    { key: 'epitope_min_atom_distance', label: 'Epitope Atom Distance', color: '#facc15', family: 'rfantibody' },
    { key: 'target_contact_count', label: 'Any-Target Contacts', color: '#65a30d', family: 'rfantibody' },
    { key: 'target_min_distance', label: 'Any-Target Min Distance', color: '#22c55e', family: 'rfantibody' },
    { key: 'target_min_atom_distance', label: 'Any-Target Atom Distance', color: '#16a34a', family: 'rfantibody' },
    { key: 'screening_passed', label: 'Screen Passed', color: '#10b981', family: 'rfantibody' },
    { key: 'rfa_hotspot_covered_count', label: 'Hotspots Covered', color: '#a3e635', family: 'rfantibody' },
    { key: 'rfa_hotspot_min_distance', label: 'Hotspot Min Distance', color: '#84cc16', family: 'rfantibody' },
    { key: 'rfa_hotspot_avg_min_distance', label: 'Hotspot Avg Min Distance', color: '#65a30d', family: 'rfantibody' },
    { key: 'rfa_runtime_seconds', label: 'RFA Runtime', color: '#94a3b8', family: 'rfantibody' },
    { key: 'rfa_plddt_final', label: 'RFA Final pLDDT', color: '#7c3aed', family: 'rfantibody' },
    { key: 'rfa_plddt_delta', label: 'RFA pLDDT Delta', color: '#8b5cf6', family: 'rfantibody' },
    { key: 'fampnn_psce', label: 'FAMPNN PSCE', color: '#22c55e', family: 'fampnn' },
    { key: 'maturation_delta_interface', label: 'Delta Interface', color: '#e879f9', family: 'ppiflow' },
    { key: 'maturation_interface_score', label: 'Matured Interface Score', color: '#d946ef', family: 'ppiflow' },
    { key: 'maturation_rmsd', label: 'Maturation RMSD', color: '#f472b6', family: 'ppiflow' },
    { key: 'frustration_high_count', label: 'High Frustration Count', color: '#f59e0b', family: 'frustrampnn' },
    { key: 'frustration_min_count', label: 'Minimal Frustration Count', color: '#f97316', family: 'frustrampnn' },
    { key: 'frustration_pct_high', label: 'High Frustration Percent', color: '#fb923c', family: 'frustrampnn' },
    { key: 'protein_iptm', label: 'Protein iPTM', color: '#38bdf8', family: 'protenix' },
    { key: 'ligand_iptm', label: 'Ligand iPTM', color: '#0ea5e9', family: 'protenix' },
    { key: 'complex_iplddt', label: 'Interface pLDDT', color: '#06b6d4', family: 'protenix' },
    { key: 'complex_ipde', label: 'Interface PDE', color: '#f97316', family: 'protenix' },
    { key: 'disorder', label: 'Disorder', color: '#facc15', family: 'protenix' },
    { key: 'num_recycles', label: 'Recycle Count', color: '#94a3b8', family: 'protenix' },
    { key: 'has_clash', label: 'Has Clash', color: '#ef4444', family: 'protenix' },
];

const CHART_BG = 'transparent';
const PLOT_BG = '#0f172a';
const FONT_COLOR = '#e2e8f0';
const GRID_COLOR = '#334155';
const AXIS_COLOR = '#94a3b8';
const MAX_ANALYTICS_DESIGNS = 1500;

const DEFAULT_PLOT_CONFIG = {
    responsive: true,
    displayModeBar: true,
    toImageButtonOptions: { format: 'svg' as const },
};

const LENS_DEFAULT_METRICS: Record<AnalysisLens, {
    custom2d: [string, string, string];
    custom3d: [string, string, string, string];
}> = {
    validation: {
        custom2d: ['plddt_overall', 'pae_overall', 'iptm'],
        custom3d: ['plddt_overall', 'iptm', 'pae_overall', 'conf_score'],
    },
    rfantibody: {
        custom2d: ['epitope_contact_count', 'epitope_min_distance', 'target_contact_count'],
        custom3d: ['epitope_contact_count', 'target_contact_count', 'epitope_min_distance', 'backbone_id'],
    },
    fampnn: {
        custom2d: ['fampnn_psce', 'plddt_overall', 'iptm'],
        custom3d: ['fampnn_psce', 'plddt_overall', 'iptm', 'conf_score'],
    },
    ppiflow: {
        custom2d: ['maturation_delta_interface', 'maturation_rmsd', 'maturation_interface_score'],
        custom3d: ['maturation_delta_interface', 'maturation_rmsd', 'maturation_interface_score', 'plddt_overall'],
    },
    frustrampnn: {
        custom2d: ['frustration_pct_high', 'frustration_high_count', 'plddt_overall'],
        custom3d: ['frustration_high_count', 'frustration_min_count', 'frustration_pct_high', 'iptm'],
    },
    protenix: {
        custom2d: ['protein_iptm', 'complex_iplddt', 'num_recycles'],
        custom3d: ['protein_iptm', 'complex_iplddt', 'complex_ipde', 'disorder'],
    },
};

function PlotCard({
    title,
    description,
    hasData,
    children,
    emptyMessage = 'No metric data available for this chart.',
}: {
    title: string;
    description?: string;
    hasData: boolean;
    children: React.ReactNode;
    emptyMessage?: string;
}) {
    const cardRef = useRef<HTMLDivElement | null>(null);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const fullscreenPlotClass = 'h-[calc(100vh-156px)] min-h-[520px] flex-1 [&_.js-plotly-plot]:!h-full [&_.js-plotly-plot]:!w-full [&_.plotly]:!h-full [&_.plotly]:!w-full [&_.plot-container]:!h-full [&_.plot-container]:!w-full [&_.svg-container]:!h-full [&_.svg-container]:!w-full [&_.main-svg]:!h-full [&_.gl-container]:!h-full';

    const toggleFullscreen = useCallback(() => {
        if (!cardRef.current) return;
        if (document.fullscreenElement === cardRef.current) {
            void document.exitFullscreen();
            return;
        }
        if (document.fullscreenElement) {
            void document.exitFullscreen().finally(() => {
                cardRef.current?.requestFullscreen().catch(() => {});
            });
            return;
        }
        cardRef.current.requestFullscreen().catch(() => {});
    }, []);

    useEffect(() => {
        const handleFullscreenChange = () => {
            const active = document.fullscreenElement === cardRef.current;
            setIsFullscreen(active);
            setTimeout(() => window.dispatchEvent(new Event('resize')), 40);
        };
        document.addEventListener('fullscreenchange', handleFullscreenChange);
        return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
    }, []);

    return (
        <div
            ref={cardRef}
            className={`${isFullscreen ? 'flex h-full w-full flex-col overflow-auto rounded-none border-0 bg-slate-950 p-6' : 'rounded-2xl border border-slate-700/60 bg-slate-900/55 p-4 shadow-xl shadow-slate-950/20'}`}
        >
            <div className="mb-3 flex items-start justify-between gap-4">
                <div>
                    <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
                    {description && <p className="mt-1 text-xs leading-5 text-slate-400">{description}</p>}
                </div>
                <div className="flex items-center gap-2">
                    {!hasData && (
                        <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-200">
                            No data
                        </span>
                    )}
                    {hasData && (
                        <button
                            type="button"
                            onClick={toggleFullscreen}
                            className="rounded-full border border-slate-700 bg-slate-900/80 px-2.5 py-1 text-[11px] font-medium text-slate-300 transition-colors hover:border-slate-600 hover:text-slate-100"
                            title={isFullscreen ? 'Exit fullscreen' : 'Open fullscreen'}
                        >
                            {isFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
                        </button>
                    )}
                </div>
            </div>
            {hasData ? (
                <div className={isFullscreen ? fullscreenPlotClass : ''}>
                    {children}
                </div>
            ) : (
                <div className="flex h-[300px] items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/40 px-6 text-center text-sm text-slate-500">
                    {emptyMessage}
                </div>
            )}
        </div>
    );
}

function SectionHeader({
    title,
    description,
    count,
    accentClass,
}: {
    title: string;
    description: string;
    count: number;
    accentClass: string;
}) {
    return (
        <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
                <h2 className="text-base font-semibold text-slate-100">{title}</h2>
                <p className="mt-1 max-w-3xl text-sm text-slate-400">{description}</p>
            </div>
            <span className={`rounded-full border px-3 py-1 text-xs font-medium ${accentClass}`}>
                {count} designs
            </span>
        </div>
    );
}

function toMetricLabel(key: string): string {
    return key
        .replace(/_mean$/i, ' Mean')
        .replace(/_min$/i, ' Min')
        .replace(/_max$/i, ' Max')
        .replace(/_n$/i, ' Count')
        .replace(/_/g, ' ')
        .replace(/\b\w/g, (token) => token.toUpperCase());
}

function hashColor(key: string): string {
    let hash = 0;
    for (let index = 0; index < key.length; index += 1) {
        hash = ((hash << 5) - hash) + key.charCodeAt(index);
    }
    const hue = Math.abs(hash) % 360;
    return `hsl(${hue}, 70%, 56%)`;
}

function inferMetricFamily(key: string): MetricFamily {
    const lower = key.toLowerCase();
    if (
        lower.startsWith('rfa_') ||
        lower.startsWith('epitope_') ||
        lower.startsWith('target_') ||
        lower.startsWith('screening_') ||
        lower.startsWith('hotspot_') ||
        lower.includes('hotspot') ||
        lower.includes('orientation') ||
        lower.includes('angle') ||
        lower.includes('tilt') ||
        lower.includes('rotation') ||
        lower.includes('backbone')
    ) {
        return 'rfantibody';
    }
    if (lower.includes('fampnn') || lower.includes('psce')) return 'fampnn';
    if (lower.includes('maturation') || lower.includes('interface_score_matured') || lower.includes('delta_interface')) return 'ppiflow';
    if (lower.includes('frustr')) return 'frustrampnn';
    if (
        lower.includes('protenix') ||
        lower.includes('disorder') ||
        lower.includes('recycle') ||
        lower.includes('clash') ||
        lower.includes('complex_ip') ||
        lower.includes('protein_iptm') ||
        lower.includes('ligand_iptm')
    ) {
        return 'protenix';
    }
    return 'dynamic';
}

function pickAvailableMetric(candidates: string[], availableMetricKeys: string[], fallbackIndex = 0): string {
    for (const candidate of candidates) {
        if (availableMetricKeys.includes(candidate)) {
            return candidate;
        }
    }
    return availableMetricKeys[Math.min(fallbackIndex, availableMetricKeys.length - 1)] || '';
}

function pearson(valuesX: number[], valuesY: number[]): number {
    const length = Math.min(valuesX.length, valuesY.length);
    if (length < 2) return 0;

    const xs = valuesX.slice(0, length);
    const ys = valuesY.slice(0, length);
    const meanX = xs.reduce((sum, value) => sum + value, 0) / length;
    const meanY = ys.reduce((sum, value) => sum + value, 0) / length;

    let numerator = 0;
    let deltaX = 0;
    let deltaY = 0;

    for (let index = 0; index < length; index += 1) {
        const centeredX = xs[index] - meanX;
        const centeredY = ys[index] - meanY;
        numerator += centeredX * centeredY;
        deltaX += centeredX * centeredX;
        deltaY += centeredY * centeredY;
    }

    const denominator = Math.sqrt(deltaX * deltaY);
    return denominator === 0 ? 0 : numerator / denominator;
}

export function AnalyticsDashboard({ designs, jobName, jobId, preferredAnalysisLens = 'auto', loadedDesignCount }: AnalyticsDashboardProps) {
    const [colorScale, setColorScale] = useState<ColorScaleName>('Viridis');
    const [analysisLensOverride, setAnalysisLensOverride] = useState<AnalysisLens | 'auto'>('auto');
    const [showAdvancedCharts, setShowAdvancedCharts] = useState(false);
    const sourceDesignCount = loadedDesignCount ?? designs.length;
    const isDesignSampled = designs.length < sourceDesignCount;
    const baseSortedDesigns = useMemo(
        () => [...designs].sort((left, right) => (right.plddt_overall ?? right.conf_score ?? 0) - (left.plddt_overall ?? left.conf_score ?? 0)),
        [designs],
    );
    const [selectedDesignId, setSelectedDesignId] = useState<string>('');
    const [custom2dX, setCustom2dX] = useState<string>('plddt_overall');
    const [custom2dY, setCustom2dY] = useState<string>('pae_overall');
    const [custom2dColor, setCustom2dColor] = useState<string>('iptm');
    const [custom3dX, setCustom3dX] = useState<string>('plddt_overall');
    const [custom3dY, setCustom3dY] = useState<string>('iptm');
    const [custom3dZ, setCustom3dZ] = useState<string>('pae_overall');
    const [custom3dColor, setCustom3dColor] = useState<string>('conf_score');
    const analyticsDesignIds = useMemo(() => designs.map((design) => design.id), [designs]);
    const analyticsDesignIdsKey = useMemo(() => analyticsDesignIds.join(','), [analyticsDesignIds]);

    const { data: plotlyMetricsData } = useQuery({
        queryKey: ['analytics-plotly-metrics', jobId, analyticsDesignIdsKey],
        queryFn: () => (
            jobId && analyticsDesignIds.length
                ? fetchDesignPlotlyMetrics(jobId, {
                    include_children: true,
                    limit: Math.min(analyticsDesignIds.length, MAX_ANALYTICS_DESIGNS),
                    design_ids: analyticsDesignIds,
                }).then((response) => response.data)
                : null
        ),
        enabled: !!jobId && analyticsDesignIds.length > 0,
        staleTime: 60_000,
    });

    const plotlyMetricsByDesign = useMemo(() => {
        const mapped = new Map<string, Record<string, number>>();
        for (const point of plotlyMetricsData?.points || []) {
            mapped.set(point.id, point.metrics || {});
        }
        return mapped;
    }, [plotlyMetricsData]);

    const metricOptions = useMemo(() => {
        const merged = new Map<string, MetricOption>();
        for (const option of CORE_METRICS) {
            merged.set(option.key, option);
        }
        for (const key of plotlyMetricsData?.metric_keys || []) {
            if (!merged.has(key)) {
                merged.set(key, {
                    key,
                    label: toMetricLabel(key),
                    color: hashColor(key),
                    family: inferMetricFamily(key),
                });
            }
        }
        return Array.from(merged.values());
    }, [plotlyMetricsData]);

    const metricLookup = useMemo(() => new Map(metricOptions.map((option) => [option.key, option])), [metricOptions]);

    const getMetricValue = (design: Design, key: string): number | null => {
        if (key === 'screening_passed') {
            if (!design.screening_reason) return null;
            return design.screening_reason.trim().toLowerCase() === 'passed' ? 1 : 0;
        }

        const direct = (design as unknown as Record<string, unknown>)[key];
        if (typeof direct === 'number' && Number.isFinite(direct)) return direct;
        if (typeof direct === 'boolean') return direct ? 1 : 0;

        const flattened = plotlyMetricsByDesign.get(design.id)?.[key];
        if (typeof flattened === 'number' && Number.isFinite(flattened)) return flattened;

        return null;
    };

    const metricCounts = useMemo(() => {
        const counts = new Map<string, number>();
        for (const option of metricOptions) {
            counts.set(option.key, 0);
        }
        for (const design of designs) {
            for (const option of metricOptions) {
                if (getMetricValue(design, option.key) != null) {
                    counts.set(option.key, (counts.get(option.key) || 0) + 1);
                }
            }
        }
        return counts;
    }, [designs, metricOptions, plotlyMetricsByDesign]);

    const hasMetricData = (key: string | null | undefined): key is string => !!key && (metricCounts.get(key) || 0) > 0;
    const getMetricLabel = (key: string) => metricLookup.get(key)?.label || toMetricLabel(key);
    const extractValues = (key: string) =>
        designs
            .map((design) => getMetricValue(design, key))
            .filter((value): value is number => value != null && Number.isFinite(value));
    const designLensById = useMemo(
        () => new Map(
            baseSortedDesigns.map((design) => [design.id, inferDesignAnalysisLens(design as unknown as Record<string, unknown>)]),
        ),
        [baseSortedDesigns],
    );

    const familyMetricKeys = useMemo(() => {
        const mapped: Record<Exclude<MetricFamily, 'dynamic'>, string[]> = {
            validation: [],
            rfantibody: [],
            fampnn: [],
            ppiflow: [],
            frustrampnn: [],
            protenix: [],
        };
        for (const option of metricOptions) {
            if (option.family === 'dynamic') continue;
            if (!hasMetricData(option.key)) continue;
            mapped[option.family].push(option.key);
        }
        return mapped;
    }, [metricOptions, metricCounts]);

    const familyDesignCounts = useMemo(() => {
        const counts: Record<Exclude<MetricFamily, 'dynamic'>, number> = {
            validation: 0,
            rfantibody: 0,
            fampnn: 0,
            ppiflow: 0,
            frustrampnn: 0,
            protenix: 0,
        };

        for (const design of designs) {
            const lens = designLensById.get(design.id);
            if (!lens) continue;
            counts[lens] += 1;
        }
        return counts;
    }, [designLensById, designs]);

    const availableMetricKeys = useMemo(
        () => metricOptions.filter((option) => hasMetricData(option.key)).map((option) => option.key),
        [metricOptions, metricCounts],
    );

    const rankedAvailableAnalysisLenses = useMemo(
        () => ANALYSIS_LENS_ORDER
            .filter((lens) => familyDesignCounts[lens] > 0)
            .sort((left, right) => (
                familyDesignCounts[right] - familyDesignCounts[left]
                || familyMetricKeys[right].length - familyMetricKeys[left].length
                || ANALYSIS_LENS_ORDER.indexOf(left) - ANALYSIS_LENS_ORDER.indexOf(right)
            )),
        [familyDesignCounts, familyMetricKeys],
    );

    const autoDetectedAnalysisLens = useMemo(() => {
        if (
            preferredAnalysisLens !== 'auto'
            && familyDesignCounts[preferredAnalysisLens] > 0
        ) {
            return preferredAnalysisLens;
        }
        return rankedAvailableAnalysisLenses[0] || 'validation';
    }, [familyDesignCounts, familyMetricKeys, preferredAnalysisLens, rankedAvailableAnalysisLenses]);

    const resolvedAnalysisLens = analysisLensOverride === 'auto' ? autoDetectedAnalysisLens : analysisLensOverride;

    const orderedLensSummary = useMemo(() => {
        if (!rankedAvailableAnalysisLenses.includes(resolvedAnalysisLens)) {
            return rankedAvailableAnalysisLenses;
        }
        return [
            resolvedAnalysisLens,
            ...rankedAvailableAnalysisLenses.filter((lens) => lens !== resolvedAnalysisLens),
        ];
    }, [rankedAvailableAnalysisLenses, resolvedAnalysisLens]);

    const lensPrioritizedDesigns = useMemo(() => {
        const matching = baseSortedDesigns.filter((design) => designLensById.get(design.id) === resolvedAnalysisLens);
        const others = baseSortedDesigns.filter((design) => designLensById.get(design.id) !== resolvedAnalysisLens);
        return matching.length ? [...matching, ...others] : baseSortedDesigns;
    }, [baseSortedDesigns, designLensById, resolvedAnalysisLens]);

    useEffect(() => {
        setAnalysisLensOverride('auto');
        setShowAdvancedCharts(false);
    }, [jobId, preferredAnalysisLens]);

    useEffect(() => {
        if (!lensPrioritizedDesigns.length) {
            if (selectedDesignId) {
                setSelectedDesignId('');
            }
            return;
        }
        if (!selectedDesignId || !baseSortedDesigns.some((design) => design.id === selectedDesignId)) {
            setSelectedDesignId(lensPrioritizedDesigns[0]?.id ?? '');
        }
    }, [baseSortedDesigns, lensPrioritizedDesigns, selectedDesignId]);

    const activeDesignId = selectedDesignId || lensPrioritizedDesigns[0]?.id || baseSortedDesigns[0]?.id || '';

    const { data: chainMetrics, isLoading: chainLoading } = useQuery({
        queryKey: ['analytics-chain-metrics', activeDesignId],
        queryFn: () => fetchChainMetrics(activeDesignId).then((response) => response.data),
        enabled: !!activeDesignId,
        staleTime: 60_000,
    });

    const { data: paeMatrix, isLoading: paeLoading } = useQuery({
        queryKey: ['analytics-pae-data', activeDesignId],
        queryFn: () => fetchPAEData(activeDesignId).then((response) => response.data),
        enabled: !!activeDesignId,
        staleTime: 60_000,
    });

    useEffect(() => {
        if (!availableMetricKeys.length) return;
        const defaults = LENS_DEFAULT_METRICS[resolvedAnalysisLens];
        setCustom2dX(pickAvailableMetric([defaults.custom2d[0], 'plddt_overall', 'conf_score'], availableMetricKeys, 0));
        setCustom2dY(pickAvailableMetric([defaults.custom2d[1], 'pae_overall', 'iptm'], availableMetricKeys, 1));
        setCustom2dColor(pickAvailableMetric([defaults.custom2d[2], 'iptm', 'protein_iptm', 'conf_score'], availableMetricKeys, 2));
        setCustom3dX(pickAvailableMetric([defaults.custom3d[0], defaults.custom2d[0], 'plddt_overall'], availableMetricKeys, 0));
        setCustom3dY(pickAvailableMetric([defaults.custom3d[1], defaults.custom2d[1], 'iptm'], availableMetricKeys, 1));
        setCustom3dZ(pickAvailableMetric([defaults.custom3d[2], defaults.custom2d[2], 'pae_overall'], availableMetricKeys, 2));
        setCustom3dColor(pickAvailableMetric([defaults.custom3d[3], 'conf_score', 'iptm'], availableMetricKeys, 3));
    }, [availableMetricKeys, resolvedAnalysisLens]);

    const firstAvailableKey = (...candidates: Array<string | null | undefined>) => {
        for (const candidate of candidates) {
            if (hasMetricData(candidate)) return candidate;
        }
        return null;
    };

    const firstMatchingKey = (matcher: RegExp) => {
        const option = metricOptions.find((item) => hasMetricData(item.key) && matcher.test(item.key));
        return option?.key ?? null;
    };

    const validatorAgreementKey = firstAvailableKey(
        'ipsae',
        'ip_sae',
        'ipsae_mean',
        'ip_sae_mean',
        'protein_iptm',
        'iptm',
    );
    const validatorRmsdKey = firstAvailableKey(
        'protenix_overall_rmsd',
        'boltz_overall_rmsd',
        'rmsd_overall',
    );
    const rfaOrientationX = firstMatchingKey(/orientation|tilt|rotation|angle|dihedral|azimuth/i);
    const rfaOrientationY = firstMatchingKey(/orientation_error|hotspot_alignment|pose_error|polar|angle/i);
    const fampnnSecondaryKey = firstAvailableKey(
        'fampnn_max_residue_psce',
        'fampnn_max_residue_psce_mean',
        'fampnn_min_residue_psce',
        'chain_avg_psce_mean',
        'mpnn_score',
    );
    const protenixDisorderKey = firstAvailableKey(
        'disorder',
        'full_disorder_prob_mean',
        'disorder_prob_mean_mean',
    );
    const protenixRecycleKey = firstAvailableKey('num_recycles');
    const lensPrimaryMetricKey = (() => {
        if (resolvedAnalysisLens === 'rfantibody') {
            return firstAvailableKey(
                'target_contact_count',
                'epitope_contact_count',
                'rfa_hotspot_covered_count',
                'rfa_hotspot_min_distance',
                'rfd_rog',
            );
        }
        if (resolvedAnalysisLens === 'fampnn') {
            return firstAvailableKey('fampnn_psce', 'mpnn_score');
        }
        if (resolvedAnalysisLens === 'ppiflow') {
            return firstAvailableKey('maturation_delta_interface', 'maturation_interface_score', 'maturation_rmsd');
        }
        if (resolvedAnalysisLens === 'frustrampnn') {
            return firstAvailableKey('frustration_pct_high', 'frustration_high_count', 'frustration_min_count');
        }
        if (resolvedAnalysisLens === 'protenix') {
            return firstAvailableKey('protein_iptm', 'complex_iplddt', 'complex_ipde', 'num_recycles');
        }
        return firstAvailableKey('conf_score', 'iptm', 'plddt_overall');
    })();
    const lensPrimaryMetricDescription = (() => {
        if (resolvedAnalysisLens === 'rfantibody') {
            return 'RFA review should lead with contact geometry, hotspot coverage, and RFA-native quality fields rather than validator-style per-residue traces.';
        }
        if (resolvedAnalysisLens === 'fampnn') {
            return 'Sequence triage should lead with PSCE and related sequence-quality summaries before structural validation exists.';
        }
        if (resolvedAnalysisLens === 'ppiflow') {
            return 'Maturation triage should lead with interface delta and structural drift, not generic confidence first.';
        }
        if (resolvedAnalysisLens === 'frustrampnn') {
            return 'FrustraMPNN triage should lead with high-frustration burden and low-frustration retention.';
        }
        if (resolvedAnalysisLens === 'protenix') {
            return 'Protenix jobs should lead with validator-native interface, disorder, recycle, and clash signals.';
        }
        return 'Validation jobs still lead with structural confidence and agreement metrics.';
    })();
    const rfaBackboneQualityKey = firstAvailableKey(
        'target_contact_count',
        'epitope_contact_count',
        'rfa_hotspot_covered_count',
        'rfd_rog',
    );
    const rfaHotspotDistanceKey = firstAvailableKey(
        'rfa_hotspot_min_distance',
        'rfa_hotspot_avg_min_distance',
        'epitope_centroid_distance',
    );
    const getLensPickerMetricLabel = (design: Design): string | null => {
        if (resolvedAnalysisLens === 'rfantibody') {
            const contacts = getMetricValue(design, 'target_contact_count') ?? getMetricValue(design, 'epitope_contact_count');
            const distance = getMetricValue(design, 'target_min_distance') ?? getMetricValue(design, 'epitope_min_distance');
            if (contacts != null || distance != null) {
                return `${contacts != null ? `${contacts.toFixed(0)} cts` : '—'}${distance != null ? ` • ${distance.toFixed(1)} A` : ''}`;
            }
        }
        if (lensPrimaryMetricKey) {
            const value = getMetricValue(design, lensPrimaryMetricKey);
            if (value != null) {
                return `${getMetricLabel(lensPrimaryMetricKey)} ${value.toFixed(2)}`;
            }
        }
        if (design.plddt_overall != null) return `pLDDT ${design.plddt_overall.toFixed(1)}`;
        if (design.conf_score != null) return `Confidence ${design.conf_score.toFixed(2)}`;
        return null;
    };

    const chainRegions = useMemo(() => {
        if (!chainMetrics) return [];

        const ordered = Object.entries(chainMetrics)
            .filter(([, metric]) => metric.type !== 'ligand')
            .sort(([leftId, leftMetric], [rightId, rightMetric]) => {
                const order = { protein: 0, dna: 1, rna: 2, ligand: 3 };
                return (order[leftMetric.type] ?? 4) - (order[rightMetric.type] ?? 4) || leftId.localeCompare(rightId);
            });

        let cursor = 0;
        return ordered.map(([chainId, metric], index) => {
            const start = cursor;
            cursor += metric.length;
            const colors = ['#60a5fa', '#2dd4bf', '#f59e0b', '#a78bfa'];
            return {
                id: chainId,
                label: `Chain ${chainId}`,
                type: metric.type,
                start,
                end: cursor,
                color: colors[index % colors.length],
            };
        });
    }, [chainMetrics]);

    const make2DLayout = (xKey: string, yKey: string, overrides: Partial<Layout> = {}): Partial<Layout> => ({
        paper_bgcolor: CHART_BG,
        plot_bgcolor: PLOT_BG,
        font: { color: FONT_COLOR, size: 11 },
        margin: { l: 58, r: 24, t: 24, b: 54 },
        hovermode: 'closest',
        xaxis: {
            title: { text: getMetricLabel(xKey), font: { color: AXIS_COLOR } },
            gridcolor: GRID_COLOR,
            color: AXIS_COLOR,
            zeroline: false,
        },
        yaxis: {
            title: { text: getMetricLabel(yKey), font: { color: AXIS_COLOR } },
            gridcolor: GRID_COLOR,
            color: AXIS_COLOR,
            zeroline: false,
        },
        ...overrides,
    });

    const make3DLayout = (xKey: string, yKey: string, zKey: string): Partial<Layout> => ({
        paper_bgcolor: CHART_BG,
        font: { color: FONT_COLOR, size: 11 },
        margin: { l: 0, r: 0, t: 18, b: 0 },
        scene: {
            bgcolor: PLOT_BG,
            xaxis: { title: { text: getMetricLabel(xKey) }, gridcolor: GRID_COLOR, color: AXIS_COLOR },
            yaxis: { title: { text: getMetricLabel(yKey) }, gridcolor: GRID_COLOR, color: AXIS_COLOR },
            zaxis: { title: { text: getMetricLabel(zKey) }, gridcolor: GRID_COLOR, color: AXIS_COLOR },
        },
    });

    const scatterCache = new Map<string, Data[]>();
    const categoryScatterCache = new Map<string, Data[]>();
    const histogramCache = new Map<string, Data[]>();
    const boxCache = new Map<string, Data[]>();
    const correlationCache = new Map<string, { labels: string[]; matrix: number[][] } | null>();
    const scatter3dCache = new Map<string, Data[]>();

    const buildScatter = (xKey: string, yKey: string, colorKey?: string | null): Data[] => {
        const cacheKey = `${xKey}::${yKey}::${colorKey || ''}::${colorScale}`;
        const cached = scatterCache.get(cacheKey);
        if (cached) return cached;

        const xValues: number[] = [];
        const yValues: number[] = [];
        const colors: number[] = [];
        const names: string[] = [];

        for (const design of designs) {
            const x = getMetricValue(design, xKey);
            const y = getMetricValue(design, yKey);
            if (x == null || y == null) continue;
            xValues.push(x);
            yValues.push(y);
            if (colorKey) {
                colors.push(getMetricValue(design, colorKey) ?? 0);
            }
            names.push(design.name);
        }

        if (!xValues.length) {
            scatterCache.set(cacheKey, []);
            return [];
        }

        const markerColor = colorKey ? colors : (metricLookup.get(yKey)?.color || '#60a5fa');

        const result = [{
            type: 'scatter',
            mode: 'markers',
            x: xValues,
            y: yValues,
            text: names,
            hovertemplate: [
                '<b>%{text}</b>',
                `${getMetricLabel(xKey)}: %{x:.3f}`,
                `${getMetricLabel(yKey)}: %{y:.3f}`,
                colorKey ? `${getMetricLabel(colorKey)}: %{marker.color:.3f}` : '',
                '<extra></extra>',
            ].filter(Boolean).join('<br>'),
            marker: {
                size: 9,
                opacity: 0.82,
                color: markerColor,
                colorscale: colorScale,
                showscale: !!colorKey,
                colorbar: colorKey ? { title: { text: getMetricLabel(colorKey), font: { color: FONT_COLOR } }, tickfont: { color: AXIS_COLOR } } : undefined,
                line: { color: '#ffffff18', width: 0.8 },
            },
        } as Data];
        scatterCache.set(cacheKey, result);
        return result;
    };

    const buildCategoryScatter = (
        categoryKey: string,
        yKey: string,
        colorKey?: string | null,
        categoryLabelFormatter?: (value: number) => string,
    ): Data[] => {
        const cacheKey = `${categoryKey}::${yKey}::${colorKey || ''}::${colorScale}::${categoryLabelFormatter ? 'fmt' : 'raw'}`;
        const cached = categoryScatterCache.get(cacheKey);
        if (cached) return cached;

        const points = designs
            .map((design) => {
                const categoryValue = getMetricValue(design, categoryKey);
                const yValue = getMetricValue(design, yKey);
                if (categoryValue == null || yValue == null) return null;
                const colorValue = colorKey ? (getMetricValue(design, colorKey) ?? 0) : null;
                return {
                    categoryValue,
                    xLabel: categoryLabelFormatter ? categoryLabelFormatter(categoryValue) : `${getMetricLabel(categoryKey)} ${Math.round(categoryValue)}`,
                    yValue,
                    colorValue,
                    name: design.name,
                };
            })
            .filter((point): point is {
                categoryValue: number;
                xLabel: string;
                yValue: number;
                colorValue: number | null;
                name: string;
            } => point != null)
            .sort((left, right) => left.categoryValue - right.categoryValue || left.yValue - right.yValue);

        if (!points.length) {
            categoryScatterCache.set(cacheKey, []);
            return [];
        }

        const result = [{
            type: 'scatter',
            mode: 'markers',
            x: points.map((point) => point.xLabel),
            y: points.map((point) => point.yValue),
            text: points.map((point) => point.name),
            hovertemplate: [
                '<b>%{text}</b>',
                `${getMetricLabel(categoryKey)}: %{x}`,
                `${getMetricLabel(yKey)}: %{y:.3f}`,
                colorKey ? `${getMetricLabel(colorKey)}: %{marker.color:.3f}` : '',
                '<extra></extra>',
            ].filter(Boolean).join('<br>'),
            marker: {
                size: 8,
                opacity: 0.78,
                color: colorKey ? points.map((point) => point.colorValue ?? 0) : (metricLookup.get(yKey)?.color || '#60a5fa'),
                colorscale: colorScale,
                showscale: !!colorKey,
                colorbar: colorKey ? { title: { text: getMetricLabel(colorKey), font: { color: FONT_COLOR } }, tickfont: { color: AXIS_COLOR } } : undefined,
                line: { color: '#ffffff18', width: 0.6 },
            },
        } as Data];
        categoryScatterCache.set(cacheKey, result);
        return result;
    };

    const buildHistogram = (key: string): Data[] => {
        const cached = histogramCache.get(key);
        if (cached) return cached;
        const values = extractValues(key);
        if (!values.length) {
            histogramCache.set(key, []);
            return [];
        }
        const result = [{
            type: 'histogram',
            x: values,
            nbinsx: Math.min(30, Math.max(10, Math.floor(Math.sqrt(values.length)))),
            marker: {
                color: metricLookup.get(key)?.color || '#60a5fa',
                line: { color: '#ffffff18', width: 1 },
                opacity: 0.88,
            },
            hovertemplate: `${getMetricLabel(key)}: %{x:.3f}<br>Count: %{y}<extra></extra>`,
        } as Data];
        histogramCache.set(key, result);
        return result;
    };

    const buildBoxByCategory = (categoryKey: string, valueKey: string): Data[] => {
        const cacheKey = `${categoryKey}::${valueKey}`;
        const cached = boxCache.get(cacheKey);
        if (cached) return cached;
        const grouped = new Map<string, number[]>();

        for (const design of designs) {
            const categoryValue = getMetricValue(design, categoryKey);
            const metricValue = getMetricValue(design, valueKey);
            if (categoryValue == null || metricValue == null) continue;
            const label = `${getMetricLabel(categoryKey)} ${Math.round(categoryValue)}`;
            const existing = grouped.get(label) || [];
            existing.push(metricValue);
            grouped.set(label, existing);
        }

        const result = Array.from(grouped.entries())
            .sort(([left], [right]) => left.localeCompare(right, undefined, { numeric: true }))
            .map(([label, values]) => ({
                type: 'box' as const,
                name: label,
                y: values,
                boxpoints: false as const,
                marker: { color: metricLookup.get(valueKey)?.color || '#60a5fa' },
                line: { color: metricLookup.get(valueKey)?.color || '#60a5fa' },
                hovertemplate: `${label}<br>${getMetricLabel(valueKey)}: %{y:.3f}<extra></extra>`,
            }));
        boxCache.set(cacheKey, result);
        return result;
    };

    const buildCorrelationMatrix = (keys: string[]) => {
        const cacheKey = keys.join('::');
        const cached = correlationCache.get(cacheKey);
        if (cached !== undefined) return cached;
        const activeKeys = keys.filter((key, index) => keys.indexOf(key) === index && hasMetricData(key));
        if (activeKeys.length < 2) {
            correlationCache.set(cacheKey, null);
            return null;
        }

        const matrix = activeKeys.map((xKey) => activeKeys.map((yKey) => {
            const pairs = designs
                .map((design) => [getMetricValue(design, xKey), getMetricValue(design, yKey)] as const)
                .filter((pair): pair is readonly [number, number] => pair[0] != null && pair[1] != null);
            if (pairs.length < 2) return 0;
            return pearson(
                pairs.map(([x]) => x),
                pairs.map(([, y]) => y),
            );
        }));

        const result = {
            labels: activeKeys.map((key) => getMetricLabel(key)),
            matrix,
        };
        correlationCache.set(cacheKey, result);
        return result;
    };

    const build3DScatter = (xKey: string, yKey: string, zKey: string, colorKey: string): Data[] => {
        const cacheKey = `${xKey}::${yKey}::${zKey}::${colorKey}::${colorScale}`;
        const cached = scatter3dCache.get(cacheKey);
        if (cached) return cached;
        const xValues: number[] = [];
        const yValues: number[] = [];
        const zValues: number[] = [];
        const colorValues: number[] = [];
        const names: string[] = [];

        for (const design of designs) {
            const x = getMetricValue(design, xKey);
            const y = getMetricValue(design, yKey);
            const z = getMetricValue(design, zKey);
            if (x == null || y == null || z == null) continue;
            xValues.push(x);
            yValues.push(y);
            zValues.push(z);
            colorValues.push(getMetricValue(design, colorKey) ?? 0);
            names.push(design.name);
        }

        if (!xValues.length) {
            scatter3dCache.set(cacheKey, []);
            return [];
        }

        const result = [{
            type: 'scatter3d',
            mode: 'markers',
            x: xValues,
            y: yValues,
            z: zValues,
            text: names,
            hovertemplate: [
                '<b>%{text}</b>',
                `${getMetricLabel(xKey)}: %{x:.3f}`,
                `${getMetricLabel(yKey)}: %{y:.3f}`,
                `${getMetricLabel(zKey)}: %{z:.3f}`,
                '<extra></extra>',
            ].join('<br>'),
            marker: {
                size: 5.5,
                opacity: 0.84,
                color: colorValues,
                colorscale: colorScale,
                showscale: true,
                colorbar: {
                    title: { text: getMetricLabel(colorKey), font: { color: FONT_COLOR } },
                    tickfont: { color: AXIS_COLOR },
                },
                line: { color: '#ffffff10', width: 0.5 },
            },
        } as Data];
        scatter3dCache.set(cacheKey, result);
        return result;
    };

    const validationCorrelation = showAdvancedCharts ? buildCorrelationMatrix([
        'plddt_overall',
        'pae_overall',
        'iptm',
        validatorAgreementKey || '',
        'conf_score',
        validatorRmsdKey || '',
    ].filter(Boolean)) : null;

    const crossFamilyCorrelation = showAdvancedCharts ? buildCorrelationMatrix([
        'epitope_contact_count',
        'fampnn_psce',
        'maturation_delta_interface',
        'frustration_pct_high',
        'disorder',
        validatorAgreementKey || '',
        validatorRmsdKey || '',
        'plddt_overall',
    ].filter(Boolean)) : null;

    const focusMeta = FAMILY_META[resolvedAnalysisLens];
    const focusDescription = analysisLensOverride === 'auto'
        ? `Auto-focused on ${focusMeta.title.toLowerCase()} from the current job context and detected output mix.`
        : `Manually pinned to ${focusMeta.title.toLowerCase()}. Auto currently prefers ${FAMILY_META[autoDetectedAnalysisLens].title.toLowerCase()}.`;
    const focusMetricPreview = familyMetricKeys[resolvedAnalysisLens].slice(0, 8);
    const structuralFollowupAvailable = (!!chainMetrics && Object.keys(chainMetrics).length > 0) || !!paeMatrix;
    const showStructuralFollowupSection = structuralFollowupAvailable
        && (resolvedAnalysisLens === 'validation' || resolvedAnalysisLens === 'protenix');

    if (!designs.length) {
        return (
            <div className="flex h-64 items-center justify-center rounded-2xl border border-slate-800 bg-slate-900/40 text-slate-500">
                No designs available for analysis.
            </div>
        );
    }

    return (
        <div className="space-y-8 p-4">
            <div className="rounded-2xl border border-slate-800 bg-slate-900/55 p-5 shadow-xl shadow-slate-950/20">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <h1 className="text-lg font-semibold text-slate-100">
                            Analytics Dashboard
                            {jobName ? <span className="font-normal text-slate-400"> - {jobName}</span> : null}
                        </h1>
                        <p className="mt-2 max-w-4xl text-sm text-slate-400">
                            Plotly analytics are now keyed off the flattened metric surface from the results API instead of a Boltz-only schema.
                            Existing RFA, FAMPNN, PPIFlow, FrustraMPNN, and Protenix signals render directly, and new numeric keys from stage outputs will appear in the custom lab without another dashboard rewrite.
                        </p>
                    </div>
                    <div className="flex items-center gap-3">
                        <button
                            type="button"
                            onClick={() => setShowAdvancedCharts((current) => !current)}
                            className={`rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
                                showAdvancedCharts
                                    ? 'border-violet-500/40 bg-violet-500/15 text-violet-100'
                                    : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:bg-slate-800'
                            }`}
                        >
                            {showAdvancedCharts ? 'Hide Advanced Charts' : 'Show Advanced Charts'}
                        </button>
                        <label className="text-xs font-medium uppercase tracking-wide text-slate-500" htmlFor="analytics-color-scale">
                            Color Scale
                        </label>
                        <select
                            id="analytics-color-scale"
                            value={colorScale}
                            onChange={(event) => setColorScale(event.target.value as ColorScaleName)}
                            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100"
                        >
                            {COLOR_SCALE_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>{option.label}</option>
                            ))}
                        </select>
                    </div>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                    {orderedLensSummary.map((family) => {
                        const meta = FAMILY_META[family];
                        const designCount = familyDesignCounts[family];
                        if (!designCount && !familyMetricKeys[family].length) return null;
                        return (
                            <button
                                key={family}
                                type="button"
                                onClick={() => setAnalysisLensOverride(family)}
                                className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${meta.accent} ${resolvedAnalysisLens === family ? 'ring-1 ring-white/30' : 'opacity-80 hover:opacity-100'}`}
                            >
                                {meta.title}: {designCount}
                            </button>
                        );
                    })}
                    <button
                        type="button"
                        onClick={() => setAnalysisLensOverride('auto')}
                        className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${analysisLensOverride === 'auto' ? 'border-slate-500 bg-slate-700 text-slate-100 ring-1 ring-white/20' : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:bg-slate-800'}`}
                    >
                        Auto Focus: {FAMILY_META[autoDetectedAnalysisLens].title}
                    </button>
                    <span className="rounded-full border border-slate-700 bg-slate-800/60 px-3 py-1 text-xs font-medium text-slate-300">
                        {designs.length} charted designs
                    </span>
                    {isDesignSampled && (
                        <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-xs font-medium text-amber-200">
                            Sampling {designs.length} of {sourceDesignCount} loaded designs
                        </span>
                    )}
                </div>
            </div>

            {showStructuralFollowupSection && (
            <section className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <SectionHeader
                        title="Per-Residue Validation Confidence"
                        description="Chain-resolved validator confidence and PAE for the selected design."
                        count={chainMetrics ? Object.keys(chainMetrics).length : 0}
                        accentClass="border-slate-700 bg-slate-800/70 text-slate-200"
                    />
                    <select
                        value={activeDesignId}
                        onChange={(event) => setSelectedDesignId(event.target.value)}
                        className="min-w-[18rem] rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100"
                    >
                        {lensPrioritizedDesigns.slice(0, 200).map((design) => (
                            <option key={design.id} value={design.id}>
                                {design.name}
                                {getLensPickerMetricLabel(design) ? ` (${getLensPickerMetricLabel(design)})` : ''}
                            </option>
                        ))}
                    </select>
                </div>

                <PlotCard
                    title="Per-Residue pLDDT Profile"
                    description="Chain-by-chain confidence curves for the selected design."
                    hasData={!!chainMetrics && Object.keys(chainMetrics).length > 0}
                    emptyMessage="No per-chain pLDDT traces are available for the selected design."
                >
                    {chainLoading ? (
                        <div className="flex h-[380px] items-center justify-center text-slate-400">Loading per-chain confidence...</div>
                    ) : (
                        <Plot
                            data={Object.entries(chainMetrics || {})
                                .filter(([, metric]) => metric.type !== 'ligand')
                                .sort(([leftId, leftMetric], [rightId, rightMetric]) => {
                                    const order = { protein: 0, dna: 1, rna: 2, ligand: 3 };
                                    return (order[leftMetric.type] ?? 4) - (order[rightMetric.type] ?? 4) || leftId.localeCompare(rightId);
                                })
                                .map(([chainId, metric], index) => ({
                                    type: 'scatter' as const,
                                    mode: 'lines',
                                    x: metric.residue_numbers ?? Array.from({ length: metric.length }, (_, value) => value + 1),
                                    y: metric.plddt,
                                    name: `Chain ${chainId} (${metric.type}, avg ${metric.avg_plddt?.toFixed(1) ?? 'n/a'})`,
                                    line: {
                                        width: 2.4,
                                        color: ['#60a5fa', '#2dd4bf', '#f59e0b', '#a78bfa'][index % 4],
                                        shape: 'spline' as const,
                                    },
                                    hovertemplate: `<b>Chain ${chainId}</b><br>Residue %{x}<br>pLDDT: %{y:.1f}<extra></extra>`,
                                })) as Data[]}
                            layout={{
                                ...make2DLayout('plddt_overall', 'plddt_overall', {
                                    xaxis: {
                                        title: { text: 'Residue Number', font: { color: AXIS_COLOR } },
                                        gridcolor: GRID_COLOR,
                                        color: AXIS_COLOR,
                                        zeroline: false,
                                    },
                                    yaxis: {
                                        title: { text: 'pLDDT', font: { color: AXIS_COLOR } },
                                        gridcolor: GRID_COLOR,
                                        color: AXIS_COLOR,
                                        range: [0, 100],
                                        dtick: 20,
                                        zeroline: false,
                                    },
                                    margin: { l: 60, r: 36, t: 20, b: 60 },
                                    legend: {
                                        orientation: 'h',
                                        y: -0.18,
                                        x: 0.5,
                                        xanchor: 'center',
                                        font: { size: 11, color: '#cbd5e1' },
                                    },
                                    shapes: [
                                        { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 90, y1: 100, fillcolor: '#1d4ed820', line: { width: 0 } },
                                        { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 70, y1: 90, fillcolor: '#0d948820', line: { width: 0 } },
                                        { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 50, y1: 70, fillcolor: '#ca8a0420', line: { width: 0 } },
                                        { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 0, y1: 50, fillcolor: '#dc262620', line: { width: 0 } },
                                    ],
                                }),
                                hovermode: 'x unified',
                            }}
                            config={DEFAULT_PLOT_CONFIG}
                            style={{ width: '100%', height: '380px' }}
                        />
                    )}
                </PlotCard>

                <PlotCard
                    title="Predicted Aligned Error"
                    description="PAE matrix for the selected design. Chain region bands are derived from the per-chain pLDDT payload when available."
                    hasData={!!paeMatrix}
                    emptyMessage="No PAE matrix is available for the selected design."
                >
                    {paeLoading ? (
                        <div className="flex h-[560px] items-center justify-center text-slate-400">Loading PAE matrix...</div>
                    ) : paeMatrix ? (
                        (() => {
                            const totalResidues = chainRegions.reduce((sum, region) => sum + (region.end - region.start), 0);
                            const scale = totalResidues > 0 ? paeMatrix.size / totalResidues : 1;
                            const scaledRegions = chainRegions.map((region) => ({
                                ...region,
                                start: region.start * scale,
                                end: region.end * scale,
                            }));

                            const dividerShapes: Partial<Shape>[] = scaledRegions.slice(1).flatMap((region) => ([
                                { type: 'line' as const, x0: region.start, x1: region.start, y0: 0, y1: paeMatrix.size, line: { color: '#ffffff', width: 1.5 } },
                                { type: 'line' as const, x0: 0, x1: paeMatrix.size, y0: region.start, y1: region.start, line: { color: '#ffffff', width: 1.5 } },
                            ]));

                            const bandShapes: Partial<Shape>[] = scaledRegions.flatMap((region) => ([
                                {
                                    type: 'rect' as const,
                                    x0: region.start,
                                    x1: region.end,
                                    y0: -8,
                                    y1: -2,
                                    fillcolor: region.color,
                                    line: { width: 0 },
                                    yref: 'y' as const,
                                },
                                {
                                    type: 'rect' as const,
                                    x0: -8,
                                    x1: -2,
                                    y0: region.start,
                                    y1: region.end,
                                    fillcolor: region.color,
                                    line: { width: 0 },
                                    xref: 'x' as const,
                                },
                            ]));

                            const annotations: Partial<Annotations>[] = scaledRegions.flatMap((region) => ([
                                {
                                    x: (region.start + region.end) / 2,
                                    y: paeMatrix.size + 14,
                                    text: `<b>${region.label}</b>`,
                                    showarrow: false,
                                    font: { size: 11, color: region.color },
                                },
                                {
                                    x: -18,
                                    y: (region.start + region.end) / 2,
                                    text: `<b>${region.label}</b>`,
                                    showarrow: false,
                                    font: { size: 11, color: region.color },
                                    textangle: '-90',
                                },
                            ]));

                            return (
                                <Plot
                                    data={[{
                                        type: 'heatmap',
                                        z: paeMatrix.pae_matrix,
                                        colorscale: [
                                            [0, '#0d1f2d'],
                                            [0.15, '#1d4f62'],
                                            [0.35, '#2f8b83'],
                                            [0.55, '#71bf93'],
                                            [0.75, '#d9e8b4'],
                                            [1, '#f6dcc8'],
                                        ],
                                        zmin: 0,
                                        zmax: 30,
                                        hovertemplate: 'Residue %{x} and Residue %{y}<br>PAE: %{z:.2f} A<extra></extra>',
                                        colorbar: {
                                            title: { text: 'PAE (A)', font: { color: FONT_COLOR } },
                                            tickfont: { color: AXIS_COLOR },
                                        },
                                    } as Data]}
                                    layout={{
                                        paper_bgcolor: CHART_BG,
                                        plot_bgcolor: PLOT_BG,
                                        font: { color: FONT_COLOR },
                                        margin: { l: 84, r: 80, t: 24, b: 84 },
                                        xaxis: {
                                            title: { text: 'Scored Residue', font: { color: AXIS_COLOR } },
                                            color: AXIS_COLOR,
                                            scaleanchor: 'y',
                                        },
                                        yaxis: {
                                            title: { text: 'Aligned Residue', font: { color: AXIS_COLOR } },
                                            color: AXIS_COLOR,
                                            autorange: 'reversed',
                                        },
                                        shapes: [...dividerShapes, ...bandShapes],
                                        annotations,
                                    }}
                                    config={DEFAULT_PLOT_CONFIG}
                                    style={{ width: '100%', height: '560px' }}
                                />
                            );
                        })()
                    ) : null}
                </PlotCard>
            </section>
            )}

            <section className="space-y-4">
                <SectionHeader
                    title={`Focused Analysis - ${focusMeta.title}`}
                    description={focusDescription}
                    count={familyDesignCounts[resolvedAnalysisLens] || designs.length}
                    accentClass={focusMeta.accent}
                />
                <div className="flex flex-wrap gap-2">
                    <button
                        type="button"
                        onClick={() => setAnalysisLensOverride('auto')}
                        className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${analysisLensOverride === 'auto' ? 'border-slate-500 bg-slate-700 text-slate-100' : 'border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800'}`}
                    >
                        Auto
                    </button>
                    {ANALYSIS_LENS_ORDER.filter((lens) => rankedAvailableAnalysisLenses.includes(lens)).map((lens) => (
                        <button
                            key={lens}
                            type="button"
                            onClick={() => setAnalysisLensOverride(lens)}
                            className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${resolvedAnalysisLens === lens ? FAMILY_META[lens].accent : 'border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800'}`}
                        >
                            {FAMILY_META[lens].title}
                        </button>
                    ))}
                </div>
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
                    <div className="rounded-2xl border border-slate-700/60 bg-slate-900/55 p-4 shadow-xl shadow-slate-950/20">
                        <div className="text-sm font-semibold text-slate-100">Current Lens</div>
                        <p className="mt-2 text-sm text-slate-300">{focusMeta.title}</p>
                        <p className="mt-2 text-xs leading-5 text-slate-400">
                            This lens now drives the top-of-tab posture. Non-validator stages no longer lead with generic per-residue confidence before their own native analytics.
                        </p>
                    </div>
                    <div className="rounded-2xl border border-slate-700/60 bg-slate-900/55 p-4 shadow-xl shadow-slate-950/20">
                        <div className="text-sm font-semibold text-slate-100">Detected Metrics</div>
                        {focusMetricPreview.length > 0 ? (
                            <div className="mt-3 flex flex-wrap gap-2">
                                {focusMetricPreview.map((key) => (
                                    <span key={key} className="rounded-full border border-slate-700 bg-slate-800/70 px-2.5 py-1 text-[11px] text-slate-300">
                                        {getMetricLabel(key)}
                                    </span>
                                ))}
                            </div>
                        ) : (
                            <p className="mt-2 text-xs leading-5 text-slate-500">
                                No lens-specific metrics are persisted yet for this family.
                            </p>
                        )}
                    </div>
                    <div className="rounded-2xl border border-slate-700/60 bg-slate-900/55 p-4 shadow-xl shadow-slate-950/20">
                        <div className="text-sm font-semibold text-slate-100">Primary Signal</div>
                        <p className="mt-2 text-sm text-slate-300">
                            {lensPrimaryMetricKey ? getMetricLabel(lensPrimaryMetricKey) : 'Lens-native metrics'}
                        </p>
                        <p className="mt-2 text-xs leading-5 text-slate-400">
                            {lensPrimaryMetricDescription}
                        </p>
                    </div>
                </div>
            </section>

            {familyDesignCounts.validation > 0 && (
            <section className="space-y-4">
                <SectionHeader
                    title={FAMILY_META.validation.title}
                    description={FAMILY_META.validation.description}
                    count={familyDesignCounts.validation}
                    accentClass={FAMILY_META.validation.accent}
                />
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                    <PlotCard
                        title="pLDDT vs PAE"
                        description="Top-level validation landscape, colored by interface confidence."
                        hasData={buildScatter('plddt_overall', 'pae_overall', firstAvailableKey('iptm', 'protein_iptm')).length > 0}
                    >
                        <Plot
                            data={buildScatter('plddt_overall', 'pae_overall', firstAvailableKey('iptm', 'protein_iptm'))}
                            layout={make2DLayout('plddt_overall', 'pae_overall')}
                            config={DEFAULT_PLOT_CONFIG}
                            style={{ width: '100%', height: '320px' }}
                        />
                    </PlotCard>

                    <PlotCard
                        title="Validator RMSD vs Interface Agreement"
                        description="Tracks structure agreement between sequence-filled outputs and the validator signal driving the downstream loop."
                        hasData={!!validatorRmsdKey && !!validatorAgreementKey && buildScatter(validatorRmsdKey, validatorAgreementKey, 'plddt_overall').length > 0}
                        emptyMessage="No paired RMSD and interface-agreement metrics were found. If ipSAE or validator RMSD starts being persisted, this card will populate automatically."
                    >
                        {validatorRmsdKey && validatorAgreementKey ? (
                            <Plot
                                data={buildScatter(validatorRmsdKey, validatorAgreementKey, 'plddt_overall')}
                                layout={make2DLayout(validatorRmsdKey, validatorAgreementKey)}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '320px' }}
                            />
                        ) : null}
                    </PlotCard>

                    <PlotCard
                        title="Confidence Distribution"
                        description="High-level quality distribution for the active validator outputs."
                        hasData={buildHistogram(firstAvailableKey('conf_score', 'iptm', 'protein_iptm') || 'conf_score').length > 0}
                    >
                        <Plot
                            data={buildHistogram(firstAvailableKey('conf_score', 'iptm', 'protein_iptm') || 'conf_score')}
                            layout={{
                                ...make2DLayout(firstAvailableKey('conf_score', 'iptm', 'protein_iptm') || 'conf_score', 'count', {
                                    yaxis: {
                                        title: { text: 'Count', font: { color: AXIS_COLOR } },
                                        gridcolor: GRID_COLOR,
                                        color: AXIS_COLOR,
                                        zeroline: false,
                                    },
                                }),
                            }}
                            config={DEFAULT_PLOT_CONFIG}
                            style={{ width: '100%', height: '320px' }}
                        />
                    </PlotCard>

                    {showAdvancedCharts && (
                        <PlotCard
                            title="Validation Correlation Matrix"
                            description="Cross-checks the main validation metrics that drive filtering and rerun decisions."
                            hasData={!!validationCorrelation}
                        >
                            {validationCorrelation ? (
                                <Plot
                                    data={[{
                                        type: 'heatmap',
                                        z: validationCorrelation.matrix,
                                        x: validationCorrelation.labels,
                                        y: validationCorrelation.labels,
                                        colorscale: 'RdBu',
                                        zmid: 0,
                                        zmin: -1,
                                        zmax: 1,
                                        hovertemplate: '%{x}<br>%{y}<br>r = %{z:.2f}<extra></extra>',
                                    } as Data]}
                                    layout={{
                                        paper_bgcolor: CHART_BG,
                                        plot_bgcolor: PLOT_BG,
                                        font: { color: FONT_COLOR, size: 11 },
                                        margin: { l: 110, r: 30, t: 18, b: 110 },
                                        xaxis: { tickangle: -40, color: AXIS_COLOR },
                                        yaxis: { color: AXIS_COLOR },
                                    }}
                                    config={DEFAULT_PLOT_CONFIG}
                                    style={{ width: '100%', height: '320px' }}
                                />
                            ) : null}
                        </PlotCard>
                    )}
                </div>
            </section>
            )}

            {familyDesignCounts.rfantibody > 0 && (
                <section className="space-y-4">
                    <SectionHeader
                        title={FAMILY_META.rfantibody.title}
                        description={FAMILY_META.rfantibody.description}
                        count={familyDesignCounts.rfantibody}
                        accentClass={FAMILY_META.rfantibody.accent}
                    />
                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
                        <PlotCard
                            title="Epitope Contacts vs Distance"
                            description="The coarse RFantibody screen lives here: more contacts and shorter distance move designs in the right direction."
                            hasData={buildScatter('epitope_contact_count', firstAvailableKey('epitope_min_atom_distance', 'epitope_min_distance') || 'epitope_min_distance', 'target_contact_count').length > 0}
                        >
                            <Plot
                                data={buildScatter('epitope_contact_count', firstAvailableKey('epitope_min_atom_distance', 'epitope_min_distance') || 'epitope_min_distance', 'target_contact_count')}
                                layout={make2DLayout('epitope_contact_count', firstAvailableKey('epitope_min_atom_distance', 'epitope_min_distance') || 'epitope_min_distance')}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '300px' }}
                            />
                        </PlotCard>

                        <PlotCard
                            title="Any-Target Contacts vs Epitope Distance"
                            description="Uses whole-target contact count on X, selected-epitope minimum distance on Y, and epitope contact count as the color channel."
                            hasData={buildScatter('target_contact_count', 'epitope_min_distance', 'epitope_contact_count').length > 0}
                        >
                            <Plot
                                data={buildScatter('target_contact_count', 'epitope_min_distance', 'epitope_contact_count')}
                                layout={make2DLayout('target_contact_count', 'epitope_min_distance')}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '300px' }}
                            />
                        </PlotCard>

                        <PlotCard
                            title="ID Families by Epitope Distance"
                            description="Per-family epitope minimum distance on Y, colored by epitope contact count, with simplified ID labels on X."
                            hasData={buildCategoryScatter('backbone_id', 'epitope_min_distance', 'epitope_contact_count', (value) => `ID ${Math.round(value)}`).length > 0}
                            emptyMessage="No backbone-grouped epitope distance/contact metrics are available for these designs."
                        >
                            <Plot
                                data={buildCategoryScatter('backbone_id', 'epitope_min_distance', 'epitope_contact_count', (value) => `ID ${Math.round(value)}`)}
                                layout={{
                                    ...make2DLayout('backbone_id', 'epitope_min_distance', {
                                        xaxis: {
                                            title: { text: 'ID', font: { color: AXIS_COLOR } },
                                            color: AXIS_COLOR,
                                            tickangle: -25,
                                        },
                                        yaxis: {
                                            title: { text: getMetricLabel('epitope_min_distance'), font: { color: AXIS_COLOR } },
                                            gridcolor: GRID_COLOR,
                                            color: AXIS_COLOR,
                                            zeroline: false,
                                        },
                                        showlegend: false,
                                        margin: { l: 58, r: 18, t: 18, b: 88 },
                                    }),
                                }}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '300px' }}
                            />
                        </PlotCard>

                        <PlotCard
                            title={rfaOrientationX && rfaOrientationY ? 'Orientation Envelope' : 'Hotspot Coverage vs Distance'}
                            description={rfaOrientationX && rfaOrientationY
                                ? 'Automatically binds to persisted RFA pose-orientation keys when those metrics exist.'
                                : 'Stage-native hotspot coverage and hotspot-distance metrics from RFantibody screening/TRB metadata.'}
                            hasData={rfaOrientationX && rfaOrientationY
                                ? buildScatter(rfaOrientationX, rfaOrientationY, 'epitope_contact_count').length > 0
                                : !!rfaHotspotDistanceKey && buildScatter('rfa_hotspot_covered_count', rfaHotspotDistanceKey, 'target_contact_count').length > 0}
                            emptyMessage={rfaOrientationX && rfaOrientationY
                                ? 'No orientation metrics are persisted yet. Keys like orientation score, tilt angle, or hotspot alignment error will light this card up once ingested.'
                                : 'No hotspot coverage and hotspot-distance metrics were found for these RFantibody backbones.'}
                        >
                            {rfaOrientationX && rfaOrientationY ? (
                                <Plot
                                    data={buildScatter(rfaOrientationX, rfaOrientationY, 'epitope_contact_count')}
                                    layout={make2DLayout(rfaOrientationX, rfaOrientationY)}
                                    config={DEFAULT_PLOT_CONFIG}
                                    style={{ width: '100%', height: '300px' }}
                                />
                            ) : rfaHotspotDistanceKey ? (
                                <Plot
                                    data={buildScatter('rfa_hotspot_covered_count', rfaHotspotDistanceKey, 'target_contact_count')}
                                    layout={make2DLayout('rfa_hotspot_covered_count', rfaHotspotDistanceKey)}
                                    config={DEFAULT_PLOT_CONFIG}
                                    style={{ width: '100%', height: '300px' }}
                                />
                            ) : null}
                        </PlotCard>
                    </div>
                </section>
            )}

            {familyDesignCounts.fampnn > 0 && (
                <section className="space-y-4">
                    <SectionHeader
                        title={FAMILY_META.fampnn.title}
                        description={FAMILY_META.fampnn.description}
                        count={familyDesignCounts.fampnn}
                        accentClass={FAMILY_META.fampnn.accent}
                    />
                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
                        <PlotCard
                            title="PSCE Distribution"
                            description="Lower PSCE should sit closer to the structural sweet spot before validation."
                            hasData={buildHistogram('fampnn_psce').length > 0}
                        >
                            <Plot
                                data={buildHistogram('fampnn_psce')}
                                layout={make2DLayout('fampnn_psce', 'count', {
                                    yaxis: {
                                        title: { text: 'Count', font: { color: AXIS_COLOR } },
                                        gridcolor: GRID_COLOR,
                                        color: AXIS_COLOR,
                                    },
                                })}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '300px' }}
                            />
                        </PlotCard>

                        <PlotCard
                            title="PSCE vs Validator Confidence"
                            description="Relates the FAMPNN sequence score to the confidence signal coming out of the validation loop."
                            hasData={buildScatter('fampnn_psce', firstAvailableKey('conf_score', 'plddt_overall') || 'plddt_overall', firstAvailableKey('iptm', 'protein_iptm')).length > 0}
                        >
                            <Plot
                                data={buildScatter('fampnn_psce', firstAvailableKey('conf_score', 'plddt_overall') || 'plddt_overall', firstAvailableKey('iptm', 'protein_iptm'))}
                                layout={make2DLayout('fampnn_psce', firstAvailableKey('conf_score', 'plddt_overall') || 'plddt_overall')}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '300px' }}
                            />
                        </PlotCard>

                        <PlotCard
                            title="PSCE vs Secondary Sequence Signal"
                            description="Hooks into flattened sidechain or residue-level FAMPNN summaries whenever those keys are present."
                            hasData={!!fampnnSecondaryKey && buildScatter('fampnn_psce', fampnnSecondaryKey, 'plddt_overall').length > 0}
                            emptyMessage="Only the aggregate PSCE is currently persisted for these designs."
                        >
                            {fampnnSecondaryKey ? (
                                <Plot
                                    data={buildScatter('fampnn_psce', fampnnSecondaryKey, 'plddt_overall')}
                                    layout={make2DLayout('fampnn_psce', fampnnSecondaryKey)}
                                    config={DEFAULT_PLOT_CONFIG}
                                    style={{ width: '100%', height: '300px' }}
                                />
                            ) : null}
                        </PlotCard>
                    </div>
                </section>
            )}

            {familyDesignCounts.ppiflow > 0 && (
                <section className="space-y-4">
                    <SectionHeader
                        title={FAMILY_META.ppiflow.title}
                        description={FAMILY_META.ppiflow.description}
                        count={familyDesignCounts.ppiflow}
                        accentClass={FAMILY_META.ppiflow.accent}
                    />
                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
                        <PlotCard
                            title="Delta Interface vs Maturation RMSD"
                            description="Shows whether interface improvement is coming with structural drift."
                            hasData={buildScatter('maturation_delta_interface', 'maturation_rmsd', 'maturation_interface_score').length > 0}
                        >
                            <Plot
                                data={buildScatter('maturation_delta_interface', 'maturation_rmsd', 'maturation_interface_score')}
                                layout={make2DLayout('maturation_delta_interface', 'maturation_rmsd')}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '300px' }}
                            />
                        </PlotCard>

                        <PlotCard
                            title="Matured Interface vs Validation Confidence"
                            description="Cross-links the PPIFlow repair step with the downstream validator view."
                            hasData={buildScatter('maturation_interface_score', firstAvailableKey('iptm', 'protein_iptm', 'conf_score') || 'conf_score', 'plddt_overall').length > 0}
                        >
                            <Plot
                                data={buildScatter('maturation_interface_score', firstAvailableKey('iptm', 'protein_iptm', 'conf_score') || 'conf_score', 'plddt_overall')}
                                layout={make2DLayout('maturation_interface_score', firstAvailableKey('iptm', 'protein_iptm', 'conf_score') || 'conf_score')}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '300px' }}
                            />
                        </PlotCard>

                        <PlotCard
                            title="Delta Interface Distribution"
                            description="Quick read on whether the maturation stage is moving the interface score in the desired direction."
                            hasData={buildHistogram('maturation_delta_interface').length > 0}
                        >
                            <Plot
                                data={buildHistogram('maturation_delta_interface')}
                                layout={make2DLayout('maturation_delta_interface', 'count', {
                                    yaxis: {
                                        title: { text: 'Count', font: { color: AXIS_COLOR } },
                                        gridcolor: GRID_COLOR,
                                        color: AXIS_COLOR,
                                    },
                                })}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '300px' }}
                            />
                        </PlotCard>
                    </div>
                </section>
            )}

            {familyDesignCounts.frustrampnn > 0 && (
                <section className="space-y-4">
                    <SectionHeader
                        title={FAMILY_META.frustrampnn.title}
                        description={FAMILY_META.frustrampnn.description}
                        count={familyDesignCounts.frustrampnn}
                        accentClass={FAMILY_META.frustrampnn.accent}
                    />
                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
                        <PlotCard
                            title="High Frustration Percent"
                            description="Distribution of highly frustrated residues across the selected set."
                            hasData={buildHistogram('frustration_pct_high').length > 0}
                        >
                            <Plot
                                data={buildHistogram('frustration_pct_high')}
                                layout={make2DLayout('frustration_pct_high', 'count', {
                                    yaxis: {
                                        title: { text: 'Count', font: { color: AXIS_COLOR } },
                                        gridcolor: GRID_COLOR,
                                        color: AXIS_COLOR,
                                    },
                                })}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '300px' }}
                            />
                        </PlotCard>

                        <PlotCard
                            title="High Frustration vs Confidence"
                            description="Checks whether local energetic frustration is showing up in the validator confidence envelope."
                            hasData={buildScatter('frustration_pct_high', 'plddt_overall', firstAvailableKey('iptm', 'protein_iptm')).length > 0}
                        >
                            <Plot
                                data={buildScatter('frustration_pct_high', 'plddt_overall', firstAvailableKey('iptm', 'protein_iptm'))}
                                layout={make2DLayout('frustration_pct_high', 'plddt_overall')}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '300px' }}
                            />
                        </PlotCard>

                        <PlotCard
                            title="High vs Minimal Frustration"
                            description="Separates designs with diffuse frustration from those retaining a healthier low-frustration footprint."
                            hasData={buildScatter('frustration_high_count', 'frustration_min_count', 'frustration_pct_high').length > 0}
                        >
                            <Plot
                                data={buildScatter('frustration_high_count', 'frustration_min_count', 'frustration_pct_high')}
                                layout={make2DLayout('frustration_high_count', 'frustration_min_count')}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '300px' }}
                            />
                        </PlotCard>
                    </div>
                </section>
            )}

            {familyDesignCounts.protenix > 0 && (
                <section className="space-y-4">
                    <SectionHeader
                        title={FAMILY_META.protenix.title}
                        description={FAMILY_META.protenix.description}
                        count={familyDesignCounts.protenix}
                        accentClass={FAMILY_META.protenix.accent}
                    />
                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
                        <PlotCard
                            title="Disorder vs Interface Quality"
                            description="Protenix-specific disorder signals against the interface metric that best matches the loaded outputs."
                            hasData={!!protenixDisorderKey && buildScatter(protenixDisorderKey, firstAvailableKey('protein_iptm', 'iptm', 'complex_iplddt') || 'iptm', protenixRecycleKey).length > 0}
                            emptyMessage="No paired disorder and interface metrics were found for these validator outputs."
                        >
                            {protenixDisorderKey ? (
                                <Plot
                                    data={buildScatter(protenixDisorderKey, firstAvailableKey('protein_iptm', 'iptm', 'complex_iplddt') || 'iptm', protenixRecycleKey)}
                                    layout={make2DLayout(protenixDisorderKey, firstAvailableKey('protein_iptm', 'iptm', 'complex_iplddt') || 'iptm')}
                                    config={DEFAULT_PLOT_CONFIG}
                                    style={{ width: '100%', height: '300px' }}
                                />
                            ) : null}
                        </PlotCard>

                        <PlotCard
                            title="Interface pLDDT vs Interface PDE"
                            description="Plots the validator's interface confidence against its predicted distance-error signal."
                            hasData={buildScatter('complex_iplddt', 'complex_ipde', firstAvailableKey('protein_iptm', 'iptm')).length > 0}
                        >
                            <Plot
                                data={buildScatter('complex_iplddt', 'complex_ipde', firstAvailableKey('protein_iptm', 'iptm'))}
                                layout={make2DLayout('complex_iplddt', 'complex_ipde')}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '300px' }}
                            />
                        </PlotCard>

                        <PlotCard
                            title="Recycle Count Distribution"
                            description="Tracks how hard Protenix is iterating before it settles on a solution."
                            hasData={!!protenixRecycleKey && buildHistogram(protenixRecycleKey).length > 0}
                            emptyMessage="Recycle counts were not persisted for these Protenix outputs."
                        >
                            {protenixRecycleKey ? (
                                <Plot
                                    data={buildHistogram(protenixRecycleKey)}
                                    layout={make2DLayout(protenixRecycleKey, 'count', {
                                        yaxis: {
                                            title: { text: 'Count', font: { color: AXIS_COLOR } },
                                            gridcolor: GRID_COLOR,
                                            color: AXIS_COLOR,
                                        },
                                    })}
                                    config={DEFAULT_PLOT_CONFIG}
                                    style={{ width: '100%', height: '300px' }}
                                />
                            ) : null}
                        </PlotCard>
                    </div>
                </section>
            )}

            {showAdvancedCharts && (
                <section className="space-y-4">
                    <SectionHeader
                        title="Custom Plotly Lab"
                        description="Use the flattened metric surface to explore any new RFA, validator, or downstream-model fields without touching the dashboard code again."
                        count={availableMetricKeys.length}
                        accentClass="border-slate-700 bg-slate-800/70 text-slate-200"
                    />
                    <div className="grid grid-cols-1 gap-4 2xl:grid-cols-2">
                        <PlotCard
                            title="Custom 2D Scatter"
                            description="Pick any two metric axes plus a color channel."
                            hasData={buildScatter(custom2dX, custom2dY, custom2dColor).length > 0}
                        >
                            <div className="mb-3 grid grid-cols-1 gap-2 md:grid-cols-4">
                                <select value={custom2dX} onChange={(event) => setCustom2dX(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100">
                                    {availableMetricKeys.map((key) => <option key={key} value={key}>{getMetricLabel(key)}</option>)}
                                </select>
                                <select value={custom2dY} onChange={(event) => setCustom2dY(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100">
                                    {availableMetricKeys.map((key) => <option key={key} value={key}>{getMetricLabel(key)}</option>)}
                                </select>
                                <select value={custom2dColor} onChange={(event) => setCustom2dColor(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100">
                                    {availableMetricKeys.map((key) => <option key={key} value={key}>{getMetricLabel(key)}</option>)}
                                </select>
                                <div className="rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2 text-xs text-slate-400">
                                    {buildScatter(custom2dX, custom2dY, custom2dColor).length ? 'Interactive Plotly scatter' : 'No overlapping values'}
                                </div>
                            </div>
                            <Plot
                                data={buildScatter(custom2dX, custom2dY, custom2dColor)}
                                layout={make2DLayout(custom2dX, custom2dY)}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '360px' }}
                            />
                        </PlotCard>

                        <PlotCard
                            title="Custom 3D Scatter"
                            description="Same dynamic metric surface, but with a Z axis for cross-family exploration."
                            hasData={build3DScatter(custom3dX, custom3dY, custom3dZ, custom3dColor).length > 0}
                        >
                            <div className="mb-3 grid grid-cols-1 gap-2 md:grid-cols-4">
                                <select value={custom3dX} onChange={(event) => setCustom3dX(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100">
                                    {availableMetricKeys.map((key) => <option key={key} value={key}>{getMetricLabel(key)}</option>)}
                                </select>
                                <select value={custom3dY} onChange={(event) => setCustom3dY(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100">
                                    {availableMetricKeys.map((key) => <option key={key} value={key}>{getMetricLabel(key)}</option>)}
                                </select>
                                <select value={custom3dZ} onChange={(event) => setCustom3dZ(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100">
                                    {availableMetricKeys.map((key) => <option key={key} value={key}>{getMetricLabel(key)}</option>)}
                                </select>
                                <select value={custom3dColor} onChange={(event) => setCustom3dColor(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100">
                                    {availableMetricKeys.map((key) => <option key={key} value={key}>{getMetricLabel(key)}</option>)}
                                </select>
                            </div>
                            <Plot
                                data={build3DScatter(custom3dX, custom3dY, custom3dZ, custom3dColor)}
                                layout={make3DLayout(custom3dX, custom3dY, custom3dZ)}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '420px' }}
                            />
                        </PlotCard>
                    </div>

                    <PlotCard
                        title="Cross-Family Correlation Matrix"
                        description="Summarizes how the major output families move together once metrics exist for more than one stage."
                        hasData={!!crossFamilyCorrelation}
                        emptyMessage="Not enough overlapping cross-family metrics are available yet."
                    >
                        {crossFamilyCorrelation ? (
                            <Plot
                                data={[{
                                    type: 'heatmap',
                                    z: crossFamilyCorrelation.matrix,
                                    x: crossFamilyCorrelation.labels,
                                    y: crossFamilyCorrelation.labels,
                                    colorscale: 'RdBu',
                                    zmid: 0,
                                    zmin: -1,
                                    zmax: 1,
                                    hovertemplate: '%{x}<br>%{y}<br>r = %{z:.2f}<extra></extra>',
                                } as Data]}
                                layout={{
                                    paper_bgcolor: CHART_BG,
                                    plot_bgcolor: PLOT_BG,
                                    font: { color: FONT_COLOR, size: 11 },
                                    margin: { l: 120, r: 30, t: 18, b: 120 },
                                    xaxis: { tickangle: -40, color: AXIS_COLOR },
                                    yaxis: { color: AXIS_COLOR },
                                }}
                                config={DEFAULT_PLOT_CONFIG}
                                style={{ width: '100%', height: '380px' }}
                            />
                        ) : null}
                    </PlotCard>
                </section>
            )}
        </div>
    );
}

export default AnalyticsDashboard;
