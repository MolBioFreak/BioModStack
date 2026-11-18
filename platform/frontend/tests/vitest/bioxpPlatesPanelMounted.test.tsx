import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { BioXpPlatesPanel } from '../../src/components/BioXpPlatesPanel';
import { api } from '../../src/lib/api';

const FAST = 'thermo-4483485-enduraplate-fast';
let host: HTMLDivElement, root: Root, client: QueryClient, calls: { method: string; url: string; body: any }[];
const original = api.defaults.adapter;
const profiles = { plates: { [FAST]: { name: 'MicroAmp EnduraPlate Optical 96-Well Fast Clear Reaction Plate with Barcode (4483485)',
    gripper: { LOC_P_MS: { z_low_offset_steps: -10236, source: 'titrated' } }, depth_offsets: [] } },
    loaded: { OUTPUT_PLATE: FAST }, stations: { LOC_MS: FAST },
    station_depth_offsets: [{ tip_type: 10, location: 'LOC_RC', offset_mm: 5.4, source: 'creep' }, { tip_type: 10, location: 'LOC_STRIP1', offset_mm: 1 }] };
const library = { profiles: [{ id: FAST, short_name: 'EnduraPlate Fast 96', name: 'MicroAmp EnduraPlate Optical 96-Well Fast Clear Reaction Plate with Barcode (4483485)', vessel_geometry: { max_volume_ul: 200 } }] };
const calibration = { active_positions: [{ name: 'LOC_P_MS', zLow: 143882 }, { name: 'LOC_P_TC', zLow: 129851 }, { name: 'LOC_P_OC', zLow: 153750 }] };
const depth = { max_mm: 15, entries: [], standard: [{ offset_mm: 0, label: 'None', source: 'x' }, { offset_mm: 5, label: '+5 mm', source: 'RC bottom' }] };
async function tick() { await act(async () => { await new Promise(r => setTimeout(r, 20)); }); }
const q = (sel: string) => host.querySelector(sel) as HTMLElement;
const buttonIn = (el: Element, text: string) => [...el.querySelectorAll('button')].find(b => b.textContent === text)!;
async function click(el: Element) { await act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })); }); await tick(); }
async function change(labelText: string, value: string) {
    const el = host.querySelector(`[aria-label="${labelText}"]`) as HTMLInputElement | HTMLSelectElement;
    expect(el, labelText).not.toBeNull();
    await act(async () => {
        Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value);
        el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    });
}

beforeEach(async () => {
    calls = [];
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    api.defaults.adapter = async config => {
        calls.push({ method: config.method!, url: config.url!, body: config.data ? JSON.parse(config.data) : undefined });
        const ok = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config });
        if (config.url?.startsWith('/api/bioxp/plate-profiles')) return ok(profiles);
        if (config.url === '/api/bioxp/methods/plates') return ok(library);
        if (config.url === '/api/bioxp/calibration-settings') return ok(calibration);
        if (config.url?.startsWith('/api/bioxp/depth-offsets')) return ok(depth);
        throw new Error(`unexpected ${config.method} ${config.url}`);
    };
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpPlatesPanel generation={7} connected /></QueryClientProvider>));
    await tick(); await tick();
});
afterEach(() => { act(() => root.unmount()); host.remove(); api.defaults.adapter = original; });

it('shows the loaded plate, its gripper heights with reached Z, and inherited station depths', async () => {
    expect(q('[data-testid="loaded-output-plate"]').textContent).toContain('EnduraPlate Fast 96');
    expect(q('[data-testid="loaded-output-plate"]').textContent).toContain('Magnet station');
    const ms = q(`[data-grip="${FAST}:LOC_P_MS"]`);
    expect(ms.textContent).toContain('\u221210 236');
    expect(ms.textContent).toContain('Z 133 646');
    expect(q(`[data-grip="${FAST}:LOC_P_TC"]`).textContent).toContain('OEM');
    expect(q(`[data-grip="${FAST}:LOC_P_TC"]`).textContent).toContain('Z 129 851');
    expect(q('[aria-label="Plate depth offsets"]').textContent).toContain('station default');
    expect(q('[data-station-default="LOC_RC"]').textContent).toContain('+5.4');
    expect(q('[data-station-default="LOC_MS"]').textContent).toContain('\u2014');
});

it('records gripper height, plate depth and station default without moving anything', async () => {
    await click(buttonIn(q(`[data-grip="${FAST}:LOC_P_TC"]`), 'Set'));
    await change('Gripper steps LOC_P_TC', '-2000');
    await click(buttonIn(q(`[data-grip="${FAST}:LOC_P_TC"]`), 'Save'));
    expect(calls.find(c => c.method === 'put' && c.url === '/api/bioxp/plate-profiles/gripper')!.body)
        .toMatchObject({ expected_connection_generation: 7, plate_id: FAST, location: 'LOC_P_TC', z_low_offset_steps: -2000 });
    await click(buttonIn(q(`[data-grip="${FAST}:LOC_P_MS"]`), 'Reset to OEM'));
    expect(calls.filter(c => c.url === '/api/bioxp/plate-profiles/gripper').at(-1)!.body).toMatchObject({ location: 'LOC_P_MS', z_low_offset_steps: null });
    await change('Plate depth station', 'LOC_MS'); await change('Plate depth mm', '2');
    await click(buttonIn(q('[aria-label="Plate profiles"]'), 'Save'));
    expect(calls.filter(c => c.method === 'put' && c.url === '/api/bioxp/depth-offsets').at(-1)!.body)
        .toMatchObject({ tip_type: 10, location: 'LOC_MS', plate_type: FAST, offset_mm: 2 });
    await change('Default depth station', 'LOC_OC'); await change('Default depth mm', '1.5');
    await click(buttonIn(q('[aria-label="Station depth defaults"]'), 'Save default'));
    expect(calls.filter(c => c.method === 'put' && c.url === '/api/bioxp/depth-offsets').at(-1)!.body)
        .toMatchObject({ location: 'LOC_OC', plate_type: null, offset_mm: 1.5 });
    expect(calls.some(c => /protocols|operator-controls|motion/.test(c.url))).toBe(false);
});
