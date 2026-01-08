import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchJobs, cancelJob, resubmitJob, fetchJobLogs, resumeJob } from '../lib/api';
import type { JobLogs, Job } from '../lib/api';

import { QuickViewer } from './QuickViewer';
import { JobQueuePanel } from './JobQueuePanel';
import { SystemResources } from './dashboard/SystemResources';
import { JobQueueTable } from './dashboard/JobQueueTable';
import { JobFilters } from './dashboard/JobFilters';

export function Dashboard() {
    const queryClient = useQueryClient();

    const [quickViewJobId, setQuickViewJobId] = useState<string | null>(null);
    const [logsModalJobId, setLogsModalJobId] = useState<string | null>(null);
    const [logsData, setLogsData] = useState<JobLogs | null>(null);
    const [logsLoading, setLogsLoading] = useState(false);
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('all');
    const [visibleCount, setVisibleCount] = useState(25); // Start with 25 jobs visible

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
        const resumePoint = completed.length > 0 ? `after ${completed[completed.length - 1]}` : 'from start (using cache)';

        if (confirm(`Resume job "${job.name}" ${resumePoint}?`)) {
            resumeMutation.mutate({ jobId: job.id });
        }
    };

    const navigate = useNavigate();

    const handleClone = (job: Job) => {
        // Store job params in localStorage for the submit form to pick up
        const cloneData = {
            name: `${job.name}_clone`,
            model_id: job.model_id,
            mode: job.mode,
            params: job.params || {}
        };
        localStorage.setItem('clonedJobData', JSON.stringify(cloneData));
        // Navigate to submit page
        navigate('/submit');
    };



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

            {/* System Overview & GPU Status */}
            <SystemResources />

            {/* GPU Orchestrator Job Queue */}
            <JobQueuePanel />

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

                <JobFilters
                    search={search}
                    onSearchChange={setSearch}
                    status={statusFilter}
                    onStatusChange={setStatusFilter}
                />

                {(() => {
                    const filteredJobs = (jobsData?.data.jobs || []).filter((job: Job) => {
                        const matchesSearch = search === '' ||
                            job.name.toLowerCase().includes(search.toLowerCase()) ||
                            job.id.includes(search);
                        const matchesStatus = statusFilter === 'all' || job.status === statusFilter;
                        return matchesSearch && matchesStatus;
                    });
                    const displayedJobs = filteredJobs.slice(0, visibleCount);
                    const hasMore = filteredJobs.length > visibleCount;

                    return (
                        <>
                            <JobQueueTable
                                jobs={displayedJobs}
                                loading={jobsLoading}
                                onCancel={handleCancel}
                                onResubmit={handleResubmit}
                                onResume={handleResume}
                                onViewLogs={handleViewLogs}
                                onViewQuick={setQuickViewJobId}
                                onClone={handleClone}
                                quickViewJobId={quickViewJobId}
                            />

                            {/* Pagination Controls */}
                            <div className="flex items-center justify-between mt-4 px-2">
                                <span className="text-sm text-slate-400">
                                    Showing {displayedJobs.length} of {filteredJobs.length} jobs
                                </span>
                                <div className="flex items-center gap-3">
                                    {/* Quick page size buttons */}
                                    <div className="flex gap-1">
                                        {[25, 50, 100].map(n => (
                                            <button
                                                key={n}
                                                onClick={() => setVisibleCount(n)}
                                                className={`px-2 py-1 text-xs rounded transition-colors ${visibleCount === n
                                                        ? 'bg-purple-500/30 text-purple-300'
                                                        : 'bg-slate-700/50 text-slate-400 hover:bg-slate-700'
                                                    }`}
                                            >
                                                {n}
                                            </button>
                                        ))}
                                        <button
                                            onClick={() => setVisibleCount(filteredJobs.length)}
                                            className={`px-2 py-1 text-xs rounded transition-colors ${visibleCount >= filteredJobs.length
                                                    ? 'bg-purple-500/30 text-purple-300'
                                                    : 'bg-slate-700/50 text-slate-400 hover:bg-slate-700'
                                                }`}
                                        >
                                            All
                                        </button>
                                    </div>

                                    {hasMore && (
                                        <button
                                            onClick={() => setVisibleCount(prev => prev + 25)}
                                            className="px-4 py-2 bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 text-white text-sm rounded-lg transition-all hover:scale-105"
                                        >
                                            Load More (+25)
                                        </button>
                                    )}
                                </div>
                            </div>
                        </>
                    );
                })()}
            </section>
        </div >
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






