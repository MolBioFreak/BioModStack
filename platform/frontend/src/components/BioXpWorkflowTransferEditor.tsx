import type { ReactNode } from 'react';
import { deckStations, type BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import type { TransferEndpoint, WorkflowTransferIntent } from '../lib/bioxpWorkflowPlan';
import './BioXpWorkflowMaterials.css';

export interface BioXpWorkflowTransferEditorProps {
    value: WorkflowTransferIntent;
    onChange: (value: WorkflowTransferIntent) => void;
    selection: BioXpDeckSelection;
    onSelect?: (selection: BioXpDeckSelection) => void;
    liquidSettings?: ReactNode;
    compact?: boolean;
}
export function BioXpWorkflowTransferEditor({ value, onChange, selection, onSelect, liquidSettings, compact = false }: BioXpWorkflowTransferEditorProps) {
    const patch = (change: Partial<WorkflowTransferIntent>) => onChange({ ...value, ...change });
    const selectedStation = deckStations.find(s => s.id === selection.station);
    const endpoint = (side: 'source' | 'destination', change: Partial<TransferEndpoint>) => patch({ [side]: { ...value[side], ...change } });
    const moveWell = (side: 'source' | 'destination', index: number, delta: number) => {
        const wells = [...value[side].wells];
        [wells[index], wells[index + delta]] = [wells[index + delta], wells[index]];
        endpoint(side, { wells });
    };
    return <section className="bioxp-plan-leaf" aria-label="Transfer editor">
        <header><h3>Transfer</h3>{!compact && <p>Ordered head-reference pairs. Selected plunger channels do not reposition the head; native TipLocation owns actual alignment.</p>}</header>
        <p>Deck selection: {selectedStation?.label || selection.station || 'None'} · {selection.wells.join(', ') || 'No reference wells'}</p>
        <div className="bioxp-plan-columns">{(['source', 'destination'] as const).map(side => {
            const title = side === 'source' ? 'Source' : 'Destination';
            const current = value[side];
            const flag = value[`${side}_position_flag`];
            const lift = value[`${side}_lift_height_steps`];
            return <fieldset key={side}><legend>{title}</legend>
                <strong>{deckStations.find(s => s.id === current.station)?.label || current.station || 'Not selected'}</strong>
                <small>Native location: {current.location_id === '' ? 'Not set' : current.location_id}</small>
                <div className="bioxp-plan-actions">
                    <button type="button" disabled={!selectedStation || selectedStation.locationId === null} onClick={() => {
                        if (selectedStation?.locationId == null) return;
                        endpoint(side, { station: selectedStation.id, location_id: selectedStation.locationId, wells: [...selection.wells] });
                    }}>Use deck selection as {side}</button>
                    {onSelect && <button type="button" disabled={!current.station} onClick={() => onSelect({ station: current.station, wells: [...current.wells] })}>Show {side} on deck</button>}
                    <button type="button" onClick={() => patch({ [side]: { station: '', location_id: '', wells: [] } })}>Clear {side}</button>
                </div>
                <ol className="bioxp-reference-list" aria-label={`${title} reference order`}>{current.wells.map((well, index) => <li key={index}>
                    <label>{title} reference {index + 1}<input value={well} onChange={e => endpoint(side, { wells: current.wells.map((w, i) => i === index ? e.target.value : w) })} /></label>
                    <div className="bioxp-plan-actions"><button type="button" aria-label={`Move ${side} reference ${index + 1} earlier`} disabled={index === 0} onClick={() => moveWell(side, index, -1)}>↑</button>
                    <button type="button" aria-label={`Move ${side} reference ${index + 1} later`} disabled={index === current.wells.length - 1} onClick={() => moveWell(side, index, 1)}>↓</button>
                    <button type="button" onClick={() => endpoint(side, { wells: current.wells.filter((_, i) => i !== index) })}>Remove {side} reference {index + 1}</button></div>
                </li>)}</ol>
                <button type="button" onClick={() => endpoint(side, { wells: [...current.wells, ''] })}>Add {side} reference</button>
                <label>{title} move Z position<select value={flag ?? ''} onChange={e => patch({ [`${side}_position_flag`]: e.target.value })}>
                    <option value="">Choose move height</option><option value="0">Clearance height</option><option value="1">Calibrated high</option><option value="2">Calibrated low</option>
                    {flag != null && !['', '0', '1', '2'].includes(String(flag)) && <option value={flag}>Saved value: {flag}</option>}
                </select></label>
                <label>{title} lift target<select value={lift === null ? 'high' : ''} onChange={e => {
                    patch({ [`${side}_lift_height_steps`]: e.target.value === 'high' ? null : '' });
                }}><option value="">Explicit height (enter steps below)</option><option value="high">Calibrated high (native null)</option></select></label>
                <label>{title} lift height (steps above calibrated low)<input inputMode="decimal" disabled={lift === null} value={lift ?? ''} onChange={e => patch({ [`${side}_lift_height_steps`]: e.target.value })} /></label>
                <small>{lift === null ? 'Deliberate calibrated-high lift.' : 'Blank height is incomplete, not calibrated high.'}</small>
            </fieldset>;
        })}</div>
        <fieldset><legend>Ordered pairing</legend>
            <ol aria-label="Transfer pairs">{Array.from({ length: Math.max(value.source.wells.length, value.destination.wells.length) }, (_, i) => <li key={i}>{value.source.wells[i] || 'Missing source'} → {value.destination.wells[i] || 'Missing destination'}</li>)}</ol>
            {value.source.wells.length !== value.destination.wells.length && <p role="status">Reference lists must have equal lengths to compile. This incomplete draft can still be saved.</p>}
            <button type="button" onClick={() => patch({ source: value.destination, destination: value.source,
                source_position_flag: value.destination_position_flag, destination_position_flag: value.source_position_flag,
                source_lift_height_steps: value.destination_lift_height_steps, destination_lift_height_steps: value.source_lift_height_steps,
            })}>Swap source and destination</button>
        </fieldset>
        <fieldset><legend>Plunger channels</legend><div className="bioxp-plan-actions">{Array.from({ length: 4 }, (_, channel) => <label className="bioxp-channel-choice" key={channel}><input type="checkbox" checked={value.channels.includes(channel)} onChange={e => patch({ channels: e.target.checked ? [...value.channels, channel] : value.channels.filter(c => c !== channel) })} />Pipette {channel + 1}</label>)}</div>
            {value.channels.some(c => c < 0 || c > 3 || !Number.isInteger(c)) && <small>Saved channels also include: {value.channels.filter(c => c < 0 || c > 3 || !Number.isInteger(c)).join(', ')}</small>}
            <button type="button" onClick={() => patch({ channels: [] })}>Clear channels</button>
        </fieldset>
        <div className="bioxp-plan-columns">{([['volume_ul', 'Volume per channel (µL)'], ['aspirate_speed', 'Aspirate speed'], ['dispense_speed', 'Dispense speed']] as const).filter(([key]) => !liquidSettings || key === 'volume_ul').map(([key, label]) => <label key={key}>{label}<input aria-label={label} inputMode="decimal" value={value[key] ?? ''} onChange={e => patch({ [key]: e.target.value })} /></label>)}</div>
        {liquidSettings}
        {!compact && <small>Each pair: move → lower → aspirate → lift → move → lower → dispense → lift. No implicit tip loading, mixing or sensing.</small>}
    </section>;
}
export default BioXpWorkflowTransferEditor;
