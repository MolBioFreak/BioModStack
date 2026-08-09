import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

interface CameraImage {
    blob: Blob;
    connectionGeneration: number;
}

interface PendingFrame {
    generation: number;
    resolve: (image: CameraImage) => void;
    reject: (error: Error) => void;
}

const camera = vi.hoisted(() => ({
    pending: [] as PendingFrame[],
    status: {
        data: {
            state: 'live' as const,
            available: true,
            frame_sequence: 1 as number | null,
            frame_age_seconds: 0 as number | null,
            freshness_budget_seconds: 30,
            provider_generation: 1,
            detail: null as string | null,
        },
        dataUpdatedAt: Date.now(),
        isError: false,
        error: null as unknown | null,
        refetch: vi.fn(async () => undefined),
    },
}));

vi.mock('../../src/lib/bioxpClient', () => ({
    useBioXpCameraStatus: () => camera.status,
    fetchBioXpCameraFrame: (generation: number) => new Promise<CameraImage>((resolve, reject) => {
        camera.pending.push({ generation, resolve, reject });
    }),
    captureBioXpCameraSnapshot: (generation: number) => new Promise<CameraImage>((resolve, reject) => {
        camera.pending.push({ generation, resolve, reject });
    }),
    bioXpErrorText: (error: unknown) => error instanceof Error ? error.message : String(error),
}));

import { BioXpCameraPanel } from '../../src/components/BioXpCameraPanel';

let container: HTMLDivElement;
let root: Root;
let nextUrl: number;
let createObjectURL: ReturnType<typeof vi.fn>;
let revokeObjectURL: ReturnType<typeof vi.fn>;

const button = (label: string) => Array.from(container.querySelectorAll('button'))
    .find((element) => element.textContent === label) as HTMLButtonElement | undefined;

async function renderPanel(generation: number) {
    await act(async () => {
        root.render(<BioXpCameraPanel connected connectionGeneration={generation} mutationEnabled />);
        await Promise.resolve();
    });
}

async function resolveNext(expectedGeneration: number) {
    const request = camera.pending.shift();
    expect(request?.generation).toBe(expectedGeneration);
    await act(async () => {
        request!.resolve({ blob: new Blob([String(expectedGeneration)]), connectionGeneration: expectedGeneration });
        await Promise.resolve();
    });
}

async function rejectNext(expectedGeneration: number, error: Error) {
    const request = camera.pending.shift();
    expect(request?.generation).toBe(expectedGeneration);
    await act(async () => {
        request!.reject(error);
        await Promise.resolve();
    });
}

beforeEach(() => {
    camera.pending.length = 0;
    camera.status.data = {
        state: 'live',
        available: true,
        frame_sequence: 1,
        frame_age_seconds: 0,
        freshness_budget_seconds: 30,
        provider_generation: 1,
        detail: null,
    };
    camera.status.dataUpdatedAt = Date.now();
    camera.status.isError = false;
    camera.status.error = null;
    camera.status.refetch.mockReset();
    camera.status.refetch.mockResolvedValue(undefined);
    nextUrl = 1;
    createObjectURL = vi.fn(() => `blob:test-${nextUrl++}`);
    revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    document.body.replaceChildren();
    camera.pending.length = 0;
    Reflect.deleteProperty(URL, 'createObjectURL');
    Reflect.deleteProperty(URL, 'revokeObjectURL');
    vi.useRealTimers();
});

describe('mounted BioXP camera URL ownership', () => {
    it('does not display Ready from retained status data after a refetch error', async () => {
        camera.status.isError = true;
        camera.status.error = new Error('camera status refetch failed');
        await renderPanel(1);

        expect(container.textContent).toContain('Unavailable');
        expect(container.textContent).toContain('camera status refetch failed');
        expect(container.textContent).not.toContain('Ready');
    });

    it('rejects stale completions, revokes replacements, and disposes the live frame', async () => {
        await renderPanel(1);
        await act(async () => button('Refresh')!.click());
        expect(camera.pending).toHaveLength(1);

        await renderPanel(2);
        await resolveNext(1);
        expect(container.querySelector('img')).toBeNull();
        expect(revokeObjectURL).toHaveBeenCalledWith('blob:test-1');

        await act(async () => button('Refresh')!.click());
        await resolveNext(2);
        expect(container.querySelector('img')?.getAttribute('src')).toBe('blob:test-2');

        await act(async () => button('Refresh')!.click());
        await resolveNext(2);
        expect(container.querySelector('img')?.getAttribute('src')).toBe('blob:test-3');
        expect(revokeObjectURL).toHaveBeenCalledWith('blob:test-2');

        await act(async () => root.unmount());
        expect(revokeObjectURL).toHaveBeenCalledWith('blob:test-3');
        root = createRoot(container);
        expect(createObjectURL).toHaveBeenCalledTimes(3);
    });

    it('advances a live presentation to stale with a local expiry timer', async () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date('2026-08-08T20:00:00Z'));
        camera.status.data.freshness_budget_seconds = 1;
        camera.status.dataUpdatedAt = Date.now();
        await renderPanel(1);
        expect(container.textContent).toContain('Ready');

        await act(async () => vi.advanceTimersByTimeAsync(1_002));

        expect(container.textContent).toContain('Stale');
        expect(container.textContent).not.toContain('Ready');
    });

    it('does not let an old status refetch clear a newer camera request', async () => {
        let finishOldRefetch: (() => void) | undefined;
        camera.status.refetch.mockImplementationOnce(() => new Promise<void>((resolve) => {
            finishOldRefetch = resolve;
        }));
        await renderPanel(1);
        await act(async () => button('Refresh')!.click());
        await resolveNext(1);

        await renderPanel(2);
        await act(async () => button('Refresh')!.click());
        expect(button('Loading…')).toBeDefined();
        await act(async () => {
            finishOldRefetch?.();
            await Promise.resolve();
        });
        expect(button('Loading…')).toBeDefined();

        await resolveNext(2);
        expect(button('Refresh')).toBeDefined();
    });

    it('refetches camera status after a failed user-triggered frame read', async () => {
        await renderPanel(1);
        await act(async () => button('Refresh')!.click());
        await rejectNext(1, new Error('frame read failed'));

        expect(camera.status.refetch).toHaveBeenCalledTimes(1);
        expect(container.textContent).toContain('frame read failed');
        expect(button('Refresh')).toBeDefined();
    });
});
