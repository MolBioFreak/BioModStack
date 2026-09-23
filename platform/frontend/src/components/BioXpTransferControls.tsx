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

function ChildReceipt({ id, generation, connected }: { id: string; generation: number; connected: boolean }) {
    const query = useBioXpOperatorReceiptV2(id, generation, connected);
    const receipt = query.data?.command_id === id ? query.data : undefined;
    return <details><summary>Robot step {id} · {receipt?.status ?? 'receipt unavailable'}</summary>
        {query.error != null && <p role="alert">{bioXpErrorText(query.error)}</p>}
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(receipt, null, 2)}</pre>
    </details>;
}

export function BioXpTransferControls({ generation, connected, controlsEnabled, commandBusy, onBusy }: {
    generation: number; connected: boolean; controlsEnabled: boolean; commandBusy: boolean; onBusy: (busy: boolean) => void;
}) {
    const [object, setObject] = useState<string>('CV_OUTPUT');
    const [destination, setDestination] = useState<string>('LOC_OCS');
    const [contract, setContract] = useState<Record<string, unknown> | null>(null);
    const [ack, setAck] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [attempt, setAttempt] = useState<{ id: string; key: string } | null>(null);
    const [accepted, setAccepted] = useState<BioXpWorkflowJob | null>(null);
    const busyRef = useRef(false);
    const mounted = useRef(true);
    const fileVersion = useRef(0);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const jobs = useBioXpWorkflowJobs(generation, connected);
    const activeJob = jobs.data?.find(job => job.command?.terminal === false);
    const id = attempt?.id ?? activeJob?.job_id ?? null;
    const query = useBioXpWorkflowJob(id, generation, connected);
    const job = query.data?.job_id === id ? query.data : accepted?.job_id === id ? accepted : null;
    const command = job?.command;
    const currentLive = command?.command_id === id && job?.execution?.dry_run === false;
    const [submitting, setSubmitting] = useState(false);
    const listedLive = !!activeJob && !(activeJob.job_id === id && currentLive && command?.terminal);
    const busy = submitting || listedLive || (!!attempt && !(currentLive && command?.terminal));
    useEffect(() => { onBusy(busy); }, [busy, onBusy]);
    const submit = useSubmitBioXpProtocol();
    const enabled = connected && controlsEnabled && !commandBusy && !busy && !submit.isPending
        && !jobs.isLoading && !jobs.isError && !!contract && ack;

    async function run(inspect: boolean) {
        if (!enabled || busyRef.current || !contract) return;
        busyRef.current = true;
        setSubmitting(true);
        setError(null); setAck(false); setAccepted(null);
        try {
            const key = crypto.randomUUID();
            const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key));
            const jobId = `protocol-live-${Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')}`;
            if (!mounted.current) return;
            setAttempt({ id: jobId, key });
            const result = await submit.mutateAsync({ source_type: 'native', document: transferDocument(object, destination, inspect),
                live_execution: { ...contract, live_execution_ack: true }, dry_run: false,
                idempotency_key: key, expected_connection_generation: generation });
            if (!mounted.current) return;
            if (result.job_id !== jobId || result.command?.idempotency_key !== key) throw new Error('Robot returned a different submission identity.');
            setAccepted(result);
            void jobs.refetch();
        } catch (cause) {
            if (!mounted.current) return;
            setError(bioXpErrorText(cause));
            const status = (cause as { response?: { status?: number } }).response?.status;
            // Definite pre-admission refusal can be corrected. Lost/invalid replies retain the original identity.
            if (status != null && status >= 400 && status < 500 && status !== 408) setAttempt(null);
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
        <label className="block">Verified live contract JSON
            <input type="file" accept=".json,application/json" disabled={busy} onChange={async event => {
                const version = ++fileVersion.current;
                const file = event.currentTarget.files?.[0];
                setContract(null); setAck(false); setError(null);
                if (!file) return;
                try {
                    const value = JSON.parse(await file.text());
                    if (!value || Array.isArray(value) || typeof value !== 'object'
                        || typeof value.operator_id !== 'string' || !value.operator_id.trim()
                        || value.physical_console_verified !== true || !value.deck_manifest || !value.preflight)
                        throw new Error('Provide the actual live_execution object: operator_id, physical_console_verified, deck_manifest and preflight with reference_snapshot and artifact_refs.');
                    if (mounted.current && version === fileVersion.current) setContract(value);
                } catch (cause) { if (mounted.current && version === fileVersion.current) setError(bioXpErrorText(cause)); }
            }} />
        </label>
        <p className="text-xs">Load an operator-verified live_execution object from the current preflight. BMS does not fabricate reference snapshots, artifact references, deck contents or console verification.</p>
        {contract && <p>Contract loaded for {String(contract.operator_id)}; robot validation still required.</p>}
        <label className="block"><input type="checkbox" checked={ack} disabled={busy} onChange={event => setAck(event.target.checked)} /> I intend physical execution of the selected operation.</label>
        <button type="button" className="rounded bg-teal-700 px-3 py-2 disabled:opacity-35" disabled={!enabled} onClick={() => void run(false)}>Pick up and move</button>
        <button type="button" className="ml-3 rounded bg-teal-700 px-3 py-2 disabled:opacity-35" disabled={!enabled} onClick={() => void run(true)}>Inspect covers (may move covers)</button>
        {id && <p className="break-all text-xs">Compound job: {id}</p>}
        {attempt && <p className="break-all text-xs">Submission key: {attempt.key}</p>}
        {busy && <p role="status">Command live or outcome unresolved; do not resubmit.</p>}
        {(query.isError || jobs.isError) && <p role="alert">Robot readback unavailable; checking again. Do not infer completion.</p>}
        {currentLive && <p role="status">Robot compound status: {command?.status} · {command?.terminal ? 'terminal' : 'live'} · {job?.execution?.runtime_state.workflow?.phase ?? 'phase unavailable'}. This is not independent physical verification.</p>}
        {job?.execution?.runtime_state.workflow?.held_reason && <p role="alert">{job.execution.runtime_state.workflow.held_reason}</p>}
        {job && <details><summary>Robot custody, action results and failures</summary>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(job.execution?.runtime_state, null, 2)}</pre>
        </details>}
        {job?.execution?.runtime_state.workflow?.child_command_ids.map(child => <ChildReceipt key={child} id={child} generation={generation} connected={connected} />)}
        {error && <p role="alert">{error}</p>}
    </section>;
}
