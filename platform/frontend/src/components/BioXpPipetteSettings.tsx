import { useEffect, useRef, useState } from 'react';
import { bioXpErrorText } from '../lib/bioxpClient';
import { readCalibrationSettings, type CalibrationSettings } from '../lib/bioxpCalibration';
import { pipetteFlags, readOperationParameters, saveOperationParameters, setManualTipTray,
    type OperationParameters, type PipetteFlag, type TipTray } from '../lib/bioxpPipetteSettings';

export function BioXpPipetteSettings({ generation, connected }: { generation: number; connected: boolean }) {
    const [tray, setTray] = useState<TipTray>(1);
    const [calibration, setCalibration] = useState<CalibrationSettings | null>(null);
    const [parameters, setParameters] = useState<OperationParameters | null>(null);
    const [draft, setDraft] = useState<Partial<Record<PipetteFlag, boolean>>>({});
    const [measurement, setMeasurement] = useState<number | null>(null);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState('');
    const [message, setMessage] = useState('');
    const epoch = useRef(0);
    const busy = useRef(false);
    useEffect(() => {
        const token = ++epoch.current;
        setCalibration(null); setParameters(null); setDraft({}); setMeasurement(null); setError(''); setMessage('');
        if (connected) {
            void readOperationParameters(generation).then(value => { if (epoch.current === token) setParameters(value); })
                .catch(cause => { if (epoch.current === token) setError(bioXpErrorText(cause)); });
        }
        return () => { ++epoch.current; };
    }, [generation, connected]);
    async function refresh() {
        const token = epoch.current;
        try {
            const [positions, flags] = await Promise.all([readCalibrationSettings(generation), readOperationParameters(generation)]);
            if (epoch.current === token) { setCalibration(positions); setParameters(flags); setError(''); }
        } catch (cause) { if (epoch.current === token) setError(bioXpErrorText(cause)); }
    }
    async function run(action: 'set' | 'flags') {
        if (!connected || busy.current) return;
        busy.current = true; setPending(true); setError(''); setMessage('');
        const token = epoch.current;
        let committed = false;
        try {
            if (action === 'set') {
                const result = await setManualTipTray(generation, tray);
                committed = true;
                if (epoch.current !== token) return;
                setMeasurement(result.measured_z_steps);
                const readback = await readCalibrationSettings(generation);
                if (epoch.current !== token) return;
                setCalibration(readback);
                setMessage(result.committed_revision_id && result.committed_revision_id !== readback.saved_revision_id
                    ? `Manual Set returned revision ${result.committed_revision_id}; latest saved revision ${readback.saved_revision_id ?? 'unavailable'} differs. Active revision: ${readback.active_revision_id ?? 'baseline'}.`
                    : `Manual Set returned; saved revision ${readback.saved_revision_id ?? 'unavailable'} read back. Active revision: ${readback.active_revision_id ?? 'baseline'}.`);
            } else {
                if (!Object.keys(draft).length) { setError('Change a pipette flag before saving.'); return; }
                await saveOperationParameters(generation, draft);
                committed = true;
                if (epoch.current !== token) return;
                const readback = await readOperationParameters(generation);
                if (epoch.current !== token) return;
                setParameters(readback); setDraft({});
                setMessage('Operation parameters saved and read back.');
            }
        } catch (cause) { if (epoch.current === token) setError(`${committed ? 'Mutation returned but readback failed: ' : ''}${bioXpErrorText(cause)}`); }
        finally { busy.current = false; if (epoch.current === token) setPending(false); }
    }
    const names = tray <= 2 ? ['TECANRACK1', 'TECANRACK2'] : ['TECANRACK3', 'TECANRACK4'];
    return <section aria-label="Pipette settings" className="mt-4 space-y-3 rounded border border-slate-700 p-4">
        <h3>Manual tip-tray Set and pipette settings</h3>
        <p>Manual Set reads the current Z and saves the same zLow to both selected rack rows. The existing robot owner applies the paired calibration in-process. It does not pick up a tip, restart or home.</p>
        <label>Tip tray<select aria-label="Tip tray for manual Set" value={tray} disabled={pending}
            onChange={e => { setTray(Number(e.target.value) as TipTray); setMeasurement(null); }}>
            {([1, 2, 3, 4] as const).map(value => <option key={value} value={value}>Tray {value}</option>)}
        </select></label>
        <p>Paired saved rows: {names.join(', ')}. Measured current Z: {measurement ?? 'not measured in this view'}.</p>
        <p>Active revision: {calibration?.active_revision_id ?? 'captured baseline'}; saved revision: {calibration?.saved_revision_id ?? 'captured baseline'}.
            {calibration?.pending_restart ? ' Saved configuration pending next ordinary startup.' : ' No pending saved revision reported.'}</p>
        <table><thead><tr><th>Rack</th><th>Saved zLow (steps)</th><th>Active zLow (steps)</th></tr></thead><tbody>
            {names.map(name => <tr key={name}><th>{name}</th><td>{calibration?.saved_positions.find(row => row.name === name)?.zLow ?? 'unavailable'}</td>
                <td>{calibration?.active_positions.find(row => row.name === name)?.zLow ?? 'unavailable'}</td></tr>)}
        </tbody></table>
        <button type="button" disabled={!connected || pending} onClick={() => void run('set')}>Set current Z for selected tray pair</button>
        <h4>Pipette operation flags</h4>
        <p>Only source-consumed pipette flags; unrelated operation parameters are not edited here.</p>
        {pipetteFlags.map(flag => <label key={flag} className="block"><input type="checkbox" aria-label={flag} disabled={pending || !parameters}
            checked={draft[flag] ?? (parameters?.runtime_values?.[flag] === true)}
            onChange={e => setDraft(previous => ({ ...previous, [flag]: e.target.checked }))} />{flag}</label>)}
        <button type="button" disabled={!connected || pending || !parameters} onClick={() => void run('flags')}>Save pipette flags</button>
        <button type="button" disabled={!connected || pending} onClick={() => void refresh()}>Read pipette settings</button>
        {pending && <p role="status">Submitting and reading back…</p>}
        {message && <p role="status">{message}</p>}
        {error && <p role="alert">{error}</p>}
    </section>;
}
