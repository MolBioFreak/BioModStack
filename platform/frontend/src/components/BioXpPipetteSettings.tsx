import { useEffect, useRef, useState } from 'react';
import { bioXpErrorText } from '../lib/bioxpClient';
import { useCalibrationObservation, useOperationObservation, useInvalidateSettings } from '../lib/bioxpSettingsObservations';
import { pipetteFlags, saveOperationParameters, setManualTipTray,
    type PipetteFlag, type TipTray } from '../lib/bioxpPipetteSettings';

export function BioXpPipetteSettings({ generation, connected, visible = true }: { generation: number; connected: boolean; visible?: boolean }) {
    const [tray, setTray] = useState<TipTray>(1);
    const positions = useCalibrationObservation(generation, connected && visible);
    const flags = useOperationObservation(generation, connected && visible);
    const invalidate = useInvalidateSettings(generation);
    const calibration = positions.data;
    const parameters = flags.data;
    const [draft, setDraft] = useState<Partial<Record<PipetteFlag, boolean>>>({});
    const [measurement, setMeasurement] = useState<number | null>(null);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState('');
    const [message, setMessage] = useState('');
    const epoch = useRef(0);
    const busy = useRef(false);

    useEffect(() => {
        ++epoch.current;
        setDraft({}); setMeasurement(null); setError(''); setMessage('');
        return () => { ++epoch.current; };
    }, [generation, connected]);
    async function refresh() { await Promise.all([positions.refetch(), flags.refetch()]); }
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
                void invalidate('calibration');
                const readback = (await positions.refetch({ throwOnError: true, cancelRefetch: false })).data!;
                if (epoch.current !== token) return;

                setMessage(result.committed_revision_id && result.committed_revision_id !== readback.saved_revision_id
                    ? `Manual Set returned revision ${result.committed_revision_id}; latest saved revision ${readback.saved_revision_id ?? 'unavailable'} differs. Active revision: ${readback.active_revision_id ?? 'baseline'}.`
                    : `Manual Set returned; saved revision ${readback.saved_revision_id ?? 'unavailable'} read back. Active revision: ${readback.active_revision_id ?? 'baseline'}.`);
            } else {
                if (!Object.keys(draft).length) { setError('Change a pipette flag before saving.'); return; }
                await saveOperationParameters(generation, draft);
                committed = true;
                if (epoch.current !== token) return;
                void invalidate('operations');
                await flags.refetch({ throwOnError: true, cancelRefetch: false });
                if (epoch.current !== token) return;
                setDraft({});
                setMessage('Operation parameters saved and read back.');
            }
        } catch (cause) { if (epoch.current === token) setError(`${committed ? 'Mutation returned but readback failed: ' : ''}${bioXpErrorText(cause)}`); }
        finally { busy.current = false; if (epoch.current === token) setPending(false); }
    }
    const names = tray <= 2 ? ['TECANRACK1', 'TECANRACK2'] : ['TECANRACK3', 'TECANRACK4'];
    return <section aria-label="Pipette settings" className="mt-4 space-y-3 [&_button]:mr-2 [&_button]:rounded [&_button]:bg-slate-800 [&_button]:px-3 [&_button]:py-2 [&_button:disabled]:opacity-35">
        <div hidden={!visible}>
        <h3>Tip-tray height & pipette settings</h3>
        <p>Set saves the current head height for both racks in the selected pair. It does not pick up tips, restart or home.</p>
        <label className="flex items-center gap-3">Tip tray<select className="rounded bg-slate-950 p-2" aria-label="Tip tray for manual Set" value={tray} disabled={pending}
            onChange={e => { setTray(Number(e.target.value) as TipTray); setMeasurement(null); }}>
            {([1, 2, 3, 4] as const).map(value => <option key={value} value={value}>Tray {value}</option>)}
        </select></label>
        <p>Paired saved rows: {names.join(', ')}. Measured current Z: {measurement ?? 'not measured in this view'}.</p>
        <p>Active revision: {calibration ? calibration.active_revision_id ?? 'captured baseline' : 'not read'}; saved revision: {calibration ? calibration.saved_revision_id ?? 'captured baseline' : 'not read'}.
            {calibration?.pending_restart ? ' Saved configuration pending next ordinary startup.' : ' No pending saved revision reported.'}</p>
        <table className="w-full text-left text-sm"><thead><tr><th>Rack</th><th>Saved zLow (steps)</th><th>Active zLow (steps)</th></tr></thead><tbody>
            {names.map(name => <tr key={name}><th>{name}</th><td>{calibration?.saved_positions.find(row => row.name === name)?.zLow ?? 'unavailable'}</td>
                <td>{calibration?.active_positions.find(row => row.name === name)?.zLow ?? 'unavailable'}</td></tr>)}
        </tbody></table>
        <button type="button" disabled={!connected || pending} onClick={() => void run('set')}>Set current Z for selected tray pair</button>
        <h4>Pipette operation flags</h4>
        <p>Changes apply only to pipette operations.</p>
        {pipetteFlags.map(flag => <label key={flag} className="block"><input type="checkbox" aria-label={flag} disabled={pending || !parameters}
            checked={draft[flag] ?? (parameters?.runtime_values?.[flag] === true)}
            onChange={e => setDraft(previous => ({ ...previous, [flag]: e.target.checked }))} />{flag === 'CheckForStaticTipLoss' ? 'Check for lost tips' : 'Log pressure'}</label>)}
        <button type="button" disabled={!connected || pending || !parameters} onClick={() => void run('flags')}>Save pipette flags</button>
        <button type="button" disabled={!connected || pending} onClick={() => void refresh()}>Read pipette settings</button>
        </div>
        <p hidden={!visible} role="status">Settings observation: {positions.isFetching || flags.isFetching ? 'loading' : positions.isError || flags.isError ? 'read error — last observation retained' : calibration && parameters ? 'loaded' : 'not read'}.</p>
        {positions.error && <p role="alert">{bioXpErrorText(positions.error)}</p>}
        {flags.error && <p role="alert">{bioXpErrorText(flags.error)}</p>}
        {pending && <p role="status">Submitting and reading back…</p>}
        {message && <p role="status">{message}</p>}
        {error && <p role="alert">{error}</p>}
    </section>;
}
