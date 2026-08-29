import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
    DigestPanel,
    getQuickMapEnzymeNames,
    mergeMappedEnzymes,
    toggleQuickMapGroupSelection,
    type QuickMapGroup,
} from '../../src/components/MolBioToolkit/panels/DigestPanel';
import {
    buildNickingMapAnnotations,
    getNickingMarkerGeometry,
    rectanglesOverlap,
    resolveNickingLabelCollisionOffset,
} from '../../src/components/MolBioToolkit/SequenceViewer';
import type { SequenceData } from '../../src/components/MolBioToolkit/types';
import { findRestrictionSiteMatches } from '../../src/components/MolBioToolkit/utils/restrictionEnzymes';

const MOBILE_SEQUENCE: SequenceData = {
    name: 'PL931 mobile fixture',
    sequence: 'AAAACCGGTTTT',
    circular: true,
    sequenceType: 'dna',
    features: [],
    primers: [],
};

const TWO_CUT_MOBILE_SEQUENCE: SequenceData = {
    ...MOBILE_SEQUENCE,
    name: 'Two-cut mobile fixture',
    sequence: 'GGCCAAAAGGCC',
};

describe('DigestPanel mobile workflow', () => {
    let root: Root | undefined;
    let container: HTMLDivElement | undefined;
    const originalFetch = globalThis.fetch;

    beforeEach(() => {
        globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
            fragments: [{
                start: 4,
                end: 4,
                length: 12,
                sequence: MOBILE_SEQUENCE.sequence,
                wraps_origin: true,
            }],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    });

    afterEach(async () => {
        if (root) await act(async () => root?.unmount());
        container?.remove();
        root = undefined;
        container = undefined;
        globalThis.fetch = originalFetch;
        vi.restoreAllMocks();
    });

    async function renderDigest(
        compactLandscape = false,
        sequenceData: SequenceData = MOBILE_SEQUENCE,
        onEnzymesChange = vi.fn(),
        onMapVisibilityRequest = vi.fn(),
    ) {
        if (!root) {
            container = document.createElement('div');
            document.body.appendChild(container);
            root = createRoot(container);
        }
        await act(async () => {
            root?.render(
                <DigestPanel
                    mobile
                    compactLandscape={compactLandscape}
                    sequenceData={sequenceData}
                    sequenceId={null}
                    onHighlight={vi.fn()}
                    selectedEnzymes={[]}
                    onEnzymesChange={onEnzymesChange}
                    onMapVisibilityRequest={onMapVisibilityRequest}
                />,
            );
        });
        return container;
    }

    it('keeps one enzyme scroll region above a sticky touch-safe digest footer', async () => {
        const rendered = await renderDigest();
        const panel = rendered.querySelector<HTMLElement>('[data-digest-layout="mobile"]');
        const scrollRegion = rendered.querySelector<HTMLElement>('[data-digest-scroll-region="enzymes"]');
        const footer = rendered.querySelector<HTMLElement>('[data-digest-mobile-footer="true"]');

        expect(panel).toBeTruthy();
        expect(panel?.className).toContain('h-full');
        expect(scrollRegion).toBeTruthy();
        expect(footer).toBeTruthy();
        expect(scrollRegion?.contains(footer || null)).toBe(false);

        const touchTargets = rendered.querySelectorAll<HTMLElement>('[data-digest-mobile-touch-target="true"]');
        expect(touchTargets.length).toBeGreaterThan(0);
        for (const target of touchTargets) {
            expect(target.className).toContain('min-h-12');
            expect(target.className).toMatch(/\bmin-w-(?:12|20)\b/u);
        }
    });

    it('selects AgeI, runs the digest, and shows the exact fragment without losing the footer', async () => {
        const rendered = await renderDigest();
        const ageIRow = rendered.querySelector<HTMLElement>('[data-enzyme-name="AgeI"]');
        expect(ageIRow).toBeTruthy();
        const addButton = [...(ageIRow?.querySelectorAll('button') || [])]
            .find((button) => button.textContent?.trim() === 'Add');
        expect(addButton).toBeTruthy();

        await act(async () => addButton?.click());
        const runButton = [...rendered.querySelectorAll('button')]
            .find((button) => button.textContent?.includes('Run Digest (1 enzyme)'));
        expect(runButton).toBeTruthy();
        expect(runButton?.disabled).toBe(false);

        await act(async () => {
            runButton?.click();
            await Promise.resolve();
        });

        await vi.waitFor(() => {
            expect(globalThis.fetch).toHaveBeenCalledTimes(1);
            expect(rendered.textContent).toContain('1 exact sequence fragments');
            expect(rendered.textContent).toContain('12 bp');
        });
        const request = vi.mocked(globalThis.fetch).mock.calls[0];
        expect(request[0]).toBe('/api/molbio/digest');
        const body = JSON.parse(String((request[1] as RequestInit).body));
        expect(body.enzymes).toEqual([{ name: 'AgeI', site: 'ACCGGT' }]);
        expect(rendered.querySelector('[data-digest-mobile-footer="true"]')).toBeTruthy();

        const panel = rendered.querySelector<HTMLElement>('[data-digest-layout="mobile"]');
        const enzymeScrollRegion = rendered.querySelector<HTMLElement>('[data-digest-scroll-region="enzymes"]');
        const verticalScrollOwners = [...(panel?.querySelectorAll<HTMLElement>('*') || [])]
            .filter((element) => element.className.includes('overflow-y-auto'));
        expect(verticalScrollOwners).toEqual([enzymeScrollRegion]);
    });

    it('keeps the complete digest workflow visible in compact landscape', async () => {
        const rendered = await renderDigest(true);
        const panel = rendered.querySelector<HTMLElement>('[data-digest-layout="mobile"]');
        expect(panel?.getAttribute('data-digest-compact-landscape')).toBe('true');
        expect(rendered.querySelector('[data-digest-mobile-sticky-search="true"] input')).toBeTruthy();
        expect(rendered.querySelector('[data-digest-mobile-filter-bar="true"]')).toBeNull();
        expect(rendered.querySelector('[data-digest-mobile-list-heading="true"]')).toBeNull();
        expect(rendered.textContent).not.toContain('Restriction Analysis');

        const ageIRow = rendered.querySelector<HTMLElement>('[data-enzyme-name="AgeI"]');
        const addButton = [...(ageIRow?.querySelectorAll('button') || [])]
            .find((button) => button.textContent?.trim() === 'Add');
        await act(async () => addButton?.click());

        const footer = rendered.querySelector<HTMLElement>('[data-digest-mobile-footer="true"]');
        expect(footer?.getAttribute('data-digest-compact-landscape')).toBe('true');
        const runButton = [...(footer?.querySelectorAll('button') || [])]
            .find((button) => button.textContent?.includes('Run Digest (1 enzyme)'));
        expect(runButton).toBeTruthy();
        expect(runButton?.className).toContain('min-h-12');

        await act(async () => {
            runButton?.click();
            await Promise.resolve();
        });
        await vi.waitFor(() => expect(rendered.textContent).toContain('12 bp'));
    });

    it('drops a stale Digest completion after a keyed construct replacement', async () => {
        let resolveResponse: ((response: Response) => void) | undefined;
        globalThis.fetch = vi.fn(() => new Promise<Response>((resolve) => {
            resolveResponse = resolve;
        }));
        const oldHighlight = vi.fn();
        const newHighlight = vi.fn();
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
        const panel = (key: string, sequenceData: SequenceData, onHighlight: typeof oldHighlight) => (
            <DigestPanel
                key={key}
                mobile
                sequenceData={sequenceData}
                sequenceId={null}
                onHighlight={onHighlight}
                selectedEnzymes={[]}
                onEnzymesChange={vi.fn()}
            />
        );
        await act(async () => root?.render(panel('sequence-a', MOBILE_SEQUENCE, oldHighlight)));
        const ageIRow = container.querySelector<HTMLElement>('[data-enzyme-name="AgeI"]');
        const addButton = [...(ageIRow?.querySelectorAll('button') || [])]
            .find((button) => button.textContent?.trim() === 'Add');
        await act(async () => addButton?.click());
        const runButton = [...container.querySelectorAll('button')]
            .find((button) => button.textContent?.includes('Run Digest (1 enzyme)'));
        await act(async () => runButton?.click());
        expect(globalThis.fetch).toHaveBeenCalledTimes(1);

        await act(async () => root?.render(panel('sequence-b', TWO_CUT_MOBILE_SEQUENCE, newHighlight)));
        await act(async () => {
            resolveResponse?.(new Response(JSON.stringify({
                fragments: [{ start: 4, end: 4, length: 12, sequence: MOBILE_SEQUENCE.sequence }],
            }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(oldHighlight).not.toHaveBeenCalled();
        expect(newHighlight).not.toHaveBeenCalled();
    });

    it('neutralizes hidden cut and class filters in compact landscape', async () => {
        const rendered = await renderDigest(false, TWO_CUT_MOBILE_SEQUENCE);
        const clickFilter = async (label: string) => {
            const button = [...rendered.querySelectorAll('button')]
                .find((candidate) => candidate.textContent?.trim() === label);
            expect(button).toBeTruthy();
            await act(async () => button?.click());
        };
        await clickFilter('3x+');
        await clickFilter('Nicking');
        expect(rendered.querySelector('[data-enzyme-name="HaeIII"]')).toBeNull();

        await renderDigest(true, TWO_CUT_MOBILE_SEQUENCE);
        const haeIII = rendered.querySelector<HTMLElement>('[data-enzyme-name="HaeIII"]');
        expect(haeIII).toBeTruthy();
        expect(haeIII?.textContent).toContain('2 cuts');
    });

    it('adds quick-map cutter groups without replacing existing mapped enzymes', () => {
        const enzymes = [
            { name: 'Single', site: 'AAAA', category: 'common' as const, cuts: [1], selectionCuts: 0 },
            { name: 'Double', site: 'CCCC', category: 'common' as const, cuts: [1, 5], selectionCuts: 0 },
            { name: 'More', site: 'GGGG', category: 'common' as const, cuts: [1, 5, 9], selectionCuts: 0 },
            { name: 'NickTop', site: 'CCTCAGC', category: 'nicking' as const, cuts: [3], selectionCuts: 0, nickingStrand: 'top' as const },
            { name: 'TypeIIS', site: 'GGTCTC', category: 'golden_gate' as const, cuts: [7], selectionCuts: 0, tags: ['type_iis'] },
            { name: 'ZeroTypeIIS', site: 'GAAGAC', category: 'golden_gate' as const, cuts: [], selectionCuts: 0, tags: ['type_iis'] },
        ];

        expect(getQuickMapEnzymeNames(enzymes, 'unique')).toEqual(['Single', 'NickTop', 'TypeIIS']);
        expect(getQuickMapEnzymeNames(enzymes, 'double')).toEqual(['Double']);
        expect(getQuickMapEnzymeNames(enzymes, 'three_plus')).toEqual(['More']);
        expect(getQuickMapEnzymeNames(enzymes, 'nicking')).toEqual(['NickTop']);
        expect(getQuickMapEnzymeNames(enzymes, 'type_iis')).toEqual(['TypeIIS']);
        expect(mergeMappedEnzymes(['Existing', 'Single'], ['Single', 'TypeIIS'])).toEqual([
            'Existing',
            'Single',
            'TypeIIS',
        ]);
    });

    it('keeps overlapping enzymes until every active quick-map owner is deselected', () => {
        const enzymes = [
            { name: 'Single', site: 'AAAA', category: 'common' as const, cuts: [1], selectionCuts: 0 },
            { name: 'TypeIIS', site: 'GGTCTC', category: 'golden_gate' as const, cuts: [2], selectionCuts: 0, tags: ['type_iis'] },
        ];
        const manual = new Set(['Existing']);
        let state: { selectedEnzymes: string[]; activeGroups: Set<QuickMapGroup> } = {
            selectedEnzymes: ['Existing'],
            activeGroups: new Set(),
        };

        state = toggleQuickMapGroupSelection(state, manual, enzymes, 'unique');
        expect(state.selectedEnzymes).toEqual(['Existing', 'Single', 'TypeIIS']);
        state = toggleQuickMapGroupSelection(state, manual, enzymes, 'type_iis');
        state = toggleQuickMapGroupSelection(state, manual, enzymes, 'unique');
        expect(state.selectedEnzymes).toEqual(['Existing', 'TypeIIS']);
        state = toggleQuickMapGroupSelection(state, manual, enzymes, 'type_iis');
        expect(state.selectedEnzymes).toEqual(['Existing']);
    });

    it('turns on cut-site visibility when a quick-map group is added', async () => {
        const onEnzymesChange = vi.fn();
        const onMapVisibilityRequest = vi.fn();
        const rendered = await renderDigest(false, MOBILE_SEQUENCE, onEnzymesChange, onMapVisibilityRequest);
        const quickMap = rendered.querySelector('[data-digest-quick-map="true"]');
        const uniqueButton = [...(quickMap?.querySelectorAll('button') || [])]
            .find((button) => button.textContent?.trim() === '1x');
        expect(uniqueButton).toBeTruthy();

        await act(async () => uniqueButton?.click());
        expect(onMapVisibilityRequest).toHaveBeenCalledTimes(1);
        expect(onEnzymesChange).toHaveBeenCalledTimes(1);
        expect(onEnzymesChange.mock.calls[0][0]).toContain('AgeI');
    });

    it('renders top nick sites outside and bottom nick sites inside with orientation-aware directions', () => {
        expect(findRestrictionSiteMatches('AACCTCAGCTT', 'CCTCAGC', false)).toEqual([
            { position: 2, orientation: 1 },
        ]);
        expect(findRestrictionSiteMatches('AAGCTGAGGTT', 'CCTCAGC', false)).toEqual([
            { position: 2, orientation: -1 },
        ]);

        const annotations = buildNickingMapAnnotations({
            sequence: 'CCTCAGCAAAAGCTGAGG',
            circular: false,
            selectedEnzymes: ['Nt.BbvCI', 'Nb.BbvCI'],
            sourceDisplayStrand: 'plus',
            resolvedDisplayStrand: 'plus',
        });
        expect(annotations.map((annotation) => ({
            name: annotation.name,
            start: annotation.start,
            direction: annotation.direction,
        }))).toEqual([
            { name: 'Nt.BbvCI', start: 0, direction: 1 },
            { name: 'Nt.BbvCI', start: 11, direction: -1 },
            { name: 'Nb.BbvCI', start: 0, direction: -1 },
            { name: 'Nb.BbvCI', start: 11, direction: 1 },
        ]);

        const top = getNickingMarkerGeometry({
            position: 0,
            direction: 1,
            sequenceLength: 100,
            centralIndex: 0,
            width: 400,
            height: 400,
            viewerMode: 'circular',
        });
        const bottom = getNickingMarkerGeometry({
            position: 0,
            direction: -1,
            sequenceLength: 100,
            centralIndex: 0,
            width: 400,
            height: 400,
            viewerMode: 'circular',
        });
        expect(top.radius).toBeGreaterThan(top.plasmidRadius);
        expect(bottom.radius).toBeLessThan(bottom.plasmidRadius);
        expect(top.lineStartX).toBeCloseTo(bottom.lineStartX, 4);
        expect(top.lineStartY).toBeCloseTo(bottom.lineStartY, 4);
        expect(top.textX).toBeCloseTo(200, 4);
        expect(top.textY).toBeLessThan(bottom.textY);
        expect(top.textAnchor).toBe('start');
    });

    it('moves nick labels clear of existing SeqViz text boxes', () => {
        const label = { x: 100, y: 100, width: 72, height: 16 };
        const obstacle = { x: 96, y: 96, width: 80, height: 24 };
        const offset = resolveNickingLabelCollisionOffset({
            label,
            obstacles: [obstacle],
            tangent: { x: 0, y: 1 },
            radial: { x: 1, y: 0 },
            bounds: { width: 400, height: 400 },
        });
        const shifted = { ...label, x: label.x + offset.x, y: label.y + offset.y };
        expect(offset).not.toEqual({ x: 0, y: 0 });
        expect(rectanglesOverlap(shifted, obstacle, 2)).toBe(false);
    });
});
