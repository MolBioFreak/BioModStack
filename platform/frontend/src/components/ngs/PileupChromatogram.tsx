import { useEffect, useMemo, useState } from 'react';
import { api } from '../../lib/api';

/** One row of the per-base read-support table, as shares of the aligned reads. */
export interface PileupPosition {
    position: number;
    called: string;
    shares: Record<'A' | 'C' | 'G' | 'T', number>;
}

const BASES = ['A', 'C', 'G', 'T'] as const;
const COLOUR: Record<string, string> = { A: '#4ade80', C: '#60a5fa', G: 'var(--text-primary)', T: '#f87171' };
const WINDOW = 44;

/** Parse the per-base support TSV the QC step already writes. */
export function parsePileupTable(text: string): PileupPosition[] {
    const lines = text.split('\n').filter(line => line.trim());
    if (lines.length < 2) return [];
    const header = lines[0].split('\t');
    const column = (name: string) => header.indexOf(name);
    const index = {
        position: column('position_1based'), depth: column('depth'), called: column('consensus_base'),
        A: column('a_count'), C: column('c_count'), G: column('g_count'), T: column('t_count'),
    };
    if (Object.values(index).some(value => value < 0)) return [];
    return lines.slice(1).map((line) => {
        const cells = line.split('\t');
        const depth = Number(cells[index.depth]) || 1;
        return {
            position: Number(cells[index.position]),
            called: (cells[index.called] || 'N').toUpperCase(),
            shares: { A: Number(cells[index.A]) / depth, C: Number(cells[index.C]) / depth, G: Number(cells[index.G]) / depth, T: Number(cells[index.T]) / depth },
        };
    }).filter(row => Number.isInteger(row.position) && row.position > 0);
}

function peakPath(rows: PileupPosition[], base: typeof BASES[number], step: number, height: number): string {
    const sigma = step * 0.28;
    const points: string[] = [];
    const width = rows.length * step;
    for (let x = 0; x <= width; x += 1) {
        let y = 0;
        const centreIndex = Math.floor(x / step);
        for (let i = Math.max(0, centreIndex - 1); i <= Math.min(rows.length - 1, centreIndex + 1); i += 1) {
            const share = rows[i].shares[base];
            if (share <= 0) continue;
            const distance = x - (i * step + step / 2);
            y += share * Math.exp(-(distance * distance) / (2 * sigma * sigma));
        }
        points.push(`${x === 0 ? 'M' : 'L'}${x},${(height - Math.min(1, y) * height).toFixed(1)}`);
    }
    return points.join(' ');
}

interface PileupChromatogramProps {
    /** Download address of the per-base support table. */
    tableUrl: string;
    /** Download address of the AB1 file, when the run wrote one. */
    ab1Url?: string | null;
    initialPosition?: number;
    coordinateLabel?: string;
}

/**
 * Chromatogram strip drawn from the per-base read counts. Peak height is the
 * share of aligned reads with that base: a read pileup, not a Sanger trace.
 */
