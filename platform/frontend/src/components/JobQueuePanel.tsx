import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    fetchQueue,
    pauseQueueJob,
    resumeQueueJob,
    cancelQueueJob,
    pinQueueJob,
    setQueueJobPriority,
    retryQueueJob,
    cancelAllQueuedJobs,
    fetchCancelledJobs,
    killActiveNextflowJobs,
    forceLaunchQueueJob,
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

const INTERNAL_BATCH_NAME_RE = /^antibody_batch_[0-9a-f-]{36}$/i;
const INTERNAL_CHILD_NAME_RE = /^antibody_batch_[0-9a-f-]{36}_(.+)$/i;

function parseApiTimestamp(timestamp: string | null): number | null {
    if (!timestamp) return null;
    const trimmed = timestamp.trim();
    if (!trimmed) return null;
    const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(trimmed) ? trimmed : `${trimmed}Z`;
    const parsed = Date.parse(normalized);
    return Number.isNaN(parsed) ? null : parsed;
}

function humanizeChildStage(token: string): string {
    const normalized = token.trim().toLowerCase();
    if (!normalized) return 'Child';
    if (normalized === 'rfa') return 'RFA';
    if (normalized === 'fampnn') return 'FA-MPNN';
    if (normalized === 'maturation' || normalized === 'validated_maturation' || normalized === 'maturation_post_validation') return 'PPIFlow';
    if (normalized === 'protenix' || normalized === 'protenix_batch') return 'Protenix';
    if (normalized === 'boltz2' || normalized === 'boltz2_batch' || normalized === 'boltz_batch' || normalized === 'boltz') return 'Boltz-2';
    return token.replace(/_/g, ' ');
}

function prettifyInternalChildName(name: string, batchName?: string | null): string {
    const rawName = name.trim();
    if (!rawName) return rawName;

    let suffix = '';
    const normalizedBatch = batchName?.trim() || '';
    if (normalizedBatch && rawName.startsWith(`${normalizedBatch}_`)) {
        suffix = rawName.slice(normalizedBatch.length + 1);
    } else {
        const internalMatch = rawName.match(INTERNAL_CHILD_NAME_RE);
        suffix = internalMatch?.[1] || '';
    }

    if (!suffix) return rawName;

    let match = suffix.match(/^(rfa|fampnn|maturation|validated_maturation|maturation_post_validation)_(\d+)$/i);
    if (match) {
        return `${humanizeChildStage(match[1])} ${Number(match[2]) + 1}`;
    }

    match = suffix.match(/^(protenix|boltz2|boltz)_batch_(\d+)$/i);
    if (match) {
        return `${humanizeChildStage(match[1])} ${Number(match[2]) + 1}`;
    }

    return rawName;
}

function getDisplayJobName(job: Pick<QueuedJob, 'name' | 'batch_name'>): string {
    return prettifyInternalChildName(job.name, job.batch_name);
}

function getDisplayBatchName(batchName?: string | null): string | null {
    const normalized = batchName?.trim() || '';
    if (!normalized || INTERNAL_BATCH_NAME_RE.test(normalized)) {
        return null;
    }
    return normalized;
}

