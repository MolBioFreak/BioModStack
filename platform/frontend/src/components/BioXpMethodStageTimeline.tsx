import { deckResources } from '../lib/bioxpWorkflowDeck';
import type { DeckMarker, StageTimeline, TimelineBlock } from '../lib/bioxpMethodTimeline';

/** Display-only stage timeline above the deck. Lanes share one column grid so steps line up. */
const need = (b: TimelineBlock) => Math.max(b.title.length * 7.4, (b.detail?.length ?? 0) * 6.6) + 16;

export function BioXpMethodStageTimeline({ timeline }: { timeline: StageTimeline }) {
    const { weights, lanes, trace } = timeline;
    const min = weights.map(() => 18);
    for (const lane of lanes) for (const b of lane.blocks) {
        const per = need(b) / Math.max(1, b.to - b.from);
        for (let i = b.from; i < b.to; i++) min[i] = Math.max(min[i], per);
    }
    const columns = `86px ${weights.map((w, i) => `minmax(${Math.ceil(min[i])}px, ${w}fr)`).join(' ')}`;
    const total = weights.reduce((t, w) => t + w, 0) || 1;
    const at = (slot: number) => weights.slice(0, slot).reduce((t, w) => t + w, 0) / total * 1000;
    let traceView: React.ReactNode = null;
    if (trace?.length) {
        const levels = [...new Set(trace.filter(s => !s.ramp).map(s => s.c))].sort((a, b) => a - b);
        const y = (c: number) => levels.length < 2 ? 40 : 66 - (c - levels[0]) / (levels[levels.length - 1] - levels[0]) * 46;
        const points = ['0,84', ...trace.map(s => `${at(s.to).toFixed(1)},${y(s.c).toFixed(1)}`)].join(' ');
        traceView = <div className="bioxp-tl-trace" style={{ gridColumn: '2 / -1' }}>
            <svg width="100%" height="96" viewBox="0 0 1000 96" preserveAspectRatio="none" aria-hidden="true">
                <line x1="0" y1="88" x2="1000" y2="88" stroke="#475569" />
                {levels.map(c => <line key={c} x1="0" y1={y(c)} x2="1000" y2={y(c)} stroke="#334155" strokeDasharray="4 4" />)}
                <polyline fill="none" stroke="#fbbf24" strokeWidth="4" points={points} vectorEffect="non-scaling-stroke" />
            </svg>
            {trace.filter(s => !s.ramp).map((s, i) => {
                const hold = lanes[0]?.blocks.find(b => b.from === s.from);
                return <span key={i} className={s.c >= 60 ? 'is-hot' : 'is-heat'} style={{ left: `${(at(s.from) + at(s.to)) / 20}%`, top: `${y(s.c) - 22}px` }}>
                    {s.c} °C · {hold?.detail?.split(',')[0] ?? ''}</span>;
            })}
        </div>;
    }
    return <section className="bioxp-stage-timeline" aria-label="Stage timeline">
        <h4><span>{timeline.title}</span><span>{timeline.total}</span></h4>
        <div className="bioxp-tl-grid" style={{ gridTemplateColumns: columns }}>
            {traceView}
            {lanes.map((lane, row) => [
                <div key={`${lane.id}-label`} className="bioxp-tl-lane" style={{ gridRow: row + (traceView ? 2 : 1), gridColumn: 1 }}>{lane.label}</div>,
                ...lane.blocks.map((b, i) => <div key={`${lane.id}-${i}`} className={`bioxp-tl-block is-${b.tone}`} data-lane={lane.id}
                    style={{ gridRow: row + (traceView ? 2 : 1), gridColumn: `${b.from + 2} / ${b.to + 2}` }} title={b.detail ? `${b.title} · ${b.detail}` : b.title}>
                    {b.title}{b.detail && <small>{b.detail}</small>}</div>),
            ])}
        </div>
    </section>;
}

const COLOR = { heat: '#f59e0b', mag: '#60a5fa', cover: '#f472b6', wait: '#94a3b8', cold: '#60a5fa' } as const;

function Chip({ x, y, lines, color }: { x: number; y: number; lines: string[]; color: string }) {
    const w = Math.max(...lines.map(l => l.length)) * 7.9 + 26, h = lines.length > 1 ? 47 : 29;
    return <g className="bioxp-stage-chip">
        <rect x={x} y={y} width={w} height={h} rx={h / 2} stroke={color} />
        <text x={x + 13} y={y + 19}>{lines[0]}</text>
        {lines[1] && <text className="is-detail" x={x + 13} y={y + 38}>{lines[1]}</text>}
    </g>;
}

/** Station glow, cover hatch and time/temperature chips for the stage's timed steps. */
export function BioXpMethodStageMarkers({ markers, reactions }: { markers: DeckMarker[]; reactions: Record<string, number> }) {
    return <g className="bioxp-stage-markers" aria-label="Timed steps on the deck" pointerEvents="none">
        <defs><pattern id="bioxp-cover-hatch" width="16" height="16" patternUnits="userSpaceOnUse" patternTransform="rotate(135)">
            <rect width="8" height="16" fill="rgba(244,114,182,.34)" /></pattern></defs>
        {markers.map((m, i) => {
            const r = deckResources.find(d => d.id === m.station);
            if (!r) return null;
            const b = { x: r.bounds.x - 13, y: r.bounds.y - 13, w: r.bounds.width + 26, h: r.bounds.height + 26 };
            const color = COLOR[m.tone];
            const lines = m.detail ? [m.title, m.detail] : [m.title];
            const count = reactions[m.station];
            if (m.station === 'LOC_TC' && m.tone === 'heat') return <g key={i} data-marker={m.station}>
                <rect className="bioxp-stage-glow" x={b.x} y={b.y} width={b.w} height={b.h} rx="11" stroke={color} style={{ filter: `drop-shadow(0 0 9px ${color})` }} />
                <Chip x={b.x - 4} y={b.y + b.h + 49} lines={[`${m.title}${count ? ` · ${count} reactions` : ''}`]} color={color} /></g>;
            if (m.tone === 'wait' && m.wells?.length) {
                const pts = r.points.filter(p => m.wells!.includes(p.well));
                const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
                const top = Math.min(...ys) - 11.6, bottom = Math.max(...ys) + 11.6;
                return <g key={i} data-marker={m.station}>
                    <rect x={Math.min(...xs) - 11.6} y={top} width={Math.max(...xs) - Math.min(...xs) + 23.2} height={bottom - top} rx="7"
                        fill="rgba(148,163,184,.18)" stroke={color} strokeWidth="3.3" />
                    <Chip x={b.x + b.w + 11} y={(top + bottom) / 2 - 15} lines={lines} color={color} /></g>;
            }
            if (m.tone === 'cold') return <g key={i} data-marker={m.station}><Chip x={b.x + b.w + 11} y={b.y + 4} lines={lines} color={color} /></g>;
            return <g key={i} data-marker={m.station}>
                <rect className="bioxp-stage-glow" x={b.x} y={b.y} width={b.w} height={b.h} rx="11" stroke={color}
                    fill={m.tone === 'cover' ? 'url(#bioxp-cover-hatch)' : 'none'} strokeDasharray={m.tone === 'cover' ? '10 7' : undefined}
                    style={m.tone === 'cover' ? undefined : { filter: `drop-shadow(0 0 8px ${color})` }} />
                <Chip x={b.x + b.w + 11} y={b.y + b.h / 2 - 24} lines={lines} color={color} /></g>;
        })}
    </g>;
}
