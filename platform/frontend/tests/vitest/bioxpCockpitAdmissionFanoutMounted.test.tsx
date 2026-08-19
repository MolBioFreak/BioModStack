import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
    admissionCalls: 0,
    invokeCalls: [] as Array<Record<string, unknown>>,
    catalog: {
        data: {
            machine_serial: '206',
            ownership_generation: 1,
            source_authority_verified: true,
            dashboard: {
                pipettes: [],
                snapshot: { freshness: null },
            },
            actions: [] as Array<Record<string, unknown>>,
        },
        error: null,
    },
    dashboard: {
        data: {
            motion: { enabled: true, reason: null },
            x_axis: {
                axis: 'x',
                status: {
                    axis: 'x',
                    reference: 'referenced',
                    position_steps: 0,
                    speed_steps_s: 0,
                    left_switch_state: 1,
                    right_switch_state: 1,
                    left_switch_disabled: false,
                    right_switch_disabled: false,
                    coordinate_contract: 'serial206_x_source_0_90263_effective_min_60_relative_margin_20',
                    min_steps: 0,
                    max_steps: 90263,
                    physical_position_verified: true,
                },
                provider: {
                    lifecycle: {
                        schema_version: 'bioxp.serial206_x_lifecycle.v2',
                        state: 'referenced_ready',
                        generation: 1,
                        board_lifecycle_generation: 1,
                        reference_state: 'referenced',
                        prepared_receipt: null,
                        active_receipt: null,
                        pending_ticket: null,
                        awaiting_observation_receipt_id: null,
                        terminal_state: null,
                        last_failure: null,
                    },
                    current_generation: 1,
                    reference_state: 'referenced',
                    state: 'referenced_ready',
                    live_status: {
                        position_steps: 0,
                        speed_steps_s: 0,
                        max_speed: 'unknown',
                        max_acceleration: 'unknown',
                        max_current: 'unknown',
                        stall_guard: 'unknown',
                        profile_verified: true,
                        switch_mask_verified: true,
                    },
                    profile: { verified: true },
                    switch_masks: { verified: true },
                },
                last_failure: null,
                latest_receipt: null,
            },
            z_axis: {
                axis: 'z',
                status: {
                    axis: 'z',
                    reference: 'referenced',
                    position_steps: 0,
                    speed_steps_s: 0,
                    left_switch_state: 1,
                    right_switch_state: 1,
                    left_switch_disabled: false,
                    right_switch_disabled: false,
                },
                provider: {
                    state: 'referenced_ready',
                },
            },
        },
        error: null,
    },
}));

const dep = (key: string, met: boolean, reason: string | null = null) => ({ key, met, reason });

const xMoveAction = () => ({
    action_id: 'oem.x.move_steps',
    label: 'X Relative Move',
    subsystem: 'motion.x',
    category: 'route',
    kind: 'primitive',
    safety_class: 'motion',
    description: 'X relative move',
    source_anchor: 'OEM source',
    informational_method: 'POST',
    informational_path: '/motion/oem/manual/relative',
    provider_available: true,
    provider_unavailable_reason: null,
    available: true,
    unavailable_reason: null,
    enabled: false,
    disabled_reason: 'Requested X relative delta exceeds the maximum source-margin span; live target preflight remains provider-owned.',
    dependencies: [
        dep('provider_available', true),
        dep('serial206_x_lifecycle', true),
        dep('transport_live', true),
        dep('operation_allows_motion', true),
        dep('x_relative_oem_envelope', false, 'Requested X relative delta exceeds the maximum source-margin span; live target preflight remains provider-owned.'),
    ],
    requires_confirmation: false,
    timeout_seconds: 30,
    inputs: [{ name: 'steps', type: 'integer', required: true }],
    stages: [],
});

