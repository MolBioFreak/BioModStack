import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
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
        data: undefined,
        error: null,
        isPending: false,
        mutate: (payload: Record<string, unknown>) => state.readbackCalls.push(payload),
    }),
    bioXpErrorText: (error: unknown) => String(error),
}));

import { BioXpPipetteControlPanel } from '../../src/components/BioXpPipetteControlPanel';

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

describe('mounted BioXP four-channel pipette panel', () => {
    it('renders four fixed truthful channel cards and robot-owned disabled physical controls', async () => {
        const pipettes = {
            ok: false,
            transport: 'novo_usb_can',
            channels: [
                channel(0, {
                    hardware_tip_status: {
                        ok: true,
                        hardware_truth_level: 'hardware_query',
                        tip_loaded: false,
                    },
                    hardware_pressure: {
                        ok: true,
                        hardware_truth_level: 'hardware_query',
                        pressure: 12.5,
                    },
                }),
                channel(2, {
                    available: false,
                    pressure: 999,
                    hardware_pressure: {
                        ok: false,
                        hardware_truth_level: 'no_readback',
                        pressure: 999,
                    },
                }),
            ],
            channel_count: 4,
            live_query_performed: false,
            liquid_mutation_enabled: false,
            allow_to_stop: true,
            last_error: {
                channel: 2,
                error_code: 17,
                source: 'ClassPipetteCollection.handlePipetteMessage',
            },
            last_group_transaction: {
                ok: false,
                outcome: 'condition_or_status_failed',
            },
            latest_receipt: null,
            application: null,
            physical_effect_verified: false,
        } as never;

        await act(async () => {
            root.render(
                <BioXpPipetteControlPanel
                    generation={77}
                    connected
                    pipettes={pipettes}
                    freshness={{ state: 'stale', age_s: 41.25, fresh_for_s: 30 }}
                />,
            );
            await Promise.resolve();
        });

        const cards = [...container.querySelectorAll('[data-pipette-channel]')];
        expect(cards).toHaveLength(4);
        expect(cards.map((card) => card.getAttribute('data-pipette-channel'))).toEqual(['0', '1', '2', '3']);
        expect(container.textContent).toContain('Channel 1');
        expect(container.textContent).toContain('Channel 4');
        expect(cards[1].textContent).toContain('Unavailable — channel missing from projection');
        expect(cards[3].textContent).toContain('Unavailable — channel missing from projection');
        expect(cards[0].textContent).toContain('Hardware tip readback: not loaded');
        expect(cards[0].textContent).toContain('Hardware pressure: 12.5');
        expect(cards[2].textContent).toContain('No valid hardware readback');
        expect(cards[0].textContent).toContain('Software shadow: initialized; tip loaded');
        expect(container.textContent).not.toContain('"position"');
        expect(container.textContent).not.toContain('999');

        expect(container.textContent).toContain('Cached projection');
        expect(container.textContent).toContain('Stale snapshot · age 41.25 s');
        expect(container.textContent).toContain('Group error: channel 3 · code 17');
        expect(container.textContent).toContain('Last transaction: condition_or_status_failed');
        expect(container.textContent).toContain('Latest receipt: unavailable');
        expect(container.textContent).toContain('Application evidence: plan only; physical execution blocked');
        expect(container.textContent).toContain('Robot-owned blocker: physical_pipette_execution_not_authorized');
        expect(container.textContent).toContain('Dependency blockers: gantry:unbound');

        const physicalControls = [...container.querySelectorAll<HTMLButtonElement>('[data-physical-pipette-control]')];
        expect(physicalControls).toHaveLength(5);
        expect(physicalControls.every((button) => button.disabled)).toBe(true);
        expect(physicalControls.map((button) => button.textContent)).toEqual([
            'Load tip physically',
            'Move to waste physically',
            'Detect fluid physically',
            'Plunger up physically',
            'Plunger down physically',
        ]);
        expect(state.planCalls).toEqual([]);
        expect(state.readbackCalls).toEqual([]);

        const readbackButton = [...container.querySelectorAll<HTMLButtonElement>('button')]
            .find((button) => button.textContent === 'Read live hardware');
        expect(readbackButton).toBeDefined();
        await act(async () => {
            readbackButton?.click();
            await Promise.resolve();
        });
        expect(state.readbackCalls).toEqual([{ include_data: false }]);
    });

    it('enables physical controls from catalog admission and dispatches the mapped OEM action', async () => {
        const invokeCalls: Array<{ actionId: string; inputs: Record<string, unknown> }> = [];
        const action = (actionId: string, enabled: boolean, disabledReason: string | null = null) => ({
            action_id: actionId,
            label: actionId,
            subsystem: 'motion',
            category: 'oem',
            kind: 'primitive',
            safety_class: 'motion',
            description: 'test action',
            source_anchor: null,
            informational_method: 'POST',
            informational_path: '/test',
            provider_available: true,
            provider_unavailable_reason: null,
            available: enabled,
            unavailable_reason: null,
            enabled,
            disabled_reason: disabledReason,
            dependencies: [],
            requires_confirmation: false,
            timeout_seconds: 30,
            required_provider_capability: null,
            inputs: [],
            stages: [],
        } as never);
        const actions = [
            action('route.liquid_tip_liquid_tip_post.08698e28', true),
            action('oem.z.scriptmove_to', true),
            action('route.liquid_fluid_detection_liquid_fluid_detection_post.a12feee3', false, 'Motion arm is not confirmed.'),
            action('oem.z.lift_pipette', true),
            action('oem.z.lower_pipette', true),
        ];

        await act(async () => {
            root.render(
                <BioXpPipetteControlPanel
                    generation={77}
                    connected
                    pipettes={null as never}
                    freshness={{ state: 'fresh', age_s: 3.2, fresh_for_s: 30 }}
                    actions={actions}
                    invokePending={false}
                    invokeAction={(actionId, inputs) => invokeCalls.push({ actionId, inputs })}
                />,
            );
            await Promise.resolve();
        });

        const buttons = [...container.querySelectorAll<HTMLButtonElement>('[data-physical-pipette-control]')];
        expect(buttons).toHaveLength(5);
        expect(buttons.map((button) => ({ label: button.textContent, disabled: button.disabled }))).toEqual([
            { label: 'Load tip physically', disabled: false },
            { label: 'Move to waste physically', disabled: false },
            { label: 'Detect fluid physically', disabled: true },
            { label: 'Plunger up physically', disabled: false },
            { label: 'Plunger down physically', disabled: false },
        ]);
        const blocked = buttons.find((button) => button.textContent === 'Detect fluid physically');
        expect(blocked?.title).toBe('Motion arm is not confirmed.');

        await act(async () => {
            buttons.find((button) => button.textContent === 'Plunger up physically')?.click();
            await Promise.resolve();
        });
        expect(invokeCalls).toEqual([
            { actionId: 'oem.z.lift_pipette', inputs: { location_id: 'LOC_TC' } },
        ]);
    });
});