export function PileupChromatogram({ tableUrl, ab1Url, initialPosition, coordinateLabel = 'reference' }: PileupChromatogramProps) {
    const [rows, setRows] = useState<PileupPosition[] | null>(null);
    const [failed, setFailed] = useState(false);
    const [centre, setCentre] = useState(initialPosition && initialPosition > 0 ? initialPosition : 1);
    const [entry, setEntry] = useState(String(initialPosition && initialPosition > 0 ? initialPosition : 1));
    useEffect(() => {
        let current = true;
        setRows(null); setFailed(false);
        api.get(tableUrl, { responseType: 'text', transformResponse: [(data: unknown) => data] })
            .then((response) => {
                if (!current) return;
                const parsed = typeof response.data === 'string' ? parsePileupTable(response.data) : [];
                if (parsed.length === 0) setFailed(true); else setRows(parsed);
            })
            .catch(() => { if (current) setFailed(true); });
        return () => { current = false; };
    }, [tableUrl]);
    const total = rows?.length ?? 0;
    const start = Math.max(0, Math.min(Math.max(0, total - WINDOW), centre - 1 - Math.floor(WINDOW / 2)));
    const shown = useMemo(() => (rows ?? []).slice(start, start + WINDOW), [rows, start]);
    const go = (position: number) => {
        if (!total) return;
        const next = Math.max(1, Math.min(total, Math.round(position)));
        setCentre(next); setEntry(String(next));
    };
    const step = 18;
    const plotHeight = 120;
    const width = Math.max(1, shown.length) * step;
    const selected = rows?.[centre - 1];
    const selectedNote = selected ? (() => {
        const called = selected.called;
        const share = called in selected.shares ? selected.shares[called as typeof BASES[number]] : null;
        return share === null
            ? `At ${centre.toLocaleString()}, most reads lack a base here.`
            : `At ${centre.toLocaleString()}, ${(share * 100).toFixed(0)}% of reads carry ${called}.`;
    })() : '';
    return (
        <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3" aria-label="Chromatogram" data-testid="pileup-chromatogram">
            <h4 className="text-sm font-semibold text-[var(--text-primary)]">Chromatogram</h4>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">
                Peak height is the share of aligned reads with that base. A read pileup, not a Sanger trace. {selectedNote}
            </p>
            <div className="my-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                <div className="flex flex-wrap items-center gap-3 text-[var(--text-secondary)]">
                    {BASES.map(base => (
                        <span key={base} className="inline-flex items-center gap-1">
                            <span aria-hidden="true" className="inline-block h-0.5 w-4" style={{ background: COLOUR[base] }} />{base}
                        </span>
                    ))}
                    <span>– base missing from the consensus</span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <label className="flex items-center gap-1 text-[var(--text-secondary)]">Go to position
                        <input
                            aria-label="Go to position" inputMode="numeric" value={entry}
                            onChange={(event) => setEntry(event.target.value)}
                            onKeyDown={(event) => { if (event.key === 'Enter') go(Number(entry.replace(/,/g, ''))); }}
                            onBlur={() => { const value = Number(entry.replace(/,/g, '')); if (Number.isFinite(value) && value > 0) go(value); else setEntry(String(centre)); }}
                            className="w-20 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-1 text-[var(--text-primary)]"
                        />
                    </label>
                    <button type="button" aria-label="Previous positions" disabled={!total || start === 0} onClick={() => go(centre - WINDOW)}
                        className="rounded border border-[var(--border-primary)] px-2 py-1 text-[var(--text-primary)] disabled:opacity-40">◀</button>
                    <button type="button" aria-label="Next positions" disabled={!total || start + WINDOW >= total} onClick={() => go(centre + WINDOW)}
                        className="rounded border border-[var(--border-primary)] px-2 py-1 text-[var(--text-primary)] disabled:opacity-40">▶</button>
                    {ab1Url ? (
                        <a href={ab1Url} download data-testid="pileup-ab1-download"
                            className="rounded border border-[var(--border-primary)] px-2 py-1 text-[var(--accent-primary)]">Download AB1</a>
                    ) : <span className="text-[var(--text-secondary)]">AB1 file not written for this result</span>}
                </div>
            </div>
            {failed && <p className="text-xs text-[var(--text-secondary)]">The per-base table could not be read, so the strip is not drawn. The downloads below are unaffected.</p>}
            {!failed && !rows && <p className="text-xs text-[var(--text-secondary)]">Loading read counts…</p>}
            {rows && shown.length > 0 && (
                <div className="overflow-x-auto">
                    <svg role="img" aria-label={`Read pileup, positions ${shown[0].position} to ${shown[shown.length - 1].position}`}
                        viewBox={`-34 0 ${width + 40} ${plotHeight + 44}`} className="w-full min-w-[640px]" style={{ maxHeight: 240 }}>
                        {shown.map((row, i) => row.position === centre && (
                            <rect key="selected" x={i * step} y={0} width={step} height={plotHeight + 18} fill="var(--text-primary)" opacity={0.08} />
                        ))}
                        {shown.map((row, i) => (
                            <text key={row.position} x={i * step + step / 2} y={12} textAnchor="middle" fontSize={11} className="font-mono"
                                fill={COLOUR[row.called] ?? 'var(--text-secondary)'} data-pileup-base={row.position}>
                                {row.called in COLOUR ? row.called : '–'}
                            </text>
                        ))}
                        <g transform="translate(0,18)">
                            {[0, 0.5, 1].map(level => (
                                <g key={level}>
                                    <line x1={0} x2={width} y1={plotHeight - level * plotHeight} y2={plotHeight - level * plotHeight} stroke="var(--border-primary)" strokeWidth={0.5} />
                                    <text x={-4} y={plotHeight - level * plotHeight + 3} textAnchor="end" fontSize={9} fill="var(--text-secondary)">{level * 100}%</text>
                                </g>
                            ))}
                            {BASES.map(base => (
                                <path key={base} d={peakPath(shown, base, step, plotHeight)} fill="none" stroke={COLOUR[base]} strokeWidth={1.2} data-pileup-channel={base} />
                            ))}
                            {shown.map((row, i) => (row.position % 5 === 0 || row.position === centre) && (
                                <text key={row.position} x={i * step + step / 2} y={plotHeight + 12} textAnchor="middle" fontSize={9}
                                    fill={row.position === centre ? 'var(--text-primary)' : 'var(--text-secondary)'}>{row.position.toLocaleString()}</text>
                            ))}
                        </g>
                    </svg>
                    <p className="text-[10px] text-[var(--text-secondary)]">Positions are in {coordinateLabel} coordinates. {total.toLocaleString()} positions.</p>
                </div>
            )}
        </section>
    );
}
