import { describe, expect, it } from 'vitest';
import './setup';
import { buildNickingMapAnnotations, buildRestrictionMapAnnotations } from '../../src/components/MolBioToolkit/SequenceViewer';
import type { RestrictionOccurrence } from '../../src/lib/restrictionAnalysis';

const occurrences = [
    { occurrence_id: 'occ:dsb', enzyme_id: 'EcoRI', canonical_name: 'EcoRI', orientation: 'reverse', certainty: 'possible', site_start: 8, site_end_unwrapped: 14, site_segments: [[8, 10], [0, 4]], wraps_origin: true, double_strand_events: [{ status: 'complete', top_boundary: 9, bottom_boundary: 3 }], nicks: [], limitations: [] },
    { occurrence_id: 'occ:nick', enzyme_id: 'Nt.BbvCI', canonical_name: 'Nt.BbvCI', orientation: 'forward', certainty: 'definite', site_start: 4, site_end_unwrapped: 11, site_segments: [[4, 11]], wraps_origin: false, double_strand_events: [], nicks: [{ status: 'complete', boundary: 6, strand: 'top' }], limitations: [] },
] as unknown as RestrictionOccurrence[];

describe('SequenceViewer restriction annotations', () => {
    it('uses only API occurrences and preserves wrapped segments, orientation, certainty, and semantics', () => {
        const annotations = buildRestrictionMapAnnotations({ occurrences, selectedEnzymes: ['EcoRI', 'Nt.BbvCI'], sequenceLength: 12, sourceDisplayStrand: 'plus', resolvedDisplayStrand: 'plus' });
        expect(annotations.map(({ id, start, end, direction, name, type }) => ({ id, start, end, direction, name, type }))).toEqual([
            { id: 'occ:dsb:segment:0', start: 8, end: 10, direction: -1, name: 'EcoRI · possible DSB', type: 'restriction_DSB' },
            { id: 'occ:dsb:segment:1', start: 0, end: 4, direction: -1, name: 'EcoRI · possible DSB', type: 'restriction_DSB' },
            { id: 'occ:nick:segment:0', start: 4, end: 11, direction: 1, name: 'Nt.BbvCI · definite nick', type: 'restriction_nick' },
        ]);
        expect(buildRestrictionMapAnnotations({ occurrences, selectedEnzymes: [], sequenceLength: 12, sourceDisplayStrand: 'plus', resolvedDisplayStrand: 'plus' })).toEqual([]);
    });

    it('builds strand-specific nick markers from API cut identity without motif scanning', () => {
        expect(buildNickingMapAnnotations({ occurrences, selectedEnzymes: ['Nt.BbvCI'], sequenceLength: 12, sourceDisplayStrand: 'plus', resolvedDisplayStrand: 'minus' }).map(({ name, start, direction }) => ({ name, start, direction }))).toEqual([
            { name: 'Nt.BbvCI', start: 5, direction: -1 },
        ]);
    });
});
