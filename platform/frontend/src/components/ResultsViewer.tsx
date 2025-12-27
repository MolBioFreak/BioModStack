import { useState, useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';

import { fetchJobs, fetchJobAnalytics, fetchDesigns, fetchDesignResidueMetrics, fetchChainMetrics, fetchStructureAnalysis, fetchAntibodyData } from '../lib/api';
import MolstarViewer from './MolstarViewer';
import FloatingViewer from './FloatingViewer';
import { Histogram, MetricScatter, ResidueLineChart, StabilityHeatmap } from './MetricCharts';
import { BatchComparePane } from './BatchComparePane';
import { PAEHeatmap as _PAEHeatmap } from './PAEHeatmap';
import { DesignComparePane } from './DesignComparePane';
import { ReferenceSelector } from './ReferenceSelector';
import { MetricOverlay } from './MetricOverlay';

// Tab definitions
const TABS = [
    { id: 'overview', label: 'Overview', icon: 'View' },
    { id: 'analytics', label: 'Analytics', icon: 'Chart' },
    { id: 'structure', label: 'Structure', icon: '3D' },
    { id: 'antibody', label: 'Antibody', icon: 'Immune' },
    { id: 'table', label: 'Data Table', icon: 'List' },
    { id: 'compare_designs', label: 'Compare Designs', icon: 'Chart' },
    { id: 'compare', label: 'Compare Jobs', icon: 'Vs' },
] as const;

type TabId = typeof TABS[number]['id'];

// Formatting helpers
const formatMetric = (val: number | null | undefined, decimals = 2): string =>
    val != null ? val.toFixed(decimals) : '—';

const getMetricColor = (metric: string, value: number | null): string => {
    if (value == null) return 'text-slate-500';
    if (metric === 'plddt_overall' || metric === 'plddt_binder') {
        return value >= 80 ? 'text-emerald-400' : value >= 60 ? 'text-amber-400' : 'text-red-400';
    }
    if (metric === 'pae_overall' || metric === 'pae_interaction') {
        return value <= 5 ? 'text-emerald-400' : value <= 10 ? 'text-amber-400' : 'text-red-400';
    }
    if (metric === 'ptm' || metric === 'conf_score') {
        return value >= 0.7 ? 'text-emerald-400' : value >= 0.5 ? 'text-amber-400' : 'text-red-400';
    }
    return 'text-slate-300';
};

export function ResultsViewer() {
    const { jobId } = useParams();
    const navigate = useNavigate();

    // State
    const [selectedJobId, setSelectedJobId] = useState<string>(jobId || '');
    const [activeTab, setActiveTab] = useState<TabId>('overview');
    const [selectedDesignId, setSelectedDesignId] = useState<string>('');

    const [sortField, setSortField] = useState<string>('name');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
    const [filterText, setFilterText] = useState('');
    const [showPlddt, setShowPlddt] = useState(true);  // pLDDT coloring on by default
    const [showReferencePanel, setShowReferencePanel] = useState(false);
    const [referenceStructures, setReferenceStructures] = useState<Array<{ url: string; format: 'pdb' | 'cif'; name: string }>>([]); // Array for multi-compare
    const MAX_COMPARE_VIEWERS = 3;

    // Fetch jobs list
    const { data: jobsData } = useQuery({
        queryKey: ['jobs'],
        queryFn: fetchJobs,
    });
    const jobs = jobsData?.data.jobs ?? [];

    // Sync URL with selection
    useEffect(() => {
        if (jobId && jobId !== selectedJobId) {
            setSelectedJobId(jobId);
        } else if (!jobId && jobs.length > 0 && !selectedJobId) {
            const completedJobs = jobs.filter(j => j.status === 'completed');
            if (completedJobs.length > 0) {
                const recent = completedJobs[0];
                setSelectedJobId(recent.id);
                navigate(`/designs/${recent.id}`, { replace: true });
            }
        }
    }, [jobId, jobs, selectedJobId, navigate]);

    const handleJobChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const newId = e.target.value;
        setSelectedJobId(newId);
        setSelectedDesignId('');
        if (newId) navigate(`/designs/${newId}`);
        else navigate('/designs');
    };

    // Fetch Analytics
    const { data: analyticsData, isLoading: analyticsLoading } = useQuery({
        queryKey: ['analytics', selectedJobId],
        queryFn: () => fetchJobAnalytics(selectedJobId),
        enabled: !!selectedJobId,
    });
    const analytics = analyticsData?.data;

    // Fetch Designs
    const { data: designsData, isLoading: designsLoading } = useQuery({
        queryKey: ['designs', selectedJobId],
        queryFn: () => fetchDesigns({ job_id: selectedJobId, limit: 500 }),
        enabled: !!selectedJobId,
    });
    const designs = designsData?.data.designs ?? [];

    // Fetch per-residue metrics for selected design (for line chart)
    const { data: residueMetricsData } = useQuery({
        queryKey: ['residueMetrics', selectedDesignId],
        queryFn: () => fetchDesignResidueMetrics(selectedDesignId),
        enabled: !!selectedDesignId,
    });
    const residueMetrics = residueMetricsData?.data;

    // Fetch per-chain metrics
    const { data: chainMetricsData } = useQuery({
        queryKey: ['chainMetrics', selectedDesignId],
        queryFn: () => fetchChainMetrics(selectedDesignId),
        enabled: !!selectedDesignId,
    });
    const chainMetrics = chainMetricsData?.data;

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
        enabled: !!selectedDesignId && activeTab === 'antibody',
        retry: false
    });
    const antibodyData = antibodyDataWrapper?.data;

    // Antibody selections for Molstar (IMGT Scheme)
    const antibodySelections = useMemo(() => {
        if (!antibodyData) return undefined;
        return [
            { chain_id: 'H', start_residue_number: 27, end_residue_number: 38, color: { r: 255, g: 50, b: 50 } }, // H1 - Red
            { chain_id: 'H', start_residue_number: 56, end_residue_number: 65, color: { r: 50, g: 255, b: 50 } }, // H2 - Green
            { chain_id: 'H', start_residue_number: 105, end_residue_number: 117, color: { r: 50, g: 100, b: 255 } }, // H3 - Blue
            { chain_id: 'L', start_residue_number: 27, end_residue_number: 38, color: { r: 255, g: 255, b: 50 } }, // L1 - Yellow
            { chain_id: 'L', start_residue_number: 56, end_residue_number: 65, color: { r: 50, g: 255, b: 255 } }, // L2 - Cyan
            { chain_id: 'L', start_residue_number: 105, end_residue_number: 117, color: { r: 255, g: 50, b: 255 } }, // L3 - Magenta
        ];
    }, [antibodyData]);

    // Sorted & Filtered designs for table
    const sortedDesigns = useMemo(() => {
        let filtered = designs;
        if (filterText) {
            const lower = filterText.toLowerCase();
            filtered = designs.filter(d => d.name.toLowerCase().includes(lower));
        }
        return [...filtered].sort((a, b) => {
            const aVal = (a as any)[sortField];
            const bVal = (b as any)[sortField];
            if (aVal == null) return 1;
            if (bVal == null) return -1;
            if (typeof aVal === 'string') {
                return sortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
            }
            return sortDir === 'asc' ? aVal - bVal : bVal - aVal;
        });
    }, [designs, sortField, sortDir, filterText]);

    // Fetch PDB content when design selected
    // Note: MolstarViewer now fetches structure directly from API URL

    // Auto-select first design
    useEffect(() => {
        if (designs.length > 0 && !selectedDesignId) {
            setSelectedDesignId(designs[0].id);
        }
    }, [designs, selectedDesignId]);

    const activeJob = jobs.find(j => j.id === selectedJobId);
    const selectedDesign = designs.find(d => d.id === selectedDesignId);
    // Detect structure format from file extension
    const structureFormat = selectedDesign?.pdb_path?.endsWith('.cif') ? 'cif' : 'pdb';
    const isLoading = analyticsLoading || designsLoading;

    // Quick stats for overview
    const stats = useMemo(() => {
        if (!designs.length) return null;
        const plddts = designs.map(d => d.plddt_overall).filter((v): v is number => v != null);
        const paes = designs.map(d => d.pae_overall).filter((v): v is number => v != null);
        const ptms = designs.map(d => d.ptm).filter((v): v is number => v != null);
        const affinities = designs.map(d => d.affinity_score).filter((v): v is number => v != null);
        const binderProbs = designs.map(d => d.binder_probability).filter((v): v is number => v != null);

        return {
            total: designs.length,
            favorites: designs.filter(d => d.is_favorite).length,
            avgPlddt: plddts.length ? plddts.reduce((a, b) => a + b, 0) / plddts.length : null,
            avgPae: paes.length ? paes.reduce((a, b) => a + b, 0) / paes.length : null,
            avgPtm: ptms.length ? ptms.reduce((a, b) => a + b, 0) / ptms.length : null,
            avgAffinity: affinities.length ? affinities.reduce((a, b) => a + b, 0) / affinities.length : null,
            avgBinderProb: binderProbs.length ? binderProbs.reduce((a, b) => a + b, 0) / binderProbs.length : null,
            highConfidence: plddts.filter(v => v >= 80).length,
            lowError: paes.filter(v => v <= 5).length,
        };
    }, [designs]);

    const handleSort = (field: string) => {
        if (sortField === field) {
            setSortDir(d => d === 'asc' ? 'desc' : 'asc');
        } else {
            setSortField(field);
            setSortDir('asc');
        }
    };

    return (
        <div className="min-h-screen bg-slate-950 text-slate-200">
            {/* Background */}
            <div className="fixed inset-0 pointer-events-none">
                <div className="absolute top-0 left-0 w-1/2 h-1/2 bg-blue-500/5 rounded-full blur-[150px]" />
                <div className="absolute bottom-0 right-0 w-1/2 h-1/2 bg-violet-500/5 rounded-full blur-[150px]" />
            </div>

            <div className="relative z-10 max-w-[1800px] mx-auto p-4 md:p-6 lg:p-8">
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
                                    const sortedJobs = [...jobs].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
                                    const batchedJobs = new Map<string, typeof sortedJobs>();
                                    const standaloneJobs: typeof sortedJobs = [];

                                    // Separate batched and standalone jobs
                                    sortedJobs.forEach(job => {
                                        if (job.batch_id && job.batch_name) {
                                            const existing = batchedJobs.get(job.batch_id) || [];
                                            existing.push(job);
                                            batchedJobs.set(job.batch_id, existing);
                                        } else {
                                            standaloneJobs.push(job);
                                        }
                                    });

                                    const elements: React.ReactNode[] = [];

                                    // Render batch groups first
                                    batchedJobs.forEach((batchJobs, batchId) => {
                                        const batchName = batchJobs[0].batch_name;
                                        elements.push(
                                            <optgroup key={batchId} label={`📦 ${batchName} (${batchJobs.length} sims)`}>
                                                {batchJobs.map(job => {
                                                    const date = new Date(job.created_at);
                                                    const timeStr = date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
                                                    const statusIcon = job.status === 'completed' ? '✓' : job.status === 'running' ? '⟳' : job.status === 'failed' ? '✗' : '○';
                                                    return (
                                                        <option key={job.id} value={job.id}>
                                                            {statusIcon} {job.name} │ {timeStr} │ {job.design_count} designs
                                                        </option>
                                                    );
                                                })}
                                            </optgroup>
                                        );
                                    });

                                    // Render standalone jobs
                                    standaloneJobs.forEach(job => {
                                        const date = new Date(job.created_at);
                                        const dateStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                                        const timeStr = date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
                                        const statusIcon = job.status === 'completed' ? '✓' : job.status === 'running' ? '⟳' : job.status === 'failed' ? '✗' : '○';
                                        elements.push(
                                            <option key={job.id} value={job.id}>
                                                {statusIcon} {job.name} │ {dateStr} {timeStr} │ {job.model_id || job.mode} │ {job.design_count} designs
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
                                <span className="text-slate-300 font-medium">{designs.length} designs</span>
                            </div>
                        )}
                    </div>
                </div>

                {selectedJobId && (
                    <>
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

                        {/* Content */}
                        <div className="bg-slate-900/50 rounded-xl border border-slate-800 min-h-[600px]">
                            {isLoading ? (
                                <div className="flex items-center justify-center h-96">
                                    <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500" />
                                </div>
                            ) : (
                                <>
                                    {/* OVERVIEW TAB */}
                                    {activeTab === 'overview' && stats && (
                                        <div className="p-6 space-y-6">
                                            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4">
                                                <StatCard label="Total Designs" value={stats.total} />
                                                <StatCard label="Favorites" value={stats.favorites} color="text-yellow-400" />
                                                <StatCard label="Avg pLDDT" value={formatMetric(stats.avgPlddt, 1)} color="text-blue-400" />
                                                <StatCard label="Avg Affinity" value={formatMetric(stats.avgAffinity, 2)} color="text-emerald-400" />
                                                <StatCard label="Avg Binder %" value={stats.avgBinderProb ? (stats.avgBinderProb * 100).toFixed(0) + '%' : '—'} color="text-emerald-400" />
                                                <StatCard label="Avg pTM" value={formatMetric(stats.avgPtm, 2)} color="text-violet-400" />
                                                <StatCard label="High Confidence" value={stats.highConfidence} subtitle="pLDDT ≥ 80" color="text-emerald-400" />
                                                <StatCard label="Low Error" value={stats.lowError} subtitle="PAE ≤ 5" color="text-emerald-400" />
                                            </div>

                                            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                                                {/* Top Designs */}
                                                <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
                                                    <h3 className="text-sm font-semibold text-slate-300 mb-4">Top Designs by pLDDT</h3>
                                                    <div className="space-y-2">
                                                        {[...designs].sort((a, b) => (b.plddt_overall ?? 0) - (a.plddt_overall ?? 0)).slice(0, 5).map(d => (
                                                            <div key={d.id} className="flex justify-between items-center py-2 px-3 bg-slate-900/50 rounded-lg">
                                                                <span className="text-sm truncate flex-1">{d.name}</span>
                                                                <span className={`text-sm font-mono ${getMetricColor('plddt_overall', d.plddt_overall)}`}>
                                                                    {formatMetric(d.plddt_overall, 1)}
                                                                </span>
                                                            </div>
                                                        ))}
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
                                        </div>
                                    )}

                                    {/* ANALYTICS TAB */}
                                    {activeTab === 'analytics' && analytics && (
                                        <div className="p-6 space-y-6">
                                            {/* Per-residue pLDDT Section */}
                                            <div className="space-y-4">
                                                <div className="flex items-center justify-between">
                                                    <h3 className="text-lg font-semibold text-white">Per-Residue Confidence</h3>
                                                    <select
                                                        value={selectedDesignId}
                                                        onChange={(e) => setSelectedDesignId(e.target.value)}
                                                        className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:ring-2 focus:ring-blue-500 focus:border-blue-500 max-w-xs"
                                                    >
                                                        <option value="">Select a design...</option>
                                                        {designs.slice(0, 50).map(d => (
                                                            <option key={d.id} value={d.id}>
                                                                {d.name} {d.plddt_overall ? `(${d.plddt_overall.toFixed(0)})` : ''}
                                                            </option>
                                                        ))}
                                                    </select>
                                                </div>
                                                {chainMetrics && Object.keys(chainMetrics).length > 0 ? (
                                                    <div className="space-y-4">
                                                        {Object.entries(chainMetrics)
                                                            .sort(([idA, a], [idB, b]) => {
                                                                const order = { protein: 0, dna: 1, rna: 2, ligand: 3 };
                                                                return (order[a.type as keyof typeof order] ?? 4) - (order[b.type as keyof typeof order] ?? 4) || idA.localeCompare(idB);
                                                            })
                                                            .map(([chainId, metric]) => (
                                                                metric.type !== 'ligand' && (
                                                                    <div key={chainId} className="bg-slate-800/30 rounded-xl p-4 border border-slate-700/30">
                                                                        <div className="flex justify-between items-center mb-2">
                                                                            <h4 className="text-sm font-semibold flex items-center gap-2 text-slate-200">
                                                                                <span className={`w-2 h-2 rounded-full ${metric.type === 'protein' ? 'bg-blue-400' :
                                                                                    metric.type === 'dna' ? 'bg-amber-400' :
                                                                                        metric.type === 'rna' ? 'bg-purple-400' : 'bg-slate-400'
                                                                                    }`} />
                                                                                Chain {chainId} <span className="text-slate-500 font-normal">({metric.type}, {metric.length} res)</span>
                                                                            </h4>
                                                                            <div className="text-xs font-mono">
                                                                                <span className="text-slate-500 mr-2">Avg pLDDT:</span>
                                                                                <span className={getMetricColor('plddt_overall', metric.avg_plddt)}>{metric.avg_plddt?.toFixed(1) ?? '—'}</span>
                                                                            </div>
                                                                        </div>
                                                                        <ResidueLineChart
                                                                            residueNumbers={metric.residue_numbers ?? Array.from({ length: metric.length }, (_, i) => i + 1)}
                                                                            plddt={metric.plddt}
                                                                            designName={`Chain ${chainId}`}
                                                                            height={180}
                                                                        />
                                                                    </div>
                                                                )
                                                            ))}
                                                    </div>
                                                ) : residueMetrics ? (
                                                    <ResidueLineChart
                                                        residueNumbers={residueMetrics.residue_numbers}
                                                        plddt={residueMetrics.plddt}
                                                        designName={residueMetrics.design_name}
                                                        height={280}
                                                    />
                                                ) : (
                                                    <div className="bg-slate-800/40 p-8 rounded-2xl border border-slate-700/40 text-center">
                                                        <p className="text-slate-500">Select a design above to view per-residue pLDDT</p>
                                                    </div>
                                                )}
                                            </div>

                                            {/* Histogram Grid */}
                                            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                                                {analytics.metrics?.plddt_overall && (
                                                    <Histogram title="pLDDT Distribution" data={analytics.metrics.plddt_overall} color="#60a5fa" />
                                                )}
                                                {analytics.metrics?.pae_overall && (
                                                    <Histogram title="PAE Distribution" data={analytics.metrics.pae_overall} color="#fbbf24" />
                                                )}
                                                {analytics.metrics?.ptm && (
                                                    <Histogram title="pTM Score" data={analytics.metrics.ptm} color="#a78bfa" />
                                                )}
                                                {analytics.metrics?.conf_score && (
                                                    <Histogram title="Confidence Score" data={analytics.metrics.conf_score} color="#34d399" />
                                                )}
                                                {analytics.correlations?.plddt_vs_pae && (
                                                    <MetricScatter
                                                        title="pLDDT vs PAE Correlation"
                                                        data={analytics.correlations.plddt_vs_pae}
                                                        xLabel="pLDDT"
                                                        yLabel="PAE"
                                                    />
                                                )}
                                            </div>

                                            {/* Design Rankings Table */}
                                            <div className="bg-gradient-to-br from-slate-800/60 to-slate-900/60 rounded-2xl border border-slate-700/50 overflow-hidden shadow-xl">
                                                <div className="px-6 py-4 border-b border-slate-700/50 flex items-center justify-between">
                                                    <h3 className="text-white text-base font-semibold flex items-center gap-2">
                                                        <svg className="w-5 h-5 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z" />
                                                        </svg>
                                                        Top Designs by pLDDT
                                                    </h3>
                                                    <span className="text-xs text-slate-400">Top 10 of {designs.length}</span>
                                                </div>
                                                <div className="overflow-x-auto">
                                                    <table className="w-full">
                                                        <thead>
                                                            <tr className="bg-slate-800/50 text-xs uppercase tracking-wider text-slate-400">
                                                                <th className="text-left px-6 py-3 font-medium">#</th>
                                                                <th className="text-left px-6 py-3 font-medium">Design Name</th>
                                                                <th className="text-right px-6 py-3 font-medium">pLDDT</th>
                                                                <th className="text-right px-6 py-3 font-medium">PAE</th>
                                                                <th className="text-right px-6 py-3 font-medium">pTM</th>
                                                                <th className="text-right px-6 py-3 font-medium">Conf</th>
                                                                <th className="text-center px-6 py-3 font-medium">Action</th>
                                                            </tr>
                                                        </thead>
                                                        <tbody className="divide-y divide-slate-700/50">
                                                            {[...designs]
                                                                .sort((a, b) => (b.plddt_overall ?? 0) - (a.plddt_overall ?? 0))
                                                                .slice(0, 10)
                                                                .map((d, idx) => (
                                                                    <tr key={d.id} className="hover:bg-slate-800/30 transition-colors">
                                                                        <td className="px-6 py-3">
                                                                            <span className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold ${idx === 0 ? 'bg-amber-500/20 text-amber-400' :
                                                                                idx === 1 ? 'bg-slate-400/20 text-slate-300' :
                                                                                    idx === 2 ? 'bg-orange-600/20 text-orange-400' :
                                                                                        'bg-slate-700/50 text-slate-500'
                                                                                }`}>
                                                                                {idx + 1}
                                                                            </span>
                                                                        </td>
                                                                        <td className="px-6 py-3 text-sm text-white font-medium truncate max-w-[200px]">{d.name}</td>
                                                                        <td className={`px-6 py-3 text-sm text-right font-mono font-semibold ${(d.plddt_overall ?? 0) >= 80 ? 'text-emerald-400' :
                                                                            (d.plddt_overall ?? 0) >= 60 ? 'text-amber-400' : 'text-red-400'
                                                                            }`}>
                                                                            {d.plddt_overall?.toFixed(1) ?? '—'}
                                                                        </td>
                                                                        <td className={`px-6 py-3 text-sm text-right font-mono ${d.pae_overall != null && d.pae_overall <= 10 ? 'text-emerald-400' :
                                                                            d.pae_overall != null && d.pae_overall <= 20 ? 'text-amber-400' :
                                                                                d.pae_overall != null ? 'text-red-400' : 'text-slate-600'
                                                                            }`}>
                                                                            {d.pae_overall?.toFixed(1) ?? '—'}
                                                                        </td>
                                                                        <td className={`px-6 py-3 text-sm text-right font-mono ${d.ptm != null && d.ptm >= 0.5 ? 'text-emerald-400' :
                                                                            d.ptm != null && d.ptm >= 0.3 ? 'text-amber-400' :
                                                                                d.ptm != null ? 'text-red-400' : 'text-slate-600'
                                                                            }`}>
                                                                            {d.ptm?.toFixed(3) ?? '—'}
                                                                        </td>
                                                                        <td className="px-6 py-3 text-sm text-right font-mono text-slate-400">
                                                                            {d.conf_score?.toFixed(3) ?? '—'}
                                                                        </td>
                                                                        <td className="px-6 py-3 text-center">
                                                                            <button
                                                                                onClick={() => {
                                                                                    setSelectedDesignId(d.id);
                                                                                    setActiveTab('structure');
                                                                                }}
                                                                                className="text-blue-400 hover:text-blue-300 text-xs font-medium"
                                                                            >
                                                                                View 3D
                                                                            </button>
                                                                        </td>
                                                                    </tr>
                                                                ))}
                                                        </tbody>
                                                    </table>
                                                </div>
                                            </div>
                                        </div>
                                    )}

                                    {/* STRUCTURE TAB - TRUE FULLSCREEN (breaks out of container) */}
                                    {activeTab === 'structure' && (
                                        <div className="fixed inset-0 top-[64px] bg-slate-950 z-10">
                                            {/* Compact Toolbar - 40% transparent */}
                                            <div className="absolute top-0 left-0 right-0 z-20 flex items-center gap-3 px-3 py-2 bg-slate-900/40 backdrop-blur-sm border-b border-slate-700/30">
                                                {/* Back Button - Exit Theater Mode */}
                                                <button
                                                    onClick={() => setActiveTab('overview')}
                                                    className="flex items-center gap-1 px-2 py-1.5 text-xs text-slate-300 bg-slate-700/50 hover:bg-slate-600/50 rounded-lg border border-slate-600/50 transition-colors"
                                                    title="Exit Theater Mode (go to Overview)"
                                                >
                                                    <span className="text-sm">←</span>
                                                    Back
                                                </button>

                                                <span className="text-slate-600">│</span>

                                                {/* Job Selector */}
                                                <div className="relative">
                                                    <select
                                                        value={selectedJobId ?? ''}
                                                        onChange={(e) => setSelectedJobId(e.target.value)}
                                                        className="appearance-none bg-slate-700/50 backdrop-blur-sm border border-slate-600/50 rounded px-2 py-1 pr-6 text-xs text-slate-300 cursor-pointer hover:bg-slate-600/50 transition-colors max-w-[120px]"
                                                        title="Switch Job"
                                                    >
                                                        {jobs
                                                            .filter(j => j.status === 'completed' && j.design_count > 0)
                                                            .slice(0, 50)
                                                            .map(job => (
                                                                <option key={job.id} value={job.id}>
                                                                    {job.name} ({job.design_count})
                                                                </option>
                                                            ))}
                                                    </select>
                                                    <div className="absolute right-1 top-1/2 -translate-y-1/2 pointer-events-none text-slate-500 text-[10px]">
                                                        ▾
                                                    </div>
                                                </div>

                                                <span className="text-slate-600">│</span>

                                                {/* Design Selector Dropdown */}
                                                <div className="relative">
                                                    <select
                                                        value={selectedDesignId ?? ''}
                                                        onChange={(e) => setSelectedDesignId(e.target.value)}
                                                        className="appearance-none bg-slate-800/60 backdrop-blur-sm border border-slate-600/50 rounded-lg px-3 py-1.5 pr-8 text-sm text-white cursor-pointer hover:bg-slate-700/60 transition-colors min-w-[180px]"
                                                    >
                                                        {designs.map(d => (
                                                            <option key={d.id} value={d.id}>
                                                                {d.name} {d.plddt_overall ? `(${d.plddt_overall.toFixed(0)})` : ''}
                                                            </option>
                                                        ))}
                                                    </select>
                                                    <div className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400">
                                                        ▾
                                                    </div>
                                                </div>

                                                {/* pLDDT Toggle */}
                                                <button
                                                    onClick={() => setShowPlddt(!showPlddt)}
                                                    className={`flex items-center gap-2 px-3 py-1.5 text-xs rounded-lg transition-colors backdrop-blur-sm ${showPlddt
                                                        ? 'bg-blue-500/30 text-blue-400 border border-blue-500/40'
                                                        : 'bg-slate-700/50 text-slate-400 border border-slate-600 hover:bg-slate-700'
                                                        }`}
                                                    title="Toggle pLDDT confidence coloring"
                                                >
                                                    <span className={`w-2 h-2 rounded-full ${showPlddt ? 'bg-blue-400' : 'bg-slate-500'}`} />
                                                    pLDDT
                                                </button>
                                                {showPlddt && (
                                                    <div className="flex items-center gap-1 text-xs text-slate-400">
                                                        <span className="text-blue-400">■</span>≥90
                                                        <span className="text-cyan-400 ml-1">■</span>≥70
                                                        <span className="text-yellow-400 ml-1">■</span>≥50
                                                        <span className="text-orange-400 ml-1">■</span>&lt;50
                                                    </div>
                                                )}

                                                {/* Compare Toggle + Counter */}
                                                <button
                                                    onClick={() => setShowReferencePanel(!showReferencePanel)}
                                                    className={`flex items-center gap-2 px-3 py-1.5 text-xs rounded-lg transition-colors backdrop-blur-sm ${showReferencePanel || referenceStructures.length > 0
                                                        ? 'bg-emerald-500/30 text-emerald-400 border border-emerald-500/40'
                                                        : 'bg-slate-700/50 text-slate-400 border border-slate-600 hover:bg-slate-700'
                                                        }`}
                                                >
                                                    <span className={`w-2 h-2 rounded-full ${referenceStructures.length > 0 ? 'bg-emerald-400' : 'bg-slate-500'}`} />
                                                    Compare
                                                    {referenceStructures.length > 0 && (
                                                        <span className="bg-emerald-500 text-white text-[10px] px-1.5 py-0.5 rounded-full font-bold">
                                                            {referenceStructures.length}/{MAX_COMPARE_VIEWERS}
                                                        </span>
                                                    )}
                                                </button>
                                                {referenceStructures.length > 0 && (
                                                    <button
                                                        onClick={() => setReferenceStructures([])}
                                                        className="text-xs text-red-400 hover:text-red-300"
                                                        title="Clear all comparisons"
                                                    >
                                                        Clear all
                                                    </button>
                                                )}

                                                {/* Quick metrics badge */}
                                                {selectedDesign && (
                                                    <div className="ml-auto flex items-center gap-3 text-xs">
                                                        {selectedDesign.plddt_overall && (
                                                            <span className={`font-mono ${getMetricColor('plddt_overall', selectedDesign.plddt_overall)}`}>
                                                                pLDDT: {selectedDesign.plddt_overall.toFixed(1)}
                                                            </span>
                                                        )}
                                                        {selectedDesign.pae_overall && (
                                                            <span className={`font-mono ${getMetricColor('pae_overall', selectedDesign.pae_overall)}`}>
                                                                PAE: {selectedDesign.pae_overall.toFixed(2)}
                                                            </span>
                                                        )}
                                                        {selectedDesign.ptm && (
                                                            <span className="font-mono text-amber-400">
                                                                pTM: {selectedDesign.ptm.toFixed(3)}
                                                            </span>
                                                        )}
                                                    </div>
                                                )}
                                            </div>

                                            {/* Reference Selector Panel - floating overlay */}
                                            {showReferencePanel && (
                                                <div className="absolute top-12 left-3 z-30 w-96 bg-slate-900/95 backdrop-blur-sm border border-slate-600 rounded-lg shadow-xl">
                                                    <div className="flex items-center justify-between px-3 py-2 border-b border-slate-700">
                                                        <span className="text-sm font-medium text-slate-200">
                                                            Add Compare Structure ({referenceStructures.length}/{MAX_COMPARE_VIEWERS})
                                                        </span>
                                                        <button
                                                            onClick={() => setShowReferencePanel(false)}
                                                            className="text-slate-400 hover:text-white"
                                                        >
                                                            ✕
                                                        </button>
                                                    </div>
                                                    <div className="p-3">
                                                        <ReferenceSelector
                                                            onSelect={(ref) => {
                                                                if (ref && referenceStructures.length < MAX_COMPARE_VIEWERS) {
                                                                    // Check if not already added
                                                                    const alreadyExists = referenceStructures.some(r => r.url === ref.url);
                                                                    if (!alreadyExists) {
                                                                        setReferenceStructures([...referenceStructures, ref]);
                                                                    }
                                                                }
                                                                setShowReferencePanel(false);
                                                            }}
                                                            selectedRef={null}
                                                            currentDesignId={selectedDesignId}
                                                        />
                                                    </div>
                                                    {referenceStructures.length >= MAX_COMPARE_VIEWERS && (
                                                        <div className="px-3 pb-3 text-xs text-amber-400">
                                                            ⚠ Maximum {MAX_COMPARE_VIEWERS} compare viewers reached
                                                        </div>
                                                    )}
                                                </div>
                                            )}

                                            {/* Main Viewer - Full Size */}
                                            <div className="absolute inset-0">
                                                <MolstarViewer
                                                    key={selectedDesignId}
                                                    structureUrl={selectedDesignId ? `/api/designs/${selectedDesignId}/pdb` : undefined}
                                                    format={structureFormat}
                                                    alphafoldView={showPlddt}
                                                    height="100%"
                                                    backgroundColor="#0f172a"
                                                />
                                            </div>

                                            {/* Floating Reference Viewers - Multiple */}
                                            {referenceStructures.map((ref, index) => (
                                                <FloatingViewer
                                                    key={ref.url}
                                                    structureUrl={ref.url}
                                                    format={ref.format}
                                                    label={ref.name}
                                                    initialPosition={{ x: 20 + (index * 30), y: 60 + (index * 30) }}
                                                    onClose={() => setReferenceStructures(refs => refs.filter((_, i) => i !== index))}
                                                />
                                            ))}

                                            {/* Floating Metric Overlay - Draggable */}
                                            {selectedDesignId && (
                                                <MetricOverlay
                                                    designId={selectedDesignId}
                                                    initialPosition={{ x: 70, y: 60 }}
                                                    initialType="structure"
                                                    structureAnalysis={structureAnalysis ?? undefined}
                                                    residueData={residueMetricsData?.data}
                                                    availableTypes={['structure', 'pae', 'plddt', 'iptm']}
                                                    pairChainsIptm={designs.find(d => d.id === selectedDesignId)?.pair_chains_iptm ?? undefined}
                                                />
                                            )}
                                        </div>
                                    )}

                                    {/* ANTIBODY TAB */}
                                    {activeTab === 'antibody' && (
                                        <div className="p-6 space-y-6">
                                            {!antibodyData ? (
                                                <div className="flex flex-col items-center justify-center py-20 text-slate-500">
                                                    <div className="text-4xl mb-4">🧬</div>
                                                    <p>Select an antibody design to view analysis.</p>
                                                    <p className="text-xs mt-2 opacity-60">If this is an antibody job, ensure ANARCI processing succeeded.</p>
                                                </div>
                                            ) : (
                                                <>
                                                    {/* CDR + Humanness Header */}
                                                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                                                        {/* CDR Table */}
                                                        <div className="lg:col-span-2 bg-slate-800/50 rounded-xl border border-slate-700/50 overflow-hidden">
                                                            <div className="px-4 py-3 bg-slate-800/80 border-b border-slate-700/50 flex justify-between items-center">
                                                                <h3 className="text-sm font-semibold text-white">CDR Loops (IMGT)</h3>
                                                                <span className="text-xs bg-blue-500/20 text-blue-300 px-2 py-0.5 rounded-md border border-blue-500/30">
                                                                    {selectedDesign?.name}
                                                                </span>
                                                            </div>
                                                            <div className="p-4 overflow-x-auto">
                                                                <table className="w-full text-sm">
                                                                    <thead>
                                                                        <tr className="text-left text-xs text-slate-400 uppercase tracking-wider border-b border-slate-700/50">
                                                                            <th className="pb-2">Chain</th>
                                                                            <th className="pb-2">Region</th>
                                                                            <th className="pb-2">Sequence</th>
                                                                            <th className="pb-2 text-right">Length</th>
                                                                        </tr>
                                                                    </thead>
                                                                    <tbody className="divide-y divide-slate-700/30 font-mono">
                                                                        {['H1', 'H2', 'H3', 'L1', 'L2', 'L3'].map(region => {
                                                                            const seq = antibodyData.cdrs[region as keyof typeof antibodyData.cdrs];
                                                                            if (!seq) return null;
                                                                            return (
                                                                                <tr key={region} className="hover:bg-slate-700/20">
                                                                                    <td className="py-2 text-slate-500">{region[0]}</td>
                                                                                    <td className="py-2 font-bold text-slate-300">{region}</td>
                                                                                    <td className="py-2 text-white break-all">{seq}</td>
                                                                                    <td className="py-2 text-right text-slate-500">{seq.length}</td>
                                                                                </tr>
                                                                            );
                                                                        })}
                                                                    </tbody>
                                                                </table>
                                                            </div>
                                                        </div>

                                                        {/* Humanness & Status */}
                                                        <div className="space-y-6">
                                                            {/* Humanness Score */}
                                                            <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 p-6 flex flex-col items-center justify-center text-center">
                                                                <span className="text-slate-400 text-sm font-medium mb-2">Humanness Score</span>
                                                                <div className="relative w-32 h-32 flex items-center justify-center">
                                                                    <svg className="w-full h-full transform -rotate-90">
                                                                        <circle cx="64" cy="64" r="56" stroke="#1e293b" strokeWidth="12" fill="transparent" />
                                                                        <circle
                                                                            cx="64"
                                                                            cy="64"
                                                                            r="56"
                                                                            stroke={
                                                                                (antibodyData.humanness_score || 0) > 0.8 ? '#10b981' :
                                                                                    (antibodyData.humanness_score || 0) > 0.6 ? '#f59e0b' : '#ef4444'
                                                                            }
                                                                            strokeWidth="12"
                                                                            fill="transparent"
                                                                            strokeDasharray={351.86}
                                                                            strokeDashoffset={351.86 * (1 - (antibodyData.humanness_score || 0))}
                                                                            className="transition-all duration-1000 ease-out"
                                                                        />
                                                                    </svg>
                                                                    <span className="absolute text-2xl font-bold text-white">
                                                                        {((antibodyData.humanness_score || 0) * 100).toFixed(0)}%
                                                                    </span>
                                                                </div>
                                                                <p className="text-xs text-slate-500 mt-2">OAS-based similarity metric</p>
                                                            </div>
                                                        </div>
                                                    </div>

                                                    {/* Structure Preview with Highlights */}
                                                    <div className="h-[500px] bg-slate-900 rounded-xl border border-slate-700/50 overflow-hidden relative">
                                                        <div className="absolute top-3 left-3 z-10 bg-slate-900/80 backdrop-blur px-3 py-1 rounded text-xs text-slate-300 pointer-events-none">
                                                            {antibodyData.imgt_pdb_url ? 'IMGT Renumbered Structure' : 'Original Structure (Highlights may be offset)'}
                                                        </div>
                                                        <MolstarViewer
                                                            key={selectedDesignId + '_ab'}
                                                            structureUrl={antibodyData.imgt_pdb_url || (selectedDesignId ? `/api/designs/${selectedDesignId}/pdb` : undefined)}
                                                            format="pdb"
                                                            alphafoldView={false}
                                                            height="100%"
                                                            backgroundColor="#0f172a"
                                                            hideControls={true}
                                                            selections={antibodySelections}
                                                            label="CDR H1:Red H2:Green H3:Blue | L1:Yel L2:Cyan L3:Mag"
                                                        />
                                                    </div>

                                                    {/* Stability Heatmap */}
                                                    {antibodyData.stability_data && (
                                                        <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 p-6">
                                                            <h3 className="text-sm font-semibold text-white mb-4 flex items-center gap-2">
                                                                <div className="w-2 h-2 rounded-full bg-pink-500"></div>
                                                                ThermoMPNN Stability Scan (ddG)
                                                            </h3>
                                                            <div className="h-[400px] w-full flex items-center justify-center bg-slate-900/50 rounded-lg border border-slate-800">
                                                                <StabilityHeatmap
                                                                    data={antibodyData.stability_data}
                                                                    width={800}
                                                                    height={380}
                                                                />
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
                                            {/* Filter */}
                                            <div className="mb-4">
                                                <input
                                                    type="text"
                                                    placeholder="Filter by name..."
                                                    value={filterText}
                                                    onChange={e => setFilterText(e.target.value)}
                                                    className="bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-sm w-64"
                                                />
                                            </div>
                                            {/* Table */}
                                            <div className="overflow-x-auto">
                                                <table className="w-full text-sm">
                                                    <thead>
                                                        <tr className="border-b border-slate-700">
                                                            {[
                                                                { key: 'name', label: 'Name' },
                                                                { key: 'affinity_score', label: 'Affinity (-log IC50)' },
                                                                { key: 'binder_probability', label: 'Binder %' },
                                                                { key: 'plddt_overall', label: 'pLDDT' },
                                                                { key: 'pae_overall', label: 'PAE' },
                                                                { key: 'ptm', label: 'pTM' },
                                                                { key: 'ligand_iptm', label: 'Lig iPTM' },
                                                                { key: 'conf_score', label: 'Conf' },
                                                                { key: 'rmsd_binder', label: 'RMSD Binder' },
                                                                { key: 'rog', label: 'RoG' },
                                                                { key: 'is_favorite', label: '★' },
                                                            ].map(col => (
                                                                <th
                                                                    key={col.key}
                                                                    onClick={() => handleSort(col.key)}
                                                                    className="px-3 py-2 text-left font-medium text-slate-400 cursor-pointer hover:text-white"
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
                                                        {sortedDesigns.map(d => (
                                                            <tr
                                                                key={d.id}
                                                                className="border-b border-slate-800 hover:bg-slate-800/30 cursor-pointer"
                                                                onClick={() => {
                                                                    setSelectedDesignId(d.id);
                                                                    setActiveTab('structure');
                                                                }}
                                                            >
                                                                <td className="px-3 py-2 font-medium truncate max-w-[200px]">{d.name}</td>

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

                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('plddt_overall', d.plddt_overall)}`}>
                                                                    {formatMetric(d.plddt_overall, 1)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('pae_overall', d.pae_overall)}`}>
                                                                    {formatMetric(d.pae_overall, 1)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('ptm', d.ptm)}`}>
                                                                    {formatMetric(d.ptm, 2)}
                                                                </td>

                                                                {/* Ligand iPTM */}
                                                                <td className={`px-3 py-2 font-mono ${d.ligand_iptm != null && d.ligand_iptm > 0.8 ? 'text-emerald-400' : 'text-slate-500'}`}>
                                                                    {formatMetric(d.ligand_iptm, 2)}
                                                                </td>

                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('conf_score', d.conf_score)}`}>
                                                                    {formatMetric(d.conf_score, 2)}
                                                                </td>
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.rmsd_binder, 2)}</td>
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.rog, 1)}</td>
                                                                <td className="px-3 py-2">{d.is_favorite ? '★' : ''}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
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
