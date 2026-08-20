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
            successive_move_queue: {},
        },
        error: null,
    },
    connectionGeneration: 1,
    history: {
        data: {
            receipts: [] as Array<Record<string, unknown>>,
        },
        error: null,
    },
    historyCalls: [] as number[],
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
                generation: state.connectionGeneration,
                freshness_budget_seconds: 1800,
                last_error: null,
            },
        },
        error: null,
    }),
    useBioXpOperatorDashboard: () => state.dashboard,
    useBioXpOperatorActionHistory: (...args: unknown[]) => {
        state.historyCalls.push(args[2] as number);
        return state.history;
    },
    bioXpReceiptIsNonTerminal: (receipt: { status?: unknown } | null | undefined): boolean =>
        typeof receipt?.status === 'string'
        && receipt.status !== 'completed'
        && receipt.status !== 'failed'
        && receipt.status !== 'rejected'
        && receipt.status !== 'blocked'
        && receipt.status !== 'cleared',
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

const xReceipt = (status: string, index = 0) => ({
    schema_version: 'bioxp.operator_action_receipt.v1',
    command_id: `cmd_x_${index}`,
    action_id: 'oem.x.move_steps',
    kind: 'primitive',
    safety_class: 'motion',
    status,
    idempotency_key: 'ik_x',
    idempotency_replay_enabled: false,
    ownership_generation: 1,
    started_at: '2026-08-18T00:00:00.000Z',
    finished_at: status === 'completed' ? '2026-08-18T00:00:01.000Z' : null,
    duration_ms: status === 'completed' ? 1000 : null,
    request_received_at: null,
    lock_acquired_at: null,
    admission_completed_at: null,
    provider_entry_at: null,
    provider_returned_at: null,
    receipt_persist_started_at: null,
    remote_acknowledged: false,
    controller_acknowledged: false,
    controller_terminal_state_verified: false,
    physical_effect_verified: false,
    automatic_retry: null,
    physical_outcome: null,
    persistence_fallback: null,
    machine_assessment: 'unverified',
    operator_assessment: null,
    operator_note: null,
    operator_assessment_idempotency_key: null,
    response: null,
    stage_receipts: [],
});

beforeEach(() => {
    state.admissionCalls = 0;
    state.invokeCalls = [];
    state.historyCalls = [];
    state.history.data.receipts = [];
    state.dashboard.data.x_axis.latest_receipt = null;
    state.dashboard.data.successive_move_queue = {};
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

    it('queues successive X moves while an X command is active and keeps Home fail-closed (R-B1/R-B2)', async () => {
        state.history.data.receipts = [xReceipt('acknowledged')];
        state.dashboard.data.successive_move_queue = {
            x: { active_command_id: 'cmd_prev', depth: 1, head_action_id: 'oem.x.move_steps', state: 'queued' },
        };

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home') as HTMLButtonElement;

        expect(movePositive.disabled).toBe(false);
        expect(movePositive.title).not.toContain('in progress');
        expect(home.disabled).toBe(true);
        expect(home.title).toContain('acknowledged');

        const queueStrip = article.querySelector('[data-testid="successive-move-queue"]') as HTMLElement;
        expect(queueStrip).not.toBeNull();
        expect(queueStrip.textContent).toContain('X:');
        expect(queueStrip.textContent).toContain('oem.x.move_steps');
        expect(state.admissionCalls).toBe(0);
    });

    it('disables X moves when the successive-move queue is full (R-B1 depth bound)', async () => {
        state.history.data.receipts = [xReceipt('acknowledged')];
        state.dashboard.data.successive_move_queue = {
            x: { active_command_id: 'cmd_prev', depth: 8, head_action_id: 'oem.x.move_steps', state: 'queued' },
        };

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;

        expect(movePositive.disabled).toBe(true);
        expect(movePositive.title).toContain('queue is full');
        expect(state.admissionCalls).toBe(0);
    });

    it('re-enables X controls from the terminal receipt even when the dashboard snapshot lags (R-A3)', async () => {
        state.history.data.receipts = [xReceipt('completed')];
        state.dashboard.data.x_axis.latest_receipt = {
            command_id: 'cmd_x',
            intent: 'x_move_steps',
            status: 'acknowledged',
        };

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home') as HTMLButtonElement;

        expect(movePositive.disabled).toBe(false);
        expect(home.disabled).toBe(false);
        expect(state.admissionCalls).toBe(0);
    });

    it('passes the selected depth to the history query and renders that many receipts (R-A5)', async () => {
        state.history.data.receipts = Array.from({ length: 30 }, (_, i) => xReceipt('completed', i));

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        expect(state.historyCalls.at(-1)).toBe(25);

        const select = [...container.querySelectorAll('select')]
            .find((node) => node.getAttribute('aria-label') === 'Recent robot actions depth') as HTMLSelectElement;
        expect(select).not.toBeUndefined();
        const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')?.set;
        setter?.call(select, '50');
        select.dispatchEvent(new Event('change', { bubbles: true }));
        await act(async () => {
            await Promise.resolve();
        });

        expect(state.historyCalls.at(-1)).toBe(50);
        const historySection = [...container.querySelectorAll('section')]
            .find((node) => node.querySelector('h2')?.textContent === 'Recent Robot Actions') as HTMLElement;
        const receiptArticles = [...historySection.querySelectorAll('article')]
            .filter((node) => node.textContent?.includes('oem.x.move_steps'));
        expect(receiptArticles.length).toBe(30);
        expect(state.admissionCalls).toBe(0);
    });

    it('keeps X controls usable when the connection token differs from the robot ownership generation (regression)', async () => {
        state.connectionGeneration = 3189298922692611;
        state.history.data.receipts = [];

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const home = buttons.find((button) => button.textContent === 'Home') as HTMLButtonElement;
        expect(movePositive.disabled).toBe(false);
        expect(home.disabled).toBe(false);
        expect(state.admissionCalls).toBe(0);
    });
});
