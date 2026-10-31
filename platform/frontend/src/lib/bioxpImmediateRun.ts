import { useEffect, useRef, useState } from 'react';
import { useBioXpDocumentVisible } from '../components/BioXpObservationVisibility';
import { bioXpErrorText, useBioXpWorkflowJob, useSubmitBioXpProtocol, type BioXpWorkflowJob } from './bioxpClient';
import { methodsPost, newMethod, type MethodCompile, type MethodValue } from './bioxpMethods';
import { pipetteResults } from './bioxpPipetteResults';
import { useInvalidateSettings } from './bioxpSettingsObservations';

/** Compile Workflows steps into one immediate native document (no robot call). */
export async function compileImmediate(name: string, steps: MethodValue[]): Promise<MethodValue> {
    const compiled = await methodsPost<MethodCompile>('compile', { method: { ...newMethod(), name, steps } });
    if (!compiled.document) throw new Error(compiled.issues.map(issue => issue.message).join('; ') || 'Native compiler returned no document.');
    return compiled.document;
}

/** One deliberate immediate program at a time; only this owner's HTTP is reserved. */
export function useBioXpImmediateRun(generation: number, connected: boolean, visible = true) {
    const invalidateSettings = useInvalidateSettings(generation);
    const [error, setError] = useState<string | null>(null);
    const [pending, setPending] = useState(false);
    const busy = useRef(false);
    const mounted = useRef(true);
    const connection = useRef({ generation, connected });
    connection.current = { generation, connected };
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const [attempt, setAttempt] = useState<{ id: string; key: string; generation: number } | null>(null);
    const [accepted, setAccepted] = useState<BioXpWorkflowJob | null>(null);
    const submit = useSubmitBioXpProtocol();
    const sameConnection = !attempt || attempt.generation === generation;
    const documentVisible = useBioXpDocumentVisible();
    const [settledJob, setSettledJob] = useState<string | null>(null);
    const observationId = attempt?.id ?? null;
    const observeJob = documentVisible && (visible || (observationId !== null && settledJob !== observationId));
    const query = useBioXpWorkflowJob(observationId, attempt?.generation ?? generation, connected && sameConnection && observeJob);
    useEffect(() => {
        if (query.data?.command?.terminal && query.data.job_id === observationId) {
            setSettledJob(observationId);
            if (query.data.execution?.runtime_state.action_results?.some(result => pipetteResults(result).some(value => value.saved_revision_id !== undefined))) void invalidateSettings();
        }
    }, [query.data, observationId]);
    const mismatch = !!query.data && !!attempt && (query.data.job_id !== attempt.id || query.data.command?.idempotency_key !== attempt.key);
    const job = mismatch ? null : query.data ?? accepted;
    const enabled = connected && !pending;
    async function run(build: () => MethodValue | Promise<MethodValue>) {
        if (!enabled || busy.current) return;
        busy.current = true; setPending(true); setError(null);
        try {
            const document = await build();
            const key = crypto.randomUUID();
            const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key));
            const id = `protocol-live-${Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')}`;
            if (!mounted.current) return;
            if (!connection.current.connected || connection.current.generation !== generation) throw new Error('Connection changed before submission.');
            setAttempt({ id, key, generation }); setAccepted(null);
            const result = await submit.mutateAsync({ source_type: 'native', document, dry_run: false,
                live_execution: { live_execution_ack: true }, idempotency_key: key, expected_connection_generation: generation });
            if (!mounted.current) return;
            if (result.job_id !== id || result.command?.idempotency_key !== key) throw new Error('Robot returned a different submission identity.');
            setAccepted(result);
        } catch (cause) { if (mounted.current) setError(bioXpErrorText(cause)); }
        finally { busy.current = false; if (mounted.current) setPending(false); }
    }
    return { run, enabled, pending, error, setError, attempt, job, query, mismatch, sameConnection };
}

/** Every liquid_levels record a finished job returned, in order. */
export function liquidLevels(job: BioXpWorkflowJob | null | undefined): Record<string, any>[] {
    const out: Record<string, any>[] = [];
    const visit = (value: unknown) => {
        if (Array.isArray(value)) { value.forEach(visit); return; }
        if (!value || typeof value !== 'object') return;
        const record = value as Record<string, unknown>;
        if (Array.isArray(record.liquid_levels)) { out.push(...(record.liquid_levels as Record<string, any>[])); return; }
        Object.values(record).forEach(visit);
    };
    visit(job?.execution?.runtime_state.action_results ?? []);
    return out;
}
