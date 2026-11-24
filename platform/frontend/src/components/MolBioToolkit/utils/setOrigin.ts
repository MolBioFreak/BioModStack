/**
 * Set origin (rotate a circular molecule) and linearization bookkeeping.
 *
 * Storage convention (shared with the server importer and the assembly mapper):
 * compound locations are stored in biological part order; a feature crossing the
 * origin is stored as ordered non-wrapping segments, never start > end. Ends are
 * half-open. Results are checked against services/assembly/feature_mapping.py by
 * tests/fixtures/set-origin-cases.json.
 */
import { reverseComplementSequence } from './nucleotides.js';

type Seg = { start: number; end: number };
type Strand = 1 | -1;

interface OriginFeature {
    start: number;
    end: number;
    strand: Strand;
    segments?: Seg[];
    provenance?: Record<string, unknown>;
}

interface OriginPrimer {
    start: number;
    end: number;
    strand: Strand;
    binding_status?: 'needs_review';
    sites?: Array<Seg & { strand: Strand }>;
}

export interface OriginSequence<F extends OriginFeature = OriginFeature, P extends OriginPrimer = OriginPrimer> {
    sequence: string;
    circular: boolean;
    sequenceType?: 'dna' | 'rna' | 'protein';
    features: F[];
    primers?: P[];
    translations?: unknown[];
    analysisTracks?: unknown[];
}

const mod = (value: number, n: number) => ((value % n) + n) % n;

/** Rotate one half-open segment; a segment crossing the new origin becomes two, in traversal order. */
function rotateSegment(segment: Seg, shift: number, n: number, strand: Strand): Seg[] {
    const start = mod(segment.start - shift, n);
    const end = start + (segment.end - segment.start);
    if (end <= n) return [{ start, end }];
    const head = { start, end: n };
    const tail = { start: 0, end: end - n };
    return strand === -1 ? [tail, head] : [head, tail];
}

function flipSegments(segments: Seg[], n: number): Seg[] {
    return segments.map((s) => ({ start: n - s.end, end: n - s.start }));
}

/** Join traversal-adjacent pieces (same as the server mapper's run compression). */
function mergeRuns(segments: Seg[], strand: Strand): Seg[] {
    const out: Seg[] = [];
    for (const s of segments) {
        const prev = out[out.length - 1];
        if (prev && strand === 1 && prev.end === s.start) prev.end = s.end;
        else if (prev && strand === -1 && s.end === prev.start) prev.start = s.start;
        else out.push({ ...s });
    }
    return out;
}

function bounds(segments: Seg[]): Seg {
    return {
        start: Math.min(...segments.map((s) => s.start)),
        end: Math.max(...segments.map((s) => s.end)),
    };
}

function featureSegs(feature: OriginFeature): Seg[] {
    return feature.segments && feature.segments.length > 0
        ? feature.segments.map((s) => ({ start: s.start, end: s.end }))
        : [{ start: feature.start, end: feature.end }];
}

/** Wrapped legacy form (start > end, no segments) is read as two segments. */
function normalizedSegments(feature: OriginFeature, n: number): Seg[] {
    if ((!feature.segments || feature.segments.length === 0) && feature.start > feature.end) {
        const head = { start: feature.start, end: n };
        const tail = { start: 0, end: feature.end };
        return feature.strand === -1 ? [tail, head] : [head, tail];
    }
    return featureSegs(feature);
}

function transformFeature<F extends OriginFeature>(feature: F, shift: number, n: number, reverse: boolean): F {
    let segments = mergeRuns(
        normalizedSegments(feature, n).flatMap((s) => rotateSegment(s, shift, n, feature.strand)),
        feature.strand,
    );
    let strand = feature.strand;
    if (reverse) {
        segments = flipSegments(segments, n);
        strand = strand === 1 ? -1 : 1;
    }
    segments = mergeRuns(segments, strand);
    return { ...feature, ...bounds(segments), strand, segments };
}

function primerSites(primer: OriginPrimer, n: number): Array<Seg & { strand: Strand }> {
    if (primer.sites && primer.sites.length > 0) return primer.sites.map((s) => ({ ...s }));
    if (primer.start > primer.end) {
        return [{ start: primer.start, end: n, strand: primer.strand }, ...(primer.end > 0 ? [{ start: 0, end: primer.end, strand: primer.strand }] : [])];
    }
    return [{ start: primer.start, end: primer.end, strand: primer.strand }];
}

function transformPrimer<P extends OriginPrimer>(primer: P, shift: number, n: number, reverse: boolean): P {
    if (primer.binding_status === 'needs_review') return primer;
    let sites = primerSites(primer, n).flatMap((site) => rotateSegment(site, shift, n, 1).map((s) => ({ ...s, strand: site.strand })));
    let strand = primer.strand;
    if (reverse) {
        sites = sites.map((s) => ({ start: n - s.end, end: n - s.start, strand: (s.strand === 1 ? -1 : 1) as Strand }));
        strand = strand === 1 ? -1 : 1;
    }
    sites.sort((a, b) => a.start - b.start);
    // Primer sites are ordered from the site start: [start, n) first, then [0, end).
    if (sites.length > 1 && sites[0].start === 0 && sites[sites.length - 1].end === n) sites.unshift(sites.pop()!);
    return { ...primer, strand, sites, start: sites[0].start, end: sites[sites.length - 1].end };
}

/**
 * Rotate so that `position` (0-based, in the current coordinates) becomes base 0.
 * With `reverseComplement` the rotated molecule is then reverse-complemented.
 */
export function setOrigin<T extends OriginSequence>(data: T, position: number, reverseComplement = false): T {
    const n = data.sequence.length;
    if (!data.circular || n === 0) return data;
    const shift = mod(position, n);
    if (shift === 0 && !reverseComplement) return data;
    const type = data.sequenceType === 'rna' ? 'rna' : 'dna';
    const rotated = data.sequence.slice(shift) + data.sequence.slice(0, shift);
    return {
        ...data,
        sequence: reverseComplement ? reverseComplementSequence(rotated, type) : rotated,
        features: data.features.map((f) => transformFeature(f, shift, n, reverseComplement)),
        primers: data.primers?.map((p) => transformPrimer(p, shift, n, reverseComplement)),
        translations: [],
        analysisTracks: [],
    };
}

function crossesOrigin(segments: Seg[], n: number): boolean {
    return segments.some((segment, index) => {
        const next = segments[(index + 1) % segments.length];
        return segments.length > 1 && ((segment.end === n && next.start === 0) || (segment.start === 0 && next.end === n));
    });
}

/**
 * Linearize at the current origin. Features and primer sites crossing the origin
 * are kept as their two-segment entries and marked as split by linearization.
 */
export function splitAtLinearization<T extends OriginSequence>(data: T): T {
    const n = data.sequence.length;
    const mark = (provenance: Record<string, unknown> | undefined) => ({ ...(provenance || {}), linearization_split: true });
    return {
        ...data,
        features: data.features.map((feature) => {
            const segments = normalizedSegments(feature, n);
            const wrapped = (!feature.segments?.length && feature.start > feature.end) || crossesOrigin(segments, n);
            return wrapped ? { ...feature, ...bounds(segments), segments, provenance: mark(feature.provenance) } : feature;
        }),
        primers: data.primers?.map((primer) => {
            if (primer.binding_status === 'needs_review') return primer;
            const sites = primerSites(primer, n);
            const wrapped = primer.start > primer.end || crossesOrigin(sites, n);
            return wrapped ? ({ ...primer, sites, provenance: mark((primer as { provenance?: Record<string, unknown> }).provenance) } as typeof primer) : primer;
        }),
    };
}
