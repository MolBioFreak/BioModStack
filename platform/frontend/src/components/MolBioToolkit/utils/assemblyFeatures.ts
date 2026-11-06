import type { SequenceFeature } from '../../../lib/api';
import type { Feature } from '../types';

/**
 * Stored-feature dicts (ordered segments, strand, qualifiers) in FRAGMENT-LOCAL coordinates for an
 * inline Assembly fragment: index 0 = first base of the fragment sequence, top strand.
 * `region` is the source interval the fragment covers; a wrapping region (start > end on a circle) is two runs.
 * Features that fall wholly outside the region are dropped; parts that cross its edges are clipped.
 */
export function inlineFragmentFeatures(
    features: Feature[] | undefined,
    sourceLength: number,
    region?: { start: number; end: number; wrapsOrigin?: boolean },
): SequenceFeature[] | undefined {
    if (!features?.length) return undefined;
    const positions: number[] = region
        ? region.wrapsOrigin
            ? [...Array(sourceLength - region.start).keys()].map(i => region.start + i).concat([...Array(region.end).keys()])
            : [...Array(Math.max(0, region.end - region.start)).keys()].map(i => region.start + i)
        : [...Array(sourceLength).keys()];
    const local = new Map(positions.map((p, i) => [p, i]));
    const out: SequenceFeature[] = [];
    for (const feature of features) {
        const parts = feature.segments?.length ? feature.segments : [{ start: feature.start, end: feature.end }];
        const segments: Array<{ start: number; end: number }> = [];
        let clipped = false;
        for (const part of parts) {
            let run: { start: number; end: number } | null = null;
            for (let p = part.start; p < part.end; p++) {
                const index = local.get(p);
                if (index === undefined) { clipped = true; if (run) { segments.push(run); run = null; } continue; }
                if (run && run.end === index) run.end = index + 1;
                else { if (run) segments.push(run); run = { start: index, end: index + 1 }; }
            }
            if (run) segments.push(run);
        }
        if (!segments.length) continue;
        const { notes, qualifiers, provenance, color, description } = feature;
        out.push({
            id: feature.id, name: feature.name, type: feature.type,
            start: Math.min(...segments.map(s => s.start)), end: Math.max(...segments.map(s => s.end)),
            strand: feature.strand, segments,
            ...(color ? { color } : {}), ...(description ? { description } : {}),
            ...(notes ? { notes: notes as SequenceFeature['notes'] } : {}),
            ...(qualifiers ? { qualifiers: qualifiers as SequenceFeature['qualifiers'] } : {}),
            ...(provenance ? { provenance: provenance as SequenceFeature['provenance'] } : {}),
            ...(clipped ? { provenance: { ...(provenance as object | undefined), clipped_by_fragment: true } as SequenceFeature['provenance'] } : {}),
        });
    }
    return out.length ? out : undefined;
}
