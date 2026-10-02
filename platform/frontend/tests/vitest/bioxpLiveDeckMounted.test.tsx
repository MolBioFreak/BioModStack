import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { BioXpLiveDeck, type BioXpLiveDeckProps } from '../../src/components/BioXpLiveDeck';
import type { BioXpOperatorDashboardV2 } from '../../src/lib/bioxpClient';
import { deckResources } from '../../src/lib/bioxpWorkflowDeck';

let host: HTMLDivElement, root: Root, query: QueryClient;
let props: BioXpLiveDeckProps;
const station = vi.fn(), well = vi.fn();
const settings = { active_motion_positions: [{ location_id: 'LOC_MS', base_coordinates: { x: 51000, y: 16000 }, inc_factor: 1 }],
    saved_motion_positions: [{ location_id: 'LOC_MS', base_coordinates: { x: 1, y: 2 }, inc_factor: 1 }] };
function dashboard(x: number | null = 50000, y: number | null = 10000, at = Date.now() / 1000): BioXpOperatorDashboardV2 {
    return { ownership_generation: 4, generated_at: Date.now() / 1000,
        deck: { current_location: 'LOC_MS', current_well: 0, position_table_revision: 'rev-a',
            head_alignment: { tip_location: 2, semantic_state_revision: 1, producer_operation: 'fixture', producer_command_id: 'inert', ownership_generation: 4 } },
        telemetry: { axes: [{ axis: 'x', position_steps: x, reference: 'referenced' }, { axis: 'y', position_steps: y, reference: 'referenced' }, { axis: 'z', position_steps: null }],
            snapshot: { snapshot_id: 'inert', observed_at: at, domain_observed_at: { axes: at }, freshness: { state: 'fresh', fresh_for_s: 30 } } }
    } as unknown as BioXpOperatorDashboardV2;
}
async function render(next: Partial<BioXpLiveDeckProps> = {}) { props = { ...props, ...next }; await act(async () => { root.render(<QueryClientProvider client={query}><BioXpLiveDeck {...props} /></QueryClientProvider>); }); }
function el(label: string) { const e = host.querySelector(`[aria-label="${label}"]`); expect(e, label).not.toBeNull(); return e!; }
async function click(label: string, init: MouseEventInit = {}) { await act(async () => el(label).dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1, ...init }))); }
async function key(label: string, key: string, init: KeyboardEventInit = {}) { await act(async () => el(label).dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key, ...init }))); }
async function button(text: string) { await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === text)!.click()); }
beforeEach(() => {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); station.mockClear(); well.mockClear();
    vi.spyOn(api, 'get').mockResolvedValue({ data: settings });
    vi.spyOn(api, 'post').mockRejectedValue(new Error('No leaf mutation allowed'));
    props = { generation: 1, connected: true, visible: true, stale: false, dashboard: dashboard(), selection: { station: '', wells: [] },
        onMoveToStation: station, onMoveToWell: well, stationDisabledReason: () => null, wellDisabledReason: null,
        doorControls: <div>Door slot</div>, movementControls: <div>Movement slot</div>, commandDetails: <div>Receipt slot</div>, transferControls: <div>Transfer slot</div> };
});
afterEach(async () => { await act(async () => root.unmount()); query.clear(); host.remove(); vi.restoreAllMocks(); });

