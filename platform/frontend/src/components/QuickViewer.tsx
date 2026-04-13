/**
 * QuickViewer - Compact structure preview widget for dashboard
 * Shows a dropdown to select a recent completed job and displays the structure
 * 
 * Can be controlled externally via selectedJobId prop for integration with other components
 */

import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import MolstarViewer from './MolstarViewer';
import { fetchJobs } from '../lib/api';
import type { Job } from '../lib/api';

const QUICK_VIEWER_COMPACT_KEY = 'bms_dashboard_quick_viewer_compact_v1';
type QuickViewerSize = 'micro' | 'compact' | 'standard' | 'large' | 'xlarge';
const QUICK_VIEWER_SIZE_OPTIONS: ReadonlyArray<{ value: QuickViewerSize; label: string; title: string }> = [
    { value: 'micro', label: 'XS', title: 'Very compact viewer' },
    { value: 'compact', label: 'S', title: 'Compact viewer' },
    { value: 'standard', label: 'M', title: 'Standard viewer' },
    { value: 'large', label: 'L', title: 'Large viewer' },
    { value: 'xlarge', label: 'XL', title: 'Maximum viewer size' },
];

const QUICK_VIEWER_HEIGHTS: Record<QuickViewerSize, number> = {
    micro: 96,
    compact: 168,
    standard: 280,
    large: 360,
    xlarge: 480,
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

export function QuickViewer({ selectedJobId: externalJobId, onJobChange }: QuickViewerProps) {
    // Use internal state if not externally controlled
    const [internalJobId, setInternalJobId] = useState<string | null>(null);
    const [selectedStructure, setSelectedStructure] = useState<StructureFile | null>(null);
    const [viewerSize, setViewerSize] = useState<QuickViewerSize>('large');

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
        queryKey: ['jobs'],
        queryFn: () => fetchJobs(),
        refetchInterval: 3000,
    });

    // Get completed jobs with structures
    const completedJobs = (jobsData?.data?.jobs || []).filter(
        (job: Job) => job.status === 'completed'
    );

    // Fetch structure files for selected job
    const { data: structureData } = useQuery<{ structures: StructureFile[]; count: number }>({
        queryKey: ['structure-files', selectedJobId],
        queryFn: async () => {
            const res = await fetch(`/api/jobs/${selectedJobId}/structure-files`);
            if (!res.ok) throw new Error('Failed to fetch structure files');
            return res.json();
        },
        enabled: !!selectedJobId,
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

    const setViewerSizePreference = (nextSize: QuickViewerSize) => {
        setViewerSize(nextSize);
        try {
            localStorage.setItem(QUICK_VIEWER_COMPACT_KEY, nextSize);
        } catch {
            // Ignore localStorage write failures and keep in-memory state.
        }
    };

    const structureUrl = selectedStructure
        ? `/api/files/pdb/${selectedStructure.path}`
        : null;

    const viewerHeight = QUICK_VIEWER_HEIGHTS[viewerSize];
    const hideViewerControls = viewerSize === 'micro' || viewerSize === 'compact';

    // Find current job name for display
    const currentJob = completedJobs.find((j: Job) => j.id === selectedJobId);

    return (
        <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700 rounded-xl p-4" style={{ position: 'relative' }}>
            {/* Header */}
            <div className="flex items-center justify-between mb-3" style={{ position: 'relative', zIndex: 10 }}>
                <h3 className="text-sm font-semibold text-slate-200">🔬 Quick Viewer</h3>
                <div className="flex items-center gap-2">
                    {selectedStructure && (
                        <span className={`px-2 py-0.5 rounded text-xs font-medium uppercase ${selectedStructure.type === 'pdb'
                            ? 'bg-green-500/20 text-green-400'
                            : 'bg-blue-500/20 text-blue-400'
                            }`}>
                            {selectedStructure.type}
                        </span>
                    )}
                    <div className="inline-flex flex-wrap items-center gap-1 rounded-lg border border-slate-600/70 bg-slate-900/60 p-1">
                        {QUICK_VIEWER_SIZE_OPTIONS.map((option) => {
                            const active = viewerSize === option.value;
                            return (
                                <button
                                    key={option.value}
                                    type="button"
                                    onClick={() => setViewerSizePreference(option.value)}
                                    className={`min-w-9 rounded border px-2 py-1 text-[11px] font-semibold transition-colors ${
                                        active
                                            ? 'border-accent bg-accent/20 text-white'
                                            : 'border-slate-600 bg-slate-800/80 text-slate-300 hover:bg-slate-700'
                                    }`}
                                    title={option.title}
                                >
                                    {option.label}
                                </button>
                            );
                        })}
                    </div>
                </div>
            </div>

            {/* Job Selector */}
            <div className="mb-3" style={{ position: 'relative', zIndex: 10 }}>
                <select
                    value={selectedJobId || ''}
                    onChange={(e) => setSelectedJobId(e.target.value || null)}
                    className="w-full bg-slate-900/50 border border-slate-600 rounded-lg px-3 py-1.5 text-white text-sm focus:ring-2 focus:ring-accent focus:border-transparent"
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
                        className="w-full bg-slate-900/50 border border-slate-600 rounded-lg px-3 py-1.5 text-white text-xs focus:ring-2 focus:ring-accent focus:border-transparent"
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
            {currentJob && (
                <div className="mb-2 text-xs text-slate-400">
                    Viewing: <span className="text-accent">{currentJob.name}</span>
                </div>
            )}

            {/* Viewer */}
            <div
                className="bg-slate-900/50 rounded-lg overflow-hidden transition-all duration-300"
                style={{ position: 'relative', zIndex: 0 }}
            >
                {structureUrl ? (
                    <MolstarViewer
                        structureUrl={structureUrl}
                        format={selectedStructure?.type || 'pdb'}
                        alphafoldView={true}
                        hideControls={hideViewerControls}
                        height={viewerHeight}
                        backgroundColor="#0f172a"
                    />
                ) : (
                    <div
                        className="flex items-center justify-center text-slate-500 text-sm transition-all duration-300"
                        style={{ height: viewerHeight }}
                    >
                        {selectedJobId ? 'No structures found' : 'Select a job to preview'}
                    </div>
                )}
            </div>
        </div>
    );
}

export default QuickViewer;
