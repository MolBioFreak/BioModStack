import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpDeckRecordPanel } from '../../src/components/BioXpDeckRecordPanel';
import { BioXpMotionToggle } from '../../src/components/BioXpMotionToggle';
import { api } from '../../src/lib/api';

vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
let host: HTMLDivElement, root: Root, client: QueryClient;
const full = Array.from({ length: 96 }, () => true);
// Tray 2: A1-H1 and A2 present (9 tips), only one four-tip pickup left.
const partial = Array.from({ length: 96 }, (_, i) => i % 12 === 0 || i === 1);
const deckState = () => ({
    schema_version: 'bioxp.operator_deck_state.v1', physical_observation: false,
    semantic: { plate_on_gantry: null, tip_loaded: false,
        movable_plate_locations: { OUTPUT_COVER: 'LOC_OC_COVER', REAGENT_COVER: 'LOC_RC_COVER_STORAGE' } },
    movable_objects: { OUTPUT_COVER: { location: 'LOC_OC_COVER' }, REAGENT_COVER: { location: 'LOC_RC_COVER_STORAGE' } },
});
const tipTrays = () => ({ schema_version: 'bioxp.operator_tip_trays.v1', physical_observation: false, trays: [
    { tray: 1, tray_id: 0, occupancy: full, tips_present: 96, four_channel_groups_available: 24 },
    { tray: 2, tray_id: 1, occupancy: partial, tips_present: 9, four_channel_groups_available: 1 },
    { tray: 3, tray_id: 2, occupancy: Array(96).fill(false), tips_present: 0, four_channel_groups_available: 0 },
    { tray: 4, tray_id: 3, occupancy: full, tips_present: 96, four_channel_groups_available: 24 },
    { tray: 5, tray_id: 4, occupancy: full, tips_present: 96, four_channel_groups_available: 24 },
] });
const tick = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); };
const render = async (node: React.ReactNode) => {
    await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)); await tick();
};
const select = (label: string) => host.querySelector(`select[aria-label="${label}"]`) as HTMLSelectElement;
const choose = async (label: string, value: string) => {
    const el = select(label);
    await act(async () => { el.value = value; el.dispatchEvent(new Event('change', { bubbles: true })); });
};
const button = (text: string) => [...host.querySelectorAll('button')].find(el => el.textContent === text) as HTMLButtonElement;
const gets = () => vi.mocked(api.get).mock.calls.map(([path]) => String(path));

beforeEach(() => {
    vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.mocked(api.get).mockImplementation(async path => {
        if (path === '/api/bioxp/operator-controls/deck/state') return { data: deckState() } as never;
        if (path === '/api/bioxp/operator-controls/tip-trays') return { data: tipTrays() } as never;
        throw new Error(`Unexpected GET ${path}`);
    });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); });

it('shows what the robot believes in plain names, tips and pickups separately, and no tip hotel', async () => {
    await render(<BioXpDeckRecordPanel generation={3} connected />);
    const row = (id: string) => host.querySelector(`[data-row="${id}"]`)!.textContent!;
    expect(row('OUTPUT_COVER')).toContain('Output chiller');
    expect(row('REAGENT_COVER')).toContain('Reagent cover storage');
    expect(row('GRIPPER')).toContain('Empty');
    expect(host.querySelector('[data-testid="tips-1"]')!.textContent).toBe('96/96 tips · 24 pickups');
    expect(host.querySelector('[data-testid="tips-2"]')!.textContent).toBe('9/96 tips · 1 pickups');
    expect(host.querySelector('[data-testid="tips-3"]')!.textContent).toBe('0/96 tips · 0 pickups');
    expect(host.querySelector('[data-row="TIPS_5"]')).toBeNull();
    expect(host.textContent).not.toMatch(/hotel/i);
    expect(host.textContent).not.toContain('LOC_');
    expect(host.textContent).toContain('Record only. Nothing moves.');
    expect(button('Save record').disabled).toBe(true);
    expect(api.post).not.toHaveBeenCalled();
});