const xAbsoluteAction = () => ({
    action_id: 'oem.x.move_absolute',
    label: 'X Absolute Move',
    subsystem: 'motion.x',
    category: 'route',
    kind: 'primitive',
    safety_class: 'motion',
    description: 'X absolute move',
    source_anchor: 'OEM source',
    informational_method: 'POST',
    informational_path: '/motion/oem/manual/absolute',
    provider_available: true,
    provider_unavailable_reason: null,
    available: true,
    unavailable_reason: null,
    enabled: false,
    disabled_reason: 'Requested X target is outside the OEM 0..90263 envelope.',
    dependencies: [
        dep('provider_available', true),
        dep('serial206_x_lifecycle', true),
        dep('transport_live', true),
        dep('operation_allows_motion', true),
        dep('x_target_oem_envelope', false, 'Requested X target is outside the OEM 0..90263 envelope.'),
    ],
    requires_confirmation: false,
    timeout_seconds: 30,
    inputs: [{ name: 'position_steps', type: 'integer', required: true, minimum: 60, maximum: 90263 }],
    stages: [],
});

const xHomeAction = () => ({
    action_id: 'oem.x.manual_panel_home',
    label: 'X Home',
    subsystem: 'motion.x',
    category: 'route',
    kind: 'primitive',
    safety_class: 'motion',
    description: 'X home',
    source_anchor: 'OEM source',
    informational_method: 'POST',
    informational_path: '/motion/oem/manual/home',
    provider_available: true,
    provider_unavailable_reason: null,
    available: true,
    unavailable_reason: null,
    enabled: true,
    disabled_reason: null,
    dependencies: [
        dep('provider_available', true),
        dep('serial206_x_lifecycle', true),
        dep('transport_live', true),
        dep('operation_allows_motion', true),
    ],
    requires_confirmation: false,
    timeout_seconds: 30,
    inputs: [],
    stages: [],
});

vi.mock('../../src/lib/bioxpClient', () => ({
    useBioXpStatus: () => ({
        data: {
            connection: {
                active: true,
                reachable: true,
                configured: true,
                generation: 1,
                freshness_budget_seconds: 1800,
                last_error: null,
            },
        },
        error: null,
    }),
    useBioXpOperatorDashboard: () => state.dashboard,
    useBioXpOperatorActionHistory: () => ({ data: { receipts: [] }, error: null }),
    useBioXpOperatorControlCatalog: () => state.catalog,
    useBioXpOperatorActionAdmission: (...args: unknown[]) => {
        state.admissionCalls += 1;
        return { data: { enabled: true, disabled_reason: null, dependencies: [] }, error: null };
    },
    useInvokeBioXpOperatorAction: () => ({
        data: undefined,
        error: null,
        isPending: false,
        mutate: (payload: Record<string, unknown>) => state.invokeCalls.push(payload),
        reset: vi.fn(),
    }),
    useConnectBioXp: () => ({ data: undefined, error: null, isPending: false, mutate: vi.fn() }),
    useDisconnectBioXp: () => ({ data: undefined, error: null, isPending: false, mutate: vi.fn() }),
    useRecoverBioXpMotion: () => ({ data: undefined, error: null, isPending: false, mutate: vi.fn(), reset: vi.fn() }),
    useUpdateBioXpFreshness: () => ({ data: undefined, error: null, isPending: false, mutate: vi.fn() }),
    bioXpErrorText: (error: unknown) => String(error),
}));

vi.mock('../../src/components/BioXpCameraPanel', () => ({ BioXpCameraPanel: () => null }));
vi.mock('../../src/components/BioXpOperatorControlTabs', () => ({ BioXpOperatorControlTabs: () => null }));
vi.mock('../../src/components/BioXpPipetteControlPanel', () => ({ BioXpPipetteControlPanel: () => null }));
vi.mock('../../src/components/BioXpQuickDashboard', () => ({ BioXpQuickDashboard: () => null }));

import { BioXpCockpit } from '../../src/components/BioXpCockpit';

let container: HTMLDivElement;
let root: Root;

const setXAbsolute = async (value: string) => {
    const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
    const inputs = [...article.querySelectorAll('input[type="number"]')] as HTMLInputElement[];
    const absolute = inputs[1];
    await act(async () => {
        const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
        valueSetter?.call(absolute, value);
        absolute.dispatchEvent(new Event('input', { bubbles: true }));
        await Promise.resolve();
    });
};

