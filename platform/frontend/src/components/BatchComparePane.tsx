import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchJobs, fetchBatchAnalytics } from '../lib/api';
import type { Job } from '../lib/api';
import { isNgsJob } from '../lib/ngsResultRouting';
// Remove unused DistributionChart import
// import { DistributionChart } from './MetricCharts'; 

interface BatchComparePaneProps {
    initialJobId?: string;
}


export function BatchComparePane({ initialJobId }: BatchComparePaneProps) {
    const [selectedJobIds, setSelectedJobIds] = useState<string[]>(initialJobId ? [initialJobId] : []);

    // Fetch all jobs for the selector
    const { data: jobsData } = useQuery({
        queryKey: ['jobs'],
        queryFn: () => fetchJobs({ limit: 500, summary: true }),
    });
    const jobs = useMemo(
        () => (jobsData?.data.jobs ?? []).filter((j: Job) => !isNgsJob(j)),
        [jobsData]
    );

    useEffect(() => {
        if (!jobs.length) {
            if (selectedJobIds.length > 0) {
                setSelectedJobIds([]);
            }
            return;
        }
        setSelectedJobIds((prev) => prev.filter((id) => jobs.some((j) => j.id === id)));
    }, [jobs, selectedJobIds.length]);

    // Fetch batch analytics for selected jobs
    const { data: batchData, isLoading } = useQuery({
        queryKey: ['batch', selectedJobIds],
        queryFn: () => fetchBatchAnalytics(selectedJobIds),
        enabled: selectedJobIds.length > 0
    });

    const comparison = batchData?.data;

    const toggleJob = (id: string) => {
        setSelectedJobIds(prev =>
            prev.includes(id)
                ? prev.filter(x => x !== id)
                : [...prev, id]
        );
    };

    // Transform BatchAnalytics data to row format for table
    const tableRows = comparison ? comparison.job_ids.map(jobId => {
        const job = jobs.find((j: Job) => j.id === jobId);
        return {
            job_id: jobId,
            job_name: job?.name || jobId.substring(0, 8),
            // Access metrics safely from the record -> record map
            avg_plddt: comparison.metrics_summary['plddt_overall']?.[jobId],
            avg_pae: comparison.metrics_summary['pae_overall']?.[jobId],
            avg_ptm: comparison.metrics_summary['ptm']?.[jobId],
            success_rate: comparison.metrics_summary['success_rate']?.[jobId],
            total_designs: job?.design_count || 0
        };
    }) : [];

    return (
        <div className="flex h-[800px]">
            {/* Sidebar: Job Selector */}
            <div className="w-80 border-r border-slate-800 bg-slate-900/30 flex flex-col">
                <div className="p-4 border-b border-slate-800">
                    <h3 className="font-semibold text-slate-200">Select Jobs</h3>
                    <p className="text-xs text-slate-500 mt-1">Select multiple jobs to compare</p>
                </div>
                <div className="flex-1 overflow-y-auto p-2">
                    {jobs.length === 0 ? (
                        <div className="p-3 text-sm text-slate-500">
                            No protein workflow jobs available for comparison.
                        </div>
                    ) : (
                        jobs.map((job: Job) => (
                            <div
                                key={job.id}
                                onClick={() => toggleJob(job.id)}
                                className={`p-3 rounded-lg mb-1 cursor-pointer transition-colors border ${selectedJobIds.includes(job.id)
                                    ? 'bg-blue-500/10 border-blue-500/50'
                                    : 'bg-transparent border-transparent hover:bg-slate-800'
                                    }`}
                            >
                                <div className="flex items-start justify-between">
                                    <span className={`text-sm font-medium ${selectedJobIds.includes(job.id) ? 'text-blue-400' : 'text-slate-300'}`}>
                                        {job.name}
                                    </span>
                                    {selectedJobIds.includes(job.id) && (
                                        <div className="w-2 h-2 rounded-full bg-blue-500 mt-1.5" />
                                    )}
                                </div>
                                <div className="flex items-center gap-2 mt-1 text-xs text-slate-500">
                                    <span>{job.mode}</span>
                                    <span>•</span>
                                    <span>{new Date(job.created_at).toLocaleDateString()}</span>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>

            {/* Main Content: Comparison */}
            <div className="flex-1 overflow-y-auto p-6">
                {selectedJobIds.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center text-slate-500">
                        <div className="text-4xl mb-4">📊</div>
                        <p>Select jobs from the sidebar to begin comparison</p>
                    </div>
                ) : isLoading ? (
                    <div className="h-full flex items-center justify-center">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
                    </div>
                ) : comparison ? (
                    <div className="space-y-8">
                        {/* Summary Table */}
                        <div className="bg-slate-800/50 rounded-xl p-6 border border-slate-700/50">
                            <h3 className="text-lg font-semibold text-white mb-4">Summary Statistics</h3>
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead>
                                        <tr className="border-b border-slate-700 text-slate-400">
                                            <th className="px-4 py-2 text-left">Job</th>
                                            <th className="px-4 py-2 text-center text-emerald-400">Success Rate</th>
                                            <th className="px-4 py-2 text-center text-blue-400">Avg pLDDT</th>
                                            <th className="px-4 py-2 text-center text-amber-400">Avg PAE</th>
                                            <th className="px-4 py-2 text-center text-violet-400">Avg pTM</th>
                                            <th className="px-4 py-2 text-center text-emerald-400">Designs</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-800">
                                        {tableRows.map((stat) => (
                                            <tr key={stat.job_id} className="hover:bg-slate-800/30">
                                                <td className="px-4 py-3 font-medium text-slate-200">{stat.job_name}</td>
                                                <td className="px-4 py-3 text-center font-mono">{stat.success_rate != null ? (stat.success_rate * 100).toFixed(1) + '%' : '—'}</td>
                                                <td className="px-4 py-3 text-center font-mono">{stat.avg_plddt?.toFixed(1) || '—'}</td>
                                                <td className="px-4 py-3 text-center font-mono">{stat.avg_pae?.toFixed(1) || '—'}</td>
                                                <td className="px-4 py-3 text-center font-mono">{stat.avg_ptm?.toFixed(2) || '—'}</td>
                                                <td className="px-4 py-3 text-center font-mono">{stat.total_designs}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        {/* Distributions */}
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                            <div className="bg-slate-800/50 rounded-xl p-6 border border-slate-700/50 h-80">
                                <h4 className="text-sm font-semibold text-slate-300 mb-4">Average pLDDT Comparison</h4>
                                <div className="flex items-end h-[200px] gap-4 px-4">
                                    {tableRows.map((stat) => (
                                        <div key={stat.job_id} className="flex-1 flex flex-col justify-end gap-2 group">
                                            <div
                                                className="w-full bg-blue-500/20 group-hover:bg-blue-500/40 rounded-t transition-all relative"
                                                style={{ height: `${(stat.avg_plddt || 0)}%` }}
                                            >
                                                <div className="absolute -top-6 left-1/2 -translate-x-1/2 text-xs text-white opacity-0 group-hover:opacity-100 transition-opacity">
                                                    {stat.avg_plddt?.toFixed(1)}
                                                </div>
                                            </div>
                                            <div className="text-xs text-slate-500 truncate text-center" title={stat.job_name}>
                                                {stat.job_name}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                    </div>
                ) : (
                    <div className="text-center text-red-400">Failed to load comparison data</div>
                )}
            </div>
        </div>
    );
}
