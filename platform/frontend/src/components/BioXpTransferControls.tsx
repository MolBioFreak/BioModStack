import { useEffect, useRef, useState } from 'react';
import {
    bioXpErrorText, useBioXpWorkflowJobs, useBioXpWorkflowJob, useSubmitBioXpProtocol,
    useBioXpOperatorReceiptV2, type BioXpWorkflowJob,
} from '../lib/bioxpClient';

// OEM MP/MC script tokens, not a source-location override or admission authority.
const objects = [
    ['CV_OUTPUT', 'Output cover'], ['CV_REAGENT', 'Reagent cover'], ['CV_BIOSECURITY', 'Biosecurity cover'],
    ['PL_POOL', 'Pool plate'], ['PL_OUTPUT', 'Output plate'], ['PL_REAGENT', 'Reagent plate'],
] as const;
const destinations = [
    ['LOC_OC', 'Output station'], ['LOC_OCS', 'Output cover storage'],
    ['LOC_RC', 'Reagent station'], ['LOC_RCS', 'Reagent cover storage'],
    ['LOC_TC', 'Thermal cycler'], ['LOC_BSCS', 'Biosecurity cover storage'], ['LOC_MS', 'Magnetic station'],
] as const;

export function transferDocument(object: string, destination: string, inspect: boolean) {
    if (!inspect && (!objects.some(([id]) => id === object) || !destinations.some(([id]) => id === destination)))
        throw new Error('Select a supported OEM object and destination.');
    return { protocol_id: 'bms-deck-compound', version: 1, stages: [{ stage_id: 'deck', actions: [{
        action_id: inspect ? 'inspect-covers' : 'transfer', stage_id: 'deck',
        kind: inspect ? 'inspect' : object.startsWith('CV_') ? 'move_cover' : 'plate_move',
        params: inspect ? {} : { [object.startsWith('CV_') ? 'cover_id' : 'plate_id']: object, target_location: destination },
    }] }] };
}

function outcomeText(value: unknown): string {
    if (typeof value === 'string') return value;
    if (!value || typeof value !== 'object') return '';
    const row = value as Record<string, unknown>;
    return ['message', 'error', 'failure', 'reason', 'custody', 'detail', 'result'].map(key => outcomeText(row[key])).filter(Boolean).join(' · ');
}

function ChildReceipt({ id, generation, connected }: { id: string; generation: number; connected: boolean }) {
    const query = useBioXpOperatorReceiptV2(id, generation, connected);
    const receipt = query.data?.command_id === id ? query.data : undefined;
    return <div>
        <p role={receipt?.status === 'failed' ? 'alert' : 'status'}>Robot step: {receipt?.status ?? 'receipt unavailable'}{outcomeText(receipt?.error) ? ` · ${outcomeText(receipt?.error)}` : ''}</p>
        {query.error != null && <p role="alert">{bioXpErrorText(query.error)}</p>}
        <details><summary>Robot step {id} · {receipt?.status ?? 'receipt unavailable'}</summary>
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(receipt, null, 2)}</pre>
    </details></div>;
}

