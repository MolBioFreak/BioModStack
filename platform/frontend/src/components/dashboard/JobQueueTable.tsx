import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import type { Job } from '../../lib/api';
import { JobDetailsPanel } from '../JobDetailsPanel';
import { getModeDisplayName, getStageDisplayName } from '../../constants/displayNames';

type SortColumn = 'name' | 'mode' | 'status' | 'designs' | 'created';
type SortDirection = 'asc' | 'desc';

interface JobQueueTableProps {
    jobs: Job[];
    loading: boolean;
    onCancel: (jobId: string, name: string) => void;
    onResubmit: (jobId: string, name: string) => void;
    onResume: (job: Job) => void;
    onViewLogs: (jobId: string) => void;
    onViewQuick: (jobId: string) => void;
    onClone?: (job: Job) => void;
    onDelete?: (jobId: string, name: string) => void;
    onForceRun?: (jobId: string, gpuId?: number) => void;
    quickViewJobId: string | null;
    debugMode?: boolean;
}

export function JobQueueTable({
    jobs,
    loading,
    onCancel,
    onResubmit,
    onResume,
    onViewLogs,
    onViewQuick,
    onClone,
    onDelete,
    onForceRun,
    quickViewJobId,
    debugMode = false
}: JobQueueTableProps) {
    const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
    const [sortColumn, setSortColumn] = useState<SortColumn>('created');
    const [sortDirection, setSortDirection] = useState<SortDirection>('desc');
    const [expandedBatches, setExpandedBatches] = useState<Set<string>>(new Set());

    const handleSort = (column: SortColumn) => {
        if (sortColumn === column) {
            setSortDirection(prev => prev === 'asc' ? 'desc' : 'asc');
        } else {
            setSortColumn(column);
            setSortDirection(column === 'created' ? 'desc' : 'asc');
        }
    };

    const toggleBatch = (batchId: string) => {
        setExpandedBatches(prev => {
            const next = new Set(prev);
            if (next.has(batchId)) {
                next.delete(batchId);
            } else {
                next.add(batchId);
            }
            return next;
        });
    };

    const SortHeader = ({ column, children }: { column: SortColumn; children: React.ReactNode }) => (
        <th
            onClick={() => handleSort(column)}
            className="text-left py-3 px-4 text-sm font-medium text-slate-400 cursor-pointer hover:text-slate-200 select-none"
        >
            <span className="flex items-center gap-1">
                {children}
                {sortColumn === column && (
                    <span className="text-purple-400">{sortDirection === 'asc' ? '▲' : '▼'}</span>
                )}
            </span>
        </th>
    );

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
                        <SortHeader column="name">Name</SortHeader>
                        <SortHeader column="mode">Mode</SortHeader>
                        <SortHeader column="status">Status</SortHeader>
                        <SortHeader column="designs">Designs</SortHeader>
                        <SortHeader column="created">Created</SortHeader>
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

                        // Dynamic sorting based on selected column and direction
                        const statusOrder: Record<string, number> = {
                            running: 0, queued: 1, failed: 2, completed: 3, cancelled: 4
                        };

                        const getValue = (item: DisplayItem, col: SortColumn): string | number => {
                            if (item.type === 'batch') {
                                const jobs = item.jobs;
                                switch (col) {
                                    case 'name': return item.jobs[0].batch_name || '';
                                    case 'mode': return jobs[0].mode;
                                    case 'status': {
                                        if (jobs.some(j => j.status === 'running')) return statusOrder.running;
                                        if (jobs.some(j => j.status === 'queued')) return statusOrder.queued;
                                        if (jobs.some(j => j.status === 'failed')) return statusOrder.failed;
                                        return statusOrder.completed;
                                    }
                                    case 'designs': return jobs.reduce((sum, j) => sum + j.design_count, 0);
                                    case 'created': return new Date(item.firstDate).getTime();
                                }
                            } else {
                                const job = item.job;
                                switch (col) {
                                    case 'name': return job.name;
                                    case 'mode': return job.mode;
                                    case 'status': return statusOrder[job.status] ?? 5;
                                    case 'designs': return job.design_count;
                                    case 'created': return new Date(job.created_at).getTime();
                                }
                            }
                            // Fallback for cases where a value might not be explicitly returned (e.g., if a switch case is missed)
                            return '';
                        };

                        displayItems.sort((a, b) => {
                            const aVal = getValue(a, sortColumn);
                            const bVal = getValue(b, sortColumn);
                            let comparison = 0;
                            if (typeof aVal === 'number' && typeof bVal === 'number') {
                                comparison = aVal - bVal;
                            } else {
                                comparison = String(aVal).localeCompare(String(bVal));
                            }
                            return sortDirection === 'asc' ? comparison : -comparison;
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

                                // Batch header row - clickable to expand/collapse
                                const isExpanded = expandedBatches.has(batchId);
                                rows.push(
                                    <tr
                                        key={`batch-${batchId}`}
                                        className="bg-purple-500/10 border-b border-purple-500/30 cursor-pointer hover:bg-purple-500/20 transition-colors"
                                        onClick={() => toggleBatch(batchId)}
                                    >
                                        <td colSpan={6} className="py-2 px-4">
                                            <div className="flex items-center justify-between">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-purple-400 w-4">{isExpanded ? '▼' : '▶'}</span>
                                                    <span className="text-white font-medium">{batchName}</span>
                                                    <span className="text-purple-300 text-sm">({batchJobs.length} sims)</span>
                                                </div>
                                                <div className="flex items-center gap-4 text-sm">
                                                    <span className="text-slate-400">{totalDesigns} designs</span>
                                                    <span className="text-slate-400">{new Date(item.firstDate).toLocaleString()}</span>
                                                    <StatusBadge status={anyFailed ? 'failed' : anyRunning ? 'running' : allCompleted ? 'completed' : 'queued'} />
                                                    {allCompleted && (
                                                        <Link
                                                            to={`/results?batch_id=${batchId}`}
                                                            onClick={(e) => e.stopPropagation()}
                                                            className="px-2 py-1 text-xs bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 rounded transition-colors"
                                                        >
                                                            Results
                                                        </Link>
                                                    )}
                                                </div>
                                            </div>
                                        </td>
                                    </tr>
                                );


                                // Individual jobs in batch (only render if expanded)
                                if (isExpanded) {
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
                                                            {getModeDisplayName(job.mode)}
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
                                                                    View
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
                                }
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
                                                    {getModeDisplayName(job.mode)}
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
                                                        <>
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
                                                                View
                                                            </button>
                                                            {onClone && (
                                                                <button
                                                                    onClick={(e) => {
                                                                        e.stopPropagation();
                                                                        onClone(job);
                                                                    }}
                                                                    className="px-2 py-1 text-xs bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30 hover:text-cyan-300 rounded transition-colors"
                                                                    title="Clone job parameters"
                                                                >
                                                                    Clone
                                                                </button>
                                                            )}
                                                        </>
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
                                                    {debugMode && job.status === 'queued' && onForceRun && (
                                                        <button
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                onForceRun(job.id);
                                                            }}
                                                            className="px-2 py-1 text-xs bg-amber-500/20 text-amber-400 hover:bg-amber-500/30 hover:text-amber-300 rounded transition-colors border border-amber-500/30"
                                                            title="[DEBUG] Force-run this job immediately, bypassing orchestrator"
                                                        >
                                                            ⚡ Force
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
                                                                Logs
                                                            </button>
                                                            <button
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    onResume(job);
                                                                }}
                                                                className="px-2 py-1 text-xs bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 hover:text-emerald-300 rounded transition-colors"
                                                            >
                                                                Resume
                                                            </button>
                                                            <button
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    onResubmit(job.id, job.name);
                                                                }}
                                                                className="px-2 py-1 text-xs bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30 hover:text-yellow-300 rounded transition-colors"
                                                            >
                                                                Retry
                                                            </button>
                                                        </>
                                                    )}
                                                    {onDelete && (
                                                        <button
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                if (window.confirm(`⚠️ PERMANENTLY DELETE "${job.name}"?\n\nThis will delete:\n- Job from database\n- All child jobs\n- All designs\n- Output directories\n\nThis is IRREVERSIBLE!`)) {
                                                                    onDelete(job.id, job.name);
                                                                }
                                                            }}
                                                            className="px-2 py-1 text-xs bg-red-900/40 text-red-300 hover:bg-red-700/50 hover:text-red-200 rounded transition-colors border border-red-700/50"
                                                            title="DEBUG: Permanently delete job and all data"
                                                        >
                                                            Delete
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

    // Job status determines overall behavior
    const jobIsCompleted = job.status === 'completed';
    const jobIsFailed = job.status === 'failed';
    const jobIsCancelled = job.status === 'cancelled';
    const completed = job.completed_stages || [];
    const current = job.current_stage;

    return (
        <div className="flex items-center space-x-1 mt-1">
            {stages.map((stage, idx) => {
                // Determine stage state based on job status
                const wasCompleted = completed.includes(stage);
                const isCurrent = stage === current;

                // Stage coloring logic:
                // - Completed job: all stages green
                // - Failed job: completed stages green, current stage red, pending gray
                // - Cancelled job: completed stages green, current stage orange, pending gray
                // - Running job: completed stages green, current stage blue (pulsing), pending gray
                let stageClass = '';
                let connectorClass = 'bg-slate-700';

                if (jobIsCompleted) {
                    // All stages completed - show all green
                    stageClass = 'bg-emerald-500/20 border-emerald-500/30 text-emerald-400';
                    connectorClass = 'bg-emerald-500/30';
                } else if (wasCompleted) {
                    // This stage finished successfully
                    stageClass = 'bg-emerald-500/20 border-emerald-500/30 text-emerald-400';
                    connectorClass = 'bg-emerald-500/30';
                } else if (isCurrent && jobIsFailed) {
                    // This is where the job failed - show red
                    stageClass = 'bg-red-500/20 border-red-500/30 text-red-400';
                } else if (isCurrent && jobIsCancelled) {
                    // This is where the job was cancelled - show orange
                    stageClass = 'bg-orange-500/20 border-orange-500/30 text-orange-400';
                } else if (isCurrent) {
                    // Currently running - show blue with pulse
                    stageClass = 'bg-blue-500/20 border-blue-500/30 text-blue-400 animate-pulse';
                } else {
                    // Pending/not reached - show gray
                    stageClass = 'bg-slate-800/50 border-slate-700 text-slate-600';
                }

                return (
                    <div key={stage} className="flex items-center">
                        <div className={`
                            px-1.5 py-0.5 text-[10px] uppercase tracking-wider font-semibold rounded-[3px] border
                            ${stageClass}
                        `}>
                            {getStageDisplayName(stage)}
                        </div>
                        {idx < stages.length - 1 && (
                            <div className={`w-1 h-px mx-0.5 ${connectorClass}`} />
                        )}
                    </div>
                );
            })}
        </div>
    );
}
