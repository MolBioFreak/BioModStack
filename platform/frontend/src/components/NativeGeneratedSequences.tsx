import type { NativeGenerationRecord } from '../lib/nativeBinderResults';

/** Display producer tokens verbatim; canonical sequence is not a replacement. */
export function NativeGeneratedSequences({ row }: { row: NativeGenerationRecord }) {
    const sequences = Array.isArray(row.sequences) ? row.sequences as Record<string, unknown>[]
        : typeof row.sequence === 'string' ? [{ sequence: row.sequence }] : [];
    if (!sequences.length) return null;
    return <section aria-label="Native generated sequences" className="my-3 min-w-0 space-y-3">
        <h5>Native generated sequences</h5>
        {sequences.map((sequence, index) => <div key={index}>
            <p>{sequence.chain_id == null ? 'Native sequence' : `Chain ${String(sequence.chain_id)}`} {String(sequence.chain_type ?? '')}</p>
            <pre className="whitespace-pre-wrap break-all">{String(sequence.sequence ?? 'Not reported')}</pre>
            {Array.isArray(sequence.positions) && <details><summary>Native component positions</summary>
                <ol>{(sequence.positions as Record<string, unknown>[]).map((position, i) => <li key={i}>{String(position.chain_id ?? '')}:{String(position.res_id ?? '')}{String(position.ins_code ?? '')} · {String(position.component_id ?? '')}</li>)}</ol>
            </details>}
        </div>)}
    </section>;
}