function getModelBadge(modelId: string): string {
    const key = modelId.toLowerCase();
    if (key === 'msa_batch') return 'MSA';
    if (key.includes('nanopore')) return 'NGS';
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

function isNgsJob(modelId: string, mode?: string): boolean {
    const modelKey = modelId.toLowerCase();
    const modeKey = (mode || '').toLowerCase();
    return (
        modelKey === 'nanopore' ||
        modelKey.includes('nanopore') ||
        modeKey === 'methylation_analysis' ||
        modeKey === 'nanopore_methylation'
    );
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

function VramBadge({ vramMb, label = 'VRAM', tone = 'accent' }: { vramMb: number | null; label?: string; tone?: 'accent' | 'live' | 'muted' }) {
    if (!vramMb) return null;
    const vramGb = (vramMb / 1024).toFixed(1);
    const toneClasses =
        tone === 'live'
            ? 'bg-emerald-500/20 text-emerald-400'
            : tone === 'muted'
                ? 'bg-slate-500/20 text-slate-400'
                : 'bg-accent/20 text-accent';
    return (
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${toneClasses}`}>
            {label} {vramGb} GB
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

function formatGpuList(gpuIds: number[] | null | undefined): string | null {
    if (!gpuIds || gpuIds.length === 0) return null;
    return gpuIds.map((gpuId) => GPU_NAMES[gpuId] || `GPU ${gpuId}`).join(', ');
}

// Elapsed time badge for running jobs
// NOTE: tick prop is passed from parent to trigger re-renders without N intervals
function ElapsedTimeBadge({ startedAt, tick: _tick }: { startedAt: string | null; tick: number }) {
    if (!startedAt) return null;

    const start = parseApiTimestamp(startedAt);
    if (start === null) return null;
    const now = Date.now();
    const elapsedMs = Math.max(0, now - start);

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
        'doradobasecall': 'Dorado Basecall',
        'doradoalign': 'Dorado Align',
        'preparebamforanalysis': 'BAM Prepare',
        'modkitpileup': 'modkit Pileup',
        'modkitsummary': 'modkit Summary',
        'fastqalign': 'FASTQ Align',
        'fastq_align': 'FASTQ Align',
        'fastqplasmidqc': 'FASTQ Plasmid QC',
        'fastq_qc': 'FASTQ Plasmid QC',
        'fastqmultimerqc': 'Multimer QC',
        'dorado_basecall': 'Dorado Basecall',
        'dorado_align': 'Dorado Align',
        'bam_prepare': 'BAM Prepare',
        'modkit': 'modkit',
        'multimer_qc': 'Multimer QC',
        'runclonevalidation': 'wf-clone-validation',
        'wf_clone_validation': 'wf-clone-validation',
        'wf-clone-validation': 'wf-clone-validation',
        'rfantibody': 'RFAntibody',
        'rfdiffusion': 'RFdiffusion',
        'rfdpoly': 'RFDpoly',
        'nampnn': 'NA-MPNN',
        'pyrosetta_rebuild': 'PyRosetta',
        'fampnn': 'FA-MPNN',
        'proteinmpnn': 'ProteinMPNN',
        'thermompnn': 'ThermoMPNN',
        'structure_validation': 'Structure Validation',
        'spawnrfantibodyjobs': 'Queueing RFA',
        'waitforchildren': 'Waiting RFA Children',
        'spawnfampnnjobs': 'Queueing FA-MPNN',
        'waitforfampnnchildren': 'Waiting FA-MPNN',
        'spawnmaturationjobs': 'Queueing PPIFlow',
        'waitformaturationchildren': 'Waiting PPIFlow',
        'spawnvalidatedmaturationjobs': 'Queueing PPIFlow',
        'waitforvalidatedmaturationchildren': 'Waiting PPIFlow',
        'spawnchildjobs': 'Queueing Validation',
        'waitandaggregatechildresults': 'Waiting Validation',
        'collectfampnnoutputs': 'Collecting FA-MPNN',
        'collectmaturationoutputs': 'Collecting PPIFlow',
        'triggeranarciiannotation': 'Trigger ANARCII',
        'boltz2': 'Boltz-2',
        'protenix': 'Protenix',
        'maturation_post_validation': 'PPIFlow Repair',
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
    const [showNgsJobs, setShowNgsJobs] = useState(true);
    // Single timer for all elapsed time badges (fixes N-interval proliferation)
    const [elapsedTick, setElapsedTick] = useState(0);

    useEffect(() => {
        const timer = setInterval(() => setElapsedTick(t => t + 1), 1000);
        return () => clearInterval(timer);
    }, []);

    // Fetch queue data
    const { data: queueData, isLoading } = useQuery({
        queryKey: ['queue'],
        queryFn: () => fetchQueue(),
        refetchInterval: 5000,
        refetchIntervalInBackground: false,
        refetchOnWindowFocus: false,
    });

    const { data: cancelledData } = useQuery({
        queryKey: ['cancelledJobs'],
        queryFn: () => fetchCancelledJobs(20),
        refetchInterval: 10000,
        refetchIntervalInBackground: false,
        refetchOnWindowFocus: false,
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

    const forceLaunchMutation = useMutation({
        mutationFn: ({ jobId, gpuId }: { jobId: string; gpuId: number }) =>
            forceLaunchQueueJob(jobId, gpuId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['queue'] });
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
        },
    });

    const queue = queueData?.data || [];
    const stats = {
        queued: queue.filter(j => j.queue_status === 'queued').length,
        running: queue.filter(j => j.queue_status === 'running').length,
        paused: queue.filter(j => j.queue_status === 'paused').length,
        total: queue.length,
    };
    const cancelledJobsRaw = cancelledData?.data || [];
    const isPending = pauseMutation.isPending || resumeMutation.isPending ||
        cancelMutation.isPending || pinMutation.isPending || cancelAllMutation.isPending || killActiveMutation.isPending || forceLaunchMutation.isPending;

    const visibleQueue = showNgsJobs ? queue : queue.filter(j => !isNgsJob(j.model_id, j.mode));
    const cancelledJobs = showNgsJobs ? cancelledJobsRaw : cancelledJobsRaw.filter(j => !isNgsJob(j.model_id, j.mode));

    // Separate running, queued, and pending_msa jobs
    const runningJobs = visibleQueue.filter(j => j.queue_status === 'running');
    const queuedJobs = visibleQueue.filter(j => j.queue_status === 'queued' || j.queue_status === 'paused');
    const pendingMsaJobs = visibleQueue.filter(j => j.queue_status === 'pending_msa');
    const hiddenQueuedCount = showNgsJobs
        ? 0
        : Math.max(
            0,
            queue.filter(j => j.queue_status === 'queued' || j.queue_status === 'paused').length - queuedJobs.length
        );

    const handleCancelAll = () => {
        if (queuedJobs.length === 0) return;
        const hiddenNgsNote = hiddenQueuedCount > 0
            ? ` This will also clear ${hiddenQueuedCount} hidden NGS job(s).`
            : '';
        if (confirm(`Clear ${queuedJobs.length} queued jobs from database?${hiddenNgsNote}`)) {
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
                        onClick={() => setShowNgsJobs(!showNgsJobs)}
                        className={`px-2 py-1 rounded text-xs ${showNgsJobs ? 'bg-cyan-600/30 text-cyan-300' : 'bg-slate-700/50 text-slate-400 hover:text-white'}`}
                        title={showNgsJobs ? 'Hide NGS jobs' : 'Show NGS jobs'}
                    >
                        {showNgsJobs ? 'Hide NGS' : 'Show NGS'}
                    </button>
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
                                                <span className="text-sm text-white truncate" title={job.name}>{getDisplayJobName(job)}</span>
                                                <VramBadge vramMb={job.vram_estimate_mb} label="Est" tone="muted" />
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
                            <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-accent" />
                        </div>
                    ) : visibleQueue.length === 0 ? (
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
                                                    if (confirm(`Cancel "${getDisplayJobName(job)}"?`)) {
                                                        cancelMutation.mutate(job.id);
                                                    }
                                                }}
                                                isPending={isPending}
                                                elapsedTick={elapsedTick}
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
                                                    if (confirm(`Cancel "${getDisplayJobName(job)}"?`)) {
                                                        cancelMutation.mutate(job.id);
                                                    }
                                                }}
                                                isPending={isPending}
                                                elapsedTick={elapsedTick}
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
                                                    if (confirm(`Cancel "${getDisplayJobName(job)}"?`)) {
                                                        cancelMutation.mutate(job.id);
                                                    }
                                                }}
                                                onPin={(gpuId) => pinMutation.mutate({ jobId: job.id, gpuId })}
                                                onForceLaunch={(gpuId) => forceLaunchMutation.mutate({ jobId: job.id, gpuId })}
                                                isPending={isPending}
                                                elapsedTick={elapsedTick}
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
    onForceLaunch?: (gpuId: number) => void;
    isPending: boolean;
    elapsedTick: number;  // For elapsed time display
}

function JobRow({
    job,
    onPause,
    onResume,
    onCancel,
    onPin,
    onForceLaunch,
    isPending,
    elapsedTick,
}: JobRowProps) {
    const [showPinMenu, setShowPinMenu] = useState(false);
    const [showForceMenu, setShowForceMenu] = useState(false);
    const displayName = getDisplayJobName(job);
    const displayBatchName = getDisplayBatchName(job.batch_name);
    const candidateGpuLabel = formatGpuList(job.scheduler_candidate_gpus);

    return (
        <div className="bg-slate-700/30 rounded-lg p-3 hover:bg-slate-700/50 transition-colors">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                    <span className={`px-1.5 py-0.5 rounded text-xs font-mono ${isMsaJob(job.model_id) ? 'bg-violet-600 text-white' : 'bg-slate-600 text-slate-300'}`}>{getModelBadge(job.model_id)}</span>
                    <div className="min-w-0">
                        <div className="flex items-center gap-2">
                            <span className="font-medium text-white truncate" title={job.name}>{displayName}</span>
                            <StatusBadge status={job.queue_status} paused={job.paused} />
                        </div>
                        <div className="flex items-center gap-2 mt-1">
                            <span className="text-xs text-slate-500">{job.mode}</span>
                            {job.queue_status === 'running' ? (
                                <>
                                    {job.live_vram_mb ? (
                                        <VramBadge vramMb={job.live_vram_mb} label="Live" tone="live" />
                                    ) : (
                                        <span
                                            className="px-2 py-0.5 rounded text-xs font-medium bg-slate-500/20 text-slate-400"
                                            title={job.vram_estimate_mb ? `Scheduler estimate: ${(job.vram_estimate_mb / 1024).toFixed(1)} GB` : 'Live VRAM not available for this process yet'}
                                        >
                                            Live n/a
                                        </span>
                                    )}
                                </>
                            ) : (
                                <VramBadge vramMb={job.vram_estimate_mb} label="Est" tone="muted" />
                            )}
                            <GPUBadge gpu={job.assigned_gpu ?? job.pinned_gpu} pinned={job.pinned_gpu !== null} />
                            {job.priority > 0 && (
                                <span className="px-2 py-0.5 rounded text-xs font-medium bg-amber-500/20 text-amber-400">
                                    P{job.priority}
                                </span>
                            )}
                            {displayBatchName && (
                                <span className="px-2 py-0.5 rounded text-xs font-medium bg-accent/20 text-accent" title={`Batch: ${job.batch_name}`}>
                                    📦 {displayBatchName.length > 15 ? displayBatchName.slice(0, 15) + '...' : displayBatchName}
                                </span>
                            )}
                            {/* Elapsed time for running jobs */}
                            {job.queue_status === 'running' && (
                                <>
                                    <StageBadge stage={job.current_stage} progress={job.stage_progress} />
                                    <ElapsedTimeBadge startedAt={job.started_at} tick={elapsedTick} />
                                </>
                            )}
                            {job.queue_status === 'queued' && job.scheduler_required_mb ? (
                                <VramBadge vramMb={job.scheduler_required_mb} label="Need" tone="accent" />
                            ) : null}
                            {job.queue_status === 'queued' && job.scheduler_ready ? (
                                <span className="px-2 py-0.5 rounded text-xs font-medium bg-emerald-500/20 text-emerald-400">
                                    Ready now
                                </span>
                            ) : null}
                        </div>
                        {job.queue_status === 'queued' && (
                            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                                {candidateGpuLabel && (
                                    <span className="text-slate-400">
                                        GPUs: <span className="text-cyan-300">{candidateGpuLabel}</span>
                                    </span>
                                )}
                                {job.scheduler_blockers && job.scheduler_blockers.length > 0 && (
                                    <span className="text-amber-300" title={job.scheduler_blockers.join(' | ')}>
                                        Why waiting: {job.scheduler_blockers.join(' • ')}
                                    </span>
                                )}
                            </div>
                        )}
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

                    {/* Force Launch - only for queued jobs */}
                    {job.queue_status === 'queued' && onForceLaunch && (
                        <div className="relative">
                            <button
                                onClick={() => setShowForceMenu(!showForceMenu)}
                                disabled={isPending}
                                className="px-2 py-1 rounded bg-emerald-600/50 hover:bg-emerald-600 text-white text-xs disabled:opacity-50"
                                title="Force launch on GPU (bypass VRAM checks)"
                            >
                                Force
                            </button>
                            {showForceMenu && (
                                <div className="absolute right-0 top-8 z-10 bg-slate-800 border border-slate-600 rounded-lg shadow-xl py-1 min-w-[140px]">
                                    {[0, 1, 2, 3].map((gpu) => (
                                        <button
                                            key={gpu}
                                            onClick={() => { onForceLaunch(gpu); setShowForceMenu(false); }}
                                            className="w-full text-left px-3 py-1.5 text-sm hover:bg-slate-700 text-slate-300"
                                        >
                                            {GPU_NAMES[gpu]}
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
                            Resume
                        </button>
                    ) : job.queue_status !== 'running' && onPause ? (
                        <button
                            onClick={onPause}
                            disabled={isPending}
                            className="px-2 py-1 rounded bg-yellow-600/50 hover:bg-yellow-600 text-white text-xs disabled:opacity-50"
                            title="Pause"
                        >
                            Pause
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
