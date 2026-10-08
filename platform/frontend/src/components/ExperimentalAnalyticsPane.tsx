/**
 * ExperimentalAnalyticsPane - Plotly-powered advanced analytics (Experimental)
 * 
 * This is a separate, isolated tab that doesn't affect existing analytics.
 * Uses Plotly.js for interactive visualizations with more statistical depth.
 */

import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import Plot from 'react-plotly.js';
import type { Data, Layout } from 'plotly.js';
import { fetchAAComposition, fetchCDRLogos, fetchContactMap, fetchChainPairIptm, fetchDesignResidueMetrics, fetchPAEData, fetchChainMetrics, fetchDesignPlotlyMetrics, type ChainMetric } from '../lib/api';

interface Design {
    id: string;
    name: string;
    plddt_overall: number | null;
    plddt_binder: number | null;
    pae_overall: number | null;
    pae_interaction: number | null;
    ptm: number | null;
    iptm: number | null;
    protein_iptm?: number | null;
    ligand_iptm?: number | null;
    complex_iplddt?: number | null;
    complex_ipde?: number | null;
    disorder?: number | null;
    num_recycles?: number | null;
    has_clash?: boolean | null;
    confidence_metrics?: Record<string, unknown> | null;
    conf_score: number | null;
    affinity_score: number | null;
    binder_probability: number | null;
    rmsd_binder: number | null;
    rog: number | null;
    mpnn_score: number | null;
    cdr_h1_length?: number | null;
    cdr_h2_length?: number | null;
    cdr_h3_length?: number | null;
    binder_length?: number | null;
    epitope_contact_count?: number | null;
    backbone_id?: number | null;
}

interface ExperimentalAnalyticsPaneProps {
    designs: Design[];
    jobName?: string;
    jobId?: string;  // For server-side data fetching
}

// Available metrics for plotting
const NUMERIC_METRICS = [
    { key: 'plddt_overall', label: 'pLDDT (Overall)', color: '#60a5fa' },
    { key: 'plddt_binder', label: 'pLDDT (Binder)', color: '#3b82f6' },
    { key: 'pae_overall', label: 'PAE (Overall)', color: '#fbbf24' },
    { key: 'pae_interaction', label: 'PAE (Interaction)', color: '#f59e0b' },
    { key: 'ptm', label: 'pTM', color: '#a78bfa' },
    { key: 'iptm', label: 'iPTM', color: '#8b5cf6' },
    { key: 'protein_iptm', label: 'Protein iPTM', color: '#7c3aed' },
    { key: 'ligand_iptm', label: 'Ligand iPTM', color: '#14b8a6' },
    { key: 'complex_iplddt', label: 'Interface pLDDT', color: '#0ea5e9' },
    { key: 'complex_ipde', label: 'Interface PDE', color: '#f97316' },
    { key: 'disorder', label: 'Disorder', color: '#eab308' },
    { key: 'num_recycles', label: 'Num Recycles', color: '#a3a3a3' },
    { key: 'has_clash', label: 'Has Clash (0/1)', color: '#ef4444' },
    { key: 'conf_score', label: 'Confidence Score', color: '#34d399' },
    { key: 'affinity_score', label: 'Affinity Score', color: '#10b981' },
    { key: 'binder_probability', label: 'Binder Probability', color: '#22c55e' },
    { key: 'rmsd_binder', label: 'RMSD (Binder)', color: '#f472b6' },
    { key: 'rog', label: 'Radius of Gyration', color: '#ec4899' },
    { key: 'mpnn_score', label: 'MPNN Score', color: '#14b8a6' },
    { key: 'cdr_h3_length', label: 'CDR-H3 Length', color: '#f97316' },
    { key: 'binder_length', label: 'Binder Length', color: '#6366f1' },
    { key: 'epitope_contact_count', label: 'Epitope Contacts', color: '#84cc16' },
] as const;

type MetricKey = string;

// Preset analysis configurations
const PRESET_ANALYSES = [
    // Scatter plots
    { id: 'plddt_vs_pae', label: 'pLDDT vs PAE', xAxis: 'plddt_overall', yAxis: 'pae_overall', zAxis: null, colorBy: 'ptm', type: 'scatter' },
    { id: 'confidence_vs_iptm', label: 'Confidence vs iPTM', xAxis: 'conf_score', yAxis: 'iptm', zAxis: null, colorBy: 'plddt_overall', type: 'scatter' },
    { id: 'cdr_vs_plddt', label: 'CDR-H3 Length vs pLDDT', xAxis: 'cdr_h3_length', yAxis: 'plddt_overall', zAxis: null, colorBy: 'iptm', type: 'scatter' },
    // Distributions (match existing Analytics tab)
    { id: 'plddt_distribution', label: 'pLDDT Distribution', xAxis: 'plddt_overall', yAxis: null, zAxis: null, colorBy: null, type: 'histogram' },
    { id: 'pae_distribution', label: 'PAE Distribution', xAxis: 'pae_overall', yAxis: null, zAxis: null, colorBy: null, type: 'histogram' },
    { id: 'ptm_distribution', label: 'pTM Distribution', xAxis: 'ptm', yAxis: null, zAxis: null, colorBy: null, type: 'histogram' },
    { id: 'confidence_distribution', label: 'Confidence Distribution', xAxis: 'conf_score', yAxis: null, zAxis: null, colorBy: null, type: 'histogram' },
    { id: 'affinity_distribution', label: 'Affinity Distribution', xAxis: 'affinity_score', yAxis: null, zAxis: null, colorBy: null, type: 'histogram' },
    // Statistical views
    { id: 'correlation_matrix', label: 'Metric Correlation Matrix', xAxis: null, yAxis: null, zAxis: null, colorBy: null, type: 'heatmap' },
    { id: 'violin_confidence', label: 'Confidence Metrics (Violin)', xAxis: null, yAxis: null, zAxis: null, colorBy: null, type: 'violin' },
    { id: 'box_binding', label: 'Binding Metrics (Box)', xAxis: null, yAxis: null, zAxis: null, colorBy: null, type: 'box' },
    // Antibody-specific
    { id: 'cdr3d', label: 'CDR Lengths 3D (H1×H2×H3)', xAxis: 'cdr_h1_length', yAxis: 'cdr_h2_length', zAxis: 'cdr_h3_length', colorBy: 'plddt_overall', type: '3d' },
    { id: 'aa_composition', label: 'AA Composition (CDRs)', xAxis: null, yAxis: null, zAxis: null, colorBy: null, type: 'bar' },
    { id: 'sequence_logo', label: 'Sequence Logo (CDR-H3)', xAxis: null, yAxis: null, zAxis: null, colorBy: null, type: 'logo' },
    // DOE-focused 3D visualizations
    { id: 'scatter_3d_custom', label: '3D Scatter (Custom)', xAxis: 'plddt_overall', yAxis: 'iptm', zAxis: 'pae_overall', colorBy: 'conf_score', type: '3d_custom' },
    { id: 'quality_metrics_3d', label: 'Quality Metrics 3D', xAxis: 'plddt_overall', yAxis: 'iptm', zAxis: 'pae_overall', colorBy: 'ptm', type: '3d' },
    { id: 'binding_landscape_3d', label: 'Binding Landscape 3D', xAxis: 'affinity_score', yAxis: 'binder_probability', zAxis: 'iptm', colorBy: 'plddt_overall', type: '3d' },
    { id: 'structure_quality_3d', label: 'Structure Quality 3D', xAxis: 'plddt_overall', yAxis: 'rog', zAxis: 'mpnn_score', colorBy: 'pae_overall', type: '3d' },
    // DOE multi-factor analysis
    { id: 'parallel_coords', label: 'Parallel Coordinates', xAxis: null, yAxis: null, zAxis: null, colorBy: 'plddt_overall', type: 'parcoords' },
    { id: 'contour_quality', label: 'Contour: pLDDT vs PAE', xAxis: 'plddt_overall', yAxis: 'pae_overall', zAxis: 'iptm', colorBy: null, type: 'contour' },
    { id: 'contour_binding', label: 'Contour: Affinity vs iPTM', xAxis: 'affinity_score', yAxis: 'iptm', zAxis: 'plddt_overall', colorBy: null, type: 'contour' },
    // Phase 3a: Per-design structural visualizations
    { id: 'residue_plddt', label: 'Per-Residue pLDDT Profile', xAxis: null, yAxis: null, zAxis: null, colorBy: null, type: 'line' },
    { id: 'chain_plddt', label: 'Chain-by-Chain pLDDT', xAxis: null, yAxis: null, zAxis: null, colorBy: null, type: 'chain_line' },
    { id: 'pae_heatmap', label: 'PAE Heatmap', xAxis: null, yAxis: null, zAxis: null, colorBy: null, type: 'pae' },
    { id: 'chain_iptm', label: 'Chain-Pair iPTM Matrix', xAxis: null, yAxis: null, zAxis: null, colorBy: null, type: 'chain_heatmap' },
    { id: 'contact_map', label: 'Residue Contact Map', xAxis: null, yAxis: null, zAxis: null, colorBy: null, type: 'contact' },
] as const;

