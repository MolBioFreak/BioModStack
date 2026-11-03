import type { RestrictionDigestFragment } from './restrictionAnalysis';

export const DUPLEX_EDIT_REASON = 'This retained digest fragment has exact duplex ends. Sequence editing and top-strand-only import are unsupported; reopen the saved digest or use its physical fragments in Assembly.';

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

/** Generic sequence import cannot persist physical end metadata. Fail before parsing/saving. */
export function requireSupportedDigestImport(value: unknown): void {
    if (!value || typeof value !== 'object') return;
    const v = value as Record<string, unknown>;
    if (v.digestFragment || v.schema === 'bms.molbio.restriction-digest-fragment-provenance.v1'
        || (v.provenance && typeof v.provenance === 'object'
            && (v.provenance as Record<string, unknown>).schema === 'bms.molbio.restriction-digest-fragment-provenance.v1')) {
        throw new Error(DUPLEX_EDIT_REASON);
    }
}
