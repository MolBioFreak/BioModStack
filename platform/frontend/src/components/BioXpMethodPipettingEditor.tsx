import type { MethodCatalog, MethodValue } from '../lib/bioxpMethods';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { deckStations, type BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import { emptyTransferIntent, type WorkflowTransferIntent } from '../lib/bioxpWorkflowPlan';
import { mergeDraftEdits, type DraftObject } from '../lib/bioxpWorkflowDraft';
import { BioXpWorkflowTransferEditor } from './BioXpWorkflowTransferEditor';
import { MethodFields, object } from './BioXpMethodFields';

const scalar = (value: unknown) => typeof value === 'string' || typeof value === 'number' ? value : '';
const projectTransfer = (inputs: MethodValue): WorkflowTransferIntent => {
    const blank = emptyTransferIntent();
    const endpoint = (value: unknown) => {
        const raw = object(value);
        return { station: typeof raw.station === 'string' ? raw.station : '', location_id: scalar(raw.location_id), wells: Array.isArray(raw.wells) ? raw.wells.filter((v): v is string => typeof v === 'string') : [] };
    };
    return { ...blank, ...Object.fromEntries(Object.keys(blank).filter(key => typeof inputs[key] === 'string' || typeof inputs[key] === 'number' || key.endsWith('lift_height_steps') && inputs[key] === null).map(key => [key, inputs[key]])), source: endpoint(inputs.source), destination: endpoint(inputs.destination), channels: Array.isArray(inputs.channels) ? inputs.channels.filter((v): v is number => typeof v === 'number') : [] };
};

/** Friendly projections change only edited fields; typed advanced inputs remain lossless. */
export function BioXpMethodPipettingEditor({ node, onChange, catalog, selection, onSelect }: {
    node: MethodValue; onChange: (next: MethodValue) => void; catalog: MethodCatalog;
    selection: BioXpDeckSelection; onSelect: (next: BioXpDeckSelection) => void;
}) {
    const inputs = object(node.inputs);
    const action = node.action === 'native_intent' ? inputs.operation : node.action;
    if (!['move', 'transfer', 'lower', 'lift', 'mix'].includes(String(action)) || inputs.expr) return null;
    const entry = catalog.actions?.find(a => (a.action ?? a.id) === action);
    const sourceSchema = entry?.input_schema ?? entry?.inputs;
    const inputSchema = sourceSchema ? { ...sourceSchema, $defs: { ...object(catalog.native_definitions) as Record<string, Schema>, ...sourceSchema.$defs } } : undefined;
    const patch = (next: MethodValue) => onChange({ ...node, inputs: { ...inputs, ...next } });
    const advanced = <details><summary>Advanced input fields & expressions</summary><MethodFields label={`Inputs ${node.step_id}`} schema={inputSchema} value={inputs} onChange={next => onChange({ ...node, inputs: next })} /></details>;
    if (['lower', 'lift', 'mix'].includes(String(action))) {
        const station = deckStations.find(s => s.locationId != null && String(s.locationId) === String(inputs.location_id));
        const numeric = (key: string, title: string) => {
            const editable = inputs[key] === undefined || typeof inputs[key] === 'string' || typeof inputs[key] === 'number';
            return <label>{title}<input aria-label={title} inputMode="decimal" value={scalar(inputs[key])} disabled={!editable} placeholder={!editable ? 'Retained value — see Advanced' : 'Not set'} onChange={e => patch({ [key]: e.target.value })} /></label>;
        };
        const channels = Array.isArray(inputs.channels) ? inputs.channels : [];
        return <section aria-label="Pipetting step settings" className="bioxp-plan-leaf"><h3>{action === 'mix' ? 'Mix at current position' : action === 'lower' ? 'Lower pipettes' : 'Lift pipettes'}</h3>
            {action !== 'mix' ? <label>Station<select aria-label="Pipetting station" value={station?.id ?? ''} onChange={e => { const target = deckStations.find(s => s.id === e.target.value); if (target?.locationId != null) patch({ location_id: target.locationId }); }}><option value="">{inputs.location_id != null ? `Saved location ${String(inputs.location_id)}` : 'Choose station…'}</option>{deckStations.filter(s => s.locationId != null).map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</select></label> : <><p>Mix uses the current head position; authored Move / Lower / Lift steps remain separate.</p><fieldset><legend>Plunger channels</legend>{[0, 1, 2, 3].map(channel => <label key={channel}><input aria-label={`Mix pipette ${channel + 1}`} type="checkbox" disabled={inputs.channels !== undefined && !Array.isArray(inputs.channels)} checked={channels.includes(channel)} onChange={e => patch({ channels: e.target.checked ? [...channels, channel] : channels.filter(c => c !== channel) })} />Pipette {channel + 1}</label>)}</fieldset>{numeric('volume_ul', 'Mix volume per plunger (µL)')}{numeric('aspirate_speed', 'Mix aspirate speed')}{numeric('dispense_speed', 'Mix dispense speed')}{numeric('cycles', 'Mix cycles')}</>}
            {action === 'lift' && numeric('height_steps', 'Lift height (steps)')}{advanced}
        </section>;
    }
    if (action === 'move') {
        const station = deckStations.find(s => s.locationId != null && String(s.locationId) === String(inputs.location_id));
        const flag = scalar(inputs.position_flag);
        return <section aria-label="Move settings" className="bioxp-plan-leaf">
            <label>Station<select aria-label="Move station" value={station?.id ?? ''} onChange={e => { const target = deckStations.find(s => s.id === e.target.value); if (target?.locationId != null) patch({ location_id: target.locationId }); }}><option value="">{inputs.location_id != null ? `Saved location ${String(inputs.location_id)}` : 'Choose a station…'}</option>{deckStations.filter(s => s.locationId != null).map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</select></label>
            <label>Reference well<input aria-label="Move reference well" value={scalar(inputs.well)} onChange={e => patch({ well: e.target.value })} /></label>
            <label>Move height<select aria-label="Move height" value={flag} onChange={e => patch({ position_flag: e.target.value })}><option value="">Choose height…</option><option value="0">Clearance height</option><option value="1">Calibrated high</option><option value="2">Calibrated low</option>{flag !== '' && !['0', '1', '2'].includes(String(flag)) && <option value={flag}>Saved value: {flag}</option>}</select></label>
            {advanced}
        </section>;
    }
    const value = projectTransfer(inputs);
    const classResolved = Object.hasOwn(inputs, 'liquid') || Object.hasOwn(inputs, 'recipe');
    const classSchema = inputSchema?.oneOf?.find(s => s.properties?.liquid) as Schema | undefined;
    const rootSchema = { ...classSchema, $defs: { ...object(catalog.native_definitions), ...inputSchema?.$defs } } as Schema;
    const liquidSettings = classResolved ? <details><summary>Liquid class & recipe</summary>
        <MethodFields label="Liquid settings" schema={classSchema?.properties?.liquid} rootSchema={rootSchema} value={inputs.liquid} onChange={liquid => patch({ liquid })} />
        <MethodFields label="Liquid recipe" schema={classSchema?.properties?.recipe} rootSchema={rootSchema} value={inputs.recipe} onChange={recipe => patch({ recipe })} />
    </details> : undefined;
    return <><BioXpWorkflowTransferEditor compact value={value} selection={selection} onSelect={onSelect} liquidSettings={liquidSettings} onChange={next => onChange({ ...node, inputs: mergeDraftEdits(inputs as DraftObject, value as unknown as DraftObject, next as unknown as DraftObject) })} />{advanced}</>;
}
