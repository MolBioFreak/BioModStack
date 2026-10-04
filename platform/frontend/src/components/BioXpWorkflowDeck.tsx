import React, { useId, useRef, useState } from 'react';
import { deckBounds, deckPark, deckRegions, deckResources, deckSize, deckStations, selectedDeckWells,
    type BioXpDeckSelection, type DeckBounds, type DeckResource, type DeckSelectionMode, type DeckWell } from '../lib/bioxpWorkflowDeck';
import './BioXpWorkflowDeck.css';

export type { BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
export type BioXpDeckLiveIntent = {
    onStation: (target: string) => void;
    onWell: (locationId: number, well: string) => void;
    stationDisabledReason: (target: string) => string | null;
    wellDisabledReason: string | null;
    selectedStation?: string | null;
};
export type BioXpWorkflowDeckProps = {
    selection: BioXpDeckSelection; onChange: (selection: BioXpDeckSelection) => void; readOnly?: boolean;
    live?: BioXpDeckLiveIntent; resources?: DeckResource[]; overlay?: React.ReactNode;
    compact?: boolean;
};
const fullView: DeckBounds = { x: 0, y: 0, ...deckSize };
const modes: DeckSelectionMode[] = ['well', 'range', 'row', 'column'];

/** Shared presentation. Authoring defaults remain selection-only; live intents are explicit callbacks. */
export function BioXpWorkflowDeck({ selection, onChange, readOnly = false, live, resources = deckResources, overlay, compact = false }: BioXpWorkflowDeckProps) {
    const id = useId();
    const svg = useRef<SVGSVGElement>(null);
    const anchor = useRef<{ station: string; well: string } | null>(null);
    const [mode, setMode] = useState<DeckSelectionMode>('well');
    const [view, setView] = useState<DeckBounds>(fullView);
    const [hover, setHover] = useState('');
    const [focusTarget, setFocusTarget] = useState('');
    const [cursor, setCursor] = useState<Record<string, string>>({});
    const station = deckStations.find(s => s.id === selection.station);
    const active = resources.find(s => s.id === selection.station);
    const selected = new Set(selection.wells);
    const chooseStation = (next: string) => {
        if (readOnly) return;
        if (live) { if (!live.stationDisabledReason(next)) live.onStation(next); return; }
        anchor.current = null; onChange({ station: next, wells: [] });
    };
    const chooseWell = (resource: DeckResource, well: DeckWell, event: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }) => {
        if (readOnly) return;
        if (live) { if (!live.wellDisabledReason && resource.locationId !== null) live.onWell(resource.locationId, well.well); return; }
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
        const target = live ? focusTarget || selection.station : selection.station;
        const bounds = resources.find(r => r.id === target)?.bounds ?? deckRegions.find(r => r.id === target)?.bounds;
        if (bounds) setView({ x: bounds.x - 55, y: bounds.y - 65, width: bounds.width + 110, height: bounds.height + 110 });
        else if (target === deckPark.id) setView({ x: deckPark.point[0] - 120, y: deckPark.point[1] - 100, width: 240, height: 200 });
    };
    const zoom = (factor: number) => setView(current => {
        const width = Math.min(deckSize.width * 2, Math.max(90, current.width * factor));
        const height = current.height * width / current.width;
        return { x: current.x + (current.width - width) / 2, y: current.y + (current.height - height) / 2, width, height };
    });
    const pan = (x: number, y: number) => setView(current => ({ ...current, x: current.x + current.width * x, y: current.y + current.height * y }));
    const keyboardWell = (event: React.KeyboardEvent<SVGGElement>, resource: DeckResource, well: DeckWell) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); if (!live || !event.repeat) chooseWell(resource, well, event); return; }
        const delta = ({ ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] } as Record<string, number[]>)[event.key];
        if (!delta) return;
        event.preventDefault();
        const next = resource.points.find(p => p.row === well.row + delta[0] && p.column === well.column + delta[1]);
        if (!next) return;
        setCursor(current => ({ ...current, [resource.id]: next.well }));
        svg.current?.querySelector<SVGGElement>(`[data-station="${resource.id}"][data-well="${next.well}"]`)?.focus();
        if (event.shiftKey && !live) chooseWell(resource, next, event);
    };
    const keyboardStation = (event: React.KeyboardEvent<SVGGElement>, stationId: string) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); if (!live || !event.repeat) chooseStation(stationId); }
    };
    // Capture only background pans: capturing a target retargets trusted clicks to SVG.
    const drag = useRef<{ x: number; y: number; view: DeckBounds; pan: boolean; moved: boolean } | null>(null);
    const suppressClick = useRef(false);
    const pointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
        if (!live || event.button !== 0) return;
        suppressClick.current = false;
        const pan = !(event.target as Element).closest('[role="button"]');
        drag.current = { x: event.clientX, y: event.clientY, view, pan, moved: false };
        if (pan) event.currentTarget.setPointerCapture?.(event.pointerId);
    };
    const pointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
        const start = drag.current;
        if (!start || !live) return;
        const dx = event.clientX - start.x, dy = event.clientY - start.y;
        if (Math.hypot(dx, dy) > 5) start.moved = suppressClick.current = true;
        const rect = event.currentTarget.getBoundingClientRect();
        const scale = Math.min(rect.width / start.view.width, rect.height / start.view.height);
        if (start.pan && start.moved && scale > 0) setView({ ...start.view, x: start.view.x - dx / scale, y: start.view.y - dy / scale });
    };
    const activate = (event: React.MouseEvent, fn: () => void) => {
        event.stopPropagation();
        if (live && (event.detail > 1 || suppressClick.current || event.button !== 0)) return;
        fn();
    };
    const moveButton = (target: string, label: string, x: number, y: number) => live && !readOnly &&
        <g key={target} className="bwd-move" role="button" tabIndex={0} data-move-to={target}
            aria-label={`Move to ${label}`} aria-disabled={!!live.stationDisabledReason(target)}
            onClick={event => activate(event, () => chooseStation(target))} onKeyDown={event => keyboardStation(event, target)}>
            <title>{live.stationDisabledReason(target) ?? `Move to ${label}`}</title>
            <rect x={x} y={y} width="72" height="30" rx="6" />
            <text x={x + 36} y={y + 21} textAnchor="middle">Move</text>
        </g>;
    const chosenResource = live?.selectedStation ? deckResources.find(resource => resource.id === live.selectedStation) : undefined;
    const chosenRegion = live?.selectedStation ? deckRegions.find(region => region.id === live.selectedStation) : undefined;
    const chosenBounds = chosenResource ? {
        x: chosenResource.bounds.x - (chosenResource.kind === 'strip' ? 14 : 26),
        y: chosenResource.bounds.y - (chosenResource.kind === 'strip' ? 21 : 49),
        width: chosenResource.bounds.width + (chosenResource.kind === 'strip' ? 28 : 52),
        height: chosenResource.bounds.height + (chosenResource.kind === 'strip' ? 42 : 76),
    } : chosenRegion ? { x: chosenRegion.bounds.x - 5, y: chosenRegion.bounds.y - 5,
        width: chosenRegion.bounds.width + 10, height: chosenRegion.bounds.height + 10 } : null;
    const strips = deckResources.filter(r => r.kind === 'strip');
    const Inspector = compact ? 'details' : 'div';
    const Help = compact ? 'details' : 'div';
    const stripBounds = deckBounds(strips.flatMap(r => r.points));
    return <section className="bioxp-workflow-deck" aria-label={live ? "BioXP live deck map" : "BioXP planned deck selection"}>
        {!live && !compact && <div className="bwd-heading"><div><h3>{readOnly ? 'Native action location' : 'Deck selection'}</h3><p>{readOnly ? 'Highlighted from the compiled action. Use the action scrubber to follow the sequence.' : 'Choose a station, then the wells for your plan.'}</p></div><span className="bwd-badge">2D deck</span></div>}
        <div className="bwd-station-control">{live ? <><label htmlFor={`${id}-view-station`}>Inspect</label><select id={`${id}-view-station`} aria-label="Deck view station" value={focusTarget || selection.station} onChange={e => setFocusTarget(e.target.value)}><option value="">Choose a station</option>{deckStations.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</select></> : readOnly ? <strong>{station?.label ?? 'No new XY destination'}</strong> : <><label htmlFor={`${id}-station`}>Station</label>
            <select id={`${id}-station`} aria-label="Deck station" value={station?.id ?? ''} onChange={e => chooseStation(e.target.value)}>
                <option value="">Choose a station</option>
                <optgroup label="Processing">{deckStations.filter(s => s.id.startsWith('LOC_') && s.wells.length === 96).map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>
                <optgroup label="Tips and reagent strips">{deckStations.filter(s => s.tipTray !== null || s.wells.length === 8).map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>
                <optgroup label="Utility and cover regions">{deckStations.filter(s => !s.wells.length).map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>
            </select></>}
            <button type="button" onClick={focusStation} disabled={live ? !(focusTarget || station) : !station}>Focus station</button>
        </div>
        {!readOnly && !live && <div className="bwd-toolbar" role="group" aria-label="Well selection mode"><span>Select</span>{modes.map(m => <button type="button" key={m} aria-pressed={mode === m} onClick={() => { setMode(m); anchor.current = null; }}>{m[0].toUpperCase() + m.slice(1)}</button>)}
            <button type="button" className="bwd-clear" onClick={() => { anchor.current = null; onChange({ station: selection.station, wells: [] }); }}>Clear selection</button>
        </div>}
        <div className="bwd-map">
            <svg ref={svg} viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`} role="group" aria-label="BioXP deck map" aria-describedby={`${id}-help`}
                onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={() => { drag.current = null; }}
                onPointerCancel={() => { drag.current = null; suppressClick.current = true; }}
                onWheel={live ? e => { e.preventDefault(); zoom(e.deltaY > 0 ? 1.12 : 1 / 1.12); } : undefined}>
                <defs><pattern id={`${id}-dots`} width="28" height="28" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="0.8" className="bwd-dot" /></pattern></defs>
                <rect width={deckSize.width} height={deckSize.height} fill={`url(#${id}-dots)`} />
                <rect className="bwd-strip-holder" x={stripBounds.x - 18} y={stripBounds.y - 25} width={stripBounds.width + 36} height={stripBounds.height + 50} rx="8" />
                <text className="bwd-map-label" x={stripBounds.x - 19} y={stripBounds.y - 39}>Reagent strips</text>
                {resources.map(resource => {
                    // Keep approved schematic housings separate from calibrated well anchors.
                    const { x, y, width, height } = (deckResources.find(r => r.id === resource.id) ?? resource).bounds;
                    const strip = resource.kind === 'strip';
                    const tabWell = cursor[resource.id] ?? (selection.station === resource.id && resource.wells.includes(selection.wells[0]) ? selection.wells[0] : resource.wells[0]);
                    return <g key={resource.id} className={`bwd-resource bwd-${resource.kind}${selection.station === resource.id ? ' is-active' : ''}`} data-resource={resource.id}>
                        <g role={readOnly ? 'img' : 'button'} tabIndex={readOnly ? undefined : 0} aria-label={`Select ${resource.label}`} aria-pressed={selection.station === resource.id}
                            aria-disabled={live ? !!live.stationDisabledReason(resource.id) : undefined}
                            onClick={e => activate(e, () => chooseStation(resource.id))} onKeyDown={e => keyboardStation(e, resource.id)} onDoubleClick={live ? undefined : () => setView({ x: x - 55, y: y - 65, width: width + 110, height: height + 110 })}>
                            <title>{live?.stationDisabledReason(resource.id) ?? resource.label}</title>
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
                            aria-disabled={live ? !!live.wellDisabledReason : undefined}
                            onClick={e => activate(e, () => chooseWell(resource, well, e))} onKeyDown={e => keyboardWell(e, resource, well)}
                            onFocus={() => setHover(`${resource.label} · ${well.well}`)} onMouseEnter={() => setHover(`${resource.label} · ${well.well}`)}>
                            <title>{resource.label} · {well.well}{live?.wellDisabledReason ? ` · ${live.wellDisabledReason}` : ''}</title>
                            <circle className="bwd-ring" cx={well.x} cy={well.y} r="5.4" />
                            <circle className="bwd-core" cx={well.x} cy={well.y} r="3.8" />
                            {resource.kind === 'rack' && <circle className="bwd-tip" cx={well.x} cy={well.y} r="1.35" />}
                            <circle className="bwd-hit" cx={well.x} cy={well.y} r="8.5" />
                        </g>)}
                    </g>;
                })}
                {deckRegions.map(region => <g key={region.id} className={`bwd-region${selection.station === region.id ? ' is-active' : ''}`} role={readOnly ? 'img' : 'button'} tabIndex={readOnly ? undefined : 0}
                    aria-label={`Select ${region.label}`} aria-pressed={selection.station === region.id} aria-disabled={live ? !!live.stationDisabledReason(region.id) : undefined} onClick={e => activate(e, () => chooseStation(region.id))} onKeyDown={e => keyboardStation(e, region.id)}>
                    <title>{region.label} · {live?.stationDisabledReason(region.id) ?? 'illustration-derived outline estimate'}</title>
                    {region.layers.map(layer => <polygon key={layer.name} className={`bwd-region-${layer.name}`} points={layer.points.map(p => `${p.x},${p.y}`).join(' ')} />)}
                    <text className="bwd-map-label" x={region.bounds.x} y={region.bounds.y - 15}>{region.label}</text>
                </g>)}
                <g className={`bwd-park${selection.station === deckPark.id ? ' is-active' : ''}`} role={readOnly ? 'img' : 'button'} tabIndex={readOnly ? undefined : 0} aria-label="Select Park target" aria-pressed={selection.station === deckPark.id} aria-disabled={live ? !!live.stationDisabledReason(deckPark.id) : undefined} onClick={e => activate(e, () => chooseStation(deckPark.id))} onKeyDown={e => keyboardStation(e, deckPark.id)}>
                    <title>{live?.stationDisabledReason(deckPark.id) ?? 'Park target'}</title>
                    <circle cx={deckPark.point[0]} cy={deckPark.point[1]} r="6" /><text className="bwd-map-label" x={deckPark.point[0] + 12} y={deckPark.point[1] + 4}>Park target</text>
                </g>
                {live && <g aria-label="Station move buttons">
                    {deckResources.filter(resource => resource.kind !== 'strip').map(resource => moveButton(resource.id, resource.label,
                        resource.bounds.x + resource.bounds.width - 55, resource.bounds.y - 41))}
                    {deckRegions.map(region => moveButton(region.id, region.label,
                        region.bounds.x + region.bounds.width - 84, region.bounds.y + 12))}
                    {moveButton(deckPark.id, deckPark.label, deckPark.point[0] - 86, deckPark.point[1] - 15)}
                </g>}
                {live?.selectedStation && (chosenBounds || live.selectedStation === deckPark.id) &&
                    <g className="bwd-chosen-outline" data-selected-station={live.selectedStation} aria-label="Selected dropdown destination">
                        {chosenBounds ? <rect {...chosenBounds} rx="10" /> : <circle cx={deckPark.point[0]} cy={deckPark.point[1]} r="13" />}
                    </g>}
                {overlay}
            </svg>
        </div>
        <div className="bwd-navigation" role="group" aria-label="Deck view controls">
            <button type="button" aria-label="Zoom out" onClick={() => zoom(1.3)}>−</button><span>{Math.round(deckSize.width / view.width * 100)}%</span>
            <button type="button" aria-label="Zoom in" onClick={() => zoom(1 / 1.3)}>+</button><button type="button" onClick={() => setView(fullView)}>Fit deck</button>
            <button type="button" aria-label="Pan left" onClick={() => pan(-0.2, 0)}>←</button><button type="button" aria-label="Pan up" onClick={() => pan(0, -0.2)}>↑</button>
            <button type="button" aria-label="Pan down" onClick={() => pan(0, 0.2)}>↓</button><button type="button" aria-label="Pan right" onClick={() => pan(0.2, 0)}>→</button>
        </div>
        {live ? <p className="bwd-live-context" aria-live="polite">{hover || 'Focus a station to inspect its wells.'}</p> : <Inspector className="bwd-inspector">
            {compact && <summary>{station?.label ?? 'Station wells'} · {selection.wells.length} selected</summary>}
            <div className="bwd-selection-summary" aria-live="polite"><strong>{station?.label ?? 'No station selected'}</strong><span>{live ? `Requested${selection.wells.length ? ` · ${selection.wells.join(', ')}` : ' station'}` : `${selection.wells.length} wells selected`}</span></div>
            <p className="bwd-hover">{hover || 'Focus a station for a closer view of its wells.'}</p>
            {active && !readOnly && !live && <div className="bwd-address-grid" style={{ gridTemplateColumns: `28px repeat(${active.kind === 'strip' ? 1 : 12}, minmax(24px, 1fr))` }} aria-label={`${active.label} well selection`}>
                <span />{active.points.filter(w => w.row === 0).map(w => <button type="button" key={w.column} aria-label={`Select column ${w.column + 1}`} title={`Select column ${w.column + 1}`} onClick={() => { anchor.current = null; onChange({ station: active.id, wells: selectedDeckWells(active, w, 'column') }); }}>{w.column + 1}</button>)}
                {Array.from({ length: 8 }, (_, row) => <React.Fragment key={row}>
                    <button type="button" aria-label={`Select row ${String.fromCharCode(65 + row)}`} onClick={() => { anchor.current = null; onChange({ station: active.id, wells: selectedDeckWells(active, active.points.find(w => w.row === row)!, 'row') }); }}>{String.fromCharCode(65 + row)}</button>
                    {active.points.filter(w => w.row === row).map(w => <button type="button" key={w.well} aria-label={`Select well ${w.well}`} aria-pressed={selected.has(w.well)} onClick={e => chooseWell(active, w, e)}>{w.well}</button>)}
                </React.Fragment>)}
            </div>}
            {selection.wells.length > 0 && <p className="bwd-chips">{selection.wells.slice(0, 12).map(w => <span key={w}>{w}</span>)}{selection.wells.length > 12 && <span>+{selection.wells.length - 12} more</span>}</p>}
            {station && !active && !live && <p>{station.locationId === null ? 'Inspect-only region; no native workflow binding.' : 'Utility / cover region. Choose any native action in the step editor.'}</p>}
        </Inspector>}
        <Help>{compact && <summary>Map help</summary>}
        <p id={`${id}-help`} className="bwd-help">{live ? 'Enter / Space moves once. Arrows explore wells. Drag the background, zoom or Focus station to inspect without moving.' : readOnly ? 'Preview only; zoom and pan change the view, not the plan or hardware.' : 'Click or press Enter / Space to select. Shift selects a rectangular range; Ctrl / ⌘ toggles wells. Arrow keys move between map wells. Range mode: choose the first and last well.'}</p>
        {!readOnly && !live && <p className="bwd-help">Selection only — no robot movement. Multiple wells do not change the fixed four-head alignment.</p>}
        {!live && <p className="bwd-caption">Native well positions · cover and utility outlines are artwork estimates, not measured dimensions.</p>}
        </Help>
    </section>;
}
