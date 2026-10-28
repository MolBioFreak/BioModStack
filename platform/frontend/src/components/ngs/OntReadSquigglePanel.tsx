import React, { useMemo, useState } from 'react';
import Plot from 'react-plotly.js';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { describeNgsError, withAlignmentAccessRecovery } from '../../lib/ngsAlignmentSession';

export interface OntSquiggleReadSummary {
    read_id: string; length: number; qscore: number | null; has_moves: boolean; modified_calls: Record<string, number>;
}
export interface OntReadSquiggle {
    schema: 'bms.ngs.read-squiggle.v1';
    read_id: string; pod5_file: string; read_length: number; stride: number; trimmed_samples: number;
    start_base: number; end_base: number; sample_offset: number; signal_pa: number[];
    bases: Array<{ index: number; base: string; sample_start: number; sample_end: number; modifications: Record<string, number> }>;
}

const WINDOW = 120;
const BASE_COLOURS: Record<string, string> = { A: '#22c55e', C: '#3b82f6', G: '#f59e0b', T: '#ef4444' };

function get<T>(jobId: string, path: string, params?: Record<string, unknown>): Promise<T> {
    return withAlignmentAccessRecovery(jobId, async () => (await api.get<T>(`/api/jobs/${encodeURIComponent(jobId)}/${path}`, { params })).data);
}

