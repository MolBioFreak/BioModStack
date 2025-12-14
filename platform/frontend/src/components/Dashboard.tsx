import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchJobs, fetchSystemStatus, cancelJob, fetchPowerControl, setPowerControlManual } from '../lib/api';
import type { GPUStatus, CPUStatus, RAMStatus } from '../lib/api';
import { JobDetailsPanel } from './JobDetailsPanel';

export function Dashboard() {
    const queryClient = useQueryClient();
    const [expandedJobId, setExpandedJobId] = useState<string | null>(null);

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

            {/* Active Tasks Queue */}
            <section className="mb-6">
                <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700 rounded-lg overflow-hidden">
                    <div className="flex items-center justify-between px-3 py-2 border-b border-slate-700/50">
                        <h3 className="text-sm font-semibold text-slate-200">📋 Active Tasks</h3>
                        <span className="text-xs text-slate-500">
                            {gpus.reduce((sum, gpu) => sum + gpu.processes.length, 0)} running
                        </span>
                    </div>
                    <QueueStatusTable gpus={gpus} runningJobs={(jobsData?.data?.jobs || []).filter((j: { status: string; model_id?: string; name: string }) => j.status === 'running')} />
                </div>
            </section>

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
                        />
                    ))}
                    {gpus.length === 0 && (
                        <div className="col-span-full text-slate-500 text-center py-8">
                            No GPU data available
                        </div>
                    )}
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
                                {jobsData?.data.jobs.map((job) => (
                                    <React.Fragment key={job.id}>
                                        <tr
                                            onClick={() => setExpandedJobId(expandedJobId === job.id ? null : job.id)}
                                            className={`border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors cursor-pointer ${expandedJobId === job.id ? 'bg-slate-700/40' : ''}`}
                                        >
                                            <td className="py-3 px-4 text-white font-medium">
                                                <span className="mr-2">{expandedJobId === job.id ? '▼' : '▶'}</span>
                                                {job.name}
                                            </td>
                                            <td className="py-3 px-4">
                                                <span className="px-2 py-1 bg-blue-500/20 text-blue-400 rounded text-xs">
                                                    {job.mode}
                                                </span>
                                            </td>
                                            <td className="py-3 px-4">
                                                <StatusBadge status={job.status} />
                                            </td>
                                            <td className="py-3 px-4 text-slate-300">{job.design_count}</td>
                                            <td className="py-3 px-4 text-slate-400 text-sm">
                                                {new Date(job.created_at).toLocaleString()}
                                            </td>
                                            <td className="py-3 px-4">
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
                                            </td>
                                        </tr>
                                        {expandedJobId === job.id && (
                                            <JobDetailsPanel
                                                job={job}
                                                onClose={() => setExpandedJobId(null)}
                                            />
                                        )}
                                    </React.Fragment>
                                )) ?? (
                                        <tr>
                                            <td colSpan={6} className="py-8 text-center text-slate-500">
                                                No jobs found
                                            </td>
                                        </tr>
                                    )}
                            </tbody>
                        </table>
                    </div>
                )}
            </section>
        </div>
    );
}

