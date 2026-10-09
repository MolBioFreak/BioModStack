import { useEffect, useRef, useState } from 'react';
import { ExecutionPlanApproval, type ExecutionPlanPreview } from '../ExecutionPlanApproval';

/** Retain domain snapshots across review/retry, not across edits or navigation. */
export function useOntDomainSubmission<T extends { execution_target_id?: string | null }>(key: string) {
    const current = useRef({ key, generation: 0 });
    if (current.current.key !== key) current.current = { key, generation: current.current.generation + 1 };
    const retained = useRef<{ key: string; request: T; previews?: Record<string, ExecutionPlanPreview> } | null>(null);
    const [review, setReview] = useState<{ key: string; previews: Record<string, ExecutionPlanPreview>; finish: (yes: boolean) => void } | null>(null);
    useEffect(() => {
        if (retained.current?.key !== key) retained.current = null;
        if (review && review.key !== key) { review.finish(false); setReview(null); }
    }, [key, review]);
    useEffect(() => () => { current.current = { key: '', generation: current.current.generation + 1 }; }, []);
    useEffect(() => () => { review?.finish(false); }, [review]);

    async function submit<R>(build: () => Promise<T>, prepare: (request: T) => Promise<{ request: T; previews: Record<string, ExecutionPlanPreview> }>, commit: (request: T, approvals: Record<string, string>) => Promise<R>) {
        const search = window.location.search;
        const generation = current.current.generation;
        const isCurrent = () => current.current.key === key && current.current.generation === generation && window.location.search === search;
        let selection = retained.current?.key === key ? retained.current : null;
        if (!selection) {
            const request = await build();
            if (!isCurrent()) return null;
            selection = { key, request };
            retained.current = selection;
        }
        if (selection.request.execution_target_id) {
            if (!selection.previews) {
                const prepared = await prepare(selection.request);
                if (!isCurrent()) return null;
                selection = { key, ...prepared };
                retained.current = selection;
            }
            const previews = selection.previews!;
            const approved = await new Promise<boolean>(finish => setReview({ key, previews, finish }));
            setReview(previous => previous?.key === key ? null : previous);
            if (!approved || !isCurrent()) return null;
        }
        const response = await commit(selection.request, Object.fromEntries(Object.entries(selection.previews || {}).map(([id, preview]) => [id, preview.approval_digest])));
        return isCurrent() ? response : null;
    }
    return { submit, review: review?.key === key ? <ExecutionPlanApproval previews={review.previews} finish={review.finish} /> : null };
}
