import { useState } from 'react';
import { deckStations, type BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import type { WorkflowDeckPlan, WorkflowMaterial } from '../lib/bioxpWorkflowPlan';
import './BioXpWorkflowMaterials.css';

export interface BioXpWorkflowMaterialsProps {
    plan: WorkflowDeckPlan;
    onChange: (plan: WorkflowDeckPlan) => void;
    selection: BioXpDeckSelection;
}
const id = () => crypto.randomUUID();
const stationLabel = (station: string) => deckStations.find(s => s.id === station)?.label || station || 'No station';

export function BioXpWorkflowMaterials({ plan, onChange, selection }: BioXpWorkflowMaterialsProps) {
    const [materialId, setMaterialId] = useState('');
    const [labwareId, setLabwareId] = useState('');
    const [amount, setAmount] = useState('');
    const station = deckStations.find(s => s.id === selection.station);
    const available = plan.labware.filter(l => l.station === selection.station);
    const target = available.find(l => l.id === labwareId) ?? (available.length === 1 ? available[0] : undefined);
    const addMaterial = (kind: WorkflowMaterial['kind']) => {
        const material = { id: id(), kind, name: '', description: '' };
        onChange({ ...plan, materials: [...plan.materials, material] });
        setMaterialId(material.id);
    };
    const assign = () => {
        if (!target || !plan.materials.some(m => m.id === materialId)) return;
        onChange({ ...plan, assignments: [...plan.assignments, ...selection.wells.map(well => ({
            id: id(), labware_id: target.id, well, material_id: materialId, volume_ul: amount,
        }))] });
    };
    return <section className="bioxp-plan-leaf" aria-label="Planned materials">
        <header><h3>Labware & materials</h3><p>Workflow-local labels and planned amounts, not measured presence or a Run requirement.</p></header>
        <fieldset><legend>Named labware</legend>
            <p>Selected: {stationLabel(selection.station)}{selection.wells.length > 0 && ` · ${selection.wells.join(', ')}`}</p>
            <button type="button" disabled={!station} onClick={() => {
                if (!station) return;
                const item = { id: id(), station: station.id, profile_id: station.id, name: '' };
                onChange({ ...plan, labware: [...plan.labware, item] }); setLabwareId(item.id);
            }}>Add labware at selected station</button>
            {plan.labware.length === 0 && <p>No planned labware yet. Select a fixed station on the deck.</p>}
            {plan.labware.map((item, index) => <div className="bioxp-plan-row" key={item.id}>
                <label>Labware {index + 1} name<input value={item.name ?? ''} onChange={e => onChange({ ...plan, labware: plan.labware.map(l => l.id === item.id ? { ...l, name: e.target.value } : l) })} /></label>
                <small>{stationLabel(item.station)} · Native layout: {item.profile_id || 'Not set'}</small>
                <button type="button" onClick={() => onChange({ ...plan, labware: plan.labware.filter(l => l.id !== item.id), assignments: plan.assignments.filter(a => a.labware_id !== item.id) })}>Remove labware {index + 1} and its assignments</button>
            </div>)}
        </fieldset>
        <fieldset><legend>Samples & reagents</legend>
            <div className="bioxp-plan-actions"><button type="button" onClick={() => addMaterial('sample')}>Add sample</button><button type="button" onClick={() => addMaterial('reagent')}>Add reagent</button></div>
            {plan.materials.map((material, index) => <div className="bioxp-plan-row" key={material.id}>
                <label>Material {index + 1} name<input value={material.name ?? ''} onChange={e => onChange({ ...plan, materials: plan.materials.map(m => m.id === material.id ? { ...m, name: e.target.value } : m) })} /></label>
                <label>Material {index + 1} kind<select value={material.kind} onChange={e => onChange({ ...plan, materials: plan.materials.map(m => m.id === material.id ? { ...m, kind: e.target.value as WorkflowMaterial['kind'] } : m) })}><option value="sample">Sample</option><option value="reagent">Reagent</option></select></label>
                <label>Material {index + 1} description<input value={material.description ?? ''} onChange={e => onChange({ ...plan, materials: plan.materials.map(m => m.id === material.id ? { ...m, description: e.target.value } : m) })} /></label>
                <button type="button" onClick={() => onChange({ ...plan, materials: plan.materials.filter(m => m.id !== material.id), assignments: plan.assignments.filter(a => a.material_id !== material.id) })}>Remove material {index + 1} and its assignments</button>
            </div>)}
        </fieldset>
        <fieldset><legend>Assign selected wells</legend>
            <label>Assignment labware<select value={target?.id ?? ''} onChange={e => setLabwareId(e.target.value)}><option value="">Choose labware at selected station</option>{available.map(l => <option key={l.id} value={l.id}>{l.name || 'Unnamed labware'} · {stationLabel(l.station)}</option>)}</select></label>
            <label>Assignment material<select value={plan.materials.some(m => m.id === materialId) ? materialId : ''} onChange={e => setMaterialId(e.target.value)}><option value="">Choose material</option>{plan.materials.map((m, i) => <option key={m.id} value={m.id}>{m.name || `Unnamed ${m.kind} ${i + 1}`}</option>)}</select></label>
            <label>Planned amount per selected well (µL)<input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} placeholder="Unspecified" /></label>
            <button type="button" disabled={!target || !materialId || !selection.wells.length} onClick={assign}>Assign material to selected wells</button>
            <small>Blank amounts remain incomplete. Each click adds associations; existing entries are not overwritten.</small>
        </fieldset>
        <fieldset><legend>Well associations ({plan.assignments.length})</legend>
            {plan.assignments.length === 0 && <p>No material assignments.</p>}
            {plan.assignments.map((assignment, index) => {
                const labware = plan.labware.find(l => l.id === assignment.labware_id);
                const material = plan.materials.find(m => m.id === assignment.material_id);
                return <div className="bioxp-plan-row" key={assignment.id}>
                    <strong>{labware?.name || assignment.labware_id} · {assignment.well} → {material?.name || assignment.material_id}</strong>
                    <label>Association {index + 1} well<input value={assignment.well} onChange={e => onChange({ ...plan, assignments: plan.assignments.map(a => a.id === assignment.id ? { ...a, well: e.target.value } : a) })} /></label>
                    <label>Association {index + 1} material<select value={assignment.material_id} onChange={e => onChange({ ...plan, assignments: plan.assignments.map(a => a.id === assignment.id ? { ...a, material_id: e.target.value } : a) })}>
                        <option value="">Unassigned</option>{!material && assignment.material_id && <option value={assignment.material_id}>Unresolved: {assignment.material_id}</option>}
                        {plan.materials.map((m, i) => <option key={m.id} value={m.id}>{m.name || `Unnamed ${m.kind} ${i + 1}`}</option>)}
                    </select></label>
                    <label>Association {index + 1} planned amount (µL)<input inputMode="decimal" value={assignment.volume_ul ?? ''} onChange={e => onChange({ ...plan, assignments: plan.assignments.map(a => a.id === assignment.id ? { ...a, volume_ul: e.target.value } : a) })} /></label>
                    <small>{assignment.volume_ul === null ? 'Unspecified (saved null)' : assignment.volume_ul === '' ? 'Incomplete amount' : 'Planned, not measured'}</small>
                    <div className="bioxp-plan-actions"><button type="button" onClick={() => onChange({ ...plan, assignments: plan.assignments.map(a => a.id === assignment.id ? { ...a, volume_ul: null } : a) })}>Unset association {index + 1} amount</button>
                    <button type="button" onClick={() => onChange({ ...plan, assignments: plan.assignments.filter(a => a.id !== assignment.id) })}>Remove association {index + 1}</button></div>
                </div>;
            })}
        </fieldset>
    </section>;
}
export default BioXpWorkflowMaterials;
