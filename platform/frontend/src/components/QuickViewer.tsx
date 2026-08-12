/**
 * QuickViewer - Compact structure preview widget for dashboard
 * Shows a dropdown to select a recent completed job and displays the structure
 * 
 * Can be controlled externally via selectedJobId prop for integration with other components
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { StructureWorkbench } from '../structureViewer/StructureWorkbench';
import { BMS_CONTROL, BMS_CONTROL_GROUP, BMS_FULLSCREEN_FLUSH, BMS_PANEL_SURFACE, BMS_SMALL_CONTROL, BMS_VIEWER_WELL } from './ui/bmsStyle';
import { fetchJobs } from '../lib/api';
import type { Job } from '../lib/api';
import { jobPollingInterval } from '../lib/queryPolling';
import { isNgsJob } from '../lib/ngsResultRouting';

const QUICK_VIEWER_COMPACT_KEY = 'bms_dashboard_quick_viewer_compact_v1';
type QuickViewerSize = 'micro' | 'compact' | 'standard' | 'large' | 'xlarge';
const QUICK_VIEWER_SIZE_OPTIONS: ReadonlyArray<{ value: QuickViewerSize; label: string; title: string }> = [
    { value: 'micro', label: 'XS', title: 'Very compact viewer' },
    { value: 'compact', label: 'S', title: 'Compact viewer' },
    { value: 'standard', label: 'M', title: 'Standard viewer' },
    { value: 'large', label: 'L', title: 'Large viewer' },
    { value: 'xlarge', label: 'XL', title: 'Maximum viewer size' },
];

interface QuickViewerLayout {
    panelMaxWidthClass: string;
    panelPaddingClass: string;
    viewerHeight: string;
    titleClass: string;
    selectorClass: string;
    structureSelectorClass: string;
    stackHeader: boolean;
    showJobIndicator: boolean;
}

const QUICK_VIEWER_LAYOUTS: Record<QuickViewerSize, QuickViewerLayout> = {
    micro: {
        panelMaxWidthClass: 'max-w-3xl',
        panelPaddingClass: 'p-3',
        viewerHeight: 'clamp(120px, 18vw, 148px)',
        titleClass: 'text-xs',
        selectorClass: 'px-2 py-1 text-xs',
        structureSelectorClass: 'px-2 py-1 text-[11px]',
        stackHeader: true,
        showJobIndicator: false,
    },
    compact: {
        panelMaxWidthClass: 'max-w-5xl',
        panelPaddingClass: 'p-3.5',
        viewerHeight: 'clamp(156px, 20vw, 208px)',
        titleClass: 'text-sm',
        selectorClass: 'px-2.5 py-1.5 text-sm',
        structureSelectorClass: 'px-2.5 py-1.5 text-xs',
        stackHeader: true,
        showJobIndicator: true,
    },
    standard: {
        panelMaxWidthClass: 'max-w-6xl',
        panelPaddingClass: 'p-4',
        viewerHeight: 'clamp(228px, 24vw, 320px)',
        titleClass: 'text-sm',
        selectorClass: 'px-3 py-1.5 text-sm',
        structureSelectorClass: 'px-3 py-1.5 text-xs',
        stackHeader: false,
        showJobIndicator: true,
    },
    large: {
        panelMaxWidthClass: 'max-w-none',
        panelPaddingClass: 'p-4',
        viewerHeight: 'clamp(288px, 30vw, 412px)',
        titleClass: 'text-sm',
        selectorClass: 'px-3 py-1.5 text-sm',
        structureSelectorClass: 'px-3 py-1.5 text-xs',
        stackHeader: false,
        showJobIndicator: true,
    },
    xlarge: {
        panelMaxWidthClass: 'max-w-none',
        panelPaddingClass: 'p-4',
        viewerHeight: 'clamp(360px, 36vw, 520px)',
        titleClass: 'text-sm',
        selectorClass: 'px-3 py-1.5 text-sm',
        structureSelectorClass: 'px-3 py-1.5 text-xs',
        stackHeader: false,
        showJobIndicator: true,
    },
};

const readQuickViewerSizePreference = (): QuickViewerSize => {
    try {
        const stored = localStorage.getItem(QUICK_VIEWER_COMPACT_KEY);
        if (stored === 'true') return 'compact';
        if (stored === 'false' || stored == null) return 'large';
        if (QUICK_VIEWER_SIZE_OPTIONS.some((option) => option.value === stored)) {
            return stored as QuickViewerSize;
        }
    } catch {
        // Fall back to the default below.
    }
    return 'large';
};

interface StructureFile {
    name: string;
    filename: string;
    path: string;
    type: 'pdb' | 'cif';
    size_bytes: number;
}

interface QuickViewerProps {
    /** Externally controlled job ID (optional) */
    selectedJobId?: string | null;
    /** Callback when job is changed via dropdown */
    onJobChange?: (jobId: string | null) => void;
}

const isMolecularDynamicsJob = (job: Job) =>
    job.model_id === 'molecular_dynamics' || job.mode === 'molecular_dynamics' || job.mode === 'md';

