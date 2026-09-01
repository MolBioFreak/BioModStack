import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import './setup';
import { GCContentTrack, restrictionBoundaryPositions } from '../../src/components/MolBioToolkit/GCContentTrack';
import type { RestrictionOccurrence } from '../../src/lib/restrictionAnalysis';

const { plotSpy } = vi.hoisted(() => ({ plotSpy: vi.fn(() => null) }));
vi.mock('react-plotly.js', () => ({ default: plotSpy }));

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
        expect(restrictionBoundaryPositions(occurrences, ['EcoRI'], 10, true)).toEqual([7]);
    });

    it('counts complete linear DSB and nick boundaries at sequence length once each in the terminal window', () => {
        const terminal = [{
            enzyme_id: 'EcoRI',
            double_strand_events: [
                { status: 'complete', top_boundary: 20, contributor_group_id: 'cut:terminal' },
                { status: 'geometry_out_of_bounds', top_boundary: null, contributor_group_id: 'cut:incomplete' },
                { status: 'complete', top_boundary: 21, contributor_group_id: 'cut:outside' },
            ],
            nicks: [
                { status: 'complete', boundary: 20, contributor_group_id: 'nick:terminal' },
                { status: 'geometry_out_of_bounds', boundary: null, contributor_group_id: 'nick:incomplete' },
            ],
        }, {
            enzyme_id: 'RecognitionOnly',
            double_strand_events: [],
            nicks: [],
        }] as unknown as RestrictionOccurrence[];

        expect(restrictionBoundaryPositions(terminal, ['EcoRI', 'RecognitionOnly'], 20)).toEqual([20, 20]);
        expect(restrictionBoundaryPositions(terminal, ['EcoRI'], 20, true)).toEqual([0, 0]);
        expect(restrictionBoundaryPositions(terminal, ['EcoRI'], 20, false, true)).toEqual([]);

        plotSpy.mockClear();
        let renderer!: ReturnType<typeof create>;
        act(() => {
            renderer = create(<GCContentTrack
                sequence={'A'.repeat(20)}
                selectedEnzymes={['EcoRI', 'RecognitionOnly']}
                restrictionOccurrences={terminal}
                windowSize={10}
                stepSize={10}
            />);
        });
        const densityButton = renderer.root.findAllByType('button').find((button) => button.children.join('') === 'Cuts/kb');
        expect(densityButton).toBeDefined();
        act(() => densityButton?.props.onClick());
        const plotProps = plotSpy.mock.lastCall?.[0] as { data: Array<{ y: number[] }> };
        expect(plotProps.data[0].y).toEqual([0, 200]);
        act(() => renderer.unmount());
    });
});
