import { useEffect, useId, useRef } from 'react';
import { deckResources, deckRegions } from '../lib/bioxpWorkflowDeck';
import type { WorkflowDeckPlan } from '../lib/bioxpWorkflowPlan';
import type { MethodCarryLink } from '../lib/bioxpMethodCanvas';

const bounds = (station: string | undefined) => deckResources.find(r => r.id === station)?.bounds ?? deckRegions.find(r => r.id === station)?.bounds;

/** Gesture-only projection of the existing saved plan/actions. Never invokes transport. */
export function BioXpMethodPlateLayer({ plan, stations, selectedPlate, selectedPath, links, onPrepare, onCarry, onSelect, holderLeft }: {
    holderLeft?: number;
    plan: WorkflowDeckPlan; stations: Record<string, string>; selectedPlate: string; selectedPath: string;
    links: MethodCarryLink[]; onPrepare: (id: string, station: string) => void;
    onCarry: (id: string, station: string) => void; onSelect: (path: string) => void;
}) {
    const marker = useId();
    const drag = useRef<{ id: string; x: number; y: number; moved: boolean; svg: SVGSVGElement | null } | null>(null);
    const suppressClick = useRef(false);
    useEffect(() => {
        const move = (event: PointerEvent) => { const start = drag.current; if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5) start.moved = suppressClick.current = true; };
        const finish = (event: PointerEvent) => {
            const start = drag.current; drag.current = null;
            if (!start?.moved) return;
            const target = document.elementFromPoint(event.clientX, event.clientY);
            const destination = target && start.svg?.contains(target) ? target.closest('[data-station]')?.getAttribute('data-station') : null;
            if (destination) onCarry(start.id, destination);
        };
        const cancel = () => { if (drag.current) suppressClick.current = true; drag.current = null; };
        // Keep a gesture working across SVG children and on release outside its handle.
        // Capture is an optimization, not the only completion path (including touch).
        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', finish);
        document.addEventListener('pointercancel', cancel);
        window.addEventListener('blur', cancel);
        return () => { document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', finish); document.removeEventListener('pointercancel', cancel); window.removeEventListener('blur', cancel); };
    }, [onCarry]);
    return <g aria-label="Named plates and carry connections">
        <defs><marker id={marker} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L7,3.5 L0,7 Z" fill="var(--text-secondary)" /></marker></defs>
        {links.map(({ path, node, source, destination, labwareId }, index) => {
            const from = bounds(source), to = bounds(destination);
            if (!from || !to) return null;
            // Plate centre to plate centre, stopping inside the outline so titles stay readable.
            const cx = from.x + from.width / 2, cy = from.y + from.height / 2, tcx = to.x + to.width / 2, tcy = to.y + to.height / 2;
            const dx = tcx - cx, dy = tcy - cy, length = Math.hypot(dx, dy) || 1, bow = 70 + index % 3 * 25;
            const x = cx + dx / length * 30, y = cy + dy / length * 30, tx = tcx - dx / length * 30, ty = tcy - dy / length * 30;
            const same = source === destination;
            const curve = same ? `M ${cx} ${from.y} C ${cx - 100} ${from.y - 100} ${cx + 100} ${from.y - 100} ${cx + 15} ${from.y}` : `M ${x} ${y} Q ${(x + tx) / 2 + dy / length * bow} ${(y + ty) / 2 - dx / length * bow} ${tx} ${ty}`;
            const name = String(node.label || plan.labware.find(l => l.id === labwareId)?.name || node.step_id);
            return <g key={path} role="button" tabIndex={0} aria-label={`Edit plate carry ${name}`} data-carry-from={source} data-carry-to={destination} className={`bioxp-method-carry${selectedPath === path ? ' is-selected' : ''}`} onClick={e => { e.stopPropagation(); onSelect(path); }} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); onSelect(path); } }}>
                <path className="bioxp-method-link-hit" d={curve} /><path className="bioxp-method-carry-line" d={curve} markerEnd={`url(#${marker})`} />
                <title>{name}: {source} → {destination}. Ordered plate carry, not liquid flow or observed custody.</title>
            </g>;
        })}
        {plan.labware.map((plate, index) => {
            const station = stations[plate.id], r = bounds(station);
            if (!r) return null;
            const atStation = plan.labware.slice(0, index).filter(p => stations[p.id] === station).length;
            // Adjacent strip holders are narrower than their labels: stack them under the holder.
            const strip = deckResources.filter(s => s.kind === 'strip').findIndex(s => s.id === station);
            // Strips: start below the holder outline and its 1-4 numbers.
            const y = r.y + r.height + (strip >= 0 ? 67 + strip * 34 : 47 + atStation * 34);
            // Strip labels fit the holder outline (same extent the deck draws: strip wells ± 18).
            const strips = deckResources.filter(s => s.kind === 'strip');
            const holderX = Math.min(...strips.map(s => s.bounds.x)) - 18;
            const holderW = Math.max(...strips.map(s => s.bounds.x + s.bounds.width)) + 18 - holderX;
            const boxX = strip >= 0 ? holderX : r.x - 10, boxW = strip >= 0 ? holderW : r.width + 20;
            const name = plate.name || 'Unnamed labware', fits = 8.6 * name.length <= boxW - 16;
            const prepare = () => onPrepare(plate.id, station);
            const region = deckRegions.find(g => g.id === station)?.bounds;
            // Wrapped lines inside an open region, left of any drawn holder rows; the space below belongs to the next station.
            const regionLines = region ? (() => {
                const width = Math.max(60, (holderLeft !== undefined && holderLeft > region.x + 40 ? holderLeft : region.x + region.width) - region.x - 14);
                const perLine = Math.max(8, Math.floor(width / 5.6)), lines: string[] = [];
                for (const word of name.split(/\s+/)) { const last = lines[lines.length - 1]; if (last !== undefined && (last + ' ' + word).length <= perLine) lines[lines.length - 1] = last + ' ' + word; else lines.push(word); }
                return { x: region.x + 4, y: region.y + 6, width, lines: lines.slice(0, Math.floor((region.height - 10) / 12)) };
            })() : null;
            return <g key={plate.id} role="button" tabIndex={0} aria-label={`Prepare ${plate.name || 'unnamed labware'}`} aria-pressed={selectedPlate === plate.id} data-labware={plate.id} data-display-station={station} className="bioxp-method-plate"
                onPointerDown={e => { if (e.button !== 0) return; e.stopPropagation(); suppressClick.current = false; drag.current = { id: plate.id, x: e.clientX, y: e.clientY, moved: false, svg: e.currentTarget.ownerSVGElement }; e.currentTarget.setPointerCapture?.(e.pointerId); }}
                onPointerCancel={e => { e.stopPropagation(); drag.current = null; suppressClick.current = true; }}
                onClick={e => { e.stopPropagation(); if (suppressClick.current) { suppressClick.current = false; return; } prepare(); }}
                onContextMenu={e => { e.preventDefault(); e.stopPropagation(); prepare(); }}
                onKeyDown={e => { if (e.key === 'Escape') { drag.current = null; suppressClick.current = true; } if (['Enter', ' ', 'ContextMenu'].includes(e.key) || e.shiftKey && e.key === 'F10') { e.preventDefault(); e.stopPropagation(); prepare(); } }}>
                {regionLines ? <>
                    <rect x={regionLines.x} y={regionLines.y} width={regionLines.width + 6} height={regionLines.lines.length * 12 + 8} rx="5" />
                    <text className="bioxp-method-plate-label is-region" x={regionLines.x + 5} y={regionLines.y + 13}>{regionLines.lines.map((line, i) => <tspan key={i} x={regionLines.x + 5} dy={i ? 12 : 0}>{line}</tspan>)}</text>
                </> : <>
                <rect x={boxX} y={y - 25} width={boxW} height="32" rx="6" />
                <text className="bioxp-method-plate-label" x={boxX + boxW / 2} y={y - 4} textAnchor="middle" textLength={strip >= 0 ? Math.min(boxW - 12, 10 * name.length) : fits ? undefined : boxW - 16} lengthAdjust="spacingAndGlyphs">{name}</text>
                </>}
                <title>Prepare {plate.name || 'unnamed labware'}. Drag to a destination to add a carry step, or open Move plate. Authoring only.</title>
            </g>;
        })}
    </g>;
}
