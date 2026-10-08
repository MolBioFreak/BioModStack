import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
    fetchNucleotideSequence: vi.fn(),
}));

vi.mock('../../src/lib/api', () => ({
    fetchNucleotideSequences: vi.fn(),
    fetchNucleotideSequence: apiMocks.fetchNucleotideSequence,
    createNucleotideSequence: vi.fn(),
    updateNucleotideSequence: vi.fn(),
    deleteNucleotideSequence: vi.fn(),
}));

import { useSequenceOperations } from '../../src/components/MolBioToolkit/hooks/useSequenceOperations';

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

describe('useSequenceOperations newest-selection ownership', () => {
    let root: Root | undefined;
    let container: HTMLDivElement | undefined;
    let operations: ReturnType<typeof useSequenceOperations>;

    function Harness() {
        operations = useSequenceOperations();
        return (
            <div
                data-loading={String(operations.loading)}
                data-error={operations.error ?? ''}
            />
        );
    }

    afterEach(async () => {
        if (root) await act(async () => root?.unmount());
        container?.remove();
        root = undefined;
        container = undefined;
        apiMocks.fetchNucleotideSequence.mockReset();
    });

    async function mountHarness() {
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
        await act(async () => root?.render(<Harness />));
    }

    it('publishes only the newest sequence result and ignores an older failure', async () => {
        const olderSuccess = deferred<{ data: { id: string; name: string } }>();
        const newerSuccess = deferred<{ data: { id: string; name: string } }>();
        apiMocks.fetchNucleotideSequence
            .mockImplementationOnce(() => olderSuccess.promise)
            .mockImplementationOnce(() => newerSuccess.promise);
        await mountHarness();

        let olderResultPromise!: ReturnType<typeof operations.getSequence>;
        let newerResultPromise!: ReturnType<typeof operations.getSequence>;
        await act(async () => {
            olderResultPromise = operations.getSequence('older');
            newerResultPromise = operations.getSequence('newer');
        });

        newerSuccess.resolve({ data: { id: 'newer', name: 'Newer construct' } });
        let newerResult: Awaited<ReturnType<typeof operations.getSequence>>;
        await act(async () => { newerResult = await newerResultPromise; });
        expect(newerResult?.id).toBe('newer');
        expect(operations.error).toBeNull();
        expect(operations.loading).toBe(false);

        olderSuccess.resolve({ data: { id: 'older', name: 'Older construct' } });
        let olderResult: Awaited<ReturnType<typeof operations.getSequence>>;
        await act(async () => { olderResult = await olderResultPromise; });
        expect(olderResult).toBeNull();
        expect(operations.error).toBeNull();
        expect(operations.loading).toBe(false);

        const staleFailure = deferred<{ data: { id: string; name: string } }>();
        const currentSuccess = deferred<{ data: { id: string; name: string } }>();
        apiMocks.fetchNucleotideSequence
            .mockImplementationOnce(() => staleFailure.promise)
            .mockImplementationOnce(() => currentSuccess.promise);

        let staleFailurePromise!: ReturnType<typeof operations.getSequence>;
        let currentSuccessPromise!: ReturnType<typeof operations.getSequence>;
        await act(async () => {
            staleFailurePromise = operations.getSequence('stale-failure');
            currentSuccessPromise = operations.getSequence('current');
        });
        currentSuccess.resolve({ data: { id: 'current', name: 'Current construct' } });
        await act(async () => { await currentSuccessPromise; });
        staleFailure.reject(new Error('stale network failure'));
        await act(async () => { await staleFailurePromise; });

        expect(operations.error).toBeNull();
        expect(operations.loading).toBe(false);
        expect(container?.querySelector('[data-error]')?.getAttribute('data-error')).toBe('');

        const invalidatedFailure = deferred<{ data: { id: string; name: string } }>();
        apiMocks.fetchNucleotideSequence.mockImplementationOnce(() => invalidatedFailure.promise);
        let invalidatedPromise!: ReturnType<typeof operations.getSequence>;
        await act(async () => { invalidatedPromise = operations.getSequence('superseded-by-open-workspace'); });
        await act(async () => { operations.invalidateGetSequence(); });
        invalidatedFailure.reject(new Error('obsolete request failed'));
        await act(async () => { await invalidatedPromise; });

        expect(operations.error).toBeNull();
        expect(operations.loading).toBe(false);
    });
});
