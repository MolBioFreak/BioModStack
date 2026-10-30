import { useContext } from 'react';
import { BioXpWorkflowProfilesContext } from './BioXpWorkflowProfiles';
import { deckResources, deckStations } from '../lib/bioxpWorkflowDeck';
import { slotDefaults, slotHolderLabels, type WorkflowDeckPlan, type WorkflowSlotHolder, type WorkflowSlotId, type WorkflowTipRack } from '../lib/bioxpWorkflowPlan';

/** Movable plate/cover objects at each fixed station (ClassMachineStatus names and locations). */
const STATION_OBJECTS: Record<string, { plate?: [string, string]; cover?: { name: string; on: string; stored: string } }> = {
    LOC_RC: { plate: ['REAGENT_PLATE', 'LOC_RC'], cover: { name: 'REAGENT_COVER', on: 'LOC_RC_COVER', stored: 'LOC_RC_COVER_STORAGE' } },
    LOC_OC: { plate: ['OUTPUT_PLATE', 'LOC_P_OC'], cover: { name: 'OUTPUT_COVER', on: 'LOC_OC_COVER', stored: 'LOC_OC_COVER_STORAGE' } },
    LOC_TC: { plate: ['POOL_PLATE', 'LOC_P_TC'], cover: { name: 'BIO_SECURITY_COVER', on: 'LOC_BSC', stored: 'LOC_BSCS' } },
    LOC_STRIP1: { plate: ['STRIP_ONE', 'LOC_STRIP1'] }, LOC_STRIP2: { plate: ['STRIP_TWO', 'LOC_STRIP2'] },
    LOC_STRIP3: { plate: ['STRIP_THREE', 'LOC_STRIP3'] }, LOC_STRIP4: { plate: ['STRIP_FOUR', 'LOC_STRIP4'] },
};
const TIP_TYPES = ['T10', 'T50', 'T200', 'T201'] as const;
const tipTypeForProfile = (id: string): WorkflowTipRack['tip_type'] => TIP_TYPES.find(t => id.endsWith(`-${t}`)) ?? null;
export const isStartingDeckStation = (station: string) => !!deckStations.find(s => s.id === station)?.tipTray || Object.hasOwn(STATION_OBJECTS, station) || Object.hasOwn(slotDefaults, station);

