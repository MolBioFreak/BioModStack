// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';

import { AlignmentPresentationStatus } from '../../src/components/ngs/AlignmentPresentationStatus';

Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });

afterEach(() => { document.body.innerHTML = ''; });

describe('AlignmentPresentationStatus', () => {
    it('keeps the operator-facing presentation scope concise', async () => {
        const container = document.createElement('div');
        document.body.append(container);
        const root = createRoot(container);
        await act(async () => root.render(<AlignmentPresentationStatus status={{
            kind: 'preview', sourceSizeBytes: 818_274_983, selectedReadCount: 2000,
            availableReadCount: 10_000, byteSize: 4_194_304, policyVersion: '1.2.3', capped: false,
        }} />));
        expect(container.textContent).toContain('Read preview');
        expect(container.textContent).toContain('2,000 of 10,000 reads');
        expect(container.textContent).not.toMatch(/scientific authority|track |source |policy|capped/i);

        await act(async () => root.render(<AlignmentPresentationStatus status={{
            kind: 'locus', sourceSizeBytes: 818_274_983, selectedReadCount: 5000,
            availableReadCount: 7000, byteSize: 8_388_608, policyVersion: '1.2.3', capped: true,
        }} />));
        expect(container.textContent).toContain('Locus reads');
        expect(container.textContent).toContain('5,000 of 7,000 reads');

        await act(async () => root.render(<AlignmentPresentationStatus status={{
            kind: 'full', sourceSizeBytes: 65_536, selectedReadCount: null,
            availableReadCount: null, byteSize: 65_536, policyVersion: null, capped: false,
        }} />));
        expect(container.textContent).toContain('Reads · all reads');
        expect(container.textContent).not.toMatch(/source|policy|capped/i);
        await act(async () => root.unmount());
    });
});
