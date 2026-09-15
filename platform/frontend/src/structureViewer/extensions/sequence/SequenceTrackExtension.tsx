import { useMemo, useState, type UIEvent } from 'react';
import { canonicalSpatialRefKey, type ResidueRef } from '../../contracts/structureIdentity.js';
import type { MetricMissingness, MetricSelection } from '../../metrics/metricContracts.js';

export interface SequenceTrackPoint {
    readonly residue: ResidueRef;
    readonly label: string;
    readonly value?: number | null;
    readonly missingness?: MetricMissingness;
    readonly displayValue?: string;
}

export interface SequenceTrackExtensionProps {
    readonly metricId: string;
    readonly granularity?: 'residue' | 'atom';
    readonly points: readonly SequenceTrackPoint[];
    readonly selectedKeys?: ReadonlySet<string>;
    readonly onSelection: (selection: MetricSelection) => void;
    readonly onHover?: (residue: ResidueRef | undefined) => void;
}

const ITEM_WIDTH = 80;
const WINDOW_ITEMS = 120;

export function SequenceTrackExtension({ metricId, points, selectedKeys, onSelection, onHover, granularity = 'residue' }: SequenceTrackExtensionProps) {
    const ordered = useMemo(() => granularity === 'atom' ? points : [...points].sort((left, right) => (
        (left.residue.labelAsymId ?? left.residue.authAsymId ?? '').localeCompare(right.residue.labelAsymId ?? right.residue.authAsymId ?? '')
        || (left.residue.labelSeqId ?? left.residue.authSeqId ?? 0) - (right.residue.labelSeqId ?? right.residue.authSeqId ?? 0)
    )), [points, granularity]);
    const [start, setStart] = useState(0);
    const windowStart = Math.min(start, Math.max(0, ordered.length - WINDOW_ITEMS));
    const window = ordered.slice(windowStart, windowStart + WINDOW_ITEMS);
    const onScroll = (event: UIEvent<HTMLDivElement>) => {
        const next = Math.max(0, Math.min(Math.max(0, ordered.length - WINDOW_ITEMS), Math.floor(event.currentTarget.scrollLeft / ITEM_WIDTH) - 10));
        if (next !== start) setStart(next);
    };

    return <section aria-label={granularity === 'atom' ? 'Linked atom track' : 'Linked sequence track'} className="space-y-1">
        <div className="flex items-center justify-between text-xs text-slate-400"><span>{granularity === 'atom' ? 'Atoms' : 'Sequence'}</span><span>{ordered.length.toLocaleString()} {granularity === 'atom' ? 'atoms' : 'residues'} · window {ordered.length ? windowStart + 1 : 0}–{Math.min(ordered.length, windowStart + WINDOW_ITEMS)}</span></div>
        <div className="overflow-x-auto" onScroll={onScroll} role="listbox" aria-multiselectable="true">
            <div className="relative h-14" style={{ width: ordered.length * ITEM_WIDTH }}>
                <div className="absolute top-0 flex" style={{ left: windowStart * ITEM_WIDTH }}>
                    {window.map((point) => {
                        const key = canonicalSpatialRefKey(point.residue);
                        const selected = selectedKeys?.has(key) ?? false;
                        return <button
                            key={key}
                            type="button"
                            role="option"
                            aria-selected={selected}
                            title={`${point.label}: ${point.missingness ?? point.displayValue ?? point.value ?? 'unavailable'}`}
                            onMouseEnter={() => onHover?.(point.residue)}
                            onMouseLeave={() => onHover?.(undefined)}
                            onFocus={() => onHover?.(point.residue)}
                            onClick={() => onSelection({ metricId, identities: [point.residue], origin: 'sequence' })}
                            className={`h-12 shrink-0 border px-1 text-[10px] ${selected ? 'border-blue-400 bg-blue-900 text-white' : 'border-slate-700 bg-slate-900 text-slate-300'} focus:outline-none focus:ring-2 focus:ring-blue-500`}
                            style={{ width: ITEM_WIDTH }}
                        >
                            <span className="block truncate font-mono">{point.label}</span>
                            <span className="block truncate">{point.missingness ?? point.displayValue ?? point.value ?? '—'}</span>
                        </button>;
                    })}
                </div>
            </div>
        </div>
    </section>;
}
