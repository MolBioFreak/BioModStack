import React, { useState } from 'react';
import type { Job } from '../../lib/api';
import { JobDetailsPanel } from '../JobDetailsPanel';

interface JobQueueTableProps {
    jobs: Job[];
    loading: boolean;
    onCancel: (jobId: string, name: string) => void;
    onResubmit: (jobId: string, name: string) => void;
    onResume: (job: Job) => void;
    onViewLogs: (jobId: string) => void;
    onViewQuick: (jobId: string) => void;
    quickViewJobId: string | null;
}

export function JobQueueTable({
    jobs,
    loading,
    onCancel,
    onResubmit,
    onResume,
    onViewLogs,
    onViewQuick,
    quickViewJobId
}: JobQueueTableProps) {
    const [expandedJobId, setExpandedJobId] = useState<string | null>(null);

    if (loading) {
        return (
            <div className="flex items-center justify-center py-12">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-500" />
            </div>
        );
    }

    return (
        <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700 rounded-xl overflow-hidden">
            <table className="w-full">
                <thead>
                    <tr className="border-b border-slate-700">
                        <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">Name</th>
                        <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">Mode</th>
                        <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">Status</th>
                        <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">Designs</th>
                        <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">Created</th>
                        <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {(() => {
                        if (!jobs.length) {
                            return (
                                <tr>
                                    <td colSpan={6} className="py-8 text-center text-slate-500">
                                        No jobs found
                                    </td>
                                </tr>
                            );
                        }

                        // Group jobs by batch while preserving order
                        const batchMap = new Map<string, typeof jobs>();
                        const seenBatches = new Set<string>();

                        // Create ordered list of displayable items (batches or standalone jobs)
                        type DisplayItem = { type: 'batch'; batchId: string; jobs: typeof jobs; firstDate: string }
                            | { type: 'standalone'; job: typeof jobs[0] };
                        const displayItems: DisplayItem[] = [];

                        // First pass: collect batches and identify positions
                        jobs.forEach(job => {
                            if (job.batch_id && job.batch_name) {
                                const existing = batchMap.get(job.batch_id) || [];
                                existing.push(job);
                                batchMap.set(job.batch_id, existing);
                            }
                        });

                        // Second pass: build ordered display list
                        jobs.forEach(job => {
                            if (job.batch_id && job.batch_name) {
                                // Only add batch once (when we see it first time)
                                if (!seenBatches.has(job.batch_id)) {
                                    seenBatches.add(job.batch_id);
                                    const batchJobs = batchMap.get(job.batch_id)!;
                                    displayItems.push({
                                        type: 'batch',
                                        batchId: job.batch_id,
                                        jobs: batchJobs,
                                        firstDate: batchJobs[0].created_at
                                    });
                                }
                            } else {
                                displayItems.push({ type: 'standalone', job });
                            }
                        });

                        const rows: React.ReactNode[] = [];

                        // Render items in order (maintaining date sort from API)
                        displayItems.forEach((item) => {
                            if (item.type === 'batch') {
                                const batchJobs = item.jobs;
                                const batchId = item.batchId;
                                const batchName = batchJobs[0].batch_name!;
                                const totalDesigns = batchJobs.reduce((sum, j) => sum + j.design_count, 0);
                                const allCompleted = batchJobs.every(j => j.status === 'completed');
                                const anyRunning = batchJobs.some(j => j.status === 'running');
                                const anyFailed = batchJobs.some(j => j.status === 'failed');

                                // Batch header row
                                rows.push(
                                    <tr key={`batch-${batchId}`} className="bg-purple-500/10 border-b border-purple-500/30">
                                        <td colSpan={6} className="py-2 px-4">
                                            <div className="flex items-center justify-between">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-purple-400">📦</span>
                                                    <span className="text-white font-medium">{batchName}</span>
                                                    <span className="text-purple-300 text-sm">({batchJobs.length} sims)</span>
                                                </div>
                                                <div className="flex items-center gap-4 text-sm">
                                                    <span className="text-slate-400">{totalDesigns} designs</span>
                                                    <StatusBadge status={anyFailed ? 'failed' : anyRunning ? 'running' : allCompleted ? 'completed' : 'queued'} />
                                                </div>
                                            </div>
                                        </td>
                                    </tr>
                                );

                                // Individual jobs in batch (indented)
                                batchJobs.forEach(job => {
                                    rows.push(
                                        <React.Fragment key={job.id}>
                                            <tr
                                                onClick={() => setExpandedJobId(expandedJobId === job.id ? null : job.id)}
                                                className={`border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors cursor-pointer ${expandedJobId === job.id ? 'bg-slate-700/40' : ''}`}
                                            >
                                                <td className="py-3 px-4 text-white font-medium pl-10">
                                                    <span className="mr-2">{expandedJobId === job.id ? '▼' : '▶'}</span>
                                                    {job.name.replace(batchName + '_', '')}
                                                </td>
                                                <td className="py-3 px-4">
                                                    <span className="px-2 py-1 bg-blue-500/20 text-blue-400 rounded text-xs">
                                                        {job.mode}
                                                    </span>
                                                </td>
                                                <td className="py-3 px-4">
                                                    <StatusBadge status={job.status} errorMessage={job.error_message} />
                                                </td>
                                                <td className="py-3 px-4 text-slate-300">{job.design_count}</td>
                                                <td className="py-3 px-4 text-slate-400 text-sm">
                                                    {new Date(job.created_at).toLocaleTimeString()}
                                                </td>
                                                <td className="py-3 px-4">
                                                    <div className="flex items-center gap-2">
                                                        {job.status === 'completed' && (
                                                            <button
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    onViewQuick(job.id);
                                                                }}
                                                                className={`px-2 py-1 text-xs rounded transition-colors ${quickViewJobId === job.id
                                                                    ? 'bg-purple-500/30 text-purple-300'
                                                                    : 'bg-purple-500/20 text-purple-400 hover:bg-purple-500/30'
                                                                    }`}
                                                                title="Load in Quick Viewer"
                                                            >
                                                                🔬 View
                                                            </button>
                                                        )}
                                                    </div>
                                                </td>
                                            </tr>
                                            {expandedJobId === job.id && (
                                                <JobDetailsPanel
                                                    job={job}
                                                    onClose={() => setExpandedJobId(null)}
                                                />
                                            )}
                                        </React.Fragment>
                                    );
                                });
                            } else {
                                // Standalone job
                                const job = item.job;
                                rows.push(
                                    <React.Fragment key={job.id}>
                                        <tr
                                            onClick={() => setExpandedJobId(expandedJobId === job.id ? null : job.id)}
                                            className={`border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors cursor-pointer ${expandedJobId === job.id ? 'bg-slate-700/40' : ''}`}
                                        >
                                            <td className="py-3 px-4 text-white font-medium">
                                                <div className="flex flex-col">
                                                    <div className="flex items-center">
                                                        <span className="mr-2">{expandedJobId === job.id ? '▼' : '▶'}</span>
                                                        {job.name}
                                                    </div>
                                                    <StageProgress job={job} />
                                                </div>
                                            </td>
                                            <td className="py-3 px-4">
                                                <span className="px-2 py-1 bg-blue-500/20 text-blue-400 rounded text-xs">
                                                    {job.mode}
                                                </span>
                                            </td>
                                            <td className="py-3 px-4">
                                                <StatusBadge status={job.status} errorMessage={job.error_message} />
                                            </td>
                                            <td className="py-3 px-4 text-slate-300">{job.design_count}</td>
                                            <td className="py-3 px-4 text-slate-400 text-sm">
                                                {new Date(job.created_at).toLocaleString()}
                                            </td>
                                            <td className="py-3 px-4">
                                                <div className="flex items-center gap-2">
                                                    {job.status === 'completed' && (
                                                        <button
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                onViewQuick(job.id);
                                                            }}
                                                            className={`px-2 py-1 text-xs rounded transition-colors ${quickViewJobId === job.id
                                                                ? 'bg-purple-500/30 text-purple-300'
                                                                : 'bg-purple-500/20 text-purple-400 hover:bg-purple-500/30'
                                                                }`}
                                                            title="Load in Quick Viewer"
                                                        >
                                                            🔬 View
                                                        </button>
                                                    )}
                                                    {(job.status === 'running' || job.status === 'queued') && (
                                                        <button
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                onCancel(job.id, job.name);
                                                            }}
                                                            className="px-2 py-1 text-xs bg-red-500/20 text-red-400 hover:bg-red-500/30 hover:text-red-300 rounded transition-colors"
                                                        >
                                                            Cancel
                                                        </button>
                                                    )}
                                                    {(job.status === 'failed' || job.status === 'cancelled') && (
                                                        <>
                                                            <button
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    onViewLogs(job.id);
                                                                }}
                                                                className="px-2 py-1 text-xs bg-slate-500/20 text-slate-400 hover:bg-slate-500/30 hover:text-slate-300 rounded transition-colors"
                                                            >
                                                                📋 Logs
                                                            </button>
                                                            <button
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    onResume(job);
                                                                }}
                                                                className="px-2 py-1 text-xs bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 hover:text-emerald-300 rounded transition-colors"
                                                            >
                                                                ⏯ Resume
                                                            </button>
                                                            <button
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    onResubmit(job.id, job.name);
                                                                }}
                                                                className="px-2 py-1 text-xs bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30 hover:text-yellow-300 rounded transition-colors"
                                                            >
                                                                🔄 Retry
                                                            </button>
                                                        </>
                                                    )}
                                                </div>
                                            </td>
                                        </tr>
                                        {expandedJobId === job.id && (
                                            <JobDetailsPanel
                                                job={job}
                                                onClose={() => setExpandedJobId(null)}
                                            />
                                        )}
                                    </React.Fragment>
                                );
                            }
                        });

                        return rows;
                    })()}
                </tbody>
            </table>
        </div>
    );
}

