import { useState } from 'react';
import type { NativeGenerationRecord } from '../lib/nativeBinderResults';

const CANONICAL_CCD = new Set([
    'ALA', 'ARG', 'ASN', 'ASP', 'CYS', 'GLN', 'GLU', 'GLY', 'HIS', 'ILE',
    'LEU', 'LYS', 'MET', 'PHE', 'PRO', 'SER', 'THR', 'TRP', 'TYR', 'VAL',
    'DA', 'DC', 'DG', 'DT', 'A', 'C', 'G', 'U',
]);

function polymerMeta(chainType: unknown) {
    const raw = String(chainType ?? '');
    const upper = raw.toUpperCase();
    const hasDna = upper.includes('DNA') || upper.includes('DEOXYRIBO');
    const hasRna = upper.includes('RNA') || upper.includes('POLYRIBONUCLEOTIDE');
    if (hasDna && hasRna) return { label: 'DNA/RNA · 5′→3′', short: 'DNA/RNA', unit: 'nt', badge: 'border-teal-500/35 bg-teal-500/10 text-teal-300' };
    if (hasDna) return { label: 'DNA · 5′→3′', short: 'DNA', unit: 'nt', badge: 'border-emerald-500/35 bg-emerald-500/10 text-emerald-300' };
    if (hasRna) return { label: 'RNA · 5′→3′', short: 'RNA', unit: 'nt', badge: 'border-amber-500/35 bg-amber-500/10 text-amber-300' };
    if (upper.includes('POLYPEPTIDE') || upper.includes('PROTEIN')) return { label: 'Protein', short: 'Protein', unit: 'aa', badge: 'border-sky-500/35 bg-sky-500/10 text-sky-300' };
    return { label: raw, short: raw || 'Chain', unit: 'res', badge: 'border-[var(--border-color)] bg-[var(--bg-secondary)] text-[var(--text-secondary)]' };
}

function nonCanonicalBadges(positions: unknown): string[] {
    if (!Array.isArray(positions)) return [];
    const found: string[] = [];
    for (const entry of positions as Record<string, unknown>[]) {
        const comp = String(entry.component_id ?? '').trim().toUpperCase();
        if (!comp || CANONICAL_CCD.has(comp)) continue;
        const res = String(entry.res_id ?? '') + String(entry.ins_code ?? '');
        found.push(res ? `${comp}@${res}` : comp);
    }
    return found;
}

/** Display producer tokens verbatim; canonical sequence is not a replacement. */
export function NativeGeneratedSequences({ row }: { row: NativeGenerationRecord }) {
    const [copiedKey, setCopiedKey] = useState<string | null>(null);
    const sequences = Array.isArray(row.sequences) ? row.sequences as Record<string, unknown>[]
        : typeof row.sequence === 'string' ? [{ sequence: row.sequence }] : [];
    if (!sequences.length) return null;

    const copyText = (key: string, value: string) => {
        navigator.clipboard?.writeText(value).catch(() => {});
        setCopiedKey(key);
        window.setTimeout(() => setCopiedKey(prev => prev === key ? null : prev), 1500);
    };

    return <section aria-label="Native generated sequences" className="mt-3 min-w-0 space-y-2.5 rounded-xl border border-[var(--border-color)] bg-[var(--bg-primary)]/40 p-3.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
            <h5 className="text-xs font-semibold text-[var(--text-primary)]">Native generated sequences</h5>
            <span className="text-[11px] text-[var(--text-secondary)]">{sequences.length} chain{sequences.length === 1 ? '' : 's'}</span>
        </div>
        <div className="space-y-2">
            {sequences.map((sequence, index) => {
                const rawSeq = String(sequence.sequence ?? 'Not reported');
                const meta = polymerMeta(sequence.chain_type);
                const len = typeof sequence.sequence === 'string' ? sequence.sequence.replace(/\s+/g, '').length : 0;
                const chainTitle = sequence.chain_id == null ? 'Native sequence' : `Chain ${String(sequence.chain_id)}`;
                const mods = nonCanonicalBadges(sequence.positions);
                const fastaHeader = sequence.chain_id == null ? `>sequence_${index + 1}` : `>chain_${String(sequence.chain_id)}`;
                return <div key={index} className="min-w-0 space-y-1.5 rounded-lg border border-[var(--border-color)] bg-[var(--bg-secondary)]/65 p-2.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--text-secondary)]">
                            <span className="font-semibold text-[var(--text-primary)]">{chainTitle}</span>
                            {String(sequence.chain_type ?? '') && <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${meta.badge}`} title={String(sequence.chain_type)}>{meta.label}</span>}
                            {len > 0 && <span className="font-mono text-[10px] text-[var(--text-secondary)]">{len} {meta.unit}</span>}
                            {mods.slice(0, 4).map((mod, mIdx) => <span key={mIdx} className="rounded-full border border-purple-500/40 bg-purple-500/15 px-1.5 py-0.5 font-mono text-[10px] text-purple-200">{mod}</span>)}
                            {mods.length > 4 && <span className="font-mono text-[10px] text-purple-300">+{mods.length - 4} mod</span>}
                        </p>
                        {typeof sequence.sequence === 'string' && sequence.sequence.length > 0 && <div className="flex items-center gap-1.5">
                            <button
                                type="button"
                                className="rounded-md border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-0.5 text-[10px] font-medium text-[var(--text-secondary)] transition hover:text-[var(--text-primary)]"
                                onClick={() => copyText(`seq:${index}`, rawSeq)}
                            >{copiedKey === `seq:${index}` ? 'Copied' : 'Copy'}</button>
                            <button
                                type="button"
                                className="rounded-md border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-0.5 text-[10px] font-medium text-[var(--text-secondary)] transition hover:text-[var(--text-primary)]"
                                onClick={() => copyText(`fasta:${index}`, `${fastaHeader}\n${rawSeq}`)}
                            >{copiedKey === `fasta:${index}` ? 'FASTA copied' : 'Copy FASTA'}</button>
                        </div>}
                    </div>
                    <pre className="overflow-x-auto whitespace-nowrap rounded-md border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 font-mono text-xs tracking-wider text-[#cfe0ff]" title={rawSeq}>{rawSeq}</pre>
                    {Array.isArray(sequence.positions) && <details className="text-xs text-[var(--text-secondary)]"><summary className="cursor-pointer select-none">Native component positions</summary>
                        <ol className="mt-1 max-h-40 overflow-auto pl-5 font-mono text-[11px]">{(sequence.positions as Record<string, unknown>[]).map((position, i) => <li key={i}>{String(position.chain_id ?? '')}:{String(position.res_id ?? '')}{String(position.ins_code ?? '')} · {String(position.component_id ?? '')}</li>)}</ol>
                    </details>}
                </div>;
            })}
        </div>
    </section>;
}