export function QuickViewer({ selectedJobId: externalJobId, onJobChange }: QuickViewerProps) {
    // Use internal state if not externally controlled
    const [internalJobId, setInternalJobId] = useState<string | null>(null);
    const [selectedStructure, setSelectedStructure] = useState<StructureFile | null>(null);
    const [viewerSize, setViewerSize] = useState<QuickViewerSize>('large');
    const [isFullscreen, setIsFullscreen] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);

    // Use external ID if provided, otherwise internal
    const isControlled = externalJobId !== undefined;
    const selectedJobId = isControlled ? externalJobId : internalJobId;

    const setSelectedJobId = (id: string | null) => {
        if (isControlled && onJobChange) {
            onJobChange(id);
        } else {
            setInternalJobId(id);
        }
    };

    // Fetch jobs
    const { data: jobsData } = useQuery({
        queryKey: ['jobs', 'quick-viewer-summary'],
        queryFn: () => fetchJobs({ status: 'completed', limit: 100, summary: true }),
        refetchInterval: (query) => jobPollingInterval(3000, query),
    });
    const { data: selectedJobData } = useQuery({
        queryKey: ['quick-viewer-selected-job', selectedJobId],
        queryFn: () => fetchJobs({ q: selectedJobId as string, limit: 100, summary: true }),
        enabled: Boolean(selectedJobId),
    });

    // Get completed jobs with structures
    const allJobs = jobsData?.data?.jobs || [];
    const completedJobs = allJobs.filter(
        (job: Job) => job.status === 'completed' && !isMolecularDynamicsJob(job) && !isNgsJob(job)
    );
    const selectedJobRecord = selectedJobData?.data?.jobs.find((job) => job.id === selectedJobId);
    const selectedJobIsExcluded = Boolean(selectedJobRecord && (
        isMolecularDynamicsJob(selectedJobRecord) || isNgsJob(selectedJobRecord)
    )) || allJobs.some(
        (job: Job) => job.id === selectedJobId && (isMolecularDynamicsJob(job) || isNgsJob(job))
    );
    const quickViewerJobId = selectedJobIsExcluded ? null : selectedJobId;

    // Fetch structure files for selected job
    const { data: structureData } = useQuery<{ structures: StructureFile[]; count: number }>({
        queryKey: ['structure-files', quickViewerJobId],
        queryFn: async () => {
            const res = await fetch(`/api/jobs/${quickViewerJobId}/structure-files`);
            if (!res.ok) throw new Error('Failed to fetch structure files');
            return res.json();
        },
        enabled: !!quickViewerJobId,
    });

    // Auto-select first structure when data loads
    useEffect(() => {
        if (structureData?.structures && structureData.structures.length > 0) {
            setSelectedStructure(structureData.structures[0]);
        } else {
            setSelectedStructure(null);
        }
    }, [structureData]);

    // Auto-select first completed job on mount (only for uncontrolled mode)
    useEffect(() => {
        if (!isControlled && !internalJobId && completedJobs.length > 0) {
            setInternalJobId(completedJobs[0].id);
        }
    }, [completedJobs, internalJobId, isControlled]);

    useEffect(() => {
        setViewerSize(readQuickViewerSizePreference());
    }, []);

    useEffect(() => {
        const handleFullscreenChange = () => {
            const activeElement = document.fullscreenElement;
            setIsFullscreen(activeElement === containerRef.current);
        };
        document.addEventListener('fullscreenchange', handleFullscreenChange);
        return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
    }, []);

    const setViewerSizePreference = (nextSize: QuickViewerSize) => {
        setViewerSize(nextSize);
        try {
            localStorage.setItem(QUICK_VIEWER_COMPACT_KEY, nextSize);
        } catch {
            // Ignore localStorage write failures and keep in-memory state.
        }
    };

    const toggleFullscreen = useCallback(async () => {
        const node = containerRef.current;
        if (!node) return;

        try {
            if (document.fullscreenElement === node) {
                await document.exitFullscreen();
                return;
            }

            if (document.fullscreenElement && document.fullscreenElement !== node) {
                await document.exitFullscreen();
            }

            await node.requestFullscreen();
        } catch (error) {
            console.error('Failed to toggle quick viewer fullscreen:', error);
        }
    }, []);

    const structureUrl = selectedStructure
        ? `/api/files/pdb/${selectedStructure.path}`
        : null;

    const layout = QUICK_VIEWER_LAYOUTS[viewerSize];
    const viewerHeight = isFullscreen ? '100%' : layout.viewerHeight;
    const hideViewerControls = !isFullscreen && (viewerSize === 'micro' || viewerSize === 'compact');
    const stackHeader = isFullscreen ? false : layout.stackHeader;
    const showJobIndicator = isFullscreen || layout.showJobIndicator;

    // Find current job name for display
    const currentJob = completedJobs.find((j: Job) => j.id === quickViewerJobId);

    return (
        <div
            ref={containerRef}
            className={isFullscreen ? 'h-full w-full bg-slate-950 p-4 md:p-6' : `mx-auto w-full transition-all duration-300 ${layout.panelMaxWidthClass}`}
        >
            <div
                className={`${BMS_PANEL_SURFACE} transition-all duration-300 ${
                    isFullscreen
                        ? `flex h-full flex-col ${BMS_FULLSCREEN_FLUSH} p-4 md:p-6`
                        : `${layout.panelPaddingClass}`
                }`}
                style={{ position: 'relative' }}
            >
            {/* Header */}
            <div
                className={`mb-3 flex gap-2 ${stackHeader ? 'flex-col' : 'flex-col sm:flex-row sm:items-center sm:justify-between'}`}
                style={{ position: 'relative', zIndex: 10 }}
            >
                <h3 className={`font-semibold text-slate-200 ${layout.titleClass}`}>Quick Viewer</h3>
                <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                    {selectedStructure && (
                        <span className={`px-2 py-0.5 rounded text-xs font-medium uppercase ${selectedStructure.type === 'pdb'
                            ? 'bg-green-500/20 text-green-400'
                            : 'bg-blue-500/20 text-blue-400'
                            }`}>
                            {selectedStructure.type}
                        </span>
                    )}
                    <div className={`inline-flex flex-wrap items-center gap-1 p-1 ${BMS_CONTROL_GROUP}`}>
                        {QUICK_VIEWER_SIZE_OPTIONS.map((option) => {
                            const active = viewerSize === option.value;
                            return (
                                <button
                                    key={option.value}
                                    type="button"
                                    onClick={() => setViewerSizePreference(option.value)}
                                    className={`min-w-9 px-2 py-1 text-[11px] font-semibold transition-colors ${
                                        active
                                            ? 'rounded border border-accent bg-accent/20 text-white'
                                            : `${BMS_SMALL_CONTROL} text-slate-300 hover:bg-slate-700`
                                    }`}
                                    title={option.title}
                                >
                                    {option.label}
                                </button>
                            );
                        })}
                    </div>
                    <button
                        type="button"
                        onClick={() => void toggleFullscreen()}
                        className={`${BMS_CONTROL} px-3 py-1.5 text-[11px] font-semibold text-slate-200 transition-colors hover:bg-slate-700`}
                        title={isFullscreen ? 'Exit fullscreen' : 'Open fullscreen'}
                    >
                        {isFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
                    </button>
                </div>
            </div>

            {/* Job Selector */}
            <div className="mb-3" style={{ position: 'relative', zIndex: 10 }}>
                <select
                    value={quickViewerJobId || ''}
                    onChange={(e) => setSelectedJobId(e.target.value || null)}
                    className={`w-full bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:ring-2 focus:ring-accent focus:border-transparent ${layout.selectorClass}`}
                >
                    <option value="">Select a job...</option>
                    {completedJobs.map((job: Job) => (
                        <option key={job.id} value={job.id}>
                            {job.name} ({job.design_count} designs)
                        </option>
                    ))}
                </select>
            </div>

            {/* Structure Selector - always show when structures available */}
            {structureData && structureData.structures.length > 0 && (
                <div className="mb-3" style={{ position: 'relative', zIndex: 10 }}>
                    <select
                        value={selectedStructure?.path || ''}
                        onChange={(e) => {
                            const struct = structureData.structures.find(s => s.path === e.target.value);
                            setSelectedStructure(struct || null);
                        }}
                        className={`w-full bg-slate-900/50 border border-slate-600 rounded-lg text-white focus:ring-2 focus:ring-accent focus:border-transparent ${layout.structureSelectorClass}`}
                    >
                        {structureData.structures.map((struct) => (
                            <option key={struct.path} value={struct.path}>
                                {struct.name} ({(struct.size_bytes / 1024).toFixed(0)} KB)
                            </option>
                        ))}
                    </select>
                </div>
            )}

            {/* Current job indicator if externally set */}
            {showJobIndicator && currentJob && (
                <div className="mb-2 text-xs text-slate-400">
                    Viewing: <span className="text-accent">{currentJob.name}</span>
                </div>
            )}

            {/* Viewer */}
            <div
                className={`${BMS_VIEWER_WELL} overflow-hidden transition-all duration-300 ${isFullscreen ? 'min-h-0 flex-1 rounded-xl' : ''}`}
                style={{ position: 'relative', zIndex: 0 }}
            >
                {structureUrl ? (
                    <StructureWorkbench
                        mode="compact"
                        structureUrl={structureUrl}
                        format={selectedStructure?.type || 'pdb'}
                        alphafoldView={true}
                        hideControls={hideViewerControls}
                        jobId={quickViewerJobId ?? undefined}
                        height={viewerHeight}
                        backgroundColor="#0f172a"
                    />
                ) : (
                    <div
                        className="flex items-center justify-center text-slate-500 text-sm transition-all duration-300"
                        style={{ height: viewerHeight }}
                    >
                        {quickViewerJobId ? 'No structures found' : 'Select a job to preview'}
                    </div>
                )}
            </div>
            </div>
        </div>
    );
}

export default QuickViewer;