export function BioXpTransferControls({ generation, connected }: {
    generation: number; connected: boolean;
}) {
    const [object, setObject] = useState<string>('CV_OUTPUT');
    const [destination, setDestination] = useState<string>('LOC_OCS');
    const [error, setError] = useState<string | null>(null);
    const [attempt, setAttempt] = useState<{ id: string; key: string; generation: number } | null>(null);
    const [accepted, setAccepted] = useState<BioXpWorkflowJob | null>(null);
    const busyRef = useRef(false);
    const mounted = useRef(true);
    const connection = useRef({ generation, connected });
    connection.current = { generation, connected };
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const jobs = useBioXpWorkflowJobs(generation, connected);
    const activeJob = jobs.data?.find(job => job.command?.terminal === false);
    const id = attempt?.id ?? activeJob?.job_id ?? null;
    const retainedGeneration = attempt?.generation ?? generation;
    const sameConnection = retainedGeneration === generation;
    const query = useBioXpWorkflowJob(id, retainedGeneration, connected && sameConnection);
    const identityMismatch = !!query.data && !!attempt && (query.data.job_id !== attempt.id || query.data.command?.idempotency_key !== attempt.key);
    const job = !identityMismatch && query.data?.job_id === id ? query.data : accepted?.job_id === id ? accepted : null;
    const command = job?.command;
    const currentLive = command?.command_id === id && job?.execution?.dry_run === false;
    const [submitting, setSubmitting] = useState(false);
    // The jobs list and receipt can lag a completed robot command. They are
    // evidence, not a second resource lock; the robot excludes live conflicts.
    const busy = submitting;
    const submit = useSubmitBioXpProtocol();
    // The robot decides live admission; unrelated cockpit requests and
    // retained status observations do not lock this independent intent.
    const enabled = connected && !busy && !submit.isPending;

    async function run(inspect: boolean) {
        if (!enabled || busyRef.current) return;
        busyRef.current = true;
        setSubmitting(true);
        setError(null); setAccepted(null);
        try {
            const key = crypto.randomUUID();
            const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key));
            const jobId = `protocol-live-${Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')}`;
            if (!mounted.current) return;
            if (!connection.current.connected || connection.current.generation !== generation) throw new Error('Connection changed before submission.');
            setAttempt({ id: jobId, key, generation });
            const result = await submit.mutateAsync({ source_type: 'native', document: transferDocument(object, destination, inspect),
                live_execution: { live_execution_ack: true }, dry_run: false,
                idempotency_key: key, expected_connection_generation: generation });
            if (!mounted.current) return;
            if (result.job_id !== jobId || result.command?.idempotency_key !== key) throw new Error('Robot returned a different submission identity.');
            setAccepted(result);
            void jobs.refetch();
        } catch (cause) {
            if (!mounted.current) return;
            setError(bioXpErrorText(cause));
            // Retain identity for readback on every error; never automatically
            // replay an uncertain POST. A later explicit click is a new intent.
        } finally { busyRef.current = false; if (mounted.current) setSubmitting(false); }
    }

    return <section aria-label="Plate and cover transfer" className="mt-4 space-y-3 rounded border border-teal-700 p-4">
        <h3 className="font-semibold">Pick up and move a plate or cover</h3>
        <p className="text-sm">Catch, carry and release. Inspect covers may move covers.</p>
        <div className="grid gap-3 sm:grid-cols-2">
            <label>Object<select className="block w-full bg-slate-950 p-2" value={object} disabled={busy} onChange={event => setObject(event.target.value)}>
                {objects.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select></label>
            <label>Transfer destination<select className="block w-full bg-slate-950 p-2" value={destination} disabled={busy} onChange={event => setDestination(event.target.value)}>
                {destinations.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select></label>
        </div>

        <button type="button" className="rounded bg-teal-700 px-3 py-2 disabled:opacity-35" disabled={!enabled} onClick={() => void run(false)}>Pick up and move</button>
        <button type="button" className="ml-3 rounded bg-teal-700 px-3 py-2 disabled:opacity-35" disabled={!enabled} onClick={() => void run(true)}>Inspect covers (may move covers)</button>
        {id && <p className="break-all text-xs">Job {id}</p>}
        {!sameConnection && <p role="alert">Connection changed. Check the earlier job.</p>}
        {busy && <p role="status">Submitting…</p>}
        {activeJob && <p role="status">Listed job {activeJob.job_id} · {activeJob.command?.status ?? 'status unknown'}</p>}
        {identityMismatch && <p role="alert">Job identity mismatch. Check robot status.</p>}
        {(query.isError || jobs.isError) && <p role="alert">Robot status unavailable.</p>}
        {currentLive && <p role="status">{command?.status} · {job?.execution?.runtime_state.workflow?.phase ?? 'phase unavailable'}</p>}
        {job?.execution?.runtime_state.workflow?.held_reason && <p role="alert">{job.execution.runtime_state.workflow.held_reason}</p>}
        {job?.execution?.runtime_state.action_results?.map((result, index) => <p key={index} role={result.ok === false ? 'alert' : 'status'}>Action {index + 1}: {result.ok === false ? 'failed' : result.ok === true ? 'completed' : 'reported'}{outcomeText(result) ? ` · ${outcomeText(result)}` : ''}</p>)}
        {job && <details><summary>Robot custody, action results and failures</summary>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(job.execution?.runtime_state, null, 2)}</pre>
        </details>}
        {job?.execution?.runtime_state.workflow?.child_command_ids.map(child => <ChildReceipt key={child} id={child} generation={retainedGeneration} connected={connected && sameConnection} />)}
        {error && <p role="alert">{error}</p>}
    </section>;
}
