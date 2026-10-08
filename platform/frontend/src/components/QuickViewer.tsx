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
    const [expanded, setExpanded] = useState(false);

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
        refetchInterval: 10000,
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

    const structureUrl = selectedStructure
        ? `/api/files/pdb/${selectedStructure.path}`
        : null;

    // Dynamic sizing based on expanded state
    const viewerHeight = expanded ? 400 : 180;

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
                    <button
                        onClick={() => setExpanded(!expanded)}
                        className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${expanded
                            ? 'bg-purple-500/30 text-purple-300'
                            : 'bg-slate-600/50 text-slate-400 hover:bg-slate-600'
                            }`}
                        title={expanded ? 'Collapse viewer' : 'Expand with controls'}
                    >
                        {expanded ? '⇲ Compact' : '⇱ Expand'}
                    </button>
                </div>
            </div>

            {/* Job Selector */}
            <div className="mb-3" style={{ position: 'relative', zIndex: 10 }}>
                <select
                    value={selectedJobId || ''}
                    onChange={(e) => setSelectedJobId(e.target.value || null)}
                    className="w-full bg-slate-900/50 border border-slate-600 rounded-lg px-3 py-1.5 text-white text-sm focus:ring-2 focus:ring-purple-500 focus:border-transparent"
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
                        className="w-full bg-slate-900/50 border border-slate-600 rounded-lg px-3 py-1.5 text-white text-xs focus:ring-2 focus:ring-purple-500 focus:border-transparent"
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
                    Viewing: <span className="text-purple-400">{currentJob.name}</span>
                </div>
            )}

            {/* Viewer - height and controls based on expanded state */}
            <div
                className="bg-slate-900/50 rounded-lg overflow-hidden transition-all duration-300"
                style={{ position: 'relative', zIndex: 0 }}
            >
                {structureUrl ? (
                    <MolstarViewer
                        structureUrl={structureUrl}
                        format={selectedStructure?.type || 'pdb'}
                        alphafoldView={true}
                        hideControls={!expanded}
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
