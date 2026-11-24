import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { bioXpErrorText } from '../lib/bioxpClient';
import type { BioXpOperatorDashboard } from '../lib/bioxpClient';
import { compileImmediate, useBioXpImmediateRun } from '../lib/bioxpImmediateRun';

type Pipettes = BioXpOperatorDashboard['pipettes'];
const AUTO_KEY = 'bioxp.unknownTips.autoEject';

/** Tips on the head with no recorded type (after a restart or re-init): the robot refuses to pipette with them. */
export function unknownTipsLoaded(pipettes: Pipettes | undefined): boolean {
    if (!pipettes || typeof pipettes.tip_type !== 'number') return false;
    const known = pipettes.tip_type_known ?? [10, 50, 200].includes(pipettes.tip_type);
    return !known && (pipettes.channels ?? []).some(channel => channel.software_tip_loaded === true);
}

const ejectSteps = () => compileImmediate('Eject unknown tips', [{ step_id: 'eject', type: 'action', action: 'tip_eject', inputs: { channels: [0, 1, 2, 3] } }]);

/**
 * Card: shows the unknown-tip notice with Eject to waste and the auto-eject option.
 * Auto (mode="auto"): renders nothing; ejects once per occurrence while the option is on.
 */
export function BioXpUnknownTipGuard({ generation, connected, visible = true, pipettes, mode = 'card' }: {
    generation: number; connected: boolean; visible?: boolean; pipettes: Pipettes | undefined; mode?: 'card' | 'auto';
}) {
    const runner = useBioXpImmediateRun(generation, connected, visible);
    const [auto, setAuto] = useState(() => typeof localStorage !== 'undefined' && localStorage.getItem(AUTO_KEY) === 'on');
    const unknown = unknownTipsLoaded(pipettes);
    const fired = useRef<number | null>(null);
    useEffect(() => {
        const sync = () => setAuto(localStorage.getItem(AUTO_KEY) === 'on');
        window.addEventListener('storage', sync); window.addEventListener('bioxp-auto-eject', sync);
        return () => { window.removeEventListener('storage', sync); window.removeEventListener('bioxp-auto-eject', sync); };
    }, []);
    useEffect(() => {
        if (!unknown) { fired.current = null; return; }
        if (mode !== 'auto' || !auto || !connected || fired.current === generation) return;
        fired.current = generation;
        void runner.run(ejectSteps);
    }, [unknown, auto, connected, generation, mode]);
    if (mode === 'auto') return null;
    const toggle = (on: boolean) => { localStorage.setItem(AUTO_KEY, on ? 'on' : 'off'); setAuto(on); window.dispatchEvent(new Event('bioxp-auto-eject')); };
    const status = runner.job?.command?.status;
    const client = useQueryClient();
    const [size, setSize] = useState<'10' | '50' | '200'>('10');
    const [fresh, setFresh] = useState(false);
    const [setMessage, setSetMessage] = useState<string | null>(null);
    const setTips = async () => {
        setSetMessage(null);
        try {
            await api.post('/api/bioxp/loaded-tip-type', { expected_connection_generation: generation, tip_type: Number(size), used: !fresh });
            setSetMessage(`Recorded ${size} µL ${fresh ? 'fresh' : 'used'} tips on the guns.`);
            void client.invalidateQueries({ queryKey: ['bioxp'] });
        } catch (cause) { setSetMessage(bioXpErrorText(cause)); }
    };
    return <div className="mt-2 text-xs" data-unknown-tip-guard>
        {unknown && <div role="alert" className="flex flex-wrap items-center gap-3 rounded border border-amber-600 bg-amber-950/40 px-3 py-2">
            <span>Tips are on the head but their type is unknown. Pipetting is refused until they are ejected.</span>
            <button type="button" disabled={!runner.enabled} onClick={() => void runner.run(ejectSteps)}
                className="rounded bg-amber-700 px-3 py-1 disabled:opacity-40">{runner.pending ? 'Sending…' : 'Eject to waste'}</button>
            <span className="text-slate-300">or set what is on the guns:</span>
            <select aria-label="Tips on the guns" value={size} onChange={event => setSize(event.target.value as typeof size)} className="rounded bg-slate-900 px-2 py-1">
                <option value="10">10 µL</option><option value="50">50 µL</option><option value="200">200 µL</option></select>
            <label className="inline-flex items-center gap-1"><input type="checkbox" checked={fresh} onChange={event => setFresh(event.target.checked)} /> fresh, unused</label>
            <button type="button" disabled={!connected} onClick={() => void setTips()} className="rounded bg-cyan-800 px-3 py-1 disabled:opacity-40">Set tips</button>
        </div>}
        {setMessage && <p role="status">{setMessage}</p>}
        <label className="mt-1 inline-flex items-center gap-2 text-slate-400"><input type="checkbox" checked={auto} onChange={event => toggle(event.target.checked)} />
            Eject unknown tips automatically</label>
        {status && <span className="ml-3" role="status">Eject: {status}</span>}
        {runner.error && <p role="alert">{runner.error}</p>}
    </div>;
}
