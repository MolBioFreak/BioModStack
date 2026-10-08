import { useRef, useState } from 'react';
import {
    bioXpErrorText, useBioXpWorkflowJobs, useBioXpWorkflowJob,
    useSubmitBioXpProtocol, useControlBioXpWorkflow, useReviewBioXpWorkflow,
    type BioXpWorkflowInput, type BioXpWorkflowAction, type BioXpWorkflowJob,
} from '../lib/bioxpClient';

const buttonClass = 'rounded bg-cyan-700 px-3 py-2 text-sm disabled:opacity-35';
const inputFields = new Set(['source_type', 'document', 'xml_path', 'live_execution', 'live_execution_ack',
    'operator_id', 'physical_console_verified', 'deck_manifest', 'preflight', 'artifact_refs', 'snapshot_refs']);

/** Select an existing prepared request, never author or expand operations in the browser. */
function selectedInput(text: string): BioXpWorkflowInput {
    const value = JSON.parse(text);
    if (!value || Array.isArray(value) || typeof value !== 'object'
        || Object.keys(value).some(key => !inputFields.has(key))
        || !['native', 'oem_xml'].includes(value.source_type)
        || (value.source_type === 'native' && (!value.document || typeof value.document !== 'object' || Array.isArray(value.document)))
        || (value.source_type === 'oem_xml' && typeof value.xml_path !== 'string')) {
        throw new Error('Select a prepared robot request with source_type and document (or xml_path), without delivery identity fields.');
    }
    return value;
}

