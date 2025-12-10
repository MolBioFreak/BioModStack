import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchJobs, fetchSystemStatus, cancelJob, fetchPowerProfile, setPowerProfile } from '../lib/api';
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

    const { data: powerProfileData } = useQuery({
        queryKey: ['powerProfile'],
        queryFn: fetchPowerProfile,
    });

    const ecoModeMutation = useMutation({
        mutationFn: (enable: boolean) => setPowerProfile(enable),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['powerProfile'] });
            queryClient.invalidateQueries({ queryKey: ['system'] });
        },
    });

    const ecoMode = powerProfileData?.data.eco_mode ?? false;

    const gpus = systemData?.data.gpus ?? [];
    const cpu = systemData?.data.cpu;
    const ram = systemData?.data.ram;

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
                        {cpu && <CPUCard cpu={cpu} />}
                        {/* RAM Card */}
                        {ram && <RAMCard ram={ram} />}
                    </div>
                </section>
            )}

            {/* GPU Status Cards */}
            <section className="mb-8">
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-xl font-semibold text-slate-200">GPU Status</h2>
                    <button
                        onClick={() => ecoModeMutation.mutate(!ecoMode)}
                        disabled={ecoModeMutation.isPending}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg font-medium text-sm transition-all ${ecoMode
                            ? 'bg-green-500/20 text-green-400 hover:bg-green-500/30 border border-green-500/50'
                            : 'bg-slate-700/50 text-slate-400 hover:bg-slate-700 border border-slate-600'
                            } ${ecoModeMutation.isPending ? 'opacity-50 cursor-wait' : ''}`}
                    >
                        <span className={`w-2 h-2 rounded-full ${ecoMode ? 'bg-green-400' : 'bg-slate-500'}`} />
                        {ecoModeMutation.isPending ? 'Applying...' : ecoMode ? 'Eco Mode ON' : 'Eco Mode OFF'}
                    </button>
                </div>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {gpus.map((gpu) => (
                        <GPUCard key={gpu.index} gpu={gpu} />
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

function GPUCard({ gpu }: { gpu: GPUStatus }) {
    const memoryPercent = (gpu.memory_used_mb / gpu.memory_total_mb) * 100;
    const powerPercent = gpu.power_limit_w > 0 ? (gpu.power_draw_w / gpu.power_limit_w) * 100 : 0;

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

            {/* Stats Grid */}
            <div className="grid grid-cols-2 gap-3 mb-4">
                {/* Power */}
                <div className="bg-slate-900/50 rounded-lg p-2">
                    <div className="text-xs text-slate-400 mb-1">Power</div>
                    <div className="text-sm font-medium text-orange-400">
                        {gpu.power_draw_w}W / {gpu.power_limit_w}W
                    </div>
                    <div className="w-full bg-slate-700 rounded-full h-1 mt-1">
                        <div
                            className="bg-orange-500 h-1 rounded-full transition-all"
                            style={{ width: `${Math.min(powerPercent, 100)}%` }}
                        />
                    </div>
                </div>

                {/* Temperature & Fan */}
                <div className="bg-slate-900/50 rounded-lg p-2">
                    <div className="text-xs text-slate-400 mb-1">Temp / Fan</div>
                    <div className="flex items-center gap-2">
                        <span className={`text-sm font-medium ${gpu.temperature > 80 ? 'text-red-400' : gpu.temperature > 60 ? 'text-yellow-400' : 'text-green-400'}`}>
                            {gpu.temperature}°C
                        </span>
                        <span className="text-slate-500">|</span>
                        <span className="text-sm font-medium text-blue-400">{gpu.fan_speed}%</span>
                    </div>
                </div>

                {/* Clocks */}
                <div className="bg-slate-900/50 rounded-lg p-2">
                    <div className="text-xs text-slate-400 mb-1">Core Clock</div>
                    <div className="text-sm font-medium text-purple-400">
                        {gpu.clock_graphics_mhz} MHz
                        <span className="text-slate-500 text-xs"> / {gpu.clock_max_graphics_mhz}</span>
                    </div>
                </div>

                <div className="bg-slate-900/50 rounded-lg p-2">
                    <div className="text-xs text-slate-400 mb-1">Memory Clock</div>
                    <div className="text-sm font-medium text-cyan-400">
                        {gpu.clock_memory_mhz} MHz
                        <span className="text-slate-500 text-xs"> / {gpu.clock_max_memory_mhz}</span>
                    </div>
                </div>
            </div>

            {/* Memory Bar */}
            <div className="mb-3">
                <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>VRAM</span>
                    <span>
                        {(gpu.memory_used_mb / 1024).toFixed(1)} / {(gpu.memory_total_mb / 1024).toFixed(1)} GB
                    </span>
                </div>
                <div className="w-full bg-slate-700 rounded-full h-2">
                    <div
                        className="bg-gradient-to-r from-blue-500 to-purple-500 h-2 rounded-full transition-all duration-500"
                        style={{ width: `${memoryPercent}%` }}
                    />
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

function CPUCard({ cpu }: { cpu: CPUStatus }) {
    return (
        <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
                <span className="text-sm text-slate-400">CPU</span>
                <span className={`px-2 py-1 rounded-full text-xs font-medium ${cpu.utilization > 80 ? 'bg-red-500/20 text-red-400' : cpu.utilization > 50 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}`}>
                    {cpu.utilization.toFixed(1)}%
                </span>
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

function RAMCard({ ram }: { ram: RAMStatus }) {
    return (
        <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
                <span className="text-sm text-slate-400">Memory</span>
                <span className={`px-2 py-1 rounded-full text-xs font-medium ${ram.utilization > 90 ? 'bg-red-500/20 text-red-400' : ram.utilization > 70 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}`}>
                    {ram.utilization.toFixed(1)}%
                </span>
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
