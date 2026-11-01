import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpDeckObjectsCard, BioXpDeckRecordPanel, deckRecordRequest } from '../../src/components/BioXpDeckRecord';
import { api } from '../../src/lib/api';

vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
let host: HTMLDivElement, root: Root, client: QueryClient;
const deckState = {
    schema_version: 'bioxp.operator_deck_state.v1', physical_observation: false,
    semantic: { plate_on_gantry: null, tip_loaded: false, semantic_state_revision: 4 },
    movable_objects: {
        OUTPUT_COVER: { location: 'LOC_OC_COVER_STORAGE', source: 'provider_machine_status' },
        POOL_PLATE: { location: 'LOC_P_TC', source: 'sqlite_record' },
    },
};
const tipTrays = { schema_version: 'bioxp.operator_tip_trays.v1', physical_observation: false,
    trays: [{ tray: 1, tray_id: 0, tips_present: 96 }, { tray: 5, tray_id: 4, unavailable: 'no projection' }] };
async function tick() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function render(node: React.ReactNode) {
    await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>));
    await tick();
}
function select(label: string) { return host.querySelector(`select[aria-label="${label}"]`) as HTMLSelectElement; }
async function choose(label: string, value: string) {
    const el = select(label);
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(el, value);
        el.dispatchEvent(new Event('change', { bubbles: true }));
    });
}
const recordButton = () => [...host.querySelectorAll('button')].find(el => el.textContent?.startsWith('Record'))!;

beforeEach(() => {
    vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.mocked(api.get).mockImplementation(async (path) => {
        if (String(path).endsWith('/operator-controls/deck/state')) return { data: deckState } as never;
        if (String(path).endsWith('/operator-controls/tip-trays')) return { data: tipTrays } as never;
        throw new Error(`Unexpected GET ${path}`);
    });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); });

it('shows recorded plate and cover locations with the gripper state', async () => {
    await render(<BioXpDeckObjectsCard generation={3} connected visible />);
    const cover = host.querySelector('[data-object="OUTPUT_COVER"]')!;
    expect(cover.getAttribute('data-location')).toBe('LOC_OC_COVER_STORAGE');
    expect(cover.textContent).toContain('Output cover storage · default');
    expect(host.querySelector('[data-object="POOL_PLATE"]')!.textContent).toContain('Thermal cycler (plate)');
    expect(host.querySelector('[data-object="gantry"]')!.textContent).toContain('Nothing');
    expect(api.post).not.toHaveBeenCalled();
});

it('records only changed rows through the BMS relay and never moves', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { schema_version: 'bioxp.operator_deck_record_result.v1',
        record_id: 'auto-1', motion_commanded: false, physical_observation: false, warnings: [] } } as never);
    await render(<BioXpDeckRecordPanel generation={3} connected visible />);
    expect(recordButton().disabled).toBe(true);
    await choose('Output cover location', 'LOC_OC_COVER');
    await choose('Pool plate location', 'LOC_P_TC'); // already recorded there: omitted
    await choose('Gripper holds', 'empty');
    await choose('Tip rack 1 tips', 'full');
    expect(recordButton().textContent).toBe('Record 3 changes');
    await act(async () => { recordButton().click(); recordButton().click(); }); await tick();
    expect(api.post).toHaveBeenCalledTimes(1);
    const [path, body] = vi.mocked(api.post).mock.calls[0];
    expect(path).toBe('/api/bioxp/operator-controls/deck/record');
    expect(body).toEqual({ expected_connection_generation: 3, movable_objects: { OUTPUT_COVER: 'LOC_OC_COVER' },
        gantry: 'empty', tip_trays: [{ tray: 1, tips: 'full' }] });
    expect(host.textContent).toContain('No motion was commanded');
    expect(select('Output cover location').value).toBe('');
});

it('surfaces a robot rejection without retrying', async () => {
    vi.mocked(api.post).mockRejectedValue({ response: { status: 422, data: { detail: { error: 'deck_record_invalid_objects' } } } });
    await render(<BioXpDeckRecordPanel generation={3} connected visible />);
    await choose('Trough location', 'LOC_TROUGH');
    await act(async () => recordButton().click()); await tick();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
});

it('builds no change for an empty edit', () => {
    expect(deckRecordRequest(2, { objects: {}, gantry: '', trays: {}, reason: ' ' })).toEqual({ expected_connection_generation: 2 });
});
