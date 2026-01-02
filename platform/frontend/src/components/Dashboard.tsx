import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchJobs, fetchSystemStatus, cancelJob, resubmitJob, fetchPowerControl, setPowerControlManual, fetchSchedulerConfig, toggleGpuDisabled, fetchJobLogs, resumeJob } from '../lib/api';
import type { GPUStatus, CPUStatus, RAMStatus, JobLogs, Job } from '../lib/api';
import { JobDetailsPanel } from './JobDetailsPanel';
import { QuickViewer } from './QuickViewer';
import { JobQueuePanel } from './JobQueuePanel';

export function Dashboard() {
    const queryClient = useQueryClient();
    const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
    const [quickViewJobId, setQuickViewJobId] = useState<string | null>(null);
    const [logsModalJobId, setLogsModalJobId] = useState<string | null>(null);
    const [logsData, setLogsData] = useState<JobLogs | null>(null);
    const [logsLoading, setLogsLoading] = useState(false);

    const { data: jobsData, isLoading: jobsLoading } = useQuery({
        queryKey: ['jobs'],
        queryFn: fetchJobs,
        refetchInterval: 5000,
    });

    const cancelMutation = useMutation({
        mutationFn: (jobId: string) => cancelJob(jobId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
        },
    });

    const handleCancel = (jobId: string, jobName: string) => {
        if (confirm(`Cancel job "${jobName}"?`)) {
            cancelMutation.mutate(jobId);
        }
    };

    const resubmitMutation = useMutation({
        mutationFn: (jobId: string) => resubmitJob(jobId),
        onSuccess: (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            alert(`Job resubmitted! New job: ${response.data.new_job_name}`);
        },
        onError: (error: any) => {
            alert(`Resubmit failed: ${error.response?.data?.detail || error.message}`);
        }
    });

    const handleResubmit = (jobId: string, jobName: string) => {
        if (confirm(`Resubmit job "${jobName}"?`)) {
            resubmitMutation.mutate(jobId);
        }
    };

    const handleViewLogs = async (jobId: string) => {
        setLogsLoading(true);
        setLogsModalJobId(jobId);
        try {
            const response = await fetchJobLogs(jobId);
            setLogsData(response.data);
        } catch (error) {
            console.error('Failed to fetch logs:', error);
            setLogsData(null);
        } finally {
            setLogsLoading(false);
        }
    };

    const resumeMutation = useMutation({
        mutationFn: ({ jobId, fromStage }: { jobId: string; fromStage?: string }) =>
            resumeJob(jobId, fromStage),
        onSuccess: (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            alert(`Job resumed! New job: ${response.data.new_job_name}\nResuming from: ${response.data.resume_from_stage}`);
        },
        onError: (error: any) => {
            alert(`Resume failed: ${error.response?.data?.detail || error.message}`);
        }
    });

    const handleResume = (job: Job) => {
        const completed = job.completed_stages || [];
        if (completed.length === 0) {
            alert('No completed stages to resume from. Use Retry instead.');
            return;
        }
        if (confirm(`Resume job "${job.name}" from after ${completed[completed.length - 1]}?`)) {
            resumeMutation.mutate({ jobId: job.id });
        }
    };

    const { data: systemData } = useQuery({
        queryKey: ['system'],
        queryFn: fetchSystemStatus,
        refetchInterval: 2000,
    });

    const { data: powerControlData } = useQuery({
        queryKey: ['powerControl'],
        queryFn: fetchPowerControl,
        refetchInterval: 5000,
    });


    const manualMutation = useMutation({
        mutationFn: ({ gpuIndex, limitWatts }: { gpuIndex: number; limitWatts: number }) =>
            setPowerControlManual(gpuIndex, limitWatts),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['powerControl'] });
            queryClient.invalidateQueries({ queryKey: ['system'] });
        },
    });

    // Scheduler config for GPU disable status
    const { data: schedulerConfigData } = useQuery({
        queryKey: ['schedulerConfig'],
        queryFn: fetchSchedulerConfig,
        refetchInterval: 5000,
    });

    const toggleDisableMutation = useMutation({
        mutationFn: (gpuId: number) => toggleGpuDisabled(gpuId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['schedulerConfig'] });
        },
    });

    const gpuOverrides = schedulerConfigData?.data?.overrides ?? {};

    const currentLimits = powerControlData?.data.limits ?? {};

    const gpus = systemData?.data.gpus ?? [];
    const cpu = systemData?.data.cpu;
    const ram = systemData?.data.ram;
    const cpuHistory = systemData?.data.cpu_history ?? [];
    const ramHistory = systemData?.data.ram_history ?? [];

    return (
        <div className="min-h-screen bg-slate-950 p-6">
            {/* Header */}
            <header className="mb-8 flex justify-between items-center">
                <div>
                    <h1 className="text-4xl font-bold bg-gradient-to-r from-blue-400 via-purple-500 to-pink-500 bg-clip-text text-transparent">
                        BioModStack
                    </h1>
                    <p className="text-slate-400 mt-2">Protein Modification & Design Platform</p>
                </div>
                <div className="flex gap-3">
                    <Link
                        to="/designs"
                        className="bg-emerald-600 hover:bg-emerald-500 text-white px-5 py-3 rounded-lg font-semibold shadow-lg shadow-emerald-500/20 transition-all hover:scale-105 active:scale-95 flex items-center gap-2"
                    >
                        🧬 Browse Designs
                    </Link>
                    <Link
                        to="/submit"
                        className="bg-blue-600 hover:bg-blue-500 text-white px-6 py-3 rounded-lg font-semibold shadow-lg shadow-blue-500/20 transition-all hover:scale-105 active:scale-95 flex items-center gap-2"
                    >
                        <span>+</span> New Experiment
                    </Link>
                </div>
            </header>

            {/* System Overview - CPU & RAM */}
            {(cpu || ram) && (
                <section className="mb-8">
                    <h2 className="text-xl font-semibold text-slate-200 mb-4">System Overview</h2>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {/* CPU Card */}
                        {cpu && <CPUCard cpu={cpu} history={cpuHistory} />}
                        {/* RAM Card */}
                        {ram && <RAMCard ram={ram} history={ramHistory} />}
                    </div>
                </section>
            )}

            {/* GPU Orchestrator Job Queue */}
            <JobQueuePanel />

            {/* GPU Status Cards */}
            <section className="mb-8">
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-xl font-semibold text-slate-200">GPU Status</h2>
                    <span className="text-sm text-slate-400">
                        Total: {gpus.reduce((sum, gpu) => sum + gpu.power_draw_w, 0).toFixed(1)}W / {gpus.reduce((sum, gpu) => sum + (currentLimits[gpu.index] ?? gpu.power_limit_w), 0)}W
                    </span>
                </div>

                {/* GPU Scheduler Settings Panel */}
                <GPUSchedulerSettings gpus={gpus} />
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {gpus.map((gpu) => (
                        <GPUCard
                            key={gpu.index}
                            gpu={gpu}
                            currentLimit={currentLimits[gpu.index] ?? gpu.power_limit_w}
                            onSetLimit={(watts) => manualMutation.mutate({ gpuIndex: gpu.index, limitWatts: watts })}
                            isPending={manualMutation.isPending}
                            disabled={gpuOverrides[String(gpu.index)]?.disabled ?? false}
                            onToggleDisable={() => toggleDisableMutation.mutate(gpu.index)}
                        />
                    ))}
                    {gpus.length === 0 && (
                        <div className="col-span-full text-slate-500 text-center py-8">
                            No GPU data available
                        </div>
                    )}
                </div>
            </section>

            {/* Logs Modal - Full screen popup */}
            {logsModalJobId && (
                <LogsModal
                    logs={logsData}
                    loading={logsLoading}
                    onClose={() => {
                        setLogsModalJobId(null);
                        setLogsData(null);
                    }}
                />
            )}

            {/* Quick Viewer - Compact structure preview */}
            <section className="mb-8">
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <QuickViewer
                        selectedJobId={quickViewJobId}
                        onJobChange={setQuickViewJobId}
                    />
                </div>
            </section>

            {/* Jobs Section */}
            <section>
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-xl font-semibold text-slate-200">Recent Jobs</h2>
                    <span className="text-sm text-slate-400">
                        {jobsData?.data.total ?? 0} total jobs
                    </span>
                </div>

                {jobsLoading ? (
                    <div className="flex items-center justify-center py-12">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-500" />
                    </div>
                ) : (
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
                                    const jobs = jobsData?.data.jobs || [];
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
                                                                                setQuickViewJobId(job.id);
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
                                                                            setQuickViewJobId(job.id);
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
                                                                            handleCancel(job.id, job.name);
                                                                        }}
                                                                        disabled={cancelMutation.isPending}
                                                                        className="px-2 py-1 text-xs bg-red-500/20 text-red-400 hover:bg-red-500/30 hover:text-red-300 rounded transition-colors disabled:opacity-50"
                                                                    >
                                                                        {cancelMutation.isPending ? '...' : 'Cancel'}
                                                                    </button>
                                                                )}
                                                                {(job.status === 'failed' || job.status === 'cancelled') && (
                                                                    <>
                                                                        <button
                                                                            onClick={(e) => {
                                                                                e.stopPropagation();
                                                                                handleViewLogs(job.id);
                                                                            }}
                                                                            className="px-2 py-1 text-xs bg-slate-500/20 text-slate-400 hover:bg-slate-500/30 hover:text-slate-300 rounded transition-colors"
                                                                        >
                                                                            📋 Logs
                                                                        </button>
                                                                        {job.completed_stages && job.completed_stages.length > 0 && (
                                                                            <button
                                                                                onClick={(e) => {
                                                                                    e.stopPropagation();
                                                                                    handleResume(job);
                                                                                }}
                                                                                className="px-2 py-1 text-xs bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 hover:text-emerald-300 rounded transition-colors"
                                                                            >
                                                                                ⏯ Resume
                                                                            </button>
                                                                        )}
                                                                        <button
                                                                            onClick={(e) => {
                                                                                e.stopPropagation();
                                                                                handleResubmit(job.id, job.name);
                                                                            }}
                                                                            disabled={resubmitMutation.isPending}
                                                                            className="px-2 py-1 text-xs bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30 hover:text-yellow-300 rounded transition-colors disabled:opacity-50"
                                                                        >
                                                                            {resubmitMutation.isPending ? '...' : '🔄 Retry'}
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
                )
                }
            </section >
        </div >
    );
}

