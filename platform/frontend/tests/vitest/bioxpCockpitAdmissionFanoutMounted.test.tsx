import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
    admissionCalls: 0,
    v1DashboardEnabled: null as boolean | null,
    v1CatalogEnabled: null as boolean | null,
    invokeCalls: [] as Array<Record<string, unknown>>,
    invokePending: false,
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
    yInvokeCalls: [] as Array<Record<string, unknown>>,
    yInterruptCalls: [] as Array<Record<string, unknown>>,
    yInvokeError: null as unknown,
    yInterruptError: null as unknown,
    yInvokeData: undefined as Record<string, unknown> | undefined,
    yInterruptData: undefined as Record<string, unknown> | undefined,
    yReceipt: { data: undefined as Record<string, unknown> | undefined, error: null as unknown, isStale: false },
    v2Dashboard: {
        data: {
            schema_version: 'bioxp.operator_dashboard.v2',
            generated_at: 1,
            ownership_generation: 1,
            board4: {
                state: 'active', prior_board_epoch: 1, active_board_epoch: 2,
                transition_phase: 'committed', transition_evidence: {},
                member_motors: { y: 0, z: 1, gripper: 2 }, state_version: 3, updated_at: 1,
            },
            y_axis: {
                axis: 'y', board_id: 4, motor_id: 0, ownership_generation: 1,
                prior_board_epoch: 1, active_board_epoch: 2, prepared_board_epoch: 2,
                lifecycle_state: 'referenced_ready', reference_state: 'referenced',
                position_steps: 100, position_reply_valid: true, position_status_code: 100,
                speed_steps_s: 0, speed_reply_valid: true, speed_status_code: 100,
                left_switch_raw: 0, left_switch_reply_valid: true, left_switch_status_code: 100,
                home_effective: false, profile_fingerprint: 'a'.repeat(64), profile_readback_valid: true,
                profile_mismatches: [], active_command: null, interrupt_epoch: 0,
                latest_compact_receipt: null as Record<string, unknown> | null, last_discrepancy_steps: null,
                state_version: 4, updated_at: 1, physical_position_verified: false,
            },
            active_commands: [], command_queue: { schema_version: 'bioxp.oem_command_queue.v1', generated_at: 1, items: [] }, latest_receipts: [],
        },
        error: null as unknown,
        isStale: false,
    },
    v2Catalog: {
        data: undefined as Record<string, unknown> | undefined,
        error: null as unknown,
        isStale: false,
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
    enabled: true,
    disabled_reason: null,
    dependencies: [
        dep('provider_available', true),
        dep('serial206_x_lifecycle', true),
        dep('transport_live', true),
        dep('operation_allows_motion', true),
        dep('x_relative_oem_envelope', true),
    ],
    requires_confirmation: false,
    timeout_seconds: 30,
    inputs: [{ name: 'steps', type: 'integer', required: true, minimum: -90243, maximum: 90243 }],
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
    enabled: true,
    disabled_reason: null,
    dependencies: [
        dep('provider_available', true),
        dep('serial206_x_lifecycle', true),
        dep('transport_live', true),
        dep('operation_allows_motion', true),
        dep('x_target_oem_envelope', true),
    ],
    requires_confirmation: false,
    timeout_seconds: 30,
    inputs: [{ name: 'position_steps', type: 'integer', required: true, minimum: 60, maximum: 90263 }],
    stages: [],
});

const zHomeAction = () => ({
    ...xMoveAction(),
    action_id: 'oem.z.manual_home',
    label: 'Z OEM Home',
    subsystem: 'motion.z',
    informational_path: '/motion/oem/manual/home',
    enabled: true,
    disabled_reason: null,
    unavailable_reason: null,
    dependencies: [
        dep('provider_available', true),
        dep('serial206_z_lifecycle', true),
        dep('transport_live', true),
        dep('operation_allows_motion', true),
    ],
    inputs: [],
});