it('saves only changed fields, shows robot warnings and reads the record again', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { schema_version: 'bioxp.operator_deck_record_result.v1', record_id: 'auto-1',
        motion_commanded: false, physical_observation: false,
        warnings: [{ code: 'object_recorded_on_gantry', objects: ['OUTPUT_COVER'], message: 'Set their deck location too.' }] } } as never);
    await render(<BioXpDeckRecordPanel generation={3} connected />);
    await choose('Set Output cover to', 'LOC_OC_COVER'); // same as recorded: omitted
    await choose('Set Reagent cover to', 'LOC_RC_COVER');
    await choose('Set Gripper to', 'empty'); // same as recorded: omitted
    await choose('Set Tips 1 to', 'full'); // already full: omitted
    await choose('Set Tips 3 to', 'full');
    await choose('Set Tips 2 to', 'pick');
    // Picker starts from the recorded wells; toggling A2 off leaves column 1.
    expect(host.querySelectorAll('[aria-label="Tip rack 2 wells"] button[aria-pressed="true"]')).toHaveLength(9);
    await act(async () => (host.querySelector('button[aria-label="Tip A2"]') as HTMLButtonElement).click());
    await act(async () => {
        const note = host.querySelector('input[type="text"]') as HTMLInputElement;
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(note, 'cover put back by hand');
        note.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const readsBefore = gets().length;
    await act(async () => { button('Save record').click(); button('Save record').click(); }); await tick();
    expect(api.post).toHaveBeenCalledTimes(1);
    const [path, body] = vi.mocked(api.post).mock.calls[0];
    expect(path).toBe('/api/bioxp/operator-controls/deck/record');
    expect(body).toEqual({ expected_connection_generation: 3, movable_objects: { REAGENT_COVER: 'LOC_RC_COVER' },
        tip_trays: [{ tray: 2, tips: ['A1', 'B1', 'C1', 'D1', 'E1', 'F1', 'G1', 'H1'] }, { tray: 3, tips: 'full' }],
        reason: 'cover put back by hand' });
    expect(host.textContent).toContain('Set their deck location too. (Output cover)');
    expect(gets().length).toBeGreaterThan(readsBefore);
    expect(gets().slice(readsBefore)).toEqual(expect.arrayContaining(['/api/bioxp/operator-controls/deck/state', '/api/bioxp/operator-controls/tip-trays']));
    expect(select('Set Reagent cover to').value).toBe('');
});

it('shows and records the tip size per rack', async () => {
    vi.mocked(api.get).mockImplementation(async path => {
        if (path === '/api/bioxp/operator-controls/deck/state') return { data: deckState() } as never;
        if (path === '/api/bioxp/operator-controls/tip-trays') {
            const data = tipTrays();
            data.trays[0] = { ...data.trays[0], tip_type: 'T10' } as never;
            data.trays[3] = { ...data.trays[3], tip_type: 'T50' } as never;
            return { data } as never;
        }
        throw new Error(`Unexpected GET ${path}`);
    });
    vi.mocked(api.post).mockResolvedValue({ data: { schema_version: 'bioxp.operator_deck_record_result.v1', record_id: 'auto-2',
        motion_commanded: false, physical_observation: false } } as never);
    await render(<BioXpDeckRecordPanel generation={3} connected />);
    expect(host.querySelector('[data-testid="tips-1"]')!.textContent).toBe('10 µL · 96/96 tips · 24 pickups');
    await choose('Set Tips 1 size', '10'); // already 10: omitted
    await choose('Set Tips 2 size', '10');
    await act(async () => button('Save record').click()); await tick();
    expect(vi.mocked(api.post).mock.calls[0][1]).toEqual({ expected_connection_generation: 3, tip_trays: [{ tray: 2, tip_type: 10 }] });
});

it('shows a robot refusal without retrying', async () => {
    vi.mocked(api.post).mockRejectedValue({ response: { status: 422, data: { detail: { error: 'deck_record_invalid_objects' } } } });
    await render(<BioXpDeckRecordPanel generation={3} connected />);
    await choose('Set Gripper to', 'OUTPUT_COVER');
    await act(async () => button('Save record').click()); await tick();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.post).mock.calls[0][1]).toEqual({ expected_connection_generation: 3, gantry: 'OUTPUT_COVER' });
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
});

it('Motion toggle: On follows the robot catalog, Off is never disabled by sensor state', async () => {
    const on = vi.fn(), off = vi.fn();
    await render(<BioXpMotionToggle enabled={false} reason="Door open" connected onDisabledReason="Door open; close it first." onOn={on} onOff={off} />);
    expect(host.textContent).toContain('Motion Off');
    expect(button('On').disabled).toBe(true);
    expect(button('Off').disabled).toBe(false);
    await act(async () => button('Off').click()); expect(off).toHaveBeenCalledTimes(1);
    await render(<BioXpMotionToggle enabled connected onDisabledReason={null} pending onOn={on} onOff={off} />);
    expect(host.textContent).toContain('Motion On');
    expect(button('Off').disabled).toBe(false);
    await render(<BioXpMotionToggle enabled connected onDisabledReason={null} onOn={on} onOff={off} />);
    await act(async () => button('On').click()); expect(on).toHaveBeenCalledTimes(1);
    await render(<BioXpMotionToggle enabled={undefined} connected={false} onDisabledReason={null} onOn={on} onOff={off} />);
    expect(host.textContent).toContain('Motion Unknown');
    expect(button('Off').disabled).toBe(true);
});
