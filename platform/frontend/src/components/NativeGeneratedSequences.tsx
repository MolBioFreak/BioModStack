import { useState } from 'react';
import type { NativeGenerationRecord } from '../lib/nativeBinderResults';

function polymerMeta(chainType: unknown) {
    const raw = String(chainType ?? '');
    const upper = raw.toUpperCase();
    if (upper.includes('DNA')) return { label: 'DNA', unit: 'nt', badge: 'border-emerald-500/35 bg-emerald-500/10 text-emerald-300' };
    if (upper.includes('RNA')) return { label: 'RNA', unit: 'nt', badge: 'border-amber-500/35 bg-amber-500/10 text-amber-300' };
    if (upper.includes('POLYPEPTIDE') || upper.includes('PROTEIN')) return { label: 'Protein', unit: 'aa', badge: 'border-sky-500/35 bg-sky-500/10 text-sky-300' };
    return { label: raw, unit: 'res', badge: 'border-[var(--border-color)] bg-[var(--bg-secondary)] text-[var(--text-secondary)]' };
}

/** Display producer tokens verbatim; canonical sequence is not a replacement. */
export function NativeGeneratedSequences({ row }: { row: NativeGenerationRecord }) {
    const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
    const sequences = Array.isArray(row.sequences) ? row.sequences as Record<string, unknown>[]
        : typeof row.sequence === 'string' ? [{ sequence: row.sequence }] : [];
    if (!sequences.length) return null;
    return <section aria-label="Native generated sequences" className="mt-3 min-w-0 space-y-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)]/40 p-2.5">
        <h5 className="text-xs font-semibold text-[var(--text-primary)]">Native generated sequences</h5>
        {sequences.map((sequence, index) => {
            const rawSeq = String(sequence.sequence ?? 'Not reported');
            const meta = polymerMeta(sequence.chain_type);
            const len = typeof sequence.sequence === 'string' ? sequence.sequence.replace(/\s+/g, '').length : 0;
            return <div key={index} className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--text-secondary)]">
                        <span className="font-medium text-[var(--text-primary)]">{sequence.chain_id == null ? 'Native sequence' : `Chain ${String(sequence.chain_id)}`}</span>
                        {String(sequence.chain_type ?? '') && <span className={`rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${meta.badge}`}>{String(sequence.chain_type)}</span>}
                        {len > 0 && <span className="font-mono text-[10px] text-[var(--text-secondary)]">{len} {meta.unit}</span>}
                    </p>
                    {typeof sequence.sequence === 'string' && sequence.sequence.length > 0 && <button
                        type="button"
                        className="rounded border border-[var(--border-color)] bg-[var(--bg-secondary)] px-2 py-0.5 text-[10px] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                        onClick={() => {
                            navigator.clipboard?.writeText(rawSeq).catch(() => {});
                            setCopiedIndex(index);
                            window.setTimeout(() => setCopiedIndex(prev => prev === index ? null : prev), 1500);
                        }}
                    >{copiedIndex === index ? 'Copied' : 'Copy'}</button>}
                </div>
                <pre className="overflow-x-auto whitespace-nowrap rounded-md border border-[var(--border-color)] bg-[var(--bg-primary)] px-2.5 py-1.5 font-mono text-xs tracking-wider text-[#cfe0ff]" title={rawSeq}>{rawSeq}</pre>
                {Array.isArray(sequence.positions) && <details className="text-xs text-[var(--text-secondary)]"><summary className="cursor-pointer">Native component positions</summary>
                    <ol className="mt-1 max-h-40 overflow-auto pl-5">{(sequence.positions as Record<string, unknown>[]).map((position, i) => <li key={i}>{String(position.chain_id ?? '')}:{String(position.res_id ?? '')}{String(position.ins_code ?? '')} · {String(position.component_id ?? '')}</li>)}</ol>
                </details>}
            </div>;
        })}
    </section>;
}