function GPUCard({ gpu, currentLimit, onSetLimit, isPending, disabled, onToggleDisable }: {
    gpu: GPUStatus;
    currentLimit: number;
    onSetLimit: (watts: number) => void;
    isPending: boolean;
    disabled: boolean;
    onToggleDisable: () => void;
}) {
    const [inputValue, setInputValue] = useState(String(Math.round(currentLimit)));
    const memoryPercent = (gpu.memory_used_mb / gpu.memory_total_mb) * 100;
    const powerPercent = currentLimit > 0 ? (gpu.power_draw_w / currentLimit) * 100 : 0;

    const handleApply = () => {
        const watts = parseInt(inputValue, 10);
        if (!isNaN(watts) && watts >= gpu.min_power_watts && watts <= gpu.max_power_watts) {
            onSetLimit(watts);
        }
    };

    const handleIncrement = () => {
        const current = parseInt(inputValue, 10) || currentLimit;
        const newVal = Math.min(current + 5, gpu.max_power_watts);
        setInputValue(String(newVal));
    };

    const handleDecrement = () => {
        const current = parseInt(inputValue, 10) || currentLimit;
        const newVal = Math.max(current - 5, gpu.min_power_watts);
        setInputValue(String(newVal));
    };

    const isOutOfRange = (() => {
        const v = parseInt(inputValue, 10);
        return !isNaN(v) && (v < gpu.min_power_watts || v > gpu.max_power_watts);
    })();

    const isDirty = parseInt(inputValue, 10) !== currentLimit;

    return (
        <div className={`bg-slate-800/50 backdrop-blur-sm border rounded-lg p-3 transition-all duration-300 ${disabled ? 'border-red-500/50 opacity-60' : 'border-slate-700 hover:border-purple-500/50'
            }`}>
            {/* Header - compact */}
            <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                    <span className="text-xs text-slate-500">GPU {gpu.index}</span>
                    <span className="text-sm font-medium text-white truncate">{gpu.name}</span>
                    {disabled && (
                        <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-red-500/20 text-red-400">
                            Disabled
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={onToggleDisable}
                        className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${disabled
                            ? 'bg-green-500/20 text-green-400 hover:bg-green-500/30'
                            : 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                            }`}
                        title={disabled ? 'Enable GPU for inference' : 'Disable GPU from inference'}
                    >
                        {disabled ? 'Enable' : 'Disable'}
                    </button>
                    <span
                        className={`px-1.5 py-0.5 rounded text-xs font-medium ${gpu.utilization > 80
                            ? 'bg-green-500/20 text-green-400'
                            : gpu.utilization > 20
                                ? 'bg-yellow-500/20 text-yellow-400'
                                : 'bg-slate-500/20 text-slate-400'
                            }`}
                    >
                        {gpu.utilization}%
                    </span>
                </div>
            </div>

            {/* Stats Row - inline compact */}
            <div className="flex items-center gap-3 text-xs mb-2">
                <span className={`${gpu.temperature > 80 ? 'text-red-400' : gpu.temperature > 60 ? 'text-yellow-400' : 'text-green-400'}`}>
                    {gpu.temperature}°C
                </span>
                <span className="text-blue-400">{gpu.fan_speed}% Fan</span>
                <span className="text-purple-400">{gpu.clock_graphics_mhz}MHz</span>
            </div>

            {/* VRAM Bar - compact */}
            <div className="mb-2">
                <div className="flex justify-between text-xs text-slate-500 mb-0.5">
                    <span>VRAM</span>
                    <span>{((gpu.memory_used_mb + gpu.reserved_memory_mb) / 1024).toFixed(1)}/{(gpu.memory_total_mb / 1024).toFixed(0)}GB</span>
                </div>
                <div className="w-full bg-slate-700 rounded-full h-1.5 relative overflow-hidden">
                    <div
                        className="absolute top-0 left-0 h-full bg-gradient-to-r from-blue-500 to-purple-500 rounded-full transition-all z-20"
                        style={{ width: `${memoryPercent}%` }}
                    />
                    {gpu.reserved_memory_mb > 0 && (
                        <div
                            className="absolute top-0 h-full bg-orange-500/30 z-10"
                            style={{ left: `${memoryPercent}%`, width: `${(gpu.reserved_memory_mb / gpu.memory_total_mb) * 100}%` }}
                        />
                    )}
                </div>
            </div>

            {/* Power Row - inline compact */}
            <div className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                    <span className="text-orange-400 font-medium">{gpu.power_draw_w}W</span>
                    <div className="w-16 bg-slate-700 rounded-full h-1">
                        <div
                            className={`h-1 rounded-full ${powerPercent > 90 ? 'bg-red-500' : powerPercent > 70 ? 'bg-yellow-500' : 'bg-orange-500'}`}
                            style={{ width: `${Math.min(powerPercent, 100)}%` }}
                        />
                    </div>
                </div>
                <div className="flex items-center gap-1">
                    <button onClick={handleDecrement} className="w-5 h-5 flex items-center justify-center bg-slate-700 hover:bg-slate-600 rounded text-slate-300 text-xs">−</button>
                    <input
                        type="text"
                        inputMode="numeric"
                        pattern="[0-9]*"
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value.replace(/[^0-9]/g, ''))}
                        className={`w-12 px-1 py-0.5 bg-slate-700 border rounded text-white text-xs text-center ${isOutOfRange ? 'border-red-500' : isDirty ? 'border-yellow-500' : 'border-slate-600'}`}
                    />
                    <button onClick={handleIncrement} className="w-5 h-5 flex items-center justify-center bg-slate-700 hover:bg-slate-600 rounded text-slate-300 text-xs">+</button>
                    {isDirty && (
                        <button
                            onClick={handleApply}
                            disabled={isPending || isOutOfRange}
                            className="px-2 py-0.5 bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 rounded text-xs disabled:opacity-50"
                        >
                            ✓
                        </button>
                    )}
                </div>
            </div>

            {/* Processes - minimal */}
            {gpu.processes.length > 0 && (
                <div className="border-t border-slate-700/50 pt-1.5 mt-2 text-xs text-slate-400">
                    {gpu.processes.slice(0, 2).map((proc) => (
                        <div key={proc.pid} className="flex justify-between truncate">
                            <span className="truncate max-w-[70%]">{proc.name}</span>
                            <span className="text-slate-500">{proc.memory_mb}MB</span>
                        </div>
                    ))}
                    {gpu.processes.length > 2 && <span className="text-slate-500">+{gpu.processes.length - 2} more</span>}
                </div>
            )}
        </div>
    );
}

function CPUCard({ cpu, history }: { cpu: CPUStatus; history: number[] }) {
    return (
        <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
                <span className="text-sm text-slate-400">CPU</span>
                <div className="flex gap-2">
                    {cpu.temperature !== null && (
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${cpu.temperature > 80 ? 'bg-red-500/20 text-red-400' :
                            cpu.temperature > 60 ? 'bg-yellow-500/20 text-yellow-400' :
                                'bg-blue-500/20 text-blue-400'
                            }`}>
                            {cpu.temperature.toFixed(0)}°C
                        </span>
                    )}
                    <span className={`px-2 py-1 rounded-full text-xs font-medium ${cpu.utilization > 80 ? 'bg-red-500/20 text-red-400' : cpu.utilization > 50 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}`}>
                        {cpu.utilization.toFixed(1)}%
                    </span>
                </div>
            </div>
            <h3 className="text-sm font-medium text-white truncate mb-3">{cpu.name}</h3>

            <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                    <span className="text-slate-400">Cores:</span>
                    <span className="text-slate-200 ml-1">{cpu.cores_physical}P / {cpu.cores_logical}T</span>
                </div>
                <div>
                    <span className="text-slate-400">Freq:</span>
                    <span className="text-slate-200 ml-1">{cpu.frequency_current_mhz.toFixed(0)} MHz</span>
                </div>
            </div>

            {/* CPU Load Sparkline */}
            {history.length > 1 && (
                <div className="mt-3">
                    <Sparkline data={history} color="green" height={24} />
                </div>
            )}

            {/* Per-core utilization mini bars */}
            <div className="mt-3 flex gap-0.5">
                {cpu.per_core_utilization.slice(0, 24).map((util, i) => (
                    <div
                        key={i}
                        className="flex-1 bg-slate-700 rounded-sm h-3 overflow-hidden"
                        title={`Core ${i}: ${util.toFixed(0)}%`}
                    >
                        <div
                            className={`h-full transition-all ${util > 80 ? 'bg-red-500' : util > 50 ? 'bg-yellow-500' : 'bg-green-500'}`}
                            style={{ height: `${util}%` }}
                        />
                    </div>
                ))}
            </div>
        </div>
    );
}