const zMoveAction = () => ({
    ...xMoveAction(),
    action_id: 'oem.z.move_steps',
    label: 'Z Relative Move',
    subsystem: 'motion.z',
    informational_path: '/motion/oem/manual/relative',
    dependencies: [dep('stale_bms_projection', false, 'Stale BMS projection')],
    inputs: [{ name: 'steps', type: 'integer', required: true, minimum: -250000, maximum: 250000 }],
});

const zAbsoluteAction = () => ({
    ...xAbsoluteAction(),
    action_id: 'oem.z.move_absolute',
    label: 'Z Absolute Move',
    subsystem: 'motion.z',
    dependencies: [dep('stale_bms_projection', false, 'Stale BMS projection')],
    inputs: [{ name: 'position_steps', type: 'integer', required: true, minimum: -5000, maximum: 250000 }],
});

const zClearAction = () => ({
    ...zHomeAction(),
    action_id: 'oem.z.clear',
    label: 'Z Clear',
    dependencies: [dep('stale_bms_projection', false, 'Stale BMS projection')],
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
    BIOXP_Y_RELATIVE_MIN_STEPS: -102_936,
    BIOXP_Y_RELATIVE_MAX_STEPS: 102_936,
    BIOXP_Y_ABSOLUTE_MIN_STEPS: 0,
    BIOXP_Y_ABSOLUTE_MAX_STEPS: 102_956,
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
    useBioXpOperatorDashboard: (_generation: number, enabled: boolean) => {
        state.v1DashboardEnabled = enabled;
        return state.dashboard;
    },
    useBioXpOperatorDashboardV2: () => state.v2Dashboard,
    useBioXpOperatorControlCatalogV2: () => state.v2Catalog,
    useBioXpOperatorReceiptV2: () => state.yReceipt,
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
    useBioXpOperatorControlCatalog: (_generation: number, enabled: boolean) => {
        state.v1CatalogEnabled = enabled;
        return state.catalog;
    },
    useBioXpOperatorActionAdmission: (...args: unknown[]) => {
        state.admissionCalls += 1;
        return { data: { enabled: true, disabled_reason: null, dependencies: [] }, error: null };
    },
    useInvokeBioXpOperatorAction: () => ({
        data: undefined,
        error: null,
        isPending: state.invokePending,
        mutate: (payload: Record<string, unknown>) => state.invokeCalls.push(payload),
        reset: vi.fn(),
    }),
    useInvokeBioXpOperatorActionV2: () => ({
        data: state.yInvokeData,
        error: state.yInvokeError,
        isPending: false,
        mutate: (payload: Record<string, unknown>) => state.yInvokeCalls.push(payload),
        reset: vi.fn(),
    }),
    useInterruptBioXpOperatorActionV1: () => ({
        data: state.yInterruptData,
        error: state.yInterruptError,
        isPending: false,
        mutate: (payload: Record<string, unknown>) => state.yInterruptCalls.push(payload),
        reset: vi.fn(),
    }),
    useConnectBioXp: () => ({ data: undefined, error: null, isPending: false, mutate: vi.fn() }),
    useDisconnectBioXp: () => ({ data: undefined, error: null, isPending: false, mutate: vi.fn() }),
    useRecoverBioXpMotion: () => ({ data: undefined, error: null, isPending: false, mutate: vi.fn(), reset: vi.fn() }),
    useUpdateBioXpFreshness: () => ({ data: undefined, error: null, isPending: false, mutate: vi.fn() }),
    bioXpErrorText: (error: unknown) => String(error),
    bioXpErrorPresentation: (error: unknown) => {
        const response = (error as { response?: { status?: number; data?: unknown } })?.response;
        const detail = (response?.data as { detail?: { error?: string } } | undefined)?.detail;
        return {
            status: response?.status ?? null,
            summary: detail?.error ?? String(error),
            rawJson: JSON.stringify(response?.data ?? null, null, 2),
        };
    },
}));

