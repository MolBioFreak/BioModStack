import React, { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import type { Job } from '../../lib/api';
import { isNgsJob, ngsResultHref } from '../../lib/ngsResultRouting';
import { JobDetailsPanel } from '../JobDetailsPanel';
import { getModeDisplayName, getStageDisplayName } from '../../constants/displayNames';

type SortColumn = 'name' | 'mode' | 'status' | 'designs' | 'created';
type SortDirection = 'asc' | 'desc';

type DisplayItem =
    | { type: 'batch'; batchId: string; jobs: Job[]; firstDate: string }
    | { type: 'standalone'; job: Job };

interface JobQueueTableProps {
    jobs: Job[];
    loading: boolean;
    onCancel: (jobId: string, name: string) => void;
    onResubmit: (jobId: string, name: string) => void;
    onResume: (job: Job) => void;
    onResumeWithSettings?: (job: Job) => void;
    onViewLogs: (jobId: string) => void;
    onViewQuick: (jobId: string) => void;
    onClone?: (job: Job) => void;
    onDelete?: (jobId: string, name: string) => void;
    onForceRun?: (jobId: string, gpuId?: number) => void;
    quickViewJobId: string | null;
    debugMode?: boolean;
}

const statusOrder: Record<string, number> = {
    running: 0,
    awaiting_input: 1,
    queued: 2,
    failed: 3,
    completed: 4,
    cancelled: 5,
};

function isMolecularDynamicsJob(job: Pick<Job, 'model_id' | 'mode'>): boolean {
    return ['md', 'molecular_dynamics'].includes(job.model_id.toLowerCase()) || job.mode.toLowerCase() === 'molecular_dynamics';
}

const MOBILE_MEDIA_QUERY = '(max-width: 767px)';
const MOBILE_TABLE_PANEL_HEIGHT = 'min(56vh, 30rem)';
const DESKTOP_TABLE_PANEL_HEIGHT = 'min(62vh, 38rem)';

export function JobQueueTable({
    jobs,
    loading,
    onCancel,
    onResubmit,
    onResume,
    onResumeWithSettings,
    onViewLogs,
    onViewQuick,
    onClone,
    onDelete,
    onForceRun,
    quickViewJobId,
    debugMode = false,
}: JobQueueTableProps) {
    const location = useLocation();
    const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
    const [sortColumn, setSortColumn] = useState<SortColumn>('created');
    const [sortDirection, setSortDirection] = useState<SortDirection>('desc');
    const [expandedBatches, setExpandedBatches] = useState<Set<string>>(new Set());
    const [mobileView, setMobileView] = useState<boolean>(() => {
        if (typeof window === 'undefined') return false;
        return window.matchMedia(MOBILE_MEDIA_QUERY).matches;
    });

    useEffect(() => {
        if (typeof window === 'undefined') return undefined;
        const mediaQuery = window.matchMedia(MOBILE_MEDIA_QUERY);
        const handleChange = (event: MediaQueryListEvent) => setMobileView(event.matches);
        setMobileView(mediaQuery.matches);
        mediaQuery.addEventListener('change', handleChange);
        return () => mediaQuery.removeEventListener('change', handleChange);
    }, []);

    const handleSort = (column: SortColumn) => {
        if (sortColumn === column) {
            setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'));
            return;
        }
        setSortColumn(column);
        setSortDirection(column === 'created' ? 'desc' : 'asc');
    };

    const toggleBatch = (batchId: string) => {
        setExpandedBatches((prev) => {
            const next = new Set(prev);
            if (next.has(batchId)) {
                next.delete(batchId);
            } else {
                next.add(batchId);
            }
            return next;
        });
    };

    const getDisplayDesignCount = (job: Job) => job.requested_design_count ?? job.design_count;

    const getAwaitingPromptSummary = (job: Job) => {
        if (job.status !== 'awaiting_input') return '';
        const title = String(job.awaiting_payload?.title || '').trim();
        const message = String(job.awaiting_payload?.message || '').trim();
        return [title, message].filter(Boolean).join(': ');
    };

    const getAwaitingContinueLabel = (job: Job) => {
        const label = String(job.awaiting_payload?.continue_label || '').trim();
        return label || 'Continue';
    };

    const usesDirectAwaitingContinue = (job: Job) => Boolean(job.awaiting_payload?.resume_direct);

    const formatCreatedAt = (value: string) => new Date(value).toLocaleString();

    const renderDesignCountCell = (job: Job) => {
        const displayCount = getDisplayDesignCount(job);
        const hasSeparateStoredCount =
            typeof job.requested_design_count === 'number' &&
            job.requested_design_count !== job.design_count;
        const title = hasSeparateStoredCount
            ? `Requested designs: ${job.requested_design_count}; stored design rows: ${job.design_count}`
            : undefined;

        return (
            <div className="flex flex-col" title={title}>
                <span className="text-slate-300">{displayCount}</span>
                {hasSeparateStoredCount && (
                    <span className="text-[11px] leading-tight text-slate-500">
                        {job.design_count} stored
                    </span>
                )}
            </div>
        );
    };

    const getDisplayItems = (): DisplayItem[] => {
        const batchMap = new Map<string, Job[]>();
        const seenBatches = new Set<string>();
        const displayItems: DisplayItem[] = [];

        jobs.forEach((job) => {
            if (!job.batch_id || !job.batch_name) return;
            const existing = batchMap.get(job.batch_id) || [];
            existing.push(job);
            batchMap.set(job.batch_id, existing);
        });

        jobs.forEach((job) => {
            if (job.batch_id && job.batch_name) {
                if (seenBatches.has(job.batch_id)) return;
                seenBatches.add(job.batch_id);
                const batchJobs = batchMap.get(job.batch_id) || [];
                displayItems.push({
                    type: 'batch',
                    batchId: job.batch_id,
                    jobs: batchJobs,
                    firstDate: batchJobs[0]?.created_at || job.created_at,
                });
                return;
            }
            displayItems.push({ type: 'standalone', job });
        });

        const getValue = (item: DisplayItem, column: SortColumn): string | number => {
            if (item.type === 'batch') {
                const batchJobs = item.jobs;
                switch (column) {
                    case 'name':
                        return batchJobs[0]?.batch_name || '';
                    case 'mode':
                        return batchJobs[0]?.mode || '';
                    case 'status':
                        if (batchJobs.some((job) => job.status === 'running')) return statusOrder.running;
                        if (batchJobs.some((job) => job.status === 'awaiting_input')) return statusOrder.awaiting_input;
                        if (batchJobs.some((job) => job.status === 'queued')) return statusOrder.queued;
                        if (batchJobs.some((job) => job.status === 'failed')) return statusOrder.failed;
                        if (batchJobs.some((job) => job.status === 'cancelled')) return statusOrder.cancelled;
                        return statusOrder.completed;
                    case 'designs':
                        return batchJobs.reduce((sum, job) => sum + getDisplayDesignCount(job), 0);
                    case 'created':
                        return new Date(item.firstDate).getTime();
                }
            }

            const job = item.job;
            switch (column) {
                case 'name':
                    return job.name;
                case 'mode':
                    return job.mode;
                case 'status':
                    return statusOrder[job.status] ?? statusOrder.cancelled;
                case 'designs':
                    return getDisplayDesignCount(job);
                case 'created':
                    return new Date(job.created_at).getTime();
            }
        };

        displayItems.sort((a, b) => {
            const aValue = getValue(a, sortColumn);
            const bValue = getValue(b, sortColumn);
            const comparison =
                typeof aValue === 'number' && typeof bValue === 'number'
                    ? aValue - bValue
                    : String(aValue).localeCompare(String(bValue));
            return sortDirection === 'asc' ? comparison : -comparison;
        });

        return displayItems;
    };

    const displayItems = getDisplayItems();

    const renderJobActions = (job: Job, compactButtons = false) => {
        const buttonClass = compactButtons ? 'px-2.5 py-1.5 text-[11px]' : 'px-2 py-1 text-xs';
        const mdJob = isMolecularDynamicsJob(job);
        const ngsJob = isNgsJob(job);

        return (
            <div className={`flex flex-wrap items-center ${compactButtons ? 'gap-1.5' : 'gap-2'}`}>
                {mdJob && (
                    <Link
                        to={`/designs/${job.id}`}
                        onClick={(event) => event.stopPropagation()}
                        className={`${buttonClass} rounded border border-cyan-400/40 bg-cyan-500/10 font-medium text-cyan-100 transition-colors hover:bg-cyan-500/20`}
                        title="Open durable MD lifecycle operations and governed results"
                    >
                        MD Operations
                    </Link>
                )}
                {job.status === 'completed' && !mdJob && (
                    <>
                        {ngsJob ? (
                            <Link
                                to={ngsResultHref(job.id, location.search)}
                                onClick={(event) => event.stopPropagation()}
                                className={`${buttonClass} rounded bg-emerald-500/20 text-emerald-400 transition-colors hover:bg-emerald-500/30`}
                                title="Open the selected NGS Run Inspector"
                            >
                                NGS Run Inspector
                            </Link>
                        ) : (
                            <>
                                <button
                                    onClick={(event) => {
                                        event.stopPropagation();
                                        onViewQuick(job.id);
                                    }}
                                    className={`${buttonClass} rounded transition-colors ${
                                        quickViewJobId === job.id
                                            ? 'bg-accent/30 text-accent'
                                            : 'bg-accent/20 text-accent hover:bg-accent/30'
                                    }`}
                                    title="Load in Quick Viewer"
                                >
                                    View
                                </button>
                                <Link
                                    to={`/designs/${job.id}`}
                                    onClick={(event) => event.stopPropagation()}
                                    className={`${buttonClass} rounded bg-emerald-500/20 text-emerald-400 transition-colors hover:bg-emerald-500/30`}
                                    title="Open in Results Viewer"
                                >
                                    Results
                                </Link>
                            </>
                        )}
                        {onClone && (
                            <button
                                onClick={(event) => {
                                    event.stopPropagation();
                                    onClone(job);
                                }}
                                className={`${buttonClass} rounded bg-cyan-500/20 text-cyan-400 transition-colors hover:bg-cyan-500/30 hover:text-cyan-300`}
                                title="Clone job parameters"
                            >
                                Clone
                            </button>
                        )}
                    </>
                )}

                {!mdJob && (job.status === 'running' || job.status === 'queued') && (
                    <button
                        onClick={(event) => {
                            event.stopPropagation();
                            onCancel(job.id, job.name);
                        }}
                        className={`${buttonClass} rounded bg-red-500/20 text-red-400 transition-colors hover:bg-red-500/30 hover:text-red-300`}
                    >
                        Cancel
                    </button>
                )}

                {!mdJob && debugMode && job.status === 'queued' && onForceRun && (
                    <button
                        onClick={(event) => {
                            event.stopPropagation();
                            onForceRun(job.id);
                        }}
                        className={`${buttonClass} rounded border border-amber-500/30 bg-amber-500/20 text-amber-400 transition-colors hover:bg-amber-500/30 hover:text-amber-300`}
                        title="[DEBUG] Force-run this job immediately, bypassing orchestrator"
                    >
                        Force
                    </button>
                )}

                {!mdJob && job.status === 'awaiting_input' && (
                    <>
                        <button
                            onClick={(event) => {
                                event.stopPropagation();
                                onViewLogs(job.id);
                            }}
                            className={`${buttonClass} rounded bg-slate-500/20 text-slate-400 transition-colors hover:bg-slate-500/30 hover:text-slate-300`}
                        >
                            Logs
                        </button>
                        {onResumeWithSettings && !usesDirectAwaitingContinue(job) ? (
                            <button
                                onClick={(event) => {
                                    event.stopPropagation();
                                    onResumeWithSettings(job);
                                }}
                                className={`${buttonClass} rounded bg-amber-500/20 text-amber-400 transition-colors hover:bg-amber-500/30 hover:text-amber-300`}
                            >
                                {getAwaitingContinueLabel(job)}
                            </button>
                        ) : (
                            <button
                                onClick={(event) => {
                                    event.stopPropagation();
                                    onResume(job);
                                }}
                                className={`${buttonClass} rounded bg-amber-500/20 text-amber-400 transition-colors hover:bg-amber-500/30 hover:text-amber-300`}
                            >
                                {getAwaitingContinueLabel(job)}
                            </button>
                        )}
                    </>
                )}

                {!mdJob && (job.status === 'failed' || job.status === 'cancelled') && (
                    <>
                        <button
                            onClick={(event) => {
                                event.stopPropagation();
                                onViewLogs(job.id);
                            }}
                            className={`${buttonClass} rounded bg-slate-500/20 text-slate-400 transition-colors hover:bg-slate-500/30 hover:text-slate-300`}
                        >
                            Logs
                        </button>
                        <button
                            onClick={(event) => {
                                event.stopPropagation();
                                onResume(job);
                            }}
                            className={`${buttonClass} rounded bg-emerald-500/20 text-emerald-400 transition-colors hover:bg-emerald-500/30 hover:text-emerald-300`}
                        >
                            Resume
                        </button>
                        {onResumeWithSettings && (
                            <button
                                onClick={(event) => {
                                    event.stopPropagation();
                                    onResumeWithSettings(job);
                                }}
                                className={`${buttonClass} rounded bg-cyan-500/20 text-cyan-400 transition-colors hover:bg-cyan-500/30 hover:text-cyan-300`}
                            >
                                Re-orchestrate
                            </button>
                        )}
                        <button
                            onClick={(event) => {
                                event.stopPropagation();
                                onResubmit(job.id, job.name);
                            }}
                            className={`${buttonClass} rounded bg-yellow-500/20 text-yellow-400 transition-colors hover:bg-yellow-500/30 hover:text-yellow-300`}
                        >
                            Retry
                        </button>
                    </>
                )}

                {!mdJob && onDelete && (
                    <button
                        onClick={(event) => {
                            event.stopPropagation();
                            if (
                                window.confirm(
                                    `⚠️ PERMANENTLY DELETE "${job.name}"?\n\nThis will delete:\n- Job from database\n- All child jobs\n- All designs\n- Output directories\n\nThis is IRREVERSIBLE!`,
                                )
                            ) {
                                onDelete(job.id, job.name);
                            }
                        }}
                        className={`${buttonClass} rounded border border-red-700/50 bg-red-900/40 text-red-300 transition-colors hover:bg-red-700/50 hover:text-red-200`}
                        title="DEBUG: Permanently delete job and all data"
                    >
                        Delete
                    </button>
                )}
            </div>
        );
    };

    const renderDesktopRows = () => {
        if (!displayItems.length) {
            return (
                <tr>
                    <td colSpan={6} className="py-8 text-center text-slate-500">
                        No jobs found
                    </td>
                </tr>
            );
        }

        const rows: React.ReactNode[] = [];

        displayItems.forEach((item) => {
            if (item.type === 'batch') {
                const batchJobs = item.jobs;
                const batchId = item.batchId;
                const batchName = batchJobs[0]?.batch_name || 'Batch';
                const totalDesigns = batchJobs.reduce((sum, job) => sum + getDisplayDesignCount(job), 0);
                const anyRunning = batchJobs.some((job) => job.status === 'running');
                const anyAwaiting = batchJobs.some((job) => job.status === 'awaiting_input');
                const anyFailed = batchJobs.some((job) => job.status === 'failed');
                const anyCancelled = batchJobs.some((job) => job.status === 'cancelled');
                const batchStatus = anyFailed
                    ? 'failed'
                    : anyAwaiting
                        ? 'awaiting_input'
                        : anyRunning
                            ? 'running'
                            : anyCancelled
                                ? 'cancelled'
                                : 'completed';
                const isExpanded = expandedBatches.has(batchId);
                const ngsBatch = batchJobs.length > 0 && batchJobs.every(isNgsJob);

                rows.push(
                    <tr
                        key={`batch-${batchId}`}
                        className="cursor-pointer border-b border-accent/30 bg-accent/10 transition-colors hover:bg-accent/20"
                        onClick={() => toggleBatch(batchId)}
                    >
                        <td colSpan={6} className="px-4 py-3">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                                <div className="flex items-center gap-2">
                                    <span className="w-4 text-accent">{isExpanded ? '▼' : '▶'}</span>
                                    <span className="font-medium text-white">{batchName}</span>
                                    <span className="text-sm text-accent">({batchJobs.length} sims)</span>
                                </div>
                                <div className="flex flex-wrap items-center gap-4 text-sm">
                                    <span className="text-slate-400">{totalDesigns} designs</span>
                                    <span className="text-slate-400">{formatCreatedAt(item.firstDate)}</span>
                                    <StatusBadge status={batchStatus} />
                                    {batchStatus === 'completed' && !ngsBatch && (
                                        <Link
                                            to={`/results?batch_id=${batchId}`}
                                            onClick={(event) => event.stopPropagation()}
                                            className="rounded bg-emerald-500/20 px-2 py-1 text-xs text-emerald-400 transition-colors hover:bg-emerald-500/30"
                                        >
                                            Results
                                        </Link>
                                    )}
                                </div>
                            </div>
                        </td>
                    </tr>,
                );

                if (!isExpanded) return;

                batchJobs.forEach((job) => {
                    rows.push(
                        <React.Fragment key={job.id}>
                            <tr
                                onClick={() => setExpandedJobId(expandedJobId === job.id ? null : job.id)}
                                className={`cursor-pointer border-b border-slate-700/50 transition-colors hover:bg-slate-700/30 ${
                                    expandedJobId === job.id ? 'bg-slate-700/40' : ''
                                }`}
                            >
                                <td className="py-3 pl-10 pr-4 font-medium text-white">
                                    <span className="mr-2">{expandedJobId === job.id ? '▼' : '▶'}</span>
                                    {job.name.replace(`${batchName}_`, '')}
                                </td>
                                <td className="px-4 py-3">
                                    <span className="rounded bg-blue-500/20 px-2 py-1 text-xs text-blue-400">
                                        {getModeDisplayName(job.mode)}
                                    </span>
                                </td>
                                <td className="px-4 py-3">
                                    <StatusBadge status={job.status} errorMessage={job.error_message} />
                                </td>
                                <td className="px-4 py-3">{renderDesignCountCell(job)}</td>
                                <td className="px-4 py-3 text-sm text-slate-400">
                                    {formatCreatedAt(job.created_at)}
                                </td>
                                <td className="px-4 py-3">
                                    {renderJobActions(job)}
                                </td>
                            </tr>
                            {expandedJobId === job.id && (
                                isNgsJob(job) ? (
                                    <tr>
                                        <td colSpan={6} className="bg-slate-900/40 px-6 py-4 text-sm text-slate-300">
                                            <Link
                                                to={ngsResultHref(job.id, location.search)}
                                                className="font-medium text-emerald-300 hover:text-emerald-200"
                                            >
                                                Open NGS Run Inspector
                                            </Link>
                                        </td>
                                    </tr>
                                ) : (
                                    <JobDetailsPanel
                                        job={job}
                                        onClose={() => setExpandedJobId(null)}
                                    />
                                )
                            )}
                        </React.Fragment>,
                    );
                });

                return;
            }

            const job = item.job;
            const awaitingPrompt = getAwaitingPromptSummary(job);
            rows.push(
                <React.Fragment key={job.id}>
                    <tr
                        data-bms-md-job-row={isMolecularDynamicsJob(job) ? job.id : undefined}
                        onClick={() => setExpandedJobId(expandedJobId === job.id ? null : job.id)}
                        className={`cursor-pointer border-b border-slate-700/50 transition-colors hover:bg-slate-700/30 ${
                            expandedJobId === job.id ? 'bg-slate-700/40' : ''
                        }`}
                    >
                        <td className="px-4 py-3 font-medium text-white">
                            <div className="flex flex-col">
                                <div className="flex items-center">
                                    <span className="mr-2">{expandedJobId === job.id ? '▼' : '▶'}</span>
                                    {job.name}
                                    {isMolecularDynamicsJob(job) && (
                                        <span className="ml-2 rounded bg-cyan-500/20 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-cyan-200">MD</span>
                                    )}
                                </div>
                                <StageProgress job={job} />
                                {awaitingPrompt && (
                                    <div className="mt-1 max-w-3xl text-[11px] leading-snug text-amber-300">
                                        {awaitingPrompt}
                                    </div>
                                )}
                            </div>
                        </td>
                        <td className="px-4 py-3">
                            <span className="rounded bg-blue-500/20 px-2 py-1 text-xs text-blue-400">
                                {getModeDisplayName(job.mode)}
                            </span>
                        </td>
                        <td className="px-4 py-3">
                            <StatusBadge status={job.status} errorMessage={job.error_message} />
                        </td>
                        <td className="px-4 py-3">{renderDesignCountCell(job)}</td>
                        <td className="px-4 py-3 text-sm text-slate-400">
                            {formatCreatedAt(job.created_at)}
                        </td>
                        <td className="px-4 py-3">
                            {renderJobActions(job)}
                        </td>
                    </tr>
                    {expandedJobId === job.id && (
                        isNgsJob(job) ? (
                            <tr>
                                <td colSpan={6} className="bg-slate-900/40 px-6 py-4 text-sm text-slate-300">
                                    <Link
                                        to={ngsResultHref(job.id, location.search)}
                                        className="font-medium text-emerald-300 hover:text-emerald-200"
                                    >
                                        Open NGS Run Inspector
                                    </Link>
                                </td>
                            </tr>
                        ) : (
                            <JobDetailsPanel
                                job={job}
                                onClose={() => setExpandedJobId(null)}
                            />
                        )
                    )}
                </React.Fragment>,
            );
        });

        return rows;
    };

    const renderMobileJobCard = (job: Job, displayName = job.name, nested = false) => {
        const awaitingPrompt = getAwaitingPromptSummary(job);
        const isExpanded = expandedJobId === job.id;

        return (
            <article
                key={job.id}
                data-bms-md-job-row={isMolecularDynamicsJob(job) ? job.id : undefined}
                className={`rounded-xl border ${
                    nested ? 'border-slate-700/70 bg-slate-950/35' : 'border-slate-700 bg-slate-900/50'
                } p-3`}
            >
                <button
                    type="button"
                    onClick={() => setExpandedJobId(isExpanded ? null : job.id)}
                    className="w-full text-left"
                >
                    <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                                <span className="text-slate-400">{isExpanded ? '▼' : '▶'}</span>
                                <h3 className="truncate text-sm font-semibold text-white">{displayName}</h3>
                                {isMolecularDynamicsJob(job) && (
                                    <span className="rounded bg-cyan-500/20 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-cyan-200">MD</span>
                                )}
                            </div>
                            <div className="mt-2 flex flex-wrap items-center gap-2">
                                <span className="rounded bg-blue-500/20 px-2 py-1 text-[11px] text-blue-400">
                                    {getModeDisplayName(job.mode)}
                                </span>
                                <StatusBadge status={job.status} errorMessage={job.error_message} />
                            </div>
                        </div>
                        <div className="shrink-0 text-right text-[11px] text-slate-400">
                            <div>{getDisplayDesignCount(job)} designs</div>
                            <div className="mt-1">{new Date(job.created_at).toLocaleDateString()}</div>
                        </div>
                    </div>
                </button>

                <div className="mt-2">
                    <StageProgress job={job} />
                </div>

                {awaitingPrompt && (
                    <div className="mt-2 rounded-lg border border-amber-500/20 bg-amber-500/10 px-2.5 py-2 text-[11px] leading-snug text-amber-200">
                        {awaitingPrompt}
                    </div>
                )}

                <div className="mt-3">
                    {renderJobActions(job, true)}
                </div>

                {isExpanded && (
                    <div className="mt-3 space-y-2 rounded-lg border border-slate-700/80 bg-slate-950/50 p-3 text-xs text-slate-300">
                        <div className="flex flex-wrap items-center gap-2">
                            <span className="text-slate-500">Mode</span>
                            <span>{job.mode}</span>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                            <span className="text-slate-500">Model</span>
                            <span>{job.model_id || '—'}</span>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                            <span className="text-slate-500">Created</span>
                            <span>{formatCreatedAt(job.created_at)}</span>
                        </div>
                        <div className="flex flex-wrap items-start gap-2">
                            <span className="text-slate-500">Job ID</span>
                            <span className="break-all font-mono text-[11px] text-slate-400">{job.id}</span>
                        </div>
                        <div className="pt-1">
                            <Link
                                to={isNgsJob(job) ? ngsResultHref(job.id, location.search) : `/designs/${job.id}`}
                                className="inline-flex rounded bg-emerald-500/20 px-2.5 py-1.5 text-[11px] text-emerald-400 transition-colors hover:bg-emerald-500/30"
                            >
                                {isNgsJob(job) ? 'Open NGS Run Inspector' : 'Open in Results Viewer'}
                            </Link>
                        </div>
                    </div>
                )}
            </article>
        );
    };

    const renderMobileContent = () => {
        if (!displayItems.length) {
            return (
                <div className="rounded-xl border border-slate-700 bg-slate-900/40 p-6 text-center text-slate-500">
                    No jobs found
                </div>
            );
        }

        return (
            <div className="rounded-xl border border-slate-700 bg-slate-800/30 backdrop-blur-sm">
                <div
                    className="space-y-3 overflow-y-auto p-3 [scrollbar-width:thin]"
                    style={{ height: MOBILE_TABLE_PANEL_HEIGHT }}
                >
                    {displayItems.map((item) => {
                        if (item.type === 'standalone') {
                            return renderMobileJobCard(item.job);
                        }

                        const batchJobs = item.jobs;
                        const batchName = batchJobs[0]?.batch_name || 'Batch';
                        const totalDesigns = batchJobs.reduce((sum, job) => sum + getDisplayDesignCount(job), 0);
                        const anyRunning = batchJobs.some((job) => job.status === 'running');
                        const anyAwaiting = batchJobs.some((job) => job.status === 'awaiting_input');
                        const anyFailed = batchJobs.some((job) => job.status === 'failed');
                        const anyCancelled = batchJobs.some((job) => job.status === 'cancelled');
                        const batchStatus = anyFailed
                            ? 'failed'
                            : anyAwaiting
                                ? 'awaiting_input'
                                : anyRunning
                                    ? 'running'
                                    : anyCancelled
                                        ? 'cancelled'
                                        : 'completed';
                        const isExpanded = expandedBatches.has(item.batchId);
                        const ngsBatch = batchJobs.length > 0 && batchJobs.every(isNgsJob);

                        return (
                            <section key={`batch-mobile-${item.batchId}`} className="rounded-xl border border-accent/30 bg-accent/10 p-3">
                                <button
                                    type="button"
                                    onClick={() => toggleBatch(item.batchId)}
                                    className="w-full text-left"
                                >
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0 flex-1">
                                            <div className="flex items-center gap-2">
                                                <span className="text-accent">{isExpanded ? '▼' : '▶'}</span>
                                                <h3 className="truncate text-sm font-semibold text-white">{batchName}</h3>
                                            </div>
                                            <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-300">
                                                <span>{batchJobs.length} sims</span>
                                                <span>{totalDesigns} designs</span>
                                                <span>{new Date(item.firstDate).toLocaleDateString()}</span>
                                            </div>
                                        </div>
                                        <div className="shrink-0">
                                            <StatusBadge status={batchStatus} />
                                        </div>
                                    </div>
                                </button>

                                {batchStatus === 'completed' && !ngsBatch && (
                                    <div className="mt-3">
                                        <Link
                                            to={`/results?batch_id=${item.batchId}`}
                                            className="inline-flex rounded bg-emerald-500/20 px-2.5 py-1.5 text-[11px] text-emerald-400 transition-colors hover:bg-emerald-500/30"
                                        >
                                            Open Batch Results
                                        </Link>
                                    </div>
                                )}

                                {isExpanded && (
                                    <div className="mt-3 space-y-2 border-l border-accent/20 pl-3">
                                        {batchJobs.map((job) =>
                                            renderMobileJobCard(job, job.name.replace(`${batchName}_`, ''), true),
                                        )}
                                    </div>
                                )}
                            </section>
                        );
                    })}
                </div>
            </div>
        );
    };

    const SortHeader = ({ column, children }: { column: SortColumn; children: React.ReactNode }) => (
        <th
            onClick={() => handleSort(column)}
            className="sticky top-0 z-10 cursor-pointer select-none bg-slate-900/95 px-4 py-3 text-left text-sm font-medium text-slate-400 backdrop-blur hover:text-slate-200"
        >
            <span className="flex items-center gap-1">
                {children}
                {sortColumn === column && (
                    <span className="text-accent">{sortDirection === 'asc' ? '▲' : '▼'}</span>
                )}
            </span>
        </th>
    );

    if (loading) {
        return (
            <div className="flex items-center justify-center py-12">
                <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-accent" />
            </div>
        );
    }

    if (mobileView) {
        return renderMobileContent();
    }

    return (
        <div className="rounded-xl border border-slate-700 bg-slate-800/30 backdrop-blur-sm overflow-hidden">
            <div
                className="overflow-auto overscroll-contain [scrollbar-width:thin]"
                style={{ height: DESKTOP_TABLE_PANEL_HEIGHT }}
            >
                <table className="w-full min-w-[980px]">
                    <thead>
                        <tr className="border-b border-slate-700">
                            <SortHeader column="name">Name</SortHeader>
                            <SortHeader column="mode">Mode</SortHeader>
                            <SortHeader column="status">Status</SortHeader>
                            <SortHeader column="designs">Designs</SortHeader>
                            <SortHeader column="created">Created</SortHeader>
                            <th className="sticky top-0 z-10 bg-slate-900/95 px-4 py-3 text-left text-sm font-medium text-slate-400 backdrop-blur">
                                Actions
                            </th>
                        </tr>
                    </thead>
                    <tbody>{renderDesktopRows()}</tbody>
                </table>
            </div>
        </div>
    );
}

function StatusBadge({ status, errorMessage }: { status: string; errorMessage?: string | null }) {
    const completedWithError = status === 'completed' && !!errorMessage;
    const styles: Record<string, string> = {
        queued: 'bg-slate-500/20 text-slate-400',
        running: 'bg-blue-500/20 text-blue-400 animate-pulse',
        awaiting_input: 'bg-amber-500/20 text-amber-400',
        completed: 'bg-green-500/20 text-green-400',
        completed_error: 'bg-amber-500/20 text-amber-400',
        failed: 'bg-red-500/20 text-red-400',
        cancelled: 'bg-orange-500/20 text-orange-400',
    };

    const showTooltip = (status === 'failed' || status === 'cancelled' || completedWithError) && errorMessage;
    const truncatedError = errorMessage
        ? errorMessage.split('\n')[0].substring(0, 100) + (errorMessage.length > 100 ? '...' : '')
        : null;
    const badgeStyle = completedWithError ? styles.completed_error : (styles[status] ?? styles.queued);
    const badgeLabel = completedWithError ? 'completed*' : status.replace('_', ' ');

    return (
        <div className="group relative inline-block">
            <span className={`cursor-default rounded px-2 py-1 text-xs font-medium ${badgeStyle}`}>
                {badgeLabel}
            </span>
            {showTooltip && (
                <div className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 max-w-xs -translate-x-1/2 rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-xs text-slate-200 opacity-0 shadow-xl transition-opacity duration-200 group-hover:opacity-100">
                    <div className="mb-1 font-medium text-red-400">{completedWithError ? 'Warning:' : 'Error:'}</div>
                    <div className="break-words whitespace-normal text-slate-300">{truncatedError}</div>
                    <div className="absolute left-1/2 top-full -translate-x-1/2 border-4 border-transparent border-t-slate-600" />
                </div>
            )}
        </div>
    );
}

function StageProgress({ job }: { job: Job }) {
    const getStages = (mode: string) => {
        if (mode.includes('antibody')) return ['rfantibody', 'fampnn', 'structure_validation'];
        if (mode.includes('binder')) return ['rfdiffusion', 'proteinmpnn', 'boltz2'];
        if (mode.includes('monomer')) return ['rfdiffusion', 'proteinmpnn', 'af2'];
        if (mode.includes('oligo')) return ['rfdpoly', 'nampnn', 'pyrosetta_rebuild'];
        return [];
    };

    const stages = (() => {
        const baseStages = job.all_stages && job.all_stages.length > 0
            ? [...job.all_stages]
            : getStages(job.mode);
        const ppiflowStageMode = String(job.params?.ppiflow_stage_mode || '').toLowerCase();
        const hasBackbonePpiFlow =
            job.params?.run_ppiflow_backbone_refine ||
            ppiflowStageMode === 'post_rfantibody' ||
            ppiflowStageMode === 'backbone_refine' ||
            ppiflowStageMode === 'both';
        const hasMaturationPpiFlow =
            job.params?.run_ppiflow_maturation ||
            job.params?.run_maturation ||
            ppiflowStageMode === 'post_fampnn' ||
            ppiflowStageMode === 'maturation' ||
            ppiflowStageMode === 'both';
        if (hasBackbonePpiFlow && !baseStages.includes('ppiflow_backbone')) {
            baseStages.splice(Math.min(1, baseStages.length), 0, 'ppiflow_backbone');
        }
        if (hasMaturationPpiFlow && !baseStages.includes('ppiflow_maturation')) {
            baseStages.splice(Math.min(3, baseStages.length), 0, 'ppiflow_maturation');
        }
        if ((job.params?.run_post_validation_maturation || job.params?.run_post_boltz_maturation) && !baseStages.includes('ppiflow_post_validation')) {
            baseStages.push('ppiflow_post_validation');
        }
        if (job.awaiting_stage && !baseStages.includes(job.awaiting_stage)) {
            baseStages.push(job.awaiting_stage);
        }
        return baseStages;
    })();

    if (stages.length === 0) return null;

    const jobIsCompleted = job.status === 'completed';
    const jobIsFailed = job.status === 'failed';
    const jobIsCancelled = job.status === 'cancelled';
    const jobIsAwaiting = job.status === 'awaiting_input';
    const completed = job.completed_stages || [];
    const rawCurrent = job.awaiting_stage || job.current_stage;
    const current =
        (rawCurrent === 'boltz2' || rawCurrent === 'protenix') && stages.includes('structure_validation')
            ? 'structure_validation'
            : (rawCurrent === 'maturation_post_boltz' || rawCurrent === 'maturation_post_validation') && stages.includes('ppiflow_post_validation')
                ? 'ppiflow_post_validation'
                : rawCurrent === 'backbone_refine' && stages.includes('ppiflow_backbone')
                    ? 'ppiflow_backbone'
                    : rawCurrent === 'maturation' && stages.includes('ppiflow_maturation')
                        ? 'ppiflow_maturation'
                        : rawCurrent;

    return (
        <div className="mt-1 flex items-center space-x-1 overflow-x-auto pb-1">
            {stages.map((stage, idx) => {
                const wasCompleted = completed.includes(stage);
                const isCurrent = stage === current;
                let stageClass = '';
                let connectorClass = 'bg-slate-700';

                if (jobIsCompleted) {
                    stageClass = 'bg-emerald-500/20 border-emerald-500/30 text-emerald-400';
                    connectorClass = 'bg-emerald-500/30';
                } else if (wasCompleted) {
                    stageClass = 'bg-emerald-500/20 border-emerald-500/30 text-emerald-400';
                    connectorClass = 'bg-emerald-500/30';
                } else if (isCurrent && jobIsFailed) {
                    stageClass = 'bg-red-500/20 border-red-500/30 text-red-400';
                } else if (isCurrent && jobIsAwaiting) {
                    stageClass = 'bg-amber-500/20 border-amber-500/30 text-amber-400';
                } else if (isCurrent && jobIsCancelled) {
                    stageClass = 'bg-orange-500/20 border-orange-500/30 text-orange-400';
                } else if (isCurrent) {
                    stageClass = 'bg-blue-500/20 border-blue-500/30 text-blue-400 animate-pulse';
                } else {
                    stageClass = 'bg-slate-800/50 border-slate-700 text-slate-600';
                }

                return (
                    <div key={stage} className="flex shrink-0 items-center">
                        <div
                            className={`rounded-[3px] border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${stageClass}`}
                        >
                            {getStageDisplayName(stage)}
                        </div>
                        {idx < stages.length - 1 && (
                            <div className={`mx-0.5 h-px w-1 ${connectorClass}`} />
                        )}
                    </div>
                );
            })}
        </div>
    );
}
