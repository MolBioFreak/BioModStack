/** @vitest-environment jsdom */

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AlignmentPanel } from '../../src/components/MolBioToolkit/panels/AlignmentPanel';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
    Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    document.body.replaceChildren();
});

describe('Alignment methods documentation', () => {
    it('documents the BMS PairwiseAligner configuration and links maintained Biopython references', async () => {
        await act(async () => {
            root.render(
                <AlignmentPanel
                    sequenceData={{
                        name: 'Circular reference',
                        sequence: 'ACGTACGT',
                        circular: true,
                        sequenceType: 'dna',
                        features: [],
                    }}
                    selection={null}
                    onHighlight={vi.fn()}
                    onAddFeatures={vi.fn()}
                />,
            );
        });

        const methods = Array.from(container.querySelectorAll('details'))
            .find((details) => details.querySelector('summary')?.textContent === 'Methods and documentation');
        expect(methods).toBeDefined();
        expect(methods?.open).toBe(false);

        const documentation = methods?.textContent ?? '';
        expect(documentation).toContain('Bio.Align.PairwiseAligner');
        expect(documentation).toContain('Local uses PairwiseAligner local mode');
        expect(documentation).toContain('Global uses PairwiseAligner global mode');
        expect(documentation).toContain('Placement is a BMS configuration, not a named Biopython algorithm');
        expect(documentation).toContain('terminal gap-open and gap-extension scores set to zero');
        expect(documentation).toContain('Auto compares the forward query and its reverse complement');
        expect(documentation).toContain('Circular local and placement searches can cross the reference origin');
        expect(documentation).toContain('Match 2.0, mismatch -1.0, gap open -6.0, and gap extend -1.0');
        expect(documentation).toContain('Origin-spanning selections cannot be used as a scalar-offset selection reference');

        const links = Array.from(methods?.querySelectorAll('a') ?? []);
        expect(links.map((link) => link.textContent)).toEqual([
            'Biopython PairwiseAligner API',
            'Biopython pairwise alignment tutorial',
        ]);
        expect(links.map((link) => link.getAttribute('href'))).toEqual([
            'https://biopython.org/docs/latest/api/Bio.Align.html#Bio.Align.PairwiseAligner',
            'https://biopython.org/docs/latest/Tutorial/chapter_pairwise.html',
        ]);
        for (const link of links) {
            expect(link.getAttribute('target')).toBe('_blank');
            expect(link.getAttribute('rel')).toBe('noreferrer');
        }
    });
});