export function BioXpWorkflowControls({ generation, connected, controlsEnabled }: {
    generation: number; connected: boolean; controlsEnabled: boolean;
}) {
    const jobs = useBioXpWorkflowJobs(generation, connected);
    const [selection, setSelection] = useState<{ name: string; input: BioXpWorkflowInput } | null>(null);
    const [selectionError, setSelectionError] = useState<string | null>(null);
    const [acknowledged, setAcknowledged] = useState(false);
    const [selectedJob, setSelectedJob] = useState<{ generation: number; id: string } | null>(null);
    const [attempt, setAttempt] = useState<{ generation: number; key: string; jobId: string } | null>(null);
    const [acceptedJob, setAcceptedJob] = useState<{ generation: number; job: BioXpWorkflowJob } | null>(null);
    const [abortConfirmed, setAbortConfirmed] = useState(false);
    const [reviewer, setReviewer] = useState('');
    const [note, setNote] = useState('');
    const [localError, setLocalError] = useState<string | null>(null);
    const busyRef = useRef(false);
    const selectionVersion = useRef(0);
    const currentGeneration = useRef(generation);
    currentGeneration.current = generation;
    const submit = useSubmitBioXpProtocol();
    const control = useControlBioXpWorkflow();
    const review = useReviewBioXpWorkflow();
    const currentAttempt = attempt?.generation === generation ? attempt : null;
    const listedActive = jobs.data?.find(job => job.command && (!job.command.terminal || job.command.status === 'ambiguous'));
    const jobId = (selectedJob?.generation === generation ? selectedJob.id : null)
        ?? currentAttempt?.jobId ?? listedActive?.job_id ?? null;
    const query = useBioXpWorkflowJob(jobId, generation, connected);
    const job = query.data?.job_id === jobId ? query.data
        : acceptedJob?.generation === generation && acceptedJob.job.job_id === jobId ? acceptedJob.job : null;
    const command = job?.command;
    const runtime = job?.execution?.runtime_state;
    const workflow = runtime?.workflow;
    const canonical = !!command && !!workflow && command.command_id === jobId && workflow.command_id === jobId && job?.execution?.dry_run === false;
    const busy = submit.isPending || control.isPending || review.isPending;
    const settled = canonical && command.terminal && workflow.phase === 'terminal';
    const mutable = connected && controlsEnabled && !busy && !query.isError && canonical && !command.terminal
        && workflow.phase !== 'reconciling';
    const pendingControl = workflow?.requested_control != null && workflow.last_control_id !== workflow.reached_control_id;
    // The reached pause owns gate_id. Completed wake reaches a distinct control
    // while retaining that gate; only then is the separate Continue eligible.
    const wakeReached = workflow?.gate === 'deferred_pause' && workflow.requested_control === null
        && workflow.reached_control_id != null && workflow.reached_control_id !== workflow.gate_id
        && workflow.reached_control_id === workflow.last_control_id;
    const maySubmit = connected && controlsEnabled && !busy && acknowledged && selection !== null
        && !jobs.isError && !jobs.isLoading && !listedActive && (!currentAttempt || settled && jobId === currentAttempt.jobId);

    async function submitSelected() {
        if (!maySubmit || !selection || busyRef.current) return;
        busyRef.current = true;
        const submittedGeneration = generation;
        const input = selection.input;
        try {
            const key = crypto.randomUUID();
            // The robot's canonical live ID is defined by the original trimmed key.
            // Retain it before POST so a lost response needs GET reconciliation, not replay.
            const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key));
            const id = `protocol-live-${Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')}`;
            if (currentGeneration.current !== submittedGeneration) return;
            setAttempt({ generation, key, jobId: id });
            setSelectedJob({ generation, id });
            setAcknowledged(false);
            setLocalError(null);
            const result = await submit.mutateAsync({ ...input, dry_run: false, idempotency_key: key, expected_connection_generation: generation });
            if (currentGeneration.current === submittedGeneration) {
                setAcceptedJob({ generation, job: result });
                setSelectedJob({ generation, id: result.job_id });
                void jobs.refetch();
            }
        } catch (error) { if (currentGeneration.current === submittedGeneration) setLocalError(bioXpErrorText(error)); }
        finally { busyRef.current = false; }
    }

    async function send(action: BioXpWorkflowAction) {
        if (!mutable || !command || !jobId || busyRef.current) return;
        busyRef.current = true;
        setLocalError(null);
        setAbortConfirmed(false);
        try {
            await control.mutateAsync({ jobId, request: { ...action, command_id: command.command_id,
                expected_ownership_generation: command.ownership_generation,
                expected_connection_generation: generation, idempotency_key: crypto.randomUUID() } });
            void query.refetch();
        } catch (error) { setLocalError(bioXpErrorText(error)); }
        finally { busyRef.current = false; }
    }

    async function acknowledgeReview() {
        const stageId = job?.operator?.pending_review?.stage_id;
        const actionId = job?.operator?.pending_review?.action_id ?? null;
        if (!mutable || !command || !jobId || workflow?.gate !== 'review' || !stageId || !reviewer.trim() || busyRef.current) return;
        busyRef.current = true;
        setLocalError(null);
        try {
            await review.mutateAsync({ jobId, request: { command_id: command.command_id,
                expected_ownership_generation: command.ownership_generation,
                expected_connection_generation: generation, idempotency_key: crypto.randomUUID(),
                stage_id: stageId, action_id: actionId, reviewer: reviewer.trim(), note: note || null } });
            void query.refetch();
        } catch (error) { setLocalError(bioXpErrorText(error)); }
        finally { busyRef.current = false; }
    }

    const gate = workflow?.gate;
    const gateId = workflow?.gate_id;
    return <section aria-label="Prepared workflow" className="space-y-3 rounded-xl border border-slate-800 bg-slate-950/70 p-4">
        <h2 className="text-lg font-semibold">Prepared workflow</h2>
        <p className="text-sm text-slate-400">Select an existing robot request with its prepared input, manifest and preflight. No recipe generation. Thermal and selected vision dependencies remain subject to robot support checks.</p>
        <label className="block text-sm">Prepared request file
            <input type="file" accept=".json,application/json" disabled={busy || !!currentAttempt && !settled} onChange={async event => {
                const file = event.currentTarget.files?.[0];
                const version = ++selectionVersion.current;
                setSelection(null); setSelectionError(null); setAcknowledged(false);
                if (!file) return;
                try {
                    const input = selectedInput(await file.text());
                    if (version === selectionVersion.current) setSelection({ name: file.name, input });
                } catch (error) { if (version === selectionVersion.current) setSelectionError(bioXpErrorText(error)); }
            }} />
        </label>
        {selection && <p className="text-sm">Selected: {selection.name} · {selection.input.source_type}</p>}
        {selectionError && <p role="alert">{selectionError}</p>}
        <label className="block text-sm"><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} /> I intend to submit this prepared request for physical execution.</label>
        <button type="button" className={buttonClass} disabled={!maySubmit} onClick={() => void submitSelected()}>Submit prepared workflow</button>
        {currentAttempt && <p className="break-all text-xs">Original submission key: {currentAttempt.key}</p>}
        {jobs.data && jobs.data.length > 0 && <label className="block text-sm">Robot workflow
            <select value={jobId ?? ''} onChange={event => { setSelectedJob({ generation, id: event.target.value }); setAbortConfirmed(false); }}>
                <option value="" disabled>Select a robot workflow</option>
                {currentAttempt && !jobs.data.some(item => item.job_id === currentAttempt.jobId) && <option value={currentAttempt.jobId}>{currentAttempt.jobId}</option>}
                {jobs.data.map(item => <option key={item.job_id} value={item.job_id}>{item.job_id} · {item.command?.status ?? item.status}</option>)}
            </select>
        </label>}
        {jobId && <p className="break-all text-sm">Canonical job: {jobId}</p>}
        {(query.isError || jobs.isError) && <p role="status">Workflow readback unavailable; checking again. Do not resubmit uncertain work.</p>}
        {currentAttempt && !job && <p role="status">Submission outcome not yet reconciled. Checking the original job; no automatic retry.</p>}
        {!controlsEnabled && connected && <p className="text-sm">Workflow controls unavailable until current connection status recovers; passive readback continues.</p>}
        {canonical && <>
            <p role="status">Robot status: {command.status} · Phase: {workflow.phase}{gate ? ` · Gate: ${gate}` : ''}</p>
            <p className="text-sm">Source occurrence: {workflow.source_occurrence_id ?? 'none'} · State version: {command.state_version}</p>
            {workflow.held_reason && <p className="text-amber-200">Held reason: {workflow.held_reason}</p>}
            {workflow.requested_control && <p>Requested control: {workflow.requested_control.action} — acceptance is not a reached boundary.</p>}
            {workflow.reached_control_id && <p className="text-xs">Reached control: {workflow.reached_control_id}</p>}
            <p className="text-xs">Pause does not establish physical quiescence. Cooperative Abort is not addressed motor Stop. Native evidence remains in the existing receipt history.</p>
            <div className="flex flex-wrap gap-2">
                <button className={buttonClass} disabled={!mutable || pendingControl || workflow.phase !== 'executing'} onClick={() => void send({ action: 'pause', mode: 'ordinary' })}>Pause workflow</button>
                <button className={buttonClass} disabled={!mutable || pendingControl || workflow.phase !== 'executing'} onClick={() => void send({ action: 'pause', mode: 'deferred' })}>Request deferred pause</button>
                {gate === 'deferred_pause' && gateId && <button className={buttonClass} disabled={!mutable || pendingControl || wakeReached || workflow.phase !== 'waiting'} onClick={() => void send({ action: 'wake', gate_id: gateId })}>Wake workflow</button>}
                {(gate === 'ordinary_pause' || gate === 'deferred_pause' || gate === 'delaypoint') && gateId && <button className={buttonClass} disabled={!mutable || pendingControl || workflow.phase !== 'waiting' || gate === 'deferred_pause' && !wakeReached} onClick={() => void send({ action: 'continue', gate, gate_id: gateId })}>{gate === 'delaypoint' ? 'Start now' : 'Continue workflow'}</button>}
                <button className={buttonClass} disabled={!mutable} onClick={() => void send({ action: 'safe_stop' })}>Request safe-state stop</button>
            </div>
            <label className="block text-sm"><input type="checkbox" checked={abortConfirmed} onChange={event => setAbortConfirmed(event.target.checked)} /> Confirm cooperative job Abort</label>
            <button className={buttonClass} disabled={!mutable || !abortConfirmed} onClick={() => void send({ action: 'abort' })}>Abort workflow</button>
            {gate === 'review' && <div className="space-y-2">
                <label className="block">Reviewer <input value={reviewer} maxLength={120} onChange={event => setReviewer(event.target.value)} /></label>
                <label className="block">Review note <input value={note} maxLength={4000} onChange={event => setNote(event.target.value)} /></label>
                <button className={buttonClass} disabled={!mutable || !reviewer.trim() || !job?.operator?.pending_review?.stage_id} onClick={() => void acknowledgeReview()}>Acknowledge protocol review</button>
            </div>}
        </>}
        {job && !canonical && <p>Historical or nonphysical job — no live workflow control authority.</p>}
        {control.data && control.variables?.request.expected_connection_generation === generation && control.data.command_id === jobId && <p>Control {control.data.control_command_id}: {control.data.accepted ? 'accepted' : 'not accepted'} · {control.data.reached ? 'reached' : 'not yet reached'}</p>}
        {localError && <p role="alert">{localError} No automatic submission or control retry; reconcile robot state before further action.</p>}
    </section>;
}
