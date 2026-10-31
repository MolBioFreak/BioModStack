import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
    data: undefined as any, submission: undefined as any,
    planCalls: [] as Array<Record<string, unknown>>,
    readbackCalls: [] as Array<Record<string, unknown>>,
}));

vi.mock('../../src/lib/bioxpClient', () => ({
    useBioXpPipetteApplicationStatus: () => ({
        data: {
            ok: false,
            mode: 'plan_only',
            execution_admitted: false,
            physical_effect_verified: false,
            operations: ['load_tip', 'move_to_waste', 'detect_fluid', 'plunger_up', 'plunger_down'],
            dependencies: {
                gantry: { bound: false, authority: null, generation: 7, state: { ready: false }, blockers: ['gantry_reference_unavailable'] },
            },
            required_dependencies: ['gantry'],
            missing_dependencies: ['gantry'],
            dependency_blockers: ['gantry:unbound'],
            dependencies_satisfied: false,
            blocker: 'physical_pipette_execution_not_authorized',
        },
        error: null,
    }),
    usePlanBioXpPipetteApplication: () => ({
        data: undefined,
        error: null,
        isPending: false,
        mutate: (payload: Record<string, unknown>) => state.planCalls.push(payload),
    }),
    useReadBioXpPipetteReadback: () => ({
        data: state.data, submission: state.submission,
        newOperation: (payload: Record<string, unknown>) => state.readbackCalls.push({newOperation: true, ...payload}),
        error: null,
        isPending: false,
        mutate: (payload: Record<string, unknown>) => state.readbackCalls.push(payload),
    }),
    bioXpErrorText: (error: unknown) => String(error),
}));

import { BioXpPipetteAdvancedTools, BioXpPipetteControlPanel, BioXpSoftwareTipType } from '../../src/components/BioXpPipetteControlPanel';

let container: HTMLDivElement;
let root: Root;

const channel = (id: number, overrides: Record<string, unknown> = {}) => ({
    channel: id,
    pipette_id: id,
    available: true,
    initialized: true,
    software_initialized: true,
    tip_loaded: true,
    software_tip_loaded: true,
    hardware_truth_level: 'cached_transport_state',
    hardware_tip_status: null,
    hardware_pressure: null,
    oem_diagnosis: null,
    oem_error_queue: [],
    liquid_level_ul: 0,
    front_air_level_ul: 0,
    rear_air_level_ul: 0,
    last_command: null,
    ...overrides,
});

beforeEach(() => {
    state.data = undefined; state.submission = undefined;
    state.planCalls = [];
    state.readbackCalls = [];
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    document.body.replaceChildren();
});

