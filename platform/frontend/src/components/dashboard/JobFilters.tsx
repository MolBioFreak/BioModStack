import { } from 'react';

interface JobFiltersProps {
    search: string;
    onSearchChange: (value: string) => void;
    status: string;
    onStatusChange: (value: string) => void;
}

export function JobFilters({ search, onSearchChange, status, onStatusChange }: JobFiltersProps) {
    return (
        <div className="flex flex-col sm:flex-row gap-4 mb-6">
            <div className="flex-1">
                <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">🔍</span>
                    <input
                        type="text"
                        placeholder="Search jobs by name or ID..."
                        value={search}
                        onChange={(e) => onSearchChange(e.target.value)}
                        className="w-full bg-slate-800/50 border border-slate-700 text-slate-200 pl-10 pr-4 py-2 rounded-lg focus:outline-none focus:border-blue-500 transition-colors"
                    />
                </div>
            </div>
            <div className="sm:w-48">
                <select
                    value={status}
                    onChange={(e) => onStatusChange(e.target.value)}
                    className="w-full bg-slate-800/50 border border-slate-700 text-slate-200 px-4 py-2 rounded-lg focus:outline-none focus:border-blue-500 transition-colors appearance-none cursor-pointer"
                >
                    <option value="all">All Statuses</option>
                    <option value="running">Running</option>
                    <option value="queued">Queued</option>
                    <option value="completed">Completed</option>
                    <option value="failed">Failed</option>
                    <option value="cancelled">Cancelled</option>
                </select>
            </div>
        </div>
    );
}
