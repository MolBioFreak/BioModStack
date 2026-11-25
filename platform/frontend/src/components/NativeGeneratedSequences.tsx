import type { NativeGenerationRecord } from '../lib/nativeBinderResults';

/** Display producer tokens verbatim; canonical sequence is not a replacement. */
export function NativeGeneratedSequences({ row }: { row: NativeGenerationRecord }) {
    const sequences = Array.isArray(row.sequences) ? row.sequences as Record<string, unknown>[]
        : typeof row.sequence === 'string' ? [{ sequence: row.sequence }] : [];
    if (!sequences.length) return null;
    return <section aria-label="Native generated sequences" className="mt-3 min-w-0 space-y-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)]/40 p-2.5">
        <h5 className="text-xs font-semibold text-[var(--text-primary)]">Native generated sequences</h5>
        {sequences.map((sequence, index) => <div key={index} className="min-w-0 space-y-1">
            <p className="text-[11px] text-[var(--text-secondary)]">{sequence.chain_id == null ? 'Native sequence' : `Chain ${String(sequence.chain_id)}`} {String(sequence.chain_type ?? '')}</p>
            <pre className="overflow-x-auto whitespace-nowrap rounded-md border border-[var(--border-color)] bg-[var(--bg-primary)] px-2.5 py-1.5 font-mono text-xs tracking-wider text-[#cfe0ff]" title={String(sequence.sequence ?? 'Not reported')}>{String(sequence.sequence ?? 'Not reported')}</pre>
            {Array.isArray(sequence.positions) && <details className="text-xs text-[var(--text-secondary)]"><summary className="cursor-pointer">Native component positions</summary>
                <ol className="mt-1 max-h-40 overflow-auto pl-5">{(sequence.positions as Record<string, unknown>[]).map((position, i) => <li key={i}>{String(position.chain_id ?? '')}:{String(position.res_id ?? '')}{String(position.ins_code ?? '')} · {String(position.component_id ?? '')}</li>)}</ol>
            </details>}
        </div>)}
    </section>;
}
