import { placeBounds, biosecurityCoverStorage, COVER_LABELS, placeName, type CustodyStep } from '../lib/bioxpMethodCustodySteps';
import type { DeckBounds } from '../lib/bioxpWorkflowDeck';

const circled = (n: number) => n >= 1 && n <= 20 ? String.fromCharCode(0x2460 + n - 1) : `(${n})`;
const centre = (b: DeckBounds) => ({ x: b.x + b.width / 2, y: b.y + b.height / 2 });

/** Edge-to-edge route between two station outlines, bowed so out-and-back moves stay distinct. */
export function deckRoute(from: DeckBounds, to: DeckBounds, bow: number) {
    // Centre to centre, trimmed so the head lands inside the destination outline.
    // Endpoints shift to the bowed side, so an out-and-back pair never shares a head or tail.
    const p = centre(from), q = centre(to), dx = q.x - p.x, dy = q.y - p.y, length = Math.hypot(dx, dy) || 1;
    const nx = -dy / length * Math.sign(bow) * 14, ny = dx / length * Math.sign(bow) * 14;
    const a = { x: p.x + dx / length * 24 + nx, y: p.y + dy / length * 24 + ny }, b = { x: q.x - dx / length * 24 + nx, y: q.y - dy / length * 24 + ny };
    const c = { x: (a.x + b.x) / 2 - dy / length * bow, y: (a.y + b.y) / 2 + dx / length * bow };
    const at = (t: number) => ({ x: (1 - t) ** 2 * a.x + 2 * (1 - t) * t * c.x + t * t * b.x, y: (1 - t) ** 2 * a.y + 2 * (1 - t) * t * c.y + t * t * b.y });
    return { d: `M ${a.x} ${a.y} Q ${c.x} ${c.y} ${b.x} ${b.y}`, mid: at(0.62) };
}

/** Presentation only: numbered plate/cover/door steps for the selected stage and
 * where the covers sit after it. Numbers match the stage summary list. */
export function BioXpMethodStageAnnotations({ steps, door, covers, markerId }: {
    steps: CustodyStep[]; door: 'open' | 'closed' | null; covers: Record<string, string>; markerId: string;
}) {
    const moved = new Set(steps.filter(s => s.kind !== 'door' && s.kind !== 'plate' && s.object).map(s => s.object!));
    const badges: React.ReactNode[] = [];
    const arrows = steps.map((s, index) => {
        if (s.kind === 'door') return null;
        const to = placeBounds(s.to), from = placeBounds(s.from);
        if (!to) return null;
        if (s.kind === 'plate' || !from) {
            // Plate carries keep their existing dashed arrow; number the destination.
            badges.push(<g key={`badge-${index}`} className={`bioxp-stage-badge is-${s.kind}`} data-step-number={s.n} data-badge-at={s.to}>
                <circle cx={to.x + to.width / 2} cy={to.y + to.height / 2} r="13" /><text x={to.x + to.width / 2} y={to.y + to.height / 2 + 5} textAnchor="middle">{s.n}</text>
                <title>{s.n}. {s.label ?? 'Object'} → {placeName(s.to)}</title></g>);
            return null;
        }
        // Same bow sign: a reversed return trip then curves on the opposite side.
        const route = deckRoute(from, to, 60);
        badges.push(<g key={`badge-${index}`} className={`bioxp-stage-badge is-${s.kind}`} data-step-number={s.n} data-badge-at={s.to}>
            <circle cx={route.mid.x} cy={route.mid.y} r="12" /><text x={route.mid.x} y={route.mid.y + 5} textAnchor="middle">{s.n}</text>
            <title>{s.n}. {s.label ?? 'Cover'}: {placeName(s.from)} → {placeName(s.to)}</title></g>);
        return <g key={`move-${index}`} className="bioxp-stage-cover-move" data-cover-from={s.from} data-cover-to={s.to}>
            <path d={route.d} markerEnd={`url(#${markerId}-cover)`} />
            <title>{s.n}. {s.label ?? 'Cover'}: {placeName(s.from)} → {placeName(s.to)}</title>
        </g>;
    });
    const tc = placeBounds('LOC_TC')!;
    const doorSteps = steps.filter(s => s.kind === 'door');
    const doorText = doorSteps.length ? `Door ${doorSteps.map(s => `${circled(s.n)} ${s.door ?? '?'}`).join(' → ')}` : door ? `Door ${door}` : null;
    const doorWidth = doorText ? 20 + doorText.length * 7.2 : 0;
    const bscs = biosecurityCoverStorage;
    return <g aria-label="Plate, cover and door steps" className="bioxp-stage-custody">
        <defs><marker id={`${markerId}-cover`} markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L11,5.5 L0,11 Z" className="bioxp-cover-arrowhead" /></marker></defs>
        <g className={`bioxp-bscs${steps.some(s => s.from === 'LOC_BSCS' || s.to === 'LOC_BSCS') ? ' is-involved' : ''}`} data-station="LOC_BSCS"><rect x={bscs.x - 3} y={bscs.y - 3} width={bscs.width + 6} height={bscs.height + 6} rx="9" />
            <text x={bscs.x + bscs.width / 2} y={bscs.y + bscs.height + 18} textAnchor="middle">Biosecurity cover storage</text>
            <title>Biosecurity cover storage (LOC_BSCS). Its calibrated gripper position is in the trough slot; outline is an estimate.</title></g>
        {Object.entries(covers).map(([name, location]) => {
            const b = placeBounds(location);
            if (!b) return null;
            return <g key={name} className={`bioxp-cover-state${moved.has(name) ? ' is-moved' : ''}`} data-cover={name} data-cover-at={location}>
                <rect x={b.x - 6} y={b.y - 6} width={b.width + 12} height={b.height + 12} rx="11" />
                <text x={b.x + b.width - 8} y={b.y + b.height - 7} textAnchor="end">{COVER_LABELS[name] ?? name}</text>
                <title>{COVER_LABELS[name] ?? name} is {placeName(location)} after this stage (planned).</title>
            </g>;
        })}
        {arrows}
        {badges}
        {doorText && <g className={`bioxp-door-state is-${door ?? 'unknown'}`} data-door={door ?? 'unknown'}>
            <rect x={tc.x + tc.width - doorWidth} y={tc.y - 34} width={doorWidth} height="26" rx="13" />
            <text x={tc.x + tc.width - doorWidth / 2} y={tc.y - 16} textAnchor="middle">{doorText}</text>
            <title>Thermal cycler door{door ? ` is ${door} after this stage` : ''} (planned).</title>
        </g>}
    </g>;
}
