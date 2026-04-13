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
        <div className="mb-6 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_12rem_auto] lg:items-center">
            <div className="flex-1">
                <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">🔍</span>
                    <input
                        type="text"
                        placeholder="Search jobs by name or ID..."
                        value={search}
                        onChange={(e) => onSearchChange(e.target.value)}
                        className="w-full rounded-lg border border-slate-700 bg-slate-800/50 py-2.5 pl-10 pr-4 text-slate-200 transition-colors focus:border-blue-500 focus:outline-none"
                    />
                </div>
            </div>
            <div className="lg:w-48">
                <select
                    value={status}
                    onChange={(e) => onStatusChange(e.target.value)}
                    className="w-full cursor-pointer appearance-none rounded-lg border border-slate-700 bg-slate-800/50 px-4 py-2.5 text-slate-200 transition-colors focus:border-blue-500 focus:outline-none"
                >
                    <option value="all">All Statuses</option>
                    <option value="running">Running</option>
                    <option value="queued">Queued</option>
                    <option value="awaiting_input">Awaiting Input</option>
                    <option value="completed">Completed</option>
                    <option value="failed">Failed</option>
                    <option value="cancelled">Cancelled</option>
                </select>
            </div>
            <label className="flex min-h-11 items-center gap-2 rounded-lg border border-slate-700 bg-slate-800/50 px-3 py-2 text-slate-200 cursor-pointer select-none lg:w-44">
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
