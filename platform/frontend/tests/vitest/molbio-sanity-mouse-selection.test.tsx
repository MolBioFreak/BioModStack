import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import './setup';

vi.mock('seqviz', async original => {
    const actual = await original<typeof import('seqviz')>();
    return { ...actual, SeqViz: (props: React.ComponentProps<typeof actual.SeqViz>) =>
        <actual.SeqViz {...props} disableExternalFonts testSize={{ width: 1000, height: 700 }} /> };
});
import { SequenceViewer } from '../../src/components/MolBioToolkit/SequenceViewer';

let host: HTMLDivElement;
let root: Root;
let frames: Map<number, FrameRequestCallback>;
const onSelection = vi.fn();
const onContextMenu = vi.fn();

beforeEach(() => {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    frames = new Map(); let frameId = 0;
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
        x: 0, y: 0, left: 0, top: 0, right: 1000, bottom: 700, width: 1000, height: 700, toJSON() {},
    });
    onSelection.mockClear(); onContextMenu.mockClear();
});

afterEach(async () => {
    await act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals();
});

async function flushFrames() {
    for (let pass = 0; frames.size && pass < 10; pass++) {
        const pending = [...frames]; frames.clear();
        await act(() => { pending.forEach(([, callback]) => callback(performance.now())); });
    }
}

async function render(viewMode: 'linear' | 'circular' | 'both' = 'linear') {
    await act(() => root.render(<SequenceViewer
        sequenceData={{ name: 'Mouse interaction fixture', sequence: 'ACGT'.repeat(100), circular: true, sequenceType: 'dna', features: [] }}
        visibility={{ features: false, primers: false, cutsites: false, translations: false, reverseComplement: true }}
        viewMode={viewMode} onSelection={onSelection} onContextMenu={onContextMenu} />));
    await flushFrames();
    return host.querySelector(viewMode === 'circular' ? '.la-vz-viewer-circular' : '[data-testid="la-vz-seqblock"]')!;
}

async function mouse(target: EventTarget, type: string, x = 100, y = 100, buttons = type === 'mouseup' ? 0 : 1, button = 0) {
    // The linear renderer virtualizes/replaces sequence blocks after selection.
    if (target instanceof Element && target.matches('[data-testid="la-vz-seqblock"]')) {
        target = host.querySelector('[data-testid="la-vz-seqblock"]')!;
    }
    const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, buttons, button });
    await act(() => { target.dispatchEvent(event); });
    return event;
}

it.each(['linear', 'circular', 'both'] as const)('keeps %s dragging inside SeqViz and commits the final range on release', async mode => {
    const surface = await render(mode);
    expect(surface).not.toBeNull();
    const down = await mouse(surface, 'mousedown', 550, 150);
    expect(down.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(host.querySelector('.la-vz-viewer-event-router'));
    const drag = new Event('dragstart', { bubbles: true, cancelable: true });
    host.querySelector('.la-vz-viewer-event-router')!.dispatchEvent(drag);
    expect(drag.defaultPrevented).toBe(true);
    await mouse(surface, 'mousemove', 700, 350);
    await flushFrames();
    expect(onSelection).not.toHaveBeenCalled();
    await mouse(surface, 'mouseup', 700, 350);
    await flushFrames();
    expect(onSelection).toHaveBeenCalledTimes(1);
    const selection = onSelection.mock.calls[0][0];
    expect(selection.end).toBeGreaterThan(selection.start);
    onSelection.mockClear();
    await mouse(surface, 'mousemove', 350, 450, 0);
    await flushFrames();
    expect(onSelection).not.toHaveBeenCalled();
    expect((await mouse(surface, 'mousedown', 350, 450, 2, 2)).defaultPrevented).toBe(false);
    await mouse(surface, 'contextmenu', 350, 450, 2, 2);
    expect(onContextMenu).toHaveBeenCalledTimes(1);
});

it.each(['blur', 'pointercancel'])('ends native dragging on %s without committing an abandoned range', async ending => {
    const surface = await render();
    await mouse(surface, 'pointerdown');
    await mouse(surface, 'mousedown');
    await mouse(surface, 'mousemove', 400);
    await act(() => { window.dispatchEvent(new Event(ending)); });
    await flushFrames();
    expect(onSelection).not.toHaveBeenCalled();
    await mouse(surface, 'mousemove', 800, 100, 0);
    await flushFrames();
    expect(onSelection).not.toHaveBeenCalled();
    await mouse(surface, 'mousedown', 200);
    await mouse(surface, 'mousemove', 500);
    await mouse(surface, 'mouseup', 500);
    await flushFrames();
    expect(onSelection).toHaveBeenCalledTimes(1);
    expect(onSelection.mock.calls[0][0].end).toBeGreaterThan(onSelection.mock.calls[0][0].start);
});

it.each(['outside release', 'missed release'])('finishes a range after %s and never tracks an unpressed mouse', async ending => {
    const surface = await render();
    await mouse(surface, 'mousedown', 100);
    await mouse(surface, 'mousemove', 400);
    if (ending === 'outside release') await mouse(document.body, 'mouseup', 1400);
    else await mouse(surface, 'mousemove', 800, 100, 0);
    await flushFrames();
    expect(onSelection).toHaveBeenCalledTimes(1);
    const first = onSelection.mock.calls[0][0];
    expect(first.end).toBeGreaterThan(first.start);
    onSelection.mockClear();
    await mouse(surface, 'mousemove', 900, 100, 0);
    await flushFrames();
    expect(onSelection).not.toHaveBeenCalled();
});

it('does not let a previous release frame end the next drag', async () => {
    const surface = await render();
    await mouse(surface, 'mousedown', 100);
    await mouse(surface, 'mousemove', 400);
    await mouse(surface, 'mouseup', 400);
    await mouse(surface, 'mousedown', 600);
    await mouse(surface, 'mousemove', 900);
    await flushFrames();
    expect(onSelection).toHaveBeenCalledTimes(1);
    await mouse(surface, 'mouseup', 900);
    await flushFrames();
    expect(onSelection).toHaveBeenCalledTimes(2);
    expect(onSelection.mock.calls[1][0].start).toBeGreaterThan(onSelection.mock.calls[0][0].end);
});

it('leaves popup text and controls available for normal browser interaction', async () => {
    await render();
    const dialog = document.createElement('div'); dialog.role = 'dialog';
    dialog.innerHTML = '<span>Details to copy</span><button>Close</button>';
    host.querySelector('.la-vz-viewer-event-router')!.append(dialog);
    for (const target of [...dialog.children]) {
        expect((await mouse(target, 'mousedown')).defaultPrevented).toBe(false);
        const drag = new Event('dragstart', { bubbles: true, cancelable: true });
        target.dispatchEvent(drag);
        expect(drag.defaultPrevented).toBe(false);
    }
    dialog.remove();
});
