interface JobFiltersProps {
    search: string;
    onSearchChange: (value: string) => void;
    status: string;
    onStatusChange: (value: string) => void;
    showNgsJobs: boolean;
    onShowNgsJobsChange: (value: boolean) => void;
}

export function JobFilters({
    search,
    onSearchChange,
    status,
    onStatusChange,
    showNgsJobs,
    onShowNgsJobsChange,
}: JobFiltersProps) {
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
            <label className="sm:w-44 flex items-center gap-2 px-3 py-2 bg-slate-800/50 border border-slate-700 rounded-lg text-slate-200 cursor-pointer select-none">
                <input
                    type="checkbox"
                    checked={showNgsJobs}
                    onChange={(e) => onShowNgsJobsChange(e.target.checked)}
                    className="h-4 w-4 rounded border-slate-600 bg-slate-900 text-blue-500 focus:ring-blue-500"
                />
                <span className="text-sm">Show NGS Jobs</span>
            </label>
        </div>
    );
}
