import { useEffect, useRef, useState } from 'react';
import {
    bioXpErrorText, useBioXpWorkflowJobs, useBioXpWorkflowJob, useSubmitBioXpProtocol,
    useBioXpOperatorReceiptV2, type BioXpWorkflowJob, getBioXpTransferPreflight, type BioXpTransferPreflight,
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

export function BioXpTransferControls({ generation, connected, controlsEnabled, commandBusy, onBusy }: {
    generation: number; connected: boolean; controlsEnabled: boolean; commandBusy: boolean; onBusy: (busy: boolean) => void;
}) {
    const [object, setObject] = useState<string>('CV_OUTPUT');
    const [destination, setDestination] = useState<string>('LOC_OCS');
    const [operator, setOperator] = useState('');
    const [preflight, setPreflight] = useState<BioXpTransferPreflight | null>(null);
    const [ack, setAck] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [attempt, setAttempt] = useState<{ id: string; key: string; generation: number } | null>(null);
    const [accepted, setAccepted] = useState<BioXpWorkflowJob | null>(null);
    const busyRef = useRef(false);
    const mounted = useRef(true);
    const connection = useRef({ generation, connected });
    connection.current = { generation, connected };
    useEffect(() => { setAck(false); setPreflight(null); }, [generation, connected, object, destination]);
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
    const listedLive = !!activeJob && !(activeJob.job_id === id && currentLive && command?.terminal);
    const busy = submitting || identityMismatch || listedLive || (!!attempt && !(currentLive && command?.terminal));
    useEffect(() => { onBusy(busy); }, [busy, onBusy]);
    const submit = useSubmitBioXpProtocol();
    const enabled = connected && controlsEnabled && !commandBusy && !busy && !submit.isPending
        && !jobs.isLoading && !jobs.isError && !!operator.trim() && ack;

    async function run(inspect: boolean) {
        if (!enabled || busyRef.current) return;
        busyRef.current = true;
        setSubmitting(true);
        setError(null); setAck(false); setAccepted(null); setPreflight(null);
        try {
            const observed = await getBioXpTransferPreflight(generation);
            if (!mounted.current) return;
            if (!connection.current.connected || connection.current.generation !== generation || observed.connection_generation !== generation)
                throw new Error('Connection changed during preflight. Confirm the current robot before trying again.');
            setPreflight(observed);
            const contract = {
                operator_id: operator.trim(), physical_console_verified: true, live_execution_ack: true,
                deck_manifest: { observation_source: 'robot operator/v2/control-catalog',
                    ownership_generation: observed.ownership_generation, observed_deck: observed.observed_deck,
                    selected_operation: inspect ? { kind: 'inspect' } : { object, target: destination } },
                preflight: observed.preflight,
            };
            const key = crypto.randomUUID();
            const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key));
            const jobId = `protocol-live-${Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')}`;
            if (!mounted.current) return;
            if (!connection.current.connected || connection.current.generation !== generation) throw new Error('Connection changed before submission.');
            setAttempt({ id: jobId, key, generation });
            const result = await submit.mutateAsync({ source_type: 'native', document: transferDocument(object, destination, inspect),
                live_execution: contract, dry_run: false,
                idempotency_key: key, expected_connection_generation: generation });
            if (!mounted.current) return;
            if (result.job_id !== jobId || result.command?.idempotency_key !== key) throw new Error('Robot returned a different submission identity.');
            setAccepted(result);
            void jobs.refetch();
        } catch (cause) {
            if (!mounted.current) return;
            setError(bioXpErrorText(cause));
            // Once POST starts, retain identity on every error. A conflict or proxy error
            // is not proof that no work was admitted; readback, never blind replay.
        } finally { busyRef.current = false; if (mounted.current) setSubmitting(false); }
    }

    return <section aria-label="Plate and cover transfer" className="mt-4 space-y-3 rounded border border-teal-700 p-4">
        <h3 className="font-semibold">Pick up and move a plate or cover</h3>
        <p className="text-sm">Compound robot operation: catch, carry and release. Source comes from robot custody state, never a guessed source location. Inspect covers discovers and may relocate covers using the OEM inspection sequence.</p>
        <div className="grid gap-3 sm:grid-cols-2">
            <label>Object<select className="block w-full bg-slate-950 p-2" value={object} disabled={busy} onChange={event => setObject(event.target.value)}>
                {objects.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select></label>
            <label>Transfer destination<select className="block w-full bg-slate-950 p-2" value={destination} disabled={busy} onChange={event => setDestination(event.target.value)}>
                {destinations.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select></label>
        </div>
        <p className="text-xs">Choices are OEM intents, not proof of readiness. The robot checks destination, door, references and custody at execution.</p>
        <label className="block">Operator name or account
            <input className="ml-2 bg-slate-950 p-2" value={operator} maxLength={120} disabled={busy} onChange={event => { setOperator(event.target.value); setAck(false); }} />
        </label>
        <p className="text-xs">Before each submission, BMS reads current robot references and deck revisions. No files or pasted artifacts are needed. These observations do not prove plates are loaded or physically verify cover locations; inspection observes covers.</p>
        {preflight && <p>Last preflight: X/Y/Z/gripper referenced; deck location {preflight.observed_deck.current_location ?? 'unknown'}. Robot rechecks admission and custody at execution.</p>}
        <label className="block"><input type="checkbox" checked={ack} disabled={busy} onChange={event => setAck(event.target.checked)} /> I have verified the physical console and intend physical execution of the selected operation, including cover movement during inspection.</label>
        <button type="button" className="rounded bg-teal-700 px-3 py-2 disabled:opacity-35" disabled={!enabled} onClick={() => void run(false)}>Pick up and move</button>
        <button type="button" className="ml-3 rounded bg-teal-700 px-3 py-2 disabled:opacity-35" disabled={!enabled} onClick={() => void run(true)}>Inspect covers (may move covers)</button>
        {id && <p className="break-all text-xs">Compound job: {id}</p>}
        {attempt && <p className="break-all text-xs">Submission key: {attempt.key}</p>}
        {!sameConnection && <p role="alert">Connection changed. Original submission identity retained; do not resubmit unresolved work on another robot.</p>}
        {busy && <p role="status">Command live or outcome unresolved; do not resubmit.</p>}
        {identityMismatch && <p role="alert">Robot readback identity does not match the retained submission; outcome remains unresolved.</p>}
        {(query.isError || jobs.isError) && <p role="alert">Robot readback unavailable; checking again. Do not infer completion.</p>}
        {currentLive && <p role="status">Robot compound status: {command?.status} · {command?.terminal ? 'terminal' : 'live'} · {job?.execution?.runtime_state.workflow?.phase ?? 'phase unavailable'}. This is not independent physical verification.</p>}
        {job?.execution?.runtime_state.workflow?.held_reason && <p role="alert">{job.execution.runtime_state.workflow.held_reason}</p>}
        {job?.execution?.runtime_state.action_results?.map((result, index) => <p key={index} role={result.ok === false ? 'alert' : 'status'}>Action {index + 1}: {result.ok === false ? 'failed' : result.ok === true ? 'completed' : 'reported'}{outcomeText(result) ? ` · ${outcomeText(result)}` : ''}</p>)}
        {job && <details><summary>Robot custody, action results and failures</summary>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(job.execution?.runtime_state, null, 2)}</pre>
        </details>}
        {job?.execution?.runtime_state.workflow?.child_command_ids.map(child => <ChildReceipt key={child} id={child} generation={retainedGeneration} connected={connected && sameConnection} />)}
        {error && <p role="alert">{error}</p>}
    </section>;
}
