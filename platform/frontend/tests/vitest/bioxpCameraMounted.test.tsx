import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import producerFixture from '../fixtures/bioxpCameraProducer.json';

interface CameraImage {
    blob: Blob;
    connectionGeneration: number;
}

interface PendingFrame {
    generation: number;
    resolve: (image: CameraImage) => void;
    reject: (error: Error) => void;
}

interface StreamState {
    schema_version: 'bioxp.camera_stream.v1';
    state: 'off' | 'starting' | 'live' | 'error';
    active: boolean;
    connection_generation: number;
}

const camera = vi.hoisted(() => ({
    pending: [] as PendingFrame[],
    status: {
        data: {
            state: 'live' as 'live' | 'stale' | 'unavailable',
            available: true,
            frame_sequence: 1 as number | null,
            frame_age_seconds: 0 as number | null,
            freshness_budget_seconds: 30,
            provider_generation: 1,
            requestConnectionGeneration: 1,
            detail: null as string | null,
        },
        dataUpdatedAt: Date.now(),
        isError: false,
        error: null as unknown | null,
        refetch: vi.fn(async (): Promise<void> => undefined),
    },
    stream: {
        data: {
            schema_version: 'bioxp.camera_stream.v1' as const,
            state: 'off' as const,
            active: false,
            connection_generation: 1,
        } as StreamState,
        isError: false,
        error: null as unknown | null,
        refetch: vi.fn(async (): Promise<void> => undefined),
    },
    startStream: vi.fn(async (generation: number): Promise<StreamState> => ({
        schema_version: 'bioxp.camera_stream.v1' as const,
        state: 'live' as const,
        active: true,
        connection_generation: generation,
    })),
    stopStream: vi.fn(async (generation: number): Promise<StreamState> => ({
        schema_version: 'bioxp.camera_stream.v1' as const,
        state: 'off' as const,
        active: false,
        connection_generation: generation,
    })),
}));

vi.mock('../../src/lib/bioxpClient', () => ({
    useBioXpCameraStatus: () => camera.status,
    useBioXpCameraStreamState: () => camera.stream,
    startBioXpCameraStream: camera.startStream,
    stopBioXpCameraStream: camera.stopStream,
    buildBioXpCameraMjpegUrl: (generation: number) => `/api/bioxp/camera/mjpeg?expected_generation=${generation}`,
    fetchBioXpCameraFrame: (generation: number) => new Promise<CameraImage>((resolve, reject) => {
        camera.pending.push({ generation, resolve, reject });
    }),
    captureBioXpCameraSnapshot: (generation: number) => new Promise<CameraImage>((resolve, reject) => {
        camera.pending.push({ generation, resolve, reject });
    }),
    bioXpErrorText: (error: unknown) => error instanceof Error ? error.message : String(error),
}));

import { BioXpCameraPanel } from '../../src/components/BioXpCameraPanel';

// Actual offline reader output (synthetic JPEG/process inputs), not hardware
// evidence. Strict BMS source bodies produced these projections; these mocked
// query hooks deliberately do NOT claim HTTP/lease integration coverage.
async function renderProducerRow(label: string, generation = 1) {
    const row = producerFixture.rows.find((entry) => entry.label === label)!;
    expect(row).toBeDefined();
    const status = row.status;
    camera.status.data = Object.assign({}, camera.status.data, status, {
        requestConnectionGeneration: generation,
    });
    camera.status.dataUpdatedAt = Date.now();
    camera.stream.data = { ...row.stream, connection_generation: generation } as StreamState;
    await renderPanel(generation);
}


let container: HTMLDivElement;
let root: Root;
let nextUrl: number;
let createObjectURL: ReturnType<typeof vi.fn>;
let revokeObjectURL: ReturnType<typeof vi.fn>;

const button = (label: string) => Array.from(container.querySelectorAll('button'))
    .find((element) => element.textContent === label) as HTMLButtonElement | undefined;

