import { isDraftObject, mergeDraftEdits, type DraftObject, type NativeDraft } from '../lib/bioxpWorkflowDraft';
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
// Visual projection only: missing fields are blank, never filled with source
// defaults. Original stored intents are retained by the owning row editor.
export function sourceEditorDraft(op: BioXpSourceStep['operation'], value: DraftObject): NativeDraft<BioXpSourceStep> {
    const blank = (sample: DraftObject, input: DraftObject): DraftObject => Object.fromEntries(Object.entries(sample).map(([key, example]) => {
        const v = input[key];
        if (key === 'operation' || key === 'action') return [key, typeof v === 'string' ? v : ''];
        if (Array.isArray(example)) return [key, Array.isArray(v) ? v.filter(n => typeof n === 'number') : []];
        if (typeof example === 'boolean') return [key, v === true];
        return [key, typeof v === 'number' || typeof v === 'string' || v === null ? v : ''];
    }));
    if (op !== 'diagnostic_pipette') return blank(sourceDefaults[op] as DraftObject, { ...value, operation: op }) as unknown as NativeDraft<BioXpSourceStep>;
    const d = isDraftObject(value.diagnostic) ? value.diagnostic : {};
    const sample: DraftObject = d.action === 'aspirate' || d.action === 'dispense' ? { action: '', channels: [], volume_ul: '', speed: '' }
        : d.action === 'eject' ? { action: '', channels: [] } : d.action === 'plunger_up' || d.action === 'plunger_down' ? { action: '', steps: '' } : { action: '' };
    return { operation: op, diagnostic: blank(sample as DraftObject, d) } as unknown as NativeDraft<BioXpSourceStep>;
}
export function BioXpSourcePipettingEditor({ drafts, onChange, enabled, run }: { drafts: Record<BioXpSourceStep['operation'], NativeDraft<BioXpSourceStep>>; onChange: (step: NativeDraft<BioXpSourceStep>) => void; enabled: boolean; run: (operation: BioXpSourceStep['operation']) => void }) {
    return <div className="space-y-3 [&_select]:mt-1 [&_select]:block [&_select]:w-full [&_select]:rounded [&_select]:bg-slate-950 [&_select]:p-2">
        {Object.values(drafts).map(raw => {
            const step = sourceEditorDraft(raw.operation, raw as unknown as DraftObject);
            const update = (fields: object) => onChange(mergeDraftEdits(raw as unknown as DraftObject, step as unknown as DraftObject, { ...step, ...fields } as unknown as DraftObject) as unknown as NativeDraft<BioXpSourceStep>);
            const num = (name: string, value: number | string | null | undefined, set: (value: number | string | null) => void, nullable = false) => <label key={name} className="block">{name.replace('Source mix ', '').replace('Source purge ', 'Purge ').replaceAll('_', ' ')}<input className="mt-1 block w-full rounded bg-slate-950 p-2" aria-label={name} type="number" step="any" placeholder={nullable ? 'Default' : undefined} value={value ?? ''} onChange={e => set(e.target.value)} /></label>;
            const bool = (name: string, value: boolean, set: (value: boolean) => void) => <label key={name}><input aria-label={name} type="checkbox" checked={value ?? false} onChange={e => set(e.target.checked)} />{name}</label>;
            return <details key={step.operation} className="min-w-0 rounded border border-slate-700 p-3"><summary className="cursor-pointer font-semibold">{sourceLabels[step.operation]}</summary><div className="mt-3 grid gap-3 sm:grid-cols-2">
                {step.operation === 'source_load_tips' && <>
                    <p className="text-sm sm:col-span-2">Physical loading sets tip alignment. If matching tips are already loaded and Force new tip is off, the existing alignment is kept. Selecting a different pipette here alone does not realign the tips.</p>
                    <label>Tip size<select aria-label="Tip size" value={step.tip_type} onChange={e => update({ tip_type: e.target.value === '' ? '' : Number(e.target.value) })}><option value="">Select tip size</option><option value="50">T50</option><option value="200">T200</option></select></label>
                    <label>Pipettes to load<select aria-label="Pipettes to load" value={step.pipette} onChange={e => update({ pipette: e.target.value === '' ? '' : Number(e.target.value) })}><option value="">Select pipettes</option><option value="-1">All four</option>{[0,1,2,3].map(c => <option key={c} value={c}>Pipette {c + 1}</option>)}</select></label>
                    {bool('Force new tip', step.force_new_tip, force_new_tip => update({ force_new_tip }))}
                </>}
                {step.operation === 'source_mix' && <>
                    <p className="text-sm sm:col-span-2">Uses the robot’s built-in mixing procedure, not repeated liquid strokes. Leave delays blank to use the robot defaults.</p>
                    {(['volume_ul', 'air_ul', 'aspirate_speed', 'dispense_speed', 'aspirate_delay_ms', 'dispense_delay_ms', 'cycles'] as const).map(key => num(`Source mix ${key}`, step[key], value => update({ [key]: value }), key.endsWith('delay_ms')))}
                    <label>Mix type<select aria-label="Source mix type" value={step.mix_type} onChange={e => update({ mix_type: e.target.value })}><option value="">Select mix type</option>{['N','H','C'].map(v => <option key={v}>{v}</option>)}</select></label>
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
                    }}><option value="">Select diagnostic action</option>{diagnosticActions.map(a => <option key={a} value={a}>{a.replaceAll('_', ' ')}</option>)}</select></label>
                    {['aspirate', 'dispense', 'eject'].includes(step.diagnostic.action) && [0,1,2,3].map(c => {
                        const d = step.diagnostic as NativeDraft<Extract<BioXpDiagnostic, { channels: number[] }>>;
                        const channels = Array.isArray(d.channels) ? d.channels : [];
                        return bool(`Diagnostic pipette ${c + 1} (ID ${c})`, channels.includes(c), checked => update({ diagnostic: { ...d, channels: checked ? [...channels, c].sort() : channels.filter(v => v !== c) } }));
                    })}
                    {(step.diagnostic.action === 'aspirate' || step.diagnostic.action === 'dispense') && <>{num('Diagnostic volume (µL)', step.diagnostic.volume_ul, volume_ul => update({ diagnostic: { ...step.diagnostic, volume_ul } }))}{num('Diagnostic speed', step.diagnostic.speed, speed => update({ diagnostic: { ...step.diagnostic, speed } }))}</>}
                    {(step.diagnostic.action === 'plunger_up' || step.diagnostic.action === 'plunger_down') && num('Diagnostic Z steps', step.diagnostic.steps, steps => update({ diagnostic: { ...step.diagnostic, steps } }))}
                </>}
                <button className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35" type="button" disabled={!enabled} onClick={() => run(step.operation)}>{sourceLabels[step.operation]} now</button>
            </div></details>;
        })}
    </div>;
}
