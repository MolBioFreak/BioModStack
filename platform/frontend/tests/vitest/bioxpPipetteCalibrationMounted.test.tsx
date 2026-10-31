import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto, createHash } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpPipetteCalibration } from '../../src/components/BioXpPipetteCalibration';
import { api } from '../../src/lib/api';

let host: HTMLDivElement, root: Root, client: QueryClient, calls: { method: string; url: string; body: any }[], job: any;
const original = api.defaults.adapter;
const tips = [{ tip_type: 10, offset_steps: 45757, measured: true, datasheet_delta_steps: 45757, confirmed: null },
    { tip_type: 50, offset_steps: 0, measured: true, datasheet_delta_steps: 0, confirmed: null }];
const plates = { profiles: [{ id: 'thermo-4483485-enduraplate-fast', name: 'Fast plate', vessel_geometry: { well_top_diameter_mm: 5.49 } }] };
async function tick() { await act(async () => { await new Promise(r => setTimeout(r, 20)); }); }
const button = (text: string) => [...host.querySelectorAll('button')].find(b => b.textContent === text)!;
async function change(label: string, value: string) {
    const el = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLSelectElement;
    expect(el, label).not.toBeNull();
    await act(async () => {
        Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value);
        el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    });
}

beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); calls = []; job = null;
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    api.defaults.adapter = async config => {
        const body = config.data ? JSON.parse(config.data) : undefined;
        calls.push({ method: config.method!, url: config.url!, body });
        const ok = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config });
        if (config.url === '/api/bioxp/tip-offsets' && config.method === 'get') return ok({ tips });
        if (config.url === '/api/bioxp/tip-offsets') return ok({ tips });
        if (config.url === '/api/bioxp/methods/plates') return ok(plates);
        if (config.url === '/api/bioxp/methods/compile') return ok({ document: { stages: [], steps: body.method.steps }, issues: [] });
        if (config.url === '/api/bioxp/methods/plate-profiles') return ok({ id: 'saved' });
        if (config.url === '/api/bioxp/protocols/submit') {
            const id = `protocol-live-${createHash('sha256').update(body.idempotency_key).digest('hex')}`;
            job = { job_id: id, status: 'completed', command: { command_id: id, idempotency_key: body.idempotency_key, terminal: true, status: 'completed' },
                execution: { dry_run: false, runtime_state: { workflow: { phase: 'terminal' }, action_results: [{ pipette_result: { kind: 'cavro_application',
                    liquid_levels: [{ detection_id: 'd', floor_steps: 78000, channels: {
                        '0': { found: true, contact_steps: 70000, height_above_floor_mm: '3.969', method: 'interpolated_descent:monotonic' },
                        '1': { found: false, contact_steps: null, height_above_floor_mm: null, method: 'no_contact' } } }] } }] } } };
            return ok(job);
        }
        if (config.url?.includes('/jobs/') && job) return ok(job);
        throw new Error(`unexpected ${config.method} ${config.url}`);
    };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = original; vi.unstubAllGlobals(); });

async function mount() {
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpPipetteCalibration generation={5} connected
        destinations={[{ target: 'rc', label: 'RC plate', location_id: 3 } as never]} /></QueryClientProvider>));
    await tick();
}

it('shows tip offsets in mm and records a confirmed value without moving anything', async () => {
    await mount();
    expect(host.querySelector('[aria-label="Tip offset values"]')?.textContent).toContain('22.70 mm');
    await change('Tip 10 offset (mm)', '22.5');
    await act(async () => button('Save').click()); await tick();
    const put = calls.find(c => c.method === 'put')!;
    expect(put.body).toMatchObject({ expected_connection_generation: 5, tip_type: 10, offset_steps: Math.round(22.5 * 2015.748) });
    expect(calls.some(c => c.url === '/api/bioxp/protocols/submit')).toBe(false);
});

it('liquid detection test compiles move, search and lift at the selected well and shows per-channel heights', async () => {
    await mount();
    expect(button('Find liquid').disabled).toBe(true);           // no station yet
    await change('Calibration station', '3'); await change('Calibration row', 'C'); await change('Calibration column', '4');
    await act(async () => (host.querySelector('[aria-label="Detection pipette 3"]') as HTMLInputElement).click());
    await act(async () => button('Find liquid').click()); await tick(); await tick();
    const steps = calls.find(c => c.url === '/api/bioxp/methods/compile')!.body.method.steps;
    expect(steps.map((s: any) => s.inputs.operation ?? s.inputs.application.operations[0].operation)).toEqual(['move', 'liquid_search', 'lift']);
    expect(steps[0].inputs).toMatchObject({ location_id: 3, well: 'C4', position_flag: 0 });
    expect(steps[1].inputs.application.operations[0]).toMatchObject({ channels: [0, 1, 3], location_id: 3, sensing: 'hybrid', floor_clearance_steps: 2016 });
    expect(calls.filter(c => c.url === '/api/bioxp/protocols/submit')).toHaveLength(1);
    const table = host.querySelector('[aria-label="Detected liquid"]')!.textContent!;
    expect(table).toContain('4.97 mm');   // 3.969 above stop + 1 mm stop height
    expect(table).toContain('no liquid');
});

it('plate measurement builds a table from detected heights and saves it with the well-bottom gap', async () => {
    await mount();
    await change('Calibration station', '3');
    await act(async () => button('Find liquid').click()); await tick(); await tick();
    await change('Known volume pipette 1', '20');
    await act(async () => button('Add points from last search').click());
    expect(host.querySelector('[aria-label="Plate points"]')?.textContent).toContain('20 µL at 4.97 mm');
    expect(button('Save plate table').disabled).toBe(true);       // one point only
    await act(async () => button('Find liquid').click()); await tick(); await tick();
    await change('Known volume pipette 1', '40');
    await act(async () => button('Add points from last search').click());
    await change('Well-bottom gap (mm)', '0.5');
    await act(async () => button('Save plate table').click()); await tick();
    const saved = calls.find(c => c.url === '/api/bioxp/methods/plate-profiles')!.body.method;
    expect(saved.schema).toBe('bms.bioxp-plate-profile.v1');
    expect(saved.profile.id).toBe('thermo-4483485-enduraplate-fast');
    expect(saved.profile.vessel_geometry).toMatchObject({ calibrated_low_gap_mm: 0.5, well_top_diameter_mm: 5.49 });
    expect(saved.profile.vessel_geometry.height_to_volume).toEqual([[0, 0], [5.469, 20], [5.469, 40]]);
});
