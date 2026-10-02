import { useRef, useState } from 'react';
import { bioXpErrorText, useSubmitBioXpProtocol, type BioXpWorkflowJob } from '../lib/bioxpClient';
import { previewBioXpWorkflow, type SavedWorkflowSnapshot } from '../lib/bioxpWorkflowPlan';
import { canonicalWorkflowJobId, copyWorkflowValue, readPendingWorkflowRuns, retainPendingWorkflowRun,
    type PendingWorkflowRun } from '../lib/bioxpSavedWorkflowRun';
import { BioXpWorkflowJobMonitor } from './BioXpWorkflowJobMonitor';

export function BioXpSavedWorkflowRun({ saved, generation, connected, controlsEnabled }: {
    saved: SavedWorkflowSnapshot | null; generation: number; connected: boolean; controlsEnabled: boolean;
}) {
    const [retained] = useState(readPendingWorkflowRuns);
    const [runs, setRuns] = useState(retained.runs);
    const [activeId, setActiveId] = useState<string | null>(retained.runs.at(-1)?.jobId ?? null);
    const [warning, setWarning] = useState(retained.warning);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [ack, setAck] = useState(false);
    const [accepted, setAccepted] = useState<{ generation: number; job: BioXpWorkflowJob } | null>(null);
    const busyRef = useRef(false);
    const authority = useRef({ generation, connected });
    authority.current = { generation, connected };
    const submit = useSubmitBioXpProtocol();
    const active = runs.find(run => run.jobId === activeId) ?? null;
    async function runSaved() {
        if (!saved || !connected || !ack || busyRef.current) return;
        busyRef.current = true; setBusy(true); setError(null);
        // Capture the exact successful saved readback, not the mutable editor or a later template update.
        const snapshot = copyWorkflowValue(saved);
        const submittedGeneration = generation;
        try {
            const key = crypto.randomUUID();
            const jobId = await canonicalWorkflowJobId(key);
            const preview = await previewBioXpWorkflow(snapshot.draft, `saved-workflow-${key}`);
            if (!preview.document) throw new Error(preview.issues.map(issue => `${issue.step_id ?? 'Workflow'}: ${issue.message}`).join('; ') || 'No native document was composed.');
            if (authority.current.generation !== submittedGeneration || !authority.current.connected) {
                throw new Error('Connection changed during composition. Nothing was submitted.');
            }
            const document = copyWorkflowValue(preview.document);
            document.metadata = { ...(document.metadata as Record<string, unknown> | undefined),
                bms_saved_workflow: copyWorkflowValue(snapshot) };
            const attempt: PendingWorkflowRun = { version: 1, key, jobId, generation: submittedGeneration,
                saved: snapshot, document };
            // Retain identity and exact snapshot BEFORE POST. Storage failure is evidence, not a motion gate.
            setWarning(retainPendingWorkflowRun(attempt));
            setRuns(previous => [...previous, attempt].slice(-8)); setActiveId(jobId); setAccepted(null);
            const job = await submit.mutateAsync({ source_type: 'native', document: copyWorkflowValue(document),
                live_execution_ack: true, dry_run: false, idempotency_key: key,
                expected_connection_generation: submittedGeneration });
            if (job.job_id !== jobId) throw new Error('Submission returned a different job identity; checking only the original canonical job.');
            if (authority.current.generation === submittedGeneration) setAccepted({ generation: submittedGeneration, job });
        } catch (cause) { setError(bioXpErrorText(cause)); }
        finally { busyRef.current = false; setBusy(false); }
    }
    return <section aria-label="Saved workflow run" className="space-y-3">
        <h3>Run saved workflow</h3>
        <p>Runs the last saved readback, not unsaved edits. Native controller checks remain authoritative. Run starts live execution; it does not connect or prepare the robot.</p>
        <p>{saved ? `Saved selection: ${saved.name}` : 'Save or open a workflow to run its saved snapshot.'}</p>
        <label className="block"><input type="checkbox" className="mr-2" checked={ack} onChange={event => setAck(event.target.checked)} /> Acknowledge live robot execution</label>
        <button type="button" className="bioxp-primary" disabled={!saved || !connected || !ack || busy} onClick={() => void runSaved()}>Run saved workflow</button>
        {warning && <p role="status">{warning}</p>}
        {error && <p role="alert">{error} No automatic submission retry. Reconcile the original job before starting another run.</p>}
        {runs.length > 0 && <label>Retained original run<select value={activeId ?? ''} onChange={event => setActiveId(event.target.value)}>
            {runs.map(run => <option key={run.jobId} value={run.jobId}>{run.saved.name} · {run.jobId}</option>)}
        </select></label>}
        {active && <>
            <p>Submitted saved snapshot: {active.saved.name} · template {active.saved.id}</p>
            <p className="break-all">Original submission key: {active.key}</p>
            <p>Original connection generation: {active.generation}; current connection generation: {generation}. Readback targets the original job only.</p>
            <details><summary>Exact run snapshot</summary><pre>{JSON.stringify(active.saved, null, 2)}</pre></details>
            <BioXpWorkflowJobMonitor jobId={active.jobId} generation={generation}
                connected={connected} controlsEnabled={controlsEnabled} submitting={busy} busyRef={busyRef} onBusyChange={setBusy} pending
                acceptedJob={accepted?.generation === generation ? accepted.job : null} />
        </>}
    </section>;
}