/** Signal behind each called base, read straight from the run's POD5 and move table. */
export function OntReadSquigglePanel({ jobId }: { jobId: string }) {
    const [readId, setReadId] = useState('');
    const [startBase, setStartBase] = useState(0);
    const reads = useQuery({
        queryKey: ['ngs-squiggle-reads', jobId],
        queryFn: () => get<{ reads: OntSquiggleReadSummary[]; total_primary_reads: number }>(jobId, 'ngs-squiggle/reads', { limit: 100 }),
        retry: false, staleTime: 60_000,
    });
    const activeRead = readId || reads.data?.reads[0]?.read_id || '';
    const squiggle = useQuery({
        queryKey: ['ngs-squiggle', jobId, activeRead, startBase],
        queryFn: () => get<OntReadSquiggle>(jobId, `ngs-squiggle/reads/${encodeURIComponent(activeRead)}`, { start_base: startBase, bases: WINDOW }),
        enabled: Boolean(activeRead), retry: false, staleTime: 300_000,
    });
    const selected = reads.data?.reads.find(read => read.read_id === activeRead);
    const figure = useMemo(() => {
        const data = squiggle.data;
        if (!data) return null;
        const x = data.signal_pa.map((_value, index) => index);
        const shapes: Array<Record<string, unknown>> = [];
        const annotations: Array<Record<string, unknown>> = [];
        for (const base of data.bases) {
            const probability = Math.max(0, ...Object.values(base.modifications));
            if (probability >= 0.5) {
                shapes.push({ type: 'rect', xref: 'x', yref: 'paper', x0: base.sample_start, x1: base.sample_end, y0: 0, y1: 1,
                    fillcolor: 'rgba(236,72,153,0.25)', line: { width: 0 }, layer: 'below' });
            }
            shapes.push({ type: 'line', xref: 'x', yref: 'paper', x0: base.sample_start, x1: base.sample_start, y0: 0, y1: 0.04,
                line: { color: 'rgba(148,163,184,0.5)', width: 1 } });
            const mods = Object.entries(base.modifications).map(([code, p]) => `${code} ${(p * 100).toFixed(0)}%`).join('<br>');
            annotations.push({ x: (base.sample_start + base.sample_end) / 2, y: 1.02, xref: 'x', yref: 'paper', text: base.base, showarrow: false,
                font: { size: 10, color: BASE_COLOURS[base.base] ?? '#e2e8f0', family: 'monospace' },
                hovertext: `base ${base.index + 1} ${base.base}${mods ? `<br>${mods}` : ''}` });
        }
        return { x, data, shapes, annotations };
    }, [squiggle.data]);

    if (reads.isLoading) return <p className="text-sm text-[var(--text-secondary)]">Loading reads with signal…</p>;
    if (reads.isError) return <p role="alert" className="text-sm text-[var(--warning)]">Squiggles unavailable: {describeNgsError(reads.error, 'this run has no POD5 signal with move tables.')}</p>;
    const list = reads.data!.reads;
    const length = squiggle.data?.read_length ?? selected?.length ?? 0;
    return (
        <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3" aria-label="Squiggle viewer">
            <div className="flex flex-wrap items-end gap-2">
                <div>
                    <h4 className="text-sm font-semibold text-[var(--text-primary)]">Squiggle</h4>
                    <p className="text-xs text-[var(--text-secondary)]">Raw current (pA) from this run's POD5, split at each called base by Dorado's move table. Pink = modification probability ≥ 50%.</p>
                </div>
                <label className="ml-auto text-xs text-[var(--text-secondary)]">Read
                    <select aria-label="Squiggle read" value={activeRead} onChange={event => { setReadId(event.target.value); setStartBase(0); }}
                        className="ml-1 max-w-[28rem] rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 font-mono text-xs">
                        {list.map(read => (
                            <option key={read.read_id} value={read.read_id} disabled={!read.has_moves}>
                                {read.read_id.slice(0, 8)} · {read.length.toLocaleString()} bp{read.qscore != null ? ` · Q${read.qscore.toFixed(1)}` : ''}
                                {Object.entries(read.modified_calls).map(([code, count]) => ` · ${code} ${count}`).join('')}
                            </option>
                        ))}
                    </select>
                </label>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                <button type="button" disabled={startBase === 0} onClick={() => setStartBase(Math.max(0, startBase - WINDOW))} className="rounded border border-[var(--border-primary)] px-2 py-1 disabled:opacity-40">◀ Previous {WINDOW} bases</button>
                <label className="text-[var(--text-secondary)]">Base
                    <input aria-label="Squiggle start base" type="number" min={1} max={Math.max(1, length)} value={startBase + 1}
                        onChange={event => setStartBase(Math.max(0, Math.min(Math.max(0, length - 1), Number(event.target.value) - 1 || 0)))}
                        className="ml-1 w-24 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 font-mono" />
                </label>
                <button type="button" disabled={!length || startBase + WINDOW >= length} onClick={() => setStartBase(startBase + WINDOW)} className="rounded border border-[var(--border-primary)] px-2 py-1 disabled:opacity-40">Next {WINDOW} bases ▶</button>
                {squiggle.data && <span className="text-[var(--text-secondary)]">bases {squiggle.data.start_base + 1}–{squiggle.data.end_base} of {length.toLocaleString()} · {squiggle.data.signal_pa.length.toLocaleString()} samples · {squiggle.data.pod5_file}</span>}
            </div>
            {squiggle.isError && <p role="alert" className="mt-2 text-sm text-[var(--warning)]">{describeNgsError(squiggle.error, 'This read could not be drawn.')}</p>}
            {squiggle.isLoading && <p className="mt-2 text-sm text-[var(--text-secondary)]">Reading signal…</p>}
            {figure && (
                <>
                    <Plot
                        data={[{ type: 'scattergl', mode: 'lines', x: figure.x, y: figure.data.signal_pa, line: { color: '#93c5fd', width: 1 }, hoverinfo: 'y', name: 'pA' }]}
                        layout={{
                            height: 320, margin: { l: 56, r: 16, t: 34, b: 40 }, showlegend: false,
                            xaxis: { title: { text: 'Samples' }, zeroline: false }, yaxis: { title: { text: 'Current (pA)' } },
                            shapes: figure.shapes, annotations: figure.annotations,
                            paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color: '#b8c4d8' },
                        }}
                        config={{ responsive: true, displaylogo: false }}
                        useResizeHandler
                        style={{ width: '100%', height: '320px' }}
                    />
                    <div className="mt-1 break-all font-mono text-[11px] leading-5" aria-label="Called bases">
                        {figure.data.bases.map(base => {
                            const probability = Math.max(0, ...Object.values(base.modifications));
                            return (
                                <span key={base.index} data-base-index={base.index}
                                    title={`base ${base.index + 1}${Object.entries(base.modifications).map(([code, p]) => ` · ${code} ${(p * 100).toFixed(0)}%`).join('')}`}
                                    style={{ color: BASE_COLOURS[base.base], background: probability >= 0.5 ? 'rgba(236,72,153,0.35)' : undefined }}>
                                    {base.base}
                                </span>
                            );
                        })}
                    </div>
                </>
            )}
        </section>
    );
}
