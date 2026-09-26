import { useEffect, useRef, useState } from 'react';
import { bioXpErrorText } from '../lib/bioxpClient';
import { calibrationFields, decideCalibrationRun, readCalibrationRun, type CalibrationRun } from '../lib/bioxpCalibration';
import { PipetteOutcome, PipetteValue } from './BioXpPipetteResults';

export function BioXpCalibrationRun({ runId, generation, connected }: {
    runId?: string; generation: number; connected: boolean;
}) {
    const [draftId, setDraftId] = useState('');
    const [selectedId, setSelectedId] = useState(runId ?? '');
    const [run, setRun] = useState<CalibrationRun | null>(null);
    const [error, setError] = useState('');
    const [message, setMessage] = useState('');
    const [pending, setPending] = useState(false);
    const busy = useRef(false);
    const request = useRef(0);
    const current = useRef({ generation, connected, selectedId });
    current.current = { generation, connected, selectedId };
    const mounted = useRef(true);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; ++request.current; }; }, []);
    const valid = (token: number, id: string) => mounted.current && request.current === token
        && current.current.generation === generation && current.current.connected && current.current.selectedId === id;
    const matching = (value: CalibrationRun, id: string) => {
        if (value.run_id !== id) throw new Error('Calibration run identity mismatch; response was not applied.');
        return value;
    };
    async function refresh(id = selectedId) {
        const token = ++request.current;
        try {
            const value = matching(await readCalibrationRun(generation, id), id);
            if (valid(token, id)) { setRun(value); setError(''); }
        } catch (cause) { if (valid(token, id)) setError(`Run readback unavailable: ${bioXpErrorText(cause)}`); }
    }
    useEffect(() => {
        if (connected && selectedId) void refresh();
        return () => { ++request.current; };
        // Read only on identity/connection changes; never automatically retry decisions.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedId, generation, connected]);
    async function decide(decision: 'accept' | 'restore') {
        if (!connected || !selectedId || busy.current) return;
        busy.current = true; setPending(true); setError(''); setMessage('');
        const id = selectedId, token = ++request.current;
        try {
            let value: CalibrationRun;
            try {
                value = matching(await decideCalibrationRun(generation, id, decision), id);
            } finally {
                // Only the POST owns local exclusion. Passive readback must not
                // become a second admission lock for a later explicit decision.
                busy.current = false;
                if (mounted.current) setPending(false);
            }
            if (!valid(token, id)) return;
            setRun(value);
            setMessage(`Decision request returned: ${value.decision ?? 'not recorded'}. Checking durable run readback…`);
            try {
                const readback = matching(await readCalibrationRun(generation, id), id);
                if (valid(token, id)) {
                    setRun(readback);
                    setMessage(readback.decision === decision
                        ? `Decision read back: ${readback.decision}. Saved and active revisions are reported below.`
                        : `Requested ${decision}; readback decision is ${readback.decision ?? 'not recorded'}. Review robot outcome below.`);
                }
            } catch (cause) {
                if (valid(token, id)) { setMessage('Decision response retained; durable readback unavailable. Do not infer it failed or repeat automatically.'); setError(bioXpErrorText(cause)); }
            }
        } catch (cause) { if (valid(token, id)) setError(bioXpErrorText(cause)); }
    }
    return <section aria-label={runId ? `Calibration comparison ${runId}` : 'Recover calibration comparison'} className="space-y-2 rounded border border-slate-700 p-3">
        <h4>Calibration comparison</h4>
        {!runId && <form onSubmit={event => { event.preventDefault(); if (!busy.current) { setRun(null); setError(''); setMessage(''); setSelectedId(draftId); if (draftId === selectedId && connected && draftId) void refresh(); } }}>
            <label>Saved calibration run ID<input aria-label="Saved calibration run ID" value={draftId} onChange={event => setDraftId(event.target.value)} /></label>
            <button type="submit" disabled={!connected || pending || !draftId}>Load comparison</button>
        </form>}
        {selectedId && <>
            <p className="break-all">Run {selectedId} · connection {generation}</p>
            <p>Calibration saves and paired-tray Z Set apply in-process through the existing robot owner. A partial run may already have saved and applied stations; body completion is not operator acceptance or physical verification.</p>
            <p>Reject / restore replaces the FULL pre-run calibration, including liquid-calibration settings and any later calibration edits. It applies the restored calibration in-process; no restart or home is requested.</p>
            <button type="button" disabled={!connected || pending} onClick={() => void refresh()}>Refresh comparison</button>
            <button type="button" disabled={!connected || pending} onClick={() => void decide('accept')}>Accept calibration</button>
            <button type="button" disabled={!connected || pending} onClick={() => void decide('restore')}>Reject / restore full pre-run calibration</button>
            {run && <>
                <p>Recorded decision: {run.decision ?? 'Not decided'}</p>
                <PipetteOutcome result={run} />
                <Comparison run={run} />
            </>}
            {!connected && <p role="alert">This run is not connected to its original connection. No decision will be sent to another robot.</p>}
        </>}
        {pending && <p role="status">Submitting calibration decision…</p>}
        {message && <p role="status">{message}</p>}
        {error && <p role="alert">{error}</p>}
    </section>;
}

function Comparison({ run }: { run: CalibrationRun }) {
    const names = [...new Set([...(run.before?.positions ?? []), ...(run.after?.positions ?? [])].map(row => row.name))];
    return <>
        <p>Pre-run revision: {run.before?.revision_id ?? 'No prior saved revision'} · Run after revision: {run.after?.revision_id ?? 'No saved revision'}</p>
        <div className="overflow-x-auto"><table aria-label="Before and after calibration">
            <thead><tr><th>Station / field</th><th>Before run</th><th>After run</th></tr></thead>
            <tbody>{names.flatMap(name => calibrationFields.map(field => <tr key={`${name}:${field}`}>
                <th>{name} · {field}</th>
                <td>{run.before?.positions.find(row => row.name === name)?.[field] ?? 'Not reported'}</td>
                <td>{run.after?.positions.find(row => row.name === name)?.[field] ?? 'Not reported'}</td>
            </tr>))}</tbody>
        </table></div>
        <h5>Liquid calibration before run</h5><PipetteValue value={run.before?.liquid_calibration} />
        <h5>Liquid calibration after run</h5><PipetteValue value={run.after?.liquid_calibration} />
    </>;
}
