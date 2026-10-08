import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from 'react';
import { CatalogReadTable, type CatalogSignalSelection, type CatalogReadAction } from './CatalogReadTable';
import {
    fetchAlignmentDerivedStatus, retryAlignmentDerivedProduct, retryAlignmentDeliveryCache,
    type AlignmentDerivedStatus as Status, type DerivedProductKind, type DerivedProductState,
} from '../../lib/ngsDerivedProducts';

const messages = {
    source_invalid: 'Source validation failed. Reopen the accepted native result before preparing this product again.',
    resource_limit: 'Preparation exceeded its resource allocation. Resolve capacity before retrying.',
    cancelled: 'Preparation was stopped. Retry explicitly when ready.',
    infrastructure_failed: 'Preparation lost its worker or lease. Retry explicitly to recover.',
    publication_failed: 'Prepared files could not be published. Resolve storage access before retrying.',
    integrity_mismatch: 'Prepared files did not match their source. No derived result was published.',
};
function label(row: DerivedProductState): string {
    if (row.state === 'unavailable') return row.reason === 'unsupported_source' ? 'Not supported for this source' : 'Not requested';
    if (row.state === 'requested') return row.blocked_on === 'catalog' ? 'Waiting for catalog' : 'Queued';
    if (row.state === 'running') return 'Preparing';
    return row.state === 'ready' ? 'Ready' : 'Failed';
}

/** Status is observational; only the explicit per-product action retries work. */
export function AlignmentDerivedStatus({ jobId, sessionId, signal, onReadAction, onSelectionIntent, selectedReadId }: { jobId: string; sessionId: string; signal?: CatalogSignalSelection; onReadAction?: CatalogReadAction; onSelectionIntent?: () => (() => boolean); selectedReadId?: string | null }) {
    const queryClient = useQueryClient();
    const [status, setStatus] = useState<Status | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [pending, setPending] = useState<DerivedProductKind | null>(null);
    const [revision, setRevision] = useState(0);
    const generation = useRef(0);
    const retryLock = useRef(false);
    useEffect(() => {
        const token = ++generation.current;
        const controller = new AbortController();
        let timer: ReturnType<typeof setTimeout> | undefined;
        setStatus((current) => current?.job_id === jobId && current.session_id === sessionId ? current : null);
        setError(null);
        const refresh = async () => {
            try {
                const next = await fetchAlignmentDerivedStatus(jobId, sessionId, controller.signal);
                if (token !== generation.current) return;
                setStatus(next);
                queryClient.setQueryData(["ngs-selected-derived", jobId, sessionId], next);
                if ([next.catalog, next.preview].some((row) => row.state === 'requested' || row.state === 'running')) {
                    timer = setTimeout(() => { void refresh(); }, 2500);
                }
            } catch (reason) {
                if (!controller.signal.aborted && token === generation.current) {
                    setError(reason instanceof Error ? reason.message : 'Product state could not be loaded.');
                }
            }
        };
        void refresh();
        return () => { generation.current += 1; controller.abort(); if (timer) clearTimeout(timer); };
    }, [jobId, sessionId, revision, queryClient]);

    const retry = async (kind: DerivedProductKind, deliveryCache = false) => {
        const row = status?.[kind];
        if (retryLock.current || !row) return;
        if (deliveryCache ? row.state !== 'ready' : row.state !== 'failed' || !row.retryable) return;
        if (row.state !== 'ready' && row.state !== 'failed') return;
        retryLock.current = true;
        setPending(kind);
        setError(null);
        const token = generation.current;
        try {
            const next = await (deliveryCache ? retryAlignmentDeliveryCache : retryAlignmentDerivedProduct)(jobId, sessionId, kind, row.request_id);
            if (token !== generation.current) return;
            setStatus(next);
            queryClient.setQueryData(["ngs-selected-derived", jobId, sessionId], next);
            setRevision((value) => value + 1);
        } catch (reason) {
            if (token === generation.current) setError(reason instanceof Error ? reason.message : 'Retry was not accepted.');
        } finally {
            retryLock.current = false;
            if (token === generation.current) setPending(null);
        }
    };

    return (
        <section aria-label="Read preparation" className="space-y-2 rounded border border-[var(--border-primary)] p-3 text-xs">
            <div className="font-semibold">Read preparation</div>
            <p className="text-[var(--text-secondary)]">These optional products do not change the scientific result or its original artifacts.</p>
            {!status && !error && <p role="status">Loading product states…</p>}
            {error && <div role="alert" className="space-y-1 text-amber-200">
                <p>{error}</p>
                <button type="button" disabled={pending !== null} onClick={() => setRevision((value) => value + 1)} className="rounded border px-2 py-1 disabled:opacity-50">Refresh status</button>
            </div>}
            {status && <div className="grid min-w-0 gap-2 sm:grid-cols-2">
                {(['catalog', 'preview'] as const).map((kind) => {
                    const row = status[kind];
                    return <div key={kind} className="min-w-0 rounded border border-[var(--border-primary)] p-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="font-medium">{kind === 'catalog' ? 'Complete read catalog' : 'Optional alignment preview'}</span>
                            <span role="status">{label(row)}</span>
                        </div>
                        {row.state === 'unavailable' && row.reason === 'request_missing' && <p className="mt-1 text-[var(--text-secondary)]">No current request is recorded. Opening this result does not start preparation.</p>}
                        {row.state === 'failed' && <>
                            <p className="mt-1 text-amber-200">{messages[row.code]}</p>
                            {kind === 'preview' && status.catalog.state === 'ready' && <p className="mt-1">The complete catalog remains ready.</p>}
                            {row.retryable && <button type="button" disabled={pending !== null} onClick={() => { void retry(kind); }} className="mt-2 rounded border px-2 py-1 disabled:opacity-50">
                                {pending === kind ? 'Requesting…' : `Retry ${kind}`}
                            </button>}
                        </>}
                        {row.state !== 'unavailable' && <details className="mt-2 text-[var(--text-secondary)]">
                            <summary className="cursor-pointer">Diagnostics</summary>
                            <dl className="mt-1 break-all">
                                <dt>Request</dt><dd>{row.request_id}</dd>
                                <dt>Attempts / explicit retries</dt><dd>{row.attempt_count} / {row.manual_retry_count}</dd>
                                {row.state === 'failed' && <><dt>Error</dt><dd>{row.code}</dd></>}
                            </dl>
                            {row.state === 'ready' && <>
                                <p className="mt-2">Delivery errors only: retry the verified cache without rebuilding this product or rerunning analysis. Active readers may need to close first.</p>
                                <button type="button" disabled={pending !== null} onClick={() => { void retry(kind, true); }} className="mt-1 rounded border px-2 py-1 disabled:opacity-50">
                                    {pending === kind ? 'Retrying…' : 'Retry delivery cache'}
                                </button>
                            </>}
                        </details>}
                    </div>;
                })}
            </div>}
            {status?.catalog.state === "ready" && <CatalogReadTable key={`${jobId}:${sessionId}:${status.catalog.authority_sha256}`} jobId={jobId} sessionId={sessionId} authority={status.catalog.authority_sha256} signal={signal} onReadAction={onReadAction} onSelectionIntent={onSelectionIntent} selectedReadId={selectedReadId} />}
        </section>
    );
}
