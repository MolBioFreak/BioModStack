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
}

const camera = vi.hoisted(() => ({
    pending: [] as PendingFrame[],
}));

vi.mock('../../src/lib/bioxpClient', () => ({
    useBioXpCameraStatus: () => ({
        data: { available: true, detail: null },
        isError: false,
        error: null,
    }),
    fetchBioXpCameraFrame: (generation: number) => new Promise<CameraImage>((resolve) => {
        camera.pending.push({ generation, resolve });
    }),
    captureBioXpCameraSnapshot: (generation: number) => new Promise<CameraImage>((resolve) => {
        camera.pending.push({ generation, resolve });
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

beforeEach(() => {
    camera.pending.length = 0;
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
});

describe('mounted BioXP camera URL ownership', () => {
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
});
