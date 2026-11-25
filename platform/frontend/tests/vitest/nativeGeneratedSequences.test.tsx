import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NativeGeneratedSequences } from '../../src/components/NativeGeneratedSequences';
import type { NativeGenerationRecord } from '../../src/lib/nativeBinderResults';

let root: Root | undefined;
let host: HTMLDivElement | undefined;

afterEach(async () => {
    if (root) await act(async () => root!.unmount());
    host?.remove();
    root = undefined;
    host = undefined;
    vi.restoreAllMocks();
});

async function renderSequences(row: NativeGenerationRecord) {
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    await act(async () => root!.render(<NativeGeneratedSequences row={row} />));
}

describe('NativeGeneratedSequences', () => {
    it('renders polymer-typed DNA/RNA/protein cards, non-canonical CCD badges, verbatim sequence, and copy actions', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });

        await renderSequences({
            id: 'cand-1',
            sequences: [
                { chain_id: 'C', chain_type: 'polypeptide(L)', sequence: 'MSELKAAVEQ' },
                {
                    chain_id: 'A',
                    chain_type: 'polydeoxyribonucleotide',
                    sequence: 'ATGGCTAGC',
                    positions: [
                        { chain_id: 'A', res_id: 1, component_id: 'DA' },
                        { chain_id: 'A', res_id: 9, component_id: '5CM' },
                    ],
                },
                { chain_id: 'R', chain_type: 'polyribonucleotide', sequence: 'AUGCUA' },
            ],
        } as unknown as NativeGenerationRecord);

        expect(host!.textContent).toContain('3 chains');
        expect(host!.textContent).toContain('Chain C');
        expect(host!.textContent).toContain('Protein');
        expect(host!.textContent).toContain('10 aa');
        expect(host!.textContent).toContain('Chain A');
        expect(host!.textContent).toContain('DNA · 5′→3′');
        expect(host!.textContent).toContain('9 nt');
        expect(host!.textContent).toContain('5CM@9');
        expect(host!.textContent).toContain('RNA · 5′→3′');
        expect(host!.textContent).toContain('6 nt');

        const pres = [...host!.querySelectorAll('pre')].map(p => p.textContent);
        expect(pres).toEqual(['MSELKAAVEQ', 'ATGGCTAGC', 'AUGCUA']);

        const buttons = [...host!.querySelectorAll('button')];
        const copyDna = buttons.filter(b => b.textContent === 'Copy')[1];
        const copyDnaFasta = buttons.filter(b => b.textContent === 'Copy FASTA')[1];
        await act(async () => copyDna.click());
        expect(writeText).toHaveBeenCalledWith('ATGGCTAGC');
        await act(async () => copyDnaFasta.click());
        expect(writeText).toHaveBeenCalledWith('>chain_A\nATGGCTAGC');
    });
});
