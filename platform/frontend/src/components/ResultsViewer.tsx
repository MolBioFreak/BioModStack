import { useState, useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';
import { fetchJobs, fetchJobAnalytics, fetchDesigns } from '../lib/api';
import { MolViewer } from './MolViewer';
import { Histogram, MetricScatter } from './MetricCharts';
import { BatchComparePane } from './BatchComparePane';

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
    const [pdbContent, setPdbContent] = useState<string>('');
    const [sortField, setSortField] = useState<string>('name');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
    const [filterText, setFilterText] = useState('');

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
    useEffect(() => {
        if (selectedDesignId && activeTab === 'structure') {
            const design = designs.find(d => d.id === selectedDesignId);
            if (design?.pdb_path) {
                fetch(`/api/designs/${selectedDesignId}/pdb`)
                    .then(res => res.text())
                    .then(text => setPdbContent(text))
                    .catch(err => console.error("Failed to load PDB", err));
            }
        }
    }, [selectedDesignId, activeTab, designs]);

    // Auto-select first design
    useEffect(() => {
        if (designs.length > 0 && !selectedDesignId) {
            setSelectedDesignId(designs[0].id);
        }
    }, [designs, selectedDesignId]);

    const activeJob = jobs.find(j => j.id === selectedJobId);
    const isLoading = analyticsLoading || designsLoading;

    // Quick stats for overview
    const stats = useMemo(() => {
        if (!designs.length) return null;
        const plddts = designs.map(d => d.plddt_overall).filter((v): v is number => v != null);
        const paes = designs.map(d => d.pae_overall).filter((v): v is number => v != null);
        const ptms = designs.map(d => d.ptm).filter((v): v is number => v != null);

        return {
            total: designs.length,
            favorites: designs.filter(d => d.is_favorite).length,
            avgPlddt: plddts.length ? plddts.reduce((a, b) => a + b, 0) / plddts.length : null,
            avgPae: paes.length ? paes.reduce((a, b) => a + b, 0) / paes.length : null,
            avgPtm: ptms.length ? ptms.reduce((a, b) => a + b, 0) / ptms.length : null,
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
                    <select
                        value={selectedJobId}
                        onChange={handleJobChange}
                        className="bg-slate-800 text-white border border-slate-700 rounded-lg px-4 py-2 min-w-[280px] focus:ring-2 focus:ring-blue-500"
                    >
                        <option value="">Select a job...</option>
                        {jobs.map(job => (
                            <option key={job.id} value={job.id}>
                                {job.name} ({job.status}) - {job.design_count} designs
                            </option>
                        ))}
                    </select>
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
                                                <StatCard label="Avg PAE" value={formatMetric(stats.avgPae, 1)} color="text-amber-400" />
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
                                        </div>
                                    )}

                                    {/* STRUCTURE TAB */}
                                    {activeTab === 'structure' && (
                                        <div className="flex h-[700px]">
                                            {/* Sidebar */}
                                            <div className="w-72 border-r border-slate-800 flex flex-col bg-slate-900/30">
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
                                            {/* Viewer */}
                                            <div className="flex-1 bg-slate-950 relative">
                                                <div className="absolute top-4 left-4 z-10 flex gap-2">
                                                    <div className="bg-slate-900/80 backdrop-blur px-3 py-1 rounded-lg border border-slate-700 text-xs text-white">
                                                        <span className="text-blue-400 font-bold">Blue</span>: High Confidence
                                                        <span className="ml-2 text-yellow-400 font-bold">Yellow</span>: Low
                                                    </div>
                                                </div>
                                                <MolViewer
                                                    pdbContent={pdbContent}
                                                    height={700}
                                                    backgroundColor="#0f172a"
                                                    colorScheme="plddt"
                                                />
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
                                                                { key: 'plddt_overall', label: 'pLDDT' },
                                                                { key: 'pae_overall', label: 'PAE' },
                                                                { key: 'ptm', label: 'pTM' },
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
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('plddt_overall', d.plddt_overall)}`}>
                                                                    {formatMetric(d.plddt_overall, 1)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('pae_overall', d.pae_overall)}`}>
                                                                    {formatMetric(d.pae_overall, 1)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('ptm', d.ptm)}`}>
                                                                    {formatMetric(d.ptm, 2)}
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
