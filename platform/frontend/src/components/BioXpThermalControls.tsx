import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { bioXpErrorText, useInvokeBioXpOperatorAction, type BioXpOperatorActionSpec } from '../lib/bioxpClient';
import { bioXpReceiptFailureText } from '../lib/bioxpEvidencePresentation';
import { compileImmediate, useBioXpImmediateRun } from '../lib/bioxpImmediateRun';

type Reading = { board: number; axis: number; current_c?: number | null; pedestal_c?: number | null; fan_speed?: number | null; target_c?: number | null; tc_on?: boolean | null };
type Bank = { id: string; label: string; board: number; axis: number | null; family: 'chiller' | 'thermal'; tuning: boolean };
// Robot fan services: board 6 = thermal cycler, board 7 = chillers (axis 0 reagent, 1 output).
const CHILLERS: Bank[] = [
    { id: 'rc', label: 'Reagent chiller', board: 7, axis: 0, family: 'chiller', tuning: true },
    { id: 'oc', label: 'Output chiller', board: 7, axis: 1, family: 'chiller', tuning: true },
];
const CYCLER: Bank[] = [
    { id: 'nest', label: 'Block', board: 6, axis: 0, family: 'thermal', tuning: true },
    { id: 'lid', label: 'Heated lid', board: 6, axis: null, family: 'thermal', tuning: true },
    { id: 'pedestal', label: 'Pedestal', board: 6, axis: null, family: 'thermal', tuning: false },
];
const RANGE = { chiller: [-20, 40], thermal: [0, 120] } as const;
const temp = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(1)} °C` : null;

/**
 * Chiller and thermal cycler controls. Every button sends one existing robot
 * command through the operator action relay; the robot checks each request.
 * Setpoints start empty: nothing is prefilled or preset.
 */
export function BioXpThermalControls({ variant, generation, ownershipGeneration, connected, visible, actions, catalogLoading = false, door }: {
    variant: 'compact' | 'full'; generation: number; ownershipGeneration: number; connected: boolean; visible: boolean;
    actions: BioXpOperatorActionSpec[] | undefined; catalogLoading?: boolean; door?: ReactNode;
}) {
    const readings = useQuery({
        queryKey: ['bioxp', 'thermal-readings', generation],
        queryFn: async ({ signal }) => (await api.get<{ services?: Reading[] }>('/api/bioxp/operator-controls/thermal/readings', { signal })).data,
        enabled: connected && visible && generation > 0, retry: false, refetchOnWindowFocus: false,
        refetchInterval: connected && visible ? 10_000 : false, refetchIntervalInBackground: false,
    });
    const invoke = useInvokeBioXpOperatorAction();
    const heaters = useBioXpImmediateRun(generation, connected, visible);
    const [draft, setDraft] = useState<Record<string, string>>({});
    const [sent, setSent] = useState<string | null>(null);
    const action = (path: string) => (actions ?? []).find(row => row.kind === 'primitive' && row.informational_path === path);
    const reading = (bank: Bank) => bank.axis === null ? undefined : readings.data?.services?.find(row => row.board === bank.board && row.axis === bank.axis);
    const value = (key: string) => draft[key] ?? '';
    const number = (key: string, min: number, max: number) => {
        const text = value(key).trim(); const parsed = Number(text);
        return text !== '' && Number.isFinite(parsed) && parsed >= min && parsed <= max ? parsed : null;
    };
    const field = (key: string, label: string, placeholder: string, min: number, max: number, step = 'any') =>
        <input type="number" className="bx-th-in" aria-label={label} placeholder={placeholder} min={min} max={max} step={step}
            value={value(key)} onChange={event => setDraft(current => ({ ...current, [key]: event.target.value }))} />;
    const send = (path: string, inputs: Record<string, unknown>, label: string) => {
        const spec = action(path);
        if (!spec || invoke.isPending) return;
        setSent(label);
        invoke.mutate({ actionId: spec.action_id, connectionGeneration: generation, ownershipGeneration, inputs });
    };
    const button = (path: string, text: string, inputs: Record<string, unknown> | null, label: string, className?: string) => {
        const spec = action(path);
        const reason = !connected ? 'Connect to the robot first.' : catalogLoading ? 'Reading robot controls…' : !spec ? 'The robot does not list this control.'
            : spec.enabled !== true ? spec.disabled_reason ?? 'Robot control unavailable.' : inputs === null ? 'Enter a value in range.' : null;
        return <button type="button" className={className} aria-label={label} disabled={reason !== null || invoke.isPending} title={reason ?? label}
            onClick={() => { if (inputs !== null) send(path, inputs, label); }}>{text}</button>;
    };
    const setpoint = (bank: Bank) => {
        const [min, max] = RANGE[bank.family], key = `${bank.id}:temp`, target = number(key, min, max);
        return <>{field(key, `${bank.label} setpoint (°C)`, '°C', min, max, '0.1')}
            {button(`/${bank.family}/set_temp`, 'Set', target === null ? null : { bank: bank.id, target_temp_c: target }, `Set ${bank.label}`, 'bx-go')}
            {bank.family === 'chiller' && button('/chiller/pwm', 'Off', { bank: bank.id, pwm: 0 }, `${bank.label} off`)}</>;
    };
    const heatersOff = <button type="button" className="bx-danger" disabled={!heaters.enabled} aria-label="Heaters off"
        title={connected ? 'Turns off the thermal cycler block and heated lid.' : 'Connect to the robot first.'}
        onClick={() => { setSent(null); void heaters.run(() => compileImmediate('Heaters off', [
            { step_id: 'block-off', type: 'action', action: 'thermal_off', inputs: { bank: 'nest' } },
            { step_id: 'lid-off', type: 'action', action: 'thermal_off', inputs: { bank: 'lid' } }])); }}>Heaters off</button>;
    const refresh = <button type="button" disabled={!connected || readings.isFetching} onClick={() => void readings.refetch()}>Refresh</button>;
    const failure = bioXpReceiptFailureText(invoke.data);
    const heaterJob = heaters.job?.command?.status ?? heaters.job?.status;
    const status = <div className="bx-th-status">
        {readings.isError && <p role="status">Temperatures unavailable: {bioXpErrorText(readings.error)}</p>}
        {invoke.isPending && <p role="status">Sending {sent}…</p>}
        {sent && invoke.data && !invoke.isPending && <p role={failure ? 'alert' : 'status'}>{sent}: {invoke.data.status}{failure ? ` · ${failure}` : ''}</p>}
        {sent && invoke.error != null && <p role="alert">{sent}: {bioXpErrorText(invoke.error)}</p>}
        {heaters.pending && <p role="status">Sending Heaters off…</p>}
        {heaterJob && <p role="status">Heaters off: {heaterJob}</p>}
        {heaters.error && <p role="alert">Heaters off: {heaters.error}</p>}
    </div>;
    const now = (bank: Bank) => temp(reading(bank)?.current_c) ?? <span className="bx-th-muted">not reported</span>;

    if (variant === 'compact') {
        return <section className="bx-ui bx-card bx-thermal-compact" aria-label="Chillers and thermal cycler" data-testid="thermal-controls">
            <header className="bx-card-head"><h3>Chillers &amp; thermal cycler</h3>{refresh}</header>
            {[...CHILLERS, ...CYCLER.filter(bank => bank.id !== 'pedestal')].map(bank => {
                const row = reading(bank);
                return <div key={bank.id} className="bx-th-row" data-bank={bank.id}>
                    <div><strong>{bank.family === 'thermal' ? `TC ${bank.label.toLowerCase()}` : bank.label}</strong>
                        <small>{row ? `ped ${temp(row.pedestal_c)?.replace(' °C', '') ?? '—'} · fan ${row.fan_speed ?? '—'}` : 'not reported'}</small></div>
                    <span className="bx-th-now">{temp(row?.current_c) ?? '—'}</span>
                    <span className="bx-th-set">{setpoint(bank)}</span>
                </div>;
            })}
            <div className="bx-th-foot"><small>Fan, PWM and rates are on Robot controls.</small>{heatersOff}</div>
            {door}
            {status}
        </section>;
    }
    const tuning = (banks: Bank[], family: 'chiller' | 'thermal') => <details className="bx-th-fold">
        <summary>Fan, PWM and ramp rates</summary>
        <table><thead><tr><th>Bank</th><th>Fan (0–255)</th><th>PWM (0–100)</th><th>Cool −2–0 / heat 0–2 (°C/s)</th></tr></thead>
            <tbody>{banks.filter(bank => bank.tuning).map((bank, index) => {
                const fan = number(`${bank.id}:fan`, 0, 255), pwm = number(`${bank.id}:pwm`, 0, 100);
                const cool = number(`${bank.id}:cool`, -2, 0), heat = number(`${bank.id}:heat`, 0, 2);
                const sharedFan = family === 'thermal' && index > 0;
                return <tr key={bank.id}><th scope="row">{bank.label}</th>
                    <td>{sharedFan ? <span className="bx-th-muted">shared fan</span> : <>{field(`${bank.id}:fan`, `${bank.label} fan speed`, '0–255', 0, 255, '1')}
                        {button(`/${family}/fan`, 'Set fan', fan === null || !Number.isInteger(fan) ? null : family === 'chiller' ? { bank: bank.id, speed: fan } : { speed: fan }, `Set ${bank.label} fan`)}</>}</td>
                    <td>{field(`${bank.id}:pwm`, `${bank.label} PWM`, '%', 0, 100, '1')}
                        {button(`/${family}/pwm`, 'Set PWM', pwm === null || !Number.isInteger(pwm) ? null : { bank: bank.id, pwm }, `Set ${bank.label} PWM`)}</td>
                    <td>{field(`${bank.id}:cool`, `${bank.label} cool rate`, 'cool', -2, 0, '0.1')}{field(`${bank.id}:heat`, `${bank.label} heat rate`, 'heat', 0, 2, '0.1')}
                        {button(`/${family}/rates`, 'Set rates', cool === null || heat === null ? null : { bank: bank.id, cool_rate_c_s: cool, heat_rate_c_s: heat }, `Set ${bank.label} rates`)}</td>
                </tr>;
            })}</tbody></table>
    </details>;
    return <div className="bx-ui bx-thermal" data-testid="thermal-controls">
        <section className="bx-card" aria-label="Chillers">
            <header className="bx-card-head"><h3>Chillers</h3><div className="bx-card-actions">{refresh}
                {button('/chiller/baseline', 'Apply baseline', {}, 'Apply chiller baseline')}
                {button('/chiller/hard_reset', 'Reset controller', {}, 'Reset chiller controller', 'bx-warn')}</div></header>
            <table><thead><tr><th>Bank</th><th>Now</th><th>Pedestal</th><th>Fan</th><th className="bx-th-right">Set to (−20 to 40 °C)</th></tr></thead>
                <tbody>{CHILLERS.map(bank => <tr key={bank.id} data-bank={bank.id}><th scope="row">{bank.label}</th><td className="bx-th-now">{now(bank)}</td>
                    <td>{temp(reading(bank)?.pedestal_c) ?? '—'}</td><td>{reading(bank)?.fan_speed ?? '—'}</td><td className="bx-th-set">{setpoint(bank)}</td></tr>)}</tbody></table>
            {tuning(CHILLERS, 'chiller')}
        </section>
        <section className="bx-card" aria-label="Thermal cycler">
            <header className="bx-card-head"><h3>Thermal cycler</h3><div className="bx-card-actions">
                {button('/thermal/baseline', 'Apply baseline', {}, 'Apply thermal cycler baseline')}
                {button('/thermal/fast_profile', 'Fast profile', {}, 'Apply thermal cycler fast profile')}
                {button('/thermal/hard_reset', 'Reset controller', {}, 'Reset thermal cycler controller', 'bx-warn')}
                {heatersOff}</div></header>
            <table><thead><tr><th>Bank</th><th>Now</th><th>Fan</th><th className="bx-th-right">Set to (0 to 120 °C)</th></tr></thead>
                <tbody>{CYCLER.map(bank => <tr key={bank.id} data-bank={bank.id}><th scope="row">{bank.label}</th>
                    <td className="bx-th-now">{bank.id === 'pedestal' ? temp(reading(CYCLER[0])?.pedestal_c) ?? <span className="bx-th-muted">not reported</span> : now(bank)}</td>
                    <td>{reading(bank)?.fan_speed ?? '—'}</td><td className="bx-th-set">{setpoint(bank)}</td></tr>)}</tbody></table>
            {tuning(CYCLER, 'thermal')}
        </section>
        {status}
    </div>;
}