beforeEach(() => {
    state.admissionCalls = 0;
    state.invokeCalls = [];
    state.catalog.data.actions = [
        xMoveAction(),
        xAbsoluteAction(),
        xHomeAction(),
        {
            action_id: 'oem.x.stop',
            label: 'X Stop',
            subsystem: 'motion.x',
            category: 'route',
            kind: 'primitive',
            safety_class: 'stop',
            description: 'X stop',
            source_anchor: 'OEM source',
            informational_method: 'POST',
            informational_path: '/motion/oem/manual/stop',
            provider_available: true,
            provider_unavailable_reason: null,
            available: true,
            unavailable_reason: null,
            enabled: true,
            disabled_reason: null,
            dependencies: [],
            requires_confirmation: false,
            timeout_seconds: 5,
            inputs: [],
            stages: [],
        },
        {
            action_id: 'oem.abort_all',
            label: 'Aggregate Abort',
            subsystem: 'motion.x',
            category: 'route',
            kind: 'primitive',
            safety_class: 'emergency',
            description: 'abort',
            source_anchor: 'OEM source',
            informational_method: 'POST',
            informational_path: '/motion/oem/abort',
            provider_available: true,
            provider_unavailable_reason: null,
            available: true,
            unavailable_reason: null,
            enabled: true,
            disabled_reason: null,
            dependencies: [],
            requires_confirmation: false,
            timeout_seconds: 5,
            inputs: [],
            stages: [],
        },
        {
            action_id: 'meta.activate_motion',
            label: 'Activate Motion',
            subsystem: 'meta',
            category: 'meta',
            kind: 'meta',
            safety_class: 'motion',
            description: 'activate',
            source_anchor: 'OEM source',
            informational_method: 'POST',
            informational_path: '/operator/actions/meta.activate_motion',
            provider_available: true,
            provider_unavailable_reason: null,
            available: true,
            unavailable_reason: null,
            enabled: true,
            disabled_reason: null,
            dependencies: [],
            requires_confirmation: false,
            timeout_seconds: 5,
            inputs: [],
            stages: [],
        },
    ];
    state.dashboard.data.x_axis.provider.lifecycle.state = 'referenced_ready';
    state.dashboard.data.x_axis.status.reference = 'referenced';
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    document.body.replaceChildren();
});

describe('mounted BioXP cockpit admission fan-out collapse (R-A1)', () => {
    it('derives X enablement from catalog and dashboard with zero always-on admission calls', async () => {
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        expect(state.admissionCalls).toBe(0);
        expect(document.body.textContent).not.toContain('Checking exact robot admission.');

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home') as HTMLButtonElement;
        const goAbsolute = buttons.find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;

        expect(movePositive.disabled).toBe(false);
        expect(home.disabled).toBe(false);
        expect(goAbsolute.disabled).toBe(false);

        await act(async () => movePositive.click());
        expect(state.invokeCalls[0]).toEqual({
            actionId: 'oem.x.move_steps',
            connectionGeneration: 1,
            ownershipGeneration: 1,
            inputs: { steps: 10000 },
        });

        await act(async () => home.click());
        expect(state.invokeCalls[1]).toEqual({
            actionId: 'oem.x.manual_panel_home',
            connectionGeneration: 1,
            ownershipGeneration: 1,
            inputs: {},
        });

        await act(async () => goAbsolute.click());
        expect(state.invokeCalls[2]).toEqual({
            actionId: 'oem.x.move_absolute',
            connectionGeneration: 1,
            ownershipGeneration: 1,
            inputs: { position_steps: 60 },
        });
        expect(state.admissionCalls).toBe(0);
    });

    it('keeps X move controls fail-closed while Home stays available when the axis is unprepared', async () => {
        state.dashboard.data.x_axis.provider.lifecycle.state = 'unprepared';
        state.dashboard.data.x_axis.status.reference = 'desynced';

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home') as HTMLButtonElement;
        const goAbsolute = buttons.find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;

        expect(movePositive.disabled).toBe(true);
        expect(movePositive.title).toContain("Current X lifecycle state 'unprepared'");
        expect(goAbsolute.disabled).toBe(true);
        expect(goAbsolute.title).toContain("Current X lifecycle state 'unprepared'");
        expect(home.disabled).toBe(false);
        expect(state.admissionCalls).toBe(0);
    });

    it('applies the local X envelope gate to absolute targets', async () => {
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        await setXAbsolute('999999');

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const goAbsolute = [...article.querySelectorAll('button')].find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;
        expect(goAbsolute.disabled).toBe(true);
        expect(goAbsolute.title).toContain('Requested X target must be an integer from 60 through 90263.');
        expect(state.admissionCalls).toBe(0);
    });
});
