import type { RestrictionDigestFragment } from './restrictionAnalysis';

export const DUPLEX_EDIT_REASON = 'This retained digest fragment has exact duplex ends. Sequence editing is unsupported; reopen the saved digest or use its physical fragments in Assembly.';

/** Keep saved chemistry attached to the sequence without inventing end repair. */
export function retainedDigestFragment(provenance: unknown, sequence: string): RestrictionDigestFragment | undefined {
    if (!provenance || typeof provenance !== 'object') return undefined;
    const p = provenance as Record<string, unknown>;
    if (p.schema !== 'bms.molbio.restriction-digest-fragment-provenance.v1') return undefined;
    const f = p.geometry as RestrictionDigestFragment | undefined;
    if (!f || f.top_strand_sequence !== sequence || f.reference_span_bp !== sequence.length
        || !f.left_end || !f.right_end || f.left_end.side !== 'left' || f.right_end.side !== 'right') {
        throw new Error('Saved digest duplex geometry does not match this molecular revision.');
    }
    return structuredClone(f);
}

export function hasStickyDigestEnds(fragment: RestrictionDigestFragment | undefined): boolean {
    return Boolean(fragment && [fragment.left_end, fragment.right_end].some(end =>
        end.kind === 'five_prime_overhang' || end.kind === 'three_prime_overhang'));
}

/** Ends recorded on a retained fragment, read back from Toolkit JSON (`digestFragment`) or its provenance geometry. */
export function digestFragmentFromImport(value: unknown, sequence: string): RestrictionDigestFragment | undefined {
    if (!value || typeof value !== 'object') return undefined;
    const v = value as Record<string, unknown>;
    const direct = v.digestFragment as RestrictionDigestFragment | undefined;
    if (direct && typeof direct === 'object' && direct.left_end && direct.right_end) return structuredClone(direct);
    const provenance = (v.schema ? v : v.provenance) as Record<string, unknown> | undefined;
    if (provenance && typeof provenance === 'object' && provenance.schema === 'bms.molbio.restriction-digest-fragment-provenance.v1') {
        try { return retainedDigestFragment(provenance, sequence); } catch { return undefined; }
    }
    return undefined;
}
