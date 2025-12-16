import { useState, useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';
import { fetchJobs, fetchJobAnalytics, fetchDesigns, fetchDesignResidueMetrics, fetchStructureAnalysis } from '../lib/api';
import MolstarViewer from './MolstarViewer';
import { Histogram, MetricScatter, ResidueLineChart } from './MetricCharts';
import { BatchComparePane } from './BatchComparePane';
import { PAEHeatmap } from './PAEHeatmap';

// Tab definitions
const TABS = [
    { id: 'overview', label: 'Overview', icon: 'View' },
    { id: 'analytics', label: 'Analytics', icon: 'Chart' },
    { id: 'structure', label: 'Structure', icon: '3D' },
    { id: 'table', label: 'Data Table', icon: 'List' },
    { id: 'compare', label: 'Compare', icon: 'Vs' },
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

    // Fetch structure analysis for selected design (Biotite-powered)
    const { data: structureAnalysisData, isLoading: structureAnalysisLoading } = useQuery({
        queryKey: ['structureAnalysis', selectedDesignId],
        queryFn: () => fetchStructureAnalysis(selectedDesignId),
        enabled: !!selectedDesignId && activeTab === 'structure',
    });
    const structureAnalysis = structureAnalysisData?.data;

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
                                {jobs
                                    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
                                    .map(job => {
                                        const date = new Date(job.created_at);
                                        const dateStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                                        const timeStr = date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
                                        const statusIcon = job.status === 'completed' ? '✓' : job.status === 'running' ? '⟳' : job.status === 'failed' ? '✗' : '○';
                                        return (
                                            <option key={job.id} value={job.id}>
                                                {statusIcon} {job.name} │ {dateStr} {timeStr} │ {job.model_id || job.mode} │ {job.design_count} designs
                                            </option>
                                        );
                                    })}
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
                                                {residueMetrics ? (
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

                                    {/* STRUCTURE TAB */}
                                    {activeTab === 'structure' && (
                                        <div className="flex h-[700px]">
                                            {/* Left Sidebar - Design List */}
                                            <div className="w-64 border-r border-slate-800 flex flex-col bg-slate-900/30">
                                                <div className="p-3 border-b border-slate-800 text-sm font-medium text-slate-300">
                                                    Designs ({designs.length})
                                                </div>
                                                <div className="flex-1 overflow-y-auto">
                                                    {designs.map(d => (
                                                        <button
                                                            key={d.id}
                                                            onClick={() => setSelectedDesignId(d.id)}
                                                            className={`w-full text-left px-3 py-2 text-sm border-l-2 transition-colors ${selectedDesignId === d.id
                                                                ? 'bg-blue-500/10 border-blue-500 text-white'
                                                                : 'border-transparent hover:bg-slate-800/50 text-slate-400'
                                                                }`}
                                                        >
                                                            <div className="truncate">{d.name}</div>
                                                            <div className="flex gap-2 mt-1 text-xs">
                                                                {d.plddt_overall && <span className={getMetricColor('plddt_overall', d.plddt_overall)}>pLDDT: {d.plddt_overall.toFixed(0)}</span>}
                                                                {d.pae_overall && <span className={getMetricColor('pae_overall', d.pae_overall)}>PAE: {d.pae_overall.toFixed(1)}</span>}
                                                            </div>
                                                        </button>
                                                    ))}
                                                </div>
                                            </div>

                                            {/* Center - 3D Viewer */}
                                            <div className="flex-1 bg-slate-950 relative flex flex-col">
                                                {/* Minimal toolbar with pLDDT toggle */}
                                                <div className="flex items-center gap-3 px-3 py-2 border-b border-slate-800 bg-slate-900/50">
                                                    <button
                                                        onClick={() => setShowPlddt(!showPlddt)}
                                                        className={`flex items-center gap-2 px-3 py-1.5 text-xs rounded-lg transition-colors ${showPlddt
                                                            ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                                                            : 'bg-slate-700/50 text-slate-400 border border-slate-600 hover:bg-slate-700'
                                                            }`}
                                                        title="Toggle pLDDT confidence coloring (AlphaFold style)"
                                                    >
                                                        <span className={`w-2 h-2 rounded-full ${showPlddt ? 'bg-blue-400' : 'bg-slate-500'}`} />
                                                        pLDDT Coloring
                                                    </button>
                                                    {showPlddt && (
                                                        <div className="flex items-center gap-1 text-xs text-slate-500">
                                                            <span className="text-blue-400">■</span>≥90
                                                            <span className="text-cyan-400 ml-1">■</span>≥70
                                                            <span className="text-yellow-400 ml-1">■</span>≥50
                                                            <span className="text-orange-400 ml-1">■</span>&lt;50
                                                        </div>
                                                    )}
                                                    <span className="text-xs text-slate-600 ml-auto">
                                                        Shift+click sequence for range selection
                                                    </span>
                                                </div>
                                                {/* Viewer */}
                                                <div className="flex-1 relative">
                                                    <MolstarViewer
                                                        key={selectedDesignId}  // Only recreate when design changes, not on pLDDT toggle
                                                        structureUrl={selectedDesignId ? `/api/designs/${selectedDesignId}/pdb` : undefined}
                                                        format={structureFormat}
                                                        alphafoldView={showPlddt}
                                                        height={620}
                                                        backgroundColor="#0f172a"
                                                    />
                                                </div>
                                            </div>

                                            {/* Right Sidebar - Structure Analysis */}
                                            <div className="w-72 border-l border-slate-800 bg-slate-900/40 flex flex-col">
                                                <div className="p-3 border-b border-slate-800 text-sm font-medium text-slate-300">
                                                    Structure Analysis
                                                </div>
                                                {structureAnalysisLoading ? (
                                                    <div className="flex-1 flex items-center justify-center text-slate-500">
                                                        <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                                                    </div>
                                                ) : structureAnalysis ? (
                                                    <div className="p-4 space-y-4 text-sm">
                                                        {/* Basic Info */}
                                                        <div className="space-y-2">
                                                            <div className="flex justify-between">
                                                                <span className="text-slate-500">Residues</span>
                                                                <span className="text-white font-mono">{structureAnalysis.residue_count}</span>
                                                            </div>
                                                            <div className="flex justify-between">
                                                                <span className="text-slate-500">Chains</span>
                                                                <span className="text-cyan-400 font-mono">{structureAnalysis.chain_ids.join(', ')}</span>
                                                            </div>
                                                            <div className="flex justify-between">
                                                                <span className="text-slate-500">Gyration Radius</span>
                                                                <span className="text-purple-400 font-mono">
                                                                    {structureAnalysis.gyration_radius?.toFixed(2) ?? '—'} Å
                                                                </span>
                                                            </div>
                                                        </div>

                                                        {/* Secondary Structure */}
                                                        <div className="pt-2 border-t border-slate-700">
                                                            <div className="text-xs text-slate-500 uppercase tracking-wider mb-3">Secondary Structure</div>
                                                            {(() => {
                                                                const sse = structureAnalysis.secondary_structure;
                                                                const total = sse.helix + sse.sheet + sse.coil;
                                                                const pctHelix = total > 0 ? (sse.helix / total * 100) : 0;
                                                                const pctSheet = total > 0 ? (sse.sheet / total * 100) : 0;
                                                                const pctCoil = total > 0 ? (sse.coil / total * 100) : 0;
                                                                return (
                                                                    <div className="space-y-3">
                                                                        {/* Helix */}
                                                                        <div>
                                                                            <div className="flex justify-between text-xs mb-1">
                                                                                <span className="text-red-400">α Helix</span>
                                                                                <span className="text-slate-400">{sse.helix} ({pctHelix.toFixed(0)}%)</span>
                                                                            </div>
                                                                            <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                                                                                <div className="h-full bg-red-500" style={{ width: `${pctHelix}%` }} />
                                                                            </div>
                                                                        </div>
                                                                        {/* Sheet */}
                                                                        <div>
                                                                            <div className="flex justify-between text-xs mb-1">
                                                                                <span className="text-yellow-400">β Sheet</span>
                                                                                <span className="text-slate-400">{sse.sheet} ({pctSheet.toFixed(0)}%)</span>
                                                                            </div>
                                                                            <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                                                                                <div className="h-full bg-yellow-500" style={{ width: `${pctSheet}%` }} />
                                                                            </div>
                                                                        </div>
                                                                        {/* Coil */}
                                                                        <div>
                                                                            <div className="flex justify-between text-xs mb-1">
                                                                                <span className="text-slate-400">Coil/Loop</span>
                                                                                <span className="text-slate-400">{sse.coil} ({pctCoil.toFixed(0)}%)</span>
                                                                            </div>
                                                                            <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                                                                                <div className="h-full bg-slate-500" style={{ width: `${pctCoil}%` }} />
                                                                            </div>
                                                                        </div>
                                                                    </div>
                                                                );
                                                            })()}
                                                        </div>

                                                        {/* PAE Heatmap */}
                                                        <div className="pt-4 border-t border-slate-700">
                                                            <PAEHeatmap
                                                                designId={selectedDesignId}
                                                                width={240}
                                                                height={240}
                                                            />
                                                        </div>
                                                    </div>
                                                ) : (
                                                    <div className="flex-1 flex items-center justify-center p-4 text-center text-slate-500 text-xs">
                                                        Select a design to view structure analysis
                                                    </div>
                                                )}
                                            </div>
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
                                </>
                            )}
                        </div>
                    </>
                )}
            </div>
        </div>
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
