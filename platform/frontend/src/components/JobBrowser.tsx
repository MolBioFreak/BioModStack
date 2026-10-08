import React, { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchJobs } from '../lib/api';
import type { Job } from '../lib/api';
import { getModeDisplayName } from '../constants/displayNames';

interface JobBrowserProps {
    onSelect: (job: Job) => void;
    selectedJobId?: string | null;
    className?: string;
}

export const JobBrowser: React.FC<JobBrowserProps> = ({ onSelect, selectedJobId, className }) => {
    const [search, setSearch] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const [page, setPage] = useState(0);
    const PAGE_SIZE = 10;

    // Debounce search
    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedSearch(search);
            setPage(0); // Reset page on search
        }, 500);
        return () => clearTimeout(timer);
    }, [search]);

    const { data, isLoading, isError } = useQuery({
        queryKey: ['jobs', 'browser', debouncedSearch, page],
        queryFn: () => fetchJobs({
            q: debouncedSearch,
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
            status: 'completed' // Typically we want completed jobs for results
        })
    });

    const isSelected = (job: Job) => selectedJobId === job.id;

    return (
        <div className={`flex flex-col h-full ${className || ''}`}>
            {/* Search Bar */}
            <div className="mb-3">
                <input
                    type="text"
                    placeholder="Search jobs by name..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
            </div>

            {/* Job List */}
            <div className="flex-1 overflow-y-auto min-h-0 border border-slate-700 rounded-lg bg-slate-900/50">
                {isLoading ? (
                    <div className="flex justify-center items-center h-32 text-slate-400">
                        Loading jobs...
                    </div>
                ) : isError ? (
                    <div className="flex justify-center items-center h-32 text-red-400">
                        Error loading jobs.
                    </div>
                ) : data?.data.jobs.length === 0 ? (
                    <div className="flex justify-center items-center h-32 text-slate-500">
                        No jobs found.
                    </div>
                ) : (
                    <div className="divide-y divide-slate-700/50">
                        {data?.data.jobs.map((job) => (
                            <div
                                key={job.id}
                                onClick={() => onSelect(job)}
                                className={`p-3 cursor-pointer transition-colors ${isSelected(job)
                                        ? 'bg-blue-600/20 border-l-2 border-blue-500'
                                        : 'hover:bg-slate-800/70'
                                    }`}
                            >
                                <div className="flex justify-between items-start">
                                    <div>
                                        <h4 className="font-medium text-slate-200">{job.name}</h4>
                                        <p className="text-xs text-slate-500 mt-1">
                                            {getModeDisplayName(job.mode)} · {new Date(job.created_at).toLocaleDateString()}
                                        </p>
                                    </div>
                                    <div className="text-right">
                                        <span className={`inline-flex px-2 py-0.5 text-xs rounded ${job.status === 'completed' ? 'bg-emerald-500/20 text-emerald-400' :
                                                job.status === 'failed' ? 'bg-red-500/20 text-red-400' :
                                                    'bg-slate-600/30 text-slate-400'
                                            }`}>
                                            {job.status}
                                        </span>
                                        {job.design_count > 0 && (
                                            <p className="text-xs text-slate-400 mt-1">
                                                {job.design_count} designs
                                            </p>
                                        )}
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Pagination Controls */}
            <div className="mt-3 flex justify-between items-center text-sm text-slate-400">
                <button
                    disabled={page === 0}
                    onClick={() => setPage(page - 1)}
                    className="px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed hover:bg-slate-700 transition-colors"
                >
                    Previous
                </button>
                <span className="text-slate-500">
                    Page {page + 1}
                    {data?.data.total ? ` of ${Math.ceil(data.data.total / PAGE_SIZE)}` : ''}
                </span>
                <button
                    disabled={!data || (page + 1) * PAGE_SIZE >= data.data.total}
                    onClick={() => setPage(page + 1)}
                    className="px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed hover:bg-slate-700 transition-colors"
                >
                    Next
                </button>
            </div>
        </div>
    );
};
