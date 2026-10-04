import { useState } from 'react';
import type { MethodCatalog, MethodValue } from '../lib/bioxpMethods';
import type { BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import type { WorkflowDeckPlan } from '../lib/bioxpWorkflowPlan';
import { MethodFields, object } from './BioXpMethodFields';
import { isMethodNumber, methodNumber } from '../lib/bioxpMethodNumber';

// Suggestions are documentary discovery, never native validation or physical inventory.
// eslint-disable-next-line react-refresh/only-export-components
export const custodyMethodActions = new Set(['plate_move', 'move_cover', 'plate_catch', 'plate_release', 'plate_press', 'plate_prepare', 'wait', 'checkpoint', 'note']);
// eslint-disable-next-line react-refresh/only-export-components
export function custodyMetadata(catalog: MethodCatalog) {
    const raw = object(object(catalog.authoring).custody);
    return { objects: Array.isArray(raw.objects) ? raw.objects as MethodValue[] : [], destinations: Array.isArray(raw.destinations) ? raw.destinations as MethodValue[] : [], press: object(raw.press_mode) };
}
export function BioXpMethodCustodyEditor({ node, onChange, catalog, plan, selection }: { node: MethodValue; onChange: (node: MethodValue) => void; catalog: MethodCatalog; plan: WorkflowDeckPlan; selection: BioXpDeckSelection }) {
    const [advanced, setAdvanced] = useState(false);
    const inputs = object(node.inputs), action = String(node.action), meta = custodyMetadata(catalog);
    const set = (key: string, value: unknown) => { const next = { ...inputs }; if (value === undefined) delete next[key]; else next[key] = value; onChange({ ...node, inputs: next }); };
    const choice = (key: string, label: string, options: Array<{ value: unknown; label: string }>) => {
        const index = options.findIndex(o => o.value === inputs[key]);
        return <label>{label}<select aria-label={label} value={inputs[key] === undefined ? '' : index < 0 ? 'retained' : String(index)} onChange={e => { if (e.target.value !== 'retained') set(key, e.target.value === '' ? undefined : options[Number(e.target.value)].value); }}><option value="">Not selected</option>{inputs[key] !== undefined && index < 0 && <option value="retained">Retained native / null / expression value</option>}{options.map((o, i) => <option key={i} value={i}>{o.label}</option>)}</select></label>;
    };
    const cover = action === 'move_cover';
    const objects = meta.objects.filter(o => o.kind === (cover ? 'cover' : 'plate'));
    const destinations = meta.destinations.filter(d => d[cover ? 'cover_destination' : 'plate_destination'] !== null && d[cover ? 'cover_destination' : 'plate_destination'] !== undefined);
    const prepareTokens = catalog.actions?.find(a => (a.action ?? a.id) === 'plate_prepare')?.inputs?.properties?.plate_ids?.items?.enum;
    const prepareObjects = objects.filter(o => !Array.isArray(prepareTokens) || prepareTokens.includes(o.token as never));
    const moving = action === 'plate_move' || cover;
    const destination = destinations.find(d => d.station === selection.station);
    const number = (key: string, label: string) => {
        const value = inputs[key], editable = value === undefined || typeof value === 'number' || typeof value === 'string' || isMethodNumber(value);
        return <label>{label}<input aria-label={label} type="text" inputMode="decimal" disabled={!editable} value={isMethodNumber(value) ? value.expr.value : editable && value !== undefined ? String(value) : ''} placeholder={editable ? 'Not set' : 'Retained null / expression'} onChange={e => set(key, isMethodNumber(value) ? methodNumber(e.target.value) : e.target.value)} /></label>;
    };
    return <section aria-label="Placement and operator stage editor">
        {moving || action.startsWith('plate_') ? <><p>Physical custody step when executed. Authoring sends no motion; named labware is planned accounting, not verified inventory.</p>
            {action !== 'plate_prepare' && choice('labware_id', 'Named labware', plan.labware.map(l => ({ value: l.id, label: l.name || l.id })))}
            {moving && <>{choice(cover ? 'cover_id' : 'plate_id', cover ? 'Cover to carry' : 'Plate to carry', objects.map(o => ({ value: o.token, label: String(o.label) })))}{choice('target_location', 'Placement destination', destinations.map(d => ({ value: d.token, label: String(d.label) })))}<button type="button" disabled={!destination} onClick={() => set('target_location', destination!.token)}>Use selected station for placement</button>{choice('move_mode', 'Placement mode', meta.press.token ? [{ value: meta.press.token, label: String(meta.press.label) }] : [])}<small>Omitted mode remains native behavior. Choose press only deliberately.</small></>}
            {(action === 'plate_catch' || action === 'plate_press') && choice('plate', 'Plate to handle', objects.map(o => ({ value: o.ordinal, label: String(o.label) })))}
            {action === 'plate_press' && <small>Standalone pool-plate Press uses the thermal-cycler press position. For destination-specific seating, choose Press on a plate-placement step.</small>}
            {action === 'plate_release' && choice('destination', 'Release destination', destinations.map(d => ({ value: d.plate_destination, label: String(d.label) })))}
            {action === 'plate_prepare' && <fieldset><legend>Plates to prepare</legend>{prepareObjects.map(o => <label key={String(o.token)}><input type="checkbox" aria-label={`Prepare ${o.label}`} disabled={inputs.plate_ids !== undefined && !Array.isArray(inputs.plate_ids)} checked={Array.isArray(inputs.plate_ids) && inputs.plate_ids.includes(o.token)} onChange={e => { const old = Array.isArray(inputs.plate_ids) ? inputs.plate_ids : []; set('plate_ids', e.target.checked ? [...old, o.token] : old.filter(v => v !== o.token)); }} />{String(o.label)}</label>)}</fieldset>}
        </> : action === 'wait' ? <>{number('seconds', 'Wait time (seconds)')}<p>Enter an operator-authored duration; no settling or drying time is prescribed.</p></> : <label>{action === 'checkpoint' ? 'Operator checkpoint message' : 'Method note'}<textarea aria-label={action === 'checkpoint' ? 'Operator checkpoint message' : 'Method note'} disabled={inputs.message !== undefined && typeof inputs.message !== 'string'} value={typeof inputs.message === 'string' ? inputs.message : ''} onChange={e => set('message', e.target.value)} /><small>{action === 'checkpoint' ? 'Host-only review pause; not an automated external actuator.' : 'Document preparation, sealing, assay or QC explicitly.'}</small></label>}
        <details onToggle={e => { if (e.currentTarget.open) setAdvanced(true); }}><summary>Advanced native custody / stage fields</summary>{advanced && <MethodFields label="Custody inputs" schema={catalog.actions?.find(a => (a.action ?? a.id) === action)?.inputs} value={inputs} onChange={v => onChange({ ...node, inputs: v })} />}</details>
    </section>;
}
