import { useBioXpDocumentVisible } from './BioXpObservationVisibility';
import { useEffect, useRef, useState, type RefObject } from 'react';
import { bioXpErrorText, useBioXpWorkflowJobObservation, useControlBioXpWorkflow, useReviewBioXpWorkflow,
    type BioXpWorkflowAction, type BioXpWorkflowJob } from '../lib/bioxpClient';
const buttonClass = 'rounded bg-cyan-700 px-3 py-2 text-sm disabled:opacity-35';

/** One native readback/control owner for both prepared and saved submissions. */
export function BioXpWorkflowJobMonitor({ jobId, generation, connected, controlsEnabled, visible = true,
    acceptedJob = null, submitting = false, pending = false, discoveryError = false, busyRef, onBusyChange }: {
    jobId: string | null; generation: number; connected: boolean; controlsEnabled: boolean; visible?: boolean;
    busyRef: RefObject<boolean>; onBusyChange: (busy: boolean) => void;
    acceptedJob?: BioXpWorkflowJob | null; submitting?: boolean; pending?: boolean; discoveryError?: boolean;
}) {
    const [abortConfirmed, setAbortConfirmed] = useState(false);
    const [reviewer, setReviewer] = useState('');
    const [note, setNote] = useState('');
    const [localError, setLocalError] = useState<string | null>(null);
    const identity = useRef({ jobId, generation });
    identity.current = { jobId, generation };
    // Selection resets presentation, never the shared in-flight operation.
    useEffect(() => {
        setAbortConfirmed(false); setReviewer(''); setNote(''); setLocalError(null);
    }, [jobId, generation]);
    const control = useControlBioXpWorkflow();
    const review = useReviewBioXpWorkflow();
    const documentVisible = useBioXpDocumentVisible();
    const [settledJob, setSettledJob] = useState<string | null>(null);
    const observationId = jobId;
    const observeJob = documentVisible && (visible || (observationId !== null && settledJob !== observationId));
    const query = useBioXpWorkflowJobObservation(observationId, generation, connected && observeJob);
    useEffect(() => {
        if (query.data?.command?.terminal && query.data.job_id === observationId) setSettledJob(observationId);
    }, [query.data, observationId]);
    const job = query.data?.job_id === jobId ? query.data : acceptedJob?.job_id === jobId ? acceptedJob : null;
    const command = job?.command;
    const runtime = job?.execution?.runtime_state;
    const workflow = runtime?.workflow;
    const canonical = !!command && !!workflow && command.command_id === jobId && workflow.command_id === jobId && job?.execution?.dry_run === false;
    const busy = submitting || control.isPending || review.isPending;
    const mutable = connected && controlsEnabled && !busy && !query.isError && canonical && !command.terminal
        && workflow.phase !== 'reconciling';
    const pendingControl = workflow?.requested_control != null && workflow.last_control_id !== workflow.reached_control_id;
    // The reached pause owns gate_id. Completed wake reaches a distinct control
    // while retaining that gate; only then is the separate Continue eligible.
    const wakeReached = workflow?.gate === 'deferred_pause' && workflow.requested_control === null
        && workflow.reached_control_id != null && workflow.reached_control_id !== workflow.gate_id
        && workflow.reached_control_id === workflow.last_control_id;
    async function send(action: BioXpWorkflowAction) {
        if (!mutable || !command || !jobId || busyRef.current) return;
        busyRef.current = true; onBusyChange(true);
        setLocalError(null);
        setAbortConfirmed(false);
        try {
            await control.mutateAsync({ jobId, request: { ...action, command_id: command.command_id,
                expected_ownership_generation: command.ownership_generation,
                expected_connection_generation: generation, idempotency_key: crypto.randomUUID() } });
            void query.refetch();
        } catch (error) {
            if (identity.current.jobId === jobId && identity.current.generation === generation) setLocalError(bioXpErrorText(error));
        }
        finally { busyRef.current = false; onBusyChange(false); }
    }

    async function acknowledgeReview() {
        const stageId = job?.operator?.pending_review?.stage_id;
        const actionId = job?.operator?.pending_review?.action_id ?? null;
        if (!mutable || !command || !jobId || workflow?.gate !== 'review' || !stageId || !reviewer.trim() || busyRef.current) return;
        busyRef.current = true; onBusyChange(true);
        setLocalError(null);
        try {
            await review.mutateAsync({ jobId, request: { command_id: command.command_id,
                expected_ownership_generation: command.ownership_generation,
                expected_connection_generation: generation, idempotency_key: crypto.randomUUID(),
                stage_id: stageId, action_id: actionId, reviewer: reviewer.trim(), note: note || null } });
            void query.refetch();
        } catch (error) {
            if (identity.current.jobId === jobId && identity.current.generation === generation) setLocalError(bioXpErrorText(error));
        }
        finally { busyRef.current = false; onBusyChange(false); }
    }

    const gate = workflow?.gate;
    const gateId = workflow?.gate_id;
    return <>
        {jobId && <p className="break-all text-sm">Canonical job: {jobId}</p>}
        {(query.isError || discoveryError) && <p role="status">Workflow readback unavailable; checking again. Do not resubmit uncertain work.</p>}
        {query.data?.command && !('schema_version' in query.data && query.data.schema_version === 'bioxp.protocol_job_observation.v1') && <p role="status">Compact workflow observation unavailable on this robot; retained detail shown. Reopen to refresh.</p>}
        {pending && !job && <p role="status">Submission outcome not yet reconciled. Checking the original job; no automatic retry.</p>}
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
    </>;
}
