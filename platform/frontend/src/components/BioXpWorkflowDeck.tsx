import React, { useId, useRef, useState } from 'react';
import { deckBounds, deckPark, deckRegions, deckResources, deckSize, deckStations, selectedDeckWells,
    type BioXpDeckSelection, type DeckBounds, type DeckResource, type DeckSelectionMode, type DeckWell } from '../lib/bioxpWorkflowDeck';
import './BioXpWorkflowDeck.css';

export type { BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
export type BioXpWorkflowDeckProps = { selection: BioXpDeckSelection; onChange: (selection: BioXpDeckSelection) => void; readOnly?: boolean };
const fullView: DeckBounds = { x: 0, y: 0, ...deckSize };
const modes: DeckSelectionMode[] = ['well', 'range', 'row', 'column'];

/** Selection-only workbench. The native editor owns adoption, persistence and execution. */
export function BioXpWorkflowDeck({ selection, onChange, readOnly = false }: BioXpWorkflowDeckProps) {
    const id = useId();
    const svg = useRef<SVGSVGElement>(null);
    const anchor = useRef<{ station: string; well: string } | null>(null);
    const [mode, setMode] = useState<DeckSelectionMode>('well');
    const [view, setView] = useState<DeckBounds>(fullView);
    const [hover, setHover] = useState('');
    const [cursor, setCursor] = useState<Record<string, string>>({});
    const station = deckStations.find(s => s.id === selection.station);
    const active = deckResources.find(s => s.id === selection.station);
    const selected = new Set(selection.wells);
    const chooseStation = (next: string) => { if (readOnly) return; anchor.current = null; onChange({ station: next, wells: [] }); };
    const chooseWell = (resource: DeckResource, well: DeckWell, event: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }) => {
        if (readOnly) return;
        const start = anchor.current?.station === resource.id ? anchor.current.well : undefined;
        const effectiveMode = event.shiftKey ? 'range' : mode;
        const picked = selectedDeckWells(resource, well, effectiveMode, start);
        const previous = selection.station === resource.id ? selection.wells : [];
        const next = new Set(previous);
        if (event.ctrlKey || event.metaKey) picked.forEach(w => next.has(w) ? next.delete(w) : next.add(w));
        else { next.clear(); picked.forEach(w => next.add(w)); }
        if (effectiveMode !== 'range' || !start) anchor.current = { station: resource.id, well: well.well };
        setCursor(current => ({ ...current, [resource.id]: well.well }));
        onChange({ station: resource.id, wells: resource.wells.filter(w => next.has(w)) });
    };
    const focusStation = () => {
        const bounds = active?.bounds ?? deckRegions.find(r => r.id === selection.station)?.bounds;
        if (bounds) setView({ x: bounds.x - 55, y: bounds.y - 65, width: bounds.width + 110, height: bounds.height + 110 });
        else if (selection.station === deckPark.id) setView({ x: deckPark.point[0] - 120, y: deckPark.point[1] - 100, width: 240, height: 200 });
    };
    const zoom = (factor: number) => setView(current => {
        const width = Math.min(deckSize.width * 2, Math.max(90, current.width * factor));
        const height = current.height * width / current.width;
        return { x: current.x + (current.width - width) / 2, y: current.y + (current.height - height) / 2, width, height };
    });
    const pan = (x: number, y: number) => setView(current => ({ ...current, x: current.x + current.width * x, y: current.y + current.height * y }));
    const keyboardWell = (event: React.KeyboardEvent<SVGGElement>, resource: DeckResource, well: DeckWell) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); chooseWell(resource, well, event); return; }
        const delta = ({ ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] } as Record<string, number[]>)[event.key];
        if (!delta) return;
        event.preventDefault();
        const next = resource.points.find(p => p.row === well.row + delta[0] && p.column === well.column + delta[1]);
        if (!next) return;
        setCursor(current => ({ ...current, [resource.id]: next.well }));
        svg.current?.querySelector<SVGGElement>(`[data-station="${resource.id}"][data-well="${next.well}"]`)?.focus();
        if (event.shiftKey) chooseWell(resource, next, event);
    };
    const keyboardStation = (event: React.KeyboardEvent<SVGGElement>, stationId: string) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); chooseStation(stationId); }
    };
    const strips = deckResources.filter(r => r.kind === 'strip');
    const stripBounds = deckBounds(strips.flatMap(r => r.points));
    return <section className="bioxp-workflow-deck" aria-label="BioXP planned deck selection">
        <div className="bwd-heading"><div><h3>{readOnly ? 'Native action location' : 'Deck selection'}</h3><p>{readOnly ? 'Highlighted from the compiled action. Use the action scrubber to follow the sequence.' : 'Choose a station, then the wells for your plan.'}</p></div><span className="bwd-badge">2D deck</span></div>
        <div className="bwd-station-control">{readOnly ? <strong>{station?.label ?? 'No new XY destination'}</strong> : <><label htmlFor={`${id}-station`}>Station</label>
            <select id={`${id}-station`} aria-label="Deck station" value={station?.id ?? ''} onChange={e => chooseStation(e.target.value)}>
                <option value="">Choose a station</option>
                <optgroup label="Processing">{deckStations.filter(s => s.id.startsWith('LOC_') && s.wells.length === 96).map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>
                <optgroup label="Tips and reagent strips">{deckStations.filter(s => s.tipTray !== null || s.wells.length === 8).map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>
                <optgroup label="Utility and cover regions">{deckStations.filter(s => !s.wells.length).map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>
            </select></>}
            <button type="button" onClick={focusStation} disabled={!station}>Focus station</button>
        </div>
        {!readOnly && <div className="bwd-toolbar" role="group" aria-label="Well selection mode"><span>Select</span>{modes.map(m => <button type="button" key={m} aria-pressed={mode === m} onClick={() => { setMode(m); anchor.current = null; }}>{m[0].toUpperCase() + m.slice(1)}</button>)}
            <button type="button" className="bwd-clear" onClick={() => { anchor.current = null; onChange({ station: selection.station, wells: [] }); }}>Clear selection</button>
        </div>}
        <div className="bwd-map">
            <svg ref={svg} viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`} role="group" aria-label="BioXP deck map" aria-describedby={`${id}-help`}>
                <defs><pattern id={`${id}-dots`} width="28" height="28" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="0.8" className="bwd-dot" /></pattern></defs>
                <rect width={deckSize.width} height={deckSize.height} fill={`url(#${id}-dots)`} />
                <rect className="bwd-strip-holder" x={stripBounds.x - 18} y={stripBounds.y - 25} width={stripBounds.width + 36} height={stripBounds.height + 50} rx="8" />
                <text className="bwd-map-label" x={stripBounds.x - 19} y={stripBounds.y - 39}>Reagent strips</text>
                {deckResources.map(resource => {
                    const { x, y, width, height } = resource.bounds;
                    const strip = resource.kind === 'strip';
                    const tabWell = cursor[resource.id] ?? (selection.station === resource.id && resource.wells.includes(selection.wells[0]) ? selection.wells[0] : resource.wells[0]);
                    return <g key={resource.id} className={`bwd-resource bwd-${resource.kind}${selection.station === resource.id ? ' is-active' : ''}`} data-resource={resource.id}>
                        <g role={readOnly ? 'img' : 'button'} tabIndex={readOnly ? undefined : 0} aria-label={`Select ${resource.label}`} aria-pressed={selection.station === resource.id}
                            onClick={() => chooseStation(resource.id)} onKeyDown={e => keyboardStation(e, resource.id)} onDoubleClick={() => setView({ x: x - 55, y: y - 65, width: width + 110, height: height + 110 })}>
                            <rect className="bwd-body" x={x - (strip ? 10 : 21)} y={y - (strip ? 17 : 44)} width={width + (strip ? 20 : 42)} height={height + (strip ? 34 : 66)} rx={strip ? 9 : 8} />
                            <rect className="bwd-band" x={x - (strip ? 7 : 21)} y={y - (strip ? 13 : 44)} width={width + (strip ? 14 : 42)} height="5" rx="2" />
                            {!strip && <><rect className="bwd-inset" x={x - 13} y={y - 9} width={width + 26} height={height + 19} rx="5" />
                                <text className="bwd-map-label" x={x - 10} y={y - 23}>{resource.label}</text>
                                {Array.from({ length: 7 }, (_, i) => <rect className="bwd-vent" key={i} x={x + 8 + i * 9} y={y + height + 15} width="5" height="2.5" rx="1" />)}</>}
                            {strip && <text className="bwd-map-label" x={x} y={y + height + 36} textAnchor="middle">{resource.label.slice(-1)}</text>}
                        </g>
                        {resource.points.map(well => <g key={well.well} role={readOnly ? 'img' : 'button'} tabIndex={readOnly ? undefined : tabWell === well.well ? 0 : -1}
                            aria-label={`${resource.label} ${well.well}`} aria-pressed={selection.station === resource.id && selected.has(well.well)}
                            data-station={resource.id} data-well={well.well}
                            className={`bwd-well${selection.station === resource.id && selected.has(well.well) ? ' is-selected' : ''}`}
                            onClick={e => chooseWell(resource, well, e)} onKeyDown={e => keyboardWell(e, resource, well)}
                            onFocus={() => setHover(`${resource.label} · ${well.well}`)} onMouseEnter={() => setHover(`${resource.label} · ${well.well}`)}>
                            <title>{resource.label} · {well.well}</title>
                            <circle className="bwd-ring" cx={well.x} cy={well.y} r="5.4" />
                            <circle className="bwd-core" cx={well.x} cy={well.y} r="3.8" />
                            {resource.kind === 'rack' && <circle className="bwd-tip" cx={well.x} cy={well.y} r="1.35" />}
                            <circle className="bwd-hit" cx={well.x} cy={well.y} r="8.5" />
                        </g>)}
                    </g>;
                })}
                {deckRegions.map(region => <g key={region.id} className={`bwd-region${selection.station === region.id ? ' is-active' : ''}`} role={readOnly ? 'img' : 'button'} tabIndex={readOnly ? undefined : 0}
                    aria-label={`Select ${region.label}`} aria-pressed={selection.station === region.id} onClick={() => chooseStation(region.id)} onKeyDown={e => keyboardStation(e, region.id)}>
                    <title>{region.label} · illustration-derived outline estimate</title>
                    {region.layers.map(layer => <polygon key={layer.name} className={`bwd-region-${layer.name}`} points={layer.points.map(p => `${p.x},${p.y}`).join(' ')} />)}
                    <text className="bwd-map-label" x={region.bounds.x} y={region.bounds.y - 15}>{region.label}</text>
                </g>)}
                <g className={`bwd-park${selection.station === deckPark.id ? ' is-active' : ''}`} role={readOnly ? 'img' : 'button'} tabIndex={readOnly ? undefined : 0} aria-label="Select Park target" aria-pressed={selection.station === deckPark.id} onClick={() => chooseStation(deckPark.id)} onKeyDown={e => keyboardStation(e, deckPark.id)}>
                    <circle cx={deckPark.point[0]} cy={deckPark.point[1]} r="6" /><text className="bwd-map-label" x={deckPark.point[0] + 12} y={deckPark.point[1] + 4}>Park target</text>
                </g>
            </svg>
        </div>
        <div className="bwd-navigation" role="group" aria-label="Deck view controls">
            <button type="button" aria-label="Zoom out" onClick={() => zoom(1.3)}>−</button><span>{Math.round(deckSize.width / view.width * 100)}%</span>
            <button type="button" aria-label="Zoom in" onClick={() => zoom(1 / 1.3)}>+</button><button type="button" onClick={() => setView(fullView)}>Fit deck</button>
            <button type="button" aria-label="Pan left" onClick={() => pan(-0.2, 0)}>←</button><button type="button" aria-label="Pan up" onClick={() => pan(0, -0.2)}>↑</button>
            <button type="button" aria-label="Pan down" onClick={() => pan(0, 0.2)}>↓</button><button type="button" aria-label="Pan right" onClick={() => pan(0.2, 0)}>→</button>
        </div>
        <div className="bwd-inspector">
            <div className="bwd-selection-summary" aria-live="polite"><strong>{station?.label ?? 'No station selected'}</strong><span>{selection.wells.length} wells selected</span></div>
            <p className="bwd-hover">{hover || 'Focus a station for a closer view of its wells.'}</p>
            {active && !readOnly && <div className="bwd-address-grid" style={{ gridTemplateColumns: `28px repeat(${active.kind === 'strip' ? 1 : 12}, minmax(24px, 1fr))` }} aria-label={`${active.label} well selection`}>
                <span />{active.points.filter(w => w.row === 0).map(w => <button type="button" key={w.column} aria-label={`Select column ${w.column + 1}`} title={`Select column ${w.column + 1}`} onClick={() => { anchor.current = null; onChange({ station: active.id, wells: selectedDeckWells(active, w, 'column') }); }}>{w.column + 1}</button>)}
                {Array.from({ length: 8 }, (_, row) => <React.Fragment key={row}>
                    <button type="button" aria-label={`Select row ${String.fromCharCode(65 + row)}`} onClick={() => { anchor.current = null; onChange({ station: active.id, wells: selectedDeckWells(active, active.points.find(w => w.row === row)!, 'row') }); }}>{String.fromCharCode(65 + row)}</button>
                    {active.points.filter(w => w.row === row).map(w => <button type="button" key={w.well} aria-label={`Select well ${w.well}`} aria-pressed={selected.has(w.well)} onClick={e => chooseWell(active, w, e)}>{w.well}</button>)}
                </React.Fragment>)}
            </div>}
            {selection.wells.length > 0 && <p className="bwd-chips">{selection.wells.slice(0, 12).map(w => <span key={w}>{w}</span>)}{selection.wells.length > 12 && <span>+{selection.wells.length - 12} more</span>}</p>}
            {station && !active && <p>{station.locationId === null ? 'Inspect-only region; no native workflow binding.' : 'Utility / cover region. Choose any native action in the step editor.'}</p>}
        </div>
        <p id={`${id}-help`} className="bwd-help">{readOnly ? 'Preview only; zoom and pan change the view, not the plan or hardware.' : 'Click or press Enter / Space to select. Shift selects a rectangular range; Ctrl / ⌘ toggles wells. Arrow keys move between map wells. Range mode: choose the first and last well.'}</p>
        {!readOnly && <p className="bwd-help">Selection only — no robot movement. Multiple wells do not change the fixed four-head alignment.</p>}
        <p className="bwd-caption">Native well positions · cover and utility outlines are artwork estimates, not measured dimensions.</p>
    </section>;
}
