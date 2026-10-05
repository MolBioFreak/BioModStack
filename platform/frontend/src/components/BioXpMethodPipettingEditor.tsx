import type { MethodCatalog, MethodValue } from '../lib/bioxpMethods';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { deckResources, deckStations, type BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import { MethodFields, object } from './BioXpMethodFields';
import './BioXpMethodPipettingEditor.css';

const scalar = (value: unknown) => typeof value === 'string' || typeof value === 'number' ? value : '';
const display = (value: unknown): string => value === undefined ? 'Not set' : typeof value === 'string' ? value || 'Blank' : JSON.stringify(value);
/** Friendly projections change only edited fields; typed advanced inputs remain lossless. */
export function BioXpMethodPipettingEditor({ node, onChange, catalog, selection, onSelect, endpointLabels }: {
    node: MethodValue; onChange: (next: MethodValue) => void; catalog: MethodCatalog;
    selection: BioXpDeckSelection; onSelect: (next: BioXpDeckSelection) => void;
    /** Presentation only: planned labware names, never persisted into native inputs. */
    endpointLabels?: Partial<Record<'source' | 'destination', string>>;
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
    const selectedStation = deckStations.find(s => s.id === selection.station);
    const endpoints = ['source', 'destination'] as const;
    const references = (side: typeof endpoints[number]): unknown[] => Array.isArray(object(inputs[side]).wells) ? object(inputs[side]).wells as unknown[] : [];
    const editEndpoint = (side: typeof endpoints[number], change: MethodValue) => patch({ [side]: { ...object(inputs[side]), ...change } });
    const channels = Array.isArray(inputs.channels) ? inputs.channels : [];
    const volumeEditable = inputs.volume_ul === undefined || typeof inputs.volume_ul === 'string' || typeof inputs.volume_ul === 'number';
    const classResolved = Object.hasOwn(inputs, 'liquid') || Object.hasOwn(inputs, 'recipe');
    const recipe = object(inputs.recipe);
    const recipeSchema = inputSchema?.oneOf?.find(s => s.properties?.recipe)?.properties?.recipe;
    const recipeModes = recipeSchema?.properties?.mode?.enum?.filter((v): v is string => typeof v === 'string') ?? [];
    const recipeEditable = (inputs.recipe === undefined || inputs.recipe !== null && typeof inputs.recipe === 'object' && !Array.isArray(inputs.recipe) && !recipe.expr) && (recipe.mode === undefined || typeof recipe.mode === 'string');
    return <section className="bioxp-transfer-scene" aria-label="Transfer editor">
        <div className="bioxp-transfer-link">{endpoints.map((side, index) => {
            const current = object(inputs[side]);
            const station = deckStations.find(s => s.id === current.station);
            const resource = deckResources.find(s => s.id === current.station);
            const wells = references(side);
            const title = side === 'source' ? 'Source' : 'Destination';
            const columns = resource ? Math.max(...resource.points.map(p => p.column)) + 1 : 0;
            return <div className="bioxp-transfer-endpoint" key={side}>
                {index === 1 && <span className="bioxp-transfer-arrow" aria-hidden="true">→</span>}
                <header><small>{title}</small><h3>{endpointLabels?.[side] || station?.label || display(current.station)}</h3><span>{wells.length} references</span></header>
                {resource ? <div className="bioxp-transfer-plate" role="group" aria-label={`${title} wells`} style={{ gridTemplateColumns: `1.2em repeat(${columns}, minmax(0, 1fr))` }}>
                    <span />{Array.from({ length: columns }, (_, i) => <small key={i}>{i + 1}</small>)}
                    {Array.from({ length: 8 }, (_, row) => <div className="bioxp-transfer-row" key={row}><small>{String.fromCharCode(65 + row)}</small>{resource.points.filter(p => p.row === row).map(p => <button key={p.well} type="button" aria-label={`${title} well ${p.well}`} aria-pressed={selection.station === resource.id && selection.wells.includes(p.well)} data-authored={wells.includes(p.well)} title={`${p.well}${wells.includes(p.well) ? ` · reference ${wells.indexOf(p.well) + 1}` : ''}`} onClick={() => onSelect({ station: resource.id, wells: selection.station !== resource.id ? [p.well] : selection.wells.includes(p.well) ? selection.wells.filter(w => w !== p.well) : [...selection.wells, p.well] })}><span>{wells.includes(p.well) ? wells.indexOf(p.well) + 1 : ''}</span></button>)}</div>)}
                </div> : <div className="bioxp-transfer-unmapped">{display(inputs[side])}<p>Select a deck station to view its wells.</p></div>}
                <p className="bioxp-transfer-reference-summary">{wells.length ? wells.map(display).join(' → ') : 'No authored references'}<small>{station?.label || display(current.station)} · location {display(current.location_id)}</small></p>
                <div className="bioxp-transfer-tools"><button type="button" disabled={selectedStation?.locationId == null} onClick={() => { if (selectedStation?.locationId != null) editEndpoint(side, { station: selectedStation.id, location_id: selectedStation.locationId, wells: [...selection.wells] }); }}>Use deck selection as {side}</button><button type="button" disabled={typeof current.station !== 'string'} onClick={() => onSelect({ station: String(current.station), wells: wells.filter((w): w is string => typeof w === 'string') })}>Show {side} on deck</button></div>
            </div>;
        })}</div>
        <div className="bioxp-transfer-selection" role="status">Selection: {selectedStation?.label || selection.station || 'None'} · {selection.wells.join(', ') || 'No wells'}<small>Click wells to select; use an endpoint button to adopt. Filled wells and numbers show authored order.</small></div>
        <div className="bioxp-transfer-amount"><label>Volume per channel<div><input aria-label="Volume per channel (µL)" inputMode="decimal" value={scalar(inputs.volume_ul)} disabled={!volumeEditable} placeholder={volumeEditable ? '—' : 'Retained'} onChange={e => patch({ volume_ul: e.target.value })} /><span>µL / channel / pair</span></div></label><div><strong>Ordered head-reference pairs</strong><ol aria-label="Transfer pairs">{Array.from({ length: Math.max(references('source').length, references('destination').length) }, (_, i) => <li key={i}>{display(references('source')[i])} → {display(references('destination')[i])}</li>)}</ol><small>No source broadcasting. Each source pairs with the destination at the same index.</small>{!volumeEditable && <small>Volume: {display(inputs.volume_ul)} · edit in Advanced.</small>}</div></div>
        <fieldset className="bioxp-transfer-channels"><legend>Plunger channels</legend>{[0, 1, 2, 3].map(channel => <label key={channel}><input type="checkbox" aria-label={`Transfer pipette ${channel + 1}`} checked={channels.includes(channel)} disabled={inputs.channels !== undefined && !Array.isArray(inputs.channels)} onChange={e => patch({ channels: e.target.checked ? [...channels, channel] : channels.filter(c => c !== channel) })} />Pipette {channel + 1}</label>)}<small>Shared head positioning · native TipLocation alignment. Saved channels: {display(inputs.channels)}</small></fieldset>
        <details className="bioxp-transfer-order"><summary>Reference order</summary>{endpoints.map(side => <div key={side}><h4>{side === 'source' ? 'Source' : 'Destination'}</h4><ol>{references(side).map((well, index, wells) => <li key={index}>{display(well)}<button type="button" aria-label={`Move ${side} reference ${index + 1} earlier`} disabled={index === 0} onClick={() => { const next = [...wells]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; editEndpoint(side, { wells: next }); }}>↑</button><button type="button" aria-label={`Move ${side} reference ${index + 1} later`} disabled={index === wells.length - 1} onClick={() => { const next = [...wells]; [next[index + 1], next[index]] = [next[index], next[index + 1]]; editEndpoint(side, { wells: next }); }}>↓</button></li>)}</ol></div>)}</details>
        {recipeModes.length > 0 && <label className="bioxp-transfer-recipe-mode">Native recipe mode<select aria-label="Native recipe mode" disabled={!recipeEditable} value={typeof recipe.mode === 'string' ? recipe.mode : ''} onChange={e => { if (e.target.value) patch({ recipe: { ...recipe, mode: e.target.value } }); }}><option value="">{recipeEditable ? 'Not authored' : 'Retained value — Advanced'}</option>{typeof recipe.mode === 'string' && !recipeModes.includes(recipe.mode) && <option value={recipe.mode}>Saved: {recipe.mode}</option>}{recipeModes.map(mode => <option key={mode} value={mode}>{mode}</option>)}</select></label>}
        <p className="bioxp-transfer-recipe"><strong>Liquid class & recipe</strong><span>{classResolved ? `Liquid mode: ${display(object(object(inputs.liquid).context).mode)} · Recipe mode: ${display(recipe.mode)}` : 'Explicit native settings · no liquid class or recipe authored'}</span><small>Choose or edit the declared native recipe and liquid settings in Advanced; no implicit tips or mixing.</small></p>
        {advanced}
    </section>;
}