describe('mounted BioXP pipette status and tools', () => {
    it.each([undefined, null, false, true])('keeps an absent stop observation unknown without erasing false (%s)', async allowToStop => {
        const pipettes = { channels: [], allow_to_stop: allowToStop } as never;
        await act(async () => root.render(<BioXpPipetteControlPanel connected generation={77} pipettes={pipettes} />));
        const term = [...container.querySelectorAll('dt')].find(node => node.textContent === 'Allow to stop')!;
        expect(term.nextElementSibling?.textContent).toBe(allowToStop == null ? 'unavailable' : String(allowToStop));
        expect(state.readbackCalls).toEqual([]);
    });
    it('shows returned hardware fields and reuses the deliberate newOperation owner', async () => {
        state.data = {truth_source:'live_hardware_queries', receipt_id:'read-1', semantic_ok:false,
            channels: [{channel:0, semantic_ok:true, firmware:{firmware:'fixture-fw'}, status:{ok:true}, tip:{ok:true, tip_loaded:false, reply_received:true}, pressure:{ok:true, pressure:0, hardware_truth_level:'hardware_query'}, data:{samples:[0,1]}}]};
        state.submission = {idempotencyKey:'identity-1', expectedConnectionGeneration:77};
        await act(async () => root.render(<BioXpPipetteControlPanel connected generation={77}/>));
        const row = container.querySelector('[data-pipette-channel="0"]')!;
        expect(row.textContent).toContain('no tip');
        expect(row.textContent).toContain('0');
        expect(container.querySelector('[data-returned-hardware]')?.textContent).toContain('fixture-fw');
        expect(container.querySelectorAll('[data-pipette-channel]')).toHaveLength(4);
        const read = [...container.querySelectorAll('button')].find(b => b.textContent === 'Read hardware again')!;
        expect(read.closest('details')).toBeNull();
        await act(async () => read.click());
        expect(state.readbackCalls).toEqual([{newOperation:true, include_data:false}]);
        expect(container.textContent).toContain('Request identity-1');
    });
    it('one row per channel: hardware values only from valid queries, errors surfaced, read on request', async () => {
        const pipettes = { channels: [
            channel(0, { hardware_tip_status: { ok: true, hardware_truth_level: 'hardware_query', tip_loaded: false },
                hardware_pressure: { ok: true, hardware_truth_level: 'hardware_query', pressure: 12.5 } }),
            channel(2, { available: false, hardware_pressure: { ok: false, hardware_truth_level: 'no_readback', pressure: 999 } }),
        ], allow_to_stop: true, last_error: { channel: 2, error_code: 17 }, last_group_transaction: { ok: false, outcome: 'condition_or_status_failed' },
            latest_receipt: null, tip_type: 10 } as never;
        await act(async () => root.render(<BioXpPipetteControlPanel generation={77} connected pipettes={pipettes} freshness={{ state: 'stale', age_s: 41.25, fresh_for_s: 30 }} />));
        const rows = [...container.querySelectorAll('[data-pipette-channel]')];
        expect(rows.map(r => r.getAttribute('data-pipette-channel'))).toEqual(['0', '1', '2', '3']);
        expect(rows[0].textContent).toContain('12.5');
        expect(rows[0].textContent).toContain('tip loaded');
        expect(rows[1].textContent).toContain('no report');
        expect(rows[2].textContent).toContain('unavailable');
        expect(container.textContent).not.toContain('999');
        expect(container.textContent).toContain('Tip type in software: 10');
        expect(container.textContent).toContain('41.25 s ago');
        expect(container.querySelector('[role="alert"]')?.textContent).toContain('channel 3 · code 17');
        expect(container.textContent).toContain('condition_or_status_failed');
        const read = [...container.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === 'Read live hardware')!;
        await act(async () => read.click());
        expect(state.readbackCalls).toEqual([{ include_data: false }]);
    });
    it('robot primitives follow catalog admission and dispatch the mapped action; planner is separate', async () => {
        const invokeCalls: Array<{ actionId: string; inputs: Record<string, unknown> }> = [];
        const action = (actionId: string, enabled: boolean, disabledReason: string | null = null) => ({ action_id: actionId, enabled, disabled_reason: disabledReason, unavailable_reason: null } as never);
        const actions = [action('route.liquid_tip_liquid_tip_post.08698e28', true), action('oem.z.scriptmove_to', true),
            action('route.liquid_fluid_detection_liquid_fluid_detection_post.a12feee3', false, 'Motion arm is not confirmed.'),
            action('oem.z.lift_pipette', true), action('oem.z.lower_pipette', true)];
        await act(async () => root.render(<BioXpPipetteAdvancedTools generation={77} connected actions={actions} invokeAction={(actionId, inputs) => invokeCalls.push({ actionId, inputs })} />));
        const buttons = [...container.querySelectorAll<HTMLButtonElement>('[data-physical-pipette-control]')];
        expect(buttons.map(b => [b.textContent, b.disabled])).toEqual([
            ['Assign source tip type / location (host only)', false], ['Move to waste physically', false],
            ['Detect fluid physically', true], ['Lift pipette head (Z)', false], ['Lower pipette head (Z)', false]]);
        expect(buttons[2].title).toBe('Motion arm is not confirmed.');
        await act(async () => buttons[2].click());
        expect(invokeCalls).toEqual([]);
        await act(async () => buttons[3].click());
        expect(invokeCalls).toEqual([{ actionId: 'oem.z.lift_pipette', inputs: { location_id: 'LOC_TC' } }]);
        expect(container.textContent).toContain('Planner dependencies: gantry:unbound');
        expect(state.planCalls).toEqual([]);
    });
    it('sets tip type only through the advertised action, including 10 µL', async () => {
        const calls: Array<{ actionId: string; inputs: Record<string, unknown> }> = [];
        const tipAction = { action_id: 'tip-type', informational_method: 'POST', informational_path: '/liquid/pipette/tip-type', enabled: true, disabled_reason: null, unavailable_reason: null } as never;
        const render = async (actions: unknown[], pipettes: unknown, invokePending = false) => act(async () => root.render(
            <BioXpSoftwareTipType connected pipettes={pipettes as never} actions={actions as never} invokePending={invokePending} invokeAction={(actionId, inputs) => calls.push({ actionId, inputs })} />));
        const button = () => container.querySelector<HTMLButtonElement>('[data-software-tip-type-control] button')!;
        await render([], { tip_type: 201, channels: [] });
        expect(button().disabled).toBe(true);
        expect(container.textContent).toContain('Currently 201');
        await render([tipAction], { tip_type: 201, channels: [] });
        const select = container.querySelector<HTMLSelectElement>('select[aria-label="Software tip type"]')!;
        expect([...select.options].map(o => Number(o.value))).toEqual([10, 50, 200, 201]);
        await act(async () => { select.value = '10'; select.dispatchEvent(new Event('change', { bubbles: true })); });
        await act(async () => button().click());
        expect(calls).toEqual([{ actionId: 'tip-type', inputs: { tip_type: 10 } }]);
        await render([tipAction], { tip_type: 10, channels: [] }, true);
        expect(button().disabled).toBe(true);
        await render([{ ...(tipAction as object), enabled: false, disabled_reason: 'Robot unavailable' }], { tip_type: 10, channels: [] });
        expect(button().title).toBe('Robot unavailable');
        expect(calls).toHaveLength(1);
    });
});
