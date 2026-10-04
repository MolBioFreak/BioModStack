import { useState } from 'react';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import type { MethodCatalog, MethodFinding, MethodValue } from '../lib/bioxpMethods';
import { deckStations, type BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import type { WorkflowDeckPlan } from '../lib/bioxpWorkflowPlan';
import { BioXpWorkflowDeck } from './BioXpWorkflowDeck';
import { BioXpWorkflowMaterials } from './BioXpWorkflowMaterials';
import { BioXpMethodPipettingEditor } from './BioXpMethodPipettingEditor';
import { BioXpMethodThermalEditor, thermalMethodActions } from './BioXpMethodThermalEditor';
import { MethodOutline, object, findMethodNode, editMethodNode } from './BioXpMethodFields';

/** Selection is presentation-only. Only explicit adoption writes authored native inputs. */
export function BioXpMethodDeckWorkbench({ method, onChange, catalog, rootSchema, findings, flat = false, actionProperties }: {
    method: MethodValue; onChange: (method: MethodValue) => void; catalog: MethodCatalog;
    rootSchema: Schema; findings?: MethodFinding[]; flat?: boolean;
    actionProperties?: (node: MethodValue, onChange: (node: MethodValue) => void) => React.ReactNode;
}) {
    const [selection, setSelection] = useState<BioXpDeckSelection>({ station: '', wells: [] });
    const [selectedId, setSelectedId] = useState('');
    const [transferMode, setTransferMode] = useState('class');
    const [sequenceOnly, setSequenceOnly] = useState(false);
    const nodes = Array.isArray(method.steps) ? method.steps as MethodValue[] : [];
    const selected = findMethodNode(nodes, selectedId) ?? nodes[0];
    const station = deckStations.find(s => s.id === selection.station);
    const endpointReady = station?.locationId != null && selection.wells.length > 0;
    const moveReady = endpointReady && selection.wells.length === 1;
    const inputs = object(selected?.inputs);
    const boundInputs = !!inputs.expr;
    const action = selected?.action === 'native_intent' ? inputs.operation : selected?.action;
    const actions = catalog.actions ?? [];
    const hasAction = (name: string) => actions.some(a => (a.action ?? a.id) === name);
    const append = (action: string, inputs: MethodValue) => {
        const step_id = crypto.randomUUID();
        onChange({ ...method, steps: [...nodes, { step_id, type: 'action', action, inputs }] });
        setSelectedId(step_id);
    };
    const updateInputs = (next: MethodValue) => onChange({ ...method, steps: editMethodNode(nodes, String(selected?.step_id), n => ({ ...n, inputs: next })) });
    const adoptEndpoint = (key: 'source' | 'destination') => {
        if (!endpointReady || action !== 'transfer') return;
        updateInputs({ ...inputs, [key]: { ...object(inputs[key]), station: station!.id, location_id: station!.locationId, wells: [...selection.wells] } });
    };
    const endpointText = (key: string) => {
        const endpoint = object(inputs[key]);
        const label = deckStations.find(s => s.id === endpoint.station)?.label ?? endpoint.station;
        return label ? `${label} · ${Array.isArray(endpoint.wells) ? endpoint.wells.join(', ') : 'no wells'}` : 'Not selected';
    };
    const rawPlan = object(method.deck_plan);
    const plan = { ...rawPlan, labware: Array.isArray(rawPlan.labware) ? rawPlan.labware : [], materials: Array.isArray(rawPlan.materials) ? rawPlan.materials : [], assignments: Array.isArray(rawPlan.assignments) ? rawPlan.assignments : [] } as WorkflowDeckPlan;
    return <><div className="bioxp-method-view" role="group" aria-label="Authoring view"><button type="button" aria-pressed={!sequenceOnly} onClick={() => setSequenceOnly(false)}>Deck & sequence</button><button type="button" aria-pressed={sequenceOnly} onClick={() => setSequenceOnly(true)}>Sequence only</button></div><div className={`bioxp-method-workbench${sequenceOnly ? ' is-sequence-only' : ''}`}>
        <section aria-label="Method deck" className="bioxp-method-map" hidden={sequenceOnly}>
            <h3>Deck</h3>
            <div className="bioxp-method-adopt" aria-label="Use deck selection">
                <p>{station?.label ?? 'Select a station or well'}{selection.wells.length ? ` · ${selection.wells.join(', ')}` : ''}</p>
                <button type="button" disabled={!moveReady || !hasAction('move')} onClick={() => append('move', { location_id: station!.locationId, well: selection.wells[0] })}>Add Move</button>
                <label>Transfer settings<select aria-label="New Transfer settings" value={transferMode} onChange={e => setTransferMode(e.target.value)}><option value="class">Liquid class & recipe</option><option value="manual">Manual speeds</option></select></label>
                <button type="button" disabled={!hasAction('transfer')} onClick={() => append('transfer', { ...(endpointReady ? { source: { station: station!.id, location_id: station!.locationId, wells: [...selection.wells] } } : {}), ...(transferMode === 'class' ? { liquid: {}, recipe: {} } : {}) })}>Add Transfer</button>
                {(station?.id === 'LOC_RC' || station?.id === 'LOC_OC') && <button type="button" disabled={!hasAction('chiller_setpoint')} onClick={() => append('chiller_setpoint', { bank: station.id === 'LOC_RC' ? 'rc' : 'oc' })}>Add temperature step</button>}
                {station?.id === 'LOC_TC' && <><button type="button" disabled={!hasAction('thermal_setpoint')} onClick={() => append('thermal_setpoint', {})}>Add temperature step</button><button type="button" disabled={!hasAction('thermal_hold')} onClick={() => append('thermal_hold', {})}>Add hold</button><button type="button" disabled={!hasAction('thermal_profile')} onClick={() => append('thermal_profile', { segments: [] })}>Add PCR cycle</button><button type="button" disabled={!hasAction('thermal_door')} onClick={() => append('thermal_door', { door_command: 'DO' })}>Add door Open</button><button type="button" disabled={!hasAction('thermal_door')} onClick={() => append('thermal_door', { door_command: 'DC' })}>Add door Close</button><small>Door Open also initializes pipettes when run.</small></>}
                {boundInputs && <p>This step uses bound inputs. Edit its Bindings or add an unbound action to use the deck.</p>}
                {action === 'move' && !boundInputs && <button type="button" disabled={!moveReady} onClick={() => updateInputs({ ...inputs, location_id: station!.locationId, well: selection.wells[0] })}>Use as Move target</button>}
                {action === 'transfer' && !boundInputs && <><button type="button" disabled={!endpointReady} onClick={() => adoptEndpoint('source')}>Use as source</button><button type="button" disabled={!endpointReady} onClick={() => adoptEndpoint('destination')}>Use as destination</button><p>Source: {endpointText('source')}<br />Destination: {endpointText('destination')}</p></>}
                <small>Add steps from the selection, then edit their Properties. Editing does not run them.</small>
            </div>
            <BioXpWorkflowDeck compact selection={selection} onChange={setSelection} />
            <details><summary>Labware & materials</summary><BioXpWorkflowMaterials methodAuthoring plan={plan} selection={selection} onChange={deck_plan => onChange({ ...method, deck_plan })} /></details>
        </section>
        <aside aria-label="Method sequence and Properties" className="bioxp-method-inspector">
            <MethodOutline actionProperties={(node, change) => actionProperties?.(node, change) ?? (thermalMethodActions.has(String(node.action)) ? <BioXpMethodThermalEditor node={node} onChange={change} catalog={catalog} /> : ['move', 'transfer'].includes(String(node.action === 'native_intent' ? object(node.inputs).operation : node.action)) && !object(node.inputs).expr ? <BioXpMethodPipettingEditor node={node} onChange={change} catalog={catalog} selection={selection} onSelect={setSelection} /> : undefined)} nodes={nodes} onChange={steps => onChange({ ...method, steps })} catalog={catalog} rootSchema={rootSchema} nodeSchema={rootSchema.properties?.steps?.items} procedures={Array.isArray(method.procedures) ? method.procedures as MethodValue[] : []} findings={findings} flat={flat} selection={{ id: String(selected?.step_id ?? ''), onSelect: setSelectedId }} />
        </aside>
    </div></>;
}
