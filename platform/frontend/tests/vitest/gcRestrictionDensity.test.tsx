import { describe, expect, it } from 'vitest';
import './setup';
import { restrictionBoundaryPositions } from '../../src/components/MolBioToolkit/GCContentTrack';
import type { RestrictionOccurrence } from '../../src/lib/restrictionAnalysis';

const occurrences = [
    { enzyme_id: 'EcoRI', site_start: 2, double_strand_events: [{ status: 'complete', top_boundary: 3, bottom_boundary: 7 }], nicks: [] },
    { enzyme_id: 'Nt.BbvCI', site_start: 8, double_strand_events: [], nicks: [{ status: 'complete', boundary: 9 }] },
    { enzyme_id: 'Unknown', site_start: 5, double_strand_events: [], nicks: [] },
] as unknown as RestrictionOccurrence[];

describe('GC restriction density authority', () => {
    it('uses API-provided DSB/nick boundaries for the selected map set', () => {
        expect(restrictionBoundaryPositions(occurrences, ['EcoRI', 'Nt.BbvCI'], 10)).toEqual([3, 7, 9]);
        expect(restrictionBoundaryPositions(occurrences, ['Unknown'], 10)).toEqual([5]);
        expect(restrictionBoundaryPositions(occurrences, [], 10)).toEqual([]);
    });

    it('transforms API boundaries for reverse display without rescanning sequence motifs', () => {
        expect(restrictionBoundaryPositions(occurrences, ['EcoRI'], 10, true)).toEqual([2, 6]);
    });
});