function GPUCard({ gpu, currentLimit, onSetLimit, isPending }: {
    gpu: GPUStatus;
    currentLimit: number;
    onSetLimit: (watts: number) => void;
    isPending: boolean;
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
        const newVal = Math.min(current + 25, gpu.max_power_watts);
        setInputValue(String(newVal));
    };

    const handleDecrement = () => {
        const current = parseInt(inputValue, 10) || currentLimit;
        const newVal = Math.max(current - 25, gpu.min_power_watts);
        setInputValue(String(newVal));
    };

    const isOutOfRange = (() => {
        const v = parseInt(inputValue, 10);
        return !isNaN(v) && (v < gpu.min_power_watts || v > gpu.max_power_watts);
    })();

    const isDirty = parseInt(inputValue, 10) !== currentLimit;

    return (
        <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700 rounded-xl p-5 hover:border-purple-500/50 transition-all duration-300">
            {/* Header */}
            <div className="flex items-center justify-between mb-3">
                <span className="text-sm text-slate-400">GPU {gpu.index}</span>
                <span
                    className={`px-2 py-1 rounded-full text-xs font-medium ${gpu.utilization > 80
                        ? 'bg-green-500/20 text-green-400'
                        : gpu.utilization > 20
                            ? 'bg-yellow-500/20 text-yellow-400'
                            : 'bg-slate-500/20 text-slate-400'
                        }`}
                >
                    {gpu.utilization}% GPU
                </span>
            </div>
            <h3 className="text-lg font-semibold text-white truncate mb-4">{gpu.name}</h3>

            {/* Power Control - Full Width */}
            <div className="bg-slate-900/50 rounded-lg p-3 mb-4">
                <div className="flex justify-between items-center mb-2">
                    <span className="text-xs text-slate-400">Power</span>
                    <span className="text-xs text-slate-500">
                        Limit: <span className="text-orange-400 font-medium">{currentLimit}W</span>
                    </span>
                </div>
                <div className="text-sm font-medium text-orange-400 mb-1">
                    Draw: {gpu.power_draw_w}W
                </div>
                <div className="w-full bg-slate-700 rounded-full h-1.5 mb-3">
                    <div
                        className={`h-1.5 rounded-full transition-all ${powerPercent > 90 ? 'bg-red-500' : powerPercent > 70 ? 'bg-yellow-500' : 'bg-orange-500'}`}
                        style={{ width: `${Math.min(powerPercent, 100)}%` }}
                    />
                </div>
                {/* Limit adjuster */}
                <div className="flex items-center gap-2">
                    <button
                        onClick={handleDecrement}
                        className="w-7 h-7 flex items-center justify-center bg-slate-700 hover:bg-slate-600 text-slate-300 rounded transition-colors text-sm font-bold"
                        title="Decrease by 25W"
                    >
                        ▼
                    </button>
                    <input
                        type="number"
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        className={`w-16 px-2 py-1 bg-slate-700 border rounded text-white text-sm text-center ${isOutOfRange ? 'border-red-500' : isDirty ? 'border-yellow-500' : 'border-slate-600'}`}
                    />
                    <button
                        onClick={handleIncrement}
                        className="w-7 h-7 flex items-center justify-center bg-slate-700 hover:bg-slate-600 text-slate-300 rounded transition-colors text-sm font-bold"
                        title="Increase by 25W"
                    >
                        ▲
                    </button>
                    <button
                        onClick={handleApply}
                        disabled={isPending || isOutOfRange || !isDirty}
                        className="px-3 py-1 bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 rounded text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        {isPending ? '...' : 'Apply'}
                    </button>
                </div>
                <div className="text-xs text-slate-500 mt-2">
                    Range: {gpu.min_power_watts}W – {gpu.max_power_watts}W
                </div>
            </div>

            {/* Stats Grid - 3 columns */}
            <div className="grid grid-cols-3 gap-3 mb-4">
                {/* Temperature & Fan */}
                <div className="bg-slate-900/50 rounded-lg p-2">
                    <div className="text-xs text-slate-400 mb-1">Temp / Fan</div>
                    <div className="flex items-center gap-1">
                        <span className={`text-sm font-medium ${gpu.temperature > 80 ? 'text-red-400' : gpu.temperature > 60 ? 'text-yellow-400' : 'text-green-400'}`}>
                            {gpu.temperature}°C
                        </span>
                        <span className="text-slate-500">|</span>
                        <span className="text-sm font-medium text-blue-400">{gpu.fan_speed}%</span>
                    </div>
                </div>

                {/* Clocks */}
                <div className="bg-slate-900/50 rounded-lg p-2">
                    <div className="text-xs text-slate-400 mb-1">Core</div>
                    <div className="text-sm font-medium text-purple-400">
                        {gpu.clock_graphics_mhz} MHz
                    </div>
                </div>

                <div className="bg-slate-900/50 rounded-lg p-2">
                    <div className="text-xs text-slate-400 mb-1">Mem</div>
                    <div className="text-sm font-medium text-cyan-400">
                        {gpu.clock_memory_mhz} MHz
                    </div>
                </div>
            </div>

            {/* Memory Bar */}
            <div className="mb-3">
                <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>VRAM {gpu.reserved_memory_mb > 0 && <span className="text-orange-400 italic">(+{Math.round(gpu.reserved_memory_mb / 1024)}GB Rsrv)</span>}</span>
                    <span>
                        {((gpu.memory_used_mb + gpu.reserved_memory_mb) / 1024).toFixed(1)} / {(gpu.memory_total_mb / 1024).toFixed(1)} GB
                    </span>
                </div>
                <div className="w-full bg-slate-700 rounded-full h-2 relative overflow-hidden">
                    {/* Real Usage */}
                    <div
                        className="absolute top-0 left-0 h-full bg-gradient-to-r from-blue-500 to-purple-500 rounded-full transition-all duration-500 z-20"
                        style={{ width: `${memoryPercent}%` }}
                    />
                    {/* Reserved Usage (Ghost Bar) */}
                    {gpu.reserved_memory_mb > 0 && (
                        <div
                            className="absolute top-0 left-0 h-full bg-orange-500/30 transition-all duration-500 z-10 striped-bar"
                            style={{
                                left: `${memoryPercent}%`,
                                width: `${(gpu.reserved_memory_mb / gpu.memory_total_mb) * 100}%`
                            }}
                        />
                    )}
                </div>
            </div>

            {/* Processes */}
            {gpu.processes.length > 0 && (
                <div className="border-t border-slate-700 pt-3 mt-3">
                    <div className="text-xs text-slate-400 mb-2">Running Processes</div>
                    <div className="space-y-1">
                        {gpu.processes.slice(0, 3).map((proc) => (
                            <div key={proc.pid} className="flex justify-between text-xs">
                                <span className="text-slate-300 truncate max-w-[60%]">{proc.name}</span>
                                <span className="text-slate-500">{proc.memory_mb} MB</span>
                            </div>
                        ))}
                        {gpu.processes.length > 3 && (
                            <div className="text-xs text-slate-500">+{gpu.processes.length - 3} more</div>
                        )}
                    </div>
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

function StatusBadge({ status }: { status: string }) {
    const styles: Record<string, string> = {
        queued: 'bg-slate-500/20 text-slate-400',
        running: 'bg-blue-500/20 text-blue-400 animate-pulse',
        completed: 'bg-green-500/20 text-green-400',
        failed: 'bg-red-500/20 text-red-400',
        cancelled: 'bg-orange-500/20 text-orange-400',
    };

    return (
        <span className={`px-2 py-1 rounded text-xs font-medium ${styles[status] ?? styles.queued}`}>
            {status}
        </span>
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

// Queue Status Table - shows running GPU processes
// Uses running jobs data to show model names instead of generic 'python'
function QueueStatusTable({ gpus, runningJobs }: { gpus: GPUStatus[]; runningJobs: Array<{ model_id: string; name: string }> }) {
    // Flatten all GPU processes into a single list with GPU info
    const tasks = gpus.flatMap(gpu =>
        gpu.processes.map(proc => ({
            gpu: gpu.index,
            gpuName: gpu.name,
            pid: proc.pid,
            name: proc.name,
            vram: proc.memory_mb
        }))
    );

    // Get model type from running jobs when available, fallback to process name detection
    const getModelType = (name: string, index: number): string => {
        // If we have running jobs, use their model_id (rotating through jobs for multiple processes)
        if (runningJobs.length > 0) {
            const job = runningJobs[index % runningJobs.length];
            const modelId = job.model_id || '';
            // Map model_id to display name
            if (modelId.toLowerCase().includes('rf3') || modelId.toLowerCase().includes('foundry')) return 'RF3';
            if (modelId.toLowerCase().includes('boltz')) return 'Boltz';
            if (modelId.toLowerCase().includes('fampnn')) return 'FAMPNN';
            if (modelId.toLowerCase().includes('mpnn')) return 'MPNN';
            if (modelId.toLowerCase().includes('rfdiff')) return 'RFdiff';
            if (modelId) return modelId;
        }
        // Fallback: extract from process name
        const lower = name.toLowerCase();
        if (lower.includes('boltz')) return 'Boltz';
        if (lower.includes('rf3') || lower.includes('foundry')) return 'RF3';
        if (lower.includes('fampnn')) return 'FAMPNN';
        if (lower.includes('mpnn')) return 'MPNN';
        if (lower.includes('rfdiff')) return 'RFdiff';
        if (lower.includes('python')) return 'Python';
        return name.slice(0, 12);
    };

    return (
        <div className="max-h-48 overflow-y-auto">
            <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-800">
                    <tr className="border-b border-slate-700/50">
                        <th className="text-left py-1 px-2 font-medium text-slate-500">Process</th>
                        <th className="text-left py-1 px-2 font-medium text-slate-500">Model</th>
                        <th className="text-left py-1 px-2 font-medium text-slate-500">GPU</th>
                        <th className="text-left py-1 px-2 font-medium text-slate-500">VRAM</th>
                    </tr>
                </thead>
                <tbody>
                    {tasks.length === 0 ? (
                        <tr>
                            <td colSpan={4} className="py-4 text-center text-slate-500">
                                No active tasks
                            </td>
                        </tr>
                    ) : (
                        tasks.map((task, idx) => (
                            <tr key={`${task.gpu}-${task.pid}-${idx}`} className="border-b border-slate-700/30 hover:bg-slate-700/20">
                                <td className="py-1 px-2 text-slate-300 truncate max-w-[100px]" title={task.name}>
                                    {task.name.slice(0, 15)}
                                </td>
                                <td className="py-1 px-2">
                                    <span className="px-1.5 py-0.5 bg-purple-500/20 text-purple-400 rounded text-xs">
                                        {getModelType(task.name, idx)}
                                    </span>
                                </td>
                                <td className="py-1 px-2 text-cyan-400">{task.gpu}</td>
                                <td className="py-1 px-2 text-amber-400">{task.vram}MB</td>
                            </tr>
                        ))
                    )}
                </tbody>
            </table>
        </div>
    );
}


// GPU Scheduler Settings Panel
interface SchedulerConfig {
    global: {
        busy_threshold: number;
        cooldown_ms: number;
        enabled: boolean;
    };
    overrides: Record<string, { force_available: boolean; quick_enable: boolean; threshold: number | null }>;
}

function GPUSchedulerSettings({ gpus }: { gpus: GPUStatus[] }) {
    const [config, setConfig] = useState<SchedulerConfig | null>(null);
    const [loading, setLoading] = useState(false);
    const [localThreshold, setLocalThreshold] = useState(50);
    const [localCooldown, setLocalCooldown] = useState(10);
    const [expanded, setExpanded] = useState(false);

    // Fetch config on mount
    useEffect(() => {
        fetch('/api/gpu/scheduler-config')
            .then(res => res.json())
            .then(data => {
                setConfig(data);
                setLocalThreshold(Math.round((data.global?.busy_threshold ?? 0.5) * 100));
                setLocalCooldown(Math.round((data.global?.cooldown_ms ?? 10000) / 1000));
            })
            .catch(console.error);
    }, []);

    const updateGlobal = async () => {
        setLoading(true);
        try {
            const res = await fetch('/api/gpu/scheduler-config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    busy_threshold: localThreshold / 100,
                    cooldown_ms: localCooldown * 1000,
                    enabled: config?.global?.enabled ?? true
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

    const toggleEnabled = async () => {
        if (!config) return;
        setLoading(true);
        try {
            const res = await fetch('/api/gpu/scheduler-config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    busy_threshold: config.global.busy_threshold,
                    cooldown_ms: config.global.cooldown_ms,
                    enabled: !config.global.enabled
                })
            });
            if (res.ok) {
                const data = await res.json();
                setConfig({ global: data.global, overrides: data.overrides });
            }
        } catch (error) {
            console.error('Failed to toggle scheduler:', error);
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

    const isDirty = localThreshold !== Math.round(config.global.busy_threshold * 100) ||
        localCooldown !== Math.round(config.global.cooldown_ms / 1000);

    return (
        <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700 rounded-xl p-4 mb-4">
            <div
                className="flex items-center justify-between cursor-pointer"
                onClick={() => setExpanded(!expanded)}
            >
                <div className="flex items-center gap-3">
                    <span className="text-sm font-medium text-slate-200">⚙️ GPU Scheduler</span>
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${config.global.enabled ? 'bg-green-500/20 text-green-400' : 'bg-slate-500/20 text-slate-400'}`}>
                        {config.global.enabled ? `${Math.round(config.global.busy_threshold * 100)}% Lock` : 'OFF'}
                    </span>
                </div>
                <span className="text-slate-500">{expanded ? '▲' : '▼'}</span>
            </div>

            {expanded && (
                <div className="mt-4 space-y-4">
                    {/* Master Enable Toggle */}
                    <div className="flex items-center justify-between">
                        <span className="text-sm text-slate-400">Capacity Lock</span>
                        <button
                            onClick={toggleEnabled}
                            disabled={loading}
                            className={`relative w-12 h-6 rounded-full transition-colors ${config.global.enabled ? 'bg-green-500' : 'bg-slate-600'}`}
                        >
                            <div className={`absolute top-1 w-4 h-4 bg-white rounded-full transition-transform ${config.global.enabled ? 'left-7' : 'left-1'}`} />
                        </button>
                    </div>

                    {/* Threshold Slider */}
                    <div>
                        <div className="flex justify-between text-xs text-slate-400 mb-1">
                            <span>VRAM Threshold</span>
                            <span className="text-cyan-400 font-medium">{localThreshold}%</span>
                        </div>
                        <input
                            type="range"
                            min="20"
                            max="90"
                            value={localThreshold}
                            onChange={(e) => setLocalThreshold(parseInt(e.target.value))}
                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                        />
                        <div className="flex justify-between text-xs text-slate-500 mt-1">
                            <span>20%</span>
                            <span>90%</span>
                        </div>
                    </div>

                    {/* Cooldown Slider */}
                    <div>
                        <div className="flex justify-between text-xs text-slate-400 mb-1">
                            <span>Cooldown Time</span>
                            <span className="text-purple-400 font-medium">{localCooldown}s</span>
                        </div>
                        <input
                            type="range"
                            min="5"
                            max="30"
                            value={localCooldown}
                            onChange={(e) => setLocalCooldown(parseInt(e.target.value))}
                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-purple-500"
                        />
                        <div className="flex justify-between text-xs text-slate-500 mt-1">
                            <span>5s</span>
                            <span>30s</span>
                        </div>
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

                    {/* Per-GPU Controls */}
                    <div className="border-t border-slate-700 pt-4">
                        <div className="text-xs text-slate-400 mb-3">Per-GPU Controls</div>
                        <div className="space-y-2">
                            {gpus.map(gpu => {
                                const gpuId = String(gpu.index);
                                const override = config.overrides[gpuId] || {};
                                const isForced = override.force_available ?? false;
                                const isQuickEnabled = override.quick_enable ?? false;
                                const memoryUsed = ((gpu.memory_used_mb / gpu.memory_total_mb) * 100).toFixed(0);

                                return (
                                    <div key={gpu.index} className="flex items-center justify-between bg-slate-800/50 rounded-lg px-3 py-2">
                                        <div className="flex items-center gap-2">
                                            <span className="text-xs text-slate-300">GPU {gpu.index}</span>
                                            <span className={`text-xs px-1.5 py-0.5 rounded ${Number(memoryUsed) > 50 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'
                                                }`}>
                                                {memoryUsed}%
                                            </span>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            {/* Quick Enable Button (one-shot, toggleable) */}
                                            <button
                                                onClick={() => toggleQuickEnable(gpuId)}
                                                className={`px-2 py-1 rounded text-xs font-medium transition-colors ${isQuickEnabled
                                                    ? 'bg-cyan-500/40 text-cyan-200 hover:bg-red-500/30 hover:text-red-300'
                                                    : 'bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30'
                                                    }`}
                                            >
                                                {isQuickEnabled ? '✓ Queued (click to cancel)' : '+ Enable'}
                                            </button>

                                            {/* Debug Mode Checkbox (permanent) */}
                                            <label className="flex items-center gap-1 text-xs text-slate-500 cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={isForced}
                                                    onChange={() => toggleForceAvailable(gpuId)}
                                                    className="w-3 h-3 accent-red-500"
                                                />
                                                <span className={isForced ? 'text-red-400' : ''}>Debug</span>
                                            </label>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                        <p className="text-xs text-slate-500 mt-2">
                            <span className="text-cyan-400">+ Enable</span> = Accept 1 job, then normal rules apply.<br />
                            <span className="text-red-400">Debug</span> = Permanent override (⚠️ can cause OOM!)
                        </p>
                    </div>
                </div>
            )}
        </div>
    );
}
