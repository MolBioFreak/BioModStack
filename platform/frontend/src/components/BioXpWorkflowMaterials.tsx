import { useState } from 'react';
import type { MethodValue } from '../lib/bioxpMethods';
import { deckStations, type BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import type { WorkflowDeckPlan, WorkflowMaterial } from '../lib/bioxpWorkflowPlan';
import './BioXpWorkflowMaterials.css';

export interface BioXpWorkflowMaterialsProps {
    plan: WorkflowDeckPlan;
    onChange: (plan: WorkflowDeckPlan) => void;
    selection: BioXpDeckSelection;
    methodAuthoring?: boolean;
    profiles?: MethodValue[];
    contextual?: boolean;
    selectedLabwareId?: string;
    displayStations?: Record<string, string>;
}
const id = () => crypto.randomUUID();
const stationLabel = (station: string) => deckStations.find(s => s.id === station)?.label || station || 'No station';

export function BioXpWorkflowMaterials({ plan, onChange, selection, methodAuthoring = false, profiles = [], contextual = false, selectedLabwareId = '', displayStations }: BioXpWorkflowMaterialsProps) {
    const [materialId, setMaterialId] = useState('');
    const [labwareId, setLabwareId] = useState('');
    const [assignmentContext, setAssignmentContext] = useState('');
    const [amount, setAmount] = useState('');
    const station = deckStations.find(s => s.id === selection.station);
    const available = plan.labware.filter(l => (displayStations?.[l.id] ?? l.station) === selection.station);
    const target = (assignmentContext === selectedLabwareId ? available.find(l => l.id === labwareId) : undefined) ?? available.find(l => l.id === selectedLabwareId) ?? (available.length === 1 ? available[0] : undefined);
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
                const item = { id: id(), station: station.id, profile_id: methodAuthoring ? '' : station.id, name: '' };
                onChange({ ...plan, labware: [...plan.labware, item] }); setLabwareId(item.id); setAssignmentContext(selectedLabwareId);
            }}>Add labware at selected station</button>
            {plan.labware.length === 0 && <p>No planned labware yet. Select a fixed station on the deck.</p>}
            {plan.labware.map((item, index) => contextual && (selectedLabwareId ? item.id !== selectedLabwareId : (displayStations?.[item.id] ?? item.station) !== selection.station) ? null : <div className="bioxp-plan-row" key={item.id}>
                <label>Labware {index + 1} name<input value={item.name ?? ''} onChange={e => onChange({ ...plan, labware: plan.labware.map(l => l.id === item.id ? { ...l, name: e.target.value } : l) })} /></label>
                <small>{stationLabel(item.station)} · starting assignment</small>
                {methodAuthoring && <label>Labware {index + 1} published profile<select aria-label={`Labware ${index + 1} published profile`} value={item.profile_id ?? ''} onChange={e => onChange({ ...plan, labware: plan.labware.map(l => l.id === item.id ? { ...l, profile_id: e.target.value } : l) })}><option value="">Not assigned</option>{item.profile_id && !profiles.some(p => p.id === item.profile_id) && <option value={item.profile_id}>{item.profile_id} (retained)</option>}{profiles.map(p => <option key={String(p.id)} value={String(p.id)}>{String(p.name || p.label || p.id)} · r{String(p.revision ?? '?')}</option>)}</select><small>Published pinned profiles only. Rack assignment is not pickup or observed loaded tips.</small></label>}
                {methodAuthoring && <><label>Labware {index + 1} station<select value={item.station} onChange={e => onChange({ ...plan, labware: plan.labware.map(l => l.id === item.id ? { ...l, station: e.target.value } : l) })}>{!deckStations.some(s => s.id === item.station) && <option value={item.station}>{item.station || 'Unspecified'}</option>}{deckStations.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</select></label><details><summary>Labware technical details</summary><small>Stable ID: {item.id}. Layout references are addressing metadata, not proof of tube, plate or cap compatibility.</small><label>Labware {index + 1} profile<input value={item.profile_id ?? ''} onChange={e => onChange({ ...plan, labware: plan.labware.map(l => l.id === item.id ? { ...l, profile_id: e.target.value } : l) })} /></label></details></>}
                <button type="button" onClick={() => onChange({ ...plan, labware: plan.labware.filter(l => l.id !== item.id), assignments: plan.assignments.filter(a => a.labware_id !== item.id) })}>Remove labware {index + 1} and its assignments</button>
            </div>)}
        </fieldset>
        <fieldset><legend>Samples & reagents</legend>
            <div className="bioxp-plan-actions"><button type="button" onClick={() => addMaterial('sample')}>Add sample</button><button type="button" onClick={() => addMaterial('reagent')}>Add reagent</button></div>
            {plan.materials.map((material, index) => contextual && material.id !== materialId && !plan.assignments.some(a => a.material_id === material.id && available.some(l => l.id === a.labware_id) && (!selection.wells.length || selection.wells.includes(a.well))) ? null : <div className="bioxp-plan-row" key={material.id}>
                <label>Material {index + 1} name<input value={material.name ?? ''} onChange={e => onChange({ ...plan, materials: plan.materials.map(m => m.id === material.id ? { ...m, name: e.target.value } : m) })} /></label>
                <label>Material {index + 1} kind<select value={material.kind} onChange={e => onChange({ ...plan, materials: plan.materials.map(m => m.id === material.id ? { ...m, kind: e.target.value as WorkflowMaterial['kind'] } : m) })}><option value="sample">Sample</option><option value="reagent">Reagent</option>{methodAuthoring && <><option value="product">Product</option><option value="waste">Waste</option></>}</select></label>
                <label>Material {index + 1} description<input value={material.description ?? ''} onChange={e => onChange({ ...plan, materials: plan.materials.map(m => m.id === material.id ? { ...m, description: e.target.value } : m) })} /></label>
                {methodAuthoring && <><label>Material {index + 1} concentration<input aria-label={`Material ${index + 1} concentration`} inputMode="decimal" value={material.concentration ?? ''} placeholder="Unknown" onChange={e => onChange({ ...plan, materials: plan.materials.map(m => m.id === material.id ? { ...m, concentration: e.target.value } : m) })} /></label><label>Material {index + 1} concentration unit<input aria-label={`Material ${index + 1} concentration unit`} value={material.concentration_unit ?? ''} placeholder="Authored unit" onChange={e => onChange({ ...plan, materials: plan.materials.map(m => m.id === material.id ? { ...m, concentration_unit: e.target.value } : m) })} /></label></>}
                <button type="button" onClick={() => onChange({ ...plan, materials: plan.materials.filter(m => m.id !== material.id), assignments: plan.assignments.filter(a => a.material_id !== material.id) })}>Remove material {index + 1} and its assignments</button>
            </div>)}
        </fieldset>
        <fieldset><legend>Assign selected wells</legend>
            <label>Assignment labware<select value={target?.id ?? ''} onChange={e => { setLabwareId(e.target.value); setAssignmentContext(selectedLabwareId); }}><option value="">Choose labware at selected station</option>{available.map(l => <option key={l.id} value={l.id}>{l.name || 'Unnamed labware'} · {stationLabel(l.station)}</option>)}</select></label>
            <label>Assignment material<select value={plan.materials.some(m => m.id === materialId) ? materialId : ''} onChange={e => setMaterialId(e.target.value)}><option value="">Choose material</option>{plan.materials.map((m, i) => <option key={m.id} value={m.id}>{m.name || `Unnamed ${m.kind} ${i + 1}`}</option>)}</select></label>
            <label>Planned amount per selected well (µL)<input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} placeholder="Unspecified" /></label>
            <button type="button" disabled={!target || !materialId || !selection.wells.length} onClick={assign}>Assign material to selected wells</button>
            <small>Blank amounts remain incomplete. Each click adds associations; existing entries are not overwritten.</small>
        </fieldset>
        <fieldset><legend>Well associations ({plan.assignments.length})</legend>
            {plan.assignments.length === 0 && <p>No material assignments.</p>}
            {plan.assignments.map((assignment, index) => {
                if (contextual && (!available.some(l => l.id === assignment.labware_id) || selection.wells.length > 0 && !selection.wells.includes(assignment.well))) return null;
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
