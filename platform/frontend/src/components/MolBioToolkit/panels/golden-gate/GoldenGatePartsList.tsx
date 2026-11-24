import type { GoldenGatePartReport } from '../../../../lib/api';

export type PartRow = {
    key: string; name: string; sequence: string; circular: boolean; revision_id?: string;
    orientation: 'forward' | 'reverse'; kind: 'auto' | 'bare' | 'prepared';
    left: string; right: string; retained: number | null;
};

const kindLabel = (r: GoldenGatePartReport) => r.kind === 'bare' ? 'Bare DNA'
    : r.kind === 'prepared' ? `Pre-configured · ${r.left_overhang ?? '—'} … ${r.right_overhang ?? '—'}${r.payload_length != null ? ` · ${r.payload_length} bp payload` : ''}`
        : r.kind === 'ambiguous' ? 'Needs a choice' : 'Invalid';
const kindColor = (k: GoldenGatePartReport['kind']) => k === 'invalid' ? 'text-red-300' : k === 'ambiguous' ? 'text-amber-300' : 'text-emerald-300';

const field = 'rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200';

export function GoldenGatePartsList({ rows, reports, onChange, onRemove, onMove, onPickFragment }: {
    rows: PartRow[]; reports: GoldenGatePartReport[];
    onChange: (key: string, patch: Partial<PartRow>) => void; onRemove: (key: string) => void; onMove: (key: string, delta: -1 | 1) => void;
    onPickFragment: (key: string, fragmentIndex: number) => void;
}) {
    if (rows.length === 0) return <p className="text-slate-500">No parts yet. Add parts from saved molecules, a pasted sequence or the open sequence.</p>;
    return <ol aria-label="Parts list" className="space-y-2">{rows.map((r, i) => {
        const mine = reports.filter(x => x.index === i);
        return <li key={r.key} className="space-y-1 rounded border border-slate-700 bg-slate-950/60 p-2">
            <div className="flex flex-wrap items-center gap-2">
                <span className="text-slate-500">{i + 1}.</span>
                <input aria-label={`Part ${i + 1} name`} className={`${field} w-40`} value={r.name} onChange={e => onChange(r.key, { name: e.target.value })} />
                <span className="text-slate-300">{r.sequence.length} bp</span>
                <label className="text-slate-400">Orientation <select aria-label={`Part ${i + 1} orientation`} className={field} value={r.orientation} onChange={e => onChange(r.key, { orientation: e.target.value as PartRow['orientation'] })}><option value="forward">Forward</option><option value="reverse">Reverse</option></select></label>
                <label className="text-slate-400">Kind <select aria-label={`Part ${i + 1} kind`} className={field} value={r.kind} onChange={e => onChange(r.key, { kind: e.target.value as PartRow['kind'] })}><option value="auto">Auto</option><option value="bare">Bare DNA</option><option value="prepared">Pre-configured</option></select></label>
                <button type="button" aria-label={`Move part ${i + 1} up`} disabled={i === 0} className="px-1 text-slate-300 disabled:opacity-30" onClick={() => onMove(r.key, -1)}>↑</button>
                <button type="button" aria-label={`Move part ${i + 1} down`} disabled={i === rows.length - 1} className="px-1 text-slate-300 disabled:opacity-30" onClick={() => onMove(r.key, 1)}>↓</button>
                <button type="button" aria-label={`Remove part ${i + 1}`} className="text-slate-400" onClick={() => onRemove(r.key)}>Remove</button>
            </div>
            {r.kind !== 'prepared' && <div className="flex flex-wrap gap-2 text-slate-400">
                <label>Left overhang (optional) <input aria-label={`Part ${i + 1} left overhang`} className={`${field} w-20 font-mono`} value={r.left} onChange={e => onChange(r.key, { left: e.target.value.toUpperCase() })} /></label>
                <label>Right overhang (optional) <input aria-label={`Part ${i + 1} right overhang`} className={`${field} w-20 font-mono`} value={r.right} onChange={e => onChange(r.key, { right: e.target.value.toUpperCase() })} /></label>
            </div>}
            {mine.map((x, j) => <div key={j} className="space-y-1">
                <div className="text-slate-300"><span className="text-slate-500">{x.enzyme_name}:</span> <span className={`font-medium ${kindColor(x.kind)}`}>{kindLabel(x)}</span> · {x.site_count} site(s){x.site_positions.length ? ` at ${x.site_positions.join(', ')}` : ''}</div>
                <div className="text-slate-400">{x.message}</div>
                {x.kind === 'ambiguous' && x.candidates.length > 0 && <div aria-label={`Part ${i + 1} fragment choice`} className="flex flex-wrap items-center gap-2 text-slate-300">Use fragment:
                    {x.candidates.map(c => <button key={c.fragment_index} type="button" className="rounded border border-slate-600 px-2 py-0.5 text-cyan-300" onClick={() => onPickFragment(r.key, c.fragment_index)}>#{c.fragment_index} · {c.length} bp · {c.left_overhang} … {c.right_overhang}</button>)}
                </div>}
            </div>)}
        </li>;
    })}</ol>;
}