function StatusBadge({ status, errorMessage }: { status: string; errorMessage?: string | null }) {
    const styles: Record<string, string> = {
        queued: 'bg-slate-500/20 text-slate-400',
        running: 'bg-blue-500/20 text-blue-400 animate-pulse',
        completed: 'bg-green-500/20 text-green-400',
        failed: 'bg-red-500/20 text-red-400',
        cancelled: 'bg-orange-500/20 text-orange-400',
    };

    const showTooltip = (status === 'failed' || status === 'cancelled') && errorMessage;
    const truncatedError = errorMessage
        ? errorMessage.split('\n')[0].substring(0, 100) + (errorMessage.length > 100 ? '...' : '')
        : null;

    return (
        <div className="relative group inline-block">
            <span className={`px-2 py-1 rounded text-xs font-medium cursor-default ${styles[status] ?? styles.queued}`}>
                {status}
            </span>
            {showTooltip && (
                <div className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-2 
                    bg-slate-800 border border-slate-600 rounded-lg shadow-xl
                    text-xs text-slate-200 whitespace-nowrap max-w-xs
                    opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none">
                    <div className="font-medium text-red-400 mb-1">Error:</div>
                    <div className="text-slate-300 break-words whitespace-normal">{truncatedError}</div>
                    <div className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-slate-600" />
                </div>
            )}
        </div>
    );
}

