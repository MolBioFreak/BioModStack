import { useState, useEffect } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchDesigns, toggleDesignFavorite, downloadDesignPdb, fetchJobs } from '../lib/api';
import type { Design, DesignFilters, Job } from '../lib/api';

export function DesignBrowser() {
    const [searchParams, setSearchParams] = useSearchParams();
    const queryClient = useQueryClient();

    // Filters
    const [filters, setFilters] = useState<DesignFilters>(() => {
        const limitParam = Number(searchParams.get('limit'));
        const offsetParam = Number(searchParams.get('offset'));
        return {
            limit: Number.isFinite(limitParam) && limitParam > 0 ? limitParam : 50,
            offset: Number.isFinite(offsetParam) && offsetParam >= 0 ? offsetParam : 0,
        };
    });
    const [rogMin, setRogMin] = useState<string>(searchParams.get('rogMin') ?? '');
    const [rogMax, setRogMax] = useState<string>(searchParams.get('rogMax') ?? '');
    const [rfdRogMin, setRfdRogMin] = useState<string>(searchParams.get('rfdRogMin') ?? '');
    const [rfdRogMax, setRfdRogMax] = useState<string>(searchParams.get('rfdRogMax') ?? '');
    const [selectedJobId, setSelectedJobId] = useState<string>(searchParams.get('job') ?? '');
    const [favoritesOnly, setFavoritesOnly] = useState(searchParams.get('fav') === '1' || searchParams.get('fav') === 'true');

    useEffect(() => {
        setSelectedJobId(searchParams.get('job') ?? '');
        setFavoritesOnly(searchParams.get('fav') === '1' || searchParams.get('fav') === 'true');
        setRogMin(searchParams.get('rogMin') ?? '');
        setRogMax(searchParams.get('rogMax') ?? '');
        setRfdRogMin(searchParams.get('rfdRogMin') ?? '');
        setRfdRogMax(searchParams.get('rfdRogMax') ?? '');
        const limitParam = Number(searchParams.get('limit'));
        const offsetParam = Number(searchParams.get('offset'));
        setFilters(prev => ({
            ...prev,
            limit: Number.isFinite(limitParam) && limitParam > 0 ? limitParam : prev.limit || 50,
            offset: Number.isFinite(offsetParam) && offsetParam >= 0 ? offsetParam : prev.offset || 0,
        }));
    }, [searchParams]);

    useEffect(() => {
        const params = new URLSearchParams();
        if (selectedJobId) params.set('job', selectedJobId);
        if (favoritesOnly) params.set('fav', '1');
        if (rogMin.trim() !== '') params.set('rogMin', rogMin.trim());
        if (rogMax.trim() !== '') params.set('rogMax', rogMax.trim());
        if (rfdRogMin.trim() !== '') params.set('rfdRogMin', rfdRogMin.trim());
        if (rfdRogMax.trim() !== '') params.set('rfdRogMax', rfdRogMax.trim());
        if (filters.limit) params.set('limit', String(filters.limit));
        if (filters.offset) params.set('offset', String(filters.offset));
        setSearchParams(params, { replace: true });
    }, [selectedJobId, favoritesOnly, rogMin, rogMax, rfdRogMin, rfdRogMax, filters.limit, filters.offset, setSearchParams]);

    // Fetch designs
    const { data: designsData, isLoading } = useQuery({
        queryKey: ['designs', filters, selectedJobId, favoritesOnly, rogMin, rogMax, rfdRogMin, rfdRogMax],
        queryFn: () => fetchDesigns({
            ...filters,
            job_id: selectedJobId || undefined,
            favorites_only: favoritesOnly || undefined,
            rog_min: rogMin.trim() === '' ? undefined : Number(rogMin),
            rog_max: rogMax.trim() === '' ? undefined : Number(rogMax),
            rfd_rog_min: rfdRogMin.trim() === '' ? undefined : Number(rfdRogMin),
            rfd_rog_max: rfdRogMax.trim() === '' ? undefined : Number(rfdRogMax),
        }),
        refetchInterval: 10000,
    });

    // Fetch jobs for filter dropdown
    const { data: jobsData } = useQuery({
        queryKey: ['jobs'],
        queryFn: () => fetchJobs({ limit: 500, summary: true }),
    });

    // Favorite mutation
    const favoriteMutation = useMutation({
        mutationFn: ({ id, isFavorite }: { id: string; isFavorite: boolean }) =>
            toggleDesignFavorite(id, isFavorite),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['designs'] });
        },
    });

    const designs = designsData?.data.designs ?? [];
    const total = designsData?.data.total ?? 0;
    const jobs = jobsData?.data.jobs ?? [];

    const handleToggleFavorite = (design: Design) => {
        favoriteMutation.mutate({ id: design.id, isFavorite: !design.is_favorite });
    };

    const currentPage = Math.floor((filters.offset || 0) / (filters.limit || 50)) + 1;
    const totalPages = Math.ceil(total / (filters.limit || 50));

    return (
        <div className="min-h-screen bg-slate-950 p-6">
            {/* Header */}
            <header className="mb-8 flex justify-between items-center">
                <div>
                    <h1 className="text-4xl font-bold bg-gradient-to-r from-green-400 via-emerald-500 to-teal-500 bg-clip-text text-transparent">
                        Design Browser
                    </h1>
                    <p className="text-slate-400 mt-2">
                        Browse, filter, and manage your protein designs
                    </p>
                </div>
                <Link
                    to="/"
                    className="bg-slate-700 hover:bg-slate-600 text-white px-4 py-2 rounded-lg transition-colors"
                >
                    ← Back to Dashboard
                </Link>
            </header>

            {/* Filters */}
            <section className="mb-6 bg-slate-800/30 backdrop-blur-sm border border-slate-700 rounded-xl p-4">
                <div className="flex flex-wrap gap-4 items-center">
                    {/* Job Filter */}
                    <div className="flex items-center gap-2">
                        <label className="text-sm text-slate-400">Job:</label>
                        <select
                            value={selectedJobId}
                            onChange={(e) => setSelectedJobId(e.target.value)}
                            className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-white text-sm min-w-[200px]"
                        >
                            <option value="">All Jobs</option>
                            {jobs.map((job: Job) => (
                                <option key={job.id} value={job.id}>
                                    {job.name} ({job.status})
                                </option>
                            ))}
                        </select>
                    </div>

                    {/* Favorites Only */}
                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={favoritesOnly}
                            onChange={(e) => setFavoritesOnly(e.target.checked)}
                            className="w-4 h-4 rounded bg-slate-700 border-slate-600"
                        />
                        <span className="text-sm text-slate-400">Favorites only</span>
                    </label>

                    {/* RoG Filters */}
                    <div className="flex items-center gap-2">
                        <span className="text-sm text-slate-400">RoG:</span>
                        <input
                            type="number"
                            min="0"
                            step="0.1"
                            value={rogMin}
                            onChange={(e) => setRogMin(e.target.value)}
                            placeholder="min"
                            className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-white text-sm w-20 font-mono"
                        />
                        <span className="text-slate-500">–</span>
                        <input
                            type="number"
                            min="0"
                            step="0.1"
                            value={rogMax}
                            onChange={(e) => setRogMax(e.target.value)}
                            placeholder="max"
                            className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-white text-sm w-20 font-mono"
                        />
                    </div>

                    <div className="flex items-center gap-2">
                        <span className="text-sm text-slate-400">RFD RoG:</span>
                        <input
                            type="number"
                            min="0"
                            step="0.1"
                            value={rfdRogMin}
                            onChange={(e) => setRfdRogMin(e.target.value)}
                            placeholder="min"
                            className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-white text-sm w-20 font-mono"
                        />
                        <span className="text-slate-500">–</span>
                        <input
                            type="number"
                            min="0"
                            step="0.1"
                            value={rfdRogMax}
                            onChange={(e) => setRfdRogMax(e.target.value)}
                            placeholder="max"
                            className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-white text-sm w-20 font-mono"
                        />
                    </div>

                    {/* Results count */}
                    <div className="ml-auto text-sm text-slate-400">
                        {total} designs found
                    </div>
                </div>
            </section>

            {/* Designs Table */}
            <section className="bg-slate-800/30 backdrop-blur-sm border border-slate-700 rounded-xl overflow-hidden">
                {isLoading ? (
                    <div className="flex items-center justify-center py-12">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-500" />
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full">
                            <thead>
                                <tr className="border-b border-slate-700 bg-slate-800/50">
                                    <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">★</th>
                                    <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">Name</th>
                                    <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">Helices</th>
                                    <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">Strands</th>
                                    <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">RoG</th>
                                    <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">pLDDT</th>
                                    <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">pAE</th>
                                    <th className="text-left py-3 px-4 text-sm font-medium text-slate-400">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {designs.length === 0 ? (
                                    <tr>
                                        <td colSpan={8} className="py-12 text-center text-slate-500">
                                            No designs found. Run a binder design job to generate designs.
                                        </td>
                                    </tr>
                                ) : (
                                    designs.map((design) => (
                                        <tr
                                            key={design.id}
                                            className="border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors"
                                        >
                                            {/* Favorite */}
                                            <td className="py-3 px-4">
                                                <button
                                                    onClick={() => handleToggleFavorite(design)}
                                                    className={`text-xl transition-colors ${design.is_favorite
                                                        ? 'text-yellow-400 hover:text-yellow-300'
                                                        : 'text-slate-600 hover:text-yellow-400'
                                                        }`}
                                                >
                                                    {design.is_favorite ? '★' : '☆'}
                                                </button>
                                            </td>

                                            {/* Name */}
                                            <td className="py-3 px-4">
                                                <span className="text-white font-medium text-sm truncate max-w-[300px] block">
                                                    {design.name.split('_').slice(-4).join('_')}
                                                </span>
                                            </td>

                                            {/* Metrics */}
                                            <td className="py-3 px-4 text-slate-300">
                                                {design.num_helices ?? '-'}
                                            </td>
                                            <td className="py-3 px-4 text-slate-300">
                                                {design.num_strands ?? '-'}
                                            </td>
                                            <td className="py-3 px-4 text-slate-300">
                                                {design.rog?.toFixed(1) ?? '-'}
                                            </td>
                                            <td className="py-3 px-4">
                                                <MetricBadge
                                                    value={design.plddt_overall}
                                                    goodThreshold={80}
                                                    okThreshold={70}
                                                    higherIsBetter={true}
                                                />
                                            </td>
                                            <td className="py-3 px-4">
                                                <MetricBadge
                                                    value={design.pae_overall}
                                                    goodThreshold={5}
                                                    okThreshold={10}
                                                    higherIsBetter={false}
                                                />
                                            </td>

                                            {/* Actions */}
                                            <td className="py-3 px-4">
                                                <a
                                                    href={downloadDesignPdb(design.id)}
                                                    download
                                                    className="px-2 py-1 text-xs bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 rounded transition-colors"
                                                >
                                                    Download PDB
                                                </a>
                                            </td>
                                        </tr>
                                    ))
                                )}
                            </tbody>
                        </table>
                    </div>
                )}

                {/* Pagination */}
                {totalPages > 1 && (
                    <div className="flex items-center justify-between px-4 py-3 border-t border-slate-700">
                        <button
                            onClick={() => setFilters(f => ({ ...f, offset: Math.max(0, (f.offset || 0) - (f.limit || 50)) }))}
                            disabled={currentPage === 1}
                            className="px-3 py-1 bg-slate-700 text-white rounded disabled:opacity-50 disabled:cursor-not-allowed hover:bg-slate-600 transition-colors"
                        >
                            Previous
                        </button>
                        <span className="text-sm text-slate-400">
                            Page {currentPage} of {totalPages}
                        </span>
                        <button
                            onClick={() => setFilters(f => ({ ...f, offset: (f.offset || 0) + (f.limit || 50) }))}
                            disabled={currentPage === totalPages}
                            className="px-3 py-1 bg-slate-700 text-white rounded disabled:opacity-50 disabled:cursor-not-allowed hover:bg-slate-600 transition-colors"
                        >
                            Next
                        </button>
                    </div>
                )}
            </section>
        </div>
    );
}

// Helper component for metric badges
function MetricBadge({
    value,
    goodThreshold,
    okThreshold,
    higherIsBetter,
}: {
    value: number | null;
    goodThreshold: number;
    okThreshold: number;
    higherIsBetter: boolean;
}) {
    if (value === null) {
        return <span className="text-slate-500">-</span>;
    }

    const isGood = higherIsBetter
        ? value >= goodThreshold
        : value <= goodThreshold;
    const isOk = higherIsBetter
        ? value >= okThreshold
        : value <= okThreshold;

    const colorClass = isGood
        ? 'bg-green-500/20 text-green-400'
        : isOk
            ? 'bg-yellow-500/20 text-yellow-400'
            : 'bg-red-500/20 text-red-400';

    return (
        <span className={`px-2 py-0.5 rounded text-xs ${colorClass}`}>
            {value.toFixed(1)}
        </span>
    );
}
