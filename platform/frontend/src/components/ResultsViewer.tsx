import { useState, useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';

import { fetchJobs, fetchDesigns, fetchStructureAnalysis, fetchAntibodyData, fetchBackboneSummary } from '../lib/api';
import type { Job } from '../lib/api';
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
    const [colorMode, setColorMode] = useState<'default' | 'plddt' | 'cdr'>('plddt');  // Structure coloring mode
    // Compare feature disabled for now:
    // const [showReferencePanel, setShowReferencePanel] = useState(false);
    // const [referenceStructures, setReferenceStructures] = useState<Array<{ url: string; format: 'pdb' | 'cif'; name: string }>>([]);
    const [selectedBackboneId, setSelectedBackboneId] = useState<number | null>(null);
    const [plddtMin, setPlddtMin] = useState<number>(0);
    const [iptmMin, setIptmMin] = useState<number>(0);
    const [contactsMin, setContactsMin] = useState<number>(0);
    // const MAX_COMPARE_VIEWERS = 3; // unused

    // Pagination state for large design sets
    const [pageSize, setPageSize] = useState<number>(100);
    const [currentPage, setCurrentPage] = useState<number>(1);
    const PAGE_SIZE_OPTIONS = [50, 100, 250, 500, 1000, 0]; // 0 = All

    // Fetch jobs list
    const { data: jobsData } = useQuery({
        queryKey: ['jobs'],
        queryFn: () => fetchJobs(),
    });
    const jobs = jobsData?.data.jobs ?? [];

    // Sync URL with selection
    useEffect(() => {
        if (jobId && jobId !== selectedJobId) {
            setSelectedJobId(jobId);
        } else if (!jobId && jobs.length > 0 && !selectedJobId) {
            const completedJobs = jobs.filter((j: Job) => j.status === 'completed');
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
        setCurrentPage(1); // Reset pagination when switching jobs
        if (newId) navigate(`/designs/${newId}`);
        else navigate('/designs');
    };

    const { data: designsData, isLoading: designsLoading } = useQuery({
        queryKey: ['designs', selectedJobId, currentPage, pageSize, sortField, sortDir, selectedBackboneId],
        queryFn: () => fetchDesigns({
            job_id: selectedJobId,
            limit: pageSize === 0 ? 10000 : pageSize, // 0 = All (fetch up to 10000)
            offset: pageSize === 0 ? 0 : (currentPage - 1) * pageSize,
            sort_by: sortField as 'plddt' | 'iptm' | 'ptm' | 'pae' | 'backbone' | undefined,
            sort_desc: sortDir === 'desc',
            backbone_id: selectedBackboneId ?? undefined,
        }),
        enabled: !!selectedJobId,
    });
    const designs = designsData?.data.designs ?? [];
    const totalDesigns = designsData?.data.total ?? 0;
    const totalPages = pageSize === 0 ? 1 : Math.ceil(totalDesigns / pageSize);

    // Fetch backbone summary for toggle UI
    const { data: backboneSummaryData } = useQuery({
        queryKey: ['backboneSummary', selectedJobId],
        queryFn: () => fetchBackboneSummary(selectedJobId),
        enabled: !!selectedJobId,
    });
    const backboneSummary = backboneSummaryData?.data;

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

    // Antibody selections for Molstar - dynamic based on actual CDR data
    const antibodySelections = useMemo(() => {
        const design = designs.find(d => d.id === selectedDesignId) as any;
        if (!design?.cdr_h1_length) return undefined;

        // Use chain 'B' which is typically the binder in RFantibody outputs
        // Compute approximate positions based on framework regions
        // Standard VHH/Fab structure: FR1 (1-26) | CDR1 | FR2 (39-55) | CDR2 | FR3 (66-104) | CDR3 | FR4
        const h1Start = 27;
        const h1End = 26 + (design.cdr_h1_length || 12);
        const h2Start = h1End + 17; // FR2 is ~17 residues
        const h2End = h2Start + (design.cdr_h2_length || 10) - 1;
        const h3Start = h2End + 39; // FR3 is ~39 residues
        const h3End = h3Start + (design.cdr_h3_length || 12) - 1;

        const selections = [
            { chain_id: 'A', start_residue_number: h1Start, end_residue_number: h1End, color: { r: 255, g: 50, b: 50 } }, // H1 - Red
            { chain_id: 'A', start_residue_number: h2Start, end_residue_number: h2End, color: { r: 50, g: 255, b: 50 } }, // H2 - Green
            { chain_id: 'A', start_residue_number: h3Start, end_residue_number: h3End, color: { r: 50, g: 100, b: 255 } }, // H3 - Blue
        ];

        // Add L-chain CDRs if this is a Fab (not VHH)
        if (design.antibody_type !== 'vhh' && design.cdr_l1_length) {
            const l1Start = 27;
            const l1End = 26 + (design.cdr_l1_length || 11);
            const l2Start = l1End + 16;
            const l2End = l2Start + (design.cdr_l2_length || 7) - 1;
            const l3Start = l2End + 33;
            const l3End = l3Start + (design.cdr_l3_length || 9) - 1;

            selections.push(
                { chain_id: 'C', start_residue_number: l1Start, end_residue_number: l1End, color: { r: 255, g: 255, b: 50 } }, // L1 - Yellow
                { chain_id: 'C', start_residue_number: l2Start, end_residue_number: l2End, color: { r: 50, g: 255, b: 255 } }, // L2 - Cyan
                { chain_id: 'C', start_residue_number: l3Start, end_residue_number: l3End, color: { r: 255, g: 50, b: 255 } }, // L3 - Magenta
            );
        }

        return selections;
    }, [designs, selectedDesignId]);

    // Sorted & Filtered designs for table
    const sortedDesigns = useMemo(() => {
        let filtered = designs;
        // Backbone filter
        if (selectedBackboneId !== null) {
            filtered = filtered.filter(d => d.backbone_id === selectedBackboneId);
        }
        // pLDDT filter
        if (plddtMin > 0) {
            filtered = filtered.filter(d => (d.plddt_overall ?? 0) >= plddtMin);
        }
        // iPTM filter
        if (iptmMin > 0) {
            filtered = filtered.filter(d => (d.iptm ?? 0) >= iptmMin);
        }
        // Epitope contacts filter
        if (contactsMin > 0) {
            filtered = filtered.filter(d => (d.epitope_contact_count ?? 0) >= contactsMin);
        }
        // Text filter
        if (filterText) {
            const lower = filterText.toLowerCase();
            filtered = filtered.filter(d => d.name.toLowerCase().includes(lower));
        }
        return [...filtered].sort((a, b) => {
            const aVal = (a as any)[sortField];
            const bVal = (b as any)[sortField];
            if (aVal == null) return 1;
            if (bVal == null) return -1;
            if (typeof aVal === 'string') {
                // Use natural sort for strings with numbers (antibody_job_2 before antibody_job_10)
                return sortDir === 'asc'
                    ? aVal.localeCompare(bVal, undefined, { numeric: true, sensitivity: 'base' })
                    : bVal.localeCompare(aVal, undefined, { numeric: true, sensitivity: 'base' });
            }
            return sortDir === 'asc' ? aVal - bVal : bVal - aVal;
        });
    }, [designs, sortField, sortDir, filterText, selectedBackboneId, plddtMin, iptmMin, contactsMin]);

    // Fetch PDB content when design selected
    // Note: MolstarViewer now fetches structure directly from API URL

    // Auto-select first design
    useEffect(() => {
        if (designs.length > 0 && !selectedDesignId) {
            setSelectedDesignId(designs[0].id);
        }
    }, [designs, selectedDesignId]);

    const activeJob = jobs.find((j: Job) => j.id === selectedJobId);
    const selectedDesign = designs.find(d => d.id === selectedDesignId);
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

        // Binding tier distribution
        const tierCounts = { A: 0, B: 0, C: 0, D: 0, none: 0 };
        designs.forEach(d => {
            const tier = getBindingTier(d.iptm, d.epitope_contact_count);
            if (tier.tier === 'A') tierCounts.A++;
            else if (tier.tier === 'B') tierCounts.B++;
            else if (tier.tier === 'C') tierCounts.C++;
            else if (tier.tier === 'D') tierCounts.D++;
            else tierCounts.none++;
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
            highConfidence: plddts.filter(v => v >= 80).length,
            lowError: paes.filter(v => v <= 5).length,
            tierA: tierCounts.A,
            tierB: tierCounts.B,
            tierC: tierCounts.C,
            tierD: tierCounts.D,
        };
    }, [designs, totalDesigns]);

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

                                    // Build parent-children map and calculate aggregated design counts
                                    const parentChildMap = new Map<string, { parent: Job; children: Job[]; totalDesigns: number }>();
                                    const childJobIds = new Set<string>();

                                    // First pass: identify all child jobs
                                    sortedJobs.forEach(job => {
                                        if (job.parent_job_id) {
                                            childJobIds.add(job.id);
                                            const existing = parentChildMap.get(job.parent_job_id);
                                            if (existing) {
                                                existing.children.push(job);
                                                existing.totalDesigns += job.design_count || 0;
                                            } else {
                                                // Find the parent job
                                                const parent = sortedJobs.find(p => p.id === job.parent_job_id);
                                                if (parent) {
                                                    parentChildMap.set(job.parent_job_id, {
                                                        parent,
                                                        children: [job],
                                                        totalDesigns: (parent.design_count || 0) + (job.design_count || 0)
                                                    });
                                                }
                                            }
                                        }
                                    });

                                    const elements: React.ReactNode[] = [];

                                    // Render jobs (skip child jobs entirely)
                                    sortedJobs.forEach(job => {
                                        // Skip child jobs - they're aggregated under parent
                                        if (childJobIds.has(job.id)) {
                                            return;
                                        }

                                        const date = new Date(job.created_at);
                                        const dateStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                                        const timeStr = date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
                                        const statusIcon = job.status === 'completed' ? '✓' : job.status === 'running' ? '⟳' : job.status === 'failed' ? '✗' : '○';

                                        // Check if this is a parent with children
                                        const parentData = parentChildMap.get(job.id);
                                        const displayDesigns = parentData ? parentData.totalDesigns : (job.design_count || 0);
                                        const childIndicator = parentData ? ` (${parentData.children.length} batches)` : '';

                                        elements.push(
                                            <option key={job.id} value={job.id}>
                                                {statusIcon} {job.name} │ {dateStr} {timeStr} │ {job.model_id || job.mode}{childIndicator} │ {displayDesigns} designs
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

                        {/* Global Pagination Bar - visible on all tabs */}
                        {totalDesigns > 0 && (
                            <div className="flex items-center justify-between px-4 py-2 mb-2 bg-gradient-to-r from-slate-800/60 to-slate-900/60 rounded-lg border border-slate-700/50">
                                <div className="flex items-center gap-3">
                                    <span className="text-xs text-slate-400">
                                        {pageSize === 0 ? (
                                            <>Showing <span className="text-blue-400 font-semibold">all {totalDesigns.toLocaleString()}</span> designs</>
                                        ) : (
                                            <>Showing <span className="text-white font-medium">{((currentPage - 1) * pageSize) + 1}-{Math.min(currentPage * pageSize, totalDesigns)}</span> of <span className="text-blue-400 font-semibold">{totalDesigns.toLocaleString()}</span> designs</>
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
                                                <StatCard label="Total Designs" value={stats.total.toLocaleString()} />
                                                <StatCard label="Favorites" value={stats.favorites} color="text-yellow-400" />
                                                <StatCard label="Avg pLDDT" value={formatMetric(stats.avgPlddt, 1)} color="text-blue-400" />
                                                <StatCard label="Avg Affinity" value={formatMetric(stats.avgAffinity, 2)} color="text-emerald-400" />
                                                <StatCard label="Avg Binder %" value={stats.avgBinderProb ? (stats.avgBinderProb * 100).toFixed(0) + '%' : '—'} color="text-emerald-400" />
                                                <StatCard label="Avg pTM" value={formatMetric(stats.avgPtm, 2)} color="text-violet-400" />
                                                <StatCard label="High Confidence" value={stats.highConfidence} subtitle="pLDDT ≥ 80" color="text-emerald-400" />
                                            </div>

                                            {/* Binding Tier Distribution */}
                                            <div className="bg-gradient-to-br from-slate-800/60 to-slate-900/60 rounded-xl p-5 border border-slate-700/50">
                                                <h3 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                                                    <span>🔗</span>
                                                    Binding Quality Distribution
                                                    <span className="text-xs text-slate-500 font-normal">(based on iPTM)</span>
                                                </h3>
                                                <div className="grid grid-cols-4 gap-3">
                                                    <div className="bg-emerald-500/20 rounded-lg p-3 text-center border border-emerald-500/30">
                                                        <div className="text-2xl font-bold text-emerald-300">{stats.tierA}</div>
                                                        <div className="text-xs text-emerald-400 font-medium">Tier A</div>
                                                        <div className="text-[10px] text-slate-500">Excellent</div>
                                                    </div>
                                                    <div className="bg-blue-500/20 rounded-lg p-3 text-center border border-blue-500/30">
                                                        <div className="text-2xl font-bold text-blue-300">{stats.tierB}</div>
                                                        <div className="text-xs text-blue-400 font-medium">Tier B</div>
                                                        <div className="text-[10px] text-slate-500">Good</div>
                                                    </div>
                                                    <div className="bg-amber-500/20 rounded-lg p-3 text-center border border-amber-500/30">
                                                        <div className="text-2xl font-bold text-amber-300">{stats.tierC}</div>
                                                        <div className="text-xs text-amber-400 font-medium">Tier C</div>
                                                        <div className="text-[10px] text-slate-500">Moderate</div>
                                                    </div>
                                                    <div className="bg-red-500/20 rounded-lg p-3 text-center border border-red-500/30">
                                                        <div className="text-2xl font-bold text-red-300">{stats.tierD}</div>
                                                        <div className="text-xs text-red-400 font-medium">Tier D</div>
                                                        <div className="text-[10px] text-slate-500">Low</div>
                                                    </div>
                                                </div>
                                                <div className="mt-3 text-xs text-slate-500 flex items-center gap-3">
                                                    <span>Thresholds: A ≥ 0.75, B ≥ 0.55, C ≥ 0.40, D &lt; 0.40</span>
                                                    <span className="text-amber-500/80">+0.05 bonus for ≥5 epitope contacts</span>
                                                </div>
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

                                            {/* CDR Annotation Button - Only for antibody/nanobody workflows */}
                                            {(activeJob?.model_id === 'rfantibody' ||
                                                activeJob?.model_id === 'antibody_child' ||
                                                activeJob?.name?.toLowerCase().includes('antibody') ||
                                                activeJob?.mode?.toLowerCase().includes('antibody') ||
                                                activeJob?.mode?.toLowerCase().includes('nanobody') ||
                                                activeJob?.mode?.toLowerCase().includes('vhh') ||
                                                (activeJob?.model_id === 'boltzgen' && (
                                                    activeJob?.params?.nanobody_mode === true ||
                                                    activeJob?.mode?.toLowerCase().includes('nanobody') ||
                                                    activeJob?.mode?.toLowerCase().includes('vhh')
                                                ))) && (
                                                    <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
                                                        <h3 className="text-sm font-semibold text-slate-300 mb-3">Antibody CDR Annotation</h3>
                                                        <p className="text-xs text-slate-400 mb-4">
                                                            Run ANARCII to extract CDR loop sequences (H1, H2, H3, L1, L2, L3) from all designs.
                                                            This populates the CDR columns in the Data Table and enables CDR-based analytics.
                                                        </p>
                                                        <button
                                                            onClick={async () => {
                                                                const jobIdToUse = activeJob?.id || selectedJobId;
                                                                if (!jobIdToUse) return;
                                                                try {
                                                                    const btn = document.getElementById('cdr-annotate-btn');
                                                                    if (btn) {
                                                                        btn.textContent = '⏳ Annotating...';
                                                                        btn.setAttribute('disabled', 'true');
                                                                    }
                                                                    const res = await fetch(`/api/jobs/${jobIdToUse}/annotate-cdrs?include_children=true`, { method: 'POST' });
                                                                    const data = await res.json();
                                                                    alert(data.message || 'CDR annotation started - refresh in 1-2 minutes');
                                                                } catch (err) {
                                                                    alert('CDR annotation failed: ' + err);
                                                                }
                                                            }}
                                                            id="cdr-annotate-btn"
                                                            className="px-4 py-2 text-sm bg-violet-600 hover:bg-violet-500 text-white rounded-lg font-medium transition-colors flex items-center gap-2"
                                                        >
                                                            🧬 Annotate CDRs
                                                        </button>
                                                    </div>
                                                )}
                                        </div>
                                    )}

                                    {/* STRUCTURE TAB - Fullscreen-Aware with Overlays */}
                                    {activeTab === 'structure' && (
                                        <StructureViewerPane
                                            selectedDesignId={selectedDesignId}
                                            setSelectedDesignId={setSelectedDesignId}
                                            designs={designs}
                                            selectedDesign={selectedDesign}
                                            colorMode={colorMode}
                                            setColorMode={setColorMode}
                                            structureFormat={structureFormat}
                                            antibodySelections={antibodySelections}
                                            structureAnalysis={structureAnalysis}
                                            activeJob={activeJob}
                                            getMetricColor={getMetricColor}
                                        />
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
                                                                {/* Design Selector Dropdown */}
                                                                <div className="relative">
                                                                    <select
                                                                        value={selectedDesignId ?? ''}
                                                                        onChange={(e) => setSelectedDesignId(e.target.value)}
                                                                        className="appearance-none bg-slate-700/60 backdrop-blur-sm border border-slate-600/50 rounded-lg px-3 py-1 pr-8 text-xs text-blue-300 cursor-pointer hover:bg-slate-600/60 transition-colors min-w-[200px]"
                                                                    >
                                                                        {[...designs].sort((a, b) => (b.plddt_overall ?? 0) - (a.plddt_overall ?? 0)).map(d => (
                                                                            <option key={d.id} value={d.id}>
                                                                                {d.name} {d.plddt_overall ? `(${d.plddt_overall.toFixed(0)})` : ''}
                                                                            </option>
                                                                        ))}
                                                                    </select>
                                                                    <div className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400 text-xs">
                                                                        ▾
                                                                    </div>
                                                                </div>
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
                                            {/* Backbone Toggle Bar */}
                                            {backboneSummary && Object.keys(backboneSummary.backbones).length > 0 && (
                                                <div className="mb-4 p-3 bg-slate-800/50 rounded-xl border border-slate-700/50">
                                                    <div className="flex items-center gap-2 mb-2 flex-wrap overflow-x-auto max-h-24">
                                                        <span className="text-xs text-slate-400 font-medium shrink-0">Backbone:</span>
                                                        <button
                                                            onClick={() => setSelectedBackboneId(null)}
                                                            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${selectedBackboneId === null
                                                                ? 'bg-blue-500/30 text-blue-400 border border-blue-500/40'
                                                                : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                                                                }`}
                                                        >
                                                            All ({backboneSummary.total})
                                                        </button>
                                                        {Object.entries(backboneSummary.backbones).map(([id, data]) => (
                                                            <button
                                                                key={id}
                                                                onClick={() => setSelectedBackboneId(Number(id))}
                                                                className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${selectedBackboneId === Number(id)
                                                                    ? 'bg-emerald-500/30 text-emerald-400 border border-emerald-500/40'
                                                                    : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                                                                    }`}
                                                                title={`pLDDT: ${data.avg_plddt ?? '—'} | iPTM: ${data.avg_iptm ?? '—'}`}
                                                            >
                                                                #{id} ({data.count})
                                                            </button>
                                                        ))}
                                                    </div>
                                                    {/* Quality Filters */}
                                                    <div className="flex items-center gap-4 pt-2 border-t border-slate-700/50">
                                                        <div className="flex items-center gap-2">
                                                            <span className="text-xs text-slate-500">pLDDT ≥</span>
                                                            <input
                                                                type="range"
                                                                min="0"
                                                                max="100"
                                                                value={plddtMin}
                                                                onChange={(e) => setPlddtMin(Number(e.target.value))}
                                                                className="w-24 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
                                                            />
                                                            <span className="text-xs text-blue-400 font-mono w-8">{plddtMin}</span>
                                                        </div>
                                                        <div className="flex items-center gap-2">
                                                            <span className="text-xs text-slate-500">iPTM ≥</span>
                                                            <input
                                                                type="range"
                                                                min="0"
                                                                max="1"
                                                                step="0.05"
                                                                value={iptmMin}
                                                                onChange={(e) => setIptmMin(Number(e.target.value))}
                                                                className="w-24 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-emerald-500"
                                                            />
                                                            <span className="text-xs text-emerald-400 font-mono w-8">{iptmMin.toFixed(2)}</span>
                                                        </div>
                                                        <div className="flex items-center gap-2">
                                                            <span className="text-xs text-slate-500">Contacts ≥</span>
                                                            <input
                                                                type="range"
                                                                min="0"
                                                                max="20"
                                                                value={contactsMin}
                                                                onChange={(e) => setContactsMin(Number(e.target.value))}
                                                                className="w-24 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-amber-500"
                                                            />
                                                            <span className="text-xs text-amber-400 font-mono w-8">{contactsMin}</span>
                                                        </div>
                                                        <span className="text-xs text-slate-500 ml-auto">
                                                            Page {currentPage} • Showing {sortedDesigns.length} of {totalDesigns.toLocaleString()} designs
                                                        </span>
                                                    </div>
                                                </div>
                                            )}
                                            {/* Text Filter + Annotate CDRs */}
                                            <div className="mb-4 flex items-center gap-4">
                                                <input
                                                    type="text"
                                                    placeholder="Filter by name..."
                                                    value={filterText}
                                                    onChange={e => setFilterText(e.target.value)}
                                                    className="bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-sm w-64"
                                                />
                                                {/* Show Annotate CDRs only for antibody jobs */}
                                                {(activeJob?.model_id === 'rfantibody' ||
                                                    activeJob?.name?.toLowerCase().includes('antibody') ||
                                                    activeJob?.mode?.toLowerCase().includes('antibody')) && (
                                                        <button
                                                            onClick={async () => {
                                                                const jobIdToUse = activeJob?.id || selectedJobId;
                                                                if (!jobIdToUse) return;
                                                                try {
                                                                    const res = await fetch(`/api/jobs/${jobIdToUse}/annotate-cdr`, { method: 'POST' });
                                                                    const data = await res.json();
                                                                    alert(data.message || 'CDR annotation complete');
                                                                    // Refetch designs to show updated data
                                                                    window.location.reload();
                                                                } catch (err) {
                                                                    alert('CDR annotation failed: ' + err);
                                                                }
                                                            }}
                                                            className="px-4 py-2 text-sm bg-violet-600 hover:bg-violet-500 text-white rounded-lg font-medium transition-colors flex items-center gap-2"
                                                        >
                                                            🧬 Annotate CDRs
                                                        </button>
                                                    )}
                                            </div>
                                            {/* Table */}
                                            <div className="overflow-x-auto">
                                                <table className="w-full text-sm">
                                                    <thead>
                                                        <tr className="border-b border-slate-700">
                                                            {[
                                                                { key: 'name', label: 'Name' },
                                                                { key: 'binding_tier', label: 'Binding' },
                                                                { key: 'binder_length', label: 'Size' },
                                                                { key: 'cdr_h3_length', label: 'CDR-H3' },
                                                                { key: 'affinity_score', label: 'Affinity' },
                                                                { key: 'binder_probability', label: 'Binder %' },
                                                                { key: 'plddt_overall', label: 'pLDDT' },
                                                                { key: 'pae_overall', label: 'PAE' },
                                                                { key: 'ptm', label: 'pTM' },
                                                                { key: 'iptm', label: 'iPTM' },
                                                                { key: 'ligand_iptm', label: 'Lig iPTM' },
                                                                { key: 'conf_score', label: 'Conf' },
                                                                { key: 'rmsd_binder', label: 'RMSD' },
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

                                                                {/* Binding Quality Tier */}
                                                                <td className="px-3 py-2">
                                                                    {(() => {
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

                                                                {/* CDR-H3 Length */}
                                                                <td className="px-3 py-2 font-mono text-violet-400">
                                                                    {(d as any).cdr_h3_length ?? '—'}
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

                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('plddt_overall', d.plddt_overall)}`}>
                                                                    {formatMetric(d.plddt_overall, 1)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('pae_overall', d.pae_overall)}`}>
                                                                    {formatMetric(d.pae_overall, 1)}
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
                                                                <td className="px-3 py-2 font-mono text-slate-300">{formatMetric(d.rog, 1)}</td>
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
                                            designs={designs}
                                            jobName={activeJob?.name}
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
