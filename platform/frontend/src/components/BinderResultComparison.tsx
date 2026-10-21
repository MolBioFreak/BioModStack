import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { useInfiniteQuery, useQueries } from '@tanstack/react-query';
import { fetchDesignById, fetchDesigns } from '../lib/api';
import { readBinderCandidateDocuments, type NativeCandidateSource } from '../lib/binderContinuation';
import type { CandidateDocuments } from '../lib/binderDiagnosticSelection';

const BatchComparePane = lazy(() => import('./BatchComparePane').then(m => ({ default: m.BatchComparePane })));
const DesignComparePane = lazy(() => import('./DesignComparePane').then(m => ({ default: m.DesignComparePane })));
const NativeBinderComparison = lazy(() => import('./NativeBinderComparison').then(m => ({ default: m.NativeBinderComparison })));
export interface BinderResultComparisonProps {
    jobId: string;
    jobIds?: string[];
    selectedDesignIds?: string[];
    selectedNativeSources?: NativeCandidateSource[];
    candidateDocuments?: CandidateDocuments;
    launchContextId?: string | null;
}
type Selection = { jobs: string[]; designs: string[]; native: NativeCandidateSource[]; documents: CandidateDocuments; view: 'jobs' | 'designs' };
const ids = (value: unknown): value is string[] => Array.isArray(value) && value.every(id => typeof id === 'string');
const nativeSources = (value: unknown): value is NativeCandidateSource[] => Array.isArray(value)
    && value.every(source => source && typeof source.job_id === 'string' && typeof source.artifact_id === 'string');

/** Demand-mounted shared comparison; observations never acquire synthetic Design IDs. */
export function BinderResultComparison(props: BinderResultComparisonProps) {
    return <Comparison key={`${props.launchContextId ?? ''}:${props.jobId}`} {...props} />;
}
function Comparison({ jobId, jobIds, selectedDesignIds, selectedNativeSources, candidateDocuments, launchContextId }: BinderResultComparisonProps) {
    const storageKey = `binder-result-comparison:${launchContextId ?? ''}:${jobId}`;
    const [selection, setSelection] = useState<Selection>(() => {
        let saved: Partial<Selection> = {};
        try { saved = JSON.parse(localStorage.getItem(storageKey) ?? '{}') ?? {}; } catch { /* Optional persistence. */ }
        return {
            jobs: jobIds ?? (ids(saved.jobs) ? saved.jobs : [jobId]),
            designs: selectedDesignIds ?? (ids(saved.designs) ? saved.designs : []),
            native: selectedNativeSources ?? (nativeSources(saved.native) ? saved.native : []),
            documents: candidateDocuments ?? saved.documents ?? readBinderCandidateDocuments(jobId),
            view: saved.view === 'jobs' || saved.view === 'designs' ? saved.view
                : selectedDesignIds?.length || selectedNativeSources?.length ? 'designs' : 'jobs',
        };
    });
    const [open, setOpen] = useState(false);
    const jobSignature = JSON.stringify(jobIds);
    const designSignature = JSON.stringify(selectedDesignIds);
    const nativeSignature = JSON.stringify(selectedNativeSources);
    const documentSignature = JSON.stringify(candidateDocuments);
    const priorJobSignature = useRef(jobSignature);
    useEffect(() => {
        if (jobIds) {
            const changed = jobSignature !== priorJobSignature.current;
            setSelection(previous => ({ ...previous, jobs: jobIds, view: changed ? 'jobs' : previous.view }));
            if (changed) setOpen(true);
        }
        priorJobSignature.current = jobSignature;
    }, [jobSignature]);
    useEffect(() => {
        if (selectedDesignIds) setSelection(previous => ({ ...previous, designs: selectedDesignIds }));
    }, [designSignature]);
    useEffect(() => {
        if (selectedNativeSources) setSelection(previous => ({ ...previous, native: selectedNativeSources }));
    }, [nativeSignature]);
    useEffect(() => {
        if (candidateDocuments) setSelection(previous => ({ ...previous, documents: candidateDocuments }));
    }, [documentSignature]);
    useEffect(() => {
        try { localStorage.setItem(storageKey, JSON.stringify(selection)); } catch { /* Still usable without storage. */ }
    }, [storageKey, selection]);
    return <section aria-label="Binder result comparison" className="rounded-lg border border-[var(--border-primary)] p-3">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Return to results' : 'Compare result sets'}</button>
        {open && <>
            <div className="flex gap-3 py-3">
                <button type="button" aria-pressed={selection.view === 'jobs'} onClick={() => setSelection({ ...selection, view: 'jobs' })}>Runs</button>
                <button type="button" aria-pressed={selection.view === 'designs'} onClick={() => setSelection({ ...selection, view: 'designs' })}>Candidates</button>
            </div>
            <Suspense fallback={<p>Loading comparison…</p>}>
                {selection.view === 'jobs'
                    ? <BatchComparePane nativeMetrics launchContextId={launchContextId} jobIds={selection.jobs} onJobIdsChange={jobs => setSelection({ ...selection, jobs })} />
                    : <CandidateComparison selection={selection} launchContextId={launchContextId} onChange={designs => setSelection({ ...selection, designs })} />}
            </Suspense>
        </>}
    </section>;
}
function CandidateComparison({ selection, onChange, launchContextId }: { selection: Selection; onChange: (ids: string[]) => void; launchContextId?: string | null }) {
    const query = useInfiniteQuery({
        queryKey: ['binder-comparison-designs', selection.jobs], initialPageParam: 0,
        queryFn: async ({ pageParam }) => Promise.all(selection.jobs.map(job_id => fetchDesigns({
            job_id, include_children: false, limit: 100, offset: pageParam, include_summary: false,
        }).then(response => response.data))),
        getNextPageParam: (last, _pages, offset) => last.some(page => offset + page.designs.length < page.total) ? offset + 100 : undefined,
    });
    const exact = useQueries({ queries: selection.designs.map(id => ({ queryKey: ['binder-comparison-exact', id, selection.documents[id]], queryFn: () => fetchDesignById(id, undefined, selection.documents[id]), staleTime: 60000 })) });
    const designs = [...new Map([...(query.data?.pages.flatMap(page => page.flatMap(result => result.designs)) ?? []),
        ...exact.flatMap(result => result.data ? [result.data.data] : [])].map(design => [design.id, design])).values()];
    // Explicit descendants are separate sources, never a recursive parent cohort.
    const selectedOwners = designs.filter(design => selection.designs.includes(design.id)).map(design => design.job_id);
    const jobs = [...new Set([...selection.jobs, ...selectedOwners, ...selection.native.map(source => source.job_id)])];
    return <>
        <NativeBinderComparison jobIds={jobs} selectedDesignIds={selection.designs} selectedNativeSources={selection.native} launchContextId={launchContextId} />
        {(query.isError || exact.some(result => result.isError)) && <p role="status">Some comparison candidates could not be loaded.</p>}
        {query.isPending && <p>Loading candidates…</p>}
        {(!!designs.length || !!selection.native.length) && <DesignComparePane designs={designs} selectedDesignIds={selection.designs} onSelectedDesignIdsChange={onChange} candidateDocuments={selection.documents} selectedNativeSources={selection.native} />}
        {query.hasNextPage && <button type="button" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>Load more candidates</button>}
    </>;
}