/** Typed edits to deck_plan only; never robot state, never a Run requirement. */
export function BioXpMethodStartingDeck({ plan, station, wells, onChange }: { plan: WorkflowDeckPlan; station: string; wells: string[]; onChange: (plan: WorkflowDeckPlan) => void }) {
    const library = useContext(BioXpWorkflowProfilesContext);
    const meta = deckStations.find(s => s.id === station);
    const tray = meta?.tipTray;
    const objects = { ...(plan.movable_objects ?? {}) };
    if (tray && tray >= 1 && tray <= 4) {
        const racks = plan.tip_racks ?? [];
        const rack = racks.find(r => r.tray === tray);
        const setRack = (next: Partial<WorkflowTipRack> | null) => onChange({ ...plan, tip_racks: next === null ? racks.filter(r => r.tray !== tray) : [...racks.filter(r => r.tray !== tray), { tips: 'full' as const, ...rack, ...next, tray: tray as WorkflowTipRack['tray'] }].sort((a, b) => a.tray - b.tray) });
        const mode = !rack ? '' : typeof rack.tips === 'string' ? rack.tips : 'wells';
        const available = rack && typeof rack.tips === 'object' ? rack.tips.available : [];
        const allWells = deckResources.find(r => r.id === station)?.wells ?? [];
        const profiles = (library?.profiles ?? []).filter(p => typeof p.id === 'string');
        return <section aria-label="Tip rack setup" className="bioxp-starting-deck">
            <h3>{meta?.label ?? `Tip rack ${tray}`}</h3>
            <div role="group" aria-label={`Tip rack ${tray} contents`}>
                {(['full', 'empty', 'wells'] as const).map(choice => <button key={choice} type="button" aria-pressed={mode === choice} onClick={() => setRack({ tips: choice === 'wells' ? { available: wells.length ? [...wells] : available } : choice })}>{choice === 'full' ? 'Full' : choice === 'empty' ? 'Empty' : 'Pick wells'}</button>)}
                {rack && <button type="button" onClick={() => setRack(null)}>Not set</button>}
            </div>
            {mode === 'wells' && <><p>{available.length} of 96 tips present. Select wells on the deck, then use them.</p><button type="button" disabled={!wells.length} onClick={() => setRack({ tips: { available: allWells.filter(w => wells.includes(w)) } })}>Use selected wells as the tips present</button></>}
            <label>Tip type<select aria-label={`Tip rack ${tray} tip type`} value={rack?.tip_type ?? ''} onChange={e => setRack({ tip_type: (e.target.value || null) as WorkflowTipRack['tip_type'] })}><option value="">Not set</option>{TIP_TYPES.map(t => <option key={t} value={t}>{t}</option>)}</select></label>
            {profiles.length > 0 && <section aria-label="Assign rack profile"><h4>Tips stored in this rack</h4><div>{profiles.map(profile => <button type="button" key={String(profile.id)} aria-pressed={rack?.profile_id === profile.id} onClick={() => {
                library?.pin(profile);
                const tipType = tipTypeForProfile(String(profile.id));
                setRack({ profile_id: String(profile.id), ...(tipType ? { tip_type: tipType } : {}) });
            }}>{String(profile.name || profile.label || profile.id)}</button>)}</div>{library?.error && <p>Published profiles are unavailable; existing settings are kept.</p>}</section>}
            <small>Starting tips only. Picking up tips is a separate method step; the plan summary counts what is used.</small>
        </section>;
    }
    if (Object.hasOwn(slotDefaults, station)) {
        const slotId = station as WorkflowSlotId;
        const slots = plan.slots ?? [];
        const current = slots.find(s => s.slot === slotId);
        const holder = current?.holder ?? slotDefaults[slotId];
        const setSlot = (next: Partial<NonNullable<WorkflowDeckPlan['slots']>[number]> | null) => onChange({ ...plan, slots: next === null ? slots.filter(s => s.slot !== slotId) : [...slots.filter(s => s.slot !== slotId), { holder, ...current, ...next, slot: slotId }] });
        return <section aria-label="Slot setup" className="bioxp-starting-deck">
            <h3>{meta?.label ?? station}</h3>
            <label>Fitted here<select aria-label={`${meta?.label ?? station} holder`} value={holder} onChange={e => setSlot({ holder: e.target.value as WorkflowSlotHolder })}>{(Object.keys(slotHolderLabels) as WorkflowSlotHolder[]).map(h => <option key={h} value={h}>{slotHolderLabels[h]}{h === slotDefaults[slotId] ? ' (default)' : ''}</option>)}</select></label>
            <label>Label<input aria-label={`${meta?.label ?? station} label`} value={current?.label ?? ''} placeholder="Optional, e.g. 4X DIYnaFLUOR" onChange={e => setSlot({ label: e.target.value })} /></label>
            {current && <button type="button" onClick={() => setSlot(null)}>Back to default</button>}
            <small>A record of what is fitted in this slot. Covers moved to a slot that is not cover storage get an advisory note.</small>
        </section>;
    }
    const spec = STATION_OBJECTS[station];
    if (!spec) return null;
    const setObject = (name: string, location: string | null) => { const next = { ...objects }; if (location === null) delete next[name]; else next[name] = location; onChange({ ...plan, movable_objects: next }); };
    const plate = spec.plate, cover = spec.cover;
    const plateState = !plate ? '' : objects[plate[0]] === undefined ? '' : objects[plate[0]] === plate[1] ? 'present' : 'absent';
    const coverState = !cover ? '' : objects[cover.name] === undefined ? '' : objects[cover.name] === cover.on ? 'on' : objects[cover.name] === cover.stored ? 'stored' : 'other';
    return <section aria-label="Station starting state" className="bioxp-starting-deck">
        <h3>{meta?.label ?? station} at start</h3>
        {plate && <label>{station.startsWith('LOC_STRIP') ? 'Strip' : 'Plate'}<select aria-label={`${meta?.label ?? station} plate at start`} value={plateState} onChange={e => setObject(plate[0], e.target.value === 'present' ? plate[1] : e.target.value === 'absent' ? 'UNKNOWN' : null)}><option value="">Not set</option><option value="present">In place</option><option value="absent">Not here</option></select></label>}
        {cover && <label>Cover<select aria-label={`${meta?.label ?? station} cover at start`} value={coverState} onChange={e => setObject(cover.name, e.target.value === 'on' ? cover.on : e.target.value === 'stored' ? cover.stored : null)}><option value="">Not set</option><option value="on">On this station</option><option value="stored">In cover storage</option>{coverState === 'other' && <option value="other">Saved: {String(objects[cover.name])}</option>}</select></label>}
        <small>Where things start, as planned. Cover moves and inspection update this in the plan summary.</small>
    </section>;
}