function RAMCard({ ram, history }: { ram: RAMStatus; history: number[] }) {
    return (
        <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
                <span className="text-sm text-slate-400">Memory</span>
                <div className="flex gap-2">
                    {ram.swap_percent > 0 && (
                        <span className="px-2 py-1 rounded-full text-xs font-medium bg-orange-500/20 text-orange-400">
                            Swap: {ram.swap_percent.toFixed(0)}%
                        </span>
                    )}
                    <span className={`px-2 py-1 rounded-full text-xs font-medium ${ram.utilization > 90 ? 'bg-red-500/20 text-red-400' : ram.utilization > 70 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}`}>
                        {ram.utilization.toFixed(1)}%
                    </span>
                </div>
            </div>

            <div className="grid grid-cols-3 gap-2 text-center mb-3">
                <div>
                    <div className="text-lg font-semibold text-white">{ram.used_gb}</div>
                    <div className="text-xs text-slate-400">Used GB</div>
                </div>
                <div>
                    <div className="text-lg font-semibold text-green-400">{ram.available_gb}</div>
                    <div className="text-xs text-slate-400">Free GB</div>
                </div>
                <div>
                    <div className="text-lg font-semibold text-slate-300">{ram.total_gb}</div>
                    <div className="text-xs text-slate-400">Total GB</div>
                </div>
            </div>

            {/* RAM Usage Sparkline */}
            {history.length > 1 && (
                <div className="mb-3">
                    <Sparkline data={history} color="purple" height={24} />
                </div>
            )}

            <div className="w-full bg-slate-700 rounded-full h-3">
                <div
                    className={`h-3 rounded-full transition-all ${ram.utilization > 90 ? 'bg-red-500' : ram.utilization > 70 ? 'bg-yellow-500' : 'bg-green-500'}`}
                    style={{ width: `${ram.utilization}%` }}
                />
            </div>
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

    // Show tooltip only for failed/cancelled status with error message
    const showTooltip = (status === 'failed' || status === 'cancelled') && errorMessage;

    // Truncate error message for tooltip (first line, max 100 chars)
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
                    {/* Tooltip arrow */}
                    <div className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-slate-600" />
                </div>
            )}
        </div>
    );
}

