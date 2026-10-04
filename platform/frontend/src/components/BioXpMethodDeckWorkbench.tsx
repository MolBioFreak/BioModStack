import { useState } from 'react';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import type { MethodCatalog, MethodFinding, MethodValue } from '../lib/bioxpMethods';
import { methodInputBinding } from '../lib/bioxpMethodInputBinding';
import { deckStations, type BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import type { WorkflowDeckPlan } from '../lib/bioxpWorkflowPlan';
import { BioXpWorkflowDeck } from './BioXpWorkflowDeck';
import { BioXpWorkflowMaterials } from './BioXpWorkflowMaterials';
import { BioXpMethodPipettingEditor } from './BioXpMethodPipettingEditor';
import { BioXpMethodThermalEditor, thermalMethodActions } from './BioXpMethodThermalEditor';
import { MethodOutline, MethodFields, methodBindingsSchema, object, findMethodNode, editMethodNode } from './BioXpMethodFields';

/** Selection and setup are draft-only. Bound edits stay with the existing binding owner. */
export function BioXpMethodDeckWorkbench({ method, onChange, catalog, rootSchema, findings, flat = false, actionProperties, bindings = {}, onBindingsChange }: {
    method: MethodValue; onChange: (method: MethodValue) => void; catalog: MethodCatalog;
    rootSchema: Schema; findings?: MethodFinding[]; flat?: boolean;
    bindings?: MethodValue; onBindingsChange?: (bindings: MethodValue) => void;
    actionProperties?: (node: MethodValue, onChange: (node: MethodValue) => void) => React.ReactNode;
}) {
    const [selection, setSelection] = useState<BioXpDeckSelection>({ station: '', wells: [] });
    const [selectedId, setSelectedId] = useState('');
    const [transferMode, setTransferMode] = useState('class');
    const [sequenceOnly, setSequenceOnly] = useState(false);
    const [setupOpen, setSetupOpen] = useState(false);
    const [additionName, setAdditionName] = useState('');
    const [insertion, setInsertion] = useState('after');
    const nodes = Array.isArray(method.steps) ? method.steps as MethodValue[] : [];
    const selected = findMethodNode(nodes, selectedId) ?? findMethodNode(nodes, String(object(method.editor_state).selected_step_id ?? '')) ?? nodes[0];
    const station = deckStations.find(s => s.id === selection.station);
    const endpointReady = station?.locationId != null && selection.wells.length > 0;
    const moveReady = endpointReady && selection.wells.length === 1;
    const binding = methodInputBinding(method, selected ?? {}, bindings);
    const inputs = object(binding.inputs);
    const boundInputs = !binding.editable || !!binding.id && !onBindingsChange;
    const action = selected?.action === 'native_intent' ? inputs.operation : selected?.action;
    const hasAction = (name: string) => (catalog.actions ?? []).some(a => (a.action ?? a.id) === name);
    const append = (action: string, inputs: MethodValue, label?: string, addition = false) => {
        const step_id = crypto.randomUUID();
        const selectedIndex = nodes.findIndex(n => n.step_id === selected?.step_id);
        const containsCycle = (node: MethodValue): boolean => node.action === 'thermal_profile' || ['steps', 'then', 'else'].some(key => Array.isArray(node[key]) && (node[key] as MethodValue[]).some(containsCycle));
        const preparationEnd = nodes.findIndex(n => n.step_id === 'mix_compound' || n.step_id === 'seal' || containsCycle(n));
        const explicitIndex = insertion === 'end' || selectedIndex < 0 ? nodes.length : selectedIndex + (insertion === 'before' ? 0 : 1);
        const index = addition && preparationEnd >= 0 ? preparationEnd : explicitIndex;
        const added = { step_id, type: 'action', action, ...(label ? { label } : {}), inputs };
        const insertSelected = (rows: MethodValue[]): MethodValue[] => rows.flatMap(node => node.step_id === selected?.step_id ? insertion === 'before' ? [added, node] : [node, added] : [{ ...node, ...Object.fromEntries(['steps', 'then', 'else'].filter(key => Array.isArray(node[key])).map(key => [key, insertSelected(node[key] as MethodValue[])])) }]);
        const steps = addition && preparationEnd >= 0 || insertion === 'end' || !selected ? [...nodes.slice(0, index), added, ...nodes.slice(index)] : insertSelected(nodes);
        onChange({ ...method, steps });
        setSelectedId(step_id);
    };
    const updateInputs = (next: MethodValue) => {
        if (binding.id && onBindingsChange) onBindingsChange({ ...bindings, [binding.id]: next });
        else onChange({ ...method, steps: editMethodNode(nodes, String(selected?.step_id), n => ({ ...n, inputs: next })) });
    };
    const adoptEndpoint = (key: 'source' | 'destination') => {
        if (!endpointReady || action !== 'transfer') return;
        const labware = plan.labware.filter(l => l.station === station!.id);
        updateInputs({ ...inputs, [key]: { ...object(inputs[key]), station: station!.id, location_id: station!.locationId, wells: [...selection.wells], ...(labware.length === 1 ? { labware_id: labware[0].id } : {}) } });
    };
    const endpointText = (key: string) => {
        const endpoint = object(inputs[key]);
        const labware = plan.labware.find(l => l.id === endpoint.labware_id);
        const label = labware?.name || deckStations.find(s => s.id === endpoint.station)?.label || endpoint.station;
        return label ? `${label} · ${Array.isArray(endpoint.wells) ? endpoint.wells.join(', ') : 'no wells'}` : 'Not selected';
    };
    const rawPlan = object(method.deck_plan);
    const plan = { ...rawPlan, labware: Array.isArray(rawPlan.labware) ? rawPlan.labware : [], materials: Array.isArray(rawPlan.materials) ? rawPlan.materials : [], assignments: Array.isArray(rawPlan.assignments) ? rawPlan.assignments : [] } as WorkflowDeckPlan;
    const properties = (node: MethodValue, change: (next: MethodValue) => void) => {
        const bound = methodInputBinding(method, node, bindings);
        if (!bound.editable || bound.id && !onBindingsChange) return actionProperties?.(node, change);
        const projected = { ...node, inputs: bound.inputs };
        const edit = (next: MethodValue) => {
            if (bound.id && onBindingsChange) { onBindingsChange({ ...bindings, [bound.id]: next.inputs }); change({ ...next, inputs: node.inputs }); }
            else change(next);
        };
        const editor = actionProperties?.(projected, edit) ?? (thermalMethodActions.has(String(node.action)) ? <BioXpMethodThermalEditor node={projected} onChange={edit} catalog={catalog} /> : ['move', 'transfer', 'lower', 'lift', 'mix'].includes(String(node.action === 'native_intent' ? object(bound.inputs).operation : node.action)) ? <BioXpMethodPipettingEditor node={projected} onChange={edit} catalog={catalog} selection={selection} onSelect={setSelection} /> : undefined);
        const bindingSchema = bound.id ? methodBindingsSchema(method, catalog) : undefined;
        const content = editor ?? (bound.id ? <MethodFields label={bound.label || 'Step settings'} schema={bindingSchema?.properties?.[bound.id]} rootSchema={bindingSchema} value={bound.inputs} onChange={inputs => edit({ ...projected, inputs })} /> : undefined);
        return content ? <>{bound.id && <small>{bound.inherited ? 'Inherited parameter default; editing creates a binding.' : 'Edits are saved in this experiment’s bindings.'}</small>}{content}</> : undefined;
    };
    const duplicate = (original: MethodValue) => {
        const parameters = [...(Array.isArray(method.parameters) ? method.parameters as MethodValue[] : [])];
        const nextBindings = { ...bindings }, copiedParameters = new Map<string, string>();
        const copy = (node: MethodValue): MethodValue => {
            const next = structuredClone(node); next.step_id = crypto.randomUUID();
            const bound = methodInputBinding(method, node, bindings);
            if (bound.id && onBindingsChange) {
                let id = copiedParameters.get(bound.id);
                if (!id) { id = crypto.randomUUID(); copiedParameters.set(bound.id, id); parameters.push({ ...structuredClone(parameters.find(p => p.id === bound.id)!), id }); if (Object.hasOwn(bindings, bound.id)) nextBindings[id] = structuredClone(bindings[bound.id]); }
                next.inputs = { ...object(next.inputs), expr: { ...object(object(next.inputs).expr), id } };
            }
            for (const key of ['steps', 'then', 'else']) if (Array.isArray(next[key])) next[key] = (next[key] as MethodValue[]).map(copy);
            return next;
        };
        const copied = copy(original);
        const insert = (rows: MethodValue[]): MethodValue[] => rows.flatMap(node => node.step_id === original.step_id ? [node, copied] : [{ ...node, ...Object.fromEntries(['steps', 'then', 'else'].filter(k => Array.isArray(node[k])).map(k => [k, insert(node[k] as MethodValue[])])) }]);
        onChange({ ...method, ...(copiedParameters.size ? { parameters } : {}), steps: insert(nodes) });
        if (copiedParameters.size) onBindingsChange?.(nextBindings);
        setSelectedId(String(copied.step_id));
    };
    const transferInputs = () => ({ ...(endpointReady ? { source: { station: station!.id, location_id: station!.locationId, wells: [...selection.wells] } } : {}), ...(transferMode === 'class' ? { liquid: {}, recipe: {} } : {}) });
    return <><div className="bioxp-method-view" role="group" aria-label="Authoring view"><button type="button" aria-pressed={!sequenceOnly} onClick={() => setSequenceOnly(false)}>Deck & sequence</button><button type="button" aria-pressed={sequenceOnly} onClick={() => setSequenceOnly(true)}>Sequence only</button></div><div className={`bioxp-method-workbench${sequenceOnly ? ' is-sequence-only' : ''}`}>
        <section aria-label="Method deck" className="bioxp-method-map" hidden={sequenceOnly}>
            <h3>Experiment deck</h3><button type="button" aria-expanded={setupOpen} onClick={() => setSetupOpen(!setupOpen)}>Set up labware & reagents</button>
            <div className="bioxp-method-adopt" aria-label="Use deck selection">
                <p>{station?.label ?? 'Select a station or well'}{selection.wells.length ? ` · ${selection.wells.join(', ')}` : ''}</p>
                <label>Insert step<select aria-label="Insert step" value={insertion} onChange={e => setInsertion(e.target.value)}><option value="before">Before selected step</option><option value="after">After selected step</option><option value="end">At end</option></select></label>
                <button type="button" disabled={!moveReady || !hasAction('move')} onClick={() => append('move', { location_id: station!.locationId, well: selection.wells[0] })}>Add Move</button>
                <label>Transfer settings<select aria-label="New Transfer settings" value={transferMode} onChange={e => setTransferMode(e.target.value)}><option value="class">Liquid class & recipe</option><option value="manual">Manual speeds</option></select></label>
                <button type="button" disabled={!hasAction('transfer')} onClick={() => append('transfer', transferInputs())}>Add Transfer</button>
                {(station?.id === 'LOC_RC' || station?.id === 'LOC_OC') && <button type="button" disabled={!hasAction('chiller_setpoint')} onClick={() => append('chiller_setpoint', { bank: station.id === 'LOC_RC' ? 'rc' : 'oc' })}>Add temperature step</button>}
                {station?.id === 'LOC_TC' && <><button type="button" disabled={!hasAction('thermal_setpoint')} onClick={() => append('thermal_setpoint', {})}>Add temperature step</button><button type="button" disabled={!hasAction('thermal_hold')} onClick={() => append('thermal_hold', {})}>Add hold</button><button type="button" disabled={!hasAction('thermal_profile')} onClick={() => append('thermal_profile', { segments: [] })}>Add PCR cycle</button><button type="button" disabled={!hasAction('thermal_door')} onClick={() => append('thermal_door', { door_command: 'DO' })}>Add door Open</button><button type="button" disabled={!hasAction('thermal_door')} onClick={() => append('thermal_door', { door_command: 'DC' })}>Add door Close</button><small>Door Open also initializes pipettes when run.</small></>}
                {boundInputs && <p>Retained expression or non-object input: edit Advanced action fields or Bindings. The original value is unchanged.</p>}
                {action === 'move' && !boundInputs && <button type="button" disabled={!moveReady} onClick={() => updateInputs({ ...inputs, location_id: station!.locationId, well: selection.wells[0] })}>Use as Move target</button>}
                {action === 'transfer' && !boundInputs && <><button type="button" disabled={!endpointReady} onClick={() => adoptEndpoint('source')}>Use as source</button><button type="button" disabled={!endpointReady} onClick={() => adoptEndpoint('destination')}>Use as destination</button><p>Source: {endpointText('source')}<br />Destination: {endpointText('destination')}</p></>}
                <small>Add steps from the selection, then edit their Properties. Editing does not run them.</small>
            </div>
            <BioXpWorkflowDeck compact selection={selection} onChange={setSelection} />
        </section>
        <aside aria-label="Method sequence and Properties" className="bioxp-method-inspector">
            {setupOpen && <BioXpWorkflowMaterials methodAuthoring plan={plan} selection={selection} onChange={deck_plan => onChange({ ...method, deck_plan })} />}
            <section aria-label="Reagent additions"><h3>Reagent additions & aliquots</h3><label>Addition name<input aria-label="Addition name" value={additionName} onChange={e => setAdditionName(e.target.value)} placeholder="e.g. Primer mix" /></label><button type="button" disabled={!hasAction('transfer')} onClick={() => { append('transfer', transferInputs(), additionName || 'Reagent addition', true); setAdditionName(''); }}>Add reagent / aliquot before cycling</button><small>Choose source, reaction wells and volume in Properties. Each addition has its own settings.</small></section>
            <section aria-label="Cycling setup"><h3>Cycling</h3><button type="button" disabled={!hasAction('thermal_profile')} onClick={() => append('thermal_profile', { segments: [] }, 'Repeated PCR cycles')}>Add repeated cycles</button><button type="button" disabled={!hasAction('thermal_hold')} onClick={() => append('thermal_hold', {}, 'Initial / final hold')}>Insert initial / final hold</button><small>Initial and final holds are separate steps, not repeated stages. Use Insert step to place them.</small></section>
            <MethodOutline onDuplicate={duplicate} actionProperties={properties} nodes={nodes} onChange={steps => onChange({ ...method, steps })} catalog={catalog} rootSchema={rootSchema} nodeSchema={rootSchema.properties?.steps?.items} procedures={Array.isArray(method.procedures) ? method.procedures as MethodValue[] : []} findings={findings} flat={flat} selection={{ id: String(selected?.step_id ?? ''), onSelect: setSelectedId }} />
        </aside>
    </div></>;
}
