import type { BioXpDiagnostic, BioXpSourceStep } from '../lib/bioxpManualPipetting';

export const sourceDefaults = {
    source_load_tips: { operation: 'source_load_tips', tip_type: 50, pipette: -1, force_new_tip: false },
    source_mix: { operation: 'source_mix', volume_ul: 20, air_ul: 15, aspirate_speed: 100, dispense_speed: 20, aspirate_delay_ms: null, dispense_delay_ms: null, cycles: 2, mix_type: 'N', tip_dip: true },
    source_aspirate_air: { operation: 'source_aspirate_air', volume_ul: 5 },
    source_dispense_air: { operation: 'source_dispense_air', volume_ul: 5 },
    source_purge: { operation: 'source_purge', speed: 30, amp: false, ntd: false },
    diagnostic_pipette: { operation: 'diagnostic_pipette', diagnostic: { action: 'get_data' } },
} satisfies Record<BioXpSourceStep['operation'], BioXpSourceStep>;
export const diagnosticActions: BioXpDiagnostic['action'][] = ['aspirate', 'dispense', 'eject', 'plunger_up', 'plunger_down', 'dispense_all', 'diagnoses', 'initialize', 'get_data', 'last_error'];
export const sourceLabels: Record<BioXpSourceStep['operation'], string> = { source_load_tips: 'Load selected tips', source_mix: 'Advanced mix', source_aspirate_air: 'Aspirate air', source_dispense_air: 'Dispense air', source_purge: 'Purge', diagnostic_pipette: 'Pipette diagnostics' };
export function BioXpSourcePipettingEditor({ drafts, onChange, enabled, run }: { drafts: Record<BioXpSourceStep['operation'], BioXpSourceStep>; onChange: (step: BioXpSourceStep) => void; enabled: boolean; run: (operation: BioXpSourceStep['operation']) => void }) {
    return <div className="space-y-3 [&_select]:mt-1 [&_select]:block [&_select]:w-full [&_select]:rounded [&_select]:bg-slate-950 [&_select]:p-2">
        {Object.values(drafts).map(step => {
            const update = (fields: object) => onChange({ ...step, ...fields } as BioXpSourceStep);
            const num = (name: string, value: number | null, set: (value: number | null) => void, nullable = false) => <label key={name} className="block">{name.replace('Source mix ', '').replace('Source purge ', 'Purge ').replaceAll('_', ' ')}<input className="mt-1 block w-full rounded bg-slate-950 p-2" aria-label={name} type="number" step="any" placeholder={nullable ? 'Default' : undefined} value={value === null || Number.isNaN(value) ? '' : value} onChange={e => set(e.target.value === '' ? (nullable ? null : NaN) : Number(e.target.value))} /></label>;
            const bool = (name: string, value: boolean, set: (value: boolean) => void) => <label key={name}><input aria-label={name} type="checkbox" checked={value} onChange={e => set(e.target.checked)} />{name}</label>;
            return <details key={step.operation} className="min-w-0 rounded border border-slate-700 p-3"><summary className="cursor-pointer font-semibold">{sourceLabels[step.operation]}</summary><div className="mt-3 grid gap-3 sm:grid-cols-2">
                {step.operation === 'source_load_tips' && <>
                    <p className="text-sm sm:col-span-2">Physical loading sets tip alignment. If matching tips are already loaded and Force new tip is off, the existing alignment is kept. Selecting a different pipette here alone does not realign the tips.</p>
                    <label>Tip size<select aria-label="Tip size" value={step.tip_type} onChange={e => update({ tip_type: Number(e.target.value) })}><option value="50">T50</option><option value="200">T200</option></select></label>
                    <label>Pipettes to load<select aria-label="Pipettes to load" value={step.pipette} onChange={e => update({ pipette: Number(e.target.value) })}><option value="-1">All four</option>{[0,1,2,3].map(c => <option key={c} value={c}>Pipette {c + 1}</option>)}</select></label>
                    {bool('Force new tip', step.force_new_tip, force_new_tip => update({ force_new_tip }))}
                </>}
                {step.operation === 'source_mix' && <>
                    <p className="text-sm sm:col-span-2">Uses the robot’s built-in mixing procedure, not repeated liquid strokes. Leave delays blank to use the robot defaults.</p>
                    {(['volume_ul', 'air_ul', 'aspirate_speed', 'dispense_speed', 'aspirate_delay_ms', 'dispense_delay_ms', 'cycles'] as const).map(key => num(`Source mix ${key}`, step[key], value => update({ [key]: value }), key.endsWith('delay_ms')))}
                    <label>Mix type<select aria-label="Source mix type" value={step.mix_type} onChange={e => update({ mix_type: e.target.value })}>{['N','H','C'].map(v => <option key={v}>{v}</option>)}</select></label>
                    {bool('Dip tips while mixing', step.tip_dip, tip_dip => update({ tip_dip }))}
                </>}
                {(step.operation === 'source_aspirate_air' || step.operation === 'source_dispense_air') && num(`${sourceLabels[step.operation]} volume (µL)`, step.volume_ul, volume_ul => update({ volume_ul }))}
                {step.operation === 'source_purge' && <>{num('Source purge speed', step.speed, speed => update({ speed }))}{bool('Source purge AMP', step.amp, amp => update({ amp }))}{bool('Source purge NTD', step.ntd, ntd => update({ ntd }))}</>}
                {step.operation === 'diagnostic_pipette' && <>
                    <p className="text-sm sm:col-span-2">Plunger up/down moves the head in Z; it is not a liquid stroke. Tip checks vary by diagnostic.</p>
                    <label>Diagnostic action<select aria-label="Diagnostic action" value={step.diagnostic.action} onChange={e => {
                        const action = e.target.value as BioXpDiagnostic['action'];
                        const diagnostic: BioXpDiagnostic = action === 'aspirate' || action === 'dispense' ? { action, channels: [], volume_ul: 10, speed: 100 } : action === 'eject' ? { action, channels: [] } : action === 'plunger_up' || action === 'plunger_down' ? { action, steps: 100 } : { action };
                        update({ diagnostic });
                    }}>{diagnosticActions.map(a => <option key={a} value={a}>{a.replaceAll('_', ' ')}</option>)}</select></label>
                    {'channels' in step.diagnostic && [0,1,2,3].map(c => { const d = step.diagnostic as Extract<BioXpDiagnostic, { channels: number[] }>; return bool(`Diagnostic pipette ${c + 1} (ID ${c})`, d.channels.includes(c), checked => update({ diagnostic: { ...d, channels: checked ? [...d.channels, c].sort() : d.channels.filter(v => v !== c) } })); })}
                    {'volume_ul' in step.diagnostic && <>{num('Diagnostic volume (µL)', step.diagnostic.volume_ul, volume_ul => update({ diagnostic: { ...step.diagnostic, volume_ul } }))}{num('Diagnostic speed', step.diagnostic.speed, speed => update({ diagnostic: { ...step.diagnostic, speed } }))}</>}
                    {'steps' in step.diagnostic && num('Diagnostic Z steps', step.diagnostic.steps, steps => update({ diagnostic: { ...step.diagnostic, steps } }))}
                </>}
                <button className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35" type="button" disabled={!enabled} onClick={() => run(step.operation)}>{sourceLabels[step.operation]} now</button>
            </div></details>;
        })}
    </div>;
}