vi.mock('../../src/components/BioXpCameraPanel', () => ({ BioXpCameraPanel: () => null }));
vi.mock('../../src/components/BioXpOperatorControlTabs', () => ({ BioXpOperatorControlTabs: () => null }));
vi.mock('../../src/components/BioXpPipetteControlPanel', () => ({ BioXpPipetteControlPanel: () => null }));
vi.mock('../../src/components/BioXpQuickDashboard', () => ({ BioXpQuickDashboard: () => null }));
vi.mock('../../src/components/BioXpOperatorReports', () => ({ BioXpOperatorReports: () => null }));

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

const setZInput = async (index: number, value: string) => {
    const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('Z Axis')) as HTMLElement;
    const inputs = [...article.querySelectorAll('input[type="number"]')] as HTMLInputElement[];
    const input = inputs[index];
    await act(async () => {
        const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
        valueSetter?.call(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
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
    state.v1DashboardEnabled = null;
    state.v1CatalogEnabled = null;
    state.connectionGeneration = 1;
    state.invokeCalls = [];
    state.invokePending = false;
    state.historyCalls = [];
    state.yInvokeCalls = [];
    state.yInterruptCalls = [];
    state.yInvokeError = null;
    state.yInterruptError = null;
    state.yInvokeData = undefined;
    state.yInterruptData = undefined;
    state.yReceipt.data = undefined;
    state.v2Dashboard.error = null;
    state.v2Dashboard.isStale = false;
    state.v2Dashboard.data.ownership_generation = 1;
    state.v2Dashboard.data.board4.state_version = 3;
    state.v2Dashboard.data.y_axis.ownership_generation = 1;
    state.v2Dashboard.data.y_axis.state_version = 4;
    state.v2Dashboard.data.y_axis.latest_compact_receipt = null;
    state.v2Catalog.error = null;
    state.v2Catalog.isStale = false;
    state.v2Catalog.data = {
        schema_version: 'bioxp.operator_control_catalog.v2',
        dashboard: structuredClone(state.v2Dashboard.data),
        actions: [
            'oem.y.move_steps',
            'oem.y.move_absolute',
            'oem.y.manual_panel_home',
            'oem.y.diagnostic_home',
        ].map((action_id) => ({
            action_id,
            request_schema_version: 'bioxp.operator_action_request.v2',
            response_schema_version: 'bioxp.operator_action_receipt.v2',
            interrupt: false,
            enabled: true,
            disabled_reason: null,
        })).concat([{
            action_id: 'oem.y.stop',
            request_schema_version: 'bioxp.operator_interrupt_request.v1',
            response_schema_version: 'bioxp.operator_interrupt_receipt.v1',
            interrupt: true,
            enabled: true,
            disabled_reason: null,
        }]),
    };
    state.history.data.receipts = [];
    state.dashboard.data.motion = { enabled: true, reason: null };
    state.dashboard.data.x_axis.latest_receipt = null;
    state.dashboard.data.successive_move_queue = {};
    state.catalog.data.actions = [
        xMoveAction(),
        xAbsoluteAction(),
        xHomeAction(),
        zHomeAction(),
        zMoveAction(),
        zAbsoluteAction(),
        zClearAction(),
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
        expect(state.v1DashboardEnabled).toBe(true);
        expect(state.v1CatalogEnabled).toBe(true);
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

    it('does not add BMS lifecycle, motion-banner, receipt, or queue gates to robot-authorized X actions', async () => {
        state.dashboard.data.x_axis.provider.lifecycle.state = 'unprepared';
        state.dashboard.data.x_axis.status.reference = 'desynced';
        state.dashboard.data.motion = { enabled: false, reason: 'stale dashboard blocker' };
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
        const home = buttons.find((button) => button.textContent === 'Home') as HTMLButtonElement;
        const goAbsolute = buttons.find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;

        expect(movePositive.disabled).toBe(false);
        expect(goAbsolute.disabled).toBe(false);
        expect(home.disabled).toBe(false);
        await act(async () => movePositive.click());
        expect(state.invokeCalls[0]).toEqual({
            actionId: 'oem.x.move_steps',
            connectionGeneration: 1,
            ownershipGeneration: 1,
            inputs: { steps: 10000 },
        });
        expect(state.admissionCalls).toBe(0);
    });

    it('uses robot enabled state and catalog input schemas without local X/Z re-adjudication', async () => {
        const xMove = state.catalog.data.actions.find((row) => row.action_id === 'oem.x.move_steps')!;
        xMove.dependencies = [dep('stale_bms_projection', false, 'Stale BMS projection')];
        const xAbsolute = state.catalog.data.actions.find((row) => row.action_id === 'oem.x.move_absolute')!;
        xAbsolute.inputs = [{ name: 'position_steps', type: 'integer', required: true, minimum: -5000, maximum: 120000 }];

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        await setXAbsolute('100000');
        await setZInput(0, '200000');
        await setZInput(1, '200000');

        const xArticle = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const xButtons = [...xArticle.querySelectorAll('button')] as HTMLButtonElement[];
        const xMovePositive = xButtons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const xGoAbsolute = xButtons.find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;
        const zArticle = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('Z Axis')) as HTMLElement;
        const zButtons = [...zArticle.querySelectorAll('button')] as HTMLButtonElement[];
        const zMovePositive = zButtons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const zGoAbsolute = zButtons.find((button) => button.textContent === 'Go absolute') as HTMLButtonElement;

        expect(xMovePositive.disabled).toBe(false);
        expect(xGoAbsolute.disabled).toBe(false);
        expect(zMovePositive.disabled).toBe(false);
        expect(zGoAbsolute.disabled).toBe(false);
        await act(async () => xMovePositive.click());
        await act(async () => xGoAbsolute.click());
        await act(async () => zMovePositive.click());
        await act(async () => zGoAbsolute.click());
        expect(state.invokeCalls.map((call) => ({ actionId: call.actionId, inputs: call.inputs }))).toEqual([
            { actionId: 'oem.x.move_steps', inputs: { steps: 10000 } },
            { actionId: 'oem.x.move_absolute', inputs: { position_steps: 100000 } },
            { actionId: 'oem.z.move_steps', inputs: { steps: 200000 } },
            { actionId: 'oem.z.move_absolute', inputs: { position_steps: 200000 } },
        ]);
    });

    it('uses the robot action enabled flag as final X command authority', async () => {
        const xHome = state.catalog.data.actions.find((row) => row.action_id === 'oem.x.manual_panel_home')!;
        Object.assign(xHome, {
            enabled: false,
            disabled_reason: 'Robot denied X Home.',
        });

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const home = [...article.querySelectorAll('button')].find((button) => button.textContent === 'Home') as HTMLButtonElement;
        expect(home.disabled).toBe(true);
        expect(home.title).toContain('Robot denied X Home.');
    });

    it('does not turn a pending unrelated mutation into a Z Clear lockout', async () => {
        state.invokePending = true;
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const zArticle = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('Z Axis')) as HTMLElement;
        const zClear = [...zArticle.querySelectorAll('button')].find((button) => button.textContent === 'Z Clear (automatic OEM position)') as HTMLButtonElement;
        expect(zClear.disabled).toBe(false);
    });

    it('applies the robot catalog X input schema to absolute targets', async () => {
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

    it('leaves successive X move and Home admission to the robot while an X command is active', async () => {
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
        expect(home.disabled).toBe(false);
        expect(home.title).not.toContain('acknowledged');

        const queueStrip = article.querySelector('[data-testid="successive-move-queue"]') as HTMLElement;
        expect(queueStrip).not.toBeNull();
        expect(queueStrip.textContent).toContain('X:');
        expect(queueStrip.textContent).toContain('oem.x.move_steps');
        expect(state.admissionCalls).toBe(0);
    });

    it('leaves X queue admission to the robot when the cached queue projection is full', async () => {
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

        expect(movePositive.disabled).toBe(false);
        expect(state.admissionCalls).toBe(0);
    });

    it('keeps moves and cross-axis Home submittable while a command is pending (successive entry)', async () => {
        state.invokePending = true;

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const xArticle = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const xButtons = [...xArticle.querySelectorAll('button')] as HTMLButtonElement[];
        const xMovePositive = xButtons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const xHome = xButtons.find((button) => button.textContent === 'Home') as HTMLButtonElement;
        const zArticle = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('Z Axis')) as HTMLElement;
        const zButtons = [...zArticle.querySelectorAll('button')] as HTMLButtonElement[];
        const zHome = zButtons.find((button) => button.textContent === 'OEM Z Home') as HTMLButtonElement;

        expect(xMovePositive.disabled).toBe(false);
        expect(xHome.disabled).toBe(false);
        expect(zHome.disabled).toBe(false);
        expect(state.invokeCalls).toHaveLength(0);
    });

    it('submits a successive move while the prior move is pending', async () => {
        state.invokePending = true;

        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });

        const article = [...container.querySelectorAll('article')].find((node) => node.textContent?.includes('X Axis')) as HTMLElement;
        const buttons = [...article.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;

        await act(async () => {
            movePositive.click();
            await Promise.resolve();
        });

        expect(state.invokeCalls).toHaveLength(1);
        expect(state.invokeCalls[0]).toMatchObject({ actionId: 'oem.x.move_steps' });
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

    it('mounts operational strict Y controls with exact bounds and payloads while generic Y remains absent', async () => {
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        expect(section).toBeTruthy();
        const manualControls = section.closest('section') as HTMLElement;
        expect(manualControls.querySelector('h2')?.textContent).toBe('Exact OEM Manual Controls');
        expect(manualControls.textContent).toContain('X Axis');
        expect(manualControls.textContent).toContain('Z Axis');
        expect(manualControls.textContent).toContain('Gripper');
        const inputs = [...section.querySelectorAll('input[type="number"]')] as HTMLInputElement[];
        expect(inputs[0]?.min).toBe('-102936');
        expect(inputs[0]?.max).toBe('102936');
        expect(inputs[1]?.min).toBe('0');
        expect(inputs[1]?.max).toBe('102956');
        const buttons = [...section.querySelectorAll('button')] as HTMLButtonElement[];
        const movePositive = buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement;
        const stop = buttons.find((button) => button.textContent === 'STOP Y') as HTMLButtonElement;
        expect(movePositive.disabled).toBe(false);
        expect(stop.disabled).toBe(false);
        await act(async () => movePositive.click());
        expect(state.yInvokeCalls).toHaveLength(1);
        expect(state.yInvokeCalls[0]).toMatchObject({
            request: {
                action_id: 'oem.y.move_steps',
                expected_connection_generation: 1,
                schema_version: 'bioxp.operator_action_request.v2',
                expected_ownership_generation: 1,
                expected_board_epoch_by_board: { '4': 2 },
                inputs: { steps: 1000 },
            },
        });
        await act(async () => stop.click());
        expect(state.yInterruptCalls[0]).toEqual({
            actionId: 'oem.y.stop',
            request: {
                expected_connection_generation: 1,
                schema_version: 'bioxp.operator_interrupt_request.v1',
                reason: 'BMS operator requested addressed Serial-206 Y STOP',
                observed_ownership_generation: 1,
                observed_board_epoch_by_board: { '4': 2 },
            },
        });
        const yCards = [...container.querySelectorAll('article')].filter((node) => node.querySelector('h3')?.textContent === 'Y Axis');
        expect(yCards).toEqual([section]);
    });

    it('fails normal Y closed on any authority-version mismatch but keeps STOP independent', async () => {
        (state.v2Catalog.data!.dashboard as typeof state.v2Dashboard.data).y_axis.state_version = 3;
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        const buttons = [...section.querySelectorAll('button')] as HTMLButtonElement[];
        expect((buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement).disabled).toBe(true);
        expect((buttons.find((button) => button.textContent === 'STOP Y') as HTMLButtonElement).disabled).toBe(false);
        expect(section.textContent).toContain('matching v2 catalog and dashboard authority is unavailable');
    });

    it('renders bounded structured enqueue and STOP errors adjacent to Y with status and raw evidence', async () => {
        state.yInvokeError = { response: { status: 409, data: { detail: { error: 'board_epoch_conflict', expected: { '4': 2 }, actual: { '4': 3 } } } } };
        state.yInterruptError = { response: { status: 504, data: { detail: { error: 'bioxp_robot_timeout', dispatch_state: 'outcome_ambiguous' } } } };
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        expect(section.textContent).toContain('Y enqueue failed · HTTP 409 · board_epoch_conflict');
        expect(section.textContent).toContain('Y STOP failed · HTTP 504 · bioxp_robot_timeout');
        const raw = [...section.querySelectorAll('pre')].map((node) => node.textContent).join('\n');
        expect(raw).toContain('"expected"');
        expect(raw).toContain('"actual"');
        expect(raw).toContain('"dispatch_state"');
    });

    it.each([
        ['dashboard error', () => { state.v2Dashboard.error = new Error('dashboard query failed'); }],
        ['dashboard stale', () => { state.v2Dashboard.isStale = true; }],
        ['catalog error', () => { state.v2Catalog.error = new Error('catalog query failed'); }],
        ['catalog stale', () => { state.v2Catalog.isStale = true; }],
    ])('fails normal Y closed for an isolated %s condition', async (_label, applyFault) => {
        applyFault();
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        const buttons = [...section.querySelectorAll('button')] as HTMLButtonElement[];
        expect((buttons.find((button) => button.textContent === 'Move +') as HTMLButtonElement).disabled).toBe(true);
        expect((buttons.find((button) => button.textContent === 'STOP Y') as HTMLButtonElement).disabled).toBe(false);
        expect(section.textContent).toContain('matching v2 catalog and dashboard authority is unavailable');
    });

    it('renders successful detailed Y action and independent STOP receipts', async () => {
        state.v2Dashboard.data.y_axis.latest_compact_receipt = { command_id: 'cmd-y-detail' };
        state.yReceipt.data = {
            command_id: 'cmd-y-detail',
            status: 'completed',
            completion_class: 'event_128',
            requested_values: { steps: 100 },
            effective_values: { target_steps: 1100 },
            observed_values: {
                terminal_position_steps: 1100,
                terminal_speed_steps_s: 0,
                discrepancy_steps: 0,
            },
            physical_effect_verified: false,
            controller_evidence: { addressed_event_128: true, speed_zero: true },
            raw_return_layers: { provider: { ok: true } },
            transport_artifacts: [{ kind: 'tmcl_reply', status: 100 }],
        };
        state.yInterruptData = {
            schema_version: 'bioxp.operator_interrupt_receipt.v1',
            interrupt_attempt_id: 'interrupt-attempt-12345678',
            action_id: 'oem.y.stop',
            controller_stop_acknowledged: true,
            persistence_state: 'committed',
            recovery_hold: false,
        };
        await act(async () => {
            root.render(<BioXpCockpit />);
            await Promise.resolve();
        });
        const section = container.querySelector('[data-testid="serial206-y-authority-panel"]') as HTMLElement;
        expect(section.textContent).toContain('Command cmd-y-detail: completed');
        expect(section.textContent).toContain('class=event_128');
        expect(section.textContent).toContain('terminal position=1100');
        expect(section.textContent).toContain('terminal speed=0');
        expect(section.textContent).toContain('Latest independent Y STOP receipt');
        expect(section.textContent).toContain('interrupt-attempt-12345678');
        const raw = [...section.querySelectorAll('pre')].map((node) => node.textContent).join('\n');
        expect(raw).toContain('"addressed_event_128": true');
        expect(raw).toContain('"status": 100');
    });
});
