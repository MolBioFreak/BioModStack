import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    fetchQueue,
    fetchQueueStats,
    pauseQueueJob,
    resumeQueueJob,
    cancelQueueJob,
    pinQueueJob,
    setQueueJobPriority,
    retryQueueJob,
    cancelAllQueuedJobs,
    fetchCancelledJobs,
    killActiveNextflowJobs,
    type QueuedJob,
} from '../lib/api';

// Model display names and icons
// Model display names removed - using text badges instead

const GPU_NAMES: Record<number, string> = {
    0: 'RTX 5090',
    1: 'RTX 5060 Ti',
    2: 'RTX 3090 #1',
    3: 'RTX 3090 #2',
};

function getModelBadge(modelId: string): string {
    const key = modelId.toLowerCase();
    if (key === 'msa_batch') return 'MSA';
    if (key.includes('boltzgen')) return 'BG';
    if (key.includes('boltz')) return 'B2';
    if (key.includes('rf3')) return 'RF';
    if (key.includes('fampnn') || key.includes('mpnn')) return 'PN';
    if (key.includes('diff')) return 'RD';
    if (key.includes('dock')) return 'DD';
    return 'JB';
}

function isMsaJob(modelId: string): boolean {
    return modelId.toLowerCase() === 'msa_batch';
}

function StatusBadge({ status, paused }: { status: string; paused: boolean }) {
    if (paused) {
        return (
            <span className="px-2 py-0.5 rounded text-xs font-medium bg-yellow-500/20 text-yellow-400">
                ⏸ Paused
            </span>
        );
    }

    switch (status) {
        case 'running':
            return (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-green-500/20 text-green-400 animate-pulse">
                    ▶ Running
                </span>
            );
        case 'queued':
            return (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-500/20 text-blue-400">
                    ⏳ Queued
                </span>
            );
        case 'pending_msa':
            return (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-violet-500/20 text-violet-400">
                    🧬 Waiting MSA
                </span>
            );
        default:
            return (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-slate-500/20 text-slate-400">
                    {status}
                </span>
            );
    }
}

function VramBadge({ vramMb }: { vramMb: number | null }) {
    if (!vramMb) return null;
    const vramGb = (vramMb / 1024).toFixed(1);
    return (
        <span className="px-2 py-0.5 rounded text-xs font-medium bg-purple-500/20 text-purple-400">
            {vramGb} GB
        </span>
    );
}

