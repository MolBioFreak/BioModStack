import { describe, expect, it } from 'vitest';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { SeqViz } from 'seqviz';
import './setup';
import { buildNickingMapAnnotations, buildRestrictionMapAnnotations } from '../../src/components/MolBioToolkit/SequenceViewer';
import type { RestrictionOccurrence } from '../../src/lib/restrictionAnalysis';
import { groupRestrictionLabels } from '../../src/components/MolBioToolkit/utils/restrictionLabels';

function site(name: string, pattern: string, changes: Partial<RestrictionOccurrence> = {}): RestrictionOccurrence {
    return {
        occurrence_id: `occ:${name}`, occurrence_ordinal: 0, enzyme_id: name, canonical_name: name,
        recognition_pattern: pattern, orientation: 'forward', certainty: 'definite', site_start: 20,
        site_end_unwrapped: 20 + pattern.length, site_segments: [[20, 20 + pattern.length]], wraps_origin: false,
        double_strand_events: [{ enzyme_id: name, occurrence_id: `occ:${name}`, event_ordinal: 0,
            orientation: 'forward', status: 'complete', top_boundary: 22, bottom_boundary: 26,
            top_boundary_unwrapped: 22, bottom_boundary_unwrapped: 26, contributor_group_id: 'cut:22:26',
            overhang_kind: 'five_prime', overhang_length_nt: 4 }], nicks: [], limitations: [], ...changes,
    };
}

