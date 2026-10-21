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
    it.each([412, 1200])('packs sparse native labels with clamped extents at width %i without changing sites', async (width) => {
        const host = document.createElement('div');
        document.body.append(host);
        const root = createRoot(host);
        const annotations = [0, 1, 16, 32, 48, 64, 79, 80, 96, 112, 128, 144, 159].map((start, i) => ({
            id: `site:${i}`, name: i % 2 ? 'EcoO157SI' : 'BmeDI',
            start, end: start + 1, direction: (i % 2 ? -1 : 1) as 1 | -1, color: '#22d3ee', labelLane: true,
        }));
        const original = structuredClone(annotations);
        try {
            const props = { seq: 'ACGT'.repeat(40), annotations, enzymes: [], disableExternalFonts: true, testSize: { width, height: 12000 } };
            await act(async () => root.render(<SeqViz {...props} viewer="linear" />));
            const labels = [...host.querySelectorAll('.la-vz-annotation-label')];
            expect(labels.map((n) => [n.id, n.textContent]).sort()).toEqual(annotations.map((a) => [a.id, a.name]).sort());
            expect(annotations).toEqual(original);
            const rows = [...host.querySelectorAll('.la-vz-linear-annotation-row')];
            expect(rows.some((row) => row.querySelectorAll('.la-vz-annotation-label').length > 1)).toBe(true);
            expect(rows.length).toBeLessThan(annotations.length);
            for (const row of rows) {
                const extents = [...row.querySelectorAll('.la-vz-annotation-label')].map((label) => {
                    const group = label.parentElement!;
                    const x = Number(group.getAttribute('transform')!.match(/translate\(([^,]+)/)![1]);
                    const center = x + Number(label.getAttribute('x'));
                    const half = label.textContent!.length * 0.591 * 12 / 2;
                    return [center - half, center + half];
                }).sort((a, b) => a[0] - b[0]);
                for (let i = 1; i < extents.length; i++) expect(extents[i][0] - extents[i - 1][1]).toBeGreaterThanOrEqual(7.99);
            }
            // The selection registry is populated by native marker refs, not label extents.
            const path = host.querySelector('path.la-vz-annotation')!;
            const fiberKey = Object.keys(path).find((key) => key.startsWith('__reactFiber'))!;
            let fiber = (path as unknown as Record<string, any>)[fiberKey];
            while (fiber && !fiber.stateNode?.findXAndWidthElement) fiber = fiber.return;
            expect(fiber).toBeTruthy();
            const native = fiber.memoizedProps.annotationRows.flat().filter((a: { labelLane?: boolean }) => a.labelLane);
            for (const a of native) expect(original.find((o) => o.id === a.id)).toMatchObject({ start: a.start, end: a.end, direction: a.direction, name: a.name });
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
