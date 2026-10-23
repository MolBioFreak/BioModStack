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

/** Explicit, nonexecuting inspection. Its result never becomes a launch gate. */
export function DeNovoNativePreview({ request }: { request: Partial<Job> }) {
    // Follow-on authoring is independent of the native generator's settings.
    const { sequence_design: _sequence, ...nativeRequest } = request;
    const key = JSON.stringify(nativeRequest);
    const generation = useRef({ key, number: 0 });
    if (generation.current.key !== key) generation.current = { key, number: generation.current.number + 1 };
    const [result, setResult] = useState<{ generation: number; preview?: NativePreview; error?: string }>();
    const [pending, setPending] = useState<number>();
    const mounted = useRef(true);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const inspect = async () => {
        const number = generation.current.number;
        setPending(number);
        try {
            const response = await previewJobExecutionPlan(prepareJobSubmission(nativeRequest));
            if (mounted.current && generation.current.number === number) setResult({ generation: number, preview: response.data });
        } catch (cause) {
            if (mounted.current && generation.current.number === number) setResult({ generation: number, error: cause instanceof Error ? cause.message : String(cause) });
        } finally { if (mounted.current) setPending(current => current === number ? undefined : current); }
    };
    const current = result?.generation === generation.current.number ? result : undefined;
    const preview = current?.preview;
    const requested = new Map(rows(preview?.native_review?.requested ?? preview?.plan.requested_json));
    const effective = new Map(rows(preview?.native_review?.native_request ?? preview?.plan.effective_json));
    return <section aria-label="Native effective settings">
        <button type="button" disabled={pending === generation.current.number} onClick={() => void inspect()}>Preview effective native settings</button>
        <p className="text-xs">Uses the existing server compiler. Does not run inference, contact scientific providers, or change the requested settings.</p>
        {current?.error && <p role="status">Preview unavailable: {current.error}. Your request is retained.</p>}
        {result && !current && <p role="status">Request changed; the earlier preview is not current.</p>}
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