describe('nonredundant restriction map labels', () => {
    const map = (rows: RestrictionOccurrence[], grouped = true) => buildRestrictionMapAnnotations({
        occurrences: rows, selectedEnzymes: rows.map(row => row.enzyme_id), sequenceLength: 400,
        sourceDisplayStrand: 'plus', resolvedDisplayStrand: 'plus', groupSameSiteLabels: grouped,
    });

    it('prefers the most constrained recognition pattern and retains all names without changing analysis', () => {
        const rows = [site('Broad', 'RACCTY'), site('Exact', 'GACCTG'), site('Unconstrained', 'NACCTN')];
        const original = structuredClone(rows);
        const labels = map(rows);
        expect(labels).toHaveLength(1);
        expect(labels[0].name).toBe('Exact +2');
        expect(labels[0].description).toContain('Broad (RACCTY)');
        expect(labels[0].description).toContain('Unconstrained (NACCTN)');
        expect(map([...rows].reverse())[0]).toEqual(labels[0]);
        expect(rows).toEqual(original);
        expect(map(rows, false).map(label => label.name)).toEqual(['Broad', 'Exact', 'Unconstrained']);
        expect(groupRestrictionLabels(rows, ['Broad'])[0].name).toBe('Broad');
    });

    it('ranks IUPAC constraints rather than counting motif characters and uses familiar names only for ties', () => {
        expect(map([site('LongSpacer', 'NNNNACGTNNNN'), site('Constrained', 'ACGTAC')])[0].name).toBe('Constrained +1');
        expect(map([site('ThreeChoices', 'BACGT'), site('TwoChoices', 'RACGT')])[0].name).toBe('TwoChoices +1');
        expect(map([site('CciNI', 'GCGGCCGC'), site('NotI', 'GCGGCCGC')])[0].name).toBe('NotI +1');
        expect(map([site('NotI', 'RACGTY'), site('MoreSpecific', 'GACGTC')])[0].name).toBe('MoreSpecific +1');
    });

    it('keeps different lower-strand cuts, uncertainty, unproven geometry and special cases separate', () => {
        const exact = site('Exact', 'GACCTG');
        const rows = [exact,
            site('DifferentBottom', 'GACCTG', { double_strand_events: [{...exact.double_strand_events[0], bottom_boundary: 27, bottom_boundary_unwrapped: 27}] }),
            site('Possible', 'GACCTG', { certainty: 'possible' }),
            site('Unknown', 'GACCTG', { double_strand_events: [] }),
            site('Unknown2', 'GACCTG', { double_strand_events: [] }),
            site('Outside', 'GACCTG', { double_strand_events: [{...exact.double_strand_events[0], status: 'geometry_out_of_bounds', bottom_boundary: null}] }),
            site('BisI', 'GACCTG'),
        ];
        expect(map(rows)).toHaveLength(rows.length);
        expect(map(rows).find(row => row.name === 'BisI')?.type).toBe('restriction_special_case');
    });

    it('does not merge a shared first cut when a second cut or circular winding differs', () => {
        const first = site('One', 'GACCTG');
        const two = site('Two', 'GACCTG', { double_strand_events: [first.double_strand_events[0], {...first.double_strand_events[0], top_boundary: 42, bottom_boundary: 46, top_boundary_unwrapped: 42, bottom_boundary_unwrapped: 46}] });
        const winding = site('Winding', 'GACCTG', { double_strand_events: [{...first.double_strand_events[0], bottom_boundary_unwrapped: 426}] });
        expect(map([first, two, winding])).toHaveLength(3);
    });

    it('groups wrapped sites before display-strand conversion and preserves both source segments', () => {
        const changes: Partial<RestrictionOccurrence> = { site_start: 396, site_end_unwrapped: 402,
            site_segments: [[396, 400], [0, 2]], wraps_origin: true,
            double_strand_events: [{...site('Wrapped', 'ACGTAC').double_strand_events[0], top_boundary: 398, bottom_boundary: 2, top_boundary_unwrapped: 398, bottom_boundary_unwrapped: 402}] };
        const rows = [site('Broad', 'RCGTAY', changes), site('Exact', 'ACGTAC', changes)];
        const annotations = buildRestrictionMapAnnotations({ occurrences: rows, selectedEnzymes: ['Broad', 'Exact'], sequenceLength: 400, sourceDisplayStrand: 'plus', resolvedDisplayStrand: 'minus' });
        expect(annotations.map(({ name, start, end, direction }) => ({name, start, end, direction}))).toEqual([
            {name: 'Exact +1', start: 0, end: 4, direction: -1},
            {name: 'Exact +1', start: 398, end: 400, direction: -1},
        ]);
    });

    it('groups nick labels only on the same strand and boundary', () => {
        const nick = { enzyme_id: 'Nicker', occurrence_id: 'nick', event_ordinal: 0, orientation: 'forward' as const,
            status: 'complete' as const, strand: 'top' as const, boundary: 22, boundary_unwrapped: 22, contributor_group_id: 'nick:22' };
        const rows = [site('Broad', 'RACCTY', {double_strand_events: [], nicks: [nick]}),
            site('Exact', 'GACCTG', {double_strand_events: [], nicks: [nick]}),
            site('OtherStrand', 'GACCTG', {double_strand_events: [], nicks: [{...nick, strand: 'bottom'}]})];
        expect(map(rows).map(row => row.name)).toEqual(['Exact +1', 'OtherStrand']);
        expect(buildNickingMapAnnotations({ occurrences: rows, selectedEnzymes: rows.map(row => row.enzyme_id), sequenceLength: 400,
            sourceDisplayStrand: 'plus', resolvedDisplayStrand: 'plus' }).map(row => row.name)).toEqual(['Exact +1', 'OtherStrand']);
    });

    it.each(['linear', 'circular'] as const)('renders a single %s label and exposes the other matches in its title', async viewer => {
        const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
        const annotations = map([site('Broad', 'RACCTY'), site('Exact', 'GACCTG'), site('Unconstrained', 'NACCTN')]);
        try {
            await act(async () => root.render(<SeqViz seq={'ACGT'.repeat(100)} annotations={annotations} enzymes={[]}
                viewer={viewer} disableExternalFonts testSize={{width: 800, height: 600}} />));
            expect(host.querySelectorAll('path.la-vz-annotation')).toHaveLength(1);
            expect(host.textContent).toContain('Exact +2');
            expect([...host.querySelectorAll('title')].some(title => title.textContent?.includes('Broad (RACCTY)'))).toBe(true);
        } finally { await act(async () => root.unmount()); host.remove(); }
    });
});

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
