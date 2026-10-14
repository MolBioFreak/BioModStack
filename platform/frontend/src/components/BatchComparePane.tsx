import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchJobs, fetchBatchAnalytics, fetchJobDesignMetrics } from '../lib/api';
import { ScientificAnalytics } from './ScientificAnalytics';
import type { ScientificPoint } from '../lib/scientificAnalytics';
import type { Job } from '../lib/api';
import { isNgsJob } from '../lib/ngsResultRouting';
const BC2Results = lazy(() => import('./BindCraft2JobResults').then(m => ({ default: m.BindCraft2JobResults })));
import { isNativeBinderGeneration } from '../lib/nativeBinderResults';
import { fetchJobById } from '../lib/api';
const NativeResults = lazy(() => import('./NativeBinderGenerationResults').then(m => ({ default: m.NativeBinderGenerationResults })));

function NativeObservations({ jobId, launchContextId }: { jobId: string; launchContextId?: string | null }) {
    const [open, setOpen] = useState(false);
    const job = useQuery({ queryKey: ['job', jobId], queryFn: () => fetchJobById(jobId), enabled: open });
    return <div className="rounded border border-[var(--border-color)] p-2">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>Native observations · {jobId}</button>
        {open && <Suspense fallback={<p>Loading native observations…</p>}>
            {job.isError ? <p>Native job could not be loaded.</p> : job.isPending ? <p>Loading job…</p>
                : job.data.data.model_id === 'bindcraft2' ? <BC2Results jobId={jobId} launchContextId={launchContextId} /> : isNativeBinderGeneration(job.data.data) ? <NativeResults jobId={jobId} launchContextId={launchContextId} />
                    : <p>Use canonical metrics or candidate confidence for this result type.</p>}
        </Suspense>}
    </div>;
}

interface BatchComparePaneProps {
    initialJobId?: string;
    jobIds?: string[];
    onJobIdsChange?: (ids: string[]) => void;
    nativeMetrics?: boolean;
    launchContextId?: string | null;
}


