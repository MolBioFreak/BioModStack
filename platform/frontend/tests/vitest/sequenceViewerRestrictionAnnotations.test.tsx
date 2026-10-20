import { describe, expect, it } from 'vitest';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { SeqViz } from 'seqviz';
import './setup';
import { buildNickingMapAnnotations, buildRestrictionMapAnnotations } from '../../src/components/MolBioToolkit/SequenceViewer';
import type { RestrictionOccurrence } from '../../src/lib/restrictionAnalysis';

const occurrences = [
    { occurrence_id: 'occ:dsb', enzyme_id: 'EcoRI', canonical_name: 'EcoRI', orientation: 'reverse', certainty: 'possible', site_start: 8, site_end_unwrapped: 14, site_segments: [[8, 10], [0, 4]], wraps_origin: true, double_strand_events: [{ status: 'complete', top_boundary: 9, bottom_boundary: 3 }], nicks: [], limitations: [] },
    { occurrence_id: 'occ:nick', enzyme_id: 'Nt.BbvCI', canonical_name: 'Nt.BbvCI', orientation: 'forward', certainty: 'definite', site_start: 4, site_end_unwrapped: 11, site_segments: [[4, 11]], wraps_origin: false, double_strand_events: [], nicks: [{ status: 'complete', boundary: 6, strand: 'top' }], limitations: [] },
] as unknown as RestrictionOccurrence[];

describe('SequenceViewer restriction annotations', () => {
    it('renders native short-span labels and touch-disclosable dense circular names without scanning', async () => {
        const host = document.createElement('div');
        document.body.append(host);
        const root = createRoot(host);
        const annotations = Array.from({ length: 80 }, (_, i) => ({
            id: `native:${i}`, name: `Enzyme${i}`, description: `Enzyme${i} · definite DSB`,
            start: 4, end: 5, direction: 1 as const, color: '#22d3ee', labelLane: true,
        }));
        const props = { seq: 'ACGT'.repeat(100), annotations, enzymes: [], disableExternalFonts: true, testSize: { width: 412, height: 760 } };
        try {
            await act(async () => root.render(<SeqViz {...props} viewer="linear" />));
            const labels = [...host.querySelectorAll('.la-vz-annotation-label')];
            expect(labels.map((n) => n.textContent)).toEqual(annotations.map((a) => a.name));
            expect(new Set([...host.querySelectorAll('.la-vz-linear-annotation-row')].map((n) => n.getAttribute('transform'))).size).toBe(80);
            expect(host.querySelector('g[aria-label="Enzyme0 · definite DSB"] title')?.textContent).toBe('Enzyme0 · definite DSB');
            await act(async () => root.render(<SeqViz {...props} viewer="circular" />));
            expect(host.querySelectorAll('path.la-vz-annotation')).toHaveLength(80);
            const trigger = host.querySelector('g[role="button"]')!;
            expect(trigger.getAttribute('data-seqviz-touch-control')).toBe('true');
            await act(async () => trigger.dispatchEvent(new MouseEvent('click', { bubbles: true })));
            const dialog = host.querySelector('[role="dialog"]')!;
            expect(dialog).not.toBeNull();
            expect([...dialog.querySelectorAll('button.la-vz-circular-label')].map((n) => n.textContent)).toEqual(annotations.map((a) => a.name));
            expect((dialog.querySelector('[aria-label="Scrollable annotation names"]') as HTMLElement).style.overflowY).toBe('auto');
            expect(dialog.querySelector('button.la-vz-circular-label')?.getAttribute('title')).toBe('Enzyme0 · definite DSB');
            await act(async () => dialog.querySelector('button')!.click());
            expect(host.querySelector('[role="dialog"]')).toBeNull();
        } finally {
            await act(async () => root.unmount());
            host.remove();
        }
    });
    it('uses only API occurrences and preserves wrapped segments, orientation, certainty, and semantics', () => {
        const annotations = buildRestrictionMapAnnotations({ occurrences, selectedEnzymes: ['EcoRI', 'Nt.BbvCI'], sequenceLength: 12, sourceDisplayStrand: 'plus', resolvedDisplayStrand: 'plus' });
        expect(annotations.map(({ id, start, end, direction, name, type }) => ({ id, start, end, direction, name, type }))).toEqual([
            { id: 'occ:dsb:segment:0', start: 8, end: 10, direction: -1, name: 'EcoRI', type: 'restriction_DSB' },
            { id: 'occ:dsb:segment:1', start: 0, end: 4, direction: -1, name: 'EcoRI', type: 'restriction_DSB' },
            { id: 'occ:nick:segment:0', start: 4, end: 11, direction: 1, name: 'Nt.BbvCI', type: 'restriction_nick' },
        ]);
        expect(annotations.every((a) => a.labelLane)).toBe(true);
        expect(annotations[0].description).toContain('possible DSB · reverse');
        expect(annotations[2].description).toContain('definite nick · forward');
        expect(buildRestrictionMapAnnotations({ occurrences, selectedEnzymes: [], sequenceLength: 12, sourceDisplayStrand: 'plus', resolvedDisplayStrand: 'plus' })).toEqual([]);
    });

    it('builds strand-specific nick markers from API cut identity without motif scanning', () => {
        expect(buildNickingMapAnnotations({ occurrences, selectedEnzymes: ['Nt.BbvCI'], sequenceLength: 12, sourceDisplayStrand: 'plus', resolvedDisplayStrand: 'minus' }).map(({ name, start, direction }) => ({ name, start, direction }))).toEqual([
            { name: 'Nt.BbvCI', start: 5, direction: -1 },
        ]);
    });
});
