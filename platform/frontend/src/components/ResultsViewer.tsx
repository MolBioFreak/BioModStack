import { useState, useEffect, useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';

import { fetchJobs, fetchDesigns, fetchStructureAnalysis, fetchAntibodyData, fetchBackboneSummary, launchAntibodyIteration } from '../lib/api';
import type { AntibodyCdrIndelConfig, AntibodyIterationAction, Job } from '../lib/api';
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
type OutputSourceFilter = 'all' | 'rfantibody' | 'fampnn' | 'validation';

// Formatting helpers
const formatMetric = (val: number | null | undefined, decimals = 2): string =>
    val != null ? val.toFixed(decimals) : '—';

const getMetricColor = (metric: string, value: number | null): string => {
    if (value == null) return 'text-slate-500';
    if (metric === 'plddt_overall' || metric === 'plddt_binder' || metric === 'plddt_target') {
        return value >= 80 ? 'text-emerald-400' : value >= 60 ? 'text-amber-400' : 'text-red-400';
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

const inferDesignOutputSource = (design: { pdb_path?: string | null; confidence_metrics?: Record<string, any> | null }): OutputSourceFilter => {
    const path = design.pdb_path || '';
    const metrics = design.confidence_metrics || {};
    if (path.includes('/validated_designs/') || path.includes('/collected/structure_validation/')) return 'validation';
    if (path.includes('/collected/fampnn/') || path.includes('/collected/fampnn_filtered/') || path.includes('/fampnn_filtered/')) return 'fampnn';
    if (path.includes('/collected/rfantibody/') || path.includes('/collected/rfantibody_raw/') || path.includes('/collected/rfantibody_filtered/') || path.includes('/rfantibody/')) return 'rfantibody';
    if (metrics && typeof metrics === 'object' && ('ranking_score' in metrics || 'gpde' in metrics || 'chain_pair_iptm' in metrics)) return 'validation';
    return 'all';
};

const getOutputSourceLabel = (design: { pdb_path?: string | null; confidence_metrics?: Record<string, any> | null }): string => {
    const source = inferDesignOutputSource(design);
    if (source === 'validation') {
        const metrics = design.confidence_metrics || {};
        if (metrics && typeof metrics === 'object' && ('ranking_score' in metrics || 'gpde' in metrics || 'chain_pair_iptm' in metrics)) {
            return 'Protenix';
        }
        return 'Validation';
    }
    if (source === 'fampnn') return 'FAMPNN';
    if (source === 'rfantibody') return 'RFantibody';
    return 'Other';
};

const getFriendlyDesignName = (design: { name: string; pdb_path?: string | null; confidence_metrics?: Record<string, any> | null }): string => {
    const source = inferDesignOutputSource(design);
    const sampleMatch = design.name.match(/_sample_(\d+)$/);
    if (source === 'validation' && sampleMatch) {
        return `${getOutputSourceLabel(design)} Sample ${sampleMatch[1]}`;
    }
    if (source === 'fampnn') return 'FAMPNN Candidate';
    if (source === 'rfantibody') return 'RFantibody Backbone';
    return design.name;
};

const parseCustomCdrLengths = (job: Job | null | undefined): Record<string, number> => {
    const loopsRaw = job?.params?.antibody_design_loops;
    const rangesRaw = job?.params?.rfantibody_design_loops_custom;
    if (!loopsRaw || !rangesRaw) return {};

    const loopNames = Array.isArray(loopsRaw)
        ? loopsRaw.map(String).map(v => v.trim()).filter(Boolean)
        : String(loopsRaw).split(',').map(v => v.trim()).filter(Boolean);

    const rangeValues = Array.isArray(rangesRaw)
        ? rangesRaw.map(String).map(v => v.trim()).filter(Boolean)
        : String(rangesRaw).replace(/^\[/, '').replace(/\]$/, '').split(',').map(v => v.trim()).filter(Boolean);

    if (loopNames.length !== rangeValues.length) return {};

    const out: Record<string, number> = {};
    loopNames.forEach((loopName, idx) => {
        const m = rangeValues[idx]?.match(/^[A-Za-z](\d+)-(\d+)$/);
        if (!m) return;
        const start = Number(m[1]);
        const end = Number(m[2]);
        if (Number.isFinite(start) && Number.isFinite(end) && end >= start) {
            out[loopName] = end - start + 1;
        }
    });
    return out;
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
    const [showCdrIndelModal, setShowCdrIndelModal] = useState(false);
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

    const [paramOverridesText, setParamOverridesText] = useState('');
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
    const [plddtMin, setPlddtMin] = useState<number>(0);
    const [iptmMin, setIptmMin] = useState<number>(0);
    const [contactsMin, setContactsMin] = useState<number>(0);
    const [rogMin, setRogMin] = useState<string>('');
    const [rogMax, setRogMax] = useState<string>('');
    const [rfdRogMin, setRfdRogMin] = useState<string>('');
    const [rfdRogMax, setRfdRogMax] = useState<string>('');
    // const MAX_COMPARE_VIEWERS = 3; // unused

    // Pagination state for large design sets
    const [pageSize, setPageSize] = useState<number>(100);
    const [currentPage, setCurrentPage] = useState<number>(1);
    const PAGE_SIZE_OPTIONS = [50, 100, 250, 500, 1000, 0]; // 0 = All

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

    // Sync URL with selection
    useEffect(() => {
        const fallbackJob = nonNgsJobs.find((j: Job) => j.status === 'completed') ?? nonNgsJobs[0];

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

            if (fallbackJob) {
                if (selectedJobId !== fallbackJob.id) {
                    setSelectedJobId(fallbackJob.id);
                    setSelectedDesignId('');
                }
                navigate(`/designs/${fallbackJob.id}`, { replace: true });
            }
            return;
        }

        if (!activeJob && fallbackJob) {
            setSelectedJobId(fallbackJob.id);
            setSelectedDesignId('');
            navigate(`/designs/${fallbackJob.id}`, { replace: true });
        }
    }, [jobId, nonNgsJobs, selectedJobId, activeJob, navigate]);

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

    const { data: designsData, isLoading: designsLoading } = useQuery({
        queryKey: ['designs', selectedJobId, currentPage, pageSize, sortField, sortDir, selectedBackboneId, rogMinValue, rogMaxValue, rfdRogMinValue, rfdRogMaxValue],
        queryFn: () => fetchDesigns({
            job_id: selectedJobId,
            include_children: true, // Include designs from child jobs (mutagenesis variants, exploration spawns)
            limit: pageSize === 0 ? 10000 : pageSize, // 0 = All (fetch up to 10000)
            offset: pageSize === 0 ? 0 : (currentPage - 1) * pageSize,
            sort_by: sortField as 'plddt' | 'iptm' | 'ptm' | 'pae' | 'rog' | 'rfd_rog' | 'backbone' | 'frustration_high_count' | 'frustration_pct_high' | undefined,
            sort_desc: sortDir === 'desc',
            backbone_id: selectedBackboneId ?? undefined,
            rog_min: rogMinValue,
            rog_max: rogMaxValue,
            rfd_rog_min: rfdRogMinValue,
            rfd_rog_max: rfdRogMaxValue,
        }),
        enabled: !!activeJob,
    });
    const designs = designsData?.data.designs ?? [];
    const totalDesigns = designsData?.data.total ?? 0;
    const totalPages = pageSize === 0 ? 1 : Math.ceil(totalDesigns / pageSize);

    // Fetch backbone summary for toggle UI
    const { data: backboneSummaryData } = useQuery({
        queryKey: ['backboneSummary', selectedJobId],
        queryFn: () => fetchBackboneSummary(selectedJobId),
        enabled: !!activeJob,
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
        // RoG filter
        if (rogMinValue !== undefined) {
            filtered = filtered.filter(d => (d.rog ?? 0) >= rogMinValue);
        }
        if (rogMaxValue !== undefined) {
            filtered = filtered.filter(d => (d.rog ?? 0) <= rogMaxValue);
        }
        if (rfdRogMinValue !== undefined) {
            filtered = filtered.filter(d => (d.rfd_rog ?? 0) >= rfdRogMinValue);
        }
        if (rfdRogMaxValue !== undefined) {
            filtered = filtered.filter(d => (d.rfd_rog ?? 0) <= rfdRogMaxValue);
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
    }, [designs, sortField, sortDir, filterText, selectedBackboneId, plddtMin, iptmMin, contactsMin, rogMinValue, rogMaxValue, rfdRogMinValue, rfdRogMaxValue]);
    const selectedDesignSet = useMemo(() => new Set(selectedDesignIds), [selectedDesignIds]);
    const cdrLengthFallbacks = useMemo(() => parseCustomCdrLengths(activeJob), [activeJob]);
    const tableDesigns = useMemo(() => {
        if (outputSourceFilter === 'all') return sortedDesigns;
        return sortedDesigns.filter((design) => inferDesignOutputSource(design as any) === outputSourceFilter);
    }, [sortedDesigns, outputSourceFilter]);
    const currentPageDesignIds = useMemo(() => tableDesigns.map((design) => design.id), [tableDesigns]);
    const allCurrentPageSelected = currentPageDesignIds.length > 0 && currentPageDesignIds.every((designId) => selectedDesignSet.has(designId));

    // Fetch PDB content when design selected
    // Note: MolstarViewer now fetches structure directly from API URL

    // Auto-select first design
    useEffect(() => {
        if (designs.length > 0 && !selectedDesignId) {
            setSelectedDesignId(designs[0].id);
        }
    }, [designs, selectedDesignId]);

    useEffect(() => {
        setSelectedDesignIds([]);
        setIterationMessage(null);
    }, [selectedJobId]);

    // For oligo_design jobs: default to element coloring (B-factors are design confidence, not pLDDT)
    const isOligoJob = (activeJob?.model_id || '').toLowerCase().includes('oligo');
    useEffect(() => {
        if (isOligoJob) {
            setColorMode('default');
        } else if ((activeJob?.name || '').toLowerCase().includes('frustrampnn')) {
            setColorMode('frustration');
        } else {
            setColorMode('plddt');
        }
    }, [selectedJobId, isOligoJob, activeJob?.name]);
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
        const epitopeContacts = designs.map(d => d.epitope_contact_count).filter((v): v is number => v != null);
        const psces = designs.map(d => d.fampnn_psce).filter((v): v is number => v != null);
        const frustrationHigh = designs.map(d => d.frustration_high_count).filter((v): v is number => v != null);
        const frustrationPct = designs.map(d => d.frustration_pct_high).filter((v): v is number => v != null);

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
            avgEpitopeContacts: epitopeContacts.length ? epitopeContacts.reduce((a, b) => a + b, 0) / epitopeContacts.length : null,
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

    const toggleDesignSelection = (designId: string, selected: boolean) => {
        setSelectedDesignIds((current) => {
            const currentSet = new Set(current);
            if (selected) currentSet.add(designId);
            else currentSet.delete(designId);
            return Array.from(currentSet);
        });
    };

    const selectCurrentPage = () => {
        setSelectedDesignIds((current) => Array.from(new Set([...current, ...currentPageDesignIds])));
    };

    const clearSelectedDesigns = () => {
        setSelectedDesignIds([]);
    };

    const toggleCurrentPageSelection = (selected: boolean) => {
        if (selected) {
            selectCurrentPage();
            return;
        }
        setSelectedDesignIds((current) => current.filter((designId) => !currentPageDesignIds.includes(designId)));
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
            if (selectedDesignIds.length === 0) {
                throw new Error('Select at least one design before launching a new round.');
            }
            return launchAntibodyIteration({
                source_job_id: selectedJobId,
                design_ids: selectedDesignIds,
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

    return (
        <div className="min-h-screen bg-slate-950 text-slate-200">
            {/* Background */}
            <div className="fixed inset-0 pointer-events-none">
                <div className="absolute top-0 left-0 w-1/2 h-1/2 bg-blue-500/5 rounded-full blur-[150px]" />
                <div className="absolute bottom-0 right-0 w-1/2 h-1/2 bg-violet-500/5 rounded-full blur-[150px]" />
            </div>

            <div className={`relative z-10 px-4 md:px-6 lg:px-8 ${activeTab === 'charts' ? 'w-full' : 'max-w-[1800px] mx-auto'}`}>
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
                                        const displayDesigns = hasChildren ? batchData.totalDesigns : (job.design_count || 0);
                                        const childIndicator = hasChildren ? ` (${batchData.children.length} variants)` : '';

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

                {nonNgsJobs.length === 0 && (
                    <div className="bg-slate-900/50 rounded-xl border border-slate-800 p-8 text-center text-slate-400">
                        No protein workflow jobs available for Data Viewer. NGS jobs are available in NGS Data Visualization Toolkit.
                    </div>
                )}

                {activeJob && (
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

                        {isAntibodyContext && (
                            <div className="mb-4 rounded-xl border border-cyan-500/20 bg-cyan-500/5 p-4">
                                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                    <div>
                                        <div className="text-sm font-medium text-cyan-200">Antibody Iteration Set</div>
                                        <p className="mt-1 text-xs text-slate-400">
                                            Use the Data Table filters and check rows to define a working set, then launch the next round directly from this viewer.
                                        </p>
                                        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                                            <span className="rounded-full border border-cyan-500/30 bg-slate-900/70 px-2 py-1 text-cyan-200">
                                                {selectedDesignIds.length} selected
                                            </span>
                                            <span className="rounded-full border border-slate-700 bg-slate-900/70 px-2 py-1 text-slate-400">
                                                {sortedDesigns.length} on current filtered page
                                            </span>
                                            {launchIterationMutation.isPending && (
                                                <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-amber-200">
                                                    Launching {launchIterationMutation.variables?.action}...
                                                </span>
                                            )}
                                        </div>
                                    </div>

                                    <div className="flex flex-wrap gap-2">
                                        <button
                                            type="button"
                                            onClick={selectCurrentPage}
                                            disabled={currentPageDesignIds.length === 0}
                                            className="rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-2 text-xs text-slate-200 transition-colors hover:border-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
                                        >
                                            Select Page
                                        </button>
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
                                                    let parsedOverrides = undefined;
                                                    if (paramOverridesText.trim()) {
                                                        try {
                                                            parsedOverrides = JSON.parse(paramOverridesText);
                                                        } catch (e) {
                                                            setIterationMessage({ kind: 'error', text: 'Invalid JSON in parameter overrides.' });
                                                            return;
                                                        }
                                                    }
                                                    launchIterationMutation.mutate({ action, paramOverrides: parsedOverrides });
                                                }}
                                                disabled={selectedDesignIds.length === 0 || launchIterationMutation.isPending}
                                                className={`rounded-lg border px-3 py-2 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${action === 'validate_protenix'
                                                        ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200 hover:border-cyan-400'
                                                        : action === 'validate_boltz2'
                                                            ? 'border-blue-500/40 bg-blue-500/10 text-blue-200 hover:border-blue-400'
                                                            : action === 'ppiflow_maturation'
                                                                ? 'border-teal-500/40 bg-teal-500/10 text-teal-200 hover:border-teal-400'
                                                                : action === 'fampnn_redesign'
                                                                    ? 'border-violet-500/40 bg-violet-500/10 text-violet-200 hover:border-violet-400'
                                                                    : 'border-amber-500/40 bg-amber-500/10 text-amber-200 hover:border-amber-400'
                                                    }`}
                                                title={
                                                    action === 'validate_boltz2'
                                                        ? 'Re-run Boltz-2 validation on the selected set and pause at structure review.'
                                                        : action === 'validate_protenix'
                                                            ? 'Re-run Protenix validation on the selected set and pause at structure review.'
                                                            : action === 'ppiflow_maturation'
                                                                ? 'Run post-validation PPIFlow maturation on the selected set.'
                                                                : action === 'fampnn_redesign'
                                                                    ? 'Use the selected structures as the next FAMPNN redesign inputs.'
                                                                    : 'Run FrustraMPNN analysis on the selected set.'
                                                }
                                            >
                                                {label}
                                            </button>
                                        ))}
                                        <button
                                            type="button"
                                            onClick={() => {
                                                setIterationMessage(null);
                                                setShowCdrIndelModal(true);
                                            }}
                                            disabled={selectedDesignIds.length === 0 || launchIterationMutation.isPending}
                                            className="rounded-lg border border-fuchsia-500/40 bg-fuchsia-500/10 px-3 py-2 text-xs text-fuchsia-200 transition-colors hover:border-fuchsia-400 disabled:cursor-not-allowed disabled:opacity-50"
                                            title="Generate explicit CDR insertion/deletion variants from the selected set, then validate them as a new round."
                                        >
                                            CDR Indels
                                        </button>
                                    </div>
                                </div>

                                {showParamOverrides && (
                                    <div className="mt-4 border-t border-slate-700/50 pt-4">
                                        <label className="block text-xs text-slate-400 mb-2">
                                            Parameter Overrides (JSON)
                                            <span className="ml-2 text-[10px] text-slate-500 font-normal">
                                                Merged into the source job's configuration. Example: {`{"run_structure_validation": false, "structure_validator": "boltz2"}`}
                                            </span>
                                        </label>
                                        <textarea
                                            value={paramOverridesText}
                                            onChange={(e) => setParamOverridesText(e.target.value)}
                                            placeholder="{\n  \n}"
                                            className="w-full h-24 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs font-mono text-slate-300 focus:outline-none focus:border-indigo-500/50"
                                            spellCheck={false}
                                        />
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

                        {showCdrIndelModal && isAntibodyContext && (
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
                                            <div>{selectedDesignIds.length} selected design{selectedDesignIds.length === 1 ? '' : 's'}</div>
                                            <div>{cdrIndelConfig.variants_per_design} variant{cdrIndelConfig.variants_per_design === 1 ? '' : 's'} per design</div>
                                            <div className="mt-1 text-fuchsia-200">
                                                {selectedDesignIds.length * cdrIndelConfig.variants_per_design} total variant predictions
                                            </div>
                                            {cdrIndelConfig.msa_provider === 'colabfold_api' && selectedDesignIds.length * cdrIndelConfig.variants_per_design > 1 && (
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
                                                    let parsedOverrides = undefined;
                                                    if (paramOverridesText.trim()) {
                                                        try {
                                                            parsedOverrides = JSON.parse(paramOverridesText);
                                                        } catch (e) {
                                                            setIterationMessage({ kind: 'error', text: 'Invalid JSON in parameter overrides.' });
                                                            return;
                                                        }
                                                    }
                                                    launchIterationMutation.mutate({
                                                        action: 'cdr_indel_round',
                                                        cdrIndelConfig,
                                                        paramOverrides: parsedOverrides,
                                                    });
                                                }}
                                                disabled={
                                                    launchIterationMutation.isPending ||
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
                                            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-4">
                                                <StatCard label="Total Designs" value={stats.total.toLocaleString()} />
                                                <StatCard label="Favorites" value={stats.favorites} color="text-yellow-400" />
                                                <StatCard label="Avg pLDDT" value={formatMetric(stats.avgPlddt, 1)} color="text-blue-400" />
                                                <StatCard label="Avg pSCE" value={formatMetric(stats.avgPsce, 2)} subtitle="FAMPNN" color="text-cyan-400" />
                                                <StatCard label="Avg Affinity" value={formatMetric(stats.avgAffinity, 2)} color="text-emerald-400" />
                                                <StatCard label="Avg Binder %" value={stats.avgBinderProb ? (stats.avgBinderProb * 100).toFixed(0) + '%' : '—'} color="text-emerald-400" />
                                                <StatCard label="Avg pTM" value={formatMetric(stats.avgPtm, 2)} color="text-violet-400" />
                                                <StatCard label="Avg Contacts" value={formatMetric(stats.avgEpitopeContacts, 1)} color="text-lime-400" />
                                                <StatCard label="High Contacts" value={stats.highContacts} subtitle="≥5 epitope" color="text-lime-400" />
                                                {stats.annotatedWithFrustration > 0 && (
                                                    <>
                                                        <StatCard label="Avg High Frust" value={formatMetric(stats.avgFrustrationHigh, 1)} color="text-red-400" />
                                                        <StatCard label="Avg % High Frust" value={stats.avgFrustrationPctHigh != null ? `${stats.avgFrustrationPctHigh.toFixed(1)}%` : '—'} color="text-orange-400" />
                                                    </>
                                                )}
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
                                                    <p className="text-xs mt-2 opacity-60">If this is an antibody job, ensure ANARCII processing succeeded.</p>
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
                                                                <div className="w-2 h-2 rounded-full bg-accent-secondary"></div>
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
                                                        <div className="flex items-center gap-2">
                                                            <span className="text-xs text-slate-500">RoG</span>
                                                            <input
                                                                type="number"
                                                                min="0"
                                                                step="0.1"
                                                                value={rogMin}
                                                                onChange={(e) => setRogMin(e.target.value)}
                                                                placeholder="min"
                                                                className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                            />
                                                            <span className="text-xs text-slate-500">–</span>
                                                            <input
                                                                type="number"
                                                                min="0"
                                                                step="0.1"
                                                                value={rogMax}
                                                                onChange={(e) => setRogMax(e.target.value)}
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
                                                                value={rfdRogMin}
                                                                onChange={(e) => setRfdRogMin(e.target.value)}
                                                                placeholder="min"
                                                                className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                            />
                                                            <span className="text-xs text-slate-500">–</span>
                                                            <input
                                                                type="number"
                                                                min="0"
                                                                step="0.1"
                                                                value={rfdRogMax}
                                                                onChange={(e) => setRfdRogMax(e.target.value)}
                                                                placeholder="max"
                                                                className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 font-mono"
                                                            />
                                                        </div>
                                                        <span className="text-xs text-slate-500 ml-auto">
                                                            Page {currentPage} • Showing {tableDesigns.length} of {totalDesigns.toLocaleString()} designs
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
                                                <span className="text-xs text-slate-500">
                                                    Check rows to build a launch set. Row clicks still open the structure view.
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
                                                    {tableDesigns.length} rows in current output set
                                                </span>
                                            </div>
                                            {/* Table */}
                                            <div className="overflow-x-auto">
                                                <table className="w-full text-sm">
                                                    <thead>
                                                        <tr className="border-b border-slate-700">
                                                            {[
                                                                {
                                                                    key: 'selected', label: (
                                                                        <input
                                                                            type="checkbox"
                                                                            checked={allCurrentPageSelected}
                                                                            onChange={(e) => toggleCurrentPageSelection(e.target.checked)}
                                                                            className="h-4 w-4 rounded border-slate-600 bg-slate-900 text-cyan-500"
                                                                            title={allCurrentPageSelected ? 'Clear current page selection' : 'Select current page'}
                                                                        />
                                                                    )
                                                                },
                                                                { key: 'name', label: 'Output' },
                                                                { key: 'binding_tier', label: 'Binding' },
                                                                { key: 'binder_length', label: 'Size' },
                                                                { key: 'cdr_h3_length', label: 'CDR-H3' },
                                                                { key: 'epitope_contact_count', label: 'Contacts' },
                                                                { key: 'epitope_min_distance', label: 'Min Dist' },
                                                                { key: 'affinity_score', label: 'Affinity' },
                                                                { key: 'binder_probability', label: 'Binder %' },
                                                                { key: 'fampnn_psce', label: 'pSCE' },
                                                                { key: 'plddt_overall', label: 'pLDDT' },
                                                                { key: 'plddt_binder', label: 'pLDDT Bd' },
                                                                { key: 'plddt_target', label: 'pLDDT Tgt' },
                                                                { key: 'pae_overall', label: 'PAE' },
                                                                { key: 'pae_interaction', label: 'iPAE' },
                                                                { key: 'ptm', label: 'pTM' },
                                                                { key: 'iptm', label: 'iPTM' },
                                                                { key: 'ligand_iptm', label: 'Lig iPTM' },
                                                                { key: 'conf_score', label: 'Conf' },
                                                                { key: 'rmsd_binder', label: 'Val RMSD Bd' },
                                                                { key: 'rmsd_overall', label: 'Val RMSD All' },
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
                                                                    {(d as any).cdr_h3_length ?? cdrLengthFallbacks.H3 ?? '—'}
                                                                </td>

                                                                {/* Epitope Contact Count */}
                                                                <td className={`px-3 py-2 font-mono ${(d.epitope_contact_count ?? 0) >= 5 ? 'text-emerald-400' :
                                                                    (d.epitope_contact_count ?? 0) > 0 ? 'text-amber-400' : 'text-slate-500'}`}>
                                                                    {d.epitope_contact_count ?? '—'}
                                                                </td>

                                                                {/* Epitope Min Distance */}
                                                                <td className={`px-3 py-2 font-mono ${d.epitope_min_distance != null && d.epitope_min_distance <= 4 ? 'text-emerald-400' :
                                                                    d.epitope_min_distance != null && d.epitope_min_distance <= 8 ? 'text-amber-400' : 'text-slate-500'}`}>
                                                                    {formatMetric(d.epitope_min_distance, 1)}
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
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('plddt_binder', d.plddt_binder)}`}>
                                                                    {formatMetric(d.plddt_binder, 1)}
                                                                </td>
                                                                <td className={`px-3 py-2 font-mono ${getMetricColor('plddt_target', d.plddt_target)}`}>
                                                                    {formatMetric(d.plddt_target, 1)}
                                                                </td>
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