function LogsModal({
    logs,
    loading,
    onClose
}: {
    logs: JobLogs | null;
    loading: boolean;
    onClose: () => void;
}) {
    const [activeTab, setActiveTab] = useState<'parsed' | 'command' | 'stderr'>('parsed');

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
            <div className="bg-slate-900 border border-slate-700 rounded-xl shadow-2xl w-full max-w-4xl max-h-[80vh] flex flex-col">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700">
                    <div>
                        <h2 className="text-xl font-semibold text-slate-100">Job Logs</h2>
                        {logs && (
                            <p className="text-sm text-slate-400 mt-1">
                                {logs.job_name} • Exit code: {logs.exit_code ?? 'N/A'}
                            </p>
                        )}
                    </div>
                    <button
                        onClick={onClose}
                        className="text-slate-400 hover:text-slate-200 text-2xl font-light transition-colors"
                    >
                        ✕
                    </button>
                </div>

                {/* Tabs */}
                <div className="flex border-b border-slate-700 px-4">
                    {[
                        { id: 'parsed' as const, label: '🎯 Parsed Error' },
                        { id: 'command' as const, label: '📜 Command Log' },
                        { id: 'stderr' as const, label: '⚠️ Stderr' },
                    ].map(tab => (
                        <button
                            key={tab.id}
                            onClick={() => setActiveTab(tab.id)}
                            className={`px-4 py-3 text-sm font-medium transition-colors ${activeTab === tab.id
                                ? 'text-blue-400 border-b-2 border-blue-400 -mb-px'
                                : 'text-slate-400 hover:text-slate-200'
                                }`}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>

                {/* Content */}
                <div className="flex-1 overflow-auto p-4 min-h-[300px]">
                    {loading ? (
                        <div className="flex items-center justify-center h-full text-slate-400">
                            <span className="animate-spin mr-2">⟳</span> Loading logs...
                        </div>
                    ) : !logs ? (
                        <div className="flex items-center justify-center h-full text-slate-400">
                            Failed to load logs
                        </div>
                    ) : (
                        <pre className="text-sm text-slate-300 font-mono whitespace-pre-wrap break-words">
                            {activeTab === 'parsed' && (
                                logs.parsed_error || <span className="text-slate-500 italic">No specific error extracted</span>
                            )}
                            {activeTab === 'command' && (
                                logs.command_log || <span className="text-slate-500 italic">No command log available</span>
                            )}
                            {activeTab === 'stderr' && (
                                logs.command_err || <span className="text-slate-500 italic">No stderr output</span>
                            )}
                        </pre>
                    )}
                </div>

                {/* Footer */}
                <div className="flex justify-end px-6 py-4 border-t border-slate-700">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-lg transition-colors"
                    >
                        Close
                    </button>
                </div>
            </div>
        </div>
    );
}

function Sparkline({ data, color, height = 24 }: { data: number[]; color: string; height?: number }) {
    if (data.length < 2) return null;

    const width = 100;
    const max = Math.max(...data, 100);
    const min = Math.min(...data, 0);
    const range = max - min || 1;

    const points = data.map((value, i) => {
        const x = (i / (data.length - 1)) * width;
        const y = height - ((value - min) / range) * height;
        return `${x},${y}`;
    }).join(' ');

    const colorMap: Record<string, string> = {
        green: '#22c55e',
        purple: '#a855f7',
        blue: '#3b82f6',
        yellow: '#eab308'
    };

    return (
        <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
            <polyline
                fill="none"
                stroke={colorMap[color] || color}
                strokeWidth="1.5"
                points={points}
            />
        </svg>
    );
}

function StageProgress({ job }: { job: Job }) {
    // Define job stages based on mode
    const getStages = (mode: string) => {
        if (mode.includes('antibody')) return ['rfantibody', 'fampnn', 'boltz2'];
        if (mode.includes('binder')) return ['rfdiffusion', 'proteinmpnn', 'boltz2'];
        if (mode.includes('monomer')) return ['rfdiffusion', 'proteinmpnn', 'af2'];
        return [];
    };

    const stages = getStages(job.mode);
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



// QueueStatusTable removed - was unused


// GPU Scheduler Settings Panel
interface SchedulerConfig {
    global: {
        busy_threshold: number;
        cooldown_ms: number;
        enabled: boolean;
        target_vram_fill: number;
        capacity_weight: number;
        emptiness_weight: number;
        msa_concurrency_limit: number;
    };
    overrides: Record<string, {
        force_available: boolean;
        quick_enable: boolean;
        threshold: number | null;
        disabled?: boolean;
        priority_tier?: number | null;
        vram_safety_margin_mb?: number;
        max_concurrent_jobs?: number | null;
    }>;
}

function GPUSchedulerSettings({ gpus }: { gpus: GPUStatus[] }) {
    const [config, setConfig] = useState<SchedulerConfig | null>(null);
    const [loading, setLoading] = useState(false);
    const [localThreshold, setLocalThreshold] = useState(75);
    const [localCooldown, setLocalCooldown] = useState(10);
    const [localCapacityWeight, setLocalCapacityWeight] = useState(3.0);
    const [localEmptinessWeight, setLocalEmptinessWeight] = useState(5.0);
    const [expanded, setExpanded] = useState(false);
    const [debugExpanded, setDebugExpanded] = useState(false);

    // GPU specs: name and max VRAM in MB
    const GPU_SPECS: Record<number, { name: string; maxVramMb: number }> = {
        0: { name: 'RTX 5090', maxVramMb: 32768 },      // 32GB
        1: { name: 'RTX 5060 Ti', maxVramMb: 16384 },   // 16GB
        2: { name: 'RTX 3090 #1', maxVramMb: 24576 },   // 24GB
        3: { name: 'RTX 3090 #2', maxVramMb: 24576 },   // 24GB
    };

    // Per-GPU local state for overrides (stores pending changes)
    const [localGpuOverrides, setLocalGpuOverrides] = useState<Record<string, {
        vramLimitMb: number;
        priorityTier: number | null;
    }>>({});

    // Fetch config on mount
    useEffect(() => {
        fetch('/api/gpu/scheduler-config')
            .then(res => res.json())
            .then(data => {
                setConfig(data);
                setLocalThreshold(Math.round((data.global?.target_vram_fill ?? 0.75) * 100));
                setLocalCooldown(Math.round((data.global?.cooldown_ms ?? 10000) / 1000));
                setLocalCapacityWeight(data.global?.capacity_weight ?? 3.0);
                setLocalEmptinessWeight(data.global?.emptiness_weight ?? 5.0);

                // Initialize per-GPU local state from config
                const gpuStates: typeof localGpuOverrides = {};
                for (const gpuIdStr of Object.keys(data.overrides || {})) {
                    const gpuIdx = parseInt(gpuIdStr);
                    const override = data.overrides[gpuIdStr] || {};
                    const maxVram = GPU_SPECS[gpuIdx]?.maxVramMb ?? 24576;
                    // Calculate vramLimit from threshold and safety margin
                    const thresholdPct = override.threshold ?? (data.global?.target_vram_fill ?? 0.75);
                    const safetyMb = override.vram_safety_margin_mb ?? 0;
                    gpuStates[gpuIdStr] = {
                        vramLimitMb: Math.round(maxVram * thresholdPct - safetyMb),
                        priorityTier: override.priority_tier ?? null,
                    };
                }
                setLocalGpuOverrides(gpuStates);
            })
            .catch(console.error);
    }, []);

    // Get or initialize local GPU override
    const getLocalGpuOverride = (gpuId: string) => {
        const gpuIdx = parseInt(gpuId);
        const maxVram = GPU_SPECS[gpuIdx]?.maxVramMb ?? 24576;

        if (localGpuOverrides[gpuId]) return localGpuOverrides[gpuId];

        const override = (config?.overrides[gpuId] || {}) as {
            threshold?: number | null;
            vram_safety_margin_mb?: number;
            priority_tier?: number | null;
        };

        // Calculate vramLimit from threshold and safety margin
        const thresholdPct = override.threshold ?? (config?.global?.target_vram_fill ?? 0.75);
        const safetyMb = override.vram_safety_margin_mb ?? 0;

        return {
            vramLimitMb: Math.round(maxVram * thresholdPct - safetyMb),
            priorityTier: override.priority_tier ?? null,
        };
    };

    // Update local GPU override state
    const updateLocalGpuOverride = (gpuId: string, field: string, value: number | null) => {
        setLocalGpuOverrides(prev => ({
            ...prev,
            [gpuId]: {
                ...getLocalGpuOverride(gpuId),
                [field]: value,
            }
        }));
    };

    // Save per-GPU override to backend
    const saveGpuOverride = async (gpuId: string) => {
        if (!config) return;
        const local = getLocalGpuOverride(gpuId);
        const existing = config.overrides[gpuId] || {};
        const gpuIdx = parseInt(gpuId);
        const maxVram = GPU_SPECS[gpuIdx]?.maxVramMb ?? 24576;

        // Convert vramLimitMb back to threshold percentage
        const thresholdPct = local.vramLimitMb / maxVram;

        try {
            const res = await fetch(`/api/gpu/scheduler-config/gpu/${gpuId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    force_available: existing.force_available ?? false,
                    quick_enable: existing.quick_enable ?? false,
                    threshold: thresholdPct,
                    disabled: existing.disabled ?? false,
                    priority_tier: local.priorityTier,
                    vram_safety_margin_mb: 0, // No longer using separate safety margin
                    max_concurrent_jobs: existing.max_concurrent_jobs ?? null,
                })
            });
            if (res.ok) {
                const data = await res.json();
                setConfig(prev => prev ? { ...prev, overrides: data.overrides } : null);
            }
        } catch (error) {
            console.error('Failed to update GPU override:', error);
        }
    };

    // Apply VRAM preset to all GPUs (percentage of max)
    const applyVramPreset = async (percentage: number) => {
        if (!config) return;
        setLoading(true);

        for (const gpu of gpus) {
            const gpuId = String(gpu.index);
            const maxVram = GPU_SPECS[gpu.index]?.maxVramMb ?? 24576;
            const vramLimitMb = Math.round(maxVram * (percentage / 100));
            const thresholdPct = percentage / 100;
            const existing = config.overrides[gpuId] || {};

            try {
                await fetch(`/api/gpu/scheduler-config/gpu/${gpuId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        force_available: existing.force_available ?? false,
                        quick_enable: existing.quick_enable ?? false,
                        threshold: thresholdPct,
                        disabled: existing.disabled ?? false,
                        priority_tier: null,
                        vram_safety_margin_mb: 0,
                        max_concurrent_jobs: null,
                    })
                });

                // Update local state
                setLocalGpuOverrides(prev => ({
                    ...prev,
                    [gpuId]: { vramLimitMb, priorityTier: null }
                }));
            } catch (error) {
                console.error(`Failed to set preset for GPU ${gpuId}:`, error);
            }
        }

        // Refresh config
        const res = await fetch('/api/gpu/scheduler-config');
        if (res.ok) {
            const data = await res.json();
            setConfig(data);
        }
        setLoading(false);
    };

    const updateGlobal = async () => {
        setLoading(true);
        try {
            const res = await fetch('/api/gpu/scheduler-config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    busy_threshold: config?.global?.busy_threshold ?? 0.5,
                    cooldown_ms: localCooldown * 1000,
                    enabled: config?.global?.enabled ?? true,
                    target_vram_fill: localThreshold / 100,
                    capacity_weight: localCapacityWeight,
                    emptiness_weight: localEmptinessWeight,
                    msa_concurrency_limit: config?.global?.msa_concurrency_limit ?? 1,
                })
            });
            if (res.ok) {
                const data = await res.json();
                setConfig({ global: data.global, overrides: data.overrides });
            }
        } catch (error) {
            console.error('Failed to update scheduler config:', error);
        } finally {
            setLoading(false);
        }
    };

    // Quick Enable - toggle: if off, enable one-shot. If on, clear it.
    const toggleQuickEnable = async (gpuId: string) => {
        if (!config) return;
        const current = config.overrides[gpuId]?.quick_enable ?? false;
        try {
            const res = await fetch(`/api/gpu/scheduler-config/gpu/${gpuId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    force_available: config.overrides[gpuId]?.force_available ?? false,
                    quick_enable: !current,  // Toggle
                    threshold: null
                })
            });
            if (res.ok) {
                const data = await res.json();
                setConfig(prev => prev ? { ...prev, overrides: data.overrides } : null);
            }
        } catch (error) {
            console.error('Failed to toggle quick enable:', error);
        }
    };

    // Debug mode - permanent force available (dangerous!)
    const toggleForceAvailable = async (gpuId: string) => {
        if (!config) return;
        const current = config.overrides[gpuId]?.force_available ?? false;
        try {
            const res = await fetch(`/api/gpu/scheduler-config/gpu/${gpuId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    force_available: !current,
                    quick_enable: false,
                    threshold: null
                })
            });
            if (res.ok) {
                const data = await res.json();
                setConfig(prev => prev ? { ...prev, overrides: data.overrides } : null);
            }
        } catch (error) {
            console.error('Failed to toggle force available:', error);
        }
    };

    if (!config) return null;

    const isDirty =
        localThreshold !== Math.round((config.global.target_vram_fill ?? 0.75) * 100) ||
        localCooldown !== Math.round(config.global.cooldown_ms / 1000) ||
        localCapacityWeight !== (config.global.capacity_weight ?? 3.0) ||
        localEmptinessWeight !== (config.global.emptiness_weight ?? 5.0);

    return (
        <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700 rounded-xl p-4 mb-4">
            <div
                className="flex items-center justify-between cursor-pointer"
                onClick={() => setExpanded(!expanded)}
            >
                <div className="flex items-center gap-3">
                    <span className="text-sm font-medium text-slate-200">⚙️ GPU Scheduler</span>
                    <span className="px-2 py-0.5 rounded text-xs font-medium bg-green-500/20 text-green-400">
                        {gpus.length} GPUs
                    </span>
                    <span className="px-2 py-0.5 rounded text-xs font-medium bg-cyan-500/20 text-cyan-400">
                        Cap: {config.global.capacity_weight ?? 3.0}
                    </span>
                </div>
                <span className="text-slate-500">{expanded ? '▲' : '▼'}</span>
            </div>

            {expanded && (
                <div className="mt-4 space-y-4">
                    {/* VRAM Preset Buttons - Set all GPUs at once */}
                    <div className="flex items-center justify-between">
                        <span className="text-sm text-slate-400">VRAM Presets</span>
                        <div className="flex items-center gap-2">
                            {[25, 50, 75, 95].map(pct => (
                                <button
                                    key={pct}
                                    onClick={() => applyVramPreset(pct)}
                                    disabled={loading}
                                    className="px-3 py-1.5 rounded text-xs font-medium bg-slate-700/50 text-slate-300 hover:bg-cyan-500/30 hover:text-cyan-300 transition-colors disabled:opacity-50"
                                >
                                    {pct}%
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* GPU Priority Weights Section */}
                    <div className="border-t border-slate-700 pt-4">
                        <div className="text-xs text-slate-400 mb-3">GPU Priority Weights</div>

                        {/* Capacity Weight Slider */}
                        <div className="mb-4">
                            <div className="flex justify-between text-xs text-slate-400 mb-1">
                                <span>Capacity Weight <span className="text-slate-600">(prefer bigger GPUs)</span></span>
                                <span className="text-emerald-400 font-medium">{localCapacityWeight.toFixed(1)}</span>
                            </div>
                            <input
                                type="range"
                                min="0"
                                max="10"
                                step="0.5"
                                value={localCapacityWeight}
                                onChange={(e) => setLocalCapacityWeight(parseFloat(e.target.value))}
                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-emerald-500"
                            />
                            <div className="flex justify-between text-xs text-slate-500 mt-1">
                                <span>0 (ignore size)</span>
                                <span>10 (strongly prefer big)</span>
                            </div>
                        </div>

                        {/* Emptiness Weight Slider */}
                        <div>
                            <div className="flex justify-between text-xs text-slate-400 mb-1">
                                <span>Emptiness Weight <span className="text-slate-600">(prefer idle GPUs)</span></span>
                                <span className="text-amber-400 font-medium">{localEmptinessWeight.toFixed(1)}</span>
                            </div>
                            <input
                                type="range"
                                min="0"
                                max="10"
                                step="0.5"
                                value={localEmptinessWeight}
                                onChange={(e) => setLocalEmptinessWeight(parseFloat(e.target.value))}
                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-amber-500"
                            />
                            <div className="flex justify-between text-xs text-slate-500 mt-1">
                                <span>0 (pack tight)</span>
                                <span>10 (spread out)</span>
                            </div>
                        </div>

                        <p className="text-xs text-slate-500 mt-3">
                            <span className="text-emerald-400">↑ Capacity</span> = fill 5090 first, then 3090s, then 5060 Ti<br />
                            <span className="text-amber-400">↑ Emptiness</span> = prefer idle GPUs over partially-full ones
                        </p>
                    </div>

                    {/* Apply Button */}
                    {isDirty && (
                        <button
                            onClick={updateGlobal}
                            disabled={loading}
                            className="w-full py-2 bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
                        >
                            {loading ? 'Saving...' : 'Apply Changes'}
                        </button>
                    )}

                    {/* Per-GPU Controls (Debug) - Toggleable */}
                    <div className="border-t border-slate-700 pt-4">
                        <button
                            onClick={() => setDebugExpanded(!debugExpanded)}
                            className="flex items-center justify-between w-full text-left"
                        >
                            <div className="flex items-center gap-2">
                                <span className="text-xs text-slate-400">Per-GPU Controls</span>
                                <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400">Debug</span>
                            </div>
                            <span className="text-slate-500 text-xs">{debugExpanded ? '▲ Hide' : '▼ Show'}</span>
                        </button>

                        {debugExpanded && (
                            <div className="mt-4 space-y-4">
                                {gpus.map(gpu => {
                                    const gpuId = String(gpu.index);
                                    const override = config.overrides[gpuId] || {};
                                    const isForced = override.force_available ?? false;
                                    const isQuickEnabled = override.quick_enable ?? false;
                                    const isDisabled = override.disabled ?? false;
                                    const memoryUsed = ((gpu.memory_used_mb / gpu.memory_total_mb) * 100).toFixed(0);

                                    // GPU Name mapping
                                    const gpuNames: Record<number, string> = {
                                        0: 'RTX 5090',
                                        1: 'RTX 5060 Ti',
                                        2: 'RTX 3090 #1',
                                        3: 'RTX 3090 #2',
                                    };
                                    const gpuName = gpuNames[gpu.index] || `GPU ${gpu.index}`;

                                    // Get local state for this GPU
                                    const localOverride = getLocalGpuOverride(gpuId);

                                    // GPU specs for this GPU
                                    const maxVram = GPU_SPECS[gpu.index]?.maxVramMb ?? 24576;
                                    const minVram = 1024; // 1GB minimum

                                    // Check if this GPU has unsaved changes
                                    const serverOverride = config.overrides[gpuId] || {};
                                    const serverThreshold = serverOverride.threshold ?? (config.global?.target_vram_fill ?? 0.75);
                                    const serverSafetyMb = serverOverride.vram_safety_margin_mb ?? 0;
                                    const serverVramLimitMb = Math.round(maxVram * serverThreshold - serverSafetyMb);
                                    const serverPriorityTier = serverOverride.priority_tier ?? null;

                                    const hasUnsavedChanges =
                                        localOverride.vramLimitMb !== serverVramLimitMb ||
                                        localOverride.priorityTier !== serverPriorityTier;

                                    return (
                                        <div key={gpu.index} className={`bg-slate-800/50 rounded-lg px-4 py-4 ${isDisabled ? 'opacity-50' : ''}`}>
                                            {/* Header Row */}
                                            <div className="flex items-center justify-between mb-3">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-sm font-medium text-slate-200">{gpuName}</span>
                                                    <span className={`text-xs px-1.5 py-0.5 rounded ${Number(memoryUsed) > 50 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}`}>
                                                        {memoryUsed}%
                                                    </span>
                                                    {isDisabled && (
                                                        <span className="text-xs px-1.5 py-0.5 rounded bg-red-500/20 text-red-400">Disabled</span>
                                                    )}
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <button
                                                        onClick={() => toggleQuickEnable(gpuId)}
                                                        className={`px-2 py-1 rounded text-xs font-medium transition-colors ${isQuickEnabled
                                                            ? 'bg-cyan-500/40 text-cyan-200'
                                                            : 'bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30'
                                                            }`}
                                                    >
                                                        {isQuickEnabled ? '✓ Queued' : '+ Enable'}
                                                    </button>
                                                    <label className="flex items-center gap-1 text-xs text-slate-500 cursor-pointer">
                                                        <input
                                                            type="checkbox"
                                                            checked={isForced}
                                                            onChange={() => toggleForceAvailable(gpuId)}
                                                            className="w-3 h-3 accent-red-500"
                                                        />
                                                        <span className={isForced ? 'text-red-400' : ''}>Force</span>
                                                    </label>
                                                </div>
                                            </div>

                                            {/* Sliders Row - 2 columns now */}
                                            <div className="grid grid-cols-2 gap-4 text-xs">
                                                {/* Priority Tier Slider */}
                                                <div>
                                                    <div className="flex justify-between text-slate-500 mb-1">
                                                        <span>Priority</span>
                                                        <span className="text-emerald-400">
                                                            {localOverride.priorityTier !== null ? localOverride.priorityTier : 'Auto'}
                                                        </span>
                                                    </div>
                                                    <input
                                                        type="range"
                                                        min="0"
                                                        max="10"
                                                        value={localOverride.priorityTier ?? 5}
                                                        onChange={(e) => {
                                                            const val = parseInt(e.target.value);
                                                            updateLocalGpuOverride(gpuId, 'priorityTier', val);
                                                        }}
                                                        className="w-full h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-emerald-500"
                                                    />
                                                    <div className="flex justify-between text-slate-600 mt-0.5">
                                                        <span>Low</span>
                                                        <button
                                                            onClick={() => updateLocalGpuOverride(gpuId, 'priorityTier', null)}
                                                            className="text-slate-500 hover:text-emerald-400"
                                                        >
                                                            Reset
                                                        </button>
                                                        <span>High</span>
                                                    </div>
                                                </div>

                                                {/* VRAM Limit Slider (merged) */}
                                                <div>
                                                    <div className="flex justify-between text-slate-500 mb-1">
                                                        <span>VRAM Limit</span>
                                                        <span className="text-cyan-400">{(localOverride.vramLimitMb / 1024).toFixed(1)}GB</span>
                                                    </div>
                                                    <input
                                                        type="range"
                                                        min={minVram}
                                                        max={maxVram}
                                                        step="512"
                                                        value={localOverride.vramLimitMb}
                                                        onChange={(e) => updateLocalGpuOverride(gpuId, 'vramLimitMb', parseInt(e.target.value))}
                                                        className="w-full h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                                    />
                                                    <div className="flex justify-between text-slate-600 mt-0.5">
                                                        <span>1GB</span>
                                                        <span>{(maxVram / 1024).toFixed(0)}GB</span>
                                                    </div>
                                                </div>
                                            </div>

                                            {/* Save Button for this GPU */}
                                            {hasUnsavedChanges && (
                                                <button
                                                    onClick={() => saveGpuOverride(gpuId)}
                                                    className="mt-3 w-full py-1.5 bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 rounded text-xs font-medium transition-colors"
                                                >
                                                    Save {gpuName} Settings
                                                </button>
                                            )}
                                        </div>
                                    );
                                })}

                                <p className="text-xs text-slate-500">
                                    <span className="text-emerald-400">Priority</span>: Higher = preferred for jobs (Auto uses GPU capacity).<br />
                                    <span className="text-cyan-400">VRAM Limit</span>: Maximum VRAM the scheduler will use on this GPU.
                                </p>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
