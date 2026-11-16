import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const renderer = vi.hoisted(() => ({ width: 1000, height: 700, setNativeIndex: null as null | ((index: number) => void) }));
vi.mock('seqviz', async original => {
    const actual = await original<typeof import('seqviz')>();
    function NativeIndexProbe() {
        const native = React.useContext(actual.Circular.contextType);
        renderer.setNativeIndex = index => native.setCentralIndex('CIRCULAR', index);
        return null;
    }
    return { ...actual, SeqViz: (props: React.ComponentProps<typeof actual.SeqViz>) =>
        <actual.SeqViz {...props} disableExternalFonts testSize={{ width: renderer.width, height: renderer.height }}
            children={props.children ? native => <><NativeIndexProbe />{props.children!(native)}</> : undefined} /> };
});
import { SequenceViewer } from '../../src/components/MolBioToolkit/SequenceViewer';

let host: HTMLDivElement, root: Root;
type Mode = 'linear' | 'circular' | 'both' | 'both_flip';
beforeEach(() => {
    renderer.width = 1000; renderer.height = 700;
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(() => ({
        x: 0, y: 0, left: 0, top: 0, right: renderer.width, bottom: renderer.height,
        width: renderer.width, height: renderer.height, toJSON() {},
    }));
});
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function render(mode: Mode, circular = false) {
    await act(() => root.render(<SequenceViewer
        sequenceData={{ name: 'Linear boundary fixture', sequence: 'ACGT'.repeat(100), circular, sequenceType: 'dna', features: [] }}
        visibility={{ features: false, primers: false, cutsites: false, translations: false, reverseComplement: true }}
        viewMode={mode} />));
}

function boundary() {
    return host.querySelector<SVGCircleElement>('[data-linear-break-boundary]')!;
}

function expectNativeOrigin() {
    // Read the actual renderer's first index tick and its rotation, independently
    // of the overlay helper. Tick "1" is centered half a base beyond boundary 0.
    const circle = host.querySelector<SVGSVGElement>('svg.la-vz-viewer-circular')!;
    const first = [...circle.querySelectorAll('.la-vz-index-tick-label')].find(text => text.textContent === '1')!;
    const rotation = first.parentElement!.getAttribute('transform')!.match(/[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?/gi)!.map(Number);
    const [degrees, cx, cy] = rotation;
    const [px, py] = first.parentElement!.querySelector('path')!.getAttribute('d')!.match(/[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?/gi)!.map(Number);
    const radians = (degrees - 0.5 / 400 * 360) * Math.PI / 180;
    const expectedX = cx + (px - cx) * Math.cos(radians) - (py - cy) * Math.sin(radians);
    const expectedY = cy + (px - cx) * Math.sin(radians) + (py - cy) * Math.cos(radians);
    expect(Number(boundary().getAttribute('cx'))).toBeCloseTo(expectedX, 6);
    expect(Number(boundary().getAttribute('cy'))).toBeCloseTo(expectedY, 6);
    const marker = host.querySelector<SVGSVGElement>('[data-linear-break-marker]')!;
    expect(marker.getAttribute('width')).toBe(circle.getAttribute('width'));
    expect(marker.getAttribute('height')).toBe(circle.getAttribute('height'));
}

it.each(['circular', 'both', 'both_flip'] as const)('anchors the %s break to the native origin and follows rotation in either direction', async mode => {
    await render(mode);
    expectNativeOrigin();
    const initialX = Number(boundary().getAttribute('cx'));
    const circularViewer = host.querySelector('.la-vz-viewer-circular')!;
    await act(() => circularViewer.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 1000 })));
    expectNativeOrigin();
    expect(Number(boundary().getAttribute('cx'))).not.toBeCloseTo(initialX);
    await act(() => circularViewer.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: -1500 })));
    expectNativeOrigin();
    const marker = host.querySelector<SVGSVGElement>('[data-linear-break-marker]')!;
    expect(marker.style.left).toBe('0px');
    expect(marker.parentElement).toBe(circularViewer.parentElement);
    expect(marker.parentElement!.getAttribute('data-linear-projection-pane')).toBe('true');
    expect(marker.getAttribute('aria-label')).toContain('Stored topology remains linear');
});

it('uses resized native geometry instead of a reserved panel position', async () => {
    await render('circular'); expectNativeOrigin();
    const before = Number(boundary().getAttribute('cy'));
    renderer.width = 760; renderer.height = 480;
    await render('circular'); expectNativeOrigin();
    expect(Number(boundary().getAttribute('cy'))).not.toBe(before);
    expect(host.querySelector<HTMLElement>('.sequence-viewer')!.style.paddingTop).toBe('');
});

it('follows native index changes that do not pass through the wheel-event tracker', async () => {
    await render('circular');
    const before = Number(boundary().getAttribute('cx'));
    await act(() => renderer.setNativeIndex!(100));
    expectNativeOrigin();
    expect(Number(boundary().getAttribute('cx'))).not.toBeCloseTo(before);
    expect(host.querySelector('[data-linear-break-central-index]')!.getAttribute('data-linear-break-central-index')).toBe('100');
});

it('shows no projection break for circular molecules or a linear-only view', async () => {
    await render('circular', true);
    expect(host.querySelector('[data-linear-break-marker]')).toBeNull();
    await render('linear', false);
    expect(host.querySelector('[data-linear-break-marker]')).toBeNull();
});