async function renderPanel(generation: number, connected = true) {
    await act(async () => {
        root.render(<BioXpCameraPanel connected={connected} connectionGeneration={generation} mutationEnabled />);
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
            requestConnectionGeneration: 1,
        detail: null,
    };
    camera.status.dataUpdatedAt = Date.now();
    camera.status.isError = false;
    camera.status.error = null;
    camera.status.refetch.mockReset();
    camera.status.refetch.mockResolvedValue(undefined);
    camera.stream.data = {
        schema_version: 'bioxp.camera_stream.v1',
        state: 'off',
        active: false,
        connection_generation: 1,
    };
    camera.stream.isError = false;
    camera.stream.error = null;
    camera.stream.refetch.mockReset();
    camera.stream.refetch.mockResolvedValue(undefined);
    camera.startStream.mockClear();
    camera.stopStream.mockClear();
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
    it('renders actual reader→strict BMS projection fixtures and locally expires frozen producer evidence', async () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date('2026-09-07T20:00:00Z'));
        expect(producerFixture.rows.map(row => row.label)).toEqual(['starting', 'first', 'advancing', 'invalid', 'stopped']);
        const labels = ['Unavailable', 'Waiting for advancing frames', 'Video live', 'Unavailable', 'Unavailable'];
        for (const [index, row] of producerFixture.rows.entries()) {
            // Values were emitted by the real worker reader with fake ffmpeg,
            // then validated/projected by the unchanged strict BMS source bodies.
            // This local tag is supplied by useBioXpCameraStatus, not the producer.
            camera.status.data = { ...row.status, state: row.status.state as typeof camera.status.data.state, requestConnectionGeneration: 1 };
            camera.status.dataUpdatedAt = Date.now();
            camera.stream.data = row.stream as StreamState;
            await renderPanel(1);
            expect(container.textContent).toContain(labels[index]);
            if (row.label === 'advancing') {
                expect(row.status.frame_sequence).toBe(2);
                await act(async () => vi.advanceTimersByTimeAsync(30_001));
                expect(container.textContent).toContain('Stale');
                expect(container.textContent).not.toContain('Video live');
            }
        }
        expect(container.querySelector('img')).toBeNull();
    });
    it('renders actual offline producer start, advancing, invalid and stopped evidence', async () => {
        vi.useFakeTimers();
        await renderProducerRow('starting');
        expect(container.textContent).not.toContain('Video live');
        await renderProducerRow('first');
        expect(container.textContent).not.toContain('Video live');
        await renderProducerRow('advancing');
        expect(container.textContent).toContain('Video live');
        await renderProducerRow('invalid');
        expect(container.textContent).not.toContain('Video live');
        expect(container.textContent).toContain('Unavailable');
        await renderProducerRow('stopped');
        expect(container.textContent).not.toContain('Video live');
        expect(camera.startStream).not.toHaveBeenCalled();
        expect(camera.stopStream).not.toHaveBeenCalled();
    });

    it('expires actual producer evidence without polling and rejects it after reconnect', async () => {
        vi.useFakeTimers();
        await renderProducerRow('first');
        await renderProducerRow('advancing');
        expect(container.textContent).toContain('Video live');
        const status = producerFixture.rows.find((row) => row.label === 'advancing')!.status;
        const remainingMs = (status.freshness_budget_seconds - status.frame_age_seconds!) * 1000;
        await act(async () => vi.advanceTimersByTimeAsync(remainingMs - 1));
        expect(container.textContent).toContain('Video live');
        await act(async () => vi.advanceTimersByTimeAsync(2));
        expect(container.textContent).not.toContain('Video live');
        expect(container.textContent).toContain('Stale');
        await renderPanel(2);
        expect(container.textContent).not.toContain('Video live');
        await renderProducerRow('first', 2);
        expect(container.textContent).not.toContain('Video live');
        await renderProducerRow('advancing', 2);
        expect(container.textContent).toContain('Video live');
    });
    const streamLive = () => { camera.stream.data = { ...camera.stream.data, state: 'live', active: true }; };
    const advanceFrame = async (sequence: number | null, provider = 1) => {
        camera.status.data = { ...camera.status.data, frame_sequence: sequence, provider_generation: provider, frame_age_seconds: 0 };
        camera.status.dataUpdatedAt = Date.now();
        await renderPanel(1);
    };

    it('requires sequence progress and expires a frozen active stream despite refreshed frame age', async () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date('2026-08-08T20:00:00Z'));
        camera.status.dataUpdatedAt = Date.now();
        camera.status.data.freshness_budget_seconds = 1;
        streamLive();
        await renderPanel(1);
        expect(container.textContent).toContain('Waiting for advancing frames');
        await advanceFrame(2);
        expect(container.textContent).toContain('Video live');
        await act(async () => vi.advanceTimersByTimeAsync(500));
        await advanceFrame(2);
        await act(async () => vi.advanceTimersByTimeAsync(500));
        expect(container.textContent).toContain('Stale');
        expect(container.textContent).not.toContain('Video live');
        await advanceFrame(3);
        expect(container.textContent).toContain('Video live');
    });

    it.each(['status', 'stream', 'image', 'server'])('never labels failed %s evidence Video live', async (failure) => {
        streamLive();
        await renderPanel(1);
        await advanceFrame(2);
        expect(container.textContent).toContain('Video live');
        if (failure === 'status') camera.status.isError = true;
        if (failure === 'stream') camera.stream.isError = true;
        if (failure === 'server') camera.stream.data = { ...camera.stream.data, state: 'error' };
        if (failure === 'image') await act(async () => container.querySelector('img')!.dispatchEvent(new Event('error')));
        await renderPanel(1);
        expect(container.textContent).toContain('Unavailable');
        expect(container.textContent).not.toContain('Video live');
    });

    it('does not treat missing, regressing or new-provider sequence as advancing', async () => {
        streamLive();
        await renderPanel(1);
        await advanceFrame(5);
        expect(container.textContent).toContain('Video live');
        for (const sequence of [null, 4, 5]) {
            await advanceFrame(sequence);
            expect(container.textContent).not.toContain('Video live');
        }
        await advanceFrame(6);
        expect(container.textContent).toContain('Video live');
        await advanceFrame(100, 2);
        expect(container.textContent).not.toContain('Video live');
        await advanceFrame(101, 2);
        expect(container.textContent).toContain('Video live');
    });

    it.each(['start', 'stop'])('rejects old-generation %s success without clearing a newer pending request', async (operation) => {
        if (operation === 'stop') streamLive();
        let finishOld!: (value: StreamState) => void;
        let finishNew!: (value: StreamState) => void;
        const oldMock = operation === 'start' ? camera.startStream : camera.stopStream;
        oldMock.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }));
        await renderPanel(1);
        await act(async () => button(operation === 'start' ? 'Video' : 'Stop Video')!.click());
        await renderPanel(2);
        camera.startStream.mockImplementationOnce(() => new Promise(resolve => { finishNew = resolve; }));
        await act(async () => button('Video')!.click());
        await act(async () => finishOld({ schema_version: 'bioxp.camera_stream.v1', state: operation === 'start' ? 'live' : 'off', active: operation === 'start', connection_generation: 1 }));
        expect(button('Working…')).toBeDefined();
        expect(container.querySelector('img')).toBeNull();
        await act(async () => finishNew({ schema_version: 'bioxp.camera_stream.v1', state: 'live', active: true, connection_generation: 2 }));
        expect(button('Stop Video')).toBeDefined();
        expect(container.querySelector('img')?.getAttribute('src')).toContain('expected_generation=2');
    });

    it('ignores late stream failure after disconnect and never resurrects retained active media', async () => {
        let fail!: (error: Error) => void;
        camera.startStream.mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }));
        await renderPanel(1);
        await act(async () => button('Video')!.click());
        await renderPanel(1, false);
        await act(async () => fail(new Error('old start failed')));
        expect(container.textContent).toContain('Disconnected');
        expect(container.textContent).not.toContain('old start failed');
        expect(container.querySelector('img')).toBeNull();
        streamLive();
        await renderPanel(2);
        expect(container.querySelector('img')).toBeNull();
        expect(button('Video')).toBeDefined();
    });

    it('stops retained active state, prevents same-tick duplicate starts and releases media on unmount', async () => {
        streamLive();
        await renderPanel(1);
        await act(async () => button('Stop Video')!.click());
        expect(container.querySelector('img')).toBeNull();
        expect(button('Video')).toBeDefined();
        camera.stream.data = { ...camera.stream.data }; // late pre-Stop active poll
        await renderPanel(1);
        expect(container.querySelector('img')).toBeNull();
        expect(button('Video')).toBeDefined();
        await act(async () => { button('Video')!.click(); button('Video')!.click(); });
        expect(camera.startStream).toHaveBeenCalledTimes(1);
        expect(container.querySelector('img')).not.toBeNull();
        await act(async () => root.unmount());
        expect(container.querySelector('img')).toBeNull();
        root = createRoot(container);
    });

    it('discards an old-generation snapshot and does not clear the replacement capture', async () => {
        await renderPanel(1);
        await act(async () => button('Capture')!.click());
        await renderPanel(2);
        await act(async () => button('Capture')!.click());
        await resolveNext(1);
        expect(button('Capturing…')).toBeDefined();
        expect(container.querySelector('img')).toBeNull();
        expect(createObjectURL).not.toHaveBeenCalled();
        await resolveNext(2);
        expect(container.querySelector('img')?.getAttribute('src')).toBe('blob:test-1');
    });
    it('rejects retained and late old-connection status/stream query data after reconnect', async () => {
        streamLive();
        await renderPanel(1);
        await advanceFrame(2);
        expect(container.textContent).toContain('Video live');
        await renderPanel(2);
        expect(container.textContent).toContain('Unavailable');
        expect(container.querySelector('img')).toBeNull();
        // Late old-key responses cannot refresh evidence for the new session.
        camera.status.data = { ...camera.status.data, frame_sequence: 99 };
        camera.status.dataUpdatedAt = Date.now();
        camera.stream.data = { ...camera.stream.data };
        await renderPanel(2);
        expect(container.textContent).not.toContain('Video live');
        expect(container.querySelector('img')).toBeNull();
        camera.status.data = { ...camera.status.data, requestConnectionGeneration: 2 };
        camera.stream.data = { ...camera.stream.data, connection_generation: 2 };
        await renderPanel(2);
        expect(container.textContent).toContain('Waiting for advancing frames');
        camera.status.data = { ...camera.status.data, frame_sequence: 100 };
        await renderPanel(2);
        expect(container.textContent).toContain('Video live');
    });

    it('does not let an old stream refetch completion clear a new-generation start', async () => {
        let finishRefetch!: () => void;
        let finishStart!: (value: StreamState) => void;
        camera.stream.refetch.mockImplementationOnce(() => new Promise<void>(resolve => { finishRefetch = resolve; }));
        await renderPanel(1);
        await act(async () => button('Video')!.click());
        await renderPanel(2);
        camera.startStream.mockImplementationOnce(() => new Promise(resolve => { finishStart = resolve; }));
        await act(async () => button('Video')!.click());
        await act(async () => finishRefetch());
        expect(button('Working…')).toBeDefined();
        expect(container.querySelector('img')).toBeNull();
        await act(async () => finishStart({ schema_version: 'bioxp.camera_stream.v1', state: 'live', active: true, connection_generation: 2 }));
        expect(button('Stop Video')).toBeDefined();
    });

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
        // A stale session is rejected before allocating/adopting any URL.
        expect(createObjectURL).not.toHaveBeenCalled();

        await act(async () => button('Refresh')!.click());
        await resolveNext(2);
        expect(container.querySelector('img')?.getAttribute('src')).toBe('blob:test-1');

        await act(async () => button('Refresh')!.click());
        await resolveNext(2);
        expect(container.querySelector('img')?.getAttribute('src')).toBe('blob:test-2');
        expect(revokeObjectURL).toHaveBeenCalledWith('blob:test-1');

        await act(async () => root.unmount());
        expect(revokeObjectURL).toHaveBeenCalledWith('blob:test-2');
        root = createRoot(container);
        expect(createObjectURL).toHaveBeenCalledTimes(2);
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

    it('starts and stops video only through the explicit Video control', async () => {
        await renderPanel(1);

        expect(camera.startStream).not.toHaveBeenCalled();
        expect(container.querySelector('img')).toBeNull();

        await act(async () => button('Video')!.click());
        expect(camera.startStream).toHaveBeenCalledWith(1);
        expect(container.querySelector('img')?.getAttribute('src')).toBe('/api/bioxp/camera/mjpeg?expected_generation=1');
        expect(button('Stop Video')).toBeDefined();

        await act(async () => button('Stop Video')!.click());
        expect(camera.stopStream).toHaveBeenCalledWith(1);
        expect(container.querySelector('img')).toBeNull();
        expect(button('Video')).toBeDefined();
    });
});