export function BatchComparePane({ initialJobId, jobIds, onJobIdsChange, nativeMetrics = false, launchContextId }: BatchComparePaneProps) {
    const [localIds, setLocalIds] = useState<string[]>(initialJobId ? [initialJobId] : []);
    const selectedJobIds = jobIds ?? localIds;
    const setSelectedJobIds = (ids: string[]) => { setLocalIds(ids); onJobIdsChange?.(ids); };

    // Fetch all jobs for the selector
    const { data: jobsData } = useQuery({
        queryKey: ['jobs', 'comparison', nativeMetrics],
        queryFn: ({ signal }) => fetchJobs({ limit: 500, summary: true, include_children: nativeMetrics }, undefined, signal),
    });
    const jobs = useMemo(
        () => (jobsData?.data.jobs ?? []).filter((j: Job) => !isNgsJob(j)),
        [jobsData]
    );

    useEffect(() => {
        // A bounded job selector must not erase explicit or historical selections.
        if (jobIds !== undefined || !jobsData) return;
        const filtered = localIds.filter(id => !jobsData.data.jobs.some(j => j.id === id && isNgsJob(j)));
        if (filtered.length !== localIds.length) setLocalIds(filtered);
    }, [jobsData, jobIds, localIds]);

    // Fetch batch analytics for selected jobs
    const { data: batchData, isLoading } = useQuery({
        queryKey: ['batch', selectedJobIds],
        queryFn: () => fetchBatchAnalytics(selectedJobIds),
        enabled: selectedJobIds.length > 0
    });

    const comparison = batchData?.data;
    const useNative = nativeMetrics || !!comparison?.scientific_cohorts?.length;
    const points = useQuery({
        queryKey: ['comparison-native-points', selectedJobIds],
        queryFn: async () => (await Promise.all(selectedJobIds.map(id => fetchJobDesignMetrics(id, false))))
            .flatMap(response => response.data).filter((point): point is ScientificPoint => point.contract_revision === 1),
        enabled: useNative && selectedJobIds.length > 0,
    });

    const toggleJob = (id: string) => {
        setSelectedJobIds(selectedJobIds.includes(id)
            ? selectedJobIds.filter(x => x !== id) : [...selectedJobIds, id]);
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
            total_designs: job?.design_count ?? null
        };
    }) : [];

    return (
        <div className="flex min-w-0 flex-col lg:h-[800px] lg:flex-row">
            {/* Sidebar: Job Selector */}
            <div className="flex max-h-64 min-w-0 flex-col border-b border-slate-800 bg-slate-900/30 lg:max-h-none lg:w-80 lg:shrink-0 lg:border-b-0 lg:border-r">
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
                            <button
                                type="button"
                                key={job.id}
                                aria-pressed={selectedJobIds.includes(job.id)}
                                onClick={() => toggleJob(job.id)}
                                className={`block w-full text-left p-3 rounded-lg mb-1 cursor-pointer transition-colors border focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 ${selectedJobIds.includes(job.id)
                                    ? 'bg-blue-500/10 border-blue-500/50'
                                    : 'bg-transparent border-transparent hover:bg-slate-800'
                                    }`}
                            >
                                <span className="flex items-start justify-between">
                                    <span className={`min-w-0 break-words text-sm font-medium ${selectedJobIds.includes(job.id) ? 'text-blue-400' : 'text-slate-300'}`}>
                                        {job.name}
                                    </span>
                                    {selectedJobIds.includes(job.id) && (
                                        <span aria-hidden="true" className="w-2 h-2 shrink-0 rounded-full bg-blue-500 mt-1.5" />
                                    )}
                                </span>
                                <span className="flex items-center gap-2 mt-1 text-xs text-slate-500">
                                    <span>{job.mode}</span>
                                    <span>•</span>
                                    <span>{new Date(job.created_at).toLocaleDateString()}</span>
                                </span>
                            </button>
                        ))
                    )}
                </div>
            </div>

            {/* Main Content: Comparison */}
            <div className="min-w-0 flex-1 overflow-y-auto p-4 lg:p-6">
                {selectedJobIds.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center text-slate-500">
                        <div className="text-4xl mb-4">📊</div>
                        <p>Select jobs to begin comparison</p>
                    </div>
                ) : isLoading ? (
                    <div className="h-full flex items-center justify-center">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
                    </div>
                ) : comparison ? (
                    <div className="space-y-8">
                        {useNative && <>
                            <p>Native measurements stay in their producer cohorts; parent and descendant jobs are not pooled. Missing measurements are not zero. Open native observations to compare original record sets without turning them into Designs.</p>
                            {selectedJobIds.map(id => <NativeObservations key={id} jobId={id} launchContextId={launchContextId} />)}
                            {points.isError ? <p role="alert">Native comparison measurements could not be loaded.</p>
                                : points.isPending ? <p>Loading native measurements…</p>
                                : points.data.length ? <>
                                    <ScientificAnalytics points={points.data} cohorts={comparison.scientific_cohorts ?? []} />
                                    <details><summary>Retained native records and producer scopes</summary><div className="overflow-auto"><table className="text-left text-xs"><thead><tr><th>Design / owning Job</th><th>Producer version / scope / unit</th><th>Availability</th></tr></thead><tbody>
                                        {points.data.map(point => <tr key={`${point.source_job_id}:${point.id}`}><td className="p-2">{point.name} ({point.id})<br />{point.source_job_id}</td>
                                            <td>{[...new Set(Object.values(point.metric_descriptors).map(d => `${d.producer_version} / ${d.scope} / ${d.unit}`))].join('; ') || 'Unavailable'}</td>
                                            <td>{point.publication_state?.reason_code ?? (Object.keys(point.metric_states).length ? `${Object.values(point.metric_states).filter(s => s.state === 'ok').length} observed measurements` : 'Measurements unavailable')}</td></tr>)}
                                    </tbody></table></div></details>
                                </>
                                : <p>No canonical native measurements reported for this selection.</p>}
                        </>}
                        {!useNative && <>
                        {/* Summary Table */}
                        <div className="bg-slate-800/50 rounded-xl p-6 border border-slate-700/50">
                            <h3 className="text-lg font-semibold text-white mb-4">Summary Statistics</h3>
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead>
                                        <tr className="border-b border-slate-700 text-slate-400">
                                            <th className="px-4 py-2 text-left">Job</th>
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
                                                <td className="px-4 py-3 text-center font-mono">{stat.avg_plddt?.toFixed(1) || '—'}</td>
                                                <td className="px-4 py-3 text-center font-mono">{stat.avg_pae?.toFixed(1) || '—'}</td>
                                                <td className="px-4 py-3 text-center font-mono">{stat.avg_ptm?.toFixed(2) || '—'}</td>
                                                <td className="px-4 py-3 text-center font-mono">{stat.total_designs ?? '—'}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        </>}
                    </div>
                ) : (
                    <div className="text-center text-red-400">Failed to load comparison data</div>
                )}
            </div>
        </div>
    );
}
