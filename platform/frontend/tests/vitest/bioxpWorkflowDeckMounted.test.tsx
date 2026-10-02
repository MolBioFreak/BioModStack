import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpWorkflowDeck } from '../../src/components/BioXpWorkflowDeck';
import { deckPark, deckRegions, deckResources, deckSize, deckStations, projectDeckPoint, type BioXpDeckSelection } from '../../src/lib/bioxpWorkflowDeck';

let host: HTMLDivElement, root: Root;
let latest: BioXpDeckSelection;
const changed = vi.fn();
function Controlled({ initial = { station: '', wells: [] } }: { initial?: BioXpDeckSelection }) {
    const [selection, setSelection] = useState(initial);
    latest = selection;
    return <BioXpWorkflowDeck selection={selection} onChange={value => { changed(value); setSelection(value); }} />;
}
function el(label: string) { const node = host.querySelector(`[aria-label="${label}"]`); expect(node, label).not.toBeNull(); return node!; }
async function click(label: string, modifiers: MouseEventInit = {}) { await act(async () => el(label).dispatchEvent(new MouseEvent('click', { bubbles: true, ...modifiers }))); }
async function button(text: string) { const node = [...host.querySelectorAll('button')].find(b => b.textContent === text)!; expect(node, text).toBeTruthy(); await act(async () => node.click()); }
async function key(label: string, key: string, modifiers: KeyboardEventInit = {}) { await act(async () => el(label).dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...modifiers }))); }
async function station(value: string) { await act(async () => { const node = el('Deck station') as HTMLSelectElement; node.value = value; node.dispatchEvent(new Event('change', { bubbles: true })); }); }
beforeEach(() => { changed.mockClear(); host = document.createElement('div'); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function mount(initial?: BioXpDeckSelection) { await act(async () => root.render(<Controlled initial={initial} />)); }

it('keeps all 800 native addresses, source orientation and explicit OEM bindings', () => {
    expect(deckResources.reduce((n, r) => n + r.wells.length, 0)).toBe(800);
    expect(new Set(deckStations.map(s => s.id)).size).toBe(17);
    const expected: Record<string, [number, number | null]> = { LOC_MS: [0, null], LOC_OC: [1, null], LOC_TC: [2, null], LOC_RC: [3, null],
        TECANRACK1: [7, 1], TECANRACK2: [8, 2], TECANRACK3: [9, 3], TECANRACK4: [10, 4], LOC_STRIP1: [11, null], LOC_STRIP2: [12, null], LOC_STRIP3: [13, null], LOC_STRIP4: [14, null],
        LOC_TROUGH: [16, null], WASTE_BIN: [6, null], LOC_OC_COVER_STORAGE: [18, null], LOC_RC_COVER_STORAGE: [20, null] };
    for (const [id, binding] of Object.entries(expected)) { const s = deckStations.find(s => s.id === id)!; expect([s.locationId, s.tipTray]).toEqual(binding); }
    expect(deckStations.find(s => s.id === 'LOC_PARK')?.locationId).toBeNull();
    for (const resource of deckResources) {
        expect(resource.points[0].well).toBe('A1');
        const b1 = resource.points.find(w => w.well === 'B1')!;
        expect(b1.x).toBe(resource.points[0].x); expect(b1.y).toBeGreaterThan(resource.points[0].y);
        if (resource.kind !== 'strip') expect(resource.points[1].x).toBeGreaterThan(resource.points[0].x);
    }
    const [x, y] = projectDeckPoint(26213, 9241);
    expect(deckResources.find(s => s.id === 'LOC_MS')!.points[0]).toMatchObject({ x, y });
    expect(deckRegions).toHaveLength(4); expect(deckRegions.every(r => r.layers.some(l => l.name === 'outer'))).toBe(true);
});

it('mounts the physical deck, selects with click and keeps keyed map DOM identity', async () => {
    await mount();
    expect(host.querySelectorAll('[data-well]')).toHaveLength(800);
    const well = el('Magnetic station A1'), region = el('Select Waste bin');
    await click('Magnetic station A1');
    expect(latest).toEqual({ station: 'LOC_MS', wells: ['A1'] });
    expect(el('Magnetic station A1')).toBe(well); expect(el('Select Waste bin')).toBe(region);
    expect(well.getAttribute('aria-pressed')).toBe('true');
    await click('Magnetic station B2', { ctrlKey: true });
    expect(latest.wells).toEqual(['A1', 'B2']);
    await click('Magnetic station A1', { metaKey: true });
    expect(latest.wells).toEqual(['B2']);
    await button('Clear selection'); expect(latest).toEqual({ station: 'LOC_MS', wells: [] });
});

it('supports rectangular range, row and column selection on map and readable grid', async () => {
    await mount(); await station('LOC_RC');
    await click('Select well A1'); await click('Select well C3', { shiftKey: true });
    expect(latest.wells).toEqual(['A1', 'A2', 'A3', 'B1', 'B2', 'B3', 'C1', 'C2', 'C3']);
    await button('Range'); await click('Reagent chiller B2'); await click('Reagent chiller C3');
    expect(latest.wells).toEqual(['B2', 'B3', 'C2', 'C3']);
    await button('Row'); await click('Reagent chiller D5'); expect(latest.wells).toHaveLength(12); expect(latest.wells.every(w => w.startsWith('D'))).toBe(true);
    await button('Column'); await click('Reagent chiller A2'); expect(latest.wells).toEqual(['A2', 'B2', 'C2', 'D2', 'E2', 'F2', 'G2', 'H2']);
    await click('Select row F'); expect(latest.wells).toHaveLength(12); expect(latest.wells[0]).toBe('F1');
    await click('Select column 12'); expect(latest.wells).toHaveLength(8); expect(latest.wells[0]).toBe('A12');
    await station('LOC_STRIP4'); await button('Column'); await click('Strip 4 A1'); expect(latest.wells).toEqual(['A1', 'B1', 'C1', 'D1', 'E1', 'F1', 'G1', 'H1']);
});

it('supports keyboard selection, roving arrow focus, shift range and station keys', async () => {
    await mount(); await key('Select Thermal cycler', 'Enter'); expect(latest.station).toBe('LOC_TC');
    await key('Thermal cycler A1', ' '); expect(latest.wells).toEqual(['A1']);
    await key('Thermal cycler A1', 'ArrowRight'); expect(document.activeElement).toBe(el('Thermal cycler A2'));
    await key('Thermal cycler A2', 'Enter'); expect(latest.wells).toEqual(['A2']);
    await key('Thermal cycler A2', 'ArrowDown', { shiftKey: true }); expect(latest.wells).toEqual(['A2', 'B2']);
    await key('Select Output cover storage', ' '); expect(latest).toEqual({ station: 'LOC_OC_COVER_STORAGE', wells: [] });
    await key('Select Park target', 'Enter'); expect(latest.station).toBe(deckPark.id); expect(host.textContent).toContain('Inspect-only region');
});

it('does not infer a selection or persist/send requests; zoom and pan are presentation-only', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    const xhr = vi.spyOn(XMLHttpRequest.prototype, 'open');
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    await mount({ station: 'TECANRACK3', wells: ['D6'] }); expect(changed).not.toHaveBeenCalled();
    const svg = el('BioXP deck map'), well = el('Tip rack 3 D6'), initial = svg.getAttribute('viewBox');
    await button('Focus station'); expect(svg.getAttribute('viewBox')).not.toBe(initial);
    const focused = svg.getAttribute('viewBox'); await click('Zoom in'); expect(svg.getAttribute('viewBox')).not.toBe(focused);
    await click('Pan left'); await click('Pan up'); await click('Pan right'); await click('Pan down'); await click('Zoom out');
    await button('Fit deck'); expect(svg.getAttribute('viewBox')).toBe(`0 0 ${deckSize.width} ${deckSize.height}`);
    expect(el('Tip rack 3 D6')).toBe(well); expect(latest).toEqual({ station: 'TECANRACK3', wells: ['D6'] }); expect(changed).not.toHaveBeenCalled();
    await click('Select Waste bin'); await station('LOC_OC'); await click('Output chiller H12'); await button('Clear selection');
    expect(fetch).not.toHaveBeenCalled(); expect(xhr).not.toHaveBeenCalled(); expect(storage).not.toHaveBeenCalled();
    expect(host.textContent).toContain('fixed four-head alignment'); expect(host.textContent).toContain('artwork estimates');
});

it('remains controlled across external reopen and does not silently replace unknown saved selection', async () => {
    const onChange = vi.fn();
    await act(async () => root.render(<BioXpWorkflowDeck selection={{ station: 'LOC_MS', wells: ['A1'] }} onChange={onChange} />));
    const a1 = el('Magnetic station A1'); await click('Magnetic station B2');
    expect(onChange).toHaveBeenLastCalledWith({ station: 'LOC_MS', wells: ['B2'] }); expect(a1.getAttribute('aria-pressed')).toBe('true');
    await act(async () => root.render(<BioXpWorkflowDeck selection={{ station: 'TECANRACK4', wells: ['C8'] }} onChange={onChange} />));
    expect(el('Tip rack 4 C8').getAttribute('aria-pressed')).toBe('true'); expect(el('Magnetic station A1')).toBe(a1);
    onChange.mockClear(); await act(async () => root.render(<BioXpWorkflowDeck selection={{ station: 'future-station', wells: ['Z99'] }} onChange={onChange} />));
    expect(onChange).not.toHaveBeenCalled(); expect(host.textContent).toContain('Z99');
});
