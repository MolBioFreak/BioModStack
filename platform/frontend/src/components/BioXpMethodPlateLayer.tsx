import { useEffect, useId, useRef } from 'react';
import { deckResources, deckRegions } from '../lib/bioxpWorkflowDeck';
import type { WorkflowDeckPlan } from '../lib/bioxpWorkflowPlan';
import type { MethodCarryLink } from '../lib/bioxpMethodCanvas';

const bounds = (station: string | undefined) => deckResources.find(r => r.id === station)?.bounds ?? deckRegions.find(r => r.id === station)?.bounds;

/** Gesture-only projection of the existing saved plan/actions. Never invokes transport. */
export function BioXpMethodPlateLayer({ plan, stations, selectedPlate, selectedPath, links, onPrepare, onCarry, onSelect }: {
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
            const x = from.x + from.width / 2, y = from.y - 13, tx = to.x + to.width / 2, ty = to.y - 13;
            const same = source === destination;
            const curve = same ? `M ${x} ${y} C ${x - 100} ${y - 100} ${x + 100} ${y - 100} ${x + 15} ${y}` : `M ${x} ${y} Q ${(x + tx) / 2} ${Math.min(y, ty) - 90 - index % 3 * 30} ${tx} ${ty}`;
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
            const y = r.y + r.height + 47 + atStation * 34;
            const prepare = () => onPrepare(plate.id, station);
            return <g key={plate.id} role="button" tabIndex={0} aria-label={`Prepare ${plate.name || 'unnamed labware'}`} aria-pressed={selectedPlate === plate.id} data-labware={plate.id} className="bioxp-method-plate"
                onPointerDown={e => { if (e.button !== 0) return; e.stopPropagation(); suppressClick.current = false; drag.current = { id: plate.id, x: e.clientX, y: e.clientY, moved: false, svg: e.currentTarget.ownerSVGElement }; e.currentTarget.setPointerCapture?.(e.pointerId); }}
                onPointerCancel={e => { e.stopPropagation(); drag.current = null; suppressClick.current = true; }}
                onClick={e => { e.stopPropagation(); if (suppressClick.current) { suppressClick.current = false; return; } prepare(); }}
                onContextMenu={e => { e.preventDefault(); e.stopPropagation(); prepare(); }}
                onKeyDown={e => { if (e.key === 'Escape') { drag.current = null; suppressClick.current = true; } if (['Enter', ' ', 'ContextMenu'].includes(e.key) || e.shiftKey && e.key === 'F10') { e.preventDefault(); e.stopPropagation(); prepare(); } }}>
                <rect x={r.x - 10} y={y - 25} width={Math.max(r.width + 20, 150)} height="32" rx="6" />
                <text className="bioxp-method-plate-label" x={r.x} y={y}>{plate.name || 'Unnamed labware'}</text>
                <title>Prepare {plate.name || 'unnamed labware'}. Drag to a destination to add a carry step, or open Move plate. Authoring only.</title>
            </g>;
        })}
    </g>;
}
