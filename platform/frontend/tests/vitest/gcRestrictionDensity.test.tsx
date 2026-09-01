import { describe, expect, it } from 'vitest';
import './setup';
import { restrictionBoundaryPositions } from '../../src/components/MolBioToolkit/GCContentTrack';
import type { RestrictionOccurrence } from '../../src/lib/restrictionAnalysis';

const occurrences = [
    { enzyme_id: 'EcoRI', site_start: 2, double_strand_events: [{ status: 'complete', top_boundary: 3, bottom_boundary: 7, contributor_group_id: 'cut:shared' }], nicks: [] },
    { enzyme_id: 'EcoRI-alt', site_start: 2, double_strand_events: [{ status: 'complete', top_boundary: 3, bottom_boundary: 7, contributor_group_id: 'cut:shared' }], nicks: [] },
    { enzyme_id: 'Nt.BbvCI', site_start: 8, double_strand_events: [], nicks: [{ status: 'complete', boundary: 9, contributor_group_id: 'nick:1' }] },
    { enzyme_id: 'Unknown', site_start: 5, double_strand_events: [], nicks: [] },
    { enzyme_id: 'Invalid', site_start: 4, double_strand_events: [{ status: 'geometry_out_of_bounds', top_boundary: null, bottom_boundary: null, contributor_group_id: 'cut:invalid' }], nicks: [{ status: 'geometry_out_of_bounds', boundary: null, contributor_group_id: 'nick:invalid' }] },
] as unknown as RestrictionOccurrence[];

describe('GC restriction density authority', () => {
    it('uses one backend physical boundary per complete contributor group and nick only', () => {
        expect(restrictionBoundaryPositions(occurrences, ['EcoRI', 'EcoRI-alt', 'Nt.BbvCI', 'Unknown', 'Invalid'], 10)).toEqual([3, 9]);
        expect(restrictionBoundaryPositions(occurrences, ['Unknown', 'Invalid'], 10)).toEqual([]);
        expect(restrictionBoundaryPositions(occurrences, [], 10)).toEqual([]);
    });

    it('rejects invalid geometry instead of normalizing and transforms valid boundaries for reverse display', () => {
        const invalid = [{ enzyme_id: 'EcoRI', double_strand_events: [{ status: 'complete', top_boundary: 13, bottom_boundary: 17, contributor_group_id: 'cut:bad' }], nicks: [] }] as unknown as RestrictionOccurrence[];
        expect(restrictionBoundaryPositions(invalid, ['EcoRI'], 10)).toEqual([]);
        expect(restrictionBoundaryPositions(occurrences, ['EcoRI'], 10, true)).toEqual([6]);
    });
});
