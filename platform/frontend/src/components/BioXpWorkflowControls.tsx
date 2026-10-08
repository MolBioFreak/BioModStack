import { useBioXpDocumentVisible } from './BioXpObservationVisibility';
import { useRef, useState } from 'react';
import { useBioXpWorkflowMutationOwner } from './BioXpWorkflowMutationOwner';
import { definiteMethodRefusal } from '../lib/bioxpMethods';
import {
    bioXpErrorText, useBioXpWorkflowJobs,
    useSubmitBioXpProtocol,
    type BioXpWorkflowInput, type BioXpWorkflowJob,
} from '../lib/bioxpClient';

import { BioXpWorkflowJobMonitor } from './BioXpWorkflowJobMonitor';

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

export function BioXpWorkflowControls({ generation, connected, controlsEnabled, visible = true }: {
    generation: number; connected: boolean; controlsEnabled: boolean; visible?: boolean;
}) {
    const documentVisible = useBioXpDocumentVisible();
    const jobs = useBioXpWorkflowJobs(generation, connected && visible && documentVisible);
    const [selection, setSelection] = useState<{ name: string; input: BioXpWorkflowInput } | null>(null);
    const [selectionError, setSelectionError] = useState<string | null>(null);
    const [selectedJob, setSelectedJob] = useState<{ generation: number; id: string } | null>(null);
    const [attempt, setAttempt] = useState<{ generation: number; key: string; jobId: string; refused?: boolean } | null>(null);
    const [acceptedJob, setAcceptedJob] = useState<{ generation: number; job: BioXpWorkflowJob } | null>(null);
    const [localError, setLocalError] = useState<string | null>(null);
    const selectionVersion = useRef(0);
    const currentGeneration = useRef(generation);
    currentGeneration.current = generation;
    const submit = useSubmitBioXpProtocol();
    const currentAttempt = attempt?.generation === generation ? attempt : null;
    const listedActive = jobs.data?.find(job => job.command && (!job.command.terminal || job.command.status === 'ambiguous'));
    const jobId = (selectedJob?.generation === generation ? selectedJob.id : null)
        ?? currentAttempt?.jobId ?? listedActive?.job_id ?? null;
    const { busy, setBusy, busyRef } = useBioXpWorkflowMutationOwner();
    const maySubmit = connected && !busy && selection !== null;

    async function submitSelected() {
        if (!maySubmit || !selection || busyRef.current) return;
        busyRef.current = true; setBusy(true);
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
            setLocalError(null);
            const result = await submit.mutateAsync({ ...input, dry_run: false, idempotency_key: key, expected_connection_generation: generation });
            if (currentGeneration.current === submittedGeneration) {
                setAcceptedJob({ generation, job: result });
                setSelectedJob({ generation, id: result.job_id });
                void jobs.refetch();
            }
        } catch (error) { if (currentGeneration.current === submittedGeneration) {
            if (definiteMethodRefusal(error)) setAttempt(value => value ? { ...value, refused: true } : value);
            setLocalError(bioXpErrorText(error));
        } }
        finally { busyRef.current = false; setBusy(false); }
    }

    return <section aria-label="Prepared workflow" className="space-y-3 rounded-xl border border-slate-800 bg-slate-950/70 p-4">
        <h2 className="text-lg font-semibold">Prepared workflow</h2>
        <p className="text-sm text-slate-400">Select an existing robot request with its prepared input, manifest and preflight. No recipe generation. Thermal and selected vision dependencies remain subject to robot support checks.</p>
        <label className="block text-sm">Prepared request file
            <input type="file" accept=".json,application/json" disabled={busy} onChange={async event => {
                const file = event.currentTarget.files?.[0];
                const version = ++selectionVersion.current;
                setSelection(null); setSelectionError(null);
                if (!file) return;
                try {
                    const input = selectedInput(await file.text());
                    if (version === selectionVersion.current) setSelection({ name: file.name, input });
                } catch (error) { if (version === selectionVersion.current) setSelectionError(bioXpErrorText(error)); }
            }} />
        </label>
        {selection && <p className="text-sm">Selected: {selection.name} · {selection.input.source_type}</p>}
        {selectionError && <p role="alert">{selectionError}</p>}
        <button type="button" className={buttonClass} disabled={!maySubmit} onClick={() => void submitSelected()}>Submit prepared workflow</button>
        {currentAttempt && <p className="break-all text-xs">Original submission key: {currentAttempt.key}</p>}
        {jobs.data && jobs.data.length > 0 && <label className="block text-sm">Robot workflow
            <select value={jobId ?? ''} onChange={event => { setSelectedJob({ generation, id: event.target.value }); }}>
                <option value="" disabled>Select a robot workflow</option>
                {currentAttempt && !jobs.data.some(item => item.job_id === currentAttempt.jobId) && <option value={currentAttempt.jobId}>{currentAttempt.jobId}</option>}
                {jobs.data.map(item => <option key={item.job_id} value={item.job_id}>{item.job_id} · {item.command?.status ?? item.status}</option>)}
            </select>
        </label>}
        {!(currentAttempt?.jobId === jobId && currentAttempt.refused) && <BioXpWorkflowJobMonitor jobId={jobId} generation={generation}
            connected={connected} controlsEnabled={controlsEnabled} visible={visible && !(currentAttempt?.jobId === jobId && currentAttempt.refused)} submitting={busy}
            busyRef={busyRef} onBusyChange={setBusy} pending={!!currentAttempt && !currentAttempt.refused} discoveryError={jobs.isError}
            acceptedJob={acceptedJob?.generation === generation ? acceptedJob.job : null} />}
        {currentAttempt?.refused && <p>Definite pre-admission refusal; nothing started for this submission. No nonexistent-job polling.</p>}
        {localError && <p role="alert">{localError} No automatic submission or control retry; reconcile robot state before further action.</p>}
    </section>;
}
