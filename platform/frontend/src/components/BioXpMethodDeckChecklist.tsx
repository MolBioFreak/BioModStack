import { useContext, useState } from 'react';
import { api } from '../lib/api';
import { bioXpErrorText, type BioXpTipTraysV1 } from '../lib/bioxpClient';
import { BioXpWorkflowProfilesContext } from './BioXpWorkflowProfiles';
import { deckResources, deckStations } from '../lib/bioxpWorkflowDeck';
import type { WorkflowDeckPlan, WorkflowTipRack } from '../lib/bioxpWorkflowPlan';
import { COVERS, coverPlace } from './BioXpMethodPlanSummary';

const TRAYS = [1, 2, 3, 4] as const;
const label = (station: string) => deckStations.find(s => s.id === station)?.label ?? station;

/** The starting deck as four short lists. Edits deck_plan only; never robot state, never a Run requirement. */
export function BioXpMethodDeckChecklist({ plan, onChange, generation, station, onSetup, onPlate, onStation }: {
    plan: WorkflowDeckPlan; onChange: (plan: WorkflowDeckPlan) => void; generation: number; station: string;
    onSetup: () => void; onPlate: (labwareId: string, station: string) => void; onStation: (station: string) => void;
}) {
    const library = useContext(BioXpWorkflowProfilesContext);
    const [note, setNote] = useState<{ at: 'labware' | 'tips'; text: string } | null>(null);
    const kind = deckResources.find(r => r.id === station)?.kind;
    const racks = plan.tip_racks ?? [];
    const tipsAtStart = (rack?: WorkflowTipRack) => !rack ? null : rack.tips === 'full' ? 96 : rack.tips === 'empty' ? 0 : rack.tips.available.length;
    const filled = plan.labware.map(l => ({ l, wells: new Set(plan.assignments.filter(a => a.labware_id === l.id).map(a => a.well)).size })).filter(row => row.wells > 0);
    const covers = Object.entries(plan.movable_objects ?? {}).filter(([name]) => COVERS[name]);
    const copyRacks = async () => {
        setNote(null);
        try {
            const rows = (await api.get<BioXpTipTraysV1>('/api/bioxp/operator-controls/tip-trays')).data.trays;
            let next = racks, copied = 0;
            for (const tray of TRAYS) {
                const row = rows.find(r => r.tray === tray), wells = deckResources.find(r => r.tipTray === tray)?.wells ?? [];
                if (!row || !Array.isArray(row.occupancy) || row.occupancy.length !== 96 || wells.length !== 96) continue;
                const present = wells.filter((_, i) => row.occupancy![i]);
                const tips: WorkflowTipRack['tips'] = present.length === 96 ? 'full' : present.length === 0 ? 'empty' : { available: present };
                next = [...next.filter(r => r.tray !== tray), { ...next.find(r => r.tray === tray), tray, tips }];
                copied++;
            }
            if (copied) onChange({ ...plan, tip_racks: [...next].sort((a, b) => a.tray - b.tray) });
            setNote({ at: 'tips', text: copied ? `Copied the robot's record for ${copied} of 4 racks. Tip types are unchanged.` : 'The robot has no tip record for these racks.' });
        } catch (cause) { setNote({ at: 'tips', text: `Robot record unavailable: ${bioXpErrorText(cause)}` }); }
    };
    const copyPlates = async () => {
        setNote(null);
        try {
            const stations = (await api.get<{ stations?: Record<string, string | null> }>('/api/bioxp/plate-profiles', { params: { expected_connection_generation: generation } })).data.stations ?? {};
            const known = new Map((library?.profiles ?? []).filter(p => typeof p.id === 'string').map(p => [String(p.id), p]));
            let set = 0, same = 0, unknown = 0;
            const labware = plan.labware.map(l => {
                const id = stations[l.station];
                if (!id) return l;
                if (l.profile_id === id) { same++; return l; }
                const profile = known.get(id);
                if (!profile) { unknown++; return l; }
                library?.pin(profile); set++;
                return { ...l, profile_id: id };
            });
            if (set) onChange({ ...plan, labware });
            setNote({ at: 'labware', text: [set ? `Set ${set} plate type${set === 1 ? '' : 's'} from the Live deck.` : same ? 'Plate types already match the Live deck.' : 'The Live deck has no plate type for these stations.',
                unknown ? `${unknown} Live deck type${unknown === 1 ? ' is' : 's are'} not in this method's plate list and ${unknown === 1 ? 'was' : 'were'} left alone.` : ''].filter(Boolean).join(' ') });
        } catch (cause) { setNote({ at: 'labware', text: `Live deck plate types unavailable: ${bioXpErrorText(cause)}` }); }
    };
    const offline = generation > 0 ? undefined : 'Connect to the robot first.';
    return <section aria-label="Starting deck" className="bxw-checklist">
        <header><b>Starting deck</b></header>
        <div className={`bxw-check${kind === 'plate' || kind === 'strip' ? ' is-on' : ''}`}><i>1</i><div><b>Labware</b>
            <small>Click a plate on the deck to name it and choose its type.</small>
            {plan.labware.map(l => <button key={l.id} type="button" className="bxw-link" onClick={() => onPlate(l.id, l.station)}>{l.name || label(l.station)}</button>)}
            {plan.labware.length === 0 && <small>No labware yet.</small>}
            <div className="bxw-acts"><button type="button" onClick={onSetup}>Set up labware & reagents</button><button type="button" disabled={!!offline} title={offline} onClick={() => void copyPlates()}>Copy plate types from Live deck</button></div>
            {note?.at === 'labware' && <small role="status">{note.text}</small>}
        </div></div>
        <div className={`bxw-check${kind === 'rack' ? ' is-on' : ''}`}><i>2</i><div><b>Tips</b>
            <small>Click a rack, then drag across its grid.</small>
            <dl>{TRAYS.map(tray => { const count = tipsAtStart(racks.find(r => r.tray === tray)), rack = deckResources.find(r => r.tipTray === tray);
                return <div key={tray}><dt>{rack ? <button type="button" className="bxw-link" onClick={() => onStation(rack.id)}>Tip rack {tray}</button> : `Tip rack ${tray}`}</dt><dd>{count === null ? 'not set' : <><b>{count}</b> tips at start</>}</dd></div>; })}</dl>
            <div className="bxw-acts"><button type="button" disabled={!!offline} title={offline} onClick={() => void copyRacks()}>Copy all racks from robot</button></div>
            {note?.at === 'tips' && <small role="status">{note.text}</small>}
        </div></div>
        <div className="bxw-check"><i>3</i><div><b>Samples and reagents</b>
            <small>Click a plate, pick a material, drag across wells.</small>
            <dl>{filled.map(({ l, wells }) => <div key={l.id}><dt><button type="button" className="bxw-link" onClick={() => onPlate(l.id, l.station)}>{l.name || label(l.station)}</button></dt><dd><b>{wells}</b> well{wells === 1 ? '' : 's'} filled</dd></div>)}</dl>
            <small>{plan.materials.length} material{plan.materials.length === 1 ? '' : 's'} in this method.</small>
        </div></div>
        <div className="bxw-check"><i>4</i><div><b>Covers</b>
            {covers.map(([name, location]) => <small key={name}>{COVERS[name]} {coverPlace(location)}</small>)}
            {covers.length === 0 && <small>Not set.</small>}
            <small>Click a station to set its starting plate and cover.</small>
        </div></div>
    </section>;
}
