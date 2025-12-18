/**
 * JobDetailPage - Standalone page for viewing job details
 * 
 * Fetches job by ID from URL params and displays results with MolstarViewer.
 */

import { useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import MolstarViewer from './MolstarViewer';
import type { Job } from '../lib/api';

interface DockingResult {
    name: string;
    path: string;
    absolute_path: string;
    confidence: number | null;
    rank: number | null;
    complex_name: string;
}

interface StructureFile {
    name: string;
    filename: string;
    path: string;
    type: 'pdb' | 'cif';
    size_bytes: number;
}

export function JobDetailPage() {
    const { jobId } = useParams<{ jobId: string }>();
    const [selectedPose, setSelectedPose] = useState<number>(0);

    // Fetch job details
    const { data: job, isLoading: jobLoading, error: jobError } = useQuery<Job>({
        queryKey: ['job', jobId],
        queryFn: async () => {
            const res = await fetch(`/api/jobs/${jobId}`);
            if (!res.ok) throw new Error('Failed to fetch job');
            return res.json();
        },
        enabled: !!jobId,
        refetchInterval: (query) => {
            const job = query.state.data;
            // Keep polling if job is running
            return job?.status === 'running' || job?.status === 'queued' ? 3000 : false;
        },
    });

    const isDockingJob = job?.model_id === 'diffdock' || job?.mode?.includes('dock');

    // Fetch docking results
    const { data: dockingData, isLoading: dockingLoading } = useQuery({
        queryKey: ['docking-results', jobId],
        queryFn: async () => {
            const res = await fetch(`/api/jobs/${jobId}/docking-results`);
            if (!res.ok) throw new Error('Failed to fetch docking results');
            return res.json();
        },
        enabled: isDockingJob && job?.status === 'completed',
    });

    // Fetch structure files for structure prediction jobs
    const { data: structureData, isLoading: structureLoading } = useQuery<{ structures: StructureFile[], count: number }>({
        queryKey: ['structure-files', jobId],
        queryFn: async () => {
            const res = await fetch(`/api/jobs/${jobId}/structure-files`);
            if (!res.ok) throw new Error('Failed to fetch structure files');
            return res.json();
        },
        enabled: job?.status === 'completed' && !isDockingJob,
    });

    const poses = dockingData?.sdfs || [];
    const currentSdf = poses[selectedPose];

    if (jobLoading) {
        return (
            <div className="flex items-center justify-center min-h-[60vh]">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-purple-500" />
            </div>
        );
    }

    if (jobError || !job) {
        return (
            <div className="max-w-4xl mx-auto px-4 py-12">
                <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-6 text-center">
                    <h2 className="text-xl font-semibold text-red-400 mb-2">Job Not Found</h2>
                    <p className="text-slate-400 mb-4">The job with ID "{jobId}" could not be found.</p>
                    <Link to="/" className="text-purple-400 hover:text-purple-300">
                        ← Back to Dashboard
                    </Link>
                </div>
            </div>
        );
    }

    return (
        <div className="max-w-6xl mx-auto px-4 py-8">
            {/* Back Link */}
            <Link
                to="/"
                className="inline-flex items-center gap-2 text-slate-400 hover:text-white mb-6 transition-colors"
            >
                ← Back to Dashboard
            </Link>

            {/* Job Header */}
            <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700/50 rounded-2xl p-6 mb-6">
                <div className="flex items-center justify-between mb-4">
                    <h1 className="text-2xl font-bold text-white">{job.name}</h1>
                    <div className="flex items-center gap-3">
                        <StatusBadge status={job.status} />
                        {(job.status === 'running' || job.status === 'queued') && (
                            <button
                                onClick={() => {
                                    if (confirm('Are you sure you want to cancel this job?')) {
                                        fetch(`/api/jobs/${job.id}`, { method: 'DELETE' })
                                            .then(res => {
                                                if (!res.ok) throw new Error('Failed to cancel');
                                                window.location.reload();
                                            })
                                            .catch(err => alert('Failed to cancel job: ' + err.message));
                                    }
                                }}
                                className="px-3 py-1 bg-red-500/10 text-red-400 border border-red-500/30 rounded-lg text-sm font-medium hover:bg-red-500/20 transition-colors"
                            >
                                Cancel
                            </button>
                        )}
                    </div>
                </div>

                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                    <div>
                        <span className="text-slate-400">Model:</span>
                        <span className="text-white ml-2">{job.model_id}</span>
                    </div>
                    <div>
                        <span className="text-slate-400">Mode:</span>
                        <span className="text-white ml-2">{job.mode}</span>
                    </div>
                    <div>
                        <span className="text-slate-400">Created:</span>
                        <span className="text-white ml-2">
                            {new Date(job.created_at).toLocaleString()}
                        </span>
                    </div>
                    <div>
                        <span className="text-slate-400">Output:</span>
                        <span className="text-slate-300 ml-2 text-xs">{job.output_dir}</span>
                    </div>
                </div>
            </div>

            {/* Results Section */}
            <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700/50 rounded-2xl p-6">
                <h2 className="text-lg font-semibold text-white mb-4">Results</h2>

                {job.status === 'running' && (
                    <div className="flex items-center gap-3 text-slate-400">
                        <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-purple-500" />
                        Job is running...
                    </div>
                )}

                {job.status === 'queued' && (
                    <div className="text-slate-400">Job is queued, waiting to start...</div>
                )}

                {job.status === 'failed' && (
                    <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4">
                        <span className="text-red-400 font-medium">Error: </span>
                        <span className="text-slate-300">{job.error_message || 'Unknown error'}</span>
                    </div>
                )}

                {job.status === 'completed' && isDockingJob && (
                    <div>
                        {dockingLoading ? (
                            <div className="flex items-center justify-center py-8">
                                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-500" />
                            </div>
                        ) : poses.length > 0 ? (
                            <div>
                                {/* Pose Selector */}
                                <div className="flex items-center gap-4 mb-4">
                                    <label className="text-slate-400 text-sm">Docking Pose:</label>
                                    <select
                                        value={selectedPose}
                                        onChange={(e) => setSelectedPose(Number(e.target.value))}
                                        className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-purple-500"
                                    >
                                        {poses.map((pose: DockingResult, idx: number) => (
                                            <option key={idx} value={idx}>
                                                {pose.name} {pose.confidence !== null ? `(conf: ${pose.confidence.toFixed(2)})` : ''}
                                            </option>
                                        ))}
                                    </select>
                                    <span className="text-slate-500 text-sm">
                                        {poses.length} poses ranked
                                    </span>
                                </div>

                                {/* 3D Viewer - Using MolstarViewer with URL */}
                                <div className="bg-slate-900/50 rounded-xl overflow-hidden">
                                    {currentSdf ? (
                                        <MolstarViewer
                                            structureUrl={`/api/jobs/${jobId}/docking-results/${currentSdf.name}`}
                                            format="pdb"
                                            height={500}
                                            backgroundColor="#0f172a"
                                            alphafoldView={false}
                                        />
                                    ) : (
                                        <div className="flex items-center justify-center h-[500px] text-slate-500">
                                            Loading 3D structure...
                                        </div>
                                    )}
                                </div>

                                {/* Confidence Score */}
                                {currentSdf?.confidence !== null && (
                                    <div className="mt-4 text-center">
                                        <span className="text-slate-400">Confidence Score:</span>
                                        <span className={`ml-2 font-semibold ${currentSdf.confidence < -2 ? 'text-green-400' :
                                            currentSdf.confidence < 0 ? 'text-yellow-400' : 'text-red-400'
                                            }`}>
                                            {currentSdf.confidence.toFixed(2)}
                                        </span>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div className="text-center py-8 text-slate-500">
                                No docking results found
                            </div>
                        )}
                    </div>
                )}

                {job.status === 'completed' && !isDockingJob && (
                    <div>
                        <h3 className="text-md font-medium text-white mb-4">
                            Structure Files ({structureData?.count || 0})
                        </h3>

                        {structureLoading ? (
                            <div className="flex items-center justify-center py-4">
                                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-purple-500" />
                            </div>
                        ) : structureData?.structures && structureData.structures.length > 0 ? (
                            <div className="grid gap-3">
                                {structureData.structures.map((struct) => (
                                    <div
                                        key={struct.path}
                                        className="flex items-center justify-between bg-slate-900/50 rounded-lg px-4 py-3 border border-slate-700/50 hover:border-purple-500/30 transition-colors"
                                    >
                                        <div className="flex items-center gap-3">
                                            <span className={`px-2 py-0.5 rounded text-xs font-medium uppercase ${struct.type === 'pdb'
                                                ? 'bg-green-500/20 text-green-400'
                                                : 'bg-blue-500/20 text-blue-400'
                                                }`}>
                                                {struct.type}
                                            </span>
                                            <div>
                                                <span className="text-white font-medium">{struct.name}</span>
                                                <span className="text-slate-500 text-sm ml-2">
                                                    ({(struct.size_bytes / 1024).toFixed(1)} KB)
                                                </span>
                                            </div>
                                        </div>
                                        <div className="flex gap-2">
                                            <a
                                                href={`/api/files/pdb/${struct.path}`}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="px-3 py-1.5 bg-purple-600/20 text-purple-400 rounded-lg text-sm hover:bg-purple-600/30 transition-colors"
                                            >
                                                View
                                            </a>
                                            <a
                                                href={`/api/files/download/${struct.path}`}
                                                download={struct.filename}
                                                className="px-3 py-1.5 bg-slate-700/50 text-slate-300 rounded-lg text-sm hover:bg-slate-700 transition-colors"
                                            >
                                                Download
                                            </a>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <div className="text-center py-8 text-slate-500">
                                No structure files found
                            </div>
                        )}

                        <div className="mt-4 text-sm text-slate-500">
                            Results directory: <code className="text-purple-400">{job.output_dir}</code>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

function StatusBadge({ status }: { status: string }) {
    const styles: Record<string, string> = {
        completed: 'bg-green-500/20 text-green-400 border-green-500/30',
        running: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
        queued: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
        failed: 'bg-red-500/20 text-red-400 border-red-500/30',
        cancelled: 'bg-slate-500/20 text-slate-400 border-slate-500/30',
    };

    return (
        <span className={`px-3 py-1 rounded-full text-sm font-medium border ${styles[status] || styles.queued}`}>
            {status}
        </span>
    );
}

export default JobDetailPage;