function GPUBadge({ gpu, pinned }: { gpu: number | null; pinned: boolean }) {
    if (gpu === null) {
        return (
            <span className="px-2 py-0.5 rounded text-xs font-medium bg-slate-500/20 text-slate-400">
                Auto
            </span>
        );
    }
    const name = GPU_NAMES[gpu] || `GPU ${gpu}`;
    return (
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${pinned ? 'bg-orange-500/20 text-orange-400' : 'bg-cyan-500/20 text-cyan-400'}`}>
            {pinned ? '📌 ' : ''}{name}
        </span>
    );
}

// Elapsed time badge for running jobs
function ElapsedTimeBadge({ startedAt }: { startedAt: string | null }) {
    const [, forceUpdate] = useState(0);

    // Update every second for running jobs
    useEffect(() => {
        if (!startedAt) return;
        const timer = setInterval(() => forceUpdate(n => n + 1), 1000);
        return () => clearInterval(timer);
    }, [startedAt]);

    if (!startedAt) return null;

    const start = new Date(startedAt).getTime();
    const now = Date.now();
    const elapsedMs = now - start;

    const seconds = Math.floor(elapsedMs / 1000) % 60;
    const minutes = Math.floor(elapsedMs / 60000) % 60;
    const hours = Math.floor(elapsedMs / 3600000);

    let display = '';
    if (hours > 0) {
        display = `${hours}h ${minutes}m`;
    } else if (minutes > 0) {
        display = `${minutes}m ${seconds}s`;
    } else {
        display = `${seconds}s`;
    }

    return (
        <span className="px-2 py-0.5 rounded text-xs font-medium bg-emerald-500/20 text-emerald-400">
            ⏱ {display}
        </span>
    );
}

// Stage badge - shows current workflow step and progress for running jobs
function StageBadge({ stage, progress }: { stage: string | null; progress?: string | null }) {
    if (!stage) return null;

    // Map stage names to display-friendly versions
    const stageDisplayNames: Record<string, string> = {
        'rfantibody': 'RFAntibody',
        'rfdiffusion': 'RFdiffusion',
        'fampnn': 'FA-MPNN',
        'proteinmpnn': 'ProteinMPNN',
        'thermompnn': 'ThermoMPNN',
        'boltz2': 'Boltz-2',
        'af2': 'AlphaFold2',
        'unidock': 'UniDock',
        'msa': 'MSA Gen',
        'prepfampnn': 'Prep FAMPNN',
        'complete': 'Complete',
    };

    const displayName = stageDisplayNames[stage.toLowerCase()] || stage;
    const displayProgress = progress ? ` (${progress})` : '';

    return (
        <span className="px-2 py-0.5 rounded text-xs font-medium bg-cyan-500/20 text-cyan-400 animate-pulse">
            🔄 {displayName}{displayProgress}
        </span>
    );
}

export function JobQueuePanel() {
    const queryClient = useQueryClient();
    const [expanded, setExpanded] = useState(true);
    const [showCancelled, setShowCancelled] = useState(false);

    // Fetch queue data
    const { data: queueData, isLoading } = useQuery({
        queryKey: ['queue'],
        queryFn: () => fetchQueue(),
        refetchInterval: 2000,
    });

    const { data: statsData } = useQuery({
        queryKey: ['queueStats'],
        queryFn: () => fetchQueueStats(),
        refetchInterval: 2000,
    });

    const { data: cancelledData } = useQuery({
        queryKey: ['cancelledJobs'],
        queryFn: () => fetchCancelledJobs(20),
        refetchInterval: 5000,
        enabled: showCancelled,
    });

    // Mutations
    const pauseMutation = useMutation({
        mutationFn: pauseQueueJob,
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['queue'] }),
    });

    const resumeMutation = useMutation({
        mutationFn: resumeQueueJob,
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['queue'] }),
    });

    const cancelMutation = useMutation({
        mutationFn: cancelQueueJob,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['queue'] });
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            queryClient.invalidateQueries({ queryKey: ['cancelledJobs'] });
        },
    });

    const pinMutation = useMutation({
        mutationFn: ({ jobId, gpuId }: { jobId: string; gpuId: number | null }) =>
            pinQueueJob(jobId, gpuId),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['queue'] }),
    });

    useMutation({
        mutationFn: ({ jobId, priority }: { jobId: string; priority: number }) =>
            setQueueJobPriority(jobId, priority),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['queue'] }),
    });

    const cancelAllMutation = useMutation({
        mutationFn: cancelAllQueuedJobs,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['queue'] });
            queryClient.invalidateQueries({ queryKey: ['queueStats'] });
            queryClient.invalidateQueries({ queryKey: ['cancelledJobs'] });
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
        },
    });

    const retryMutation = useMutation({
        mutationFn: retryQueueJob,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['queue'] });
            queryClient.invalidateQueries({ queryKey: ['cancelledJobs'] });
        },
    });

    const killActiveMutation = useMutation({
        mutationFn: killActiveNextflowJobs,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['queue'] });
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            alert('Kill signal sent to all Nextflow processes');
        },
    });

    const queue = queueData?.data || [];
    const stats = statsData?.data;
    const cancelledJobs = cancelledData?.data || [];
    const isPending = pauseMutation.isPending || resumeMutation.isPending ||
        cancelMutation.isPending || pinMutation.isPending || cancelAllMutation.isPending || killActiveMutation.isPending;

    // Separate running, queued, and pending_msa jobs
    const runningJobs = queue.filter(j => j.queue_status === 'running');
    const queuedJobs = queue.filter(j => j.queue_status === 'queued' || j.queue_status === 'paused');
    const pendingMsaJobs = queue.filter(j => j.queue_status === 'pending_msa');

    const handleCancelAll = () => {
        if (queuedJobs.length === 0) return;
        if (confirm(`Clear ${queuedJobs.length} queued jobs from database?`)) {
            cancelAllMutation.mutate();
        }
    };

    const handleKillActive = () => {
        if (confirm('Kill ALL running Nextflow processes? This will terminate all active jobs.')) {
            killActiveMutation.mutate();
        }
    };

    return (
        <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700 rounded-xl overflow-hidden mb-6">
            {/* Header */}
            <div
                className="flex items-center justify-between px-4 py-2 border-b border-slate-700/50 cursor-pointer hover:bg-slate-700/20"
                onClick={() => setExpanded(!expanded)}
            >
                <div className="flex items-center gap-3">
                    <span className="text-sm font-semibold text-slate-200">GPU Queue</span>
                    {stats && (
                        <div className="flex gap-2">
                            <span className="px-2 py-0.5 rounded text-xs font-medium bg-green-500/20 text-green-400">
                                {stats.running} run
                            </span>
                            <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-500/20 text-blue-400">
                                {stats.queued} queue
                            </span>
                            {stats.paused > 0 && (
                                <span className="px-2 py-0.5 rounded text-xs font-medium bg-yellow-500/20 text-yellow-400">
                                    {stats.paused} paused
                                </span>
                            )}
                        </div>
                    )}
                </div>
                <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
                    {/* Kill Active - kills actual Nextflow processes */}
                    <button
                        onClick={handleKillActive}
                        disabled={isPending}
                        className="px-2 py-1 rounded bg-orange-600/30 hover:bg-orange-600/50 text-orange-400 text-xs disabled:opacity-50"
                        title="Kill all running Nextflow processes"
                    >
                        Kill Active
                    </button>
                    {/* Clear Queue - removes from database */}
                    {queuedJobs.length > 0 && (
                        <button
                            onClick={handleCancelAll}
                            disabled={isPending}
                            className="px-2 py-1 rounded bg-red-600/30 hover:bg-red-600/50 text-red-400 text-xs disabled:opacity-50"
                            title="Clear all queued jobs from database"
                        >
                            Clear Queue
                        </button>
                    )}
                    {/* Tab toggle */}
                    <button
                        onClick={() => setShowCancelled(!showCancelled)}
                        className={`px-2 py-1 rounded text-xs ${showCancelled ? 'bg-slate-600 text-white' : 'bg-slate-700/50 text-slate-400 hover:text-white'}`}
                    >
                        {showCancelled ? '← Back' : 'Cancelled'}
                    </button>
                    <span className="text-slate-500">{expanded ? '▲' : '▼'}</span>
                </div>
            </div>

            {expanded && (
                <div className="p-3">
                    {showCancelled ? (
                        /* Cancelled Jobs Tab */
                        <div>
                            <h4 className="text-xs font-semibold text-slate-400 mb-2 uppercase tracking-wide">
                                Cancelled Jobs (click to requeue)
                            </h4>
                            {cancelledJobs.length === 0 ? (
                                <div className="text-center py-4 text-slate-500 text-sm">
                                    No cancelled jobs
                                </div>
                            ) : (
                                <div className="space-y-1 max-h-64 overflow-y-auto">
                                    {cancelledJobs.map((job) => (
                                        <div
                                            key={job.id}
                                            className="bg-slate-700/30 rounded p-2 flex items-center justify-between hover:bg-slate-700/50"
                                        >
                                            <div className="flex items-center gap-2 min-w-0">
                                                <span className="px-1.5 py-0.5 rounded text-xs font-mono bg-slate-600 text-slate-300">{getModelBadge(job.model_id)}</span>
                                                <span className="text-sm text-white truncate">{job.name}</span>
                                                <VramBadge vramMb={job.vram_estimate_mb} />
                                            </div>
                                            <button
                                                onClick={() => retryMutation.mutate(job.id)}
                                                disabled={retryMutation.isPending}
                                                className="px-2 py-1 rounded bg-green-600/30 hover:bg-green-600/50 text-green-400 text-xs"
                                            >
                                                ↻ Requeue
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    ) : isLoading ? (
                        <div className="flex justify-center py-4">
                            <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-purple-500" />
                        </div>
                    ) : queue.length === 0 ? (
                        <div className="text-center py-4 text-slate-500 text-sm">
                            No jobs in queue
                        </div>
                    ) : (
                        <div className="space-y-3">
                            {/* Running Jobs */}
                            {runningJobs.length > 0 && (
                                <div>
                                    <h4 className="text-xs font-semibold text-slate-400 mb-1 uppercase tracking-wide">
                                        Running
                                    </h4>
                                    <div className="space-y-1">
                                        {runningJobs.map((job) => (
                                            <JobRow
                                                key={job.id}
                                                job={job}
                                                onPause={() => pauseMutation.mutate(job.id)}
                                                onCancel={() => {
                                                    if (confirm(`Cancel "${job.name}"?`)) {
                                                        cancelMutation.mutate(job.id);
                                                    }
                                                }}
                                                isPending={isPending}
                                            />
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Pending MSA Jobs */}
                            {pendingMsaJobs.length > 0 && (
                                <div>
                                    <h4 className="text-xs font-semibold text-violet-400 mb-1 uppercase tracking-wide">
                                        Generating MSA ({pendingMsaJobs.length})
                                    </h4>
                                    <div className="space-y-1">
                                        {pendingMsaJobs.map((job) => (
                                            <JobRow
                                                key={job.id}
                                                job={job}
                                                onPause={() => pauseMutation.mutate(job.id)}
                                                onCancel={() => {
                                                    if (confirm(`Cancel "${job.name}"?`)) {
                                                        cancelMutation.mutate(job.id);
                                                    }
                                                }}
                                                isPending={isPending}
                                            />
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Queued Jobs */}
                            {queuedJobs.length > 0 && (
                                <div>
                                    <h4 className="text-xs font-semibold text-slate-400 mb-1 uppercase tracking-wide">
                                        Pending ({queuedJobs.length})
                                    </h4>
                                    <div className="space-y-1 max-h-48 overflow-y-auto">
                                        {queuedJobs.map((job) => (
                                            <JobRow
                                                key={job.id}
                                                job={job}
                                                onPause={() => pauseMutation.mutate(job.id)}
                                                onResume={() => resumeMutation.mutate(job.id)}
                                                onCancel={() => {
                                                    if (confirm(`Cancel "${job.name}"?`)) {
                                                        cancelMutation.mutate(job.id);
                                                    }
                                                }}
                                                onPin={(gpuId) => pinMutation.mutate({ jobId: job.id, gpuId })}
                                                isPending={isPending}
                                            />
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

interface JobRowProps {
    job: QueuedJob;
    onPause?: () => void;
    onResume?: () => void;
    onCancel: () => void;
    onPin?: (gpuId: number | null) => void;
    isPending: boolean;
}

function JobRow({
    job,
    onPause,
    onResume,
    onCancel,
    onPin,
    isPending,
}: JobRowProps) {
    const [showPinMenu, setShowPinMenu] = useState(false);

    return (
        <div className="bg-slate-700/30 rounded-lg p-3 hover:bg-slate-700/50 transition-colors">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                    <span className={`px-1.5 py-0.5 rounded text-xs font-mono ${isMsaJob(job.model_id) ? 'bg-violet-600 text-white' : 'bg-slate-600 text-slate-300'}`}>{getModelBadge(job.model_id)}</span>
                    <div className="min-w-0">
                        <div className="flex items-center gap-2">
                            <span className="font-medium text-white truncate">{job.name}</span>
                            <StatusBadge status={job.queue_status} paused={job.paused} />
                        </div>
                        <div className="flex items-center gap-2 mt-1">
                            <span className="text-xs text-slate-500">{job.mode}</span>
                            <VramBadge vramMb={job.vram_estimate_mb} />
                            <GPUBadge gpu={job.assigned_gpu ?? job.pinned_gpu} pinned={job.pinned_gpu !== null} />
                            {job.priority > 0 && (
                                <span className="px-2 py-0.5 rounded text-xs font-medium bg-amber-500/20 text-amber-400">
                                    P{job.priority}
                                </span>
                            )}
                            {job.batch_name && (
                                <span className="px-2 py-0.5 rounded text-xs font-medium bg-purple-500/20 text-purple-400" title={`Batch: ${job.batch_name}`}>
                                    📦 {job.batch_name.length > 15 ? job.batch_name.slice(0, 15) + '...' : job.batch_name}
                                </span>
                            )}
                            {/* Elapsed time for running jobs */}
                            {job.queue_status === 'running' && (
                                <>
                                    <StageBadge stage={job.current_stage} progress={job.stage_progress} />
                                    <ElapsedTimeBadge startedAt={job.started_at} />
                                </>
                            )}
                        </div>
                    </div>
                </div>

                {/* Right: Controls */}
                <div className="flex items-center gap-1">
                    {/* Priority controls removed - was redundant */}

                    {/* Pin to GPU */}
                    {job.queue_status !== 'running' && onPin && (
                        <div className="relative">
                            <button
                                onClick={() => setShowPinMenu(!showPinMenu)}
                                className="px-2 py-1 rounded bg-slate-600/50 hover:bg-slate-600 text-slate-300 text-xs"
                                title="Pin to GPU"
                            >
                                📌
                            </button>
                            {showPinMenu && (
                                <div className="absolute right-0 top-8 z-10 bg-slate-800 border border-slate-600 rounded-lg shadow-xl py-1 min-w-[140px]">
                                    <button
                                        onClick={() => { onPin(null); setShowPinMenu(false); }}
                                        className="w-full text-left px-3 py-1.5 text-sm hover:bg-slate-700 text-slate-300"
                                    >
                                        🔄 Auto-assign
                                    </button>
                                    {[0, 1, 2, 3].map((gpu) => (
                                        <button
                                            key={gpu}
                                            onClick={() => { onPin(gpu); setShowPinMenu(false); }}
                                            className={`w-full text-left px-3 py-1.5 text-sm hover:bg-slate-700 ${job.pinned_gpu === gpu ? 'text-orange-400' : 'text-slate-300'
                                                }`}
                                        >
                                            {job.pinned_gpu === gpu ? '✓ ' : ''}{GPU_NAMES[gpu]}
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Pause/Resume */}
                    {job.paused && onResume ? (
                        <button
                            onClick={onResume}
                            disabled={isPending}
                            className="px-2 py-1 rounded bg-green-600/50 hover:bg-green-600 text-white text-xs disabled:opacity-50"
                            title="Resume"
                        >
                            ▶
                        </button>
                    ) : job.queue_status !== 'running' && onPause ? (
                        <button
                            onClick={onPause}
                            disabled={isPending}
                            className="px-2 py-1 rounded bg-yellow-600/50 hover:bg-yellow-600 text-white text-xs disabled:opacity-50"
                            title="Pause"
                        >
                            ⏸
                        </button>
                    ) : null}

                    {/* Cancel */}
                    <button
                        onClick={onCancel}
                        disabled={isPending}
                        className="px-2 py-1 rounded bg-red-600/50 hover:bg-red-600 text-white text-xs disabled:opacity-50"
                        title="Cancel"
                    >
                        ✕
                    </button>
                </div>
            </div>
        </div>
    );
}