it('shares the full deck with exact station/well callbacks for every rendered address; GET only', async () => {
    await render();
    expect(host.querySelectorAll('[data-well]')).toHaveLength(800);
    for (const resource of deckResources) for (const point of resource.points) {
        await click(`${resource.label} ${point.well}`);
        expect(well).toHaveBeenLastCalledWith(resource.locationId, point.well);
    }
    expect(well).toHaveBeenCalledTimes(800); expect(station).not.toHaveBeenCalled();
    await click('Select Waste bin'); await click('Select Park target'); await click('Select Output cover storage');
    expect(station.mock.calls).toEqual([['WASTE_BIN'], ['LOC_PARK'], ['LOC_OC_COVER_STORAGE']]);
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith('/api/bioxp/calibration-settings', { params: { expected_connection_generation: 1 } });
    expect(api.post).not.toHaveBeenCalled();
    expect(host.querySelector('[data-testid="oem-deck-movement"]')!.textContent).toContain('Door slot');
    expect([...host.querySelectorAll('details')].every(d => !d.open)).toBe(true);
});
it('renders a vertical rail and four recognizable gun modules with fixed source centers; destination is not position', async () => {
    await render();
    const rail = host.querySelector('[data-testid="vertical-deck-arm"]')!;
    expect(Number(rail.getAttribute('height'))).toBeGreaterThan(Number(rail.getAttribute('width')));
    const guns = [...host.querySelectorAll('[data-gun-module]')]; expect(guns).toHaveLength(4);
    for (const gun of guns) { expect(gun.querySelector('.bld-gun-housing')).not.toBeNull(); expect(gun.querySelector('.bld-gun-motor')).not.toBeNull(); }
    const transforms = guns.map(g => g.getAttribute('transform'));
    await render({ selection: { station: 'LOC_OC', wells: ['H12'] } });
    expect([...host.querySelectorAll('[data-gun-module]')].map(g => g.getAttribute('transform'))).toEqual(transforms);
    expect(station).not.toHaveBeenCalled(); expect(well).not.toHaveBeenCalled();
    expect(host.textContent).toContain('Z stepsUnavailable');
    expect(host.textContent).toContain('Channel 3 (source 2)');
    const ring = el('Magnetic station A1').querySelector('.bwd-ring')!;
    await vi.waitFor(() => expect(Number(ring.getAttribute('cx'))).toBeCloseTo(60 + 2 * (119541.50999505838 - 51000) / 236.94));
});
it('focus/arrows/shift exploration, pan/zoom/focus/fit and hidden-return never submit; double click/key repeat submit once', async () => {
    await render({ selection: { station: 'LOC_MS', wells: [] } });
    await act(async () => (el('Magnetic station A1') as SVGElement).focus());
    await key('Magnetic station A1', 'ArrowDown', { shiftKey: true });
    await button('Focus station'); await button('Fit deck'); await click('Zoom in'); await click('Pan right');
    await render({ visible: false }); await render({ visible: true });
    expect(well).not.toHaveBeenCalled(); expect(station).not.toHaveBeenCalled();
    await click('Magnetic station A1', { shiftKey: true }); await click('Magnetic station A1', { detail: 2 });
    expect(well.mock.calls).toEqual([[0, 'A1']]);
    await key('Magnetic station B1', 'Enter'); await key('Magnetic station B1', 'Enter', { repeat: true });
    await key('Magnetic station C1', ' '); await key('Magnetic station C1', ' ', { repeat: true });
    expect(well.mock.calls).toEqual([[0, 'A1'], [0, 'B1'], [0, 'C1']]);
    expect(api.get).toHaveBeenCalledTimes(1); expect(api.post).not.toHaveBeenCalled();
});
it('suppresses target drags and pans without suppressing the next deliberate tap', async () => {
    await render();
    const target = el('Magnetic station A1');
    const pointer = async (node: Element, name: string, x: number, y: number) => act(async () => node.dispatchEvent(new MouseEvent(name, { bubbles: true, button: 0, clientX: x, clientY: y })));
    await pointer(target, 'pointerdown', 10, 10); await pointer(target, 'pointermove', 40, 50); await pointer(target, 'pointerup', 40, 50);
    await click('Magnetic station A1'); expect(well).not.toHaveBeenCalled();
    await pointer(target, 'pointerdown', 10, 10); await pointer(target, 'pointerup', 10, 10); await click('Magnetic station A1');
    expect(well).toHaveBeenCalledTimes(1);
    const svg = el('BioXP deck map'); await pointer(svg, 'pointerdown', 10, 10); await pointer(svg, 'pointermove', 40, 50); await pointer(svg, 'pointerup', 40, 50);
    expect(well).toHaveBeenCalledTimes(1); expect(station).not.toHaveBeenCalled();
});
it('keeps missing/stale pose as display state, retains same-connection position, clears on generation replacement', async () => {
    await render(); const initial = host.querySelector('[data-gun-module]')!.getAttribute('transform');
    await render({ dashboard: dashboard(null), stale: true });
    expect(host.textContent).toContain('Last known position'); expect(host.querySelector('[data-gun-module]')!.getAttribute('transform')).toBe(initial);
    await click('Select Waste bin'); expect(station).toHaveBeenLastCalledWith('WASTE_BIN');
    await render({ generation: 2 }); expect(host.querySelector('[data-testid="reported-gantry"]')).toBeNull(); expect(host.textContent).toContain('Position unavailable');
    await render({ dashboard: dashboard(0, 0), stale: false }); expect(host.querySelector('[data-testid="reported-gantry"]')).not.toBeNull();
});
it('read failure/reference layout does not gate intent; revision invalidates geometry and hidden demand does not read', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('offline fixture'));
    await render({ visible: false, dashboard: null }); expect(api.get).not.toHaveBeenCalled();
    await render({ visible: true }); await vi.waitFor(() => expect(host.textContent).toContain('Geometry read unavailable'));
    expect(host.textContent).toContain('Reference layout'); await click('Strip 1 H1'); expect(well).toHaveBeenLastCalledWith(11, 'H1');
    vi.mocked(api.get).mockResolvedValue({ data: settings });
    await render({ dashboard: dashboard() }); await vi.waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    const d = dashboard(); d.deck!.position_table_revision = 'rev-b'; await render({ dashboard: d }); await vi.waitFor(() => expect(api.get).toHaveBeenCalledTimes(3));
});
it('preserves epoch-zero observation time and uses friendly requested station labels', async () => {
    await render({ dashboard: dashboard(0, 0, 0), selection: { station: 'LOC_TC', wells: ['H12'] } });
    const card = el('Reported pose');
    expect(card.textContent).toContain(new Date(0).toLocaleString());
    expect(card.textContent).not.toContain('Observation time unavailable');
    expect(card.textContent).toContain('Requested destinationThermal cycler · H12');
    expect(card.querySelector('[title="LOC_TC"]')).not.toBeNull();
});
it('can inspect another station without requesting travel and keeps view through hidden tab changes', async () => {
    await render();
    await act(async () => { const select = el('Deck view station') as HTMLSelectElement; select.value = 'TECANRACK4'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    await button('Focus station'); const view = el('BioXP deck map').getAttribute('viewBox');
    await render({ visible: false }); await render({ visible: true });
    expect(el('BioXP deck map').getAttribute('viewBox')).toBe(view);
    expect(station).not.toHaveBeenCalled(); expect(well).not.toHaveBeenCalled();
});
it('rejects late old-generation geometry and cannot resurrect a prior connection pose', async () => {
    let oldReply!: (response: { data: typeof settings }) => void;
    vi.mocked(api.get).mockImplementationOnce(() => new Promise(resolve => { oldReply = resolve; }));
    await render();
    await render({ generation: 2, dashboard: null });
    await vi.waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(host.textContent).toContain('Active geometry'));
    const before = el('Magnetic station A1').querySelector('.bwd-ring')!.getAttribute('cx');
    await act(async () => oldReply({ data: { ...settings, active_motion_positions: [{ ...settings.active_motion_positions[0], base_coordinates: { x: 1, y: 2 } }] } }));
    expect(el('Magnetic station A1').querySelector('.bwd-ring')!.getAttribute('cx')).toBe(before);
    expect(host.querySelector('[data-testid="reported-gantry"]')).toBeNull();
    expect(station).not.toHaveBeenCalled(); expect(well).not.toHaveBeenCalled();
});
it('respects only parent action admission, not missing pose/geometry, and preserves disabled reasons', async () => {
    await render({ dashboard: null, connected: false, wellDisabledReason: 'Submitting', stationDisabledReason: () => 'Submitting' });
    await click('Select Waste bin'); await click('Magnetic station A1'); expect(station).not.toHaveBeenCalled(); expect(well).not.toHaveBeenCalled();
    expect(el('Magnetic station A1').getAttribute('aria-disabled')).toBe('true');
    await render({ wellDisabledReason: null, stationDisabledReason: () => null });
    await key('Select Waste bin', 'Enter'); await key('Magnetic station A1', ' '); expect(station).toHaveBeenCalledTimes(1); expect(well).toHaveBeenCalledTimes(1);
});