export function ExperimentalAnalyticsPane({ designs, jobName: _jobName, jobId }: ExperimentalAnalyticsPaneProps) {
    const [selectedPreset, setSelectedPreset] = useState<string>('plddt_vs_pae');
    const [xAxis, setXAxis] = useState<MetricKey>('plddt_overall');
    const [yAxis, setYAxis] = useState<MetricKey>('pae_overall');
    const [zAxis, setZAxis] = useState<MetricKey>('iptm');  // Phase 1: Z-axis for 3D
    const [colorBy, setColorBy] = useState<MetricKey | 'none'>('ptm');
    const [markerSize, setMarkerSize] = useState<number>(6);  // Phase 1: Adjustable marker size
    const [showCustom, setShowCustom] = useState(false);
    const [selectedCDR, setSelectedCDR] = useState<string>('CDR-H3');
    const [selectedDesignId, setSelectedDesignId] = useState<string | null>(designs[0]?.id || null);

    // Fetch server-side data for AA composition and sequence logos
    const { data: aaComposition, isLoading: aaLoading } = useQuery({
        queryKey: ['aa-composition', jobId],
        queryFn: () => jobId ? fetchAAComposition(jobId).then(r => r.data) : null,
        enabled: !!jobId && selectedPreset === 'aa_composition',
        staleTime: 60000,
    });

    const { data: cdrLogos, isLoading: logosLoading } = useQuery({
        queryKey: ['cdr-logos', jobId],
        queryFn: () => jobId ? fetchCDRLogos(jobId).then(r => r.data) : null,
        enabled: !!jobId && selectedPreset === 'sequence_logo',
        staleTime: 60000,
    });

    // Phase 3a: Fetch per-residue pLDDT for selected design
    const { data: residueData, isLoading: residueLoading } = useQuery({
        queryKey: ['residue-metrics', selectedDesignId],
        queryFn: () => selectedDesignId ? fetchDesignResidueMetrics(selectedDesignId).then(r => r.data) : null,
        enabled: !!selectedDesignId && selectedPreset === 'residue_plddt',
        staleTime: 60000,
    });

    // Phase 3a: Fetch contact map for selected design
    const { data: contactMapData, isLoading: contactMapLoading } = useQuery({
        queryKey: ['contact-map', selectedDesignId],
        queryFn: () => selectedDesignId ? fetchContactMap(selectedDesignId, 300).then(r => r.data) : null,
        enabled: !!selectedDesignId && selectedPreset === 'contact_map',
        staleTime: 60000,
    });

    // Phase 3a: Fetch chain-pair iPTM for selected design
    const { data: chainIptmData, isLoading: chainIptmLoading } = useQuery({
        queryKey: ['chain-iptm', selectedDesignId],
        queryFn: () => selectedDesignId ? fetchChainPairIptm(selectedDesignId).then(r => r.data) : null,
        enabled: !!selectedDesignId && selectedPreset === 'chain_iptm',
        staleTime: 60000,
    });

    // Phase 3a: Fetch PAE matrix for selected design
    const { data: paeData, isLoading: paeLoading } = useQuery({
        queryKey: ['pae-data', selectedDesignId],
        queryFn: () => selectedDesignId ? fetchPAEData(selectedDesignId).then(r => r.data) : null,
        enabled: !!selectedDesignId && selectedPreset === 'pae_heatmap',
        staleTime: 60000,
    });

    // Phase 3a: Fetch chain-by-chain metrics for selected design
    const { data: chainMetricsData, isLoading: chainMetricsLoading } = useQuery({
        queryKey: ['chain-metrics', selectedDesignId],
        queryFn: () => selectedDesignId ? fetchChainMetrics(selectedDesignId).then(r => r.data) : null,
        enabled: !!selectedDesignId && selectedPreset === 'chain_plddt',
        staleTime: 60000,
    });

    // Flattened numeric metrics for Plotly (includes raw confidence_metrics summaries)
    const { data: plotlyMetricsData } = useQuery({
        queryKey: ['plotly-metrics', jobId],
        queryFn: () => jobId ? fetchDesignPlotlyMetrics(jobId, { include_children: true, limit: 50000 }).then(r => r.data) : null,
        enabled: !!jobId,
        staleTime: 60000,
    });

    const plotlyMetricsByDesign = useMemo(() => {
        const byId = new Map<string, Record<string, number>>();
        (plotlyMetricsData?.points || []).forEach((point) => byId.set(point.id, point.metrics || {}));
        return byId;
    }, [plotlyMetricsData]);

    const metricOptions = useMemo(() => {
        const base = [...NUMERIC_METRICS] as Array<{ key: string; label: string; color: string }>;
        const existing = new Set(base.map((m) => m.key));
        const dynamicKeys = plotlyMetricsData?.metric_keys || [];

        const toLabel = (key: string) =>
            key
                .replace(/_mean$/i, ' (mean)')
                .replace(/_min$/i, ' (min)')
                .replace(/_max$/i, ' (max)')
                .replace(/_n$/i, ' (n)')
                .replace(/_/g, ' ')
                .replace(/\b\w/g, (c) => c.toUpperCase());

        const toColor = (key: string) => {
            let hash = 0;
            for (let i = 0; i < key.length; i += 1) hash = ((hash << 5) - hash) + key.charCodeAt(i);
            const hue = Math.abs(hash) % 360;
            return `hsl(${hue}, 70%, 55%)`;
        };

        dynamicKeys.forEach((key) => {
            if (existing.has(key)) return;
            existing.add(key);
            base.push({ key, label: toLabel(key), color: toColor(key) });
        });

        return base;
    }, [plotlyMetricsData]);

    const getMetricValue = (design: Design, key: string): number | null => {
        const direct = (design as unknown as Record<string, unknown>)[key];
        if (typeof direct === 'number' && Number.isFinite(direct)) return direct;
        if (typeof direct === 'boolean') return direct ? 1 : 0;
        const mapped = plotlyMetricsByDesign.get(design.id)?.[key];
        if (typeof mapped === 'number' && Number.isFinite(mapped)) return mapped;
        return null;
    };

    const getMetricLabel = (key: string): string =>
        metricOptions.find((m) => m.key === key)?.label || key;

    // Sort designs by relevant metric based on preset
    const sortedDesigns = useMemo(() => {
        const getSortKey = (): keyof Design | null => {
            switch (selectedPreset) {
                case 'residue_plddt': return 'plddt_overall';
                case 'chain_plddt': return 'plddt_overall';
                case 'pae_heatmap': return 'pae_overall';
                case 'contact_map': return 'plddt_overall';
                case 'chain_iptm': return 'iptm';
                default: return 'plddt_overall';
            }
        };
        const sortKey = getSortKey();
        if (!sortKey) return designs;

        return [...designs].sort((a, b) => {
            const aVal = a[sortKey];
            const bVal = b[sortKey];
            // For PAE, lower is better; for others, higher is better
            if (sortKey === 'pae_overall' || sortKey === 'pae_interaction') {
                // Lower PAE is better, nulls go to end
                if (aVal === null) return 1;
                if (bVal === null) return -1;
                return (aVal as number) - (bVal as number);
            } else {
                // Higher pLDDT/iPTM is better, nulls go to end
                if (aVal === null) return 1;
                if (bVal === null) return -1;
                return (bVal as number) - (aVal as number);
            }
        });
    }, [designs, selectedPreset]);

    // Extract numeric values from designs
    const extractValues = (key: MetricKey): number[] => {
        return designs
            .map((d) => getMetricValue(d, key))
            .filter((v): v is number => v != null && typeof v === 'number');
    };

    // Apply preset
    const handlePresetChange = (presetId: string) => {
        setSelectedPreset(presetId);
        const preset = PRESET_ANALYSES.find(p => p.id === presetId);
        if (preset) {
            if (preset.xAxis) setXAxis(preset.xAxis as MetricKey);
            if (preset.yAxis) setYAxis(preset.yAxis as MetricKey);
            if (preset.zAxis) setZAxis(preset.zAxis as MetricKey);  // Phase 1: 3D support
            if (preset.colorBy) setColorBy(preset.colorBy as MetricKey);
            // Only auto-enable custom mode for the custom 3D scatter
            setShowCustom(preset.type === '3d_custom');
        }
    };

    // Scatter plot data
    const scatterData = useMemo((): Data[] => {
        const preset = PRESET_ANALYSES.find(p => p.id === selectedPreset);
        if (preset?.type !== 'scatter') return [];

        const xVals: number[] = [];
        const yVals: number[] = [];
        const colorVals: number[] = [];
        const names: string[] = [];

        designs.forEach(d => {
            const x = getMetricValue(d, xAxis);
            const y = getMetricValue(d, yAxis);
            if (x != null && y != null) {
                xVals.push(x);
                yVals.push(y);
                if (colorBy !== 'none') {
                    const c = getMetricValue(d, colorBy);
                    colorVals.push(c ?? 0);
                }
                names.push(d.name);
            }
        });

        return [{
            type: 'scatter',
            mode: 'markers',
            x: xVals,
            y: yVals,
            text: names,
            hoverinfo: 'x+y+text' as const,
            marker: {
                size: 8,
                color: colorBy !== 'none' ? colorVals : '#60a5fa',
                colorscale: 'Viridis',
                showscale: colorBy !== 'none',
                colorbar: colorBy !== 'none' ? {
                    title: { text: getMetricLabel(colorBy) },
                    thickness: 15,
                    len: 0.5,
                } : undefined,
                opacity: 0.7,
            },
        }];
    }, [designs, xAxis, yAxis, colorBy, selectedPreset]);

    // Histogram data for distribution view
    const histogramData = useMemo((): Data[] => {
        const preset = PRESET_ANALYSES.find(p => p.id === selectedPreset);
        if (preset?.type !== 'histogram') return [];
        const values = extractValues(xAxis);
        return [{
            type: 'histogram',
            x: values,
            marker: { color: '#60a5fa' },
            nbinsx: 30,
        } as Data];
    }, [designs, xAxis, selectedPreset]);

    // Violin plot data for confidence metrics
    const violinData = useMemo((): Data[] => {
        const preset = PRESET_ANALYSES.find(p => p.id === selectedPreset);
        if (preset?.type !== 'violin') return [];

        const confidenceMetrics = [
            { key: 'plddt_overall', label: 'pLDDT', color: '#60a5fa' },
            { key: 'ptm', label: 'pTM', color: '#a78bfa' },
            { key: 'iptm', label: 'iPTM', color: '#8b5cf6' },
            { key: 'conf_score', label: 'Conf', color: '#34d399' },
        ];

        return confidenceMetrics.map(m => ({
            type: 'violin' as const,
            y: extractValues(m.key as MetricKey),
            name: m.label,
            box: { visible: true },
            meanline: { visible: true },
            line: { color: m.color },
            fillcolor: m.color + '40',
        }));
    }, [designs, selectedPreset]);

    // Box plot data for binding metrics
    const boxData = useMemo((): Data[] => {
        const preset = PRESET_ANALYSES.find(p => p.id === selectedPreset);
        if (preset?.type !== 'box') return [];

        const bindingMetrics = [
            { key: 'affinity_score', label: 'Affinity', color: '#10b981' },
            { key: 'binder_probability', label: 'Binder %', color: '#22c55e' },
            { key: 'iptm', label: 'iPTM', color: '#8b5cf6' },
            { key: 'pae_interaction', label: 'PAE Int', color: '#f59e0b' },
        ];

        return bindingMetrics.map(m => ({
            type: 'box' as const,
            y: extractValues(m.key as MetricKey),
            name: m.label,
            marker: { color: m.color },
            boxpoints: 'outliers' as const,
        }));
    }, [designs, selectedPreset]);

    // Dynamic 3D scatter data (supports any X/Y/Z metric combination)
    const scatter3DData = useMemo((): Data[] => {
        const preset = PRESET_ANALYSES.find(p => p.id === selectedPreset);
        // Support both '3d' and '3d_custom' types
        if (preset?.type !== '3d' && preset?.type !== '3d_custom') return [];

        const xVals: number[] = [];
        const yVals: number[] = [];
        const zVals: number[] = [];
        const colorVals: number[] = [];
        const names: string[] = [];

        // Use the current xAxis/yAxis/zAxis state for dynamic selection
        const xKey = xAxis;
        const yKey = yAxis;
        const zKey = zAxis;

        designs.forEach(d => {
            const x = getMetricValue(d, xKey);
            const y = getMetricValue(d, yKey);
            const z = getMetricValue(d, zKey);
            const col = getMetricValue(d, colorBy === 'none' ? 'plddt_overall' : colorBy);

            if (x != null && y != null && z != null) {
                xVals.push(x);
                yVals.push(y);
                zVals.push(z);
                colorVals.push(col ?? 0);
                names.push(d.name);
            }
        });

        if (xVals.length === 0) return [];

        // Get axis labels
        const xLabel = getMetricLabel(xKey);
        const yLabel = getMetricLabel(yKey);
        const zLabel = getMetricLabel(zKey);
        const colorLabel = getMetricLabel(colorBy === 'none' ? 'plddt_overall' : colorBy);

        return [{
            type: 'scatter3d',
            mode: 'markers',
            x: xVals,
            y: yVals,
            z: zVals,
            text: names,
            hovertemplate: `<b>%{text}</b><br>${xLabel}: %{x:.2f}<br>${yLabel}: %{y:.2f}<br>${zLabel}: %{z:.2f}<extra></extra>`,
            marker: {
                size: markerSize,
                color: colorVals,
                colorscale: 'Viridis',
                showscale: true,
                colorbar: {
                    title: { text: colorLabel },
                    thickness: 15,
                    len: 0.5,
                },
                opacity: 0.85,
            },
        }];
    }, [designs, xAxis, yAxis, zAxis, colorBy, markerSize, selectedPreset]);

    // Correlation matrix data
    const correlationData = useMemo(() => {
        if (selectedPreset !== 'correlation_matrix') return null;

        // Select metrics with enough data
        const metricsToCorrelate = NUMERIC_METRICS.filter(m => extractValues(m.key).length > 5);
        const n = metricsToCorrelate.length;

        if (n < 2) return null;

        // Build data matrix
        const dataMatrix: number[][] = metricsToCorrelate.map(m => extractValues(m.key));

        // Compute correlation matrix
        const corrMatrix: number[][] = [];
        for (let i = 0; i < n; i++) {
            corrMatrix[i] = [];
            for (let j = 0; j < n; j++) {
                corrMatrix[i][j] = pearsonCorrelation(dataMatrix[i], dataMatrix[j]);
            }
        }

        return {
            matrix: corrMatrix,
            labels: metricsToCorrelate.map(m => m.label),
        };
    }, [designs, selectedPreset]);

    // Simple Pearson correlation
    function pearsonCorrelation(x: number[], y: number[]): number {
        const n = Math.min(x.length, y.length);
        if (n < 2) return 0;

        const xSlice = x.slice(0, n);
        const ySlice = y.slice(0, n);

        const xMean = xSlice.reduce((a, b) => a + b, 0) / n;
        const yMean = ySlice.reduce((a, b) => a + b, 0) / n;

        let num = 0, denomX = 0, denomY = 0;
        for (let i = 0; i < n; i++) {
            const dx = xSlice[i] - xMean;
            const dy = ySlice[i] - yMean;
            num += dx * dy;
            denomX += dx * dx;
            denomY += dy * dy;
        }

        const denom = Math.sqrt(denomX * denomY);
        return denom === 0 ? 0 : num / denom;
    }

    // Parallel coordinates data - shows all designs across multiple metrics
    const parallelCoordsData = useMemo((): Data[] => {
        if (selectedPreset !== 'parallel_coords') return [];

        // Use metrics that have sufficient data
        const metricsForParallel = NUMERIC_METRICS.filter(m => extractValues(m.key).length > 3);
        if (metricsForParallel.length < 3) return [];

        // Build dimensions array for parcoords
        const dimensions = metricsForParallel.map(m => {
            const values = designs.map(d => {
                const val = getMetricValue(d, m.key);
                return typeof val === 'number' ? val : null;
            });
            const validValues = values.filter((v): v is number => v !== null);
            const minVal = validValues.length > 0 ? Math.min(...validValues) : 0;
            const maxVal = validValues.length > 0 ? Math.max(...validValues) : 1;

            return {
                label: m.label,
                values: values.map(v => v ?? minVal),  // Replace nulls with min
                range: [minVal, maxVal],
            };
        });

        // Color by the selected colorBy metric
        const colorMetric = colorBy === 'none' ? 'plddt_overall' : colorBy;
        const colorValues = designs.map(d => {
            const val = getMetricValue(d, colorMetric);
            return typeof val === 'number' ? val : 0;
        });

        return [{
            type: 'parcoords',
            line: {
                color: colorValues,
                colorscale: 'Viridis',
                showscale: true,
                colorbar: {
                    title: { text: getMetricLabel(colorMetric) },
                    thickness: 15,
                    len: 0.5,
                },
            },
            dimensions: dimensions,
        } as Data];
    }, [designs, colorBy, selectedPreset]);

    // Contour plot data - 2D density visualization for factor interactions
    const contourData = useMemo((): Data[] => {
        const preset = PRESET_ANALYSES.find(p => p.id === selectedPreset);
        if (preset?.type !== 'contour') return [];

        const xVals: number[] = [];
        const yVals: number[] = [];
        const zVals: number[] = [];

        designs.forEach(d => {
            const x = getMetricValue(d, xAxis);
            const y = getMetricValue(d, yAxis);
            const z = getMetricValue(d, zAxis);

            if (x != null && y != null && z != null) {
                xVals.push(x);
                yVals.push(y);
                zVals.push(z);
            }
        });

        if (xVals.length < 3) return [];

        // Create a 2D histogram contour (density-based)
        return [{
            type: 'histogram2dcontour',
            x: xVals,
            y: yVals,
            colorscale: 'Viridis',
            contours: {
                showlabels: true,
                labelfont: { color: 'white', size: 10 },
            },
            colorbar: {
                title: { text: 'Density', font: { color: '#e2e8f0' } },
                tickfont: { color: '#94a3b8' },
            },
            hovertemplate: `${getMetricLabel(xAxis)}: %{x:.2f}<br>${getMetricLabel(yAxis)}: %{y:.2f}<extra></extra>`,
        } as Data];
    }, [designs, xAxis, yAxis, zAxis, selectedPreset]);

    // Layout configuration
    const scatterLayout: Partial<Layout> = {
        title: {
            text: `${getMetricLabel(xAxis)} vs ${getMetricLabel(yAxis)}`,
            font: { color: '#e2e8f0', size: 16 },
        },
        xaxis: {
            title: { text: getMetricLabel(xAxis), font: { color: '#94a3b8' } },
            gridcolor: '#334155',
            color: '#94a3b8',
        },
        yaxis: {
            title: { text: getMetricLabel(yAxis), font: { color: '#94a3b8' } },
            gridcolor: '#334155',
            color: '#94a3b8',
        },
        paper_bgcolor: 'transparent',
        plot_bgcolor: '#1e293b',
        font: { color: '#e2e8f0' },
        margin: { l: 60, r: 40, t: 50, b: 50 },
        hovermode: 'closest',
    };

    const histogramLayout: Partial<Layout> = {
        title: {
            text: `Distribution: ${getMetricLabel(xAxis)}`,
            font: { color: '#e2e8f0', size: 16 },
        },
        xaxis: {
            title: { text: getMetricLabel(xAxis), font: { color: '#94a3b8' } },
            gridcolor: '#334155',
            color: '#94a3b8',
        },
        yaxis: {
            title: { text: 'Count', font: { color: '#94a3b8' } },
            gridcolor: '#334155',
            color: '#94a3b8',
        },
        paper_bgcolor: 'transparent',
        plot_bgcolor: '#1e293b',
        font: { color: '#e2e8f0' },
        margin: { l: 60, r: 40, t: 50, b: 50 },
        bargap: 0.05,
    };

    // Stats summary
    const stats = useMemo(() => {
        const xVals = extractValues(xAxis);
        const yVals = extractValues(yAxis);
        return {
            xCount: xVals.length,
            yCount: yVals.length,
            xMean: xVals.length ? (xVals.reduce((a, b) => a + b, 0) / xVals.length).toFixed(2) : '—',
            yMean: yVals.length ? (yVals.reduce((a, b) => a + b, 0) / yVals.length).toFixed(2) : '—',
        };
    }, [designs, xAxis, yAxis]);

    return (
        <div className="p-6 space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-xl font-bold text-white flex items-center gap-2">
                        <span className="text-2xl">📊</span>
                        Experimental Analytics
                        <span className="px-2 py-0.5 text-xs bg-amber-500/20 text-amber-400 rounded-full border border-amber-500/30">
                            BETA
                        </span>
                    </h2>
                    <p className="text-sm text-slate-400 mt-1">
                        Plotly-powered interactive visualizations • {designs.length} designs loaded
                    </p>
                </div>
            </div>

            {/* Controls */}
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
                <div className="flex flex-wrap items-center gap-4">
                    {/* Preset Selector */}
                    <div className="flex items-center gap-2">
                        <label className="text-sm text-slate-400">Analysis:</label>
                        <select
                            value={selectedPreset}
                            onChange={(e) => handlePresetChange(e.target.value)}
                            className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white"
                        >
                            {PRESET_ANALYSES.map(p => (
                                <option key={p.id} value={p.id}>{p.label}</option>
                            ))}
                        </select>
                    </div>

                    {/* Toggle Custom */}
                    <button
                        onClick={() => setShowCustom(!showCustom)}
                        className={`px-3 py-2 text-sm rounded-lg transition-colors ${showCustom
                            ? 'bg-blue-500/30 text-blue-400 border border-blue-500/40'
                            : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
                            }`}
                    >
                        {showCustom ? '✓ Custom Mode' : 'Customize...'}
                    </button>

                    {/* Custom Axis Selectors */}
                    {showCustom && selectedPreset !== 'correlation_matrix' && (
                        <>
                            <div className="flex items-center gap-2">
                                <label className="text-sm text-slate-400">X:</label>
                                <select
                                    value={xAxis}
                                    onChange={(e) => setXAxis(e.target.value as MetricKey)}
                                    className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white"
                                >
                                    {metricOptions.map(m => (
                                        <option key={m.key} value={m.key}>{m.label}</option>
                                    ))}
                                </select>
                            </div>
                            {selectedPreset !== 'affinity_distribution' && (
                                <>
                                    <div className="flex items-center gap-2">
                                        <label className="text-sm text-slate-400">Y:</label>
                                        <select
                                            value={yAxis}
                                            onChange={(e) => setYAxis(e.target.value as MetricKey)}
                                            className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white"
                                        >
                                            {metricOptions.map(m => (
                                                <option key={m.key} value={m.key}>{m.label}</option>
                                            ))}
                                        </select>
                                    </div>
                                    {/* Phase 1: Z-axis for 3D charts */}
                                    {(selectedPreset.includes('3d') || PRESET_ANALYSES.find(p => p.id === selectedPreset)?.type?.includes('3d')) && (
                                        <div className="flex items-center gap-2">
                                            <label className="text-sm text-slate-400">Z:</label>
                                            <select
                                                value={zAxis}
                                                onChange={(e) => setZAxis(e.target.value as MetricKey)}
                                                className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white"
                                            >
                                                {metricOptions.map(m => (
                                                    <option key={m.key} value={m.key}>{m.label}</option>
                                                ))}
                                            </select>
                                        </div>
                                    )}
                                    <div className="flex items-center gap-2">
                                        <label className="text-sm text-slate-400">Color:</label>
                                        <select
                                            value={colorBy}
                                            onChange={(e) => setColorBy(e.target.value as MetricKey | 'none')}
                                            className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white"
                                        >
                                            <option value="none">None</option>
                                            {metricOptions.map(m => (
                                                <option key={m.key} value={m.key}>{m.label}</option>
                                            ))}
                                        </select>
                                    </div>
                                    {/* Phase 1: Marker size for scatter/3D charts */}
                                    {(selectedPreset.includes('3d') || selectedPreset.includes('scatter') || PRESET_ANALYSES.find(p => p.id === selectedPreset)?.type === 'scatter') && (
                                        <div className="flex items-center gap-2">
                                            <label className="text-sm text-slate-400">Size:</label>
                                            <input
                                                type="range"
                                                min="2"
                                                max="15"
                                                value={markerSize}
                                                onChange={(e) => setMarkerSize(Number(e.target.value))}
                                                className="w-20 accent-blue-500"
                                            />
                                            <span className="text-xs text-slate-500 font-mono">{markerSize}</span>
                                        </div>
                                    )}
                                </>
                            )}
                        </>
                    )}
                </div>

                {/* Quick Stats */}
                {selectedPreset !== 'correlation_matrix' && (
                    <div className="mt-4 pt-4 border-t border-slate-700/50 flex gap-6 text-sm">
                        <div>
                            <span className="text-slate-500">X points: </span>
                            <span className="text-white font-mono">{stats.xCount}</span>
                            <span className="text-slate-500 ml-2">Mean: </span>
                            <span className="text-blue-400 font-mono">{stats.xMean}</span>
                        </div>
                        {selectedPreset !== 'affinity_distribution' && (
                            <div>
                                <span className="text-slate-500">Y points: </span>
                                <span className="text-white font-mono">{stats.yCount}</span>
                                <span className="text-slate-500 ml-2">Mean: </span>
                                <span className="text-emerald-400 font-mono">{stats.yMean}</span>
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* Chart Area */}
            <div className="bg-slate-900/50 rounded-xl border border-slate-700/50 overflow-hidden">
                {designs.length === 0 ? (
                    <div className="h-[500px] flex items-center justify-center text-slate-500">
                        No designs available for analysis
                    </div>
                ) : selectedPreset === 'cdr3d' ? (
                    scatter3DData.length > 0 ? (
                        <Plot
                            data={scatter3DData}
                            layout={{
                                title: {
                                    text: PRESET_ANALYSES.find(p => p.id === selectedPreset)?.label || '3D Scatter',
                                    font: { color: '#e2e8f0', size: 16 }
                                },
                                paper_bgcolor: 'transparent',
                                scene: {
                                    xaxis: { title: { text: getMetricLabel(xAxis) }, color: '#94a3b8', gridcolor: '#334155' },
                                    yaxis: { title: { text: getMetricLabel(yAxis) }, color: '#94a3b8', gridcolor: '#334155' },
                                    zaxis: { title: { text: getMetricLabel(zAxis) }, color: '#94a3b8', gridcolor: '#334155' },
                                    bgcolor: '#1e293b',
                                },
                                font: { color: '#e2e8f0' },
                                margin: { l: 0, r: 0, t: 50, b: 0 },
                            }}
                            config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: `3d_scatter_${selectedPreset}` } }}
                            style={{ width: '100%', height: '600px' }}
                        />
                    ) : (
                        <div className="h-[500px] flex items-center justify-center text-slate-500">
                            <div className="text-center">
                                <div className="text-2xl mb-2">📊</div>
                                No data available for selected metrics<br />
                                <span className="text-xs">Try different X/Y/Z axis combinations</span>
                            </div>
                        </div>
                    )
                ) : selectedPreset === 'correlation_matrix' ? (
                    correlationData ? (
                        <Plot
                            data={[{
                                type: 'heatmap',
                                z: correlationData.matrix,
                                x: correlationData.labels,
                                y: correlationData.labels,
                                colorscale: 'RdBu',
                                zmid: 0,
                                zmin: -1,
                                zmax: 1,
                                hoverongaps: false,
                                hovertemplate: '%{x}<br>%{y}<br>r = %{z:.3f}<extra></extra>',
                            } as Data]}
                            layout={{
                                title: { text: 'Metric Correlation Matrix', font: { color: '#e2e8f0', size: 16 } },
                                paper_bgcolor: 'transparent',
                                plot_bgcolor: '#1e293b',
                                font: { color: '#e2e8f0' },
                                margin: { l: 120, r: 40, t: 50, b: 120 },
                                xaxis: { tickangle: -45, color: '#94a3b8' },
                                yaxis: { color: '#94a3b8' },
                            }}
                            config={{ responsive: true, displayModeBar: true }}
                            style={{ width: '100%', height: '600px' }}
                        />
                    ) : (
                        <div className="h-[500px] flex items-center justify-center text-slate-500">
                            Not enough data for correlation matrix (need at least 2 metrics with 5+ data points)
                        </div>
                    )
                ) : PRESET_ANALYSES.find(p => p.id === selectedPreset)?.type === 'histogram' ? (
                    <Plot
                        data={histogramData}
                        layout={histogramLayout}
                        config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: `distribution_${xAxis}` } }}
                        style={{ width: '100%', height: '500px' }}
                    />
                ) : selectedPreset === 'violin_confidence' ? (
                    <Plot
                        data={violinData}
                        layout={{
                            title: { text: 'Confidence Metrics Distribution', font: { color: '#e2e8f0', size: 16 } },
                            paper_bgcolor: 'transparent',
                            plot_bgcolor: '#1e293b',
                            font: { color: '#e2e8f0' },
                            margin: { l: 60, r: 40, t: 50, b: 50 },
                            yaxis: { title: { text: 'Value', font: { color: '#94a3b8' } }, gridcolor: '#334155', color: '#94a3b8' },
                            showlegend: false,
                        }}
                        config={{ responsive: true, displayModeBar: true }}
                        style={{ width: '100%', height: '500px' }}
                    />
                ) : selectedPreset === 'box_binding' ? (
                    <Plot
                        data={boxData}
                        layout={{
                            title: { text: 'Binding Metrics Distribution', font: { color: '#e2e8f0', size: 16 } },
                            paper_bgcolor: 'transparent',
                            plot_bgcolor: '#1e293b',
                            font: { color: '#e2e8f0' },
                            margin: { l: 60, r: 40, t: 50, b: 50 },
                            yaxis: { title: { text: 'Value', font: { color: '#94a3b8' } }, gridcolor: '#334155', color: '#94a3b8' },
                            showlegend: false,
                        }}
                        config={{ responsive: true, displayModeBar: true }}
                        style={{ width: '100%', height: '500px' }}
                    />
                ) : PRESET_ANALYSES.find(p => p.id === selectedPreset)?.type?.includes('3d') ? (
                    scatter3DData.length > 0 ? (
                        <Plot
                            data={scatter3DData}
                            layout={{
                                title: {
                                    text: PRESET_ANALYSES.find(p => p.id === selectedPreset)?.label || '3D Scatter',
                                    font: { color: '#e2e8f0', size: 16 }
                                },
                                paper_bgcolor: 'transparent',
                                scene: {
                                    xaxis: { title: { text: getMetricLabel(xAxis) }, color: '#94a3b8', gridcolor: '#334155' },
                                    yaxis: { title: { text: getMetricLabel(yAxis) }, color: '#94a3b8', gridcolor: '#334155' },
                                    zaxis: { title: { text: getMetricLabel(zAxis) }, color: '#94a3b8', gridcolor: '#334155' },
                                    bgcolor: '#1e293b',
                                },
                                font: { color: '#e2e8f0' },
                                margin: { l: 0, r: 0, t: 50, b: 0 },
                            }}
                            config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: `3d_scatter_${selectedPreset}` } }}
                            style={{ width: '100%', height: '600px' }}
                        />
                    ) : (
                        <div className="h-[500px] flex items-center justify-center text-slate-500">
                            <div className="text-center">
                                <div className="text-2xl mb-2">📊</div>
                                No data available for selected metrics<br />
                                <span className="text-xs">Try different X/Y/Z axis combinations</span>
                            </div>
                        </div>
                    )
                ) : selectedPreset === 'parallel_coords' ? (
                    parallelCoordsData.length > 0 ? (
                        <Plot
                            data={parallelCoordsData}
                            layout={{
                                title: { text: 'Parallel Coordinates: Multi-Factor Analysis', font: { color: '#e2e8f0', size: 16 } },
                                paper_bgcolor: 'transparent',
                                plot_bgcolor: '#1e293b',
                                font: { color: '#e2e8f0' },
                                margin: { l: 80, r: 80, t: 50, b: 30 },
                            }}
                            config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: 'parallel_coords' } }}
                            style={{ width: '100%', height: '600px' }}
                        />
                    ) : (
                        <div className="h-[500px] flex items-center justify-center text-slate-500">
                            Not enough metrics with data for parallel coordinates (need at least 3 metrics with 4+ values)
                        </div>
                    )
                ) : PRESET_ANALYSES.find(p => p.id === selectedPreset)?.type === 'contour' ? (
                    contourData.length > 0 ? (
                        <Plot
                            data={contourData}
                            layout={{
                                title: {
                                    text: PRESET_ANALYSES.find(p => p.id === selectedPreset)?.label || 'Contour Plot',
                                    font: { color: '#e2e8f0', size: 16 }
                                },
                                paper_bgcolor: 'transparent',
                                plot_bgcolor: '#1e293b',
                                font: { color: '#e2e8f0' },
                                margin: { l: 60, r: 80, t: 50, b: 60 },
                                xaxis: {
                                    title: { text: getMetricLabel(xAxis), font: { color: '#94a3b8' } },
                                    gridcolor: '#334155',
                                    color: '#94a3b8',
                                },
                                yaxis: {
                                    title: { text: getMetricLabel(yAxis), font: { color: '#94a3b8' } },
                                    gridcolor: '#334155',
                                    color: '#94a3b8',
                                },
                            }}
                            config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: `contour_${selectedPreset}` } }}
                            style={{ width: '100%', height: '550px' }}
                        />
                    ) : (
                        <div className="h-[500px] flex items-center justify-center text-slate-500">
                            Not enough data for contour plot (need at least 3 data points with valid X/Y values)
                        </div>
                    )
                ) : selectedPreset === 'aa_composition' ? (
                    aaLoading ? (
                        <div className="h-[500px] flex items-center justify-center text-slate-400">
                            Loading amino acid composition...
                        </div>
                    ) : aaComposition && aaComposition.overall.length > 0 ? (
                        <Plot
                            data={[{
                                type: 'bar',
                                x: aaComposition.overall.map(aa => aa.aa),
                                y: aaComposition.overall.map(aa => aa.frequency * 100),
                                marker: {
                                    color: aaComposition.overall.map((_, i) =>
                                        `hsl(${(i * 18) % 360}, 70%, 50%)`
                                    ),
                                },
                                hovertemplate: '%{x}: %{y:.1f}%<extra></extra>',
                            }]}
                            layout={{
                                title: { text: 'Amino Acid Composition (All CDRs)', font: { color: '#e2e8f0', size: 16 } },
                                paper_bgcolor: 'transparent',
                                plot_bgcolor: '#1e293b',
                                font: { color: '#e2e8f0' },
                                margin: { l: 60, r: 40, t: 50, b: 60 },
                                xaxis: {
                                    title: { text: 'Amino Acid', font: { color: '#94a3b8' } },
                                    color: '#94a3b8',
                                    tickfont: { size: 14 },
                                },
                                yaxis: {
                                    title: { text: 'Frequency (%)', font: { color: '#94a3b8' } },
                                    gridcolor: '#334155',
                                    color: '#94a3b8',
                                },
                            }}
                            config={{ responsive: true, displayModeBar: true }}
                            style={{ width: '100%', height: '500px' }}
                        />
                    ) : (
                        <div className="h-[500px] flex items-center justify-center text-slate-500">
                            No CDR sequence data available (requires antibody designs with annotated CDRs)
                        </div>
                    )
                ) : selectedPreset === 'sequence_logo' ? (
                    logosLoading ? (
                        <div className="h-[500px] flex items-center justify-center text-slate-400">
                            Loading sequence logo data...
                        </div>
                    ) : cdrLogos && cdrLogos.logos.length > 0 ? (
                        (() => {
                            const logo = cdrLogos.logos.find(l => l.cdr_name === selectedCDR) || cdrLogos.logos[0];
                            if (!logo) return <div className="h-[500px] flex items-center justify-center text-slate-500">No logo data</div>;

                            const aas = 'ACDEFGHIKLMNPQRSTVWY'.split('');
                            const traces: Data[] = aas.map((aa, idx) => ({
                                type: 'bar' as const,
                                name: aa,
                                x: logo.positions.map(p => p.position),
                                y: logo.positions.map(p => (p.frequencies[aa] || 0) * 2),  // Scale for visibility
                                marker: { color: `hsl(${idx * 18}, 70%, 50%)` },
                                hovertemplate: `${aa}: %{customdata:.1f}%<extra></extra>`,
                                customdata: logo.positions.map(p => (p.frequencies[aa] || 0) * 100),
                            }));

                            return (
                                <div className="space-y-3">
                                    <div className="flex gap-2 px-4">
                                        {cdrLogos.logos.map(l => (
                                            <button
                                                key={l.cdr_name}
                                                onClick={() => setSelectedCDR(l.cdr_name)}
                                                className={`px-3 py-1 rounded text-sm ${selectedCDR === l.cdr_name
                                                    ? 'bg-blue-600 text-white'
                                                    : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                                                    }`}
                                            >
                                                {l.cdr_name} ({l.sequence_count})
                                            </button>
                                        ))}
                                    </div>
                                    <Plot
                                        data={traces}
                                        layout={{
                                            title: {
                                                text: `Sequence Logo: ${logo.cdr_name} (n=${logo.sequence_count})`,
                                                font: { color: '#e2e8f0', size: 16 },
                                            },
                                            paper_bgcolor: 'transparent',
                                            plot_bgcolor: '#1e293b',
                                            font: { color: '#e2e8f0' },
                                            margin: { l: 60, r: 40, t: 60, b: 60 },
                                            barmode: 'stack',
                                            xaxis: {
                                                title: { text: 'Position', font: { color: '#94a3b8' } },
                                                tickmode: 'linear',
                                                dtick: 1,
                                                color: '#94a3b8',
                                            },
                                            yaxis: {
                                                title: { text: 'Bits (scaled)', font: { color: '#94a3b8' } },
                                                gridcolor: '#334155',
                                                color: '#94a3b8',
                                            },
                                            showlegend: true,
                                            legend: { orientation: 'h', y: -0.15, font: { size: 10 } },
                                            annotations: [{
                                                x: 0.5,
                                                y: -0.25,
                                                xref: 'paper',
                                                yref: 'paper',
                                                text: `Consensus: ${logo.consensus}`,
                                                showarrow: false,
                                                font: { family: 'monospace', size: 14, color: '#60a5fa' },
                                            }],
                                        }}
                                        config={{ responsive: true, displayModeBar: true }}
                                        style={{ width: '100%', height: '500px' }}
                                    />
                                </div>
                            );
                        })()
                    ) : (
                        <div className="h-[500px] flex items-center justify-center text-slate-500">
                            No CDR sequence data available (requires antibody designs with annotated CDRs)
                        </div>
                    )
                ) : selectedPreset === 'residue_plddt' ? (
                    /* Phase 3a: Per-Residue pLDDT Profile */
                    <div className="space-y-3">
                        <div className="flex items-center gap-4 px-4 pt-4">
                            <label className="text-sm text-slate-400">Select Design:</label>
                            <select
                                value={selectedDesignId || ''}
                                onChange={(e) => setSelectedDesignId(e.target.value)}
                                className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white max-w-xs"
                            >
                                {sortedDesigns.map(d => (
                                    <option key={d.id} value={d.id}>
                                        {d.name} {d.plddt_overall ? `(${d.plddt_overall.toFixed(1)})` : ''}
                                    </option>
                                ))}
                            </select>
                        </div>
                        {residueLoading ? (
                            <div className="h-[450px] flex items-center justify-center text-slate-400">
                                Loading per-residue data...
                            </div>
                        ) : residueData ? (
                            <Plot
                                data={[{
                                    type: 'scatter',
                                    mode: 'lines+markers',
                                    x: residueData.residue_numbers,
                                    y: residueData.plddt,
                                    name: 'pLDDT',
                                    line: { color: '#60a5fa', width: 1 },
                                    marker: {
                                        size: 3, color: residueData.plddt.map((v: number) =>
                                            v >= 90 ? '#3b82f6' : v >= 70 ? '#22d3ee' : v >= 50 ? '#fbbf24' : '#f87171'
                                        )
                                    },
                                    hovertemplate: 'Residue %{x}<br>pLDDT: %{y:.1f}<extra></extra>',
                                }] as Data[]}
                                layout={{
                                    title: { text: `Per-Residue pLDDT: ${residueData.design_name}`, font: { color: '#e2e8f0', size: 16 } },
                                    paper_bgcolor: 'transparent',
                                    plot_bgcolor: '#1e293b',
                                    font: { color: '#e2e8f0' },
                                    margin: { l: 60, r: 40, t: 50, b: 60 },
                                    xaxis: { title: { text: 'Residue Number', font: { color: '#94a3b8' } }, gridcolor: '#334155', color: '#94a3b8' },
                                    yaxis: { title: { text: 'pLDDT', font: { color: '#94a3b8' } }, gridcolor: '#334155', color: '#94a3b8', range: [0, 100] },
                                    shapes: [
                                        { type: 'line', x0: 0, x1: residueData.length, y0: 90, y1: 90, line: { color: '#3b82f6', width: 1, dash: 'dash' } },
                                        { type: 'line', x0: 0, x1: residueData.length, y0: 70, y1: 70, line: { color: '#22d3ee', width: 1, dash: 'dash' } },
                                        { type: 'line', x0: 0, x1: residueData.length, y0: 50, y1: 50, line: { color: '#fbbf24', width: 1, dash: 'dash' } },
                                    ],
                                }}
                                config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: `plddt_${residueData.design_name}` } }}
                                style={{ width: '100%', height: '450px' }}
                            />
                        ) : (
                            <div className="h-[450px] flex items-center justify-center text-slate-500">
                                No per-residue data available for selected design
                            </div>
                        )}
                    </div>
                ) : selectedPreset === 'chain_plddt' ? (
                    /* Phase 3a: Chain-by-Chain pLDDT */
                    <div className="space-y-3">
                        <div className="flex items-center gap-4 px-4 pt-4">
                            <label className="text-sm text-slate-400">Select Design:</label>
                            <select
                                value={selectedDesignId || ''}
                                onChange={(e) => setSelectedDesignId(e.target.value)}
                                className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white max-w-xs"
                            >
                                {sortedDesigns.map(d => (
                                    <option key={d.id} value={d.id}>
                                        {d.name} {d.plddt_overall ? `(${d.plddt_overall.toFixed(1)})` : ''}
                                    </option>
                                ))}
                            </select>
                        </div>
                        {chainMetricsLoading ? (
                            <div className="h-[550px] flex items-center justify-center text-slate-400">
                                Loading chain metrics...
                            </div>
                        ) : chainMetricsData && Object.keys(chainMetricsData).length > 0 ? (
                            <Plot
                                data={Object.entries(chainMetricsData)
                                    .filter(([, m]: [string, ChainMetric]) => m.type !== 'ligand')
                                    .sort(([idA, a]: [string, ChainMetric], [idB, b]: [string, ChainMetric]) => {
                                        const order = { protein: 0, dna: 1, rna: 2, ligand: 3 };
                                        return (order[a.type as keyof typeof order] ?? 4) - (order[b.type as keyof typeof order] ?? 4) || idA.localeCompare(idB);
                                    })
                                    .map(([chainId, metric]: [string, ChainMetric]) => ({
                                        type: 'scatter' as const,
                                        mode: 'lines' as const,
                                        x: metric.residue_numbers ?? Array.from({ length: metric.length }, (_, i) => i + 1),
                                        y: metric.plddt,
                                        name: `Chain ${chainId} (${metric.type}, avg: ${metric.avg_plddt?.toFixed(1) ?? '—'})`,
                                        line: {
                                            width: 2.5,
                                            color: metric.type === 'protein' ? '#3b82f6' : metric.type === 'dna' ? '#f59e0b' : metric.type === 'rna' ? '#8b5cf6' : '#64748b',
                                            shape: 'spline' as const,
                                        },
                                        hovertemplate: `<b>Chain ${chainId}</b><br>Residue %{x}<br>pLDDT: <b>%{y:.1f}</b><extra></extra>`,
                                    })) as Data[]}
                                layout={{
                                    title: {
                                        text: 'Chain-by-Chain pLDDT Profile',
                                        font: { color: '#f1f5f9', size: 18, family: 'Inter, sans-serif' },
                                        x: 0.5,
                                    },
                                    paper_bgcolor: 'transparent',
                                    plot_bgcolor: 'transparent',
                                    font: { color: '#e2e8f0', family: 'Inter, sans-serif' },
                                    margin: { l: 70, r: 30, t: 60, b: 70 },
                                    xaxis: {
                                        title: { text: 'Residue Number', font: { color: '#94a3b8', size: 13 }, standoff: 15 },
                                        gridcolor: '#334155',
                                        color: '#94a3b8',
                                        linecolor: '#475569',
                                        linewidth: 1,
                                        zeroline: false,
                                    },
                                    yaxis: {
                                        title: { text: 'pLDDT Score', font: { color: '#94a3b8', size: 13 }, standoff: 15 },
                                        gridcolor: '#33415580',
                                        color: '#94a3b8',
                                        range: [0, 100],
                                        dtick: 20,
                                        linecolor: '#475569',
                                        linewidth: 1,
                                        zeroline: false,
                                    },
                                    legend: {
                                        orientation: 'h',
                                        y: -0.18,
                                        x: 0.5,
                                        xanchor: 'center',
                                        font: { size: 11, color: '#cbd5e1' },
                                        bgcolor: 'transparent',
                                    },
                                    // Colored confidence region backgrounds
                                    shapes: [
                                        // Very high confidence (90-100) - Dark blue
                                        { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 90, y1: 100, fillcolor: '#1d4ed820', line: { width: 0 } },
                                        // High confidence (70-90) - Teal
                                        { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 70, y1: 90, fillcolor: '#0d948820', line: { width: 0 } },
                                        // Low confidence (50-70) - Yellow
                                        { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 50, y1: 70, fillcolor: '#ca8a0420', line: { width: 0 } },
                                        // Very low confidence (0-50) - Orange/Red
                                        { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 0, y1: 50, fillcolor: '#dc262620', line: { width: 0 } },
                                        // Threshold lines
                                        { type: 'line', x0: 0, x1: 1, xref: 'paper', y0: 90, y1: 90, line: { color: '#3b82f6', width: 1.5, dash: 'dot' } },
                                        { type: 'line', x0: 0, x1: 1, xref: 'paper', y0: 70, y1: 70, line: { color: '#14b8a6', width: 1.5, dash: 'dot' } },
                                        { type: 'line', x0: 0, x1: 1, xref: 'paper', y0: 50, y1: 50, line: { color: '#f59e0b', width: 1.5, dash: 'dot' } },
                                    ],
                                    // Confidence region annotations
                                    annotations: [
                                        { x: 1.01, xref: 'paper', y: 95, text: '<b>Very High</b>', showarrow: false, font: { size: 9, color: '#60a5fa' }, xanchor: 'left' },
                                        { x: 1.01, xref: 'paper', y: 80, text: '<b>High</b>', showarrow: false, font: { size: 9, color: '#2dd4bf' }, xanchor: 'left' },
                                        { x: 1.01, xref: 'paper', y: 60, text: '<b>Low</b>', showarrow: false, font: { size: 9, color: '#fbbf24' }, xanchor: 'left' },
                                        { x: 1.01, xref: 'paper', y: 30, text: '<b>Very Low</b>', showarrow: false, font: { size: 9, color: '#f87171' }, xanchor: 'left' },
                                    ],
                                    hovermode: 'x unified',
                                }}
                                config={{
                                    responsive: true,
                                    displayModeBar: true,
                                    modeBarButtonsToRemove: ['lasso2d', 'select2d'],
                                    toImageButtonOptions: { format: 'svg', filename: 'chain_plddt_profile', width: 1200, height: 600, scale: 2 }
                                }}
                                style={{ width: '100%', height: '550px' }}
                            />
                        ) : (
                            <div className="h-[550px] flex items-center justify-center text-slate-500">
                                No chain metrics available for selected design
                            </div>
                        )}
                    </div>
                ) : selectedPreset === 'pae_heatmap' ? (
                    /* Phase 3a: PAE Heatmap */
                    <div className="space-y-3">
                        <div className="flex items-center gap-4 px-4 pt-4">
                            <label className="text-sm text-slate-400">Select Design:</label>
                            <select
                                value={selectedDesignId || ''}
                                onChange={(e) => setSelectedDesignId(e.target.value)}
                                className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white max-w-xs"
                            >
                                {sortedDesigns.map(d => (
                                    <option key={d.id} value={d.id}>
                                        {d.name} {d.pae_overall ? `(PAE: ${d.pae_overall.toFixed(1)})` : ''}
                                    </option>
                                ))}
                            </select>
                        </div>
                        {paeLoading ? (
                            <div className="h-[550px] flex items-center justify-center text-slate-400">
                                Loading PAE matrix...
                            </div>
                        ) : paeData ? (
                            <Plot
                                data={[{
                                    type: 'heatmap',
                                    z: paeData.pae_matrix,
                                    colorscale: [
                                        [0, '#0d47a1'],
                                        [0.25, '#2196f3'],
                                        [0.5, '#4caf50'],
                                        [0.75, '#ffeb3b'],
                                        [1, '#f44336']
                                    ],
                                    zmin: 0,
                                    zmax: 30,
                                    hoverongaps: false,
                                    hovertemplate: 'Residue %{x} ↔ Residue %{y}<br>PAE: %{z:.1f} Å<extra></extra>',
                                    colorbar: { title: { text: 'PAE (Å)', font: { color: '#e2e8f0' } }, tickfont: { color: '#94a3b8' } },
                                } as Data]}
                                layout={{
                                    title: { text: `Predicted Aligned Error: ${paeData.design_name}`, font: { color: '#e2e8f0', size: 16 } },
                                    paper_bgcolor: 'transparent',
                                    plot_bgcolor: '#1e293b',
                                    font: { color: '#e2e8f0' },
                                    margin: { l: 60, r: 80, t: 50, b: 60 },
                                    xaxis: { title: { text: 'Scored Residue', font: { color: '#94a3b8' } }, color: '#94a3b8', scaleanchor: 'y' },
                                    yaxis: { title: { text: 'Aligned Residue', font: { color: '#94a3b8' } }, color: '#94a3b8', autorange: 'reversed' },
                                }}
                                config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: `pae_${paeData.design_name}` } }}
                                style={{ width: '100%', height: '550px' }}
                            />
                        ) : (
                            <div className="h-[550px] flex items-center justify-center text-slate-500">
                                No PAE data available for selected design
                            </div>
                        )}
                    </div>
                ) : selectedPreset === 'contact_map' ? (
                    /* Phase 3a: Residue Contact Map */
                    <div className="space-y-3">
                        <div className="flex items-center gap-4 px-4 pt-4">
                            <label className="text-sm text-slate-400">Select Design:</label>
                            <select
                                value={selectedDesignId || ''}
                                onChange={(e) => setSelectedDesignId(e.target.value)}
                                className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white max-w-xs"
                            >
                                {sortedDesigns.map(d => (
                                    <option key={d.id} value={d.id}>{d.name}</option>
                                ))}
                            </select>
                        </div>
                        {contactMapLoading ? (
                            <div className="h-[550px] flex items-center justify-center text-slate-400">
                                Computing contact map...
                            </div>
                        ) : contactMapData ? (
                            <Plot
                                data={[{
                                    type: 'heatmap',
                                    z: contactMapData.distance_matrix,
                                    x: contactMapData.residue_numbers,
                                    y: contactMapData.residue_numbers,
                                    colorscale: [[0, '#1e3a5f'], [0.1, '#60a5fa'], [0.25, '#22d3ee'], [0.5, '#fbbf24'], [1, '#ef4444']],
                                    zmin: 0,
                                    zmax: 30,
                                    hoverongaps: false,
                                    hovertemplate: 'Res %{x} ↔ Res %{y}<br>Distance: %{z:.1f} Å<extra></extra>',
                                    colorbar: { title: { text: 'Distance (Å)', font: { color: '#e2e8f0' } }, tickfont: { color: '#94a3b8' } },
                                } as Data]}
                                layout={{
                                    title: { text: `Contact Map: ${contactMapData.design_name}`, font: { color: '#e2e8f0', size: 16 } },
                                    paper_bgcolor: 'transparent',
                                    plot_bgcolor: '#1e293b',
                                    font: { color: '#e2e8f0' },
                                    margin: { l: 60, r: 80, t: 50, b: 60 },
                                    xaxis: { title: { text: 'Residue', font: { color: '#94a3b8' } }, color: '#94a3b8', scaleanchor: 'y' },
                                    yaxis: { title: { text: 'Residue', font: { color: '#94a3b8' } }, color: '#94a3b8', autorange: 'reversed' },
                                }}
                                config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: `contact_map_${contactMapData.design_name}` } }}
                                style={{ width: '100%', height: '550px' }}
                            />
                        ) : (
                            <div className="h-[550px] flex items-center justify-center text-slate-500">
                                No structure data available for contact map
                            </div>
                        )}
                    </div>
                ) : selectedPreset === 'chain_iptm' ? (
                    /* Phase 3a: Chain-Pair iPTM Matrix */
                    <div className="space-y-3">
                        <div className="flex items-center gap-4 px-4 pt-4">
                            <label className="text-sm text-slate-400">Select Design:</label>
                            <select
                                value={selectedDesignId || ''}
                                onChange={(e) => setSelectedDesignId(e.target.value)}
                                className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white max-w-xs"
                            >
                                {sortedDesigns.map(d => (
                                    <option key={d.id} value={d.id}>{d.name}</option>
                                ))}
                            </select>
                        </div>
                        {chainIptmLoading ? (
                            <div className="h-[500px] flex items-center justify-center text-slate-400">
                                Loading chain interface data...
                            </div>
                        ) : chainIptmData ? (
                            <Plot
                                data={[{
                                    type: 'heatmap',
                                    z: chainIptmData.iptm_matrix.map((row: (number | null)[]) => row.map((v: number | null) => v ?? 0)),
                                    x: chainIptmData.chain_ids,
                                    y: chainIptmData.chain_ids,
                                    colorscale: 'Viridis',
                                    zmin: 0,
                                    zmax: 1,
                                    hoverongaps: false,
                                    hovertemplate: '%{x} ↔ %{y}<br>iPTM: %{z:.3f}<extra></extra>',
                                    colorbar: { title: { text: 'iPTM', font: { color: '#e2e8f0' } }, tickfont: { color: '#94a3b8' } },
                                } as Data]}
                                layout={{
                                    title: { text: `Chain-Pair Interface Quality: ${chainIptmData.design_name}`, font: { color: '#e2e8f0', size: 16 } },
                                    paper_bgcolor: 'transparent',
                                    plot_bgcolor: '#1e293b',
                                    font: { color: '#e2e8f0' },
                                    margin: { l: 100, r: 80, t: 50, b: 100 },
                                    xaxis: { title: { text: 'Chain', font: { color: '#94a3b8' } }, color: '#94a3b8', tickangle: -45 },
                                    yaxis: { title: { text: 'Chain', font: { color: '#94a3b8' } }, color: '#94a3b8' },
                                    annotations: chainIptmData.iptm_matrix.flatMap((row: (number | null)[], i: number) =>
                                        row.map((val: number | null, j: number) => ({
                                            x: chainIptmData.chain_ids[j],
                                            y: chainIptmData.chain_ids[i],
                                            text: val !== null ? val.toFixed(2) : '—',
                                            showarrow: false,
                                            font: { color: val !== null && val > 0.5 ? '#1e293b' : '#e2e8f0', size: 12 },
                                        }))
                                    ),
                                }}
                                config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: `chain_iptm_${chainIptmData.design_name}` } }}
                                style={{ width: '100%', height: '500px' }}
                            />
                        ) : (
                            <div className="h-[500px] flex items-center justify-center text-slate-500">
                                No chain-pair iPTM data available (requires Boltz2/AF3 complex prediction)
                            </div>
                        )}
                    </div>
                ) : (
                    <Plot
                        data={scatterData}
                        layout={scatterLayout}
                        config={{ responsive: true, displayModeBar: true }}
                        style={{ width: '100%', height: '500px' }}
                    />
                )}
            </div>

            {/* Help Info */}
            <div className="bg-slate-800/30 rounded-lg p-4 text-sm text-slate-400 border border-slate-700/30">
                <strong className="text-slate-300">💡 Tips:</strong>
                <ul className="mt-2 space-y-1 list-disc list-inside">
                    <li>Use box select or lasso to zoom into regions of interest</li>
                    <li>Double-click chart to reset zoom</li>
                    <li>Hover over points to see design names and exact values</li>
                    <li>Use the camera icon in the modebar to download as PNG</li>
                </ul>
            </div>
        </div>
    );
}

export default ExperimentalAnalyticsPane;
