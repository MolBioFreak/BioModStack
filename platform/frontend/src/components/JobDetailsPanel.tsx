/**
 * JobDetailsPanel - Expandable panel showing job results summary
 * 
 * No longer includes 3D viewer - use QuickViewer component for that.
 * Shows job metadata, structure file list, and download links.
 */

import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import type { Job } from '../lib/api';
import { CandidateAccountingStatus } from './CandidateAccountingStatus';
import { ExecutionSettingsPanel } from './ExecutionSettingsPanel';

interface DockingResult {
    name: string;
    path: string;
    confidence: number | null;
    rank: number | null;
}

interface StructureFile {
    name: string;
    filename: string;
    path: string;
    type: 'pdb' | 'cif';
    size_bytes: number;
}

interface JobDetailsPanelProps {
    job: Job;
    onClose: () => void;
}

export function JobDetailsPanel({ job, onClose }: JobDetailsPanelProps) {
    // Check if this is a docking job
    const isDockingJob = job.model_id === 'diffdock' || job.mode?.includes('dock');
    const isMolecularDynamicsJob = job.model_id === 'molecular_dynamics' ||
        job.mode === 'molecular_dynamics' || job.mode === 'md';

    // Fetch docking results
    const { data: dockingData, isLoading: dockingLoading } = useQuery({
        queryKey: ['docking-results', job.id],
        queryFn: async () => {
            const res = await fetch(`/api/jobs/${job.id}/docking-results`);
            if (!res.ok) throw new Error('Failed to fetch docking results');
            return res.json();
        },
        enabled: isDockingJob && job.status === 'completed',
    });

    // Fetch structure files for non-docking jobs
    const { data: structureData, isLoading: structureLoading } = useQuery<{ structures: StructureFile[], count: number }>({
        queryKey: ['structure-files', job.id],
        queryFn: async () => {
            const res = await fetch(`/api/jobs/${job.id}/structure-files`);
            if (!res.ok) throw new Error('Failed to fetch structure files');
            return res.json();
        },
        enabled: !isDockingJob && !isMolecularDynamicsJob && job.status === 'completed',
    });

    const poses = dockingData?.sdfs || [];

    return (
        <tr>
            <td colSpan={6} className="bg-slate-900/50 border-b border-slate-700">
                <div className="p-4">
                    {/* Header */}
                    <div className="flex justify-between items-center mb-3">
                        <div className="flex items-center gap-3">
                            <h3 className="text-sm font-semibold text-white">{job.name}</h3>
                            <span className="px-2 py-0.5 bg-accent/20 text-accent rounded text-xs">
                                {job.model_id}
                            </span>
                        </div>
                        <div className="flex items-center gap-2">
                            <Link
                                to={`/designs/${job.id}`}
                                className="px-3 py-1 bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 rounded text-xs font-medium transition-colors"
                            >
                                {isMolecularDynamicsJob ? 'MD Operations →' : 'Open in Results Viewer →'}
                            </Link>
                            <button
                                onClick={onClose}
                                className="text-slate-400 hover:text-white transition-colors text-sm"
                            >
                                ✕
                            </button>
                        </div>
                    </div>

                    {/* Job Info Row */}
                    <div className="flex items-center gap-6 text-xs text-slate-400 mb-3">
                        <span>Mode: <span className="text-slate-300">{job.mode}</span></span>
                        {typeof job.requested_design_count === 'number' && job.requested_design_count !== job.design_count ? (
                            <>
                                <span>Requested: <span className="text-slate-300">{job.requested_design_count}</span></span>
                                <span>Stored Rows: <span className="text-slate-300">{job.design_count}</span></span>
                            </>
                        ) : (
                            <span>Designs: <span className="text-slate-300">{job.design_count}</span></span>
                        )}
                        <span>Output: <code className="text-accent/80">{job.output_dir}</code></span>
                    </div>

                    <CandidateAccountingStatus job={job} />
                    {['esmfold2', 'esmfold2_experimental', 'antibody_denovo', 'antibody_child'].includes(job.model_id) && <ExecutionSettingsPanel jobId={job.id} />}
                    {/* Results Summary */}
                    {job.status === 'completed' && (
                        <div className="flex flex-wrap gap-2">
                            {/* Docking results */}
                            {isDockingJob && (
                                dockingLoading ? (
                                    <span className="text-xs text-slate-500">Loading docking results...</span>
                                ) : poses.length > 0 ? (
                                    poses.slice(0, 5).map((pose: DockingResult, idx: number) => (
                                        <a
                                            key={idx}
                                            href={`/api/jobs/${job.id}/docking-results/${pose.name}`}
                                            download={pose.name}
                                            className="px-2 py-1 bg-slate-700/50 hover:bg-slate-600/50 rounded text-xs text-slate-300 transition-colors"
                                        >
                                            {pose.name} {pose.confidence !== null && (
                                                <span className={`ml-1 ${pose.confidence < -2 ? 'text-green-400' : pose.confidence < 0 ? 'text-yellow-400' : 'text-red-400'}`}>
                                                    ({pose.confidence.toFixed(2)})
                                                </span>
                                            )}
                                        </a>
                                    ))
                                ) : (
                                    <span className="text-xs text-slate-500">No docking results</span>
                                )
                            )}

                            {/* Structure files */}
                            {!isDockingJob && !isMolecularDynamicsJob && (
                                structureLoading ? (
                                    <span className="text-xs text-slate-500">Loading structures...</span>
                                ) : structureData?.structures && structureData.structures.length > 0 ? (
                                    structureData.structures.slice(0, 6).map((struct) => (
                                        <a
                                            key={struct.path}
                                            href={`/api/files/download/${struct.path}`}
                                            download={struct.filename}
                                            className="px-2 py-1 bg-slate-700/50 hover:bg-slate-600/50 rounded text-xs text-slate-300 transition-colors flex items-center gap-1"
                                        >
                                            <span className={`text-xs ${struct.type === 'pdb' ? 'text-green-400' : 'text-blue-400'}`}>
                                                [{struct.type.toUpperCase()}]
                                            </span>
                                            {struct.name}
                                        </a>
                                    ))
                                ) : (
                                    <span className="text-xs text-slate-500">No structure files</span>
                                )
                            )}

                            {isMolecularDynamicsJob && (
                                <span className="text-xs text-cyan-300">
                                    Trajectories, checkpoints, analysis, and lifecycle controls are available only in MD Operations.
                                </span>
                            )}

                            {/* Show more indicator */}
                            {((isDockingJob && poses.length > 5) ||
                                (!isDockingJob && !isMolecularDynamicsJob && structureData && structureData.count > 6)) && (
                                    <Link
                                        to={`/designs/${job.id}`}
                                        className="px-2 py-1 bg-accent/20 text-accent hover:bg-accent/30 rounded text-xs transition-colors"
                                    >
                                        +{isDockingJob ? poses.length - 5 : structureData!.count - 6} more →
                                    </Link>
                                )}
                        </div>
                    )}

                    {/* Job not completed */}
                    {job.status !== 'completed' && (
                        <div className="text-xs text-slate-500">
                            Job is {job.status}. Results will be available when completed.
                        </div>
                    )}
                </div>
            </td>
        </tr>
    );
}

export default JobDetailsPanel;
