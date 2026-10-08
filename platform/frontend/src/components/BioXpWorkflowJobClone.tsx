import { useLayoutEffect, useRef, useState } from 'react';
import './BioXpWorkflowJobClone.css';
import { api } from '../lib/api';
import type { BioXpWorkflowJob } from '../lib/bioxpClient';
import { copyWorkflowValue, type PendingWorkflowRun } from '../lib/bioxpSavedWorkflowRun';
import { cloneBioXpWorkflowJob, type WorkflowJobClone } from '../lib/bioxpWorkflowPlan';

export type BioXpWorkflowJobCloneProps = {
    generation: number;
    connected: boolean;
    retained: PendingWorkflowRun[];
    onClone: (clone: WorkflowJobClone) => void;
    disabled?: boolean;
};

/** Demand-only authoring: no monitor, submission, template lookup or robot control. */
export function BioXpWorkflowJobClone({ generation, connected, retained, onClone, disabled = false }: BioXpWorkflowJobCloneProps) {
    const [open, setOpen] = useState(false);
    const [source, setSource] = useState<'retained' | 'remote'>('retained');
    const [jobId, setJobId] = useState('');
    const [rows, setRows] = useState<BioXpWorkflowJob[]>([]);
    const [listed, setListed] = useState(false);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState('');
    const epoch = useRef(0);
    const inFlight = useRef(false);
    // Fence both remote reads and pure-clone replies across connection changes/unmount.
    useLayoutEffect(() => {
        epoch.current++;
        inFlight.current = false;
        setBusy(false);
        setRows([]);
        setListed(false);
        setMessage('');
        return () => { epoch.current++; };
    }, [generation, connected, disabled]);

    function invalidate() {
        epoch.current++;
        inFlight.current = false;
        setBusy(false);
        setMessage('');
    }
    function select(nextSource: 'retained' | 'remote', id: string) {
        invalidate();
        setSource(nextSource);
        setJobId(id);
    }
    async function recent() {
        if (!connected || disabled || inFlight.current) return;
        const token = ++epoch.current;
        inFlight.current = true;
        setBusy(true);
        setMessage('');
        try {
            const result = await api.get<{ rows: BioXpWorkflowJob[] }>('/api/bioxp/protocols/jobs', {
                params: { expected_connection_generation: generation },
            });
            if (epoch.current !== token) return;
            setRows(result.data.rows);
            setListed(true);
        } catch (error) {
            if (epoch.current === token) setMessage(error instanceof Error ? error.message : 'Could not read recent jobs.');
        } finally {
            if (epoch.current === token) { inFlight.current = false; setBusy(false); }
        }
    }
    async function clone() {
        if (!jobId || disabled || inFlight.current || (source === 'remote' && !connected)) return;
        const applyClone = onClone; // Capture this editor owner; never adopt a newer callback after awaiting.
        const token = ++epoch.current;
        inFlight.current = true;
        setBusy(true);
        setMessage('');
        try {
            let document: Record<string, unknown> | null | undefined;
            if (source === 'retained') {
                document = retained.find(run => run.jobId === jobId)?.document;
            } else {
                const { data: job } = await api.get<BioXpWorkflowJob>(`/api/bioxp/protocols/jobs/${encodeURIComponent(jobId)}`, {
                    params: { expected_connection_generation: generation },
                });
                if (epoch.current !== token) return;
                if (job.job_id !== jobId) throw new Error('Original job identity mismatch; editor unchanged.');
                document = job.protocol?.document;
            }
            if (!document || typeof document !== 'object' || Array.isArray(document)) {
                throw new Error('Original job document unavailable; editor unchanged.');
            }
            const result = await cloneBioXpWorkflowJob(jobId, copyWorkflowValue(document));
            if (epoch.current !== token) return;
            if (!result.draft || result.issues.length) {
                setMessage(result.issues.map(issue => `${issue.step_id ? `${issue.step_id}: ` : ''}${issue.message}`).join('\n')
                    || 'This original job has no supported editable clone.');
                return;
            }
            applyClone(result);
            setMessage('Original job cloned into an unsaved draft. Nothing was run or saved.');
        } catch (error) {
            if (epoch.current === token) setMessage(error instanceof Error ? error.message : 'Could not clone original job.');
        } finally {
            if (epoch.current === token) { inFlight.current = false; setBusy(false); }
        }
    }
    return <section className="bioxp-job-clone" aria-label="Clone previous job">
        <button type="button" aria-expanded={open} onClick={() => { invalidate(); setOpen(!open); }}>Clone previous job</button>
        {open && <div className="bioxp-job-clone-body">
            <p>Use an original job document to create an unsaved draft. The original job and its monitoring remain unchanged.</p>
            <label>Retained original job <select value={source === 'retained' ? jobId : ''} onChange={event => select('retained', event.target.value)}>
                <option value="">Select a retained job</option>
                {retained.map(run => <option key={run.jobId} value={run.jobId}>{run.saved.name} — {run.jobId}</option>)}
            </select></label>
            <p>Retained documents can be cloned while the robot is disconnected.</p>
            <button type="button" disabled={!connected || disabled || busy} onClick={() => void recent()}>Load recent jobs</button>
            {listed && (rows.length ? <label>Recent original job <select value={source === 'remote' ? jobId : ''} onChange={event => select('remote', event.target.value)}>
                <option value="">Select a recent job</option>
                {rows.map(job => <option key={job.job_id} value={job.job_id}>{job.job_id} — {job.status}</option>)}
            </select></label> : <p>No recent jobs returned. An exact older job ID can still be read.</p>)}
            <label>Exact older job ID <input value={source === 'remote' ? jobId : ''} onChange={event => select('remote', event.target.value)} /></label>
            {!connected && <p>Connect to read robot history or an exact older job. Retained cloning remains available.</p>}
            <button type="button" disabled={disabled || busy || !jobId || (source === 'remote' && !connected)} onClick={() => void clone()}>Clone selected original job</button>
            {busy && <p role="status">Reading original job clone…</p>}
            {message && <p role="status" style={{ whiteSpace: 'pre-wrap' }}>{message}</p>}
        </div>}
    </section>;
}
