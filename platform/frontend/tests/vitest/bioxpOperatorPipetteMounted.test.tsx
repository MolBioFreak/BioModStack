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

import { BioXpPipetteAdvancedTools, BioXpPipetteControlPanel } from '../../src/components/BioXpPipetteControlPanel';

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
        expect(container.textContent).toContain('Tip type: 10 µL');
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
        const actions = [action('oem.z.scriptmove_to', false, 'Motion arm is not confirmed.'),
            action('oem.z.lift_pipette', true), action('oem.z.lower_pipette', true)];
        await act(async () => root.render(<BioXpPipetteAdvancedTools generation={77} connected actions={actions} invokeAction={(actionId, inputs) => invokeCalls.push({ actionId, inputs })} />));
        const buttons = [...container.querySelectorAll<HTMLButtonElement>('[data-physical-pipette-control]')];
        expect(buttons.map(b => [b.textContent, b.disabled])).toEqual([
            ['Move to waste physically', true], ['Lift pipette head (Z)', false], ['Lower pipette head (Z)', false]]);
        expect(buttons[0].title).toBe('Motion arm is not confirmed.');
        await act(async () => buttons[0].click());
        expect(invokeCalls).toEqual([]);
        await act(async () => buttons[1].click());
        expect(invokeCalls).toEqual([{ actionId: 'oem.z.lift_pipette', inputs: { location_id: 'LOC_TC' } }]);
        expect(container.textContent).toContain('Planner dependencies: gantry:unbound');
        expect(state.planCalls).toEqual([]);
    });
});
