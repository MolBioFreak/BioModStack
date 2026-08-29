import assert from 'node:assert/strict';
import test from 'node:test';
import {
    isOwnedFullscreen,
    toggleOwnedFullscreen,
} from '../src/components/ngs/ngsFullscreenOwner.js';

test('owned fullscreen requests the exact viewer shell', async () => {
    const calls: string[] = [];
    const shell = {
        requestFullscreen: async () => { calls.push('shell'); },
    } as unknown as HTMLElement;
    const documentLike = {
        fullscreenEnabled: true,
        fullscreenElement: null,
        exitFullscreen: async () => { calls.push('exit'); },
    } as unknown as Document;

    await toggleOwnedFullscreen(shell, documentLike);

    assert.deepEqual(calls, ['shell']);
});

test('owned fullscreen exits only when the viewer shell owns fullscreen', async () => {
    const calls: string[] = [];
    const shell = { requestFullscreen: async () => { calls.push('request'); } } as unknown as HTMLElement;
    const documentLike = {
        fullscreenEnabled: true,
        fullscreenElement: shell,
        exitFullscreen: async () => { calls.push('exit'); },
    } as unknown as Document;

    assert.equal(isOwnedFullscreen(shell, documentLike), true);
    await toggleOwnedFullscreen(shell, documentLike);

    assert.deepEqual(calls, ['exit']);
});

test('another fullscreen owner is rejected without changing fullscreen state', async () => {
    const calls: string[] = [];
    const shell = { requestFullscreen: async () => { calls.push('request'); } } as unknown as HTMLElement;
    const other = {} as HTMLElement;
    const documentLike = {
        fullscreenEnabled: true,
        fullscreenElement: other,
        exitFullscreen: async () => { calls.push('exit'); },
    } as unknown as Document;

    assert.equal(isOwnedFullscreen(shell, documentLike), false);
    await assert.rejects(
        toggleOwnedFullscreen(shell, documentLike),
        /Another surface already owns fullscreen/,
    );
    assert.deepEqual(calls, []);
});

test('unsupported fullscreen fails visibly through the caller error path', async () => {
    const shell = {} as HTMLElement;
    const documentLike = {
        fullscreenEnabled: false,
        fullscreenElement: null,
    } as Document;

    await assert.rejects(
        toggleOwnedFullscreen(shell, documentLike),
        /Fullscreen is unavailable/,
    );
});
