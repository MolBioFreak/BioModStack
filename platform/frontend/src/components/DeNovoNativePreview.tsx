import { useEffect, useRef, useState } from 'react';
import { prepareJobSubmission, previewJobExecutionPlan, type Job } from '../lib/api';
import type { ExecutionPlanPreview } from './ExecutionPlanApproval';
type NativePreview = ExecutionPlanPreview & { native_review?: { model_id: string; requested: Record<string, unknown>; native_request: Record<string, unknown>; native_overlay?: Record<string, unknown> } };

function rows(value: unknown, path = ''): Array<[string, string]> {
    if (value !== null && typeof value === 'object') {
        const entries = Object.entries(value);
        return entries.length ? entries.flatMap(([key, child]) => rows(child, path ? `${path}.${key}` : key)) : [[path, Array.isArray(value) ? '[]' : '{}']];
    }
    return [[path, value === null ? 'null' : String(value)]];
}

type PreviewAttempt = { number: number; valid: boolean; preparing: boolean; expectedKey?: string };
type NativePreviewProps = {
    request: Partial<Job>;
    /** Return the latest serialized request AFTER capturing mounted molecular documents. */
    prepareRequest?: () => Promise<Partial<Job>>;
    /** Monotonic document edit generation, including edits before the source path changes. */
    documentGeneration?: number | string;
};
const withoutOperations = ({ sequence_design: _sequence, ...request }: Partial<Job>) => request;

/** Explicit, nonexecuting inspection. Its result never becomes a launch gate. */
export function DeNovoNativePreview({ request, prepareRequest, documentGeneration }: NativePreviewProps) {
    // Follow-on authoring is independent of the native generator's settings.
    const nativeRequest = withoutOperations(request);
    const key = JSON.stringify(nativeRequest);
    const generation = useRef({ key, documentGeneration, number: 0 });
    const active = useRef<PreviewAttempt | undefined>(undefined);
    if (generation.current.key !== key || generation.current.documentGeneration !== documentGeneration) {
        const previous = generation.current;
        generation.current = { key, documentGeneration, number: previous.number + 1 };
        const attempt = active.current;
        if (attempt?.valid) {
            // Capture can publish its own source update either before or after it
            // returns. Only that exact prepared payload may rebase a sent preview.
            const sameDocument = previous.documentGeneration === documentGeneration;
            if (sameDocument && (attempt.preparing || attempt.expectedKey === key)) {
                attempt.number = generation.current.number;
                if (!attempt.preparing) attempt.expectedKey = undefined;
            } else attempt.valid = false;
        }
    }
    const [result, setResult] = useState<{ attempt: PreviewAttempt; preview?: NativePreview; error?: string }>();
    const [pending, setPending] = useState<PreviewAttempt>();
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; if (active.current) active.current.valid = false; };
    }, []);
    const inspect = async () => {
        if (active.current) active.current.valid = false;
        const attempt: PreviewAttempt = { number: generation.current.number, valid: true, preparing: true };
        active.current = attempt;
        setPending(attempt);
        try {
            // Do not combine a post-export source with pre-export settings. The
            // child serializer owns the entire current snapshot, including edits
            // made while capture was pending. Manual/source-free callers omit it.
            const prepared = withoutOperations(prepareRequest ? await prepareRequest() : request);
            if (!mounted.current || !attempt.valid) return;
            attempt.preparing = false;
            attempt.number = generation.current.number;
            const preparedKey = JSON.stringify(prepared);
            attempt.expectedKey = preparedKey !== generation.current.key ? preparedKey : undefined;
            const response = await previewJobExecutionPlan(prepareJobSubmission(prepared));
            if (mounted.current && attempt.valid && attempt.number === generation.current.number) setResult({ attempt, preview: response.data });
        } catch (cause) {
            if (mounted.current && attempt.valid && attempt.number === generation.current.number) setResult({ attempt, error: cause instanceof Error ? cause.message : String(cause) });
        } finally { if (mounted.current) setPending(current => current === attempt ? undefined : current); }
    };
    const current = result?.attempt.valid ? result : undefined;
    const preview = current?.preview;
    const requested = new Map(rows(preview?.native_review?.requested ?? preview?.plan.requested_json));
    const effective = new Map(rows(preview?.native_review?.native_request ?? preview?.plan.effective_json));
    return <section aria-label="Native effective settings">
        <button type="button" disabled={!!pending?.valid} onClick={() => void inspect()}>Preview settings</button>
        <p className="text-xs">Does not run a model.</p>
        {current?.error && <p role="status">Preview unavailable: {current.error}</p>}
        {result && !current && <p role="status">Settings changed. Refresh preview.</p>}
        {preview && <>
            {(preview.blockers ?? []).map((row, index) => <p key={index}>{row.reason}</p>)}
            {(preview.deferred_preparation ?? []).map(text => <p key={text}>Deferred preparation: {text}</p>)}
            <details><summary>Requested and effective native values</summary><dl>
                {[...new Set([...requested.keys(), ...effective.keys()])].sort().map(path => <div key={path} className="mb-2">
                    <dt>{path}</dt><dd>Requested: {requested.get(path) ?? '(omitted)'}</dd><dd>Effective: {effective.get(path) ?? '(not projected)'}</dd>
                </div>)}
            </dl></details>
            {preview.native_review?.native_overlay && <details><summary>Compiled native overlay</summary><dl>{rows(preview.native_review.native_overlay).map(([path, value]) => <div key={path}><dt>{path}</dt><dd>{value}</dd></div>)}</dl></details>}
        </>}
    </section>;
}
