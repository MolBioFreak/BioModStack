/** @vitest-environment jsdom */

import React, { StrictMode, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as molecularWorkspaceState from '../../src/components/MolBioToolkit/utils/molecularWorkspaceState';

type RestoreEffectHook = <T>(
    enabled: boolean,
    load: () => Promise<T>,
    publish: (value: T) => void,
) => { restoring: boolean; error: string | null };

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

describe('molecular workspace restoration under React StrictMode', () => {
    it('suppresses stale rejection state and publishes the current replay only', async () => {
        const useRestoreEffect = (
            molecularWorkspaceState as Record<string, unknown>
        ).useMolecularWorkspaceRestoreEffect;
        expect(typeof useRestoreEffect).toBe('function');
        if (typeof useRestoreEffect !== 'function') return;

        let rejectFirst!: (reason: Error) => void;
        let resolveSecond!: (value: string) => void;
        const first = new Promise<string>((_resolve, reject) => { rejectFirst = reject; });
        const second = new Promise<string>((resolve) => { resolveSecond = resolve; });
        const load = vi.fn<() => Promise<string>>()
            .mockReturnValueOnce(first)
            .mockReturnValueOnce(second);
        const publish = vi.fn<(value: string) => void>();
        const useRestore = useRestoreEffect as RestoreEffectHook;

        function Harness() {
            const state = useRestore(true, load, publish);
            return (
                <div
                    data-restoring={String(state.restoring)}
                    data-error={state.error ?? ''}
                />
            );
        }

        await act(async () => {
            root.render(<StrictMode><Harness /></StrictMode>);
            await Promise.resolve();
        });
        expect(load).toHaveBeenCalledTimes(2);
        expect(container.firstElementChild?.getAttribute('data-restoring')).toBe('true');

        await act(async () => {
            rejectFirst(new Error('stale first restore failed'));
            await first.catch(() => undefined);
            await Promise.resolve();
        });
        expect(publish).not.toHaveBeenCalled();
        expect(container.firstElementChild?.getAttribute('data-restoring')).toBe('true');
        expect(container.firstElementChild?.getAttribute('data-error')).toBe('');

        await act(async () => {
            resolveSecond('current second restore');
            await second;
            await Promise.resolve();
        });
        expect(publish).toHaveBeenCalledTimes(1);
        expect(publish).toHaveBeenCalledWith('current second restore');
        expect(container.firstElementChild?.getAttribute('data-restoring')).toBe('false');
        expect(container.firstElementChild?.getAttribute('data-error')).toBe('');
    });
});
