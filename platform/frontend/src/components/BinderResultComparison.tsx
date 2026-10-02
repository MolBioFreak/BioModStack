import { lazy, Suspense, useEffect, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { fetchDesigns } from '../lib/api';

const BatchComparePane = lazy(() => import('./BatchComparePane').then(m => ({ default: m.BatchComparePane })));
const DesignComparePane = lazy(() => import('./DesignComparePane').then(m => ({ default: m.DesignComparePane })));
export interface BinderResultComparisonProps {
    jobId: string;
    jobIds?: string[];
    selectedDesignIds?: string[];
    launchContextId?: string | null;
}
type Selection = { jobs: string[]; designs: string[]; view: 'jobs' | 'designs' };
const ids = (value: unknown): value is string[] => Array.isArray(value) && value.every(id => typeof id === 'string');

/** Demand-mounted shared comparison; native observations never become synthetic Designs. */
export function BinderResultComparison(props: BinderResultComparisonProps) {
    return <Comparison key={`${props.launchContextId ?? ''}:${props.jobId}`} {...props} />;
}
function Comparison({ jobId, jobIds, selectedDesignIds, launchContextId }: BinderResultComparisonProps) {
    const storageKey = `binder-result-comparison:${launchContextId ?? ''}:${jobId}`;
    const [selection, setSelection] = useState<Selection>(() => {
        let saved: Partial<Selection> = {};
        try { saved = JSON.parse(localStorage.getItem(storageKey) ?? '{}') ?? {}; } catch { /* Storage is optional. */ }
        return {
            jobs: jobIds ?? (ids(saved.jobs) ? saved.jobs : [jobId]),
            designs: selectedDesignIds ?? (ids(saved.designs) ? saved.designs : []),
            view: saved.view === 'designs' ? 'designs' : 'jobs',
        };
    });
    const [open, setOpen] = useState(false);
    // Explicit family selections replace the old comparison, including while it is open.
    const jobSignature = JSON.stringify(jobIds);
    const designSignature = JSON.stringify(selectedDesignIds);
    useEffect(() => {
        if (jobIds) setSelection(previous => ({ ...previous, jobs: jobIds }));
    }, [jobSignature]); // Array identity is not a new selection.
    useEffect(() => {
        if (selectedDesignIds) setSelection(previous => ({ ...previous, designs: selectedDesignIds }));
    }, [designSignature]);
    useEffect(() => {
        try { localStorage.setItem(storageKey, JSON.stringify(selection)); } catch { /* Still usable without persistence. */ }
    }, [storageKey, selection]);
    return <section aria-label="Binder result comparison" className="rounded-lg border border-[var(--border-color)] p-3">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Return to native results' : 'Compare result sets'}</button>
        {open && <>
            <div className="flex gap-3 py-3">
                <button type="button" aria-pressed={selection.view === 'jobs'} onClick={() => setSelection({ ...selection, view: 'jobs' })}>Jobs and native metrics</button>
                <button type="button" aria-pressed={selection.view === 'designs'} onClick={() => setSelection({ ...selection, view: 'designs' })}>Candidate confidence</button>
            </div>
            <Suspense fallback={<p>Loading comparison…</p>}>
                {selection.view === 'jobs'
                    ? <BatchComparePane nativeMetrics launchContextId={launchContextId} jobIds={selection.jobs} onJobIdsChange={jobs => setSelection({ ...selection, jobs })} />
                    : <CandidateComparison selection={selection} onChange={designs => setSelection({ ...selection, designs })} />}
            </Suspense>
        </>}
    </section>;
}
function CandidateComparison({ selection, onChange }: { selection: Selection; onChange: (ids: string[]) => void }) {
    const query = useInfiniteQuery({
        queryKey: ['binder-comparison-designs', selection.jobs],
        initialPageParam: 0,
        queryFn: async ({ pageParam }) => Promise.all(selection.jobs.map(job_id => fetchDesigns({
            job_id, include_children: false, limit: 100, offset: pageParam, include_summary: false,
        }).then(response => response.data))),
        getNextPageParam: (last, _pages, offset) => last.some(page => offset + page.designs.length < page.total) ? offset + 100 : undefined,
    });
    const designs = [...new Map((query.data?.pages.flatMap(page => page.flatMap(result => result.designs)) ?? []).map(design => [design.id, design])).values()];
    return <>
        <p>Only published Designs are selectable. Native metrics-only observations are not confidence profiles. Descendants are included only when their job is explicitly selected.</p>
        {query.isError && <p role="alert">Could not load comparison candidates.</p>}
        {query.isPending ? <p>Loading candidates…</p> : <DesignComparePane designs={designs} selectedDesignIds={selection.designs} onSelectedDesignIdsChange={onChange} />}
        {query.hasNextPage && <button type="button" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>Load more comparison candidates</button>}
    </>;
}