function StageProgress({ job }: { job: Job }) {
    const getStages = (mode: string) => {
        if (mode.includes('antibody')) return ['rfantibody', 'fampnn', 'boltz2'];
        if (mode.includes('binder')) return ['rfdiffusion', 'proteinmpnn', 'boltz2'];
        if (mode.includes('monomer')) return ['rfdiffusion', 'proteinmpnn', 'af2'];
        return [];
    };

    const stages = job.all_stages && job.all_stages.length > 0 
        ? job.all_stages 
        : getStages(job.mode);

    if (stages.length === 0) return null;

    const completed = job.completed_stages || [];
    const current = job.current_stage;

    return (
        <div className="flex items-center space-x-1 mt-1">
            {stages.map((stage, idx) => {
                const isCompleted = completed.includes(stage);
                const isCurrent = stage === current;
                const isPending = !isCompleted && !isCurrent;

                return (
                    <div key={stage} className="flex items-center">
                        <div className={`
                            px-1.5 py-0.5 text-[10px] uppercase tracking-wider font-semibold rounded-[3px] border
                            ${isCompleted ? 'bg-emerald-500/20 border-emerald-500/30 text-emerald-400' : ''}
                            ${isCurrent ? 'bg-blue-500/20 border-blue-500/30 text-blue-400 animate-pulse' : ''}
                            ${isPending ? 'bg-slate-800/50 border-slate-700 text-slate-600' : ''}
                        `}>
                            {stage}
                        </div>
                        {idx < stages.length - 1 && (
                            <div className={`w-1 h-px mx-0.5 ${isCompleted ? 'bg-emerald-500/30' : 'bg-slate-700'}`} />
                        )}
                    </div>
                );
            })}
        </div>
    );
}
